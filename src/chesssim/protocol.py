"""Protocolos versionados: separar teste rápido, torneio e calibração."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json


@dataclass(frozen=True)
class TournamentProtocol:
    version: str = "copa64-classico-v1"
    format: str = "duelo_2_jogos_cores_invertidas"
    time_control: str = "10+2"  # minutos + incremento em segundos
    threads_per_engine: int = 1
    hash_mb_per_engine: int = 64
    uci_limit_strength: bool = False
    use_book: bool = True
    pondering: bool = False
    adjudication: str = "somente_regras_do_xadrez_ou_tempo"
    max_plies_safety: int = 600
    opening_mode: str = "historical_guides_from_profiles"
    platform: str = "mesma_maquina_mesmo_motor_mesma_versao"
    tie_break: str = "pendente_de_decisao_antes_do_torneio"

    def json(self) -> dict:
        d = asdict(self)
        d["sha256"] = sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return d


OFFICIAL = TournamentProtocol()

CALIBRATION = {
    "version": "rodent-style-calibration-v1",
    "engine": "Rodent IV same executable",
    "source": "verified target-player historical PGN; Mecking first",
    "era": "explicit historical range; Mecking default 1973-1978",
    "split": "by whole game, deterministic seed; no FEN overlap",
    "book": False,
    "threads": 1,
    "hash_mb": 64,
    "limit_strength": False,
    "search": "fixed depth (default 5, test 3)",
    "metric": "held-out top-1 move agreement (not Elo or historical fidelity by itself)",
    "baseline": "original Karpov file, not a claimed Mecking model",
    "promotion": "never automatic; audit style, tactical quality, opening repertoire separately",
}
