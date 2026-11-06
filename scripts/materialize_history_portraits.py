"""Explicit one-shot importer of freely licensed historical tournament portraits."""
from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
from html import unescape
from io import BytesIO
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

DATA = Path(__file__).resolve().parents[1] / "src/chesssim/data/history"
MANIFEST = DATA / "portrait_candidates.json"
USER_AGENT = "ChessSimHistory/1.0 (github.com/carloshgalves/chessCupMotorSimulation)"
LICENSES = {
    "CC0 1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "CC BY-SA 3.0": "https://creativecommons.org/licenses/by-sa/3.0/",
}


def get_bytes(url: str, *, limit: int) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=30) as reply:
        data = reply.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Exceeded download size limit")
    return data


def manifest_rows() -> list[dict]:
    obj = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if obj.get("schema") != "history-portrait-candidates-v1":
        raise ValueError("Unknown portrait manifest schema")
    rows = obj.get("candidates")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 64:
        raise ValueError("Invalid portrait candidate count")
    seen = set()
    expected_keys = {
        "slug", "file", "event", "event_date", "author", "rights_holder",
        "license", "license_url", "source_url", "centering"
    }
    for row in rows:
        if not isinstance(row, dict) or set(row) != expected_keys:
            raise ValueError("Incomplete portrait provenance")
        slug = row["slug"]
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9-]{2,48}", slug) or slug in seen:
            raise ValueError("Invalid or duplicate portrait slug")
        seen.add(slug)
        if not isinstance(row["file"], str) or not row["file"].endswith(".jpg"):
            raise ValueError("Unexpected image extension")
        if row["license"] not in LICENSES or row["license_url"] != LICENSES[row["license"]]:
            raise ValueError("Image license not on approved allowlist")
        url = urlsplit(row["source_url"])
        if url.scheme != "https" or url.hostname != "commons.wikimedia.org" or not url.path.startswith("/wiki/File:"):
            raise ValueError("Unexpected Wikimedia Commons file page")
        if not all(isinstance(row[k], str) and row[k].strip()
                   for k in ("event", "event_date", "author", "rights_holder")):
            raise ValueError("Missing author or competition")
        center = row["centering"]
        if not isinstance(center, list) or len(center) != 2 or not all(
                isinstance(c, (int, float)) and 0 <= c <= 1 for c in center):
            raise ValueError("Invalid crop position")
        path = DATA / (slug + ".json")
        if not path.is_file() or json.loads(path.read_text(encoding="utf-8"))["slug"] != slug:
            raise ValueError("Portrait without player pack")
    return rows


def _meta(meta: dict, field: str) -> str:
    return unescape(re.sub(r"<[^>]*>", "", meta.get(field, {}).get("value", ""))).strip()


def acquire(row: dict) -> bytes:
    """Verify Commons' current rights metadata before downloading any image."""
    from PIL import Image, ImageOps
    query = urlencode({
        "action": "query", "prop": "imageinfo", "iiprop": "url|extmetadata",
        "titles": "File:" + row["file"], "iiurlwidth": "800",
        "format": "json", "formatversion": "2"
    })
    obj = json.loads(get_bytes("https://commons.wikimedia.org/w/api.php?" + query,
                               limit=200_000))
    pages = obj.get("query", {}).get("pages", [])
    if len(pages) != 1 or "missing" in pages[0]:
        raise ValueError("Commons file missing: " + row["slug"])
    info = pages[0].get("imageinfo", [{}])[0]
    meta = info.get("extmetadata", {})
    name = _meta(meta, "LicenseShortName").replace("-", " ").upper()
    expected = row["license"].replace("-", " ").upper()
    if not (name == expected or (expected == "CC0 1.0" and name.startswith("CC0"))):
        raise ValueError(f"License mismatch for {row['slug']}: {name}")
    license_url = _meta(meta, "LicenseUrl")
    if license_url:
        actual = urlsplit(license_url)
        expected_url = urlsplit(row["license_url"])
        # Commons may return /deed.en rather than the canonical license URL.
        if (actual.hostname not in {"creativecommons.org", "www.creativecommons.org"}
                or not actual.path.rstrip("/").startswith(expected_url.path.rstrip("/"))):
            raise ValueError("Commons license URL differs from audited record")
    remote = info.get("thumburl") or info.get("url")
    parsed = urlsplit(remote or "")
    if (parsed.scheme != "https" or parsed.hostname not in
            {"upload.wikimedia.org", "thumb.wikimedia.org"} or
            not parsed.path.startswith("/wikipedia/commons/")):
        raise ValueError("Unsafe media URL")
    with Image.open(BytesIO(get_bytes(remote, limit=8_000_000))) as im:
        if im.format not in {"JPEG", "PNG"}:
            raise ValueError("Unsupported media type")
        im = ImageOps.fit(im.convert("RGB"), (480, 480),
                          method=Image.Resampling.LANCZOS,
                          centering=tuple(row["centering"]))
        out = BytesIO()
        im.save(out, format="JPEG", quality=82, optimize=True, progressive=True)
        data = out.getvalue()
    if len(data) > 500_000:
        raise ValueError("Portrait exceeds size budget")
    return data


def apply_photos(rows: list[dict]) -> None:
    staged = [(row, acquire(row)) for row in rows]  # Fetch all before writes.
    directory = DATA / "portraits"
    directory.mkdir(exist_ok=True)
    for row, binary in staged:
        slug = row["slug"]
        (directory / (slug + ".jpg")).write_bytes(binary)
        path = DATA / (slug + ".json")
        pack = json.loads(path.read_text(encoding="utf-8"))
        pack["portrait"] = {
            "local_asset": f"history/portraits/{slug}.jpg",
            "alt": f"{slug} em {row['event']} ({row['event_date']})",
            "author": row["author"],
            "license": row["license"],
            "source_url": row["source_url"],
            "verified_at": date.today().isoformat(),
            "event": row["event"],
            "event_date": row["event_date"],
            "rights_holder": row["rights_holder"],
            "license_url": row["license_url"],
            "sha256": sha256(binary).hexdigest(),
            "derivative_note": "Recorte quadrado e redução a 480×480 px.",
            "approval_status": "approved"
        }
        path.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(f"Materialized {len(staged)} licensed photographs with attribution.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Download and prepare copyright-vetted local files")
    options = parser.parse_args()
    candidates = manifest_rows()
    if options.apply:
        apply_photos(candidates)
    else:
        print(f"{len(candidates)} candidates validated; run --apply to download")
