"""Read-only installation audit for all human-play opponents (not calibration)."""
from __future__ import annotations

import os
from pathlib import Path

import chess
import chess.engine

from .catalog import load_roster
from .human_game import resolve_human_profile
from .matches import file_hash


def audit_human_roster(root: Path, *, probe: bool = False) -> dict:
    """Check each registered file and optionally make one legal UCI move per alias.

    Generic Simple is tested once, not misrepresented as 43 independent engines.
    A static check does not establish that the executable can actually play.
    """
    root = root.expanduser().resolve()
    rows = []
    probes = {}
    for player in load_roster():
        row = {"slug": player.slug, "name": player.name,
               "profile_source": "individual" if player.rodent_alias else "shared_simple",
               "available": False, "uci": "NOT_RUN"}
        try:
            effective, exe, paths, source = resolve_human_profile(root, player)
            path = paths[player.slug]
            if not os.access(exe, os.X_OK):
                raise OSError("Executável sem permissão de execução")
            digest = file_hash(path)
            row.update(alias=effective.rodent_alias, profile_source=source,
                       profile_sha256=digest, available=True)
            if probe:
                key = (effective.rodent_alias, digest)
                if key not in probes:
                    probes[key] = _probe(root, exe, effective.rodent_alias)
                row.update(probes[key])
        except (OSError, ValueError) as exc:
            row["reason"] = str(exc)
        rows.append(row)
    available = sum(row["available"] for row in rows)
    verified = sum(row["uci"] == "PASS" for row in rows)
    return {"schema": "chesssim-human-roster-install-v1",
            "status": ("UCI_READY" if verified == 64 else "UCI_INCOMPLETE") if probe
                      else ("FILES_READY" if available == 64 else "FILES_INCOMPLETE"),
            "total": 64, "available": available, "uci_verified": verified,
            "distinct_uci_probes": len(probes), "historically_validated": False,
            "players": rows}


def _probe(root: Path, exe: Path, alias: str) -> dict:
    engine = None
    try:
        engine = chess.engine.SimpleEngine.popen_uci(
            str(exe), cwd=str(root), env={**os.environ, "RODENT4HOME": str(root)},
            timeout=15.0)
        option = engine.options.get("Personality")
        if option is None or alias not in (option.var or []):
            raise ValueError(f"Alias não anunciado por UCI: {alias}")
        engine.configure({"Personality": alias})
        if "UseBook" not in engine.options:
            raise ValueError("Motor não anuncia UseBook")
        engine.configure({"UseBook": False})
        board = chess.Board()
        move = engine.play(board, chess.engine.Limit(depth=1)).move
        if move is None or move not in board.legal_moves:
            raise ValueError("Motor não retornou lance legal")
        return {"uci": "PASS", "legal_move": move.uci()}
    except (OSError, ValueError, chess.engine.EngineError, TimeoutError) as exc:
        return {"uci": "FAIL", "reason": str(exc)}
    finally:
        if engine is not None:
            try:
                engine.quit()
            except (OSError, chess.engine.EngineError, TimeoutError):
                pass
            finally:
                engine.close()
