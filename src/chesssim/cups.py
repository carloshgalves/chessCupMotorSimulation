"""Local Cup orchestrator (#58): auditable knockout and five-player women's league.

No uncontrolled executable paths, invented UCI moves, imported ratings or
historical-strength promises. Persist only verified completed games.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import Event, RLock, Thread
import json
import re
import secrets

import chess.pgn

from .catalog import find_player
from .cup_rules import (
    CupInput, RULES_VERSION, STAGES, checksum,
    normalize, projected_game, seed_slots, stage_game, women_schedule,
)
from .cup_games import play_cup_game
from .matches import _atomic_text, file_hash, validate

SCHEMA = "chesssim-cup-v1"
RECEIPT_SCHEMA = "chesssim-cup-game-v1"
CUP_RX = re.compile(r"cup-[0-9a-f]{16}\Z")
GAME_RX = re.compile(r"(?:r[0-9]{2}-m[0-9]{2}-s[0-5]-g[12]|w[0-9]{2}-g0[12])\Z")
HEXSHA = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_URL = "https://calendar.fide.com/event_files/2644/WorldCup2025Regulations.pdf"


class CupBusy(RuntimeError):
    """The Arena cannot run another game or competition concurrently."""


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _spec_time(spec: dict) -> str:
    clock = spec["clock"]
    if spec["extra_after_40"]:
        return f"40/{clock[0]}:{spec['extra_after_40']}+{clock[1]}"
    if len(clock) == 3:
        return f"{clock[0]}/{clock[1]}+{clock[2]}"
    return f"{clock[0]}+{clock[1]}"


def _format_result(result: str, white: str, black: str) -> dict:
    if result not in ("1-0","0-1","1/2-1/2"):
        raise ValueError("Resultado sem prova")
    return {white: 1 if result == "1-0" else 0.5 if result == "1/2-1/2" else 0,
            black: 1 if result == "0-1" else 0.5 if result == "1/2-1/2" else 0}


class CupManager:
    def __init__(self, root: Path, workspace: Path, *, runner=None):
        self.root = root
        self.folder = workspace / "cups"
        if self.folder.is_symlink():
            raise ValueError("Diretório de Copas redirecionado")
        self.folder.mkdir(exist_ok=True,parents=True)
        self.runner = runner or play_cup_game
        self._lock = RLock()
        self._worker: Thread | None = None
        self._active: str | None = None
        self._stop = Event()
        self._closed = Event()
        self._pgn_cache: dict[tuple[str,str,str], tuple[int,int,int]] = {}
        self._recover_crashed_sessions()

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._active is not None

    def _dir(self, ident: str) -> Path:
        if type(ident) is not str or not CUP_RX.fullmatch(ident):
            raise CupInput("ID de Copa inválido")
        folder = self.folder / ident
        if folder.is_symlink():
            raise CupInput("Diretório de Copa redirecionado")
        return folder

    def _manifest(self, ident: str) -> dict:
        path = self._dir(ident) / "cup.json"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
            raise CupInput("Copa não encontrada")
        data = json.loads(path.read_text(encoding="utf-8"))
        if (type(data) is not dict or data.get("schema") != SCHEMA or
                data.get("id") != ident or
                data.get("status") not in ("READY","RUNNING","PAUSED","FAILED","COMPLETED") or
                type(data.get("config")) is not dict):
            raise ValueError("Manifesto da Copa incompatível")
        config = data["config"]
        if normalize({k:v for k,v in config.items()
                      if k not in ("format","rules_version","armageddon") and
                      not k.startswith("armageddon_")} |
                     {("armageddon_white_seconds" if k == "white_seconds" else
                       "armageddon_increment_seconds" if k == "increment_seconds" else
                       "armageddon_min_black_seconds" if k == "min_black_seconds" else
                       "armageddon_max_black_seconds" if k == "max_black_seconds" else k):v
                      for k,v in config["armageddon"].items()
                      if k in ("white_seconds","increment_seconds",
                               "min_black_seconds","max_black_seconds")}) != config:
            raise ValueError("Regras congeladas foram alteradas")
        resources = data.get("resources")
        if resources is not None and (not isinstance(resources,dict) or
                type(resources.get("engine_sha256")) is not str or
                not HEXSHA.fullmatch(resources["engine_sha256"]) or
                type(resources.get("profile_sha256")) is not dict or
                set(resources["profile_sha256"]) != set(config["participants"]) or
                any(type(v) is not str or not HEXSHA.fullmatch(v)
                    for v in resources["profile_sha256"].values())):
            raise ValueError("Snapshot de recursos da Copa inválido")
        return data

    def _save(self, state: dict) -> None:
        _atomic_text(self._dir(state["id"]) / "cup.json",
                     json.dumps(state,ensure_ascii=False,indent=2,allow_nan=False)+"\n")

    def _recover_crashed_sessions(self) -> None:
        for manifest in sorted(self.folder.glob("cup-*/cup.json")):
            if not CUP_RX.fullmatch(manifest.parent.name):
                continue
            data = self._manifest(manifest.parent.name)
            # A crash may happen between persisting the last immutable receipt
            # and updating cup.json. Recompute the winner from verified receipts
            # before deciding whether the interrupted season must be resumed.
            computed = self._state(data)
            if computed["champion"] is not None and data["status"] != "COMPLETED":
                data["status"] = "COMPLETED"
                data["champion"] = computed["champion"]
                data["current_game"] = None
                data["message"] = "Resultado final recuperado de recibos verificados"
                data["updated_utc"] = utc()
                self._save(data)
            elif data["status"] == "RUNNING":
                data["status"] = "PAUSED"
                data["current_game"] = None
                data["message"] = "Execução interrompida; retome manualmente"
                data["updated_utc"] = utc()
                self._save(data)

    def create(self, raw: object) -> dict:
        config = normalize(raw)
        with self._lock:
            if self._closed.is_set():
                raise CupBusy("Servidor encerrando")
            ident = "cup-" + secrets.token_hex(8)
            path = self._dir(ident)
            path.mkdir(mode=0o700,exist_ok=False)
            state={
                "schema": SCHEMA, "id": ident, "config": config,
                "status": "READY", "created_utc": utc(),
                "updated_utc": utc(), "resources": None,
                "current_game": None, "message": None,
                "champion": None, "rules_digest": checksum(config),
            }
            self._save(state)
            return {"id":ident,"status":"READY"}

    def _receipts(self, state: dict, *, force: bool = False) -> dict:
        directory = self._dir(state["id"]) / "receipts"
        if directory.is_symlink():
            raise ValueError("Recibos redirecionados")
        entries = {}
        if not directory.exists():
            return entries
        if not directory.is_dir():
            raise ValueError("Recibos inválidos")
        for path in sorted(directory.glob("*.json")):
            if (path.is_symlink() or path.stat().st_size > 8192 or
                    not GAME_RX.fullmatch(path.stem)):
                raise ValueError("Recibo não reconhecido")
            row=json.loads(path.read_text(encoding="utf-8"))
            if (type(row) is not dict or row.get("schema") != RECEIPT_SCHEMA or
                    row.get("cup_id") != state["id"] or row.get("id") != path.stem or
                    type(row.get("sha256")) is not str or
                    checksum({k:v for k,v in row.items() if k!="sha256"})!=row["sha256"]):
                raise ValueError("Recibo corrompido ou incompatível")
            if path.stem in entries:
                raise ValueError("Jogo repetido no histórico")
            entries[path.stem]=row
        return entries

    def _verify_game(self, state: dict, spec: dict, row: dict,
                     *, force: bool = False) -> None:
        cfg = state["config"]
        if (row.get("id") != spec["id"] or
                row.get("round") != spec["round"] or
                row.get("white") != spec["white"] or
                row.get("black") != spec["black"] or
                row.get("stage") != spec["stage"] or
                row.get("clock") != spec["clock"] or
                row.get("color_draw") != spec.get("color_draw") or
                row.get("bid_proof") != spec.get("bid_proof") or
                row.get("source") != cfg["execution_mode"] or
                row.get("result") not in ("1-0","0-1","1/2-1/2")):
            raise ValueError("Recibo não corresponde ao calendário")
        if cfg["execution_mode"] == "fast_statistical":
            if (row.get("pgn") is not None or row.get("pgn_sha256") is not None
                    or row.get("resources") is not None or
                    row.get("clock_left") is not None or
                    row.get("termination") != "SyntheticProjection" or
                    row["result"] != projected_game(cfg,spec)):
                raise ValueError("Projeção sintética adulterada ou fingindo PGN")
            return
        snapshot = state.get("resources")
        if not snapshot or row.get("resources") != {
            "engine_sha256": snapshot["engine_sha256"],
            "white_profile_sha256": snapshot["profile_sha256"][spec["white"]],
            "black_profile_sha256": snapshot["profile_sha256"][spec["black"]],
        }:
            raise ValueError("Proveniência dos perfis da Copa divergente")
        relative = f"games/{spec['id']}.pgn"
        if row.get("pgn") != relative or not HEXSHA.fullmatch(str(row.get("pgn_sha256"))):
            raise ValueError("PGN obrigatório ausente ou incompatível")
        path=self._dir(state["id"])/relative
        if path.is_symlink() or not path.is_file():
            raise ValueError("PGN ausente")
        stat=path.stat()
        signature=(stat.st_ino,stat.st_size,stat.st_mtime_ns)
        key=(state["id"],spec["id"],row["sha256"])
        if not force and self._pgn_cache.get(key)==signature:
            return
        if file_hash(path)!=row["pgn_sha256"]:
            raise ValueError("PGN mudou desde o recibo")
        with path.open(encoding="utf-8") as stream:
            pg=chess.pgn.read_game(stream)
            extra=chess.pgn.read_game(stream)
        if (pg is None or pg.errors or extra is not None or
                pg.headers.get("White") != find_player(spec["white"]).name or
                pg.headers.get("Black") != find_player(spec["black"]).name or
                pg.headers.get("Result") != row["result"] or
                pg.headers.get("Termination") != row["termination"] or
                pg.headers.get("CupGame") != spec["id"] or
                pg.headers.get("CupStage") != spec["stage"] or
                pg.headers.get("TimeControl") != _spec_time(spec) or
                pg.headers.get("RulesVersion") != cfg["rules_version"] or
                pg.headers.get("CupMatch") != spec.get("match_id") or
                pg.headers.get("Round") != str(spec["round"]) or
                pg.headers.get("EngineSHA256") != snapshot["engine_sha256"] or
                pg.headers.get("WhiteProfileSHA256") !=
                snapshot["profile_sha256"][spec["white"]] or
                pg.headers.get("BlackProfileSHA256") !=
                snapshot["profile_sha256"][spec["black"]]):
            raise ValueError("Cabeçalhos ou trajetória de PGN inválidos")
        if spec.get("color_draw") and (
                pg.headers.get("ColorDrawSHA256") !=
                spec["color_draw"]["sha256"] or
                pg.headers.get("ColorDrawPolicy") !=
                spec["color_draw"]["policy"]):
            raise ValueError("Sorteio independente de cores sem proveniência")
        if spec.get("bid_proof") and (
                pg.headers.get("ArmageddonBlackBid") !=
                str(spec["bid_proof"]["winning_black_seconds"]) or
                pg.headers.get("ArmageddonBlackDrawOdds") != "true" or
                pg.headers.get("BidPolicy") != "synthetic-deterministic-no-UCI-bid"):
            raise ValueError("Proveniência do Armagedom inválida")
        board=pg.end().board()
        if row["termination"]=="TimeForfeit":
            clocks=row.get("clock_left")
            loser=0 if board.turn else 1
            expected=("0-1" if loser==0 else "1-0")
            if board.has_insufficient_material(not board.turn):
                expected="1/2-1/2"
            if (type(clocks) is not list or len(clocks)!=2 or
                    type(clocks[loser]) not in (int,float) or
                    clocks[loser]>0 or row["result"]!=expected):
                raise ValueError("Queda de bandeira sem prova")
        else:
            outcome=board.outcome(claim_draw=True)
            if outcome is None or outcome.result()!=row["result"]:
                raise ValueError("Resultado não decorre da posição final")
        self._pgn_cache[key]=signature

    def _knockout(self,state:dict, rows:dict, *, force=False) -> dict:
        config=state["config"]
        slots=[(True,s) for s in seed_slots(config)]
        brackets=[]
        next_game=None
        expected_ids=set()
        champion=None
        games_count=0
        completed_matches=0
        for round_number in range(1,len(slots).bit_length()):
            matches=[]
            upcoming=[]
            for i in range(0,len(slots),2):
                known_a,a=slots[i]
                known_b,b=slots[i+1]
                ident=f"r{round_number:02d}-m{i//2+1:02d}"
                match={
                    "id":ident,"round":round_number,
                    "players":[a,b] if known_a and known_b else [a if known_a else None,
                                                                b if known_b else None],
                    "winner":None,"status":"WAITING",
                    "stage":None,"score":{a:0,b:0} if a and b else {},
                    "games":[],"next_game":None,
                }
                winner=None
                if known_a and known_b:
                    if a is None or b is None:
                        winner=a or b
                        match["winner"]=winner
                        match["status"]="BYE"
                    else:
                        match["status"]="PENDING"
                        for stage in range(len(STAGES)):
                            descriptor=STAGES[stage]
                            phase_games=[]
                            missing=False
                            for game_index in range(descriptor["games"]):
                                spec=stage_game(match,config,stage,game_index)
                                entry=rows.get(spec["id"])
                                if entry is None:
                                    if next_game is None:
                                        next_game=spec
                                    match["next_game"]=spec
                                    match["stage"]=descriptor["key"]
                                    missing=True
                                    break
                                expected_ids.add(spec["id"])
                                self._verify_game(state,spec,entry,force=force)
                                phase_games.append(entry)
                                games_count+=1
                                match["games"].append({
                                    "id":spec["id"], "stage":spec["stage"],
                                    "white":spec["white"],"black":spec["black"],
                                    "result":entry["result"],
                                    "source":entry["source"],"pgn":entry["pgn"],
                                    "bid_proof":entry.get("bid_proof"),
                                    "color_draw":entry.get("color_draw"),
                                    "finished_utc":entry["finished_utc"],
                                })
                                scored=_format_result(entry["result"],spec["white"],spec["black"])
                                for slug,points in scored.items():
                                    match["score"][slug]+=points
                            if missing:
                                break
                            if stage==len(STAGES)-1:
                                arm=phase_games[0]
                                winner=(stage_game(match,config,stage,0)["black"]
                                        if arm["result"]=="1/2-1/2" else
                                        arm["white"] if arm["result"]=="1-0" else arm["black"])
                                match["status"]="COMPLETED"
                                match["stage"]=descriptor["key"]
                                break
                            score_a=sum(_format_result(e["result"],e["white"],e["black"])[a]
                                        for e in phase_games)
                            score_b=sum(_format_result(e["result"],e["white"],e["black"])[b]
                                        for e in phase_games)
                            if score_a!=score_b:
                                winner=a if score_a>score_b else b
                                match["status"]="COMPLETED"
                                match["stage"]=descriptor["key"]
                                break
                        if winner:
                            completed_matches+=1
                            match["winner"]=winner
                            match["next_game"]=None
                matches.append(match)
                upcoming.append((winner is not None,winner))
            brackets.append({"round":round_number,"matches":matches})
            slots=upcoming
            if len(matches)==1:
                champion=matches[0]["winner"]
        if set(rows)!=expected_ids:
            raise ValueError("Existem jogos não programados ou duplicados após conclusão")
        return {"bracket":brackets,"champion":champion,
                "completed_matches":completed_matches,
                "completed_games":games_count,
                "total_matches":len(config["participants"])-1,
                "next_game":next_game}

    def _women(self,state:dict,rows:dict,*,force=False) -> dict:
        config=state["config"]
        rounds=women_schedule(config)
        used=set()
        standings={
            slug:{"slug":slug,"name":find_player(slug).name,
                  "played":0,"wins":0,"draws":0,"losses":0,"points":0}
            for slug in config["participants"]
        }
        details=[]
        results=[]
        next_game=None
        for rnd in rounds:
            games=[]
            for spec in rnd["games"]:
                entry=rows.get(spec["id"])
                if entry:
                    self._verify_game(state,spec,entry,force=force)
                    used.add(spec["id"])
                    results.append((spec,entry))
                    scores=_format_result(entry["result"],spec["white"],spec["black"])
                    for slug,value in scores.items():
                        item=standings[slug]
                        item["played"]+=1
                        item["points"]+=3 if value==1 else 1 if value==.5 else 0
                        item["wins"]+=value==1
                        item["draws"]+=value==.5
                        item["losses"]+=value==0
                elif next_game is None:
                    next_game=spec
                games.append({
                    **spec,"status":"COMPLETED" if entry else "PENDING",
                    "result":entry["result"] if entry else None,
                    "source":entry["source"] if entry else None,
                    "pgn":entry["pgn"] if entry else None,
                })
            details.append({"round":rnd["round"],"bye":rnd["bye"],"games":games})
        if set(rows)!=used:
            raise ValueError("Jogos femininos fora do calendário")
        # Every ranked row uses completed results only. Head-to-head is
        # meaningful for two tied players only after BOTH encounters ended.
        for item in standings.values():
            item["sb"] = 0.0
            item["h2h"] = None
        h2h = {}
        for spec,entry in results:
            pair = frozenset((spec["white"],spec["black"]))
            h2h.setdefault(pair,[]).append((spec,entry))
            points = _format_result(entry["result"],spec["white"],spec["black"])
            for slug, opponent in ((spec["white"],spec["black"]),
                                   (spec["black"],spec["white"])):
                standings[slug]["sb"] += points[slug]*standings[opponent]["points"]
        groups = {}
        for item in standings.values():
            groups.setdefault((item["points"],item["wins"]),[]).append(item)
        table=[]
        for group_key in sorted(groups,reverse=True):
            members=groups[group_key]
            if len(members)==2:
                pair=frozenset(x["slug"] for x in members)
                encounters=h2h.get(pair,[])
                if len(encounters)==2:
                    for member in members:
                        member["h2h"]=sum(
                            3 if _format_result(e["result"],sp["white"],sp["black"])[
                                member["slug"]]==1 else
                            1 if _format_result(e["result"],sp["white"],sp["black"])[
                                member["slug"]]==.5 else 0
                            for sp,e in encounters)
                    members.sort(key=lambda x:(-x["h2h"],-x["sb"],x["slug"]))
                else:
                    members.sort(key=lambda x:(-x["sb"],x["slug"]))
            else:
                members.sort(key=lambda x:(-x["sb"],x["slug"]))
            table.extend(members)
        for rank,item in enumerate(table,1):
            item["rank"]=rank
        champion=table[0]["slug"] if next_game is None else None
        return {"rounds":details,"standings":table,"champion":champion,
                "completed_games":len(used),"total_games":20,
                "total_matches":20,"completed_matches":len(used),
                "next_game":next_game}

    def _state(self,state:dict,*,force=False) -> dict:
        if state.get("rules_digest")!=checksum(state["config"]):
            raise ValueError("Regras da Copa divergiram da configuração original")
        rows=self._receipts(state)
        if state["config"]["preset"]=="women":
            computed=self._women(state,rows,force=force)
        else:
            computed=self._knockout(state,rows,force=force)
        if state["status"] == "COMPLETED" and (
                computed["champion"] is None or
                state.get("champion") != computed["champion"]):
            raise ValueError("Campeão persistido diverge dos resultados comprovados")
        return {**computed,"game_receipts":rows}

    def _preflight(self,config:dict) -> dict:
        engine_sha=None
        profiles={}
        for slug in config["participants"]:
            player=find_player(slug)
            try:
                exe, paths=validate(self.root,player,player)
            except (OSError,ValueError) as exc:
                raise CupInput(
                    "Perfis Rodent IV não instalados; selecione apenas perfis "
                    "executáveis ou use Projeção estatística (sem PGN).") from exc
            current=file_hash(exe)
            if engine_sha is None:
                engine_sha=current
            elif current!=engine_sha:
                raise CupInput("Executável diferente detectado")
            profiles[slug]=file_hash(paths[slug])
        return {"engine_sha256":engine_sha, "profile_sha256":profiles}

    def _persist_game(self,state:dict,spec:dict,game,meta:dict,
                      *, started_utc:str) -> None:
        method=state["config"]["execution_mode"]
        result=meta["result"]
        if meta["status"]!="COMPLETED" or result not in (
                "1-0","0-1","1/2-1/2"):
            raise RuntimeError("Partida inconclusiva; não será pontuada")
        directory=self._dir(state["id"])
        if not GAME_RX.fullmatch(spec["id"]):
            raise RuntimeError("ID de partida inválido")
        path=directory/"receipts"/(spec["id"]+".json")
        if path.is_symlink() or path.exists():
            raise ValueError("Recibo não pode ser sobrescrito")
        pgn_rel=None
        pgn_sha=None
        resources=None
        if method=="engine_full":
            if game is None or game.errors or game.headers.get("Result")!=result:
                raise ValueError("Jogo UCI não apresenta resultado comprovado")
            pgn_rel="games/"+spec["id"]+".pgn"
            pgn_file=directory/pgn_rel
            if pgn_file.is_symlink():
                raise ValueError("PGN foi redirecionado")
            pgn_file.parent.mkdir(exist_ok=True)
            if pgn_file.exists():
                # Old orphan after a crash is never silently treated as a game.
                orphan=pgn_file.with_name(spec["id"]+".orphan-"+file_hash(pgn_file)[:12]+".pgn")
                if orphan.exists():
                    raise ValueError("Checkpoint órfão duplicado")
                pgn_file.rename(orphan)
            _atomic_text(pgn_file,str(game)+"\n")
            pgn_sha=file_hash(pgn_file)
            resources={
                "engine_sha256":state["resources"]["engine_sha256"],
                "white_profile_sha256":state["resources"]["profile_sha256"][spec["white"]],
                "black_profile_sha256":state["resources"]["profile_sha256"][spec["black"]],
            }
        row={
            "schema":RECEIPT_SCHEMA,"cup_id":state["id"],
            "id":spec["id"],"round":spec["round"],"stage":spec["stage"],
            "white":spec["white"],"black":spec["black"],
            "clock":spec["clock"],"bid_proof":spec.get("bid_proof"),
            "color_draw":spec.get("color_draw"),
            "source":method,"result":result,"termination":meta["termination"],
            "clock_left":meta.get("clock_left"),"pgn":pgn_rel,
            "pgn_sha256":pgn_sha,"resources":resources,
            "started_utc":started_utc,"finished_utc":utc(),
        }
        row["sha256"]=checksum(row)
        path.parent.mkdir(exist_ok=True)
        _atomic_text(path,json.dumps(row,ensure_ascii=False,indent=2,
                                     sort_keys=True,allow_nan=False)+"\n")

    def _next(self,state:dict) -> dict:
        computed=self._state(state)
        if computed["champion"]:
            state["status"]="COMPLETED"
            state["champion"]=computed["champion"]
            state["message"]="Copa concluída"
            self._save(state)
        return computed

    def run(self, ident:str,*, arena_busy=False) -> dict:
        with self._lock:
            if self.busy or arena_busy or self._closed.is_set():
                raise CupBusy("Já existe partida, liga ou Copa em execução")
            state=self._manifest(ident)
            computed=self._state(state)
            spec=computed["next_game"]
            if not spec or computed["champion"]:
                raise CupInput("Copa não possui partida pendente")
            config=state["config"]
            if config["execution_mode"]=="engine_full":
                resources=self._preflight(config)
                if state["resources"] is not None and resources!=state["resources"]:
                    raise CupInput("Motor ou perfis mudaram desde o início da Copa")
                state["resources"]=resources
            state["status"]="RUNNING"
            state["current_game"]=spec["id"]
            state["message"]=None
            state["updated_utc"]=utc()
            self._save(state)
            self._stop=Event()
            self._active=ident
            thread=Thread(target=self._play,args=(ident,spec,self._stop),
                          name="chesssim-cup-game",daemon=True)
            self._worker=thread
            thread.start()
            return {"id":ident,"status":"RUNNING","game":spec["id"],
                    "stage":spec["stage"]}

    def _play(self,ident:str,spec:dict,stop:Event) -> None:
        try:
            with self._lock:
                state=self._manifest(ident)
                config=state["config"]
            started=utc()
            if config["execution_mode"]=="fast_statistical":
                game=None
                result=projected_game(config,spec)
                meta={"result":result,"status":"COMPLETED",
                      "termination":"SyntheticProjection",
                      "clock_left":None}
            else:
                game,meta=self.runner(
                    self.root,spec,config,state["resources"],
                    lambda:stop.is_set() or self._closed.is_set())
            if stop.is_set() and meta["status"]!="COMPLETED":
                with self._lock:
                    state=self._manifest(ident)
                    state["status"]="PAUSED"
                    state["message"]="Jogo incompleto não foi contabilizado"
                    state["current_game"]=None
                    state["updated_utc"]=utc()
                    self._save(state)
                return
            with self._lock:
                state=self._manifest(ident)
                self._persist_game(state,spec,game,meta,started_utc=started)
                computed=self._state(state)
                state["current_game"]=None
                state["status"]="COMPLETED" if computed["champion"] else "PAUSED"
                state["champion"]=computed["champion"]
                state["message"]=("Campeão definido" if computed["champion"]
                                  else "Partida concluída; avance quando desejar")
                state["updated_utc"]=utc()
                self._save(state)
        except Exception as exc:
            with self._lock:
                state=self._manifest(ident)
                state["status"]="FAILED"
                state["current_game"]=None
                state["message"]="Falha; jogo não comprovado não foi pontuado"
                state["updated_utc"]=utc()
                self._save(state)
            print("Falha na Copa local: "+type(exc).__name__+": "+str(exc),
                  flush=True)
        finally:
            with self._lock:
                self._active=None
                self._worker=None

    def pause(self,ident:str) -> dict:
        with self._lock:
            if self._active!=ident:
                raise CupBusy("Nenhuma partida desta Copa em execução")
            self._stop.set()
            return {"id":ident,"pause_requested":True,
                    "message":"Parada solicitada; partidas incompletas não pontuam"}

    def detail(self,ident:str,*,compact:bool=False) -> dict:
        with self._lock:
            state=self._manifest(ident)
            summary=self._state(state)
            cfg=state["config"]
            total=summary["total_matches"]
            overview={
                "schema":SCHEMA,"id":ident,"name":cfg["name"],"edition":cfg["edition"],
                "preset":cfg["preset"],"format":cfg["format"],
                "execution_mode":cfg["execution_mode"],
                "participants_count":len(cfg["participants"]),
                "completed_games":summary["completed_games"],
                "completed_matches":summary["completed_matches"],
                "total_matches":total,"champion":summary["champion"],
                "status":state["status"],"current_game":state["current_game"],
                "message":state.get("message"),
                "available_pgn":sum(bool(x["pgn"]) for x in summary["game_receipts"].values()),
            }
            if compact:
                return overview
            rows=summary.pop("game_receipts")
            return {
                **overview,**summary,"config":cfg,"created_utc":state["created_utc"],
                "updated_utc":state["updated_utc"],
                "next_game":summary["next_game"],
                "source_warning":("Projeção sintética sem jogo UCI/PGN/Elo"
                                  if cfg["execution_mode"]=="fast_statistical" else
                                  "Motor Rodent IV: fidelidade histórica não comprovada"),
                "presets_version":RULES_VERSION,
                "recent":[{
                    "id":r["id"],"round":r["round"],"stage":r["stage"],
                    "white":r["white"],"black":r["black"],"result":r["result"],
                    "pgn":bool(r["pgn"]), "source":r["source"],
                    "finished_utc":r["finished_utc"],
                } for r in list(rows.values())[-40:]]
            }

    def list(self) -> dict:
        with self._lock:
            leagues=[]
            for file in sorted(self.folder.glob("cup-*/cup.json"),
                               key=lambda p:p.stat().st_mtime,reverse=True):
                if CUP_RX.fullmatch(file.parent.name):
                    leagues.append(self.detail(file.parent.name,compact=True))
            return {"schema":SCHEMA,"cups":leagues}

    def export(self,ident:str,fmt:str) -> tuple[bytes,str]:
        with self._lock:
            state=self._manifest(ident)
            self._pgn_cache.clear()
            summary=self._state(state,force=True)
            rows=summary.pop("game_receipts")
            if fmt=="json":
                return (json.dumps({
                    "schema":SCHEMA,
                    "cup":self.detail(ident),
                    "completed_games":list(rows.values()),
                    "source":SOURCE_URL,
                },ensure_ascii=False,indent=2,allow_nan=False).encode("utf-8"),
                        "application/json; charset=utf-8")
            if fmt!="pgn":
                raise CupInput("Formato de exportação desconhecido")
            games=[x for x in rows.values() if x["pgn"]]
            if not games:
                raise CupInput("Não existem PGNs reais comprovados")
            content=[]
            for entry in games:
                p=self._dir(ident)/entry["pgn"]
                content.append(p.read_text(encoding="utf-8"))
            return ("\n".join(content).encode("utf-8"),
                    "application/x-chess-pgn; charset=utf-8")

    def replay(self,ident:str,game_id:str) -> dict:
        with self._lock:
            if not GAME_RX.fullmatch(game_id):
                raise CupInput("Identificador de partida inválido")
            state=self._manifest(ident)
            summary=self._state(state)
            entry=summary["game_receipts"].get(game_id)
            if not entry or not entry["pgn"]:
                raise CupInput("Partida real não disponível para replay")
            path=self._dir(ident)/entry["pgn"]
            with path.open(encoding="utf-8") as stream:
                game=chess.pgn.read_game(stream)
            board=game.board()
            states=[{"fen":board.fen(),"last":None,"turn":"white"}]
            moves=[]
            for node in game.mainline():
                uci=node.move.uci()
                san=board.san(node.move)
                board.push(node.move)
                moves.append({"san":san,"uci":uci,"clock":node.clock()})
                states.append({"fen":board.fen(),"last":[uci[:2],uci[2:4]],
                               "turn":"white" if board.turn else "black"})
            return {"schema":"chesssim-cup-replay-v1","game":entry,
                    "states":states,"moves":moves}

    def shutdown(self) -> None:
        self._closed.set()
        with self._lock:
            self._stop.set()
            worker=self._worker
        if worker is not None:
            # Keep the workspace execution lock until the UCI worker stops.
            # Releasing the lock while an old worker writes receipts is unsafe.
            worker.join()
