"""Pre-registered Fischer product gate: legal play, clocks, style and provenance."""
from __future__ import annotations

import json
import random

import chess
import chess.pgn
import pytest

from chesssim import fischer_final_gate as gate


def sample_history(number):
    rng=random.Random(number+91821)
    game=chess.pgn.Game()
    node=game
    for _ in range(42):
        board=node.board()
        if board.is_game_over(claim_draw=True):
            break
        node=node.add_variation(rng.choice(list(board.legal_moves)))
    return game


def test_selection_uses_121_heldout_games_and_deduplicates_roots(monkeypatch):
    games=[sample_history(i) for i in range(121)]
    rows=[{"mainline_sha256":f"{i+1:064x}","color":"white" if i%2 else "black"}
          for i in range(121)]
    monkeypatch.setattr(gate,"_source",lambda archive_dir=None:
                        (b"synthetic",rows,{}, "LOCAL_TEST","Fischer"))
    monkeypatch.setattr(gate,"neural_arm",lambda id:"excluded_test")
    index={r["mainline_sha256"]:g for r,g in zip(rows,games)}
    monkeypatch.setattr(gate,"_iter_verified_games",lambda pgn,subset,name:
                        ((index[r["mainline_sha256"]],r) for r in subset))
    panel=gate.source_cases()
    assert panel["test_games"]==121
    assert len(panel["roots"])==12
    assert len({tuple(r["prefix"]) for r in panel["roots"]})==12
    assert len(panel["cases"])==24
    assert panel["style_positions"]>=100
    assert {c["color"] for c in panel["cases"]}=={"white","black"}
    assert gate.source_cases()["roots"]==panel["roots"]
    p=gate.plan()
    assert p["historical_test_games"]==121
    assert p["clock_games"]==24
    assert sum(p["game_shards"].values())==24


def test_features_predecision_legal_and_reproducible():
    board=chess.Board()
    e4=chess.Move.from_uci("e2e4")
    assert gate.features(board,e4)=={
        "forcing":False,"check":False,"pawn_move":True,"kingward":False}
    with pytest.raises(ValueError,match="Illegal"):
        gate.features(board,chess.Move.from_uci("d2d5"))


def test_clock_game_replays_real_checkmate_and_rejects_fabricated_result(monkeypatch):
    # Scholar's mate from the start, no engine in the unit-test fixture.
    monkeypatch.setattr(gate,"initial_board",lambda root:chess.Board())
    legal=[chess.Move.from_uci(x) for x in
           ("e2e4","e7e5","d1h5","b8c6","f1c4","g8f6","h5f7")]
    def mock_play(engine,b,clocks):
        return legal[b.ply()],1.5,{"depth":16}
    monkeypatch.setattr(gate,"_clock_choose",mock_play)
    case={"id":"a"*64,"root":"b"*64,"color":"white",
          "opponent":"simple_d16","clock":[600,2]}
    item=gate.play_clock(case,{},object(),object())
    assert item["result"]=="1-0" and item["termination"]=="CHECKMATE"
    assert item["status"]=="COMPLETE"
    assert gate.validate_clock(item,case,{})==item
    with pytest.raises(ValueError,match="terminal"):
        gate.validate_clock({**item,"result":"1/2-1/2"},case,{})
    bad={**item,"move_elapsed":[0.0]*len(legal)}
    with pytest.raises(ValueError,match="Unverifiable"):
        gate.validate_clock(bad,case,{})
    with pytest.raises(ValueError,match="Invalid clock history"):
        gate.validate_clock({**item,"move_elapsed":[-1.0]*len(legal)},case,{})


def test_legal_time_forfeit_replayed_exactly(monkeypatch):
    monkeypatch.setattr(gate,"initial_board",lambda root:chess.Board())
    rng=random.Random(6115)
    def mock_play(engine,board,clocks):
        legal=list(board.legal_moves)
        return rng.choice(legal),75.0,{"depth":16}
    monkeypatch.setattr(gate,"_clock_choose",mock_play)
    case={"id":"a"*64,"root":"b"*64,"color":"white",
          "opponent":"simple_d16","clock":[600,2]}
    item=gate.play_clock(case,{},object(),object())
    # 75s per move; a flag should occur before the classical game ends.
    assert item["termination"]=="TIME_FORFEIT"
    assert item["time_forfeit"] in ("fischer","simple")
    assert gate.validate_clock(item,case,{})==item
    with pytest.raises(ValueError,match="without clock expiration"):
        fake={**item,"flag_spent":.001}
        gate.validate_clock(fake,case,{})
    with pytest.raises(ValueError,match="Unverifiable"):
        fake={**item,"clock_remaining":[600.0,600.0]}
        gate.validate_clock(fake,case,{})


def test_style_checks_bind_original_fen_and_premove_features():
    board=chess.Board()
    human="e2e4"
    expected={"id":"a"*64,"game":"b"*64,"phase":"opening","ply":0,
              "fen_sha256":"c"*64,"human":human}
    # This position does not require an extra repetition probe.
    # The original all-a prefix is divisible by seven. Change the
    # prefix itself: incrementing only the last hex digit never exits.
    expected["id"]="b"+expected["id"][1:]
    assert int(expected["id"][:12],16)%gate.REPEAT_MODULUS!=0
    move=chess.Move.from_uci(human)
    data={"schema":gate.SCHEMA,"id":expected["id"],"game":expected["game"],
          "phase":"opening","ply":0,"human":human,"fischer":human,
          "simple":"d2d4","fen_sha256":expected["fen_sha256"],
          "features":{"human":gate.features(board,move),
                      "fischer":gate.features(board,move),
                      "simple":gate.features(board,chess.Move.from_uci("d2d4"))},
          "repeat_fischer":None,"depth_met":{"fischer":True,"simple":True},
          "manifest_sha256":"d"*64}
    assert gate.validate_style(data,expected,board,"d"*64)==data
    with pytest.raises(ValueError,match="historical"):
        gate.validate_style({**data,"human":"e2e3"},expected,board,"d"*64)
    with pytest.raises(ValueError,match="proxy"):
        doct={**data,"features":{**data["features"],
                    "fischer":{"forcing":True,"check":False,
                               "pawn_move":True,"kingward":False}}}
        gate.validate_style(doct,expected,board,"d"*64)


def _source_summary():
    cells={}
    for arm,n in (("fit",429),("tune",23)):
        for depth in (12,16):
            for color in ("white","black"):
                for mode in ("original","book"):
                    cells[f"{arm}|{depth}|{color}|{mode}"]={
                        "games":n,"wins":int(n*.6 if depth==12 else n*.3),
                        "draws":int(n*.6 if depth==16 else n*.3),
                        "losses":n-int(n*.6 if depth==12 else n*.3)-
                            int(n*.6 if depth==16 else n*.3)}
    return {"schema":"chesssim-fischer-cap-recovery-v1",
            "source_panel_sha256":
            "5f186aefaa7728ae44f150ca8f1e6f399cfb7cdaeaf07ea71e78fd5e9b5eb8cb",
            "source_run_id":gate.SOURCE_GAME_RUN,
            "status":gate.SOURCE_RESULT,"observed_cases":3616,
            "original_complete":3594,"recovered":22,
            "recovery_termination_counts":{"FIFTY_MOVES":22},
            "scores":cells}


def test_baseline_gate_rejects_wrong_or_incomplete_historical_corpus():
    source=_source_summary()
    assert set(gate.source_baseline(source))=={"fit|12","fit|16","tune|12","tune|16"}
    with pytest.raises(ValueError,match="incomplete"):
        gate.source_baseline({**source,"recovered":21})
    with pytest.raises(ValueError,match="incomplete"):
        gate.source_baseline({**source,"source_panel_sha256":"bad"})
    with pytest.raises(ValueError,match="denominators"):
        mutant=json.loads(json.dumps(source))
        mutant["scores"]["fit|16|white|original"]["games"]=5
        gate.source_baseline(mutant)


def test_cluster_confidence_requires_ten_independent_roots():
    assert gate.root_ci([("same",.5)]*100) is None
    bounds=gate.root_ci([(f"r{i}",.5) for i in range(12)])
    assert bounds==[.5,.5]
