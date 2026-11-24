"""Audit reference instability without weakening Tal/Fischer historical parity gates.

Read every original source-locked position from the completed 887-decision run.
Re-evaluate ALL 21 inconsistencies and a matched control per inconsistency at
Stockfish depth 24. The resulting evidence is diagnostic ONLY: it cannot
retroactively certify a failed preregistered 2% reference-stability gate.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import re

import chess
import chess.engine

from .fischer_full_games import atomic_json, digest, load_json
from .historical_quality_parity import (
    panel, shard_cases, validate_measurement,
)
from .matches import file_hash
from .real85_graded_phase_attribution import _score

SCHEMA = "chesssim-tal-fischer-reference-diagnostic-v1"
ORIGINAL_RUN = 45258562373
ORIGINAL_REPORT_SHA = "febdd827c726348ac0560c347de1f785ba5b754eb6a98dd8dcb01dd085062624"
ORIGINAL_STOCKFISH_SHA = "250ff9c3548d06b06616356b01972230607469c95fb187e262f7044c4f6cfbe9"
ORIGINAL_PROTOCOL_SHA = "7503d2ef13d9b952b08c8110136ed95f286855b72dc5828ceacca2ffcde59a0b"
ORIGINAL_COUNT = 887
ORIGINAL_FLAGS = 21
REFERENCE_DEPTH = 24
SHARDS = 4
SALT = "tal-fischer-root-reference-adjudication-v1"
INCONSISTENCY_CP = 35
SOURCE_STATUS = "INCONCLUSIVE_NEEDS_HUMAN_QUALITY_SUPPORT"


def load_prior(shards, report, archive_dir=None):
    """Check original report and all 887 original chess decisions before sampling."""
    report = Path(report)
    if sha256(report.read_bytes()).hexdigest() != ORIGINAL_REPORT_SHA:
        raise ValueError("Original result is not the exact completed run artifact")
    prior = load_json(report)
    if (prior["status"] != SOURCE_STATUS or
            prior["observed_decisions"] != ORIGINAL_COUNT or
            prior["verified_decisions"] != ORIGINAL_COUNT or
            prior["parity_ci95_cp"] is not None or
            prior["release_eligible"] is not False):
        raise ValueError("Source result no longer has its inconclusive disposition")
    expected, boards, meta = panel(archive_dir)
    if len(expected) != ORIGINAL_COUNT or prior["positions"] != len(expected):
        raise ValueError("Changed historical source position population")
    records = {}
    hashes = None
    for index in range(32):
        directory = Path(shards) / f"shard-{index}"
        manifest = load_json(directory / "manifest.json")
        cases = shard_cases(expected, index)
        if (manifest.get("index") != index or
                manifest.get("case_ids") != [c["id"] for c in cases] or
                manifest.get("positions") != meta["positions"] or
                manifest.get("source_sha256") != meta["source_sha256"]):
            raise ValueError("Historical evidence manifest mismatch")
        fp = manifest.get("fingerprints")
        if (not isinstance(fp, dict) or
                fp.get("protocol_sha256") != ORIGINAL_PROTOCOL_SHA or
                fp.get("stockfish") != ORIGINAL_STOCKFISH_SHA):
            raise ValueError("Original Stockfish / protocol SHA mismatch")
        if hashes is None:
            hashes = fp
        elif hashes != fp:
            raise ValueError("Mixed engine evidence from unrelated runs")
        wanted = {"manifest.json"} | {c["id"] + ".json" for c in cases}
        if {p.name for p in directory.iterdir()} != wanted:
            raise ValueError("Original artifacts omit or add historical evidence")
        for case in cases:
            if case["id"] in records:
                raise ValueError("Duplicated source position")
            record = load_json(directory / (case["id"] + ".json"))
            validate_measurement(record, case, boards[case["id"]],
                                 digest(manifest))
            records[case["id"]] = record
    if set(records) != set(expected):
        raise ValueError("Missing historical candidate decisions")
    return records, boards, prior


def sample(records):
    flagged = [row for row in records.values()
               if row["measured"]["reference"]["inconsistent"]]
    if len(flagged) != ORIGINAL_FLAGS:
        raise ValueError("Reference inconsistency source count changed")
    by_era = Counter((x["case"]["player"], x["case"]["era"]) for x in flagged)
    if by_era[("fischer", "peak")] != 5 or by_era[("tal", "peak")] != 5:
        raise ValueError("Peak reference instability census changed")
    candidates = defaultdict(list)
    for row in records.values():
        if not row["measured"]["reference"]["inconsistent"]:
            key = tuple(row["case"][k] for k in ("player", "era", "phase"))
            candidates[key].append(row)
    selected = {x["case"]["id"]: ("flagged", x) for x in flagged}
    used = set(selected)
    for row in sorted(flagged, key=lambda x: x["case"]["id"]):
        key = tuple(row["case"][k] for k in ("player", "era", "phase"))
        pool = [x for x in candidates[key] if x["case"]["id"] not in used]
        pool.sort(key=lambda x: (
            sha256((SALT + ":" + row["case"]["id"] + ":" +
                    x["case"]["id"]).encode()).hexdigest(), x["case"]["id"]
        ))
        if not pool:
            raise ValueError("Cannot match a deterministic source control")
        control = pool[0]
        used.add(control["case"]["id"])
        selected[control["case"]["id"]] = ("control", control)
    if len(selected) != 2 * ORIGINAL_FLAGS:
        raise ValueError("Some source control was reused")
    return selected


def assignment(selected, index):
    if type(index) is not int or not 0 <= index < SHARDS:
        raise ValueError("Invalid adjudication shard index")
    return [(kind, x) for ident, (kind, x) in sorted(selected.items())
            if int(sha256((SALT + ident).encode()).hexdigest()[:8], 16)
            % SHARDS == index]


def inspect_case(stockfish, board, original, kind):
    ref = original["measured"]["reference"]
    info = stockfish.analyse(
        board, chess.engine.Limit(depth=REFERENCE_DEPTH), multipv=2,
        game=object()
    )
    if len(info) != 2:
        raise ValueError("Depth-24 two-line reference not available")
    reranked = []
    for candidate in info:
        pv = candidate.get("pv")
        depth = candidate.get("depth")
        value = candidate.get("score")
        if (not pv or pv[0] not in board.legal_moves or
                type(depth) is not int or depth < REFERENCE_DEPTH or
                value is None):
            raise ValueError("Incomplete depth-24 independent top2")
        pov = value.pov(board.turn)
        reranked.append({
            "move": pv[0].uci(), "depth": depth,
            "cp": pov.score(), "mate": pov.mate(),
        })
    if reranked[0]["move"] == reranked[1]["move"]:
        raise ValueError("Depth-24 duplicate root principal variation")
    moves = sorted(set([ref["best"], ref["second"],
                        *original["measured"]["moves"].values(),
                        *(r["move"] for r in reranked)]))
    evaluated = {}
    for m in moves:
        if re.fullmatch("[a-h][1-8][a-h][1-8][nbrq]?", m) is None:
            raise ValueError("Illegal source UCI")
        move = chess.Move.from_uci(m)
        if move not in board.legal_moves:
            raise ValueError("Adjudication candidate outside historic root")
        evaluated[m] = _score(stockfish, board, move, REFERENCE_DEPTH)
    baseline = evaluated[reranked[0]["move"]]
    if baseline["mate"] is None:
        distances = {
            m: scored["cp"] - baseline["cp"]
            for m, scored in evaluated.items() if scored["mate"] is None
        }
        instability = max(distances.values()) > INCONSISTENCY_CP
        max_better = max(distances.values())
    else:
        instability = None
        max_better = None
    return {
        "schema": SCHEMA, "case": original["case"], "selection": kind,
        "baseline_reference_move": ref["best"],
        "baseline_inconsistent": ref["inconsistent"],
        "depth24_ranked": reranked,
        "depth24_forced": evaluated,
        "depth24_top_underperformed": instability,
        "max_forced_better_cp": max_better,
        "status": "DIAGNOSTIC_ONLY_NO_PARITY_PROMOTION",
    }


def plan(shards, report, archive_dir=None):
    old, _, _ = load_prior(shards, report, archive_dir)
    selected = sample(old)
    sizes = [len(assignment(selected, i)) for i in range(SHARDS)]
    if sum(sizes) != 2 * ORIGINAL_FLAGS or any(x == 0 for x in sizes):
        raise ValueError("Unbalanced depth24 quality adjudication design")
    return {
        "schema": SCHEMA, "source_run": ORIGINAL_RUN,
        "source_report_sha256": ORIGINAL_REPORT_SHA,
        "source_positions": ORIGINAL_COUNT,
        "source_flags": ORIGINAL_FLAGS,
        "flagged_peak": {"fischer": 5, "tal": 5},
        "matched_controls": ORIGINAL_FLAGS,
        "target_positions": len(selected), "shards": SHARDS,
        "per_shard": sizes, "reference_depth": REFERENCE_DEPTH,
        "posthoc_diagnostic": True,
        "may_approve_tal_or_fischer": False,
        "status": "DESIGNED_NOT_ADJUDICATED",
    }


def run(shards, report, stockfish_path, out, index, archive_dir=None):
    old, boards, _ = load_prior(shards, report, archive_dir)
    selected = sample(old)
    planned = assignment(selected, index)
    target = Path(out)
    if target.exists() or target.is_symlink():
        raise ValueError("Do not overwrite original diagnostic evidence")
    sf_path = Path(stockfish_path).resolve(strict=True)
    if file_hash(sf_path) != ORIGINAL_STOCKFISH_SHA:
        raise ValueError("Wrong independent Stockfish binary")
    target.mkdir(parents=False)
    plan_doc = plan(shards, report, archive_dir)
    manifest = {
        **plan_doc, "index": index,
        "case_ids": [row["case"]["id"] for _, row in planned],
        "stockfish_sha256": file_hash(sf_path),
        "protocol_sha256": file_hash(Path(__file__)),
    }
    atomic_json(target / "manifest.json", manifest)
    with ExitStack() as stack:
        sf = stack.enter_context(chess.engine.SimpleEngine.popen_uci(
            str(sf_path), timeout=240.0))
        sf.configure({"Threads": 1, "Hash": 64})
        for n, (kind, row) in enumerate(planned, 1):
            result = inspect_case(sf, boards[row["case"]["id"]], row, kind)
            result["manifest_sha256"] = digest(manifest)
            atomic_json(target / (row["case"]["id"] + ".json"), result)
            print("QUALITY_DEPTH24", index, n, len(planned),
                  kind, row["case"]["player"], row["case"]["era"],
                  result["depth24_top_underperformed"], flush=True)
    return {"shard": index, "positions": len(planned), "status": "DIAGNOSTIC_ONLY"}


def aggregate(shards, report, evaluated, archive_dir=None):
    old, boards, _ = load_prior(shards, report, archive_dir)
    selected = sample(old)
    observed = {}
    for index in range(SHARDS):
        folder = Path(evaluated) / f"shard-{index}"
        manifest = load_json(folder / "manifest.json")
        planned = assignment(selected, index)
        blueprint = plan(shards, report, archive_dir)
        expected = {
            **blueprint, "index": index,
            "case_ids": [row["case"]["id"] for _, row in planned],
            "stockfish_sha256": ORIGINAL_STOCKFISH_SHA,
            "protocol_sha256": file_hash(Path(__file__)),
        }
        if manifest != expected:
            raise ValueError("Source-locked higher-depth manifest mismatch")
        wanted = {"manifest.json"} | {
            row["case"]["id"] + ".json" for _, row in planned}
        if {x.name for x in folder.iterdir()} != wanted:
            raise ValueError("Source evidence missing, replaced or added")
        for kind, source in planned:
            case = source["case"]
            if case["id"] in observed:
                raise ValueError("Duplicated adjudicated historical position")
            doc = load_json(folder / (case["id"] + ".json"))
            if (doc.get("schema") != SCHEMA or doc.get("case") != case
                    or doc.get("selection") != kind
                    or doc.get("manifest_sha256") != digest(manifest)
                    or doc.get("status") != "DIAGNOSTIC_ONLY_NO_PARITY_PROMOTION"
                    or doc.get("baseline_inconsistent") !=
                    source["measured"]["reference"]["inconsistent"]
                    or doc.get("baseline_reference_move") !=
                    source["measured"]["reference"]["best"]):
                raise ValueError("Tampered original or diagnostic case binding")
            board = boards[case["id"]]
            new = doc.get("depth24_forced")
            top = doc.get("depth24_ranked")
            if not isinstance(new, dict) or not isinstance(top, list) or len(top) != 2:
                raise ValueError("Incomplete depth24 reference diagnostics")
            needed = set([source["measured"]["reference"]["best"],
                          source["measured"]["reference"]["second"],
                          *source["measured"]["moves"].values(),
                          *(x["move"] for x in top)])
            if set(new) != needed:
                raise ValueError("Depth24 case scored different candidate moves")
            for m, result in new.items():
                if (chess.Move.from_uci(m) not in board.legal_moves
                        or result.get("move") != m
                        or (result.get("mate") is None and
                            type(result.get("cp")) is not int)):
                    raise ValueError("Invalid forced-root chess evaluation")
            base = new[top[0]["move"]]
            if base["mate"] is None:
                diff = max([v["cp"] - base["cp"] for v in new.values()
                            if v["mate"] is None])
                if (doc.get("max_forced_better_cp") != diff or
                        doc.get("depth24_top_underperformed") !=
                        (diff > INCONSISTENCY_CP)):
                    raise ValueError("Falsified higher-depth inconsistency")
            observed[case["id"]] = doc
    if set(observed) != set(selected):
        raise ValueError("Not all anomalies and controls were diagnosed")
    summary = {}
    for kind in ("flagged", "control"):
        rows = [x for x in observed.values() if x["selection"] == kind]
        summary[kind] = {
            "observed": len(rows),
            "still_unstable_at_depth24": sum(
                x["depth24_top_underperformed"] is True for x in rows),
            "mate_sensitive": sum(
                x["depth24_top_underperformed"] is None for x in rows),
            "by_player_era": {
                "|".join(key): {
                    "cases": count,
                    "still_unstable": sum(
                        x["depth24_top_underperformed"] is True
                        for x in rows if (
                            x["case"]["player"], x["case"]["era"]) == key),
                }
                for key, count in Counter((
                    x["case"]["player"], x["case"]["era"]
                ) for x in rows).items()
            },
        }
    return {
        "schema": SCHEMA, "source_run": ORIGINAL_RUN,
        "original_status": SOURCE_STATUS, "original_positions": ORIGINAL_COUNT,
        "original_reference_instabilities": ORIGINAL_FLAGS,
        "original_peak_flags": {"fischer": 5, "tal": 5},
        "higher_depth": REFERENCE_DEPTH, "rechecked_positions": len(observed),
        "diagnosis": summary, "only_diagnostic": True,
        "parity_approved": False, "tal_frozen": False,
        "fischer_frozen_asset_unchanged": True,
        "status": "REFERENCE_INSTABILITY_DIAGNOSED_RELEASE_STILL_BLOCKED",
        "note": "Targeted posthoc reruns cannot be counted as an independent replacement for the locked 887-case reference-quality gate.",
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("plan", "play", "aggregate"):
        spec = sub.add_parser(name)
        spec.add_argument("--shards", type=Path, required=True)
        spec.add_argument("--prior-report", type=Path, required=True)
        sources = spec.add_mutually_exclusive_group(required=True)
        sources.add_argument("--fetch", action="store_true")
        sources.add_argument("--archive-dir", type=Path)
        if name == "play":
            spec.add_argument("--stockfish", type=Path, required=True)
            spec.add_argument("--out", type=Path, required=True)
            spec.add_argument("--index", type=int, required=True)
        if name == "aggregate":
            spec.add_argument("--adjudication-shards", type=Path, required=True)
            spec.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    if args.command == "plan":
        result = plan(args.shards, args.prior_report, args.archive_dir)
    elif args.command == "play":
        result = run(args.shards, args.prior_report,
                     args.stockfish, args.out, args.index, args.archive_dir)
    else:
        result = aggregate(
            args.shards, args.prior_report, args.adjudication_shards,
            args.archive_dir
        )
        if args.out.exists() or args.out.is_symlink():
            raise ValueError("Do not overwrite diagnostic output")
        atomic_json(args.out, result)
    print(json.dumps(result, sort_keys=True, allow_nan=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
