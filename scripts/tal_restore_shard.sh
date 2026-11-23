#!/usr/bin/env bash
# Infrastructure only. The scientific executor validates the exact manifest
# (including code, engine, source and book hashes) and replays every case.
set -euo pipefail

mode="${1:?expected validate or restore}"
repo="${GITHUB_REPOSITORY:?missing repository}"
run_id="${GITHUB_RUN_ID:?missing current run}"
resume="${RESUME_RUN_ID:-}"

validate_source() {
    [[ -z "$resume" ]] && return 0
    if ! [[ "$resume" =~ ^[1-9][0-9]*$ ]] || [[ "$resume" == "$run_id" ]]; then
        echo "Invalid prior run id" >&2
        return 1
    fi
    local found
    found="$(gh api "repos/$repo/actions/runs/$resume" \
        --jq '[.event,.status,.head_branch,(.path|split("@")[0])]|@tsv')"
    if [[ "$found" != $'workflow_dispatch\tcompleted\tmain\t.github/workflows/tal-graded-games.yml' ]]; then
        echo "Resume requires a completed dispatch of the same workflow on main" >&2
        return 1
    fi
}

validate_source
[[ "$mode" == validate ]] && exit 0
[[ "$mode" == restore ]] || { echo "Unknown mode" >&2; exit 1; }

index="${SHARD_INDEX:?missing shard index}"
out="${SHARD_OUT:?missing shard output}"
attempt="${GITHUB_RUN_ATTEMPT:-1}"
[[ "$index" =~ ^([0-9]|[12][0-9]|3[01])$ ]] || exit 1
[[ "$attempt" =~ ^[1-9][0-9]*$ ]] || exit 1
if [[ -e "$out" || -L "$out" ]]; then
    echo "Refusing to replace existing output directory" >&2
    exit 1
fi
name="shard-$index"
source=""
artifact_id=""

find_artifact() {
    local candidate="$1" ids
    ids="$(gh api --paginate "repos/$repo/actions/runs/$candidate/artifacts?per_page=100" \
        --jq ".artifacts[] | select(.name == \"$name\" and .expired == false) | .id")"
    if [[ -z "$ids" ]]; then return 0; fi
    if ! [[ "$ids" =~ ^[1-9][0-9]*$ ]]; then
        echo "Ambiguous or malformed artifact identity" >&2
        return 1
    fi
    source="$candidate"
    artifact_id="$ids"
}

# On a retry, prefer this very run's checkpoint, then its explicitly
# selected predecessor. Never silently fall back when an API call fails.
if (( attempt > 1 )); then find_artifact "$run_id"; fi
if [[ -z "$source" && -n "$resume" ]]; then find_artifact "$resume"; fi
if [[ -z "$source" ]]; then
    echo "No saved checkpoint for $name; execute all its planned cases."
    exit 0
fi

stage="$(mktemp -d "$(dirname "$out")/.restore-$index.XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
# Download by exact run and unique nonexpired artifact name.
gh run download "$source" --repo "$repo" --name "$name" --dir "$stage"
# Only the original executor's atomically committed JSON files can be
# recovered. An interrupted .tmp is not a checkpoint; preserve the original
# artifact and discard only this staging copy. Reject unknown files/links.
python - "$stage" <<'PY'
import json
from pathlib import Path
import re
import sys

directory = Path(sys.argv[1])
for path in directory.iterdir():
    if path.is_symlink() or not path.is_file():
        raise SystemExit("Invalid restored file type")
    if path.name == "manifest.tmp" or re.fullmatch(r"[0-9a-f]{64}\.tmp", path.name):
        path.unlink()
    elif path.name != "manifest.json" and not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
        raise SystemExit("Unexpected restored file")
manifest = directory / "manifest.json"
if not manifest.exists():
    if any(directory.iterdir()):
        raise SystemExit("Case records without committed manifest")
else:
    data = json.loads(manifest.read_text())
    ids = data["case_ids"]
    if not isinstance(ids, list) or any(
        not isinstance(x, str) or not re.fullmatch(r"[0-9a-f]{64}", x) for x in ids
    ) or len(set(ids)) != len(ids):
        raise SystemExit("Invalid manifest case IDs")
    expected = {"manifest.json", *(x + ".json" for x in ids)}
    if not {p.name for p in directory.iterdir()} <= expected:
        raise SystemExit("Checkpoint contains unplanned case")
    print(f"Restored {len(list(directory.glob('*.json'))) - 1} committed case files; executor will validate them.")
PY
if [[ -f "$stage/manifest.json" ]]; then
    mv -T -- "$stage" "$out"
    echo "Restored $name from run $source (artifact $artifact_id)."
else
    echo "Prior artifact had no committed manifest; no completed case to restore."
fi
