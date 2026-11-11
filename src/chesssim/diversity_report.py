"""Read-only cross-duel diversity audit grounded in locally completed PGN moves.

Does not call UCI, change seeds, create chess moves or use training/holdout data.
Distinct PGN move orders count as distinct trajectories; matching positions
at an opening checkpoint are a diagnostic, not proof of duplicate games.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable

import chess.pgn

from .game_diversity import mainline_identity


SCHEMA = "chesssim-cross-duel-diversity-v1"
MAX_SUMMARIES = 1000
MAX_GAMES = 2000
MAX_PGN_BYTES = 2 * 1024 * 1024
MAX_SUMMARY_BYTES = 2 * 1024 * 1024
VALID_RESULTS = {"1-0", "0-1", "1/2-1/2"}


def _safe_file(path: Path, max_bytes: int) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Arquivo local ausente, simbólico ou inválido")
    if path.stat().st_size > max_bytes:
        raise ValueError("Arquivo local excede o limite de leitura")


def _load_game(path: Path):
    _safe_file(path, MAX_PGN_BYTES)
    with path.open("r", encoding="utf-8") as handle:
        game = chess.pgn.read_game(handle)
        if game is None or game.errors:
            raise ValueError("PGN ausente ou com movimento inválido")
        if chess.pgn.read_game(handle) is not None:
            raise ValueError("PGN de jogo contém partidas adicionais")
    return game


def _opening_position(game, opening_plies: int) -> str | None:
    board = game.board()
    moves = list(game.mainline_moves())
    if len(moves) < opening_plies:
        return None
    for move in moves[:opening_plies]:
        if move not in board.legal_moves:
            raise ValueError("Linha principal ilegal")
        board.push(move)
    # Move counters must not manufacture distinct positions; keep side to
    # move, castling rights and legal en-passant in the identity.
    position = " ".join(board.fen(en_passant="legal").split()[:4])
    return sha256(position.encode("ascii")).hexdigest()


def _source_tag(root_index: int, root: Path, summary: Path, game_number: int) -> str:
    relative = summary.relative_to(root).as_posix()
    return f"root-{root_index:02d}/{relative}#game-{game_number}"


def _discover(roots: Iterable[Path]) -> list[tuple[int, Path, Path]]:
    manifests = []
    visited = set()
    for number, supplied in enumerate(roots, 1):
        if supplied.is_symlink():
            raise ValueError("Raiz simbólica não permitida")
        root = supplied.expanduser().resolve()
        if not root.is_dir():
            raise ValueError("Diretório de duelos não encontrado")
        for summary in sorted(root.rglob("summary.json")):
            # Ignore nested directory symlinks without following them.
            if summary.is_symlink() or not summary.is_file():
                raise ValueError("Manifesto simbólico/inválido")
            if any(part.is_symlink() for part in summary.relative_to(root).parents
                   if part != Path(".")):
                raise ValueError("Diretório intermediário simbólico")
            real = summary.resolve()
            if real in visited:
                continue
            visited.add(real)
            manifests.append((number, root, summary))
            if len(manifests) > MAX_SUMMARIES:
                raise ValueError("Número excessivo de manifestos")
    return manifests


def _load_records(roots: Iterable[Path], opening_plies: int) -> tuple[list[dict], int]:
    rows = []
    skipped = 0
    for number, root, summary in _discover(roots):
        _safe_file(summary, MAX_SUMMARY_BYTES)
        try:
            manifest = json.loads(summary.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Manifesto JSON inválido") from exc
        if not isinstance(manifest, dict):
            raise ValueError("Manifesto JSON não é objeto")
        if "players" not in manifest or "games" not in manifest:
            skipped += 1  # Other summary.json files are not duel manifests.
            continue
        players = manifest["players"]
        games = manifest["games"]
        if (not isinstance(players, list) or len(players) != 2 or
                not all(isinstance(x, str) and 0 < len(x) <= 80 for x in players) or
                not isinstance(games, list) or len(games) > 2):
            raise ValueError("Manifesto de duelo com estrutura inválida")
        for i, meta in enumerate(games, 1):
            if not isinstance(meta, dict):
                raise ValueError("Registro de partida não é objeto")
            if len(rows) >= MAX_GAMES:
                raise ValueError("Limite de partidas excedido")
            path = summary.parent / f"jogo_{i}.pgn"
            game = _load_game(path)
            digest, plies = mainline_identity(game)
            if (meta.get("mainline_sha256") != digest or
                    type(meta.get("plies")) is not int or meta["plies"] != plies):
                raise ValueError("PGN não corresponde ao resumo de diversidade")
            if meta.get("white") not in players or meta.get("black") not in players:
                raise ValueError("Jogadores incompatíveis com o manifesto")
            if meta["white"] == meta["black"] and players[0] != players[1]:
                raise ValueError("Cor dos jogadores incompatível")
            if (game.headers.get("Result", "*") != meta.get("result") or
                    meta.get("result") not in VALID_RESULTS | {"*"}):
                raise ValueError("Resultado do PGN difere do resumo")
            if meta.get("status") not in ("INCOMPLETE", "COMPLETED"):
                raise ValueError("Status de jogo inválido")
            seed = meta.get("opening_seed")
            if seed is not None and type(seed) is not int:
                raise ValueError("Semente de abertura inválida")
            mode = meta.get("opening_mode", "unknown")
            if not isinstance(mode, str) or not 0 < len(mode) <= 100:
                raise ValueError("Modo de abertura inválido")
            row = {
                "source": _source_tag(number, root, summary, i),
                "players": sorted(players),
                "white": meta["white"],
                "black": meta["black"],
                "opening_seed": seed,
                "opening_mode": mode,
                "complete": meta["status"] == "COMPLETED" and meta["result"] in VALID_RESULTS,
                "result": meta["result"],
                "plies": plies,
                "mainline_sha256": digest,
                "opening_position_sha256": _opening_position(game, opening_plies),
            }
            rows.append(row)
    if not rows:
        raise ValueError("Nenhum duelo com partidas verificáveis encontrado")
    return rows, skipped


def cross_duel_report(roots: Iterable[Path], *, opening_plies: int = 12) -> dict:
    if type(opening_plies) is not int or not 2 <= opening_plies <= 40:
        raise ValueError("opening_plies deve ser inteiro entre 2 e 40")
    rows, ignored = _load_records(roots, opening_plies)
    first: dict[str, str] = {}
    groups = defaultdict(list)
    opening_positions = defaultdict(set)
    for row in rows:
        digest = row["mainline_sha256"]
        if row["plies"] > 0:
            row["duplicate_of"] = first.get(digest)
            first.setdefault(digest, row["source"])
        else:
            row["duplicate_of"] = None
        key = (tuple(row["players"]), row["opening_mode"])
        groups[key].append(row)
        if row["opening_position_sha256"]:
            opening_positions[row["opening_position_sha256"]].add(digest)
    comparisons = []
    for (players, mode), records in sorted(groups.items()):
        eligible = [r for r in records if r["plies"] > 0]
        complete = [r for r in records if r["complete"]]
        per_seed = defaultdict(list)
        for r in eligible:
            per_seed[str(r["opening_seed"]) if r["opening_seed"] is not None else "unrecorded"].append(r)
        comparisons.append({
            "players": list(players),
            "opening_mode": mode,
            "games": len(records),
            "completed_games": len(complete),
            "exact_duplicate_games": sum(r["duplicate_of"] is not None for r in eligible),
            "distinct_mainlines": len({r["mainline_sha256"] for r in eligible}),
            "seeds": [{
                "seed": seed,
                "games": len(items),
                "distinct_mainlines": len({r["mainline_sha256"] for r in items}),
            } for seed, items in sorted(per_seed.items())],
        })
    nonempty = [r for r in rows if r["plies"] > 0]
    return {
        "schema": SCHEMA, "opening_checkpoint_plies": opening_plies,
        "games_total": len(rows), "games_with_moves": len(nonempty),
        "completed_games": sum(r["complete"] for r in rows),
        "incomplete_or_unscored_games": sum(not r["complete"] for r in rows),
        "distinct_mainlines": len({r["mainline_sha256"] for r in nonempty}),
        "exact_duplicate_games": sum(r["duplicate_of"] is not None for r in nonempty),
        "transposed_opening_position_groups": sum(
            len(trajectories) > 1 for trajectories in opening_positions.values()),
        "non_duel_manifests_ignored": ignored,
        "comparisons": comparisons,
        "games": rows,
        "warnings": [
            "This report is descriptive: it does not prove style fidelity, playing strength or improvement.",
            "A shared opening position can be reached via different legal move orders; it is not an exact game duplicate.",
            "OpeningSeed affects the external position-conditioned repertoire only if active; internal UCI randomness is not controlled by that seed.",
            "Incomplete and zero-ply games are retained for diagnosis, never counted as completed outcomes.",
            "Inputs are local PGNs and summaries, not sealed historical training, validation or holdout corpora.",
        ],
    }


def export_diversity(roots: Iterable[Path], out: Path, *, opening_plies: int = 12) -> dict:
    if out.is_symlink() or out.exists():
        raise FileExistsError("Relatório já existe ou aponta para symlink; não sobrescrever")
    report = cross_duel_report(roots, opening_plies=opening_plies)
    out = out.expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    # Create exclusively; no overwrites of an existing user file.
    with out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return report
