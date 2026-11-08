"""Explicit opt-in local arena: one UCI duel at a time, protected POST controls.

The existing chesssim watch server stays read-only. No user-supplied paths,
shell commands or untrusted executable names are accepted by this service.
"""
from __future__ import annotations

from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
import json
import math
import re
import secrets
import threading
import uuid
from urllib.parse import urlsplit, parse_qs

from .catalog import load_roster, find_player
from .history_profiles import list_history_players, load_history_profile
from .history_games import InvalidCatalogue, UnknownGame, list_games, replay
from .history_stats import player_stats
from .representation import RepresentationLab, RepresentationInput
from .competition_rating import rating_report, RATING_POOLS
from .human_game import HumanGame, HumanInput, HumanBusy
from .leagues import LeagueManager, LeagueInput, LeagueBusy
from .cups import CupManager, CupBusy
from .cup_rules import CupInput, presets as cup_presets
from .live_board import SCHEMA, make_handler, snapshot
from .matches import _atomic_text, official_book_hashes, play_duel, validate
from .peak_windows import CLOCK_PRESETS, PROVISIONAL_PEAKS

MAX_JSON_BYTES = 4096
PRESETS = {"official": (600, 2), **CLOCK_PRESETS}
ALLOWED_KEYS = {"white", "black", "mode", "preset", "depth", "movetime",
                "reverse", "opening_mode", "seed", "max_plies",
                "initial_seconds", "increment_seconds"}
STATUSES = {"STARTING", "RUNNING", "COMPLETED", "INCOMPLETE", "FAILED", "INTERRUPTED"}


def _rating_query(query: str) -> str | None:
    if not query:
        return "rapid"
    try:
        params = parse_qs(query, strict_parsing=True, keep_blank_values=True)
    except ValueError:
        return None
    if set(params) != {"modality"} or len(params["modality"]) != 1:
        return None
    mode = params["modality"][0]
    return mode if mode in RATING_POOLS else None


class BadLaunch(ValueError):
    """Client-supplied launch configuration is invalid."""


class ArenaBusy(RuntimeError):
    """A previous match is still executing in this local service."""


def _integer(value: object, lower: int, upper: int, name: str) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise BadLaunch(f"{name} deve ser inteiro entre {lower} e {upper}")
    return value


def normalize_config(raw: object) -> dict:
    if not isinstance(raw, dict) or set(raw) - ALLOWED_KEYS:
        raise BadLaunch("Configuração desconhecida ou inválida")
    white = raw.get("white")
    black = raw.get("black")
    if not isinstance(white, str) or not isinstance(black, str):
        raise BadLaunch("Selecione dois jogadores")
    slugs = {p.slug for p in load_roster()}
    if white not in slugs or black not in slugs or white == black:
        raise BadLaunch("Jogadores diferentes e cadastrados são obrigatórios")
    mode = raw.get("mode")
    if mode not in ("depth", "movetime", "preset"):
        raise BadLaunch("Limite de busca inválido")
    reverse = raw.get("reverse", True)
    if type(reverse) is not bool:
        raise BadLaunch("Inversão de cores deve ser booleana")
    opening = raw.get("opening_mode", "none")
    if opening not in ("none", "internal", "historical"):
        raise BadLaunch("Modo de abertura inválido")
    max_plies = _integer(raw.get("max_plies", 400 if mode != "preset" else 600),
                         40, 600, "max_plies")
    result = {"white": white, "black": black, "mode": mode, "reverse": reverse,
              "opening_mode": opening, "max_plies": max_plies,
              "preset": None, "depth": None, "movetime": None, "seed": None}
    if mode == "depth":
        if "movetime" in raw:
            raise BadLaunch("Escolha profundidade ou tempo por lance, não ambos")
        result["depth"] = _integer(raw.get("depth", 6), 1, 16, "depth")
    elif mode == "movetime":
        if ("depth" in raw or "preset" in raw or "initial_seconds" in raw
                or "increment_seconds" in raw):
            raise BadLaunch("Tempo fixo por lance não pode usar relógio simultâneo")
        seconds = raw.get("movetime")
        if (type(seconds) not in (float, int) or not math.isfinite(seconds)
                or not 0.05 <= seconds <= 10):
            raise BadLaunch("movetime deve estar entre 0,05 e 10 segundos")
        result["movetime"] = float(seconds)
    else:
        # Legacy clock-only mode: official and past sessions keep their contract.
        if "depth" in raw or "movetime" in raw:
            raise BadLaunch("Protocolo de relógio exclusivo não aceita limite de busca")
    preset = raw.get("preset")
    if mode == "preset" and preset is None:
        raise BadLaunch("Informe um ritmo de relógio")
    if preset is not None:
        if (mode == "movetime" or not isinstance(preset, str)
                or preset not in (set(PRESETS) | {"custom"})):
            raise BadLaunch("Ritmo de relógio inválido")
        if not reverse:
            raise BadLaunch("Confrontos com relógio exigem ambas as cores")
        if preset == "official":
            if mode != "preset":
                raise BadLaunch("Protocolo official não aceita profundidade adicional")
            if opening != "internal":
                raise BadLaunch("Protocolo official exige livro UCI interno")
            if max_plies != 600:
                raise BadLaunch("Protocolo official exige limite de segurança 600")
            if "seed" in raw:
                raise BadLaunch("Semente não integra o protocolo official")
        result["preset"] = preset
        if preset == "custom":
            result["initial_seconds"] = _integer(
                raw.get("initial_seconds"), 60, 14400, "initial_seconds")
            result["increment_seconds"] = _integer(
                raw.get("increment_seconds"), 0, 120, "increment_seconds")
        elif "initial_seconds" in raw or "increment_seconds" in raw:
            raise BadLaunch("Relógio personalizado exige preset custom")
    elif "initial_seconds" in raw or "increment_seconds" in raw:
        raise BadLaunch("Relógio personalizado exige preset custom")
    if opening == "historical":
        if result["preset"] == "official":
            raise BadLaunch("Abertura externa não pertence ao protocolo official")
        result["seed"] = _integer(raw.get("seed"), 0, 2_147_483_647, "seed")
    elif "seed" in raw:
        raise BadLaunch("Semente só é efetiva com abertura externa histórica")
    return result


def _profiles(root: Path) -> list[dict]:
    players = []
    for p in load_roster():
        available = False
        if p.rodent_alias:
            try:
                validate(root, p, p)
                available = True
            except (FileNotFoundError, ValueError, OSError):
                pass
        period = p.peak_period
        hypothesis = PROVISIONAL_PEAKS.get(p.slug)
        if period:
            window = period
            window_status = "CATALOG_REFERENCE_UNVERIFIED"
            justification = "Metadado do catálogo; intervalo não comprova fidelidade do motor."
        elif hypothesis:
            window = f"{hypothesis[0]}–{hypothesis[1]}"
            window_status = "RESEARCH_HYPOTHESIS"
            justification = hypothesis[2]
        else:
            window = None
            window_status = "UNREVIEWED"
            justification = "Sem janela histórica aprovada no catálogo."
        players.append({
            "slug": p.slug, "name": p.name, "group": p.group,
            "alias": p.rodent_alias, "installed": available,
            "model_status": ("RODENT_PROFILE_AVAILABLE_NOT_VALIDATED" if available else
                             "PROFILE_UNAVAILABLE"),
            "historically_validated": False,
            "reference_period": window,
            "reference_status": window_status,
            "reference_note": justification,
            "provenance": ("Alias oficial reconhecido no registro Rodent IV"
                           if p.rodent_alias else "Elenco histórico sem perfil Rodent registrado"),
        })
    return players


def _validated_books(book_dir: Path | None, white: str, black: str) -> dict:
    if book_dir is None:
        raise BadLaunch("Pasta de repertórios históricos não configurada na CLI")
    from .opening_books import load_opening_book
    selected = {}
    for slug in (white, black):
        path = book_dir / f"{slug}.json"
        if not path.is_file() or path.is_symlink():
            raise BadLaunch(f"Repertório não disponível para {slug}")
        if path.stat().st_size > 5 * 1024 * 1024:
            raise BadLaunch(f"Repertório excede limite para {slug}")
        try:
            book = load_opening_book(path, slug)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise BadLaunch(f"Repertório inválido para {slug}") from exc
        selected[slug] = book
    return selected


def _load_json_file(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
        raise ValueError("Manifesto de sessão inválido")
    result = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(result, dict) or
            result.get("schema") != "chesssim-arena-session-v1" or
            result.get("id") != path.parent.name or
            result.get("status") not in STATUSES or
            not isinstance(result.get("config"), dict)):
        raise ValueError("Manifesto de sessão incompatível")
    return result


class ArenaManager:
    def __init__(self, root: Path, workspace: Path, *,
                 opening_books: Path | None = None,
                 max_game_wall_seconds: float = 3600,
                 runner=None):
        self.root = root.expanduser().resolve(strict=True)
        self.workspace = workspace.expanduser().absolute()
        if self.workspace.is_symlink():
            raise ValueError("A pasta da arena não pode ser um link simbólico")
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.workspace = self.workspace.resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("Saída da arena inválida")
        if opening_books is not None:
            if opening_books.is_symlink() or not opening_books.is_dir():
                raise ValueError("Diretório de livros históricos inválido")
            opening_books = opening_books.resolve(strict=True)
        self.opening_books = opening_books
        if not isinstance(max_game_wall_seconds, (float, int)) or (
                not math.isfinite(max_game_wall_seconds) or
                not 30 <= max_game_wall_seconds <= 86400):
            raise ValueError("Limite de duração deve estar entre 30 e 86400 segundos")
        self.max_game_wall_seconds = float(max_game_wall_seconds)
        self.runner = runner or play_duel
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._active = False
        self._thread: threading.Thread | None = None
        self._selected: Path | None = None
        candidates = sorted(self.workspace.glob("session-*/session.json"))
        if candidates:
            chosen = candidates[-1]
            if chosen.parent.parent.resolve() != self.workspace:
                raise ValueError("Sessão fora da arena")
            _load_json_file(chosen)
            self._selected = chosen.parent
        # Pré-voo da sessão concluído; só agora reter o bloqueio do workspace.
        # Erros de leitura anteriores não deixam a arena bloqueada.
        from .tournament import _lock
        self._process_lock = _lock(self.workspace)
        self._process_lock.__enter__()
        self._process_lock_closed = False
        try:
            self.human = HumanGame(self.root, self.workspace,
                                   max_wall_seconds=self.max_game_wall_seconds)
            try:
                self.leagues = LeagueManager(self.root, self.workspace)
                try:
                    self.cups = CupManager(self.root, self.workspace)
                    self.representation = RepresentationLab(self.root, self.workspace)
                except BaseException:
                    self.leagues.shutdown()
                    raise
            except BaseException:
                self.human.shutdown()
                raise
        except BaseException:
            self._process_lock_closed = True
            self._process_lock.__exit__(None, None, None)
            raise

    def _save(self, folder: Path, state: dict) -> None:
        _atomic_text(folder / "session.json",
                     json.dumps(state, ensure_ascii=False, indent=2) + "\n")

    def _status_unlocked(self) -> dict | None:
        if self._selected is None:
            return None
        state = _load_json_file(self._selected / "session.json")
        if state["status"] in ("STARTING", "RUNNING") and not self._active:
            state = {**state, "status": "INTERRUPTED"}
        folder = self._duel_folder()
        duel_summary = None
        if folder is not None and (folder / "summary.json").is_file():
            # Checkpoints created by our own engine are atomic; during a
            # third-party/legacy non-atomic write, an active duel may briefly
            # expose an incomplete file. Do not present it as a final result.
            try:
                summary = json.loads((folder / "summary.json").read_text(
                    encoding="utf-8"))
            except json.JSONDecodeError as exc:
                if not self._active:
                    raise ValueError("Resumo de confronto final inválido") from exc
                summary = None
            if summary is not None and not isinstance(summary, dict):
                if not self._active:
                    raise ValueError("Resumo de confronto final inválido")
                summary = None
            if summary is not None:
                games = summary.get("games", [])
                if not isinstance(games, list):
                    if not self._active:
                        raise ValueError("Lista final de partidas inválida")
                    games = []
                duel_summary = {
                    "status": summary.get("status", "UNKNOWN"),
                    "results": [g.get("result") for g in games
                                if isinstance(g, dict)],
                }
        return {"id": state["id"], "status": state["status"],
                "config": state["config"], "created_utc": state["created_utc"],
                "duel": duel_summary,
                "error": state.get("error_code")}

    def status(self) -> dict:
        with self._lock:
            selected = self._status_unlocked()
            return {"session": selected, "busy": self._active or self.human.busy or
                    self.leagues.busy or self.cups.busy,
                    "duel_busy": self._active, "human_busy": self.human.busy,
                    "league_busy": self.leagues.busy, "cup_busy": self.cups.busy,
                    "players": _profiles(self.root),
                    "presets": {name: {"initial_seconds": v[0], "increment_seconds": v[1]}
                                for name, v in PRESETS.items()},
                    "opening_books_configured": self.opening_books is not None}

    def _duel_folder(self) -> Path | None:
        if self._selected is None:
            return None
        root = self._selected / "duels"
        candidates = sorted(root.glob("*/*/summary.json"))
        if not candidates:
            return None
        path = candidates[-1]
        if path.is_symlink() or path.parent.is_symlink() or (
                path.parent.parent.is_symlink()):
            raise ValueError("Artefato redirecionado por link simbólico")
        if not path.resolve(strict=True).is_relative_to(self._selected.resolve()):
            raise ValueError("Artefato fora da sessão")
        return path.parent

    def state(self) -> dict:
        with self._lock:
            folder = self._duel_folder()
        if folder is None:
            return {"schema": SCHEMA, "mode": "duel", "tournament": None,
                    "duel": None, "server_time": datetime.now(timezone.utc).isoformat()}
        return snapshot(folder)

    def launch(self, raw: object) -> dict:
        config = normalize_config(raw)
        with self._lock:
            if self._active or self.human.busy or self.leagues.busy or self.cups.busy:
                raise ArenaBusy("Já existe confronto, partida, liga ou Copa em andamento")
            white, black = (find_player(config[x]) for x in ("white", "black"))
            try:
                _, files = validate(self.root, white, black)
                if config["opening_mode"] == "internal":
                    official_book_hashes(self.root, files)
                opening = (_validated_books(self.opening_books, white.slug, black.slug)
                           if config["opening_mode"] == "historical" else None)
            except (FileNotFoundError, OSError, ValueError) as exc:
                raise BadLaunch("Perfis ou livros do duelo não estão prontos") from exc
            ident = "session-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
            folder = self.workspace / ident
            folder.mkdir(mode=0o700, exist_ok=False)
            state = {
                "schema": "chesssim-arena-session-v1",
                "id": ident, "created_utc": datetime.now(timezone.utc).isoformat(),
                "status": "STARTING", "config": config,
            }
            self._save(folder, state)
            self._selected = folder
            self._active = True
            self._stop = threading.Event()
            thread = threading.Thread(
                target=self._execute, args=(folder, state, white, black, opening, self._stop),
                name="chesssim-arena-duel", daemon=False)
            self._thread = thread
            thread.start()
            return {"id": ident, "status": "STARTING"}

    def _execute(self, folder, session, white, black, opening, stop_event) -> None:
        config = session["config"]
        try:
            session["status"] = "RUNNING"
            self._save(folder, session)
            clock = ((config["initial_seconds"], config["increment_seconds"])
                     if config["preset"] == "custom" else
                     PRESETS[config["preset"]] if config["preset"] else None)
            kwargs = {
                "depth": config["depth"], "movetime": config["movetime"],
                "clock": clock, "reverse": config["reverse"],
                "max_plies": config["max_plies"],
                "use_books": config["opening_mode"] == "internal",
                "stop_check": stop_event.is_set,
                "max_wall_seconds": self.max_game_wall_seconds,
            }
            if opening is not None:
                kwargs.update({"historical_openings": opening,
                               "opening_seed": config["seed"], "opening_min_count": 1})
            output = self.runner(self.root, folder / "duels", white, black, **kwargs)
            if not output.resolve().is_relative_to(folder.resolve()):
                raise RuntimeError("Saída do duelo fora da sessão autorizada")
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            if summary.get("status") not in {"COMPLETED", "INCOMPLETE", "FAILED"}:
                raise RuntimeError("Estado final do duelo não reconhecido")
            session["status"] = summary["status"]
        except Exception as exc:
            # Keep details on the local server stderr, never expose path/stack to UI.
            print(f"Falha do confronto local: {type(exc).__name__}: {exc}", flush=True)
            session["status"] = "FAILED"
            session["error_code"] = "ENGINE_OR_ARTIFACT_FAILURE"
        finally:
            # Publish the terminal checkpoint and clear the busy flag as one
            # observable transition. Readers of status() use this same lock.
            with self._lock:
                try:
                    self._save(folder, session)
                finally:
                    self._active = False

    def human_start(self, raw: object) -> dict:
        with self._lock:
            return self.human.start(raw, duel_busy=self._active or self.leagues.busy or self.cups.busy)

    def human_resume(self, raw: object) -> dict:
        with self._lock:
            return self.human.resume(raw, duel_busy=self._active or self.leagues.busy or self.cups.busy)

    def league_run(self, ident: str) -> dict:
        with self._lock:
            return self.leagues.run(ident, arena_busy=self._active or self.human.busy or self.cups.busy)

    def cup_run(self, ident: str) -> dict:
        with self._lock:
            return self.cups.run(
                ident, arena_busy=self._active or self.human.busy or self.leagues.busy)

    def stop(self) -> dict:
        with self._lock:
            if not self._active:
                raise ArenaBusy("Não há confronto em execução")
            self._stop.set()
            return {"stop_requested": True}

    def shutdown(self) -> None:
        self.representation.shutdown()
        self.cups.shutdown()
        self.leagues.shutdown()
        self.human.shutdown()
        with self._lock:
            if self._active:
                self._stop.set()
            thread = self._thread
        try:
            if thread is not None:
                thread.join()
        finally:
            if not self._process_lock_closed:
                self._process_lock_closed = True
                self._process_lock.__exit__(None, None, None)


def _decode_json(body: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, val in items:
            if key in result:
                raise BadLaunch("Campo JSON duplicado")
            result[key] = val
        return result
    try:
        payload = json.loads(body.decode("utf-8"), object_pairs_hook=pairs)
    except BadLaunch:
        raise
    except (ValueError, UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise BadLaunch("Corpo JSON inválido") from exc
    if not isinstance(payload, dict):
        raise BadLaunch("Corpo deve ser um objeto JSON")
    return payload


def make_arena_handler(manager: ArenaManager, token: str):
    Base = make_handler(manager.workspace)

    class ArenaHandler(Base):
        server_version = "ChessSimArena/1"

        def _authorized_host(self) -> bool:
            host = self.headers.get("Host")
            return host in {f"127.0.0.1:{self.server.server_port}",
                            f"localhost:{self.server.server_port}"}

        def _json(self, status: int, payload: dict) -> None:
            self._reply(status, json.dumps(payload, ensure_ascii=False,
                                          allow_nan=False).encode("utf-8"),
                        "application/json; charset=utf-8")

        def do_GET(self) -> None:
            if not self._authorized_host():
                self._reply(403, b"Forbidden", "text/plain; charset=utf-8")
                return
            url = urlsplit(self.path)
            if url.path == "/api/cups/presets" and not url.query:
                self._json(200, cup_presets())
            elif url.path == "/api/cups" and not url.query:
                try:
                    self._json(200, manager.cups.list())
                except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError):
                    self._json(503, {"error": "Catálogo de Copas indisponível"})
            elif re.fullmatch(
                    r"/api/cups/cup-[0-9a-f]{16}/games/"
                    r"(?:r[0-9]{2}-m[0-9]{2}-s[0-5]-g[12]|w[0-9]{2}-g0[12])/replay",
                    url.path) and not url.query:
                parts=url.path.split("/")
                try:
                    self._json(200, manager.cups.replay(parts[3],parts[5]))
                except CupInput as exc:
                    self._json(404, {"error": str(exc)})
                except (ValueError, OSError, KeyError, TypeError,
                        json.JSONDecodeError):
                    self._json(503, {"error": "Replay da Copa indisponível"})
            elif url.path.startswith("/api/cups/") and not url.query:
                suffix=url.path.removeprefix("/api/cups/")
                export=re.fullmatch(r"(cup-[a-f0-9]{16})/export/(json|pgn)",suffix)
                try:
                    if export:
                        payload,kind=manager.cups.export(export.group(1),export.group(2))
                        self.send_response(200)
                        self.send_header("Content-Type",kind)
                        self.send_header("Content-Length",str(len(payload)))
                        self.send_header("Content-Disposition",
                                         'attachment; filename="chesssim-'+
                                         export.group(1)+'.'+export.group(2)+'"')
                        self.send_header("Cache-Control","no-store")
                        self.send_header("X-Content-Type-Options","nosniff")
                        self.end_headers()
                        self.wfile.write(payload)
                    elif re.fullmatch(r"cup-[a-f0-9]{16}",suffix):
                        self._json(200,manager.cups.detail(suffix))
                    else:
                        self._json(404,{"error":"Copa não encontrada"})
                except CupInput as exc:
                    self._json(404, {"error": str(exc)})
                except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                    self._json(503, {"error": "Dados da Copa inconsistentes"})
            elif url.path == "/api/leagues" and not url.query:
                try:
                    self._json(200, manager.leagues.list())
                except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                    self._json(503, {"error": "Catálogo de ligas indisponível"})
            elif re.fullmatch(r"/api/leagues/league-[a-f0-9]{16}/games", url.path):
                ident = url.path.split("/")[3]
                try:
                    qs = parse_qs(url.query, strict_parsing=True, keep_blank_values=True) if url.query else {}
                    if set(qs) - {"player", "offset", "limit"} or any(
                            len(items) != 1 for items in qs.values()):
                        raise LeagueInput("Filtro de partidas inválido")
                    player = qs.get("player", [None])[0]
                    offset = int(qs.get("offset", ["0"])[0])
                    limit = int(qs.get("limit", ["50"])[0])
                    self._json(200, manager.leagues.history(
                        ident, player=player, offset=offset, limit=limit))
                except (LeagueInput, ValueError):
                    self._json(400, {"error": "Filtro de partidas inválido"})
                except (OSError, KeyError, TypeError, json.JSONDecodeError):
                    self._json(503, {"error": "Histórico da liga indisponível"})
            elif re.fullmatch(r"/api/leagues/league-[a-f0-9]{16}/round/[0-9]{1,3}", url.path) and not url.query:
                try:
                    parts = url.path.split("/")
                    self._json(200, manager.leagues.round_detail(parts[3], int(parts[5])))
                except LeagueInput as exc:
                    self._json(404, {"error": str(exc)})
                except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                    self._json(503, {"error": "Rodada indisponível"})
            elif url.path.startswith("/api/leagues/") and not url.query:
                league_part = url.path.removeprefix("/api/leagues/")
                export = re.fullmatch(r"(league-[a-f0-9]{16})/export/(json|pgn)", league_part)
                try:
                    if export:
                        payload, kind = manager.leagues.export(export.group(1), export.group(2))
                        # The downloadable artifact is derived solely from the verified ledger.
                        self.send_response(200)
                        self.send_header("Content-Type", kind)
                        self.send_header("Content-Length", str(len(payload)))
                        self.send_header("Content-Disposition",
                                         'attachment; filename="chesssim-' +
                                         export.group(1) + '.' + export.group(2) + '"')
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.end_headers()
                        self.wfile.write(payload)
                    elif re.fullmatch(r"league-[a-f0-9]{16}", league_part):
                        self._json(200, manager.leagues.detail(league_part))
                    else:
                        self._json(404, {"error": "Liga não encontrada"})
                except LeagueInput as exc:
                    self._json(404, {"error": str(exc)})
                except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                    self._json(503, {"error": "Dados da temporada inconsistentes"})
            elif url.path == "/api/competition/ratings":
                modality = _rating_query(url.query)
                if modality is None:
                    self._json(404, {"error": "Modalidade desconhecida"})
                    return
                try:
                    self._json(200, rating_report(manager.workspace,
                                                  modality=modality))
                except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                    self._json(503, {"error": "Elo experimental indisponível"})
            elif url.path.startswith("/api/history/players/") and url.path.endswith("/rating"):
                slug = url.path.removeprefix("/api/history/players/").removesuffix("/rating")
                modality = _rating_query(url.query)
                if modality is None or not slug or "/" in slug:
                    self._json(404, {"error": "Jogador ou modalidade desconhecido"})
                    return
                if slug not in {p.slug for p in load_roster()}:
                    self._json(404, {"error": "Jogador não encontrado"})
                    return
                try:
                    report = rating_report(manager.workspace, modality=modality)
                    row = next(x for x in report["ratings"] if x["slug"] == slug)
                    report["ratings"] = [row]
                    report["slug"] = slug
                    self._json(200, report)
                except (OSError, ValueError, KeyError, TypeError, StopIteration,
                        json.JSONDecodeError):
                    self._json(503, {"error": "Elo experimental indisponível"})
            elif re.fullmatch(r"/api/history/representation/[a-z0-9-]{2,48}",url.path) and not url.query:
                slug=url.path.rsplit("/",1)[1]
                try:
                    self._json(200,manager.representation.status(slug))
                except RepresentationInput as exc:
                    self._json(404,{"error":str(exc)})
                except (OSError,ValueError,TypeError,KeyError,json.JSONDecodeError):
                    self._json(503,{"error":"Laboratório de representação indisponível"})
            elif url.path == "/api/history/players" and not url.query:
                try:
                    self._json(200, list_history_players())
                except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                    self._json(503, {"error": "Catálogo histórico indisponível"})
            elif url.path.startswith("/api/history/players/") and url.path.endswith("/stats"):
                slug = url.path.removeprefix("/api/history/players/").removesuffix("/stats")
                if url.query or not slug or "/" in slug:
                    self._json(404, {"error": "Jogador não encontrado"})
                    return
                try:
                    self._json(200, player_stats(manager.workspace, slug))
                except ValueError as exc:
                    if str(exc) in {"Slug inválido", "Jogador fora do elenco"}:
                        self._json(404, {"error": "Jogador não encontrado"})
                    else:
                        self._json(503, {"error": "Estatísticas locais indisponíveis"})
                except (OSError, KeyError, TypeError, json.JSONDecodeError):
                    self._json(503, {"error": "Estatísticas locais indisponíveis"})
            elif url.path.startswith("/api/history/players/") and url.path.endswith("/games"):
                slug = url.path.removeprefix("/api/history/players/").removesuffix("/games")
                if url.query or not slug or "/" in slug:
                    self._json(404, {"error": "Jogador não encontrado"})
                    return
                try:
                    self._json(200, list_games(manager.workspace, slug))
                except ValueError as exc:
                    if str(exc) in {"Slug inválido", "Jogador fora do elenco"}:
                        self._json(404, {"error": "Jogador não encontrado"})
                    else:
                        self._json(503, {"error": "Catálogo de partidas indisponível"})
                except (OSError, KeyError, TypeError, json.JSONDecodeError):
                    self._json(503, {"error": "Catálogo de partidas indisponível"})
            elif url.path.startswith("/api/history/games/") and url.path.endswith("/replay"):
                game_id = url.path.removeprefix("/api/history/games/").removesuffix("/replay")
                if url.query or not game_id or "/" in game_id:
                    self._json(404, {"error": "Partida não encontrada"})
                    return
                try:
                    self._json(200, replay(manager.workspace, game_id))
                except UnknownGame:
                    self._json(404, {"error": "Partida não encontrada"})
                except (InvalidCatalogue, OSError, KeyError, TypeError,
                        json.JSONDecodeError):
                    self._json(503, {"error": "Replay indisponível"})
            elif url.path.startswith("/api/history/players/"):
                slug = url.path.removeprefix("/api/history/players/")
                if url.query or not slug or "/" in slug:
                    self._json(404, {"error": "Jogador não encontrado"})
                    return
                try:
                    self._json(200, load_history_profile(slug))
                except ValueError as exc:
                    if str(exc) in {"Slug inválido", "Jogador fora do elenco"}:
                        self._json(404, {"error": "Jogador não encontrado"})
                    else:
                        self._json(503, {"error": "Perfil histórico indisponível"})
                except (OSError, KeyError, TypeError, json.JSONDecodeError):
                    self._json(503, {"error": "Perfil histórico indisponível"})
            elif url.path.startswith("/api/history/portraits/"):
                name = url.path.removeprefix("/api/history/portraits/")
                slug = name.removesuffix(".jpg")
                if url.query or not name.endswith(".jpg") or not slug or "/" in slug:
                    self._reply(404, b"Not Found", "text/plain; charset=utf-8")
                    return
                try:
                    profile = load_history_profile(slug)
                    if profile["portrait"] is None:
                        self._reply(404, b"Not Found", "text/plain; charset=utf-8")
                    else:
                        photo = files("chesssim.data").joinpath(
                            "history", "portraits", slug + ".jpg").read_bytes()
                        self._reply(200, photo, "image/jpeg")
                except (ValueError, OSError, KeyError):
                    self._reply(404, b"Not Found", "text/plain; charset=utf-8")
            elif url.path.startswith("/piece-assets/"):
                # Source-pinned SVG files, allowlisted by the read-only handler.
                super().do_GET()
            elif url.path == "/api/arena/human" and not url.query:
                try:
                    self._json(200, manager.human.state())
                except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError):
                    self._json(503, {"error": "Estado do modo Jogar indisponível"})
            elif url.path == "/api/arena":
                try:
                    self._json(200, manager.status())
                except (ValueError, OSError, KeyError, TypeError,
                        json.JSONDecodeError):
                    self._json(503, {"error": "Estado da arena indisponível"})
            elif url.path == "/api/state":
                try:
                    self._json(200, manager.state())
                except (ValueError, OSError, FileNotFoundError,
                        KeyError, TypeError, json.JSONDecodeError):
                    self._json(503, {"error": "Estado do duelo indisponível"})
            elif url.path in ("/", "/arena.js", "/arena.css", "/history.js", "/history_replay.js", "/history_stats.js", "/competition_rating.js", "/history.css", "/chess-board.js", "/chess-board.css", "/board-preferences.js", "/human-play.js", "/league.js", "/league.css",
                              "/cup.js", "/cup.css", "/representation.js", "/representation.css", "/viewer"):
                asset = {"/": "arena.html", "/arena.js": "arena.js",
                         "/arena.css": "arena.css", "/history.js": "history.js",
                         "/history_replay.js": "history_replay.js",
                         "/history_stats.js": "history_stats.js",
                         "/competition_rating.js": "competition_rating.js",
                         "/history.css": "history.css",
                         "/chess-board.js": "chess_board.js",
                         "/board-preferences.js": "board_preferences.js",
                         "/human-play.js": "human_play.js",
                         "/league.js": "league.js",
                         "/league.css": "league.css",
                         "/cup.js": "cup.js", "/cup.css": "cup.css",
                         "/representation.js": "representation.js",
                         "/representation.css": "representation.css",
                         "/chess-board.css": "chess_board.css", "/viewer": "monitor.html"}[url.path]
                kind = ("text/html; charset=utf-8" if asset.endswith(".html") else
                        "text/javascript; charset=utf-8" if asset.endswith(".js") else
                        "text/css; charset=utf-8")
                body = files("chesssim.data").joinpath(asset).read_bytes()
                if url.path == "/viewer":
                    # The read-only viewer can be embedded only by this same-origin arena.
                    self.send_response(200)
                    self.send_header("Content-Type", kind)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("X-Frame-Options", "SAMEORIGIN")
                    self.send_header("Content-Security-Policy",
                                     "default-src 'none'; script-src 'self'; style-src 'self'; "
                                     "connect-src 'self'; img-src 'self'; object-src 'none'; "
                                     "base-uri 'none'; frame-ancestors 'self'")
                    self.end_headers()
                    self.wfile.write(body)
                elif url.path == "/":
                    # Only the opt-in launcher embeds its own read-only local viewer.
                    self.send_response(200)
                    self.send_header("Content-Type", kind)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("X-Frame-Options", "DENY")
                    self.send_header("Content-Security-Policy",
                                     "default-src 'none'; script-src 'self'; style-src 'self'; "
                                     "connect-src 'self'; img-src 'self'; frame-src 'self'; form-action 'none'; "
                                     "base-uri 'none'; frame-ancestors 'none'")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._reply(200, body, kind)
            else:
                super().do_GET()  # /app.js, /app.css and nothing else

        def do_POST(self) -> None:
            if not self._authorized_host():
                self._json(403, {"error": "Host não autorizado"})
                return
            route = urlsplit(self.path)
            league_action = re.fullmatch(
                r"/api/leagues/(league-[a-f0-9]{16})/(run|pause)", route.path)
            cup_action = re.fullmatch(
                r"/api/cups/(cup-[a-f0-9]{16})/(run|pause)",route.path)
            representation_action = re.fullmatch(
                r"/api/history/representation/([a-z0-9-]{2,48})/run",route.path)
            if (route.path not in (
                "/api/arena/launch", "/api/arena/stop",
                "/api/arena/human/start", "/api/arena/human/move",
                "/api/arena/human/action", "/api/arena/human/resume",
                "/api/leagues/create", "/api/cups/create",
            ) and league_action is None and cup_action is None and
                    representation_action is None) or route.query:
                self._json(404, {"error": "Rota desconhecida"})
                return
            origins = {f"http://127.0.0.1:{self.server.server_port}",
                       f"http://localhost:{self.server.server_port}"}
            if (self.headers.get("Origin") not in origins or
                    not secrets.compare_digest(self.headers.get("X-Chesssim-Token", ""), token)):
                self._json(403, {"error": "Acesso não autorizado"})
                return
            if (self.headers.get("Content-Type", "").split(";")[0] != "application/json"
                    or self.headers.get("Transfer-Encoding")):
                self._json(415, {"error": "Apenas JSON local é permitido"})
                return
            try:
                size = int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                size = -1
            if not 0 <= size <= MAX_JSON_BYTES:
                self._json(413, {"error": "Corpo excede limite"})
                return
            body = self.rfile.read(size)
            if len(body) != size:
                self._json(400, {"error": "Corpo incompleto"})
                return
            try:
                data = _decode_json(body)
                if representation_action:
                    self._json(202,manager.representation.start(
                        representation_action.group(1),data,
                        simulator_busy=bool(manager.status()["busy"])))
                elif route.path == "/api/cups/create":
                    self._json(201, manager.cups.create(data))
                elif cup_action:
                    if data:
                        raise CupInput("Ação da Copa não recebe parâmetros")
                    if cup_action.group(2)=="run":
                        self._json(202,manager.cup_run(cup_action.group(1)))
                    else:
                        self._json(202,manager.cups.pause(cup_action.group(1)))
                elif route.path == "/api/leagues/create":
                    self._json(201, manager.leagues.create(data))
                elif league_action:
                    if data:
                        raise LeagueInput("Ação da liga não aceita parâmetros")
                    if league_action.group(2) == "run":
                        self._json(202, manager.league_run(league_action.group(1)))
                    else:
                        self._json(202, manager.leagues.pause(league_action.group(1)))
                elif route.path == "/api/arena/launch":
                    self._json(202, manager.launch(data))
                elif route.path == "/api/arena/stop":
                    if data:
                        raise BadLaunch("Parada não aceita parâmetros")
                    self._json(202, manager.stop())
                elif route.path == "/api/arena/human/start":
                    self._json(201, manager.human_start(data))
                elif route.path == "/api/arena/human/move":
                    self._json(200, manager.human.move(data))
                elif route.path == "/api/arena/human/action":
                    self._json(200, manager.human.action(data))
                else:
                    self._json(200, manager.human_resume(data))
            except (BadLaunch, HumanInput, LeagueInput, CupInput, RepresentationInput) as exc:
                self._json(400, {"error": str(exc)})
            except (ArenaBusy, HumanBusy, LeagueBusy, CupBusy) as exc:
                self._json(409, {"error": str(exc)})
            except (OSError, RuntimeError, ValueError) as exc:
                print(f"Falha operacional local: {type(exc).__name__}: {exc}",
                      flush=True)
                self._json(503, {"error": "Não foi possível iniciar o motor local"})

    return ArenaHandler


def create_server(root: Path, workspace: Path, *, port: int = 8766,
                  opening_books: Path | None = None,
                  max_game_wall_seconds: float = 3600,
                  runner=None):
    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("Porta inválida")
    manager = ArenaManager(root, workspace, opening_books=opening_books,
                           max_game_wall_seconds=max_game_wall_seconds,
                           runner=runner)
    token = secrets.token_urlsafe(32)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port),
                                     make_arena_handler(manager, token))
    except BaseException:
        manager.shutdown()
        raise
    server.daemon_threads = True
    server.arena_manager = manager
    server.arena_token = token
    return server


def serve(root: Path, workspace: Path, *, port: int = 8766,
          opening_books: Path | None = None,
          max_game_wall_seconds: float = 3600) -> None:
    server = create_server(root, workspace, port=port,
                           opening_books=opening_books,
                           max_game_wall_seconds=max_game_wall_seconds)
    print(f"Arena local: http://127.0.0.1:{server.server_port}/#token={server.arena_token}",
          flush=True)
    print("Token local: não compartilhe o endereço completo. Ctrl+C solicita parada.", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.arena_manager.shutdown()
