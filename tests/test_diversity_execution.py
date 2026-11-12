"""#69: pre-registered A1/A2/B experiments, fail-closed and no real UCI required."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import chess
import chess.pgn
import pytest

from chesssim import diversity_execution as execution
from chesssim.diversity_experiment import export_plan
from chesssim.game_diversity import mainline_identity


def _plan(tmp_path: Path, *, external: bool = False) -> Path:
    path = tmp_path / "plan.json"
    export_plan(["fischer", "tal"], path, seed=2029, trials=2,
                declared_external_repertoire=external)
    return path


def _environment(tmp_path, monkeypatch):
    root = tmp_path / "rodent"
    root.mkdir()
    exe = root / "rodent-iv-plain.exe"
    exe.write_bytes(b"test-rodent-binary-not-an-engine")
    pdir = root / "personalities"
    pdir.mkdir()
    paths = {}
    for slug in ("fischer", "tal"):
        p = pdir / (slug + ".txt")
        p.write_text("registered profile for " + slug, encoding="utf-8")
        paths[slug] = p

    def validate(_root, white, black):
        assert _root == root
        assert {white.slug, black.slug} == {"fischer", "tal"}
        return exe, paths.copy()

    monkeypatch.setattr(execution, "validate", validate)
    calls = []
    state = {"corrupt": False, "fail_at": None, "modify_exe": False}

    def fake_duel(_root, output_dir, white, black, **options):
        assert _root == root
        assert options["reverse"] is True
        assert options["use_books"] is False
        assert options["movetime"] is None
        assert options["max_wall_seconds"] == 200.0
        assert options["depth"] == 7
        calls.append({"output": output_dir, "seed": options["opening_seed"],
                      "books": options["historical_openings"]})
        if state["fail_at"] == len(calls):
            raise RuntimeError("injected UCI termination")
        folder = output_dir / "fischer-vs-tal" / ("attempt-" + str(len(calls)))
        folder.mkdir(parents=True, exist_ok=False)
        receipts = []
        pgndata = []
        for index, (white_player, black_player) in enumerate(((white, black), (black, white)), 1):
            game = chess.pgn.Game()
            game.headers["White"] = white_player.name
            game.headers["Black"] = black_player.name
            game.headers["Result"] = "1-0"
            node = game
            for move in ("e2e4", "e7e5", "d1h5", "b8c6", "f1c4", "g8f6", "h5f7"):
                node = node.add_variation(chess.Move.from_uci(move))
            digest, plies = mainline_identity(game)
            if state["corrupt"] and index == 2:
                digest = "a" * 64
            receipts.append({
                "round": index, "white": white_player.slug,
                "black": black_player.slug, "result": "1-0",
                "status": "COMPLETED", "mainline_sha256": digest,
                "plies": plies, "opening_moves": {"white": 0, "black": 0},
                "opening_seed": options["opening_seed"] if options["historical_openings"] else None,
                "opening_trace": [],
                "opening_mode": ("historical-v09-exploratory"
                                 if options["historical_openings"] else "disabled"),
            })
            text = str(game) + "\n\n"
            (folder / f"jogo_{index}.pgn").write_text(text, encoding="utf-8")
            pgndata.append(str(game))
        (folder / "duelo.pgn").write_text("\n\n".join(pgndata) + "\n\n", encoding="utf-8")
        hashes = {"exe": execution.file_hash(exe)}
        hashes.update({slug: execution.file_hash(path) for slug, path in paths.items()})
        summary = {
            "status": "COMPLETED", "players": [white.slug, black.slug],
            "file_hashes": hashes,
            "opening_seed": options["opening_seed"] if options["historical_openings"] else None,
            "games": receipts,
        }
        (folder / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        if state["modify_exe"]:
            exe.write_bytes(b"changed after the first arm")
        return folder

    monkeypatch.setattr(execution, "play_duel", fake_duel)
    return root, calls, state


def _run(root, plan, out, **kwargs):
    return execution.execute_plan(root, plan, out,
                                  blocks=kwargs.pop("blocks", 1),
                                  depth=7, max_plies=120,
                                  max_game_wall_seconds=200.0, **kwargs)


def test_three_controlled_arms_keep_a1_a2_identical_seed_and_b_alternate(
    tmp_path, monkeypatch
):
    root, calls, _ = _environment(tmp_path, monkeypatch)
    plan = _plan(tmp_path)
    report = _run(root, plan, tmp_path / "experiment")
    assert report["status"] == execution.STATUS_SUBSET
    assert report["blocks_total_in_plan"] == 2
    assert report["blocks_requested"] == 1
    assert report["arms_planned"] == 3
    assert report["arms_completed"] == 3
    assert [c["seed"] for c in calls] == [2029, 2029, 2031]
    assert all(c["books"] is None for c in calls)
    assert [a["condition"] for a in report["arms"]] == list(execution.CONDITIONS)
    assert len({c["output"] for c in calls}) == 3
    assert all(len(a["receipt"]["games"]) == 2 for a in report["arms"])
    assert all(g["pgn_sha256"] for a in report["arms"] for g in a["receipt"]["games"])
    assert report["seed_effectiveness"] == "NO_EFFECT_EXPECTED_WITHOUT_EXTERNAL_REPERTOIRE"
    assert json.loads((tmp_path / "experiment" / "execution.json").read_text()) == report
    assert sha256(plan.read_bytes()).hexdigest() == report["plan_file_sha256"]
    assert plan.read_bytes() == (tmp_path / "plan.json").read_bytes()
    with pytest.raises(FileExistsError):
        _run(root, plan, tmp_path / "experiment")
    assert len(calls) == 3, "no implicit retries of a completed experiment"


def test_first_two_blocks_preserve_plan_order_and_same_game_number_policy(
    tmp_path, monkeypatch
):
    root, calls, _ = _environment(tmp_path, monkeypatch)
    report = _run(root, _plan(tmp_path), tmp_path / "trial", blocks=2)
    assert len(report["arms"]) == 6
    assert report["status"] == execution.STATUS_COMPLETED
    assert [c["seed"] for c in calls] == [2029, 2029, 2031, 2030, 2030, 2032]
    assert [a["block"] for a in report["arms"]] == [1, 1, 1, 2, 2, 2]
    assert all(a["game_number_policy"] == "1,2 per arm" for a in report["arms"])


def test_engine_failure_keeps_prior_receipt_and_fails_without_replaying(
    tmp_path, monkeypatch
):
    root, calls, state = _environment(tmp_path, monkeypatch)
    state["fail_at"] = 2
    plan = _plan(tmp_path)
    out = tmp_path / "trial"
    with pytest.raises(RuntimeError, match="injected UCI termination"):
        _run(root, plan, out)
    saved = json.loads((out / "execution.json").read_text())
    assert saved["status"] == execution.STATUS_FAILED
    assert saved["arms_completed"] == 1
    assert saved["arms"][0]["condition"] == "A1_RESEARCH_CONTROL"
    assert len(calls) == 2
    with pytest.raises(FileExistsError):
        _run(root, plan, out)
    assert len(calls) == 2
    assert any((out / "arms").rglob("jogo_1.pgn"))


def test_corrupt_second_game_receipt_is_not_counted_as_complete(
    tmp_path, monkeypatch
):
    root, calls, state = _environment(tmp_path, monkeypatch)
    state["corrupt"] = True
    out = tmp_path / "trial"
    with pytest.raises(ValueError, match="Recibo diverge"):
        _run(root, _plan(tmp_path), out)
    saved = json.loads((out / "execution.json").read_text())
    assert saved["arms_completed"] == 0
    assert saved["status"] == execution.STATUS_FAILED
    assert len(calls) == 1


def test_mutating_engine_between_arms_stops_before_next_uci(
    tmp_path, monkeypatch
):
    root, calls, state = _environment(tmp_path, monkeypatch)
    state["modify_exe"] = True
    out = tmp_path / "trial"
    with pytest.raises(RuntimeError, match="Hash do motor"):
        _run(root, _plan(tmp_path), out)
    saved = json.loads((out / "execution.json").read_text())
    assert saved["arms_completed"] == 1
    assert saved["status"] == execution.STATUS_FAILED
    assert len(calls) == 1


@pytest.mark.parametrize("mutation", [
    lambda d: d["blocks"][0]["runs"][1].update(seed=1000),
    lambda d: d.update(design_sha256="a" * 64),
    lambda d: d["blocks"][0]["fixed_variables"].remove("game_number"),
    lambda d: d.update(status="CALIBRATED"),
    lambda d: d.update(seed_effectiveness="UNKNOWN"),
])
def test_tampered_plan_is_rejected_before_output_or_engine(
    tmp_path, monkeypatch, mutation
):
    root, calls, _ = _environment(tmp_path, monkeypatch)
    plan = _plan(tmp_path)
    data = json.loads(plan.read_text(encoding="utf-8"))
    mutation(data)
    plan.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        _run(root, plan, tmp_path / "trial")
    assert not (tmp_path / "trial").exists()
    assert not calls


def test_duplicate_json_keys_and_symlink_plan_fail_closed(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"players":[],"players":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicadas"):
        execution.load_registered_plan(path)
    alias = tmp_path / "plan-alias.json"
    original = _plan(tmp_path)
    alias.symlink_to(original)
    with pytest.raises(ValueError, match="simbólico"):
        execution.load_registered_plan(alias)


def test_missing_external_books_and_surprise_books_are_not_allowed(
    tmp_path, monkeypatch
):
    root, calls, _ = _environment(tmp_path, monkeypatch)
    actual = tmp_path / "book.json"
    actual.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="declaração|Declaração"):
        _run(root, _plan(tmp_path), tmp_path / "trial1", white_book=actual)
    assert not (tmp_path / "trial1").exists()
    plan = tmp_path / "plan.json"
    plan.unlink()
    plan = _plan(tmp_path, external=True)
    with pytest.raises(ValueError, match="declaração|Declaração"):
        _run(root, plan, tmp_path / "trial2")
    assert not (tmp_path / "trial2").exists()
    assert not calls


def test_realistic_external_book_is_loaded_only_when_declared(
    tmp_path, monkeypatch
):
    root, calls, _ = _environment(tmp_path, monkeypatch)
    path = _plan(tmp_path, external=True)
    bookfile = tmp_path / "repertoire.json"
    bookfile.write_text('{"test":"book"}', encoding="utf-8")
    expected_book = {
        "schema": "chesssim-opening-v9", "player": "fischer",
        "policy": {"max_plies": 16}, "positions": {"white": {}, "black": {}},
        "source": {
            "pgn_sha256": "a" * 64,
            "provenance": {"status": "PUBLIC_PGN_UNPARTITIONED_EXPLORATORY"},
        },
    }
    def load(source, slug):
        assert source == bookfile and slug == "fischer"
        return expected_book
    monkeypatch.setattr(execution, "load_opening_book", load)
    report = _run(root, path, tmp_path / "trial", white_book=bookfile)
    assert len(calls) == 3
    assert all("fischer" in x["books"] for x in calls)
    assert report["seed_effectiveness"] == (
        "OPERATOR_DECLARED_EXTERNAL_REPERTOIRE_UNVERIFIED")
    assert report["inputs"]["external_books"]["fischer"]["file_sha256"] == (
        execution.file_hash(bookfile))


@pytest.mark.parametrize("options", [
    {"blocks": 3},
    {"depth": True},
    {"depth": 31},
    {"max_plies": 1},
    {"max_game_wall_seconds": float("nan")},
    {"max_game_wall_seconds": 0},
])
def test_bounded_experiment_rejects_invalid_parameters_before_uci(
    tmp_path, monkeypatch, options
):
    root, calls, _ = _environment(tmp_path, monkeypatch)
    plan = _plan(tmp_path)
    out = tmp_path / "trial"
    params = {"blocks": 1, "depth": 7, "max_plies": 120,
              "max_game_wall_seconds": 200.0}
    params.update(options)
    with pytest.raises(ValueError):
        execution.execute_plan(root, plan, out, **params)
    assert not out.exists()
    assert not calls


def test_caller_destination_symlink_is_rejected(tmp_path, monkeypatch):
    root, calls, _ = _environment(tmp_path, monkeypatch)
    plan = _plan(tmp_path)
    external = tmp_path / "real"
    external.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        _run(root, plan, alias / "trial")
    assert not (external / "trial").exists()
    assert not calls


def test_corrupt_consolidated_duel_is_not_counted(tmp_path, monkeypatch):
    root, calls, _ = _environment(tmp_path, monkeypatch)
    original = execution.play_duel

    def corrupt_duo(*args, **kwargs):
        folder = original(*args, **kwargs)
        (folder / "duelo.pgn").write_text("not a tournament PGN", encoding="utf-8")
        return folder

    monkeypatch.setattr(execution, "play_duel", corrupt_duo)
    out = tmp_path / "trial"
    with pytest.raises(ValueError, match="PGN consolidado"):
        _run(root, _plan(tmp_path), out)
    report = json.loads((out / "execution.json").read_text())
    assert report["arms_completed"] == 0
    assert report["status"] == execution.STATUS_FAILED
    assert len(calls) == 1


def test_wrong_per_game_opening_seed_is_never_counted(tmp_path, monkeypatch):
    root, calls, _ = _environment(tmp_path, monkeypatch)
    original = execution.play_duel

    def forged_record(*args, **kwargs):
        folder = original(*args, **kwargs)
        p = folder / "summary.json"
        receipt = json.loads(p.read_text())
        receipt["games"][1]["opening_seed"] = 9
        p.write_text(json.dumps(receipt))
        return folder

    monkeypatch.setattr(execution, "play_duel", forged_record)
    out = tmp_path / "trial"
    with pytest.raises(ValueError, match="seed e política"):
        _run(root, _plan(tmp_path), out)
    assert json.loads((out / "execution.json").read_text())["arms_completed"] == 0
    assert len(calls) == 1


def test_registered_plan_does_not_treat_partial_execution_as_all_trials(
    tmp_path, monkeypatch
):
    root, _, _ = _environment(tmp_path, monkeypatch)
    p = _plan(tmp_path)
    report = _run(root, p, tmp_path / "trial", blocks=1)
    assert report["status"] == execution.STATUS_SUBSET
    assert report["blocks_requested"] == 1
    assert report["blocks_total_in_plan"] == 2
    assert report["arms_planned"] == 3
