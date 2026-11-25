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
    PARTITION_VERSION, WAVE, WAVE2, WAVE3, WAVE5, WAVE6, WAVE7, INDIVIDUAL_SEEDS, PHASES, MIN_PER_PHASE, MIN_SCORE, ROOTS,
    bound_measure, eligible_quality, identity_comparison, overlay, product_book, sample, source,
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
WAVE2_RUN = 45597119316
WAVE2_PRIOR = {
    "spassky": (15027934275, "original20", "000da9c591c8e63fe5e4b11c812c42d3e700032e034f75934d67faba089b4bbd",
                "4927d40a8fa8e9062152ecba3bcac937ccc813c606de7164d91f6ab1391b549d"),
    "lasker": (15027405259, "restriction20", "d68f959d9995d01c715b85063298e881cc7ce1720c279e4fcc32aea706522784",
               "1969384c06f21ecb4228ca1ba7454627843acb130059173aac54958d598420ff"),
    "rubinstein": (15027357055, "initiative20", "fd6b301c42ec24253dcf55cc197ce4fd4df5be5d6e4f94905247fdd57762f76e",
                   "5ae4caff36ace33baac6c1b8d46778ae502c4a05477290f7559b0d0c5437e453"),
    "anand": (15028321226, "restriction20", "7a8ccf830762f23adb2486bf633ba992b487b8d1dc23744d675cc7281a08a456",
              "db483b709ddd45363c84f57e83f7e33c26bfbb77efab8bca0b5b0f79203c8198"),
}
WAVE3_RUN = 45633448337
WAVE3_PRIOR = {
    "steinitz": (15054926890, "original20", "f7ab0efd23c94f96f025104dc82eab993163d7a7523cab8b612add1d85d1b338",
                 "59ef19579b6e02c62e35943bbe01b4f6fd1a86c14bf1d38c66214a19aacfddc9"),
    "tarrasch": (15052430360, "initiative20", "5df46b5f2e280fc5075be67edeb946d8c922d0f25f8210b2416268a8199947a6",
                 "874f85c21af1ad63d0fb12b53cc8da56787459292a2c6686e82f007919900064"),
    "reti": (15051882168, "original20", "8d15e479e118903309afe215cd070faaa2e4337b2b62809a26476113a9469d20",
             "3ac0222104ed99797d741f83c2d666289bc3f75526f8c48d2e4d099978ed40cc"),
    "larsen": (15050556789, "initiative16", "0c68296c0070a21db5d53e76653b27d3497f1c00ea6e6a7e9274cd9d7e5a7188",
               "ae0372dc16659137718ddbe5e3ee71eca949d40c07fc4d4623b77f49b4dae4cc"),
    "marshall": (15049839584, "original16", "38dfc223a8067f5a041e011c25c914e56bebbfd78e0dc10648c6a73a228f5711",
                 "dd0e9d5ef78a22c190a5fe65b511c6a943078e9c9e56d45e667246ce432d04b2"),
    "anderssen": (15049399796, "original16", "5d4235febae596783c0d68c2567900440addfebf6697c48ad5d0ada2eb94cceb",
                  "961bf0303b3571da768d6e20452d17cd2439cc1086dee8a42c1e1b5e0101e113"),
    "nimzowitsch": (15049319826, "original20", "53b1825b4dd6ea709b5fcfc401ce9ab920ffcd03b9e54936e8fa3c36812ceae1",
                    "d1da8e7e21648958e863899230e4957e85fa2e8524d01b3bb89e4018558e7e54"),
}
READY = {"MOTOR_BASE_REVIEW_READY", "INDIVIDUAL_CANDIDATE_REVIEW_READY"}


def prior_binding(slug):
    if slug in PRIOR:
        return PRIOR_RUN, PRIOR[slug]
    if slug in WAVE2_PRIOR:
        return WAVE2_RUN, WAVE2_PRIOR[slug]
    if slug in WAVE3_PRIOR:
        return WAVE3_RUN, WAVE3_PRIOR[slug]
    return None, None


def read_candidate(slug, selection, final):
    report = json.loads((final / "report.json").read_text())
    if report.get("slug") != slug:
        raise ValueError("Wrong final candidate")
    if report.get("status") not in READY:
        return report, None, None
    data = source(slug)
    doc, _ = bound_measure(slug, selection, data)
    name, depth = split_label(report["selected"])
    _, binding = prior_binding(slug)
    if binding is not None:
        _, label, profile, book_hash = binding
        if (report["selected"], report["profile_sha256"], report["book_sha256"]) != (label, profile, book_hash):
            raise ValueError("Prior candidate changed")
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
    if slug in INDIVIDUAL_SEEDS:
        if name == "original":
            raise ValueError("Generic control cannot be released")
        for key, records in (("fit_individual_control", doc["fit_records"]), ("individual_control", tune)):
            comparison = identity_comparison(records, report["selected"])
            if comparison != report.get(key) or not comparison["passed"]:
                raise ValueError("Individual control evidence failed: " + key)
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
    prior_run, binding = prior_binding(slug)
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
                  "prior_run": prior_run,
                  "prior_artifact": binding[0] if binding else None}
        atomic_json(out / "release.json", result)
        print("WAVE_RELEASE_RESULT", json.dumps({k: v for k, v in result.items() if k != "calibration"},
                                               sort_keys=True), flush=True)


def release_recipe(first, second):
    """Produce an inactive registry proposal; publishing requires reviewed run IDs."""
    if {first.get("seed"), second.get("seed")} != {2029, 2030}:
        raise ValueError("Two independent seed pairs required")
    for doc in (first, second):
        if doc.get("status") != "FULL_PRODUCT_REVIEW_READY" or len(doc.get("games", [])) != 2:
            raise ValueError("Complete book evidence required")
    for key in ("slug", "calibration_report_sha256", "calibration", "alias", "profile_filename",
                "anchor", "engine_sha256", "prior_run", "prior_artifact"):
        if first[key] != second[key]:
            raise ValueError("Release reports disagree: " + key)
    c = first["calibration"]
    slug = first["slug"]
    if c["slug"] != slug or c["status"] not in READY:
        raise ValueError("Calibration not accepted")
    variant, depth = split_label(c["selected"])
    for doc in (first, second):
        games = doc["games"]
        if [(g["white"], g["black"]) for g in games] != [(slug, "fischer"), ("fischer", slug)]:
            raise ValueError("Expected paired colors")
        for g in games:
            if (g["result"] not in ("1-0", "0-1", "1/2-1/2")
                    or g["plies"] <= 16 or not all(g["counts"].get(p + "_uci", 0)
                                                   for p in (slug, "fischer"))):
                raise ValueError("Missing complete search evidence")
        if not all(sum(g["counts"].get(p + "_book", 0) for g in games) > 0
                   for p in (slug, "fischer")):
            raise ValueError("Missing complete book evidence")
    return {"status": "REVIEW_PENDING_REGISTRY_COMMIT", "profile": first["alias"],
            "alias": first["alias"], "wave_variant": variant, "depth": depth,
            "uci_node_cap": NODE_CAP, "internal_opening_book": False,
            "phase_adaptive_depth": False, "neural_override": False,
            "time_control_benchmark": "10+2", "release_scope": "FULL_PRODUCT",
            "partition_version": PARTITION_VERSION,
            "hashes": {"rodent": first["engine_sha256"], "profile": c["profile_sha256"]},
            "overrides": c["overrides"], "historical_source_sha256": c["source_sha256"],
            "historical_book_digest": c["book_sha256"], "period": c["period"],
            "calibration_report_sha256": first["calibration_report_sha256"],
            "release_report_digests": {str(d["seed"]): digest(d) for d in (first, second)},
            "limitations": ["No human Elo or exact identity equivalence",
                            "Book validation is unclocked; competitive screen is 10+2",
                            "Descriptive FIT/TUNE metrics; reserved TEST not scored"]}


def aggregate(shards, out, wave=1):
    proposals, blocked = {}, {}
    groups = {1: (*PRIOR, "alekhine"), 2: WAVE2, 3: tuple(s for s in WAVE3 if s != "topalov"), 4: ("topalov",), 5: WAVE5, 6: WAVE6, 7: WAVE7}
    for slug in groups[wave]:
        docs = [json.loads((shards / f"release-{slug}-{seed}" / "release.json").read_text())
                for seed in (2029, 2030)]
        if all(d.get("status") == "BLOCKED_BY_CALIBRATION" for d in docs):
            blocked[slug] = docs[0]["calibration_status"]
            continue
        proposals[slug] = release_recipe(*docs)
    atomic_json(out, {"players": proposals, "blocked": blocked,
                      "promotion": "EXPLICIT_REGISTRY_COMMIT_REQUIRED"})
    print("WAVE_RELEASE_PROPOSALS", json.dumps({"ready": sorted(proposals), "blocked": blocked}), flush=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--player", choices=sorted(WAVE))
    parser.add_argument("--aggregate", type=Path)
    parser.add_argument("--wave", type=int, choices=(1, 2, 3, 4, 5, 6, 7), default=1)
    parser.add_argument("--rodent", type=Path)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--final", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--seed", type=int, default=2029)
    args = parser.parse_args()
    if args.out is None:
        parser.error("--out is required")
    if args.aggregate:
        aggregate(args.aggregate, args.out, args.wave)
    else:
        if not all((args.player, args.rodent, args.selection, args.final)):
            parser.error("validation requires --player, --rodent, --selection and --final")
        validate_release(args.player, args.rodent, args.selection, args.final, args.out, args.seed)


if __name__ == "__main__":
    main()
