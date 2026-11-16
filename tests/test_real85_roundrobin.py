"""Real-color-reversed historic UCI round robin, not fabricated Elo."""
import json
import chess.pgn
import pytest

from chesssim import real85_roundrobin as robin
from chesssim import real85_playable as real85
from chesssim.matches import file_hash


def _fixture(tmp_path):
    slugs=("fischer","tal","karpov","kasparov")
    root=tmp_path/"engine"
    (root/"personalities").mkdir(parents=True)
    (root/"personalities/basic.ini").write_text("".join(
        f"{x.capitalize()}={x}.txt\n" for x in slugs))
    for slug in slugs:
        (root/"personalities"/f"{slug}.txt").write_text("real engine profile fixture")
    (root/"rodent-iv-plain.exe").write_text("stub not executed")
    folder=tmp_path/"trained"
    folder.mkdir()
    entries={}
    for slug in slugs:
        book={"schema":"chesssim-opening-v9","player":slug,
              "policy":{"max_plies":24},"positions":{"white":{},"black":{}}}
        path=folder/f"{slug}.json"
        path.write_text(json.dumps(book))
        entries[slug]={"status":"REAL_GAMES_TRAINED_PLAYABLE_REPERTOIRE",
                       "book_sha256":file_hash(path)}
    (folder/"report.json").write_text(json.dumps({
        "schema":real85.SCHEMA,"players":entries}))
    return root,folder,slugs


def test_four_real_personalities_play_12_pairs_with_both_colors(monkeypatch,tmp_path):
    root,folder,slugs=_fixture(tmp_path)
    invocations=[]
    def fake(*args,**kwargs):
        white,black=args[2:4]
        invocations.append((white.slug,black.slug,kwargs["historical_openings"]))
        game=chess.pgn.Game()
        game.headers["White"]=white.name
        game.headers["Black"]=black.name
        game.headers["Result"]="1/2-1/2"
        return game,{"status":"COMPLETED","result":"1/2-1/2",
                     "termination":"DRAW","plies":20,
                     "opening_moves":{"white":3,"black":4}}
    monkeypatch.setattr(robin,"_play_one",fake)
    result=robin.run(root,folder,tmp_path/"matches")
    assert len(invocations)==12
    assert len({(a,b) for a,b,_ in invocations})==12
    assert all(set(books)=={a,b} for a,b,books in invocations)
    assert result["status"]=="ALL_ROUNDROBIN_GAMES_COMPLETE"
    assert result["scheduled_games"]==result["completed_games"]==12
    assert set(result["standings"])==set(slugs)
    assert all(x["scheduled"]==6 and x["completed"]==6 and
               x["points"]==3 for x in result["standings"].values())
    assert sum(x["points"] for x in result["standings"].values())==12
    assert (tmp_path/"matches/games.pgn").is_file()
    assert result["historical_strength_elo_estimated"] is False


def test_roundrobin_rejects_untrained_and_incomplete_does_not_score(
        monkeypatch,tmp_path):
    root,folder,slugs=_fixture(tmp_path)
    with pytest.raises(ValueError,match="distinct"):
        robin.run(root,folder,tmp_path/"no",players=[slugs[0],slugs[0]])
    with pytest.raises(ValueError,match="Rodent personality"):
        robin.run(root,folder,tmp_path/"missing",players=[slugs[0],"mequinho"])
    n=[0]
    def fake(*args,**kwargs):
        n[0]+=1
        game=chess.pgn.Game()
        game.headers["White"]=args[2].name
        game.headers["Black"]=args[3].name
        game.headers["Result"]="*" if n[0]==1 else "1/2-1/2"
        return game,{"status":"INCOMPLETE" if n[0]==1 else "COMPLETED",
                     "result":"*" if n[0]==1 else "1/2-1/2",
                     "termination":"PLY_LIMIT" if n[0]==1 else "DRAW",
                     "plies":450 if n[0]==1 else 18,
                     "opening_moves":{"white":0,"black":0}}
    monkeypatch.setattr(robin,"_play_one",fake)
    r=robin.run(root,folder,tmp_path/"incomplete",players=list(slugs[:2]),
                use_repertoire=False)
    assert r["status"]=="ROUNDROBIN_HAS_REAL_INCOMPLETE_GAMES"
    assert r["scheduled_games"]==2 and r["completed_games"]==1
    assert r["standings"][slugs[0]]["points"]==0.5
    assert r["standings"][slugs[1]]["points"]==0.5
