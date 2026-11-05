"""Mata-mata persistente: duelos pareados, checkpoint, retomada e desempates explícitos."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import random

import chess.pgn

from .catalog import find_player
from .matches import _atomic_text, file_hash, official_book_hashes, play_duel, validate
from .game_diversity import mainline_identity
from .peak_windows import CLOCK_PRESETS
from .protocol import OFFICIAL

SCHEMA = "chesssim-tournament-v1"
RESULTS = {"1-0", "0-1", "1/2-1/2"}
CLOCKS = {"official": (600, 2), **CLOCK_PRESETS}


def _save(directory: Path, state: dict) -> None:
    _atomic_text(directory / "tournament.json", json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def load_tournament(directory: Path) -> dict:
    path = directory.expanduser().resolve() / "tournament.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"Manifesto de torneio incompatível: {path}")
    return data


@contextmanager
def _lock(directory: Path):
    """Lock de execução local; o SO libera-o se o processo morrer."""
    with (directory / ".tournament.lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.write(b".")
            stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Outra execução do torneio está ativa") from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Outra execução do torneio está ativa") from exc
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _match(round_number: int, number: int, a: str, b: str) -> dict:
    return {"id": f"r{round_number:02d}-m{number:02d}",
            "players": [a, b], "status": "PENDING",
            "winner": None, "attempts": [], "excluded_attempts": []}


def _round(number: int, players: list[str]) -> dict:
    return {"number": number,
            "matches": [_match(number, i // 2 + 1, players[i], players[i + 1])
                        for i in range(0, len(players), 2)]}


def _preflight(root: Path, slugs: list[str], preset: str) -> dict:
    engine = root / "rodent-iv-plain.exe"
    profiles = {}
    books = {}
    per_player_books = {}
    for slug in slugs:
        player = find_player(slug)
        exe, paths = validate(root, player, player)
        if exe != engine:
            raise RuntimeError("Executable inconsistente no pré-voo")
        profiles[slug] = file_hash(paths[slug])
        required = official_book_hashes(root, paths) if preset == "official" else {}
        books.update(required)
        per_player_books[slug] = sorted(required)
    return {"engine_sha256": file_hash(engine), "profile_sha256": profiles,
            "book_sha256": books, "books_by_player": per_player_books}


def create_tournament(root: Path, directory: Path, *, participants: list[str],
                      seed: int, preset: str = "official",
                      max_tiebreak_pairs: int = 3, max_plies: int = 600) -> dict:
    directory = directory.expanduser().resolve()
    root = root.expanduser().resolve()
    if directory.exists():
        raise FileExistsError(f"Diretório já existe: {directory}")
    if preset not in CLOCKS:
        raise ValueError(f"Preset inválido: {preset}")
    if not 1 <= max_tiebreak_pairs <= 20:
        raise ValueError("max_tiebreak_pairs deve estar entre 1 e 20")
    if max_plies < 2:
        raise ValueError("max_plies deve ser >= 2")
    selected = [find_player(p) for p in participants]
    if len(selected) < 2 or len(selected) > 64 or len(selected) & (len(selected) - 1):
        raise ValueError("Informe 2, 4, 8, 16, 32 ou 64 participantes")
    slugs = [p.slug for p in selected]
    if len(set(slugs)) != len(slugs):
        raise ValueError("Participantes repetidos")
    if any(p.rodent_alias is None for p in selected):
        missing = [p.name for p in selected if p.rodent_alias is None]
        raise ValueError(f"Perfis ainda indisponíveis: {', '.join(missing)}")
    resources = _preflight(root, slugs, preset)
    shuffled = slugs.copy()
    random.Random(seed).shuffle(shuffled)
    state = {"schema": SCHEMA, "created_utc": datetime.now(timezone.utc).isoformat(),
             "status": "READY", "seed": seed, "participants": slugs,
             "initial_bracket": shuffled, "preset": preset,
             "clock": list(CLOCKS[preset]), "max_plies": max_plies,
             "max_tiebreak_pairs": max_tiebreak_pairs,
             "engine": "Rodent IV", "historically_validated": False,
             "resources": resources, "rounds": [_round(1, shuffled)],
             "champion": None, "resolution_log": []}
    directory.mkdir(parents=True, exist_ok=False)
    _save(directory, state)
    return state


def _verify_resources(root: Path, state: dict) -> None:
    now = _preflight(root, state["participants"], state["preset"])
    if now != state["resources"]:
        raise RuntimeError("Executável, perfis ou livros divergiram da versão congelada do torneio")


def _duel_dir(directory: Path, match: dict) -> Path:
    return directory / "matches" / match["id"] / "duels"


def _valid_completed_attempt(directory: Path, state: dict, match: dict,
                             summary_file: Path) -> dict:
    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    a, b = match["players"]
    if summary.get("status") != "COMPLETED":
        raise ValueError("Confronto ainda não concluído")
    games = summary.get("games", [])
    if summary.get("players") != [a, b] or len(games) != 2:
        raise RuntimeError(f"Resumo de confronto incompatível: {summary_file}")
    expected_resources = {"exe": state["resources"]["engine_sha256"],
                          a: state["resources"]["profile_sha256"][a],
                          b: state["resources"]["profile_sha256"][b]}
    if summary.get("file_hashes") != expected_resources:
        raise RuntimeError(f"Hashes do motor/perfis divergentes: {summary_file}")
    book_keys = set(state["resources"]["books_by_player"][a])
    book_keys.update(state["resources"]["books_by_player"][b])
    expected_books = {key: state["resources"]["book_sha256"][key] for key in book_keys}
    if summary.get("book_hashes") != expected_books:
        raise RuntimeError(f"Hashes dos livros divergentes: {summary_file}")
    protocol = summary.get("protocol", {})
    if state["preset"] == "official":
        if not isinstance(protocol, dict) or protocol.get("sha256") != OFFICIAL.json()["sha256"]:
            raise RuntimeError("Protocolo oficial incorreto")
    elif not isinstance(protocol, dict) or protocol.get("time_control") != (
        f"{state['clock'][0]}+{state['clock'][1]}"):
        raise RuntimeError("Ritmo do confronto divergente")
    pgn_file = summary_file.with_name("duelo.pgn")
    results = []
    digests = []
    with pgn_file.open(encoding="utf-8") as stream:
        for i in range(2):
            pgn = chess.pgn.read_game(stream)
            if pgn is None or pgn.errors:
                raise RuntimeError(f"PGN incompleto ou inválido: {pgn_file}")
            expected_white, expected_black = ((a, b) if i == 0 else (b, a))
            if (pgn.headers.get("White") != find_player(expected_white).name
                    or pgn.headers.get("Black") != find_player(expected_black).name):
                raise RuntimeError(f"Cores/identidade inconsistentes: {pgn_file}")
            result = games[i].get("result")
            digest, plies = mainline_identity(pgn)
            if (result not in RESULTS or games[i].get("status") != "COMPLETED"
                    or pgn.headers.get("Result") != result or
                    games[i].get("mainline_sha256") != digest
                    or games[i].get("plies") != plies):
                raise RuntimeError(f"Resultados ou trajetórias inconsistentes: {pgn_file}")
            if pgn.headers.get("Termination") != games[i].get("termination"):
                raise RuntimeError(f"Terminação do PGN diverge do resumo: {pgn_file}")
            if games[i]["termination"] == "TimeForfeit":
                if not isinstance(games[i].get("clock_left"), list):
                    raise RuntimeError(f"Falta evidência do relógio: {pgn_file}")
            else:
                outcome = pgn.end().board().outcome(claim_draw=True)
                if outcome is None or outcome.result() != result:
                    raise RuntimeError(f"Resultado não comprovado pelo tabuleiro: {pgn_file}")
            results.append(result)
            digests.append(digest)
        if chess.pgn.read_game(stream) is not None:
            raise RuntimeError(f"PGN contém partidas extras: {pgn_file}")
    return {"folder": str(summary_file.parent.relative_to(directory)),
            "results": results, "mainline_sha256": digests,
            "summary_sha256": file_hash(summary_file), "pgn_sha256": file_hash(pgn_file)}


def _score(match: dict) -> dict:
    a, b = match["players"]
    scores = {a: 0.0, b: 0.0}
    for attempt in match["attempts"]:
        for i, result in enumerate(attempt["results"]):
            w, k = (a, b) if i == 0 else (b, a)
            if result == "1-0":
                scores[w] += 1
            elif result == "0-1":
                scores[k] += 1
            else:
                scores[w] += 0.5
                scores[k] += 0.5
    return scores


def _refresh_match(match: dict, max_pairs: int) -> None:
    if match.get("resolution"):
        match["winner"] = match["resolution"]["winner"]
        match["status"] = "COMPLETED"
        return
    scores = _score(match)
    a, b = match["players"]
    if scores[a] != scores[b]:
        match["winner"] = a if scores[a] > scores[b] else b
        match["status"] = "COMPLETED"
    elif len(match["attempts"]) >= max_pairs:
        match["winner"] = None
        match["status"] = "TIE_PENDING"
    elif match["excluded_attempts"] and (
            not match["attempts"] or
            match["excluded_attempts"][-1]["folder"] >
            match["attempts"][-1]["folder"]):
        match["winner"] = None
        match["status"] = "RETRY_REQUIRED"
    else:
        match["winner"] = None
        match["status"] = "PENDING"


def _reconcile(directory: Path, state: dict, match: dict) -> None:
    """Reconstrói tentativas já salvas se a execução caiu antes do checkpoint."""
    a, b = match["players"]
    base = _duel_dir(directory, match) / f"{a}-vs-{b}"
    present = set()
    seen_failures = set()
    previous = {x["folder"]: x for x in match["attempts"]}
    for summary_file in sorted(base.glob("*/summary.json")):
        folder = str(summary_file.parent.relative_to(directory))
        present.add(folder)
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
        if summary.get("status") != "COMPLETED":
            seen_failures.add(folder)
            continue
        attempt = _valid_completed_attempt(directory, state, match, summary_file)
        if folder in previous:
            if previous[folder] != attempt:
                raise RuntimeError(f"Artefato de partida alterado após registro: {folder}")
        else:
            match["attempts"].append(attempt)
    if set(previous) - present:
        raise RuntimeError("Artefato de confronto já registrado foi apagado")
    match["attempts"].sort(key=lambda a: a["folder"])
    match["excluded_attempts"] = [{"folder": p, "reason": "NOT_COMPLETED"}
                                  for p in sorted(seen_failures)]
    _refresh_match(match, state["max_tiebreak_pairs"])


def _advance(state: dict) -> None:
    while True:
        current = state["rounds"][-1]
        matches = current["matches"]
        if not all(m["winner"] is not None for m in matches):
            return
        if len(matches) == 1:
            state["champion"] = matches[0]["winner"]
            state["status"] = "COMPLETED"
            return
        players = [m["winner"] for m in matches]
        state["rounds"].append(_round(current["number"] + 1, players))


def _summary(state: dict) -> dict:
    matches = [m for rd in state["rounds"] for m in rd["matches"]]
    return {"status": state["status"], "champion": state["champion"],
            "participants": len(state["participants"]),
            "rounds": len(state["rounds"]),
            "matches_completed": sum(m["winner"] is not None for m in matches),
            "matches_total_generated": len(matches),
            "matches_pending_tiebreak": [m["id"] for m in matches
                                         if m["status"] == "TIE_PENDING"],
            "matches_retry_required": [m["id"] for m in matches
                                       if m["status"] == "RETRY_REQUIRED"]}


def tournament_status(directory: Path) -> dict:
    return _summary(load_tournament(directory))


def run_tournament(root: Path, directory: Path, *, limit_pairs: int | None = None,
                   retry_incomplete: bool = False) -> dict:
    directory = directory.expanduser().resolve()
    root = root.expanduser().resolve()
    if limit_pairs is not None and limit_pairs < 1:
        raise ValueError("limit_pairs deve ser positivo")
    with _lock(directory):
        state = load_tournament(directory)
        _verify_resources(root, state)
        played = 0
        while True:
            # Valida todos os artefatos já incorporados, inclusive de rodadas anteriores.
            for rd in state["rounds"]:
                for match in rd["matches"]:
                    _reconcile(directory, state, match)
            _advance(state)
            _save(directory, state)
            if state["champion"] is not None:
                break
            current = state["rounds"][-1]
            pending = [m for m in current["matches"] if m["status"] == "PENDING" or
                       (retry_incomplete and m["status"] == "RETRY_REQUIRED")]
            if not pending:
                state["status"] = "BLOCKED"
                break
            for match in pending:
                if limit_pairs is not None and played >= limit_pairs:
                    state["status"] = "PAUSED"
                    _save(directory, state)
                    return _summary(state)
                a, b = (find_player(slug) for slug in match["players"])
                match["status"] = "RUNNING"
                state["status"] = "RUNNING"
                _save(directory, state)
                try:
                    play_duel(root, _duel_dir(directory, match), a, b,
                              depth=None, clock=tuple(state["clock"]),
                              reverse=True, max_plies=state["max_plies"],
                              use_books=state["preset"] == "official")
                finally:
                    _reconcile(directory, state, match)
                    _save(directory, state)
                played += 1
            _advance(state)
            if state["champion"] is not None:
                break
            if limit_pairs is not None and played >= limit_pairs:
                state["status"] = "PAUSED"
                break
        if state["champion"] is None and state["status"] not in {"PAUSED", "BLOCKED"}:
            state["status"] = "READY"
        _save(directory, state)
        return _summary(state)


def resolve_tiebreak(directory: Path, match_id: str, winner: str, reason: str) -> dict:
    directory = directory.expanduser().resolve()
    if len(reason.strip()) < 10:
        raise ValueError("Justificativa de arbitragem insuficiente")
    winner = find_player(winner).slug
    with _lock(directory):
        state = load_tournament(directory)
        matches = [m for rd in state["rounds"] for m in rd["matches"]
                   if m["id"] == match_id]
        if len(matches) != 1:
            raise ValueError(f"Confronto inexistente: {match_id}")
        match = matches[0]
        _reconcile(directory, state, match)
        if match["status"] != "TIE_PENDING" or winner not in match["players"]:
            raise ValueError("Somente empate esgotado pode ser resolvido por arbitragem")
        decision = {"winner": winner, "reason": reason.strip(),
                    "method": "EXPLICIT_EXTERNAL_ARBITRATION_NOT_ENGINE_RESULT",
                    "utc": datetime.now(timezone.utc).isoformat()}
        match["resolution"] = decision
        state["resolution_log"].append({"match": match_id, **decision})
        _refresh_match(match, state["max_tiebreak_pairs"])
        _advance(state)
        state["status"] = "COMPLETED" if state["champion"] else "READY"
        _save(directory, state)
        return _summary(state)
