#!/usr/bin/env bash
# Pipeline health test — VM-side wrapper for the health-snapshot
# workflow. Invoked over SSH by `.github/workflows/health-snapshot-pr.yml`.
#
# Runs the live trader's smoke harness (`scripts/smoke_test_trade.py`)
# in --dry-run mode (forces `client=None` inside the smoke so
# `safe_place_order` cannot reach the exchange) and emits a single
# JSON object on stdout that `scripts/run_health_check.py
# --pipeline-test` merges into `checks.pipeline` of the health report.
#
# Output schema:
#   {
#     "status": "ok" | "warn" | "fail",
#     "note": "one-sentence summary",
#     "exit_code": <int>,
#     "duration_seconds": <float>,
#     "account": "<name from accounts.yaml>",
#     "tail": "<last 30 combined stdout+stderr lines>"
#   }
#
# Status mapping (mirrors smoke_test_trade.py exit code contract):
#   exit 0   → ok    open + close round-trip completed in dry-run mode
#   exit 1   → warn  order rejected by safe_place_order (plumbing-on-rejection
#                    path still exercised — valid smoke outcome)
#   exit 2   → fail  script-level error (missing creds, safety guard, etc.)
#   exit 124 → fail  timeout (smoke hung; pipeline likely stalled)
#   other    → fail  unexpected exit
#
# Tunables (env vars; sane defaults match the smoke docstring):
#   HEALTH_TEST_ACCOUNT  account name in config/accounts.yaml (default: bybit_2)
#   HEALTH_TEST_QTY      order qty in BTC, hard-capped at 0.001 by smoke (default: 0.001)
#   HEALTH_TEST_TIMEOUT  hard kill in seconds (default: 90 — covers open + sleep(2) + close)
set -uo pipefail

# Run from repo root so `python scripts/smoke_test_trade.py` resolves
# its REPO_ROOT path-helper exactly the way the live bot's systemd
# unit does.
cd "$(dirname "$0")/.."

ACCOUNT="${HEALTH_TEST_ACCOUNT:-bybit_2}"
QTY="${HEALTH_TEST_QTY:-0.001}"
TIMEOUT="${HEALTH_TEST_TIMEOUT:-90}"

# Source the .env the systemd unit's `EnvironmentFile=` would have
# loaded. smoke_test_trade.py reads BYBIT credentials directly out of
# os.environ — without sourcing here, `os.environ.get(api_key_env)`
# returns None and the smoke exits rc=2 ("creds not found").
# Best-effort: a missing .env surfaces as a clean smoke error, not a
# wrapper crash.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

LOG_FILE="$(mktemp -t pipeline_health.XXXXXX)"
trap 'rm -f "$LOG_FILE"' EXIT

start_ts=$(date +%s.%N)
set +e
timeout "$TIMEOUT" python scripts/smoke_test_trade.py \
    --dry-run \
    --account "$ACCOUNT" \
    --qty "$QTY" \
    > "$LOG_FILE" 2>&1
RC=$?
set -e
end_ts=$(date +%s.%N)

DURATION=$(awk -v s="$start_ts" -v e="$end_ts" 'BEGIN { printf "%.3f", e-s }')

case "$RC" in
  0)   STATUS="ok";   NOTE="open + close round-trip completed in dry-run mode" ;;
  1)   STATUS="warn"; NOTE="order rejected by safe_place_order (plumbing-on-rejection path exercised)" ;;
  2)   STATUS="fail"; NOTE="smoke_test_trade.py exited rc=2 — script-level error (see tail)" ;;
  124) STATUS="fail"; NOTE="smoke_test_trade.py timed out after ${TIMEOUT}s" ;;
  *)   STATUS="fail"; NOTE="smoke_test_trade.py exited unexpectedly rc=${RC}" ;;
esac

# Hand the case-derived strings to python via env so json.dumps escapes
# the tail content correctly even if it contains quotes or newlines.
export ACCOUNT DURATION LOG_FILE NOTE RC STATUS
python3 - <<'PYEOF'
import json
import os

try:
    with open(os.environ["LOG_FILE"], errors="replace") as fh:
        tail = "\n".join(fh.read().splitlines()[-30:])
except Exception as exc:  # noqa: BLE001
    tail = f"[could not read log file: {exc}]"

print(json.dumps({
    "status": os.environ["STATUS"],
    "note": os.environ["NOTE"],
    "exit_code": int(os.environ["RC"]),
    "duration_seconds": float(os.environ["DURATION"]),
    "account": os.environ["ACCOUNT"],
    "tail": tail,
}, indent=2, sort_keys=True))
PYEOF
