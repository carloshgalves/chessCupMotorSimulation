"""Local human versus installed UCI profile. Server-owned turns, clocks and PGN.

No browser-supplied file paths, executables, personalities or arbitrary FENs.
The process-wide Arena workspace lock is held by ArenaManager. One game at a time.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import RLock, Thread
from time import monotonic
import json
import math
import re
import secrets
import uuid

import chess
import chess.engine
import chess.pgn

from .catalog import find_player, load_roster
from .matches import _atomic_text, _open_engine, file_hash, validate, official_book_hashes
from .peak_windows import CLOCK_PRESETS

SCHEMA = "chesssim-human-game-v1"
VALID_STATES = {"RUNNING", "COMPLETED", "INCOMPLETE", "INTERRUPTED", "FAILED"}
CLOCKS = {"none": None, **CLOCK_PRESETS}
# The "official" arena clock is special to motor vs motor; human play has
# explicit, independent clock presets and no misleading official claim.
CLOCKS["official"] = (600, 2)
MOVE_RX = re.compile(r"[a-h][1-8][a-h][1-8][qrbn]?\Z")
IDENT_RX = re.compile(r"human-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}\Z")


class HumanInput(ValueError):
    """Invalid, stale or illegal command from a browser."""


class HumanBusy(RuntimeError):
    """Cannot run two games concurrently."""


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_human(raw: object) -> dict:
    if not isinstance(raw, dict) or set(raw) - {
        "opponent", "color", "depth", "preset", "initial_seconds",
        "increment_seconds", "opening_mode",
    }:
        raise HumanInput("Configuração de partida desconhecida")
    opponent = raw.get("opponent")
    if type(opponent) is not str or opponent not in {p.slug for p in load_roster()}:
        raise HumanInput("Adversário fora do catálogo")
    color = raw.get("color")
    if color not in ("white", "black", "random"):
        raise HumanInput("Escolha peças brancas, pretas ou aleatórias")
    depth = raw.get("depth")
    if type(depth) is not int or not 1 <= depth <= 16:
        raise HumanInput("Profundidade máxima deve estar entre 1 e 16")
    preset = raw.get("preset")
    if type(preset) is not str or preset not in {*CLOCKS, "custom"}:
        raise HumanInput("Relógio da partida inválido")
    opening = raw.get("opening_mode", "none")
    if opening not in ("none", "internal"):
        raise HumanInput("Somente abertura desligada ou livro UCI interno")
    if preset == "custom":
        initial = raw.get("initial_seconds")
        inc = raw.get("increment_seconds")
        if (type(initial) is not int or not 60 <= initial <= 14400 or
                type(inc) is not int or not 0 <= inc <= 120):
            raise HumanInput("Relógio personalizado inválido")
        clock = (initial, inc)
    else:
        if "initial_seconds" in raw or "increment_seconds" in raw:
            raise HumanInput("Relógio personalizado exige preset custom")
        clock = CLOCKS[preset]
    return {
        "opponent": opponent,
        "color": ("white" if secrets.randbelow(2) == 0 else "black")
        if color == "random" else color,
        "requested_color": color,
        "depth": depth, "preset": preset, "clock": list(clock) if clock else None,
        "opening_mode": opening,
    }


class HumanGame:
    def __init__(self, root: Path, workspace: Path, *,
                 max_wall_seconds: float = 3600, engine_factory=None):
        self.root = root
        self.workspace = workspace
        self.max_wall_seconds = max_wall_seconds
        self.engine_factory = engine_factory or _open_engine
        self._lock = RLock()
        self._data: dict | None = None
        self._folder: Path | None = None
        self._game: chess.pgn.Game | None = None
        self._board: chess.Board | None = None
        self._node: chess.pgn.GameNode | None = None
        self._turn_started = monotonic()
        self._session_started = monotonic()
        self._thread: Thread | None = None
        self._engine = None
        self._closed = False
        self._epoch = 0
        self._wall_budget = self.max_wall_seconds
        self._load_last()

    @property
    def busy(self) -> bool:
        with self._lock:
            return bool(self._data and self._data["status"] == "RUNNING")

    def _load_last(self) -> None:
        parent = self.workspace / "human-games"
        if not parent.exists():
            return
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError("Pasta de partidas humanas inválida")
        candidates = sorted(parent.glob("human-*/state.json"))
        if not candidates:
            return
        path = candidates[-1]
        if (path.is_symlink() or path.parent.is_symlink() or
                not IDENT_RX.fullmatch(path.parent.name) or
                path.stat().st_size > 32768):
            raise ValueError("Registro humano incompatível")
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("schema") != SCHEMA or
                data.get("id") != path.parent.name or
                data.get("status") not in VALID_STATES or
                type(data.get("revision")) is not int):
            raise ValueError("Estado de jogo humano inválido")
        config = data.get("config")
        clocks = data.get("clocks")
        configured_clock = config.get("clock") if isinstance(config, dict) else None
        hashes = data.get("hashes")
        if (not isinstance(config, dict) or
                config.get("color") not in ("white", "black") or
                config.get("opponent") not in {p.slug for p in load_roster()} or
                config.get("opening_mode") not in ("none", "internal") or
                config.get("preset") not in (*CLOCKS, "custom") or
                type(config.get("depth")) is not int or
                not 1 <= config["depth"] <= 16 or
                not isinstance(hashes, dict) or
                any(not isinstance(hashes.get(k), str) or
                    not re.fullmatch(r"[0-9a-f]{64}", hashes[k])
                    for k in ("engine", "profile")) or
                ((configured_clock is None) != (clocks is None)) or
                (configured_clock is not None and
                 (not isinstance(configured_clock, list) or
                  len(configured_clock) != 2 or
                  type(configured_clock[0]) is not int or
                  type(configured_clock[1]) is not int or
                  not 60 <= configured_clock[0] <= 14400 or
                  not 0 <= configured_clock[1] <= 120)) or
                (clocks is not None and
                 (not isinstance(clocks, list) or len(clocks) != 2 or
                  any(type(c) not in (int, float) or
                      not math.isfinite(c) or c < 0 or c > 86400
                      for c in clocks)))):
            raise ValueError("Checkpoint humano contém valores inválidos")
        pgn = path.parent / "game.pgn"
        if pgn.is_symlink() or not pgn.is_file() or pgn.stat().st_size > 1024 * 1024:
            raise ValueError("PGN humano inválido")
        with pgn.open(encoding="utf-8") as stream:
            game = chess.pgn.read_game(stream)
            if game is None or game.errors or chess.pgn.read_game(stream) is not None:
                raise ValueError("PGN humano corrompido")
        self._folder, self._data, self._game = path.parent, data, game
        self._board, self._node = game.end().board(), game.end()
        if data["revision"] != self._board.ply():
            # PGN was published while manifest was not. Clock/turn metadata
            # are no longer provable; never grant an unsafe resume.
            self._data["status"] = "FAILED"
            self._data["revision"] = self._board.ply()
            self._data["result"] = "*"
            self._data["termination"] = "CheckpointMismatch"
            self._game.headers["Result"] = "*"
            self._game.headers["Termination"] = "CheckpointMismatch"
            self._save_locked()
        elif data["status"] == "RUNNING":
            self._data["status"] = "INTERRUPTED"
            self._data["termination"] = "ServerRestart"
            self._save_locked()

    def _save_locked(self) -> None:
        if self._folder is None or self._data is None or self._game is None:
            return
        self._folder.mkdir(parents=True, exist_ok=True)
        # PGN is published before the state manifest, so a crash can only
        # produce an explicit interrupted recovery, never a silent false result.
        _atomic_text(self._folder / "game.pgn", str(self._game) + "\n")
        _atomic_text(self._folder / "state.json",
                     json.dumps(self._data, ensure_ascii=False,
                                allow_nan=False, indent=2) + "\n")

    def _finish_locked(self, result: str, termination: str, status="COMPLETED") -> None:
        self._data["status"] = status
        self._data["result"] = result
        self._data["termination"] = termination
        self._data["finished_utc"] = _utc()
        self._data["updated_utc"] = _utc()
        self._game.headers["Result"] = result
        self._game.headers["Termination"] = termination
        self._save_locked()

    def _tick_locked(self) -> None:
        if self._data is None or self._data["status"] != "RUNNING":
            return
        now = monotonic()
        elapsed = max(0.0, now - self._turn_started)
        self._turn_started = now
        if now - self._session_started > self._wall_budget:
            self._finish_locked("*", "WallTimeLimit", "INCOMPLETE")
            return
        clocks = self._data["clocks"]
        if clocks is None:
            return
        turn = 0 if self._board.turn else 1
        clocks[turn] = max(0.0, clocks[turn] - elapsed)
        if clocks[turn] <= 0:
            winner_has_material = not self._board.has_insufficient_material(
                not self._board.turn)
            result = ("0-1" if turn == 0 else "1-0") if winner_has_material else "1/2-1/2"
            self._finish_locked(result, "TimeForfeit")

    def _append_locked(self, move: chess.Move) -> None:
        actor = 0 if self._board.turn else 1
        self._node = self._node.add_variation(move)
        if self._data["clocks"] is not None:
            self._data["clocks"][actor] += self._data["config"]["clock"][1]
            self._node.set_clock(self._data["clocks"][actor])
        self._board.push(move)
        self._data["revision"] = self._board.ply()
        self._data["updated_utc"] = _utc()
        # Threefold repetition and 50-move draws require a claim by the
        # player; fivefold/75-move draws are automatic per chess rules.
        outcome = self._board.outcome(claim_draw=False)
        if outcome:
            self._finish_locked(outcome.result(), outcome.termination.name)
        elif self._board.ply() >= 400:
            self._finish_locked("*", "PlyLimit", "INCOMPLETE")
        else:
            self._turn_started = monotonic()
            self._save_locked()

    def _is_human_turn_locked(self) -> bool:
        return (self._data["status"] == "RUNNING" and
                (self._board.turn == chess.WHITE) ==
                (self._data["config"]["color"] == "white"))

    def _is_engine_turn_locked(self) -> bool:
        return self._data["status"] == "RUNNING" and not self._is_human_turn_locked()

    def _spawn_locked(self) -> None:
        if self._closed or not self._is_engine_turn_locked():
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = Thread(target=self._engine_turn, name="chesssim-human-uci",
                              daemon=True)
        self._thread.start()

    def _engine_turn(self) -> None:
        session_id = None
        try:
            with self._lock:
                if not self._is_engine_turn_locked():
                    return
                session_id = self._data["id"]
                epoch = self._epoch
                revision = self._data["revision"]
                conf = self._data["config"].copy()
                board = self._board.copy(stack=True)
                game = self._game
                limits = self._data["clocks"]
                limit = chess.engine.Limit(
                    depth=conf["depth"],
                    white_clock=max(.001, limits[0]) if limits else None,
                    black_clock=max(.001, limits[1]) if limits else None,
                    white_inc=conf["clock"][1] if limits else None,
                    black_inc=conf["clock"][1] if limits else None)
            opponent = find_player(conf["opponent"])
            exe, _ = validate(self.root, opponent, opponent)
            engine = self.engine_factory(self.root, exe, opponent,
                                         book=conf["opening_mode"] == "internal")
            try:
                with self._lock:
                    current = (self._data["id"] == session_id and
                               self._epoch == epoch and
                               self._is_engine_turn_locked())
                    if current:
                        self._engine = engine
                if not current:
                    return
                proposed = engine.play(board, limit, game=game,
                                       info=chess.engine.INFO_NONE).move
            finally:
                try:
                    engine.quit()
                except Exception:
                    pass
                finally:
                    try:
                        engine.close()
                    finally:
                        with self._lock:
                            if self._engine is engine:
                                self._engine = None
            with self._lock:
                if (self._data["id"] != session_id or
                        self._epoch != epoch or
                        self._data["revision"] != revision or
                        not self._is_engine_turn_locked()):
                    return
                self._tick_locked()
                if self._data["status"] != "RUNNING":
                    return
                if proposed is None or proposed not in self._board.legal_moves:
                    self._finish_locked("*", "IllegalEngineMove", "FAILED")
                    return
                self._append_locked(proposed)
        except Exception as exc:
            print("Falha no motor do modo Jogar: " + type(exc).__name__ + ": " +
                  str(exc), flush=True)
            with self._lock:
                if (self._data and self._data["status"] == "RUNNING" and
                        self._data["id"] == session_id and
                        self._epoch == epoch):
                    self._finish_locked("*", "EngineFailure", "FAILED")
        finally:
            with self._lock:
                self._thread = None
                # A user may have moved while the previous worker was closing.
                self._spawn_locked()

    def start(self, raw: object, *, duel_busy: bool = False) -> dict:
        conf = normalize_human(raw)
        with self._lock:
            if self._closed or duel_busy or self.busy:
                raise HumanBusy("Já há uma partida ou duelo em execução")
            opponent = find_player(conf["opponent"])
            try:
                exe, profiles = validate(self.root, opponent, opponent)
                if conf["opening_mode"] == "internal":
                    official_book_hashes(self.root, profiles)
                hashes = {"engine": file_hash(exe),
                          "profile": file_hash(profiles[opponent.slug])}
            except (FileNotFoundError, ValueError, OSError) as exc:
                raise HumanInput("Perfil UCI do adversário indisponível") from exc
            ident = "human-" + datetime.now(timezone.utc).strftime(
                "%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
            parent = self.workspace / "human-games"
            if parent.is_symlink():
                raise HumanInput("Pasta de partidas redirecionada")
            parent.mkdir(exist_ok=True)
            folder = parent / ident
            folder.mkdir(mode=0o700, exist_ok=False)
            self._folder = folder
            self._board = chess.Board()
            self._game = chess.pgn.Game()
            self._node = self._game
            white = "Você" if conf["color"] == "white" else opponent.name
            black = "Você" if conf["color"] == "black" else opponent.name
            self._game.headers.update({
                "Event": "ChessSimulation - Jogar versus UCI",
                "Site": "Local", "Date": datetime.now(timezone.utc).strftime("%Y.%m.%d"),
                "White": white, "Black": black, "Result": "*",
                "TimeControl": (str(conf["clock"][0]) + "+" + str(conf["clock"][1]))
                if conf["clock"] else "-",
                "EngineSHA256": hashes["engine"],
                "EngineProfileSHA256": hashes["profile"],
                "EngineSlug": opponent.slug, "SearchLimit": "depth=" + str(conf["depth"]),
                "SourceKind": "human_vs_engine",
            })
            self._epoch += 1
            # Long classical/custom clocks must not hit the standard 1h
            # duel safety budget while both players still have time.
            self._wall_budget = max(
                self.max_wall_seconds,
                2 * conf["clock"][0] + 400 * conf["clock"][1] + 600
                if conf["clock"] else self.max_wall_seconds)
            self._data = {
                "schema": SCHEMA, "id": ident, "config": conf,
                "created_utc": _utc(), "updated_utc": _utc(), "finished_utc": None,
                "revision": 0, "status": "RUNNING", "result": "*",
                "termination": None, "clocks": [float(conf["clock"][0])] * 2
                if conf["clock"] else None, "hashes": hashes,
                "draw_offer": None,
            }
            self._turn_started = monotonic()
            self._session_started = monotonic()
            self._save_locked()
            self._spawn_locked()
            return {"id": ident, "status": "RUNNING"}

    def state(self) -> dict:
        with self._lock:
            if self._data is None:
                return {"schema": SCHEMA, "game": None}
            self._tick_locked()
            data = self._data
            moves = []
            replay_board = self._game.board()
            replay_clocks = ([float(self._data["config"]["clock"][0])] * 2
                             if self._data["config"]["clock"] else [None,None])
            states = [{"fen": replay_board.fen(), "last": None,
                       "clocks": replay_clocks.copy(), "turn": "white"}]
            for ply, node in enumerate(self._game.mainline(), 1):
                actor = 0 if replay_board.turn else 1
                san = replay_board.san(node.move)
                uci = node.move.uci()
                replay_board.push(node.move)
                move_clock = node.clock()
                if move_clock is not None:
                    replay_clocks[actor] = move_clock
                moves.append({
                    "ply": ply, "san": san, "uci": uci,
                    "clock": move_clock,
                })
                states.append({
                    "fen": replay_board.fen(),
                    "last": [uci[:2],uci[2:4]],
                    "turn": "white" if replay_board.turn else "black",
                    "clocks": replay_clocks.copy(),
                })
            last = moves[-1]["uci"] if moves else None
            return {
                "schema": SCHEMA,
                "game": {
                    "id": data["id"], "status": data["status"],
                    "revision": data["revision"], "config": data["config"],
                    "created_utc": data["created_utc"],
                    "updated_utc": data["updated_utc"],
                    "finished_utc": data["finished_utc"],
                    "white": self._game.headers["White"],
                    "black": self._game.headers["Black"],
                    "fen": self._board.fen(),
                    "turn": "white" if self._board.turn else "black",
                    "human_turn": self._is_human_turn_locked(),
                    "legal_moves": [m.uci() for m in self._board.legal_moves]
                    if self._is_human_turn_locked() else [],
                    "last": [last[:2], last[2:4]] if last else None,
                    "result": data["result"], "termination": data["termination"],
                    "clocks": list(data["clocks"]) if data["clocks"] else None,
                    "moves": moves, "states": states,
                    "draw_offer": data["draw_offer"],
                },
            }

    def move(self, raw: object) -> dict:
        if (not isinstance(raw, dict) or set(raw) !=
                {"id", "revision", "uci"}):
            raise HumanInput("Comando de lance inválido")
        with self._lock:
            self._tick_locked()
            if not self._data or raw["id"] != self._data["id"]:
                raise HumanInput("Partida não encontrada")
            if (type(raw["revision"]) is not int or
                    raw["revision"] != self._data["revision"]):
                raise HumanBusy("Posição desatualizada; recarregue a partida")
            if not self._is_human_turn_locked():
                raise HumanBusy("Não é a vez do jogador")
            uci = raw["uci"]
            if type(uci) is not str or not MOVE_RX.fullmatch(uci):
                raise HumanInput("Formato UCI inválido")
            try:
                move = chess.Move.from_uci(uci)
            except ValueError as exc:
                raise HumanInput("Lance inválido") from exc
            if move not in self._board.legal_moves:
                raise HumanInput("Lance ilegal nesta posição")
            self._append_locked(move)
            self._spawn_locked()
            return {"id": self._data["id"], "revision": self._data["revision"],
                    "status": self._data["status"]}

    def action(self, raw: object) -> dict:
        if not isinstance(raw, dict) or set(raw) != {"id", "action"}:
            raise HumanInput("Ação inválida")
        engine_to_cancel = None
        with self._lock:
            self._tick_locked()
            if not self._data or raw["id"] != self._data["id"]:
                raise HumanInput("Partida não encontrada")
            if self._data["status"] != "RUNNING":
                raise HumanBusy("Partida não está em andamento")
            kind = raw["action"]
            if kind == "resign":
                self._epoch += 1
                engine_to_cancel = self._engine
                result = "0-1" if self._data["config"]["color"] == "white" else "1-0"
                self._finish_locked(result, "HumanResignation")
            elif kind == "claim_draw":
                if not self._is_human_turn_locked():
                    raise HumanBusy("Empate só pode ser reivindicado na sua vez")
                if not self._board.can_claim_draw():
                    raise HumanInput("Não existe empate reivindicável nesta posição")
                self._finish_locked("1/2-1/2", "HumanDrawClaim")
            elif kind == "offer_draw":
                # UCI has no draw negotiation protocol. Never invent acceptance.
                self._data["draw_offer"] = "Motor UCI não negocia propostas; oferta recusada."
                self._save_locked()
            elif kind == "interrupt":
                self._epoch += 1
                engine_to_cancel = self._engine
                self._finish_locked("*", "OperatorStop", "INTERRUPTED")
            else:
                raise HumanInput("Ação não reconhecida")
            result = {"id": self._data["id"], "status": self._data["status"],
                      "draw_offer": self._data["draw_offer"]}
        # Closing the previous UCI transport occurs outside the state lock.
        if engine_to_cancel is not None:
            try:
                engine_to_cancel.close()
            except Exception:
                pass
        return result

    def resume(self, raw: object, *, duel_busy: bool = False) -> dict:
        if not isinstance(raw, dict) or set(raw) != {"id"}:
            raise HumanInput("Retomada inválida")
        with self._lock:
            if (duel_busy or self.busy or not self._data or
                    raw["id"] != self._data["id"] or
                    self._data["status"] != "INTERRUPTED"):
                raise HumanBusy("Não existe partida interrompida elegível")
            conf = self._data["config"]
            p = find_player(conf["opponent"])
            try:
                exe, profiles = validate(self.root, p, p)
                if (file_hash(exe) != self._data["hashes"]["engine"] or
                        file_hash(profiles[p.slug]) != self._data["hashes"]["profile"]):
                    raise HumanInput("Motor ou perfil mudou desde o último checkpoint")
            except (OSError, ValueError, FileNotFoundError) as exc:
                raise HumanInput("Motor não disponível para retomada") from exc
            if self._data["clocks"] and min(self._data["clocks"]) <= 0:
                raise HumanInput("Partida sem tempo restante")
            self._epoch += 1
            self._wall_budget = max(
                self.max_wall_seconds,
                2 * conf["clock"][0] + 400 * conf["clock"][1] + 600
                if conf["clock"] else self.max_wall_seconds)
            self._data["status"] = "RUNNING"
            self._data["result"] = "*"
            self._data["termination"] = None
            self._data["finished_utc"] = None
            self._game.headers["Result"] = "*"
            self._game.headers.pop("Termination", None)
            self._turn_started = monotonic()
            self._session_started = monotonic()
            self._save_locked()
            self._spawn_locked()
            return {"id": self._data["id"], "status": "RUNNING"}

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            self._epoch += 1
            if self.busy:
                self._finish_locked("*", "ServerShutdown", "INTERRUPTED")
            thread = self._thread
            engine = self._engine
        if engine is not None:
            try:
                engine.close()
            except Exception:
                pass
        if thread:
            thread.join(timeout=3.0)
