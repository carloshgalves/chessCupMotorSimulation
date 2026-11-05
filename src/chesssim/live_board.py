"""Read-only loopback monitor for tournament and duel PGN checkpoints.

All chess legality and FEN generation remain server-side via python-chess.
No external assets, engine control, or mutable HTTP endpoints.
"""
from __future__ import annotations

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import json
import re

import chess
import chess.pgn

MAX_PGN_BYTES = 2 * 1024 * 1024
class InvalidSelection(ValueError):
    """Client-selected match or attempt is not part of the tournament."""


SCHEMA = "chesssim-live-board-v1"
_ASSETS = {"/": ("monitor.html", "text/html; charset=utf-8"),
           "/app.js": ("monitor.js", "text/javascript; charset=utf-8"),
           "/app.css": ("monitor.css", "text/css; charset=utf-8")}


def _checked_file(run: Path, path: Path) -> Path:
    """Never traverse an external symlink while serving a selected run."""
    target = path.resolve(strict=True)
    if not target.is_relative_to(run.resolve(strict=True)):
        raise ValueError("Arquivo de partida fora do diretório monitorado")
    if not target.is_file():
        raise ValueError("Artefato esperado não é arquivo")
    return target


def _one_game(run: Path, path: Path) -> tuple[chess.pgn.Game, float]:
    target = _checked_file(run, path)
    stat = target.stat()
    if stat.st_size > MAX_PGN_BYTES:
        raise ValueError("PGN excedeu o limite de visualização")
    with target.open(encoding="utf-8-sig") as stream:
        game = chess.pgn.read_game(stream)
        if game is None or game.errors or chess.pgn.read_game(stream) is not None:
            raise ValueError("PGN vazio, inválido ou contém mais de uma partida")
    return game, stat.st_mtime


def _states(game: chess.pgn.Game) -> list[dict]:
    board = game.board()
    raw_control = game.headers.get("TimeControl", "")
    clock_match = re.fullmatch(r"(\d+)\+(\d+)", raw_control)
    clocks = ([float(clock_match.group(1))] * 2 if clock_match else [None, None])
    states = [{"fen": board.fen(), "san": "Início", "last": None,
               "turn": "white", "clocks": clocks.copy()}]
    for node in game.mainline():
        move = node.move
        if move not in board.legal_moves:
            raise ValueError("Lance ilegal no PGN")
        actor = 0 if board.turn == chess.WHITE else 1
        san = board.san(move)
        uci = move.uci()
        board.push(move)
        seconds = node.clock()
        # Legacy PGNs may contain TimeControl but no [%clk] annotations:
        # the moving side's remaining time is then unknown, not initial.
        clocks[actor] = seconds
        states.append({"fen": board.fen(), "san": san,
                       "last": [uci[:2], uci[2:4]],
                       "turn": "white" if board.turn == chess.WHITE else "black",
                       "clocks": clocks.copy()})
    return states


def _duel_snapshot(run: Path, folder: Path, *, match_id: str | None = None) -> dict:
    summary_path = _checked_file(run, folder / "summary.json")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(summary, dict):
        raise ValueError("Resumo de confronto inválido")
    completed = summary.get("games", [])
    if not isinstance(completed, list):
        raise ValueError("Lista de partidas inválida")
    games = []
    for index in (1, 2):
        final = folder / f"jogo_{index}.pgn"
        partial = folder / f"jogo_{index}.partial.pgn"
        if final.exists():
            source = final
        elif partial.exists():
            source = partial
        else:
            continue
        game, updated = _one_game(run, source)
        metadata = completed[index - 1] if len(completed) >= index else {}
        if not isinstance(metadata, dict):
            raise ValueError("Metadados de partida inválidos")
        states = _states(game)
        if metadata.get("termination") == "TimeForfeit":
            final_clocks = metadata.get("clock_left")
            if (isinstance(final_clocks, list) and len(final_clocks) == 2 and
                    all(isinstance(c, (int, float)) for c in final_clocks)):
                # No timed-out move is added to PGN; show the final official clock.
                states[-1]["clocks"] = [max(0.0, float(c)) for c in final_clocks]
        games.append({
            "id": f"{folder.name}:game-{index}",
            "number": index,
            "white": game.headers.get("White", "Brancas"),
            "black": game.headers.get("Black", "Pretas"),
            "event": game.headers.get("Event", "Copa dos Imortais"),
            "result": game.headers.get("Result", "*"),
            "termination": game.headers.get("Termination"),
            "status": metadata.get("status", "RUNNING" if not final.exists() else "UNKNOWN"),
            "live": not final.exists() and summary.get("status") == "RUNNING",
            "updated_at": updated,
            "states": states,
        })
    return {"folder": folder.name, "match_id": match_id,
            "status": summary.get("status", "UNKNOWN"), "games": games}


def _latest_duel(run: Path, state: dict) -> tuple[Path | None, str | None]:
    """Use latest on-disk checkpoint, never hide corruption behind older data."""
    candidates = []
    for round_data in state.get("rounds", []):
        for match in round_data.get("matches", []):
            match_id = match["id"]
            a, b = match["players"]
            base = run / "matches" / match_id / "duels" / f"{a}-vs-{b}"
            for item in base.glob("*/summary.json"):
                checked = _checked_file(run, item)
                folder = checked.parent
                timestamps = [checked.stat().st_mtime]
                for p in folder.glob("jogo_*.partial.pgn"):
                    timestamps.append(_checked_file(run, p).stat().st_mtime)
                candidates.append((max(timestamps), folder, match_id))
    if not candidates:
        return None, None
    _, folder, match_id = max(candidates, key=lambda row: (row[0], str(row[1])))
    return folder, match_id


def _match_attempts(run: Path, match: dict) -> list[dict]:
    """Enumerate saved duels, indexed by immutable attempt folder ordering."""
    match_id = match["id"]
    a, b = match["players"]
    base = run / "matches" / match_id / "duels" / f"{a}-vs-{b}"
    attempts = []
    for summary_file in sorted(base.glob("*/summary.json")):
        checked = _checked_file(run, summary_file)
        folder = checked.parent
        if checked.stat().st_size > 256 * 1024:
            raise ValueError("Resumo excedeu limite de visualização")
        metadata = json.loads(checked.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict) or not isinstance(metadata.get("status"), str):
            raise ValueError("Resumo de tentativa inválido")
        timestamps = [checked.stat().st_mtime]
        for p in folder.glob("jogo_*.pgn"):
            timestamps.append(_checked_file(run, p).stat().st_mtime)
        for p in folder.glob("jogo_*.partial.pgn"):
            timestamps.append(_checked_file(run, p).stat().st_mtime)
        attempts.append({"index": len(attempts) + 1, "folder": folder,
                         "status": metadata["status"], "updated_at": max(timestamps)})
    return attempts


def snapshot(directory: Path, *, match_id: str | None = None,
             attempt: int | None = None) -> dict:
    run = directory.expanduser().resolve(strict=True)
    if not run.is_dir():
        raise ValueError("Informe uma pasta de torneio ou confronto")
    tournament = None
    folder = None
    selected_match = None
    attempts = []
    selected_attempt = None
    if (run / "tournament.json").is_file():
        from .tournament import load_tournament, tournament_status
        state = load_tournament(run)
        tournament = tournament_status(run)
        matches = {m["id"]: m for rd in state["rounds"] for m in rd["matches"]}
        if match_id is not None and match_id not in matches:
            raise InvalidSelection("Confronto não pertence a este torneio")
        if match_id is None:
            folder, selected_match = _latest_duel(run, state)
        else:
            selected_match = match_id
        if selected_match is not None:
            attempts = _match_attempts(run, matches[selected_match])
            if attempt is not None:
                if attempt < 1 or attempt > len(attempts):
                    raise InvalidSelection("Tentativa inexistente neste confronto")
                selected_attempt = attempt
                folder = attempts[attempt - 1]["folder"]
            elif attempts:
                chosen = max(attempts, key=lambda x: (x["updated_at"], str(x["folder"])))
                selected_attempt = chosen["index"]
                folder = chosen["folder"]
        elif attempt is not None:
            raise InvalidSelection("Tentativa sem confronto")
    elif (run / "summary.json").is_file():
        if match_id is not None or attempt is not None:
            raise InvalidSelection("Seleção de confronto requer torneio")
        folder = run
    else:
        raise ValueError("Pasta não contém manifesto de torneio nem resumo de duelo")
    duel = _duel_snapshot(run, folder, match_id=selected_match) if folder else None
    public_attempts = [{k: a[k] for k in ("index", "status", "updated_at")}
                       for a in attempts]
    return {"schema": SCHEMA, "mode": "tournament" if tournament else "duel",
            "tournament": tournament, "duel": duel,
            "selection": {"match_id": selected_match, "attempt": selected_attempt},
            "attempts": public_attempts,
            "server_time": datetime.now(timezone.utc).isoformat()}


def _parse_selection(query: str) -> tuple[str | None, int | None]:
    try:
        params = parse_qs(query, keep_blank_values=True, max_num_fields=2)
    except ValueError as exc:
        raise InvalidSelection("Consulta inválida") from exc
    if (set(params) - {"match", "attempt"} or
            any(len(values) != 1 for values in params.values())):
        raise InvalidSelection("Consulta inválida")
    match_id = params.get("match", [None])[0]
    raw_attempt = params.get("attempt", [None])[0]
    if match_id is not None and not re.fullmatch(r"r[0-9]{2}-m[0-9]{2}", match_id):
        raise InvalidSelection("Identificador de confronto inválido")
    if raw_attempt is not None and (match_id is None or
            not re.fullmatch(r"[1-9][0-9]{0,4}", raw_attempt)):
        raise InvalidSelection("Número de tentativa inválido")
    return match_id, int(raw_attempt) if raw_attempt is not None else None


def make_handler(directory: Path):
    root = directory.expanduser().resolve(strict=True)

    class MonitorHandler(BaseHTTPRequestHandler):
        server_version = "ChessSimMonitor/1"
        sys_version = ""

        def _reply(self, status: int, content: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'self'; style-src 'self'; "
                             "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:
            host = self.headers.get("Host", "")
            permitted = {f"127.0.0.1:{self.server.server_port}",
                         f"localhost:{self.server.server_port}"}
            if host not in permitted:
                self._reply(403, b"Forbidden", "text/plain; charset=utf-8")
                return
            url = urlsplit(self.path)
            route = url.path
            if route == "/api/state":
                try:
                    match_id, attempt = _parse_selection(url.query)
                    payload = json.dumps(snapshot(root, match_id=match_id, attempt=attempt),
                                         ensure_ascii=False, allow_nan=False).encode("utf-8")
                except InvalidSelection:
                    self._reply(400, b'{"error":"Selecao de partida invalida"}',
                                "application/json; charset=utf-8")
                    return
                except (ValueError, FileNotFoundError, OSError, TypeError, KeyError,
                        json.JSONDecodeError):
                    payload = b'{"error":"Nao foi possivel ler o estado local da partida"}'
                    self._reply(503, payload, "application/json; charset=utf-8")
                    return
                self._reply(200, payload, "application/json; charset=utf-8")
                return
            if route in _ASSETS:
                filename, kind = _ASSETS[route]
                data = files("chesssim.data").joinpath(filename).read_bytes()
                self._reply(200, data, kind)
                return
            self._reply(404, b"Not Found", "text/plain; charset=utf-8")

        def do_POST(self) -> None:
            self._reply(405, b"Read-only interface", "text/plain; charset=utf-8")

        def log_message(self, format_string: str, *args) -> None:
            # Avoid printing potentially untrusted request paths into the terminal.
            pass

    return MonitorHandler


def create_server(directory: Path, port: int = 8765) -> ThreadingHTTPServer:
    if not 0 <= port <= 65535:
        raise ValueError("Porta deve estar entre 0 e 65535")
    snapshot(directory)  # fail fast before binding
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(directory))
    server.daemon_threads = True
    return server


def serve(directory: Path, port: int = 8765) -> None:
    server = create_server(directory, port)
    print(f"Tabuleiro ao vivo: http://127.0.0.1:{server.server_port}/", flush=True)
    print("Somente leitura, acesso local. Ctrl+C encerra o visualizador.", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
