"""v0.8: controlled temporal sampling and provisional profile materialization.

This module adds two exploratory capabilities:
1. compare peak/mixed/career training mixes while controlling the number of
   independent games and the per-game position budget;
2. build a provisional playable Rodent profile from a chosen temporal mix.

Nothing here opens the sealed historic tests.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean, pstdev
import random

from .calibration import file_hash, tune_text
from .historical import position_key
from .peak_v6 import annotate_decisions, load_annotations, load_peak_window
from .research_v3 import evaluate_text
from .research_v4 import _directed_search, _folds_by_game, _load_bases, DEFAULT_PROBES
from .research_v5 import _choose_base
from .research_v6 import _cohort_v5_training, _distinct_positions

PROTOCOL = {
    "version": "temporal-controlled-v8",
    "evaluation": "same heldout peak-era positions for every arm, grouped by game",
    "control": "same position budget/depth/trials, plus explicit caps on training games and positions per game",
    "arms": "peak, career and user-declared temporal mixes",
    "selection": "exploratory only; no sealed validation or final historic test is opened",
    "promotion": "disabled; profiles emitted here are provisional and playable, not validated",
}


def _mode_rows(rows: list[dict], annotations: dict, include_unknown: bool) -> list[dict]:
    checked = annotate_decisions(rows, annotations)
    return _distinct_positions([
        d for d in checked if
        (d['modality'] == 'classical' and d['modality_evidence'] in ('TIMECONTROL_TAG','DOCUMENTED_EXTERNAL')) or
        (include_unknown and d['modality'] == 'unknown')
    ])


def _profile_eval(root: Path, bases: dict, development: list[dict], held: list[dict], *, depth: int,
                  trials: int, probes: int, penalty: float, seed: int) -> dict:
    origin, scores = _choose_base(bases, development, root, depth)
    baseline_text = bases[origin][0]
    params: dict = {}
    if trials:
        _, best, _, _ = _directed_search(
            root,
            baseline_text,
            development,
            trials=trials,
            depth=depth,
            delta=20,
            penalty=penalty,
            seed=seed,
            probe_positions=min(probes, len(development)),
            probe_params=DEFAULT_PROBES,
        )
        params = dict(best["params"])
    contents = tune_text(baseline_text, params) if params else baseline_text
    prediction = evaluate_text(root, contents, held, depth)
    if len(prediction["_predicted_moves"]) != len(held):
        raise ValueError("Engine returned unaligned predictions")
    by_game = {}
    for row, move in zip(held, prediction["_predicted_moves"]):
        stat = by_game.setdefault(row["game"], {"total": 0, "correct": 0})
        stat["total"] += 1
        stat["correct"] += int(move == row["move"])
    return {
        "base": origin,
        "params": params,
        "baseline_screening": {
            name: {
                "correct": data["metric"]["correct"],
                "top1": data["metric"]["top1"],
                "macro": data["macro"],
            }
            for name, data in scores.items()
        },
        "correct": prediction["correct"],
        "positions": len(held),
        "by_game": by_game,
        "prediction_trace_sha256": sha256(" ".join(prediction["_predicted_moves"]).encode()).hexdigest(),
    }


def _round_robin_sample(rows: list[dict], cap: int, *, seed: int,
                        max_games: int | None = None, max_positions_per_game: int = 8) -> list[dict]:
    if cap < 1:
        raise ValueError("Sample cap must be positive")
    unique = {}
    for row in rows:
        unique.setdefault(position_key(row), row)
    groups = defaultdict(list)
    for row in unique.values():
        groups[row["game"]].append(row)
    gids = sorted(groups)
    rng = random.Random(seed)
    rng.shuffle(gids)
    if max_games is not None:
        gids = gids[:max_games]
    queues = {}
    for gid in gids:
        vals = list(groups[gid])
        rng.shuffle(vals)
        queues[gid] = vals
    taken = Counter()
    result = []
    while len(result) < cap:
        progressed = False
        for gid in gids:
            if len(result) >= cap:
                break
            if taken[gid] >= max_positions_per_game:
                continue
            if queues[gid]:
                result.append(queues[gid].pop(0))
                taken[gid] += 1
                progressed = True
        if not progressed:
            break
    return result


def _mix_sample(peak_rows: list[dict], outside_rows: list[dict], cap: int, *, peak_fraction: float,
                seed: int, max_games: int, max_positions_per_game: int) -> tuple[list[dict], dict]:
    """Return constrained training sample: 0 means whole career, not only outside-peak.

    For intermediate fractions, enforce two separate era quotas. If the quotas
    cannot be met, fail closed instead of silently violating game diversity caps.
    """
    if not (0.0 <= peak_fraction <= 1.0):
        raise ValueError("peak_fraction must be within [0,1]")
    if max_games < 1 or max_positions_per_game < 1:
        raise ValueError("max_games and max_positions_per_game must be positive")
    if cap > max_games * max_positions_per_game:
        raise ValueError('Training budget exceeds configured game×position capacity')

    if peak_fraction == 0.0:
        # The full-career baseline must draw from the entire eligible timeline.
        result = _round_robin_sample(peak_rows + outside_rows, cap, seed=seed + 19,
                                     max_games=max_games, max_positions_per_game=max_positions_per_game)
    elif peak_fraction == 1.0:
        result = _round_robin_sample(peak_rows, cap, seed=seed + 11,
                                     max_games=max_games, max_positions_per_game=max_positions_per_game)
    else:
        peak_cap = round(cap * peak_fraction)
        out_cap = cap - peak_cap
        peak_game_cap = round(max_games * peak_fraction)
        out_game_cap = max_games - peak_game_cap
        if min(peak_game_cap, out_game_cap) < 1:
            raise ValueError('Game cap is too small to represent both temporal cohorts')
        peak_sample = _round_robin_sample(peak_rows, peak_cap, seed=seed + 11,
                                          max_games=peak_game_cap, max_positions_per_game=max_positions_per_game)
        out_sample = _round_robin_sample(outside_rows, out_cap, seed=seed + 19,
                                         max_games=out_game_cap, max_positions_per_game=max_positions_per_game)
        result = peak_sample + out_sample

    if len(result) != cap:
        raise ValueError(f'Insufficient eligible games for constrained sample: {len(result)} of {cap}')
    actual_games = Counter(row['game'] for row in result)
    if len(actual_games) > max_games or max(actual_games.values()) > max_positions_per_game:
        raise AssertionError('Game-diversity invariant violated')
    peak_ids = {row['game'] for row in peak_rows}
    actual_peak = sum(row['game'] in peak_ids for row in result)
    meta = {
        "requested_cap": cap,
        "sampled_positions": len(result),
        "peak_fraction_requested": peak_fraction,
        "actual_peak_fraction": actual_peak / cap,
        "peak_positions": actual_peak,
        "outside_positions": cap - actual_peak,
        "distinct_games": len(actual_games),
        "max_games": max_games,
        "max_positions_per_game": max_positions_per_game,
        "career_arm_includes_peak_games": peak_fraction == 0.0,
    }
    return result, meta


def _arm_key(value: float) -> str:
    if value == 1.0:
        return "peak"
    if value == 0.0:
        return "career"
    return f"mix_{int(round(value * 100)):02d}"


def controlled_temporal_compare(root: Path, pgn: Path, peak_registry: Path, output: Path, *,
                                slug: str, match_name: str, v5_report: Path, legacy_v3: Path,
                                legacy_pgn: Path, allow_provisional: bool = False,
                                include_unknown: bool = False, annotations: Path | None = None,
                                cohort_ledger: Path | None = None, positions: int = 120,
                                train_budget: int = 120, depth: int = 3, folds: int = 3,
                                repeats: int = 3, trials: int = 0, probe_positions: int = 15,
                                seed: int = 31415, penalty: float = 0.08, max_train_games: int = 24,
                                max_positions_per_game: int = 8,
                                peak_fractions: tuple[float, ...] = (1.0, 0.75, 0.5, 0.25, 0.0),
                                base_files: tuple[str, ...] = ('karpov.txt','botvinnik.txt','petrosian.txt','fischer.txt')) -> dict:
    if output.exists():
        raise FileExistsError(output)
    if not (2 <= folds <= 8 and 1 <= repeats <= 10 and 20 <= positions <= 5000 and 20 <= train_budget <= 10000 and 1 <= depth <= 20 and 0 <= trials <= 20 and 0 <= penalty <= 1 and max_train_games >= 1 and max_positions_per_game >= 1):
        raise ValueError("Invalid controlled temporal arguments")
    if len(set(peak_fractions)) != len(peak_fractions) or any(not 0 <= f <= 1 for f in peak_fractions):
        raise ValueError('Peak fractions must be distinct numbers between 0 and 1')
    peak = load_peak_window(peak_registry, slug, allow_provisional=allow_provisional)
    all_rows, source = _cohort_v5_training(
        extract_decisions_v5(pgn, player_name=match_name, from_year=1600, to_year=2100)[0],
        report_path=v5_report,
        legacy_v3=legacy_v3,
        legacy_pgn=legacy_pgn,
        slug=slug,
        pgn=pgn,
        registry_path=cohort_ledger,
    )
    ann = load_annotations(annotations) if annotations else {}
    mode_rows = _mode_rows(all_rows, ann, include_unknown)
    peak_rows = [r for r in mode_rows if peak['from_year'] <= r['year'] <= peak['to_year']]
    outside_rows = [r for r in mode_rows if r['year'] < peak['from_year'] or r['year'] > peak['to_year']]
    if len({r['game'] for r in peak_rows}) < folds * 2:
        raise ValueError('Insufficient peak games for controlled CV folds')
    bases = _load_bases(root, base_files)

    report = {
        'protocol': PROTOCOL,
        'status': 'PAIRED_CONTROLLED_EXPLORATORY',
        'player': slug,
        'peak': peak,
        'source': source,
        'inputs': {
            'pgn_sha256': file_hash(pgn),
            'engine_sha256': file_hash(root / 'rodent-iv-plain.exe'),
            'v5_report_sha256': file_hash(v5_report / 'calibration.json' if v5_report.is_dir() else v5_report),
            'annotations_sha256': file_hash(annotations) if annotations else None,
            'depth': depth,
            'trials': trials,
            'train_budget': train_budget,
            'positions': positions,
            'seed': seed,
            'max_train_games': max_train_games,
            'max_positions_per_game': max_positions_per_game,
            'peak_fractions': list(peak_fractions),
            'base_profiles': {name: file_hash(path) for name, (_, path) in bases.items()},
        },
        'available_training': {
            'career_games': len({r['game'] for r in mode_rows}),
            'peak_games': len({r['game'] for r in peak_rows}),
            'outside_peak_games': len({r['game'] for r in outside_rows}),
            'career_positions': len(mode_rows),
            'peak_positions': len(peak_rows),
            'outside_peak_positions': len(outside_rows),
            'include_unknown_opt_in': include_unknown,
        },
        'arms': {},
        'folds': [],
        'selection': {'status': 'NOT_PERMITTED'},
        'final_test': {'status': 'SEALED_NOT_EVALUATED'},
    }

    summary_bucket = { _arm_key(frac): [] for frac in peak_fractions }
    fold_id = 0
    for repeat in range(repeats):
        eval_sample = _round_robin_sample(peak_rows, positions, seed=seed + repeat * 101,
                                          max_games=None, max_positions_per_game=max_positions_per_game)
        repeat_folds = _folds_by_game(eval_sample, folds, seed + repeat * 131)
        for fold_index, (_dev_ignore, held) in enumerate(repeat_folds):
            held_games = {r['game'] for r in held}
            held_positions = {position_key(r) for r in held}
            train_peak_pool = [r for r in peak_rows if r['game'] not in held_games and position_key(r) not in held_positions]
            train_out_pool = [r for r in outside_rows if position_key(r) not in held_positions]
            fold_record = {
                'repeat': repeat,
                'fold': fold_index,
                'evaluation_positions': len(held),
                'evaluation_games': len({r['game'] for r in held}),
                'train_budget': train_budget,
                'arms': {},
            }
            for frac in peak_fractions:
                arm_name = _arm_key(frac)
                train_rows, sample_meta = _mix_sample(train_peak_pool, train_out_pool, train_budget,
                                                      peak_fraction=frac, seed=seed + fold_id * 97,
                                                      max_games=max_train_games,
                                                      max_positions_per_game=max_positions_per_game)
                if len(train_rows) < max(20, min(train_budget, 40)):
                    raise ValueError(f'Arm {arm_name} produced too few training rows')
                prediction = _profile_eval(root, bases, train_rows, held, depth=depth, trials=trials,
                                           probes=probe_positions, penalty=penalty, seed=seed + fold_id * 97)
                arm_doc = {
                    'base': prediction['base'],
                    'params': prediction['params'],
                    'correct': prediction['correct'],
                    'positions': prediction['positions'],
                    'train_games': len({r['game'] for r in train_rows}),
                    'train_era_counts': {
                        'peak': len([r for r in train_rows if peak['from_year'] <= r['year'] <= peak['to_year']]),
                        'outside_peak': len([r for r in train_rows if r['year'] < peak['from_year'] or r['year'] > peak['to_year']]),
                    },
                    'sample_meta': sample_meta,
                }
                fold_record['arms'][arm_name] = arm_doc
                report['folds'].append({
                    'repeat': repeat,
                    'fold': fold_index,
                    'arm': arm_name,
                    'peak_fraction': frac,
                    'evaluation_positions': len(held),
                    'evaluation_games': len({r['game'] for r in held}),
                    'train_budget': train_budget,
                    'base': arm_doc['base'],
                    'correct': arm_doc['correct'],
                    'positions': arm_doc['positions'],
                    'train_games': arm_doc['train_games'],
                    'train_era_counts': arm_doc['train_era_counts'],
                    'sample_meta': sample_meta,
                })
                summary_bucket[arm_name].append({'repeat': repeat, 'correct': prediction['correct'], 'positions': len(held)})
            fold_id += 1
    arm_summary = {}
    for arm_name, rows in summary_bucket.items():
        by_repeat = defaultdict(lambda: {'correct': 0, 'positions': 0})
        for item in rows:
            by_repeat[item['repeat']]['correct'] += item['correct']
            by_repeat[item['repeat']]['positions'] += item['positions']
        per_repeat = []
        deltas = []
        for repeat, agg in sorted(by_repeat.items()):
            accuracy = 100 * agg['correct'] / agg['positions']
            per_repeat.append({'repeat': repeat, 'positions': agg['positions'], 'correct': agg['correct'], 'accuracy_pct': accuracy})
            deltas.append(accuracy)
        arm_summary[arm_name] = {
            'per_repeat': per_repeat,
            'mean_accuracy_pct': mean(deltas),
            'std_accuracy_pct': pstdev(deltas) if len(deltas) > 1 else 0.0,
            'total_correct': sum(v['correct'] for v in per_repeat),
            'total_positions': sum(v['positions'] for v in per_repeat),
        }
    ranked = sorted(arm_summary.items(), key=lambda kv: (kv[1]['mean_accuracy_pct'], kv[1]['total_correct'], -kv[1]['std_accuracy_pct'], kv[0]), reverse=True)
    report['arms'] = arm_summary
    report['recommendation'] = {
        'best_arm': ranked[0][0],
        'mean_accuracy_pct': ranked[0][1]['mean_accuracy_pct'],
        'ranking': [{'arm': name, 'mean_accuracy_pct': data['mean_accuracy_pct'], 'std_accuracy_pct': data['std_accuracy_pct']} for name, data in ranked],
        'warning': 'Exploratory cross-validation only. Do not treat as validated historical identity.',
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def build_provisional_profile_v8(root: Path, pgn: Path, peak_registry: Path, output_dir: Path, *, slug: str,
                                 match_name: str, v5_report: Path, legacy_v3: Path, legacy_pgn: Path,
                                 allow_provisional: bool = False, include_unknown: bool = False,
                                 annotations: Path | None = None, cohort_ledger: Path | None = None,
                                 peak_fraction: float = 0.75, train_budget: int = 160, depth: int = 3,
                                 trials: int = 4, probe_positions: int = 20, seed: int = 31415,
                                 penalty: float = 0.08, max_train_games: int = 24,
                                 max_positions_per_game: int = 8,
                                 base_files: tuple[str, ...] = ('karpov.txt','botvinnik.txt','petrosian.txt','fischer.txt')) -> dict:
    if output_dir.exists():
        raise FileExistsError(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    peak = load_peak_window(peak_registry, slug, allow_provisional=allow_provisional)
    all_rows, source = _cohort_v5_training(
        extract_decisions_v5(pgn, player_name=match_name, from_year=1600, to_year=2100)[0],
        report_path=v5_report,
        legacy_v3=legacy_v3,
        legacy_pgn=legacy_pgn,
        slug=slug,
        pgn=pgn,
        registry_path=cohort_ledger,
    )
    ann = load_annotations(annotations) if annotations else {}
    mode_rows = _mode_rows(all_rows, ann, include_unknown)
    peak_rows = [r for r in mode_rows if peak['from_year'] <= r['year'] <= peak['to_year']]
    outside_rows = [r for r in mode_rows if r['year'] < peak['from_year'] or r['year'] > peak['to_year']]
    sample_rows, sample_meta = _mix_sample(peak_rows, outside_rows, train_budget, peak_fraction=peak_fraction,
                                           seed=seed, max_games=max_train_games,
                                           max_positions_per_game=max_positions_per_game)
    bases = _load_bases(root, base_files)
    origin, scores = _choose_base(bases, sample_rows, root, depth)
    baseline_text = bases[origin][0]
    params = {}
    if trials:
        _, best, _, _ = _directed_search(root, baseline_text, sample_rows, trials=trials, depth=depth,
                                         delta=20, penalty=penalty, seed=seed,
                                         probe_positions=min(probe_positions, len(sample_rows)),
                                         probe_params=DEFAULT_PROBES)
        params = dict(best['params'])
    contents = tune_text(baseline_text, params) if params else baseline_text
    profile_name = f"{slug}_v08_PROVISIONAL.txt"
    profile_path = output_dir / profile_name
    metadata = (
        f"; ChessSimulation64 v0.8 provisional profile\n"
        f"; player={slug}\n; match_name={match_name}\n; peak_fraction={peak_fraction}\n"
        f"; selected_base={origin}\n; train_budget={train_budget}\n; max_train_games={max_train_games}\n"
        f"; max_positions_per_game={max_positions_per_game}\n; trials={trials}\n"
        f"; exploratory_only=1\n; not_validated_historical_identity=1\n\n"
    )
    profile_path.write_text(metadata + contents, encoding='utf-8')
    report = {
        'protocol': PROTOCOL,
        'status': 'PROVISIONAL_PROFILE_NOT_VALIDATED',
        'player': slug,
        'match_name': match_name,
        'peak_window': peak,
        'inputs': {
            'pgn_sha256': file_hash(pgn),
            'engine_sha256': file_hash(root / 'rodent-iv-plain.exe'),
            'v5_report_sha256': file_hash(v5_report / 'calibration.json' if v5_report.is_dir() else v5_report),
            'annotations_sha256': file_hash(annotations) if annotations else None,
            'depth': depth,
            'trials': trials,
            'train_budget': train_budget,
            'seed': seed,
            'peak_fraction': peak_fraction,
            'max_train_games': max_train_games,
            'max_positions_per_game': max_positions_per_game,
            'base_profiles': {name: file_hash(path) for name, (_, path) in bases.items()},
        },
        'source': source,
        'training_sample': {
            **sample_meta,
            'games': len({r['game'] for r in sample_rows}),
            'peak_year_positions': len([r for r in sample_rows if peak['from_year'] <= r['year'] <= peak['to_year']]),
            'outside_peak_positions': len([r for r in sample_rows if r['year'] < peak['from_year'] or r['year'] > peak['to_year']]),
            'sha256_positions': sha256(json.dumps(sorted((r['game'], r['fen'], r['move']) for r in sample_rows), ensure_ascii=False).encode()).hexdigest(),
        },
        'baseline_screening': {
            name: {
                'correct': data['metric']['correct'],
                'top1': data['metric']['top1'],
                'macro': data['macro'],
            }
            for name, data in scores.items()
        },
        'selected_base': origin,
        'params': params,
        'profile_file': profile_name,
        'style_signature_reference': 'NOT_INCLUDED: full-corpus descriptive data can include legacy sealed groups; research signatures must use a separate approved training-only cohort',
        'warning': 'Playable provisional personality. It has not passed any sealed historical identity validation.',
        'selection': {'status': 'NOT_PERMITTED'},
        'final_test': {'status': 'SEALED_NOT_EVALUATED'},
    }
    (output_dir / 'provisional_profile_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


# Local import placed at end to avoid circular import during module loading.
from .corpus_v5 import extract_decisions_v5  # noqa: E402
from .peak_v6 import load_annotations  # noqa: E402
