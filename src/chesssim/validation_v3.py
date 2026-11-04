"""Frozen final-test, optional Stockfish tactical check and style fingerprints.

Explicit one-time final-eval prevents accidental peeking during tuning.
No result grants historic authenticity or an Elo rating.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean, median

from .calibration import _diagnostics, evaluate_candidate, file_hash
from .historical import extract_decisions, split_three_ways
from .research_v3 import _by_game, evaluate_text, paired_game_bootstrap


def _trace_diff(left: dict, right: dict) -> int | None:
    a = left.get("_predicted_moves")
    b = right.get("_predicted_moves")
    if a is None or b is None:
        return None
    return sum(x != y for x, y in zip(a, b)) if len(a) == len(b) else None


def stockfish_quality(positions: list[dict], baseline: dict, selected: dict,
                      executable: Path, *, depth: int = 10) -> dict:
    import chess
    import chess.engine
    if not executable.is_file():
        raise FileNotFoundError(f"Stockfish não encontrado: {executable}")
    if not 1 <= depth <= 30:
        raise ValueError("Profundidade Stockfish deve ser 1..30")
    paths = {"baseline": baseline.get("_predicted_moves"),
             "selected": selected.get("_predicted_moves")}
    if any(trace is None or len(trace) != len(positions) for trace in paths.values()):
        raise ValueError("Previsões para a análise tática não disponíveis")
    losses = {"baseline": [], "selected": []}
    with chess.engine.SimpleEngine.popen_uci(str(executable), timeout=90) as sf:
        for idx, d in enumerate(positions):
            board = chess.Board(d["fen"])
            pov = board.turn
            best = sf.analyse(board, chess.engine.Limit(depth=depth), info=chess.engine.INFO_SCORE)
            best_cp = best["score"].pov(pov).score(mate_score=100000)
            for name, trace in paths.items():
                move = chess.Move.from_uci(trace[idx])
                if move not in board.legal_moves:
                    raise ValueError(f"{name} move is illegal: {trace[idx]}")
                restricted = sf.analyse(board, chess.engine.Limit(depth=depth), root_moves=[move],
                                        info=chess.engine.INFO_SCORE)
                cp = restricted["score"].pov(pov).score(mate_score=100000)
                losses[name].append(max(0, best_cp - cp))
    def summary(values):
        return {"positions": len(values), "mean_centipawn_loss": mean(values),
                "median_centipawn_loss": median(values),
                "blunders_ge_200cp": sum(v >= 200 for v in values),
                "errors_ge_100cp": sum(v >= 100 for v in values)}
    return {"status": "EXTERNAL_ENGINE_DIAGNOSTIC", "stockfish_sha256": file_hash(executable),
            "depth": depth, "baseline": summary(losses["baseline"]),
            "selected": summary(losses["selected"]),
            "note": "Comparação de duas buscas em cada posição; não é Elo nem prova de segurança tática"}


def final_evaluate(root: Path, run: Path, *, stockfish: Path | None = None,
                   stockfish_depth: int = 10,
                   comparisons: list[str] | None = None) -> dict:
    """Final test runs ONCE per saved calibration; never update fitted params."""
    run = run.resolve()
    report_path = run / "calibration.json"
    destination = run / "final_test.json"
    if destination.exists():
        raise FileExistsError(f"Teste final já existe: {destination}. Não abrir novamente.")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("protocol", {}).get("version") != "rodent-style-calibration-v3":
        raise ValueError("final-eval exige calibration.json v0.3")
    if report.get("final_test", {}).get("status") != "SEALED_NOT_EVALUATED":
        raise ValueError("Teste final já aberto ou não está lacrado")
    args = report["input"]
    pgn = Path(args["source_pgn"])
    base_path = root / "personalities" / args["base_file"]
    chosen_path = Path(report["selected_profile"])
    if not chosen_path.is_absolute():
        chosen_path = chosen_path.resolve()
    # Check all optional dependencies and comparison profiles BEFORE unsealing.
    if stockfish is not None and not stockfish.is_file():
        raise FileNotFoundError(f"Stockfish não encontrado: {stockfish}")
    if comparisons and len(comparisons) > 6:
        raise ValueError("Limite: seis personalidades comparativas")
    for filename in comparisons or []:
        compare_path = root / "personalities" / filename
        if compare_path.resolve().parent != (root / "personalities").resolve() or not compare_path.is_file():
            raise ValueError(f"Personalidade comparativa inválida: {filename}")
    if file_hash(pgn) != args["pgn_sha256"]:
        raise ValueError("PGN modificado após a calibração: hash difere")
    if file_hash(base_path) != args["base_sha256"]:
        raise ValueError("Personalidade-base modificada após a calibração")
    if file_hash(root / "rodent-iv-plain.exe") != args["engine_sha256"]:
        raise ValueError("Executável Rodent diferente da calibração")
    if file_hash(chosen_path) != report["selected_profile_sha256"]:
        raise ValueError("Candidato modificado após seleção")
    data, source = extract_decisions(pgn, player_name=report["match_name"],
                                     from_year=args["from_year"], to_year=args["to_year"])
    if source["pgn_sha256"] != args["pgn_sha256"]:
        raise ValueError("Fonte histórica mudou")
    _, _, final_test, split = split_three_ways(data, seed=args["seed"],
                                                train_limit=args["train_positions"],
                                                val_limit=args["val_positions"],
                                                test_limit=args["test_positions"],
                                                group_by=args["split_by"])
    if split["test_sealed"]["manifest_sha256"] != report["final_test"]["manifest_sha256"]:
        raise ValueError("Partição lacrada mudou; teste cancelado")
    original = base_path.read_text(encoding="utf-8-sig")
    candidate = chosen_path.read_text(encoding="utf-8-sig")
    baseline_raw = evaluate_text(root, original, final_test, args["depth"])
    if report["selection"]["selected_trial"] == 0:
        selected_raw = baseline_raw
    else:
        selected_raw = evaluate_text(root, candidate, final_test, args["depth"])
    result = {
        "status": "FINAL_TEST_EVALUATED_ONCE_NOT_VALIDATED",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_calibration_sha256": file_hash(report_path),
        "test_manifest_sha256": split["test_sealed"]["manifest_sha256"],
        "positions": len(final_test),
        "baseline": _diagnostics(baseline_raw),
        "selected": _diagnostics(selected_raw, baseline_raw),
        "gain": selected_raw["top1"] - baseline_raw["top1"],
        "baseline_by_game": _by_game(baseline_raw, final_test),
        "selected_by_game": _by_game(selected_raw, final_test),
        "paired_game_bootstrap": paired_game_bootstrap(baseline_raw, selected_raw, final_test,
                                                      seed=args["seed"] + 999),
        "stockfish_quality": "NOT_RUN (use --stockfish PATH)" if stockfish is None else None,
        "style_comparison": [],
        "note": "Test was sealed through training and validation selection. Repetition on same PGN/seed will invalidate its finality."
    }
    if stockfish is not None:
        result["stockfish_quality"] = stockfish_quality(final_test, baseline_raw, selected_raw,
                                                        stockfish, depth=stockfish_depth)
    if comparisons:
        if len(comparisons) > 6:
            raise ValueError("Limite: seis personalidades comparativas")
        for filename in comparisons:
            path = root / "personalities" / filename
            if path.resolve().parent != (root / "personalities").resolve() or not path.is_file():
                raise ValueError(f"Personalidade comparativa inválida: {filename}")
            other = evaluate_text(root, path.read_text(encoding="utf-8-sig"), final_test,
                                  args["depth"])
            result["style_comparison"].append({
                "personality": filename, "sha256": file_hash(path),
                "historical_top1": other["top1"],
                "disagreement_vs_selected": _trace_diff(selected_raw, other),
                "disagreement_vs_baseline": _trace_diff(baseline_raw, other)})
    # Exclusive creation; no overwrite of final result. No PGN or preferences changed.
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result
