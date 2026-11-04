"""Otimização experimental baseada em partidas de treino com holdout intacto.

Acurácia top-1 não equivale a Elo ou fidelidade histórica. Nunca publica perfil
como validado automaticamente. Usar sem livro e com parâmetros idênticos.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import random
import re
from datetime import datetime, timezone

from .historical import extract_decisions, split_positions

from .protocol import CALIBRATION
from .uci_raw import RodentUci


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()

PARAM_RANGES = {
    "OwnAttack": (75, 230),
    "OppAttack": (70, 160),
    "OwnMobility": (60, 160),
    "OppMobility": (60, 160),
    "PawnStructure": (65, 190),
    "PassedPawns": (70, 190),
    "KingTropism": (0, 110),
    "PiecePressure": (60, 210),
    "PawnShield": (75, 175),
    "PawnStorm": (60, 190),
    "Contempt": (-20, 45),
    "PrimaryPstWeight": (20, 110),
    "SecondaryPstWeight": (20, 100),
}


def tune_text(base: str, params: dict[str, int]) -> str:
    """Altera somente opções reconhecidas (ou insere após comentários)."""
    text = base
    for key, value in params.items():
        if key not in PARAM_RANGES:
            raise ValueError(f"Parâmetro desconhecido: {key}")
        lo, hi = PARAM_RANGES[key]
        if not lo <= value <= hi:
            raise ValueError(f"Fora do intervalo para {key}: {value}")
        rx = re.compile(rf"(?m)^setoption name {re.escape(key)} value -?\d+\s*$")
        text, count = rx.subn(f"setoption name {key} value {value}", text)
        if count > 1:
            raise ValueError(f"Múltiplos parâmetros ativos encontrados: {key}")
        if count == 0:
            text += f"\nsetoption name {key} value {value}\n"
    return text


def extract_values(text: str) -> dict[str, int]:
    vals = {}
    for key in PARAM_RANGES:
        found = re.search(rf"(?m)^setoption name {re.escape(key)} value (-?\d+)\s*$", text)
        if found:
            vals[key] = int(found.group(1))
    return vals


def validate_performance(scores: list[dict]) -> dict:
    total = len(scores)
    correct = sum(int(s["match"]) for s in scores)
    return {"decisions": total, "correct": correct, "top1": correct/total if total else None,
            "white": sum(s["color"] == "white" for s in scores),
            "black": sum(s["color"] == "black" for s in scores)}


def evaluate_candidate(root: Path, profile_rel: str, positions: list[dict], depth: int,
                       timeout: float = 45) -> dict:
    import chess
    exe = root / "rodent-iv-plain.exe"
    scores = []
    with RodentUci(exe, root, profile=profile_rel, book=False, timeout=timeout) as engine:
        for d in positions:
            board = chess.Board(d["fen"])
            best, _ = engine.bestmove(board.fen(), depth)
            try:
                move = chess.Move.from_uci(best)
            except ValueError as exc:
                raise RuntimeError(f"UCI inválido: {best}") from exc
            if move not in board.legal_moves:
                raise RuntimeError(f"Lance ilegal do Rodent: {best} em {d['fen']}")
            scores.append({"match": best == d["move"], "color": d["color"], "game": d["game"],
                           "predicted_move": best})
    metrics = validate_performance(scores)
    # Predictions are internal diagnostics. They are not PGN training targets
    # and are omitted from the public report after comparisons are made.
    metrics["_predicted_moves"] = [s["predicted_move"] for s in scores]
    return metrics


def _fitness(metric: dict):
    return metric["top1"] if metric["top1"] is not None else -1


def _diagnostics(metric: dict, reference: dict | None = None) -> dict:
    """Compact diagnostics; does not leak/store individual predictions."""
    result = {k: v for k, v in metric.items() if not k.startswith("_")}
    trace = metric.get("_predicted_moves")
    ref_trace = reference.get("_predicted_moves") if reference else None
    if trace is not None:
        result["move_trace_sha256"] = sha256(" ".join(trace).encode()).hexdigest()
    if trace is not None and ref_trace is not None:
        if len(trace) != len(ref_trace):
            raise ValueError("Comparação de previsões desalinhadas")
        changes = sum(a != b for a, b in zip(trace, ref_trace))
        result["moves_changed_vs_baseline"] = changes
        result["move_change_ratio"] = changes / len(trace) if trace else 0.0
    return result


def _remove_inherited_opening_books(profile_text: str) -> str:
    """Do not ship a new persona that silently inherits Karpov's repertoire."""
    pattern = re.compile(r"(?m)^setoption name (?:GuideBookFile|MainBookFile) value .*\r?\n?")
    return pattern.sub("", profile_text)


def calibrate_personality(root: Path, pgn: Path, output: Path, *, target: str,
                      target_slug: str, match_name: str, base_file: str = "karpov.txt",
                      depth: int = 5,
                      trials: int = 24, train_positions: int = 60,
                      val_positions: int = 30, seed: int = 2026,
                      min_holdout_games: int = 5, from_year: int = 1973, to_year: int = 1978):
    if from_year > to_year:
        raise ValueError("Intervalo histórico inválido")
    if depth < 1 or depth > 20:
        raise ValueError("depth deve estar entre 1 e 20")
    if not (1 <= trials <= 1000):
        raise ValueError("trials deve estar entre 1 e 1000")
    root = root.resolve()
    if not (root / "rodent-iv-plain.exe").is_file():
        raise FileNotFoundError(root / "rodent-iv-plain.exe")
    base_path = root / "personalities" / base_file
    if base_path.resolve().parent != (root / "personalities").resolve():
        raise ValueError("base_file deve ser um arquivo direto da pasta personalities")
    if not base_path.exists():
        raise FileNotFoundError(base_path)
    if output.exists():
        raise FileExistsError(f"Diretório de saída já existe: {output}")
    decisions, source_stats = extract_decisions(pgn, player_name=match_name,
                                                 from_year=from_year, to_year=to_year)
    train, val, split = split_positions(decisions, seed=seed,
                                        train_limit=train_positions, val_limit=val_positions)
    if split["val_games"] < min_holdout_games:
        raise ValueError(f"Holdout pequeno: {split['val_games']} jogos; esperado >= {min_holdout_games}.")
    source_text = base_path.read_text(encoding="utf-8-sig")
    basis = extract_values(source_text)
    # Candidate baseline Karpov is an initialization, NOT a claim of Mecking fidelity.
    output.mkdir(parents=True, exist_ok=False)
    candidates = root / "personalities/custom"
    candidates.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    history = []
    best, best_values, best_score = None, {}, -1
    baseline_training = None

    run_id = sha256((str(output.resolve()) + str(seed)).encode()).hexdigest()[:12]
    temp_file = candidates / f"chesssim-calibrate-{run_id}.txt"
    if temp_file.exists():
        raise FileExistsError(temp_file)
    try:
        for i in range(trials + 1):
            if i == 0:
                params = {}
            else:
                origin = best_values if i % 3 == 0 and best_values else basis
                params = {k: v for k, v in origin.items() if k in PARAM_RANGES}
                keys = rng.sample(list(PARAM_RANGES), k=rng.randint(1, min(3, len(PARAM_RANGES))))
                for key in keys:
                    lo, hi = PARAM_RANGES[key]
                    current = params.get(key, (lo+hi)//2)
                    params[key] = max(lo, min(hi, current + rng.choice((-1,1))*rng.randint(5,25)))
            txt = tune_text(source_text, params)
            temp_file.write_text(txt, encoding="utf-8")
            stats = evaluate_candidate(root, "custom/" + temp_file.name, train, depth)
            if i == 0:
                baseline_training = stats
            row = {"trial": i, "params": params,
                   "training": _diagnostics(stats, baseline_training)}
            history.append(row)
            if _fitness(stats) > best_score:
                best, best_values, best_score = txt, params, _fitness(stats)
            print(f"Tentativa {i:03}/{trials}: acerto treino {stats['correct']}/{stats['decisions']} ({_fitness(stats):.1%})", flush=True)
        if best is None:
            raise RuntimeError("Nenhum candidato foi avaliado")
        temp_file.write_text(source_text, encoding="utf-8")
        baseline_raw = evaluate_candidate(root, "custom/" + temp_file.name, val, depth)
        temp_file.write_text(best, encoding="utf-8")
        candidate_raw = evaluate_candidate(root, "custom/" + temp_file.name, val, depth)
        baseline = _diagnostics(baseline_raw)
        candidate = _diagnostics(candidate_raw, baseline_raw)
    finally:
        temp_file.unlink(missing_ok=True)

    # NÃO promover automaticamente. Só relatório experimental e TXT candidato.
    unchanged = best == source_text
    status = "NO_TRAIN_IMPROVEMENT_BASELINE_RETAINED" if unchanged else "EXPERIMENTAL_NOT_VALIDATED"
    profile = output / (f"{target_slug}_BASELINE_NAO_PERSONALIZADO.txt" if unchanged
                        else f"{target_slug}_EXPERIMENTAL_NAO_VALIDADO.txt")
    profile.write_text(f"; {target} - {status}; NOT historically validated\n"
                       f"; Original model initialized from Rodent {base_file}\n"
                       "; No opening books inherited from source personality.\n"
                       "; Do not register this as a finished historical profile.\n"
                       + _remove_inherited_opening_books(best), encoding="utf-8")
    training_gain = best_score - _fitness(baseline_training)
    observed_changes = [r["training"].get("moves_changed_vs_baseline") for r in history[1:]]
    report = {"status": status, "protocol": CALIBRATION,
              "profile_is_baseline_copy": unchanged,
              "training_gain": training_gain,
              "trials_with_changed_moves": sum(n > 0 for n in observed_changes if n is not None),
              "trials_with_identical_moves": sum(n == 0 for n in observed_changes if n is not None),
              "max_moves_changed_vs_baseline": max((n for n in observed_changes if n is not None), default=None),
              "sampled_validation_games": len({d["game"] for d in val}),
              "target": target, "date_utc": datetime.now(timezone.utc).isoformat(),
              "source_stats": source_stats, "split": split, "trials": len(history), "match_name": match_name,
              "depth": depth, "base": str(base_path), "base_sha256": file_hash(base_path),
              "engine_sha256": file_hash(root / "rodent-iv-plain.exe"),
              "pgn_path": str(pgn), "baseline_holdout": baseline,
              "candidate_holdout": candidate,
              "holdout_gain": candidate["top1"] - baseline["top1"],
              "candidate": str(profile), "candidate_sha256": file_hash(profile),
              "warnings": ["Acurácia top-1 NÃO é Elo nem calibração conclusiva de estilo.",
                           *(["Não houve melhora no treino; o vencedor é o Karpov original, NÃO um Mequinho calibrado."] if unchanged else []),
                           "A calibração não transfere o livro de aberturas de Karpov ao perfil candidato.",
                           "O alvo é um estilo em posições históricas, não força/segurança tática.",
                           "Validar com Stockfish e confrontos independentes antes de promover.",
                           "A busca de parâmetros pode ter sobreajuste mesmo com holdout por partida."],
              "history": history}
    (output / "calibration.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report
