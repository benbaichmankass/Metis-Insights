#!/usr/bin/env bash
# Tier-2 operator action: backfill trades.account_class from
# config/accounts.yaml.
#
# Wraps scripts/ops/backfill_account_class.py. The account_class column
# (paper | real_money) was added 2026-06-15 as the single source of truth
# for the paper/real-money reporting axis. Rows written before then have
# account_class IS NULL and only the legacy is_demo boolean. Worse, the
# pre-fix ib_paper account carried NO category stamp, so its PAPER trades
# were journaled is_demo=0 (indistinguishable from real money) and
# polluted the real-money journal / PnL — this action CORRECTS those rows.
#
# Two invocations in one run for a self-documenting audit trail:
#   1. dry-run (no flag)  → prints the per-account before/after table.
#   2. --apply            → commits the UPDATE inside one transaction and
#                            keeps is_demo in sync (= paper).
#
# Idempotent: re-running once every row already carries the correct
# account_class is a no-op (0 changes). Does NOT restart any service. The
# Python helper self-ensures the account_class column (idempotent ALTER),
# so this wrapper needs no sqlite3 CLI (which isn't on the live VM PATH).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/backfill_account_class.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: backfill helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "backfill-account-class" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "backfill-account-class" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

# NULL-account_class count via the python sqlite3 module (NOT the sqlite3
# CLI, which isn't on PATH). Best-effort: prints "?" on any error. The
# column may not exist yet on a brand-new DB; treat that as "all NULL".
_null_count() {
    python3 - "${DB_PATH}" <<'PY' 2>/dev/null || echo "?"
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
cols = {r[1] for r in con.execute("PRAGMA table_info(trades)")}
if "account_class" not in cols:
    print(con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]); sys.exit()
print(con.execute("SELECT COUNT(*) FROM trades WHERE account_class IS NULL").fetchone()[0])
PY
}

pre_null="$(_null_count)"
log "Rows with NULL account_class (pre-backfill): ${pre_null}"

echo
echo "===== backfill_account_class.py (DRY-RUN preview) ====="
set +e
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}"
dry_code=$?
set -e
if [ "${dry_code}" -ne 0 ]; then
    log "ERROR: dry-run preview exited ${dry_code} — NOT applying."
    record_audit "backfill-account-class" "failed" \
        "{\"phase\": \"dry-run\", \"exit_code\": ${dry_code}}" >/dev/null || true
    exit "${dry_code}"
fi

echo
echo "===== backfill_account_class.py --apply (COMMIT) ====="
set +e
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}" --apply
apply_code=$?
set -e

post_null="$(_null_count)"
log "Post-backfill: NULL account_class=${post_null}"

if [ "${apply_code}" -ne 0 ]; then
    record_audit "backfill-account-class" "failed" \
        "{\"phase\": \"apply\", \"pre_null\": \"${pre_null}\", \"post_null\": \"${post_null}\", \"exit_code\": ${apply_code}}" \
        >/dev/null || true
    log "ERROR: --apply exited ${apply_code}."
    exit "${apply_code}"
fi

record_audit "backfill-account-class" "ok" \
    "{\"pre_null\": \"${pre_null}\", \"post_null\": \"${post_null}\"}" \
    >/dev/null || true
log "Backfill complete. NULL account_class ${pre_null} → ${post_null}."
exit 0
