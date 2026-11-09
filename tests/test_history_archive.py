"""#70 local match archive — pagination, filters, isolation, no fake PGNs."""
from __future__ import annotations

import json
import time

import pytest

from chesssim import history_archive as archive
from chesssim.cups import CupManager


def record(n, *, origin="arena", status="COMPLETED", result="1-0",
           white="fischer", black="tal", date=None, modality="classical"):
    return {
        "id":f"{origin}:fixture-{n:04d}","duel_id":f"{origin}:fixture",
        "white":white,"black":black,"status":status,"result":result,
        "origin":origin,"recorded_utc":date or "2026-11-08T11:00:00+00:00",
        "time_control":modality,"clock":[5400,30],
        "evidence_pgn_sha256":"a"*64,"engine_strength_config":None,
    }


@pytest.mark.parametrize("query",[
    "page=0","page_size=51","page=nan","page=-1",
    "page=1&page=2","unknown=x","origin=evil","modality=bogus",
    "result=draw&result=win","q="+"a"*81,
    "year_from=1801&year_to=1800","year_from=1",
    "year_from=202x","page=16385",
    "date_from=2026-02-30","date_from=2026-12-01&date_to=2026-11-01",
    "color=green","opponent=../../bad","engine_sha256=aa",
])
def test_strict_query_validation(query):
    with pytest.raises(archive.ArchiveInput):
        archive.parameters(query)


def test_filter_sort_page_and_summary_are_from_evidence(monkeypatch,tmp_path):
    workspace=tmp_path/"workspace";workspace.mkdir()
    rows=[record(n,white="tal" if n%2 else "fischer",
                 black="fischer" if n%2 else "tal",
                 result="1-0" if n%3 else "1/2-1/2",
                 modality="rapid" if n%2 else "classical") for n in range(31)]
    monkeypatch.setattr(archive,"verified_competition_games",lambda root, **kwargs:(
        rows,{"rejected_records":0,"partial":False}))
    monkeypatch.setattr(archive,"_projections",lambda *args:([],0))
    first=archive.archive(workspace,"fischer","page=1&page_size=20")
    assert first["schema"]==archive.SCHEMA
    assert first["total"]==31 and first["page"]==1 and first["pages"]==2
    assert len(first["games"])==20
    assert first["games"][0]["id"]=="arena:fixture-0030"
    assert first["overview"]["verified"]==31
    assert first["overview"]["projections"]==0
    assert sum(m["games"] for m in first["overview"]["modalities"].values())==31
    second=archive.archive(workspace,"fischer","page=2&page_size=20")
    assert len(second["games"])==11
    subset=archive.archive(workspace,"fischer","modality=rapid&result=win")
    assert all(x["modality"]=="rapid" and x["outcome"]=="win"
               for x in subset["games"])
    assert subset["overview"]==first["overview"]
    assert archive.archive(workspace,"fischer","q=Tal")["total"]==31
    assert archive.archive(workspace,"fischer","q=Kasparov")["total"]==0
    assert archive.archive(workspace,"fischer","year_from=2026&year_to=2026")["total"]==31
    assert archive.archive(workspace,"fischer","year_from=2027")["total"]==0
    assert sum(x["games"] for x in first["competitions"])==31


def test_projections_are_typed_and_do_not_enter_verified_stats(tmp_path):
    engine=tmp_path/"rodent";engine.mkdir()
    work=tmp_path/"workspace";work.mkdir()
    manager=CupManager(engine,work)
    try:
        ident=manager.create({
            "name":"Copa sintética","edition":"Edição 1",
            "preset":"custom","participants":["fischer","tal"],
            "execution_mode":"fast_statistical","seed":2026,"depth":6,
        })["id"]
        manager.run(ident)
        until=time.monotonic()+12
        while manager.busy and time.monotonic()<until:
            time.sleep(.02)
        assert manager.detail(ident)["completed_games"]==1
        report=archive.archive(work,"fischer")
        assert report["overview"]["verified"]==0
        assert report["overview"]["projections"]==1
        row=report["games"][0]
        assert row["origin"]=="projection"
        assert row["rating_eligible"] is False
        assert row["pgn"] is False
        assert row["id"].startswith("projection:"+ident)
        with pytest.raises(archive.ArchiveInput):
            archive.replay(work,"fischer",row["id"])
        with pytest.raises(archive.ArchiveInput,match="Página sem PGN"):
            archive.export_page(work,"fischer","","pgn")
        before={p.relative_to(work):p.read_bytes()
                for p in (work/"cups").rglob("*") if p.is_file()}
        archive.archive(work,"tal","origin=projection")
        after={p.relative_to(work):p.read_bytes()
               for p in (work/"cups").rglob("*") if p.is_file()}
        assert before==after
    finally:
        manager.shutdown()


def test_verified_rows_and_export_are_bounded(monkeypatch,tmp_path):
    workspace=tmp_path/"workspace";workspace.mkdir()
    sample=[record(1),record(2,origin="tournament",status="INCOMPLETE",
                             result=None)]
    monkeypatch.setattr(archive,"verified_competition_games",lambda _, **kwargs:
        (sample,{"rejected_records":1,"partial":True}))
    monkeypatch.setattr(archive,"_projections",lambda *args:([],0))
    report=archive.archive(workspace,"fischer")
    assert report["coverage"]["partial"] is True
    assert report["overview"]["verified"]==2
    assert report["overview"]["completed"]==1
    assert report["games"][0]["pgn"] is False
    assert report["games"][1]["pgn"] is True
    csv_data,mime,extension=archive.export_page(workspace,"fischer","","csv")
    assert extension=="csv" and "text/csv" in mime
    assert "opponent_name" in csv_data.decode("utf-8-sig")
    raw,mime,extension=archive.export_page(workspace,"fischer","","json")
    assert extension=="json"
    assert json.loads(raw)["overview"]["verified"]==2


def test_no_game_id_can_read_outside_verified_record(monkeypatch,tmp_path):
    workspace=tmp_path/"workspace";workspace.mkdir()
    monkeypatch.setattr(archive,"verified_competition_games",lambda root:
        ([],{"rejected_records":0,"partial":False}))
    with pytest.raises(archive.ArchiveInput):
        archive.replay(workspace,"fischer","../../etc/passwd")
    with pytest.raises(archive.ArchiveInput):
        archive.replay(workspace,"fischer","arena:fixture-1")


def test_noncanonical_slug_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        archive.archive(tmp_path,"../fischer")


def test_metadata_filters_and_provenance_exports(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    proof = {"engine_sha256": "b" * 64,
             "white_profile_sha256": "c" * 64,
             "black_profile_sha256": "d" * 64}
    rows = [dict(record(1), engine_strength_config=proof,
                 recorded_utc="2026-11-08T11:00:00+00:00",
                 started_at_utc="2026-11-08T11:05:00+00:00",
                 finished_at_utc="2026-11-08T11:07:00+00:00",
                 duration_seconds=120)]
    monkeypatch.setattr(archive, "verified_competition_games",
                        lambda root, **kwargs: (rows, {"partial": False}))
    monkeypatch.setattr(archive, "_projections", lambda *args: ([], 0))
    for query in ("color=black", "opponent=kasparov",
                  "date_from=2026-11-09", "date_to=2026-11-07",
                  "engine_sha256=" + "a" * 64):
        assert archive.archive(workspace, "fischer", query)["total"] == 0
    good = ("color=white&opponent=tal&date_from=2026-11-08&"
            "date_to=2026-11-08&engine_sha256=" + "b" * 64)
    report = archive.archive(workspace, "fischer", good)
    assert report["total"] == 1
    row = report["games"][0]
    assert row["engine_sha256"] == "b" * 64
    assert row["evidence_pgn_sha256"] == "a" * 64
    assert row["duration_seconds"] == 120
    assert row["started_at_utc"].endswith("+00:00")
    assert row["license_status"] == "not_assessed"
    csv_data, _, _ = archive.export_page(workspace, "fischer", good, "csv")
    assert b"engine_sha256" in csv_data and b"evidence_pgn_sha256" in csv_data
    assert b"started_at_utc" in csv_data and b"license_status" in csv_data


def test_4032_game_pagination_reuses_versioned_index(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "leagues" / "league-0123456789abcdef"
    source.mkdir(parents=True)
    (source / "league.json").write_text("v1", encoding="utf-8")
    rows = [record(i, origin="league", modality="rapid") for i in range(4032)]
    scanned = []
    def read_records(root, **kwargs):
        scanned.append(1)
        return rows, {"partial": False, "rejected_records": 0}
    monkeypatch.setattr(archive, "verified_competition_games", read_records)
    monkeypatch.setattr(archive, "_projections", lambda *args: ([], 0))
    first = archive.archive(workspace, "fischer", "page=1&page_size=50")
    last = archive.archive(workspace, "fischer", "page=81&page_size=50")
    assert first["total"] == last["total"] == 4032
    assert first["pages"] == last["pages"] == 81
    assert len(first["games"]) == 50 and len(last["games"]) == 32
    assert len({x["id"] for x in first["games"] + last["games"]}) == 82
    assert scanned == [1], "page changes must not reparse every PGN"
    # A changed receipt/manifest invalidates the index and forces re-audit.
    (source / "league.json").write_text("v2-altered", encoding="utf-8")
    archive.archive(workspace, "fischer", "page=2")
    assert scanned == [1, 1]
