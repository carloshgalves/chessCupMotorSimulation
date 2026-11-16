"""Adversarial contracts for complete PGN trajectories, no neural claims."""
from hashlib import sha256
import json

import chess
import chess.pgn
import pytest

from chesssim import real85_neural_stream as neural


def _real_legal_pgn_fixture(count=280):
    games = []
    for i in range(count):
        board = chess.Board()
        game = chess.pgn.Game()
        game.headers["White"] = "Bobby Fischer" if i%2==0 else "Demo Opponent"
        game.headers["Black"] = "Demo Opponent" if i%2==0 else "Bobby Fischer"
        game.headers["Date"] = f"{1960+i%35}.01.02"
        game.headers["Result"] = "*"
        node = game
        for k in range(26):
            if board.is_game_over():
                break
            legal = sorted(board.legal_moves, key=lambda m: m.uci())
            move = legal[(i*7+k*11 + i//7) % len(legal)]
            node = node.add_variation(move)
            board.push(move)
        games.append(str(game))
    return ("\n\n".join(games)+"\n").encode()


def test_neural_subsplit_is_global_and_never_serializes_test():
    for digest in ("a"*64, "b"*64, "c"*64, "d"*64):
        assert neural.neural_arm(digest) in ("fit","tune","excluded_test")
        assert neural.neural_arm(digest)==neural.neural_arm(digest)
        if neural.game_arm(digest)=="test":
            assert neural.neural_arm(digest)=="excluded_test"
    with pytest.raises(ValueError, match="Invalid historical"):
        neural.neural_arm("not-a-sha")


def test_real85_neural_input_streams_all_focal_moves_and_verifies(monkeypatch,tmp_path):
    raw=_real_legal_pgn_fixture()
    monkeypatch.setattr(neural,"_source_bytes",lambda *a,**k:(raw,raw,"FIXTURE_REAL_PGN"))
    report=neural.build(tmp_path/"shards",players=["fischer"])
    row=report["players"]["fischer"]
    assert report["training_gradients_applied"] is False
    assert report["neural_weights_changed"] is False
    assert report["excluded_test_games_serialized"] == 0
    assert row["status"]=="READY_ALL_REAL_MOVES_FOR_NEURAL_TRAINING"
    assert row["accepted_distinct_games"]>100
    assert row["fit_fraction_of_all_eligible_games"]>=0.80
    assert row["fit_games"]>row["tune_games"]>0
    assert row["fit_games"]+row["tune_games"]+row["excluded_test_games"]==row["accepted_distinct_games"]
    assert row["fit_focal_decisions_all_phases"]>row["fit_games"]
    assert row["source_pgn_sha256"]==sha256(raw).hexdigest()
    data=(tmp_path/"shards/fischer-fit.jsonl").read_text().splitlines()
    assert len(data)==row["fit_games"]
    samples=[json.loads(line) for line in data]
    assert all(x["arm"]=="fit" and neural.neural_arm(x["game_sha256"])=="fit"
               for x in samples)
    assert all(x["focal_decisions"]==len(x["uci_moves"])//2 for x in samples
               if x["focal_color"]=="white" and len(x["uci_moves"])%2==0)
    verified=neural.verify(tmp_path/"shards")
    assert verified["players"]["fischer"]["games"]==row["fit_games"]+row["tune_games"]
    assert verified["players"]["fischer"]["focal_decisions"]==(
        row["fit_focal_decisions_all_phases"]+row["tune_focal_decisions_all_phases"])
    with pytest.raises(ValueError,match="Output must be"):
        neural.build(tmp_path/"shards",players=["fischer"])


def test_all_move_shard_tamper_rejected_even_if_manifest_hash_updated(monkeypatch,tmp_path):
    raw=_real_legal_pgn_fixture()
    monkeypatch.setattr(neural,"_source_bytes",lambda *a,**k:(raw,raw,"FIXTURE_PGN"))
    neural.build(tmp_path/"shards",players=["fischer"])
    root=tmp_path/"shards"
    file=root/"fischer-fit.jsonl"
    rows=file.read_text().splitlines()
    first=json.loads(rows[0])
    first["uci_moves"][0]="e2e5"  # illegal; identity remains historical digest
    rows[0]=json.dumps(first,sort_keys=True,separators=(",",":"))
    file.write_text("\n".join(rows)+"\n")
    report=json.loads((root/"manifest.json").read_text())
    report["players"]["fischer"]["shards"]["fit"]["sha256"]=sha256(file.read_bytes()).hexdigest()
    (root/"manifest.json").write_text(json.dumps(report))
    with pytest.raises(ValueError,match="identity or partition leak"):
        neural.verify(root)


def test_missing_source_explicit_and_fails_closed(monkeypatch,tmp_path):
    from urllib.error import HTTPError
    def missing(*args,**kwargs):
        raise HTTPError("https://example.org",404,"missing",None,None)
    monkeypatch.setattr(neural,"_source_bytes",missing)
    r=neural.build(tmp_path/"allowed",players=["fischer"],allow_missing=True)
    assert r["players"]["fischer"]["status"]=="SOURCE_MISSING"
    assert not (tmp_path/"allowed/fischer-fit.jsonl").exists()
    assert neural.verify(tmp_path/"allowed")["status"] == "NO_REAL_INPUT_SHARDS"
    with pytest.raises(HTTPError):
        neural.build(tmp_path/"forbidden",players=["fischer"],allow_missing=False)
    assert not (tmp_path/"forbidden").exists()



def test_local_absence_is_explicit_but_malformed_source_is_not_ignored(tmp_path):
    archive = tmp_path/"archive"
    archive.mkdir()
    r=neural.build(tmp_path/"allowed-local",players=["fischer"],
                   archive_dir=archive,allow_missing=True)
    assert r["players"]["fischer"]=={
        "status":"SOURCE_MISSING","reason":"LOCAL_SOURCE_MISSING"}
    assert neural.verify(tmp_path/"allowed-local")["status"] == "NO_REAL_INPUT_SHARDS"
    with pytest.raises(FileNotFoundError,match="Local historical"):
        neural.build(tmp_path/"forbidden-local",players=["fischer"],
                     archive_dir=archive,allow_missing=False)
    assert not (tmp_path/"forbidden-local").exists()
    source = archive/"Fischer.pgn"
    source.write_bytes(b"invalid not a valid PGN")
    # Present-but-malformed data must NEVER be classified as absent.
    with pytest.raises(ValueError, match="Invalid raw player PGN"):
        neural.build(tmp_path/"malformed-local",players=["fischer"],
                     archive_dir=archive,allow_missing=True)
    assert not (tmp_path/"malformed-local").exists()
