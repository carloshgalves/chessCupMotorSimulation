"""Release evidence for a wave candidate, without automatic runtime promotion."""
import argparse
from collections import Counter
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

import chess.pgn

from .catalog import find_player
from .fischer_full_games import atomic_json, digest
from .matches import file_hash, play_duel
from .opening_books import select_book_move
from .product_peak_profiles import split_label, summarize
from .qualified_runtime import NODE_CAP, prepare, resolve
from .registry import add_to_first_set
from .roster_product_wave import (
    PARTITION_VERSION, WAVE, PHASES, MIN_PER_PHASE, MIN_SCORE, ROOTS,
    bound_measure, eligible_quality, overlay, product_book, sample, source,
)
from .product_repertoires import audit

# Exact final artifacts from the reviewed wave-01 run, never "latest".
PRIOR_RUN = 45513328071
PRIOR = {
    "botvinnik": (14985841771, "restriction20", "ace71d564530c2a887bc59db1083669fc3e9e43e79e81b417a182f01a621a2df",
                  "ac9115a0c764feb98264822ce2ba55b0bd9993574c1375318970c8356c902d6f"),
    "smyslov": (14986561263, "initiative20", "01a8f730fab459a516e95f1651009c5265a714d1c0cd435453fe5cbb8227f429",
                "f14214ab17f6c98a761d94373f815311d635b5542bd26a818bd267c107c6a2d9"),
    "petrosian": (14988291153, "original16", "e4558a9035d0e21afa9bbd453f5ded76d22fb274ca39889cf2163d7472879ef4",
                  "96d467f8919afa0130ecc151abc87df4620a1ef89a28bf6cd9f055f6eedd4e22"),
}
READY = {"MOTOR_BASE_REVIEW_READY", "INDIVIDUAL_CANDIDATE_REVIEW_READY"}


def read_candidate(slug, selection, final):
    report = json.loads((final / "report.json").read_text())
    if report.get("slug") != slug:
        raise ValueError("Wrong final candidate")
    if report.get("status") not in READY:
        return report, None, None
    data = source(slug)
    doc, _ = bound_measure(slug, selection, data)
    name, depth = split_label(report["selected"])
    if slug in PRIOR:
        _, label, profile, book_hash = PRIOR[slug]
        if (report["selected"], report["profile_sha256"], report["book_sha256"]) != (label, profile, book_hash):
            raise ValueError("Wave-01 candidate changed")
    book = product_book(slug, data)
    if (report["period"] != list(WAVE[slug]) or report["partition_version"] != PARTITION_VERSION
            or report["source_sha256"] != sha256(data[0]).hexdigest()
            or report["book_sha256"] != digest(book) or report["book_sha256"] != doc["book_sha256"]
            or report["book_audit"] != audit(book) or report["fit_stats"] != doc["fit_stats"]
            or report["selected"] not in doc["shortlist"] or report["depth"] != depth
            or report["overrides"] != doc["params"][name]
            or report["profile_sha256"] != doc["hashes"][name]
            or file_hash(final / "candidate.txt") != report["profile_sha256"]
            or report["repeat_matches"] != 10 or report["repeat_total"] != 10):
        raise ValueError("Candidate provenance mismatch")
    strength = report["strength"][report["selected"]]
    if (strength["wins"] + strength["draws"] + strength["losses"] != 2 * ROOTS
            or strength["incomplete"] or strength["flags"]
            or strength["score"] != (strength["wins"] + strength["draws"] / 2) / (2 * ROOTS)
            or strength["score"] < MIN_SCORE or not eligible_quality(doc["fit_stats"][report["selected"]])):
        raise ValueError("Competitive acceptance failed")
    tune = json.loads((final / "tune.json").read_text())
    probes, _ = sample(slug, data, "tune")
    if len(tune) != len(probes) or {x["game"] for x in tune} & {x["game"] for x in doc["fit_records"]}:
        raise ValueError("TUNE trajectory overlap or incomplete panel")
    for row, (_, board, human, gid) in zip(tune, probes):
        if (row["game"] != gid or row["fen"] != board.fen() or row["values"]["human"]["move"] != human.uci()):
            raise ValueError("TUNE panel mismatch")
    counts = dict(Counter(x["phase"] for x in tune))
    if (counts != report["tune_phase_support"] or any(counts.get(p, 0) < MIN_PER_PHASE for p in PHASES)
            or summarize(tune)[report["selected"]] != report["tune"]
            or not eligible_quality(report["tune"])):
        raise ValueError("TUNE acceptance failed")
    return report, book, name


def replay(folder, books, candidate, anchor, seed):
    summary = json.loads((folder / "summary.json").read_text())
    if summary["status"] != "COMPLETED" or len(summary["games"]) != 2:
        raise ValueError("Two complete games required")
    results = []
    for i, meta in enumerate(summary["games"], 1):
        with (folder / f"jogo_{i}.pgn").open() as stream:
            game = chess.pgn.read_game(stream)
        if game is None or game.errors:
            raise ValueError("Invalid PGN")
        board = game.board()
        pair = (candidate, anchor) if i == 1 else (anchor, candidate)
        trace = {e["ply"]: e for e in meta["opening_trace"]}
        if len(trace) != len(meta["opening_trace"]):
            raise ValueError("Duplicate opening trace")
        seen, counts = set(), Counter()
        for move in game.mainline_moves():
            owner = pair[0 if board.turn else 1]
            expected = select_book_move(books[owner], board, seed=seed, game_number=i)
            ply = board.ply() + 1
            if expected is not None:
                event = trace.get(ply)
                if expected != move or event is None or event["player"] != owner or event["move"] != move.uci():
                    raise ValueError("Historical book replay mismatch")
                seen.add(ply)
                counts[owner + "_book"] += 1
            else:
                counts[owner + "_uci"] += 1
            if move not in board.legal_moves:
                raise ValueError("Illegal move")
            board.push(move)
        outcome = board.outcome(claim_draw=True)
        if (seen != set(trace) or outcome is None or outcome.result() != game.headers["Result"]
                or outcome.result() != meta["result"]):
            raise ValueError("Unverified terminal game")
        if not all(counts[p + "_uci"] for p in pair):
            raise ValueError("Missing search evidence")
        results.append({"white": pair[0], "black": pair[1], "result": outcome.result(),
                        "plies": board.ply(), "counts": dict(counts)})
    for p in (candidate, anchor):
        if sum(g["counts"].get(p + "_book", 0) for g in results) == 0:
            raise ValueError("Missing book evidence: " + p)
    return results


def validate_release(slug, root, selection, final, out, seed):
    report, book, variant = read_candidate(slug, selection, final)
    out.mkdir(parents=True, exist_ok=False)
    if book is None:
        atomic_json(out / "release.json", {"slug": slug, "status": "BLOCKED_BY_CALIBRATION",
                                           "calibration_status": report["status"]})
        print("WAVE_RELEASE_BLOCKED", slug, report["status"], flush=True)
        return
    # Reconstruct exact candidate from pinned source, then use the real qualified
    # Fischer as an external anchor. Do not change the approved runtime manifest.
    with TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        variants, _ = overlay(root, tmp / "candidate", slug)
        p = variants[variant]
        candidate = tmp / "candidate" / "personalities" / p.profile_filename
        if file_hash(candidate) != report["profile_sha256"]:
            raise ValueError("Reconstructed candidate differs from measured profile")
        prepare(root)
        bundle, players, books, depths, resources = resolve(root, ["fischer"])
        location = tmp / "duel"
        shutil.copytree(bundle, location)
        alias = p.rodent_alias
        shutil.copy2(candidate, location / "personalities" / p.profile_filename)
        # The original Petrosian alias is already in the baseline registry.
        if variant != "original" or alias != find_player(slug).rodent_alias:
            ini = location / "personalities/basic.ini"
            ini.write_text(add_to_first_set(ini.read_text(), {alias: p.profile_filename}))
        p = replace(find_player(slug), rodent_alias=alias, rodent_file=p.profile_filename)
        books[slug], depths[slug] = book, report["depth"]
        folder = play_duel(location, out / "games", p, players["fischer"],
                           depth=max(depths.values()), reverse=True, max_plies=600,
                           use_books=False, historical_openings=books, opening_seed=seed,
                           max_wall_seconds=3600, node_cap=NODE_CAP, product_depths=depths)
        summary = json.loads((folder / "summary.json").read_text())
        if (summary["file_hashes"]["exe"] != resources["engine_sha256"]
                or summary["file_hashes"][slug] != report["profile_sha256"]
                or summary["file_hashes"]["fischer"] != resources["profile_sha256"]["fischer"]):
            raise ValueError("Played profile identity mismatch")
        games = replay(folder, books, slug, "fischer", seed)
        shutil.copyfile(candidate, out / "candidate.txt")
        atomic_json(out / "book.json", book)
        result = {"slug": slug, "status": "FULL_PRODUCT_REVIEW_READY",
                  "promotion": "REQUIRES_EXPLICIT_REGISTRY_COMMIT",
                  "calibration_report_sha256": file_hash(final / "report.json"),
                  "calibration": report, "alias": alias, "profile_filename": p.profile_filename,
                  "seed": seed, "games": games, "anchor": "fischer",
                  "engine_sha256": resources["engine_sha256"],
                  "prior_run": PRIOR_RUN if slug in PRIOR else None,
                  "prior_artifact": PRIOR[slug][0] if slug in PRIOR else None}
        atomic_json(out / "release.json", result)
        print("WAVE_RELEASE_RESULT", json.dumps({k: v for k, v in result.items() if k != "calibration"},
                                               sort_keys=True), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--player", required=True, choices=sorted(WAVE))
    parser.add_argument("--rodent", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--final", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=2029)
    args = parser.parse_args()
    validate_release(args.player, args.rodent, args.selection, args.final, args.out, args.seed)


if __name__ == "__main__":
    main()
