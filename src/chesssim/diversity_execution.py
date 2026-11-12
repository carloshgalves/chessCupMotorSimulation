"""Controlled UCI pilot for pre-registered A1/A2/B opening-seed experiments.

One immutable plan, three isolated two-game arms per block. This executor
records REAL results when run with the operator's authorized Rodent files,
but never treats variation as historical similarity or independent evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import stat

import chess.pgn

from .catalog import find_player
from .diversity_experiment import design
from .game_diversity import audit_mainlines, mainline_identity
from .matches import _atomic_text, file_hash, play_duel, validate
from .opening_books import load_opening_book, select_book_move

MAX_PLAN_BYTES = 256 * 1024
MAX_SUMMARY_BYTES = 256 * 1024
MAX_PGN_BYTES = 2 * 1024 * 1024
MAX_BOOK_BYTES = 64 * 1024 * 1024
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
STATUS_PLANNED = "REGISTERED_NOT_RUN"
STATUS_RUNNING = "RUNNING_UNVERIFIED"
STATUS_COMPLETED = "EXECUTED_RESULTS_NOT_CALIBRATED"
STATUS_SUBSET = "EXECUTED_SUBSET_NOT_CALIBRATED"
STATUS_FAILED = "FAILED_REQUIRES_MANUAL_INSPECTION"
CONDITIONS = ("A1_RESEARCH_CONTROL", "A2_EXACT_REPLICATE", "B_ALTERNATE_SEED")


def _read_regular(path: Path, cap: int) -> bytes:
    """Bounded, single-FD POSIX read. Reject aliases and nonregular files."""
    if path.is_symlink():
        raise ValueError(f"Arquivo simbólico não permitido: {path.name}")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ValueError(f"Arquivo inacessível: {path.name}") from exc
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > cap:
            raise ValueError(f"Arquivo inválido ou excessivo: {path.name}")
        data = source.read(cap + 1)
    if len(data) > cap:
        raise ValueError(f"Arquivo cresceu além do limite: {path.name}")
    return data


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Chaves JSON duplicadas no plano")
        result[key] = value
    return result


def load_registered_plan(path: Path) -> tuple[dict, str]:
    """Regenerate the deterministic contract before accepting any UCI work."""
    raw = _read_regular(path, MAX_PLAN_BYTES)
    try:
        plan = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Plano JSON inválido") from exc
    if not isinstance(plan, dict):
        raise ValueError("Plano deve ser objeto JSON")
    players, trials, blocks = plan.get("players"), plan.get("trials"), plan.get("blocks")
    flag = plan.get("seed_effectiveness")
    declared = flag == "OPERATOR_DECLARED_EXTERNAL_REPERTOIRE_UNVERIFIED"
    if flag not in {
        "OPERATOR_DECLARED_EXTERNAL_REPERTOIRE_UNVERIFIED",
        "NO_EFFECT_EXPECTED_WITHOUT_EXTERNAL_REPERTOIRE",
    }:
        raise ValueError("Hipótese de repertório inválida")
    if not (isinstance(players, list) and len(players) == 2 and
            isinstance(blocks, list) and bool(blocks) and type(trials) is int and
            2 <= trials <= 40 and len(blocks) == trials):
        raise ValueError("Estrutura ou N de blocos inválidos")
    first = blocks[0]
    if not isinstance(first, dict) or not isinstance(first.get("runs"), list) or not first["runs"]:
        raise ValueError("Primeiro bloco inválido")
    seed = first["runs"][0]
    if not isinstance(seed, dict):
        raise ValueError("Condição de seed inválida")
    expected = design(players, seed=seed.get("seed"), trials=trials,
                      declared_external_repertoire=declared)
    if plan != expected:
        raise ValueError("Plano diverge do pré-registro imutável; gere novamente")
    return plan, sha256(raw).hexdigest()


def _no_symbolic_ancestors(path: Path) -> None:
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("Destino com symlink não permitido")
    if path.exists():
        raise FileExistsError("Pasta do ensaio já existe; nenhuma retomada implícita")


def _load_books(plan: dict, white, black, white_book: Path | None,
                black_book: Path | None) -> tuple[dict, dict]:
    declared = plan["seed_effectiveness"] == "OPERATOR_DECLARED_EXTERNAL_REPERTOIRE_UNVERIFIED"
    if declared != (white_book is not None or black_book is not None):
        raise ValueError("Declaração do plano e presença dos livros externos não coincidem")
    books = {}
    provenance = {}
    for player, source in ((white, white_book), (black, black_book)):
        if source is None:
            continue
        if source.is_symlink():
            raise ValueError("Livro simbólico não autorizado")
        # The current book loader accepts paths; bracket its read with
        # bounded no-follow byte hashes to detect mid-preflight mutation.
        book_sha = sha256(_read_regular(source, MAX_BOOK_BYTES)).hexdigest()
        book = load_opening_book(source, player.slug)
        if sha256(_read_regular(source, MAX_BOOK_BYTES)).hexdigest() != book_sha:
            raise ValueError("Repertório foi alterado durante o pré-voo")
        info = book.get("source")
        if not isinstance(info, dict):
            raise ValueError("Livro sem fonte auditável")
        provenance_info = info.get("provenance")
        recorded_sha = info.get("pgn_sha256")
        if (not isinstance(provenance_info, dict) or
                provenance_info.get("status") not in {
                    "FROZEN_V5_TRAIN_ONLY", "PUBLIC_PGN_UNPARTITIONED_EXPLORATORY"
                } or not isinstance(recorded_sha, str) or not SHA64.fullmatch(recorded_sha)):
            raise ValueError("Proveniência do repertório ausente ou não reconhecida")
        books[player.slug] = book
        provenance[player.slug] = {
            "file_sha256": book_sha,
            "source_pgn_sha256": recorded_sha,
            "source_policy": provenance_info["status"],
            "path": str(source),
        }
    return books, provenance


def _audit_completed_pair(folder: Path, *, players: list[str],
                          player_objects: dict, seed: int,
                          expected_hashes: dict, books: dict, book_sources: dict) -> dict:
    """Reconcile disk receipts and both UCI PGNs before recording completion."""
    has_books = bool(book_sources)
    expected_mode = "historical-v09-exploratory" if has_books else "disabled"
    raw = _read_regular(folder / "summary.json", MAX_SUMMARY_BYTES)
    report = json.loads(raw)
    if (not isinstance(report, dict) or report.get("status") != "COMPLETED" or
            report.get("players") != players or
            report.get("file_hashes") != expected_hashes or
            report.get("book_hashes") != {} or
            report.get("historical_opening_policy") != (
                "EXPLORATORY_POSITION_CONDITIONED" if has_books else "DISABLED") or
            not isinstance(report.get("variation_control"), dict) or
            report["variation_control"].get("historical_opening_seed_effective") is not has_books or
            report["variation_control"].get("internal_uci_book") is not False or
            report["variation_control"].get("colors_reversed") is not True or
            type(report.get("opening_seed")) is not (int if has_books else type(None)) or
            (has_books and report["opening_seed"] != seed)):
        raise ValueError("Resumo UCI incompleto ou com recursos diferentes")
    games = report.get("games")
    if not isinstance(games, list) or len(games) != 2:
        raise ValueError("Controle requer duas partidas completas com cores invertidas")
    expected_colors = (players, list(reversed(players)))
    receipts = []
    for index, record in enumerate(games, 1):
        if (not isinstance(record, dict) or record.get("status") != "COMPLETED" or
                record.get("white") != expected_colors[index - 1][0] or
                record.get("black") != expected_colors[index - 1][1] or
                type(record.get("round")) is not int or record["round"] != index or
                record.get("result") not in {"1-0", "0-1", "1/2-1/2"} or
                record.get("opening_mode") != expected_mode or
                type(record.get("opening_seed")) is not (
                    int if has_books else type(None)) or
                (has_books and record["opening_seed"] != seed)):
            raise ValueError("Recibo UCI não confirma ambas as cores, seed e política")
        pgn_path = folder / f"jogo_{index}.pgn"
        payload = _read_regular(pgn_path, MAX_PGN_BYTES)
        from io import StringIO
        stream = StringIO(payload.decode("utf-8-sig"))
        game = chess.pgn.read_game(stream)
        if game is None or game.errors or chess.pgn.read_game(stream) is not None:
            raise ValueError("PGN não contém exatamente uma partida legal")
        trajectory, plies = mainline_identity(game)
        white_player = player_objects[record["white"]]
        black_player = player_objects[record["black"]]
        if (type(record.get("plies")) is not int or record["plies"] != plies or
                record.get("mainline_sha256") != trajectory or
                game.headers.get("Result") != record["result"] or
                game.headers.get("BookMode") != expected_mode or
                game.headers.get("White") != white_player.name or
                game.headers.get("Black") != black_player.name or
                game.headers.get("WhiteModel") != f"RodentIV:{white_player.rodent_alias}" or
                game.headers.get("BlackModel") != f"RodentIV:{black_player.rodent_alias}" or
                game.headers.get("WhiteProfileSHA256") != expected_hashes[record["white"]] or
                game.headers.get("BlackProfileSHA256") != expected_hashes[record["black"]] or
                game.headers.get("EngineSHA256") != expected_hashes["exe"] or
                (has_books and (game.headers.get("OpeningSeed") != str(seed) or
                                game.headers.get("OpeningMinCount") != "1"))):
            raise ValueError("Recibo diverge da trajetória, seed, perfil ou cabeçalho do PGN")
        # A receipt claiming a different opening seed/round must not be
        # counted as a controlled observation even when its PGN is legal.
        trace = record.get("opening_trace")
        if not isinstance(trace, list) or any(
            not isinstance(entry, dict) or entry.get("seed") != seed or
            entry.get("round") != index or
            entry.get("player") not in players or
            type(entry.get("ply")) is not int or not 1 <= entry["ply"] <= plies
            for entry in trace
        ):
            raise ValueError("Traço de abertura sem seed/rodada verificável")
        if not has_books and trace:
            raise ValueError("Traço de abertura presente sem repertório declarado")
        # Regenerate every opening decision against the frozen original books.
        # Mere agreement of a trace with a PGN is not enough: a fabricated
        # trace could otherwise be counted as a controlled repertoire choice.
        board = game.board()
        expected_trace = []
        counts = {"white": 0, "black": 0}
        for ply, move in enumerate(game.mainline_moves(), 1):
            side = "white" if board.turn else "black"
            owner = record["white"] if board.turn else record["black"]
            book = books.get(owner)
            expected_move = (select_book_move(book, board, seed=seed,
                                              game_number=index, min_count=1)
                             if book is not None else None)
            if expected_move is not None:
                if expected_move != move:
                    raise ValueError("PGN diverge da escolha de abertura pré-registrada")
                expected_trace.append({
                    "ply": ply, "player": owner, "move": move.uci(),
                    "seed": seed, "round": index,
                    "book_source_sha256": book_sources[owner]["source_pgn_sha256"],
                })
                counts[side] += 1
            board.push(move)
        if trace != expected_trace or record.get("opening_moves") != counts:
            raise ValueError("Traço de abertura não reproduz o livro congelado")
        receipts.append({
            "game": index, "pgn_sha256": sha256(payload).hexdigest(),
            "mainline_sha256": trajectory, "plies": plies,
            "white": record["white"], "black": record["black"], "result": record["result"],
            "white_name": white_player.name, "black_name": black_player.name,
            "opening_moves": record.get("opening_moves"),
            "opening_seed": record.get("opening_seed"),
            "opening_trace": record.get("opening_trace"),
        })
    # A separately aggregated duel PGN must contain exactly the two
    # verified trajectories, in order. Hashing it without parsing would
    # silently bless an unrelated PGN as consolidated evidence.
    duo = _read_regular(folder / "duelo.pgn", MAX_PGN_BYTES * 2)
    from io import StringIO
    with StringIO(duo.decode("utf-8-sig")) as stream:
        duo_games = []
        while (game := chess.pgn.read_game(stream)) is not None:
            if game.errors or len(duo_games) >= 2:
                raise ValueError("PGN consolidado corrompido ou com partidas extras")
            identity, count = mainline_identity(game)
            duo_games.append((
                identity, count, game.headers.get("Result"),
                game.headers.get("White"), game.headers.get("Black"),
                game.headers.get("EngineSHA256")))
    expected = [
        (item["mainline_sha256"], item["plies"], item["result"],
         item["white_name"], item["black_name"], expected_hashes["exe"])
        for item in receipts
    ]
    if duo_games != expected:
        raise ValueError("PGN consolidado diverge das duas partidas individuais")
    return {
        "folder": str(folder), "summary_sha256": sha256(raw).hexdigest(),
        "duel_pgn_sha256": sha256(duo).hexdigest(), "games": receipts,
        "status": "COMPLETED_RECEIPTS_VERIFIED",
    }


def execute_plan(root: Path, plan_path: Path, out: Path, *,
                 white_book: Path | None = None, black_book: Path | None = None,
                 blocks: int = 1, depth: int = 8, max_plies: int = 400,
                 max_game_wall_seconds: float = 600) -> dict:
    if type(blocks) is not int or not 1 <= blocks <= 40:
        raise ValueError("Execução limitada a 1..40 blocos")
    if type(depth) is not int or not 1 <= depth <= 30:
        raise ValueError("Profundidade de teste deve ser 1..30")
    if type(max_plies) is not int or not 2 <= max_plies <= 600:
        raise ValueError("Limite de meios-lances deve ser 2..600")
    if (type(max_game_wall_seconds) not in (float, int) or
            not math.isfinite(max_game_wall_seconds) or
            not 1 <= max_game_wall_seconds <= 7200):
        raise ValueError("Orçamento por partida deve ser 1..7200 segundos")
    plan, plan_file_sha = load_registered_plan(plan_path)
    if blocks > plan["trials"]:
        raise ValueError("Blocos solicitados excedem o plano")
    _no_symbolic_ancestors(out)
    if out.resolve() == plan_path.resolve() or plan_path.resolve().is_relative_to(out.resolve()):
        raise ValueError("Plano de origem não pode ficar dentro do destino novo")
    players = [find_player(slug) for slug in plan["players"]]
    white, black = players
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Diretório de motor inexistente")
    if out.resolve().is_relative_to(root):
        raise ValueError("Destino experimental deve ficar fora da instalação Rodent")
    executable, paths = validate(root, white, black)
    books, book_sources = _load_books(plan, white, black, white_book, black_book)
    frozen = {"exe": file_hash(executable)}
    frozen.update({slug: file_hash(path) for slug, path in paths.items()})
    def assert_frozen_inputs() -> None:
        current_exe, current_paths = validate(root, white, black)
        current = {"exe": file_hash(current_exe)}
        current.update({slug: file_hash(path) for slug, path in current_paths.items()})
        if current != frozen or current_exe != executable or current_paths != paths:
            raise RuntimeError("Hash/caminho do motor/perfis mudou entre braços")
        for slug, provenance in book_sources.items():
            source = white_book if slug == white.slug else black_book
            if (source is None or
                    sha256(_read_regular(source, MAX_BOOK_BYTES)).hexdigest()
                    != provenance["file_sha256"]):
                raise RuntimeError("Hash do repertório mudou entre braços")

    inputs = {"engine_sha256": frozen["exe"],
              "profiles": {slug: frozen[slug] for slug in plan["players"]},
              "external_books": book_sources,
              "depth": depth, "max_plies": max_plies,
              "max_game_wall_seconds": max_game_wall_seconds,
              "use_uci_internal_book": False, "two_reversed_colors": True}
    # Explicit, exclusive creation. Never overwrite or implicitly resume an
    # uncertain run after a crash (even if some arm folders look complete).
    out.mkdir(parents=True, exist_ok=False)
    _, initial_diversity = audit_mainlines([])
    observations = []
    manifest = {
        "schema": "chesssim-diversity-execution-v1",
        "status": STATUS_PLANNED, "created_utc": datetime.now(timezone.utc).isoformat(),
        "plan_file_sha256": plan_file_sha,
        "design_sha256": plan["design_sha256"],
        "seed_effectiveness": plan["seed_effectiveness"],
        "players": plan["players"], "blocks_requested": blocks,
        "blocks_total_in_plan": plan["trials"],
        "arms_planned": 3 * blocks, "arms_completed": 0,
        "inputs": inputs, "arms": [],
        "game_evidence": [], "diversity": initial_diversity,
        "warning": ("Executed UCI observations only; no claim of improved diversity, "
                    "independent observations or historical fidelity. "
                    "No sealed holdout is loaded or evaluated."),
    }

    def checkpoint() -> None:
        _atomic_text(out / "execution.json", json.dumps(
            manifest, ensure_ascii=False, indent=2) + "\n",
            durable=os.name == "posix")

    checkpoint()
    try:
        manifest["status"] = STATUS_RUNNING
        checkpoint()
        for block in plan["blocks"][:blocks]:
            for expected_condition, arm in zip(CONDITIONS, block["runs"], strict=True):
                if arm["condition"] != expected_condition:
                    raise RuntimeError("Condição divergente do plano auditado")
                # Refuse mutation of engine, profile or book files between
                # arms: otherwise a seed experiment has changed covariates.
                assert_frozen_inputs()
                block_dir = out / "arms" / f"block-{block['block']:03d}" / expected_condition.lower()
                folder = play_duel(
                    root, block_dir, white, black, depth=depth, movetime=None,
                    reverse=True, max_plies=max_plies, use_books=False,
                    historical_openings=books or None, opening_seed=arm["seed"],
                    opening_min_count=1, max_wall_seconds=max_game_wall_seconds)
                if (folder.is_symlink() or
                        not folder.resolve().is_relative_to(block_dir.resolve())):
                    raise ValueError("Artefatos UCI fora do braço isolado")
                # Do not count a finished arm if covariates changed while
                # the UCI processes were running, including the very last arm.
                assert_frozen_inputs()
                receipt = _audit_completed_pair(
                    folder, players=plan["players"],
                    player_objects={p.slug: p for p in players}, seed=arm["seed"],
                    expected_hashes=frozen, books=books, book_sources=book_sources)
                manifest["arms"].append({
                    "block": block["block"], "condition": expected_condition,
                    "seed": arm["seed"], "game_number_policy": "1,2 per arm",
                    "receipt": receipt,
                })
                manifest["arms_completed"] += 1
                for game in receipt["games"]:
                    observations.append({
                        "source": (f"block_{block['block']:03d}/"
                                   f"{expected_condition}/game_{game['game']}"),
                        "mainline_sha256": game["mainline_sha256"],
                        "plies": game["plies"],
                        "opening_seed": arm["seed"] if books else None,
                        "opening_mode": ("historical-v09-exploratory"
                                         if books else "disabled"),
                        "color_order": game["white"] + "|" + game["black"],
                    })
                annotated, diversity = audit_mainlines(observations)
                manifest["game_evidence"] = annotated
                manifest["diversity"] = diversity
                checkpoint()
        manifest["status"] = (
            STATUS_COMPLETED if blocks == plan["trials"] else STATUS_SUBSET)
        manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
    except BaseException as exc:
        manifest["status"] = STATUS_FAILED
        manifest["error"] = type(exc).__name__ + ": " + str(exc)[:250]
        raise
    finally:
        checkpoint()
    return manifest
