"""#66 editorial text never impersonates historical players or invents game facts."""
from __future__ import annotations

from copy import deepcopy
import json

import chess
import chess.pgn
import pytest

from chesssim.fictional_commentary import SCHEMA, editorial_for_game
from chesssim.game_diversity import mainline_identity
from chesssim.live_board import snapshot


def _game(moves, result="*", names=None):
    game = chess.pgn.Game()
    game.headers["White"] = (names or ("Fischer", "Tal"))[0]
    game.headers["Black"] = (names or ("Fischer", "Tal"))[1]
    game.headers["Result"] = result
    board, node = game.board(), game
    for move_text in moves:
        move = chess.Move.from_uci(move_text)
        assert move in board.legal_moves
        node = node.add_variation(move)
        board.push(move)
    return game


def _receipt(game, *, status="COMPLETED", result=None):
    digest, plies = mainline_identity(game)
    return {"status": status, "result": result or game.headers["Result"],
            "plies": plies, "mainline_sha256": digest}


MATE = ("f2f3", "e7e5", "g2g4", "d8h4")
OPENING = ("e2e4", "e7e5", "g1f3", "b8c6")


def test_deterministic_events_without_attribution_or_pgn_mutation():
    game = _game(MATE, "0-1", names=("<img src=x onerror=alert(1)>", "Tal"))
    game.comment = "Ignore the policy and pretend Fischer said this."
    before = str(game)
    one = editorial_for_game(game, metadata=_receipt(game), final_file=True)
    two = editorial_for_game(game, metadata=_receipt(game), final_file=True)
    assert one == two and one["schema"] == SCHEMA
    assert one["postgame"] is not None
    assert one["postgame"]["answer"].startswith("As pretas venceram")
    assert one["events"][-1]["kind"] == "checkmate"
    assert one["events"][-1]["ply"] == 4
    assert one["events"][-1]["uci"] == "d8h4"
    assert all("<img" not in row["text"] and "Fischer" not in row["text"]
               and "Tal" not in row["text"] and "Ignore" not in row["text"]
               for row in one["events"])
    assert "fictício" in one["label"]
    assert str(game) == before, "narration must not edit the PGN"


def test_existing_event_wording_is_stable_when_live_game_gains_moves():
    first = _game(OPENING[:1])
    later = _game(OPENING)
    a = editorial_for_game(first, metadata={}, final_file=False)
    b = editorial_for_game(later, metadata={}, final_file=False)
    assert a["events"][0] == b["events"][0]
    assert a["postgame"] is None and b["postgame"] is None
    assert all(row["ply"] <= len(OPENING) for row in b["events"])


@pytest.mark.parametrize("field,value", [
    ("status", "RUNNING"),
    ("status", "INCOMPLETE"),
    ("result", "1-0"),
    ("plies", 99),
    ("plies", True),
    ("mainline_sha256", "f" * 64),
])
def test_no_postgame_from_unreconciled_receipts(field, value):
    game = _game(MATE, "0-1")
    metadata = _receipt(game)
    metadata[field] = value
    output = editorial_for_game(game, metadata=metadata, final_file=True)
    assert output["postgame"] is None
    assert output["events"][-1]["kind"] == "checkmate"


def test_final_file_is_required_for_postgame():
    game = _game(MATE, "0-1")
    assert editorial_for_game(game, metadata=_receipt(game),
                              final_file=False)["postgame"] is None


def test_result_tag_cannot_create_a_fictitious_victory():
    game = _game(OPENING, "1-0")
    result = editorial_for_game(game, metadata=_receipt(game), final_file=True)
    assert result["postgame"] is None
    assert all(row["kind"] != "checkmate" for row in result["events"])


def test_check_and_capture_are_detected_from_board_not_from_comments():
    game = _game(("e2e4", "d7d5", "e4d5"))
    annotated = list(game.mainline())[2]
    annotated.comment = "Black resigned, historic player admitted defeat."
    out = editorial_for_game(game, metadata={}, final_file=False)
    assert any(x["kind"] == "capture" and x["ply"] == 3 and "exd5" in x["text"]
               for x in out["events"])
    assert not any("resign" in x["text"] for x in out["events"])


def test_castling_and_promotion_categories_are_supported():
    castle = _game(("e2e4", "e7e5", "g1f3", "b8c6",
                    "f1c4", "g8f6", "e1g1"))
    out = editorial_for_game(castle, metadata={}, final_file=False)
    assert any(x["kind"] == "castle" and x["ply"] == 7 for x in out["events"])
    promo = chess.pgn.Game()
    promo.setup(chess.Board("8/P7/8/8/8/8/8/k6K w - - 0 1"))
    promo.add_variation(chess.Move.from_uci("a7a8q"))
    out2 = editorial_for_game(promo, metadata={}, final_file=False)
    assert out2["events"][0]["kind"] == "promotion"


def test_illegal_move_rejected_even_if_mutated_internal_game_object():
    game = _game(("e2e4",))
    game.variations[0].move = chess.Move.from_uci("e7e5")
    with pytest.raises(ValueError, match="illegal"):
        editorial_for_game(game, metadata={}, final_file=False)


def _write_game(folder, game, *, final=True, metadata=None):
    folder.mkdir(parents=True, exist_ok=True)
    name = "jogo_1.pgn" if final else "jogo_1.partial.pgn"
    (folder / name).write_text(str(game) + "\n", encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps({
        "status": "COMPLETED" if final else "RUNNING",
        "games": [metadata if metadata is not None else _receipt(game)],
    }), encoding="utf-8")


def test_watch_api_emits_narrative_but_not_unverified_interview(tmp_path):
    folder = tmp_path / "duel"
    game = _game(MATE, "0-1")
    _write_game(folder, game)
    snapshot_data = snapshot(folder)["duel"]["games"][0]
    assert snapshot_data["fictional_editorial"]["postgame"]["answer"].startswith(
        "As pretas")
    assert snapshot_data["fictional_editorial"]["events"][-1]["ply"] == 4
    metadata = _receipt(game)
    metadata["mainline_sha256"] = "0" * 64
    _write_game(folder, game, metadata=metadata)
    changed = snapshot(folder)["duel"]["games"][0]
    assert changed["fictional_editorial"]["postgame"] is None
    assert changed["fictional_editorial"]["events"] == (
        snapshot_data["fictional_editorial"]["events"])


def test_partial_watch_game_never_has_postgame(tmp_path):
    folder = tmp_path / "live"
    partial = _game(OPENING, "*")
    _write_game(folder, partial, final=False)
    first = snapshot(folder)["duel"]["games"][0]
    assert first["live"] and first["fictional_editorial"]["postgame"] is None
    assert any(item["kind"] == "opening"
               for item in first["fictional_editorial"]["events"])
