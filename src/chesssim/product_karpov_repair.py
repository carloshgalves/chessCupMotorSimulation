"""Focused product repair: preserve prior evidence, select only competitive candidates."""
import argparse
from collections import Counter
from contextlib import ExitStack
from importlib.resources import files
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

import chess
import chess.pgn

from .catalog import Player
from .fischer_deep_calibration import choose
from .fischer_final_gate import play_clock, validate_clock
from .fischer_full_games import atomic_json, digest
from .matches import _open_engine, file_hash, play_duel
from .opening_books import select_book_move
from .product_peak_profiles import (
    PEAKS, VARIANTS, overlay, panel, source, split_label, summarize,
)
from .product_repertoires import audit, build
from .product_strength_ladder import DepthControl

LABELS = tuple(f"{name}{depth}" for name in VARIANTS["karpov"] for depth in (16, 20))
KARPOV_SOURCE = "1efbbb6bc364b08647d42ad99c1493d8098f6971b388615a98ef9896f75e78c1"
KASPAROV_SOURCE = "6f6cdfe8e8dd09323ad8b324d2beac9326c155ce8020768a0995c24b018564e1"


def checked_source(slug):
    data = source(slug)
    expected = KARPOV_SOURCE if slug == "karpov" else KASPAROV_SOURCE
    from hashlib import sha256
    if sha256(data[0]).hexdigest() != expected:
        raise ValueError("Historical source differs from run 45408582198")
    return data


def points(game, color):
    if game["result"] == "*":
        return None
    if game["result"] == "1/2-1/2":
        return .5
    return float((game["result"] == "1-0") == (color == "white"))


def screen(root, index, out):
    _, roots = panel("karpov", checked_source("karpov"))
    start = roots[index]
    records = []
    with TemporaryDirectory() as temp, ExitStack() as stack:
        location = Path(temp) / "rodent"
        players = overlay(root, location, "karpov")
        exe = location / "rodent-iv-plain.exe"
        simple = Player(0, "Simple", "control-simple", "control", "Simple", "simple.txt")
        control = stack.enter_context(_open_engine(location, exe, simple, book=False))
        for label in LABELS:
            name, depth = split_label(label)
            candidate = players[name]
            engine = stack.enter_context(_open_engine(location, exe, candidate, book=False))
            for color in ("white", "black"):
                case = {"id": digest([label, start["id"], color]),
                        "root": start["id"], "color": color, "depth": depth,
                        "opponent_depth": 16}
                game = play_clock(case, start, DepthControl(engine, depth), DepthControl(control, 16))
                validate_clock(game, case, start)
                records.append({"label": label, "case": case, "game": game,
                                "profile_sha256": file_hash(location / "personalities" /
                                                           candidate.profile_filename)})
                print("KARPOV_SCREEN", index, label, color, game["result"], flush=True)
    atomic_json(out, {"index": index, "records": records})


def select(root, prior, shards, out):
    data = checked_source("karpov")
    probes, roots = panel("karpov", data)
    old = json.loads((prior / "selection.json").read_text())
    if (old["player"] != "karpov" or old["source_sha256"] != KARPOV_SOURCE
            or old["selected"] != "original12" or old["roots"] != roots
            or old["book_sha256"] != digest(build("karpov", data, period=PEAKS["karpov"]))):
        raise ValueError("Prior calibration identity mismatch")
    if len(old["records"]) != len(probes):
        raise ValueError("Missing historical probes")
    for record, (_, board, human, gid) in zip(old["records"], probes):
        if (record["game"] != gid or record["fen"] != board.fen()
                or record["values"]["human"]["move"] != human.uci()):
            raise ValueError("Historical probe binding mismatch")
    stats = summarize(old["records"])
    rows = {label: Counter() for label in LABELS}
    hashes = {}
    for i, start in enumerate(roots):
        doc = json.loads((shards / f"repair-{i}" / "report.json").read_text())
        if doc["index"] != i:
            raise ValueError("Wrong shard")
        seen = set()
        for item in doc["records"]:
            label, case = item["label"], item["case"]
            color = case["color"]
            if (label not in LABELS or color not in ("white", "black")
                    or (label, color) in seen or case["root"] != start["id"]
                    or case["depth"] != split_label(label)[1] or case["opponent_depth"] != 16
                    or case["id"] != digest([label, start["id"], color])):
                raise ValueError("Unexpected game case")
            seen.add((label, color))
            if hashes.setdefault(label, item["profile_sha256"]) != item["profile_sha256"]:
                raise ValueError("Inconsistent candidate identity")
            game = validate_clock(item["game"], case, start)
            score = points(game, color)
            rows[label]["scheduled"] += 1
            rows[label]["incomplete"] += int(score is None)
            rows[label]["flags"] += int(game["time_forfeit"] is not None)
            if score is not None:
                rows[label][{0.: "losses", .5: "draws", 1.: "wins"}[score]] += 1
        if len(seen) != 2 * len(LABELS):
            raise ValueError("Missing paired screen games")
    eligible = []
    for label, row in rows.items():
        for key in ("wins", "draws", "losses"):
            row.setdefault(key, 0)
        row["score"] = (row["wins"] + .5 * row["draws"]) / 24
        q = stats[label]
        if (not row["incomplete"] and not row["flags"] and row["score"] >= .40
                and q["numeric"] >= 20 and q["excess_cp"] <= 35
                and q["severe_excess"] <= .15):
            eligible.append(label)
    selected = min(eligible, key=lambda k: (stats[k]["objective"], -rows[k]["score"], k)) if eligible else None
    report = {"screen": rows, "quality": {k: stats[k] for k in LABELS},
              "selected": selected, "status": "NO_ELIGIBLE_CANDIDATE"}
    out.mkdir(parents=True, exist_ok=False)
    if selected:
        name, depth = split_label(selected)
        with TemporaryDirectory() as temp, ExitStack() as stack:
            location = Path(temp) / "rodent"
            candidate = overlay(root, location, "karpov")[name]
            profile = location / "personalities" / candidate.profile_filename
            if file_hash(profile) != hashes[selected]:
                raise ValueError("Screened profile changed")
            engine = stack.enter_context(_open_engine(location, location / "rodent-iv-plain.exe",
                                                      candidate, book=False))
            repeats = sum(choose(engine, board, depth)[0].uci() == record["values"][selected]["move"]
                          for record, (_, board, _, _) in zip(old["records"][:10], probes[:10]))
            shutil.copyfile(profile, out / "candidate.txt")
        report.update({"profile_sha256": hashes[selected], "depth": depth, "variant": name,
                       "overrides": VARIANTS["karpov"][name], "repeat_matches": repeats,
                       "repeat_total": 10,
                       "status": "SCREEN_PASSED" if repeats == 10 else "REPEATABILITY_FAILED"})
    atomic_json(out / "selection.json", report)
    print("KARPOV_REPAIR_RESULT", json.dumps(report, sort_keys=True), flush=True)


def duel(root, selection, out):
    doc = json.loads((selection / "selection.json").read_text())
    if doc["status"] != "SCREEN_PASSED":
        raise ValueError("No stable competitive Karpov candidate")
    manifest = json.loads(files("chesssim.data").joinpath("qualified_game_assets.json").read_text())
    kas = manifest["players"]["kasparov"]
    books = {slug: build(slug, checked_source(slug), period=PEAKS[slug]) for slug in PEAKS}
    for book in books.values():
        audit(book)
    if digest(books["kasparov"]) != kas["historical_book_digest"]:
        raise ValueError("Kasparov repertoire changed")
    if digest(books["karpov"]) != "89903c23be426fa7e151117d026588273b9601e682f217b4955dc57e3584d191":
        raise ValueError("Karpov repertoire changed")
    with TemporaryDirectory() as temp:
        location = Path(temp) / "rodent"
        kp = overlay(root, location, "karpov")[doc["variant"]]
        second = Path(temp) / "kasparov"
        gp = overlay(location, second, "kasparov")["initiative"]
        if (file_hash(second / "personalities" / kp.profile_filename) != doc["profile_sha256"]
                or file_hash(second / "personalities" / gp.profile_filename) != kas["hashes"]["kasparov_profile"]):
            raise ValueError("Duel profile mismatch")
        folder = play_duel(second, out, kp, gp, depth=doc["depth"], reverse=True,
                           max_plies=600, use_books=False, historical_openings=books,
                           opening_seed=2029, max_wall_seconds=3600, node_cap=5000000,
                           product_depths={"karpov": doc["depth"], "kasparov": 20})
        summary = json.loads((folder / "summary.json").read_text())
        if summary["status"] != "COMPLETED" or len(summary["games"]) != 2:
            raise ValueError("Both complete games required")
        counts = Counter()
        for index, meta in enumerate(summary["games"], 1):
            with (folder / f"jogo_{index}.pgn").open() as stream:
                game = chess.pgn.read_game(stream)
            if game is None or game.errors:
                raise ValueError("Invalid PGN")
            board = game.board()
            slugs = ("karpov", "kasparov") if index == 1 else ("kasparov", "karpov")
            trace = {e["ply"]: e for e in meta["opening_trace"]}
            seen = set()
            for move in game.mainline_moves():
                owner = slugs[0 if board.turn else 1]
                expected = select_book_move(books[owner], board, seed=2029, game_number=index)
                if expected is not None:
                    event = trace.get(board.ply() + 1)
                    if (expected != move or event is None or event["player"] != owner
                            or event["move"] != move.uci()):
                        raise ValueError("Historical book replay mismatch")
                    seen.add(board.ply() + 1)
                    counts[owner + "_book"] += 1
                else:
                    counts[owner + "_uci"] += 1
                if move not in board.legal_moves:
                    raise ValueError("Illegal move")
                board.push(move)
            outcome = board.outcome(claim_draw=True)
            if seen != set(trace) or outcome is None or outcome.result() != game.headers["Result"]:
                raise ValueError("Unverified game")
        if not all(counts[s + suffix] for s in PEAKS for suffix in ("_book", "_uci")):
            raise ValueError("Missing book or search evidence")
        print("PEAK_DUEL_RESULT", json.dumps(dict(counts), sort_keys=True), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("screen", "select", "duel"))
    p.add_argument("--rodent", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--index", type=int)
    p.add_argument("--prior", type=Path)
    p.add_argument("--shards", type=Path)
    p.add_argument("--selection", type=Path)
    a = p.parse_args()
    if a.action == "screen":
        screen(a.rodent, a.index, a.out)
    elif a.action == "select":
        select(a.rodent, a.prior, a.shards, a.out)
    else:
        duel(a.rodent, a.selection, a.out)


if __name__ == "__main__":
    main()
