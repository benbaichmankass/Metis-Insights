#!/usr/bin/env bash
# Tier-2 operator action: venue-validate the Bybit broker-naked re-arm sweep on
# the DEMO account (BL-20260729-BYBIT-NAKED-POSITION-BLINDSPOT).
#
# Wraps scripts/ops/validate_bybit_naked_rearm.py — hard-locked to bybit_1
# (account_class: paper / demo: true; the script refuses anything else).
# Opens a tiny NAKED position (isolated symbol LTCUSDT, flat-at-start guarded),
# verifies the detection reads it as UNPROTECTED, re-arms a Full-mode
# set_trading_stop (the exact call order_monitor._attempt_naked_autoprotect's
# bybit branch makes), verifies it now reads PROTECTED, then cleans up (cancel
# stops, clear the position stop, reduce-only close).
#
# PASS verdict = the evidence gate for merging the real-money Bybit naked-rearm
# fix (PR #7874). Never touches a real-money account.
set -euo pipefail

SCRIPT_NAME="validate_bybit_naked_rearm"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

# Inherit the live trader's runtime env (.env): Bybit demo creds + BYBIT_TESTNET,
# so the one-shot validation client authenticates exactly like
# ict-trader-live.service does (the #1314 / #7142 creds-missing failure class).
load_runtime_secrets

PY_SCRIPT="${REPO_DIR}/scripts/ops/validate_bybit_naked_rearm.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: validation helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "validate-bybit-naked-rearm" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

log "Running validate_bybit_naked_rearm.py (demo account bybit_1, opens + re-arms + cleans up a tiny demo position) …"
echo
echo "===== validate_bybit_naked_rearm.py ====="

PY="${REPO_DIR}/.venv/bin/python3"
[ -x "${PY}" ] || PY="python3"

set +e
"${PY}" "${PY_SCRIPT}"
exit_code=$?
set -e

if [ "${exit_code}" -ne 0 ]; then
    record_audit "validate-bybit-naked-rearm" "failed" \
        "{\"exit_code\": ${exit_code}}" >/dev/null || true
    log "Validation FAILED (exit ${exit_code}) — do NOT merge the real-money re-arm yet."
    exit "${exit_code}"
fi

record_audit "validate-bybit-naked-rearm" "ok" "{}" >/dev/null || true
log "validate-bybit-naked-rearm complete: PASS."
exit 0
