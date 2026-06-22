#!/usr/bin/env bash
# Tier-2 operator action: normalise existing trades.closed_at epoch-ms rows to
# ISO-8601 (BL-20260620-RECONCILER-CLOSEDAT-MS).
#
# Wraps scripts/ops/migrate_closed_at_to_iso.py. The reconciler-filled close
# path historically wrote Bybit's updatedTime/execTime as a raw epoch-ms string
# into trades.closed_at (and notes.closed_at), while the column contract is
# ISO-8601. The writer was fixed (PR #4168) and the read endpoints guard the ms
# rows (PR #4162); this one-shot rewrites the rows ALREADY persisted in ms form
# so the column is uniformly ISO and the read-side guard becomes belt-and-
# suspenders.
#
# Distinct from `backfill-closed-at`, which fills closed_at IS NULL rows — this
# action converts the OPPOSITE case: closed_at populated as an epoch-ms string.
#
# Two invocations in one run for a self-documenting audit trail:
#   1. dry-run (default)  → prints counts (scanned / ms→ISO) + a sample, no write.
#   2. --apply            → commits the UPDATE inside one transaction.
#
# Idempotent: only all-digit, >=12-char closed_at values are converted, so a
# re-run after an apply is a no-op. Does NOT restart any service.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/migrate_closed_at_to_iso.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: migration helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "migrate-closed-at-iso" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "migrate-closed-at-iso" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

# Count epoch-ms closed_at rows via the python sqlite3 module (the sqlite3 CLI
# isn't on the live VM PATH). Best-effort: prints "?" on any error.
_ms_count() {
    python3 - "${DB_PATH}" <<'PY' 2>/dev/null || echo "?"
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
cols = {r[1] for r in con.execute("PRAGMA table_info(trades)")}
if "closed_at" not in cols:
    print(0); sys.exit()
print(con.execute(
    "SELECT COUNT(*) FROM trades WHERE closed_at IS NOT NULL "
    "AND closed_at GLOB '[0-9]*' AND NOT closed_at GLOB '*[^0-9]*' "
    "AND length(closed_at) >= 12"
).fetchone()[0])
PY
}

pre_ms="$(_ms_count)"
log "Closed rows with epoch-ms closed_at (pre-migration): ${pre_ms}"

echo
echo "===== migrate_closed_at_to_iso.py (DRY-RUN preview) ====="
set +e
python3 "${PY_SCRIPT}" --db "${DB_PATH}"
dry_code=$?
set -e
if [ "${dry_code}" -ne 0 ]; then
    log "ERROR: dry-run preview exited ${dry_code} — NOT applying."
    record_audit "migrate-closed-at-iso" "failed" \
        "{\"phase\": \"dry-run\", \"exit_code\": ${dry_code}}" >/dev/null || true
    exit "${dry_code}"
fi

echo
echo "===== migrate_closed_at_to_iso.py --apply (COMMIT) ====="
set +e
python3 "${PY_SCRIPT}" --db "${DB_PATH}" --apply
apply_code=$?
set -e

post_ms="$(_ms_count)"
log "Post-migration: epoch-ms closed_at=${post_ms}"

if [ "${apply_code}" -ne 0 ]; then
    record_audit "migrate-closed-at-iso" "failed" \
        "{\"phase\": \"apply\", \"pre_ms\": \"${pre_ms}\", \"post_ms\": \"${post_ms}\", \"exit_code\": ${apply_code}}" \
        >/dev/null || true
    log "ERROR: --apply exited ${apply_code}."
    exit "${apply_code}"
fi

record_audit "migrate-closed-at-iso" "ok" \
    "{\"pre_ms\": \"${pre_ms}\", \"post_ms\": \"${post_ms}\"}" \
    >/dev/null || true
log "Migration complete. epoch-ms closed_at ${pre_ms} → ${post_ms}."
exit 0
