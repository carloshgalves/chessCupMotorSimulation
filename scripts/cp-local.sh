#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
OUT="${CP_RESULTS:-$HOME/chesssim-cp-results}"
IMAGE="${CP_IMAGE:-chesssim-cp-local:$(git rev-parse --short HEAD)}"
NAME=chesssim-cp-local
mkdir -p "$OUT"
OUT="$(realpath "$OUT")"
case "${1:-status}" in
    sync)
        python3 scripts/cp_local_evidence.py sync --root "$OUT"
        python3 scripts/cp_local_evidence.py prepare --root "$OUT"
        python3 scripts/cp_local_evidence.py plan --root "$OUT"
        ;;
    plan) python3 scripts/cp_local_evidence.py plan --root "$OUT" ;;
    build)
        docker build -t "$IMAGE" -f scripts/cp-local.Dockerfile .
        docker run --rm "$IMAGE" bash -c 'sha256sum -c /opt/engine-hashes && python -m pytest -q tests/test_bilateral_cp.py tests/test_bilateral_cp_recovery.py tests/test_cp_local.py'
        ;;
    start)
        workers="${2:-2}"; cpus="${3:-4}"
        [[ "$workers" =~ ^[1-9][0-9]*$ ]] || exit 2
        if docker inspect "$NAME" >/dev/null 2>&1; then
            [[ "$(docker inspect -f '{{.State.Running}}' "$NAME")" == false ]] || { echo "Already running"; exit 1; }
            docker rm "$NAME" >/dev/null
        fi
        [[ -s "$OUT/archive-manifest.json" ]] || { echo "Run sync first"; exit 1; }
        docker run --rm "$IMAGE" sha256sum -c /opt/engine-hashes
        python3 scripts/cp_local_evidence.py plan --root "$OUT"
        if ! python3 -c 'import json,sys; sys.exit(bool(json.load(open(sys.argv[1]))["queued"]))' "$OUT/cloud-plan.json"; then
            [[ "${CP_ALLOW_CLOUD_OVERLAP:-0}" == 1 ]] || { echo "Cloud still has queued recovery jobs. Coordinate cutover or explicitly opt in to overlap."; exit 1; }
        fi
        rm -f "$OUT/STOP"
        docker run -d --init --name "$NAME" --cpus="$cpus" --memory="${CP_MEMORY:-8g}" \
            --user "$(id -u):$(id -g)" -e HOME=/tmp -e WORKERS="$workers" \
            -v "$OUT:/results" "$IMAGE" bash scripts/cp-local-worker.sh
        ;;
    stop)
        touch "$OUT/STOP"
        if docker inspect "$NAME" >/dev/null 2>&1; then
            if [[ "$(docker inspect -f '{{.State.Paused}}' "$NAME")" == true ]]; then docker unpause "$NAME"; fi
            docker wait "$NAME"
        fi
        echo "Workers stopped; verify logs for [SAVED] or [DONE] before shutdown."
        ;;
    pause) docker pause "$NAME" ;;
    resume) docker unpause "$NAME" ;;
    cpu) docker update --cpus="${2:?CPU limit required}" "$NAME" ;;
    status)
        docker ps -a --filter "name=^/$NAME$"
        find "$OUT/local" -name report.json -print 2>/dev/null || true
        ;;
    review)
        slug="${2:?player required}"; run="${3:?original wave run required}"
        python3 scripts/cp_local_evidence.py review --root "$OUT" --player "$slug" --run "$run"
        mkdir -p "$OUT/reviews/review-$slug"
        docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$OUT:/results" "$IMAGE" \
            python -m chesssim.roster_strength_review --player "$slug" \
            --folder "/results/review-inputs/$slug" --out "/results/reviews/review-$slug/review.json"
        ;;
    *) echo "Usage: $0 sync|plan|build|start [workers] [cpus]|stop|pause|resume|cpu N|status|review PLAYER RUN"; exit 2 ;;
esac
