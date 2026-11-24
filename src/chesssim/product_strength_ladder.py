"""Measure the common Simple depth ladder; no human Elo conversion."""
import argparse
from collections import Counter
from contextlib import ExitStack
from itertools import combinations
import json
from pathlib import Path

import chess
import chess.engine

from .fischer_final_gate import play_clock, validate_clock
from .fischer_full_games import atomic_json, digest
from .matches import _open_engine
from .tal_full_games import _profiles
from .tal_balanced_clock_replication import fixed_roots

DEPTHS = (8, 12, 16, 20)
ROOT_COUNT = 12
PAIRS = tuple(combinations(DEPTHS, 2))


class DepthControl:
    """Retain the real clock and node limit, overriding only requested depth."""

    def __init__(self, engine, depth):
        self.engine = engine
        self.depth = depth
        self.reported = Counter()

    def play(self, board, limit, **kwargs):
        adjusted = chess.engine.Limit(**{**vars(limit), "depth": self.depth})
        reply = self.engine.play(board, adjusted, **kwargs)
        self.reported["searches"] += 1
        self.reported["reached"] += int(reply.info.get("depth", 0) >= self.depth)
        return reply

    def close(self):
        self.engine.close()


def roots(archive_dir=None):
    panel, _ = fixed_roots(archive_dir)
    return sorted(panel, key=lambda r: r["id"])[:ROOT_COUNT]


def run(root, out, index, archive_dir=None):
    selected = roots(archive_dir)
    if not 0 <= index < ROOT_COUNT:
        raise ValueError("Unknown ladder shard")
    location, exe, _, simple, _, hashes = _profiles(root)
    source = selected[index]
    records = []
    with ExitStack() as stack:
        first, second = [stack.enter_context(_open_engine(location, exe, simple, book=False))
                         for _ in range(2)]
        for low, high in PAIRS:
            for color in ("white", "black"):
                a, b = DepthControl(first, high), DepthControl(second, low)
                case = {"id": digest([source["id"], low, high, color]),
                        "root": source["id"], "color": color,
                        "depth": high, "opponent_depth": low}
                raw = play_clock(case, source, a, b)
                validate_clock(raw, case, source)
                records.append({"case": case, "game": raw,
                                "telemetry": {"high": dict(a.reported), "low": dict(b.reported)}})
                print("LADDER", index, low, high, color, raw["result"], flush=True)
    atomic_json(out, {"index": index, "hashes": hashes, "records": records})


def aggregate(folder, out, archive_dir=None):
    selected = roots(archive_dir)
    rows = {}
    hashes = None
    for i, source in enumerate(selected):
        doc = json.loads((Path(folder) / f"ladder-{i}" / "report.json").read_text())
        if doc["index"] != i or len(doc["records"]) != 2 * len(PAIRS):
            raise ValueError("Incomplete ladder shard")
        if hashes is None:
            hashes = doc["hashes"]
        if doc["hashes"] != hashes:
            raise ValueError("Different control binaries")
        seen = set()
        for record in doc["records"]:
            case = record["case"]
            low, high, color = case["opponent_depth"], case["depth"], case["color"]
            key = (low, high, color)
            if ((low, high) not in PAIRS or color not in ("white", "black")
                    or key in seen or case["root"] != source["id"]
                    or case["id"] != digest([source["id"], low, high, color])):
                raise ValueError("Invalid ladder case identity")
            seen.add(key)
            game = validate_clock(record["game"], case, source)
            row = rows.setdefault(f"{high}_vs_{low}", Counter())
            row["scheduled"] += 1
            result = game["result"]
            outcome = ("incomplete" if result == "*" else "draws" if result == "1/2-1/2"
                       else "wins" if (result == "1-0") == (color == "white") else "losses")
            row[outcome] += 1
            row["flags"] += int(game["time_forfeit"] is not None)
            for role in ("high", "low"):
                for k in ("searches", "reached"):
                    row[f"{role}_{k}"] += record["telemetry"][role].get(k, 0)
    for row in rows.values():
        n = row["wins"] + row["draws"] + row["losses"]
        row["score"] = (row["wins"] + row["draws"] / 2) / n if n else None
    report = {"scope": "internal competitive control, not human Elo",
              "clock": "10+2", "node_cap": 5000000, "hashes": hashes,
              "roots": ROOT_COUNT, "pairings": rows,
              "complete": all(r["scheduled"] == 24 and not r["incomplete"] for r in rows.values()),
              "ordered": all(r["score"] is not None and r["score"] >= .5 for r in rows.values())}
    atomic_json(out, report)
    print("PRODUCT_LADDER_RESULT", json.dumps(report, sort_keys=True))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rodent", type=Path)
    p.add_argument("--index", type=int)
    p.add_argument("--shards", type=Path)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if a.shards is not None:
        aggregate(a.shards, a.out)
    else:
        run(a.rodent, a.out, a.index)


if __name__ == "__main__":
    main()
