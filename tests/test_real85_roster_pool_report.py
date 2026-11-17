"""Only synthetic, legal engineering fixtures; never historic-match evidence."""
from hashlib import sha256
import json
from urllib.error import HTTPError

import chess
import chess.pgn
import pytest

from chesssim import real85_neural_stream as stream
from chesssim import real85_online_residual as trainer
from chesssim.real85_roster_pool_report import census


def _two_historical_names_fixture(count=280):
    games = []
    for i in range(count):
        board = chess.Board()
        game = chess.pgn.Game()
        game.headers["White"] = "Bobby Fischer" if i % 2 == 0 else "Mikhail Tal"
        game.headers["Black"] = "Mikhail Tal" if i % 2 == 0 else "Bobby Fischer"
        game.headers["Date"] = f"{1960+i%35}.01.02"
        node = game
        for k in range(26):
            if board.is_game_over():
                break
            moves = sorted(board.legal_moves, key=lambda move: move.uci())
            move = moves[(i*7+k*11+i//7) % len(moves)]
            node = node.add_variation(move)
            board.push(move)
        games.append(str(game))
    return ("\n\n".join(games) + "\n").encode()


def test_full_real_game_pool_deduplicates_cross_player_and_excludes_test(monkeypatch,tmp_path):
    raw = _two_historical_names_fixture()
    def source(name, archive_dir):
        if name == "Karpov":
            raise HTTPError("https://example.org",404,"missing",None,None)
        assert name in ("Fischer","Tal")
        return raw, raw, "FIXTURE_PINNED_SOURCE"
    monkeypatch.setattr(stream,"_source_bytes",source)
    inputs = tmp_path/"input"
    report = stream.build(inputs,players=["fischer","tal","karpov"],
                          allow_missing=True)
    assert report["players"]["karpov"] == {"status":"SOURCE_MISSING","reason":"HTTP_404"}
    assert stream.verify(inputs)["status"]=="PARTIAL_REAL_INPUT_VERIFICATION"
    audited = census(inputs,require_full_roster=False)
    assert audited["roster_size"] == 3
    assert audited["source_present"] == 2
    assert audited["ready_for_training"] == 2
    assert audited["missing_or_insufficient"] == ["karpov"]
    assert audited["global_unique_training_game_trajectories"] == (
        audited["global_distinct_fit_games"]+audited["global_distinct_tune_games"])
    assert audited["games_occurring_in_multiple_player_sources"] == (
        audited["global_unique_training_game_trajectories"])
    assert audited["excluded_test_trajectories_exported"] == 0
    assert audited["holdout_used_for_training_or_tuning"] is False
    assert audited["players"]["fischer"]["source_pgn_sha256"] == sha256(raw).hexdigest()
    for slug in ("fischer","tal"):
        row=audited["players"][slug]
        assert row["fit_games"]+row["tune_games"]+row["excluded_test_games_not_serialized"] == (
            row["accepted_distinct_source_games"])
        assert sum(row["training_ply_phase_proxies"].values()) == (
            row["fit_focal_decisions"]+row["tune_focal_decisions"])
        assert row["unique_training_openings_first12"] > 1
    with pytest.raises(ValueError,match="entire roster"):
        census(inputs)
    # Regression: legitimate subset of verified 64-player pool MUST train
    # despite an unavailable third player's PGN source.
    trained = trainer.train(inputs,tmp_path/"partial-model",players=["fischer"],
                            max_fit_games=1,checkpoint_every=1)
    assert trained["status"]=="PARTIAL_NEURAL_FIT_RESUME_REQUIRED"
    assert trained["players"]["fischer"]["fit_games_processed"]==1
    with pytest.raises(ValueError,match="selected players"):
        trainer.train(inputs,tmp_path/"missing-model",players=["karpov"],
                      max_fit_games=1)


def test_roster_pool_rejects_tampering_even_with_updated_shard_hash(monkeypatch,tmp_path):
    raw=_two_historical_names_fixture()
    monkeypatch.setattr(stream,"_source_bytes",
                        lambda *a,**k:(raw,raw,"FIXTURE"))
    stream.build(tmp_path/"stream",players=["fischer"])
    path=tmp_path/"stream/fischer-fit.jsonl"
    entries=path.read_text().splitlines()
    original=json.loads(entries[0])
    original["uci_moves"][0]="e2e5"  # impossible illegal move
    entries[0]=json.dumps(original,sort_keys=True,separators=(",",":"))
    path.write_text("\n".join(entries)+"\n")
    manifest=tmp_path/"stream/manifest.json"
    data=json.loads(manifest.read_text())
    data["players"]["fischer"]["shards"]["fit"]["sha256"]=sha256(path.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError,match="identity or partition leak"):
        census(tmp_path/"stream",require_full_roster=False)
