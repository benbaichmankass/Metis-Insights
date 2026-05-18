#!/usr/bin/env bash
# Tier-1 read-only diagnostic: classify reconciler-filled closes by
# where they landed (TP / SL / inside bracket / overshoot).
#
# Answers the operator's 2026-05-18 question after #1439's audit:
# is the 39% reconciler-filled rate a monitor-detection BUG or
# the safety-net working as designed?
#
# No DB writes; no live-trading side effects.
#
# Operator invokes via operator-actions issue:
#   action: monitor-miss-analysis
#   reason: <text>
#   account: <id>     (required)
#   days: <int>       (optional, default 7)
set -euo pipefail

SCRIPT_NAME="monitor_miss_analysis"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/monitor_miss_analysis.py"

ACCOUNT="${ACCOUNT_ID:-}"
DAYS="${ACTION_DAYS:-7}"

if [ -z "${ACCOUNT}" ]; then
    log "ERROR: monitor-miss-analysis requires 'account: <id>' in issue body"
    record_audit "monitor-miss-analysis" "error" \
        "{\"reason\": \"missing account\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: helper missing at ${PY_SCRIPT}"
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db missing at ${DB_PATH}"
    exit 1
fi

echo
echo "===== monitor_miss_analysis.py --account ${ACCOUNT} --days ${DAYS} ====="
set +e
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}" \
    --account "${ACCOUNT}" --days "${DAYS}"
exit_code=$?
set -e

if [ "${exit_code}" -ne 0 ]; then
    record_audit "monitor-miss-analysis" "failed" \
        "{\"account\": \"${ACCOUNT}\", \"exit_code\": ${exit_code}}" \
        >/dev/null || true
    log "Helper exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "monitor-miss-analysis" "ok" \
    "{\"account\": \"${ACCOUNT}\", \"days\": ${DAYS}}" >/dev/null || true
exit 0
