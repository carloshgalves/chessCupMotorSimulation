"""Training-only experimental split of an actively linked, rights-attested corpus.

No validation/holdout PGN, UCI engine, rating, or historical style claim is
published. The source cache still contains all games: this is a reproducible
data-preparation guardrail, not a sealed filesystem security boundary.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil

import chess.pgn

from . import custom_corpus_links as links
from . import custom_profiles as core
from .age_cohort import _cache_snapshot, _full_date, _header_name, _partition
from .matches import _atomic_text

SCHEMA = "chesssim-custom-train-only-split-v1"
SCHEMA_POSITION_AUDITED = "chesssim-custom-train-only-split-v2"
MAX_OUTPUT_MB = 32
MAX_GAMES = 1000


def _target(out: Path, cache: Path, library: Path) -> Path:
    output = core._safe_path(out)
    if output.exists() or output.is_symlink():
        raise FileExistsError("Diretório de saída já existe")
    if not output.parent.is_dir():
        raise ValueError("Diretório pai da saída precisa existir")
    if (output.is_relative_to(core._safe_path(cache))
            or output.is_relative_to(core._safe_path(library))):
        raise ValueError("Saída deve ficar fora do cache de origem e da biblioteca")
    return output


def _partition_record(entries: list[dict], *, expose_train: bool) -> dict:
    return {
        "games": len(entries),
        "first_date": entries[0]["date"] if entries else None,
        "last_date": entries[-1]["date"] if entries else None,
        "white_games": sum(row["side"] == "white" for row in entries),
        "black_games": sum(row["side"] == "black" for row in entries),
        # Sealed groups expose only a SHA-256 commitment; no UCI moves or PGN.
        "game_commitment_sha256": core._sha([
            {"date": row["date"], "mainline_sha256": row["mainline_sha256"]}
            for row in entries
        ]),
        "mainline_sha256": (
            [row["mainline_sha256"] for row in entries] if expose_train else None),
        "exported_pgn": bool(expose_train),
    }


def prepare_custom_train(library: Path, identifier: str, cache: Path, out: Path,
                         *, expected_head: str, min_train: int = 20,
                         max_output_mb: int = 32,
                         embargo_min_ply: int | None = None) -> dict:
    """Only training PGN is materialized after a link + source revalidation.

    The shared library lock is held across source verification and publication.
    Thus a cooperative CLI detach/retire cannot invalidate the snapshot while
    being exported. The external source is parsed exactly once into memory.
    """
    if type(min_train) is not int or not 1 <= min_train <= MAX_GAMES:
        raise ValueError("min-train deve estar entre 1 e 1000")
    if type(max_output_mb) is not int or not 1 <= max_output_mb <= MAX_OUTPUT_MB:
        raise ValueError("max-output-mb deve estar entre 1 e 32")
    if embargo_min_ply is not None and (
            type(embargo_min_ply) is not int
            or not 1 <= embargo_min_ply <= 80):
        raise ValueError("embargo-min-ply deve ser inteiro de 1 a 80")
    if not isinstance(expected_head, str) or not core.DIGEST.fullmatch(expected_head):
        raise ValueError("Informe head_sha256 válido do vínculo")
    root = core._library(library)
    if not root.is_dir():
        raise ValueError("Biblioteca local inexistente")
    output = _target(out, cache.expanduser(), root)

    with core._exclusive_library(root):
        doc, _ = core._read_id(root, identifier)
        if core._read_retirement(root, doc):
            raise ValueError("Personagem desativado não pode preparar treino")
        events = links._events(root, doc)
        if not events or events[-1]["event"]["action"] != "LINK":
            raise ValueError("Personagem não possui corpus ativo")
        latest = events[-1]
        if latest["sha256"] != expected_head:
            raise ValueError("Vínculo obsoleto: expected-head diverge do evento atual")
        linked = latest["event"]["link"]
        if linked["games"] == 0:
            # A zero-game link provides no data and should not publish a
            # misleading empty training directory.
            raise ValueError("Vínculo sem partidas elegíveis não produz treino")
        name = linked["player_pgn_name_operator_attested"]
        snapshot = _cache_snapshot(cache.expanduser())
        source, games = snapshot
        verified = links._selection(cache, name, snapshot=snapshot)
        if verified != linked:
            raise ValueError("Cache ou recorte diverge do vínculo ativo; desvincule e revise")

        key = links._clean_name(name)
        excluded = Counter()
        selected = []
        now = date.today()
        for game, digest in games:
            white = _header_name(game.headers.get("White")) == key
            black = _header_name(game.headers.get("Black")) == key
            if white and black:
                excluded["ambiguous_both_sides"] += 1
                continue
            if not white and not black:
                continue
            played = _full_date(game.headers.get("Date"))
            if played is None:
                excluded["unknown_or_invalid_date"] += 1
                continue
            if played > now:
                excluded["future_date"] += 1
                continue
            selected.append({
                "mainline_sha256": digest,
                "date": played.isoformat(),
                "side": "white" if white else "black",
                "_game": game,
            })
        # We do not rewrite the link: its audit count includes games with
        # incomplete dates; those are quarantined from train/validation/holdout.
        if (len(selected) + excluded["unknown_or_invalid_date"] +
                excluded["future_date"] != linked["games"]):
            # Games where both headers equal the same ambiguous name were
            # already excluded by the link itself; they are diagnostic only.
            raise ValueError("Partidas elegíveis não reconciliadas com vínculo")
        pieces = _partition(selected)  # chronological whole-date grouping
        embargo = {
            "schema": "chesssim-custom-position-embargo-v1",
            "status": "NOT_RUN",
            "reason": (
                "The historical v1 train-only export is not position embargoed; "
                "pass --embargo-min-ply explicitly to audit and quarantine."
            ),
        }
        if embargo_min_ply is not None:
            from .custom_position_embargo import apply_embargo
            pieces, embargo = apply_embargo(pieces, min_ply=embargo_min_ply)
        train_text = "".join(
            row["_game"].accept(chess.pgn.StringExporter(
                headers=True, variations=False, comments=False)).rstrip() + "\n\n"
            for row in pieces["train"]
        )
        train_bytes = train_text.encode("utf-8")
        sufficient = (len(pieces["train"]) >= min_train
                      and bool(pieces["validation"]) and bool(pieces["holdout"]))
        embargo_incomplete = (embargo_min_ply is not None
                              and embargo["status"] != "AUDITED_DISJOINT")
        report = {
            "schema": (SCHEMA_POSITION_AUDITED if embargo_min_ply is not None
                       else SCHEMA),
            "status": (
                "INSUFFICIENT_POSITION_EVIDENCE" if embargo_incomplete else
                "TRAIN_ONLY_PREPARED_NOT_VALIDATED" if sufficient else
                "INSUFFICIENT_PARTITION_EVIDENCE"),
            "training_only": True,
            "uci_eligible": False,
            "historically_validated": False,
            "player_id": identifier,
            "display_name_at_export": core._summary(root, doc)["display_name"],
            "profile_sha256": doc["sha256"],
            "name_revision_sha256_at_link": latest["event"]["name_revision_sha256"],
            "corpus_head_sha256": expected_head,
            "source": {
                "cache_pgn_sha256": source["cache_pgn_sha256"],
                "source_catalog_sha256": source["source_catalog_sha256"],
                "selection_sha256": verified["selection_sha256"],
                "rights_status": verified["rights_status"],
                "sources_operator_attested": verified["source_ids"],
                "player_pgn_name_operator_attested": name,
                "read_at_utc": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"),
            },
            "linked_unique_games": linked["games"],
            "fully_dated_nonfuture_games": len(selected),
            "excluded": dict(sorted(excluded.items())),
            "minimum_train_games": min_train,
            "partition_policy": "chronological 60/20/20 targets; civil dates not split",
            "position_embargo": embargo,
            "partitions": {
                part: _partition_record(rows, expose_train=part == "train")
                for part, rows in pieces.items()
            },
            "train_pgn_sha256": sha256(train_bytes).hexdigest(),
            "train_pgn_bytes": len(train_bytes),
            "limits": [
                "Not a calibrated, installed or playable UCI profile.",
                "Rights and exact player identity remain operator-attested.",
                "No validation or holdout PGN is emitted; original cache still holds all games.",
                "Without --embargo-min-ply position cross-split overlap is not checked; in opt-in mode only post-move positions from the specified ply are checked.",
                "Unknown or future-dated games do not enter any partition.",
                "Not an independently sealed holdout, licensed redistribution, or source of engine strength.",
            ],
        }
        manifest_text = json.dumps(
            report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if len(train_bytes) + len(manifest_text.encode("utf-8")) > (
            max_output_mb * 1024 * 1024
        ):
            raise ValueError("PGN de treino mais manifesto excede orçamento da saída")
        # Recheck the destination after potentially expensive cache processing.
        output = _target(out, cache.expanduser(), root)
        output.mkdir(mode=0o700, exist_ok=False)
        try:
            _atomic_text(output / "train.pgn", train_text,
                         durable=os.name == "posix")
            # The manifest is the final commit marker.
            _atomic_text(output / "split.json", manifest_text,
                         durable=os.name == "posix")
        except BaseException:
            shutil.rmtree(output)
            raise
        return report
