"""Issue #11: user-facing offline entrypoint and no-overwrite gates."""
from __future__ import annotations

from hashlib import sha256
import json

import chess
import pytest

from chesssim.cli import main
from chesssim.shared_fen_controls import run_shared_fen_file


def _payload():
    board = chess.Board()
    board.fullmove_number = 9
    fen = board.fen()
    return {
        "player_a": "Fischer", "player_b": "Kasparov",
        "profile_a": "rodent-fischer", "profile_b": "rodent-kasparov",
        "min_ply": 17,
        "positions": [
            {"player": "Fischer", "game": "game-f", "fen": fen,
             "move": "e2e4", "ply": 17},
            {"player": "Kasparov", "game": "game-k", "fen": fen,
             "move": "d2d4", "ply": 17},
        ],
        "predictions": {
            "rodent-fischer": {fen: "e2e4"},
            "rodent-kasparov": {fen: "d2d4"},
        },
    }


def test_cli_generates_new_json_without_changing_source(tmp_path, capsys):
    inp, out = tmp_path / "input.json", tmp_path / "results.json"
    raw = json.dumps(_payload(), ensure_ascii=False).encode()
    inp.write_bytes(raw)
    assert main(["shared-fen-controls", "--input", str(inp), "--out", str(out),
                 "--ack-unsealed"]) == 0
    assert inp.read_bytes() == raw
    output = json.loads(out.read_text(encoding="utf-8"))
    assert output["matched_fens"] == 1
    assert output["contrast"] == 1.0
    assert output["input_file_sha256"] == sha256(raw).hexdigest()
    assert len(output["input_evidence_sha256"]) == 64
    assert "Fischer" in capsys.readouterr().out


def test_cli_requires_explicit_unsealed_data_acknowledgement(tmp_path):
    inp, out = tmp_path / "input.json", tmp_path / "results.json"
    inp.write_text(json.dumps(_payload()))
    with pytest.raises(SystemExit) as exc:
        main(["shared-fen-controls", "--input", str(inp), "--out", str(out)])
    assert exc.value.code == 2
    assert not out.exists()


def test_runner_rejects_overwrite_without_touching_existing_content(tmp_path):
    inp, out = tmp_path / "input.json", tmp_path / "results.json"
    inp.write_text(json.dumps(_payload()))
    out.write_text("permanent")
    with pytest.raises(FileExistsError):
        run_shared_fen_file(inp, out, ack_unsealed=True)
    assert out.read_text() == "permanent"


@pytest.mark.parametrize("mutation", ["extra_field", "missing_field", "bad_predictions",
                                       "invalid_json", "duplicate_json_key", "symlink_input", "symlink_output",
                                       "denied_ack"])
def test_runner_fails_closed_without_partial_report(tmp_path, mutation):
    inp, out = tmp_path / "input.json", tmp_path / "results.json"
    data = _payload()
    if mutation == "extra_field":
        data["test_sealed"] = True
    elif mutation == "missing_field":
        del data["player_a"]
    elif mutation == "bad_predictions":
        data["predictions"] = {}
    inp.write_text(json.dumps(data))
    if mutation == "invalid_json":
        inp.write_text("{not json")
    if mutation == "duplicate_json_key":
        raw = inp.read_text()
        inp.write_text(raw[:-1] + ', "player_a":"Faked name"}')
    if mutation == "symlink_input":
        link = tmp_path / "link.json"
        link.symlink_to(inp)
        inp = link
    if mutation == "symlink_output":
        out.symlink_to(tmp_path / "missing-target")
    with pytest.raises((ValueError, FileExistsError)):
        run_shared_fen_file(inp, out, ack_unsealed=mutation != "denied_ack")
    assert not out.is_file()


def test_input_digest_changes_on_role_swap_even_with_same_evidence(tmp_path):
    inp, out = tmp_path / "input.json", tmp_path / "first.json"
    data = _payload()
    inp.write_text(json.dumps(data))
    first = run_shared_fen_file(inp, out, ack_unsealed=True)
    data["profile_a"], data["profile_b"] = data["profile_b"], data["profile_a"]
    inp.write_text(json.dumps(data))
    second = run_shared_fen_file(inp, tmp_path / "second.json", ack_unsealed=True)
    assert first["input_evidence_sha256"] != second["input_evidence_sha256"]
    assert first["contrast"] == 1
    assert second["contrast"] == -1
