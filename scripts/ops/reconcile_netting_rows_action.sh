#!/usr/bin/env bash
# Tier-2 operator action: general same-moment netting partial-close reconcile
# (BL-20260801-NETTING-PARTIAL-CLOSE-ROWS-NEVER-REDUCED, option (c)+(b),
# operator-approved 2026-08-02).
#
# The generalization of the signature-pinned one-shot reconcile-netting-phantom-rows.
# Under Bybit one-way netting several journal `trades` rows share ONE exchange
# position; a PARTIAL (non-flat) shrink is attributed to at most one row and the
# siblings survive at full size, inflating the journal and suppressing
# netting-guard re-entries. The live `journal_qty_divergent` sweep DETECTS every
# instance per tick but remediates nothing — this action does.
#
# TWO STEPS on the VM:
#   1. netting_reconcile_snapshot.py reads the OPEN non-pairs journal groups +
#      the LIVE per-account exchange positions (account_open_positions — the same
#      read /api/diag/exchange_positions uses) + the Bybit resting protective-leg
#      ids, and writes the engine's same-moment input JSON
#      ({account/symbol/direction -> {size, resting_legs}}).
#   2. reconcile_netting_rows.py consumes that snapshot and closes the SURPLUS
#      open rows so each group's open sum matches the broker's netted size —
#      status='closed' + reconcile_status='superseded' +
#      exit_reason='netting_partial_reconciled', pnl/exit_price left NULL
#      (UNMEASURED — the real closes happened inside position-level exits at
#      unknown moments, never mark-priced), full provenance under
#      notes.netting_partial_reconcile.
#
# FAIL-SAFE (honoured by both steps): an account that could-not-read is OMITTED
# from the snapshot -> the engine SKIPS its groups (never close on an unconfirmed
# broker read); pairs-sleeve rows are excluded; never more than the surplus is
# closed (a straddling row is KEPT). The real-money bybit_2 book was verified
# CLEAN on 2026-08-01 — this must not introduce churn there, and the fail-safes
# guarantee it can only ever reduce a genuine surplus.
#
# DRY-RUN by default; pass apply: true (issue body) to write. Idempotent — a
# re-run finds the surplus already gone. No service restart.
set -euo pipefail

SCRIPT_NAME="reconcile_netting_rows"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

heal_devnull || true
# account_open_positions opens a live Bybit read client -> needs exchange creds.
# The wrapper runs from a fresh SSH shell and does NOT inherit the trader unit's
# EnvironmentFile, so source .env in full (same rationale as the fills pullers).
load_runtime_secrets

DB_PATH="$(runtime_db_path)"
SNAPSHOT_SCRIPT="${REPO_DIR}/scripts/ops/netting_reconcile_snapshot.py"
ENGINE_SCRIPT="${REPO_DIR}/scripts/ops/reconcile_netting_rows.py"

for f in "${SNAPSHOT_SCRIPT}" "${ENGINE_SCRIPT}"; do
    if [ ! -f "${f}" ]; then
        log "ERROR: helper not present at ${f}. Did the VM pull the latest main?"
        record_audit "reconcile-netting-rows" "error" \
            "{\"reason\": \"helper missing\", \"path\": \"${f}\"}" >/dev/null || true
        exit 1
    fi
done

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "reconcile-netting-rows" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

# ACTION_APPLY gates dry-run (default) vs the real write.
APPLY_FLAG=""
case "${ACTION_APPLY:-}" in
    true|True) APPLY_FLAG="--apply" ;;
    *)         APPLY_FLAG="" ;;
esac

# Step 1: build the same-moment exchange snapshot from a live broker read.
EXCH_JSON="$(mktemp "${TMPDIR:-/tmp}/netting_exch.XXXXXX.json")"
trap 'rm -f "${EXCH_JSON}"' EXIT

log "Building same-moment exchange snapshot (live broker read) on ${DB_PATH} …"
echo
echo "===== netting_reconcile_snapshot.py (live exchange read) ====="
set +e
python3 "${SNAPSHOT_SCRIPT}" --db "${DB_PATH}" --out "${EXCH_JSON}"
snap_code=$?
set -e
if [ "${snap_code}" -ne 0 ]; then
    record_audit "reconcile-netting-rows" "failed" \
        "{\"stage\": \"snapshot\", \"exit_code\": ${snap_code}}" >/dev/null || true
    log "ERROR: snapshot builder exited ${snap_code}."
    exit "${snap_code}"
fi
echo "----- snapshot (account/symbol/direction -> size, resting_legs) -----"
cat "${EXCH_JSON}"
echo

# Step 2: run the reconcile engine against the snapshot.
if [ -n "${APPLY_FLAG}" ]; then
    log "Running reconcile_netting_rows.py --apply (Tier-2 DB write) on ${DB_PATH} …"
else
    log "Running reconcile_netting_rows.py DRY RUN (pass apply: true to write) on ${DB_PATH} …"
fi
echo
echo "===== reconcile_netting_rows.py ${APPLY_FLAG:-(dry run)} ====="
set +e
python3 "${ENGINE_SCRIPT}" --db "${DB_PATH}" --exchange-json "${EXCH_JSON}" ${APPLY_FLAG}
exit_code=$?
set -e

if [ "${exit_code}" -ne 0 ]; then
    record_audit "reconcile-netting-rows" "failed" \
        "{\"stage\": \"engine\", \"apply\": \"${ACTION_APPLY:-}\", \"exit_code\": ${exit_code}}" >/dev/null || true
    log "ERROR: reconcile engine exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "reconcile-netting-rows" "ok" \
    "{\"apply\": \"${ACTION_APPLY:-}\"}" >/dev/null || true
log "reconcile-netting-rows complete (apply=${ACTION_APPLY:-false})."
exit 0
