#!/usr/bin/env bash
# Tier-2 operator action: venue-validate BYBIT_TPSL_MODE=partial on the
# DEMO account (Fix 2 of BL-20260720-ICTSCALP-PASTSTOP-EXITS).
#
# Wraps scripts/ops/validate_partial_tpsl.py — hard-locked to bybit_1
# (account_class: paper / demo: true; the script refuses anything else).
# Places two tiny netted BTCUSDT orders with qty-scoped Partial tpsl,
# verifies BOTH bracket pairs coexist on the venue (under the current Full
# mode the second order would replace the first's bracket — the June 21-23
# incident mechanism), amends one SL qty-scoped, then cleans up (cancels
# the stop orders + reduce-only closes the position).
#
# PASS verdict = the evidence gate for the Tier-3 BYBIT_TPSL_MODE=partial
# flip on the live VM (via set-env). Never touches a real-money account.
set -euo pipefail

SCRIPT_NAME="validate_partial_tpsl"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

# Inherit the live trader's runtime env (.env): Bybit demo creds + BYBIT_TESTNET,
# so the one-shot validation client authenticates exactly like
# ict-trader-live.service does. System-action wrappers run via SSH from a fresh
# shell and do NOT inherit the systemd unit's EnvironmentFile — without this the
# client build fails with "creds missing" (the #1314 failure class; seen live on
# the first validate-partial-tpsl dispatch, issue #7142).
load_runtime_secrets

PY_SCRIPT="${REPO_DIR}/scripts/ops/validate_partial_tpsl.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: validation helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "validate-partial-tpsl" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

log "Running validate_partial_tpsl.py (demo account bybit_1, places + cleans up tiny demo orders) …"
echo
echo "===== validate_partial_tpsl.py ====="

PY="${REPO_DIR}/.venv/bin/python3"
[ -x "${PY}" ] || PY="python3"

set +e
"${PY}" "${PY_SCRIPT}"
exit_code=$?
set -e

if [ "${exit_code}" -ne 0 ]; then
    record_audit "validate-partial-tpsl" "failed" \
        "{\"exit_code\": ${exit_code}}" >/dev/null || true
    log "Validation FAILED (exit ${exit_code}) — do NOT flip BYBIT_TPSL_MODE=partial."
    exit "${exit_code}"
fi

record_audit "validate-partial-tpsl" "ok" "{}" >/dev/null || true
log "validate-partial-tpsl complete: PASS."
exit 0
