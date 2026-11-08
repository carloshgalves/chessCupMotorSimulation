"""Complete local game archive filters, pagination and replay gates (#70)."""
from __future__ import annotations


import pytest

from chesssim import game_archive as arc


def test_filter_schema_and_bounds():
    assert arc.normalize_query({})["offset"]==0
    assert arc.normalize_query({})["limit"]==20
    assert arc.normalize_query({"limit":["100"],"offset":["4000"]})["limit"]==100
    with pytest.raises(arc.ArchiveInput):
        arc.normalize_query({"offset":["-1"]})
    with pytest.raises(arc.ArchiveInput):
        arc.normalize_query({"limit":["101"]})
    with pytest.raises(arc.ArchiveInput):
        arc.normalize_query({"source":["historical_documented"]})
    with pytest.raises(arc.ArchiveInput):
        arc.normalize_query({"q":["x","y"]})
    with pytest.raises(arc.ArchiveInput):
        arc.normalize_query({"date_from":["2026-02-30"]})
    with pytest.raises(arc.ArchiveInput):
        arc.normalize_query({"unknown":["data"]})


def record(i: int, *, source="engine_full", result="1-0", status="COMPLETED"):
    return {
        "id": "arena:session-20261108T023000Z-abcdef123456:test-1:"+str(i),
        "white": "fischer", "black": "tal", "origin": "arena",
        "duel_id": "arena:test-1",
        "time_control": "rapid", "clock": [600,2],
        "recorded_utc": "2026-11-08T02:30:00+00:00",
        "result": result, "status": status,
        "engine_strength_config": None,
        "evidence_pgn_sha256": "a"*64,
    }


def test_empty_archive_does_not_create_files(tmp_path):
    archive=arc.GameArchive(tmp_path)
    report=archive.page("fischer",{})
    assert report["total"]==0
    assert report["games"]==[]
    assert report["overview"]["uci"]==0
    assert report["overview"]["projections"]==0
    assert not (tmp_path/"leagues").exists()
    assert not (tmp_path/"cups").exists()


def test_4032_games_stable_pagination_no_pgn_read(tmp_path,monkeypatch):
    # Simulates a large verified ledger; there are no PGN files to fetch.
    rows=[record(i) for i in range(4032)]
    monkeypatch.setattr(arc,"verified_competition_games",lambda ws:
                        (rows,{"rejected_games":0,"rejected_records":0}))
    archive=arc.GameArchive(tmp_path)
    first=archive.page("fischer",{"offset":["0"],"limit":["50"]})
    assert first["total"]==4032
    assert len(first["games"])==50
    assert first["overview"]["uci"]==4032
    assert len(first["facets"]["competitions"])==1
    last=archive.page("fischer",{"offset":["4000"],"limit":["50"]})
    assert len(last["games"])==32
    assert last["next_offset"] is None
    repeat=archive.page("fischer",{"offset":["4000"],"limit":["50"]})
    assert repeat["games"]==last["games"]
    assert repeat["snapshot"]==first["snapshot"]
    assert all("_pgn_ref" not in row for row in last["games"])


def test_filtered_results_and_synthetic_no_replay(tmp_path,monkeypatch):
    def mocked(_):
        return ([record(1),record(2,result="1/2-1/2"),
                 record(3,status="INCOMPLETE",result=None)],
                {"rejected_games":0,"rejected_records":0})
    monkeypatch.setattr(arc,"verified_competition_games",mocked)
    archive=arc.GameArchive(tmp_path)
    result=archive.page("fischer",{"result":["draw"]})
    assert result["total"]==1
    assert result["games"][0]["outcome"]=="draw"
    assert archive.page("fischer",{"result":["incomplete"]})["total"]==1
    assert archive.page("tal",{"color":["black"]})["total"]==3
    with pytest.raises(arc.ArchiveInput):
        archive.replay("fischer","cup:../secret")
    fake=arc._record(
        ident="league:league-0123456789abcdef:r01-m01",
        competition="Liga",kind="league",white="fischer",black="tal",
        result="1-0",status="COMPLETED",modality="rapid",
        round_number=1,started=None,finished=None,source="fast_statistical",
        replay=False,evidence=None)
    assert fake["pgn_sha256"] is None and fake["has_replay"] is False
    with pytest.raises(arc.ArchiveInput):
        arc._record(**{**{
            "ident":"cup:cup-0123456789abcdef:r01-m01",
            "competition":"Cup","kind":"cup","white":"fischer","black":"tal",
            "result":"1-0","status":"COMPLETED","modality":"classical",
            "round_number":1,"started":None,"finished":None,
            "source":"fast_statistical","replay":True,"evidence":"0"*64,
        }})


def test_csv_export_blocks_spreadsheet_formulas(tmp_path,monkeypatch):
    monkeypatch.setattr(arc,"verified_competition_games",
                        lambda ws: ([],{"rejected_games":0,"rejected_records":0}))
    archive=arc.GameArchive(tmp_path)
    archive.page("fischer",{})
    unsafe=arc._record(
        ident="league:league-0123456789abcdef:r01-m01",
        competition="=CMD('bad')",kind="league",white="fischer",
        black="tal",result="1-0",status="COMPLETED",
        modality="rapid",round_number=1,started=None,finished=None,
        source="fast_statistical",replay=False,evidence=None)
    archive._rows=[unsafe]
    data,mime=archive.export("fischer",{},"csv")
    assert "text/csv" in mime
    assert "'=CMD" in data.decode("utf-8-sig")
    data,mime=archive.export("fischer",{},"json")
    assert "application/json" in mime
    assert b"fast_statistical" in data
    with pytest.raises(arc.ArchiveInput):
        archive.export("fischer",{},"pgn")


def test_symlinked_archive_competition_folder_is_rejected(tmp_path):
    outside=tmp_path.parent/"outside-archive-target"
    outside.mkdir(exist_ok=True)
    (tmp_path/"cups").symlink_to(outside,target_is_directory=True)
    archive=arc.GameArchive(tmp_path)
    with pytest.raises(arc.ArchiveInput,match="redirecionado"):
        archive.page("fischer",{})



def test_actual_league_and_cup_projections_are_visible_without_pgn(tmp_path):
    import time
    from chesssim.leagues import LeagueManager
    from chesssim.cups import CupManager

    root=tmp_path/"engine"
    root.mkdir()
    work=tmp_path/"workspace"
    work.mkdir()
    league=LeagueManager(root,work)
    cup=CupManager(root,work)
    def finish(obj,ident):
        end=time.monotonic()+15
        while time.monotonic()<end:
            data=obj.detail(ident)
            if not obj.busy and data["status"]!="RUNNING":
                return data
            time.sleep(.015)
        raise AssertionError("Competition did not finish")
    try:
        lid=league.create({
            "name":"Liga Piloto","season":"2026","format":"single",
            "modality":"rapid","execution_mode":"fast_statistical",
            "participants":["fischer","tal","lasker","steinitz"],
            "depth":3,"points":{"win":3,"draw":1,"loss":0},"seed":9,
        })["id"]
        league.run(lid)
        assert finish(league,lid)["completed_games"]==2
        cid=cup.create({
            "name":"Copa Piloto","edition":"2026","preset":"custom",
            "participants":["fischer","tal"],
            "execution_mode":"fast_statistical","seed":2026,"depth":4,
        })["id"]
        cup.run(cid)
        assert finish(cup,cid)["completed_games"]==1
        archive=arc.GameArchive(work,leagues=league,cups=cup)
        data=archive.page("fischer",{})
        assert data["total"]>=1
        assert all(x["source_kind"]=="fast_statistical" for x in data["games"])
        assert all(not x["has_replay"] and x["pgn_sha256"] is None
                   for x in data["games"])
        assert data["overview"]["uci"]==0
        assert data["overview"]["projections"]==len(data["games"])
        assert data["overview"]["wins"]==data["overview"]["draws"]==0
        for row in data["games"]:
            with pytest.raises(arc.ArchiveInput):
                archive.replay("fischer",row["id"])
        assert archive.page("fischer",{"source":["engine_full"]})["total"]==0
        assert archive.page("fischer",{"source":["fast_statistical"]})["total"]==data["total"]
        exported,mime=archive.export("fischer",{},"json")
        assert b"fast_statistical" in exported and b'"has_replay": false' in exported
        with pytest.raises(arc.ArchiveInput):
            archive.export("fischer",{},"pgn")
    finally:
        cup.shutdown()
        league.shutdown()
