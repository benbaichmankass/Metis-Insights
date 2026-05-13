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
#   DATASET_VERSION    — defaults to v001
#   MARKET_START       — defaults to 2024-01-01
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
DATASET_VERSION="${DATASET_VERSION:-v001}"
MARKET_START="${MARKET_START:-2024-01-01}"
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
MARKET_RAW_PATH="${DATASETS_ROOT}/market_raw/BTCUSDT/1h/${DATASET_VERSION}"

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
  -- "db_path=${DB_PATH}"

build_family trade_outcomes \
  --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
  --source "trade_journal.db" --overwrite \
  -- "db_path=${DB_PATH}"

build_family execution_quality \
  --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
  --source "trade_journal.db" --overwrite \
  -- "db_path=${DB_PATH}" "slippage_cap_bps=200.0"

if [ -f "$ACCOUNTS_YAML" ]; then
  build_family account_context \
    --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
    --source "trade_journal.db" --overwrite \
    -- "db_path=${DB_PATH}" "accounts_yaml_path=${ACCOUNTS_YAML}"
else
  emit "$(printf '{"ts":"%s","status":"skipped","family":"account_context","detail":"config/accounts.yaml not found"}' "$(iso_now)")"
fi

build_family setup_labels \
  --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
  --source "trade_journal.db" --overwrite \
  -- "db_path=${DB_PATH}" "risk_pct=1.0" "r_cap=3.0"

if [ -f "$AUDIT_PATH" ]; then
  build_family setup_labels_audit \
    --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
    --source "trade_journal.db" --overwrite \
    -- "db_path=${DB_PATH}" "audit_log_path=${AUDIT_PATH}" \
       "risk_pct=1.0" "r_cap=3.0" "match_window_seconds=60"
else
  emit "$(printf '{"ts":"%s","status":"skipped","family":"setup_labels_audit","detail":"signal_audit.jsonl absent — no signals fired yet"}' "$(iso_now)")"
fi

# ---- review_journal (comms/ in the repo) --------------------------------
# Produces 0 rows until the operator answers health-review prompts.
build_family review_journal \
  --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
  --source "comms-artifacts" --overwrite \
  -- "comms_root=${COMMS_ROOT}" "include_archive=true"

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

build_family market_raw \
  --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
  --source "bybit_v5_offvm" --symbol-scope BTCUSDT --timeframe 1h --overwrite \
  -- "adapter=bybit_v5_offvm" "symbol=BTCUSDT" "timeframe=1h" \
     "start=${MARKET_START}" "end=${MARKET_END}"

if [ -d "$MARKET_RAW_PATH" ]; then
  build_family market_features \
    --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
    --source "${MARKET_RAW_PATH}" --symbol-scope BTCUSDT --timeframe 1h --overwrite \
    -- "market_raw_path=${MARKET_RAW_PATH}" "vol_window_n=20" "forward_window_m=5" \
       "vol_threshold=0.005" "trend_threshold=0.005" "n_vol_buckets=3"
else
  emit "$(printf '{"ts":"%s","status":"skipped","family":"market_features","detail":"market_raw path not found; regime-classifier baseline skipped"}' "$(iso_now)")"
fi

emit "$(printf '{"ts":"%s","status":"build_end","overall_rc":%d}' "$(iso_now)" "$overall_rc")"
exit "$overall_rc"
