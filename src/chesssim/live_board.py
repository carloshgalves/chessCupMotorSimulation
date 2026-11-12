"""Read-only loopback monitor for tournament and duel PGN checkpoints.

All chess legality and FEN generation remain server-side via python-chess.
No external assets, engine control, or mutable HTTP endpoints.
"""
from __future__ import annotations

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from threading import BoundedSemaphore
from urllib.parse import parse_qs, urlsplit
import json
import re

import chess
import chess.pgn

from .fictional_commentary import editorial_for_game
from .event_journal import EventJournal
from .live_events import MAX_EVENT_CLIENTS, same_origin, stream_events, stream_events_v2
from .quality_overlay import QualityOverlay

MAX_PGN_BYTES = 2 * 1024 * 1024
class InvalidSelection(ValueError):
    """Client-selected match or attempt is not part of the tournament."""


SCHEMA = "chesssim-live-board-v1"
_PIECE_PATH = re.compile(
    r"/piece-assets/(cburnett|merida|chessnut|fantasy|spatial|celtic|rhosgfx)/"
    r"([wb][KQRBNP])\.svg\Z"
)
_ASSETS = {"/": ("monitor.html", "text/html; charset=utf-8"),
           "/app.js": ("monitor.js", "text/javascript; charset=utf-8"),
           "/chess-board.js": ("chess_board.js", "text/javascript; charset=utf-8"),
           "/board-preferences.js": ("board_preferences.js", "text/javascript; charset=utf-8"),
           "/chess-board.css": ("chess_board.css", "text/css; charset=utf-8"),
           "/app.css": ("monitor.css", "text/css; charset=utf-8")}


def _checked_file(run: Path, path: Path) -> Path:
    """Never traverse an external symlink while serving a selected run."""
    target = path.resolve(strict=True)
    if not target.is_relative_to(run.resolve(strict=True)):
        raise ValueError("Arquivo de partida fora do diretório monitorado")
    # Links internos também poderiam fazer um confronto exibir um PGN de outro.
    if target != path.absolute():
        raise ValueError("Link simbólico não permitido em artefatos da Copa")
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
               "turn": "white", "clocks": clocks.copy(), "uci": None}]
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
                       "clocks": clocks.copy(), "uci": uci})
    return states


def _duel_snapshot(run: Path, folder: Path, *, match_id: str | None = None,
                   quality_overlay: QualityOverlay | None = None) -> dict:
    summary_path = _checked_file(run, folder / "summary.json")
    if summary_path.stat().st_size > 256 * 1024:
        raise ValueError("Resumo de confronto excedeu limite de visualização")
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
        quality_stats = None
        if quality_overlay is not None and final.exists():
            mapped = quality_overlay.for_game(source, states)
            for ply, analysis in mapped.items():
                states[ply]["evaluation"] = analysis
            # The provenance has already been matched to the final PGN.
            # Summaries require a matching COMPLETED receipt and PGN result.
            if mapped and metadata.get("status") == "COMPLETED":
                # The receipt must describe this exact PGN trajectory, not
                # merely claim COMPLETED for a different game attempt.
                from .game_diversity import mainline_identity
                trajectory_sha, observed_plies = mainline_identity(game)
                if (type(metadata.get("plies")) is int and
                        metadata["plies"] == observed_plies and
                        metadata.get("mainline_sha256") == trajectory_sha):
                    quality_stats = quality_overlay.stats_for_game(
                        mapped, headers=dict(game.headers), metadata=metadata,
                        total_plies=len(states) - 1)
        if metadata.get("termination") == "TimeForfeit":
            final_clocks = metadata.get("clock_left")
            if (isinstance(final_clocks, list) and len(final_clocks) == 2 and
                    all(isinstance(c, (int, float)) for c in final_clocks)):
                # No timed-out move is added to PGN; show the final official clock.
                states[-1]["clocks"] = [max(0.0, float(c)) for c in final_clocks]
        is_live = (not final.exists() and summary.get("status") == "RUNNING")
        if is_live:
            # Legal candidates only for the current live position, not the
            # entire PGN. This is a read-only observer contract.
            board = chess.Board(states[-1]["fen"])
            states[-1]["legal_moves"] = [m.uci() for m in board.legal_moves]
        games.append({
            "id": f"{folder.name}:game-{index}",
            "number": index,
            "white": game.headers.get("White", "Brancas"),
            "black": game.headers.get("Black", "Pretas"),
            "event": game.headers.get("Event", "Copa dos Imortais"),
            "result": game.headers.get("Result", "*"),
            "termination": game.headers.get("Termination"),
            "status": metadata.get("status", "RUNNING" if not final.exists() else "UNKNOWN"),
            "live": is_live,
            "updated_at": updated,
            "states": states,
        })
        if quality_stats is not None:
            games[-1]["quality_stats"] = quality_stats
        # Editorial data is read-only and derived exclusively from legal PGN
        # moves. Postgame text is gated independently by completed receipt.
        games[-1]["fictional_editorial"] = editorial_for_game(
            game, metadata=metadata, final_file=final.exists())
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
             attempt: int | None = None,
             quality_overlay: QualityOverlay | None = None) -> dict:
    run = directory.expanduser().resolve(strict=True)
    if not run.is_dir():
        raise ValueError("Informe uma pasta de torneio ou confronto")
    tournament = None
    folder = None
    if attempt is not None and match_id is None:
        raise InvalidSelection("Tentativa sem confronto")
    selected_match = None
    attempts = []
    selected_attempt = None
    if (run / "tournament.json").is_file():
        from .tournament import STOP_FILE, _summary, load_tournament
        state = load_tournament(run)
        # Uma única leitura atômica do manifesto evita mistura entre rodadas.
        tournament = _summary(state)
        tournament["stop_requested"] = (run / STOP_FILE).exists()
        from .catalog import find_player
        tournament["names"] = {slug: find_player(slug).name
                               for slug in state["participants"]}
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
    duel = (_duel_snapshot(run, folder, match_id=selected_match,
                           quality_overlay=quality_overlay) if folder else None)
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


def make_handler(directory: Path, quality_overlay: QualityOverlay | None = None):
    root = directory.expanduser().resolve(strict=True)

    class MonitorHandler(BaseHTTPRequestHandler):
        server_version = "ChessSimMonitor/1"
        sys_version = ""

        def _reply(self, status: int, content: bytes, kind: str,
                   cache_control: str = "no-store") -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", cache_control)
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'self'; style-src 'self'; "
                             "connect-src 'self'; img-src 'self'; object-src 'none'; "
                             "base-uri 'none'; frame-ancestors 'none'")
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
            if route in ("/api/state", "/api/events", "/api/events/v2"):
                # Reject cross-site streams BEFORE reading any PGN or bracket.
                if route in ("/api/events", "/api/events/v2") and not same_origin(self):
                    self._reply(403, b"Forbidden", "text/plain; charset=utf-8")
                    return
                try:
                    match_id, attempt = _parse_selection(url.query)
                    report = snapshot(root, match_id=match_id, attempt=attempt,
                                      quality_overlay=quality_overlay)
                    if route == "/api/state":
                        payload = json.dumps(report, ensure_ascii=False,
                                             allow_nan=False).encode("utf-8")
                except InvalidSelection:
                    self._reply(400, b'{"error":"Selecao de partida invalida"}',
                                "application/json; charset=utf-8")
                    return
                except (ValueError, FileNotFoundError, OSError, TypeError, KeyError,
                        json.JSONDecodeError):
                    payload = b'{"error":"Nao foi possivel ler o estado local da partida"}'
                    self._reply(503, payload, "application/json; charset=utf-8")
                    return
                if route == "/api/events":
                    stream_events(self, report,
                        lambda: snapshot(root, match_id=match_id, attempt=attempt,
                                         quality_overlay=quality_overlay))
                elif route == "/api/events/v2":
                    key = f"watch:{match_id or 'live'}:{attempt or 'latest'}"
                    stream_events_v2(
                        self, report,
                        lambda: snapshot(root, match_id=match_id, attempt=attempt,
                                         quality_overlay=quality_overlay),
                        key)
                else:
                    self._reply(200, payload, "application/json; charset=utf-8")
                return
            if route.startswith("/piece-assets/"):
                match = _PIECE_PATH.fullmatch(route)
                if not match or url.query:
                    self._reply(404, b"Not Found", "text/plain; charset=utf-8")
                    return
                name, piece = match.groups()
                try:
                    svg = files("chesssim.data").joinpath(
                        "pieces", name, piece + ".svg").read_bytes()
                except (FileNotFoundError, OSError):
                    self._reply(404, b"Not Found", "text/plain; charset=utf-8")
                    return
                # SHA-pinned, public artwork can be cached privately by browsers.
                # Sessions, PGNs, tokens and HTML still use no-store.
                self._reply(200, svg, "image/svg+xml",
                            "private, max-age=86400, immutable")
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


def create_server(directory: Path, port: int = 8765, *,
                  quality_report: Path | None = None) -> ThreadingHTTPServer:
    if not 0 <= port <= 65535:
        raise ValueError("Porta deve estar entre 0 e 65535")
    overlay = QualityOverlay(quality_report) if quality_report is not None else None
    snapshot(directory, quality_overlay=overlay)  # fail fast before binding
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(directory, overlay))
    server.event_stream_slots = BoundedSemaphore(MAX_EVENT_CLIENTS)
    server.event_journal = EventJournal()
    server.daemon_threads = True
    return server


def serve(directory: Path, port: int = 8765, *,
          quality_report: Path | None = None) -> None:
    server = create_server(directory, port, quality_report=quality_report)
    print(f"Tabuleiro ao vivo: http://127.0.0.1:{server.server_port}/", flush=True)
    print("Somente leitura, acesso local. Ctrl+C encerra o visualizador.", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
