"""Reproducible exploratory SHADOW/GUIDED full-game UCI seed crossover.

A fixed seed is an *experiment repetition*, not a historical source game.
The authentic Rodent IV, archived real85 FIT books, fit-on-80%+ historical
neural residuals, and the depth-confirmed Stockfish guard are unchanged.
Each block compares the two arms with the SAME seed and player color.
Do not turn this small observational crossover into human Elo.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import mean

import chess.pgn

from .game_diversity import mainline_identity
from .matches import file_hash
from .real85_neural_counterfactual import _load_replayed, _read_json
from .real85_neural_guarded_uci import SCHEMA as GAME_SCHEMA, play

SCHEMA = "chesssim-real85-depth-confirmed-four-player-crossover-v1"
DEFAULT_PLAYERS = ("fischer", "tal", "karpov", "kasparov")
DEFAULT_SEEDS = (2033, 2041, 2053, 2063)


def _validated_seeds(seeds: list[int] | tuple[int, ...]) -> list[int]:
    if (not isinstance(seeds, (list, tuple)) or
            len(seeds) < 2 or len(seeds) > 8 or
            any(type(seed) is not int or not 0 <= seed <= 2**31 - 1
                for seed in seeds) or len(set(seeds)) != len(seeds)):
        raise ValueError("Expected 2..8 distinct bounded predetermined opening seeds")
    return list(seeds)


def _focal_points(game: dict) -> float:
    if game["status"] != "COMPLETED":
        raise ValueError("Unfinished games cannot enter crossover scores")
    result = game["result"]
    if result == "1/2-1/2":
        return 0.5
    if result not in ("1-0", "0-1"):
        raise ValueError("Invalid terminal UCI result")
    return float((result == "1-0") == (game["color"] == "white"))


def _pgn_identities(path: Path, expected_seed: int) -> list[tuple[str,str,str]]:
    games = []
    with path.open(encoding="utf-8") as stream:
        while (game := chess.pgn.read_game(stream)) is not None:
            if (game.errors or game.headers.get("OpeningSeed") != str(expected_seed)
                    or game.headers.get("BookMode") != "historical-v09-exploratory"):
                raise ValueError("Corrupt, wrong-seed or unbooked real UCI game in crossover PGN")
            identity_sha = game.headers.get("ExperimentalNeuralSourceSHA256")
            if (not isinstance(identity_sha, str) or len(identity_sha) != 64
                    or any(ch not in "0123456789abcdef" for ch in identity_sha)):
                raise ValueError("Original UCI game lacks verified frozen neural model identity")
            digest, _ = mainline_identity(game)
            # Opening variation directly observed in the genuine game,
            # not synthesized by the experimental runner.
            opening = " ".join(move.uci() for i,move in
                               enumerate(game.mainline_moves()) if i < 12)
            games.append((digest,opening,identity_sha))
    return games


def summarize(folder: Path, seeds: list[int] | tuple[int, ...] = DEFAULT_SEEDS,
              *, expected_players: tuple[str, ...] = DEFAULT_PLAYERS) -> dict:
    """Verify complete original PGNs and paired block outcomes, then score."""
    selected = _validated_seeds(seeds)
    if (tuple(expected_players) != DEFAULT_PLAYERS or
            not Path(folder).is_dir() or Path(folder).is_symlink()):
        raise ValueError("Crossover is preregistered for the four historical pilots")
    baseline = None
    cross: dict[str, dict[tuple[int,str], dict]] = {
        slug:{} for slug in DEFAULT_PLAYERS
    }
    paths = {}
    unique = defaultdict(lambda: defaultdict(set))
    openers = defaultdict(lambda: defaultdict(set))
    player_model_hashes = {}
    for seed in selected:
        root = Path(folder)/f"seed-{seed}"
        result_file = root/"results.json"
        pgn_file = root/"games.pgn"
        if (root.is_symlink() or result_file.is_symlink() or pgn_file.is_symlink()
                or not root.is_dir()):
            raise ValueError("Missing/unsafe original UCI seed evidence")
        document = _read_json(result_file)
        if (document.get("schema") != GAME_SCHEMA or
                document.get("status") != "ALL_REAL_UCI_GAMES_COMPLETE" or
                document.get("opening_seed") != seed or
                document.get("min_ply") != 41 or
                document.get("stockfish_depth") != 8 or
                document.get("confirmation_depth") != 12 or
                document.get("cp_budget") != 25 or
                document.get("deep_guard_enabled") is not True or
                document.get("historical_focal_book_enabled") is not True or
                document.get("players") != list(DEFAULT_PLAYERS)):
            raise ValueError("Wrong/unfinished historical four-profile seed experiment")
        provenance = {
            "rodent_original_sha256":document["rodent_original_sha256"],
            "stockfish_sha256":document["stockfish_sha256"],
            "rodent_depth":document["rodent_depth"],
            "historical_book_training":document["historical_book_training"],
        }
        if baseline is None:
            baseline = provenance
        elif baseline != provenance:
            raise ValueError("Mixed Rodent, Stockfish, depth or corpus across seed arms")
        _load_replayed(document, pgn_file)
        ids = _pgn_identities(pgn_file, seed)
        if len(ids) != len(document["games"]) or len(ids) != 16:
            raise ValueError("Real UCI game count and hashes do not reconcile")
        pairs = defaultdict(dict)
        for record, (identity, opening, model_sha) in zip(document["games"], ids):
            slug = record["player"]
            if slug in player_model_hashes and player_model_hashes[slug] != model_sha:
                raise ValueError("Historical neural weights changed across seed/color arms")
            player_model_hashes[slug] = model_sha
            if record.get("opening_seed") != seed:
                raise ValueError("Detached per-game opening seed")
            slug, arm, color = record["player"],record["arm"],record["color"]
            key = (slug,color)
            if arm in pairs[key]:
                raise ValueError("Duplicate SHADOW/GUIDED color arm")
            pairs[key][arm] = record
            unique[slug][arm].add(identity)
            openers[slug][arm].add(opening)
        if len(pairs) != 8 or any(set(arms)!={"shadow","guided"}
                                  for arms in pairs.values()):
            raise ValueError("Missing paired UCI seed/color condition")
        for (slug,color), arms in pairs.items():
            guided,shadow = arms["guided"],arms["shadow"]
            if shadow["actual_overrides"] != 0:
                raise ValueError("SHADOW observation illegally overrode Rodent")
            if guided["simple_book_plies"] or shadow["simple_book_plies"]:
                raise ValueError("Simple control must not use historical book")
            for game in (guided,shadow):
                traces = game["policy_trace"]
                if (game["actual_overrides"] != sum(
                        t["outcome"]=="EXTERNAL_POLICY_OVERRIDE" for t in traces) or
                        game["deep_confirmed_candidate_pairs"] < game["actual_overrides"] or
                        game["deep_rejected"] != sum(
                            t["outcome"] in ("DEEP_CP_GUARD_RETAINED_RODENT",
                                             "DEEP_MATE_GUARD_RETAINED_RODENT")
                            for t in traces)):
                    raise ValueError("Forged empirical override or safety-rejection count")
                for event in traces:
                    if event["outcome"] == "EXTERNAL_POLICY_OVERRIDE":
                        if (event.get("confirmation_depth") != 12 or
                                event.get("confirmation_original_mate") is not None or
                                event.get("confirmation_proposed_mate") is not None or
                                type(event.get("confirmation_neural_minus_rodent_cp")) is not int or
                                event["confirmation_neural_minus_rodent_cp"] < -25):
                            raise ValueError("Real guided neural decision escaped deep safety")
            block = {
                "seed":seed,"color":color,
                "guided_result":guided["result"],
                "shadow_result":shadow["result"],
                "guided_points":_focal_points(guided),
                "shadow_points":_focal_points(shadow),
                "paired_point_difference":(
                    _focal_points(guided)-_focal_points(shadow)),
                "guided_neural_overrides":guided["actual_overrides"],
                "shadow_neural_overrides":0,
                "guided_deep_rejected":guided["deep_rejected"],
                "shadow_deep_rejected":shadow["deep_rejected"],
                "guided_book_plies":guided["focal_historical_book_plies"],
                "shadow_book_plies":shadow["focal_historical_book_plies"],
                "guided_plies":guided["plies"],
                "shadow_plies":shadow["plies"],
            }
            if (seed,color) in cross[slug]:
                raise ValueError("Duplicate paired experimental block")
            cross[slug][(seed,color)] = block
        paths[str(seed)] = {
            "results_sha256":file_hash(result_file),
            "games_sha256":file_hash(pgn_file),
            "games_replayed":len(ids),
        }
    if any(len(blocks) != 2*len(selected) for blocks in cross.values()):
        raise ValueError("Incomplete crossovers")
    population = {}
    for slug, blocks in cross.items():
        rows = [blocks[(seed,color)] for seed in selected for color in ("white","black")]
        by_seed = []
        for seed in selected:
            cohort = [row for row in rows if row["seed"]==seed]
            by_seed.append({
                "seed":seed,
                "guided_points_two_colors":sum(x["guided_points"] for x in cohort),
                "shadow_points_two_colors":sum(x["shadow_points"] for x in cohort),
                "paired_point_difference_two_colors":sum(
                    x["paired_point_difference"] for x in cohort),
            })
        n = len(rows)
        population[slug] = {
            "games_per_arm":n,
            "complete_games":n*2,
            "paired_seed_color_blocks":n,
            "guided_points":sum(x["guided_points"] for x in rows),
            "shadow_points":sum(x["shadow_points"] for x in rows),
            "paired_score_difference_points":sum(x["paired_point_difference"] for x in rows),
            "paired_mean_difference_per_game":round(mean(
                x["paired_point_difference"] for x in rows),6),
            "guided_actual_neural_overrides":sum(x["guided_neural_overrides"] for x in rows),
            "guided_deep_rejections":sum(x["guided_deep_rejected"] for x in rows),
            "guided_historical_book_plies":sum(x["guided_book_plies"] for x in rows),
            "unique_guided_game_trajectories":len(unique[slug]["guided"]),
            "unique_shadow_game_trajectories":len(unique[slug]["shadow"]),
            "unique_guided_first12_uci_openings":len(openers[slug]["guided"]),
            "unique_shadow_first12_uci_openings":len(openers[slug]["shadow"]),
            "seed_clusters":by_seed,
            "games":[*rows],
            "confidence_interval":None,
            "strength_inference":"INSUFFICIENT_INDEPENDENT_SEED_CLUSTERS_FOR_ELO_OR_CI",
        }
    result = {
        "schema":SCHEMA,
        "status":"EXPLORATORY_ACTUAL_UCI_MULTISEED_CROSSOVER_NOT_HUMAN_ELO",
        "seeds":selected,
        "players":population,
        "total_complete_uci_games":sum(x["complete_games"] for x in population.values()),
        "total_paired_seed_color_blocks":sum(x["paired_seed_color_blocks"]
                                             for x in population.values()),
        "total_guided_actual_overrides":sum(x["guided_actual_neural_overrides"]
                                             for x in population.values()),
        "total_guided_historical_book_plies":sum(x["guided_historical_book_plies"]
                                                 for x in population.values()),
        "original_engines_and_model_profiles_unchanged":True,
        "historical_test_or_final_holdout_used_for_parameter_tuning":False,
        "preselected_seeds_not_adapted_to_results":True,
        "mixed_historical_and_new_seed_note":
            "2033 was inspected in earlier pilots; all seeds and match settings are development observations, not independent final evidence.",
        "paired_interpretation":
            "SHADOW/GUIDED matched by seed/color/opponent/depth, but diverging actual chess trajectories preclude per-position causal equivalence.",
        "source_binary_provenance":baseline,
        "per_player_frozen_model_sha256":player_model_hashes,
        "source_files":paths,
    }
    target = Path(folder)/"crossover-report.json"
    if target.exists() or target.is_symlink():
        raise FileExistsError("Crossover report already exists")
    with target.open("x",encoding="utf-8",newline="") as writer:
        json.dump(result,writer,ensure_ascii=False,sort_keys=True,allow_nan=False)
        writer.write("\n")
    return result


def run(rodent: Path, stockfish: Path, weights: Path, profiles: Path, out: Path,
        *, seeds: list[int] | tuple[int, ...] = DEFAULT_SEEDS,
        rodent_depth: int = 4) -> dict:
    selected = _validated_seeds(seeds)
    out = Path(out)
    if (out.exists() or out.is_symlink() or not out.parent.is_dir() or
            type(rodent_depth) is not int or not 1 <= rodent_depth <= 12):
        raise ValueError("Crossover requires new output and bounded Rodent UCI depth")
    out.mkdir()
    for seed in selected:
        per_seed = out/f"seed-{seed}"
        result=play(rodent,stockfish,weights,per_seed,
                    players=list(DEFAULT_PLAYERS),rodent_depth=rodent_depth,
                    stockfish_depth=8,cp_budget=25,min_ply=41,
                    max_plies=450,profiles=profiles,confirmation_depth=12,
                    opening_seed=seed)
        if result["status"] != "ALL_REAL_UCI_GAMES_COMPLETE":
            raise RuntimeError(f"Real UCI seed {seed} contains incomplete games")
    return summarize(out,selected)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Real four-player, multi-seed paired UCI development crossover")
    parser.add_argument("--rodent-root",type=Path,required=True)
    parser.add_argument("--stockfish",type=Path,required=True)
    parser.add_argument("--weights",type=Path,required=True)
    parser.add_argument("--profiles",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--seeds",type=int,nargs="+",default=list(DEFAULT_SEEDS))
    parser.add_argument("--rodent-depth",type=int,default=4)
    args=parser.parse_args(argv)
    report=run(args.rodent_root,args.stockfish,args.weights,args.profiles,args.out,
               seeds=args.seeds,rodent_depth=args.rodent_depth)
    print(json.dumps({
        "status":report["status"],
        "total_complete_uci_games":report["total_complete_uci_games"],
        "total_guided_actual_overrides":report["total_guided_actual_overrides"],
        "players":{slug:{
            "guided_points":data["guided_points"],
            "shadow_points":data["shadow_points"],
            "paired_mean_score_delta":data["paired_mean_difference_per_game"],
            "unique_guided_first12_uci_openings":data["unique_guided_first12_uci_openings"],
        } for slug,data in report["players"].items()},
    },sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
