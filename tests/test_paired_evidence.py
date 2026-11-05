"""Issue #20: strength, results and phase-style are separate game-level evidence."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import chess
import chess.pgn
import pytest

from chesssim.paired_evidence import paired_evidence, _rate

SERIES = (
    ("A", "B", "1-0", ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5")),
    ("B", "A", "1/2-1/2", ("e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6")),
    ("A", "B", "0-1", ("d2d4", "d7d5", "c2c4", "e7e6", "b1c3", "g8f6")),
    ("B", "A", "1-0", ("d2d4", "d7d5", "g1f3", "g8f6", "c2c4", "e7e6")),
    ("A", "B", "1-0", ("c2c4", "e7e5", "b1c3", "g8f6", "g2g3", "d7d5")),
    ("B", "A", "0-1", ("e2e4", "c7c5", "g1f3", "d7d6", "d2d4", "c5d4")),
    ("A", "B", "*", ("e2e4", "e7e5")),
)


def _write_case(tmp_path: Path, *, duplicate=True, player_a="A"):
    pgn = tmp_path / "duel.pgn"
    games = []
    for white, black, result, sequence in SERIES:
        white = player_a if white == "A" else white
        black = player_a if black == "A" else black
        game = chess.pgn.Game()
        game.headers["White"] = white
        game.headers["Black"] = black
        game.headers["Result"] = result
        board = game.board()
        node = game
        for raw in sequence:
            move = chess.Move.from_uci(raw)
            assert move in board.legal_moves
            node = node.add_variation(move)
            board.push(move)
        games.append(game)
    if duplicate:
        copied = chess.pgn.read_game(__import__("io").StringIO(str(games[0])))
        copied.headers["Event"] = "Repeated with a different event"
        games.append(copied)
    pgn.write_text("\n\n".join(str(game) for game in games) + "\n", encoding="utf-8")
    rows = []
    for index, game in enumerate(games[:len(SERIES)]):
        board = game.board()
        for ply, move in enumerate(game.mainline_moves(), 1):
            actor = game.headers["White"] if board.turn else game.headers["Black"]
            if actor == player_a:
                cp = 10 + index * 10
            else:
                cp = 50 + index * 10
            rows.append({
                "source_game_index": index, "game_index": index,
                "ply": ply, "played": move.uci(),
                "fen": board.fen(), "player": actor,
                "color": "white" if board.turn else "black",
                "depth": 10, "cp_loss": cp,
                "mate_affected": index == 0 and ply == 1,
            })
            board.push(move)
    if duplicate:
        rows.append({
            "source_game_index": len(SERIES), "game_index": len(SERIES),
            "ply": 1, "played": "e2e4", "player": player_a,
            "color": "white", "fen": games[0].board().fen(),
            "depth": 10, "cp_loss": 900, "mate_affected": False,
        })
    quality = tmp_path / "quality.json"
    quality.write_text(json.dumps({
        "protocol":"independent-stockfish-postgame-v9.1",
        "inputs":{
            "pgn_sha256":sha256(pgn.read_bytes()).hexdigest(),
            "stockfish_sha256":"f" * 64,"depth":10,
        },
        "decisions":rows,
    }),encoding="utf-8")
    return pgn, quality, tmp_path / "paired.json", tmp_path / "paired.html"


def test_full_paired_json_and_script_free_html_with_game_level_uncertainty(tmp_path):
    pgn, quality, out, html = _write_case(tmp_path)
    report = paired_evidence(pgn, quality, out, html=html,
                             opening_plies=2, bootstrap=250, seed=123)
    assert out.exists() and html.exists()
    assert json.loads(out.read_text(encoding="utf-8")) == report
    assert report["schema"] == "chesssim-paired-style-strength-v1"
    assert report["accounting"]["raw_games"] == 8
    assert report["accounting"]["distinct_nonempty_games"] == 7
    assert report["accounting"]["duplicate_games_excluded"] == 1
    assert report["accounting"]["completed_distinct_games"] == 6
    assert report["accounting"]["incomplete_distinct_games"] == 1
    assert report["accounting"]["duplicate_quality_decisions_excluded"] == 1
    assert report["accounting"]["mate_affected_excluded_from_cp_mean"] == 1
    assert report["accounting"]["independent_quality_decisions"] == 38
    assert sorted(report["players"]) == ["A", "B"]
    assert report["players"]["A"]["completed_outcomes"] == {
        "win": 3, "draw": 1, "loss": 2
    }
    assert report["players"]["B"]["completed_outcomes"] == {
        "win": 2, "draw": 1, "loss": 3
    }
    assert report["players"]["A"]["incomplete_results_excluded"] == 1
    assert report["players"]["A"]["total_played_moves"] == 19
    assert report["players"]["A"]["strength"]["decisions_non_mate"] == 18
    assert report["players"]["A"]["strength"]["non_mate_sampled_move_fraction"] == pytest.approx(18/19)
    strength = report["players"]["A"]["strength"]["game_level"]
    assert strength["games"] == 7
    assert strength["bootstrap_ci95"] is not None
    pair = report["pairwise"][0]
    assert pair["a"] == "A" and pair["b"] == "B"
    assert pair["distinct_head_to_head_games"] == 7
    assert pair["completed_head_to_head_games"] == 6
    assert pair["strength_paired_by_game"]["games"] == 7
    assert pair["strength_paired_by_game"]["bootstrap_ci95"] is not None
    assert pair["strength_paired_by_game"]["mean"] < 0
    assert pair["phase_style_differences"]["opening"]["pawn_move"]["games"] == 7
    assert pair["phase_style_differences"]["middlegame_plus"]["check"]["games"] == 6
    assert pair["same_position_choices"]["exact_fen_positions"] >= 1
    assert pair["same_position_choices"]["unit"].startswith("descriptive_")
    assert len(pair["game_records"]) == 7
    assert "<script" not in html.read_text(encoding="utf-8")
    assert "Força ≠ identidade de estilo" in html.read_text(encoding="utf-8")
    assert "Cobertura CP sem mate" in html.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        paired_evidence(pgn, quality, out, html=html)


def test_clone_headers_cannot_increase_game_cluster_n(tmp_path):
    pgn, quality, out, _ = _write_case(tmp_path, duplicate=True)
    with_duplicate = paired_evidence(pgn, quality, out, bootstrap=200)
    assert with_duplicate["pairwise"][0]["distinct_head_to_head_games"] == 7
    assert with_duplicate["pairwise"][0]["strength_paired_by_game"]["games"] == 7
    assert with_duplicate["accounting"]["duplicate_quality_decisions_excluded"] == 1


def test_tiny_samples_do_not_get_pseudo_confidence_intervals():
    for values in ([], [10], [10, 100], [20, 40, 100, 200]):
        item = _rate(values, bootstrap=200, seed=3)
        assert item["games"] == len(values)
        assert item["bootstrap_ci95"] is None
        assert item["uncertainty"] != "DESCRIPTIVE_GAME_CLUSTER_BOOTSTRAP"
    many = _rate([1,2,3,4,5],bootstrap=200,seed=4)
    assert many["games"] == 5
    assert many["bootstrap_ci95"] is not None
    assert many == _rate([1,2,3,4,5],bootstrap=200,seed=4)


def test_mate_affected_moves_are_excluded_from_strength_not_style(tmp_path):
    pgn, quality, out, _ = _write_case(tmp_path, duplicate=False)
    report = paired_evidence(pgn, quality, out, bootstrap=200)
    assert report["accounting"]["mate_affected_excluded_from_cp_mean"] == 1
    assert report["players"]["A"]["strength"]["decisions_non_mate"] < sum(
        1 for r in json.loads(quality.read_text())["decisions"] if r["player"] == "A"
    )
    assert report["players"]["A"]["distinct_games"] == 7
    assert report["players"]["A"]["style"]["opening"]["games_with_moves"] == 7


@pytest.mark.parametrize("mutation,diagnostic", [
    ("wrong_pgn_hash","not derived from this exact PGN"),
    ("wrong_ply","does not match an eligible PGN"),
    ("wrong_color","provenance disagrees"),
    ("wrong_fen","provenance disagrees"),
    ("wrong_move","provenance disagrees"),
    ("wrong_player","provenance disagrees"),
    ("duplicate_decision","repeats a game/ply"),
    ("negative_cp","invalid centipawn loss"),
    ("nan_cp","invalid centipawn loss"),
    ("wrong_depth","differs from report depth"),
    ("foreign_game","non-existent PGN game"),
])
def test_invalid_quality_evidence_is_rejected_without_output(tmp_path,mutation,diagnostic):
    pgn, quality, out, _ = _write_case(tmp_path, duplicate=False)
    doc = json.loads(quality.read_text(encoding="utf-8"))
    row = doc["decisions"][0]
    if mutation == "wrong_pgn_hash": doc["inputs"]["pgn_sha256"] = "1" * 64
    elif mutation == "wrong_ply": row["ply"] = 99
    elif mutation == "wrong_color": row["color"] = "black"
    elif mutation == "wrong_fen": row["fen"] = "bad"
    elif mutation == "wrong_move": row["played"] = "d2d4"
    elif mutation == "wrong_player": row["player"] = "Z"
    elif mutation == "duplicate_decision": doc["decisions"].append(dict(row))
    elif mutation == "negative_cp": row["cp_loss"] = -1
    elif mutation == "nan_cp": row["cp_loss"] = float("nan")
    elif mutation == "wrong_depth": row["depth"] = 8
    elif mutation == "foreign_game": row["source_game_index"] = 919
    quality.write_text(json.dumps(doc),encoding="utf-8")
    with pytest.raises(ValueError,match=diagnostic):
        paired_evidence(pgn, quality, out, bootstrap=200)
    assert not out.exists()


def test_script_injection_in_pgn_headers_is_escaped_in_html(tmp_path):
    malicious = "<img src=x onerror=alert(1)>"
    pgn, quality, out, html = _write_case(tmp_path, player_a=malicious)
    report = paired_evidence(pgn, quality, out, html=html, bootstrap=200)
    assert malicious in report["players"]
    text = html.read_text(encoding="utf-8")
    assert malicious not in text
    assert "&lt;img src=x onerror=alert(1)&gt;" in text
    assert "<script" not in text


def test_no_completed_outcomes_if_pgn_result_is_asterisk(tmp_path):
    pgn, quality, out, _ = _write_case(tmp_path, duplicate=False)
    content = pgn.read_text(encoding="utf-8")
    # Replace all game results, preserving the original quality decisions.
    content = content.replace('[Result "1-0"]','[Result "*"]').replace(
        '[Result "0-1"]','[Result "*"]').replace(
        '[Result "1/2-1/2"]','[Result "*"]')
    # PGN movetext outcomes must be replaced too for parser consistency.
    content = content.replace(" 1-0", " *").replace(" 0-1", " *").replace(
        " 1/2-1/2", " *")
    pgn.write_text(content,encoding="utf-8")
    doc=json.loads(quality.read_text(encoding="utf-8"))
    doc["inputs"]["pgn_sha256"]=sha256(pgn.read_bytes()).hexdigest()
    quality.write_text(json.dumps(doc),encoding="utf-8")
    report=paired_evidence(pgn,quality,out,bootstrap=200)
    assert report["accounting"]["completed_distinct_games"] == 0
    assert report["pairwise"][0]["completed_head_to_head_games"] == 0
    assert report["players"]["A"]["completed_outcomes"] == {
        "win":0,"draw":0,"loss":0
    }


def test_empty_and_low_game_accounting(tmp_path):
    pgn, quality, out, _ = _write_case(tmp_path, duplicate=False)
    # truncate to a single completed game: manifest and quality stay consistent
    game = chess.pgn.read_game(__import__("io").StringIO(pgn.read_text(encoding="utf-8")))
    pgn.write_text(str(game)+"\n",encoding="utf-8")
    doc=json.loads(quality.read_text(encoding="utf-8"))
    doc["inputs"]["pgn_sha256"]=sha256(pgn.read_bytes()).hexdigest()
    doc["decisions"]=[r for r in doc["decisions"] if r["source_game_index"] == 0]
    quality.write_text(json.dumps(doc),encoding="utf-8")
    report=paired_evidence(pgn,quality,out,bootstrap=200,opening_plies=2)
    assert report["accounting"]["distinct_nonempty_games"]==1
    assert report["pairwise"][0]["strength_paired_by_game"]["games"]==1
    assert report["pairwise"][0]["strength_paired_by_game"]["bootstrap_ci95"] is None
    assert report["pairwise"][0]["phase_style_differences"]["middlegame_plus"]["capture"]["games"]==1


def test_html_and_json_destinations_must_be_distinct(tmp_path):
    pgn,quality,out,_=_write_case(tmp_path)
    with pytest.raises(ValueError,match="different paths"):
        paired_evidence(pgn,quality,out,html=out,bootstrap=200)
    assert not out.exists()



@pytest.mark.parametrize("mutation,diagnostic", [
    ("inconsistent_cp", "inconsistent centipawn loss"),
    ("inconsistent_raw_gap", "inconsistent raw centipawn gap"),
    ("nonfinite_engine", "non-finite engine evaluations"),
    ("mate_unclassified", "explicitly classify mate-affected"),
])
def test_report_validates_stockfish_score_consistency(tmp_path, mutation, diagnostic):
    pgn, quality, out, _ = _write_case(tmp_path, duplicate=False)
    doc = json.loads(quality.read_text(encoding="utf-8"))
    row = doc["decisions"][0]
    row.update({"best_eval_cp": 100, "played_eval_cp": 90, "raw_gap_cp": 10,
                "cp_loss": 10, "mate_affected": True})
    if mutation == "inconsistent_cp": row["cp_loss"] = 20
    elif mutation == "inconsistent_raw_gap": row["raw_gap_cp"] = 14
    elif mutation == "nonfinite_engine": row["best_eval_cp"] = "nan"
    elif mutation == "mate_unclassified": row.pop("mate_affected")
    quality.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match=diagnostic):
        paired_evidence(pgn, quality, out, bootstrap=200)
    assert not out.exists()


def test_report_rejects_out_of_range_bootstrap_seed_and_opening_window(tmp_path):
    pgn, quality, out, _ = _write_case(tmp_path)
    for keyword in ({"bootstrap": 10}, {"bootstrap": True},
                    {"seed": -1}, {"opening_plies": 500}):
        with pytest.raises(ValueError):
            paired_evidence(pgn, quality, out, **keyword)
    assert not out.exists()



@pytest.mark.parametrize("mutation,diagnostic", [
    ("protocol", "Unsupported quality analysis protocol"),
    ("missing_engine_hash", "valid Stockfish fingerprint"),
    ("bad_depth", "invalid depth"),
])
def test_unknown_quality_contract_is_rejected(tmp_path, mutation, diagnostic):
    pgn, quality, out, _ = _write_case(tmp_path)
    doc = json.loads(quality.read_text(encoding="utf-8"))
    if mutation == "protocol":
        doc["protocol"] = "unknown-quality-v999"
    elif mutation == "missing_engine_hash":
        doc["inputs"]["stockfish_sha256"] = None
    else:
        doc["inputs"]["depth"] = -5
    quality.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match=diagnostic):
        paired_evidence(pgn, quality, out, bootstrap=200)
    assert not out.exists()


def test_unknown_player_header_is_rejected_before_publishing(tmp_path):
    pgn, quality, out, _ = _write_case(tmp_path, duplicate=False)
    source = pgn.read_text(encoding="utf-8")
    source = source.replace('[White "A"]', '[White "?"]', 1)
    pgn.write_text(source, encoding="utf-8")
    doc = json.loads(quality.read_text(encoding="utf-8"))
    doc["inputs"]["pgn_sha256"] = sha256(pgn.read_bytes()).hexdigest()
    quality.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid player identities"):
        paired_evidence(pgn, quality, out, bootstrap=200)
    assert not out.exists()
