"""Stacked History screens: representation and archive must coexist."""
from __future__ import annotations

from pathlib import Path
import re

DATA = Path(__file__).resolve().parents[1] / "src" / "chesssim" / "data"


def test_history_and_representation_share_one_document_with_unique_ids():
    html = (DATA / "arena.html").read_text(encoding="utf-8")
    ids = re.findall(r'\\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids)), "Duplicated History HTML IDs"
    assert {"history-modern", "representation-view", "history-original",
            "archive-pane-games", "rep-run", "archive-replay-dialog",
            "history-representation-open"}.issubset(ids)
    # The lab entry point is in the player header, not hidden in "Registros".
    archive = html.split('id="history-modern"', 1)[1].split(
        'id="history-original"', 1)[0]
    assert 'id="history-representation-open"' in archive
    assert 'id="archive-player-name"' in archive
    assert 'id="archive-replay-dialog"' in archive
    assert html.count('id="history-representation-open"') == 1
    assert html.index('src="/history.js"') < html.index('src="/representation.js"')
    assert html.index('src="/history.js"') < html.index('src="/history_archive.js"')
    for asset in ("representation.js", "representation.css",
                  "history_archive.js", "history_archive.css"):
        assert (DATA / asset).is_file()


def test_enter_leave_resume_preserve_both_history_views():
    history = (DATA / "history.js").read_text(encoding="utf-8")
    for call in (
        "ChessSimHistoryArchive?.enter(profile,state.players)",
        "ChessSimHistoryArchive?.leave()",
        "ChessSimHistoryArchive?.suspend()",
        "ChessSimHistoryArchive?.resume()",
        "ChessSimRepresentation?.enter(selected.slug)",
        "ChessSimRepresentation?.leave()",
    ):
        assert call in history
    assert "openRepresentation" in history
    assert "closeRepresentation" in history
    for asset in ("history_archive.js", "representation.js"):
        content = (DATA / asset).read_text(encoding="utf-8")
        assert "innerHTML" not in content
        assert "eval(" not in content


def test_server_routes_and_static_assets_for_both_features_remain_registered():
    server = (DATA.parent / "arena.py").read_text(encoding="utf-8")
    for route in (
        '"/api/history/representation/',
        '/archive/export/(json|csv|pgn)',
        '"/api/history/archive/replay"',
        '"/history_archive.js"',
        '"/representation.js"',
    ):
        assert route in server
    css = (DATA / "history_archive.css").read_text(encoding="utf-8")
    assert ".archive-lab-action" in css
