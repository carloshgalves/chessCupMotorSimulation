"""Read-only historical representation evidence and opt-in local UCI tests.

No online PGN retrieval, strength inference from Elo, unlicensed redistribution,
or publication of an uncalibrated historical-personality percentage.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import threading
import uuid

from .calibration import evaluate_candidate
from .catalog import find_player
from .historical import extract_decisions, split_three_ways
from .history_profiles import load_history_profile

SCHEMA = "chesssim-representation-test-v1"
MAX_SOURCE_BYTES = 5_000_000
MAX_REPORT_BYTES = 100_000
MAX_REPORTS_PER_PLAYER = 100


def _report_digest(report: dict) -> str:
    """Cover every public report field; verify it before displaying a metric."""
    plain = {key: value for key, value in report.items()
             if key != "report_sha256"}
    return sha256(json.dumps(plain, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()



class RepresentationInput(ValueError):
    """Expected user input cannot be used safely."""


def _player(slug: str):
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9-]{2,48}", slug):
        raise RepresentationInput("Jogador inválido")
    try:
        result = find_player(slug)
    except ValueError as exc:
        raise RepresentationInput("Jogador fora do catálogo") from exc
    if result.slug != slug:
        raise RepresentationInput("Use o slug canônico")
    return result


def _resource(root: Path, person) -> tuple[Path, Path]:
    if not person.rodent_alias:
        raise RepresentationInput("Perfil do motor não está disponível para esse jogador")
    exe = root / "rodent-iv-plain.exe"
    profile = root / "personalities" / person.profile_filename
    base = root / "personalities"
    if (exe.is_symlink() or not exe.is_file() or exe.stat().st_size > 100_000_000 or
            base.is_symlink() or not base.is_dir() or profile.is_symlink() or
            not profile.is_file() or profile.parent != base):
        raise RepresentationInput("Motor/perfil Rodent IV indisponível ou inseguro")
    if profile.stat().st_size > 1_000_000:
        raise RepresentationInput("Perfil Rodent IV excede limite")
    return exe, profile


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _source(root: Path, slug: str) -> Path | None:
    parent = root / "representation_sources"
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise RepresentationInput("Pasta de corpora não é um diretório seguro")
    file = parent / (slug + ".pgn")
    if file.is_symlink() or (file.exists() and (not file.is_file() or
                                                file.stat().st_size > MAX_SOURCE_BYTES)):
        raise RepresentationInput("PGN inválido, redirecionado ou maior que 5 MB")
    return file if file.is_file() else None


def _results(workspace: Path, slug: str) -> list[dict]:
    root = workspace / "representation_results"
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise RepresentationInput("Diretório de resultados inseguro")
    folder = root / slug
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        raise RepresentationInput("Diretório do jogador inseguro")
    if not folder.is_dir():
        return []
    paths = sorted(folder.glob("test-*.json"), reverse=True)
    if len(paths) > MAX_REPORTS_PER_PLAYER:
        raise RepresentationInput("Limite de relatórios excedido")
    reports = []
    for path in paths:
        if path.is_symlink() or path.stat().st_size > MAX_REPORT_BYTES:
            raise RepresentationInput("Relatório redirecionado ou grande demais")
        raw = path.read_bytes()
        if len(raw) > MAX_REPORT_BYTES:
            raise RepresentationInput("Relatório excede limite")
        try:
            item = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RepresentationInput("Relatório inválido") from exc
        if (not isinstance(item, dict) or item.get("schema") != SCHEMA or
                item.get("slug") != slug or item.get("id") != path.stem or
                not re.fullmatch(r"test-[0-9a-f]{16}", path.stem) or
                not isinstance(item.get("report_sha256"), str) or
                not re.fullmatch(r"[a-f0-9]{64}", item["report_sha256"]) or
                item["report_sha256"] != _report_digest(item)):
            raise RepresentationInput("Identidade ou schema do relatório inválido")
        reports.append(item)
    return reports


def representation_status(root: Path, workspace: Path, slug: str) -> dict:
    person = _player(slug)
    profile = load_history_profile(slug)
    try:
        executable, personality = _resource(root, person)
        installed = True
        hashes = {"engine_sha256": _sha(executable),
                  "personality_sha256": _sha(personality)}
    except RepresentationInput:
        installed = False
        hashes = None
    pgndata = _source(workspace, slug)
    result = _results(workspace, slug)
    return {
        "schema": "chesssim-representation-v1",
        "slug": slug,
        "name": person.name,
        "period": person.peak_period or profile["era_label"],
        "period_status": "CATALOG_REFERENCE_UNVERIFIED",
        "review_status": profile["review_status"],
        "personality": person.rodent_alias,
        "installed": installed,
        "source_available": pgndata is not None,
        "source_sha256": _sha(pgndata) if pgndata else None,
        "engine": hashes,
        "reports": result,
        "rating_history": [],
        "historical_rating_status": "NOT_IMPORTABLE_WITH_CURRENT_EVIDENCE",
        "measures": {"historical_fidelity_percent": None,
                     "strength_calibration": None,
                     "opening_similarity": None,
                     "results_distribution": None},
        "disclaimer": ("Top-1 por decisões históricas é diagnóstico de concordância, "
                       "não Elo, força nem fidelidade histórica validada."),
    }


def normalize_test_request(raw: object) -> dict:
    if not isinstance(raw, dict) or set(raw) != {
        "match_name", "from_year", "to_year", "depth", "positions", "seed"}:
        raise RepresentationInput("Parâmetros do teste incompletos ou desconhecidos")
    match = raw["match_name"]
    if (not isinstance(match, str) or not 2 <= len(match) <= 70 or
            not re.fullmatch(r"[\w \-.'À-ÿ]+", match)):
        raise RepresentationInput("Nome de pesquisa PGN inválido")
    for key, lo, hi in [("from_year", 1750, 2026), ("to_year", 1750, 2026),
                        ("depth", 1, 16), ("positions", 5, 40),
                        ("seed", 0, 2_147_483_647)]:
        value = raw[key]
        if type(value) is not int or not lo <= value <= hi:
            raise RepresentationInput("Fora do intervalo: " + key)
    if raw["from_year"] > raw["to_year"]:
        raise RepresentationInput("Intervalo de anos invertido")
    return dict(raw, match_name=match.strip())


class RepresentationLab:
    """Single local experiment worker; all reads are side-effect free."""

    def __init__(self, root: Path, workspace: Path, *,
                 evaluator=evaluate_candidate):
        self.root = root
        self.workspace = workspace
        self.evaluator = evaluator
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._active: dict | None = None

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._active is not None

    def status(self, slug: str) -> dict:
        result = representation_status(self.root, self.workspace, slug)
        with self._lock:
            result["active"] = dict(self._active) if self._active and (
                self._active["slug"] == slug) else None
            recent = getattr(self, "_last", None)
            result["last"] = dict(recent) if recent and recent["slug"] == slug else None
        return result

    def start(self, slug: str, raw: object, *, simulator_busy: bool = False) -> dict:
        person = _player(slug)
        args = normalize_test_request(raw)
        if simulator_busy:
            raise RepresentationInput("Uma partida do simulador já está em execução")
        _resource(self.root, person)
        if not _source(self.workspace, slug):
            raise RepresentationInput("PGN local ausente: representation_sources/" + slug + ".pgn")
        # No network, no new corpus, no writes before the local pre-flight.
        with self._lock:
            if self._active is not None:
                raise RepresentationInput("Já existe um teste de representação em execução")
            test_id = "test-" + uuid.uuid4().hex[:16]
            self._active = {"id": test_id, "slug": slug, "status": "RUNNING"}
            self._thread = threading.Thread(
                target=self._worker, args=(person, args, test_id), daemon=True)
            self._thread.start()
            return {"id": test_id, "slug": slug, "status": "RUNNING"}

    def _worker(self, person, args: dict, test_id: str) -> None:
        try:
            source = _source(self.workspace, person.slug)
            if source is None:
                raise RepresentationInput("Arquivo de evidência não encontrado")
            exe, profile = _resource(self.root, person)
            source_digest = _sha(source)
            engine_digest = _sha(exe)
            profile_digest = _sha(profile)
            decisions, stats = extract_decisions(
                source, player_name=args["match_name"],
                from_year=args["from_year"], to_year=args["to_year"])
            _, validation, _, partition = split_three_ways(
                decisions, seed=args["seed"], train_limit=40,
                val_limit=args["positions"], test_limit=20)
            metrics = self.evaluator(
                self.root, person.profile_filename,
                validation, args["depth"], timeout=20)
            if (_sha(source) != source_digest or _sha(exe) != engine_digest or
                    _sha(profile) != profile_digest):
                raise RepresentationInput("Fonte ou motor mudou durante o teste")
            if (not isinstance(metrics, dict) or
                    type(metrics.get("decisions")) is not int or
                    metrics["decisions"] < 1 or
                    metrics["decisions"] != len(validation) or
                    type(metrics.get("correct")) is not int or
                    not 0 <= metrics["correct"] <= metrics["decisions"]):
                raise RepresentationInput("Retorno do avaliador inconsistente")
            report = {
                "schema": SCHEMA, "id": test_id, "slug": person.slug,
                "status": "EXPLORATORY_NOT_HISTORICALLY_VALIDATED",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "depth": args["depth"], "seed": args["seed"],
                "period": [args["from_year"], args["to_year"]],
                "match_name": args["match_name"],
                "sample": {"accepted_games": stats["games_accepted"],
                           "validation_games": partition["validation"]["games_sampled"],
                           "evaluated_decisions": metrics["decisions"],
                           "correct_top1": metrics["correct"],
                           "top1_rate": metrics["correct"]/metrics["decisions"]},
                "evidence": {"source_pgn_sha256": source_digest,
                             "rodent_sha256": engine_digest,
                             "personality_sha256": profile_digest,
                             "partition_sha256": partition["partition_sha256"]},
                "cautions": ["Concordância top-1 não mede fidelidade estilística ou força.",
                             "O conjunto de teste final permanece lacrado.",
                             "Sem avaliação cega e calibração de força independente."],
            }
            report["report_sha256"] = _report_digest(report)
            root = self.workspace / "representation_results"
            folder = root / person.slug
            if (root.is_symlink() or folder.is_symlink()):
                raise RepresentationInput("Saída redirecionada")
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / (test_id + ".json")
            raw = (json.dumps(report, sort_keys=True, ensure_ascii=False,
                              allow_nan=False) + "\n").encode("utf-8")
            if len(raw) > MAX_REPORT_BYTES:
                raise RepresentationInput("Relatório excedeu limite")
            temp = folder / (test_id + ".tmp")
            if target.exists() or target.is_symlink():
                raise RepresentationInput("ID de relatório já existe")
            try:
                with temp.open("xb") as file:
                    file.write(raw)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temp, target)
            finally:
                temp.unlink(missing_ok=True)
            state = {"id": test_id, "slug": person.slug, "status": "COMPLETED"}
        except (OSError, RuntimeError, ValueError, TypeError, KeyError,
                ArithmeticError) as exc:
            state = {"id": test_id, "slug": person.slug, "status": "FAILED",
                     "message": str(exc)[:280]}
        with self._lock:
            self._active = None
            self._last = state

    def current(self, slug: str) -> dict | None:
        _player(slug)
        with self._lock:
            if self._active and self._active["slug"] == slug:
                return dict(self._active)
            last = getattr(self, "_last", None)
            return dict(last) if last and last["slug"] == slug else None

    def shutdown(self) -> None:
        if self._thread is not None:
            # Do not free the Arena's process lock while UCI work continues.
            self._thread.join()
