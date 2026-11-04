#!/usr/bin/env python3
"""Linux/WSL preflight and real Rodent IV match smoke (no third-party binaries bundled)."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

import chess
import chess.engine
import chess.pgn

from chesssim.catalog import find_player
from chesssim.rodent import profile_status

ROOT = Path(__file__).resolve().parents[1]


class SetupError(RuntimeError):
    """Recoverable environment or smoke verification error."""


def resolve_engine(value: str) -> Path:
    path = shutil.which(value)
    executable = Path(path if path else value).expanduser().resolve()
    if not executable.is_file():
        raise SetupError(
            f"UCI engine not found: {value}. Set CHESSSIM_UCI_ENGINE to a real "
            "executable or use --uci-engine /absolute/path."
        )
    if not os.access(executable, os.X_OK):
        raise SetupError(
            f"UCI engine cannot be executed: {executable}. Check Linux permissions "
            "and WSL executable interoperability (or use a native Linux engine)."
        )
    return executable


def resolve_rodent(value: str | None) -> tuple[Path, Path]:
    if not value:
        raise SetupError(
            "Rodent IV path missing. Export RODENT_IV_HOME=/path/to/Rodent-IV "
            "or pass --rodent /path/to/Rodent-IV."
        )
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise SetupError(f"Rodent IV directory not found: {root}. Set RODENT_IV_HOME to its ROOT directory.")
    executable = root / "rodent-iv-plain.exe"
    ini = root / "personalities" / "basic.ini"
    if not executable.is_file():
        raise SetupError(
            f"Rodent binary missing: {executable}. Obtain Rodent IV separately; "
            "the setup script does not download or redistribute motor executables."
        )
    if not ini.is_file():
        raise SetupError(
            f"Rodent personality registry missing: {ini}. Use an intact Rodent IV "
            "installation; the path must be the root containing personalities/basic.ini."
        )
    if not os.access(executable, os.X_OK):
        raise SetupError(
            f"Rodent binary is not executable: {executable}. Fix executable permissions "
            "or use a compatible Linux/WSL binary or WSL interoperability."
        )
    return root, executable


def probe_uci(executable: Path, *, cwd: Path | None = None,
              require_personality: bool = False) -> dict:
    """Complete UCI handshake and depth-1 legal-move check; always shut down."""
    try:
        engine = chess.engine.SimpleEngine.popen_uci(
            str(executable), cwd=str(cwd) if cwd else None, timeout=15.0
        )
    except (OSError, chess.engine.EngineError, chess.engine.EngineTerminatedError,
            TimeoutError) as exc:
        raise SetupError(
            f"Could not start UCI engine at {executable}: {exc}. "
            "On WSL, confirm Windows .exe interoperability or install a compatible engine."
        ) from exc
    try:
        if require_personality and "Personality" not in engine.options:
            raise SetupError(
                "The configured Rodent engine did not advertise UCI 'Personality'. "
                "Check that the binary is Rodent IV and that basic.ini is intact."
            )
        board = chess.Board()
        result = engine.play(board, chess.engine.Limit(depth=1))
        if result.move not in board.legal_moves:
            raise SetupError(
                f"UCI engine at {executable} returned no legal move at depth 1."
            )
        return {"engine_id": dict(engine.id), "legal_move": result.move.uci()}
    except (OSError, chess.engine.EngineError, chess.engine.EngineTerminatedError,
            TimeoutError) as exc:
        raise SetupError(f"UCI handshake/move failed for {executable}: {exc}") from exc
    finally:
        try:
            engine.quit()
        except (OSError, chess.engine.EngineError, chess.engine.EngineTerminatedError,
                TimeoutError):
            engine.close()


def require_profiles(root: Path, white: str, black: str) -> tuple[str, str]:
    participants = (find_player(white), find_player(black))
    if participants[0].slug == participants[1].slug:
        raise SetupError("Smoke requires two distinct players.")
    ready = {name: status for name, _, status in profile_status(root)}
    for player in participants:
        state = ready.get(player.name)
        if state != "PRONTO_PARA_TESTE":
            raise SetupError(
                f"Rodent profile for {player.name} not ready ({state}). "
                "Run python -m chesssim setup --rodent PATH for a preview, then "
                "explicitly rerun with --apply-profiles if safe."
            )
    return participants[0].slug, participants[1].slug


def validate_pgn(folder: Path, white: str, black: str) -> dict:
    """Check actual PGN bytes, legal mainline, results and consistent JSON metadata."""
    summary_path = folder / "summary.json"
    pgn_path = folder / "jogo_1.pgn"
    combined = folder / "duelo.pgn"
    if not summary_path.is_file() or not pgn_path.is_file() or not combined.is_file():
        raise SetupError(f"Expected PGN and summary files missing in: {folder}")
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SetupError(f"Invalid summary JSON: {summary_path}: {exc}") from exc
    games = summary.get("games", [])
    if len(games) != 1 or games[0].get("status") not in ("COMPLETED", "INCOMPLETE"):
        raise SetupError("Smoke summary must record exactly one finished or ply-limited game")
    record = games[0]
    if (record.get("white"), record.get("black")) != (white, black):
        raise SetupError("Smoke participants differ from summary")
    if summary.get("status") not in ("COMPLETED", "INCOMPLETE"):
        raise SetupError("Smoke summary has an invalid overall status")
    if summary["status"] != record["status"]:
        raise SetupError("Smoke summary and game status disagree")
    moves_in_first_pgn: list[str] | None = None
    for path in (pgn_path, combined):
        with path.open(encoding="utf-8") as handle:
            game = chess.pgn.read_game(handle)
            if game is None or game.errors or chess.pgn.read_game(handle) is not None:
                raise SetupError(f"Invalid PGN or more than one game: {path}")
        if game.headers.get("Result") != record["result"]:
            raise SetupError(f"PGN Result does not match summary: {path}")
        if game.headers.get("Termination") != record["termination"]:
            raise SetupError(f"PGN termination does not match summary: {path}")
        board = game.board()
        for move in game.mainline_moves():
            if move not in board.legal_moves:
                raise SetupError(f"Illegal move in PGN: {move.uci()}")
            board.push(move)
        if board.ply() != record["plies"] or board.ply() < 1:
            raise SetupError(f"Missing or mismatched smoke moves in PGN: {path}")
        line = [move.uci() for move in game.mainline_moves()]
        if moves_in_first_pgn is None:
            moves_in_first_pgn = line
        elif line != moves_in_first_pgn:
            raise SetupError("duelo.pgn mainline differs from jogo_1.pgn")
        outcome = board.outcome(claim_draw=True)
        if record["status"] == "INCOMPLETE":
            if (record["result"], record["termination"]) != ("*", "InterruptedAtPlyLimit"):
                raise SetupError("An incomplete game must not be counted as a draw/win")
            if outcome is not None:
                raise SetupError("A terminal PGN must not be labelled incomplete")
        elif record["termination"] == "TimeForfeit":
            if record["result"] not in ("1-0", "0-1", "1/2-1/2"):
                raise SetupError("A clock-forfeited PGN requires a decisive or drawn result")
        elif (outcome is None or outcome.result() != record["result"]
              or outcome.termination.name != record["termination"]):
            raise SetupError("Completed PGN conflicts with chess outcome")
    return {
        "folder": str(folder),
        "pgn": str(pgn_path),
        "summary": str(summary_path),
        "status": record["status"],
        "result": record["result"],
        "plies": record["plies"],
        "pgn_sha256": sha256(pgn_path.read_bytes()).hexdigest(),
        "execution_limit": "short experimental smoke, not a completed tournament match",
    }


def run_smoke(python: Path, rodent: Path, out: Path, *,
              white: str, black: str, depth: int, max_plies: int) -> dict:
    """Never overwrite an existing destination; inspect results of the real CLI."""
    if out.exists():
        raise SetupError(f"Smoke output directory already exists: {out}. Choose --out NEW_PATH.")
    out.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [str(python), "-m", "chesssim", "play", white, black, "--rodent",
         str(rodent), "--depth", str(depth), "--max-plies", str(max_plies),
         "--out", str(out)],
        cwd=ROOT, check=False
    )
    if completed.returncode != 0:
        raise SetupError(
            f"Real Rodent game failed with exit code {completed.returncode}; "
            f"inspect partial artifacts in {out}. Check profiles/engine compatibility."
        )
    folders = list(out.glob(f"{white}-vs-{black}/*/summary.json"))
    if len(folders) != 1:
        raise SetupError(
            f"Expected exactly one real duel summary, found {len(folders)} in {out}"
        )
    report = validate_pgn(folders[0].parent, white, black)
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    report["environment"] = "Linux/WSL external Rodent IV"
    report["rodent_dir"] = str(rodent)
    report["mode"] = "SHORT_REAL_UCI_SMOKE"
    (out / "smoke_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rodent", help="Rodent IV root directory (or RODENT_IV_HOME)")
    parser.add_argument("--uci-engine", help="Optional general UCI engine path (or CHESSSIM_UCI_ENGINE)")
    parser.add_argument("--apply-profiles", action="store_true",
                        help="EXPLICIT permission to modify Rodent profile registry and download missing profiles")
    parser.add_argument("--with-books", action="store_true",
                        help="Download referenced external Polyglot books (requires --apply-profiles)")
    parser.add_argument("--smoke", action="store_true", help="Play one real short Rodent duel and verify PGN")
    parser.add_argument("--out", type=Path, default=None, help="New directory for smoke artifacts")
    parser.add_argument("--white", default="fischer")
    parser.add_argument("--black", default="tal")
    parser.add_argument("--depth", type=int, default=1)
    parser.add_argument("--max-plies", type=int, default=12)
    args = parser.parse_args(argv)
    if args.with_books and not args.apply_profiles:
        parser.error("--with-books requires explicit --apply-profiles")
    if not 1 <= args.depth <= 10 or not 1 <= args.max_plies <= 100:
        parser.error("Smoke --depth must be 1..10 and --max-plies 1..100")

    try:
        if args.uci_engine or os.getenv("CHESSSIM_UCI_ENGINE"):
            engine = resolve_engine(args.uci_engine or os.environ["CHESSSIM_UCI_ENGINE"])
            details = probe_uci(engine)
            print(f"Generic UCI OK: {engine}; depth-1 move {details['legal_move']}", flush=True)
        path = args.rodent or os.getenv("RODENT_IV_HOME")
        if not path and not (args.smoke or args.apply_profiles):
            print("Python setup OK. Rodent IV not configured; to run a real match set "
                  "RODENT_IV_HOME or pass --rodent. Nothing has been downloaded.", flush=True)
            return 0
        root, executable = resolve_rodent(path)
        details = probe_uci(executable, cwd=root, require_personality=True)
        print(f"Rodent UCI OK: {executable}; depth-1 move {details['legal_move']}", flush=True)
        if args.apply_profiles:
            cmd = [sys.executable, "-m", "chesssim", "setup", "--rodent", str(root), "--apply"]
            if args.with_books:
                cmd.append("--with-books")
            subprocess.run(cmd, cwd=ROOT, check=True)
        else:
            print("No profile or book changes requested; --apply-profiles is opt-in.", flush=True)
        if args.smoke:
            white, black = require_profiles(root, args.white, args.black)
            out = (args.out or ROOT / "runs" / "setup-smoke" /
                   (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    + "-" + uuid4().hex[:8])).expanduser().resolve()
            report = run_smoke(Path(sys.executable), root, out,
                               white=white, black=black, depth=args.depth,
                               max_plies=args.max_plies)
            print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        else:
            print("To play and verify a real short PGN, add --smoke.", flush=True)
        return 0
    except (SetupError, subprocess.CalledProcessError, OSError, ValueError) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
