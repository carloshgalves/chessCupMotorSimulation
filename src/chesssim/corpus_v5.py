"""v0.5: acquire and merge publicly available career PGNs with evidence trails.

PGN Mentor is a source, NOT a claim of completeness. Network is used only
when the user explicitly invokes `corpus-fetch`; calibration is offline.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path
import re
import time
from urllib.request import Request, urlopen
from zipfile import ZipFile

from .catalog import load_roster
from .historical import player_side

CATALOG_URL = "https://www.pgnmentor.com/files.html"
SOURCE = "https://www.pgnmentor.com/players/"
# Filename exceptions; every filename is checked against the live official index.
NAMES = {
    "mequinho":"Mecking", "ding-liren":"Ding", "vachier-lagrave":"VachierLagrave",
    "wesley-so":"So", "judit-polgar":"PolgarJ", "hou-yifan":"Hou",
    "ivanchuk":"Ivanchuk", "korchnoi":"Korchnoi", "judit-polgar":"PolgarJ",
    "philidor":"Philidor", "steinitz":"Steinitz", "caruana":"Caruana",
    "kamsky":"Kamsky", "gligoric":"Gligoric", "nepomniachtchi":"Nepomniachtchi",
    "aronian":"Aronian", "gelfand":"Gelfand", "chiburdanidze":"Chiburdanidze",
    "gaprindashvili":"Gaprindashvili", "mamedyarov":"Mamedyarov",
    "botvinnik":"Botvinnik", "capablanca":"Capablanca", "nimzowitsch":"Nimzowitsch",
    "reti":"Reti", "topalov":"Topalov", "grischuk":"Grischuk",
    "fischer":"Fischer", "najdorf":"Najdorf", "reshevsky":"Reshevsky",
    "polugaevsky":"Polugaevsky", "kasparov":"Kasparov", "karpov":"Karpov",
    "kortchnoi":"Kortchnoi", "shirov":"Shirov", "timman":"Timman",
    "smyslov":"Smyslov", "euwe":"Euwe", "tal":"Tal", "spassky":"Spassky",
    "petrosian":"Petrosian", "kramnik":"Kramnik", "anand":"Anand",
    "carlsen":"Carlsen", "menchik":"Menchik", "svidler":"Svidler",
    "short":"Short", "leko":"Leko", "larsen":"Larsen",
    "morphy":"Morphy", "anderssen":"Anderssen", "zukertort":"Zukertort",
    "pillsbury":"Pillsbury", "chigorin":"Chigorin", "tarrasch":"Tarrasch",
    "marshall":"Marshall", "rubinstein":"Rubinstein", "keres":"Keres",
    "bronstein":"Bronstein", "geller":"Geller", "portisch":"Portisch",
    "taimanov":"Taimanov", "staunton":"Staunton", "lasker":"Lasker",
    "alekhine":"Alekhine", "karjakin":"Karjakin", "nakamura":"Nakamura",
}


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.filenames = set()
    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href", "")
        match = re.search(r"(?:^|/)players/([A-Za-z0-9_-]+)\.zip(?:[?#].*)?$", href, re.I)
        if match:
            self.filenames.add(match.group(1))


def _request(url: str, limit: int) -> bytes:
    with urlopen(Request(url, headers={"User-Agent":"ChessSimulation64/0.5 (academic personal research)"}), timeout=60) as res:
        payload = res.read(limit + 1)
    if len(payload) > limit:
        raise ValueError(f"Remote resource exceeded byte limit: {url}")
    return payload


def source_catalog() -> dict:
    html = _request(CATALOG_URL, 5_000_000)
    parser = _Links()
    parser.feed(html.decode("utf-8", "replace"))
    if not parser.filenames:
        raise ValueError("PGN Mentor index has no player ZIP entries: website markup may have changed")
    return {"retrieved_utc":datetime.now(timezone.utc).isoformat(),
            "index_url":CATALOG_URL, "index_sha256":sha256(html).hexdigest(),
            "filenames": sorted(parser.filenames)}


def roster_sources(index: dict) -> list[dict]:
    available = {name.casefold(): name for name in index["filenames"]}
    result = []
    for p in load_roster():
        hint = NAMES.get(p.slug, p.slug.replace("-", "").capitalize())
        found = available.get(hint.casefold())
        result.append({"slug":p.slug, "player":p.name,
                       "available":bool(found), "source_file":f"{found}.zip" if found else None,
                       "url":SOURCE + f"{found}.zip" if found else None})
    return result


def fetch_profiles(destination: Path, *, slugs: list[str], dry_run: bool = False,
                   delay: float = 2.0, skip_existing: bool = True,
                   index: dict | None = None, get_payload=None) -> dict:
    if delay < 0:
        raise ValueError("delay cannot be negative")
    catalog = index or source_catalog()
    available = {item["slug"]: item for item in roster_sources(catalog)}
    if slugs == ["all"]:
        targets = list(available)
    else:
        if not slugs or len(set(slugs)) != len(slugs):
            raise ValueError("Provide unique roster slugs, or 'all'")
        targets = slugs
    report = {"index_sha256":catalog.get("index_sha256"), "index_url":CATALOG_URL,
              "entries":[], "notice":"These are publicly indexed recordings, NOT all games ever played."}
    downloader = get_payload or (lambda url: _request(url, 50_000_000))
    completed = 0
    for slug in targets:
        if slug not in available:
            raise ValueError(f"Unknown ChessSimulation64 roster slug: {slug}")
        item = available[slug]
        row = dict(item)
        output = destination / f"{slug}.pgn"
        row["output"] = str(output)
        if not item["available"]:
            row["status"] = "NOT_LISTED_ON_SOURCE"
        elif output.exists() and skip_existing:
            row["status"] = "EXISTS_NOT_OVERWRITTEN"
        elif output.exists():
            raise FileExistsError(output)
        elif dry_run:
            row["status"] = "PREVIEW"
        else:
            if completed and delay:
                time.sleep(delay)
            archive = downloader(item["url"])
            with ZipFile(BytesIO(archive)) as z:
                names = [info for info in z.infolist()
                         if info.filename.lower().endswith(".pgn") and not info.is_dir()]
                if len(names) != 1 or names[0].file_size > 150_000_000:
                    raise ValueError(f"Unexpected PGN archive for {slug}")
                data = z.read(names[0])
            if b"[White " not in data or b"[Black " not in data:
                raise ValueError(f"Invalid PGN source for {slug}")
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("xb") as stream:
                stream.write(data)
            row.update({"status":"FETCHED", "archive_sha256":sha256(archive).hexdigest(),
                        "pgn_sha256":sha256(data).hexdigest(), "bytes":len(data)})
            output.with_suffix(".source.json").write_text(json.dumps(row,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
            completed += 1
        report["entries"].append(row)
    return report


def _pgn_inputs(paths: list[Path]) -> list[Path]:
    inputs = []
    for path in paths:
        if path.is_dir():
            inputs.extend(sorted(path.glob("*.pgn")))
        elif path.is_file() and path.suffix.lower() == ".pgn":
            inputs.append(path)
        else:
            raise ValueError(f"Source must be an existing .pgn file or folder: {path}")
    return sorted({p.resolve() for p in inputs})


def make_corpus(sources: list[Path], output: Path, *, match_name: str) -> dict:
    """Normalize, remove duplicate mainlines, and preserve source audit trail.

    Accept local PGN files or directories. Source archives must be explicitly
    acquired separately. Never overwrite the original research dataset.
    """
    import chess.pgn
    if output.exists() or output.with_suffix(".manifest.json").exists():
        raise FileExistsError(output)
    if not match_name.strip():
        raise ValueError("match-name required")
    files = _pgn_inputs(sources)
    if not files:
        raise ValueError("No PGN inputs")
    candidates = []
    errors = Counter()
    seen = {}
    stats = []
    for path in files:
        accepted = 0
        with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
            while (game := chess.pgn.read_game(stream)) is not None:
                if game.errors:
                    errors["malformed"] += 1
                    continue
                if player_side(game, match_name) is None:
                    errors["different_player"] += 1
                    continue
                if game.headers.get("Variant", "Standard") not in ("Standard", "Chess"):
                    errors["non_standard_variant"] += 1
                    continue
                moves = [m.uci() for m in game.mainline_moves()]
                if len(moves) < 12:
                    errors["too_short"] += 1
                    continue
                identity = (" ".join(moves),
                            *(' '.join(game.headers.get(h,'?').casefold().split())
                              for h in ('Date','White','Black','Event','Site','Round')))
                key = sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()
                if key in seen:
                    errors["duplicate"] += 1
                    seen[key]["source_files"].append(str(path))
                    continue
                record = {"id":key,"source_files":[str(path)],
                          "date":game.headers.get("Date", "?"),
                          "event":game.headers.get("Event", "?"),
                          "white":game.headers.get("White", "?"),
                          "black":game.headers.get("Black", "?")}
                seen[key] = record
                # PGN exporter strips private annotations and retains mainline.
                candidates.append((key,game.accept(chess.pgn.StringExporter(headers=True,variations=False,comments=False))))
                accepted += 1
        stats.append({"source":str(path),"sha256":sha256(path.read_bytes()).hexdigest(),"unique_added":accepted})
    if not candidates:
        raise ValueError("No valid games for selected player")
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open("x",encoding="utf-8") as f:
        for _, text in sorted(candidates):
            f.write(text.rstrip()+"\n\n")
    manifest = {"version":"corpus-v5", "match_name":match_name,
                "completeness":"UNKNOWN: games recorded in supplied sources only",
                "sources":stats,"unique_games":len(candidates),
                "excluded":dict(errors),"output":str(output),
                "pgn_sha256":sha256(output.read_bytes()).hexdigest(),
                "games": [seen[key] for key, _ in sorted(candidates)]}
    output.with_suffix(".manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return {key:value for key,value in manifest.items() if key != "games"}


def extract_decisions_v5(pgn: Path, *, player_name: str, from_year: int,
                         to_year: int, min_fullmove: int = 9,
                         max_per_game: int = 120) -> tuple[list[dict],dict]:
    """Read all eligible career games, distinguishing identical move sequences
    played at different dated events. `legacy_key` carries old v3 move-only ID
    to embargo its earlier held-out groups, including source duplicates.
    """
    import chess.pgn
    seen=set()
    decisions=[]
    excluded=Counter()
    year_histogram=Counter()
    games=0
    with pgn.open(encoding='utf-8-sig',errors='replace') as stream:
        while (g := chess.pgn.read_game(stream)) is not None:
            if g.errors:
                excluded['malformed']+=1
                continue
            side=player_side(g,player_name)
            if side is None:
                excluded['other_player_or_ambiguous']+=1
                continue
            yearmatch=re.match(r'^(\d{4})',g.headers.get('Date',''))
            if not yearmatch:
                excluded['unknown_year']+=1
                continue
            year=int(yearmatch.group(1))
            if year<from_year or year>to_year:
                excluded['outside_period']+=1
                continue
            if g.headers.get('Variant','Standard') not in ('Standard','Chess'):
                excluded['variant']+=1
                continue
            moves=[m.uci() for m in g.mainline_moves()]
            if len(moves)<16:
                excluded['too_short']+=1
                continue
            uci=' '.join(moves)
            legacy_key=sha256(uci.encode()).hexdigest()
            def clean(tag):
                return ' '.join(g.headers.get(tag,'?').casefold().split())
            identity=(legacy_key,clean('Date'),clean('White'),clean('Black'),
                      clean('Event'),clean('Site'),clean('Round'))
            key=sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()
            if key in seen:
                excluded['duplicate_metadata_and_moves']+=1
                continue
            seen.add(key)
            board=g.board()
            rows=[]
            for move in g.mainline_moves():
                if board.turn==side and board.fullmove_number>=min_fullmove and not board.is_game_over():
                    if len(list(board.legal_moves))>1:
                        rows.append({'fen':board.fen(), 'move':move.uci(), 'game':key,
                            'legacy_key':legacy_key, 'year':year,
                            'color':'white' if side else 'black',
                            'fullmove':board.fullmove_number,
                            'event':g.headers.get('Event','?'),
                            'time_control':g.headers.get('TimeControl','?'),
                            'speed_tag':g.headers.get('EventType', g.headers.get('Speed', '')),
                            'date':g.headers.get('Date','????.??.??')})
                board.push(move)
            if len(rows)<8:
                excluded['too_few_decisions']+=1
                continue
            if len(rows)>max_per_game:
                rows=[rows[round(i*(len(rows)-1)/(max_per_game-1))] for i in range(max_per_game)]
            decisions.extend(rows)
            games+=1
            year_histogram[year]+=1
    if not decisions:
        raise ValueError('No eligible historical decisions in corpus')
    return decisions,{'games_accepted':games,'decisions':len(decisions),
        'rejected':dict(excluded),'year_range':[from_year,to_year],
        'years':dict(sorted(year_histogram.items())),
        'pgn_sha256':sha256(pgn.read_bytes()).hexdigest(),
        'identity':'date+players+event+site+round+mainline, with legacy move-only key for exclusion'}
