#!/usr/bin/env bash
# Tier-2 operator action: pull recent Bybit fills into the local
# exchange-fills store (runtime_state/exchange_fills.sqlite) so the
# exchange-truth P&L surface (/api/bot/pnl/exchange) has data.
#
# Wraps scripts/pull_exchange_fills.py for EVERY live Bybit account
# (--all-bybit-accounts: bybit_1 / bybit_2 / bybit_portfolio, each with
# its own BYBIT_API_KEY_* creds + market_type category from
# config/accounts.yaml). Added 2026-07-13 (BL-20260713-EXCHANGE-FILLS-STORE-EMPTY):
# the puller existed since S-067 but had no timer, no system-action, and
# pulled the spot category only — so the store had never accrued a
# single fill while bybit_2 traded linear perps daily. Multi-account
# rollout (rec #7 broker-truth cost coverage, 2026-07-29) so bybit_1 +
# bybit_portfolio accrue exchange-truth fees too, not just bybit_2.
#
# Read-only on the exchange side (fetch_my_trades). Idempotent — the
# store's primary key is exec_id, so overlapping windows are safe.
# Touches NO service, NO trade_journal.db table.
#
# Window: 7 days (Bybit V5 execution history retention for this
# endpoint comfortably covers it; re-runs over-sample harmlessly).
set -euo pipefail

SCRIPT_NAME="pull_exchange_fills"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

load_runtime_secrets  # every account's BYBIT_API_KEY_* / _SECRET_* from .env
# Canonical, DATA_DIR-anchored fills-store path so the puller writes to the
# SAME absolute file the systemd web-api reader + the offline cost sweep use.
# A fresh SSH wrapper shell does not inherit the systemd DATA_DIR, so without
# this the python child would resolve runtime_state/ repo-relative
# (BL-20260717-FILLS-STORE-PATH-SPLIT).
FILLS_DB="$(fills_store_path)"
PY_SCRIPT="${REPO_DIR}/scripts/pull_exchange_fills.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: puller not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "pull-exchange-fills" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

echo
echo "===== pull_exchange_fills.py --all-bybit-accounts --days 7 ====="
echo "fills store: ${FILLS_DB}"
python3 "${PY_SCRIPT}" \
    --all-bybit-accounts \
    --days 7 \
    --fills-db "${FILLS_DB}"
rc=$?

record_audit "pull-exchange-fills" "$([ ${rc} -eq 0 ] && echo ok || echo error)" \
    "{\"accounts\": \"all-bybit\", \"days\": 7, \"fills_db\": \"${FILLS_DB}\", \"exit\": ${rc}}" >/dev/null || true
log "pull-exchange-fills complete (exit ${rc})."
exit ${rc}
