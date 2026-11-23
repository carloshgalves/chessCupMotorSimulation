"""Tal source inventory; synthetic unit fixtures never certify real PGN."""
import random

import chess
import chess.pgn
import pytest

from chesssim import tal_product_plan as tal


def synthetic_corpus():
    rows=[]
    games={}
    arms={}
    for i in range(36):
        rng=random.Random(90900+i)
        game=chess.pgn.Game()
        node=game
        for _ in range(30):
            if node.board().is_game_over(claim_draw=True):
                break
            node=node.add_variation(rng.choice(list(node.board().legal_moves)))
        gid=f"{i+1:064x}"
        row={"mainline_sha256":gid,"color":"white" if i%2 else "black",
             "date":1960+i%20,"plies":node.board().ply()}
        rows.append(row)
        games[gid]=game
        arms[gid]="fit" if i<26 else "tune" if i<31 else "excluded_test"
    return rows,games,arms


def install(monkeypatch,rows,games,arms):
    monkeypatch.setattr(tal,"load_source",lambda archive_dir=None:
                        (b"synthetic",rows,{}, "LOCAL_TEST","Tal"))
    monkeypatch.setattr(tal,"neural_arm",lambda gid:arms[gid])
    monkeypatch.setattr(tal,"_iter_verified_games",lambda pgn,selected,surname:
                        ((games[row["mainline_sha256"]],row) for row in selected))


def test_complete_source_all_games_and_all_focal_decisions(monkeypatch):
    rows,games,arms=synthetic_corpus()
    install(monkeypatch,rows,games,arms)
    doc=tal.plan()
    assert doc["accepted_games"]==36
    assert doc["status"]=="FULL_SOURCE_INVENTORY_ONLY_STRENGTH_NOT_TESTED"
    assert doc["arms"]["fit"]["games"]==26
    assert doc["arms"]["tune"]["games"]==5
    assert doc["arms"]["excluded_test"]["games"]==5
    assert len(doc["development_roots_12_ply"])==12
    assert doc["pilot_clock_games_planned"]==24
    assert doc["competitive_rating_or_elo"] is None
    assert doc["frozen"] is False
    assert doc["fischer_asset_untouched"] is True
    for arm in tal.ARMS:
        values=doc["arms"][arm]
        assert values["games"]==values["eligible_root_games"]+values["root_exclusions"]
        assert values["nonforced"]+values["forced"]==values["focal_decisions"]
        phases=sum(doc["phases"][f"{arm}|{stage}"].get("nonforced",0)
                   for stage in tal.STAGES)
        assert phases==values["nonforced"]
    assert len(doc["years"])>0
    assert all(doc["arms"][a]["nonforced"]>0 for a in tal.ARMS)


def test_no_implicit_repartition_or_silent_shrinking(monkeypatch):
    rows,games,arms=synthetic_corpus()
    install(monkeypatch,rows,games,arms)
    full=tal.plan()
    # Playing order cannot alter deterministic root selection.
    rows.reverse()
    again=tal.plan()
    assert full["development_roots_12_ply"]==again["development_roots_12_ply"]
    rows.pop()
    with pytest.raises(ValueError,match="scan does not reconcile"):
        # A selected source meta row with no matching verified replay fails.
        monkeypatch.setattr(tal,"_iter_verified_games",lambda pgn,selected,surname:
                            iter(()))
        tal.plan()


def test_insufficient_roots_and_wrong_split_fail_closed(monkeypatch):
    rows,games,arms=synthetic_corpus()
    install(monkeypatch,rows,games,arms)
    for x in list(arms):
        arms[x]="tune"
    with pytest.raises(ValueError,match="Missing one"):
        tal.plan()
    with pytest.raises(ValueError,match="protocol constant"):
        tal.choose_development_roots({"fit":{}},count=11)


def test_historical_rates_not_confused_with_motor_fidelity(monkeypatch):
    rows,games,arms=synthetic_corpus()
    install(monkeypatch,rows,games,arms)
    doc=tal.plan()
    assert doc["observed_historical_move_preferences"]["fit|opening"]["decisions"]>0
    assert all(x["provisional_frequencies"]=={
        "forcing":None,"check":None,"pawn_move":None,"kingward":None
    } for key,x in doc["observed_historical_move_preferences"].items() if not x["decisions"])
    assert doc["validation_test_games_opened_for_training"]==0
    assert doc["test_style_frequency_published"] is False
    assert all(doc["observed_historical_move_preferences"][f"excluded_test|{stage}"]
               ["decisions"]==0 for stage in tal.STAGES)
