"""Pinned third-party chess SVGs: provenance, local serving and no active content."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha1
from importlib.resources import files
from pathlib import Path
import json
import re
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

import pytest

from chesssim import arena, live_board

PIECES = {f"{color}{piece}.svg" for color in "wb" for piece in "KQRBNP"}
LICENSES = {
    "cburnett": "GPL-2.0-or-later",
    "merida": "GPL-2.0-or-later",
    "chessnut": "Apache-2.0",
    "fantasy": "MIT",
    "spatial": "MIT",
    "celtic": "MIT",
    "rhosgfx": "CC0-1.0",
}
LICENSE_FILES = ("GPL-2.0.txt", "Apache-2.0.txt", "MIT.txt", "CC0-1.0.txt")
PIN = "104a754b35740129fade423f920ad6de965031dd"


def _assets():
    return files("chesssim.data").joinpath("pieces")


def test_84_pieces_have_verified_git_blob_hash_and_redistribution_rights():
    base = _assets()
    manifest = json.loads(base.joinpath("SOURCES.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "chesssim-piece-source-v1"
    assert manifest["upstream_repo"] == "lichess-org/lila"
    assert manifest["upstream_commit"] == PIN
    assert {row["id"] for row in manifest["sets"]} == set(LICENSES)
    assert len(manifest["sets"]) == 7
    for name in LICENSE_FILES:
        assert base.joinpath("licenses", name).is_file()
        assert len(base.joinpath("licenses", name).read_bytes()) > 1000
    count = 0
    for row in manifest["sets"]:
        set_id = row["id"]
        assert row["license"] == LICENSES[set_id]
        assert row["author"]
        assert set(row["files"]) == PIECES
        for filename, desc in row["files"].items():
            raw = base.joinpath(set_id, filename).read_bytes()
            assert len(raw) == desc["size"]
            digest = sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw,
                          usedforsecurity=False).hexdigest()
            assert digest == desc["sha"]
            assert len(raw) < 80_000
            text = raw.decode("utf-8")
            assert not re.search(
                r"<!DOCTYPE|<!ENTITY|<script\b|<foreignObject\b|<iframe\b|"
                r"<(?:image|animate|set)\b|<\?xml-stylesheet|"
                r"\son[a-z]{2,}\s*=|javascript:|data:|@import\b",
                text, re.I,
            ), f"{set_id}/{filename}"
            for match in re.finditer(r"\s(?:xlink:)?href\s*=\s*['\"]([^'\"]+)['\"]",
                                     text, flags=re.I):
                assert match.group(1).startswith("#")
            for match in re.finditer(r"url\(\s*['\"]?([^'\"\s)]+)",
                                     text, flags=re.I):
                assert match.group(1).startswith("#")
            root = ET.fromstring(raw)
            assert root.tag == "{http://www.w3.org/2000/svg}svg"
            count += 1
    assert count == 84


@contextmanager
def _serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        if hasattr(server, "arena_manager"):
            server.arena_manager.shutdown()
        thread.join(timeout=2)


def test_piece_assets_are_exactly_allowlisted_in_both_local_servers(tmp_path):
    (tmp_path / "summary.json").write_text(
        '{"status":"RUNNING","games":[]}', encoding="utf-8")
    base = _assets()
    for server in (
        arena.create_server(tmp_path, tmp_path / "sessions", port=0),
        live_board.create_server(tmp_path, port=0),
    ):
        with _serve(server) as url:
            for name in LICENSES:
                for piece in ("wK", "bQ", "wN", "bP"):
                    path = f"/piece-assets/{name}/{piece}.svg"
                    with urlopen(url + path, timeout=5) as response:
                        assert response.status == 200
                        assert response.headers["Content-Type"] == "image/svg+xml"
                        assert response.headers["Cache-Control"] == (
                            "private, max-age=86400, immutable")
                        assert response.headers["X-Content-Type-Options"] == "nosniff"
                        assert response.headers["Referrer-Policy"] == "no-referrer"
                        assert response.read() == base.joinpath(
                            name, piece + ".svg").read_bytes()
            with urlopen(url + "/", timeout=5) as response:
                csp = response.headers["Content-Security-Policy"]
                assert "img-src 'self'" in csp
                assert "default-src 'none'" in csp
            for path in (
                "/piece-assets/unknown/wK.svg",
                "/piece-assets/cburnett/wk.svg",
                "/piece-assets/cburnett/not-a-piece.svg",
                "/piece-assets/cburnett/wK.png",
                "/piece-assets/cburnett/wK.svg?redirect=1",
                "/piece-assets/cburnett/wK.svg/extra",
            ):
                with pytest.raises(HTTPError) as error:
                    urlopen(url + path, timeout=5)
                assert error.value.code == 404
            with pytest.raises(HTTPError) as forbidden:
                urlopen(Request(url + "/piece-assets/cburnett/wK.svg",
                                data=b"{}", method="POST"), timeout=5)
            assert forbidden.value.code in (403, 404, 405)


def test_svg_assets_are_in_wheel_manifest_and_have_notice():
    root = Path(__file__).resolve().parents[1]
    project = (root / "pyproject.toml").read_text(encoding="utf-8")
    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    notice = (root / "NOTICE.md").read_text(encoding="utf-8")
    assert '"pieces/*/*.svg"' in project
    assert '"pieces/licenses/*.txt"' in project
    assert '"pieces/SOURCES.json"' in project
    assert "source_pieces = json.loads(archive.read(" in ci
    assert "for piece_set in source_pieces['sets']:" in ci
    for set_id in LICENSES:
        assert set_id.casefold() in notice.casefold()
    assert "Public Domain Mark" in notice
