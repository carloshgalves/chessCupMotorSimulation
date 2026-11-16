"""Adversarial synthetic TEST-ONLY fixtures for the real 64-game UCI crossover.

These tiny artificial games are *only* unit-test input: never training,
historical evidence, model weight selection or apparent benchmark outcomes.
The workflow runs actual pinned Rodent IV and Stockfish separately.
"""
from hashlib import sha256
import json

import chess
import chess.pgn
import pytest

from chesssim import real85_seeded_crossover as cross
from chesssim.real85_neural_guarded_uci import SCHEMA as REAL_UCI_SCHEMA


def _seed(tmp_path, seed: int):
    root = tmp_path / f"seed-{seed}"
    root.mkdir()
    games, pgns = [], []
    actors = ["fischer","tal","karpov","kasparov"]
    for slug in actors:
        for arm in ("shadow","guided"):
            for color in ("white","black"):
                board = chess.Board()
                game = chess.pgn.Game()
                game.headers.update({
                    "Result":"0-1","Termination":"CHECKMATE",
                    "NeuralMode":arm,"EngineSHA256":"a"*64,
                    "IndependentStockfishSHA256":"b"*64,
                    "OpeningSeed":str(seed),
                })
                node=game
                trace=[]
                for ply,text in enumerate(("f2f3","e7e5","g2g4","d8h4"),1):
                    move=chess.Move.from_uci(text)
                    assert move in board.legal_moves
                    side = slug if board.turn == (color=="white") else "control-simple"
                    trace.append({
                        "ply":ply,"player":side,
                        "fen_sha256":sha256(board.fen().encode()).hexdigest(),
                        "rodent_move":text,"played_move":text,
                        "outcome":("WARMUP_RODENT_UNMODIFIED"
                                   if side==slug else "CONTROL_RODENT_UNMODIFIED"),
                        "sampled":False,
                    })
                    node=node.add_variation(move)
                    board.push(move)
                assert board.outcome(claim_draw=True).result()=="0-1"
                pgns.append(str(game))
                games.append({
                    "player":slug,"arm":arm,"color":color,"opening_seed":seed,
                    "status":"COMPLETED","result":"0-1",
                    "termination":"CHECKMATE","plies":4,
                    "policy_trace":trace,"opening_trace":[],
                    "focal_historical_book_plies":0,"simple_book_plies":0,
                    "actual_overrides":0,
                    "deep_confirmed_candidate_pairs":0,"deep_rejected":0,
                })
    data = {
        "schema":REAL_UCI_SCHEMA,
        "status":"ALL_REAL_UCI_GAMES_COMPLETE",
        "opening_seed":seed,
        "players":actors,"games":games,
        "rodent_original_sha256":"a"*64,
        "stockfish_sha256":"b"*64,
        "rodent_depth":4,"stockfish_depth":8,
        "confirmation_depth":12,"cp_budget":25,"min_ply":41,
        "deep_guard_enabled":True,
        "historical_focal_book_enabled":True,
        "historical_book_training":"real85_game_disjoint_85pct_train",
        "official_personality_files_changed":False,
        "rated_official_games":False,
        "historical_elo_estimated":False,
        "maia_dsc_weights_changed":False,
    }
    (root/"results.json").write_text(json.dumps(data),encoding="utf-8")
    (root/"games.pgn").write_text("\n\n".join(pgns)+"\n",encoding="utf-8")
    return root


def test_seeded_crossover_exact_game_replay_and_paired_accounting(tmp_path):
    for seed in (2033,2041):
        _seed(tmp_path, seed)
    report = cross.summarize(tmp_path,(2033,2041))
    assert report["status"]=="EXPLORATORY_ACTUAL_UCI_MULTISEED_CROSSOVER_NOT_HUMAN_ELO"
    assert report["total_complete_uci_games"]==32
    assert report["total_paired_seed_color_blocks"]==16
    assert report["total_guided_actual_overrides"]==0
    for slug,row in report["players"].items():
        assert row["games_per_arm"]==4
        assert row["complete_games"]==8
        assert row["guided_points"]==row["shadow_points"]==2
        assert row["paired_mean_difference_per_game"]==0
        assert row["unique_guided_game_trajectories"]==1
        assert row["unique_guided_first12_uci_openings"]==1
        assert row["confidence_interval"] is None
        assert row["strength_inference"]=="INSUFFICIENT_INDEPENDENT_SEED_CLUSTERS_FOR_ELO_OR_CI"
        assert len(row["seed_clusters"])==2
        assert len(row["games"])==4
    assert (tmp_path/"crossover-report.json").is_file()
    with pytest.raises(FileExistsError,match="already exists"):
        cross.summarize(tmp_path,(2033,2041))


def test_crossover_fails_closed_on_missing_colormirror_and_incomplete_results(tmp_path):
    for seed in (2033,2041):
        _seed(tmp_path,seed)
    docpath=tmp_path/"seed-2041/results.json"
    doc=json.loads(docpath.read_text())
    doc["games"][0]["color"]="black"
    docpath.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match="coverage"):
        cross.summarize(tmp_path,(2033,2041))
    doc["games"][0]["color"]="white"
    doc["games"][0]["status"]="INCOMPLETE"
    docpath.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match="Wrong/unfinished|missing or incomplete"):
        cross.summarize(tmp_path,(2033,2041))
    assert not (tmp_path/"crossover-report.json").exists()


def test_crossover_rejects_tampered_pgn_seed_model_binary_and_trace(tmp_path):
    for seed in (2033,2041):
        _seed(tmp_path,seed)
    file=tmp_path/"seed-2041/results.json"
    data=json.loads(file.read_text())
    data["stockfish_sha256"]="0"*64
    file.write_text(json.dumps(data))
    with pytest.raises(ValueError,match="Mixed Rodent|PGN provenance"):
        cross.summarize(tmp_path,(2033,2041))
    _seed_path=tmp_path/"seed-2041"
    file.write_text(json.dumps({**data,"stockfish_sha256":"b"*64}))
    data=json.loads(file.read_text())
    data["games"][0]["opening_seed"]=2033
    file.write_text(json.dumps(data))
    with pytest.raises(ValueError,match="Detached per-game opening seed"):
        cross.summarize(tmp_path,(2033,2041))
    data["games"][0]["opening_seed"]=2041
    data["games"][0]["actual_overrides"]=1
    file.write_text(json.dumps(data))
    with pytest.raises(ValueError,match="count does not reconcile"):
        cross.summarize(tmp_path,(2033,2041))
    assert _seed_path.is_dir()


def test_seed_list_contract_and_no_explicit_fabrication(tmp_path):
    for invalid in ((2033,), (2033,2033), (-1,2), (0,True),
                    ([2033],2041),tuple(range(9))):
        with pytest.raises(ValueError,match="seed"):
            cross._validated_seeds(invalid)
    assert cross._validated_seeds((2033,2041,2053,2063))==[2033,2041,2053,2063]
    with pytest.raises(ValueError,match="new output"):
        cross.run(tmp_path,tmp_path,tmp_path,tmp_path,tmp_path,seeds=(2033,2041))
