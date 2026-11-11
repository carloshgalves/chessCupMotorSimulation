"""Read-only, opt-in mapping of a Stockfish report to one exact local PGN.

This overlay is *not* an engine: sampled positions without evidence stay unknown.
The replay cannot infer playing strength, win probability, or historical style.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re

MAX_REPORT_BYTES = 4 * 1024 * 1024
HEX_SHA = re.compile(r"[0-9a-f]{64}\Z")
PROTOCOL = "independent-stockfish-postgame-v9.1"


def _integer(value: object, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


class QualityOverlay:
    """Immutable validated presentation data, loaded once at server startup."""

    def __init__(self, path: Path):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
            raise ValueError("Relatório de análise ausente, simbólico ou acima do limite")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("protocol") != PROTOCOL:
            raise ValueError("Protocolo de análise de qualidade incompatível")
        inputs = raw.get("inputs")
        rows = raw.get("decisions")
        if not isinstance(inputs, dict) or not isinstance(rows, list) or len(rows) > 5000:
            raise ValueError("Análise sem entradas ou decisões válidas")
        digest = inputs.get("pgn_sha256")
        if not isinstance(digest, str) or not HEX_SHA.fullmatch(digest):
            raise ValueError("Falta hash verificável do PGN de origem")
        if not _integer(inputs.get("depth"), 1, 35):
            raise ValueError("Profundidade de análise inválida")
        engine = inputs.get("engine_uci_id")
        if not isinstance(engine, dict):
            raise ValueError("Identidade do motor ausente")
        name = engine.get("name", "Stockfish")
        if not isinstance(name, str) or not 1 <= len(name) <= 100:
            raise ValueError("Nome do motor inválido")
        self.pgn_sha256 = digest
        self.engine_name = name
        self.depth = inputs["depth"]
        self.positions = {}
        for row in rows:
            if not isinstance(row, dict) or row.get("game_index") != 0:
                raise ValueError("A camada visual aceita relatório de uma única partida")
            ply = row.get("ply")
            fen = row.get("fen")
            played = row.get("played")
            cp = row.get("played_eval_cp")
            mate = row.get("played_mate_in")
            loss = row.get("cp_loss")
            depth = row.get("depth")
            if not (_integer(ply, 1, 10000) and ply not in self.positions
                    and isinstance(fen, str) and 10 <= len(fen) <= 120
                    and isinstance(played, str) and re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbn]?", played)
                    and _integer(cp, -100000, 100000)
                    and (mate is None or _integer(mate, -10000, 10000))
                    and _integer(loss, 0, 100000)
                    and _integer(depth, 1, 35)):
                raise ValueError("Posição analisada inválida, duplicada ou não finita")
            critical = "blunder" if loss >= 250 else "mistake" if loss >= 100 else None
            self.positions[ply] = (fen, played, cp, mate, depth, loss, critical)

    def for_game(self, source: Path, states: list[dict]) -> dict[int, dict]:
        """Fail closed on any stale report/move rather than displaying foreign scores."""
        if source.name.endswith(".partial.pgn"):
            return {}
        digest = sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != self.pgn_sha256:
            return {}
        mapped = {}
        for ply, (fen, played, cp, mate, depth, loss, critical) in self.positions.items():
            if (ply >= len(states) or states[ply - 1]["fen"] != fen
                    or states[ply]["uci"] != played):
                return {}
            white_sign = 1 if fen.split()[1] == "w" else -1
            mapped[ply] = {
                "white_cp": white_sign * cp,
                "white_mate_in": white_sign * mate if mate is not None else None,
                "depth": depth, "engine": self.engine_name,
                "loss_cp": loss, "critical": critical,
                "source": "stockfish-sampled-forced-move",
            }
        return mapped
