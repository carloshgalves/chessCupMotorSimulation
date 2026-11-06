"""One-shot import from independently licensed original chess photo archives.

Fetches ONLY official archive pages, follows their advertised download endpoint,
checks local metadata and CC0 notices, and never accesses a site that has denied
automated access. No runtime dependency or background action.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from html import unescape
from io import BytesIO
import json
from pathlib import Path
import re
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from materialize_history_portraits import DATA, manifest_rows

ALLOWED_HOSTS = {
    "www.nationaalarchief.nl", "service.archief.nl",
    "noord-hollandsarchief.nl", "images.memorix.nl"
}
IDENTITY_TOKENS = {
    "kasparov": "kasparov", "keres": "keres",
    "portisch": "portisch", "geller": "geller",
    "botvinnik": "botwinnik", "gligoric": "gligoric",
    "judit-polgar": "judit",
    "chiburdanidze": "chiburdanidze",
    "gaprindashvili": "gaprinda",
    "tal": "tal",
    "korchnoi": "kortsjnoj",
    "taimanov": "taimanov",
    "reshevsky": "reshevsky",
    "polugaevsky": "poloegajevski",
    "short": "short",
    "gelfand": "gelf",
}
USER_AGENT = "ChessSimulationCurator/1.0 (https://github.com/carloshgalves/chessCupMotorSimulation)"


class SourceDenied(RuntimeError):
    """An archive refused automated access; no retries are permitted."""


def _read(url: str, *, max_bytes: int) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("Unapproved archive/download hostname")
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urlopen(req, timeout=20) as response:
            if urlsplit(response.url).hostname not in ALLOWED_HOSTS:
                raise ValueError("Archive redirected to unknown domain")
            content = response.read(max_bytes + 1)
    except HTTPError as exc:
        if exc.code in {401, 403, 429, 451}:
            raise SourceDenied(f"Archive access denied: HTTP {exc.code}") from exc
        raise
    if len(content) > max_bytes:
        raise ValueError("Archive file exceeds size limit")
    return content


def source_download(row: dict) -> tuple[str, str]:
    """Use public download URLs explicitly advertised by the licensed archive."""
    source = row["source_url"]
    page = unescape(_read(source, max_bytes=300_000).decode("utf-8", "replace"))
    unaccented = "".join(c for c in unicodedata.normalize("NFKD", page.lower()) if not unicodedata.combining(c))
    if IDENTITY_TOKENS[row["slug"]] not in unaccented:
        raise ValueError("Archive landing page does not identify selected player")
    origin = urlsplit(source).hostname
    if origin == "www.nationaalarchief.nl":
        decoded = page.replace("\\u0022", '"')
        for _ in range(4):
            decoded = decoded.replace("\\/", "/")  # Drupal double-escaped JSON
        if "Nationaal Archief, CC0" not in decoded:
            raise ValueError("Archive does not display a CC0 rights declaration")
        if row["event_date"] not in decoded:
            raise ValueError("Archive date disagrees with candidate metadata")
        # The archive serializes Drupal JSON with escaped quote/slash
        # sequences. Parse the UUID from the original/download service link
        # already embedded in the official page, never a caller-supplied URL.
        match = re.search(r"/gaf/api/file/v1/(?:original|default)/([0-9a-f-]{36})", decoded)
        if not match:
            print("ARCHIVE_DIAGNOSTIC", row["slug"], repr(decoded[decoded.find("downloadURI") - 20:decoded.find("downloadURI") + 260]), flush=True)
            raise ValueError("Official archive download link is absent")
        return ("https://service.archief.nl/gaf/api/file/v1/default/" +
                match.group(1)), "Nationaal Archief CC0 download"
    if origin == "noord-hollandsarchief.nl":
        if "kosteloos gebruiken" not in page.lower() or "1998" not in page:
            raise ValueError("North Holland Archive has not confirmed unrestricted reuse/event")
        match = re.search(
            r'<meta\s+property="og:image"\s+content="(https://images\.memorix\.nl/ranh/thumb/640x480/[a-f0-9-]+\.jpg)"',
            page, flags=re.I
        )
        if not match:
            raise ValueError("Official archive preview asset is absent")
        return match.group(1), "Noord-Hollands Archief free download"
    raise ValueError("Unknown official archive")


def render(row: dict, url: str) -> bytes:
    from PIL import Image, ImageOps

    binary = _read(url, max_bytes=8_000_000)
    with Image.open(BytesIO(binary)) as image:
        if image.format not in ("JPEG", "PNG"):
            raise ValueError("Official archival media is not JPEG/PNG")
        if image.width < 400 or image.height < 400:
            raise ValueError("Original has insufficient resolution for 480px")
        if image.width * image.height > 35_000_000:
            raise ValueError("Potential image decompression bomb")
        square = ImageOps.fit(ImageOps.exif_transpose(image).convert("RGB"),
                              (480, 480), method=Image.Resampling.LANCZOS,
                              centering=tuple(row["centering"]))
        output = BytesIO()
        square.save(output, "JPEG", quality=82, optimize=True, progressive=True)
        result = output.getvalue()
    if len(result) > 500_000:
        raise ValueError("Resulting JPEG exceeds package budget")
    return result


def main() -> None:
    rows = [row for row in manifest_rows()
            if urlsplit(row["source_url"]).hostname in
            {"www.nationaalarchief.nl", "noord-hollandsarchief.nl"}]
    denied_hosts: set[str] = set()
    imported = 0
    kept = 0
    for row in rows:
        slug = row["slug"]
        source_host = urlsplit(row["source_url"]).hostname
        if source_host in denied_hosts:
            print(f"SKIP {slug}: official source denied access earlier", flush=True)
            continue
        filename = DATA / "portraits" / f"{slug}.jpg"
        pack_file = DATA / f"{slug}.json"
        pack = json.loads(pack_file.read_text(encoding="utf-8"))
        if filename.exists() and pack["portrait"] is not None:
            assert sha256(filename.read_bytes()).hexdigest() == pack["portrait"]["sha256"]
            kept += 1
            continue
        if filename.exists() or pack["portrait"] is not None:
            raise ValueError(f"Incomplete existing photo import: {slug}")
        try:
            remote, license_evidence = source_download(row)
            data = render(row, remote)
        except SourceDenied as exc:
            denied_hosts.add(source_host)
            print(f"DENIED {slug}: {exc}; no further requests to {source_host}", flush=True)
            continue
        except (OSError, ValueError, URLError) as exc:
            print(f"NOT_IMPORTED {slug}: {type(exc).__name__}: {exc}", flush=True)
            continue
        portrait = {
            "local_asset": f"history/portraits/{slug}.jpg",
            "alt": f"{slug} em {row['event']} ({row['event_date']})",
            "author": row["author"], "license": "CC0 1.0",
            "source_url": row["source_url"], "verified_at": date.today().isoformat(),
            "event": row["event"], "event_date": row["event_date"],
            "rights_holder": row["rights_holder"],
            "license_url": row["license_url"],
            "sha256": sha256(data).hexdigest(),
            "derivative_note": "Recorte quadrado e redução para 480×480 px a partir de fotografia oficial de torneio.",
            "approval_status": "approved"
        }
        filename.parent.mkdir(parents=True, exist_ok=True)
        filename.write_bytes(data)
        pack["portrait"] = portrait
        pack_file.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        imported += 1
        print(f"IMPORTED {slug}: {len(data)} bytes, {portrait['sha256'][:12]}, {license_evidence}",flush=True)
    print(f"ARCHIVE_RESULT imported={imported} preserved={kept} attempted={len(rows)}",
          flush=True)


if __name__ == "__main__":
    main()
