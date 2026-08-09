#!/usr/bin/env bash
# Tier-2 operator action: pull recent Bybit perp FUNDING into the local
# exchange-funding store (runtime_state/exchange_fills.sqlite :: exchange_funding)
# so the broker-truth cost sweep can attribute funding_paid_usd (Slice B / B1,
# MB-20260629-ALLOC-COSTCAP).
#
# Wraps scripts/pull_exchange_funding.py for EVERY live Bybit account
# (--all-bybit-accounts: bybit_1 / bybit_2 / bybit_portfolio, each with its own
# BYBIT_API_KEY_* creds + declared symbols from config/accounts.yaml). Sibling
# of pull-exchange-fills — perp funding is not in the execution list, so it
# needs its own pull.
#
# Read-only on the exchange side (fetch_funding_history). Idempotent — the store
# keys on funding_id, so overlapping windows are safe. Touches NO service, NO
# trade_journal.db table.
#
# Window: ACTION_DAYS (default 30). Previously hardcoded to 30 with no override,
# which made the one measurement that matters impossible to take: the sibling
# fills puller was measured on 2026-08-08 returning a 7-DAY slice for a 90-day
# request, because Bybit V5 caps the queryable RANGE (while retaining 2 years)
# and MOVES the window to the old end rather than widening it. If the same cap
# applies to funding, this action's 30-day request has been serving
# [now-30d, now-23d] — silently missing the most RECENT three weeks while
# printing a healthy row count. That is UNVERIFIED for this endpoint
# (BL-20260808-FUNDING-PULLER-SAME-RANGE-CAP-EXPOSURE); a selectable window is
# what makes it testable, by the same monotonicity argument that caught the
# fills bug — a wider window cannot legitimately hold FEWER rows than a
# narrower one nested inside it.
#
# The puller also now logs the span it was actually SERVED beside the one it
# asked for, so the next ordinary nightly run is itself evidence and no probe
# may even be needed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

load_runtime_secrets  # every account's BYBIT_API_KEY_* / _SECRET_* from .env
# Canonical, DATA_DIR-anchored fills-store path (holds the exchange_funding
# table) so the funding puller writes to the SAME absolute file the cost sweep
# reads — a fresh SSH wrapper shell doesn't inherit systemd's DATA_DIR
# (BL-20260717-FILLS-STORE-PATH-SPLIT).
FILLS_DB="$(fills_store_path)"
# Window override, validated as a positive integer before it reaches the CLI —
# same shape as pull_exchange_fills_action.sh.
DAYS="${ACTION_DAYS:-30}"
case "${DAYS}" in
    ''|*[!0-9]*)
        log "ERROR: ACTION_DAYS='${DAYS}' is not a positive integer."
        exit 1
        ;;
esac
if [ "${DAYS}" -lt 1 ]; then
    log "ERROR: ACTION_DAYS='${DAYS}' must be >= 1."
    exit 1
fi
PY_SCRIPT="${REPO_DIR}/scripts/pull_exchange_funding.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: puller not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "pull-exchange-funding" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

# --all-bybit-accounts pulls each live Bybit account's OWN declared symbols
# (config/accounts.yaml::symbols) — Bybit V5 funding is retrieved per contract,
# so the all-symbols (symbol=None) query returns 0 records
# (BL-20260717-FUNDING-ZERO). Funding only accrues on symbols the account
# actually held across an 8h funding timestamp; a symbol with no such position
# just returns empty. Config-driven — no hard-coded symbol list to drift.
echo
echo "===== pull_exchange_funding.py --all-bybit-accounts --days ${DAYS} ====="
echo "funding store: ${FILLS_DB}"
python3 "${PY_SCRIPT}" \
    --all-bybit-accounts \
    --days "${DAYS}" \
    --fills-db "${FILLS_DB}"
rc=$?

record_audit "pull-exchange-funding" "$([ ${rc} -eq 0 ] && echo ok || echo error)" \
    "{\"accounts\": \"all-bybit\", \"days\": ${DAYS}, \"fills_db\": \"${FILLS_DB}\", \"exit\": ${rc}}" >/dev/null || true
log "pull-exchange-funding complete (exit ${rc})."
exit ${rc}
