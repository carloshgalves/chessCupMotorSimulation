"""Engineering fixtures only; no synthetic game counts are scientific evidence."""
from hashlib import sha256
import json
from types import SimpleNamespace

import chess
import pytest

from chesssim import real85_graded_attribution as graded
from chesssim import real85_neural_stream as stream
from chesssim.matches import _play_one


def _legal_line(variation):
    board=chess.Board()
    line=[]
    for ply in range(14):
        moves=sorted(board.legal_moves,key=lambda move:move.uci())
        move=moves[(variation*7+ply*11)%len(moves)]
        line.append(move.uci())
        board.push(move)
    return line


def _inputs(tmp_path, monkeypatch):
    root=tmp_path/"inputs"
    root.mkdir()
    players={}
    for slug in graded.PLAYERS:
        path=root/f"{slug}-fit.jsonl"
        records=[]
        for color in ("white","black"):
            for variation in range(20):
                moves=_legal_line(variation)
                identity=stream._digest_trajectory(chess.STARTING_FEN,moves)
                if stream.neural_arm(identity)!="fit":
                    continue
                records.append({
                    "player":slug,"arm":"fit",
                    "source_pgn_sha256":"a"*64,
                    "game_sha256":identity,"initial_fen":chess.STARTING_FEN,
                    "focal_color":color,"uci_moves":moves,
                })
        path.write_text("".join(json.dumps(x)+"\n" for x in records))
        players[slug]={
            "status":"READY_ALL_REAL_MOVES_FOR_NEURAL_TRAINING",
            "source_pgn_sha256":"a"*64,
            "shards":{"fit":{"file":path.name}}
        }
    (root/"manifest.json").write_text(json.dumps({"players":players}))
    monkeypatch.setattr(graded,"verify_inputs",lambda _: {
        "status":"VERIFIED_ALL_REAL_NEURAL_INPUT_TRAJECTORIES"})
    return root


def test_schedule_selects_two_legal_distinct_fit_root_fens_per_color(tmp_path,monkeypatch):
    root=_inputs(tmp_path,monkeypatch)
    schedule=graded.choose_roots(root)
    assert schedule["test_or_holdout_used"] is False
    assert schedule["depths"]==[2,4,6]
    assert len(schedule["players"])==4
    for player in graded.PLAYERS:
        for color in ("white","black"):
            roots=schedule["players"][player][color]
            assert len(roots)==2
            assert roots[0]["prefix_uci"][:4]!=roots[1]["prefix_uci"][:4]
            for item in roots:
                board=chess.Board()
                for move in item["prefix_uci"]:
                    board.push_uci(move)
                assert item["fen"]==board.fen()
                assert board.ply()==12
                assert item["count"]>0
    assert schedule==graded.choose_roots(root)


def test_schedule_fails_closed_for_missing_legal_fit_family(tmp_path,monkeypatch):
    root=_inputs(tmp_path,monkeypatch)
    path=root/"fischer-fit.jsonl"
    rows=path.read_text().splitlines()
    first=json.loads(rows[0])
    path.write_text(json.dumps(first)+"\n")
    with pytest.raises(ValueError,match="Not enough distinct"):
        graded.choose_roots(root)


def test_start_fen_rejected_outside_opt_in_historical_root():
    p=SimpleNamespace(gate=None,bindings={"fischer":"overall",
                                        "control-simple":"overall"},
                      player="fischer",min_ply=41,
                      model={"model_sha256":"a"*64})
    player=SimpleNamespace(slug="fischer")
    simple=SimpleNamespace(slug="control-simple")
    args=(None,None,player,simple,4,None,450,1,
          {"exe":"a"*64,"fischer":"b"*64,"control-simple":"c"*64})
    with pytest.raises(ValueError,match="legal 8..20-ply"):
        _play_one(*args,use_books=False,experimental_policy=p,
                  experimental_depths={"fischer":4,"control-simple":2},
                  experimental_start_fen=chess.STARTING_FEN)
    with pytest.raises(ValueError,match="unchanged focal depth"):
        _play_one(*args,use_books=False,experimental_policy=p,
                  experimental_depths={"fischer":3,"control-simple":2})
    with pytest.raises(ValueError,match="only supported"):
        _play_one(*args,use_books=False,experimental_start_fen=chess.STARTING_FEN)


def test_schedule_tamper_is_detected_before_scoring(tmp_path,monkeypatch):
    root=_inputs(tmp_path,monkeypatch)
    schedule=graded.choose_roots(root)
    out=tmp_path/"crossover"
    out.mkdir()
    schedule["players"]["fischer"]["white"][0]["count"]+=100
    (out/"schedule.json").write_text(json.dumps(schedule))
    with pytest.raises(ValueError,match="Forged historical"):
        graded.inspect(out)


def test_phase_uses_material_not_only_number_of_moves():
    board=chess.Board()
    assert graded._phase(board)=="opening_position"
    board.fullmove_number=55
    assert graded._phase(board)=="middlegame_position"
    material=chess.Board("8/8/8/8/8/8/5k2/4K2R w - - 0 55")
    assert graded._phase(material)=="material_endgame"


def test_independent_forced_root_refuses_illegal_move_before_analysis():
    board=chess.Board()
    with pytest.raises(ValueError,match="illegal"):
        graded._root_score(None,board,chess.Move.from_uci("e2e5"),12)
