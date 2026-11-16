"""Adversarial tests for actual CPU gradients over real full-game FIT shards."""
import json

import chess
import chess.pgn
import pytest

from chesssim import real85_neural_stream as stream
from chesssim import real85_online_residual as net


def _games(count=280):
    games = []
    for i in range(count):
        board = chess.Board()
        game = chess.pgn.Game()
        game.headers["White"] = "Bobby Fischer" if i % 2 == 0 else "Demo Opponent"
        game.headers["Black"] = "Demo Opponent" if i % 2 == 0 else "Bobby Fischer"
        game.headers["Date"] = f"{1960+i%35}.01.02"
        node = game
        for ply in range(34):
            if board.is_game_over():
                break
            legal = sorted(board.legal_moves, key=lambda x: x.uci())
            move = legal[(i*7+ply*11+i//7) % len(legal)]
            node = node.add_variation(move)
            board.push(move)
        games.append(str(game))
    return ("\n\n".join(games)+"\n").encode()


def _input(tmp_path, monkeypatch):
    raw = _games()
    monkeypatch.setattr(stream, "_source_bytes",
                        lambda *args, **kwargs: (raw, raw, "TEST_REAL_PGN_FIXTURE"))
    root = tmp_path / "inputs"
    m = stream.build(root, players=["fischer"])
    assert m["players"]["fischer"]["status"] == "READY_ALL_REAL_MOVES_FOR_NEURAL_TRAINING"
    assert stream.verify(root)["status"] == "VERIFIED_ALL_REAL_NEURAL_INPUT_TRAJECTORIES"
    return root, m


def test_actual_gradient_learning_and_bitwise_resume(monkeypatch, tmp_path):
    root,m = _input(tmp_path, monkeypatch)
    partial = net.train(root, tmp_path/"part", players=["fischer"],
                        max_fit_games=9, checkpoint_every=3)
    p = partial["players"]["fischer"]
    assert partial["status"] == "PARTIAL_NEURAL_FIT_RESUME_REQUIRED"
    assert p["fit_games_processed"] == 9
    assert p["gradient_steps"] > 0
    assert p["tune"] is None
    a = json.loads((tmp_path/"part/fischer.checkpoint.json").read_text())
    assert a["completed_games"]==9
    assert a["payload_sha256"] == net._fingerprint({
        k:v for k,v in a.items() if k!="payload_sha256"})
    assert a["model"]["bias"] != {}
    assert a["gradient_steps"] > 0
    # An interruption after a partial checkpoint must be restartable.
    (tmp_path/"part/.fischer.checkpoint.json.tmp").write_text("partial write")
    full_resume = net.train(root, tmp_path/"part",players=["fischer"],
                            resume=True, checkpoint_every=17)
    full_clean = net.train(root,tmp_path/"clean",players=["fischer"],
                           checkpoint_every=21)
    assert full_resume["status"] == full_clean["status"] == "ALL_REQUESTED_PLAYER_RESIDUALS_FITTED"
    resumed = json.loads((tmp_path/"part/fischer.checkpoint.json").read_text())
    uninterrupted = json.loads((tmp_path/"clean/fischer.checkpoint.json").read_text())
    assert resumed == uninterrupted
    assert resumed["completed_games"] == m["players"]["fischer"]["fit_games"]
    assert resumed["focal_decisions"] == m["players"]["fischer"]["fit_focal_decisions_all_phases"]
    assert resumed["gradient_steps"] + resumed["forced_without_gradient"] == resumed["focal_decisions"]
    assert full_resume["players"]["fischer"]["tune"]["comparisons"] > 0
    assert full_resume["players"]["fischer"]["tune"]["fit_weights_mutated_by_tune"] is False
    assert full_resume["maia_dsc_backbone_finetuned"] is False
    assert full_resume["neural_weights_actually_updated"] is True
    assert full_resume["uci_playable_and_tactical_safe"] is False


def test_fail_closed_mismatched_checkpoint_and_partial_cap(monkeypatch,tmp_path):
    root, _ = _input(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match="max_fit_games"):
        net.train(root,tmp_path/"no",players=["fischer"],max_fit_games=True)
    with pytest.raises(ValueError,match="checkpoint_every"):
        net.train(root,tmp_path/"bad",players=["fischer"],checkpoint_every=0)
    with pytest.raises(ValueError,match="Resume needs"):
        net.train(root,tmp_path/"missing",players=["fischer"],resume=True)
    net.train(root,tmp_path/"partial",players=["fischer"],max_fit_games=4)
    checkpoint=tmp_path/"partial/fischer.checkpoint.json"
    state=json.loads(checkpoint.read_text())
    state["input_manifest_sha256"]="0"*64
    checkpoint.write_text(json.dumps(state))
    with pytest.raises(ValueError,match="checkpoint integrity"):
        net.train(root,tmp_path/"partial",players=["fischer"],resume=True)
    state["input_manifest_sha256"]=net.file_hash(root/"manifest.json")
    state["optimizer"]["learning_rate"]=0.5
    state["payload_sha256"]=net._fingerprint({
        k:v for k,v in state.items() if k!="payload_sha256"})
    checkpoint.write_text(json.dumps(state))
    with pytest.raises(ValueError,match="optimizer or model metadata"):
        net.train(root,tmp_path/"partial",players=["fischer"],resume=True)
    state["optimizer"]["learning_rate"]=net.LR
    state["gradient_steps"]+=1
    state["payload_sha256"]=net._fingerprint({
        k:v for k,v in state.items() if k!="payload_sha256"})
    checkpoint.write_text(json.dumps(state))
    with pytest.raises(ValueError,match="training counts"):
        net.train(root,tmp_path/"partial",players=["fischer"],resume=True)
    assert not (tmp_path/"no").exists() and not (tmp_path/"bad").exists()


def test_tune_never_updates_parameters_or_creates_features():
    model=net._initial()
    board=chess.Board()
    white=chess.Move.from_uci("e2e4")
    black=chess.Move.from_uci("d2d4")
    before=json.dumps(model,sort_keys=True)
    loss,correct=net._pair(model,board,white,black,update=False)
    assert loss > 0 and type(correct) is bool
    assert json.dumps(model,sort_keys=True) == before
    net._pair(model,board,white,black,update=True)
    assert model["bias"] != {}
    assert json.dumps(model,sort_keys=True) != before
    model_after_fit=json.dumps(model,sort_keys=True)
    net._pair(model,board,white,black,update=False)
    assert json.dumps(model,sort_keys=True) == model_after_fit


def test_full_game_legal_promotions_castling_and_endgame_input():
    b=chess.Board()
    king_castle=chess.Move.from_uci("e1g1")
    assert king_castle not in b.legal_moves
    end=chess.Board("4k3/6P1/8/8/8/8/8/4K3 w - - 0 1")
    promote=chess.Move.from_uci("g7g8q")
    assert promote in end.legal_moves
    assert "phase:endgame" in net._context(end)
    assert "promotion:5" in net._action(end,promote)
    assert "piece:1" in net._action(end,promote)
    with pytest.raises(ValueError,match="Illegal source"):
        net._action(b,king_castle)


def test_no_forged_test_arm_in_train():
    m=net._initial()
    board=chess.Board()
    entry={"schema":stream.GAME_SCHEMA,"arm":"test","game_sha256":"a"*64,
           "initial_fen":board.fen(),"focal_color":"white",
           "uci_moves":["e2e4"],"focal_decisions":1}
    with pytest.raises(ValueError,match="Invalid stream arm"):
        net._game(m,entry,update=True)
