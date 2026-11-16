"""Real UCI round-robin among four trained historical profiles, with true PGNs.

No classifier is asked to separate similar styles. Only the actual outcome
of full games, color-reversed, counts for this pilot's relative scoreboard.
"""
from __future__ import annotations

import argparse
from itertools import combinations
import json
from pathlib import Path

from .catalog import load_roster
from .matches import _play_one, file_hash
from .opening_books import load_opening_book
from .real85_playable import SCHEMA as TRAIN_SCHEMA, _style_of_engine_game, _write_new
from .registry import registered_aliases

SCHEMA = "chesssim-real85-four-player-roundrobin-uci-v1"


def run(root: Path, trained_dir: Path, out: Path, *,
        players: list[str] | None = None, depth: int = 4,
        max_plies: int = 450, use_repertoire: bool = True) -> dict:
    participants = list(players or ("fischer","tal","karpov","kasparov"))
    if len(participants) < 2 or len(participants) > 16 or len(set(participants)) != len(participants):
        raise ValueError("Round robin needs 2..16 distinct historical players")
    if not 1 <= depth <= 12 or not 100 <= max_plies <= 800:
        raise ValueError("Invalid UCI search or game length budget")
    root = Path(root).resolve(strict=True)
    trained_dir = Path(trained_dir)
    out = Path(out)
    if out.exists() or out.is_symlink() or not out.parent.is_dir():
        raise FileExistsError("Real round-robin output directory must be new")
    manifest = json.loads((trained_dir/"report.json").read_text())
    if manifest.get("schema") != TRAIN_SCHEMA:
        raise ValueError("The profile set was not actually trained on genuine PGNs")
    engine = root/"rodent-iv-plain.exe"
    if not engine.is_file():
        raise FileNotFoundError(engine)
    registry = registered_aliases(root/"personalities"/"basic.ini")
    catalog = {p.slug:p for p in load_roster()}
    source_hashes = {}
    book = {}
    for slug in participants:
        player = catalog.get(slug)
        row = manifest["players"].get(slug)
        if player is None or player.rodent_alias is None or (
                registry.get(player.rodent_alias) != player.profile_filename):
            raise ValueError(f"No authentic runnable Rodent personality: {slug}")
        if not isinstance(row, dict) or row.get("status") != (
                "REAL_GAMES_TRAINED_PLAYABLE_REPERTOIRE"):
            raise ValueError(f"No genuine trained historical corpus for {slug}")
        source = trained_dir/f"{slug}.json"
        if file_hash(source) != row["book_sha256"]:
            raise ValueError("Historical repertoire file was modified")
        profile = root/"personalities"/player.profile_filename
        if not profile.is_file():
            raise FileNotFoundError(profile)
        source_hashes[slug] = file_hash(profile)
        book[slug] = load_opening_book(source, slug)
    exe_sha = file_hash(engine)
    games, pgns = [], []
    counts = {p:{"scheduled":0,"completed":0,"points":0.0,
                 "historical_book_plies":0,"opponents":{}} for p in participants}
    for a,b in combinations(participants,2):
        for color_idx,(white_slug,black_slug) in enumerate(((a,b),(b,a))):
            white,black=catalog[white_slug],catalog[black_slug]
            pgn, meta=_play_one(
                root, engine, white, black, depth=depth, movetime=None,
                max_plies=max_plies, round_number=color_idx+1,
                hashes={"exe":exe_sha,white.slug:source_hashes[white.slug],
                        black.slug:source_hashes[black.slug]},
                use_books=False,
                historical_openings=({white.slug:book[white.slug],
                                      black.slug:book[black.slug]}
                                     if use_repertoire else None),
                opening_min_count=2, opening_seed=3219,
            )
            pgn.headers["ExperimentalProfile"]="true"
            pgn.headers["TrueResultOnly"]="true"
            pgn.headers["TrainingProtocol"]="real85_games_each_profile"
            pgns.append(str(pgn))
            complete=meta["status"]=="COMPLETED"
            result=meta["result"]
            if complete and result not in ("1-0","0-1","1/2-1/2"):
                raise ValueError("Completed UCI match without actual outcome")
            record={"white":white.slug,"black":black.slug,"status":meta["status"],
                    "result":result,"termination":meta["termination"],
                    "plies":meta["plies"],"book_plies":meta["opening_moves"],
                    "styles":{
                        white.slug:_style_of_engine_game(pgn,True),
                        black.slug:_style_of_engine_game(pgn,False)}}
            games.append(record)
            for slug,opponent in ((white.slug,black.slug),(black.slug,white.slug)):
                count=counts[slug]
                count["scheduled"]+=1
                count["completed"]+=int(complete)
                count["historical_book_plies"]+=meta["opening_moves"][
                    "white" if slug==white.slug else "black"]
                if opponent not in count["opponents"]:
                    count["opponents"][opponent]={"games":0,"points":0.0}
                if complete:
                    points=(0.5 if result=="1/2-1/2" else
                            float((result=="1-0")== (slug==white.slug)))
                    count["points"]+=points
                    count["opponents"][opponent]["points"]+=points
                    count["opponents"][opponent]["games"]+=1
    report={
        "schema":SCHEMA,
        "status":("ALL_ROUNDROBIN_GAMES_COMPLETE" if all(
            r["status"]=="COMPLETED" for r in games) else
            "ROUNDROBIN_HAS_REAL_INCOMPLETE_GAMES"),
        "players":participants,"games":games,"standings":counts,
        "engine_sha256":exe_sha,"depth":depth,
        "historical_85pct_repertoire":use_repertoire,
        "scheduled_games":len(games),
        "completed_games":sum(x["status"]=="COMPLETED" for x in games),
        "official_personality_files_changed":False,
        "historical_strength_elo_estimated":False,
        "note":"Actual limited-depth Rodent UCI outcomes; not historical Elo or identity proof.",
    }
    out.mkdir()
    try:
        _write_new(out/"results.json",report)
        (out/"games.pgn").write_text("\n\n".join(pgns)+"\n",encoding="utf-8")
    except BaseException:
        import shutil
        shutil.rmtree(out)
        raise
    return report


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description="Actual full UCI historical four-player round robin")
    p.add_argument("--rodent-root",type=Path,required=True)
    p.add_argument("--profiles",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--players",nargs="+",default=["fischer","tal","karpov","kasparov"])
    p.add_argument("--depth",type=int,default=4)
    p.add_argument("--max-plies",type=int,default=450)
    p.add_argument("--without-repertoire",action="store_true")
    a=p.parse_args(argv)
    report=run(a.rodent_root,a.profiles,a.out,players=a.players,
               depth=a.depth,max_plies=a.max_plies,
               use_repertoire=not a.without_repertoire)
    print(json.dumps({"status":report["status"],"standings":report["standings"],
                      "completed_games":report["completed_games"]},sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
