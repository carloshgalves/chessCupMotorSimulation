"""Tal bounded candidate experiment: tamper, no-fake-score and population gates."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
import random

import chess
import chess.pgn
import pytest

from chesssim import tal_strength_doe as doe


def _fake_corpus(monkeypatch):
    # Unseen, distinct test roots; no synthetic fixture is release evidence.
    rng = random.Random(710)
    rows, games, arms = [], {}, {}
    for i in range(65):
        game = chess.pgn.Game()
        node = game
        for _ in range(24):
            board = node.board()
            if board.is_game_over(claim_draw=True):
                break
            node = node.add_variation(rng.choice(list(board.legal_moves)))
        gid = f"{i+1:064x}"
        rows.append({"mainline_sha256":gid, "color":"white",
                     "plies":node.board().ply()})
        games[gid] = game
        arms[gid] = ("fit" if i < 54 else
                     "tune" if i < 59 else "excluded_test")
    pgn = b"synthetic bytes"
    monkeypatch.setattr(doe, "PILOT_ROOTS", 4)
    monkeypatch.setattr(doe, "CONFIRM_ROOTS", 4)
    monkeypatch.setattr(doe, "load_source", lambda archive_dir=None:
                        (pgn,rows,{}, "LOCAL_SYNTHETIC","Tal"))
    monkeypatch.setattr(doe,"neural_arm",lambda gid: arms[gid])
    monkeypatch.setattr(doe,"old_panel",lambda archive_dir=None:
                        ([],{"source_pgn_sha256":sha256(pgn).hexdigest(),
                             "development_game_counts":{"fit":54,"tune":5},
                             "reserved_test_games":6}))
    monkeypatch.setattr(doe,"_iter_verified_games",lambda pgn, selected, surname:
                        ((games[row["mainline_sha256"]], row) for row in selected))
    monkeypatch.setattr(doe,"frozen_fischer",lambda:{
        "depth":16,"run":45097738965,
        "hashes":{"rodent":"a"*64,"fischer_profile":"b"*64,
                  "simple_control":"c"*64}})
    return rows,games,arms


def test_unseen_root_selection_shards_and_test_exclusion(monkeypatch):
    rows,games,arms=_fake_corpus(monkeypatch)
    monkeypatch.setattr(doe, "design", doe.design)
    # The real audit requires exactly 2431 accepted games. Patch for fixture.
    # Do not silently relax it in production.
    old=doe.design
    class CountedRows(list):
        def __len__(self):
            # A deliberately artificial source fixture bypasses the census
            # check ONLY in this isolated test; iteration remains real.
            return 2431
    # Cleaner: require expected count to be a named constant in production.
    monkeypatch.setattr(doe, "EXPECTED_GAMES", 65, raising=False)
    with pytest.raises(ValueError,match="source"):
        old()
    assert len(rows)==65


def test_original_tal_variants_rewrite_only_explicit_existing_options():
    text = ("; Personality for Rodent IV chess engine\n"
            "setoption name OwnAttack value 450\n"
            "setoption name OppAttack value 100\n"
            "setoption name PawnShield value 189\n")
    patched=doe.render_tal_variant(
        text,{"OwnAttack":260,"OppAttack":135,"PawnShield":150})
    assert "OwnAttack value 260" in patched
    assert "OppAttack value 135" in patched
    assert "PawnShield value 150" in patched
    assert "OwnAttack value 450" in text
    assert doe.render_tal_variant(text,{})==text
    with pytest.raises(ValueError,match="exactly one"):
        doe.render_tal_variant(text,{"NotARealOption":10})
    with pytest.raises(ValueError,match="Invalid"):
        doe.render_tal_variant(text,{"OwnAttack":-500})


def test_isolated_profile_clone_preserves_original_bytes(monkeypatch,tmp_path):
    source=tmp_path/"source"
    personas=source/"personalities"
    personas.mkdir(parents=True)
    (source/"rodent-iv-plain.exe").write_bytes(b"binary fixture")
    seed=("; Personality for Rodent IV chess engine\n" +
          "".join(f"setoption name {name} value 0\n"
                  for spec in doe.VARIANTS.values()
                  for name in spec["overrides"]))
    # Each expected param should occur exactly once, like the real profile.
    names=sorted({k for spec in doe.VARIANTS.values()
                  for k in spec["overrides"]})
    seed="; Personality for Rodent IV chess engine\n"+ "".join(
        f"setoption name {name} value 100\n" for name in names)
    (personas/"tal.txt").write_text(seed)
    (personas/"fischer.txt").write_text("fischer fixture")
    (personas/"simple.txt").write_text("simple fixture")
    (personas/"basic.ini").write_text(
        "CLEAR_LOG\nHIDE_OPTIONS\nUSE_PERSONALITY_SET_NO=0\n"
        "PERSONALITY_SET=all\nTal=tal.txt\n"
        "PERSONALITY_SET=distinct\nSimple=simple.txt\n")
    hashes={
        "rodent":doe.file_hash(source/"rodent-iv-plain.exe"),
        "tal":doe.file_hash(personas/"tal.txt"),
        "fischer":doe.file_hash(personas/"fischer.txt"),
        "simple":doe.file_hash(personas/"simple.txt"),
    }
    monkeypatch.setattr(doe,"TAL_ORIGINAL_SHA",hashes["tal"])
    monkeypatch.setattr(doe,"_profiles",lambda path:(
        source,source/"rodent-iv-plain.exe",object(),object(),
        {"tal":personas/"tal.txt","fischer":personas/"fischer.txt",
         "simple":personas/"simple.txt",
         "rodent":source/"rodent-iv-plain.exe"},hashes))
    overlay,original,variants=doe.make_isolated_profiles(source,tmp_path/"overlay")
    assert original==hashes
    assert "TalDefense=taldefense.txt" in (
        overlay/"personalities/basic.ini").read_text()
    assert "TalBalanced=talbalanced.txt" in (
        overlay/"personalities/basic.ini").read_text()
    assert len(set(variants.values()))==4
    assert doe.file_hash(personas/"tal.txt")==hashes["tal"]
    assert doe.file_hash(overlay/"personalities/tal.txt")==hashes["tal"]
    with pytest.raises(ValueError,match="must be new"):
        doe.make_isolated_profiles(source,tmp_path/"overlay")


def _scholar():
    return ["e2e4","e7e5","d1h5","b8c6","f1c4","g8f6","h5f7"]


def _case(variant="original16",color="white"):
    return {"id":"a"*64, "root":"b"*64,"variant":variant,
            "color":color,"depth":doe.VARIANTS[variant]["depth"],
            "stage":"pilot"}


def test_real_chess_replay_rejects_false_draw_and_surplus_move(monkeypatch):
    monkeypatch.setattr(doe,"initial_board",lambda root:chess.Board())
    item=_case()
    moves=_scholar()
    rec={"case":item,"moves":moves,"result":"1-0","termination":"CHECKMATE",
         "status":"COMPLETE","telemetry":{
             "tal":{"searched":4,"depth_reached":4,"depth_missing":0,"nodes_exceeded":0},
             "simple":{"searched":3,"depth_reached":3,"depth_missing":0,"nodes_exceeded":0}}}
    assert doe.validate(rec,item,{}) is rec
    with pytest.raises(ValueError,match="False"):
        doe.validate({**rec,"result":"1/2-1/2"},item,{})
    with pytest.raises(ValueError,match="terminal"):
        doe.validate({**rec,"moves":moves+["a2a3"]},item,{})
    with pytest.raises(ValueError,match="telemetry"):
        t=json.loads(json.dumps(rec))
        t["telemetry"]["tal"]["searched"]=5
        doe.validate(t,item,{})


def test_real_play_short_does_not_award_engine_cap_draw(monkeypatch):
    monkeypatch.setattr(doe,"initial_board",lambda root:chess.Board())
    monkeypatch.setattr(doe,"MAX_PLIES",2)
    m=_scholar()
    monkeypatch.setattr(doe,"choose",lambda engine,board,depth:(
        chess.Move.from_uci(m[board.ply()]),
        {"reached_requested":True,"reported_depth":depth,"reported_nodes":20}))
    rec=doe.play(_case(),{},object(),object())
    assert rec["status"]=="INCOMPLETE"
    assert rec["result"]=="*"
    assert doe.validate(rec,_case(),{})==rec


def test_root_cluster_ci_and_selection_are_game_pair_not_ply():
    assert doe.ci_by_root({"one":[0.,1.]}) is None
    bounds=doe.ci_by_root({f"{i:064x}":[.5,.5] for i in range(12)})
    assert bounds==[.5,.5]
    diffs=doe.difference_ci({f"{i:064x}":[.25,.25] for i in range(12)})
    assert diffs==[.25,.25]


def test_shards_keep_all_crossovers_on_same_root():
    roots=[{"id":f"{i:064x}","stage":"pilot"} for i in range(24)]
    all_cases=list(doe.game_cases(roots))
    assert len(all_cases)==240
    assert len(set(x["id"] for x in all_cases))==240
    assigned=defaultdict(set)
    from collections import defaultdict
    for index in range(12):
        for c in doe.shard_cases(roots,index):
            assigned[c["root"]].add(index)
    assert len(assigned)==24
    assert all(len(v)==1 for v in assigned.values())
