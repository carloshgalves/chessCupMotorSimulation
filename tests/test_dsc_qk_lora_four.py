"""Engineering-only fixtures. They never claim historical style fidelity."""
from __future__ import annotations

import chess
import pytest

torch = pytest.importorskip("torch")

from chesssim.dsc_qk_lora_core import (
    QKLoRA, logits, load_state, loss, model_state, pack, predict, train_epoch,
)
from chesssim.dsc_qk_lora_four import (
    BOOTSTRAP_REPETITIONS, GAMES, PLAYERS, RANKS, SALT, SEED, WINDOW,
    _blind, _game_window, arm_for_game,
)


def _example(*, target=0, codes=None):
    torch.manual_seed(7)
    codes = codes or [((12 * 64 + 28) * 4), ((6 * 64 + 21) * 4)]
    return {
        "latent": torch.randn(64, 6, dtype=torch.float32).half(),
        "q0": torch.randn(64, 4, dtype=torch.float32).half(),
        "k0": torch.randn(64, 4, dtype=torch.float32).half(),
        "indices": torch.tensor(codes, dtype=torch.long),
        "prior_logp": torch.log(torch.tensor([1 / len(codes)] * len(codes))),
        "moves": [f"m{i}" for i in range(len(codes))],
        "target": target,
        "game_sha256": "a" * 64,
    }


def test_zero_low_rank_delta_is_exact_original_policy():
    batch = pack([_example()])
    torch.manual_seed(10)
    for rank in RANKS:
        head = QKLoRA(6, 4, rank)
        output = logits(batch, head, torch.randn(3, 4), 0.25)
        assert torch.allclose(output, batch["prior"], atol=1e-6)
    with pytest.raises(ValueError, match="rank"):
        QKLoRA(6, 4, 5)


def test_qk_lora_and_promotion_channel_are_differentiable():
    # Same from/to pair, queen and knight promotion: only channel differs.
    square_pair = (52 * 64 + 60) * 4
    batch = pack([_example(codes=[square_pair, square_pair + 3])])
    head = QKLoRA(6, 4, 2)
    with torch.no_grad():
        head.bk.fill_(0.3)
        head.bq.fill_(0.05)
    promo = torch.ones(3, 4)
    scores = logits(batch, head, promo, 0.25)
    assert torch.isfinite(scores).all()
    assert not torch.allclose(scores, batch["prior"])
    assert abs(float((scores[0, 1] - scores[0, 0]))) > 1e-7
    objective = loss(batch, head, promo, 0.25)
    objective.backward()
    assert any(p.grad is not None and torch.isfinite(p.grad).all()
               for p in head.parameters())


def test_fit_changes_only_adapter_not_frozen_latent_cache():
    example = _example()
    original_latent = example["latent"].clone()
    original_q = example["q0"].clone()
    head = QKLoRA(6, 4, 2)
    before = [p.detach().clone() for p in head.parameters()]
    train_epoch(head, [example] * 8, torch.ones(3, 4), 0.25, seed=37)
    assert any(not torch.equal(a, p) for a, p in zip(before, head.parameters()))
    assert torch.equal(example["latent"], original_latent)
    assert torch.equal(example["q0"], original_q)
    predictions = predict([example], head, torch.ones(3, 4), 0.25)
    assert sum(predictions[0]["probs"]) == pytest.approx(1.0)
    recovered = load_state(6, 4, 2, model_state(head))
    assert recovered.rank == 2
    assert recovered.aq.shape == head.aq.shape


def test_game_hash_not_player_identity_determines_split():
    assert arm_for_game("b" * 64) in GAMES
    assert SEED == 3719 and WINDOW == 8 and SALT == "chesssim-latent-qk-v1-2026-11-15"
    assert GAMES == {"fit": 48, "tune": 12, "audit": 12}
    assert BOOTSTRAP_REPETITIONS == 2000
    with pytest.raises(ValueError, match="SHA-256"):
        arm_for_game("bad-game-id")


def test_only_eight_consecutive_focal_player_moves_form_a_window():
    rows = [{"fen": chess.Board().fen(), "ply": 21 + 2 * i,
             "game_sha256": "c"*64, "player":"fischer"} for i in range(8)]
    # Repeated identical positions are not a valid independent window.
    assert _game_window(rows) is None
    board = chess.Board()
    white = ("a2a3", "b2b3", "c2c3", "d2d3",
             "e2e3", "f2f3", "g2g3", "h2h3")
    black = ("a7a6", "b7b6", "c7c6", "d7d6",
             "e7e6", "f7f6", "g7g6", "h7h6")
    changing = []
    for i in range(8):
        changing.append({"fen": board.fen(), "ply": 21+2*i,
                         "game_sha256": "c"*64, "player": "fischer"})
        board.push_uci(white[i])
        board.push_uci(black[i])
    assert _game_window(changing) is not None
    changing[3]["ply"] += 1
    assert _game_window(changing) is None


def test_blind_sequence_requires_real_global_game_clusters_and_balanced_labels():
    rows = []
    windows = []
    scores = {player: [] for player in PLAYERS}
    for p_idx, slug in enumerate(PLAYERS):
        for game in range(12):
            gameid = f"{p_idx*12+game+1:064x}"
            entries = [
                {"player":slug, "game_sha256":gameid, "move":"e2e4"}
                for _ in range(WINDOW)
            ]
            rows.extend(entries)
            windows.append(entries)
            for candidate in PLAYERS:
                true_p = 0.9 if candidate == slug else 0.15
                scores[candidate].extend([
                    {"moves":["e2e4", "d2d4"], "target":0,
                     "probs":[true_p, 1-true_p],
                     "game_sha256":gameid}
                    for _ in range(WINDOW)
                ])
    outcome = _blind(rows, scores, windows, 41)
    assert outcome["sequence_length"] == 8
    assert outcome["games"] == 48
    assert outcome["decisions"] == 384
    assert outcome["sequence_correct"] == 48
    assert outcome["sequence_balanced_accuracy"] == pytest.approx(1.0)
    assert outcome["sequence_balanced_game_bootstrap_95_ci"] == pytest.approx([1.,1.])
    assert outcome["sequence_game_permutation_one_sided_p"] < 0.05
    assert outcome["single_move_correct"] == 384
    assert outcome["majority_game_baseline"] == pytest.approx(0.25)

    # Identical policies are unidentifiable, even if they have different names.
    tied = {slug: [{**r,"probs":[0.5,0.5]} for r in scores[slug]]
            for slug in PLAYERS}
    neutral = _blind(rows, tied, windows, 41)
    assert neutral["sequence_balanced_accuracy"] == pytest.approx(0.25)
    assert neutral["sequence_game_permutation_one_sided_p"] > 0.05


def test_blind_rejects_cross_player_game_reuse():
    rows=[]
    groups=[]
    predictions={slug:[] for slug in PLAYERS}
    for player in PLAYERS:
        group=[{"player":player,"game_sha256":"f"*64,
                "move":"e2e4"} for _ in range(WINDOW)]
        rows.extend(group)
        groups.append(group)
        for slug in PLAYERS:
            predictions[slug].extend([{
                "moves":["e2e4","d2d4"],"probs":[.5,.5],"target":0,
                "game_sha256":"f"*64,
            } for _ in range(WINDOW)])
    with pytest.raises(ValueError, match="Repeated original game"):
        _blind(rows,predictions,groups,44)



def test_historic_target_label_never_changes_frozen_policy_logits():
    example = _example()
    batch = pack([example])
    head = QKLoRA(6, 4, 2)
    with torch.no_grad():
        head.bq.fill_(0.2)
        head.bk.fill_(0.05)
    promo = torch.randn(3, 4)
    first = logits(batch, head, promo, 0.3).detach().clone()
    # Simulate a different human move and game metadata. The prediction
    # is made without any label/identity data.
    changed = {**example, "target": 1, "game_sha256": "d"*64}
    after = logits(pack([changed]), head, promo, 0.3)
    assert torch.allclose(first, after, atol=0, rtol=0)
    assert changed["target"] != example["target"]


def test_original_projection_and_promotion_weights_remain_frozen():
    torch.manual_seed(78)
    q = torch.nn.Linear(6,4)
    k = torch.nn.Linear(6,4)
    promo = torch.nn.Linear(4,3)
    for layer in (q,k,promo):
        for p in layer.parameters():
            p.requires_grad_(False)
    example = _example()
    example["q0"] = q(example["latent"].float()).detach().half()
    example["k0"] = k(example["latent"].float()).detach().half()
    head = QKLoRA(6,4,2)
    objective = loss(pack([example]),head,promo.weight.detach(),0.25)
    objective.backward()
    assert all(p.grad is None for layer in (q,k,promo)
               for p in layer.parameters())
    assert any(p.grad is not None for p in head.parameters())



def test_blind_detects_mispaired_candidate_trajectory_before_scoring():
    rows=[]
    groups=[]
    forecasts={slug:[] for slug in PLAYERS}
    for player_index, player in enumerate(PLAYERS):
        for game_index in range(12):
            game_sha=f"{player_index*12+game_index+1:064x}"
            entries=[{"player":player,"game_sha256":game_sha,
                      "move":"e2e4"} for _ in range(WINDOW)]
            rows.extend(entries); groups.append(entries)
            for slug in PLAYERS:
                forecasts[slug].extend([
                    {"moves":["e2e4","d2d4"],"probs":[.6,.4],
                     "target":0,"game_sha256":game_sha}
                    for _ in range(WINDOW)])
    forecasts["tal"][1]["game_sha256"]="e"*64
    with pytest.raises(ValueError,match="identical positions"):
        _blind(rows,forecasts,groups,42)


def test_lora_only_checkpoint_reloads_without_material_prediction_drift():
    example=_example()
    head=QKLoRA(6,4,4)
    torch.manual_seed(15)
    with torch.no_grad():
        head.bq.normal_(0,.1)
        head.bk.normal_(0,.1)
    promo=torch.randn(3,4)
    original=predict([example],head,promo,.4)[0]["probs"]
    restored=load_state(6,4,4,model_state(head))
    after=predict([example],restored,promo,.4)[0]["probs"]
    assert after==pytest.approx(original,abs=1e-5)
