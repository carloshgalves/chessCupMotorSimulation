"""Acquire explicitly licensed upstream chess SVGs, unmodified and hash-verified.

One-time maintenance helper; no proxies, mirror fallback, asset rewriting,
or retries against provider blocks. The checked-in SOURCES.json records each
upstream git blob SHA-1 pinned to a specific Lichess commit.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "src/chesssim/data/pieces"
MANIFEST = DEST / "SOURCES.json"
SET_IDS = {
    "cburnett", "merida", "chessnut", "fantasy", "spatial", "celtic", "rhosgfx",
}
PIECES = {f"{color}{kind}.svg" for color in "wb" for kind in "KQRBNP"}
MAX_SIZE = 80_000


def safe_svg(payload: bytes, path: str) -> None:
    if not 80 < len(payload) <= MAX_SIZE:
        raise ValueError(f"Unexpected SVG size: {path}")
    text = payload.decode("utf-8")
    if not text.lstrip().startswith(("<svg", "<?xml")):
        raise ValueError(f"Unexpected SVG prefix: {path}")
    if re.search(
        r"<!DOCTYPE|<!ENTITY|<script\b|<foreignObject\b|<iframe\b|"
        r"<(?:image|animate|set)\b|<\?xml-stylesheet|"
        r"\son[a-z]{2,}\s*=|javascript:|data:|@import\b",
        text, flags=re.I,
    ):
        raise ValueError(f"Disallowed active SVG content: {path}")
    for match in re.finditer(r"\s(?:xlink:)?href\s*=\s*['\"]([^'\"]+)['\"]",
                             text, flags=re.I):
        if not match.group(1).startswith("#"):
            raise ValueError(f"External SVG reference: {path}")
    for match in re.finditer(r"url\(\s*['\"]?([^'\"\s)]+)", text, flags=re.I):
        if not match.group(1).startswith("#"):
            raise ValueError(f"External CSS resource in SVG: {path}")
    root = ET.fromstring(payload)
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        raise ValueError(f"Invalid SVG namespace: {path}")


def main() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sha = data["upstream_commit"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Unpinned upstream revision")
    index = data["sets"]
    if {item["id"] for item in index} != SET_IDS:
        raise ValueError("Incomplete/unexpected piece set")
    count = 0
    for item in index:
        name = item["id"]
        if set(item["files"]) != PIECES:
            raise ValueError("Each set must contain precisely the twelve chess pieces")
        for filename in sorted(PIECES):
            expected = item["files"][filename]["sha"]
            if not re.fullmatch(r"[0-9a-f]{40}", expected):
                raise ValueError("Invalid upstream blob SHA")
            path = DEST / name / filename
            if path.exists() or path.is_symlink():
                raise ValueError("Would overwrite previously imported piece " + str(path))
            url = (f"https://raw.githubusercontent.com/lichess-org/lila/"
                   f"{sha}/public/piece/{name}/{filename}")
            request = Request(
                url, headers={"User-Agent": "ChessSimulation-piece-audit/1.0"}
            )
            with urlopen(request, timeout=30) as response:
                if urlparse(response.url).hostname != "raw.githubusercontent.com":
                    raise ValueError("Unexpected asset origin")
                svg = response.read(MAX_SIZE + 1)
            safe_svg(svg, f"{name}/{filename}")
            blob_id = hashlib.sha1(
                b"blob " + str(len(svg)).encode("ascii") + b"\0" + svg
            ).hexdigest()
            if blob_id != expected:
                raise ValueError("Source file hash mismatch: " + name + "/" + filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(svg)
            count += 1
            print("Verified", f"{name}/{filename}", blob_id, flush=True)
    if count != 84:
        raise ValueError(f"Expected 84 immutable SVG originals; got {count}")
    print(f"SVG_ACQUISITION_OK: {count} unmodified upstream assets")


if __name__ == "__main__":
    main()
