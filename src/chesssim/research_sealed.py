""" partition-sealed historical calibration and reproducible diagnostics.

Train tunes parameters; validation chooses from a frozen shortlist; test is
never evaluated here. final-eval is a separate explicitly invoked step.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import random
import shutil
import tempfile
import uuid

from .calibration import (PARAM_RANGES, _diagnostics, _fitness, _remove_inherited_opening_books,
                          evaluate_candidate, extract_values, file_hash, tune_text)
from .historical import extract_decisions, split_three_ways

PROTOCOL_V3 = {
    "version": "rodent-style-calibration-v3",
    "isolation": "train / selection-validation / sealed one-time final-test",
    "split_group": "whole game; optional (year,event) grouping; shared FEN discarded",
    "sampling": "seeded, by game/color/phase, unique normalized board state",
    "search": "fixed depth; all candidates train-only, fixed shortlist validated",
    "selection": "validation top1 strictly above baseline; tie means baseline retained",
    "final_test": "NOT evaluated during calibration; separate explicit final-eval",
    "books": False, "threads": 1, "hash_mb": 64,
    "safety": "external Stockfish optional at final-eval; strength benchmark separate",
    "promotion": "NEVER automatic; historical fidelity and human review still required",
}


def _canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_obj(obj):
    return sha256(_canonical(obj).encode()).hexdigest()


@contextmanager
def staged_profile(root: Path, contents: str):
    """Temporary candidate inside Rodent; never touches basic.ini or official files."""
    folder = root / "personalities" / "custom"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"chesssim-v3-{uuid.uuid4().hex}.txt"
    path = folder / name
    with path.open("x", encoding="utf-8") as file:
        file.write(contents)
    try:
        yield f"custom/{name}"
    finally:
        path.unlink(missing_ok=True)


def evaluate_text(root: Path, contents: str, positions: list[dict], depth: int) -> dict:
    with staged_profile(root, contents) as rel:
        return evaluate_candidate(root, rel, positions, depth)


def _by_game(metric: dict, positions: list[dict]) -> dict:
    trace = metric.get("_predicted_moves")
    if trace is None:
        return {"sampled_games": len({x["game"] for x in positions}), "per_game": "not available"}
    if len(trace) != len(positions):
        raise ValueError("Vetor de previsões desalinhado com posições")
    sums = {}
    for d, move in zip(positions, trace):
        row = sums.setdefault(d["game"], {"correct": 0, "total": 0})
        row["total"] += 1
        row["correct"] += move == d["move"]
    scores = [d["correct"] / d["total"] for d in sums.values()]
    return {"sampled_games": len(sums),
            "macro_top1_by_game": sum(scores)/len(scores) if scores else None,
            "min_game_accuracy": min(scores) if scores else None,
            "max_game_accuracy": max(scores) if scores else None}


def paired_game_bootstrap(reference: dict, candidate: dict, positions: list[dict],
                          seed: int = 2026, replicates: int = 500) -> dict:
    a, b = reference.get("_predicted_moves"), candidate.get("_predicted_moves")
    if a is None or b is None or len(a) != len(positions) or len(b) != len(positions):
        return {"status": "UNAVAILABLE", "reason": "Individual predictions not returned"}
    diffs = {}
    for d, original, challenger in zip(positions, a, b):
        diffs.setdefault(d["game"], []).append(int(challenger == d["move"]) - int(original == d["move"]))
    per_game = [sum(x)/len(x) for _, x in sorted(diffs.items())]
    n = len(per_game)
    if n < 5:
        return {"status": "INSUFFICIENT_GAMES", "sampled_games": n}
    rng = random.Random(seed)
    boots = sorted(sum(rng.choice(per_game) for _ in range(n))/n for _ in range(replicates))
    return {"status": "DESCRIPTIVE_ONLY", "sampled_games": n,
            "macro_gain": sum(per_game)/n,
            "bootstrap_95pct_interval": [boots[int(.025*(replicates-1))], boots[int(.975*(replicates-1))]],
            "replicates": replicates,
            "note": "Bootstrap por partida, com amostra e posições pequenas; não comprova fidelidade"}


def _mutate(origin: dict, ranges: dict[str, tuple[int, int]], rng: random.Random) -> dict:
    params = {k: v for k, v in origin.items() if k in ranges}
    keys = rng.sample(list(ranges), k=rng.randint(1, min(3, len(ranges))))
    for key in keys:
        lo, hi = ranges[key]
        current = params.get(key, (lo+hi)//2)
        params[key] = min(hi, max(lo, current + rng.choice([-1, 1]) * rng.randint(5, 25)))
    return params


def _safe_filename(slug: str) -> str:
    import re
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", slug):
        raise ValueError("Slug inválido: utilize letras minúsculas, números, _ e -")
    return slug


def calibrate_v3(root: Path, pgn: Path, output: Path, *, target: str,
                 target_slug: str, match_name: str, base_file: str = "karpov.txt",
                 depth: int = 5, trials: int = 24, train_positions: int = 120,
                 val_positions: int = 48, test_positions: int = 48,
                 shortlist: int = 4, seed: int = 2026,
                 from_year: int = 1973, to_year: int = 1978,
                 split_by: str = "game") -> dict:
    slug = _safe_filename(target_slug)
    pgn = pgn.resolve()
    output = output.resolve()
    if from_year > to_year or not 1 <= depth <= 20 or not 1 <= trials <= 1000:
        raise ValueError("Anos/profundidade/tentativas fora dos intervalos admitidos")
    if shortlist < 1 or shortlist > trials:
        raise ValueError("shortlist deve estar entre 1 e o número de trials")
    root = root.resolve()
    exe = root / "rodent-iv-plain.exe"
    if not exe.is_file():
        raise FileNotFoundError(exe)
    base_path = root / "personalities" / base_file
    if base_path.resolve().parent != (root / "personalities").resolve() or not base_path.is_file():
        raise ValueError("base-file deve ser arquivo existente diretamente em personalities")
    if output.exists():
        raise FileExistsError(output)
    decisions, source_stats = extract_decisions(pgn, player_name=match_name,
                                                from_year=from_year, to_year=to_year)
    train, validation, sealed_test, partition = split_three_ways(
        decisions, seed=seed, train_limit=train_positions, val_limit=val_positions,
        test_limit=test_positions, group_by=split_by)
    # Ensure final split exists, but never access its moves to fit/choose.
    assert sealed_test
    source_text = base_path.read_text(encoding="utf-8-sig")
    basis = extract_values(source_text)
    rng = random.Random(seed)
    output.mkdir(parents=True, exist_ok=False)
    history, contenders = [], []
    reference_training = None
    for i in range(trials+1):
        if i == 0:
            params = {}
        else:
            # Training results may guide search; validation/test results may NOT.
            top_train = max(contenders, key=lambda d: d["score"])
            origin = top_train["params"] if i % 3 == 0 else basis
            params = _mutate(origin, PARAM_RANGES, rng)
        txt = tune_text(source_text, params)
        raw = evaluate_text(root, txt, train, depth)
        if i == 0:
            reference_training = raw
        row = {"trial": i, "params": params, "training": _diagnostics(raw, reference_training)}
        history.append(row)
        contenders.append({"trial": i, "params": params, "text": txt, "score": _fitness(raw)})
        print(f"Treino {i:03d}/{trials}: {raw['correct']}/{raw['decisions']} ({_fitness(raw):.1%})", flush=True)
    ordered = sorted(contenders[1:], key=lambda c: (-c["score"], c["trial"]))
    shortlist_rows = [contenders[0], *ordered[:shortlist]]
    validation_results = []
    base_validation = None
    for candidate in shortlist_rows:
        raw = evaluate_text(root, candidate["text"], validation, depth)
        if candidate["trial"] == 0:
            base_validation = raw
        validation_results.append({"trial": candidate["trial"], "raw": raw,
                                   "metrics": _diagnostics(raw, base_validation),
                                   "macro": _by_game(raw, validation)})
        print(f"Validação tentativa {candidate['trial']:03d}: {raw['correct']}/{len(validation)}", flush=True)
    assert base_validation is not None
    # Frozen rule: do not promote a model unless it strictly BEATS baseline on validation.
    baseline_correct = base_validation["correct"]
    winner = max(validation_results, key=lambda item: (item["raw"]["correct"],
                                                       -item["trial"]))
    selected_trial = winner["trial"] if winner["raw"]["correct"] > baseline_correct else 0
    selected = contenders[selected_trial]
    best_train = ordered[0]
    best_train_path = output / f"{slug}_BEST_TRAIN_EXPERIMENTAL.txt"
    best_train_path.write_text(
        f"; EXPERIMENTAL TRAIN LEADER; NOT selected/validated\n"
        f"; initialized from {base_file}; no inherited opening books\n"
        + _remove_inherited_opening_books(best_train["text"]), encoding="utf-8")
    status = "VALIDATION_GAIN_EXPERIMENTAL" if selected_trial else "VALIDATION_NO_GAIN_BASELINE_RETAINED"
    selected_path = output / (f"{slug}_SELECTED_EXPERIMENTAL.txt" if selected_trial else
                               f"{slug}_BASELINE_NAO_PERSONALIZADO.txt")
    selected_path.write_text(
        f"; {target} - {status}; NOT historically validated\n"
        f"; initialized from {base_file}; training+validation only; test SEALED\n"
        f"; no books; do not register as a finished personality\n"
        + _remove_inherited_opening_books(selected["text"]), encoding="utf-8")
    winner_raw = next(r["raw"] for r in validation_results if r["trial"] == selected_trial)
    report = {
        "protocol": PROTOCOL_V3, "status": status,
        "target": target, "target_slug": slug, "match_name": match_name,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_stats": source_stats, "split": partition,
        "input": {"source_pgn": str(pgn), "pgn_sha256": file_hash(pgn),
                  "base_file": base_file, "base_sha256": file_hash(base_path),
                  "engine_sha256": file_hash(exe), "depth": depth, "trials": trials,
                  "shortlist": shortlist, "seed": seed, "from_year": from_year,
                  "to_year": to_year, "train_positions": train_positions,
                  "val_positions": val_positions, "test_positions": test_positions,
                  "split_by": split_by},
        "history": history,
        "validation_shortlist": [{"trial": r["trial"], "metrics": r["metrics"],
                                   "by_game": r["macro"]} for r in validation_results],
        "selection": {"rule": "strict validation correct-count gain; tied baseline wins",
                      "selected_trial": selected_trial,
                      "baseline_correct": baseline_correct,
                      "selected_correct": winner_raw["correct"],
                      "validation_gain": winner_raw["top1"] - base_validation["top1"],
                      "by_game": _by_game(winner_raw, validation),
                      "paired_game_bootstrap": paired_game_bootstrap(base_validation,
                                                     winner_raw, validation, seed=seed)},
        "best_training_trial": best_train["trial"],
        "best_training_profile": str(best_train_path),
        "best_training_profile_sha256": file_hash(best_train_path),
        "selected_profile": str(selected_path),
        "selected_profile_sha256": file_hash(selected_path),
        "final_test": {"status": "SEALED_NOT_EVALUATED",
                       "positions": len(sealed_test),
                       "manifest_sha256": partition["test_sealed"]["manifest_sha256"],
                       "command": "chesssim final-eval --run <calibration_dir>"},
        "limitations": ["No Elo inferred", "Top-1 imitation alone is not player identity",
                        "Validation was used for selection; never interpret as final test",
                        "Tactical quality and match strength require independent gates"]}
    (output / "calibration.json").write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    return report


def sensitivity_v3(root: Path, pgn: Path, out: Path, *, match_name: str,
                   base_file: str = "karpov.txt", seed: int = 2026,
                   depth: int = 4, positions: int = 32, delta: int = 20,
                   from_year: int = 1973, to_year: int = 1978,
                   split_by: str = "game") -> dict:
    if not 1 <= depth <= 20 or positions < 1 or delta <= 0:
        raise ValueError("depth/positions/delta inválidos")
    if out.exists():
        raise FileExistsError(out)
    root = root.resolve()
    base_path = root / "personalities" / base_file
    if base_path.resolve().parent != (root / "personalities").resolve() or not base_path.is_file():
        raise ValueError("base-file inválido")
    data, source = extract_decisions(pgn, player_name=match_name, from_year=from_year, to_year=to_year)
    train, _, _, split = split_three_ways(data, seed=seed, train_limit=positions,
                                          val_limit=1, test_limit=1, group_by=split_by)
    text = base_path.read_text(encoding="utf-8-sig")
    basis = extract_values(text)
    baseline = evaluate_text(root, text, train, depth)
    rows = []
    for key, (lo, hi) in PARAM_RANGES.items():
        current = basis.get(key, (lo+hi)//2)
        for direction in (-1, 1):
            value = min(hi, max(lo, current + direction*delta))
            if value == current:
                continue
            raw = evaluate_text(root, tune_text(text, {key: value}), train, depth)
            diag = _diagnostics(raw, baseline)
            rows.append({"parameter": key, "baseline_value": current,
                         "candidate_value": value, "top1_gain": raw["top1"]-baseline["top1"],
                         "changed_moves": diag.get("moves_changed_vs_baseline"),
                         "new_correct": raw["correct"]})
            print(f"{key:<20} {current:>4} -> {value:>4} | changed={diag.get('moves_changed_vs_baseline')}", flush=True)
    report = {"status": "EXPLORATORY_TRAIN_ONLY", "source": source, "split": split,
              "depth": depth, "delta": delta, "base_sha256": file_hash(base_path),
              "engine_sha256": file_hash(root/"rodent-iv-plain.exe"),
              "baseline": _diagnostics(baseline), "sensitivity": rows,
              "note": "Only training partition observed. Does not open validation or final-test targets."}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    return report
