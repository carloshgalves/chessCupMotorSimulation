"""Issue #60 — competitive Elo is a deterministic ledger, not a UCI strength."""
from __future__ import annotations

from decimal import Decimal
import chess.pgn
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from chesssim import arena
from chesssim.competition_rating import (
    BASELINE, SCHEMA, build_rating_ledger, expected_score, export_rating_ledger,
    rating_report, verify_rating_ledger, classify_clock,
)

from test_history_stats import cup, session


def event(i, result="1-0", *, first="fischer", second="tal", status="COMPLETED",
          source="arena", date="2026-11-06T12:00:00+00:00", mode="rapid"):
    return {
        "id": f"{source}:test-{i}", "duel_id": f"{source}:duel-{i // 2}",
        "white": first, "black": second, "status": status, "result": result,
        "recorded_utc": date, "origin": source, "time_control": mode,
        "engine_strength_config": None,
    }


def by_slug(report, slug):
    return next(x for x in report["ratings"] if x["slug"] == slug)


@pytest.mark.parametrize(("result", "delta", "score"), [
    ("1-0", Decimal("5"), "1"), ("0-1", Decimal("-5"), "0"),
    ("1/2-1/2", Decimal("0"), "0.5"),
])
def test_equal_ratings_analytic_and_no_engine_effect(result, delta, score):
    s = build_rating_ledger([event(0, result)])
    w, b = by_slug(s,"fischer"), by_slug(s,"tal")
    assert Decimal(w["rating"]) == BASELINE + delta
    assert Decimal(b["rating"]) == BASELINE - delta
    assert Decimal(w["rating"]) + Decimal(b["rating"]) == 5000
    assert s["rating_ledger"][0]["score_white"] == score
    assert s["rating_ledger"][0]["k"] == "10"
    assert w["strength_evaluation"] is None
    assert w["move_accuracy"] is None
    assert w["initial_rating"] == "2500"
    assert verify_rating_ledger(s)


def test_logistic_scale_offset_and_equal_k_zero_sum():
    with pytest.raises(ValueError):
        build_rating_ledger([event(1)],k="NaN")
    assert expected_score(Decimal("2500"),Decimal("2500")) == Decimal("0.5")
    assert expected_score(Decimal("2600"),Decimal("2400")) == (
        expected_score(Decimal("3600"),Decimal("3400")))
    a=expected_score(Decimal("2900"),Decimal("2500"))
    b=expected_score(Decimal("2500"),Decimal("2900"))
    assert abs(a+b-1) < Decimal("0.000000000000000000001")
    for k in (True,"-1","0","101","Infinity",float("nan"),None,"abc"):
        with pytest.raises(ValueError):
            build_rating_ledger([],k=k)
    assert build_rating_ledger([event(1)],k="20")["k"] == "20"


def test_100_games_reprocess_order_snapshot_and_chain_tampering():
    records = [
        event(i,("1-0" if i%3==0 else "0-1" if i%3==1 else "1/2-1/2"),
              mode=("rapid" if i%2 else "blitz"))
        for i in range(100)
    ]
    snap = build_rating_ledger(records, modality="rapid")
    blitz = build_rating_ledger(records, modality="blitz")
    assert snap == build_rating_ledger(list(reversed(records)), modality="rapid")
    assert len(snap["rating_ledger"]) == 50
    assert len(blitz["rating_ledger"]) == 50
    assert not set(x["id"] for x in snap["rating_ledger"]) & set(
        x["id"] for x in blitz["rating_ledger"])
    assert verify_rating_ledger(blitz)
    assert snap["counts"]["scored_games"] == 50
    assert snap["mixed_modalities_warning"] is False
    assert verify_rating_ledger(snap) is True
    assert by_slug(snap,"fischer")["games"] == 50
    assert by_slug(snap,"fischer")["status"] == "observed_experimental"
    for p in ("fischer","tal"):
        assert Decimal(by_slug(snap,p)["rating"]).is_finite()
        assert by_slug(snap,p)["points_3_1_0"] >= 0
    assert (Decimal(by_slug(snap,"fischer")["rating"]) +
            Decimal(by_slug(snap,"tal")["rating"])) == 5000
    tampered=json.loads(json.dumps(snap))
    original_result = tampered["rating_ledger"][42]["result"]
    tampered["rating_ledger"][42]["result"] = (
        "0-1" if original_result == "1-0" else "1-0")
    assert not verify_rating_ledger(tampered)
    truncated=json.loads(json.dumps(snap))
    truncated["rating_ledger"].pop()
    assert not verify_rating_ledger(truncated)
    reversed_ledger=json.loads(json.dumps(snap))
    reversed_ledger["rating_ledger"].reverse()
    assert not verify_rating_ledger(reversed_ledger)


def test_resume_duplicate_incomplete_and_conflicting_identity_fail_closed():
    one=event(0)
    two=event(1,status="INCOMPLETE",result=None)
    report=build_rating_ledger([one,one,two,two])
    assert report["counts"] == {
        "scored_games":1,"ignored_incomplete":1,"ignored_ineligible":0,
        "duplicate_records":2,"participants":64}
    assert by_slug(report,"fischer")["games"] == 1
    assert by_slug(report,"tal")["losses"] == 1
    assert by_slug(report,"carlsen")["status"] == "sem_amostra"
    assert by_slug(report,"carlsen")["rank"] is None
    assert by_slug(report,"carlsen")["display_rating"] == "2500.0"
    with pytest.raises(ValueError,match="provas divergentes"):
        build_rating_ledger([one,{**one,"result":"0-1"}])
    with pytest.raises(ValueError,match="Interrupção"):
        build_rating_ledger([{**two,"result":"1-0"}])
    with pytest.raises(ValueError,match="Resultado"):
        build_rating_ledger([{**one,"result":"*"}])
    with pytest.raises(ValueError,match="Identidade"):
        build_rating_ledger([{**one,"black":"fischer"}])


def test_no_game_no_classification_and_score_confidence_is_not_elo():
    snapshot=build_rating_ledger([])
    assert len(snapshot["ratings"]) == 64
    assert all(row["games"]==0 and row["rank"] is None for row in snapshot["ratings"])
    assert all(row["score_range_exploratory"] is None for row in snapshot["ratings"])
    assert "não é IC de Elo" in snapshot["confidence_note"]


def test_real_verified_arena_and_tournament_and_original_files_unchanged(tmp_path):
    session(tmp_path,0,results=("1-0","0-1"))
    cup(tmp_path)
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    before={p.relative_to(tmp_path):p.read_bytes() for p in files}
    report=rating_report(tmp_path,include_ledger=True,modality="rapid")
    unclocked=rating_report(tmp_path,include_ledger=True,modality="unclassified")
    assert report["schema"] == SCHEMA
    assert report["counts"]["scored_games"] == 2, report["coverage"]
    assert unclocked["counts"]["scored_games"] == 2
    assert report["available_modalities"]["rapid"] == 2
    assert report["available_modalities"]["unclassified"] == 2
    assert report["coverage"]["arena_verified_games"] == 2
    assert report["coverage"]["tournament_verified_games"] == 2
    assert verify_rating_ledger({
        "schema":report["ledger_schema"],
        "modality":report["modality"],
        "ledger_genesis":report["ledger_genesis"],
        "rating_ledger":report["rating_ledger"],
        "ledger_checksum":report["ledger_checksum"],
    })
    assert set(e["source"] for e in report["rating_ledger"]) == {"tournament"}
    assert set(e["source"] for e in unclocked["rating_ledger"]) == {"arena"}
    assert all(e["engine_strength_config"] is not None for e in report["rating_ledger"])
    assert all(e["engine_strength_config"] is None for e in unclocked["rating_ledger"])
    assert all(len(e["evidence_pgn_sha256"]) == 64
               for e in report["rating_ledger"] + unclocked["rating_ledger"])
    assert all(not e["id"].startswith("historical:") for e in report["rating_ledger"])
    assert before=={p.relative_to(tmp_path):p.read_bytes() for p in files}


def test_failed_or_incomplete_and_corrupted_games_do_not_become_elo(tmp_path):
    session(tmp_path,0,results=("1-0","*"))
    session(tmp_path,1,results=("1-0",),broken=True)
    report=rating_report(tmp_path, modality="unclassified")
    assert report["counts"]["scored_games"] == 1
    assert report["counts"]["ignored_incomplete"] == 1
    assert report["coverage"]["partial"] is True
    assert report["coverage"]["rejected_games"] >= 1
    assert by_slug(report,"fischer")["games"] == 1


def test_explicit_export_is_bytewise_idempotent_and_requires_replace(tmp_path):
    session(tmp_path,0,results=("1-0",))
    out=tmp_path/"rating-ledger.json"
    result=export_rating_ledger(tmp_path,out,modality="unclassified")
    assert result["changed"] is True
    original=out.read_bytes()
    assert export_rating_ledger(tmp_path,out,modality="unclassified")["changed"] is False
    assert out.read_bytes()==original
    session(tmp_path,1,results=("0-1",))
    with pytest.raises(FileExistsError):
        export_rating_ledger(tmp_path,out,modality="unclassified")
    assert out.read_bytes()==original
    updated=export_rating_ledger(tmp_path,out,modality="unclassified",replace=True)
    assert updated["changed"] is True
    assert len(json.loads(out.read_text())["rating_ledger"]) == 2
    exported = json.loads(out.read_text())
    assert "snapshot_checksum" in exported
    assert verify_rating_ledger(exported)
    exported["ratings"][0]["display_rating"] = "9000.0"
    assert not verify_rating_ledger(exported)
    no_checksum=json.loads(out.read_text())
    del no_checksum["snapshot_checksum"]
    assert not verify_rating_ledger(no_checksum)
    changed_meta=json.loads(out.read_text())
    changed_meta["coverage"]["partial"] = True
    assert not verify_rating_ledger(changed_meta)
    protected=tmp_path/"tournament.json"
    with pytest.raises(ValueError, match="arquivos de partida"):
        export_rating_ledger(tmp_path,protected,replace=True)
    wrong=tmp_path/"missing"/"ledger.json"
    with pytest.raises(ValueError):
        export_rating_ledger(tmp_path,wrong)
    symlink=tmp_path/"fake.json"
    symlink.symlink_to(out)
    with pytest.raises(ValueError):
        export_rating_ledger(tmp_path,symlink)


def test_rehashed_modified_result_still_fails_rating_transition_verification():
    snap = build_rating_ledger([event(1),event(2,"0-1")])
    assert verify_rating_ledger(snap)
    forged = json.loads(json.dumps(snap))
    forged["rating_ledger"][0]["white_after"] = "9999"
    from hashlib import sha256
    from chesssim.competition_rating import _canonical
    first=forged["rating_ledger"][0]
    first["checksum"]=sha256((first["previous_checksum"]+"\n").encode() +
                             _canonical({k:v for k,v in first.items()
                                         if k!="checksum"})).hexdigest()
    second=forged["rating_ledger"][1]
    second["previous_checksum"]=first["checksum"]
    second["checksum"]=sha256((second["previous_checksum"]+"\n").encode() +
                              _canonical({k:v for k,v in second.items()
                                          if k!="checksum"})).hexdigest()
    forged["ledger_checksum"]=second["checksum"]
    assert not verify_rating_ledger(forged), (
        "Recomputing a checksum cannot conceal impossible Elo transitions"
    )


def test_cli_ledger_export_idempotent_and_invalid_k_is_nonzero(tmp_path):
    session(tmp_path, 0, results=("1-0",))
    out=tmp_path/"elo-cli.json"
    cmd=[sys.executable,"-m","chesssim","rating-export",
         "--workspace",str(tmp_path),"--out",str(out),
         "--modality","unclassified"]
    first=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
    assert first.returncode==0,first.stderr
    assert json.loads(first.stdout)["changed"] is True
    assert len(json.loads(out.read_text())["rating_ledger"])==1
    second=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
    assert second.returncode==0,second.stderr
    assert json.loads(second.stdout)["changed"] is False
    invalid=subprocess.run(cmd+["--k","NaN"],capture_output=True,
                           text=True,timeout=30)
    assert invalid.returncode==2
    assert "K" in invalid.stderr
    assert len(json.loads(out.read_text())["rating_ledger"])==1


def test_global_get_without_games_reports_initial_unranked_state(tmp_path):
    server=arena.create_server(tmp_path,tmp_path,port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        base=f"http://127.0.0.1:{server.server_port}"
        for path in ("/api/competition/ratings",
                     "/api/history/players/fischer/rating"):
            with urlopen(base+path,timeout=10) as response:
                report=json.loads(response.read())
                assert report["counts"]["scored_games"]==0
                assert report["ledger_checksum"]
                assert all(x["display_rating"]=="2500.0" and x["rank"] is None
                           and x["status"]=="sem_amostra"
                           for x in report["ratings"])
                assert all(x["strength_evaluation"] is None and
                           x["move_accuracy"] is None for x in report["ratings"])
    finally:
        server.shutdown()
        server.server_close()
        server.arena_manager.shutdown()
        thread.join(timeout=5)


def test_elo_api_local_get_only_and_no_token_leak(tmp_path):
    session(tmp_path,0,results=("1-0",))
    server=arena.create_server(tmp_path,tmp_path,port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    base=f"http://127.0.0.1:{server.server_port}"
    try:
        for path in ("/api/competition/ratings",
                     "/api/history/players/fischer/rating"):
            with urlopen(base+path,timeout=15) as response:
                assert response.status==200
                assert response.headers["Cache-Control"]=="no-store"
                assert response.headers["X-Content-Type-Options"]=="nosniff"
                raw=response.read()
                assert server.arena_token.encode() not in raw
                data=json.loads(raw)
                assert data["schema"]==SCHEMA
                assert "rating_ledger" not in data
                assert len(data["ratings"]) == (1 if "fischer" in path else 64)
        for path in ("/api/competition/ratings?path=/tmp",
                     "/api/history/players/fischer/rating?foo=bar",
                     "/api/history/players/unknown/rating",
                     "/api/history/players/fischer/extra/rating"):
            with pytest.raises(HTTPError) as error:
                urlopen(base+path,timeout=15)
            assert error.value.code==404
        for path in ("/api/competition/ratings",
                     "/api/history/players/fischer/rating"):
            with pytest.raises(HTTPError) as error:
                urlopen(Request(base+path,data=b"{}",method="POST"),timeout=15)
            assert error.value.code in (403,404,405)
    finally:
        server.shutdown()
        server.server_close()
        server.arena_manager.shutdown()
        thread.join(timeout=5)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js unavailable")
def test_elo_browser_ui_no_xss_and_stale_response():
    root=Path(__file__).resolve().parents[1]
    js=root/"src/chesssim/data/competition_rating.js"
    result=subprocess.run(["node","--check",str(js)],
                          capture_output=True,text=True,timeout=10,check=False)
    assert result.returncode==0,result.stderr
    script=root/"tests/assets/competition_rating_browser_harness.cjs"
    result=subprocess.run(["node",str(script),str(js)],capture_output=True,
                          text=True,timeout=15,check=False)
    assert result.returncode==0,result.stdout+result.stderr
    assert "Experimental Elo browser: PASS" in result.stdout


@pytest.mark.parametrize(("initial","increment","expected"), [
    (60,0,"bullet"), (120,0,"bullet"), (180,0,"blitz"), (180,2,"blitz"),
    (600,0,"blitz"), (600,2,"rapid"), (1800,5,"rapid"),
    (1800,20,"rapid"), (2700,0,"rapid"), (1800,30,"classical"),
    (2700,30,"classical"), (3600,0,"classical"),
])
def test_clock_classification_uses_initial_plus_sixty_increments(
        initial, increment, expected):
    assert classify_clock(initial,increment) == expected


@pytest.mark.parametrize(("initial","increment"), [
    (True,0), (0,0), (60,-1), (60,False), (1.5,0), (60,"2"),
])
def test_clock_rejects_invalid_input(initial,increment):
    with pytest.raises(ValueError):
        classify_clock(initial,increment)


def test_custom_clock_changes_only_its_own_rating_pool():
    custom_rapid={**event(0),"clock":[1800,20],"time_control":"custom"}
    custom_classical={**event(1,"0-1"),"clock":[1800,30],
                       "time_control":"custom"}
    original=build_rating_ledger([custom_rapid],modality="rapid")
    mixed=build_rating_ledger([custom_rapid,custom_classical],modality="rapid")
    classical=build_rating_ledger([custom_rapid,custom_classical],modality="classical")
    assert original["ratings"] == mixed["ratings"]
    assert original["rating_ledger"] == mixed["rating_ledger"]
    assert mixed["counts"]["scored_games"] == 1
    assert classical["counts"]["scored_games"] == 1
    assert by_slug(mixed,"fischer")["rating"] == "2505.0"
    assert by_slug(classical,"fischer")["rating"] == "2495.0"
    assert verify_rating_ledger(mixed)
    assert verify_rating_ledger(classical)


def test_legacy_official_10_plus_2_is_rapid_not_classical():
    original={**event(0),"time_control":"official"}
    assert build_rating_ledger([original],modality="rapid")["counts"]["scored_games"] == 1
    assert build_rating_ledger([original],modality="classical")["counts"]["scored_games"] == 0


def test_verified_custom_clock_pgn_provenance_and_category(tmp_path):
    folder, duel = session(tmp_path, 3, results=("1-0",))
    manifest = folder / "session.json"
    content = json.loads(manifest.read_text())
    content["config"].update({
        "mode": "preset", "preset": "custom",
        "initial_seconds": 1800, "increment_seconds": 20,
    })
    manifest.write_text(json.dumps(content))
    pgn_file = duel / "jogo_1.pgn"
    with pgn_file.open(encoding="utf-8") as stream:
        game = chess.pgn.read_game(stream)
    game.headers["TimeControl"] = "1800+20"
    pgn_file.write_text(str(game) + "\n", encoding="utf-8")
    rapid = rating_report(tmp_path, modality="rapid")
    classical = rating_report(tmp_path, modality="classical")
    assert rapid["counts"]["scored_games"] == 1
    assert classical["counts"]["scored_games"] == 0
    assert rapid["available_modalities"]["rapid"] == 1
    # Editing only the manifest cannot silently move a game into classical:
    content["config"]["increment_seconds"] = 30
    manifest.write_text(json.dumps(content))
    rejected = rating_report(tmp_path, modality="classical")
    assert rejected["counts"]["scored_games"] == 0
    assert rejected["coverage"]["rejected_games"] == 1
    # Matching PGN evidence permits classification as classical.
    game.headers["TimeControl"] = "1800+30"
    pgn_file.write_text(str(game) + "\n", encoding="utf-8")
    classical = rating_report(tmp_path, modality="classical")
    assert classical["counts"]["scored_games"] == 1
    assert rating_report(tmp_path, modality="rapid")["counts"]["scored_games"] == 0


def test_rating_http_pools_and_invalid_query_fail_closed(tmp_path):
    session(tmp_path, 4, results=("1-0",))
    server = arena.create_server(tmp_path,tmp_path,port=0)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        base=f"http://127.0.0.1:{server.server_port}"
        with urlopen(base + "/api/competition/ratings?modality=rapid", timeout=10) as response:
            rapid=json.loads(response.read())
        with urlopen(base + "/api/competition/ratings?modality=unclassified",
                     timeout=10) as response:
            unclocked=json.loads(response.read())
        assert rapid["counts"]["scored_games"] == 0
        assert unclocked["counts"]["scored_games"] == 1
        assert unclocked["modality"] == "unclassified"
        assert unclocked["available_modalities"]["unclassified"] == 1
        for query in ("modality=invalid","modality=rapid&modality=classical","path=/tmp"):
            with pytest.raises(HTTPError) as error:
                urlopen(base + "/api/competition/ratings?" + query, timeout=10)
            assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        server.arena_manager.shutdown()
        thread.join(timeout=5)


def test_arena_depth_plus_clock_is_scored_in_timed_pool(tmp_path):
    folder,duel=session(tmp_path,7,results=("1-0",))
    manifest=folder/"session.json"
    data=json.loads(manifest.read_text())
    data["config"].update({"mode":"depth","depth":16,"preset":"bullet"})
    manifest.write_text(json.dumps(data))
    pgn_file=duel/"jogo_1.pgn"
    with pgn_file.open(encoding="utf-8") as stream:
        game=chess.pgn.read_game(stream)
    game.headers["TimeControl"]="60+0"
    pgn_file.write_text(str(game)+"\n",encoding="utf-8")
    bullet=rating_report(tmp_path,modality="bullet")
    assert bullet["counts"]["scored_games"]==1
    assert rating_report(tmp_path,modality="unclassified")["counts"]["scored_games"]==0
    assert rating_report(tmp_path,modality="rapid")["counts"]["scored_games"]==0
