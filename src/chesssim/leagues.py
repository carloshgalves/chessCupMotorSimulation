"""Persistent, provenance-aware league seasons. Issue #59.

A season schedule is derived from its immutable configuration; the append-only
ledger and per-game receipts are the only scoring authority. Statistical
projections are explicitly not UCI games and never have fabricated PGNs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import RLock, Event, Thread
from typing import Callable
import json
import os
import re

import chess.pgn

from .catalog import find_player, load_roster
from .matches import _play_one, _atomic_text, file_hash, validate
from .peak_windows import CLOCK_PRESETS

SCHEMA = "chesssim-league-v1"
ENTRY_SCHEMA = "chesssim-league-game-v1"
ID_RX = re.compile(r"league-[0-9a-f]{16}\Z")
MATCH_RX = re.compile(r"r[0-9]{3}-g[0-9]{2}\Z")
RESULTS = {"1-0", "0-1", "1/2-1/2"}
FORMATS = {"single", "double"}
EXECUTIONS = {"engine_full", "fast_statistical"}
ROUNDS_LIMIT = 126
MAX_PLAYERS = 64
MAX_LEDGER_BYTES = 16 * 1024 * 1024


class LeagueInput(ValueError):
    """Invalid configuration or stale league command."""


class LeagueBusy(RuntimeError):
    """Only one local execution is permitted."""


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(obj: object) -> str:
    return sha256(canonical(obj)).hexdigest()


def schedule(participants: list[str], format: str = "single") -> list[list[dict]]:
    """Circle method: 2..64 even entrants, unique unordered pair per leg.

    Reverse colors in the second leg; 64 double has 126 x 32 = 4032 games.
    """
    if type(participants) is not list:
        raise LeagueInput("Participantes devem formar uma lista")
    n = len(participants)
    if (n < 2 or n > MAX_PLAYERS or n % 2 or
            any(type(s) is not str for s in participants) or
            len(set(participants)) != n or
            type(format) is not str or format not in FORMATS):
        raise LeagueInput("Informe 2 a 64 participantes distintos, em número par")
    pool = participants.copy()
    first: list[list[tuple[str, str]]] = []
    for idx in range(n - 1):
        pairs = []
        for j in range(n // 2):
            a, b = pool[j], pool[n - 1 - j]
            # Home/away balance for the fixed first seat.
            if j == 0 and idx % 2:
                a, b = b, a
            elif j != 0 and (j + idx) % 2:
                a, b = b, a
            pairs.append((a, b))
        first.append(pairs)
        pool = [pool[0], pool[-1], *pool[1:-1]]
    legs = first + [[(b, a) for a, b in row] for row in first] if format == "double" else first
    return [[{"id": f"r{r:03d}-g{j:02d}", "round": r,
              "virtual_week": r, "virtual_day": (r - 1) * 7 + 1,
              "white": w, "black": b, "status": "PENDING"}
             for j, (w, b) in enumerate(pairs, 1)]
            for r, pairs in enumerate(legs, 1)]


def normalize_config(raw: object) -> dict:
    allowed = {"name", "season", "format", "modality", "participants",
               "execution_mode", "depth", "points", "seed"}
    if type(raw) is not dict or set(raw) - allowed:
        raise LeagueInput("Configuração desconhecida de liga")
    name, season = raw.get("name"), raw.get("season", "Temporada 1")
    if (type(name) is not str or not 3 <= len(name.strip()) <= 80 or
            any(ord(ch) < 32 for ch in name)):
        raise LeagueInput("Nome deve conter entre 3 e 80 caracteres")
    if (type(season) is not str or not 1 <= len(season.strip()) <= 40 or
            any(ord(ch) < 32 for ch in season)):
        raise LeagueInput("Identificação de temporada inválida")
    fmt, modality = raw.get("format", "single"), raw.get("modality", "rapid")
    if (type(fmt) is not str or type(modality) is not str or
            fmt not in FORMATS or modality not in CLOCK_PRESETS):
        raise LeagueInput("Formato ou modalidade indisponível")
    participants = raw.get("participants")
    if type(participants) is not list:
        raise LeagueInput("Selecione explicitamente os participantes")
    all_slugs = {p.slug for p in load_roster()}
    if any(type(slug) is not str or slug not in all_slugs for slug in participants):
        raise LeagueInput("Liga contém enxadrista fora do catálogo")
    schedule(participants, fmt)
    execution = raw.get("execution_mode", "engine_full")
    if type(execution) is not str or execution not in EXECUTIONS:
        raise LeagueInput("Tipo de execução inválido")
    depth = raw.get("depth", 6)
    if type(depth) is not int or not 1 <= depth <= 16:
        raise LeagueInput("Profundidade deve estar entre 1 e 16")
    points = raw.get("points", {"win": 3, "draw": 1, "loss": 0})
    if (type(points) is not dict or set(points) != {"win", "draw", "loss"} or
            any(type(points[k]) is not int or not 0 <= points[k] <= 9
                for k in points) or
            not points["win"] > points["draw"] >= points["loss"]):
        raise LeagueInput("Pontuação exige vitória > empate >= derrota")
    seed = raw.get("seed", 0)
    if type(seed) is not int or not 0 <= seed <= 2147483647:
        raise LeagueInput("Semente de simulação inválida")
    return {"name": name.strip(), "season": season.strip(), "format": fmt,
            "modality": modality, "clock": list(CLOCK_PRESETS[modality]),
            "participants": participants.copy(), "execution_mode": execution,
            "depth": depth, "points": points.copy(), "seed": seed}


def _row_result(result: str, who: str, match: dict) -> tuple[int, int, int]:
    if result not in RESULTS:
        raise ValueError("Resultado não comprovado")
    if result == "1/2-1/2":
        return 0, 1, 0
    winner = match["white"] if result == "1-0" else match["black"]
    return (1, 0, 0) if who == winner else (0, 0, 1)


def standings(config: dict, rounds: list[list[dict]], entries: dict) -> list[dict]:
    """3/1/0 league points, W/D/L, two-player H2H, then Sonneborn-Berger.

    Multiway ties use SB (no ungrounded recursive mini-league); all remaining
    ties resolve by stable canonical roster slug.
    """
    pts = config["points"]
    rows = {slug: {"slug": slug, "name": find_player(slug).name,
                   "played": 0, "wins": 0, "draws": 0, "losses": 0,
                   "points": 0, "sb": 0.0, "h2h": None}
            for slug in config["participants"]}
    games = [m for rd in rounds for m in rd if m["id"] in entries]
    h2h: dict[tuple[str, str], list] = {}
    for m in games:
        entry = entries[m["id"]]
        for slug in (m["white"], m["black"]):
            win, draw, loss = _row_result(entry["result"], slug, m)
            row = rows[slug]
            row["played"] += 1
            row["wins"] += win
            row["draws"] += draw
            row["losses"] += loss
            row["points"] += win * pts["win"] + draw * pts["draw"] + loss * pts["loss"]
        pair = tuple(sorted((m["white"], m["black"])))
        h2h.setdefault(pair, []).append((m, entry["result"]))
    for m in games:
        result = entries[m["id"]]["result"]
        for who, opponent in ((m["white"], m["black"]), (m["black"], m["white"])):
            win, draw, _ = _row_result(result, who, m)
            rows[who]["sb"] += (rows[opponent]["points"] if win else
                                rows[opponent]["points"] / 2 if draw else 0)
    groups: dict[tuple[int, int], list] = {}
    for row in rows.values():
        groups.setdefault((row["points"], row["wins"]), []).append(row)
    sorted_rows = []
    for key in sorted(groups, reverse=True):
        group = groups[key]
        if len(group) == 2:
            a, b = group
            pair = tuple(sorted((a["slug"], b["slug"])))
            played = h2h.get(pair, [])
            required = 2 if config["format"] == "double" else 1
            if len(played) == required:
                for candidate in (a, b):
                    candidate["h2h"] = sum(
                        _row_result(result, candidate["slug"], m)[0] * pts["win"] +
                        _row_result(result, candidate["slug"], m)[1] * pts["draw"]
                        for m, result in played)
                group.sort(key=lambda r: (-r["h2h"], -r["sb"], r["slug"]))
            else:
                group.sort(key=lambda r: (-r["sb"], r["slug"]))
        else:
            group.sort(key=lambda r: (-r["sb"], r["slug"]))
        sorted_rows.extend(group)
    for rank, row in enumerate(sorted_rows, 1):
        row["rank"] = rank
    return sorted_rows


def projected_result(config: dict, match: dict) -> str:
    """Transparent seed-only synthetic result, not inferred engine strength."""
    h = sha256(canonical({"seed": config["seed"], "id": match["id"],
                          "white": match["white"], "black": match["black"],
                          "participants": config["participants"]})).digest()
    score = int.from_bytes(h[:8], "big") % 100
    return "1/2-1/2" if score < 24 else "1-0" if score < 62 else "0-1"


def default_engine_runner(root: Path, match: dict, config: dict,
                          stop_check: Callable[[], bool]) -> tuple:
    white, black = find_player(match["white"]), find_player(match["black"])
    exe, paths = validate(root, white, black)
    hashes = {"exe": file_hash(exe), white.slug: file_hash(paths[white.slug]),
              black.slug: file_hash(paths[black.slug])}
    clock = tuple(config["clock"])
    game, meta = _play_one(root, exe, white, black, config["depth"], None,
                           400, match["round"], hashes, clock=clock,
                           use_books=False, stop_check=stop_check,
                           max_wall_seconds=min(86400, 2 * clock[0] +
                                                400 * clock[1] + 600))
    return game, meta


class LeagueManager:
    def __init__(self, root: Path, workspace: Path, *, runner=None):
        self.root = root
        self.workspace = workspace
        self.folder = workspace / "leagues"
        if self.folder.is_symlink():
            raise ValueError("Diretório de ligas não pode ser simbólico")
        self.folder.mkdir(parents=True, exist_ok=True)
        self.runner = runner or default_engine_runner
        self._lock = RLock()
        self._thread: Thread | None = None
        self._active: str | None = None
        self._stop = Event()
        self._shutdown = Event()
        # Repeated league polls must not reparse thousands of PGNs.
        # Stat changes invalidate the cache; export forces full verification.
        self._verified_pgn: dict[tuple[str, str, str], tuple[int, int, int, int]] = {}
        self._recover_interrupted()

    def _recover_interrupted(self) -> None:
        """Startup-only transition: GET handlers never mutate season files."""
        for source in sorted(self.folder.glob("league-*/league.json")):
            if not ID_RX.fullmatch(source.parent.name):
                continue
            state = self._load(source.parent.name)
            if state["status"] == "RUNNING":
                state["status"] = "PAUSED"
                state["message"] = "Execução interrompida; selecione Retomar rodada"
                state["current_match"] = None
                state["updated_utc"] = utc()
                self._save(state)

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._active is not None

    def _dir(self, ident: str) -> Path:
        if type(ident) is not str or not ID_RX.fullmatch(ident):
            raise LeagueInput("ID de liga inválido")
        path = self.folder / ident
        if path.is_symlink():
            raise LeagueInput("Liga redirecionada")
        return path

    def _load(self, ident: str) -> dict:
        path = self._dir(ident)
        state_file = path / "league.json"
        if (not state_file.is_file() or state_file.is_symlink() or
                state_file.stat().st_size > 65536):
            raise LeagueInput("Liga não encontrada ou inválida")
        data = json.loads(state_file.read_text(encoding="utf-8"))
        if (type(data) is not dict or data.get("schema") != SCHEMA or
                data.get("id") != ident or type(data.get("config")) is not dict or
                normalize_config({k: v for k,v in data["config"].items()
                                  if k != "clock"}) != data["config"] or
                data.get("status") not in {"READY", "RUNNING", "PAUSED", "COMPLETED", "FAILED"}):
            raise ValueError("Manifesto de liga incompatível")
        return data

    def _save(self, state: dict) -> None:
        _atomic_text(self._dir(state["id"]) / "league.json",
                     json.dumps(state, ensure_ascii=False,
                                allow_nan=False, indent=2) + "\n")

    def create(self, raw: object) -> dict:
        config = normalize_config(raw)
        with self._lock:
            if self._shutdown.is_set():
                raise LeagueBusy("Arena em encerramento")
            from secrets import token_hex
            ident = "league-" + token_hex(8)
            directory = self._dir(ident)
            directory.mkdir(mode=0o700, exist_ok=False)
            state = {"schema": SCHEMA, "id": ident, "config": config,
                     "created_utc": utc(), "started_utc": None,
                     "updated_utc": utc(), "status": "READY",
                     "message": None, "current_match": None,
                     "resources": None, "execution_started_utc": None}
            self._save(state)
            return {"id": ident, "status": "READY"}

    def _receipt(self, ident: str, match_id: str) -> Path:
        if not MATCH_RX.fullmatch(match_id):
            raise ValueError("Identificador de partida inválido")
        root = self._dir(ident) / "receipts"
        if root.is_symlink():
            raise ValueError("Recibos redirecionados")
        return root / (match_id + ".json")

    def _ledger(self, state: dict, verify: bool = True,
                reconcile: bool = False) -> dict[str, dict]:
        directory = self._dir(state["id"])
        path = directory / "ledger.jsonl"
        rounds = schedule(state["config"]["participants"], state["config"]["format"])
        expected = {m["id"]: m for rd in rounds for m in rd}
        registered: dict[str, dict] = {}
        if path.exists():
            if path.is_symlink() or path.stat().st_size > MAX_LEDGER_BYTES:
                raise ValueError("Ledger ausente de integridade")
            raw = path.read_bytes()
            # Journal append may be interrupted between write and fsync.
            # Only a newline-terminated entry is committed; the receipt
            # remains the immutable source used to recover a partial tail.
            committed = raw if not raw or raw.endswith(b"\n") else (
                raw[:raw.rfind(b"\n") + 1])
            if committed != raw and reconcile:
                with path.open("r+b") as stream:
                    stream.truncate(len(committed))
                    stream.flush()
                    os.fsync(stream.fileno())
            for line in committed.splitlines(keepends=True):
                if len(line) > 8192 or not line.endswith(b"\n"):
                    raise ValueError("Ledger possui linha inválida")
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    raise ValueError("Registro de liga inválido")
                mid = entry.get("match_id")
                if mid in registered or mid not in expected:
                    raise ValueError("Partida repetida ou fora do calendário")
                self._verify_entry(state, expected[mid], entry, verify=verify)
                registered[mid] = entry
        receipt_dir = directory / "receipts"
        if receipt_dir.exists():
            if receipt_dir.is_symlink():
                raise ValueError("Pasta de recibos redirecionada")
            for receipt in sorted(receipt_dir.glob("*.json")):
                if receipt.is_symlink() or receipt.stat().st_size > 8192:
                    raise ValueError("Recibo de liga inválido")
                mid = receipt.stem
                if mid not in expected:
                    raise ValueError("Recibo fora da tabela")
                entry = json.loads(receipt.read_text(encoding="utf-8"))
                self._verify_entry(state, expected[mid], entry, verify=verify)
                if mid in registered:
                    if registered[mid] != entry:
                        raise ValueError("Ledger e recibo divergentes")
                else:
                    if reconcile:
                        # Only a POST run operation commits the receipt back
                        # into the append-only journal. GET remains read-only.
                        self._append(directory, entry)
                    registered[mid] = entry
        return registered

    def _verify_entry(self, state: dict, match: dict, entry: dict,
                      verify: bool = True) -> None:
        proof = entry.get("sha256")
        if (entry.get("schema") != ENTRY_SCHEMA or
                entry.get("league_id") != state["id"] or
                entry.get("match_id") != match["id"] or
                entry.get("round") != match["round"] or
                entry.get("virtual_week") != match["virtual_week"] or
                entry.get("virtual_day") != match["virtual_day"] or
                entry.get("white") != match["white"] or
                entry.get("black") != match["black"] or
                entry.get("result") not in RESULTS or
                entry.get("source") != state["config"]["execution_mode"] or
                type(proof) is not str or
                digest({k:v for k,v in entry.items() if k != "sha256"}) != proof):
            raise ValueError("Registro de partida adulterado")
        pgn_path = entry.get("pgn")
        if entry["source"] == "fast_statistical":
            if (pgn_path is not None or entry.get("engine") is not None or
                    entry.get("clock_left") is not None or
                    entry["result"] != projected_result(state["config"], match)):
                raise ValueError("Projeção estatística não possui PGN ou engine")
        else:
            resources = state.get("resources")
            expected_engine = ({
                "engine_sha256": resources["engine_sha256"],
                "profile_sha256": {
                    slug: resources["profile_sha256"][slug]
                    for slug in (match["white"], match["black"])
                },
            } if isinstance(resources, dict) else None)
            if (pgn_path != f"games/{match['id']}.pgn" or
                    not isinstance(entry.get("engine"), dict) or
                    entry["engine"] != expected_engine):
                raise ValueError("Proveniência do motor divergente")
            if verify:
                pgn = self._dir(state["id"]) / pgn_path
                if pgn.parent.is_symlink() or pgn.is_symlink() or not pgn.is_file():
                    raise ValueError("PGN da liga ausente ou alterado")
                stats = pgn.stat()
                signature = (stats.st_ino, stats.st_size, stats.st_mtime_ns, stats.st_ctime_ns)
                key = (state["id"], match["id"], proof)
                if self._verified_pgn.get(key) != signature:
                    if file_hash(pgn) != entry.get("pgn_sha256"):
                        raise ValueError("PGN da liga ausente ou alterado")
                    with pgn.open(encoding="utf-8") as stream:
                        game = chess.pgn.read_game(stream)
                        extra = chess.pgn.read_game(stream)
                    if (game is None or game.errors or extra is not None or
                            game.headers.get("White") != find_player(match["white"]).name or
                            game.headers.get("Black") != find_player(match["black"]).name or
                            game.headers.get("Result") != entry["result"] or
                            game.headers.get("Termination") != entry["termination"] or
                            game.headers.get("Round") != str(match["round"]) or
                            game.headers.get("EngineSHA256") != entry["engine"]["engine_sha256"] or
                            game.headers.get("WhiteProfileSHA256") !=
                            entry["engine"]["profile_sha256"][match["white"]] or
                            game.headers.get("BlackProfileSHA256") !=
                            entry["engine"]["profile_sha256"][match["black"]] or
                            game.headers.get("TimeControl") !=
                            f"{state['config']['clock'][0]}+{state['config']['clock'][1]}"):
                        raise ValueError("PGN da liga não comprova o resultado")
                    position = game.end().board()
                    if entry["termination"] == "TimeForfeit":
                        clock_left = entry.get("clock_left")
                        idx = 0 if position.turn else 1
                        result_on_flag = ("0-1" if position.turn else "1-0")
                        if position.has_insufficient_material(not position.turn):
                            result_on_flag = "1/2-1/2"
                        if (not isinstance(clock_left, list) or
                                len(clock_left) != 2 or
                                type(clock_left[idx]) not in (int, float) or
                                clock_left[idx] > 0 or
                                entry["result"] != result_on_flag):
                            raise ValueError("Queda de bandeira não comprovada")
                    else:
                        outcome = position.outcome(claim_draw=True)
                        if outcome is None or outcome.result() != entry["result"]:
                            raise ValueError("Resultado não comprovado pelo tabuleiro")
                    self._verified_pgn[key] = signature
        receipt = self._receipt(state["id"], match["id"])
        if not receipt.is_file() or receipt.is_symlink():
            raise ValueError("Recibo obrigatório ausente")

    def _append(self, directory: Path, entry: dict) -> None:
        path = directory / "ledger.jsonl"
        if path.is_symlink():
            raise ValueError("Ledger redirecionado")
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _record(self, state: dict, match: dict, result: str,
                started: str, *, pgn=None, meta: dict | None = None,
                termination: str = "StatisticalProjection") -> None:
        if result not in RESULTS:
            raise ValueError("Somente partidas concluídas contam na classificação")
        directory = self._dir(state["id"])
        relative = f"games/{match['id']}.pgn" if pgn is not None else None
        if pgn is not None:
            if (pgn.headers.get("White") != find_player(match["white"]).name or
                    pgn.headers.get("Black") != find_player(match["black"]).name or
                    pgn.headers.get("Result") != result or
                    pgn.headers.get("Termination") != termination):
                raise ValueError("Resultado ou jogadores divergentes do PGN")
            game_file = directory / relative
            if game_file.parent.is_symlink() or game_file.is_symlink():
                raise ValueError("Arquivo PGN redirecionado")
            game_file.parent.mkdir(exist_ok=True)
            _atomic_text(game_file, str(pgn) + "\n")
            pgn_sha = file_hash(game_file)
        else:
            pgn_sha = None
        item = {
            "schema": ENTRY_SCHEMA, "league_id": state["id"],
            "match_id": match["id"], "round": match["round"],
            "virtual_week": match["virtual_week"],
            "virtual_day": match["virtual_day"],
            "white": match["white"], "black": match["black"],
            "result": result, "termination": termination,
            "source": state["config"]["execution_mode"],
            "started_utc": started, "finished_utc": utc(),
            "virtual_round": f"Rodada {match['round']}",
            "pgn": relative, "pgn_sha256": pgn_sha,
            "clock_left": meta.get("clock_left") if meta is not None else None,
            "engine": ({
                "engine_sha256": state["resources"]["engine_sha256"],
                "profile_sha256": {
                    slug: state["resources"]["profile_sha256"][slug]
                    for slug in (match["white"], match["black"])
                },
            } if pgn is not None else None),
        }
        item["sha256"] = digest(item)
        receipt = self._receipt(state["id"], match["id"])
        receipt.parent.mkdir(exist_ok=True)
        if receipt.exists():
            raise ValueError("Recibo de partida duplicado")
        _atomic_text(receipt, json.dumps(item, ensure_ascii=False,
                                         sort_keys=True, indent=2) + "\n")
        self._append(directory, item)

    def list(self) -> dict:
        with self._lock:
            leagues = []
            for child in sorted(self.folder.glob("league-*/league.json"),
                                key=lambda p: p.stat().st_mtime, reverse=True):
                if not ID_RX.fullmatch(child.parent.name):
                    continue
                info = self.detail(child.parent.name, compact=True)
                leagues.append(info)
            return {"schema": SCHEMA, "leagues": leagues}

    def detail(self, ident: str, *, compact: bool = False) -> dict:
        with self._lock:
            state = self._load(ident)
            config = state["config"]
            rounds = schedule(config["participants"], config["format"])
            entries = self._ledger(state)
            complete = len(entries)
            total = sum(map(len, rounds))
            if state["status"] == "COMPLETED" and complete != total:
                raise ValueError("Temporada encerrada perdeu partidas comprovadas")
            current_idx = next((i for i,rd in enumerate(rounds)
                                if any(m["id"] not in entries for m in rd)), len(rounds))
            overview = {
                "id": ident, "name": config["name"], "season": config["season"],
                "format": config["format"], "modality": config["modality"],
                "execution_mode": config["execution_mode"],
                "participants_count": len(config["participants"]),
                "status": state["status"],
                "total_games": total, "completed_games": complete,
                "pending_games": total-complete,
                "rounds_total": len(rounds), "round_current": min(current_idx+1,len(rounds)),
                "running_games": 1 if self._active == ident and
                            state["current_match"] is not None else 0,
                "message": state.get("message"),
            }
            if compact:
                return overview
            selected_round = (rounds[current_idx] if current_idx < len(rounds)
                              else rounds[-1])
            recent = [entries[m["id"]] for rd in rounds for m in rd
                      if m["id"] in entries]
            for entry in recent:
                entry["name_white"] = find_player(entry["white"]).name
                entry["name_black"] = find_player(entry["black"]).name
            round_progress = [
                {"round": idx, "virtual_week": idx,
                 "virtual_day": (idx - 1) * 7 + 1,
                 "completed": sum(m["id"] in entries for m in rd),
                 "total": len(rd)}
                for idx, rd in enumerate(rounds, 1)
            ]
            games_round = [{
                **m,
                "status": "COMPLETED" if m["id"] in entries else
                          "RUNNING" if self._active == ident and
                          state["current_match"] == m["id"] else "PENDING",
                "result": entries[m["id"]]["result"] if m["id"] in entries else None,
                "source": entries[m["id"]]["source"] if m["id"] in entries else None,
                "name_white": find_player(m["white"]).name,
                "name_black": find_player(m["black"]).name,
            } for m in selected_round]
            return {**overview, "config": config,
                    "players": [{"slug": slug, "name": find_player(slug).name}
                                for slug in config["participants"]],
                    "standings": standings(config, rounds, entries),
                    "round_progress": round_progress,
                    "games": games_round, "recent": recent[-40:],
                    "created_utc": state["created_utc"],
                    "execution_started_utc": state.get("execution_started_utc"),
                    "updated_utc": state["updated_utc"],
                    "current_match": state["current_match"],
                    "available_pgn": sum(x["pgn"] is not None for x in entries.values()),
                    "source_warning": (
                        "Projeções estatísticas determinísticas: não são partidas UCI, "
                        "não têm PGN e não entram no Elo competitivo."
                        if config["execution_mode"] == "fast_statistical" else
                        "Partidas efetivas do Rodent IV; rating histórico não é validado."),
                    }

    def round_detail(self, ident: str, round_number: int) -> dict:
        with self._lock:
            state = self._load(ident)
            rounds = schedule(state["config"]["participants"],
                              state["config"]["format"])
            if type(round_number) is not int or not 1 <= round_number <= len(rounds):
                raise LeagueInput("Rodada desconhecida")
            records = self._ledger(state)
            games = []
            for match in rounds[round_number-1]:
                entry = records.get(match["id"])
                games.append({
                    **match,
                    "white_name": find_player(match["white"]).name,
                    "black_name": find_player(match["black"]).name,
                    "status": ("COMPLETED" if entry else "RUNNING"
                               if self._active == ident and
                               state["current_match"] == match["id"] else "PENDING"),
                    "result": entry["result"] if entry else None,
                    "source": entry["source"] if entry else None,
                    "pgn": bool(entry and entry["pgn"]),
                    "finished_utc": entry["finished_utc"] if entry else None,
                })
            return {"id": ident, "round": round_number,
                    "rounds_total": len(rounds), "games": games}

    def history(self, ident: str, *, player: str | None = None,
                offset: int = 0, limit: int = 50) -> dict:
        with self._lock:
            state = self._load(ident)
            if player is not None and player not in state["config"]["participants"]:
                raise LeagueInput("Jogador não pertence à temporada")
            if (type(offset) is not int or offset < 0 or offset > 10000 or
                    type(limit) is not int or not 1 <= limit <= 100):
                raise LeagueInput("Paginação inválida")
            records = self._ledger(state)
            completed = list(records.values())
            if player:
                completed = [r for r in completed
                             if player in (r["white"], r["black"])]
            completed.sort(key=lambda r: (r["round"], r["match_id"]), reverse=True)
            page = completed[offset:offset+limit]
            return {"id": ident, "total": len(completed), "offset": offset,
                    "limit": limit,
                    "games": [{
                        **r,
                        "white_name": find_player(r["white"]).name,
                        "black_name": find_player(r["black"]).name,
                    } for r in page]}

    def _resources(self, config: dict) -> dict:
        profiles = {}
        executable = None
        for slug in config["participants"]:
            player = find_player(slug)
            exe, paths = validate(self.root, player, player)
            if executable is not None and exe != executable:
                raise LeagueInput("Executáveis inconsistentes")
            executable = exe
            profiles[slug] = file_hash(paths[slug])
        return {"engine_sha256": file_hash(executable),
                "profile_sha256": profiles}

    def run(self, ident: str, *, arena_busy: bool = False) -> dict:
        with self._lock:
            if self._active is not None or arena_busy or self._shutdown.is_set():
                raise LeagueBusy("Já existe duelo, partida ou liga em execução")
            state = self._load(ident)
            if state["status"] == "COMPLETED":
                raise LeagueInput("Temporada já concluída")
            completed = self._ledger(state, reconcile=True)
            config = state["config"]
            if config["execution_mode"] == "engine_full":
                try:
                    resources = self._resources(config)
                except (FileNotFoundError, OSError, ValueError) as exc:
                    raise LeagueInput(
                        "Perfis Rodent IV indisponíveis. Instale e registre todos "
                        "os enxadristas selecionados antes de executar.") from exc
                if state["resources"] is not None and resources != state["resources"]:
                    raise LeagueInput("Motor ou perfis mudaram desde o início da temporada")
                state["resources"] = resources
            rounds = schedule(config["participants"], config["format"])
            next_round = next((rd for rd in rounds if any(m["id"] not in completed
                            for m in rd)), None)
            if next_round is None:
                state["status"] = "COMPLETED"
                state["current_match"] = None
                state["updated_utc"] = utc()
                self._save(state)
                return {"id": ident, "status": "COMPLETED"}
            state["status"] = "RUNNING"
            state["message"] = None
            state["updated_utc"] = utc()
            state["execution_started_utc"] = utc()
            if state["started_utc"] is None:
                state["started_utc"] = utc()
            self._save(state)
            self._stop = Event()
            self._active = ident
            thread = Thread(target=self._execute_round,
                            args=(ident, next_round, self._stop),
                            daemon=False, name="chesssim-league-round")
            self._thread = thread
            thread.start()
            return {"id": ident, "status": "RUNNING",
                    "round": next_round[0]["round"]}

    def _execute_round(self, ident: str, matches: list[dict], stop: Event) -> None:
        try:
            for m in matches:
                if stop.is_set() or self._shutdown.is_set():
                    break
                with self._lock:
                    state = self._load(ident)
                    completed = self._ledger(state, reconcile=True)
                    if m["id"] in completed:
                        continue
                    state["current_match"] = m["id"]
                    state["updated_utc"] = utc()
                    self._save(state)
                    config = state["config"]
                began = utc()
                if config["execution_mode"] == "fast_statistical":
                    result = projected_result(config, m)
                    game = None
                    meta = None
                    termination = "StatisticalProjection"
                else:
                    game, meta = self.runner(self.root, m, config,
                                             self._shutdown.is_set)
                    if self._shutdown.is_set() and meta.get("status") == "INCOMPLETE":
                        break  # Graceful shutdown; leave unproven game pending.
                    if (meta.get("status") != "COMPLETED" or
                            meta.get("result") not in RESULTS):
                        raise RuntimeError("Partida não foi concluída; nenhuma pontuação aplicada")
                    result = meta["result"]
                    termination = meta.get("termination", "Unknown")
                with self._lock:
                    state = self._load(ident)
                    self._ledger(state)
                    self._record(state, m, result, began, pgn=game, meta=meta,
                                 termination=termination)
            with self._lock:
                state = self._load(ident)
                entries = self._ledger(state)
                rounds = schedule(state["config"]["participants"], state["config"]["format"])
                if len(entries) == sum(map(len, rounds)):
                    state["status"] = "COMPLETED"
                    state["message"] = "Temporada concluída"
                else:
                    state["status"] = "PAUSED"
                    state["message"] = ("Rodada concluída; avance quando desejar"
                                        if not stop.is_set() else "Pausa solicitada")
                state["current_match"] = None
                state["updated_utc"] = utc()
                self._save(state)
        except Exception as exc:
            with self._lock:
                state = self._load(ident)
                state["status"] = "FAILED"
                state["current_match"] = None
                state["updated_utc"] = utc()
                state["message"] = ("A rodada foi interrompida. Partidas comprovadas "
                                    "permanecem; verifique os logs locais.")
                self._save(state)
            print("Falha da liga local: " + type(exc).__name__ + ": " +
                  str(exc), flush=True)
        finally:
            with self._lock:
                self._active = None
                self._thread = None

    def pause(self, ident: str) -> dict:
        with self._lock:
            if self._active != ident:
                raise LeagueBusy("Nenhuma rodada dessa liga em execução")
            self._stop.set()
            return {"id": ident, "pause_requested": True,
                    "message": "Pausará após a partida corrente"}

    def export(self, ident: str, fmt: str) -> tuple[bytes, str]:
        with self._lock:
            # Exports are audit checkpoints: verify every PGN from bytes.
            self._verified_pgn.clear()
            data = self.detail(ident)
            state = self._load(ident)
            entries = self._ledger(state)
            if fmt == "json":
                raw = {"schema": SCHEMA, "league": data,
                       "completed_games": list(entries.values())}
                return (json.dumps(raw, ensure_ascii=False, indent=2,
                                   allow_nan=False).encode("utf-8"),
                        "application/json; charset=utf-8")
            if fmt != "pgn":
                raise LeagueInput("Formato de exportação inválido")
            games = [entry for entry in entries.values() if entry["pgn"] is not None]
            if not games:
                raise LeagueInput("Nenhuma partida UCI comprovada disponível para PGN")
            body = []
            for entry in games:
                path = self._dir(ident) / entry["pgn"]
                body.append(path.read_text(encoding="utf-8"))
            return ("\n".join(body).encode("utf-8"),
                    "application/x-chess-pgn; charset=utf-8")

    def shutdown(self) -> None:
        self._shutdown.set()
        with self._lock:
            self._stop.set()
            thread = self._thread
        if thread is not None:
            thread.join()


def verified_league_games(workspace: Path) -> tuple[list[dict], list[dict], int]:
    """Audit local league receipts without starting a manager or modifying files.

    Return independently checked UCI games, separately labelled projections,
    and the number of rejected seasons. A damaged season contributes no games.
    The reader reuses the same manifest, journal, receipt and PGN verification
    as league standings; it never calls startup recovery or reconciles a ledger.
    """
    folder = workspace / "leagues"
    if not folder.exists():
        return [], [], 0
    if folder.is_symlink() or not folder.is_dir():
        raise LeagueInput("Diretório de ligas inseguro")
    manifests = sorted(folder.glob("league-*/league.json"))
    if len(manifests) > 100:
        raise LeagueInput("Limite de temporadas para auditoria excedido")
    auditor = object.__new__(LeagueManager)
    auditor.folder = folder
    auditor._verified_pgn = {}
    games: list[dict] = []
    projections: list[dict] = []
    rejected = 0

    def stamp(value: object) -> str | None:
        if not isinstance(value, str) or len(value) > 48:
            raise ValueError("Timestamp de liga inválido")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Timestamp de liga sem fuso")
        return parsed.astimezone(timezone.utc).isoformat()

    for manifest in manifests:
        try:
            ident = manifest.parent.name
            if not ID_RX.fullmatch(ident) or manifest.is_symlink():
                raise LeagueInput("Manifesto de liga redirecionado")
            state = auditor._load(ident)
            recorded = stamp(state["created_utc"])
            entries = auditor._ledger(state, verify=True, reconcile=False)
            rounds = schedule(state["config"]["participants"], state["config"]["format"])
            if state["status"] == "COMPLETED" and len(entries) != sum(map(len, rounds)):
                raise LeagueInput("Temporada finalizada sem evidências completas")
            season_games: list[dict] = []
            season_projections: list[dict] = []
            for match_id, entry in sorted(entries.items()):
                start = stamp(entry["started_utc"])
                finish = stamp(entry["finished_utc"])
                start_dt = datetime.fromisoformat(start)
                finish_dt = datetime.fromisoformat(finish)
                if finish_dt < start_dt:
                    raise LeagueInput("Relógios cronológicos incompatíveis")
                is_projection = entry["source"] == "fast_statistical"
                evidence = entry["engine"] if not is_projection else None
                clock = list(state["config"]["clock"]) if not is_projection else None
                record = {
                    "id": ("projection:league:" if is_projection else "league:") +
                          ident + ":" + match_id,
                    "duel_id": "league:" + ident + ":" + match_id,
                    "white": entry["white"], "black": entry["black"],
                    "status": "COMPLETED", "result": entry["result"],
                    "origin": "projection" if is_projection else "league",
                    "source_kind": entry["source"], "recorded_utc": recorded,
                    "started_at_utc": start if not is_projection else None,
                    "finished_at_utc": finish if not is_projection else None,
                    "duration_seconds": (
                        round((finish_dt - start_dt).total_seconds(), 3)
                        if not is_projection else None),
                    "virtual_week": entry["virtual_week"],
                    "virtual_day": entry["virtual_day"],
                    "event_id": ident, "event_name": state["config"]["name"],
                    "season_id": state["config"]["season"],
                    "round": entry["round"], "time_control": (
                        state["config"]["modality"] if not is_projection else
                        "unclassified"),
                    "modality": state["config"]["modality"],
                    "clock": clock,
                    "engine_strength_config": ({
                        "engine_sha256": evidence["engine_sha256"],
                        "white_profile_sha256":
                            evidence["profile_sha256"][entry["white"]],
                        "black_profile_sha256":
                            evidence["profile_sha256"][entry["black"]],
                    } if evidence else None),
                    "evidence_pgn_sha256": entry["pgn_sha256"],
                    "receipt_sha256": entry["sha256"],
                    "rating_eligible": not is_projection,
                }
                (season_projections if is_projection else season_games).append(record)
            games.extend(season_games)
            projections.extend(season_projections)
        except (OSError, ValueError, TypeError, KeyError, IndexError,
                AttributeError, OverflowError):
            rejected += 1
    return games, projections, rejected
