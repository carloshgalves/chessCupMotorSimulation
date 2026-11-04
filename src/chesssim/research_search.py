"""v0.4 directed, grouped-CV research with an immutable final-test boundary.

No held-out test position is passed to evaluate_text here. Cross-validation
folds are nested inside TRAIN; fold-local sensitivity/optimization cannot see
its held-out fold. Validation selects once from a frozen train-only shortlist.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import random

from .calibration import (PARAM_RANGES, _diagnostics, _remove_inherited_opening_books,
                          extract_values, file_hash, tune_text)
from .historical import extract_decisions, split_three_ways
from .research_sealed import (_by_game, _safe_filename, evaluate_text)

BASELINES = ("karpov.txt", "petrosian.txt", "fischer.txt", "botvinnik.txt")
DEFAULT_PROBES = ("OwnAttack", "OppAttack", "OwnMobility", "KingTropism",
                  "PawnStructure", "PiecePressure")
PROTOCOL_V4 = {
    "version": "rodent-style-calibration-v4",
    "isolation": "fixed 3-way partition, same sealed manifest as v3 when --sealed-reference supplied",
    "cross_validation": "grouped folds inside training; probe and tune separately in each development fold",
    "baselines": "four official personalities screened on training ONLY",
    "search": "greedy coordinate mutations guided by measured sensitivity (no random parameter search)",
    "selection": "strictly beat every official baseline on validation; by-game macro may not decline",
    "objective": "top-1 imitation minus penalty for changed moves; divergence has NO positive reward",
    "final_test": "SEALED; v0.4 calibrator cannot read final-test moves or evaluate them",
    "books": False, "threads": 1, "hash_mb": 64,
    "promotion": "NEVER automatic; evaluation of style, strength, tactical quality and human review required",
}


def _objective(metric: dict, reference: dict, penalty: float) -> float:
    a, b = metric.get("_predicted_moves"), reference.get("_predicted_moves")
    if a is None or b is None or len(a) != len(b):
        raise ValueError("objective requires aligned individual predictions")
    if not a:
        raise ValueError("empty prediction sample")
    return metric["top1"] - penalty * sum(x != y for x, y in zip(a, b)) / len(a)


def _folds_by_game(train: list[dict], count: int, seed: int) -> list[tuple[list[dict], list[dict]]]:
    gids = sorted({d["game"] for d in train})
    if count < 2 or len(gids) < count * 2:
        raise ValueError("Insufficient independent sampled games for requested CV folds")
    random.Random(seed).shuffle(gids)
    assignments = {name: i % count for i, name in enumerate(gids)}
    result = []
    for fold in range(count):
        dev = [d for d in train if assignments[d["game"]] != fold]
        held = [d for d in train if assignments[d["game"]] == fold]
        if not dev or not held:
            raise ValueError("empty CV fold")
        assert not ({d["game"] for d in dev} & {d["game"] for d in held})
        result.append((dev, held))
    return result


def _sensitivity(root: Path, original: str, development: list[dict], *,
                 probe_params: tuple[str, ...], depth: int, delta: int,
                 probe_positions: int, seed: int, penalty: float) -> tuple[list[dict], dict]:
    """All measured probes are taken from development only, never fold heldout."""
    pool = list(development)
    random.Random(seed).shuffle(pool)
    probe = pool[:min(probe_positions, len(pool))]
    base = evaluate_text(root, original, probe, depth)
    values = extract_values(original)
    ranked = []
    for key in probe_params:
        lo, hi = PARAM_RANGES[key]
        default = values.get(key, (lo + hi) // 2)
        for direction in (-1, 1):
            value = max(lo, min(hi, default + direction * delta))
            if value == default:
                continue
            result = evaluate_text(root, tune_text(original, {key: value}), probe, depth)
            ranked.append({"parameter": key, "value": value,
                           "base_value": default, "changed": _diagnostics(result, base)["moves_changed_vs_baseline"],
                           "correct_gain": result["correct"]-base["correct"],
                           "objective_gain": _objective(result, base, penalty)-_objective(base, base, penalty)})
    ranked.sort(key=lambda r: (-r["objective_gain"], -r["correct_gain"],
                               r["changed"], r["parameter"], r["value"]))
    return ranked, base


def _directed_search(root: Path, original: str, training: list[dict], *,
                     trials: int, depth: int, delta: int, penalty: float,
                     seed: int, probe_positions: int, probe_params: tuple[str, ...]):
    """Probe directional changes, then explore candidates on training only.

    A candidate replaces current best ONLY if it improves the penalized
    objective; an equally accurate but more divergent configuration cannot win.
    """
    probes, _ = _sensitivity(root, original, training, probe_params=probe_params,
                             depth=depth, delta=delta, penalty=penalty,
                             seed=seed, probe_positions=probe_positions)
    baseline = evaluate_text(root, original, training, depth)
    best = {"params": {}, "raw": baseline,
            "objective": _objective(baseline, baseline, penalty), "trial": 0}
    candidates = [best]
    known = {()}
    basis = extract_values(original)
    for index in range(1, trials + 1):
        proposal = None
        # Prefer promising single-coordinate moves before combinations, use a
        # deterministic breadth search without letting validation guide it.
        for probe in probes:
            params = dict(best["params"])
            params[probe["parameter"]] = probe["value"]
            signature = tuple(sorted(params.items()))
            if signature not in known:
                proposal = params
                break
        if proposal is None:
            # A second increment can find useful thresholds missed by first step.
            for probe in probes:
                params = dict(best["params"])
                name = probe["parameter"]
                current = params.get(name, basis.get(name, probe["base_value"]))
                difference = 1 if probe["value"] > probe["base_value"] else -1
                low, high = PARAM_RANGES[name]
                params[name] = min(high, max(low, current + difference * delta))
                signature = tuple(sorted(params.items()))
                if signature not in known:
                    proposal = params
                    break
        if proposal is None:
            break
        known.add(tuple(sorted(proposal.items())))
        raw = evaluate_text(root, tune_text(original, proposal), training, depth)
        score = _objective(raw, baseline, penalty)
        item = {"trial": index, "params": proposal, "raw": raw, "objective": score}
        candidates.append(item)
        if score > best["objective"] + 1e-12:
            best = item
    return candidates, best, probes, baseline


def _load_bases(root: Path, filenames: tuple[str, ...]) -> dict[str, tuple[str, Path]]:
    if not filenames or len(set(filenames)) != len(filenames):
        raise ValueError("Inform distinct base profile filenames")
    directory = (root / "personalities").resolve()
    loaded = {}
    for name in filenames:
        path = (directory / name).resolve()
        if path.parent != directory or not path.is_file() or not name.endswith(".txt"):
            raise ValueError(f"Invalid or missing direct Rodent personality: {name}")
        loaded[name] = (path.read_text(encoding="utf-8-sig"), path)
    return loaded


def _check_v3_seal(reference: Path | None, *, pgn_hash: str, partition: dict,
                   seed: int, group_by: str, test_count: int) -> dict:
    if reference is None:
        return {"status": "NEW_COHORT_NO_V3_REFERENCE"}
    report_path = reference / "calibration.json" if reference.is_dir() else reference
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("protocol", {}).get("version") != "rodent-style-calibration-v3":
        raise ValueError("--sealed-reference must point to a v0.3 calibration.json")
    past = report["input"]
    if (report_path.parent / "final_test.json").exists():
        raise ValueError("The v0.3 final test has already been opened; cannot claim it remains sealed")
    if (past["pgn_sha256"] != pgn_hash or past["seed"] != seed or
            past["split_by"] != group_by or past["test_positions"] != test_count or
            report["final_test"]["manifest_sha256"] != partition["test_sealed"]["manifest_sha256"]):
        raise ValueError("V3 TEST SEAL MISMATCH: same PGN, partition seed, grouping and test count required")
    return {"status": "MATCHES_V3_SEAL", "reference_sha256": file_hash(report_path),
            "reference_manifest_sha256": report["final_test"]["manifest_sha256"]}


def _assert_ledger(path: Path, cohort: dict):
    """Sticky per-dataset seal: reject future split drift, never overwrite."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump(cohort, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
        except FileExistsError:
            pass
    stored = json.loads(path.read_text(encoding="utf-8"))
    if stored != cohort:
        raise ValueError("SEALED COHORT CONFLICT: existing registry forbids another test partition")


def calibrate_v4(root: Path, pgn: Path, output: Path, *, target: str, target_slug: str,
                 match_name: str, base_files: tuple[str, ...] = BASELINES,
                 partition_seed: int = 2027, search_seed: int = 31415,
                 from_year: int = 1973, to_year: int = 1978,
                 train_positions: int = 120, val_positions: int = 36,
                 test_positions: int = 30, split_by: str = "game",
                 depth: int = 4, trials: int = 8, shortlist: int = 3,
                 folds: int = 3, cv_trials: int = 3, delta: int = 20,
                 probe_positions: int = 20,
                 probe_params: tuple[str, ...] = DEFAULT_PROBES,
                 divergence_penalty: float = 0.08,
                 sealed_reference: Path | None = None,
                 ledger: Path | None = None) -> dict:
    slug = _safe_filename(target_slug)
    root, pgn, output = root.resolve(), pgn.resolve(), output.resolve()
    if not (1 <= depth <= 20 and 1 <= trials <= 100 and
            1 <= shortlist <= trials and 2 <= folds <= 8 and
            1 <= cv_trials <= 25 and delta > 0 and probe_positions >= 5 and
            0 <= divergence_penalty <= 1 and from_year <= to_year):
        raise ValueError("Out-of-range depth/trials/folds/sensitivity/penalty/date settings")
    if train_positions < folds * 4 or val_positions < 5 or test_positions < 5:
        raise ValueError("Too few positions for grouped training/validation/final-test")
    if any(p not in PARAM_RANGES for p in probe_params) or not probe_params or len(set(probe_params)) != len(probe_params):
        raise ValueError("Probe parameters must be unique Rodent parameters")
    if output.exists():
        raise FileExistsError(output)
    engine = root / "rodent-iv-plain.exe"
    if not engine.is_file():
        raise FileNotFoundError(engine)
    bases = _load_bases(root, base_files)
    if slug == "mecking" and sealed_reference is None:
        raise ValueError("Mecking already has a v0.3 sealed test: pass --sealed-reference path/to/v3/run")
    decisions, source = extract_decisions(pgn, player_name=match_name,
                                          from_year=from_year, to_year=to_year)
    train, validation, _sealed, partition = split_three_ways(
        decisions, seed=partition_seed, train_limit=train_positions,
        val_limit=val_positions, test_limit=test_positions, group_by=split_by)
    # Do not use the sealed test anywhere beyond manifest confirmation.
    seal = _check_v3_seal(sealed_reference, pgn_hash=source["pgn_sha256"],
                          partition=partition, seed=partition_seed,
                          group_by=split_by, test_count=test_positions)
    cohort = {"version": 1, "pgn_sha256": source["pgn_sha256"],
              "player": match_name, "from_year": from_year, "to_year": to_year,
              "split_by": split_by, "partition_seed": partition_seed,
              "sealed_manifest_sha256": partition["test_sealed"]["manifest_sha256"]}
    _assert_ledger((ledger or Path("calibracoes/registry") / (slug+"-sealed-v04.json")).resolve(), cohort)
    folds_data = _folds_by_game(train, folds, partition_seed + 81)
    baseline_train = {}
    print("Official baseline comparison on TRAIN (no validation/test used):", flush=True)
    for name, (text, _path) in bases.items():
        raw = evaluate_text(root, text, train, depth)
        baseline_train[name] = {"raw": raw, "macro": _by_game(raw, train)}
        print(f"  {name:<19} {raw['correct']}/{len(train)}; game macro={baseline_train[name]['macro']['macro_top1_by_game']:.2%}", flush=True)
    base_name = max(base_files, key=lambda name: (baseline_train[name]["macro"]["macro_top1_by_game"],
                                              baseline_train[name]["raw"]["correct"], -base_files.index(name)))
    original = bases[base_name][0]
    print(f"Starting base selected from training: {base_name}", flush=True)
    cross_validation = []
    for index, (dev, held) in enumerate(folds_data):
        # Nested CV: choose the INITIAL profile again, using development only.
        # Choosing the globally best training profile would leak heldout fold
        # labels into the CV model-selection step.
        dev_comparisons = {}
        for name, (text, _) in bases.items():
            raw = evaluate_text(root, text, dev, depth)
            dev_comparisons[name] = (raw, _by_game(raw, dev)["macro_top1_by_game"])
        local_base = max(base_files, key=lambda name: (
            dev_comparisons[name][1], dev_comparisons[name][0]["correct"],
            -base_files.index(name)))
        local_source = bases[local_base][0]
        items, tuned, probes, _ = _directed_search(
            root, local_source, dev, trials=cv_trials, depth=depth,
            delta=delta, penalty=divergence_penalty,
            seed=search_seed+index*19, probe_positions=probe_positions,
            probe_params=probe_params)
        # This heldout fold has NOT been passed to _sensitivity or _directed_search.
        reference = evaluate_text(root, local_source, held, depth)
        other = evaluate_text(root, tune_text(local_source, tuned["params"]), held, depth) if tuned["params"] else reference
        cross_validation.append({"fold": index, "fold_chosen_base": local_base, "development_games": len({d['game'] for d in dev}),
                                 "heldout_games": len({d['game'] for d in held}),
                                 "heldout_positions": len(held),
                                 "train_selected_params": tuned["params"],
                                 "heldout_baseline": _diagnostics(reference),
                                 "heldout_candidate": _diagnostics(other, reference),
                                 "heldout_macro_gain": _by_game(other, held)["macro_top1_by_game"] - _by_game(reference, held)["macro_top1_by_game"],
                                 "probes_ranked": probes[:min(8,len(probes))]})
        print(f"CV fold {index+1}/{folds}: {reference['correct']} -> {other['correct']} "
              f"on {len(held)} heldout TRAIN positions", flush=True)
    items, best, probes, reference_train = _directed_search(
        root, original, train, trials=trials, depth=depth,
        delta=delta, penalty=divergence_penalty, seed=search_seed+999,
        probe_positions=probe_positions, probe_params=probe_params)
    ranked = sorted(items[1:], key=lambda x: (-x["objective"], x["trial"]))
    frozen_shortlist = [items[0], *ranked[:shortlist]]
    # Validation consulted only now; every baseline is a fair comparator on same positions.
    baseline_validations = {}
    for name, (text, _) in bases.items():
        raw = evaluate_text(root, text, validation, depth)
        baseline_validations[name] = raw
    strongest = max(base_files, key=lambda name: (
        baseline_validations[name]["correct"],
        _by_game(baseline_validations[name], validation)["macro_top1_by_game"],
        -base_files.index(name)))
    selected_base_raw = baseline_validations[strongest]
    selected = None
    checks = []
    for candidate in frozen_shortlist[1:]:
        raw = evaluate_text(root, tune_text(original, candidate["params"]), validation, depth)
        macro = _by_game(raw, validation)["macro_top1_by_game"]
        threshold_macro = _by_game(selected_base_raw, validation)["macro_top1_by_game"]
        # Must strictly improve historical imitation, avoid worse per-game outcomes,
        # and must survive a positive divergence penalty (difference never rewarded).
        changes = _diagnostics(raw, baseline_validations[base_name])["move_change_ratio"]
        penalized_gain = raw["top1"] - selected_base_raw["top1"] - divergence_penalty*changes
        accepted = (raw["correct"] > selected_base_raw["correct"] and
                    macro >= threshold_macro and penalized_gain > 0)
        checks.append({"trial": candidate["trial"], "validation": _diagnostics(raw, baseline_validations[base_name]),
                       "macro_by_game": macro, "penalized_gain_vs_strongest_baseline": penalized_gain,
                       "accepted": accepted})
        if accepted and (selected is None or (raw["correct"], macro, -changes) >
                         (selected["raw"]["correct"], selected["macro"], -selected["changes"])):
            selected = {"item": candidate, "raw": raw, "macro": macro, "changes": changes}
    status = "VALIDATION_GAIN_EXPERIMENTAL" if selected else "VALIDATION_NO_GAIN_BASELINE_RETAINED"
    chosen_text = tune_text(original, selected["item"]["params"]) if selected else bases[strongest][0]
    best_train = ranked[0]
    # File creation occurs AFTER all evaluation and selection, with immutable summary.
    output.mkdir(parents=True, exist_ok=False)
    lead_path = output / f"{slug}_BEST_TRAIN_EXPERIMENTAL.txt"
    lead_path.write_text("; TRAIN LEADER ONLY; NOT validated; NO inherited opening books\n"+
                         _remove_inherited_opening_books(tune_text(original, best_train["params"])), encoding="utf-8")
    selected_path = output / (f"{slug}_SELECTED_EXPERIMENTAL.txt" if selected else
                              f"{slug}_BASELINE_NAO_PERSONALIZADO.txt")
    selected_path.write_text(f"; {status}; NOT historically validated; final test SEALED\n"+
                             _remove_inherited_opening_books(chosen_text), encoding="utf-8")
    report = {
        "protocol": PROTOCOL_V4, "status": status, "target": target,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_stats": source, "split": partition,
        "sealed_reference": seal, "cohort_registry": cohort,
        "input": {"pgn": str(pgn), "pgn_sha256": file_hash(pgn),
                  "engine_sha256": file_hash(engine), "starting_base": base_name,
                  "baseline_files": {name: {"sha256": file_hash(path)} for name, (_,path) in bases.items()},
                  "depth": depth, "partition_seed": partition_seed, "search_seed": search_seed,
                  "cv_folds": folds, "cv_trials": cv_trials,
                  "train_positions": train_positions, "validation_positions": val_positions,
                  "test_positions": test_positions, "trials": trials,
                  "shortlist": shortlist, "delta": delta, "probe_positions": probe_positions,
                  "probe_params": list(probe_params), "divergence_penalty": divergence_penalty},
        "official_training_comparison": [
            {"profile": name, "sha256": file_hash(bases[name][1]),
             "metrics": _diagnostics(baseline_train[name]["raw"]),
             "by_game": baseline_train[name]["macro"]} for name in base_files],
        "cross_validation": cross_validation,
        "directed_sensitivity": probes,
        "search_history": [{"trial": x["trial"], "params": x["params"],
                            "objective": x["objective"],
                            "training": _diagnostics(x["raw"], reference_train)} for x in items],
        "validation_baselines": [{"profile": name, "metrics": _diagnostics(baseline_validations[name]),
                                  "by_game": _by_game(baseline_validations[name], validation)} for name in base_files],
        "validation_shortlist": checks,
        "selection": {"rule": "strict top1 gain over ALL reference profiles, macro-by-game nondeclining, penalized gain > 0",
                      "starting_base_train_only": base_name, "strongest_baseline_validation": strongest,
                      "selected_trial": selected["item"]["trial"] if selected else 0,
                      "selected_is_personalized": bool(selected),
                      "best_baseline_correct": selected_base_raw["correct"],
                      "selected_correct": selected["raw"]["correct"] if selected else selected_base_raw["correct"]},
        "best_training_trial": best_train["trial"],
        "best_training_profile": str(lead_path), "best_training_sha256": file_hash(lead_path),
        "selected_profile": str(selected_path), "selected_profile_sha256": file_hash(selected_path),
        "final_test": {"status": "SEALED_NOT_EVALUATED", "positions": partition["test_sealed"]["positions"],
                       "manifest_sha256": partition["test_sealed"]["manifest_sha256"],
                       "note": "Do not use v0.3 final-eval on v0.4 output; keep seal reserved until v0.4 review."},
        "limitations": ["Small sample; no Elo or historical identity inferred", "CV heldout folds are within TRAIN",
                        "Validation is used for selection; no final-test scoring", "No Stockfish tactical gate executed"]
    }
    (output/"calibration.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return report
