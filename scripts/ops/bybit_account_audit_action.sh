#!/usr/bin/env bash
# Tier-1 read-only diagnostic: full Bybit account audit.
#
# Pulls /v5/position/closed-pnl + /v5/execution/list directly from
# Bybit for one account, summarises wins/losses/fees, and
# cross-checks the local trade_journal.db (Bybit is ground truth,
# DB is suspect).
#
# Triggered by the 2026-05-18 operator concern: "trader is posting
# 7-10 losses per win". Before drawing strategy conclusions we
# need to know what Bybit ACTUALLY shows for the account — the
# DB is known stale on PnL (issue #1419, 11 rows wrong; matcher
# bug latent in #1425).
#
# No DB writes. No live-trading side effects.
#
# Operator invokes via system-actions issue with body:
#   action: bybit-account-audit
#   reason: <text>
#   account: <id>     (e.g. bybit_2 — REQUIRED)
#   symbol: <ticker>  (optional; default = all symbols)
#   days: <int>       (optional; default 7, max 7 — Bybit retention)
set -euo pipefail

SCRIPT_NAME="bybit_account_audit"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

load_runtime_secrets  # exchange creds for the Bybit query
DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/bybit_account_audit.py"

# account_id is plumbed via the existing ACCOUNT_ID env (used by
# set-account-mode). Symbol + days come through new env slots
# added by this PR's system-actions.yml edit.
ACCOUNT="${ACCOUNT_ID:-}"
SYMBOL="${ACTION_SYMBOL:-}"
DAYS="${ACTION_DAYS:-7}"

if [ -z "${ACCOUNT}" ]; then
    log "ERROR: bybit-account-audit requires 'account: <id>' in issue body"
    record_audit "bybit-account-audit" "error" \
        "{\"reason\": \"missing account\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: helper missing at ${PY_SCRIPT}"
    record_audit "bybit-account-audit" "error" \
        "{\"reason\": \"helper missing\"}" >/dev/null || true
    exit 1
fi

# Build the argument vector.
ARGS=(--account "${ACCOUNT}" --days "${DAYS}" --db "${DB_PATH}")
if [ -n "${SYMBOL}" ]; then
    ARGS+=(--symbol "${SYMBOL}")
fi

echo
echo "===== bybit_account_audit.py ${ARGS[*]} ====="
set +e
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}" "${ARGS[@]}"
exit_code=$?
set -e

if [ "${exit_code}" -ne 0 ]; then
    record_audit "bybit-account-audit" "failed" \
        "{\"account\": \"${ACCOUNT}\", \"exit_code\": ${exit_code}}" \
        >/dev/null || true
    log "Helper exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "bybit-account-audit" "ok" \
    "{\"account\": \"${ACCOUNT}\", \"symbol\": \"${SYMBOL}\", \"days\": ${DAYS}}" \
    >/dev/null || true
exit 0
