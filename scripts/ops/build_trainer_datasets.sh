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

# Night-parity stagger (MB-20260709, structural memory + runtime relief): BTC
# rebuilds every night (the primary fleet); the ALT intraday shards (ETH/SOL
# 5m/15m) rebuild on alternating nights so no single cycle rebuilds all 9 crypto
# shards + their heavy market_features derivations at once. A stale-by-one-day
# alt shard is harmless (RG4 tolerates ~1-2d lag). Set BUILD_ALL_SHARDS=1 to
# force all shards regardless of parity.
NIGHT_PARITY="$(( 10#$(date -u +%j) % 2 ))"
build_alt_intraday() { [ "${BUILD_ALL_SHARDS:-0}" = "1" ] || [ "$NIGHT_PARITY" = "0" ]; }

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
  "db_path=${DB_PATH}" "include_snapshots=true"

# conviction_meta — v2 conviction meta-model training rows (one per closed/
# filled/non-backtest order package joined to its trade). Journal-backed; same
# shape as trade_outcomes. Without this build line the conviction-meta-v1
# manifest skips with empty_dataset (the dataset never gets built). Added
# 2026-06-16 alongside the trades.order_package_id join fix.
build_family conviction_meta \
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
    "db_path=${DB_PATH}" "accounts_yaml_path=${ACCOUNTS_YAML}" \
    "include_snapshots=true"
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

# Build the (market_raw, market_features) pair for one Bybit (symbol, timeframe).
# Each timeframe is its own dataset shard; the regime-classifier manifests
# in ml/configs/ are sharded by timeframe (1h baseline, 5m + 15m for the
# v2 LightGBM heads + their v1 counterparts), so the builder has to
# produce every shard the manifests reference.
#
# The ALT symbols (ETHUSDT/SOLUSDT) use the SAME market_features params as BTC
# — per the eth-regime-1h-lgbm-v1 manifest ("identical to btc-regime-1h-lgbm-v2
# except the symbol"), so the realized regime_label is computed the same way the
# alt heads were trained against. Adding the alts here is the durable fix for the
# MES/ETH live-labeling gap (MB-20260627-002 / MB-20260626-001 #1): the daily
# cycle previously refreshed ONLY BTCUSDT market_raw, so the alt regime heads'
# label datasets perpetually went stale and RG4 could never score their live
# shadow rows (ETH dataset ended 2026-06-17 while BTC was fresh to 06-26).
# Split into raw / features halves so the ETH 1h build can interpose the
# cross-asset side-stream between them (BL-20260628-XA-TRAINING-ZERO).
# build_bybit_features accepts optional extra market_features build params
# (e.g. cross_asset_path=...) after symbol + timeframe.
build_bybit_raw() {
  local symbol="$1"
  local tf="$2"
  build_family market_raw \
    --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
    --source "bybit_v5_offvm" --symbol-scope "$symbol" --timeframe "$tf" --overwrite \
    "adapter=bybit_v5_offvm" "symbol=${symbol}" "timeframe=${tf}" \
    "start=${MARKET_START}" "end=${MARKET_END}"
}

build_bybit_features() {
  local symbol="$1"
  local tf="$2"
  shift 2
  local raw_path="${DATASETS_ROOT}/market_raw/${symbol}/${tf}/${DATASET_VERSION}"
  if [ -d "$raw_path" ]; then
    build_family market_features \
      --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
      --source "${raw_path}" --symbol-scope "$symbol" --timeframe "$tf" --overwrite \
      "market_raw_path=${raw_path}" "vol_window_n=20" "forward_window_m=5" \
      "vol_threshold=0.005" "trend_threshold=0.005" "n_vol_buckets=3" "$@"
  else
    emit "$(printf '{"ts":"%s","status":"skipped","family":"market_features","symbol":"%s","timeframe":"%s","detail":"market_raw path not found"}' "$(iso_now)" "$symbol" "$tf")"
  fi
}

build_bybit_pair() {
  build_bybit_raw "$1" "$2"
  build_bybit_features "$1" "$2"
}

# BTCUSDT — the primary fleet (1h baseline + 5m/15m v2 heads).
build_bybit_pair BTCUSDT 1h
build_bybit_pair BTCUSDT 5m
build_bybit_pair BTCUSDT 15m
# ALT symbols (multi-symbol A, #1) — keep the alt regime heads' label datasets
# fresh so RG4 can score their live shadow rows. Non-fatal: a per-pair failure
# is logged + counted like any other family, never aborts.
#
# ETH 1h + its cross-asset side-stream (BL-20260628-XA-TRAINING-ZERO): the ETH
# 1h market_features build previously omitted cross_asset_path, so the xa_*
# block the xasset heads (eth-regime/-direction-1h-lgbm-xasset-v1) trained on
# was ALL-ZEROS (dead) while the LIVE per-bar scorer (cross_asset_live.py)
# computed real peer features — a train/serve gap that made the xasset heads
# NO_EDGE by construction. Build ETH + SOL 1h market_raw first (BTC 1h is
# built above), derive the cross_asset stream (peers BTC + SOL, mirroring
# config/cross_asset.yaml and the manifest headers), then pass
# cross_asset_path into the ETH 1h market_features build. Fail-open: a stream
# failure is logged LOUDLY (family=cross_asset failed, overall_rc=1 — a silent
# regression here is the original bug) but the ETH features still build
# without the path, so the base (non-xa) ETH heads never lose their dataset.
build_bybit_raw ETHUSDT 1h
build_bybit_raw SOLUSDT 1h
XA_ETH_OUT="${DATASETS_ROOT}/cross_asset/ETHUSDT/1h/${DATASET_VERSION}"
XA_ETH_PARAM=""
emit "$(printf '{"ts":"%s","status":"building","family":"cross_asset","symbol":"ETHUSDT","timeframe":"1h"}' "$(iso_now)")"
set +e
python -m scripts.ml.build_cross_asset \
  --target "${DATASETS_ROOT}/market_raw/ETHUSDT/1h/${DATASET_VERSION}" \
  --peer "${DATASETS_ROOT}/market_raw/BTCUSDT/1h/${DATASET_VERSION}" \
  --peer "${DATASETS_ROOT}/market_raw/SOLUSDT/1h/${DATASET_VERSION}" \
  --out "$XA_ETH_OUT" >"/tmp/bld_xa_eth_$$.out" 2>"/tmp/bld_xa_eth_$$.err"
xa_rc=$?
set -e
if [ "$xa_rc" -eq 0 ] && [ -s "${XA_ETH_OUT}/data.jsonl" ]; then
  emit "$(printf '{"ts":"%s","status":"ok","family":"cross_asset","symbol":"ETHUSDT","timeframe":"1h","out":"%s"}' "$(iso_now)" "$XA_ETH_OUT")"
  XA_ETH_PARAM="cross_asset_path=${XA_ETH_OUT}"
else
  xa_err="$(tail -n 3 "/tmp/bld_xa_eth_$$.err" 2>/dev/null | tr '\n' ' ' | head -c 400)"
  emit "$(python3 -c "
import json, sys
ts, rc, err = sys.argv[1:]
print(json.dumps({'ts': ts, 'status': 'failed', 'family': 'cross_asset',
                  'symbol': 'ETHUSDT', 'timeframe': '1h', 'exit_code': int(rc),
                  'detail': 'ETH 1h xa_* block will be zeros this build (BL-20260628-XA-TRAINING-ZERO regression)',
                  'stderr_tail': err}))" \
    "$(iso_now)" "$xa_rc" "$xa_err")"
  overall_rc=1
fi
rm -f "/tmp/bld_xa_eth_$$.out" "/tmp/bld_xa_eth_$$.err"
build_bybit_features ETHUSDT 1h ${XA_ETH_PARAM:+"$XA_ETH_PARAM"}
build_bybit_features SOLUSDT 1h
# ETH 5m + 15m (MB-20260627-003): the 1h ETH regime heads fail RG4 live
# (NO_EDGE — near-constant volatile output), the same weak spot as the BTC 1h
# head, while the BTC 5m/15m heads pass RG4 cleanly. eth-regime-{5m,15m}-lgbm-v1
# port the proven BTC 5m/15m recipe; their label datasets must be refreshed
# daily so RG4 can score their live shadow rows once they soak.
if build_alt_intraday; then
  build_bybit_pair ETHUSDT 5m
  build_bybit_pair ETHUSDT 15m
fi
# SOL 5m + 15m (multi-symbol regime, follow-on to ETH): sol-regime-{5m,15m}-lgbm-v1
# port the proven BTC/ETH 5m/15m recipe to SOLUSDT (live-traded: trend_donchian_sol
# prop 1h + a 4h SOL alt on demo). Same rationale as the ETH 5m/15m heads — the 1h
# regime family is the weak RG4 timeframe — so SOL goes straight to 5m/15m. Their
# label datasets must refresh daily so RG4 can score the live shadow rows post-soak.
if build_alt_intraday; then
  build_bybit_pair SOLUSDT 5m
  build_bybit_pair SOLUSDT 15m
fi

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

# Cross-asset/macro side-stream for the MES regime heads (S-MLOPT-S12, Phase
# 2.4). Best-effort + NON-FATAL: fetched once per cycle into
# $DATASETS_ROOT/macro/MES/$DATASET_VERSION and joined into every MES
# market_features build via macro_path. When the fetch is unavailable (no
# yfinance / no network / MES_MACRO=0) the path stays empty and the macro
# columns emit 0.0 — every existing MES build is unchanged (default-preserving).
MES_MACRO="${MES_MACRO:-1}"
MES_MACRO_DIR=""   # set by ensure_mes_macro on success; read by build_mes_features_tf

ensure_mes_macro() {  # fetch the daily macro side-stream once; set MES_MACRO_DIR
  [ "$MES_MACRO" = "1" ] || { emit "$(printf '{"ts":"%s","status":"info","detail":"MES_MACRO=0; skipping macro side-stream"}' "$(iso_now)")"; return 0; }
  local out="${DATASETS_ROOT}/macro/MES/${DATASET_VERSION}"
  if ! python -c "import yfinance" 2>/dev/null; then
    set +e; pip install --quiet "yfinance>=0.2"; set -e
  fi
  if ! python -c "import yfinance" 2>/dev/null; then
    emit "$(printf '{"ts":"%s","status":"skipped","family":"macro","symbol":"MES","detail":"yfinance unavailable; macro columns emit 0.0"}' "$(iso_now)")"
    return 0
  fi
  set +e
  ICT_OFFVM_BUILD_HOST=1 python -m scripts.ml.fetch_macro \
    --start "$MES_1D_START" --end "$MARKET_END" --out "$out" \
    >"/tmp/mes_macro_$$.out" 2>"/tmp/mes_macro_$$.err"
  local rc=$?
  set -e
  if [ "$rc" -eq 0 ] && [ -f "$out/data.jsonl" ]; then
    MES_MACRO_DIR="$out"
    emit "$(printf '{"ts":"%s","status":"ok","family":"macro","symbol":"MES","rows":"%s"}' "$(iso_now)" "$(wc -l < "$out/data.jsonl" | tr -d ' ')")"
  else
    local err; err="$(tail -n 2 "/tmp/mes_macro_$$.err" 2>/dev/null | tr '\n' ' ' | head -c 200)"
    emit "$(python3 -c "import json,sys; print(json.dumps({'ts':sys.argv[1],'status':'warn','family':'macro','symbol':'MES','exit_code':int(sys.argv[2]),'stderr_tail':sys.argv[3]}))" "$(iso_now)" "$rc" "$err")"
  fi
  rm -f "/tmp/mes_macro_$$.out" "/tmp/mes_macro_$$.err"
}

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
  # Join the macro side-stream when present (S-MLOPT-S12); empty arg otherwise
  # leaves the macro columns at 0.0 (default-preserving).
  local macro_arg=""
  [ -n "$MES_MACRO_DIR" ] && macro_arg="macro_path=${MES_MACRO_DIR}"
  mes_build market_features \
    --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
    --source "$raw_dir" --symbol-scope MES --timeframe "$tf" --overwrite \
    "market_raw_path=${raw_dir}" "vol_window_n=20" "forward_window_m=5" \
    "vol_threshold=0.001" "trend_threshold=0.001" "n_vol_buckets=3" ${macro_arg:+"$macro_arg"}
  local vt; vt="$(mes_median_vt "$feat_dir")"
  mes_build market_features \
    --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
    --source "$raw_dir" --symbol-scope MES --timeframe "$tf" --overwrite \
    "market_raw_path=${raw_dir}" "vol_window_n=20" "forward_window_m=5" \
    "vol_threshold=${vt}" "trend_threshold=${vt}" "n_vol_buckets=3" ${macro_arg:+"$macro_arg"}
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

mes_file_stale() {  # <data.jsonl> <max_stale_days> — echo 1 iff the last bar is
  # older than max_stale_days before now. Fail-permissive: any error -> 0 (not
  # stale) so an unreadable file never *forces* the yfinance fallback. Guards the
  # IBKR-preference against a frozen snapshot (BL-20260626-MES-BASE-STALE: the
  # live-VM pull_mes_ibkr_history.sh stopped 2026-06-14, freezing MES at 06-12,
  # while the daily build kept preferring the stale shard over fresh yfinance).
  python3 -c "
import json, sys, datetime
try:
    last = None
    with open(sys.argv[1]) as f:
        for line in f:
            line = line.strip()
            if line:
                last = line
    ts = json.loads(last)['ts'].replace('Z', '+00:00')
    d = datetime.datetime.fromisoformat(ts)
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    age_days = (datetime.datetime.now(datetime.timezone.utc) - d).total_seconds() / 86400.0
    print('1' if age_days > float(sys.argv[2]) else '0')
except Exception:
    print('0')
" "$1" "$2" 2>/dev/null || echo 0
}

build_mes_market() {
  local base_tf="5m"
  local base_raw="${DATASETS_ROOT}/market_raw/MES/${base_tf}/${DATASET_VERSION}"

  # Fetch the cross-asset/macro side-stream once (S-MLOPT-S12); every MES
  # market_features build below joins it via macro_path when present.
  ensure_mes_macro

  # Prefer deep native MES history pulled from IBKR on the live VM (synced by
  # sync_trainer_data.sh into $DATA_DIR/ibkr_datasets) over the rolling ~60d
  # ES=F yfinance window, when present and non-trivial. See MB-20260528-002 +
  # scripts/ops/pull_mes_ibkr_history.sh.
  local ibkr_src="${DATA_DIR}/ibkr_datasets/market_raw/MES"
  local ibkr_5m="${ibkr_src}/5m/${DATASET_VERSION}/data.jsonl"
  # Freshness gate (BL-20260626-MES-BASE-STALE): prefer the deep IBKR base ONLY
  # when it is current. A frozen snapshot (the live-VM pull stopped 2026-06-14)
  # must NOT be preferred over fresh yfinance, or every MES 5m/15m head trains on
  # stale candles and RG4 can't label any live row past the snapshot date.
  local ibkr_stale=0
  [ -f "$ibkr_5m" ] && ibkr_stale="$(mes_file_stale "$ibkr_5m" "${MES_IBKR_MAX_STALE_DAYS:-5}")"
  if [ -f "$ibkr_5m" ] && [ "$(wc -l < "$ibkr_5m" 2>/dev/null | tr -d ' ')" -gt 1000 ] && [ "$ibkr_stale" != "1" ]; then
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

  if [ -f "$ibkr_5m" ] && [ "$ibkr_stale" = "1" ]; then
    emit "$(printf '{"ts":"%s","status":"warn","detail":"IBKR MES base STALE (last bar older than %s days) — building fresh yfinance ES=F instead; revive pull_mes_ibkr_history.sh on the live VM (point it at the gateway VM 10.0.0.251:4002) to restore deep history. BL-20260626-MES-BASE-STALE"}' "$(iso_now)" "${MES_IBKR_MAX_STALE_DAYS:-5}")"
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

# ---- Crypto funding-rate + open-interest features (S-MLOPT-S11) ----------
# OPT-IN (default OFF): set ICT_BUILD_FUNDING_OI=1 to fetch the Bybit funding/OI
# side-stream and REBUILD the BTCUSDT market_features with it joined (the v4
# funding/OI columns become non-zero). Default off keeps the daily cycle's
# market_features identical (funding columns emit 0.0) — the funding A/B
# manifest (btc-regime-1h-lgbm-funding-v1) only needs this when it is being
# evaluated. Non-fatal: a fetch hiccup just leaves the funding columns at 0.0.
build_funding_oi() {
  [ "${ICT_BUILD_FUNDING_OI:-0}" = "1" ] || return 0
  local fo_dir="${DATASETS_ROOT}/funding_oi/BTCUSDT/v001"
  emit "$(printf '{"ts":"%s","status":"building","family":"funding_oi","symbol":"BTCUSDT"}' "$(iso_now)")"
  set +e
  ICT_OFFVM_BUILD_HOST=1 python -m scripts.ml.fetch_funding_oi \
    --symbol BTCUSDT --start "${MARKET_START}" --end "${MARKET_END}" \
    --oi-interval 1h --out "$fo_dir" >/tmp/funding_oi_$$.out 2>/tmp/funding_oi_$$.err
    local rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    emit "$(printf '{"ts":"%s","status":"skipped","family":"funding_oi","detail":"fetch failed rc=%d"}' "$(iso_now)" "$rc")"
    rm -f "/tmp/funding_oi_$$.out" "/tmp/funding_oi_$$.err"
    return 0
  fi
  rm -f "/tmp/funding_oi_$$.out" "/tmp/funding_oi_$$.err"
  # Rebuild each BTC market_features shard WITH the funding/OI join.
  for tf in 1h 5m 15m; do
    local raw_path="${DATASETS_ROOT}/market_raw/BTCUSDT/${tf}/${DATASET_VERSION}"
    [ -d "$raw_path" ] || continue
    build_family market_features \
      --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
      --source "${raw_path}" --symbol-scope BTCUSDT --timeframe "$tf" --overwrite \
      "market_raw_path=${raw_path}" "funding_oi_path=${fo_dir}" "funding_window_n=168" \
      "vol_window_n=20" "forward_window_m=5" \
      "vol_threshold=0.005" "trend_threshold=0.005" "n_vol_buckets=3"
  done
}

build_funding_oi

# ---- Cross-symbol joint setup_candidates (S-MLOPT-S8 / M14 Phase 1.4) -----
# OPT-IN (default OFF): set ICT_BUILD_XSYM=1 to build the JOINT BTC+MES
# setup_candidates/all/all/v001 dataset that the cross-symbol meta-label
# (ml/configs/setup-candidates-metalabel-xsym-v1.yaml, research_only) trains
# on. Default off keeps the daily cycle byte-identical — the xsym manifest then
# skips cleanly (`ml train` exit 78 / reason=dataset_absent, PR #2886), zero
# cycle harm. Flip ICT_BUILD_XSYM=1 on the trainer unit once the operator
# promotes the xsym model research_only -> shadow (Tier-3) so it retrains on
# fresh data each cycle.
#
# S-MLOPT-S8 eval (trainer-vm-diag #2892-#2894, 2026-06-06): on the only real
# holdout we have (354 closed BTCUSDT trades — MES has ZERO closed trades, so
# the intended BTC->MES transfer is unmeasurable and this is REVERSE transfer),
# the joint model is the FIRST meta-label in the family to edge above the BTC
# majority baseline (acc 0.757 > 0.751) with precision 0.54 = 2.2x the 0.249
# base rate, vs the clean BTC-only ablation (acc 0.681, precision 0.21 — below
# base rate). Caveats: win is on BTC not MES; leak-free purged-WF does not
# corroborate; n=354 is noisy. A qualified positive — observe-in-shadow, not a
# decisive floor-break. Full writeup: docs/sprint-logs/S-MLOPT-S8.md.
#
# MES leg pinned to 15m/v001 (the deep ~29k-bar history the eval used — MES
# contributes only SYNTHETIC candidates, so deeper history = more candidates;
# the daily-refreshed MES v002 is the shallow ~5k-bar IBKR window). The BTC leg
# (1h/$DATASET_VERSION) IS daily-refreshed, so the BTC synthetic rows + the
# growing real-BTC holdout stay fresh. Non-fatal: a build hiccup just leaves the
# xsym manifest to skip on the missing dataset.
build_xsym_setup_candidates() {
  [ "${ICT_BUILD_XSYM:-0}" = "1" ] || return 0
  local btc="${DATASETS_ROOT}/market_raw/BTCUSDT/1h/${DATASET_VERSION}"
  local mes="${DATASETS_ROOT}/market_raw/MES/15m/v001"
  if [ ! -f "$btc/data.jsonl" ] || [ ! -f "$mes/data.jsonl" ]; then
    emit "$(printf '{"ts":"%s","status":"skipped","family":"setup_candidates","scope":"all","detail":"BTC 1h or MES 15m/v001 market_raw missing"}' "$(iso_now)")"
    return 0
  fi
  emit "$(printf '{"ts":"%s","status":"building","family":"setup_candidates","scope":"all"}' "$(iso_now)")"
  set +e
  python -m ml.datasets build setup_candidates \
    --output-dir "$DATASETS_ROOT" --version v001 --source market_raw \
    --symbol-scope all --timeframe all --overwrite \
    -- "market_raw_paths=${btc},${mes}" "live_trades_db=${DB_PATH}" \
    >"/tmp/xsym_$$.out" 2>"/tmp/xsym_$$.err"
  local rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    emit "$(printf '{"ts":"%s","status":"ok","family":"setup_candidates","scope":"all"}' "$(iso_now)")"
  else
    local err; err="$(tail -n 2 "/tmp/xsym_$$.err" 2>/dev/null | tr '\n' ' ' | head -c 300)"
    emit "$(python3 -c "import json,sys; print(json.dumps({'ts':sys.argv[1],'status':'warn','family':'setup_candidates','scope':'all','exit_code':int(sys.argv[2]),'stderr_tail':sys.argv[3]}))" \
      "$(iso_now)" "$rc" "$err")"
  fi
  rm -f "/tmp/xsym_$$.out" "/tmp/xsym_$$.err"
}

build_xsym_setup_candidates

emit "$(printf '{"ts":"%s","status":"build_end","overall_rc":%d}' "$(iso_now)" "$overall_rc")"
exit "$overall_rc"
