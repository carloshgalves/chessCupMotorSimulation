"""Obtain public-domain-marked historical chess portraits from Cleveland Public Library.

Does not bypass anti-automation restrictions or use any Wikimedia infrastructure.
The metadata is rechecked per item, including NoC-US and Public Domain Mark 1.0.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from materialize_history_portraits import DATA, manifest_rows

SOURCE_HOST = "cplorg.contentdm.oclc.org"
AGENT = "ChessSimulationHistoricalPhoto/1.0 (+https://github.com/carloshgalves/chessCupMotorSimulation)"
SOURCE_RE = re.compile(
    r"^/digital/collection/p4014coll20/id/([0-9]{1,6})/?$"
)


class AccessDenied(RuntimeError):
    """Stop requesting a host that has rejected automated access."""


def fetch(url: str, *, limit: int) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != SOURCE_HOST:
        raise ValueError("Unapproved photo provider")
    req = Request(url, headers={"User-Agent": AGENT, "Accept": "*/*"})
    try:
        with urlopen(req, timeout=23) as res:
            if urlsplit(res.url).hostname != SOURCE_HOST:
                raise ValueError("Redirected to a different provider")
            data = res.read(limit + 1)
    except HTTPError as exc:
        if exc.code in (401, 403, 429, 451):
            raise AccessDenied(f"Cleveland denied automated access (HTTP {exc.code})") from exc
        raise
    if len(data) > limit:
        raise ValueError("Image or metadata exceeds size budget")
    return data


def acquire(row: dict) -> tuple[bytes, str, str]:
    from PIL import Image, ImageOps

    item_match = SOURCE_RE.fullmatch(urlsplit(row["source_url"]).path)
    if item_match is None:
        raise ValueError("Unapproved or unnumbered original item URL")
    item_id = item_match.group(1)
    api = (
        "https://cplorg.contentdm.oclc.org/digital/api/singleitem/"
        "collection/p4014coll20/id/" + item_id
    )
    metadata = json.loads(fetch(api, limit=35000))
    fields = {f["key"]: str(f["value"] or "")
              for f in metadata.get("fields", [])
              if isinstance(f, dict) and "key" in f}
    title = fields.get("title", "")
    expected_name = {
        "philidor": "Philidor",
        "nimzowitsch": "Nimzowitsch",
    }.get(row["slug"], row["slug"].split("-")[0])
    if expected_name.casefold() not in title.casefold():
        raise ValueError(f"Source title does not identify {row['slug']}: {title}")
    rights = fields.get("rightsl")
    rights_statement = fields.get("standa", "").strip()
    if (rights != "Public Domain Mark 1.0"
            or rights_statement not in (
                "http://rightsstatements.org/vocab/NoC-US/1.0/",
                "https://rightsstatements.org/vocab/NoC-US/1.0/"
            )):
        raise ValueError("Not an institution-marked public-domain item")
    if row["license"] != rights or row["license_url"] != (
            "https://creativecommons.org/publicdomain/mark/1.0/"):
        raise ValueError("Manifest and original library rights do not agree")
    # Do not convert approximate year captions into invented timestamps.
    original_date = fields.get("date", "")
    year = re.search(r"(?:17|18|19|20)\d{2}", original_date)
    if year and year.group() not in row["event_date"]:
        raise ValueError("Published item date differs from the catalogued date")
    url = (f"https://cplorg.contentdm.oclc.org/digital/iiif/p4014coll20/"
           f"{item_id}/full/960,/0/default.jpg")
    photo = fetch(url, limit=3_000_000)
    with Image.open(BytesIO(photo)) as img:
        if img.format != "JPEG":
            raise ValueError("Invalid IIIF JPEG data")
        if min(img.size) < 280 or img.width * img.height > 30_000_000:
            raise ValueError("Invalid IIIF photo dimensions")
        square = ImageOps.fit(
            ImageOps.exif_transpose(img).convert("RGB"), (480, 480),
            method=Image.Resampling.LANCZOS,
            centering=tuple(row["centering"]),
        )
        buffer = BytesIO()
        square.save(buffer, format="JPEG", quality=82, optimize=True,
                    progressive=True)
        cropped = buffer.getvalue()
    if len(cropped) > 500_000:
        raise ValueError("Derivative exceeds package limit")
    return cropped, title, fields.get("origin", "")


def run() -> None:
    candidates = [row for row in manifest_rows()
                  if urlsplit(row["source_url"]).hostname == SOURCE_HOST]
    imported, kept = 0, 0
    for row in candidates:
        slug = row["slug"]
        picture = DATA / "portraits" / f"{slug}.jpg"
        packfile = DATA / f"{slug}.json"
        profile = json.loads(packfile.read_text(encoding="utf-8"))
        if picture.is_file() and profile["portrait"]:
            if sha256(picture.read_bytes()).hexdigest() != profile["portrait"]["sha256"]:
                raise ValueError(f"Prior approved asset changed: {slug}")
            kept += 1
            continue
        if picture.exists() or profile["portrait"] is not None:
            raise ValueError(f"Partial import for {slug}")
        try:
            photo, title, original_medium = acquire(row)
        except AccessDenied as exc:
            print(f"HOST_DENIED: {exc}; all remaining Cleveland requests canceled",
                  flush=True)
            break
        except (OSError, ValueError, URLError) as exc:
            print(f"NOT_IMPORTED {slug}: {type(exc).__name__} {exc}", flush=True)
            continue
        is_art = slug in {"philidor", "staunton"}
        if slug == "philidor":
            alt = ("Fotografia de um busto representando François-André "
                   "Danican Philidor; não é uma fotografia tirada em vida")
        elif slug == "staunton":
            alt = "Desenho histórico de Howard Staunton; não é uma fotografia"
        elif slug == "morphy":
            alt = "Reprodução fotográfica histórica atribuída a Paul Morphy (1859)"
        else:
            alt = f"Retrato histórico de {title}, fonte Cleveland Public Library"
        profile["portrait"] = {
            "local_asset": f"history/portraits/{slug}.jpg",
            "alt": alt,
            "author": row["author"],
            "license": row["license"],
            "source_url": row["source_url"],
            "verified_at": date.today().isoformat(),
            "event": row["event"],
            "event_date": row["event_date"],
            "rights_holder": row["rights_holder"],
            "license_url": row["license_url"],
            "sha256": sha256(photo).hexdigest(),
            "derivative_note": (
                "Recorte e redução para 480×480 px de reprodução"
                + (" artística" if is_art else " histórica")
                + "; identificado por Cleveland Public Library como PDM 1.0/NoC-US."
                + " PDM é uma marcação de domínio público, não licença CC0."
                + " Verificar exigências territoriais antes de redistribuir."
            ),
            "approval_status": "approved",
        }
        picture.parent.mkdir(exist_ok=True)
        picture.write_bytes(photo)
        packfile.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        imported += 1
        print(f"IMPORTED {slug}: {len(photo)} bytes, PDM, {original_medium}",
              flush=True)
        sleep(0.4)
    print(f"CLEVELAND_RESULT imported={imported} preserved={kept} total={len(candidates)}",
          flush=True)


if __name__ == "__main__":
    run()
