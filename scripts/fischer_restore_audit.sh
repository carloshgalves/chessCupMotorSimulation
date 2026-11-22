#!/usr/bin/env bash
# Restore only atomically committed whole-game audits, never trust old metrics
# without the Python engine/source/protocol manifest checks.
set -euo pipefail
repo="${GITHUB_REPOSITORY:?}"
current="${GITHUB_RUN_ID:?}"
index="${SHARD_INDEX:?}"
out="${SHARD_OUT:?}"
resume="${RESUME_RUN_ID:-}"
attempt="${GITHUB_RUN_ATTEMPT:-1}"
if ! [[ "$index" =~ ^([0-9]|[1-5][0-9]|6[0-3])$ ]]; then
  echo "Invalid shard" >&2; exit 1
fi
if [[ -e "$out" || -L "$out" ]]; then
  echo "Output already exists" >&2; exit 1
fi
name="shard-$index"

identity() {
  local run="$1" actual wanted
  if ! [[ "$run" =~ ^[1-9][0-9]*$ ]] || [[ "$run" == "$current" ]]; then
    echo "Invalid predecessor" >&2; return 1
  fi
  actual="$(gh api "repos/$repo/actions/runs/$run" \
      --jq '[.event,.status,.head_branch,(.path|split("@")[0])]|@tsv')"
  wanted="$(printf 'workflow_dispatch\tcompleted\tmain\t.github/workflows/fischer-all-competencies.yml')"
  if [[ "$actual" != "$wanted" ]]; then
    echo "Only a completed matching workflow_dispatch may supply checkpoints" >&2; return 1
  fi
}
if [[ -n "$resume" ]]; then identity "$resume"; fi

find_id() {
  local run="$1" ids
  ids="$(gh api --paginate "repos/$repo/actions/runs/$run/artifacts?per_page=100" \
       --jq ".artifacts[]|select(.name == \"$name\" and .expired == false)|.id")"
  [[ -z "$ids" ]] && return 0
  if ! [[ "$ids" =~ ^[1-9][0-9]*$ ]]; then
    echo "Ambiguous checkpoint" >&2; return 1
  fi
  SOURCE_RUN="$run"
}
SOURCE_RUN=""
if [[ "$attempt" =~ ^[2-9][0-9]*$ || "$attempt" =~ ^[2-9]$ ]]; then
  # Rerunning this workflow can restore its own uploaded shard.
  find_id "$current"
fi
if [[ -z "$SOURCE_RUN" && -n "$resume" ]]; then find_id "$resume"; fi
if [[ -z "$SOURCE_RUN" ]]; then
  echo "No compatible artifact found; all original source games remain assigned"
  exit 0
fi
stage="$(mktemp -d "$(dirname "$out")/.fischer-audit-restore-XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
gh run download "$SOURCE_RUN" --repo "$repo" --name "$name" --dir "$stage"
python - "$stage" <<'PY'
from pathlib import Path
import json, re, sys
directory=Path(sys.argv[1])
for path in directory.iterdir():
    if path.is_symlink() or not path.is_file():
        raise SystemExit("Invalid checkpoint file")
    if path.name=="manifest.tmp" or re.fullmatch("[0-9a-f]{64}\\.tmp",path.name):
        path.unlink()
    elif path.name!="manifest.json" and not re.fullmatch("[0-9a-f]{64}\\.json",path.name):
        raise SystemExit("Unexpected checkpoint")
manifest=directory/"manifest.json"
if manifest.exists():
    data=json.loads(manifest.read_text())
    ids=data["game_ids"]
    if (not isinstance(ids,list) or len(ids)!=len(set(ids)) or
            any(not re.fullmatch("[0-9a-f]{64}", x) for x in ids)):
        raise SystemExit("Invalid checkpoint manifest")
    expected={"manifest.json",*(x+".json" for x in ids)}
    if not {p.name for p in directory.iterdir()}<=expected:
        raise SystemExit("Unplanned checkpoint")
elif any(directory.iterdir()):
    raise SystemExit("Orphan checkpoint without manifest")
PY
if [[ -f "$stage/manifest.json" ]]; then
  mv -T -- "$stage" "$out"
  echo "Restored complete-game checkpoints from run $SOURCE_RUN; every case will be revalidated."
else
  echo "No committed manifest in prior artifact"
fi
