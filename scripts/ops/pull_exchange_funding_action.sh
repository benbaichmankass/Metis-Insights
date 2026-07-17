#!/usr/bin/env bash
# Tier-2 operator action: pull recent Bybit perp FUNDING into the local
# exchange-funding store (runtime_state/exchange_fills.sqlite :: exchange_funding)
# so the broker-truth cost sweep can attribute funding_paid_usd (Slice B / B1,
# MB-20260629-ALLOC-COSTCAP).
#
# Wraps scripts/pull_exchange_funding.py for the REAL-MONEY account bybit_2
# (BYBIT_API_KEY_2 / BYBIT_API_SECRET_2). Sibling of pull-exchange-fills —
# perp funding is not in the execution list, so it needs its own pull.
#
# Read-only on the exchange side (fetch_funding_history). Idempotent — the store
# keys on funding_id, so overlapping windows are safe. Touches NO service, NO
# trade_journal.db table.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

load_runtime_secrets  # BYBIT_API_KEY_2 / BYBIT_API_SECRET_2 from .env
PY_SCRIPT="${REPO_DIR}/scripts/pull_exchange_funding.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: puller not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "pull-exchange-funding" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

echo
echo "===== pull_exchange_funding.py --account bybit_2 --days 30 ====="
python3 "${PY_SCRIPT}" \
    --account bybit_2 \
    --days 30 \
    --api-key-env BYBIT_API_KEY_2 \
    --api-secret-env BYBIT_API_SECRET_2
rc=$?

record_audit "pull-exchange-funding" "$([ ${rc} -eq 0 ] && echo ok || echo error)" \
    "{\"account\": \"bybit_2\", \"days\": 30, \"exit\": ${rc}}" >/dev/null || true
log "pull-exchange-funding complete (exit ${rc})."
exit ${rc}
