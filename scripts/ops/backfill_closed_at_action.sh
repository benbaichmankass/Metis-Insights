#!/usr/bin/env bash
# Tier-2 operator action: backfill trades.closed_at (and, in the same pass,
# trades.account_class) for historical closed rows (dashboard-truth P1-E).
#
# Wraps scripts/ops/backfill_closed_at.py. The canonical closed_at column
# (added 2026-06-16, P1-B) is the single source of truth for a trade's close
# timestamp — every close path now stamps it going forward. Rows that closed
# BEFORE the column existed have closed_at IS NULL and the read path derives
# the value on the fly (linked order_packages.updated_at, else notes.closed_at).
# This one-shot repair writes that same derived value into the column so old
# data matches the new write-path — no more on-the-fly derivation divergence.
#
# Widest-scope (operator directive 2026-06-17 "backfill historical orders as
# accurately as possible"): runs with --also-account-class so the SAME action
# also repairs any rows still missing the paper/real_money category stamp,
# delegating to backfill_account_class.py. A single operator action closes both
# historical-persistence gaps in one audited pass.
#
# Two invocations in one run for a self-documenting audit trail:
#   1. dry-run (no flag)  → prints counts (scanned / fillable / left-NULL) +
#                            a sample of what WOULD change, exits WITHOUT writing.
#   2. --apply            → commits the UPDATE inside one transaction.
#
# Idempotent: only closed_at IS NULL rows are SELECTed and the UPDATE re-asserts
# AND closed_at IS NULL, so a re-run after an apply is a no-op. Does NOT restart
# any service. The Python helper self-ensures the closed_at column (idempotent
# ALTER), so this wrapper needs no sqlite3 CLI (which isn't on the live VM PATH).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/backfill_closed_at.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: backfill helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "backfill-closed-at" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "backfill-closed-at" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

# NULL-closed_at count via the python sqlite3 module (NOT the sqlite3 CLI,
# which isn't on PATH). Best-effort: prints "?" on any error. The column may
# not exist yet on a brand-new DB; treat that as "all closed rows NULL".
_null_count() {
    python3 - "${DB_PATH}" <<'PY' 2>/dev/null || echo "?"
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
cols = {r[1] for r in con.execute("PRAGMA table_info(trades)")}
if "closed_at" not in cols:
    print(con.execute(
        "SELECT COUNT(*) FROM trades WHERE status='closed' AND COALESCE(is_backtest,0)=0"
    ).fetchone()[0]); sys.exit()
print(con.execute(
    "SELECT COUNT(*) FROM trades "
    "WHERE status='closed' AND closed_at IS NULL AND COALESCE(is_backtest,0)=0"
).fetchone()[0])
PY
}

pre_null="$(_null_count)"
log "Closed rows with NULL closed_at (pre-backfill): ${pre_null}"

echo
echo "===== backfill_closed_at.py (DRY-RUN preview, +account_class) ====="
set +e
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}" --also-account-class
dry_code=$?
set -e
if [ "${dry_code}" -ne 0 ]; then
    log "ERROR: dry-run preview exited ${dry_code} — NOT applying."
    record_audit "backfill-closed-at" "failed" \
        "{\"phase\": \"dry-run\", \"exit_code\": ${dry_code}}" >/dev/null || true
    exit "${dry_code}"
fi

echo
echo "===== backfill_closed_at.py --apply (COMMIT, +account_class) ====="
set +e
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}" --also-account-class --apply
apply_code=$?
set -e

post_null="$(_null_count)"
log "Post-backfill: NULL closed_at=${post_null}"

if [ "${apply_code}" -ne 0 ]; then
    record_audit "backfill-closed-at" "failed" \
        "{\"phase\": \"apply\", \"pre_null\": \"${pre_null}\", \"post_null\": \"${post_null}\", \"exit_code\": ${apply_code}}" \
        >/dev/null || true
    log "ERROR: --apply exited ${apply_code}."
    exit "${apply_code}"
fi

record_audit "backfill-closed-at" "ok" \
    "{\"pre_null\": \"${pre_null}\", \"post_null\": \"${post_null}\"}" \
    >/dev/null || true
log "Backfill complete. NULL closed_at ${pre_null} → ${post_null}."
exit 0
