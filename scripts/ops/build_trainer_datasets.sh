#!/usr/bin/env bash
# scripts/ops/build_trainer_datasets.sh — build all WS5 dataset families.
#
# Builds every dataset family consumed by the baseline training manifests
# in ml/configs/.  Run AFTER sync_trainer_data.sh has populated DATA_DIR
# with trade_journal.db (and optionally signal_audit.jsonl).
#
# All builds pass --overwrite so re-running from the same feedstock is
# idempotent.  Per-family failures are logged and counted but do not abort
# the run — a 0-row review_journal (no health-review answers yet) or a
# missing signal_audit.jsonl must not prevent the other seven families
# from building.
#
# Dataset families built:
#   backtest_results    — trade_journal.db
#   trade_outcomes      — trade_journal.db
#   execution_quality   — trade_journal.db
#   account_context     — trade_journal.db + config/accounts.yaml
#   setup_labels        — trade_journal.db
#   setup_labels_audit  — trade_journal.db + signal_audit.jsonl (skipped if absent)
#   review_journal      — comms/ directory in the repo
#   market_raw          — Bybit V5 public klines (ICT_OFFVM_BUILD_HOST=1)
#   market_features     — derived from market_raw
#
# Environment knobs:
#   REPO_ROOT          — defaults to /home/ubuntu/ict-trading-bot
#   VENV_DIR           — defaults to $REPO_ROOT/.venv
#   DATA_DIR           — defaults to $REPO_ROOT/data
#   DATASETS_ROOT      — defaults to $REPO_ROOT/datasets-out
#   DATASET_VERSION    — defaults to v002 (bumped from v001 in the Phase-2
#                        feature-expansion sprint: market_features +
#                        setup_labels gained new non-leaking columns and a
#                        builder_version bump; manifests now point at v002)
#   MARKET_START       — defaults to a rolling 5-years-ago (UTC), so the
#                        BTCUSDT market_raw / market_features builds carry
#                        ≥5y of history for the regime classifiers (operator
#                        directive 2026-05-30: "all models should have at
#                        least five years of data to train on"). Bybit's
#                        BTCUSDT linear-perp klines reach back to ~2020-03,
#                        so a 5y window is fully covered; the adapter clamps
#                        to the earliest available bar if the start predates
#                        listing. Was 2024-01-01 (~1.4y) before this change.
#                        Note this governs the BTC (Bybit) leg only — the MES
#                        leg has its own MES_YF_START (yfinance caps intraday
#                        history at ~60d) and the deep-history MES path is the
#                        separate IBKR pull (pull_mes_ibkr_history.sh) /
#                        run_mes_training.sh (MES_MARKET_START=2000-01-01,
#                        daily ES=F).
#   MARKET_END         — defaults to today (UTC)
#   BUILD_LOG_PATH     — defaults to $REPO_ROOT/runtime_logs/trainer/dataset_builds.jsonl
#
# Exit codes:
#   0   all required families built (0-row families are not errors)
#   1   one or more families failed with a non-data error
#   2   environment misconfigured (no venv, no repo, no trade_journal.db)
set -uo pipefail

REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv}"
DATA_DIR="${DATA_DIR:-$REPO_ROOT/data}"
DATASETS_ROOT="${DATASETS_ROOT:-$REPO_ROOT/datasets-out}"
DATASET_VERSION="${DATASET_VERSION:-v002}"
# Rolling 5-years-ago window for the BTCUSDT (Bybit) market-data builds so
# the regime classifiers always train on ≥5y of recent history. The daily
# cron re-derives this each run; `date -d '5 years ago'` is GNU coreutils
# (present on the trainer VM). Fallback to a fixed 2021-01-01 (still ≥5y as
# of 2026) if `date -d` is unavailable on some host.
MARKET_START="${MARKET_START:-$(date -u -d '5 years ago' +%Y-%m-%d 2>/dev/null || echo 2021-01-01)}"
MARKET_END="${MARKET_END:-$(date -u +%Y-%m-%d)}"
BUILD_LOG_PATH="${BUILD_LOG_PATH:-$REPO_ROOT/runtime_logs/trainer/dataset_builds.jsonl}"

iso_now() { date -u +'%Y-%m-%dT%H:%M:%S+00:00'; }

emit() {
  local payload="$1"
  mkdir -p "$(dirname "$BUILD_LOG_PATH")"
  printf '%s\n' "$payload" >> "$BUILD_LOG_PATH"
  printf '%s\n' "$payload"
}

# --- Env checks -----------------------------------------------------------
if [ ! -d "$REPO_ROOT/.git" ]; then
  emit "$(printf '{"ts":"%s","status":"env_error","detail":"REPO_ROOT is not a git repo: %s"}' \
    "$(iso_now)" "$REPO_ROOT")"
  exit 2
fi

if [ ! -d "$VENV_DIR" ]; then
  emit "$(printf '{"ts":"%s","status":"env_error","detail":"venv not found at %s — run run_training_cycle.sh or setup venv first"}' \
    "$(iso_now)" "$VENV_DIR")"
  exit 2
fi

DB_PATH="${DATA_DIR}/trade_journal.db"
if [ ! -f "$DB_PATH" ]; then
  emit "$(printf '{"ts":"%s","status":"env_error","detail":"trade_journal.db not found at %s — run sync_trainer_data.sh first"}' \
    "$(iso_now)" "$DB_PATH")"
  exit 2
fi

cd "$REPO_ROOT"
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

AUDIT_PATH="${DATA_DIR}/signal_audit.jsonl"
ACCOUNTS_YAML="${REPO_ROOT}/config/accounts.yaml"
COMMS_ROOT="${REPO_ROOT}/comms"

overall_rc=0

# build_family: run one dataset build; logs result; updates overall_rc on failure.
build_family() {
  local family="$1"; shift
  emit "$(printf '{"ts":"%s","status":"building","family":"%s"}' "$(iso_now)" "$family")"
  set +e
  python -m ml build-dataset "$family" "$@" \
    >"/tmp/bld_${family}_$$.out" 2>"/tmp/bld_${family}_$$.err"
  local rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    local rows="?"
    rows="$(python3 -c "
import glob, json, sys
files = glob.glob(sys.argv[1])
if files:
    print(json.load(open(files[0])).get('row_count', '?'))
else:
    print('?')
" "${DATASETS_ROOT}/${family}"/*/*/*/metadata.json 2>/dev/null || echo '?')"
    emit "$(python3 -c "
import json, sys
ts, fam, rows = sys.argv[1:]
print(json.dumps({'ts': ts, 'status': 'ok', 'family': fam, 'row_count': rows}))" \
      "$(iso_now)" "$family" "$rows")"
  else
    local err
    err="$(tail -n 3 "/tmp/bld_${family}_$$.err" 2>/dev/null | tr '\n' ' ' | head -c 400)"
    emit "$(python3 -c "
import json, sys
ts, fam, rc, err = sys.argv[1:]
print(json.dumps({'ts': ts, 'status': 'failed', 'family': fam, 'exit_code': int(rc), 'stderr_tail': err}))" \
      "$(iso_now)" "$family" "$rc" "$err")"
    overall_rc=1
  fi
  rm -f "/tmp/bld_${family}_$$.out" "/tmp/bld_${family}_$$.err"
}

emit "$(printf '{"ts":"%s","status":"build_start","datasets_root":"%s","version":"%s"}' \
  "$(iso_now)" "$DATASETS_ROOT" "$DATASET_VERSION")"

# ---- journal-backed families --------------------------------------------
build_family backtest_results \
  --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
  --source "trade_journal.db" --overwrite \
  "db_path=${DB_PATH}"

build_family trade_outcomes \
  --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
  --source "trade_journal.db" --overwrite \
  "db_path=${DB_PATH}"

build_family execution_quality \
  --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
  --source "trade_journal.db" --overwrite \
  "db_path=${DB_PATH}" "slippage_cap_bps=200.0"

if [ -f "$ACCOUNTS_YAML" ]; then
  build_family account_context \
    --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
    --source "trade_journal.db" --overwrite \
    "db_path=${DB_PATH}" "accounts_yaml_path=${ACCOUNTS_YAML}"
else
  emit "$(printf '{"ts":"%s","status":"skipped","family":"account_context","detail":"config/accounts.yaml not found"}' "$(iso_now)")"
fi

build_family setup_labels \
  --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
  --source "trade_journal.db" --overwrite \
  "db_path=${DB_PATH}" "risk_pct=1.0" "r_cap=3.0"

if [ -f "$AUDIT_PATH" ]; then
  build_family setup_labels_audit \
    --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
    --source "trade_journal.db" --overwrite \
    "db_path=${DB_PATH}" "audit_log_path=${AUDIT_PATH}" \
    "risk_pct=1.0" "r_cap=3.0" "match_window_seconds=60"
else
  emit "$(printf '{"ts":"%s","status":"skipped","family":"setup_labels_audit","detail":"signal_audit.jsonl absent — no signals fired yet"}' "$(iso_now)")"
fi

# ---- review_journal (comms/ in the repo) --------------------------------
# Produces 0 rows until the operator answers health-review prompts.
build_family review_journal \
  --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
  --source "comms-artifacts" --overwrite \
  "comms_root=${COMMS_ROOT}" "include_archive=true"

# ---- market_raw + market_features (Bybit public klines) -----------------
# The bybit_offvm adapter requires ICT_OFFVM_BUILD_HOST=1 to guard against
# accidental runs on the live trader VM.  The trainer VM is not the live
# trader VM, so this flag is correct here.
export ICT_OFFVM_BUILD_HOST=1

if ! python -c "import ccxt" 2>/dev/null; then
  emit "$(printf '{"ts":"%s","status":"info","detail":"installing ccxt for Bybit klines fetch"}' "$(iso_now)")"
  set +e
  pip install --quiet "ccxt>=4.0"
  ccxt_rc=$?
  set -e
  if [ "$ccxt_rc" -ne 0 ]; then
    emit "$(printf '{"ts":"%s","status":"skipped","family":"market_raw","detail":"ccxt install failed; regime-classifier baseline skipped"}' "$(iso_now)")"
    emit "$(printf '{"ts":"%s","status":"build_end","overall_rc":%d}' "$(iso_now)" "$overall_rc")"
    exit "$overall_rc"
  fi
fi

# Build the (market_raw, market_features) pair for one BTCUSDT timeframe.
# Each timeframe is its own dataset shard; the regime-classifier manifests
# in ml/configs/ are sharded by timeframe (1h baseline, 5m + 15m for the
# v2 LightGBM heads + their v1 counterparts), so the builder has to
# produce every shard the manifests reference.
build_btcusdt_pair() {
  local tf="$1"
  local raw_path="${DATASETS_ROOT}/market_raw/BTCUSDT/${tf}/${DATASET_VERSION}"

  build_family market_raw \
    --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
    --source "bybit_v5_offvm" --symbol-scope BTCUSDT --timeframe "$tf" --overwrite \
    "adapter=bybit_v5_offvm" "symbol=BTCUSDT" "timeframe=${tf}" \
    "start=${MARKET_START}" "end=${MARKET_END}"

  if [ -d "$raw_path" ]; then
    build_family market_features \
      --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
      --source "${raw_path}" --symbol-scope BTCUSDT --timeframe "$tf" --overwrite \
      "market_raw_path=${raw_path}" "vol_window_n=20" "forward_window_m=5" \
      "vol_threshold=0.005" "trend_threshold=0.005" "n_vol_buckets=3"
  else
    emit "$(printf '{"ts":"%s","status":"skipped","family":"market_features","symbol":"BTCUSDT","timeframe":"%s","detail":"market_raw path not found"}' "$(iso_now)" "$tf")"
  fi
}

build_btcusdt_pair 1h
build_btcusdt_pair 5m
build_btcusdt_pair 15m

# ---- MES market_features (yfinance ES=F 5m base, resampled to 15m) -------
# MES has no deep intraday feed on the trainer VM: the IBKR gateway lives on
# the live VM and the historical pull is operator-gated (run_serious_baseline.sh
# MES_IBKR=1, currently blocked by an IB session conflict). So the daily cycle
# pulls ~60d of ES=F 5m bars via yfinance — the same index level as MES, the
# deepest intraday history yfinance serves — caches it once, and resamples
# 5m -> 15m. This folds the MES leg of run_serious_baseline.sh into the daily
# cycle (MB-20260527-001) so mes-regime-{5m,15m} + mes-setup-quality stop
# FileNotFound-failing every run (data existed only at the stale v001; manifests
# want v002).
#
# market_features use a DATA-DRIVEN vol_threshold (median forward-vol) so the
# range/volatile regime label stays ~balanced. A fixed threshold collapses MES
# intraday to all-range (forward vol is small) and every regime model's
# f1_volatile goes to 0 (MB-20260527-002).
#
# All MES builds are NON-FATAL (build_warn, not overall_rc=1): a yfinance hiccup
# must not turn the whole cycle red — the manifests then skip on empty data.
MES_YF_START="$(date -u -d '58 days ago' +%Y-%m-%d 2>/dev/null || echo "$MARKET_START")"

# Deep DAILY ES=F window for the mes-regime-1d-lgbm-v2 manifest
# (MB-20260528-001). yfinance serves 1d bars back many years (unlike its
# ~60d intraday cap), so the daily regime head gets a real multi-year
# training set (~2500 daily bars from 2015) - the daily timeframe is where
# the MES regime label separates (the orphan baseline scored macro_f1=0.685
# / f1_volatile=0.543 on 1d, vs the 5m/15m modal collapse to 0).
MES_1D_START="${MES_1D_START:-2015-01-01}"

mes_median_vt() {  # median forward_log_return_vol of a market_features dir
  python3 -c "import json,sys,statistics as s
try:
    rows=[json.loads(l) for l in open(sys.argv[1]+'/data.jsonl') if l.strip()]
    v=[r['forward_log_return_vol'] for r in rows if r.get('forward_log_return_vol') not in (None,0)]
    print(round(s.median(v),7) if v else 0.001)
except Exception:
    print(0.001)" "$1" 2>/dev/null || echo 0.001
}

mes_build() {  # non-fatal build-dataset wrapper: logs ok/warn, never flips overall_rc
  local family="$1"; shift
  emit "$(printf '{"ts":"%s","status":"building","family":"%s","symbol":"MES"}' "$(iso_now)" "$family")"
  set +e
  python -m ml build-dataset "$family" "$@" \
    >"/tmp/mesbld_${family}_$$.out" 2>"/tmp/mesbld_${family}_$$.err"
  local rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    emit "$(printf '{"ts":"%s","status":"ok","family":"%s","symbol":"MES"}' "$(iso_now)" "$family")"
  else
    local err; err="$(tail -n 2 "/tmp/mesbld_${family}_$$.err" 2>/dev/null | tr '\n' ' ' | head -c 300)"
    emit "$(python3 -c "import json,sys; print(json.dumps({'ts':sys.argv[1],'status':'warn','family':sys.argv[2],'symbol':'MES','exit_code':int(sys.argv[3]),'stderr_tail':sys.argv[4]}))" \
      "$(iso_now)" "$family" "$rc" "$err")"
  fi
  rm -f "/tmp/mesbld_${family}_$$.out" "/tmp/mesbld_${family}_$$.err"
  return 0
}

build_mes_features_tf() {  # <tf> <raw_dir> — 2-pass median-calibrated features
  local tf="$1" raw_dir="$2"
  local feat_dir="${DATASETS_ROOT}/market_features/MES/${tf}/${DATASET_VERSION}"
  if [ ! -f "$raw_dir/data.jsonl" ]; then
    emit "$(printf '{"ts":"%s","status":"skipped","family":"market_features","symbol":"MES","timeframe":"%s","detail":"no market_raw at %s"}' "$(iso_now)" "$tf" "$raw_dir")"
    return 0
  fi
  mes_build market_features \
    --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
    --source "$raw_dir" --symbol-scope MES --timeframe "$tf" --overwrite \
    "market_raw_path=${raw_dir}" "vol_window_n=20" "forward_window_m=5" \
    "vol_threshold=0.001" "trend_threshold=0.001" "n_vol_buckets=3"
  local vt; vt="$(mes_median_vt "$feat_dir")"
  mes_build market_features \
    --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
    --source "$raw_dir" --symbol-scope MES --timeframe "$tf" --overwrite \
    "market_raw_path=${raw_dir}" "vol_window_n=20" "forward_window_m=5" \
    "vol_threshold=${vt}" "trend_threshold=${vt}" "n_vol_buckets=3"
  emit "$(printf '{"ts":"%s","status":"ok","family":"market_features","symbol":"MES","timeframe":"%s","vol_threshold":"%s"}' "$(iso_now)" "$tf" "$vt")"
}

build_mes_setup_labels() {
  # Symbol-scoped MES setup_labels so mes-setup-quality finds a (currently
  # empty — no closed MES trades) v002 dataset and SKIPS cleanly
  # (empty_dataset) instead of FileNotFound-failing.
  mes_build setup_labels \
    --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
    --source "trade_journal.db" --symbol-scope MES --overwrite \
    "db_path=${DB_PATH}" "symbol=MES" "risk_pct=1.0" "r_cap=3.0"
}

build_mes_market() {
  local base_tf="5m"
  local base_raw="${DATASETS_ROOT}/market_raw/MES/${base_tf}/${DATASET_VERSION}"

  # Prefer deep native MES history pulled from IBKR on the live VM (synced by
  # sync_trainer_data.sh into $DATA_DIR/ibkr_datasets) over the rolling ~60d
  # ES=F yfinance window, when present and non-trivial. See MB-20260528-002 +
  # scripts/ops/pull_mes_ibkr_history.sh.
  local ibkr_src="${DATA_DIR}/ibkr_datasets/market_raw/MES"
  local ibkr_5m="${ibkr_src}/5m/${DATASET_VERSION}/data.jsonl"
  if [ -f "$ibkr_5m" ] && [ "$(wc -l < "$ibkr_5m" 2>/dev/null | tr -d ' ')" -gt 1000 ]; then
    emit "$(printf '{"ts":"%s","status":"info","detail":"using synced IBKR MES market_raw (deep history) instead of yfinance"}' "$(iso_now)")"
    local used=0
    for tf in 5m 15m; do
      local src_dir="${ibkr_src}/${tf}/${DATASET_VERSION}"
      if [ -f "${src_dir}/data.jsonl" ]; then
        local dst_dir="${DATASETS_ROOT}/market_raw/MES/${tf}/${DATASET_VERSION}"
        mkdir -p "$dst_dir"
        cp -f "${src_dir}/data.jsonl" "${dst_dir}/data.jsonl"
        [ -f "${src_dir}/metadata.json" ] && cp -f "${src_dir}/metadata.json" "${dst_dir}/metadata.json"
        build_mes_features_tf "$tf" "$dst_dir"
        used=1
      fi
    done
    if [ "$used" -eq 1 ]; then
      build_mes_setup_labels
      return 0
    fi
    emit "$(printf '{"ts":"%s","status":"warn","detail":"IBKR MES base present but no usable timeframe shards; falling back to yfinance"}' "$(iso_now)")"
  fi

  if ! python -c "import yfinance" 2>/dev/null; then
    emit "$(printf '{"ts":"%s","status":"info","detail":"installing yfinance for MES ES=F pull"}' "$(iso_now)")"
    set +e; pip install --quiet "yfinance>=0.2"; set -e
  fi
  if ! python -c "import yfinance" 2>/dev/null; then
    emit "$(printf '{"ts":"%s","status":"skipped","family":"market_raw","symbol":"MES","detail":"yfinance unavailable; MES regime manifests will skip"}' "$(iso_now)")"
    return 0
  fi
  # 5m base via yfinance ES=F (the deepest intraday history yfinance serves)
  mes_build market_raw \
    --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
    --source yfinance_offvm --symbol-scope MES --timeframe "$base_tf" --overwrite \
    "adapter=yfinance_offvm" "symbol=MES" "start=${MES_YF_START}" "end=${MARKET_END}"
  build_mes_features_tf 5m "$base_raw"
  # 15m derived from the cached 5m base (resample — no second download)
  if [ -f "$base_raw/data.jsonl" ]; then
    mes_build market_raw \
      --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
      --source resample --symbol-scope MES --timeframe 15m --overwrite \
      "adapter=resample" "symbol=MES" "timeframe=15m" "source_path=${base_raw}"
    build_mes_features_tf 15m "${DATASETS_ROOT}/market_raw/MES/15m/${DATASET_VERSION}"
  fi
  build_mes_setup_labels
}

# Deep daily MES (ES=F) regime data - independent of the intraday 5m/15m
# path (which the IBKR branch can early-return out of), so the 1d build
# always runs. Feeds mes-regime-1d-lgbm-v2 (MB-20260528-001). Pulls 1d
# directly from yfinance (decades of daily history), then the same
# median-calibrated market_features as the intraday shards. Non-fatal:
# a yfinance hiccup just makes the 1d manifest skip on empty data.
build_mes_1d() {
  if ! python -c "import yfinance" 2>/dev/null; then
    set +e; pip install --quiet "yfinance>=0.2"; set -e
  fi
  if ! python -c "import yfinance" 2>/dev/null; then
    emit "$(printf '{"ts":"%s","status":"skipped","family":"market_raw","symbol":"MES","timeframe":"1d","detail":"yfinance unavailable; mes-regime-1d will skip"}' "$(iso_now)")"
    return 0
  fi
  mes_build market_raw \
    --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
    --source yfinance_offvm --symbol-scope MES --timeframe 1d --overwrite \
    "adapter=yfinance_offvm" "symbol=MES" "timeframe=1d" "start=${MES_1D_START}" "end=${MARKET_END}"
  build_mes_features_tf 1d "${DATASETS_ROOT}/market_raw/MES/1d/${DATASET_VERSION}"
}

build_mes_market
build_mes_1d

emit "$(printf '{"ts":"%s","status":"build_end","overall_rc":%d}' "$(iso_now)" "$overall_rc")"
exit "$overall_rc"
