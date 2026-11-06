"""Explicit one-shot importer of freely licensed historical tournament portraits."""
from __future__ import annotations

import argparse
from datetime import date
from hashlib import md5, sha256
from html import unescape
from io import BytesIO
import json
from pathlib import Path
import re
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from time import sleep

DATA = Path(__file__).resolve().parents[1] / "src/chesssim/data/history"
MANIFEST = DATA / "portrait_candidates.json"
USER_AGENT = "ChessSimHistory/1.0 (github.com/carloshgalves/chessCupMotorSimulation)"
LICENSES = {
    "CC0 1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "CC BY-SA 3.0": "https://creativecommons.org/licenses/by-sa/3.0/",
    "CC BY-SA 2.0": "https://creativecommons.org/licenses/by-sa/2.0/",
    "CC BY 2.0": "https://creativecommons.org/licenses/by/2.0/",
    "Public Domain Mark 1.0": "https://creativecommons.org/publicdomain/mark/1.0/",
}


class RobotPolicyStop(RuntimeError):
    """The provider explicitly declined automated access; do not retry."""


def get_bytes(url: str, *, limit: int) -> bytes:
    # Wikimedia may rate-limit shared GitHub Actions IPs. Honor backoff, no
    # unlimited retries, and avoid treating upstream 429 as license failure.
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": USER_AGENT}),
                         timeout=30) as reply:
                data = reply.read(limit + 1)
            if len(data) > limit:
                raise ValueError("Exceeded download size limit")
            return data
        except HTTPError as exc:
            if exc.code == 429:
                detail = exc.read(1200).decode("utf-8", "replace").lower()
                if "robot policy" in detail:
                    raise RobotPolicyStop("Commons robot policy blocked this runner") from exc
            if exc.code != 429 or attempt == 2:
                raise
            sleep(3 * (attempt + 1))
    raise RuntimeError("Unreachable retry state")


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
        approved_archive = (url.hostname == "www.nationaalarchief.nl" and
                            re.fullmatch(r"/onderzoeken/fotocollectie/[0-9a-f-]{36}", url.path))
        holland_archive = (url.hostname == "noord-hollandsarchief.nl" and
                           re.fullmatch(r"/beelden/beeldbankdeboer/detail/[0-9a-f-]{36}", url.path))
        commons = url.hostname == "commons.wikimedia.org" and url.path.startswith("/wiki/File:")
        flickr = (url.hostname == "www.flickr.com" and
                  re.fullmatch(r"/photos/(?:8022405@N02|karpidis|suspeeters)/[0-9]{8,20}/?", url.path))
        cleveland = (url.hostname == "cplorg.contentdm.oclc.org" and
                     re.fullmatch(r"/digital/collection/p4014coll20/id/[0-9]{1,6}/?", url.path))
        met = url.hostname == "www.metmuseum.org" and url.path == "/art/collection/search/423124"
        chesshistory = (url.hostname == "www.chesshistory.com" and
                        url.path == "/winter/extra/menchikclub.html")
        if url.scheme != "https" or not (commons or approved_archive or holland_archive or flickr or cleveland or met or chesshistory):
            raise ValueError("Unexpected or unapproved photo source")
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
    """Acquire from a manually checked Commons file record, verifying licensing when API allows."""
    from PIL import Image, ImageOps
    query = urlencode({
        "action": "query", "prop": "imageinfo", "iiprop": "url|extmetadata",
        "titles": "File:" + row["file"], "iiurlwidth": "500",
        "format": "json", "formatversion": "2"
    })
    api_thumbnail = None
    try:
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
            if (actual.hostname not in {"creativecommons.org", "www.creativecommons.org"}
                    or not actual.path.rstrip("/").startswith(expected_url.path.rstrip("/"))):
                raise ValueError("Commons license URL differs from audited record")
        api_thumbnail = info.get("thumburl")
    except HTTPError as exc:
        if exc.code != 429:
            raise
        print(f"Commons API rate-limited: using pre-reviewed file {row['slug']}", flush=True)
    filename = row["file"].replace(" ", "_")
    digest = md5(filename.encode("utf-8"), usedforsecurity=False).hexdigest()
    width = 500 if row["slug"] in {"hou-yifan", "caruana"} else 960
    prefix = ("https://upload.wikimedia.org/wikipedia/commons/thumb/"
              + digest[0] + "/" + digest[:2] + "/" + quote(filename))
    # Prefer existing Commons thumbnails: Wikimedia explicitly asks clients
    # not to trigger mass resizing of full-size originals on shared runners.
    known_thumbnail = prefix + "/" + str(width) + "px-" + quote(filename)
    candidate = api_thumbnail if api_thumbnail and api_thumbnail == known_thumbnail else known_thumbnail
    parsed = urlsplit(candidate)
    if parsed.scheme != "https" or parsed.hostname != "upload.wikimedia.org":
        raise ValueError("Unsafe thumbnail URL")
    with Image.open(BytesIO(get_bytes(candidate, limit=2_000_000))) as im:
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
    directory = DATA / "portraits"
    # Preserve previously licensed files and metadata; fetch only new entries.
    todo = []
    for row in rows:
        destination = directory / (row["slug"] + ".jpg")
        profile = json.loads((DATA / (row["slug"] + ".json")).read_text(encoding="utf-8"))
        if destination.is_file() and profile["portrait"] is not None:
            assert sha256(destination.read_bytes()).hexdigest() == profile["portrait"]["sha256"]
            if profile["portrait"]["license"] != row["license"]:
                raise ValueError("Existing asset license differs from manifest")
            continue
        if destination.exists() or profile["portrait"] is not None:
            raise ValueError("Incomplete previous portrait materialization")
        if urlsplit(row["source_url"]).hostname != "commons.wikimedia.org":
            print(f"Use official archive importer for {row['slug']}", flush=True)
            continue
        todo.append(row)
    staged = []
    unavailable = []
    for row in todo:
        try:
            staged.append((row, acquire(row)))
        except RobotPolicyStop as exc:
            unavailable.append((row["slug"], str(exc)))
            print(f"ROBOT_POLICY_STOP: {exc}; remaining requests skipped", flush=True)
            break
        except (OSError, ValueError) as exc:
            unavailable.append((row["slug"], str(exc)))
            print(f"NOT_IMPORTED {row['slug']}: {type(exc).__name__}: {exc}",
                  flush=True)
        sleep(0.5)  # Commons CDN is shared infrastructure; avoid bursts.
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
    print(f"Materialized {len(staged)} new licensed photographs; "
          f"{len(unavailable)} skipped; {len(rows) - len(todo)} existing preserved.")
    if not staged and todo:
        raise RuntimeError("No new portraits were available; no photos approved.")


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
