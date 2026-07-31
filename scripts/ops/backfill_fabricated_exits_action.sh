#!/usr/bin/env bash
# Operator action: re-derive FABRICATED exit prices from the broker fills the
# system has been storing all along (BL-20260730-BROKER-TRUTH-COLLECTED-NEVER-READ).
#
# Wraps scripts/ops/backfill_fabricated_exits.py. Matches each fabricated /
# unverified closed row against `exchange_fills` on account+symbol+side+window
# via the same `exit_from_fills` the LIVE close path uses, so a repaired row is
# reached by exactly the route a fresh close takes.
#
# WHY THIS IS NOT A "RELABEL ONLY" VIOLATION. That rule exists because IBKR's
# execution history is short-lived (reqExecutions ~ the current trading day), so
# for IB the evidence is genuinely gone. Bybit's is NOT — it is on disk, pulled
# daily. Applying the IB constraint to Bybit was an over-generalisation of a
# venue-specific limit.
#
# WHAT IT REPAIRS, and why waiting made it worse: fabrication is a REGRESSION
# WITH A START DATE, not drift. bybit_1 ran 0/47 (May, 0.0%) -> 28/124 (Jun,
# 22.6%) -> 126/155 (Jul, 81.3%) on the same code path; the exit-source mix went
# from bybit_closed_pnl x187 to local_markprice x161 at the June boundary — when
# BL-20260608-DEMOPNL added the `if is_demo: return None` dead-end. Every demo
# close since then landed on a mark, so the damage compounds per trade.
#
# TWO TIERS, kept apart on purpose:
#   own fills   -> exit_price_source='exchange_fill'        MEASURED
#   mirror fill -> exit_price_source='mirror_account_fill'  ESTIMATED
# The mirror tier (bybit_portfolio<-bybit_2, alpaca_portfolio<-alpaca_live) is
# only reached with --allow-mirror. It is ESTIMATED and must stay that way: the
# paper book mirrors the live book's SETUPS, so the sibling's fill is a
# defensible anchor, but it is an inference about a DIFFERENT account's
# execution — capacity between the books differs, which is exactly why it is not
# a measurement. qty is never copied.
#
# SAFETY. Dry-run is the default and is what this action runs unless APPLY=1.
# Only FABRICATED/UNVERIFIED rows are candidates, so it can only improve
# provenance and never overwrites a measured row. Each write records
# notes.backfill with the prior source + run id (auditable, reversible). Rows
# that cannot be resolved are LEFT ALONE, never guessed. `pnl` is NOT recomputed
# here — the monitor's local sweep re-derives it from the corrected exit on its
# next tick, through the same path a live close uses. Touches no order path and
# restarts no service.
#
# APPLY=1 makes this a Tier-2 money-DB write and requires an operator OK.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
# Canonical DATA_DIR-anchored path. A fresh SSH wrapper shell does not inherit
# systemd's DATA_DIR, so resolving runtime_state/ repo-relative would report
# "fills store not found" (BL-20260717-FILLS-STORE-PATH-SPLIT). Do not hardcode.
FILLS_DB="$(fills_store_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/backfill_fabricated_exits.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "backfill-fabricated-exits" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi
if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "backfill-fabricated-exits" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi
if [ ! -f "${FILLS_DB}" ]; then
    log "ERROR: fills store not present at ${FILLS_DB}. Run pull-exchange-fills first."
    record_audit "backfill-fabricated-exits" "error" \
        "{\"reason\": \"fills store missing\", \"path\": \"${FILLS_DB}\"}" >/dev/null || true
    exit 1
fi

echo "db:          ${DB_PATH}"
echo "fills store: ${FILLS_DB}"

echo
echo "===== TIER 1 ONLY — own fills (MEASURED). DRY RUN, no writes. ====="
set +e
python3 "${PY_SCRIPT}" --db "${DB_PATH}" --fills "${FILLS_DB}"
own_code=$?
set -e

echo
echo "===== TIER 1 + TIER 2 — incl. mirror account (ESTIMATED). DRY RUN. ====="
echo "The delta between this and the block above IS the mirror-only count."
set +e
python3 "${PY_SCRIPT}" --db "${DB_PATH}" --fills "${FILLS_DB}" --allow-mirror
mirror_code=$?
set -e

if [ "${own_code}" -ne 0 ] || [ "${mirror_code}" -ne 0 ]; then
    log "ERROR: a dry run exited non-zero (own=${own_code} mirror=${mirror_code}) — NOT applying."
    record_audit "backfill-fabricated-exits" "error" \
        "{\"own_rc\": ${own_code}, \"mirror_rc\": ${mirror_code}}" >/dev/null || true
    exit 1
fi

case "${ACTION_APPLY:-}" in
    1|true|TRUE|yes) DO_APPLY=1 ;;
    *)                DO_APPLY=0 ;;
esac
case "${ACTION_ALLOW_MIRROR:-}" in
    1|true|TRUE|yes) ALLOW_MIRROR=1 ;;
    *)                ALLOW_MIRROR=0 ;;
esac

if [ "${DO_APPLY}" != "1" ]; then
    echo
    echo "DRY RUN ONLY. Nothing was written."
    echo "Re-dispatch with an 'apply: 1' body line to commit (Tier-2, operator OK required)."
    record_audit "backfill-fabricated-exits" "ok" '{"mode": "dry_run"}' >/dev/null || true
    exit 0
fi

echo
echo "===== APPLYING (Tier-2 money-DB write) ====="
# ALLOW_MIRROR must be opted into separately even under APPLY: the MEASURED tier
# and the ESTIMATED tier are different claims and should be committable apart.
MIRROR_FLAG=""
if [ "${ALLOW_MIRROR}" = "1" ]; then
    MIRROR_FLAG="--allow-mirror"
    echo "including the ESTIMATED mirror tier"
else
    echo "own fills only (MEASURED); pass allow_mirror: 1 to include the mirror tier"
fi
python3 "${PY_SCRIPT}" --db "${DB_PATH}" --fills "${FILLS_DB}" --apply ${MIRROR_FLAG}
record_audit "backfill-fabricated-exits" "ok" \
    "{\"mode\": \"apply\", \"allow_mirror\": \"${ALLOW_MIRROR}\"}" >/dev/null || true
echo "DONE."
