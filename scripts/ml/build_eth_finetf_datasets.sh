#!/usr/bin/env bash
# Build ONLY the ETHUSDT 5m + 15m (market_raw, market_features) dataset pairs —
# the prerequisite for training eth-regime-{5m,15m}-lgbm-v1 (MB-20260627-003).
#
# Targeted subset of scripts/ops/build_trainer_datasets.sh::build_bybit_pair so
# we can build just the two ETH fine-timeframe shards (a full daily build also
# rebuilds BTC + MES). Same canonical market_features params every Bybit pair
# uses (vol_window_n=20 forward_window_m=5 vol_threshold=0.005 trend_threshold=
# 0.005 n_vol_buckets=3) so the realized regime_label is computed identically to
# the BTC heads. Trainer-side, long-running (5y of 5m klines) — run under nohup.
#
#   nohup bash scripts/ml/build_eth_finetf_datasets.sh > /tmp/eth_ds_build.log 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/../.." 2>/dev/null || true
PY=python3; for c in .venv/bin/python venv/bin/python; do [ -x "$c" ] && PY="$c" && break; done

DATASETS_ROOT="${DATASETS_ROOT:-datasets-out}"
DATASET_VERSION="${DATASET_VERSION:-v002}"
# Rolling 5 years of history (matches the daily build's MARKET_START default).
MARKET_START="${MARKET_START:-$(date -u -d '5 years ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo 2021-06-27T00:00:00Z)}"
MARKET_END="${MARKET_END:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"

# The bybit_offvm adapter requires this guard; the trainer VM is not the live VM.
export ICT_OFFVM_BUILD_HOST=1

if ! "$PY" -c "import ccxt" 2>/dev/null; then
  echo "installing ccxt"; pip install --quiet "ccxt>=4.0" || { echo "ccxt install failed"; exit 1; }
fi

build_pair() {
  local symbol="$1" tf="$2"
  local raw_path="${DATASETS_ROOT}/market_raw/${symbol}/${tf}/${DATASET_VERSION}"
  echo "== [$(date -u +%H:%M:%S)] market_raw ${symbol}/${tf} (${MARKET_START} .. ${MARKET_END}) =="
  "$PY" -m ml build-dataset market_raw \
    --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
    --source bybit_v5_offvm --symbol-scope "$symbol" --timeframe "$tf" --overwrite \
    "adapter=bybit_v5_offvm" "symbol=${symbol}" "timeframe=${tf}" \
    "start=${MARKET_START}" "end=${MARKET_END}" || { echo "market_raw ${symbol}/${tf} FAILED"; return 1; }
  if [ ! -d "$raw_path" ]; then echo "no market_raw at ${raw_path}"; return 1; fi
  echo "== [$(date -u +%H:%M:%S)] market_features ${symbol}/${tf} =="
  "$PY" -m ml build-dataset market_features \
    --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
    --source "${raw_path}" --symbol-scope "$symbol" --timeframe "$tf" --overwrite \
    "market_raw_path=${raw_path}" "vol_window_n=20" "forward_window_m=5" \
    "vol_threshold=0.005" "trend_threshold=0.005" "n_vol_buckets=3" \
    || { echo "market_features ${symbol}/${tf} FAILED"; return 1; }
  local rows; rows=$(wc -l < "${DATASETS_ROOT}/market_features/${symbol}/${tf}/${DATASET_VERSION}/data.jsonl" 2>/dev/null || echo "?")
  echo "== OK ${symbol}/${tf} market_features rows=${rows} =="
}

rc=0
build_pair ETHUSDT 5m || rc=1
build_pair ETHUSDT 15m || rc=1
echo "ETH_FINETF_BUILD_DONE rc=${rc}"
exit "$rc"
