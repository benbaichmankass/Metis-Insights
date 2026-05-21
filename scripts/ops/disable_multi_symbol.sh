#!/usr/bin/env bash
# operator-action `disable-mes`: turn OFF multi-symbol trading and restart the
# trader, reverting to the single-symbol (BTCUSDT-only) path. The crypto
# trader is unaffected by the flip either way — this just stops the MES tick.
set -euo pipefail

ENV_FILE="${TRADER_ENV_FILE:-/home/ubuntu/ict-trading-bot/.env}"

if grep -qE "^MULTI_SYMBOL_ENABLED=" "${ENV_FILE}" 2>/dev/null; then
  sed -i "s|^MULTI_SYMBOL_ENABLED=.*|MULTI_SYMBOL_ENABLED=false|" "${ENV_FILE}"
else
  printf 'MULTI_SYMBOL_ENABLED=false\n' >> "${ENV_FILE}"
fi

echo "[disable-mes] MULTI_SYMBOL_ENABLED set to false in ${ENV_FILE}"
grep -E "^(MULTI_SYMBOL_ENABLED|SYMBOLS)=" "${ENV_FILE}" || true

echo "[disable-mes] restarting ict-trader-live.service"
sudo systemctl restart ict-trader-live.service
sleep 3
sudo systemctl --no-pager --full status ict-trader-live.service | head -12 || true
echo "[disable-mes] done — back to single-symbol (BTCUSDT) mode."
