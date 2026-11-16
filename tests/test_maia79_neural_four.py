"""Negative controls for the genuine Maia3-79M four-profile neural fit."""
from __future__ import annotations

from collections import defaultdict

import pytest
torch = pytest.importorskip("torch")
import chess

from chesssim import maia79_neural_four as v1
from chesssim.dsc_qk_lora_core import QKLoRA


def test_split_uses_global_trajectory_not_player_label():
    game = "a" * 64
    assert v1.game_arm(game) == v1.game_arm(game)
    assert v1.game_arm(game) in {"fit", "tune", "audit", "unused"}
    for invalid in ("abc", "Z" * 64, "0" * 65, "", 123):
        with pytest.raises(ValueError):
            v1.game_arm(invalid)


def test_train_only_four_identity_selector_and_global_game_embargo(monkeypatch, tmp_path):
    players = v1.PLAYERS
    indexes = defaultdict(list)
    for number in range(30000):
        game = f"{number:064x}"
        arm = v1.game_arm(game)
        if arm != "unused" and len(indexes[arm]) < 12:
            indexes[arm].append(game)
        if all(len(indexes[arm]) == 12 for arm in ("fit", "tune", "audit")):
            break
    assert all(len(indexes[arm]) == 12 for arm in ("fit", "tune", "audit"))
    rows = []
    for i, slug in enumerate(players):
        for arm in ("fit", "tune", "audit"):
            for j in range(3):
                game = indexes[arm][3*i+j]
                rows.append({
                    "player": slug, "game_sha256": game, "ply": 21,
                    "policy_input": {"fen": f"faked-fen-{game}"},
                    "policy_target": {"move_uci": "g1f3"}
                })
    monkeypatch.setattr(v1, "_read_json", lambda *_: {"players": {p:{} for p in players}})
    monkeypatch.setattr(v1, "_position_key", lambda x: x)
    monkeypatch.setattr(v1, "_validated_training_rows", lambda *_: iter(rows))
    chosen, counts = v1.choose_training(tmp_path, fit_games=1,
                                        tune_games=1, audit_games=1,
                                        moves_per_game=1)
    assert counts["selected_unique_games"] == 12
    hashes = [r["game_sha256"] for arms in chosen.values() for rows in arms.values()
              for r in rows]
    assert len(hashes) == len(set(hashes)) == 12
    assert set(chosen) == set(players)
    with pytest.raises(ValueError, match="Game budget"):
        v1.choose_training(tmp_path, fit_games=0)
    with pytest.raises(ValueError, match="Moves per game"):
        v1.choose_training(tmp_path, moves_per_game=0)


def test_global_position_embargo_denies_non_disjoint_fit(monkeypatch, tmp_path):
    selected = []
    for number in range(3000):
        key = f"{number:064x}"
        arm = v1.game_arm(key)
        if arm in ("fit", "tune"):
            selected.append((key, arm))
        if {arm for _, arm in selected} == {"fit", "tune"}:
            break
    fit_id = next(key for key, arm in selected if arm == "fit")
    tune_id = next(key for key, arm in selected if arm == "tune")
    fake = lambda ident: {"player": "fischer", "game_sha256": ident, "ply": 21,
                           "policy_input": {"fen": "shared-fen"},
                           "policy_target": {"move_uci": "g1f3"}}
    monkeypatch.setattr(v1, "_read_json", lambda *_: {"players": {p:{} for p in v1.PLAYERS}})
    monkeypatch.setattr(v1, "_position_key", lambda x: x)
    monkeypatch.setattr(v1, "_validated_training_rows",
                        lambda *_: iter([fake(fit_id), fake(tune_id)]))
    with pytest.raises(ValueError, match="UNDERPOWERED"):
        v1.choose_training(tmp_path, fit_games=1, tune_games=1, audit_games=1)


def _example(ids=(0, 4096, 4097, 4098, 4099, 4351)):
    torch.manual_seed(6)
    latent = torch.randn(64, 1024) * 0.01
    return {
        "latent": latent.half(),
        "q0": (torch.randn(64, 1024) * 0.01).half(),
        "k0": (torch.randn(64, 1024) * 0.01).half(),
        "ids": torch.tensor(ids, dtype=torch.long),
        "prior": torch.log_softmax(torch.arange(len(ids), dtype=torch.float32), dim=0),
        "target": 0, "game_sha256": "f" * 64,
    }


def test_frozen_original_exactly_retained_at_zero_delta_all_promotions():
    ex = _example()
    b = v1._pack([ex])
    model = QKLoRA(1024, 1024, 4)   # B matrices initialized to zero
    weight = torch.randn(4, 1024)
    base = v1._adapted_logits(b, None, weight)
    adapted = v1._adapted_logits(b, model, weight)
    torch.testing.assert_close(adapted, base, atol=1e-7, rtol=0)


def test_nonzero_k_lora_changes_promotion_channels():
    ex = _example()
    b = v1._pack([ex])
    model = QKLoRA(1024, 1024, 4)
    torch.manual_seed(20)
    with torch.no_grad():
        model.bk.normal_(0, 0.2)
    weight = torch.randn(4, 1024) * 0.1
    scores = v1._adapted_logits(b, model, weight)
    assert torch.isfinite(scores).all()
    # Four different promotion pieces share the same source+dest but differ
    # in the (frozen) promo_bias_proj weight dotted with K-LoRA delta.
    diffs = (scores - b["prior"])[0, 1:5].tolist()
    assert len(set(round(x, 6) for x in diffs)) > 1


def test_gradients_change_only_low_rank_matrices_no_frozen_maia_loaded():
    torch.set_num_threads(1)
    e = _example(ids=(0, 1, 2, 3))
    model = QKLoRA(1024, 1024, 4)
    before = {name: p.detach().clone() for name, p in model.named_parameters()}
    loss = v1.train_epoch(model, [e], torch.zeros(4, 1024), seed=4)
    assert 0 <= loss < 100
    assert any(not torch.equal(p, before[name])
               for name, p in model.named_parameters())


def test_pack_rejects_impossible_target_and_vocabulary():
    e = _example(ids=(0, 1, 2))
    e["target"] = 3
    with pytest.raises(ValueError, match="label"):
        v1._pack([e])
    e["target"] = 0
    e["ids"] = torch.tensor([0, 4352])
    model = QKLoRA(1024, 1024, 4)
    with pytest.raises(ValueError, match="vocabulary"):
        v1._adapted_logits(v1._pack([e]), model, torch.zeros(4, 1024))


def test_historical_tokens_reconstructs_both_sides_with_context(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace
    observed = []
    dataset = ModuleType("maia3.dataset")
    dataset.tokenize_board = lambda board: board.fen()
    def fake_historical(tokens, cfg, **kw):
        observed.extend(tokens)
        return tokens
    dataset.get_historical_tokens = fake_historical
    maia = ModuleType("maia3")
    maia.dataset = dataset
    monkeypatch.setitem(sys.modules, "maia3", maia)
    monkeypatch.setitem(sys.modules, "maia3.dataset", dataset)
    board = chess.Board()
    board.push_uci("e2e4")
    board.push_uci("e7e5")
    board.push_uci("g1f3")
    v1.historical_tokens(board, SimpleNamespace(history=8))
    assert len(observed) == 4
    assert observed[-1] == board.fen()
    assert observed[0] == chess.Board().fen()
