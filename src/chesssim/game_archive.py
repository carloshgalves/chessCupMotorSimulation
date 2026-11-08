"""Paged, read-only local game archive for History (#70).

Competition ledgers remain the authority. Projections have no PGN or replay.
The index cache is invalidated by metadata and PGN file signatures, not by
background I/O or writes to the user's workspace.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import csv
import io
import json
import re
from threading import RLock

from .catalog import load_roster
from .cups import CupManager
from .history_stats import verified_competition_games
from .competition_rating import classify_competition_record
from .leagues import LeagueManager
from .history_games import (_read as safe_read, _game as parse_pgn,
                            _validate_game, _states as game_states,
                            )

SCHEMA = "chesssim-game-archive-v1"
MAX_ROWS = 20_000
MAX_LEAGUES = 100
MAX_CUPS = 100
MAX_LIST_FILES = 30_000
MAX_PGN = 2_000_000
REPLAY_KEY = re.compile(r"^(cup|league|arena):[A-Za-z0-9._:-]{6,160}$")
FILTER_KEYS = {"q", "competition", "opponent", "modality", "color",
               "result", "source", "date_from", "date_to", "offset", "limit"}


class ArchiveInput(ValueError):
    """Invalid query, source, replay identity or request."""


def _date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _slug(slug: str) -> str:
    if (not isinstance(slug, str) or
            not re.fullmatch(r"[a-z0-9-]{2,48}", slug) or
            slug not in {p.slug for p in load_roster()}):
        raise ArchiveInput("Jogador não encontrado")
    return slug


def normalize_query(raw: dict[str, list[str]]) -> dict:
    if set(raw) - FILTER_KEYS or any(len(items) != 1 for items in raw.values()):
        raise ArchiveInput("Filtro desconhecido ou repetido")
    q = {key: values[0] for key, values in raw.items()}
    for key, maxlen in (("q", 100), ("competition", 80), ("opponent", 48)):
        if len(q.get(key, "")) > maxlen:
            raise ArchiveInput("Busca excede limite")
    for key, allowed in {
        "modality": ("", "classical", "rapid", "blitz", "bullet", "unclassified"),
        "color": ("", "white", "black"),
        "result": ("", "win", "draw", "loss", "incomplete"),
        "source": ("", "engine_full", "fast_statistical"),
    }.items():
        if q.get(key, "") not in allowed:
            raise ArchiveInput("Valor inválido: " + key)
    for key in ("date_from", "date_to"):
        val = q.get(key, "")
        if val and not re.fullmatch(r"[12][0-9]{3}-[01][0-9]-[0-3][0-9]", val):
            raise ArchiveInput("Data inválida")
        if val:
            try:
                datetime.strptime(val, "%Y-%m-%d")
            except ValueError as exc:
                raise ArchiveInput("Data inválida") from exc
    if q.get("date_from") and q.get("date_to") and q["date_from"] > q["date_to"]:
        raise ArchiveInput("Intervalo de datas invertido")
    for key, lo, hi, default in (("offset", 0, 20000, "0"),
                                  ("limit", 1, 100, "20")):
        value = q.get(key, default)
        if not re.fullmatch(r"[0-9]{1,5}", value) or not lo <= int(value) <= hi:
            raise ArchiveInput("Paginação inválida")
        q[key] = int(value)
    return {key: q.get(key, "") for key in FILTER_KEYS if key not in ("offset", "limit")} | {
        "offset": q["offset"], "limit": q["limit"],
    }


def _mode(stage: str | None) -> str:
    return {"classical": "classical", "rapid15": "rapid",
            "rapid10": "rapid", "women_double_round_robin": "rapid",
            "blitz5": "blitz", "blitz3": "blitz",
            "armageddon": "unclassified"}.get(stage or "", "unclassified")


def _record(*, ident: str, competition: str, kind: str, white: str, black: str,
            result: str | None, status: str, modality: str, round_number: int | None,
            started: str | None, finished: str | None, source: str, replay: bool,
            evidence: str | None, simulated_date: str | None = None,
            season: str | None = None, pgn_ref: tuple | None = None) -> dict:
    if status not in ("COMPLETED", "INCOMPLETE", "FAILED", "CANCELLED"):
        raise ArchiveInput("Status de jogo incompatível")
    if status == "COMPLETED" and result not in ("1-0", "0-1", "1/2-1/2"):
        raise ArchiveInput("Resultado inválido")
    if source not in ("engine_full", "fast_statistical"):
        raise ArchiveInput("Origem inválida")
    if source == "fast_statistical" and (replay or evidence):
        raise ArchiveInput("Projeção não pode conter evidência UCI")
    return {"id": ident, "competition": competition, "kind": kind,
            "season": season, "round": round_number, "white": white,
            "black": black, "status": status, "result": result,
            "modality": modality, "started_at_utc": _date(started),
            "finished_at_utc": _date(finished), "simulated_calendar_date": simulated_date,
            "source_kind": source, "has_replay": bool(replay),
            "pgn_sha256": evidence, "_pgn_ref": pgn_ref}


class GameArchive:
    def __init__(self, workspace: Path, *, leagues: LeagueManager | None = None,
                 cups: CupManager | None = None):
        self.workspace = workspace
        self.leagues = leagues or self._readonly_leagues(workspace)
        self.cups = cups or CupManager.readonly(workspace)
        self._lock = RLock()
        self._signature = None
        self._rows: list[dict] = []
        self._coverage: dict = {}
        self._generation = 0

    @staticmethod
    def _readonly_leagues(workspace: Path) -> LeagueManager:
        observer = object.__new__(LeagueManager)
        observer.workspace = workspace
        observer.folder = workspace / "leagues"
        observer._verified_pgn = {}
        observer._lock = RLock()
        observer._active = None
        return observer

    def _source_signature(self) -> str:
        root = self.workspace
        for folder in ("leagues", "cups"):
            if (root/folder).is_symlink():
                raise ArchiveInput("Diretório competitivo redirecionado")
        patterns = (
            "session-*/duels/*/*/summary.json",
            "session-*/duels/*/*/*.pgn",
            "tournament-*/**/summary.json",
            "leagues/league-*/league.json",
            "leagues/league-*/ledger.jsonl",
            "leagues/league-*/receipts/*.json",
            "leagues/league-*/games/*.pgn",
            "cups/cup-*/cup.json",
            "cups/cup-*/receipts/*.json",
            "cups/cup-*/games/*.pgn",
        )
        items = []
        for pattern in patterns:
            for item in root.glob(pattern):
                if (item.is_symlink() or item.parent.is_symlink() or
                        item.parent.parent.is_symlink()):
                    raise ArchiveInput("Arquivo competitivo redirecionado")
                stat = item.stat()
                items.append((item.relative_to(root).as_posix(),
                              stat.st_size, stat.st_mtime_ns))
                if len(items) > MAX_LIST_FILES:
                    raise ArchiveInput("Índice excede limite de arquivos")
        return sha256(json.dumps(sorted(items)).encode()).hexdigest()

    def _refresh(self):
        signature = self._source_signature()
        if signature == self._signature:
            return
        names = {p.slug: p.name for p in load_roster()}
        rows = []
        rejected = Counter()
        # Trusted verified records from classic Arena duels and legacy tournaments.
        # Cup records are collected below to retain synthetic projections.
        actual, coverage = verified_competition_games(self.workspace)
        for item in actual:
            if item["origin"] in ("cup", "league"):
                continue
            origin = item["origin"]
            ident = (item["id"] if origin == "arena" else
                     "legacy:" + sha256(item["id"].encode()).hexdigest()[:24])
            if origin == "arena" and not REPLAY_KEY.fullmatch(ident):
                raise ArchiveInput("ID inválido em jogo verificado")
            rows.append(_record(
                ident=ident, competition="Arena" if origin=="arena" else "Torneio local",
                kind=origin, white=item["white"], black=item["black"],
                result=item["result"], status=item["status"],
                modality=classify_competition_record(item)[0],
                round_number=None,
                started=None, finished=item.get("recorded_utc"),
                source="engine_full", replay=origin=="arena" and
                    item["status"]=="COMPLETED",
                evidence=item.get("evidence_pgn_sha256"), pgn_ref=("arena",item["id"])))
        # Leagues: verify full ledger and PGNs through the existing contract.
        leagues_root = self.workspace/"leagues"
        manifests = sorted(leagues_root.glob("league-*/league.json"))
        if len(manifests)>MAX_LEAGUES:
            raise ArchiveInput("Excesso de ligas")
        for file in manifests:
            ident=file.parent.name
            if not re.fullmatch(r"league-[a-f0-9]{16}",ident):
                raise ArchiveInput("ID de liga não reconhecido")
            with self.leagues._lock:
                state=self.leagues._load(ident)
                entries=self.leagues._ledger(state)
            cfg=state["config"]
            for entry in entries.values():
                ref=("league",ident,entry["match_id"])
                rows.append(_record(
                    ident="league:"+ident+":"+entry["match_id"],
                    competition=cfg["name"], kind="league", season=cfg["season"],
                    white=entry["white"],black=entry["black"],
                    result=entry["result"],status="COMPLETED",
                    modality=cfg["modality"],round_number=entry["round"],
                    started=entry.get("started_utc"),finished=entry.get("finished_utc"),
                    source=entry["source"],replay=bool(entry["pgn"]),
                    evidence=entry.get("pgn_sha256"),
                    simulated_date=None, pgn_ref=ref))
        # Cups: verified receipts from both knockout and women's schedule.
        cups_root=self.workspace/"cups"
        manifests=sorted(cups_root.glob("cup-*/cup.json"))
        if len(manifests)>MAX_CUPS:
            raise ArchiveInput("Excesso de Copas")
        for file in manifests:
            ident=file.parent.name
            if not re.fullmatch(r"cup-[a-f0-9]{16}",ident):
                raise ArchiveInput("ID de Copa não reconhecido")
            state=self.cups._manifest(ident)
            overview=self.cups._state(state)
            cfg=state["config"]
            for entry in overview["game_receipts"].values():
                ref=("cup",ident,entry["id"])
                rows.append(_record(
                    ident="cup:"+ident+":"+entry["id"],
                    competition=cfg["name"], kind="cup", season=cfg["edition"],
                    white=entry["white"],black=entry["black"],
                    result=entry["result"],status="COMPLETED",
                    modality=_mode(entry["stage"]),round_number=entry["round"],
                    started=entry.get("started_utc"),finished=entry.get("finished_utc"),
                    source=entry["source"],replay=bool(entry.get("pgn")),
                    evidence=entry.get("pgn_sha256"),pgn_ref=ref))
        if len(rows)>MAX_ROWS:
            raise ArchiveInput("Arquivo excede limite de partidas")
        ids=[r["id"] for r in rows]
        if len(set(ids))!=len(ids):
            raise ArchiveInput("Colisão no identificador do arquivo")
        if any(r["white"] not in names or r["black"] not in names for r in rows):
            raise ArchiveInput("Partida fora do catálogo")
        rows.sort(key=lambda r:(r["finished_at_utc"] or "",
                                r["id"]),reverse=True)
        self._rows=rows
        self._coverage={"verified":sum(x["source_kind"]=="engine_full" and
                             x["status"]=="COMPLETED" for x in rows),
                        "projections":sum(x["source_kind"]=="fast_statistical" for x in rows),
                        "incomplete":sum(x["status"]!="COMPLETED" for x in rows),
                        "legacy_rejections":coverage["rejected_records"]+
                             coverage["rejected_games"], **rejected}
        self._signature=signature
        self._generation+=1

    @staticmethod
    def _public(row: dict) -> dict:
        return {k:v for k,v in row.items() if not k.startswith("_")}

    def _filtered(self, slug: str, params: dict) -> list[dict]:
        rows=[]
        for item in self._rows:
            if slug not in (item["white"],item["black"]):
                continue
            rival=item["black"] if item["white"]==slug else item["white"]
            color="white" if item["white"]==slug else "black"
            score=item["result"]
            outcome=("incomplete" if item["status"]!="COMPLETED" else
                     "draw" if score=="1/2-1/2" else
                     "win" if (score=="1-0" and color=="white") or
                              (score=="0-1" and color=="black") else "loss")
            date=(item["finished_at_utc"] or "")[:10]
            if (params["competition"] and
                    params["competition"].casefold() not in item["competition"].casefold() or
                    params["q"] and params["q"].casefold() not in (
                    item["competition"]+" "+rival+" "+item["id"]).casefold() or
                    params["opponent"] and params["opponent"]!=rival or
                    params["modality"] and params["modality"]!=item["modality"] or
                    params["color"] and params["color"]!=color or
                    params["result"] and params["result"]!=outcome or
                    params["source"] and params["source"]!=item["source_kind"] or
                    params["date_from"] and (not date or date<params["date_from"]) or
                    params["date_to"] and (not date or date>params["date_to"])):
                continue
            rows.append({**item,"opponent":rival,"color":color,"outcome":outcome})
        return rows

    def page(self, slug: str, raw: dict[str,list[str]]) -> dict:
        slug=_slug(slug)
        params=normalize_query(raw)
        with self._lock:
            self._refresh()
            selected=self._filtered(slug,params)
            start=params["offset"]
            count=len(selected)
            page=selected[start:start+params["limit"]]
            signature=sha256((self._signature+slug+json.dumps(
                {k:v for k,v in params.items() if k not in ("offset","limit")},
                sort_keys=True)).encode()).hexdigest()
            player_rows=[x for x in self._rows
                         if slug in (x["white"],x["black"])]
            counts=Counter(x["competition"] for x in player_rows)
            def outcome(item):
                if item["source_kind"]!="engine_full" or item["status"]!="COMPLETED":
                    return None
                if item["result"]=="1/2-1/2":
                    return "draws"
                white_won=item["result"]=="1-0"
                return "wins" if (white_won==(item["white"]==slug)) else "losses"
            outcome_counts=Counter(outcome(x) for x in player_rows)
            overview={
                "wins":outcome_counts["wins"],
                "draws":outcome_counts["draws"],
                "losses":outcome_counts["losses"],
                "uci":sum(x["source_kind"]=="engine_full" and
                          x["status"]=="COMPLETED" for x in player_rows),
                "projections":sum(x["source_kind"]=="fast_statistical"
                                  for x in player_rows),
                "incomplete":sum(x["status"]!="COMPLETED" for x in player_rows),
                "competitions":len(counts),
            }
            return {"schema":SCHEMA,"slug":slug,"total":count,
                    "offset":start,"limit":params["limit"],
                    "next_offset":start+params["limit"] if
                        start+params["limit"]<count else None,
                    "snapshot":signature,"coverage":self._coverage,
                    "overview":overview,
                    "facets":{"competitions":[{"name":name,"games":amount}
                        for name,amount in sorted(counts.items(),
                            key=lambda x:(-x[1],x[0]))[:100]]},
                    "games":[self._public(row) for row in page]}

    def replay(self, slug: str, key: str) -> dict:
        slug=_slug(slug)
        if not isinstance(key,str) or not REPLAY_KEY.fullmatch(key):
            raise ArchiveInput("ID de replay inválido")
        with self._lock:
            self._refresh()
            item=next((r for r in self._rows if r["id"]==key and
                       slug in (r["white"],r["black"])),None)
            if (item is None or not item["has_replay"] or
                    item["source_kind"]!="engine_full"):
                raise ArchiveInput("PGN não comprovado para esse jogador")
            source=item["_pgn_ref"]
            if source[0]=="cup":
                return self.cups.replay(source[1],source[2])
            if source[0]=="league":
                with self.leagues._lock:
                    state=self.leagues._load(source[1])
                    entries=self.leagues._ledger(state)
                    row=entries.get(source[2])
                    if row is None or not row["pgn"]:
                        raise ArchiveInput("Partida não registrada")
                    path=self.leagues._dir(source[1])/"games"/(source[2]+".pgn")
                    if (path.is_symlink() or path.parent.is_symlink() or
                            path.stat().st_size>MAX_PGN):
                        raise ArchiveInput("PGN inválido")
                    raw=path.read_bytes()
                    if sha256(raw).hexdigest()!=row["pgn_sha256"]:
                        raise ArchiveInput("PGN da liga mudou")
                    game=parse_pgn(raw)
                    return {"schema":"chesssim-archive-replay-v1",
                            "game":self._public(item),"states":game_states(game)}
            if source[0]=="arena":
                parts=source[1].split(":")
                if (len(parts)!=4 or parts[0]!="arena" or
                        not re.fullmatch(r"session-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}",parts[1]) or
                        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}",parts[2]) or
                        parts[3] not in ("1","2")):
                    raise ArchiveInput("ID da sessão inválido")
                sid,attempt,index=parts[1:]
                candidates=list((self.workspace/sid/"duels").glob(
                    "*/"+attempt+"/jogo_"+index+".pgn"))
                if len(candidates)!=1:
                    raise ArchiveInput("PGN de sessão ambíguo")
                file=candidates[0]
                raw=safe_read(self.workspace,file.relative_to(self.workspace),MAX_PGN)
                if sha256(raw).hexdigest()!=item["pgn_sha256"]:
                    raise ArchiveInput("PGN de sessão mudou")
                game=parse_pgn(raw)
                names={p.slug:p.name for p in load_roster()}
                _validate_game(game,item["white"],item["black"],names,
                               item["status"],item["result"])
                return {"schema":"chesssim-archive-replay-v1",
                        "game":self._public(item),"states":game_states(game)}
            raise ArchiveInput("Replay indisponível para esta origem")

    def _pgn_bytes(self,slug: str,item: dict) -> bytes:
        """Get the exact immutable PGN only after the authoritative replay gate."""
        source=item["_pgn_ref"]
        if source is None or not item["has_replay"]:
            raise ArchiveInput("Jogo sem PGN comprovado")
        self.replay(slug,item["id"])
        if source[0]=="cup":
            path=self.cups._dir(source[1])/"games"/(source[2]+".pgn")
            base=self.workspace
        elif source[0]=="league":
            path=self.leagues._dir(source[1])/"games"/(source[2]+".pgn")
            base=self.workspace
        elif source[0]=="arena":
            parts=source[1].split(":")
            if len(parts)!=4:
                raise ArchiveInput("ID da Arena inválido")
            matches=list((self.workspace/parts[1]/"duels").glob(
                "*/"+parts[2]+"/jogo_"+parts[3]+".pgn"))
            if len(matches)!=1:
                raise ArchiveInput("PGN da Arena ambíguo")
            path=matches[0]
            base=self.workspace
        else:
            raise ArchiveInput("PGN não suportado")
        if (not path.is_relative_to(base) or path.is_symlink() or
                path.parent.is_symlink() or not path.is_file() or
                path.stat().st_size>MAX_PGN):
            raise ArchiveInput("Arquivo PGN redirecionado ou inválido")
        raw=path.read_bytes()
        if sha256(raw).hexdigest()!=item["pgn_sha256"]:
            raise ArchiveInput("PGN alterado desde a auditoria")
        return raw

    def export(self,slug: str,raw: dict[str,list[str]],fmt: str) -> tuple[bytes,str]:
        slug=_slug(slug)
        params=normalize_query(raw)
        with self._lock:
            self._refresh()
            selected=self._filtered(slug,params)
            if fmt=="json":
                data={"schema":SCHEMA,"slug":slug,"total":len(selected),
                      "games":[self._public(x) for x in selected],
                      "note":"Projeções não contêm PGN"}
                return (json.dumps(data,ensure_ascii=False,
                                   allow_nan=False,indent=2).encode("utf-8"),
                        "application/json; charset=utf-8")
            if fmt=="pgn":
                games=[r for r in selected
                       if r["source_kind"]=="engine_full" and r["has_replay"]]
                if not games:
                    raise ArchiveInput("Nenhum PGN UCI comprovado para esses filtros")
                chunks=[self._pgn_bytes(slug,row) for row in games]
                return b"\n\n".join(chunks)+b"\n", \
                    "application/x-chess-pgn; charset=utf-8"
            if fmt=="csv":
                stream=io.StringIO()
                columns=("id","competition","kind","season","round",
                         "white","black","result","status","modality",
                         "source_kind","started_at_utc","finished_at_utc",
                         "simulated_calendar_date","pgn_sha256")
                writer=csv.DictWriter(stream,fieldnames=columns,extrasaction="ignore")
                writer.writeheader()
                for row in selected:
                    # Prefix spreadsheet formula triggers to prevent CSV injection.
                    safe={k:("'"+str(row[k]) if isinstance(row.get(k),str) and
                             row[k].startswith(("=","+","-","@")) else row.get(k))
                          for k in columns}
                    writer.writerow(safe)
                return stream.getvalue().encode("utf-8-sig"),"text/csv; charset=utf-8"
            raise ArchiveInput("Formato de exportação desconhecido")
