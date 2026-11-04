"""Optional smoke test for a real UCI engine; unit tests remain engine-free."""
from __future__ import annotations

import os
from pathlib import Path
import shutil

import chess
import chess.engine
import pytest


@pytest.mark.requires_engine
def test_configured_uci_engine_handshake_and_legal_move() -> None:
    configured = os.environ.get("CHESSSIM_UCI_ENGINE")
    if not configured:
        pytest.skip("Set CHESSSIM_UCI_ENGINE to run the external UCI smoke test")

    executable = shutil.which(configured)
    if executable is None:
        candidate = Path(configured).expanduser()
        if not candidate.is_file():
            pytest.fail(
                "CHESSSIM_UCI_ENGINE does not resolve to a file or executable: "
                f"{configured}"
            )
        executable = str(candidate.resolve())

    engine = chess.engine.SimpleEngine.popen_uci(executable, timeout=10.0)
    try:
        board = chess.Board()
        result = engine.play(board, chess.engine.Limit(depth=1))
        assert result.move is not None, "UCI engine returned no move"
        assert result.move in board.legal_moves, "UCI engine returned an illegal move"
    finally:
        engine.quit()
