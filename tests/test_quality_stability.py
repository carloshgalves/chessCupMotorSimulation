"""Deterministic regression gates for depth stability and critical refinements."""
from __future__ import annotations

from copy import deepcopy
import json

import chess
import chess.engine
import pytest

from chesssim.quality_audit import analyze_games_v91, compare_quality_reports
from chesssim.quality_stability import compare_depth_evaluations, decision_class


def _decision(*, player, color, ply, played, fen, loss, depth, best="g1f3", game=0):
    return {
        "source_game_index": game,
        "game_index": game,
        "player": player, "color": color,
        "fullmove": (ply + 1) // 2, "ply": ply,
        "played": played, "fen": fen, "cp_loss": loss,
        "depth": depth, "raw_gap_cp": loss,
        "best_move": best, "best_eval_cp": 100,
        "played_eval_cp": 100-loss, "best_mate_in": None,
        "played_mate_in": None, "mate_affected": False,
        "search_disagreement": False,
        "best_line_uci": [best] if best else [],
        "played_line_uci": [played],
    }


@pytest.fixture
def reports(tmp_path):
    board = chess.Board()
    moves = ["e2e4", "e7e5", "g1f3"]
    states = []
    for move in moves:
        states.append(board.fen())
        board.push_uci(move)
    alpha = [
        ("Alpha", "white", 1, "e2e4", states[0]),
        ("Beta", "black", 2, "e7e5", states[1]),
        ("Alpha", "white", 3, "g1f3", states[2]),
    ]
    losses = [
        [20, 150, 15],  # 8
        [120, 200, 30],  # 12, crossing mistake threshold at an intermediate depth
        [30, 300, 15],  # 20, terminal classification change for second move only
    ]
    output = []
    for depth, values in zip((8,12,20), losses):
        doc = {
            "protocol":"independent-stockfish-postgame-v9.1",
            "inputs":{
                "pgn_sha256":"abc123","stockfish_sha256":"def456",
                "depth":depth,
                "engine_uci_id":{"name":"Deterministic UCI Fixture"},
                "engine_configured_options":{"Threads":1,"Hash":64},
                "mate_mapping_cp":1000,
                "analysis_limit":{"kind":"depth","value":depth},
            },
            "games":[{"game_index":0,"source_game_index":0,
                      "duplicate_mainline":False}],
            "decisions":[
                _decision(player=row[0],color=row[1],ply=row[2],
                          played=row[3],fen=row[4],loss=value,depth=depth,
                          best="d2d4" if i==0 and depth==12 else "g1f3")
                for i,(row,value) in enumerate(zip(alpha,values))
            ],
        }
        path=tmp_path / f"quality-{depth}.json"
        path.write_text(json.dumps(doc),encoding="utf-8")
        output.append(path)
    return output, tmp_path / "stability.json"


def test_three_depths_retained_in_order_without_overwriting_originals(reports):
    paths, out = reports
    original = {p:p.read_bytes() for p in paths}
    report = compare_quality_reports(list(reversed(paths)),out)
    assert report["protocol"] == "depth-quality-comparison-v9.2"
    assert report["depths"] == [8,12,20]
    assert report["aligned_decisions"] == 3
    assert report["all_aligned_decisions"] == 3
    assert report["diagnostic_duplicate_aligned"] == 0
    assert report["classification_changes"] == 1
    assert report["classification_changes_at_any_step"] == 2
    assert report["classification_change_rate"] == 1/3
    assert report["classification_change_rate_any_step"] == 2/3
    assert report["best_move_changes_at_any_step"] == 1
    assert report["best_move_change_status_counts"] == {
        "changed": 1, "unchanged": 2, "unknown": 0
    }
    assert report["best_move_change_rate_known"] == pytest.approx(1/3)
    assert report["unmatched_or_ambiguous_by_report"] == [0,0,0]
    assert report["mean_cp_loss_range"] == pytest.approx((100+150+15)/3)
    assert report["median_cp_loss_range"] == 100
    assert {entry["depth"] for entry in report["inputs"]} == {8,12,20}
    assert [r["key"]["ply"] for r in report["aligned_history"]] == [1,2,3]
    first = report["aligned_history"][0]
    assert first["loss_by_depth"] == {"8":20,"12":120,"20":30}
    assert first["classification_changed"] is False
    assert first["classification_changed_at_any_step"] is True
    assert [e["classification"] for e in first["evaluations"]] == [
        "under_100cp","mistake_100_249cp","under_100cp"
    ]
    assert [d["cp_loss_delta"] for d in first["consecutive_changes"]] == [100,-90]
    assert all("decision_index" in v and "best_line_uci" in v
               for row in report["aligned_history"] for v in row["evaluations"])
    assert report["largest_changes"][0]["key"]["ply"] == 2
    assert all(p.read_bytes()==b for p,b in original.items())
    assert json.loads(out.read_text(encoding="utf-8")) == report
    with pytest.raises(FileExistsError):
        compare_quality_reports(paths,out)


def test_mismatched_fen_does_not_fabricate_aligned_stability(reports):
    paths, out = reports
    doc=json.loads(paths[1].read_text(encoding="utf-8"))
    doc["decisions"][0]["fen"]=chess.Board().fen().replace(" w ", " b ")
    paths[1].write_text(json.dumps(doc),encoding="utf-8")
    report=compare_quality_reports(paths,out)
    assert report["aligned_decisions"] == 2
    assert report["unmatched_or_ambiguous_by_report"] == [1,1,1]
    assert all(row["key"]["ply"] != 1 for row in report["aligned_history"])


def test_zero_overlap_reports_unknown_stability_not_zero_change_rate(reports):
    paths,out=reports
    for path in paths[1:]:
        doc=json.loads(path.read_text(encoding="utf-8"))
        for d in doc["decisions"]:
            d["source_game_index"]=int(path.stem.rsplit("-",1)[-1])
        path.write_text(json.dumps(doc), encoding="utf-8")
    report=compare_quality_reports(paths,out)
    assert report["status"]=="NO_ALIGNED_INDEPENDENT_DECISIONS"
    assert report["aligned_decisions"] == 0
    assert report["classification_change_rate"] is None
    assert report["mean_cp_loss_range"] is None
    assert report["median_cp_loss_range"] is None


def test_ambiguous_duplicate_key_is_never_chosen_arbitrarily(reports):
    paths, out=reports
    doc=json.loads(paths[1].read_text(encoding="utf-8"))
    doc["decisions"].append(deepcopy(doc["decisions"][0]))
    paths[1].write_text(json.dumps(doc),encoding="utf-8")
    result=compare_quality_reports(paths,out)
    assert result["aligned_decisions"] == 2
    assert result["legacy_collision_counts"] == [0,1,0]
    assert result["unmatched_or_ambiguous_by_report"] == [1,2,1]


def test_duplicate_game_diagnostic_remains_outside_independent_stability(reports):
    paths, out=reports
    for path in paths:
        doc=json.loads(path.read_text(encoding="utf-8"))
        duplicate=deepcopy(doc["decisions"][0])
        duplicate["source_game_index"]=1
        duplicate["game_index"]=1
        doc["decisions"].append(duplicate)
        doc["games"].append({"game_index":1,"source_game_index":1,
                             "duplicate_mainline":True,"diagnostic_only":True})
        path.write_text(json.dumps(doc),encoding="utf-8")
    result=compare_quality_reports(paths,out)
    assert result["aligned_decisions"]==3
    assert result["diagnostic_duplicate_aligned"]==1
    assert result["all_aligned_decisions"]==4
    assert len(result["aligned_history"])==3
    assert len(result["diagnostic_duplicate_history"])==1
    assert result["unmatched_or_ambiguous_by_report"]==[0,0,0]


@pytest.mark.parametrize("mutation,error",[
    ("same_depth","distinct analysis depths"),
    ("engine","different Stockfish"),
    ("pgn","different PGNs"),
    ("options","incompatible engine_configured_options"),
    ("uci_identity","incompatible engine_uci_id"),
    ("mate_mapping","incompatible mate_mapping_cp"),
    ("limit","inconsistent search limit"),
    ("invalid_cp","finite and non-negative"),
    ("missing_cp","Missing or invalid centipawn loss"),
    ("row_depth","contradicts report depth"),
])
def test_incompatible_sources_and_corrupted_rows_are_rejected(reports,mutation,error):
    paths,out=reports
    doc=json.loads(paths[1].read_text(encoding="utf-8"))
    if mutation=="same_depth": doc["inputs"]["depth"]=8;doc["inputs"]["analysis_limit"]["value"]=8
    elif mutation=="engine":doc["inputs"]["stockfish_sha256"]="different"
    elif mutation=="pgn":doc["inputs"]["pgn_sha256"]="different"
    elif mutation=="options":doc["inputs"]["engine_configured_options"]["Hash"]=128
    elif mutation=="uci_identity":doc["inputs"]["engine_uci_id"]["name"]="Different Judge"
    elif mutation=="mate_mapping":doc["inputs"]["mate_mapping_cp"]=100000
    elif mutation=="limit":doc["inputs"]["analysis_limit"]["value"]=13
    elif mutation=="invalid_cp":doc["decisions"][0]["cp_loss"]=-1
    elif mutation=="missing_cp":doc["decisions"][0].pop("cp_loss")
    elif mutation=="row_depth":doc["decisions"][0]["depth"]=20
    paths[1].write_text(json.dumps(doc),encoding="utf-8")
    with pytest.raises(ValueError,match=error):
        compare_quality_reports(paths,out)
    assert not out.exists()


def test_missing_provenance_on_one_side_rejected_and_legacy_all_missing_allowed(reports):
    paths,out=reports
    doc=json.loads(paths[1].read_text(encoding="utf-8"))
    doc["inputs"].pop("engine_configured_options")
    paths[1].write_text(json.dumps(doc),encoding="utf-8")
    with pytest.raises(ValueError,match="incompatible engine_configured_options"):
        compare_quality_reports(paths,out)
    for path in paths:
        data=json.loads(path.read_text(encoding="utf-8"))
        for key in ("engine_configured_options","engine_uci_id","mate_mapping_cp"):
            data["inputs"].pop(key,None)
        for row in data["decisions"]:
            row.pop("source_game_index",None)
            row.pop("game_index",None)
            row.pop("fen",None)
            row.pop("ply",None)
        path.write_text(json.dumps(data),encoding="utf-8")
    result=compare_quality_reports(paths,out)
    assert result["aligned_decisions"]==3
    assert "reduced alignment assurance" in result["warnings"][2]


def test_decision_class_boundaries_and_unavailable_best_move():
    assert [decision_class(n) for n in (0,99,100,249,250)]==[
        "under_100cp","under_100cp","mistake_100_249cp",
        "mistake_100_249cp","blunder_250plus_cp"
    ]
    with pytest.raises(ValueError):
        decision_class(float("nan"))
    a={"depth":8,"cp_loss":95,"best_move":None}
    b={"depth":16,"cp_loss":115,"best_move":"e2e4"}
    comparison=compare_depth_evaluations(a,b)
    assert comparison["best_move_changed"] is None
    assert comparison["classification_changed"] is True
    assert comparison["cp_loss_delta"]==20


class DepthSensitiveJudge:
    def __init__(self):
        self.id={"name":"Controlled Depth Judge"}
        self.options={"Threads":object(),"Hash":object()}
        self.configured={}
        self.calls=[]
    def __enter__(self):return self
    def __exit__(self,*args):return None
    def configure(self,settings):self.configured=settings.copy()
    def analyse(self,board,limit,*,root_moves=None,info=None):
        assert info == chess.engine.INFO_ALL
        forced=root_moves is not None
        chosen=(root_moves[0] if forced else
                sorted(board.legal_moves,key=lambda x:x.uci())[0])
        self.calls.append((limit.depth,forced))
        score=(300 if not forced else -50) if limit.depth==10 else (
            80 if not forced else 60
        )
        return {
            "score":chess.engine.PovScore(chess.engine.Cp(score),board.turn),
            "pv":[chosen],
        }


def _pgames(path,*,duplicate=False):
    import chess.pgn
    series=[
        ["e2e4","e7e5","g1f3","b8c6"],
        ["d2d4","d7d5","c2c4","e7e6"],
    ]
    with path.open("w",encoding="utf-8") as out:
        for seq in series+([series[0]] if duplicate else []):
            game=chess.pgn.Game()
            game.headers["White"]="Alpha"
            game.headers["Black"]="Beta"
            node=game
            for move in seq:
                node=node.add_variation(chess.Move.from_uci(move))
            print(game,file=out,end="\n\n")


def test_refinement_preserves_baseline_and_includes_critical_history(monkeypatch,tmp_path):
    pgn=tmp_path/"games.pgn";exe=tmp_path/"engine";out=tmp_path/"quality.json"
    _pgames(pgn)
    exe.write_bytes(b"mock-binary")
    judge=DepthSensitiveJudge()
    monkeypatch.setattr(chess.engine.SimpleEngine,"popen_uci",lambda *a,**kw:judge)
    report=analyze_games_v91(
        pgn,exe,out,depth=5,refine_depth=10,refine_threshold=10,
        refine_max_positions=3,max_positions=8,min_fullmove=1,pv_plies=2
    )
    assert len(report["decisions"])==8
    assert report["inputs"]["depth"]==5
    assert report["summary"]["Alpha"]["mean_centipawn_loss"]==20
    assert report["summary"]["Beta"]["mean_centipawn_loss"]==20
    assert report["refinement"]["selected"]==3
    assert report["refinement"]["independent_selected"]==3
    assert report["refinement"]["diagnostic_duplicates_selected"]==0
    assert report["refinement"]["classification_changes_independent"]==3
    refined=[r for r in report["decisions"] if "refined" in r]
    assert len(refined)==3
    assert all(r["cp_loss"]==20 and r["refined"]["cp_loss"]==350
               for r in refined)
    assert all(r["stability"]["classification_from"]=="under_100cp" and
               r["stability"]["classification_to"]=="blunder_250plus_cp" and
               r["stability"]["cp_loss_delta"]==330 for r in refined)
    assert {d for d,_ in judge.calls}=={5,10}
    assert len([1 for depth,_ in judge.calls if depth==10])==6
    assert json.loads(out.read_text(encoding="utf-8"))==report


def test_refinement_never_prioritizes_duplicate_trajectory(monkeypatch,tmp_path):
    pgn=tmp_path/"games.pgn";exe=tmp_path/"engine";out=tmp_path/"quality.json"
    _pgames(pgn,duplicate=True)
    exe.write_bytes(b"mock-binary")
    monkeypatch.setattr(chess.engine.SimpleEngine,"popen_uci",
                        lambda *a,**kw:DepthSensitiveJudge())
    report=analyze_games_v91(
        pgn,exe,out,depth=5,refine_depth=10,refine_threshold=0,
        refine_max_positions=9,max_positions=12,min_fullmove=1,pv_plies=2,
        dedupe_games=False,
    )
    # Exact independent first; duplicate material may only use spare capacity.
    assert report["refinement"]["independent_selected"]==8
    assert report["refinement"]["diagnostic_duplicates_selected"]==1
    assert report["refinement"]["selected"]==9
    assert report["reconciliation"]["independent_decisions"]==8
    assert report["summary"]["Alpha"]["decisions_analyzed"]==4
    assert report["summary"]["Beta"]["decisions_analyzed"]==4



def test_missing_principal_variation_marks_best_move_stability_unknown(reports):
    paths, out = reports
    middle = json.loads(paths[1].read_text(encoding="utf-8"))
    middle["decisions"][2]["best_move"] = None
    middle["decisions"][2]["best_line_uci"] = []
    paths[1].write_text(json.dumps(middle), encoding="utf-8")
    result = compare_quality_reports(paths, out)
    assert result["aligned_decisions"] == 3
    assert result["best_move_change_status_counts"] == {
        "changed": 1, "unchanged": 1, "unknown": 1
    }
    assert result["best_move_change_rate_known"] == pytest.approx(1/2)
    third = next(row for row in result["aligned_history"]
                 if row["key"]["ply"] == 3)
    assert third["best_move_change_status"] == "UNKNOWN"
    assert third["best_move_changed_at_any_step"] is False
    assert third["consecutive_changes"][0]["best_move_changed"] is None


def test_all_principal_variations_unavailable_gives_no_best_move_rate(reports):
    paths, out = reports
    for path in paths:
        doc = json.loads(path.read_text(encoding="utf-8"))
        for row in doc["decisions"]:
            row["best_move"] = None
            row["best_line_uci"] = []
        path.write_text(json.dumps(doc), encoding="utf-8")
    result = compare_quality_reports(paths, out)
    assert result["best_move_change_status_counts"]["unknown"] == 3
    assert result["best_move_change_rate_known"] is None
    assert result["best_move_changes_at_any_step"] == 0
    assert result["aligned_decisions"] == 3



@pytest.mark.parametrize("field,wrong,error", [
    ("cp_loss", 70, "inconsistent centipawn loss"),
    ("raw_gap_cp", 40, "inconsistent raw centipawn gap"),
    ("best_eval_cp", "unavailable", "Non-finite centipawn evaluations"),
])
def test_invalid_engine_evaluation_triplet_is_not_published(reports, field, wrong, error):
    paths, out = reports
    doc = json.loads(paths[1].read_text(encoding="utf-8"))
    doc["decisions"][0][field] = wrong
    paths[1].write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        compare_quality_reports(paths, out)
    assert not out.exists()


def test_negative_unclipped_gap_is_valid_when_cp_loss_is_zero(reports):
    paths, out = reports
    for path in paths:
        doc = json.loads(path.read_text(encoding="utf-8"))
        row = doc["decisions"][2]
        row["best_eval_cp"] = -20
        row["played_eval_cp"] = 10
        row["raw_gap_cp"] = -30
        row["cp_loss"] = 0
        path.write_text(json.dumps(doc), encoding="utf-8")
    result = compare_quality_reports(paths, out)
    assert result["aligned_decisions"] == 3
    assert result["aligned_history"][2]["loss_by_depth"] == {
        "8": 0, "12": 0, "20": 0
    }
    assert all(e["raw_gap_cp"] == -30 for e in
               result["aligned_history"][2]["evaluations"])
