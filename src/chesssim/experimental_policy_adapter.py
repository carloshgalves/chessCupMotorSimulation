"""Opt-in experimental move-level adapter: trained policy + real Rodent UCI.

The adapter DOES NOT tune Rodent's internal weights, learn online, modify
personality files or participate in official tournament modes. It takes a
verified historical policy model trained in #137, obtains a real UCI move
from Rodent, and optionally overrides that move on selected development
plies. Every accepted intervention remains auditable.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import math
from pathlib import Path

import chess

from .real_policy_experiment import POLICY_SCHEMA, _digest, _read_json, predict
from .contextual_policy_v3 import (
    SCHEMA as CONTEXTUAL_SCHEMA, STATUS as CONTEXTUAL_STATUS,
    predict_contextual,
)

ERAS = frozenset({"overall", "early_observed", "middle_observed", "late_observed"})
RESEARCH_STATUS = "EXPERIMENTAL_EXTERNAL_POLICY_NOT_HISTORICALLY_VALIDATED"


@dataclass(frozen=True)
class PolicyDecision:
    move: chess.Move
    audit: dict


@dataclass(frozen=True)
class ExperimentalPolicyAdapter:
    """Immutable selection contract for exactly two roster slugs.

    Selection is deterministic for (seed, game number, player, ply, FEN).
    percent=0 means SHADOW: report the policy but keep Rodent bestmove.
    percent=100 means full external replacement on eligible plies.
    Intervening at 0<percent<100 changes *real* played moves by a
    repeatable sampling decision, not by editing the Rodent personality.
    """

    model: dict
    bindings: dict[str, str]
    override_percent: int
    seed: int
    min_ply: int
    file_sha256: str
    gate: dict | None = None

    @classmethod
    def load(cls, model_file: Path, *, bindings: dict[str, str],
             override_percent: int, seed: int = 2026,
             min_ply: int = 17,
             gate_file: Path | None = None) -> "ExperimentalPolicyAdapter":
        if (type(override_percent) is not int or
                not 0 <= override_percent <= 100):
            raise ValueError("Override percent must be an integer from 0 to 100")
        if type(seed) is not int or not 0 <= seed <= 2**63 - 1:
            raise ValueError("Policy seed must be a nonnegative 63-bit integer")
        if type(min_ply) is not int or not 1 <= min_ply <= 500:
            raise ValueError("Minimum policy ply must be from 1 to 500")
        if (not isinstance(bindings, dict) or len(bindings) != 2 or
                any(not isinstance(k, str) or not k for k in bindings) or
                any(v not in ERAS for v in bindings.values())):
            raise ValueError("Explicit player-to-era bindings required for two distinct players")

        # _read_json validates symlink/size and duplicate JSON keys.
        model_file = Path(model_file).expanduser()
        if model_file.is_symlink() or any(parent.is_symlink() for parent in model_file.parents):
            raise ValueError("Symbolic model files or model directories are not allowed")
        model_file = model_file.resolve(strict=True)
        raw = model_file.read_bytes()
        model = _read_json(model_file, 8_000_000)
        signature = model.get("model_sha256")
        statuses = {
            POLICY_SCHEMA: "REAL_PGN_TRAINED_EXPERIMENTAL_NOT_STYLE_VALIDATED",
            CONTEXTUAL_SCHEMA: CONTEXTUAL_STATUS,
        }
        if (model.get("schema") not in statuses or
                model.get("status") != statuses[model["schema"]] or
                not isinstance(signature, str) or len(signature) != 64):
            raise ValueError("Expected experimental trained historical-policy model")
        unsigned = {key: value for key, value in model.items()
                    if key != "model_sha256"}
        if _digest(unsigned) != signature:
            raise ValueError("Historical policy model SHA-256 is invalid")
        profiles = model.get("profiles")
        if not isinstance(profiles, dict) or not set(bindings) <= set(profiles):
            raise ValueError("Trained model does not cover both requested players")
        for slug, era in bindings.items():
            profile = profiles[slug]
            if not isinstance(profile, dict) or not isinstance(profile.get("weights"), dict):
                raise ValueError("Missing player-specific policy coefficients")
            career_weights = profile.get("career_weights")
            if not isinstance(career_weights, dict):
                raise ValueError("Missing observed-era policy coefficients")
            if era != "overall" and era not in career_weights:
                raise ValueError(f"Era {era} was not trained for {slug}; select overall explicitly")
            weights = profile["weights"] if era == "overall" else career_weights[era]
            if not isinstance(weights, dict) or any(
                    not isinstance(k, str) or not isinstance(value, (float, int)) or
                    isinstance(value, bool) or not math.isfinite(value) or abs(value) > 8
                    for k, value in weights.items()):
                raise ValueError("Invalid policy coefficients")
        generic = model.get("generic_weights")
        if not isinstance(generic, dict):
            raise ValueError("Missing generic policy baseline")
        gate = None
        if gate_file is not None:
            if any(era != "overall" for era in bindings.values()):
                raise ValueError("V4 gate was calibrated for overall V3 weights, not era-specific weights")
            if model["schema"] != CONTEXTUAL_SCHEMA:
                raise ValueError("Calibrated Rodent gate requires a contextual V3 model")
            from .real_gate_v4 import load_gate
            gate = load_gate(Path(gate_file).expanduser(), model)
            if min_ply < gate["min_training_ply"]:
                raise ValueError(
                    f"Calibrated V4 gate cannot act before its real training ply "
                    f"{gate['min_training_ply']}")
            if not set(bindings) <= set(gate["players"]):
                raise ValueError("Calibrated gate does not contain both players")
        return cls(
            model=model, bindings=dict(bindings), override_percent=override_percent,
            seed=seed, min_ply=min_ply, file_sha256=sha256(raw).hexdigest(),
            gate=gate)

    def manifest(self) -> dict:
        return {
            "status": RESEARCH_STATUS,
            "mechanism": (
                "GAME_DISJOINT_CALIBRATED_RODENT_V3_GATE"
                if self.gate is not None
                else "EXTERNAL_MOVE_OVERRIDE_AFTER_REAL_RODENT_UCI_BESTMOVE"),
            "model_sha256": self.model["model_sha256"],
            "model_file_sha256": self.file_sha256,
            "source_pgn_sha256": self.model.get("source_pgn_sha256"),
            "train_manifest_sha256": self.model.get("train_manifest_sha256"),
            "trained_method": self.model.get("method"),
            "model_schema": self.model.get("schema"),
            "gate_sha256": self.gate["gate_sha256"] if self.gate else None,
            "min_training_ply": self.gate["min_training_ply"] if self.gate else None,
            "gate_active_thresholds": (
                {p: self.gate["players"][p]["threshold"] for p in sorted(self.bindings)}
                if self.gate else None),
            "players_and_eras": dict(sorted(self.bindings.items())),
            "override_percent": self.override_percent,
            "seed": self.seed,
            "min_ply": self.min_ply,
            "opening_books": "DISABLED_REQUIRED",
            "engine_internal_parameters_changed": False,
            "learning_during_game": False,
            "official_tournament_allowed": False,
            "semantics": (
                "The model is a trained external move selector. Each engine "
                "bestmove is computed by unmodified Rodent IV; eligible moves "
                "may be replaced externally. This is NOT engine retraining, "
                "personality calibration or proof of historical fidelity."),
        }

    def select(self, board: chess.Board, *, slug: str,
               engine_move: chess.Move, round_number: int,
               ply: int) -> PolicyDecision:
        if slug not in self.bindings:
            raise ValueError("Player is not bound to the experimental policy")
        if (type(ply) is not int or ply != board.ply() + 1 or
                type(round_number) is not int or round_number < 1):
            raise ValueError("Policy decision index disagrees with the actual board")
        if board.is_game_over(claim_draw=True):
            raise ValueError("Cannot select a move from terminal game")
        if engine_move is None or engine_move not in board.legal_moves:
            raise ValueError("Rodent UCI bestmove is absent or illegal")
        fen_hash = sha256(board.fen().encode("utf-8")).hexdigest()
        audit = {
            "ply": ply,
            "player": slug,
            "era": self.bindings[slug],
            "fen_sha256": fen_hash,
            "rodent_move": engine_move.uci(),
            "policy_move": None,
            "played_move": engine_move.uci(),
            "eligible": ply >= self.min_ply,
            "sampled": False,
            "outcome": "WARMUP_UNMODIFIED",
        }
        if ply < self.min_ply:
            return PolicyDecision(move=engine_move, audit=audit)
        inference = (predict_contextual if self.model["schema"] == CONTEXTUAL_SCHEMA
                     else predict)
        if self.gate is not None:
            from .real_gate_v4 import gate_move
            gated_uci, calibrated = gate_move(
                self.gate, self.model, board, slug,
                self.bindings[slug], engine_move.uci())
            proposed = chess.Move.from_uci(calibrated["proposal"])
            allowed = chess.Move.from_uci(gated_uci)
            audit["gate_sha256"] = self.gate["gate_sha256"]
            audit["gate_threshold"] = calibrated["gate_threshold"]
            audit["style_lift"] = calibrated["style_lift"]
        else:
            proposed = chess.Move.from_uci(inference(
                self.model, board, slug, self.bindings[slug]))
            allowed = proposed
        if proposed not in board.legal_moves or allowed not in board.legal_moves:
            raise ValueError("Trained policy returned illegal move")
        audit["policy_move"] = proposed.uci()
        selection = sha256(
            f"{self.seed}|{round_number}|{slug}|{ply}|{fen_hash}".encode(
                "utf-8")).digest()
        # Comparison uses an unbiased 64-bit threshold (not modulo 100).
        threshold = self.override_percent * (1 << 64) // 100
        sampled = int.from_bytes(selection[:8], "big") < threshold
        audit["sampled"] = sampled
        if self.override_percent == 0:
            audit["outcome"] = "SHADOW_ONLY"
            return PolicyDecision(move=engine_move, audit=audit)
        if not sampled:
            audit["outcome"] = "RODENT_RETAINED_BY_SAMPLING"
            return PolicyDecision(move=engine_move, audit=audit)
        if proposed == engine_move:
            audit["outcome"] = "POLICY_RODENT_AGREEMENT"
            return PolicyDecision(move=engine_move, audit=audit)
        if self.gate is not None and allowed == engine_move:
            audit["outcome"] = "GATE_RETAINED_RODENT"
            return PolicyDecision(move=engine_move, audit=audit)
        audit["outcome"] = "EXTERNAL_POLICY_OVERRIDE"
        audit["played_move"] = allowed.uci()
        return PolicyDecision(move=allowed, audit=audit)


def trace_summary(events: list[dict]) -> dict:
    by_player = {}
    for event in events:
        stats = by_player.setdefault(event["player"], {
            "played_plies": 0, "eligible": 0, "sampled": 0,
            "actual_overrides": 0, "agreements": 0,
            "shadow_decisions": 0,
        })
        stats["played_plies"] += 1
        stats["eligible"] += int(event["eligible"])
        stats["sampled"] += int(event["sampled"])
        stats["actual_overrides"] += int(event["outcome"] == "EXTERNAL_POLICY_OVERRIDE")
        stats["agreements"] += int(event["outcome"] == "POLICY_RODENT_AGREEMENT")
        stats["shadow_decisions"] += int(event["outcome"] == "SHADOW_ONLY")
    return {k: v for k, v in sorted(by_player.items())}
