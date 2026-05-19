#!/usr/bin/env bash
# Tier-1 read-only diagnostic: VWAP HTF-gate parameter sweep.
#
# Runs scripts/ops/fetch_backtest_candles.py to refresh historical
# 5m BTCUSDT data (PUBLIC Bybit API — no auth required), then
# runs src.backtest.run_backtest_vwap with --compare to evaluate
# multiple HTF-gate configurations side-by-side over a 90-day
# window with 8x 30-day random sub-windows.
#
# Tests the live-trading concern from 2026-05-18: VWAP-long is
# losing 89% (10.9% win) while VWAP-short hits 40.9%. The
# 4h-EMA-200 HTF gate (disabled 2026-05-13 due to long-bias
# entrenchment) protected the 1.0σ entry threshold in backtests,
# but that gate's 33-day lookback is too slow for the 5m
# strategy in a fast-moving market. We want gates that work
# across various regimes.
#
# Compares: no-gate (baseline), 15m EMA-20, 1h EMA-20/50/200,
# 4h EMA-20. The 1h EMA-200 was the Phase-3 design from
# 2026-05-08-all-models-training; the shorter ones test whether
# fast-response gates work better in chop.
#
# No DB writes. No live-trading side effects. ~2-5 min runtime
# depending on data freshness.
#
# Operator invokes via operator-actions issue:
#   action: vwap-backtest-sweep
#   reason: <text>
#   mode: <compare|threshold-sweep|adaptive|param-sweep>  (optional, default 'compare')
#   days: <int>          (optional, default 90 — total history pulled)
#   windows: <int>       (optional, default 8 — random sub-windows)
#   window_days: <int>   (optional, default 30 — size of each sub-window)
set -euo pipefail

SCRIPT_NAME="vwap_backtest_sweep"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

MODE="${ACTION_MODE:-compare}"
DAYS="${ACTION_DAYS:-90}"
WINDOWS="${ACTION_WINDOWS:-8}"
WINDOW_DAYS="${ACTION_WINDOW_DAYS:-30}"
RECENT_ONLY_FRAC="${ACTION_RECENT_ONLY_FRAC:-1.0}"

# Map mode → run_backtest_vwap flag.
case "${MODE}" in
    compare)            BACKTEST_FLAG="--compare" ;;
    threshold-sweep)    BACKTEST_FLAG="--threshold-sweep" ;;
    adaptive)           BACKTEST_FLAG="--adaptive" ;;
    param-sweep)        BACKTEST_FLAG="--param-sweep" ;;
    *)
        log "ERROR: unknown mode '${MODE}' (allowed: compare, threshold-sweep, adaptive, param-sweep)"
        record_audit "vwap-backtest-sweep" "error" \
            "{\"reason\": \"bad mode\", \"mode\": \"${MODE}\"}" >/dev/null || true
        exit 1
        ;;
esac

DATA_PATH="${REPO_DIR}/data/backtest_sweep_$(date +%Y%m%d).csv"

echo
echo "===== fetch_backtest_candles.py --days ${DAYS} ====="
set +e
BACKTEST_DATA_PATH="${DATA_PATH}" python3 \
    "${REPO_DIR}/scripts/ops/fetch_backtest_candles.py" \
    --days "${DAYS}"
fetch_code=$?
set -e

if [ "${fetch_code}" -ne 0 ]; then
    log "ERROR: candle fetch exited ${fetch_code}"
    record_audit "vwap-backtest-sweep" "failed" \
        "{\"stage\": \"fetch\", \"exit_code\": ${fetch_code}}" \
        >/dev/null || true
    exit "${fetch_code}"
fi

if [ ! -f "${DATA_PATH}" ]; then
    log "ERROR: fetcher succeeded but no CSV at ${DATA_PATH}"
    exit 1
fi

echo
echo "===== run_backtest_vwap.py ${BACKTEST_FLAG} --windows ${WINDOWS} --window-days ${WINDOW_DAYS} --days ${DAYS} --recent-only-frac ${RECENT_ONLY_FRAC} ====="
set +e
( cd "${REPO_DIR}" && \
  BACKTEST_DATA_PATH="${DATA_PATH}" python3 -m src.backtest.run_backtest_vwap \
    "${BACKTEST_FLAG}" --windows "${WINDOWS}" --window-days "${WINDOW_DAYS}" \
    --days "${DAYS}" --recent-only-frac "${RECENT_ONLY_FRAC}" )
backtest_code=$?
set -e

# Cleanup the temp CSV to keep VM disk tidy.
rm -f "${DATA_PATH}" 2>/dev/null || true

if [ "${backtest_code}" -ne 0 ]; then
    record_audit "vwap-backtest-sweep" "failed" \
        "{\"stage\": \"backtest\", \"exit_code\": ${backtest_code}}" \
        >/dev/null || true
    log "ERROR: backtest exited ${backtest_code}"
    exit "${backtest_code}"
fi

record_audit "vwap-backtest-sweep" "ok" \
    "{\"mode\": \"${MODE}\", \"days\": ${DAYS}, \"windows\": ${WINDOWS}, \"window_days\": ${WINDOW_DAYS}}" \
    >/dev/null || true
exit 0
