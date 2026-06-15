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
# account_class is a no-op (0 changes). Does NOT restart any service.
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

# Defensive column-ensure. The deployed code adds account_class lazily on
# Database() init (create_tables → _migrate_add_account_class), so after a
# normal deploy+restart the column already exists. But if this action runs
# before the trader/web-api has re-initialised, the helper's SELECT would
# fail — so add the column here, idempotently, first. (ALTER TABLE ... ADD
# COLUMN has no IF NOT EXISTS in SQLite, hence the PRAGMA guard.)
has_col="$(sqlite3 "${DB_PATH}" \
    "SELECT COUNT(*) FROM pragma_table_info('trades') WHERE name='account_class';" \
    2>/dev/null || echo "?")"
if [ "${has_col}" = "0" ]; then
    log "account_class column absent — adding it (idempotent migration)."
    sqlite3 "${DB_PATH}" "ALTER TABLE trades ADD COLUMN account_class TEXT;" || {
        log "ERROR: failed to add account_class column."
        record_audit "backfill-account-class" "error" \
            "{\"reason\": \"alter failed\"}" >/dev/null || true
        exit 1
    }
elif [ "${has_col}" != "1" ]; then
    log "WARN: could not confirm account_class column presence (sqlite3 returned '${has_col}'); proceeding — the helper will error cleanly if it's truly absent."
fi

# Pre-snapshot: rows still missing a category (NULL account_class).
pre_null="$(sqlite3 "${DB_PATH}" \
    "SELECT COUNT(*) FROM trades WHERE account_class IS NULL;" 2>/dev/null || echo "?")"
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

post_null="$(sqlite3 "${DB_PATH}" \
    "SELECT COUNT(*) FROM trades WHERE account_class IS NULL;" 2>/dev/null || echo "?")"
post_paper="$(sqlite3 "${DB_PATH}" \
    "SELECT COUNT(*) FROM trades WHERE account_class='paper';" 2>/dev/null || echo "?")"
log "Post-backfill: NULL account_class=${post_null}, paper rows=${post_paper}"

if [ "${apply_code}" -ne 0 ]; then
    record_audit "backfill-account-class" "failed" \
        "{\"phase\": \"apply\", \"pre_null\": \"${pre_null}\", \"post_null\": \"${post_null}\", \"exit_code\": ${apply_code}}" \
        >/dev/null || true
    log "ERROR: --apply exited ${apply_code}."
    exit "${apply_code}"
fi

record_audit "backfill-account-class" "ok" \
    "{\"pre_null\": \"${pre_null}\", \"post_null\": \"${post_null}\", \"post_paper\": \"${post_paper}\"}" \
    >/dev/null || true
log "Backfill complete. NULL account_class ${pre_null} → ${post_null}; paper rows now ${post_paper}."
exit 0
