#!/usr/bin/env bash
set -euo pipefail
ROOT=/results
mkdir -p "$ROOT/logs" "$ROOT/locks" "$ROOT/local"
run_one() {
    local slug="$1" dir="/results/local/$1"
    mkdir -p "$dir"
    [[ ! -e /results/STOP ]] || return 0
    [[ ! -e "/results/exclude/$slug" ]] || return 0
    [[ ! -s "$dir/report.json" ]] || return 0
    # A completed cloud recovery is preserved, not repeated automatically.
    if compgen -G "/results/imported/45985663890/cp-recovery-$slug/*/report.json" >/dev/null; then
        echo "[CLOUD COMPLETED] $slug"
        return 0
    fi
    # Pin artifact input on first execution so checkpoint bindings survive future syncs.
    if [[ ! -f "$dir/prior-choice" ]]; then
        local source="-"
        for run in 45888949887 45913126242 45918461269; do
            local file
            for file in "/results/imported/$run/cp-fit-$slug/"*/selection.json; do
                if [[ -s "$file" ]]; then source="$file"; break; fi
            done
            [[ "$source" == "-" ]] || break
        done
        printf '%s\n' "$source" > "$dir/prior-choice.tmp"
        mv "$dir/prior-choice.tmp" "$dir/prior-choice"
    fi
    local source
    source="$(cat "$dir/prior-choice")"
    local args=()
    [[ "$source" == "-" ]] || args+=(--prior-fit "$source")
    echo "[START] $slug $(date)"
    local rc=0
    python -m chesssim.bilateral_cp_local --player "$slug" \
        --rodent /opt/rodent --stockfish /usr/games/stockfish \
        --out "$dir/report.json" --checkpoint-dir "$dir/checkpoints" \
        --stop-file /results/STOP "${args[@]}" 2>&1 | tee -a "/results/logs/$slug.log" || rc=$?
    case "$rc" in
        0) echo "[DONE] $slug" ;;
        75) echo "[SAVED] $slug" ;;
        *) echo "[BLOCKED] $slug exit=$rc"; touch "$dir/FAILED" ;;
    esac
}
export -f run_one
python - <<'PY' | xargs -r -P "${WORKERS:-2}" -n 1 bash -o pipefail -c 'run_one "$1"' _
from chesssim.catalog import load_roster
from chesssim.bilateral_cp_recovery import verify_locks
from pathlib import Path
retained = verify_locks()
priority = ["fischer", "grischuk", "portisch", "larsen", "gligoric", "marshall", "reti", "topalov", "carlsen", "karpov", "kasparov"]
players = [p.slug for p in load_roster() if p.slug not in retained
           and not Path("/results/local", p.slug, "FAILED").exists()]
players.sort(key=lambda s: (priority.index(s) if s in priority else len(priority), s))
print("\n".join(players))
PY
