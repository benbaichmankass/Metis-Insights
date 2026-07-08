#!/usr/bin/env bash
# system-action wrapper: void-flag the paper-account RESET orphan-adoption
# artifacts (one-shot journal hygiene).
#
# Runs scripts/ops/supersede_reset_orphan_artifacts.py on the live VM.
# DRY-RUN by default — prints the matched bare-orphan phantom paper rows
# (id / symbol / fabricated PnL) WITHOUT writing. Only writes when ACTION_APPLY
# is true, and takes a timestamped DB backup first.
#
# Background: the 2026-07-07 alpaca_paper external reset re-seeded the paper
# account with a default portfolio the bot never opened; the reverse reconciler
# adopted those as bare `adopted_orphan` trades (strategy_name='orphan_adopt',
# no order package) and the local-PnL sweep priced them with the equity formula
# (fabricated PnL — e.g. the 1360-share SLV short adopted twice as trades
# 3265+3266 at -693.6 each). The live-path fix (PR #5951 reset-detection) stops
# NEW strategy-attributed reset artifacts; this cleans up the historical bare
# phantoms that still carry the fabricated PnL so they stop polluting paper KPIs.
#
# Env (passed by system-actions.yml):
#   ACTION_APPLY - "true" to write (backup taken first); else = dry-run report
#   ACTION_IDS   - optional comma-separated trade-id allowlist to restrict the match
#
# WHY apply is gated: a live apply void-flags rows on the canonical money DB
# (paper rows only — the script's predicate is `is_demo=1`, never real money;
# and only bare `strategy_name='orphan_adopt'` + NULL order_package_id rows, so
# a genuinely-reattached adopted orphan is never touched). Run dry-run first,
# eyeball the matched rows, then apply with operator approval.
#
# Idempotent. Safe to re-run — rows already reconcile_status='superseded' are
# skipped by the SQL guard. Pure journal hygiene: never closes/opens an exchange
# position, never deletes a row.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/supersede_reset_orphan_artifacts.py"
ACTION_APPLY="${ACTION_APPLY:-}"
ACTION_IDS="${ACTION_IDS:-}"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: superseder not present at ${PY_SCRIPT}. Did the VM pull latest main?"
    record_audit "supersede-reset-orphan-artifacts" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi
if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "supersede-reset-orphan-artifacts" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

PY="${REPO_DIR}/.venv/bin/python3"
[ -x "${PY}" ] || PY="python3"

ARGS=(--db "${DB_PATH}")
if [ -n "${ACTION_IDS// }" ]; then
    ARGS+=(--ids "${ACTION_IDS}")
fi
case "${ACTION_APPLY}" in
  true|True)
    echo ">>> supersede-reset-orphan-artifacts: APPLY mode — will write (DB backup taken first)."
    ARGS+=(--apply)
    ;;
  *)
    echo ">>> supersede-reset-orphan-artifacts: DRY-RUN (set apply: true to write)."
    ;;
esac

"${PY}" "${PY_SCRIPT}" "${ARGS[@]}"
