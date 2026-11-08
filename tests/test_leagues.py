"""Issue #59: league calendar, ledger integrity, scoring, restart and engine provenance."""
from __future__ import annotations

from collections import Counter
import json
import threading
import time

import chess
import chess.pgn
import pytest

from chesssim import leagues
from chesssim.catalog import find_player


def config(n=4, fmt="double", execution="fast_statistical", **changes):
    from chesssim.catalog import load_roster
    value={
        "name":"Liga dos Imortais", "season":"Temporada 1",
        "format":fmt, "modality":"rapid", "execution_mode":execution,
        "participants":[p.slug for p in load_roster()[:n]],
        "depth":3, "points":{"win":3,"draw":1,"loss":0}, "seed":17,
    }
    value.update(changes)
    return value


def wait(manager, ident, timeout=12):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        data=manager.detail(ident)
        if data["status"]!="RUNNING" and not manager.busy:
            return data
        time.sleep(.02)
    raise AssertionError("Rodada não concluiu")


@pytest.mark.parametrize(("n","fmt","rounds","total"), [
    (4,"single",3,6),(4,"double",6,12),
    (8,"single",7,28),(8,"double",14,56),
    (64,"single",63,2016),(64,"double",126,4032),
])
def test_round_robin_has_exact_pairs_and_reverse_colors(n,fmt,rounds,total):
    players=config(n=n)["participants"]
    calendar=leagues.schedule(players,fmt)
    assert len(calendar)==rounds
    assert all(len(r)==n//2 for r in calendar)
    games=[g for rd in calendar for g in rd]
    assert len(games)==total
    assert len({g["id"] for g in games})==total
    pairs=Counter(tuple(sorted((g["white"],g["black"]))) for g in games)
    assert len(pairs)==n*(n-1)//2
    assert set(pairs.values())==({1} if fmt=="single" else {2})
    oriented=Counter((g["white"],g["black"]) for g in games)
    assert set(oriented.values())=={1}
    if fmt=="double":
        middle=n-1
        for a,b in zip(calendar[:middle],calendar[middle:]):
            for first,second in zip(a,b):
                assert first["white"]==second["black"]
                assert first["black"]==second["white"]


@pytest.mark.parametrize("change",[
    {"participants":["fischer","fischer","tal","capablanca"]},
    {"participants":["fischer","tal","unknown","capablanca"]},
    {"participants":["fischer","tal","capablanca"]},
    {"participants":[]},
    {"format":"knockout"},{"modality":"unknown"},
    {"execution_mode":"fake_elo"},{"points":{"win":2,"draw":3,"loss":0}},
    {"points":{"win":3,"draw":1,"loss":-1}},
    {"depth":True},{"depth":17},{"seed":False},
    {"name":"  "},{"season":"x\ninjected"},
    {"path":"../../etc/passwd"},
])
def test_config_rejects_invalid_or_untrusted_input(change):
    with pytest.raises(leagues.LeagueInput):
        leagues.normalize_config(config(**change))


def test_table_scores_only_recorded_games_and_direct_h2h():
    normalized=leagues.normalize_config(config(4,"double",
        participants=["fischer","tal","capablanca","lasker"]))
    calendar=leagues.schedule(normalized["participants"],"double")
    all_matches={g["id"]:g for rd in calendar for g in rd}
    fixtures=[
        ("fischer","tal","1-0"),
        ("fischer","capablanca","1/2-1/2"),
        ("tal","capablanca","0-1"),
    ]
    records={}
    for white,black,result in fixtures:
        match=next((m for m in all_matches.values()
                    if m["white"]==white and m["black"]==black),None)
        if match is None:
            # The schedule fixes colors; adapt result to actual orientation.
            match=next(m for m in all_matches.values()
                       if {m["white"],m["black"]}=={white,black})
            if result!="1/2-1/2" and match["white"]!=white:
                result="0-1" if result=="1-0" else "1-0"
        records[match["id"]]={"result":result}
    table=leagues.standings(normalized,calendar,records)
    by_slug={row["slug"]:row for row in table}
    assert sum(row["played"] for row in table)==6
    assert sum(row["points"] for row in table)==8
    assert by_slug["fischer"]["played"]==2
    assert by_slug["fischer"]["wins"]==1
    assert by_slug["fischer"]["draws"]==1
    assert by_slug["fischer"]["points"]==4
    assert by_slug["tal"]["wins"]==0
    assert by_slug["capablanca"]["points"]==4
    assert by_slug["fischer"]["rank"]!=by_slug["capablanca"]["rank"]
    assert all(row["wins"]+row["draws"]+row["losses"]==row["played"]
               for row in table)
    assert all(row["played"]<=3 for row in table)


@pytest.fixture
def manager(tmp_path):
    root=tmp_path/"rodent";root.mkdir()
    workspace=tmp_path/"workspace";workspace.mkdir()
    obj=leagues.LeagueManager(root,workspace)
    try:
        yield obj
    finally:
        obj.shutdown()


def test_fast_statistical_round_restarts_without_duplicate_scoring(manager):
    ident=manager.create(config())["id"]
    assert manager.detail(ident)["total_games"]==12
    assert manager.list()["leagues"][0]["id"]==ident
    first=manager.run(ident)
    assert first["round"]==1
    state=wait(manager,ident)
    assert state["completed_games"]==2
    assert state["round_current"]==2
    assert state["status"]=="PAUSED"
    assert len(manager.round_detail(ident,1)["games"])==2
    assert all(x["status"]=="COMPLETED" for x in manager.round_detail(ident,1)["games"])
    assert manager.history(ident)["total"]==2
    assert manager.history(ident,player=config()["participants"][0])["total"]==1
    assert manager.history(ident,offset=1,limit=1)["games"][0]["pgn"] is None
    content,ctype=manager.export(ident,"json")
    assert "json" in ctype
    saved=json.loads(content)
    assert len(saved["completed_games"])==2
    assert all(x["source"]=="fast_statistical" and x["pgn"] is None
               for x in saved["completed_games"])
    with pytest.raises(leagues.LeagueInput,match="Nenhuma partida UCI"):
        manager.export(ident,"pgn")
    before=(manager._dir(ident)/"ledger.jsonl").read_text()
    manager2=leagues.LeagueManager(manager.root,manager.workspace)
    try:
        assert manager2.detail(ident)["completed_games"]==2
        assert (manager2._dir(ident)/"ledger.jsonl").read_text()==before
        for _ in range(5):
            manager2.run(ident)
            wait(manager2,ident)
        final=manager2.detail(ident)
        assert final["status"]=="COMPLETED"
        assert final["completed_games"]==12
        assert final["pending_games"]==0
        assert final["rounds_total"]==6
        assert sum(r["played"] for r in final["standings"])==24
        assert sum(r["points"] for r in final["standings"])==sum(
            3 if item["result"]!="1/2-1/2" else 2
            for item in manager2._ledger(manager2._load(ident)).values())
        assert len(manager2._ledger(manager2._load(ident)))==12
        with pytest.raises(leagues.LeagueInput,match="concluída"):
            manager2.run(ident)
    finally:
        manager2.shutdown()


def test_64_entrant_creation_and_one_round_without_engine(manager):
    ident=manager.create(config(64,"double"))["id"]
    data=manager.detail(ident)
    assert data["total_games"]==4032
    assert data["rounds_total"]==126
    assert data["participants_count"]==64
    assert manager.run(ident)["round"]==1
    done=wait(manager,ident,timeout=25)
    assert done["completed_games"]==32
    assert done["total_games"]==4032
    assert done["round_current"]==2
    assert sum(row["played"] for row in done["standings"])==64


def test_receipt_reconciliation_does_not_reexecute_or_double_count(manager,monkeypatch):
    ident=manager.create(config())["id"]
    original=manager._append
    called=[]
    def fail_once(path,entry):
        if not called:
            called.append(entry["match_id"])
            raise OSError("crash before ledger append")
        return original(path,entry)
    monkeypatch.setattr(manager,"_append",fail_once)
    manager.run(ident)
    failed=wait(manager,ident)
    assert failed["completed_games"]==1  # receipt was recovered on next read
    assert not (manager._dir(ident)/"ledger.jsonl").exists(), (
        "GET must not append recovered receipt into the journal")
    monkeypatch.setattr(manager,"_append",original)
    assert manager.run(ident)["round"]==1
    recovered=wait(manager,ident)
    assert recovered["completed_games"]==2
    assert len((manager._dir(ident)/"ledger.jsonl").read_text().splitlines())==2
    assert len({x["match_id"] for x in manager.history(ident)["games"]})==2


def test_engine_provenance_true_pgn_and_cannot_change_profiles(manager,monkeypatch):
    root=manager.root
    exe=root/"rodent-iv-plain.exe";exe.write_bytes(b"uci synthetic")
    players=config()["participants"]
    for slug in players:(root/(slug+".txt")).write_bytes(slug.encode())
    def validate_stub(root_arg,white,black):
        return (exe,{white.slug:root/(white.slug+".txt"),
                     black.slug:root/(black.slug+".txt")})
    monkeypatch.setattr(leagues,"validate",validate_stub)
    def runner(root_arg,match,conf,stop_check):
        game=chess.pgn.Game()
        game.headers.update({
            "White":find_player(match["white"]).name,
            "Black":find_player(match["black"]).name,
            "Result":"0-1","Termination":"CHECKMATE",
            "TimeControl":f"{conf['clock'][0]}+{conf['clock'][1]}",
            "EngineSHA256":leagues.file_hash(exe),
            "WhiteProfileSHA256":leagues.file_hash(root/(match["white"]+".txt")),
            "BlackProfileSHA256":leagues.file_hash(root/(match["black"]+".txt"))
        })
        node=game
        board=game.board()
        for uci in ("f2f3","e7e5","g2g4","d8h4"):
            move=chess.Move.from_uci(uci)
            assert move in board.legal_moves
            node=node.add_variation(move);board.push(move)
        assert board.is_checkmate()
        return game,{"status":"COMPLETED","result":"0-1",
                     "termination":"CHECKMATE"}
    manager.runner=runner
    ident=manager.create(config(execution="engine_full"))["id"]
    manager.run(ident)
    data=wait(manager,ident)
    assert data["completed_games"]==2
    assert data["available_pgn"]==2
    payload,ctype=manager.export(ident,"pgn")
    assert "chess-pgn" in ctype
    assert payload.count(b'[Result "0-1"]')==2
    items=manager.history(ident)["games"]
    assert all(x["pgn_sha256"] and x["engine"] for x in items)
    # A dashboard poll reuses verified PGNs; JSON export performs a full audit.
    original_read=chess.pgn.read_game
    reads=[]
    def tracked(stream):
        reads.append(1)
        return original_read(stream)
    monkeypatch.setattr(chess.pgn,"read_game",tracked)
    manager.detail(ident)
    manager.detail(ident)
    assert not reads, "Repeated status requests should not parse unchanged PGNs"
    manager.export(ident,"json")
    assert reads, "Export must revalidate PGNs from bytes"
    (root/(players[0]+".txt")).write_bytes(b"tampered")
    with pytest.raises(leagues.LeagueInput,match="mudaram"):
        manager.run(ident)
    pgn=manager._dir(ident)/"games"/(items[0]["match_id"]+".pgn")
    pgn.write_text(pgn.read_text(encoding="utf-8")+"\n",encoding="utf-8")
    with pytest.raises(ValueError,match="PGN da liga ausente ou alterado"):
        manager.detail(ident)


def test_tampered_ledger_and_missing_receipt_fail_closed(manager):
    ident=manager.create(config())["id"]
    manager.run(ident);wait(manager,ident)
    directory=manager._dir(ident)
    receipt=next((directory/"receipts").glob("*.json"))
    data=json.loads(receipt.read_text())
    data["result"]="1-0" if data["result"]!="1-0" else "0-1"
    receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError,match="adulterado"):
        manager.detail(ident)
    receipt.unlink()
    with pytest.raises(ValueError,match="Recibo obrigatório"):
        manager.detail(ident)


def test_failed_or_incomplete_engine_game_never_scores(manager,monkeypatch):
    root=manager.root
    exe=root/"rodent-iv-plain.exe";exe.write_bytes(b"synthetic")
    for slug in config()["participants"]:
        (root/(slug+".txt")).write_text(slug)
    monkeypatch.setattr(leagues,"validate",lambda r,w,b:(
        exe,{w.slug:root/(w.slug+".txt"),b.slug:root/(b.slug+".txt")}))
    manager.runner=lambda *args:(None,{"status":"INCOMPLETE",
                                       "result":"*","termination":"PlyLimit"})
    ident=manager.create(config(execution="engine_full"))["id"]
    manager.run(ident)
    status=wait(manager,ident)
    assert status["status"]=="FAILED"
    assert status["completed_games"]==0
    assert manager.history(ident)["total"]==0


def test_pause_and_busy_between_rounds(manager,monkeypatch):
    entered=threading.Event()
    release=threading.Event()
    def blocked(root,match,conf,stop_check):
        entered.set()
        assert release.wait(8)
        raise RuntimeError("synthetic failure")
    root=manager.root
    exe=root/"rodent-iv-plain.exe";exe.write_bytes(b"synthetic")
    for slug in config()["participants"]:
        (root/(slug+".txt")).write_text(slug)
    monkeypatch.setattr(leagues,"validate",lambda r,w,b:(
        exe,{w.slug:root/(w.slug+".txt"),b.slug:root/(b.slug+".txt")}))
    manager.runner=blocked
    ident=manager.create(config(execution="engine_full"))["id"]
    manager.run(ident)
    try:
        assert entered.wait(4)
        with pytest.raises(leagues.LeagueBusy):
            manager.run(ident)
        assert manager.pause(ident)["pause_requested"]
        with pytest.raises(leagues.LeagueBusy):
            manager.pause("league-"+"0"*16)
    finally:
        release.set()
        wait(manager,ident)


def test_restart_recovers_incomplete_round_at_server_start_not_get(manager):
    ident=manager.create(config())["id"]
    state=manager._load(ident)
    state["status"]="RUNNING"
    state["current_match"]="r001-g01"
    manager._save(state)
    restarted=leagues.LeagueManager(manager.root,manager.workspace)
    try:
        manifest=restarted._dir(ident)/"league.json"
        first=manifest.read_bytes()
        data=restarted.detail(ident)
        assert data["status"]=="PAUSED"
        assert data["current_match"] is None
        assert data["message"] and "Retomar" in data["message"]
        for _ in range(3):
            restarted.list()
            restarted.detail(ident)
        assert manifest.read_bytes()==first, "GET must never mutate the manifest"
    finally:
        restarted.shutdown()


def test_virtual_calendar_is_separate_from_real_utc_event_times(manager):
    ident=manager.create(config(n=4,fmt="double"))["id"]
    before=manager.round_detail(ident,1)
    after=manager.round_detail(ident,4)
    assert before["games"][0]["virtual_day"]==1
    assert after["games"][0]["virtual_day"]==22
    assert before["games"][0]["virtual_week"]==1
    assert after["games"][0]["virtual_week"]==4
    manager.run(ident)
    wait(manager,ident)
    recorded=manager.history(ident)["games"]
    assert recorded
    for item in recorded:
        assert item["virtual_week"]==1
        assert item["virtual_day"]==1
        assert item["started_utc"].endswith("+00:00")
        assert item["finished_utc"].endswith("+00:00")
        assert "virtual" not in item["started_utc"]
