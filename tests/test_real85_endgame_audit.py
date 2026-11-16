"""Adversarial contracts: real TEST only, source locks, legal historical FENs."""
from hashlib import sha256
import json
from types import SimpleNamespace

import chess
import chess.engine
import chess.pgn
import pytest

from chesssim import real85_endgame_audit as endgames
from chesssim.game_diversity import mainline_identity
from chesssim.real85_playable import SCHEMA as REAL85_SCHEMA
from chesssim.catalog import find_player


def _example(focal_move: str) -> chess.pgn.Game:
    game = chess.pgn.Game()
    game.headers["White"] = "Bobby Fischer"
    game.headers["Black"] = "Demo Opponent"
    game.headers["Date"] = "1970.01.01"
    board = chess.Board("8/8/4k3/8/8/4K3/3P4/8 w - - 0 1")
    game.setup(board)
    move = chess.Move.from_uci(focal_move)
    assert move in board.legal_moves
    game.add_variation(move)
    return game


def _fixture(tmp_path, monkeypatch):
    train = _example("e3d3")
    heldout = _example("e3f3")
    td, _ = mainline_identity(train)
    hd, _ = mainline_identity(heldout)
    assert td != hd
    pgn = (str(train) + "\n\n" + str(heldout) + "\n").encode()
    monkeypatch.setattr(endgames, "_source_bytes",
                        lambda *a, **k: (pgn, pgn, "FIXTURE_PGN"))
    monkeypatch.setattr(endgames, "game_arm",
                        lambda digest: "test" if digest == hd else "train")
    folder = tmp_path / "trained"
    folder.mkdir()
    manifest = {
        "schema": REAL85_SCHEMA,
        "players": {"fischer": {
            "status": "REAL_GAMES_TRAINED_PLAYABLE_REPERTOIRE",
            "source_pgn_sha256": sha256(pgn).hexdigest(),
            "accepted_distinct_games": 2, "train_games": 1, "test_games": 1,
        }},
    }
    (folder / "report.json").write_text(json.dumps(manifest))
    return folder, manifest, hd


def test_extract_never_uses_train_and_picks_real_legal_endgame(monkeypatch, tmp_path):
    folder, manifest, hd = _fixture(tmp_path, monkeypatch)
    doc = endgames.extract(folder, tmp_path/"test-endgames.json", players=["fischer"])
    row = doc["players"]["fischer"]
    assert row["test_games"] == 1
    assert row["sampled_distinct_games"] == 1
    assert row["test_endgame_decisions_available"] == 1
    sample = row["sampled_decisions"][0]
    assert sample["game_sha256"] == hd
    assert sample["historical_move_uci"] == "e3f3"
    assert sample["fen_sha256"] == sha256(sample["fen"].encode()).hexdigest()
    assert doc["trained_on_test"] is False
    with pytest.raises(FileExistsError):
        endgames.extract(folder, tmp_path/"test-endgames.json", players=["fischer"])
    manifest["players"]["fischer"]["source_pgn_sha256"] = "0"*64
    (folder/"report.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="source changed"):
        endgames.extract(folder, tmp_path/"wrong-source.json", players=["fischer"])
    assert not (tmp_path/"wrong-source.json").exists()


class FakeRodent:
    def play(self, board, *args, **kwargs):
        return SimpleNamespace(move=next(iter(board.legal_moves)))

    def quit(self):
        pass

    def close(self):
        pass


class FakeStockfish:
    id = {"name": "fake TEST stockfish"}

    def __init__(self):
        self.calls = []

    def analyse(self, board, limit, root_moves=None):
        # A unit-test contract, NEVER scientific chess-quality evidence.
        self.calls.append((board.fen(), limit.depth,
                           None if root_moves is None else tuple(root_moves)))
        return {"score": chess.engine.PovScore(
            chess.engine.Cp(30 if board.turn == chess.WHITE else -30),
            board.turn)}

    def quit(self):
        pass

    def close(self):
        pass


def test_audit_provenance_legality_and_true_game_count(monkeypatch, tmp_path):
    folder, _, _ = _fixture(tmp_path, monkeypatch)
    sample = tmp_path/"test-endgames.json"
    endgames.extract(folder, sample, players=["fischer"])
    root = tmp_path/"rodent"
    (root/"personalities").mkdir(parents=True)
    player = find_player("fischer")
    (root/"personalities"/player.profile_filename).write_text("fake profile")
    (root/"personalities/basic.ini").write_text(
        f"{player.rodent_alias}={player.profile_filename}\n")
    (root/"rodent-iv-plain.exe").write_text("mock")
    stockfish = tmp_path/"stockfish"
    stockfish.write_text("mock")
    monkeypatch.setattr(endgames, "_open_engine",
                        lambda *args, **kwargs: FakeRodent())
    fake_stockfish = FakeStockfish()
    monkeypatch.setattr(endgames.chess.engine.SimpleEngine, "popen_uci",
                        lambda *args, **kwargs: fake_stockfish)
    result = endgames.audit(sample, root, stockfish, tmp_path/"quality.json", profiles=folder)
    assert len(fake_stockfish.calls) == 3
    assert all(fen == fake_stockfish.calls[0][0] and depth == 8
               for fen, depth, _ in fake_stockfish.calls)
    assert fake_stockfish.calls[0][2] is None
    assert all(len(root_moves) == 1
               for _, _, root_moves in fake_stockfish.calls[1:])
    assert result["comparison"] == (
        "same_root_fen_and_depth_with_forced_historical_and_rodent_root_moves")
    assert result["schema"] == endgames.AUDIT_SCHEMA
    assert result["training_or_parameter_selection_performed"] is False
    row = result["players"]["fischer"]
    assert row["sampled_distinct_games"] == 1
    assert row["observations"][0]["historical_uci"] == "e3f3"
    assert row["observations"][0]["rodent_uci"] in [
        m.uci() for m in chess.Board(
            json.loads(sample.read_text())["players"]["fischer"]
            ["sampled_decisions"][0]["fen"]).legal_moves]
    assert row["nonmate_mean_rodent_cp_loss"] >= 0
    with pytest.raises(FileExistsError):
        endgames.audit(sample, root, stockfish, tmp_path/"quality.json", profiles=folder)
    corrupt = json.loads(sample.read_text())
    corrupt["players"]["fischer"]["sampled_decisions"][0]["fen_sha256"] = "0"*64
    (tmp_path/"corrupted.json").write_text(json.dumps(corrupt))
    with pytest.raises(ValueError, match="Corrupted historical"):
        endgames.audit(tmp_path/"corrupted.json", root, stockfish,
                       tmp_path/"no-quality.json", profiles=folder)
    assert not (tmp_path/"no-quality.json").exists()
    bad_arm=json.loads(sample.read_text())
    train_mainline,_ = mainline_identity(_example("e3d3"))
    bad_arm["players"]["fischer"]["sampled_decisions"][0]["game_sha256"] = train_mainline
    (tmp_path/"injected-fit.json").write_text(json.dumps(bad_arm))
    with pytest.raises(ValueError, match="FIT historical game"):
        endgames.audit(tmp_path/"injected-fit.json", root, stockfish,
                       tmp_path/"invalid-fit-quality.json", profiles=folder)
    assert not (tmp_path/"invalid-fit-quality.json").exists()
    tampered=json.loads(sample.read_text())
    tampered["source_manifest_sha256"]="0"*64
    (tmp_path/"invalid-provenance.json").write_text(json.dumps(tampered))
    with pytest.raises(ValueError, match="FIT manifest"):
        endgames.audit(tmp_path/"invalid-provenance.json", root, stockfish,
                       tmp_path/"bad-provenance-quality.json", profiles=folder)


def test_invalid_depth_and_non_test_manifest_fail_closed(tmp_path):
    a = tmp_path/"input.json"
    a.write_text(json.dumps({"schema":endgames.EXTRACT_SCHEMA,
                             "trained_on_test":True,"players":{"fischer":{}}}))
    with pytest.raises(ValueError, match="TEST-only"):
        endgames.audit(a, tmp_path, tmp_path, tmp_path/"result.json", profiles=tmp_path)
    with pytest.raises(ValueError, match="depth budget"):
        endgames.audit(a, tmp_path, tmp_path, tmp_path/"bad.json",
                       profiles=tmp_path, rodent_depth=0)


def test_full_real_test_endgame_game_population_no_artificial_cap(monkeypatch, tmp_path):
    examples = [_example("e3d3"), _example("e3f3"), _example("e3e4")]
    ids = [mainline_identity(g)[0] for g in examples]
    assert len(set(ids)) == 3
    pgn = ("\n\n".join(map(str, examples)) + "\n").encode()
    monkeypatch.setattr(endgames, "_source_bytes",
                        lambda *a, **k: (pgn,pgn,"FIXTURE_THREE_GAMES"))
    monkeypatch.setattr(endgames, "game_arm",
                        lambda digest: "train" if digest == ids[0] else "test")
    folder=tmp_path/"fit"
    folder.mkdir()
    (folder/"report.json").write_text(json.dumps({
        "schema":REAL85_SCHEMA,
        "players":{"fischer":{
            "status":"REAL_GAMES_TRAINED_PLAYABLE_REPERTOIRE",
            "source_pgn_sha256":sha256(pgn).hexdigest(),
            "accepted_distinct_games":3,"train_games":1,"test_games":2
        }}}))
    all_games=endgames.extract(folder,tmp_path/"all.json",players=["fischer"])
    player=all_games["players"]["fischer"]
    assert all_games["all_available_test_endgame_games_selected"] is True
    assert player["sampled_distinct_games"]==player["test_endgame_games_available"]==2
    assert {x["game_sha256"] for x in player["sampled_decisions"]}==set(ids[1:])
    limited=endgames.extract(folder,tmp_path/"limited.json",
                             players=["fischer"],per_player=1)
    assert limited["players"]["fischer"]["sampled_distinct_games"]==1
    for wrong in (-1, True, 10001):
        with pytest.raises(ValueError,match="per_player"):
            endgames.extract(folder,tmp_path/"invalid.json",
                             players=["fischer"],per_player=wrong)
