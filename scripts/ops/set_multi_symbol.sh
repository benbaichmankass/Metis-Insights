#!/usr/bin/env bash
# operator-action `enable-mes`: turn on multi-symbol trading (BTCUSDT + MES)
# on the live VM, then restart the trader so it picks up the new env.
#
# Sets two env vars in the trader's .env (loaded by src/main.py's
# load_dotenv()):
#   MULTI_SYMBOL_ENABLED=true   — activates the per-symbol tick loop
#   SYMBOLS=BTCUSDT,MES         — symbols iterated each tick
#
# The symbol→exchange routing gate keeps BTCUSDT on bybit and MES on IB, so
# enabling this never sends a crypto signal to the IB account. MES requires
# the IB Gateway to be up (provision-ib-gateway); if it is down, MES candle
# fetch returns None and MES simply idles — crypto is unaffected.
#
# Idempotent: re-running just re-asserts the values + restarts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

ENV_FILE="${TRADER_ENV_FILE:-/home/ubuntu/ict-trading-bot/.env}"
SYMBOLS_VALUE="${SYMBOLS_VALUE:-BTCUSDT,MES}"

upsert() {
  local key="$1" val="$2"
  touch "${ENV_FILE}"
  if grep -qE "^${key}=" "${ENV_FILE}"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "${ENV_FILE}"
  else
    printf '%s=%s\n' "${key}" "${val}" >> "${ENV_FILE}"
  fi
}

echo "[enable-mes] setting MULTI_SYMBOL_ENABLED=true SYMBOLS=${SYMBOLS_VALUE} in ${ENV_FILE}"
upsert MULTI_SYMBOL_ENABLED true
upsert SYMBOLS "${SYMBOLS_VALUE}"

echo "[enable-mes] current values:"
grep -E "^(MULTI_SYMBOL_ENABLED|SYMBOLS)=" "${ENV_FILE}" || true

echo "[enable-mes] restarting ict-trader-live.service"
sudo systemctl restart ict-trader-live.service
sleep 3
sudo systemctl --no-pager --full status ict-trader-live.service | head -12 || true
echo "[enable-mes] done — MES (delayed data) will trade once the IB Gateway is logged in."
