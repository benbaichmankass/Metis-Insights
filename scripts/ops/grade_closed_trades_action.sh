#!/usr/bin/env bash
# Tier-1: grade closed order packages and return ONLY the new (delta) rows.
#
# This is the sanctioned web/PM-session path to keep the Claude decision
# grade (scripts/ops/score_order_packages.py::_grade_package — a pure
# deterministic rubric, not an LLM call) current WITHOUT pulling the whole
# trade_journal.db::trades table back through the size-limited GitHub-issue
# diag relay (a full table can run ~650KB; the relay comment budget is
# ~55KB, which repeatedly truncated/failed prior grading attempts —
# see docs/claude/system-actions.md's grade-closed-trades entry and
# .claude/skills/system-review/SKILL.md's grading section).
#
# Runs score_order_packages.py --emit-delta-only against the live
# trade_journal.db and the VM's read-only ict-git-sync mirror of the
# checked-in comms/claude_strategy_scores.jsonl. READ-ONLY end to end:
#   * the DB connection is sqlite3 mode=ro
#   * the score file is only ever READ, never written/appended — the
#     script prints NDJSON to stdout, nothing lands on disk on the VM
#   * this wrapper never git-adds/commits/pushes (the VM_GIT_DEPLOY_TOKEN
#     credential is Contents:Read-only by design; a write attempt here
#     would be a Tier-3 violation of that boundary)
#
# The operator/session applies the returned delta by appending it to
# comms/claude_strategy_scores.jsonl in a normal PR (or via a follow-up
# --append run wherever the DB is directly reachable).
#
# Operator invokes via system-actions issue with body:
#   action: grade-closed-trades
#   reason: <text>
#   since: <ISO_TS>        (optional; only packages created at/after this)
#   limit: <int>           (optional, default 300 — see score_order_packages.py)
#   include_open: <true|1> (optional; widen scope beyond closed-only)
set -euo pipefail

SCRIPT_NAME="grade_closed_trades"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

load_runtime_secrets
DB_PATH="$(runtime_db_path)"

SINCE="${ACTION_SINCE:-}"
LIMIT="${ACTION_LIMIT:-300}"
INCLUDE_OPEN="${ACTION_INCLUDE_OPEN:-}"

case "${INCLUDE_OPEN,,}" in
    1|true|yes|on) INCLUDE_OPEN=1 ;;
    *) INCLUDE_OPEN=0 ;;
esac

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db missing at ${DB_PATH}"
    record_audit "grade-closed-trades" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

cd "${REPO_DIR}"

# The score file this VM has via its read-only ict-git-sync mirror. Never
# opened for write below — score_order_packages.py --emit-delta-only only
# reads it to compute the skip-set.
SCORES_PATH="${REPO_DIR}/comms/claude_strategy_scores.jsonl"

CMD=(python3 scripts/ops/score_order_packages.py "${DB_PATH}" "${SCORES_PATH}"
     --emit-delta-only --limit "${LIMIT}")
if [ -n "${SINCE}" ]; then
    CMD+=(--since "${SINCE}")
fi
if [ "${INCLUDE_OPEN}" -eq 1 ]; then
    CMD+=(--include-open)
fi

echo
echo "===== ${CMD[*]} ====="
set +e
"${CMD[@]}"
exit_code=$?
set -e

record_audit "grade-closed-trades" "ok" \
    "{\"since\": \"${SINCE}\", \"limit\": ${LIMIT}, \"include_open\": ${INCLUDE_OPEN}, \"exit_code\": ${exit_code}}" >/dev/null || true

exit "${exit_code}"
