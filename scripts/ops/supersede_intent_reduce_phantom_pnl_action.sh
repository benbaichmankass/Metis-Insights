#!/usr/bin/env bash
# system-action wrapper: void-flag historical INTENT-REDUCE phantom-PnL rows
# (one-shot journal hygiene — BL-20260711, PR #6926 follow-up).
#
# Runs scripts/ops/supersede_intent_reduce_phantom_pnl.py on the live VM.
# DRY-RUN by default — prints the matched reduce-leg rows carrying a non-NULL
# pnl (id / symbol / entry==exit signature / REAL vs paper / fabricated PnL)
# WITHOUT writing. Only writes when ACTION_APPLY is true, and takes a
# timestamped DB backup first.
#
# Background: before PR #6926 the reconciler write-back + the mark-to-market
# sweep booked a non-NULL pnl onto an `intent_reduce` bookkeeping leg. On a
# netting account the qty-matched closed_pnl is the PARENT position's realized
# close, so it was attributed onto the reduce leg with an entry==exit signature
# — a fabricated win/loss (trend_donchian demo rows 2604/2607/2610 at
# +$561/+620/+898). PR #6926 stops NEW phantoms at the source (reduce-leg pnl
# stays NULL; the sweep skips reduce legs); this cleans the historical rows.
#
# Env (passed by system-actions.yml):
#   ACTION_APPLY - "true" to write (backup taken first); else = dry-run report
#   ACTION_IDS   - optional comma-separated trade-id allowlist to restrict the match
#   ACTION_EQUAL_ONLY - "true" to restrict to the ironclad entry==exit rows
#
# WHY apply is gated: a live apply void-flags rows on the canonical money DB
# (real-money `bybit_2` rows included — the phantom is account-agnostic), so
# run dry-run first, eyeball the matched rows (real-money ones are reported
# prominently), then apply with operator approval. Pure journal hygiene: it
# void-flags ONLY the bookkeeping reduce leg (never the parent close that
# carries the real pnl), opens/closes no exchange position, deletes no row.
#
# Idempotent. Safe to re-run — rows already reconcile_status='superseded' are
# skipped by the SQL guard.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/supersede_intent_reduce_phantom_pnl.py"
ACTION_APPLY="${ACTION_APPLY:-}"
ACTION_IDS="${ACTION_IDS:-}"
ACTION_EQUAL_ONLY="${ACTION_EQUAL_ONLY:-}"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: superseder not present at ${PY_SCRIPT}. Did the VM pull latest main?"
    record_audit "supersede-intent-reduce-phantom-pnl" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi
if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "supersede-intent-reduce-phantom-pnl" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

PY="${REPO_DIR}/.venv/bin/python3"
[ -x "${PY}" ] || PY="python3"

ARGS=(--db "${DB_PATH}")
if [ -n "${ACTION_IDS// }" ]; then
    ARGS+=(--ids "${ACTION_IDS}")
fi
case "${ACTION_EQUAL_ONLY}" in
  true|True) ARGS+=(--equal-only) ;;
esac
case "${ACTION_APPLY}" in
  true|True)
    echo ">>> supersede-intent-reduce-phantom-pnl: APPLY mode — will write (DB backup taken first)."
    ARGS+=(--apply)
    ;;
  *)
    echo ">>> supersede-intent-reduce-phantom-pnl: DRY-RUN (set apply: true to write)."
    ;;
esac

"${PY}" "${PY_SCRIPT}" "${ARGS[@]}"
