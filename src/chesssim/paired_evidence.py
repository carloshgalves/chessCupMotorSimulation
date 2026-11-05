"""Paired chess evidence: outcome, strength, style and exact-position choice.

The independent unit is a *distinct PGN game*, never an engine decision.
This is descriptive research evidence, not an identity classifier or Elo model.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
from html import escape
from itertools import combinations
import json
import math
from pathlib import Path
import random
from statistics import mean, median

from .game_diversity import mainline_identity
from .style_postgame import FEATURES

SCHEMA = "chesssim-paired-style-strength-v1"
PHASES = ("opening", "middlegame_plus")


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rate(values: list[float], *, bootstrap: int, seed: int) -> dict:
    """Cluster bootstrap on games, not individual moves or positions."""
    n = len(values)
    if not n:
        return {"games": 0, "mean": None, "median": None,
                "min": None, "max": None, "bootstrap_ci95": None,
                "uncertainty": "NO_GAME_LEVEL_OBSERVATIONS"}
    summary = {"games": n, "mean": mean(values), "median": median(values),
               "min": min(values), "max": max(values),
               "bootstrap_ci95": None, "uncertainty": "SMALL_GAME_SAMPLE" if n < 5
               else "DESCRIPTIVE_GAME_CLUSTER_BOOTSTRAP"}
    if n >= 5:
        rng = random.Random(seed)
        samples = sorted(mean(values[rng.randrange(n)] for _ in range(n))
                         for _ in range(bootstrap))
        summary["bootstrap_ci95"] = [
            samples[int((bootstrap - 1) * .025)],
            samples[int((bootstrap - 1) * .975)],
        ]
    return summary


def _seed(base: int, *parts: str) -> int:
    digest = sha256("|".join(parts).encode("utf-8")).digest()
    return (base + int.from_bytes(digest[:8], "big")) % (1 << 64)


def _feature_flags(board, move) -> dict:
    import chess
    piece = board.piece_at(move.from_square)
    piece_map = {
        chess.PAWN: "pawn_move", chess.QUEEN: "queen_move",
        chess.KING: "king_move", chess.KNIGHT: "knight_move",
        chess.BISHOP: "bishop_move", chess.ROOK: "rook_move",
    }
    flags = {
        "capture": board.is_capture(move),
        "check": board.gives_check(move),
        "castle": board.is_castling(move),
        "promotion": bool(move.promotion),
    }
    if piece:
        flags[piece_map[piece.piece_type]] = True
    return flags


def _read_games(pgn: Path, *, opening_plies: int) -> tuple[list[dict], list[dict]]:
    import chess.pgn

    all_games = []
    seen = set()
    for index, game in enumerate(_parse_games(pgn)):
        signature, plies = mainline_identity(game)
        duplicate = plies > 0 and signature in seen
        if plies:
            seen.add(signature)
        white, black = game.headers.get("White", "?"), game.headers.get("Black", "?")
        if not white.strip() or not black.strip() or white == black:
            raise ValueError(f"Invalid player identities in game {index + 1}")
        result = game.headers.get("Result", "*")
        if result not in ("1-0", "0-1", "1/2-1/2", "*"):
            raise ValueError(f"Invalid outcome in game {index + 1}")
        all_games.append({
            "source_game_index": index, "white": white, "black": black,
            "result": result, "mainline_sha256": signature,
            "plies": plies, "duplicate_mainline": duplicate,
            "eligible": plies > 0 and not duplicate,
        })
        if duplicate or not plies:
            continue
        board = game.board()
        game_features = {
            white: {p: Counter() for p in PHASES},
            black: {p: Counter() for p in PHASES},
        }
        moves = []
        for ply, move in enumerate(game.mainline_moves(), 1):
            player = white if board.turn else black
            color = "white" if board.turn else "black"
            phase = "opening" if ply <= opening_plies else "middlegame_plus"
            bucket = game_features[player][phase]
            bucket["moves"] += 1
            for name, active in _feature_flags(board, move).items():
                if active:
                    bucket[name] += 1
            moves.append({
                "ply": ply, "player": player, "color": color,
                "fen": board.fen(), "played": move.uci(),
            })
            board.push(move)
        all_games[-1]["_moves"] = moves
        all_games[-1]["_features"] = {
            name: {
                phase: {
                    "moves": slots[phase]["moves"],
                    "rates": {
                        feature: slots[phase][feature] / slots[phase]["moves"]
                        if slots[phase]["moves"] else None
                        for feature in FEATURES
                    },
                }
                for phase in PHASES
            }
            for name, slots in game_features.items()
        }
    return all_games, [g for g in all_games if g["eligible"]]


def _parse_games(path: Path):
    import chess.pgn
    with path.open(encoding="utf-8-sig", errors="replace") as stream:
        while (game := chess.pgn.read_game(stream)) is not None:
            if game.errors:
                raise ValueError("Illegal or malformed PGN; paired evidence refused")
            yield game


def _strength(quality: dict, games: list[dict]) -> tuple[dict, dict]:
    inputs = quality.get("inputs")
    rows = quality.get("decisions")
    if not isinstance(inputs, dict) or not isinstance(rows, list):
        raise ValueError("Quality JSON missing decisions and inputs")
    if not isinstance(inputs.get("depth"), int) or type(inputs["depth"]) is not int:
        raise ValueError("Quality JSON has invalid depth")
    meta = {g["source_game_index"]: g for g in games}
    positions = {
        (g["source_game_index"], move["ply"]): move
        for g in games if g["eligible"] for move in g["_moves"]
    }
    per_game = defaultdict(lambda: defaultdict(list))
    all_counts = Counter()
    used = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid quality decision")
        gi = row.get("source_game_index", row.get("game_index"))
        ply = row.get("ply")
        if type(gi) is not int or type(ply) is not int:
            raise ValueError("Quality decisions require original game and ply")
        game = meta.get(gi)
        if game is None:
            raise ValueError("Quality references a non-existent PGN game")
        if row.get("depth") != inputs["depth"]:
            raise ValueError("Quality row depth differs from report depth")
        match = positions.get((gi, ply))
        if match is None:
            # Explicitly allow diagnostics of duplicated trajectories, but
            # never include them as independent strength evidence.
            if game["duplicate_mainline"]:
                all_counts["duplicate_quality_decisions_excluded"] += 1
                continue
            raise ValueError("Quality decision does not match an eligible PGN move")
        if (row.get("player") != match["player"] or
                row.get("color") != match["color"] or
                row.get("fen") != match["fen"] or
                row.get("played") != match["played"]):
            raise ValueError("Quality decision provenance disagrees with PGN")
        if (gi, ply) in used:
            raise ValueError("Quality report repeats a game/ply decision")
        used.add((gi, ply))
        value = row.get("cp_loss")
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("Quality report has invalid centipawn loss")
        all_counts["independent_quality_decisions"] += 1
        if row.get("mate_affected"):
            all_counts["mate_affected_excluded_from_cp_mean"] += 1
        else:
            per_game[gi][match["player"]].append(value)
    return per_game, dict(all_counts)


def _game_result(game: dict, player: str) -> str | None:
    result = game["result"]
    if result == "*":
        return None
    if result == "1/2-1/2":
        return "draw"
    won_white = result == "1-0"
    return "win" if (game["white"] == player) == won_white else "loss"


def _phase_diff(games: list[dict], a: str, b: str, phase: str,
                *, feature: str, bootstrap: int, seed: int) -> dict:
    diffs = []
    for game in games:
        fa = game["_features"][a][phase]["rates"][feature]
        fb = game["_features"][b][phase]["rates"][feature]
        if fa is not None and fb is not None:
            diffs.append(fa - fb)
    result = _rate(diffs, bootstrap=bootstrap, seed=seed)
    result["feature"] = feature
    result["unit"] = "within_game_rate_difference_a_minus_b"
    return result


def _paired_positions(games: list[dict], a: str, b: str) -> dict:
    # A shared full FEN includes turn, castling, en-passant and counters:
    # no conjectural equivalence between different chess situations.
    choices = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
    for g in games:
        prior = set()
        for move in g["_moves"]:
            if move["player"] not in (a, b):
                continue
            key = (move["player"], move["fen"])
            if key in prior:
                continue
            prior.add(key)
            choices[move["fen"]][move["player"]][move["played"]].add(g["source_game_index"])
    matched = []
    for fen, both in sorted(choices.items()):
        if a not in both or b not in both:
            continue
        options_a = set(both[a])
        options_b = set(both[b])
        matched.append({
            "fen": fen,
            "a_choices": {m: len(ids) for m, ids in sorted(both[a].items())},
            "b_choices": {m: len(ids) for m, ids in sorted(both[b].items())},
            "any_shared_move": bool(options_a & options_b),
            "any_different_move": bool(options_a ^ options_b),
            "a_distinct_games": len(set().union(*both[a].values())),
            "b_distinct_games": len(set().union(*both[b].values())),
        })
    return {
        "exact_fen_positions": len(matched),
        "positions_with_any_common_choice": sum(x["any_shared_move"] for x in matched),
        "positions_with_any_different_choice": sum(x["any_different_move"] for x in matched),
        "examples": matched[:10],
        "unit": "descriptive_exact_FEN_positions_not_independent_games",
    }


def paired_evidence(pgn: Path, quality_json: Path, out: Path, *,
                    html: Path | None = None, opening_plies: int = 16,
                    bootstrap: int = 1000, seed: int = 2026) -> dict:
    if not 0 <= opening_plies <= 40:
        raise ValueError("opening_plies outside 0..40")
    if type(bootstrap) is not int or not 200 <= bootstrap <= 5000:
        raise ValueError("bootstrap must be an integer between 200 and 5000")
    if type(seed) is not int or seed < 0 or seed > 2_147_483_647:
        raise ValueError("seed must be a non-negative 32-bit integer")
    if out.exists() or (html is not None and html.exists()):
        raise FileExistsError("Refusing to overwrite a paired-evidence report")
    if html is not None and out.resolve() == html.resolve():
        raise ValueError("JSON and HTML outputs must be different paths")
    if not pgn.is_file() or not quality_json.is_file():
        raise FileNotFoundError("PGN and Stockfish-quality JSON are required")
    pgn_hash = _file_hash(pgn)
    quality_hash = _file_hash(quality_json)
    quality = json.loads(quality_json.read_text(encoding="utf-8"))
    if not isinstance(quality, dict) or not isinstance(quality.get("inputs"), dict):
        raise ValueError("Malformed quality JSON")
    if quality["inputs"].get("pgn_sha256") != pgn_hash:
        raise ValueError("Quality JSON is not derived from this exact PGN")
    all_games, eligible = _read_games(pgn, opening_plies=opening_plies)
    if not eligible:
        raise ValueError("No distinct nonempty games in PGN")
    strength, strength_counts = _strength(quality, all_games)
    players = sorted(set(name for g in eligible for name in (g["white"], g["black"])))
    player_reports = {}
    for player in players:
        owned = [g for g in eligible if player in (g["white"], g["black"])]
        result_counts = Counter(_game_result(g, player) for g in owned)
        means = [mean(strength[g["source_game_index"]][player])
                 for g in owned if strength[g["source_game_index"]][player]]
        per_phase = {}
        for phase in PHASES:
            per_phase[phase] = {
                "games_with_moves": sum(g["_features"][player][phase]["moves"] > 0
                                        for g in owned),
                "features": {
                    feature: _rate([
                        g["_features"][player][phase]["rates"][feature]
                        for g in owned if g["_features"][player][phase]["moves"] > 0
                    ], bootstrap=bootstrap, seed=_seed(seed, player, phase, feature))
                    for feature in FEATURES
                },
            }
        player_reports[player] = {
            "distinct_games": len(owned),
            "completed_outcomes": {k: result_counts[k] for k in ("win", "draw", "loss")},
            "incomplete_results_excluded": result_counts[None],
            "strength": {
                "unit": "game_mean_cp_loss_non_mate_sampled_positions",
                "game_level": _rate(means, bootstrap=bootstrap,
                                    seed=_seed(seed, player, "strength")),
                "decisions_non_mate": sum(
                    len(strength[g["source_game_index"]][player]) for g in owned
                ),
                "warning": "CP values use sampled, potentially non-equivalent chess positions; not Elo.",
            },
            "style": per_phase,
        }
    groups = defaultdict(list)
    for g in eligible:
        groups[tuple(sorted((g["white"], g["black"])))].append(g)
    pairs = []
    for (a, b), games in sorted(groups.items()):
        completed = [g for g in games if g["result"] != "*"]
        diff_strength = []
        game_records = []
        for g in games:
            index = g["source_game_index"]
            aa = strength[index][a]
            bb = strength[index][b]
            difference = mean(aa) - mean(bb) if aa and bb else None
            if difference is not None:
                diff_strength.append(difference)
            game_records.append({
                "source_game_index": index, "mainline_sha256": g["mainline_sha256"],
                "result": g["result"], "white": g["white"], "black": g["black"],
                "completed": g["result"] != "*",
                "strength_game_mean_cp_loss": {
                    a: mean(aa) if aa else None,
                    b: mean(bb) if bb else None,
                },
                "paired_cp_difference": difference,
            })
        style = {
            phase: {
                feature: _phase_diff(
                    games, a, b, phase, feature=feature,
                    bootstrap=bootstrap, seed=_seed(seed, a, b, phase, feature)
                )
                for feature in FEATURES
            }
            for phase in PHASES
        }
        pairs.append({
            "a": a, "b": b, "distinct_head_to_head_games": len(games),
            "completed_head_to_head_games": len(completed),
            "incomplete_head_to_head_games": len(games) - len(completed),
            "outcomes_a": {key: sum(_game_result(g, a) == key for g in completed)
                           for key in ("win", "draw", "loss")},
            "strength_paired_by_game": {
                **_rate(diff_strength, bootstrap=bootstrap,
                        seed=_seed(seed, a, b, "paired_strength")),
                "unit": "within_game_mean_CP_loss_a_minus_b",
                "note": "Non-mate sampled CP only. Both players must have analyzed decisions in the game.",
            },
            "phase_style_differences": style,
            "same_position_choices": _paired_positions(games, a, b),
            "game_records": game_records,
            "warning": (
                "Game-clustered descriptive comparisons only; "
                "shared board positions are not independent trials; "
                "different choices do not prove historical identity."
            ),
        })
    report = {
        "schema": SCHEMA,
        "status": "DESCRIPTIVE_PAIRED_EVIDENCE_NOT_ELO_OR_STYLE_IDENTITY",
        "inputs": {
            "pgn_sha256": pgn_hash,
            "quality_json_sha256": quality_hash,
            "stockfish_sha256": quality["inputs"].get("stockfish_sha256"),
            "quality_depth": quality["inputs"].get("depth"),
            "opening_plies": opening_plies, "bootstrap_iterations": bootstrap,
            "bootstrap_seed": seed,
        },
        "accounting": {
            "raw_games": len(all_games),
            "distinct_nonempty_games": len(eligible),
            "duplicate_games_excluded": sum(g["duplicate_mainline"] for g in all_games),
            "empty_games_excluded": sum(g["plies"] == 0 for g in all_games),
            "completed_distinct_games": sum(g["result"] != "*" for g in eligible),
            "incomplete_distinct_games": sum(g["result"] == "*" for g in eligible),
            **strength_counts,
            "unit_of_independent_evidence": "distinct_game_trajectory_cluster",
        },
        "players": player_reports, "pairwise": pairs,
        "warnings": [
            "Strength: postgame Stockfish centipawn loss on sampled positions; no Elo estimate.",
            "Style: descriptive move-feature rates per phase; no historical identity or fidelity classifier.",
            "Outcomes: only completed PGN results; games with '*' are excluded from wins/draws/losses.",
            "Game, not move, is the bootstrap resampling cluster; two colors and different openings can confound comparisons.",
            "Exact-FEN move choices are descriptive and correlated within games; they do not increase independent game N.",
            "When fewer than five distinct games contribute, no bootstrap confidence interval is reported.",
            "Mate-affected centipawn mappings are excluded from mean strength estimates and reported separately.",
        ],
    }
    if (_file_hash(pgn) != pgn_hash or _file_hash(quality_json) != quality_hash):
        raise ValueError("PGN or quality analysis changed during evidence pairing")
    rendered = _render_html(report) if html is not None else None
    out.parent.mkdir(parents=True, exist_ok=True)
    if html is not None:
        html.parent.mkdir(parents=True, exist_ok=True)
    # Pre-check and exclusive creation prevent silent overwrites of prior results.
    if rendered is not None:
        with html.open("x", encoding="utf-8") as stream:
            stream.write(rendered)
    with out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return report


def _fmt(value: float | None, *, pct: bool = False) -> str:
    if value is None:
        return "Sem dados"
    return f"{value * (100 if pct else 1):.2f}" + ("%" if pct else "")


def _render_html(report: dict) -> str:
    """Offline, script-free dashboard: dynamic player names are escaped."""
    def e(value: object) -> str:
        return escape(str(value), quote=True)

    rows = []
    for name, item in report["players"].items():
        strength = item["strength"]["game_level"]
        outcomes = item["completed_outcomes"]
        rows.append(
            "<tr><td>" + e(name) + "</td><td>" + str(item["distinct_games"]) +
            "</td><td>" + str(outcomes["win"]) + "/" + str(outcomes["draw"]) +
            "/" + str(outcomes["loss"]) + "</td><td>" +
            _fmt(strength["mean"]) + "</td><td>" +
            str(strength["games"]) + "</td><td>" +
            e(strength["uncertainty"]) + "</td></tr>"
        )
    sections = []
    for pair in report["pairwise"]:
        a, b = e(pair["a"]), e(pair["b"])
        strength = pair["strength_paired_by_game"]
        outcomes = pair["outcomes_a"]
        style_rows = []
        for phase, features in pair["phase_style_differences"].items():
            for feature in ("capture", "check", "pawn_move", "queen_move", "king_move"):
                record = features[feature]
                style_rows.append(
                    "<tr><td>" + e(phase) + "</td><td>" + e(feature) +
                    "</td><td>" + str(record["games"]) + "</td><td>" +
                    _fmt(record["mean"], pct=True) + "</td></tr>"
                )
        sections.append(
            f"<section><h2>{a} × {b}</h2>"
            "<p>Partidas distintas: " + str(pair["distinct_head_to_head_games"]) +
            " · concluídas: " + str(pair["completed_head_to_head_games"]) +
            " · vitórias/empates/derrotas de A: " +
            f'{outcomes["win"]}/{outcomes["draw"]}/{outcomes["loss"]}' + "</p>"
            "<h3>Força · diferença de CP loss médio por partida (A − B)</h3>"
            "<p>Média: " + _fmt(strength["mean"]) + " · n de partidas pareadas: " +
            str(strength["games"]) + " · incerteza: " + e(strength["uncertainty"]) +
            " · intervalo bootstrap: " +
            (" a ".join(_fmt(x) for x in strength["bootstrap_ci95"])
             if strength["bootstrap_ci95"] else "não estimado") + "</p>"
            "<h3>Estilo · diferença de frequência por partida (A − B)</h3>"
            "<table><thead><tr><th>Fase</th><th>Traço</th><th>Partidas</th>"
            "<th>Diferença média</th></tr></thead><tbody>" +
            "".join(style_rows) + "</tbody></table>"
            "<h3>Posições exatamente iguais</h3><p>" +
            str(pair["same_position_choices"]["exact_fen_positions"]) +
            " FENs compartilhados · " +
            str(pair["same_position_choices"]["positions_with_any_different_choice"]) +
            " com alguma escolha divergente. As posições não são novas partidas independentes.</p>"
            "</section>"
        )
    return (
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\">"
        "<title>ChessSim · evidências pareadas</title>"
        "<style>body{font:15px/1.55 system-ui,sans-serif;background:#101c23;color:#e9f2f1;"
        "max-width:1150px;margin:20px auto;padding:0 18px}h1,h2,h3{color:#f2d59a}"
        "section{background:#20343d;border:1px solid #47636c;border-radius:12px;"
        "padding:18px;margin:18px 0}table{width:100%;border-collapse:collapse}"
        "th,td{text-align:left;padding:8px;border-bottom:1px solid #49636a}"
        "th{color:#d7bd88}p,li{color:#c9dadd}small{color:#b0c7c8}"
        "</style></head><body><h1>Força ≠ identidade de estilo</h1>"
        "<p>Relatório exploratório por partidas distintas; não estima Elo, "
        "precisão Chess.com nem fidelidade histórica.</p>"
        "<section><h2>Jogadores</h2><table><thead><tr><th>Jogador</th>"
        "<th>Partidas</th><th>V/E/D</th><th>CP médio por partida</th>"
        "<th>Jogos com CP</th><th>Incerteza</th></tr></thead><tbody>" +
        "".join(rows) + "</tbody></table></section>" +
        "".join(sections) +
        "<section><h2>Limites da evidência</h2><ul>" +
        "".join("<li>" + e(w) + "</li>" for w in report["warnings"]) +
        "</ul></section></body></html>"
    )
