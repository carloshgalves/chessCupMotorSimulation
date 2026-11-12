"""#24 slice 2: provenance, accounting and non-fabricated quality statistics."""
from __future__ import annotations

from hashlib import sha256
import json

import chess
import chess.pgn
import pytest

from chesssim import live_board
from chesssim.quality_overlay import QualityOverlay, PROTOCOL
from chesssim.quality_stats import phase_for_ply, summarize_quality


def _scores():
    return {
        1: {"loss_cp": 100, "critical": "mistake"},
        2: {"loss_cp": 240, "critical": "mistake"},
        17: {"loss_cp": 300, "critical": "blunder"},
        61: {"loss_cp": 80, "critical": None},
    }


def _summary(mapping=None, usable=None, **overrides):
    kw = {
        "mapping": _scores() if mapping is None else mapping,
        "usable": {1: True, 2: False, 17: True, 61: True} if usable is None else usable,
        "headers": {
            "White": "Fischer", "Black": "Tal",
            "WhiteModel": "RodentIV:Fischer",
            "WhiteProfileSHA256": "f" * 64,
            "BlackModel": "<script>not-a-model</script>",
        },
        "status": "COMPLETED", "result": "1-0", "total_plies": 66,
        "report_pgn_sha256": "a" * 64,
        "stockfish_sha256": "b" * 64,
        "engine": "Stockfish", "depth": 18,
    }
    kw.update(overrides)
    return summarize_quality(**kw)


def test_phase_thresholds_are_explicit_proxies():
    assert [phase_for_ply(x) for x in (1, 16, 17, 60, 61, 999)] == [
        "opening", "opening", "middle", "middle", "late", "late"]
    with pytest.raises(ValueError):
        phase_for_ply(True)
    with pytest.raises(ValueError):
        phase_for_ply(0)


def test_descriptive_aggregation_keeps_sample_counts_and_exclusions():
    x = _summary()
    assert x["schema"] == "chesssim-verified-quality-stats-v1"
    assert x["status"] == "DESCRIPTIVE_SINGLE_COMPLETED_GAME_NOT_RATING"
    assert x["coverage"] == {
        "sampled": 4, "usable": 3, "excluded": 1,
        "critical": 2, "mean_loss_cp": 160.0,
    }
    assert x["by_phase"]["opening"] == {
        "sampled": 2, "usable": 1, "excluded": 1,
        "critical": 1, "mean_loss_cp": 100.0,
    }
    assert x["by_phase"]["middle"]["mean_loss_cp"] == 300.0
    assert x["by_phase"]["late"]["mean_loss_cp"] == 80.0
    assert x["by_side"]["black"]["sampled"] == 1
    assert x["by_side"]["black"]["usable"] == 0
    assert x["by_side"]["black"]["mean_loss_cp"] is None
    assert x["by_phase_side"]["middle"]["black"]["mean_loss_cp"] is None
    assert x["players"]["white"]["model_declared"] == "RodentIV:Fischer"
    assert x["players"]["white"]["profile_sha256_prefix"] == "f" * 12
    assert x["players"]["black"]["model_declared"] is None
    assert x["provenance"]["pgn_sha256_prefix"] == "a" * 12
    assert x["provenance"]["stockfish_sha256_prefix"] == "b" * 12
    assert "chronological" in x["phase_definition"]
    assert "not model strength" in x["warning"]


def test_no_n_is_never_rendered_as_zero_loss_or_zero_accuracy():
    x = _summary(mapping={1: {"loss_cp": 400, "critical": "blunder"}}, usable={})
    assert x["coverage"]["sampled"] == 1
    assert x["coverage"]["usable"] == 0
    assert x["coverage"]["mean_loss_cp"] is None
    assert x["by_phase"]["opening"]["excluded"] == 1
    assert x["by_phase"]["middle"]["mean_loss_cp"] is None


@pytest.mark.parametrize("change", [
    {"status": "RUNNING"},
    {"result": "*"},
    {"result": "invalid"},
    {"mapping": {}},
    {"report_pgn_sha256": "wrong"},
    {"stockfish_sha256": "wrong"},
    {"depth": 0},
    {"depth": True},
    {"total_plies": 0},
    {"total_plies": True},
])
def test_invalid_game_or_provenance_never_has_summary(change):
    assert _summary(**change) is None


@pytest.mark.parametrize("mapping", [
    {0: {"loss_cp": 0, "critical": None}},
    {2: {"loss_cp": -1, "critical": None}},
    {100: {"loss_cp": 0, "critical": None}},
    {2: {"loss_cp": True, "critical": None}},
])
def test_invalid_sample_fails_closed(mapping):
    assert _summary(mapping=mapping) is None


def test_decision_order_does_not_change_statistics():
    records = _scores()
    other = dict(reversed(list(records.items())))
    assert _summary(mapping=records) == _summary(mapping=other)


def _verified_game(tmp_path):
    run = tmp_path / "duel"
    run.mkdir()
    game = chess.pgn.Game()
    game.headers["White"] = "White"
    game.headers["Black"] = "Black"
    game.headers["Result"] = "1-0"
    game.headers["WhiteModel"] = "RodentIV:White"
    game.headers["WhiteProfileSHA256"] = "e" * 64
    node = game
    for uci in ("e2e4", "e7e5", "g1f3", "b8c6"):
        node = node.add_variation(chess.Move.from_uci(uci))
    pgn = run / "jogo_1.pgn"
    pgn.write_text(str(game) + "\n", encoding="utf-8")
    (run / "summary.json").write_text(
        json.dumps({"status": "COMPLETED", "games": [
            {"status": "COMPLETED", "result": "1-0"}]}), encoding="utf-8")
    states = live_board._states(game)
    rows = [
        {"game_index": 0, "ply": 1, "fen": states[0]["fen"], "played": "e2e4",
         "played_eval_cp": 90, "played_mate_in": None, "cp_loss": 100,
         "depth": 18, "mate_affected": False, "search_disagreement": False},
        {"game_index": 0, "ply": 2, "fen": states[1]["fen"], "played": "e7e5",
         "played_eval_cp": -35, "played_mate_in": -2, "cp_loss": 250,
         "depth": 18, "mate_affected": True, "search_disagreement": False},
        {"game_index": 0, "ply": 3, "fen": states[2]["fen"], "played": "g1f3",
         "played_eval_cp": 60, "played_mate_in": None, "cp_loss": 0,
         "depth": 18, "mate_affected": False, "search_disagreement": True},
        {"game_index": 0, "ply": 4, "fen": states[3]["fen"], "played": "b8c6",
         "played_eval_cp": 40, "played_mate_in": None, "cp_loss": 30,
         "depth": 18},  # unannotated evidence is not usable for means
    ]
    doc = {"protocol": PROTOCOL, "inputs": {
        "pgn_sha256": sha256(pgn.read_bytes()).hexdigest(),
        "stockfish_sha256": "d" * 64,
        "engine_uci_id": {"name": "Stockfish"},
        "depth": 18}, "decisions": rows}
    report = tmp_path / "stockfish.json"
    report.write_text(json.dumps(doc), encoding="utf-8")
    return run, pgn, report, doc


def test_http_snapshot_only_aggregates_completed_byte_identical_pgn(tmp_path):
    run, pgn, report, doc = _verified_game(tmp_path)
    overlay = QualityOverlay(report)
    g = live_board.snapshot(run, quality_overlay=overlay)["duel"]["games"][0]
    x = g["quality_stats"]
    assert x["coverage"] == {
        "sampled": 4, "usable": 1, "excluded": 3,
        "critical": 1, "mean_loss_cp": 100.0}
    assert x["by_side"]["white"]["usable"] == 1
    assert x["by_side"]["black"]["usable"] == 0
    assert g["states"][2]["evaluation"]["white_mate_in"] == 2
    assert "quality_stats" not in live_board.snapshot(run)["duel"]["games"][0]
    pgn.write_bytes(pgn.read_bytes() + b"\n")
    again = live_board.snapshot(run, quality_overlay=overlay)["duel"]["games"][0]
    assert "quality_stats" not in again
    assert not any("evaluation" in s for s in again["states"])


def test_incomplete_or_conflicting_receipt_cannot_claim_verified_stats(tmp_path):
    run, _, report, _ = _verified_game(tmp_path)
    overlay = QualityOverlay(report)
    summary_file = run / "summary.json"
    for status, result in (("INCOMPLETE", "1-0"), ("COMPLETED", "0-1"),
                           ("RUNNING", "1-0")):
        summary_file.write_text(
            json.dumps({"status": status, "games": [
                {"status": status, "result": result}]}), encoding="utf-8")
        x = live_board.snapshot(run, quality_overlay=overlay)["duel"]["games"][0]
        assert "quality_stats" not in x
        assert "evaluation" in x["states"][1]


def test_report_depth_mismatch_does_not_mix_in_quantitative_mean(tmp_path):
    _, _, report, doc = _verified_game(tmp_path)
    doc["decisions"][1]["depth"] = 20
    report.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError):
        QualityOverlay(report)
