"""One-time import of verified chess-tournament photos from their Flickr photographer.

The source manifest identifies the exact image, author, competition and license.
Each live Flickr page must independently confirm the license and own media URL.
Never bypass a host restriction and never fetch an image from Wikimedia.
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

ALLOWED = {"www.flickr.com", "live.staticflickr.com"}
USER_AGENT = "ChessSimulationHistoricalPhoto/1.0 (https://github.com/carloshgalves/chessCupMotorSimulation)"


class HostDenied(Exception):
    """Stop all requests to this host once it denies automation."""


def read(url: str, *, limit: int) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED:
        raise ValueError("Source not on photographer-host whitelist")
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        with urlopen(request, timeout=24) as resp:
            if urlsplit(resp.url).hostname not in ALLOWED:
                raise ValueError("Unexpected off-host redirect")
            contents = resp.read(limit + 1)
    except HTTPError as exc:
        if exc.code in (401, 403, 429, 451):
            raise HostDenied(f"Flickr access rejected: HTTP {exc.code}") from exc
        raise
    if len(contents) > limit:
        raise ValueError("Source size limit exceeded")
    return contents


def obtain(row: dict) -> bytes:
    from PIL import Image, ImageOps

    url = row["source_url"]
    match = re.fullmatch(
        r"https://www\.flickr\.com/photos/8022405@N02/([0-9]{8,20})/?", url
    )
    if not match:
        raise ValueError("Unexpected Flickr album/profile")
    photo_id = match.group(1)
    page = read(url, limit=500_000).decode("utf-8", "replace")
    # Compare live photographer license and media rather than trusting a
    # third-party index.  Both are present in Flickr's first-party JSON-LD.
    marker = re.search(
        r'"contentUrl"\s*:\s*"(https://live\.staticflickr\.com/[^"]+\.jpg)"'
        r'\s*,\s*"license"\s*:\s*"(https://creativecommons\.org/licenses/(?:by|by-sa)/2\.0/)"',
        page
    )
    if not marker:
        raise ValueError("First-party Flickr JSON-LD media or license absent")
    remote, license_url = marker.groups()
    if license_url != row["license_url"]:
        raise ValueError(f"Live license differs for {row['slug']}: {license_url}")
    if not re.search(r'"/(?:photos/karpidis/|photos/8022405@N02/)' + photo_id + r'/?', page):
        # Flickr may normalize the vanity account to karpidis.
        if f"/photos/karpidis/{photo_id}" not in page:
            raise ValueError("Flickr ID not present in the original photo page")
    if "Andreas Kontokanis" not in page and "karpidis" not in page:
        raise ValueError("Photographer attribution missing")
    media = urlsplit(remote)
    if media.hostname != "live.staticflickr.com" or not re.search(
        r"/" + photo_id + r"_[a-zA-Z0-9]+(?:_[a-z])?\.jpg$", media.path
    ):
        raise ValueError("Flickr CDN photo does not match licensed page")
    source = read(remote, limit=3_000_000)
    with Image.open(BytesIO(source)) as img:
        if img.format not in {"JPEG", "PNG"}:
            raise ValueError("Unexpected media file type")
        if img.width < 480 or img.height < 480:
            raise ValueError("Media cannot support requested square avatar size")
        if img.width * img.height > 30_000_000:
            raise ValueError("Oversized media dimensions")
        square = ImageOps.fit(
            ImageOps.exif_transpose(img).convert("RGB"), (480, 480),
            method=Image.Resampling.LANCZOS,
            centering=tuple(row["centering"])
        )
        out = BytesIO()
        square.save(out, "JPEG", quality=82, optimize=True, progressive=True)
        derivative = out.getvalue()
    if len(derivative) > 500_000:
        raise ValueError("Avatar package-size limit exceeded")
    return derivative


def main() -> None:
    candidates = [row for row in manifest_rows()
                  if urlsplit(row["source_url"]).hostname == "www.flickr.com"]
    done = 0
    preserved = 0
    blocked = False
    for row in candidates:
        slug = row["slug"]
        filepath = DATA / "portraits" / (slug + ".jpg")
        pack_path = DATA / (slug + ".json")
        pack = json.loads(pack_path.read_text(encoding="utf-8"))
        if filepath.is_file() and pack["portrait"]:
            if sha256(filepath.read_bytes()).hexdigest() != pack["portrait"]["sha256"]:
                raise ValueError("Previously approved image failed SHA-256")
            preserved += 1
            continue
        if filepath.exists() or pack["portrait"] is not None:
            raise ValueError("Partial prior photo import")
        if blocked:
            print(f"SKIPPED {slug}: Flickr denied further requests", flush=True)
            continue
        try:
            jpg = obtain(row)
        except HostDenied as exc:
            blocked = True
            print(f"DENIED {slug}: {exc}; remaining Flickr requests cancelled", flush=True)
            continue
        except (ValueError, OSError, URLError) as exc:
            print(f"NOT_IMPORTED {slug}: {type(exc).__name__}: {exc}", flush=True)
            continue

        filepath.parent.mkdir(exist_ok=True)
        pack["portrait"] = {
            "local_asset": f"history/portraits/{slug}.jpg",
            "alt": f"{slug} fotografado em {row['event']} ({row['event_date']})",
            "author": row["author"],
            "license": row["license"],
            "source_url": row["source_url"],
            "verified_at": date.today().isoformat(),
            "event": row["event"],
            "event_date": row["event_date"],
            "rights_holder": row["rights_holder"],
            "license_url": row["license_url"],
            "sha256": sha256(jpg).hexdigest(),
            "derivative_note": "Recorte quadrado de 480×480 px de imagem de torneio. A fotografia derivada mantém a licença da fonte; atribuição ao fotógrafo.",
            "approval_status": "approved",
        }
        filepath.write_bytes(jpg)
        pack_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        done += 1
        print(f"IMPORTED {slug}: {len(jpg)} bytes, {pack['portrait']['license']}",
              flush=True)
        sleep(0.35)  # Be respectful to independent photographer host.
    print(f"FLICKR_RESULT imported={done} preserved={preserved} candidates={len(candidates)}",
          flush=True)


if __name__ == "__main__":
    main()
