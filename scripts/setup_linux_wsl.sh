#!/usr/bin/env bash
# Linux/WSL venv bootstrap. External engines and PGNs are never bundled.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
PYTHON=python3
if [[ -v CHESSSIM_PYTHON ]]; then PYTHON="$CHESSSIM_PYTHON"; fi
VENV="$ROOT/.venv"
if [[ -v CHESSSIM_VENV ]]; then VENV="$CHESSSIM_VENV"; fi
SKIP_TESTS=0
SKIP_INSTALL=0
FORWARD=()
for arg in "$@"; do
  case "$arg" in
    --help|-h)
      echo 'Usage: bash scripts/setup_linux_wsl.sh [--skip-install] [--skip-tests] [--rodent PATH] [--apply-profiles] [--with-books] [--smoke] [--out NEW_PATH] [--uci-engine PATH]'
      echo 'Environment: CHESSSIM_PYTHON, CHESSSIM_VENV, RODENT_IV_HOME, CHESSSIM_UCI_ENGINE'
      echo 'Does not install engines. --apply-profiles explicitly consents to external profile changes.'
      exit 0 ;;
    --skip-tests) SKIP_TESTS=1 ;;
    --skip-install) SKIP_INSTALL=1 ;;
    *) FORWARD+=("$arg") ;;
  esac
done
if [[ "$(uname -s)" != Linux ]]; then echo 'ERROR: Linux/WSL required' >&2; exit 2; fi
if ! command -v "$PYTHON" >/dev/null 2>&1 && [[ ! -x "$PYTHON" ]]; then echo 'ERROR: Python missing; install python3 and python3-venv' >&2; exit 2; fi
if ! "$PYTHON" -c 'import sys;sys.exit(0 if sys.version_info >= (3,11) else 1)'; then echo 'ERROR: Python >=3.11 required' >&2; exit 2; fi
if [[ ! -e "$VENV/bin/python" ]]; then
  if [[ -e "$VENV" ]]; then echo 'ERROR: venv exists but lacks bin/python; set CHESSSIM_VENV' >&2; exit 2; fi
  if [[ "$SKIP_INSTALL" == 1 ]]; then echo 'ERROR: --skip-install needs a populated venv' >&2; exit 2; fi
  if ! "$PYTHON" -m venv "$VENV"; then echo 'ERROR: could not create venv; install python3-venv' >&2; exit 2; fi
fi
VPY="$VENV/bin/python"
if [[ "$SKIP_INSTALL" == 0 ]]; then
  if ! "$VPY" -m pip install --disable-pip-version-check -e "$ROOT[dev]"; then echo 'ERROR: pip installation failed; check index/network/build dependencies' >&2; exit 2; fi
fi
if ! "$VPY" -c 'import chess, chesssim, pytest' 2>/dev/null; then
  echo 'ERROR: venv lacks project or test dependencies; rerun without --skip-install' >&2
  exit 2
fi
if ! "$VPY" -m pip check; then echo 'ERROR: inconsistent dependencies; fix venv' >&2; exit 2; fi
if [[ "$SKIP_TESTS" == 0 ]]; then
  if ! (cd "$ROOT" && "$VPY" -m pytest -q -ra); then echo 'ERROR: project tests failed' >&2; exit 2; fi
fi
exec "$VPY" "$ROOT/scripts/verify_linux_wsl.py" "${FORWARD[@]}"
