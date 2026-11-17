"""Legal synthetic engineering fixtures only; no fabricated historical results."""
from hashlib import sha256
import json

import chess
import chess.pgn
import pytest

from chesssim import matches
from chesssim import real85_graded_openings as graded
from chesssim import real85_graded_phase_attribution as attribution
from chesssim.game_diversity import mainline_identity


def _opening(d0: str, serial: int):
    game=chess.pgn.Game()
    board=chess.Board()
    node=game
    for i in range(20):
        legal=sorted(board.legal_moves,key=lambda m:m.uci())
        move=(chess.Move.from_uci(d0) if i==0 else
              legal[(i*13+serial*7) % len(legal)])
        assert move in board.legal_moves
        node=node.add_variation(move)
        board.push(move)
    digest,_=mainline_identity(game)
    return digest,[m.uci() for m in game.mainline_moves()]


def _fixture_input(tmp_path,monkeypatch):
    from chesssim import real85_neural_stream as stream
    monkeypatch.setattr(graded,"verify_stream",lambda _:{"status":
                         "VERIFIED_ALL_REAL_NEURAL_INPUT_TRAJECTORIES"})
    root=tmp_path/"verified-input"
    root.mkdir()
    roster={}
    for slug in graded.PILOTS:
        chosen=[]
        for first in ("e2e4","d2d4","c2c4","g1f3"):
            for serial in range(20):
                digest,moves=_opening(first,serial)
                if stream.neural_arm(digest)=="fit":
                    chosen.append((digest,moves))
                    break
            if len(chosen)>=3:
                break
        assert len(chosen)>=3
        source=sha256(slug.encode()).hexdigest()
        filename=f"{slug}-fit.jsonl"
        path=root/filename
        entries=[{
            "schema":graded.GAME_SCHEMA,"player":slug,"arm":"fit",
            "game_sha256":digest,"source_pgn_sha256":source,
            "initial_fen":chess.STARTING_FEN,
            "uci_moves":moves,
        } for digest,moves in chosen]
        path.write_text("".join(json.dumps(x)+"\n" for x in entries))
        roster[slug]={
            "status":"READY_ALL_REAL_MOVES_FOR_NEURAL_TRAINING",
            "source_pgn_sha256":source,
            "shards":{"fit":{"file":filename,"sha256":sha256(path.read_bytes()).hexdigest()}},
        }
    (root/"manifest.json").write_text(json.dumps({"players":roster}))
    return root


def test_three_distinct_real_fit_source_positions_and_no_outer_test(monkeypatch,tmp_path):
    root=_fixture_input(tmp_path,monkeypatch)
    panel=graded.select_fit_openings(root)
    assert panel["source_arm"]=="fit"
    assert panel["historical_test_used"] is False
    assert panel["starts_per_player"]==3
    for slug,entries in panel["players"].items():
        assert len(entries)==3
        assert len({entry["fen"] for entry in entries})==3
        assert len({tuple(entry["opening_uci_12"][:4]) for entry in entries})==3
        for row in entries:
            from chesssim.real85_neural_stream import neural_arm
            assert neural_arm(row["game_sha256"])=="fit"
            board=chess.Board()
            for encoded in row["opening_uci_12"]:
                board.push_uci(encoded)
            assert board.fen()==row["fen"] and board.ply()==12
    # Deterministic, before any game outcome can influence opening choice.
    assert graded.select_fit_openings(root)==panel


def test_input_shard_change_or_test_arm_rejected(monkeypatch,tmp_path):
    root=_fixture_input(tmp_path,monkeypatch)
    file=root/"fischer-fit.jsonl"
    file.write_text(file.read_text()+"\n")
    with pytest.raises(ValueError,match="shard changed"):
        graded.select_fit_openings(root)


def _policy():
    from chesssim.real85_neural_guarded_uci import NeuralGuardedPolicy
    return NeuralGuardedPolicy("fischer",{
        "schema": "chesssim-real85-online-lowrank-style-residual-v1",
        "player":"fischer","gradient_steps":1,"model":{"context":{},"action":{}}
    },stockfish=object(),confirmation_depth=12,min_ply=41)


class _FakeEngine:
    def quit(self): pass
    def close(self): pass


def test_true_fen_start_accepts_only_development_policy_with_graded_control(monkeypatch,tmp_path):
    white=next(p for p in graded.load_roster() if p.slug=="fischer")
    black=graded.Player(number=0,name="Rodent IV Simple",slug="control-simple",
                        group="baseline",rodent_alias="Simple")
    board=chess.Board()
    for move in "e2e4 e7e5 g1f3 b8c6 f1c4 f8c5 c2c3 g8f6 d2d3 d7d6 e1g1 e8g8".split():
        board.push_uci(move)
    assert board.ply()==12
    monkeypatch.setattr(matches,"_open_engine",lambda *a,**kw:_FakeEngine())
    args=dict(root=tmp_path,exe=tmp_path/"original-engine",white=white,black=black,
              depth=4,movetime=None,max_plies=150,round_number=1,
              hashes={"exe":"a"*64,"fischer":"b"*64,"control-simple":"c"*64},
              use_books=False,stop_check=lambda:True,
              experimental_policy=_policy(),
              experimental_depths={"fischer":4,"control-simple":6},
              experimental_start_fen=board.fen())
    game,meta=matches._play_one(**args)
    assert meta["status"]=="INCOMPLETE"
    assert game.board().fen()==board.fen()
    assert game.headers["ExperimentalDepthWhite"]=="4"
    assert game.headers["ExperimentalDepthBlack"]=="6"
    assert game.headers["ExperimentalOpeningSource"]=="REAL85_FIT_START_12_PLIES"
    with pytest.raises(ValueError,match="fixed focal Rodent"):
        matches._play_one(**{**args,"experimental_depths":{
            "fischer":5,"control-simple":6}})
    with pytest.raises(ValueError,match="12 plies"):
        matches._play_one(**{**args,"experimental_start_fen":chess.STARTING_FEN})
    with pytest.raises(ValueError,match="guarded UCI-only"):
        matches._play_one(**{**args,"experimental_policy":None})


def test_phase_is_position_based_and_not_just_fullmove_number():
    from chesssim.real85_playable import _material_endgame
    opening=chess.Board()
    assert attribution.phase(opening)=="opening"
    middle=chess.Board()
    middle.fullmove_number=27
    assert not _material_endgame(middle)
    assert attribution.phase(middle)=="middlegame"
    end=chess.Board("8/8/8/8/8/8/4k3/4K3 w - - 0 27")
    assert attribution.phase(end)=="endgame"


def test_book_counterfactual_never_called_rodent_without_original_search():
    import inspect
    source=inspect.getsource(attribution.sample)
    assert '"original_rodent_move"' in source
    assert 'None if kind=="repertoire"' in source
    assert graded._points("1-0",True)==1.0
    assert graded._points("1-0",False)==0.0
    with pytest.raises(ValueError,match="incomplete"):
        graded._points("*",True)
