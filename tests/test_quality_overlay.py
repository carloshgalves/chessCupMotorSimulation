"""#24: sampled Stockfish presentation evidence is tied to the exact final PGN."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import chess
import chess.pgn
import pytest

from chesssim import live_board
from chesssim.quality_overlay import QualityOverlay, PROTOCOL


def _fixture(tmp_path: Path):
    root = tmp_path / "duel"
    root.mkdir()
    game = chess.pgn.Game()
    game.headers["White"] = "White"
    game.headers["Black"] = "Black"
    node = game
    for uci in ("e2e4", "e7e5", "g1f3", "b8c6"):
        node = node.add_variation(chess.Move.from_uci(uci))
    pgn = root / "jogo_1.pgn"
    pgn.write_text(str(game) + "\n", encoding="utf-8")
    (root / "summary.json").write_text('{"status":"COMPLETED","games":[]}', encoding="utf-8")
    states = live_board._states(game)
    report = tmp_path / "quality.json"
    rows = [
        {"game_index": 0, "ply": 1, "fen": states[0]["fen"],
         "played": "e2e4", "played_eval_cp": 92, "played_mate_in": None,
         "cp_loss": 120, "depth": 18},
        {"game_index": 0, "ply": 2, "fen": states[1]["fen"],
         "played": "e7e5", "played_eval_cp": -45, "played_mate_in": -2,
         "cp_loss": 260, "depth": 18},
    ]
    doc = {"protocol": PROTOCOL,
           "inputs": {"pgn_sha256": sha256(pgn.read_bytes()).hexdigest(),
                      "stockfish_sha256": "a" * 64,
                      "engine_uci_id": {"name": "Stockfish test"},
                      "depth": 18}, "decisions": rows}
    report.write_text(json.dumps(doc), encoding="utf-8")
    return root, pgn, report, doc


def test_overlay_matches_only_exact_final_game_and_preserves_unknown_positions(tmp_path):
    root, pgn, report, _ = _fixture(tmp_path)
    overlay = QualityOverlay(report)
    response = live_board.snapshot(root, quality_overlay=overlay)
    states = response["duel"]["games"][0]["states"]
    assert states[0].get("evaluation") is None
    assert states[1]["evaluation"] == {
        "white_cp": 92, "white_mate_in": None, "depth": 18,
        "engine": "Stockfish test", "loss_cp": 120, "critical": "mistake",
        "source": "stockfish-sampled-forced-move"}
    assert states[2]["evaluation"]["white_cp"] == 45
    assert states[2]["evaluation"]["white_mate_in"] == 2
    assert states[2]["evaluation"]["critical"] == "blunder"
    assert states[3].get("evaluation") is None
    assert states[4].get("evaluation") is None
    assert "evaluation" not in live_board.snapshot(root)["duel"]["games"][0]["states"][1]
    pgn.write_bytes(pgn.read_bytes() + b"\n")
    assert not any("evaluation" in s for s in
                   live_board.snapshot(root, quality_overlay=overlay)["duel"]["games"][0]["states"])


def test_overlay_rejects_foreign_fen_and_move_without_partial_evidence(tmp_path):
    root, _, report, doc = _fixture(tmp_path)
    doc["decisions"][1]["played"] = "d7d5"
    report.write_text(json.dumps(doc), encoding="utf-8")
    states = live_board.snapshot(root, quality_overlay=QualityOverlay(report))["duel"]["games"][0]["states"]
    assert not any("evaluation" in state for state in states)
    doc["decisions"][1]["played"] = "e7e5"
    doc["decisions"][1]["fen"] = doc["decisions"][0]["fen"]
    report.write_text(json.dumps(doc), encoding="utf-8")
    states = live_board.snapshot(root, quality_overlay=QualityOverlay(report))["duel"]["games"][0]["states"]
    assert not any("evaluation" in state for state in states)


@pytest.mark.parametrize("change", [
    lambda d: d.update(protocol="unknown"),
    lambda d: d["inputs"].update(pgn_sha256="wrong"),
    lambda d: d["inputs"].update(depth=True),
    lambda d: d["inputs"].update(stockfish_sha256=None),
    lambda d: d["inputs"].update(engine_uci_id={}),
    lambda d: d["decisions"][0].update(game_index=False),
    lambda d: d["decisions"][0].update(ply=0),
    lambda d: d["decisions"][0].update(played_eval_cp=float("nan")),
    lambda d: d["decisions"][0].update(cp_loss=-1),
    lambda d: d["decisions"][0].update(depth="18"),
    lambda d: d["decisions"][0].update(game_index=1),
    lambda d: d["decisions"].append(d["decisions"][0].copy()),
])
def test_report_fails_closed_on_invalid_input(tmp_path, change):
    _, _, report, doc = _fixture(tmp_path)
    change(doc)
    report.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError):
        QualityOverlay(report)


def test_symlinked_report_not_accepted(tmp_path):
    _, _, report, _ = _fixture(tmp_path)
    link = tmp_path / "link.json"
    link.symlink_to(report)
    with pytest.raises(ValueError):
        QualityOverlay(link)


def test_partial_pgn_is_never_labeled_as_analyzed(tmp_path):
    root, pgn, report, _ = _fixture(tmp_path)
    partial = root / "jogo_1.partial.pgn"
    pgn.rename(partial)
    states = live_board.snapshot(root, quality_overlay=QualityOverlay(report))["duel"]["games"][0]["states"]
    assert not any("evaluation" in s for s in states)


def test_http_surface_remains_read_only(tmp_path):
    root, _, report, _ = _fixture(tmp_path)
    server = live_board.create_server(root, port=0, quality_report=report)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/state"
        with urlopen(url, timeout=3) as response:
            assert response.headers["Cache-Control"] == "no-store"
            game = json.load(response)["duel"]["games"][0]
            assert game["states"][1]["evaluation"]["engine"] == "Stockfish test"
        with pytest.raises(HTTPError) as err:
            urlopen(Request(url, data=b"{}", method="POST"), timeout=3)
        assert err.value.code == 405
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
