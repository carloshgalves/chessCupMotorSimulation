"""Independently review immutable completed wave 05–08 artifacts.

The strength proxy is the player's own historical decision quality (not move
agreement) plus paired competitive games. The four anchor games are an
integration test, not an estimate of human Elo or a historical ranking.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

from .fischer_final_gate import RODENT_HASHES
from .fischer_full_games import atomic_json, digest
from .historical_quality_parity import source_rows
from .matches import file_hash
from .product_peak_profiles import PEAKS
from .product_repertoires import PERIODS, build
from .product_wave_release import read_candidate, release_recipe, replay
from .qualified_runtime import assets, book_digest
from .roster_product_wave import bound_measure, eligible_quality, scores, source

BINDINGS = {
    "bronstein": (45698384748, 15095085101, 15092875518, 15093770553, 15093876280),
    "geller": (45698384748, 15095085101, 15093305435, 15093444212, 15092899312),
    "gligoric": (45698384748, 15095085101, 15092375888, 15093926825, 15093628162),
    "keres": (45698384748, 15095085101, 15092236526, 15093442618, 15092783715),
    "najdorf": (45698384748, 15095085101, 15092647289, 15093433790, 15093223618),
    "polugaevsky": (45698384748, 15095085101, 15092076176, 15093942684, 15093254557),
    "portisch": (45698384748, 15095085101, 15093431389, 15094441168, 15094655832),
    "timman": (45698384748, 15095085101, 15092430029, 15094626849, 15093874196),
    "aronian": (45698384585, 15092413601, 15091326531, 15092016063, 15091368406),
    "caruana": (45698384585, 15092413601, 15089452338, 15091327521, 15091278856),
    "grischuk": (45698384585, 15092413601, 15090157499, 15092820745, 15092742403),
    "mamedyarov": (45698384585, 15092413601, 15090501106, 15092667143, 15093160267),
    "nakamura": (45698384585, 15092413601, 15089679954, 15091237904, 15092020124),
    "nepomniachtchi": (45698384585, 15092413601, 15091075475, 15091254852, 15091728934),
    "vachier-lagrave": (45698384585, 15092413601, 15089594665, 15092197766, 15092053479),
    "wesley-so": (45698384585, 15092413601, 15089214581, 15092092195, 15092470690),
    "chiburdanidze": (45698384602, 15092704334, 15090750415, 15093258013, 15093178052),
    "gaprindashvili": (45698384602, 15092704334, 15090148072, 15092433641, 15093371776),
    "ivanchuk": (45698384602, 15092704334, 15091068718, 15091624651, 15092886522),
    "leko": (45698384602, 15092704334, 15091328865, 15092717950, 15092916855),
    "mequinho": (45698384602, 15092704334, 15089484200, 15091369773, 15092745029),
    "shirov": (45698384602, 15092704334, 15092075405, 15092931592, 15092264240),
    "short": (45698384602, 15092704334, 15091575231, 15092765461, 15092197146),
    "chigorin": (45699740879, 15099326502, 15096164666, 15097123494, 15097257028),
    "kamsky": (45699740879, 15099326502, 15095204683, 15098393328, 15098158711),
    "karjakin": (45699740879, 15099326502, 15096002489, 15098514234, 15099735969),
    "korchnoi": (45699740879, 15099326502, 15095976072, 15098620784, 15098566538),
    "pillsbury": (45699740879, 15099326502, 15095936526, 15098106133, 15097593172),
    "reshevsky": (45699740879, 15099326502, 15095398173, 15097009343, 15097956947),
    "staunton": (45699740879, 15099326502, 15097395106, 15096319824, 15097316103),
    "svidler": (45699740879, 15099326502, 15096732734, 15098687606, 15099217016),
    "taimanov": (45699740879, 15099326502, 15095358325, 15098093606, 15098386886),
    "zukertort": (45699740879, 15099326502, 15096449250, 15096639137, 15097672579),
}


def review(slug, folder, out):
    run, aggregate_id, final_id, first_id, second_id = BINDINGS[slug]
    selection, final = folder / "measure", folder / "final"
    c, book, _ = read_candidate(slug, selection, final)
    if book is None:
        raise ValueError("Candidate failed calibration")
    doc, roots = bound_measure(slug, selection, source(slug))
    results = scores(slug, doc, roots, folder / "screen")
    if results != c["strength"]:
        raise ValueError("Screen results differ from replay")
    row = results[c["selected"]]
    if not all(eligible_quality(q) for q in (c["fit_stats"][c["selected"]], c["tune"])):
        raise ValueError("Historical human move-quality evidence failed")
    anchor_book = build("fischer", source_rows()["fischer"], period=(PERIODS | PEAKS)["fischer"])
    book_digest(anchor_book, "fischer")
    reports = []
    wdl = Counter()
    for seed in (2029, 2030):
        shard = folder / f"release-{seed}"
        r = json.loads((shard / "release.json").read_text())
        if (r["calibration"] != c or r["calibration_report_sha256"] != file_hash(final / "report.json")
                or r["seed"] != seed or r["engine_sha256"] != RODENT_HASHES["rodent"]
                or file_hash(shard / "candidate.txt") != c["profile_sha256"]
                or digest(json.loads((shard / "book.json").read_text())) != c["book_sha256"]):
            raise ValueError("Release identity mismatch")
        summaries = list((shard / "games").rglob("summary.json"))
        if len(summaries) != 1:
            raise ValueError("Expected exactly one paired duel")
        summary = json.loads(summaries[0].read_text())
        hashes = summary["file_hashes"]
        if (hashes["exe"] != RODENT_HASHES["rodent"] or hashes[slug] != c["profile_sha256"]
                or hashes["fischer"] != assets()["fischer"]["hashes"]["fischer_profile"]):
            raise ValueError("Played profile identity mismatch")
        games = replay(summaries[0].parent, {slug: book, "fischer": anchor_book}, slug, "fischer", seed)
        if games != r["games"]:
            raise ValueError("Book duel replay differs")
        for g in games:
            result = g["result"]
            outcome = ("draws" if result == "1/2-1/2" else
                       "wins" if (result == "1-0") == (g["white"] == slug) else "losses")
            wdl[outcome] += 1
        reports.append(r)
    proposal = release_recipe(*reports)
    aggregate = json.loads((folder / "proposals" / "release-proposals.json").read_text())
    if aggregate["players"][slug] != proposal:
        raise ValueError("Aggregate proposal differs from replay")
    proposal.update({
        "status": "FROZEN_GAME_ASSET",
        "qualification_run_id": run,
        "qualification_artifact_id": aggregate_id,
        "calibration_run_id": run,
        "calibration_artifact_id": final_id,
        "release_artifact_ids": [first_id, second_id],
        "gates": {"historical_practical_strength": True, "clock_play": True,
                  "reproducibility": True, "full_book_games": True},
        "evidence": {"screen_wdl": [row[k] for k in ("wins", "draws", "losses")],
                     "screen_score": row["score"],
                     "full_book_duel_wdl": [wdl[k] for k in ("wins", "draws", "losses")],
                     "anchor": "fischer", "seeds": [2029, 2030],
                     "fit_positions": sum(c["fit_phase_support"].values()),
                     "tune_positions": sum(c["tune_phase_support"].values()),
                     "fit_excess_cp": c["fit_stats"][c["selected"]]["excess_cp"],
                     "tune_excess_cp": c["tune"]["excess_cp"],
                     "fit_severe_excess": c["fit_stats"][c["selected"]]["severe_excess"],
                     "tune_severe_excess": c["tune"]["severe_excess"],
                     "repeat_matches": c["repeat_matches"],
                     "strength_proxy": "paired practical games and same-board human move quality",
                     "style_descriptive": {"tune_style_gap": c["tune"]["style_gap"],
                                           "tune_exact_match": c["tune"]["exact_match"]}},
        "decision": "Candidate for explicit registry promotion after independent artifact replay and runtime integration.",
        "restrictions": ["Any profile, depth, binary or book change requires new qualification"],
    })
    proposal["hashes"]["simple_control"] = RODENT_HASHES["simple"]
    proposal["limitations"] += ["Player-specific opening roots are not a common rating ladder",
                                "No verified player-specific historical Elo or relative ranking"]
    atomic_json(out, {"slug": slug, "asset": proposal})
    print("STRENGTH_REVIEW", json.dumps({"slug": slug, "evidence": proposal["evidence"]}, sort_keys=True))


def aggregate_review(folder, out):
    current = assets()
    if set(current) & set(BINDINGS):
        raise ValueError("Cannot overwrite existing frozen players")
    new = {}
    for slug in sorted(BINDINGS):
        r = json.loads((folder / f"review-{slug}" / "review.json").read_text())
        if r["slug"] != slug or r["asset"]["qualification_run_id"] != BINDINGS[slug][0]:
            raise ValueError("Wrong review shard")
        new[slug] = r["asset"]
    manifest = {"schema": "chesssim-qualified-game-assets-v1",
                "purpose": "Immutable product qualification registry; no automatic Arena/XP setting change",
                "players": {**current, **new}}
    atomic_json(out, manifest)
    print("REGISTRY_CANDIDATE", json.dumps(manifest, sort_keys=True))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--player", choices=sorted(BINDINGS))
    p.add_argument("--folder", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if a.player:
        review(a.player, a.folder, a.out)
    else:
        aggregate_review(a.folder, a.out)


if __name__ == "__main__":
    main()
