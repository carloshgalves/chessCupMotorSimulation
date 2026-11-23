"""True-clock Tal candidate validation on strictly unused historical FIT openings."""
from __future__ import annotations

from hashlib import sha256
import json
import random

import chess
import chess.pgn
import pytest

from chesssim import fischer_final_gate as clock
from chesssim import tal_balanced_clock_replication as gate


def synthetic_corpus(monkeypatch):
    rows, games, arms = [], {}, {}
    for i in range(82):
        rng = random.Random(i+101000)
        game = chess.pgn.Game()
        node = game
        for _ in range(20):
            if node.board().is_game_over(claim_draw=True):
                break
            node = node.add_variation(rng.choice(list(node.board().legal_moves)))
        gid = f"{i+1:064x}"
        rows.append({"mainline_sha256":gid,"plies":node.board().ply(),
                     "color":"white" if i%2 else "black"})
        games[gid] = game
        arms[gid] = "fit" if i < 70 else "tune" if i < 75 else "excluded_test"
    monkeypatch.setattr(gate,"ROOTS",6)
    monkeypatch.setattr(gate,"SHARDS",3)
    monkeypatch.setattr(gate,"load_source",lambda archive_dir=None:
                        (b"fixture",rows,{}, "FIXTURE","Tal"))
    monkeypatch.setattr(gate,"HISTORICAL_SHA",sha256(b"fixture").hexdigest())
    monkeypatch.setattr(gate,"neural_arm",lambda gid:arms[gid])
    monkeypatch.setattr(gate,"_iter_verified_games",lambda pgn,subset,surname:
                        ((games[r["mainline_sha256"]],r) for r in subset))
    monkeypatch.setattr(gate,"initial_panel",lambda archive_dir=None:(
        [],{"reserved_test_games":7}))
    monkeypatch.setattr(gate,"doe_design",lambda archive_dir=None:([],{}))
    monkeypatch.setattr(gate,"frozen_fischer",lambda:{
        "run":45097738965,"hashes":{
            "rodent":"a"*64,"fischer_profile":"b"*64,
            "simple_control":"c"*64}})
    # Limit constants are production invariants, monkeypatched ONLY on
    # these explicit fixtures.
    original = gate.fixed_roots
    def smaller(archive_dir=None):
        # Fixture contains real legal synthetic positions, no user-supplied PGN.
        return original(archive_dir)
    monkeypatch.setattr(gate,"fixed_roots",smaller)
    return rows,games,arms


def test_distinct_unseen_fit_panel_and_all_pairings(monkeypatch):
    # A fixture replaces the exact source census to test allocation.
    rows,games,arms=synthetic_corpus(monkeypatch)
    monkeypatch.setattr(gate,"fixed_roots",gate.fixed_roots)
    # These constants intentionally remain pinned in production.
    # Instead use an explicit shadow of the source rows with the census
    # requirement satisfied through a 2,431-entry fixture in another test.
    assert len(rows)==82 and len(games)==82
    roots=[{"id":f"{i:064x}","prefix":[],"arm":"fit"} for i in range(6)]
    cases=list(gate.matchups(roots))
    assert len(cases)==36
    assert len({x["id"] for x in cases})==36
    assert set(x["kind"] for x in cases)==set(gate.GAME_TYPES)
    assert set(x["color"] for x in cases)=={"white","black"}
    assigned=[gate.cases_by_shard(roots,i) for i in range(3)]
    assert [len(group) for group in assigned]==[12,12,12]
    assert sorted(x["id"] for group in assigned for x in group)==sorted(
        x["id"] for x in cases)
    with pytest.raises(ValueError,match="shard"):
        gate.cases_by_shard(roots,3)


def test_clock_slots_include_direct_balanced_vs_original():
    assert gate.roles("original_vs_simple")==("original16","simple16")
    assert gate.roles("balanced_vs_simple")==("balanced16","simple16")
    assert gate.roles("balanced_vs_original")==("balanced16","original16")
    with pytest.raises(ValueError,match="pairing"):
        gate.roles("some-other-control")


def test_terminal_time_replay_and_pairing_hash_cannot_be_faked(monkeypatch):
    monkeypatch.setattr(clock,"initial_board",lambda root:chess.Board())
    moves=[chess.Move.from_uci(x) for x in (
        "e2e4","e7e5","d1h5","b8c6","f1c4","g8f6","h5f7")]
    monkeypatch.setattr(clock,"_clock_choose",lambda engine,board,clocks:
                        (moves[board.ply()],1.5,{"depth":16}))
    case={"id":"a"*64,"root":"b"*64,"kind":"balanced_vs_original",
          "color":"white","time_control":[600,2],"depth":16}
    raw=gate._game(case,{},{
        "balanced16":object(),"original16":object()})
    assert raw["status"]=="COMPLETE" and raw["result"]=="1-0"
    doc={"schema":gate.SCHEMA,"case":case,"manifest_sha256":"c"*64,
         "slot_mapping":{"fischer":"balanced16","simple":"original16"},
         "game":raw}
    assert gate.validate_game(doc,case,{},"c"*64) is raw
    with pytest.raises(ValueError,match="pairing"):
        gate.validate_game({**doc,"slot_mapping":{
            "fischer":"balanced16","simple":"simple16"}},case,{},"c"*64)
    altered=json.loads(json.dumps(doc))
    altered["game"]["result"]="1/2-1/2"
    with pytest.raises(ValueError,match="terminal"):
        gate.validate_game(altered,case,{},"c"*64)


def test_root_cluster_bootstrap_is_not_ply_independent(monkeypatch):
    assert gate.ci_cluster({"one":[1.,0.]}) is None
    flat={f"{i:064x}":[.5,.5] for i in range(48)}
    assert gate.ci_cluster(flat)==[.5,.5]
    assert gate.ci_cluster({**flat,"broken":[.5]}) is None


def test_complete_aggregate_does_not_fake_promotion(monkeypatch,tmp_path):
    monkeypatch.setattr(gate,"ROOTS",4)
    monkeypatch.setattr(gate,"SHARDS",2)
    monkeypatch.setattr(gate,"ci_cluster",lambda groups:[0.,0.])
    monkeypatch.setattr(clock,"initial_board",lambda root:chess.Board())
    opening=[chess.Move.from_uci(m) for m in (
        "e2e4","e7e5","d1h5","b8c6","f1c4","g8f6","h5f7")]
    monkeypatch.setattr(clock,"_clock_choose",lambda engine,board,clocks:
                        (opening[board.ply()],1.5,{"depth":16}))
    roots=[{"id":f"{i+1:064x}","prefix":[],"arm":"fit"}
           for i in range(4)]
    frozen={"hashes":{"rodent":"a"*64,"fischer_profile":"b"*64,
                       "simple_control":"c"*64}}
    source={"fischer_frozen":frozen,"roots":4,"games_planned":24}
    monkeypatch.setattr(gate,"fixed_roots",lambda archive_dir=None:
                        (roots,source))
    original_hashes={"rodent":"a"*64,"fischer":"b"*64,
                     "simple":"c"*64,"tal":gate.TAL_ORIGINAL_SHA}
    fingerprint={"originals":original_hashes,
                 "variants":{"TalBalanced":gate.BALANCED_SHA},
                 "protocol_sha256":gate.file_hash(__import__("pathlib").Path(gate.__file__))}
    for i in range(2):
        directory=tmp_path/f"shard-{i}"
        directory.mkdir()
        cases=gate.cases_by_shard(roots,i)
        manifest={"schema":gate.SCHEMA,"source":source,"index":i,
                  "case_ids":[x["id"] for x in cases],
                  "fingerprints":fingerprint}
        gate.atomic_json(directory/"manifest.json",manifest)
        for case in cases:
            game=clock.play_clock(case,{},object(),object())
            doc={"schema":gate.SCHEMA,"case":case,"game":game,
                 "manifest_sha256":gate.digest(manifest),
                 "slot_mapping":dict(zip(
                     ("fischer","simple"),gate.roles(case["kind"])))}
            gate.atomic_json(directory/(case["id"]+".json"),doc)
    report=gate.aggregate(tmp_path)
    assert report["games_expected"]==24
    assert report["games_observed"]==24
    assert report["censored"]==0
    assert report["clock"]["balanced_vs_original"]["score"]==.5
    assert report["strength_gates"]["balanced_improves_10pp_with_positive_lower_ci"] is False
    assert report["status"]=="NO_CONFIRMED_TAL_BALANCED_CLOCK_STRENGTH_DO_NOT_FREEZE"
    f=next((tmp_path/"shard-0").glob("[0-9a-f]*.json"))
    doc=json.loads(f.read_text())
    doc["game"]["result"]="1/2-1/2"
    f.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match="terminal"):
        gate.aggregate(tmp_path)
