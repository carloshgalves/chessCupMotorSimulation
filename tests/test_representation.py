"""Representation lab: evidence-only, opt-in PGN, no artificial accuracy."""
from __future__ import annotations


import pytest

from chesssim import representation as rep


def root(tmp_path):
    engine=tmp_path/"rodent"
    engine.mkdir()
    exe=engine/"rodent-iv-plain.exe"
    exe.write_bytes(b"fixture-engine")
    profiles=engine/"personalities"
    profiles.mkdir()
    # Fischer is known to have a versioned Rodent IV alias.
    person=rep._player("fischer")
    (profiles/person.profile_filename).write_text(
        "setoption name OwnAttack value 100\n",encoding="utf-8")
    work=tmp_path/"workspace"
    work.mkdir()
    return engine,work


def test_input_strict_schema_and_defensive_validations(tmp_path):
    with pytest.raises(rep.RepresentationInput):
        rep._player("../fischer")
    good={"match_name":"Bobby Fischer","from_year":1970,"to_year":1972,
          "depth":4,"positions":20,"seed":2026}
    assert rep.normalize_test_request(good)==good
    for key,invalid in [("depth",True),("positions",0),("match_name","a/../../x"),
                        ("from_year",2027),("seed",-1)]:
        with pytest.raises(rep.RepresentationInput):
            rep.normalize_test_request({**good,key:invalid})
    with pytest.raises(rep.RepresentationInput):
        rep.normalize_test_request({**good,"unknown":123})


def test_no_pgn_no_fabricated_curve_or_accuracy(tmp_path):
    engine,work=root(tmp_path)
    lab=rep.RepresentationLab(engine,work)
    status=lab.status("fischer")
    assert status["schema"]=="chesssim-representation-v1"
    assert status["source_available"] is False
    assert status["installed"] is True
    assert status["rating_history"]==[]
    assert all(value is None for value in status["measures"].values())
    assert status["reports"]==[]
    with pytest.raises(rep.RepresentationInput,match="PGN local ausente"):
        lab.start("fischer",{"match_name":"Fischer",
            "from_year":1970,"to_year":1972,
            "depth":4,"positions":20,"seed":2026})


def test_symlinks_in_opt_in_source_are_rejected(tmp_path):
    engine,work=root(tmp_path)
    folder=work/"representation_sources"
    folder.mkdir()
    outside=tmp_path/"private.pgn"
    outside.write_text("[Event \"private\"]",encoding="utf-8")
    (folder/"fischer.pgn").symlink_to(outside)
    with pytest.raises(rep.RepresentationInput,match="redirecionado"):
        rep.representation_status(engine,work,"fischer")


def test_external_uci_evidence_report_is_reproduced_and_read_only(tmp_path,monkeypatch):
    engine,work=root(tmp_path)
    folder=work/"representation_sources"
    folder.mkdir()
    path=folder/"fischer.pgn"
    path.write_text("[Event \"verified fixture\"]\n",encoding="utf-8")
    example={"game":"a"*64,"fen":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
             "move":"e2e4","color":"white","year":1972}
    decisions=[dict(example,game=f"{i:064x}") for i in range(20)]
    stats={"games_accepted":20,"decisions":20}
    def extract(path,**kwargs):
        assert path==folder/"fischer.pgn"
        assert kwargs["player_name"]=="Fischer"
        return decisions,stats
    def split(rows,**kwargs):
        assert len(rows)==20
        return rows[:5],rows[5:10],rows[10:15],{
            "validation":{"games_sampled":5},
            "partition_sha256":"d"*64}
    def evaluate(root_dir,profile,positions,depth,timeout):
        assert root_dir==engine and depth==4 and timeout==20
        assert len(positions)==5
        return {"decisions":5,"correct":2,"top1":0.4,
                "_predicted_moves":["d2d4"]*5}
    monkeypatch.setattr(rep,"extract_decisions",extract)
    monkeypatch.setattr(rep,"split_three_ways",split)
    lab=rep.RepresentationLab(engine,work,evaluator=evaluate)
    input={"match_name":"Fischer","from_year":1970,"to_year":1972,
           "depth":4,"positions":20,"seed":2026}
    response=lab.start("fischer",input)
    assert response["status"]=="RUNNING"
    lab.shutdown()
    result=lab.status("fischer")
    assert result["active"] is None
    assert result["last"]["status"]=="COMPLETED"
    assert len(result["reports"])==1
    report=result["reports"][0]
    assert report["sample"]["top1_rate"]==0.4
    assert report["status"]=="EXPLORATORY_NOT_HISTORICALLY_VALIDATED"
    assert report["evidence"]["source_pgn_sha256"]==rep._sha(path)
    assert "_predicted_moves" not in str(report)
    original=(work/"representation_results"/"fischer"/
              (response["id"]+".json")).read_bytes()
    lab.status("fischer")
    assert original==(work/"representation_results"/"fischer"/
                     (response["id"]+".json")).read_bytes()
    # A second reader must neither rerun the evaluator nor mutate artifacts.
    another=rep.RepresentationLab(engine,work)
    assert another.status("fischer")["reports"][0]==report


def test_failed_evaluation_never_creates_bogus_scores(tmp_path,monkeypatch):
    engine,work=root(tmp_path)
    folder=work/"representation_sources"
    folder.mkdir()
    (folder/"fischer.pgn").write_text("invalid PGN",encoding="utf-8")
    def fail(*args,**kwargs):
        raise ValueError("Nenhuma amostra histórica verificável")
    monkeypatch.setattr(rep,"extract_decisions",fail)
    lab=rep.RepresentationLab(engine,work)
    lab.start("fischer",{"match_name":"Fischer","from_year":1970,
             "to_year":1972,"depth":4,"positions":20,"seed":2026})
    lab.shutdown()
    assert lab.status("fischer")["last"]["status"]=="FAILED"
    assert lab.status("fischer")["reports"]==[]
    assert not (work/"representation_results").exists()


def test_persisted_report_checksum_is_mandatory_and_covers_metrics(tmp_path,monkeypatch):
    import json
    engine,work=root(tmp_path)
    folder=work/"representation_sources"
    folder.mkdir()
    (folder/"fischer.pgn").write_text("[Event \"local\"]\n",encoding="utf-8")
    monkeypatch.setattr(rep,"extract_decisions",lambda *args,**kwargs:(
        [{"game":str(i),"fen":"start","move":"e2e4","color":"white",
          "year":1971} for i in range(20)],
        {"games_accepted":20}))
    monkeypatch.setattr(rep,"split_three_ways",lambda decisions,**kwargs:(
        decisions[:4],decisions[4:9],decisions[9:14],
        {"validation":{"games_sampled":5},"partition_sha256":"e"*64}))
    lab=rep.RepresentationLab(engine,work,
        evaluator=lambda *args,**kwargs:{"decisions":5,"correct":2})
    result=lab.start("fischer",{"match_name":"Fischer",
        "from_year":1970,"to_year":1972,"depth":4,"positions":20,"seed":2026})
    lab.shutdown()
    target=work/"representation_results"/"fischer"/(result["id"]+".json")
    original=json.loads(target.read_text())
    assert original["report_sha256"]==rep._report_digest(original)
    corrupted=dict(original)
    corrupted["sample"]=dict(original["sample"],correct_top1=5)
    target.write_text(json.dumps(corrupted),encoding="utf-8")
    with pytest.raises(rep.RepresentationInput,match="schema"):
        rep.RepresentationLab(engine,work).status("fischer")
    corrupted.pop("report_sha256")
    target.write_text(json.dumps(corrupted),encoding="utf-8")
    with pytest.raises(rep.RepresentationInput,match="schema"):
        rep.RepresentationLab(engine,work).status("fischer")
