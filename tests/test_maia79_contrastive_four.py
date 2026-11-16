"""Fail-closed and actual-gradient tests for Maia79 contrastive four-player FIT."""
from __future__ import annotations

import copy
import math

import pytest

torch = pytest.importorskip("torch")
from chesssim import maia79_contrastive_four as v2
from chesssim.dsc_qk_lora_core import QKLoRA


def game(seed: int) -> str:
    return f"{seed:064x}"


def examples_one_per_player():
    torch.manual_seed(37)
    result = {}
    for i, slug in enumerate(v2.PLAYERS):
        ids = torch.tensor([0, 1, 2, 3])
        result[slug] = [{
            "latent": (torch.randn(64, 1024)*0.02).half(),
            "q0": (torch.randn(64, 1024)*0.02).half(),
            "k0": (torch.randn(64, 1024)*0.02).half(),
            "ids": ids,
            "prior": torch.log_softmax(torch.tensor([-0.8, 0.6, 0.1, -0.2]), dim=0),
            "target": i,
            "game_sha256": game(i),
        }]
    return result


def test_real_gradients_have_four_player_label_discrimination():
    torch.set_num_threads(1)
    torch.manual_seed(11)
    generic = QKLoRA(1024, 1024, 4)
    adapters = {}
    for slug in v2.PLAYERS:
        m = QKLoRA(1024, 1024, 4)
        m.load_state_dict(generic.state_dict())
        adapters[slug] = m
    initial = {p:copy.deepcopy(m.state_dict()) for p,m in adapters.items()}
    original = examples_one_per_player()
    out = v2.contrastive_epoch(
        adapters, original, torch.zeros(4,1024), generic, epoch=0)
    assert out["steps"] == 1
    assert all(math.isfinite(out[key]) for key in ("total", "move_ce", "identity_ce"))
    assert any(
        not torch.equal(initial[p]["bq"], adapters[p].state_dict()["bq"])
        for p in v2.PLAYERS
    )
    assert not any(param.requires_grad for param in generic.parameters())


def test_four_balanced_real_labels_required():
    x = examples_one_per_player()
    torch.manual_seed(11)
    g = QKLoRA(1024,1024,4)
    models = {p:QKLoRA(1024,1024,4) for p in v2.PLAYERS}
    with pytest.raises(ValueError, match="equal nonzero"):
        v2.contrastive_epoch(models, {**x,v2.PLAYERS[0]:[]},
                             torch.zeros(4,1024),g,epoch=0)
    with pytest.raises(ValueError, match="Exactly four"):
        v2.contrastive_epoch(models, {p:x[p] for p in v2.PLAYERS[:3]},
                             torch.zeros(4,1024),g,epoch=0)


def test_blind_groups_real_games_not_individual_lances():
    names = v2.PLAYERS
    rows = {p:[{"game_sha256":game(i+100*j)} for j in range(2)
               for i in [names.index(p)+1]] for p in names}
    scores = {}
    for target in names:
        scores[target] = {}
        for candidate in names:
            scores[target][candidate] = [
                -0.1 if candidate==target else -2.0 for _ in rows[target]]
    result = v2._game_blind(rows,scores,permutations=80)
    assert result["games"] == 8
    assert result["correct"] == 8
    assert result["accuracy"] == 1
    assert result["abstentions"] == 0
    assert result["coverage"] == 1
    assert result["game_label_permutation_p"] >= 1/81
    assert all(result["confusion"][p][p] == 2 for p in names)


def test_blind_prevents_tie_from_defaulting_to_first_player():
    rows = {p:[{"game_sha256":game(i+1)}] for i,p in enumerate(v2.PLAYERS)}
    scores = {p:{q:[-1.0] for q in v2.PLAYERS} for p in v2.PLAYERS}
    result = v2._game_blind(rows,scores,permutations=40)
    assert result["games"] == 4
    assert result["correct"] == 0
    assert result["abstentions"] == 4
    assert result["coverage"] == 0
    assert result["game_label_permutation_p"] == 1


def test_swapped_names_are_real_negative_control():
    rows = {p:[{"game_sha256":game(i+1)}] for i,p in enumerate(v2.PLAYERS)}
    scores = {p:{q:[-0.1 if q==p else -2.0] for q in v2.PLAYERS}
              for p in v2.PLAYERS}
    shuffled = {p:{q:scores[p][v2.SWAP[q]] for q in v2.PLAYERS}
                for p in v2.PLAYERS}
    assert v2._game_blind(rows,scores,permutations=40)["correct"] == 4
    assert v2._game_blind(rows,shuffled,permutations=40)["correct"] == 0


def test_blind_denies_duplicate_games_or_inconsistent_scores():
    rows = {p:[{"game_sha256":game(i+1)}] for i,p in enumerate(v2.PLAYERS)}
    scores = {p:{q:[-1.0] for q in v2.PLAYERS} for p in v2.PLAYERS}
    rows[v2.PLAYERS[1]][0]["game_sha256"] = rows[v2.PLAYERS[0]][0]["game_sha256"]
    with pytest.raises(ValueError, match="Shared game"):
        v2._game_blind(rows,scores,permutations=40)
    rows[v2.PLAYERS[1]][0]["game_sha256"] = game(2)
    scores[v2.PLAYERS[0]][v2.PLAYERS[1]]=[]
    with pytest.raises(ValueError, match="coverage"):
        v2._game_blind(rows,scores,permutations=40)


def test_blind_rejects_invalid_scores_and_permutation_budget():
    rows = {p:[{"game_sha256":game(i+1)}] for i,p in enumerate(v2.PLAYERS)}
    scores = {p:{q:[-1.0] for q in v2.PLAYERS} for p in v2.PLAYERS}
    scores[v2.PLAYERS[0]][v2.PLAYERS[0]] = [float("nan")]
    with pytest.raises(ValueError, match="invalid blind probability"):
        v2._game_blind(rows,scores,permutations=40)
    with pytest.raises(ValueError, match="number of balanced"):
        v2._game_blind(rows,scores,permutations=1)


def test_log_probabilities_aligned_and_original_prior_is_frozen():
    torch.set_num_threads(1)
    ex = examples_one_per_player()[v2.PLAYERS[0]]
    promo = torch.zeros(4,1024)
    base_logprob = v2._target_logprob(ex,None,promo)
    expected = torch.log_softmax(ex[0]["prior"],dim=0)[ex[0]["target"]].item()
    assert base_logprob[0] == pytest.approx(expected, abs=1e-6)
    assert ex[0]["target"] == 0
    assert not any(emb.requires_grad for emb in [ex[0]["latent"],ex[0]["q0"],ex[0]["k0"]])
