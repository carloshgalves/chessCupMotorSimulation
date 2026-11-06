"""Biographical packs for the local History tab; never an engine-style endorsement."""
from __future__ import annotations

from importlib.resources import files
from hashlib import sha256
import json
import re
from urllib.parse import urlsplit

from .catalog import load_roster

SCHEMA = "history-profile-v1"
INDEX_SCHEMA = "history-index-v1"
VOICE = "first_person_editorial"
_REQUIRED = {
    "schema", "slug", "review_status", "narrative_voice", "short_bio",
    "era_label", "portrait", "highlights", "timeline", "sources",
}
_FIRST_PERSON = (
    "Fui ", "Venci ", "Conquistei ", "Derrotei ", "Defendi ",
    "Mantive ", "Disputei ", "Publiquei ", "Tornei-me ",
    "Alcancei ", "Recebi ", "Recuperei ", "Voltei ", "Concluí ",
    "Joguei ", "Desenvolvi ", "Ajudei ", "Comecei ",
    "Dividi ", "Empatei ", "Realizei ", "Terminei ",
)


def _text(value: object, name: str, *, max_len: int = 1200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        raise ValueError(f"Campo biográfico inválido: {name}")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise ValueError(f"Caracteres inválidos: {name}")
    return value


def _url(value: object, name: str) -> str:
    s = _text(value, name, max_len=1000)
    parsed = urlsplit(s)
    if (parsed.scheme != "https" or not parsed.hostname or
            parsed.username or parsed.password or parsed.fragment or
            not s.startswith("https://")):
        raise ValueError(f"Fonte HTTPS inválida: {name}")
    return s


def _assert_year(value: object) -> None:
    if type(value) is not int or not 1700 <= value <= 2026:
        raise ValueError("Ano histórico inválido")


def validate_history_pack(data: object, *, slug: str) -> dict:
    """Validate self-contained history content before making it available over HTTP."""
    if not isinstance(data, dict) or set(data) != _REQUIRED:
        raise ValueError("Schema de história incompleto ou desconhecido")
    if data["schema"] != SCHEMA or data["slug"] != slug:
        raise ValueError("Pacote incompatível com o catálogo")
    if data["review_status"] not in {"pending", "reviewed"} or data["narrative_voice"] != VOICE:
        raise ValueError("Estado editorial ou voz narrativa inválidos")
    for field in ("short_bio", "era_label"):
        if data[field] is not None:
            _text(data[field], field, max_len=1500 if field == "short_bio" else 100)
    if data["short_bio"] and not data["short_bio"].startswith(_FIRST_PERSON):
        raise ValueError("Biografia deve ser escrita em primeira pessoa")
    portrait = data["portrait"]
    if portrait is not None:
        required = {"local_asset", "alt", "author", "license", "source_url",
                    "verified_at", "event", "event_date", "rights_holder",
                    "license_url", "sha256", "derivative_note", "approval_status"}
        if not isinstance(portrait, dict) or set(portrait) != required:
            raise ValueError("Retrato sem proveniência e direitos completos")
        if portrait["approval_status"] != "approved":
            raise ValueError("Retrato sem aprovação")
        path = _text(portrait["local_asset"], "portrait.local_asset")
        if path != f"history/portraits/{slug}.jpg":
            raise ValueError("Caminho de retrato não corresponde ao jogador")
        allowed = {"CC0 1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
                   "CC BY-SA 3.0": "https://creativecommons.org/licenses/by-sa/3.0/",
                   "CC BY-SA 2.0": "https://creativecommons.org/licenses/by-sa/2.0/",
                   "CC BY 2.0": "https://creativecommons.org/licenses/by/2.0/",
                   "Public Domain Mark 1.0": "https://creativecommons.org/publicdomain/mark/1.0/"}
        if (portrait["license"] not in allowed or
                portrait["license_url"] != allowed[portrait["license"]]):
            raise ValueError("Licença incompatível")
        for field in ("alt", "author", "verified_at", "event", "event_date",
                      "rights_holder", "derivative_note"):
            _text(portrait[field], "portrait." + field)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", portrait["verified_at"]):
            raise ValueError("Data de conferência inválida")
        url = _url(portrait["source_url"], "portrait.source_url")
        parsed = urlsplit(url)
        archive = (parsed.hostname == "www.nationaalarchief.nl" and
                   re.fullmatch(r"/onderzoeken/fotocollectie/[0-9a-f-]{36}", parsed.path))
        holland = (parsed.hostname == "noord-hollandsarchief.nl" and
                   re.fullmatch(r"/beelden/beeldbankdeboer/detail/[0-9a-f-]{36}", parsed.path))
        commons = (parsed.hostname == "commons.wikimedia.org" and
                   parsed.path.startswith("/wiki/File:"))
        flickr = (parsed.hostname == "www.flickr.com" and
                  re.fullmatch(r"/photos/(?:8022405@N02|karpidis|suspeeters)/[0-9]{8,20}/?", parsed.path))
        cleveland = (parsed.hostname == "cplorg.contentdm.oclc.org" and
                     re.fullmatch(r"/digital/collection/p4014coll20/id/[0-9]{1,6}/?", parsed.path))
        met = (parsed.hostname == "www.metmuseum.org" and
               parsed.path == "/art/collection/search/423124")
        if not (archive or holland or commons or flickr or cleveland or met):
            raise ValueError("Origem do retrato não pertence a acervo auditado")
        digest = _text(portrait["sha256"], "portrait.sha256", max_len=64)
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("SHA-256 de retrato inválido")
        resource = files("chesssim.data").joinpath(*path.split("/"))
        if not resource.is_file():
            raise ValueError("Arquivo local de retrato inexistente")
        binary = resource.read_bytes()
        if len(binary) > 500_000 or sha256(binary).hexdigest() != digest:
            raise ValueError("Retrato alterado ou muito grande")
    sources = data["sources"]
    if not isinstance(sources, list) or len(sources) > 32:
        raise ValueError("Fontes inválidas")
    source_ids = set()
    for src in sources:
        if not isinstance(src, dict) or set(src) != {
                "id", "title", "publisher", "url", "accessed_at"}:
            raise ValueError("Fonte sem metadados obrigatórios")
        source_id = _text(src["id"], "source.id", max_len=60)
        if source_id in source_ids:
            raise ValueError("Fonte duplicada")
        source_ids.add(source_id)
        for name in ("title", "publisher", "accessed_at"):
            _text(src[name], f"source.{name}")
        _url(src["url"], "source.url")
    highlights = data["highlights"]
    timeline = data["timeline"]
    if not isinstance(highlights, list) or len(highlights) > 16:
        raise ValueError("Maiores feitos inválidos")
    if not isinstance(timeline, list) or len(timeline) > 32:
        raise ValueError("Cronologia inválida")
    ids = set()
    for idx, record in enumerate(highlights):
        if not isinstance(record, dict) or set(record) != {"id", "year", "text", "source_ids"}:
            raise ValueError("Feito com schema inválido")
        event_id = _text(record["id"], "highlight.id", max_len=80)
        if event_id in ids:
            raise ValueError("Feito repetido")
        ids.add(event_id)
        _assert_year(record["year"])
        if not _text(record["text"], f"highlight[{idx}].text").startswith(_FIRST_PERSON):
            raise ValueError("Feito deve usar primeira pessoa")
        _check_sources(record["source_ids"], source_ids)
    last_year = 1700
    for record in timeline:
        if not isinstance(record, dict) or set(record) != {
                "year", "title", "summary", "source_ids"}:
            raise ValueError("Marco cronológico inválido")
        _assert_year(record["year"])
        if record["year"] < last_year:
            raise ValueError("Cronologia não ordenada")
        last_year = record["year"]
        _text(record["title"], "timeline.title")
        if not _text(record["summary"], "timeline.summary").startswith(_FIRST_PERSON):
            raise ValueError("Cronologia deve usar primeira pessoa")
        _check_sources(record["source_ids"], source_ids)
    if data["review_status"] == "reviewed" and (not highlights or not sources):
        raise ValueError("Pack revisado requer feitos com fontes")
    return data


def _check_sources(ids: object, valid_ids: set[str]) -> None:
    if (not isinstance(ids, list) or not ids or
            any(not isinstance(i, str) for i in ids) or
            len(set(ids)) != len(ids) or not set(ids).issubset(valid_ids)):
        raise ValueError("Referência de fonte inexistente ou repetida")


def _player(slug: str):
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9-]{2,48}", slug):
        raise ValueError("Slug inválido")
    player = next((p for p in load_roster() if p.slug == slug), None)
    if player is None:
        raise ValueError("Jogador fora do elenco")
    return player


def load_history_profile(slug: str) -> dict:
    """Return a sourced biography or an explicit pending placeholder for any roster member."""
    player = _player(slug)
    resource = files("chesssim.data").joinpath("history", f"{slug}.json")
    if resource.is_file():
        raw = json.loads(resource.read_text(encoding="utf-8"))
        profile = validate_history_pack(raw, slug=slug)
        has_pack = True
    else:
        profile = {
            "schema": SCHEMA, "slug": slug, "review_status": "pending",
            "narrative_voice": VOICE, "short_bio": None, "era_label": None,
            "portrait": None, "highlights": [], "timeline": [], "sources": [],
        }
        has_pack = False
    return {**profile, "name": player.name, "group": player.group,
            "has_historical_pack": has_pack, "historically_validated_model": False}


def list_history_players() -> dict:
    """Read-only 64-player projection; biography existence does not depend on Rodent installation."""
    players = []
    for player in load_roster():
        profile = load_history_profile(player.slug)
        players.append({
            "slug": player.slug, "name": player.name, "group": player.group,
            "has_historical_pack": profile["has_historical_pack"],
            "review_status": profile["review_status"],
            "era_label": profile["era_label"], "short_bio": profile["short_bio"],
            "portrait": profile["portrait"],
            "highlight_count": len(profile["highlights"]),
        })
    return {"schema": INDEX_SCHEMA, "players": players}
