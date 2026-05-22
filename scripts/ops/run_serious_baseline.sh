#!/usr/bin/env bash
# scripts/ops/run_serious_baseline.sh — one-time "serious baseline" training
# session on the trainer VM, timeframe-aligned to how the strategies trade
# (turtle_soup=15m, vwap=5m, ict_scalp_5m=5m), with backtests.
#
# Per the 2026-05-22 directive: models train on the timeframes the strategies
# actually use (5m / 15m), over as deep a history as each source supports,
# slow and steady so we don't overload the upstream APIs.
#
#   BTCUSDT — Bybit public klines (bybit_v5_offvm), 5m + 15m, back to BTC_START
#             (default 2020-01-01), throttled via BYBIT_PAUSE_S between pages.
#   MES     — IBKR (ibkr_offvm) intraday via the live-VM gateway. GATED: only
#             runs when MES_IBKR=1 AND the IB gateway is healthy. As of
#             2026-05-22 the gateway returns IBKR error 162 ("session connected
#             from a different IP") so the MES leg is skipped until the IB
#             session conflict is resolved (operator-side).
#
# Backtests: the existing ICT + VWAP backtest entrypoints are run over the deep
# 5m history, writing backtest_results to a dedicated baseline DB and building
# the backtest_results dataset family.
#
# Detached, survivable launch:
#   nohup bash scripts/ops/run_serious_baseline.sh \
#     > runtime_logs/trainer/serious_baseline.out 2>&1 &
# Progress: tail $SERIOUS_LOG_PATH (default runtime_logs/trainer/serious_baseline.jsonl)
set -uo pipefail

REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv}"
DATA_DIR="${DATA_DIR:-$REPO_ROOT/data}"
DATASETS_ROOT="${DATASETS_ROOT:-$REPO_ROOT/datasets-out}"
EXPERIMENTS_ROOT="${EXPERIMENTS_ROOT:-$REPO_ROOT/ml/experiments-runs}"
REGISTRY_ROOT="${REGISTRY_ROOT:-$REPO_ROOT/ml/registry-store}"
DATASET_VERSION="${DATASET_VERSION:-v001}"
BTC_SYMBOL="${BTC_SYMBOL:-BTCUSDT}"
BTC_START="${BTC_START:-2020-01-01}"
BYBIT_PAUSE_S="${BYBIT_PAUSE_S:-0.25}"
TIMEFRAMES="${TIMEFRAMES:-5m 15m}"
MES_IBKR="${MES_IBKR:-0}"
SERIOUS_LOG_PATH="${SERIOUS_LOG_PATH:-$REPO_ROOT/runtime_logs/trainer/serious_baseline.jsonl}"

cd "$REPO_ROOT" 2>/dev/null || { echo "no repo at $REPO_ROOT"; exit 2; }

emit() {
  mkdir -p "$(dirname "$SERIOUS_LOG_PATH")"
  python3 - "$@" <<'PY' | tee -a "$SERIOUS_LOG_PATH"
import json, sys, datetime
o = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
for p in sys.argv[1:]:
    k, _, v = p.partition("="); o[k] = v
print(json.dumps(o))
PY
}

# vol_threshold for the regime label, calibrated per intraday timeframe
# (forward log-return vol grows with bar size).
vol_threshold_for() { case "$1" in 5m) echo 0.0015;; 15m) echo 0.0025;; 1h) echo 0.004;; *) echo 0.003;; esac; }

emit status=session_start head="$(git rev-parse --short HEAD 2>/dev/null || echo '?')" branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')" timeframes="$TIMEFRAMES" btc_start="$BTC_START"

# --- venv -----------------------------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
  python3.11 -m venv "$VENV_DIR" 2>/dev/null || python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install --quiet --upgrade pip
  "$VENV_DIR/bin/pip" install --quiet -r requirements.txt || true
fi
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
python -c "import ccxt" 2>/dev/null || { pip install --quiet "ccxt>=4.0" && emit status=ccxt_installed || emit status=ccxt_warn; }
export ICT_OFFVM_BUILD_HOST=1

build_and_train_regime() {
  # build_and_train_regime <symbol> <adapter-args...> via globals SYM/TF
  local sym="$1" tf="$2" adapter="$3"; shift 3
  local raw_dir="${DATASETS_ROOT}/market_raw/${sym}/${tf}/${DATASET_VERSION}"
  emit status=building family=market_raw symbol="$sym" timeframe="$tf" adapter="$adapter"
  if python -m ml build-dataset market_raw \
      --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
      --source "$adapter" --symbol-scope "$sym" --timeframe "$tf" --overwrite \
      "adapter=$adapter" "symbol=$sym" "timeframe=$tf" "$@" \
      >/tmp/sb_raw_$$.out 2>/tmp/sb_raw_$$.err; then
    emit status=build_ok family=market_raw symbol="$sym" timeframe="$tf"
  else
    emit status=build_warn family=market_raw symbol="$sym" timeframe="$tf" detail="$(tail -n1 /tmp/sb_raw_$$.err 2>/dev/null | head -c 200)"
    return 1
  fi
  [ -d "$raw_dir" ] || { emit status=build_warn family=market_raw detail="no raw dir $raw_dir"; return 1; }
  local vt; vt="$(vol_threshold_for "$tf")"
  emit status=building family=market_features symbol="$sym" timeframe="$tf" vol_threshold="$vt"
  python -m ml build-dataset market_features \
    --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
    --source "$raw_dir" --symbol-scope "$sym" --timeframe "$tf" --overwrite \
    "market_raw_path=$raw_dir" "vol_window_n=20" "forward_window_m=5" \
    "vol_threshold=$vt" "trend_threshold=$vt" "n_vol_buckets=3" \
    >/tmp/sb_feat_$$.out 2>/tmp/sb_feat_$$.err \
    && emit status=build_ok family=market_features symbol="$sym" timeframe="$tf" \
    || { emit status=build_warn family=market_features symbol="$sym" timeframe="$tf" detail="$(tail -n1 /tmp/sb_feat_$$.err 2>/dev/null | head -c 200)"; return 1; }
  local key; key="$(echo "$sym" | grep -qi MES && echo mes || echo btc)"
  local manifest="ml/configs/${key}-regime-${tf}.yaml"
  [ -f "$manifest" ] || { emit status=manifest_missing manifest="$manifest"; return 1; }
  python -m ml train "$manifest" --datasets-root "$DATASETS_ROOT" \
    --experiments-root "$EXPERIMENTS_ROOT" --registry-root "$REGISTRY_ROOT" \
    >/tmp/sb_train_$$.out 2>/tmp/sb_train_$$.err
  local rc=$?
  local mid; mid="$(python3 -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get("model_id") or "")
except Exception: print("")' /tmp/sb_train_$$.out 2>/dev/null)"
  if [ "$rc" -eq 0 ]; then emit status=manifest_ok manifest="$manifest" model_id="$mid"
  else emit status=manifest_failed manifest="$manifest" exit_code="$rc" detail="$(tail -n1 /tmp/sb_train_$$.err 2>/dev/null | head -c 200)"; fi
  rm -f /tmp/sb_raw_$$.* /tmp/sb_feat_$$.* /tmp/sb_train_$$.*
}

# --- BTCUSDT (Bybit, deep, throttled) -------------------------------------
for tf in $TIMEFRAMES; do
  build_and_train_regime "$BTC_SYMBOL" "$tf" "bybit_v5_offvm" \
    "start=$BTC_START" "pause_s=$BYBIT_PAUSE_S"
done

# --- Backtests over the deep 5m history -----------------------------------
BT_RAW="${DATASETS_ROOT}/market_raw/${BTC_SYMBOL}/5m/${DATASET_VERSION}/data.jsonl"
BT_CSV="${DATA_DIR}/backtest_${BTC_SYMBOL}_5m.csv"
BT_DB="${DATA_DIR}/backtest_baseline.db"
if [ -f "$BT_RAW" ]; then
  python3 - "$BT_RAW" "$BT_CSV" <<'PY'
import json, sys, csv
src, dst = sys.argv[1], sys.argv[2]
with open(src) as f, open(dst, "w", newline="") as o:
    w = csv.writer(o); w.writerow(["timestamp","open","high","low","close","volume"])
    for line in f:
        if not line.strip(): continue
        r = json.loads(line)
        w.writerow([r["ts"], r["open"], r["high"], r["low"], r["close"], r.get("volume", 0)])
PY
  emit status=backtest_csv_ready path="$BT_CSV" rows="$(($(wc -l < "$BT_CSV")-1))"
  for bt in "src.backtest.run_backtest" "src.backtest.run_backtest_vwap"; do
    BACKTEST_DATA_PATH="$BT_CSV" TRADE_JOURNAL_DB="$BT_DB" \
      python -m "$bt" >/tmp/sb_bt_$$.out 2>/tmp/sb_bt_$$.err \
      && emit status=backtest_ok runner="$bt" tail="$(tail -n1 /tmp/sb_bt_$$.out 2>/dev/null | head -c 160)" \
      || emit status=backtest_warn runner="$bt" detail="$(tail -n1 /tmp/sb_bt_$$.err 2>/dev/null | head -c 200)"
  done
  rm -f /tmp/sb_bt_$$.*
  # Build the backtest_results dataset from the baseline DB.
  [ -f "$BT_DB" ] && python -m ml build-dataset backtest_results \
    --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
    --source trade_journal.db --symbol-scope "$BTC_SYMBOL" --overwrite \
    "db_path=$BT_DB" >/dev/null 2>&1 \
    && emit status=build_ok family=backtest_results || emit status=build_warn family=backtest_results
else
  emit status=backtest_skipped detail="no 5m market_raw at $BT_RAW"
fi

# --- MES (IBKR) — GATED on IB health --------------------------------------
if [ "$MES_IBKR" = "1" ]; then
  emit status=mes_ibkr_start note="requires healthy IB gateway + ICT_IB_HISTORICAL_OK=1"
  export ICT_IB_HISTORICAL_OK=1
  for tf in $TIMEFRAMES; do
    build_and_train_regime "MES" "$tf" "ibkr_offvm" \
      "start=${MES_START:-2018-01-01}" "host=127.0.0.1" "port=${IB_HIST_PORT:-4002}" \
      "client_id=${IB_HIST_CLIENT_ID:-450}" "pause_s=${IB_HIST_PAUSE_S:-12}"
  done
else
  emit status=mes_ibkr_skipped reason="MES_IBKR!=1 (IB gateway error 162 / session conflict as of 2026-05-22)"
fi

emit status=session_end
