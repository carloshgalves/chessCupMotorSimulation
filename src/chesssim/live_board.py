"""Read-only loopback monitor for tournament and duel PGN checkpoints.

All chess legality and FEN generation remain server-side via python-chess.
No external assets, engine control, or mutable HTTP endpoints.
"""
from __future__ import annotations

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit
import json
import re

import chess
import chess.pgn

MAX_PGN_BYTES = 2 * 1024 * 1024
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
        if seconds is not None:
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
    """Choose current RUNNING attempt, otherwise the most recently updated."""
    candidates = []
    for round_data in state.get("rounds", []):
        for match in round_data.get("matches", []):
            match_id = match["id"]
            a, b = match["players"]
            base = run / "matches" / match_id / "duels" / f"{a}-vs-{b}"
            for item in base.glob("*/summary.json"):
                try:
                    checked = _checked_file(run, item)
                    folder = checked.parent
                    data = json.loads(checked.read_text(encoding="utf-8"))
                    files_changed = [checked.stat().st_mtime]
                    for p in folder.glob("jogo_*.partial.pgn"):
                        files_changed.append(_checked_file(run, p).stat().st_mtime)
                    live = data.get("status") == "RUNNING"
                    candidates.append((int(live), max(files_changed), folder, match_id))
                except (ValueError, OSError, json.JSONDecodeError):
                    # A malformed attempt will be reported if selected directly.
                    continue
    if not candidates:
        return None, None
    _, _, folder, match_id = max(candidates, key=lambda row: (row[0], row[1]))
    return folder, match_id


def snapshot(directory: Path) -> dict:
    run = directory.expanduser().resolve(strict=True)
    if not run.is_dir():
        raise ValueError("Informe uma pasta de torneio ou confronto")
    match_id = None
    tournament = None
    if (run / "tournament.json").is_file():
        from .tournament import tournament_status
        tournament = tournament_status(run)
        folder, match_id = _latest_duel(run, json.loads(
            _checked_file(run, run / "tournament.json").read_text(encoding="utf-8")))
    elif (run / "summary.json").is_file():
        folder = run
    else:
        raise ValueError("Pasta não contém manifesto de torneio nem resumo de duelo")
    duel = _duel_snapshot(run, folder, match_id=match_id) if folder else None
    return {"schema": SCHEMA, "mode": "tournament" if tournament else "duel",
            "tournament": tournament, "duel": duel,
            "server_time": datetime.now(timezone.utc).isoformat()}


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
            route = urlsplit(self.path).path
            if route == "/api/state":
                try:
                    payload = json.dumps(snapshot(root), ensure_ascii=False,
                                         allow_nan=False).encode("utf-8")
                except (ValueError, FileNotFoundError, OSError, json.JSONDecodeError):
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
