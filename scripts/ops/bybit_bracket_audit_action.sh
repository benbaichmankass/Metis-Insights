#!/usr/bin/env bash
# Tier-1 system-action: READ-ONLY broker-truth audit of Bybit protective-bracket
# coverage, plus a definitive three-source read of the effective
# BYBIT_TPSL_MODE on the live VM.
#
# Why this exists
# ---------------
# Nothing in the system could previously answer, with receipts, either of:
#   (a) "what value of BYBIT_TPSL_MODE is the RUNNING trader actually using?"
#       — the diag relay is fixed-curl (no shell, no file reads) and
#         /api/bot/config does not expose process env; and
#   (b) "is every open Bybit trade actually protected at the broker right now?"
#       — order_monitor's Bybit sweep treats protection as a BOOLEAN (any one
#         resting SL leg ⇒ "protected"), so a netted position whose per-trade
#         qty-scoped legs are partly missing reads as protected while being only
#         partially covered.
#
# (a) is answered from three independent places, because a set-env writes the
# FILE while the running process keeps whatever it was started with — they can
# legitimately disagree and only the process environ is the truth:
#   1. the .env file the unit loads,
#   2. any systemd Environment= / EnvironmentFile= on the unit,
#   3. /proc/<MainPID>/environ of the live ict-trader-live process.
#
# (b) is answered by scripts/ops/bybit_bracket_audit.py (qty coverage, per-trade
# leg liveness).
#
# READ-ONLY: this action places/amends/cancels nothing and writes no DB row. It
# is safe to run at any time, including against real money.
#
# Dispatch:
#   action: bybit-bracket-audit
#   account: bybit_2          (optional — default: every bybit account)
#   symbol: XRPUSDT           (optional — default: every symbol with an open row)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "${SCRIPT_DIR}/_lib.sh" 2>/dev/null || true

REPO_DIR="${REPO_DIR:-/home/ubuntu/ict-trading-bot}"
ENV_FILE="${REPO_DIR}/.env"
UNIT="ict-trader-live.service"
KEY="BYBIT_TPSL_MODE"

echo "===== (1) ${KEY} in ${ENV_FILE} ====="
if [ -f "${ENV_FILE}" ]; then
    # Print every occurrence — a duplicated key is itself a finding (last wins).
    if grep -nE "^[[:space:]]*(export[[:space:]]+)?${KEY}=" "${ENV_FILE}"; then
        n="$(grep -cE "^[[:space:]]*(export[[:space:]]+)?${KEY}=" "${ENV_FILE}")"
        [ "${n}" -gt 1 ] && echo "  !! ${n} occurrences — LAST one wins on load"
    else
        echo "  (absent from .env — code default 'full' applies unless set elsewhere)"
    fi
else
    echo "  !! ${ENV_FILE} not found"
fi

echo
echo "===== (2) systemd env on ${UNIT} ====="
systemctl show "${UNIT}" -p EnvironmentFiles -p Environment 2>/dev/null \
    || echo "  (systemctl show unavailable)"

echo
echo "===== (3) RUNNING process environ (the authoritative value) ====="
MAIN_PID="$(systemctl show -p MainPID --value "${UNIT}" 2>/dev/null || echo 0)"
echo "  ${UNIT} MainPID=${MAIN_PID}"
if [ -n "${MAIN_PID}" ] && [ "${MAIN_PID}" != "0" ] && [ -r "/proc/${MAIN_PID}/environ" ]; then
    val="$(tr '\0' '\n' < "/proc/${MAIN_PID}/environ" | grep -E "^${KEY}=" || true)"
    if [ -n "${val}" ]; then
        echo "  ${val}"
    else
        echo "  ${KEY} NOT PRESENT in the running process environment"
        echo "  => _bybit_tpsl_mode() resolves to the code default 'full'"
    fi
    echo "  --- process start time (has it restarted since the last set-env?) ---"
    ps -o lstart= -p "${MAIN_PID}" 2>/dev/null || true
else
    echo "  cannot read /proc/${MAIN_PID}/environ (process down or not permitted)"
fi

echo
echo "===== (4) bybit_bracket_audit.py ====="
cd "${REPO_DIR}" || { echo "cannot cd ${REPO_DIR}"; exit 1; }
PY="${REPO_DIR}/.venv/bin/python3"
[ -x "${PY}" ] || PY="python3"

ARGS=()
[ -n "${ACCOUNT_ID:-}" ] && ARGS+=(--account "${ACCOUNT_ID}")
[ -n "${ACTION_SYMBOL:-}" ] && ARGS+=(--symbol "${ACTION_SYMBOL}")

# Inherit the live trader's runtime env (.env) so the Bybit creds + BYBIT_TESTNET
# resolve exactly as they do for the trader. `set -a` exports every assignment.
if [ -f "${ENV_FILE}" ]; then
    set -a
    # shellcheck source=/dev/null
    . "${ENV_FILE}"
    set +a
fi

"${PY}" scripts/ops/bybit_bracket_audit.py "${ARGS[@]}"
rc=$?
echo
echo "bybit_bracket_audit exit=${rc}"
exit "${rc}"
