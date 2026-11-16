"""Original book and neural middle-game are only combined behind narrow gate."""
from types import SimpleNamespace

import pytest

from chesssim import matches
from chesssim import real85_online_residual as residual
from chesssim.real85_neural_guarded_uci import NeuralGuardedPolicy
from chesssim.catalog import Player
from chesssim.opening_books import SCHEMA as OPENING_SCHEMA


class FakeRodent:
    def play(self, board, limit, **kwargs):
        return SimpleNamespace(move=next(iter(board.legal_moves)))
    def quit(self):
        pass
    def close(self):
        pass


class ForbiddenStockfish:
    def analyse(self, *args, **kwargs):
        raise AssertionError("No neural safe search in book window")


def _setup(monkeypatch,tmp_path):
    monkeypatch.setattr(matches,"_open_engine",lambda *a,**kw:FakeRodent())
    white=Player(1,"Bobby Fischer","fischer","baseline")
    black=Player(2,"Rodent IV Simple","control-simple","baseline")
    state={"schema":residual.SCHEMA,"player":"fischer",
           "gradient_steps":1,"model":residual._initial()}
    p=NeuralGuardedPolicy("fischer",state,ForbiddenStockfish(),min_ply=41)
    book={"schema":OPENING_SCHEMA,"player":"fischer",
          "policy":{"max_plies":40},
          "source":{"pgn_sha256":"f"*64},
          "positions":{"white":{},"black":{}}}
    args=(tmp_path,tmp_path/"not-executed",white,black,4,None,2,1,
          {"exe":"a"*64,"fischer":"b"*64,"control-simple":"c"*64})
    return args,p,book


def test_book_is_allowed_only_before_gated_neural_min_ply(monkeypatch,tmp_path):
    args,policy,book=_setup(monkeypatch,tmp_path)
    pgn,meta=matches._play_one(*args,use_books=False,
                                historical_openings={"fischer":book},
                                opening_min_count=2,experimental_policy=policy)
    assert meta["status"]=="INCOMPLETE"
    assert meta["plies"]==2
    assert len(meta["policy_trace"])==2
    assert all(entry["outcome"] in (
        "WARMUP_RODENT_UNMODIFIED","CONTROL_RODENT_UNMODIFIED")
        for entry in meta["policy_trace"])
    assert pgn.headers["BookMode"]=="historical-v09-exploratory"
    assert pgn.headers["PolicyOfficial"]=="false"


def test_legacy_policy_cannot_ever_mix_books(monkeypatch,tmp_path):
    args,policy,book=_setup(monkeypatch,tmp_path)
    legacy=SimpleNamespace(bindings=policy.bindings,gate=None)
    with pytest.raises(ValueError,match="Only frozen Stockfish-guarded"):
        matches._play_one(*args,use_books=False,
                          historical_openings={"fischer":book},
                          experimental_policy=legacy)
    with pytest.raises(ValueError,match="control opening must stay free"):
        matches._play_one(*args,use_books=False,
                          historical_openings={"fischer":book,"control-simple":book},
                          experimental_policy=policy)
    bad_book={**book,"policy":{"max_plies":41}}
    with pytest.raises(ValueError,match="after the complete historical book"):
        matches._play_one(*args,use_books=False,
                          historical_openings={"fischer":bad_book},
                          experimental_policy=policy)
    early=NeuralGuardedPolicy("fischer",policy.checkpoint,ForbiddenStockfish(),
                              min_ply=40)
    with pytest.raises(ValueError,match="after the complete historical book"):
        matches._play_one(*args,use_books=False,
                          historical_openings={"fischer":book},
                          experimental_policy=early)
    with pytest.raises(ValueError,match="development-only"):
        matches._play_one(*args,use_books=False,clock=(600,2),
                          historical_openings={"fischer":book},
                          experimental_policy=policy)
    with pytest.raises(ValueError,match="internal books"):
        matches._play_one(*args,use_books=True,
                          historical_openings={"fischer":book},
                          experimental_policy=policy)


def test_python_m_entrypoint_uses_canonical_policy_class(monkeypatch):
    """An actual -m entrypoint must not create a foreign __main__ class."""
    import runpy
    from chesssim import real85_neural_guarded_uci as canonical
    calls = []
    def stub_main():
        calls.append(canonical.NeuralGuardedPolicy.__module__)
        return 0
    monkeypatch.setattr(canonical, "main", stub_main)
    with pytest.raises(SystemExit) as stopped:
        runpy.run_module("chesssim.real85_neural_guarded_uci", run_name="__main__")
    assert stopped.value.code == 0
    assert calls == ["chesssim.real85_neural_guarded_uci"]
