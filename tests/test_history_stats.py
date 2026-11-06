"""Canonical read-only player statistics: games != duels != tournament entries."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import random
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import chess
import chess.pgn
import pytest

from chesssim import arena, history_stats, tournament
from chesssim.catalog import find_player
from chesssim.matches import mainline_identity
from chesssim.protocol import OFFICIAL


SESSION = "session-20261106T120000Z-123456789abc"
MATE = ("f2f3", "e7e5", "g2g4", "d8h4")
QUIET = ("g1f3", "g8f6", "f3g1", "f6g8") * 2


def game(white="fischer", black="tal", *, result="0-1", incomplete=False):
    obj = chess.pgn.Game()
    obj.headers["White"] = find_player(white).name
    obj.headers["Black"] = find_player(black).name
    obj.headers["Event"] = "Simulação local"
    obj.headers["Date"] = "2026.11.06"
    seq = (QUIET if result == "1/2-1/2" else MATE)
    if incomplete:
        seq = seq[:2]
    node = obj
    board = obj.board()
    for code in seq:
        mv = chess.Move.from_uci(code)
        assert mv in board.legal_moves
        node = node.add_variation(mv)
        board.push(mv)
    obj.headers["Result"] = "*" if incomplete else result
    obj.headers["Termination"] = "OperatorStop" if incomplete else (
        "THREEFOLD_REPETITION" if result == "1/2-1/2" else "CHECKMATE")
    digest, plies = mainline_identity(obj)
    entry = {"white": white, "black": black,
             "result": "*" if incomplete else result,
             "status": "INCOMPLETE" if incomplete else "COMPLETED",
             "plies": plies, "mainline_sha256": digest,
             "termination": obj.headers["Termination"]}
    return obj, entry


def arena_session(workspace: Path, *, session=SESSION, specs=None,
                  attempt="20261106T120000Z-test", reverse=True):
    specs = specs if specs is not None else [
        ("fischer", "tal", "0-1", False),
        ("tal", "fischer", "0-1", False),
    ]
    root = workspace / session
    root.mkdir(exist_ok=True)
    meta = {"schema": "chesssim-arena-session-v1", "id":session,
            "created_utc":"2026-11-06T12:00:00Z",
            "config":{"white":"fischer","black":"tal","reverse":reverse},
            "status":"COMPLETED"}
    (root / "session.json").write_text(json.dumps(meta),encoding="utf-8")
    folder = root / "duels/fischer-vs-tal" / attempt
    folder.mkdir(parents=True)
    records = []
    for index,(white,black,result,incomplete) in enumerate(specs,1):
        pgn, record = game(white,black,result=result,incomplete=incomplete)
        filename=f"jogo_{index}.pgn"
        (folder/filename).write_text(str(pgn)+"\n",encoding="utf-8")
        records.append(record)
    (folder/"summary.json").write_text(json.dumps({
        "status":"INCOMPLETE" if any(row["status"]=="INCOMPLETE" for row in records)
        else "COMPLETED","players":["fischer","tal"],"games":records
    }),encoding="utf-8")
    return folder


def tournament_run(workspace: Path, *, name="copa-2026", players=None):
    root = workspace / name
    root.mkdir(parents=True)
    seed = 2026
    players = players or ["fischer","tal"]
    shuffled = players.copy()
    random.Random(seed).shuffle(shuffled)
    match = tournament._match(1,1,*shuffled)
    resources = {"engine_sha256":"e"*64,
                 "profile_sha256":{p:p[0]*64 for p in players},
                 "book_sha256":{},"books_by_player":{p:[] for p in players}}
    state = {"schema":tournament.SCHEMA,"created_utc":"2026-11-06T10:00:00Z",
             "preset":"official","clock":[600,2],"resources":resources,
             "status":"RUNNING","participants":players,"seed":seed,
             "initial_bracket":shuffled,"max_tiebreak_pairs":3,
             "rounds":[{"number":1,"matches":[match]}],"champion":None}
    (root/"tournament.json").write_text(json.dumps(state),encoding="utf-8")
    return root,state,match


def register_tournament(root, state, match, *, attempt="20261106T120000Z-demo",
                        gamespecs=None):
    a,b=match["players"]
    gamespecs=gamespecs or [(a,b,"0-1",False),(b,a,"1/2-1/2",False)]
    folder=root / "matches" / match["id"] / "duels" / f"{a}-vs-{b}" / attempt
    folder.mkdir(parents=True)
    games=[]
    records=[]
    for w,k,res,incomplete in gamespecs:
        pgn,record=game(w,k,result=res,incomplete=incomplete)
        pgn.headers["EngineSHA256"]=state["resources"]["engine_sha256"]
        pgn.headers["WhiteProfileSHA256"]=state["resources"]["profile_sha256"][w]
        pgn.headers["BlackProfileSHA256"]=state["resources"]["profile_sha256"][k]
        pgn.headers["TimeControl"]="600+2"
        games.append(pgn)
        records.append(record)
    summary={"status":"COMPLETED","players":[a,b],"games":records,
             "file_hashes":{"exe":state["resources"]["engine_sha256"],
                            a:state["resources"]["profile_sha256"][a],
                            b:state["resources"]["profile_sha256"][b]},
             "book_hashes":{},"protocol":OFFICIAL.json()}
    summary_path=folder/"summary.json"
    pgn_path=folder/"duelo.pgn"
    summary_path.write_text(json.dumps(summary),encoding="utf-8")
    pgn_path.write_text("\n\n".join(map(str,games))+"\n",encoding="utf-8")
    info={"folder":str(folder.relative_to(root)),
          "results":[r["result"] for r in records],
          "mainline_sha256":[r["mainline_sha256"] for r in records],
          "summary_sha256":sha256(summary_path.read_bytes()).hexdigest(),
          "pgn_sha256":sha256(pgn_path.read_bytes()).hexdigest()}
    match["attempts"].append(info)
    (root/"tournament.json").write_text(json.dumps(state),encoding="utf-8")
    return folder,info


@pytest.mark.parametrize("slug", ["fischer","tal","carlsen"])
def test_empty_scope_does_not_invent_game_or_rating(tmp_path,slug):
    report=history_stats.list_stats(tmp_path,slug)
    assert report["schema"]==history_stats.SCHEMA
    assert report["slug"]==slug
    assert report["run_count"]==0
    assert report["completed_games"]==report["incomplete_games"]==0
    assert report["tournaments_participated"]==report["duels_count"]==0
    assert report["head_to_head"]==[]
    assert report["coverage"]["complete"] is True
    assert report["updated_at"] is None
    assert "rating" not in report and "elo" not in report


def test_reverse_colors_are_two_games_one_duel(tmp_path):
    arena_session(tmp_path)
    f=history_stats.list_stats(tmp_path,"fischer")
    t=history_stats.list_stats(tmp_path,"tal")
    assert (f["run_count"],f["duels_count"],f["completed_games"])==(1,1,2)
    assert (f["wins"],f["draws"],f["losses"])==(1,0,1)
    assert (t["wins"],t["draws"],t["losses"])==(1,0,1)
    assert f["head_to_head"]==[{"opponent_slug":"tal","duels_count":1,
                               "completed_games":2,"wins":1,"draws":0,
                               "losses":1,"incomplete_games":0}]
    assert f["coverage"]["games_accepted"]==2
    assert f["updated_at"]=="2026-11-06T12:00:00Z"
    assert history_stats.list_stats(tmp_path,"fischer")==f


def test_rematch_is_separate_duel_but_same_arena_run(tmp_path):
    arena_session(tmp_path)
    arena_session(tmp_path,attempt="20261106T130000Z-test",
                  specs=[("fischer","tal","0-1",False)])
    report=history_stats.list_stats(tmp_path,"fischer")
    assert (report["run_count"],report["duels_count"],report["completed_games"])==(1,2,3)
    assert (report["wins"],report["losses"])==(1,2)
    assert report["coverage"]["complete"] is True


def test_incomplete_verified_pgn_separate_from_result_and_unknown_game(tmp_path):
    arena_session(tmp_path,specs=[
        ("fischer","tal","0-1",False),
        ("tal","fischer","*",True),
    ])
    report=history_stats.list_stats(tmp_path,"fischer")
    assert report["completed_games"]==1
    assert report["losses"]==1
    assert report["draws"]==0 and report["wins"]==0
    assert report["incomplete_games"]==1
    assert report["duels_count"]==1


def test_draw_and_reverse_colors_concile(tmp_path):
    arena_session(tmp_path,specs=[("fischer","tal","1/2-1/2",False),
                                  ("tal","fischer","1/2-1/2",False)])
    report=history_stats.list_stats(tmp_path,"fischer")
    assert (report["completed_games"],report["draws"])==(2,2)
    assert report["wins"]==report["losses"]==0


def test_tournament_bye_or_nonplayed_entry_not_a_game(tmp_path):
    root,state,match=tournament_run(tmp_path)
    a,b=match["players"]
    report=history_stats.list_stats(tmp_path,a)
    assert report["tournaments_participated"]==1
    assert report["run_count"]==1
    assert report["duels_count"]==report["completed_games"]==0
    assert report["updated_at"]=="2026-11-06T10:00:00Z"
    assert history_stats.list_stats(tmp_path,"carlsen")["run_count"]==0
    # Adding bye metadata cannot manufacture a game; no registered attempt.
    match["bye"]="fischer"
    (root/"tournament.json").write_text(json.dumps(state),encoding="utf-8")
    assert history_stats.list_stats(tmp_path,a)["completed_games"]==0


def test_registered_tournament_attempt_replayed_once_even_manifest_repeats(tmp_path):
    root,state,match=tournament_run(tmp_path)
    _,record=register_tournament(root,state,match)
    a,b=match["players"]
    stats=history_stats.list_stats(tmp_path,a)
    assert (stats["tournaments_participated"],stats["duels_count"],
            stats["completed_games"])==(1,1,2)
    assert stats["wins"]+stats["draws"]+stats["losses"]==2
    match["attempts"].append(record.copy())
    (root/"tournament.json").write_text(json.dumps(state),encoding="utf-8")
    repeated=history_stats.list_stats(tmp_path,a)
    assert repeated["completed_games"]==2
    assert repeated["duels_count"]==1
    assert repeated["coverage"]["complete"] is False
    assert repeated["coverage"]["reasons"]["DUPLICATE_ATTEMPT"]==1


def test_tiebreak_rematch_distinct_attempt_even_same_sides(tmp_path):
    root,state,match=tournament_run(tmp_path)
    register_tournament(root,state,match,attempt="20261106T120000Z-a")
    register_tournament(root,state,match,attempt="20261106T120001Z-b")
    report=history_stats.list_stats(tmp_path,match["players"][0])
    assert report["tournaments_participated"]==1
    assert report["run_count"]==1
    assert report["duels_count"]==2
    assert report["completed_games"]==4


def test_corrupt_arena_second_game_excludes_entire_attempt(tmp_path):
    folder=arena_session(tmp_path)
    (folder/"jogo_2.pgn").write_bytes(b"bad PGN")
    report=history_stats.list_stats(tmp_path,"fischer")
    assert report["run_count"]==1
    assert report["completed_games"]==0
    assert report["duels_count"]==0
    assert report["coverage"]["complete"] is False
    assert report["coverage"]["reasons"]["INVALID_ARENA_ATTEMPT"]==1


def test_tampered_summary_result_and_symlink_never_count(tmp_path):
    folder=arena_session(tmp_path)
    data=json.loads((folder/"summary.json").read_text())
    data["games"][0]["result"]="1-0"
    (folder/"summary.json").write_text(json.dumps(data))
    report=history_stats.list_stats(tmp_path,"fischer")
    assert report["completed_games"]==0 and not report["coverage"]["complete"]
    # A link under the workspace cannot escape its root even if it looks valid.
    external=tmp_path.parent/"external-chess-stats.pgn"
    external.write_text(str(game()[0]),encoding="utf-8")
    target=folder/"jogo_1.pgn"
    target.unlink()
    target.symlink_to(external)
    report=history_stats.list_stats(tmp_path,"fischer")
    assert report["completed_games"]==0
    assert not report["coverage"]["complete"]
    external.unlink()


def test_corrupt_registered_tournament_attempt_is_excluded_and_reported(tmp_path):
    root,state,match=tournament_run(tmp_path)
    folder,_=register_tournament(root,state,match)
    (folder/"duelo.pgn").write_text("adulterated")
    report=history_stats.list_stats(tmp_path,match["players"][0])
    assert report["tournaments_participated"]==1
    assert report["completed_games"]==0
    assert report["coverage"]["reasons"]["INVALID_TOURNAMENT_ATTEMPT"]==1


@pytest.mark.parametrize("bad", ["unknown","..","fischer/../../tmp",""])
def test_unknown_slug_cannot_select_files(tmp_path,bad):
    with pytest.raises(LookupError):
        history_stats.list_stats(tmp_path,bad)


@contextmanager
def serve(workspace: Path):
    server=arena.create_server(workspace,workspace/"arena-workspace",port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        server.arena_manager.shutdown()
        thread.join(timeout=2)


def test_http_get_stats_status_errors_and_post_forbidden(tmp_path):
    workspace=tmp_path/"arena-workspace"
    workspace.mkdir()
    arena_session(workspace)
    with serve(tmp_path) as base:
        with urlopen(base+"/api/history/players/fischer/stats") as response:
            data=json.load(response)
            assert data["wins"]==1
            assert data["duels_count"]==1
            assert response.headers["Cache-Control"]=="no-store"
            assert "script-src 'self'" in response.headers["Content-Security-Policy"]
        for uri in ("/api/history/players/unknown/stats",
                    "/api/history/players/../../etc/passwd/stats",
                    "/api/history/players/fischer/stats?file=/tmp/pgn"):
            with pytest.raises(HTTPError) as err:
                urlopen(base+uri)
            assert err.value.code==404
        with pytest.raises(HTTPError) as err:
            urlopen(Request(base+"/api/history/players/fischer/stats",
                            data=b"{}",method="POST"))
        assert err.value.code==404


def test_running_duel_with_no_completed_game_does_not_look_corrupt(tmp_path):
    folder=arena_session(tmp_path,specs=[])
    summary={"status":"RUNNING","players":["fischer","tal"],"games":[]}
    (folder/"summary.json").write_text(json.dumps(summary),encoding="utf-8")
    result=history_stats.list_stats(tmp_path,"fischer")
    assert result["run_count"]==1
    assert result["completed_games"]==result["duels_count"]==0
    assert result["coverage"]["complete"] is True


def test_unproven_declared_result_does_not_count_as_win(tmp_path):
    folder=arena_session(tmp_path,specs=[("fischer","tal","1/2-1/2",False)])
    info=json.loads((folder/"summary.json").read_text())
    pgn,record=game("fischer","tal",result="0-1",incomplete=True)
    pgn.headers["Result"]="1-0"
    pgn.headers["Termination"]="Normal"
    record.update(result="1-0",status="COMPLETED",termination="Normal")
    (folder/"jogo_1.pgn").write_text(str(pgn)+"\n",encoding="utf-8")
    info["games"]=[record]
    (folder/"summary.json").write_text(json.dumps(info))
    result=history_stats.list_stats(tmp_path,"fischer")
    assert result["wins"]==result["completed_games"]==0
    assert result["coverage"]["reasons"]["INVALID_ARENA_ATTEMPT"]==1


def test_verified_time_forfeit_counts_only_actual_losing_side(tmp_path):
    folder=arena_session(tmp_path,specs=[("fischer","tal","0-1",False)])
    pgn,meta=game("fischer","tal",result="0-1",incomplete=True)
    pgn.headers["Result"]="0-1"
    pgn.headers["Termination"]="TimeForfeit"
    meta.update(result="0-1",status="COMPLETED",termination="TimeForfeit",
                clock_left=[-0.25,600])
    (folder/"jogo_1.pgn").write_text(str(pgn)+"\n",encoding="utf-8")
    (folder/"summary.json").write_text(json.dumps({
        "status":"COMPLETED","players":["fischer","tal"],"games":[meta]
    }))
    stats=history_stats.list_stats(tmp_path,"fischer")
    assert stats["losses"]==stats["completed_games"]==1
    assert stats["coverage"]["complete"] is True
    meta["clock_left"]=[600,-0.25]  # the wrong side allegedly lost on time
    (folder/"summary.json").write_text(json.dumps({
        "status":"COMPLETED","players":["fischer","tal"],"games":[meta]
    }))
    rejected=history_stats.list_stats(tmp_path,"fischer")
    assert rejected["completed_games"]==0
    assert not rejected["coverage"]["complete"]


def test_unregistered_tournament_attempt_never_counted_and_is_reported(tmp_path):
    root,state,match=tournament_run(tmp_path)
    folder,_=register_tournament(root,state,match)
    match["attempts"].clear()
    (root/"tournament.json").write_text(json.dumps(state))
    result=history_stats.list_stats(tmp_path,match["players"][0])
    assert result["tournaments_participated"]==1
    assert result["duels_count"]==result["completed_games"]==0
    assert result["coverage"]["reasons"]["UNREGISTERED_TOURNAMENT_ATTEMPT"]==1
    match["excluded_attempts"]=[{"folder":str(folder.relative_to(root)),
                                  "reason":"NOT_COMPLETED"}]
    (root/"tournament.json").write_text(json.dumps(state))
    excluded=history_stats.list_stats(tmp_path,match["players"][0])
    assert excluded["coverage"]["reasons"]["EXCLUDED_TOURNAMENT_ATTEMPT"]==1
    assert excluded["completed_games"]==0


def test_freezing_tournament_engines_profiles_prevents_forged_results(tmp_path):
    root,state,match=tournament_run(tmp_path)
    folder,entry=register_tournament(root,state,match)
    # A forged PGN is re-hashed to bypass a SHA-only check. Frozen engine
    # identity in the tournament still must reject it.
    raw=(folder/"duelo.pgn").read_text(encoding="utf-8")
    raw=raw.replace('EngineSHA256 "eeeeeeee', 'EngineSHA256 "ffffffff',1)
    (folder/"duelo.pgn").write_text(raw,encoding="utf-8")
    entry["pgn_sha256"]=sha256((folder/"duelo.pgn").read_bytes()).hexdigest()
    (root/"tournament.json").write_text(json.dumps(state))
    report=history_stats.list_stats(tmp_path,match["players"][0])
    assert report["completed_games"]==0
    assert report["coverage"]["reasons"]["INVALID_TOURNAMENT_ATTEMPT"]==1


def test_nested_tournament_scope_is_opt_in_under_workspace(tmp_path):
    nested=tmp_path/"tournaments"
    nested.mkdir()
    root,state,match=tournament_run(nested)
    register_tournament(root,state,match)
    report=history_stats.list_stats(tmp_path,match["players"][0])
    assert report["tournaments_participated"]==1
    assert report["completed_games"]==2
    assert report["coverage"]["complete"] is True


def test_duplicate_json_keys_rejected_with_coverage_warning(tmp_path):
    folder=arena_session(tmp_path)
    summary=(folder/"summary.json").read_text(encoding="utf-8")
    (folder/"summary.json").write_text('{"players":[],"players":[],"games":[]}',
                                       encoding="utf-8")
    assert summary
    data=history_stats.list_stats(tmp_path,"fischer")
    assert data["completed_games"]==0
    assert data["coverage"]["complete"] is False
