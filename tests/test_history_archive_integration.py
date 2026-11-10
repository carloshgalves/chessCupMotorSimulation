"""Integration: Cup UCI PGN survives archive search, read-only replay and export."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

import chess
import pytest

from chesssim import arena, cup_games, cups
from chesssim import history_archive as archive


class LegalUCI:
    def play(self,board,limit,game,info):
        # Fool's Mate is a complete, legal four-ply game, regardless of
        # which catalog player was seeded with the White pieces.
        uci=("f2f3","e7e5","g2g4","d8h4")[board.ply()]
        move=chess.Move.from_uci(uci)
        assert move in board.legal_moves
        return SimpleNamespace(move=move)
    def quit(self):
        pass
    def close(self):
        pass


def await_complete(manager,ident):
    until=time.monotonic()+15
    while time.monotonic()<until:
        value=manager.detail(ident)
        if not manager.busy and value["status"]!="RUNNING":
            return value
        time.sleep(.03)
    raise AssertionError("Cup worker did not finish")


@pytest.fixture
def verified_cup(tmp_path,monkeypatch):
    root=tmp_path/"engine";root.mkdir()
    workspace=tmp_path/"workspace";workspace.mkdir()
    exe=root/"rodent-iv-plain.exe"
    exe.write_bytes(b"local test engine")
    personalities=root/"personalities";personalities.mkdir()
    for slug in ("fischer","tal"):
        (personalities/(slug+".txt")).write_text("profile",encoding="utf-8")

    def validate(root_arg,white,black):
        assert root_arg==root
        return exe,{slug:(personalities/(slug+".txt"))
                    for slug in (white.slug,black.slug)}
    monkeypatch.setattr(cups,"validate",validate)
    manager=cups.CupManager(root,workspace)
    manager.runner=lambda root_arg,spec,config,resources,stop:(
        cup_games.play_cup_game(root_arg,spec,config,resources,stop,
                                factory=lambda *args,**kw:LegalUCI()))
    try:
        yield manager,workspace
    finally:
        manager.shutdown()


def test_real_cup_is_paginated_and_replay_has_full_legal_states(verified_cup):
    manager,workspace=verified_cup
    ident=manager.create({
        "name":"Copa de Evidência","edition":"2026",
        "preset":"custom","participants":["fischer","tal"],
        "execution_mode":"engine_full","seed":2026,"depth":4,
    })["id"]
    manager.run(ident)
    completed=await_complete(manager,ident)
    assert completed["completed_games"]==1
    assert completed["available_pgn"]==1
    original={p.relative_to(workspace):p.read_bytes()
              for p in (workspace/"cups").rglob("*") if p.is_file()}

    report=archive.archive(workspace,"fischer","origin=cup&page_size=20")
    assert report["overview"]["verified"]==1
    assert report["overview"]["projections"]==0
    assert report["total"]==1
    row=report["games"][0]
    assert row["origin"]=="cup" and row["pgn"] is True
    assert row["source_kind"] == "engine_full"
    assert row["started_at_utc"] and row["finished_at_utc"]
    assert row["duration_seconds"] >= 0
    assert len(row["receipt_sha256"]) == 64
    assert row["event_id"] == ident
    replay=archive.replay(workspace,"fischer",row["id"])
    assert replay["schema"]=="chesssim-archive-replay-v1"
    assert replay["game"]["result"]=="0-1"
    assert len(replay["states"])==5
    assert replay["states"][0]["fen"].startswith("rnbqkbnr/")
    assert replay["states"][-1]["san"]=="Qh4#"
    raw,mime,fmt=archive.export_page(workspace,"fischer","origin=cup","pgn")
    assert fmt=="pgn"
    assert "chess-pgn" in mime
    assert b"Qh4#" in raw
    assert b"Result" in raw

    after={p.relative_to(workspace):p.read_bytes()
           for p in (workspace/"cups").rglob("*") if p.is_file()}
    assert after==original
    with pytest.raises(archive.ArchiveInput):
        archive.replay(workspace,"steinitz",row["id"])


def test_local_get_endpoints_are_strict_and_read_only(tmp_path):
    root=tmp_path/"rodent";root.mkdir()
    workspace=tmp_path/"workspace"
    server=arena.create_server(root,workspace,port=0)
    worker=threading.Thread(target=server.serve_forever,
        kwargs={"poll_interval":.01},daemon=True)
    worker.start()
    base="http://127.0.0.1:"+str(server.server_port)
    try:
        with urlopen(base+"/api/history/players/fischer/archive?page=1") as response:
            body=json.load(response)
            assert response.status==200
            assert body["schema"]==archive.SCHEMA
            assert body["total"]==0
        with urlopen(base+"/api/history/players/fischer/archive/export/csv") as r:
            assert r.status==200
            assert b"opponent_name" in r.read()
        for bad in (
            "/api/history/players/fischer/archive?page=0",
            "/api/history/players/fischer/archive?page=1&page=2",
            "/api/history/players/fischer/archive?origin=unknown",
        ):
            with pytest.raises(HTTPError) as e:
                urlopen(base+bad)
            assert e.value.code==400
        with pytest.raises(HTTPError) as e:
            urlopen(base+"/api/history/archive/replay?slug=fischer&id=projection:fake")
        assert e.value.code==404
        with pytest.raises(HTTPError) as e:
            urlopen(base+"/api/history/players/fischer/archive/export/pgn")
        assert e.value.code==400
        with urlopen(base+"/history_archive.js") as r:
            assert r.status==200 and b"ChessSimHistoryArchive" in r.read()
        with urlopen(base+"/history_archive.css") as r:
            assert r.status==200 and b".archive-hub" in r.read()
    finally:
        server.shutdown()
        server.server_close()
        server.arena_manager.shutdown()
        worker.join(timeout=2)
