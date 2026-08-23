#!/usr/bin/env bash
# Tier-2 operator action: backfill trades.exit_reason on rows that were PRICED
# after they were closed, gated on the price's own provenance.
#
# Wraps scripts/ops/backfill_exit_labels.py.
#
# WHY. `_close_trade_from_order_status`'s no-record fallback hard-codes
# exit_reason='reconciler_filled' and leaves exit_price NULL — correctly, since
# at that moment there is no price to classify against. Two sweeps later supply
# the price. Until #10151 the Bybit-truth sweep left the label frozen; until its
# sibling commit, so did the anchored-price sweep. Every row priced before those
# fixes still carries the generic label.
#
# WHAT IT REFUSES TO DO. The classifier is provenance-BLIND: it compares a price
# to the package bracket and cannot know whether that price is a broker fill or
# `local_markprice` — the market read at SWEEP time, hours after the exit.
# Classifying the latter would manufacture an sl/tp verdict out of unrelated
# later price action. So a FABRICATED-price row is stamped `refused_unmeasured_price`
# and its label is left alone. Measured 2026-08-23: 105 of 497 eligible rows.
#
# Touches NO monetary field — not pnl, not exit_price. Writes one label plus its
# provenance, and records the prior value under notes.pre_backfill_exit_reason,
# so the operation is reversible from the row itself.
#
# Two invocations in one run for a self-documenting audit trail:
#   1. dry-run (no flag) → prints the full plan and its denominators.
#   2. --apply           → writes.
#
# Idempotent: a row already carrying `exit_reason_source` is skipped, so
# re-running is a no-op. Does NOT restart any service.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/backfill_exit_labels.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "backfill-exit-labels" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi
if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "backfill-exit-labels" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

# Count rows still carrying the generic label with no classifier stamp. Uses the
# python sqlite3 module, NOT the sqlite3 CLI (which isn't on the live VM PATH).
_generic_count() {
    python3 - "${DB_PATH}" <<'PY' 2>/dev/null || echo "?"
import sqlite3, sys
con = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
print(con.execute(
    "SELECT COUNT(*) FROM trades "
    " WHERE status='closed' AND COALESCE(is_backtest,0)=0 "
    "   AND COALESCE(exit_reason,'') IN ('','reconciler_filled') "
    "   AND COALESCE(notes,'') NOT LIKE '%\"exit_reason_source\"%'"
).fetchone()[0])
PY
}

# The self-test is a PRECONDITION, not decoration: this writes to the money DB,
# and a classifier that cannot be shown able to fire must not be trusted to
# relabel 191 rows of history.
echo "===== backfill_exit_labels.py --self-test (planted controls) ====="
set +e
python3 "${PY_SCRIPT}" --self-test
st_code=$?
set -e
if [ "${st_code}" -ne 0 ]; then
    log "ERROR: self-test exited ${st_code} — NOT applying."
    record_audit "backfill-exit-labels" "failed" \
        "{\"phase\": \"self-test\", \"exit_code\": ${st_code}}" >/dev/null || true
    exit "${st_code}"
fi

pre="$(_generic_count)"
log "Rows generic + unstamped (pre-backfill): ${pre}"

echo
echo "===== backfill_exit_labels.py (DRY-RUN plan) ====="
set +e
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}" --db "${DB_PATH}"
dry_code=$?
set -e
if [ "${dry_code}" -ne 0 ]; then
    log "ERROR: dry run exited ${dry_code} — NOT applying."
    record_audit "backfill-exit-labels" "failed" \
        "{\"phase\": \"dry-run\", \"exit_code\": ${dry_code}}" >/dev/null || true
    exit "${dry_code}"
fi

echo
echo "===== backfill_exit_labels.py --apply (COMMIT) ====="
set +e
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}" --db "${DB_PATH}" --apply
apply_code=$?
set -e

post="$(_generic_count)"
log "Post-backfill: generic + unstamped=${post}"

if [ "${apply_code}" -ne 0 ]; then
    record_audit "backfill-exit-labels" "failed" \
        "{\"phase\": \"apply\", \"pre\": \"${pre}\", \"post\": \"${post}\", \"exit_code\": ${apply_code}}" \
        >/dev/null || true
    log "ERROR: --apply exited ${apply_code}."
    exit "${apply_code}"
fi

record_audit "backfill-exit-labels" "ok" \
    "{\"pre\": \"${pre}\", \"post\": \"${post}\"}" >/dev/null || true
log "Backfill complete. Generic+unstamped ${pre} → ${post}."
exit 0
