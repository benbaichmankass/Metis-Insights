#!/usr/bin/env bash
# scripts/ops/run_mes_training.sh — one-shot "starter" training cycle
# scoped to the MES symbol, across the three live strategies
# (turtle_soup, vwap, ict_scalp_5m).
#
# Why this is separate from run_training_cycle.sh:
#   * run_training_cycle.sh does `git reset --hard origin/main` and trains
#     every baseline manifest over ALL symbols. This script trains the
#     MES-scoped manifests (ml/configs/mes-*.yaml) and does NOT touch the
#     checked-out branch — so it can run from a feature branch before the
#     MES manifests are merged to main.
#   * It pulls "as much historical data as the pipeline supports" for MES:
#       - journal-backed families (per-strategy win-rate / setup-quality /
#         execution-quality) over every closed MES trade in trade_journal.db
#       - YEARS of MES daily market history via the `yfinance_offvm` adapter
#         (default ticker ES=F — same index level as MES, deepest history),
#         feeding the MES regime-classifier baseline.
#
# Designed to run detached and survive the launching session:
#   nohup bash scripts/ops/run_mes_training.sh \
#     > runtime_logs/trainer/mes_training.out 2>&1 &
#
# Progress: tail $MES_LOG_PATH (default runtime_logs/trainer/mes_training_cycle.jsonl).
#
# Environment knobs (all have sane trainer-VM defaults):
#   REPO_ROOT          /home/ubuntu/ict-trading-bot
#   VENV_DIR           $REPO_ROOT/.venv
#   DATA_DIR           $REPO_ROOT/data            (trade_journal.db lands here)
#   DATASETS_ROOT      $REPO_ROOT/datasets-out
#   EXPERIMENTS_ROOT   $REPO_ROOT/ml/experiments-runs
#   REGISTRY_ROOT      $REPO_ROOT/ml/registry-store
#   DATASET_VERSION    v001
#   MES_SYMBOL         MES
#   MES_MARKET_TICKER  (unset -> adapter default ES=F)
#   MES_MARKET_START   2000-01-01
#   MES_MARKET_TF      1d
#   MES_LOG_PATH       $REPO_ROOT/runtime_logs/trainer/mes_training_cycle.jsonl
#
# Exit codes: 0 = every manifest trained or cleanly skipped (empty data);
#             1 = a manifest failed with a non-data error.
set -uo pipefail

REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv}"
DATA_DIR="${DATA_DIR:-$REPO_ROOT/data}"
DATASETS_ROOT="${DATASETS_ROOT:-$REPO_ROOT/datasets-out}"
EXPERIMENTS_ROOT="${EXPERIMENTS_ROOT:-$REPO_ROOT/ml/experiments-runs}"
REGISTRY_ROOT="${REGISTRY_ROOT:-$REPO_ROOT/ml/registry-store}"
DATASET_VERSION="${DATASET_VERSION:-v001}"
MES_SYMBOL="${MES_SYMBOL:-MES}"
MES_MARKET_TICKER="${MES_MARKET_TICKER:-}"
MES_MARKET_START="${MES_MARKET_START:-2000-01-01}"
MES_MARKET_TF="${MES_MARKET_TF:-1d}"
MES_LOG_PATH="${MES_LOG_PATH:-$REPO_ROOT/runtime_logs/trainer/mes_training_cycle.jsonl}"

iso_now() { date -u +'%Y-%m-%dT%H:%M:%S+00:00'; }

emit() {
  local payload="$1"
  mkdir -p "$(dirname "$MES_LOG_PATH")"
  printf '%s\n' "$payload" >> "$MES_LOG_PATH"
  printf '%s\n' "$payload"
}

json_kv() {
  # json_kv status=foo manifest=bar ... -> compact JSON object incl ts.
  python3 - "$@" <<'PY'
import json, sys
obj = {"ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
       .isoformat(timespec="seconds")}
for pair in sys.argv[1:]:
    k, _, v = pair.partition("=")
    obj[k] = v
print(json.dumps(obj))
PY
}

if [ ! -d "$REPO_ROOT/.git" ]; then
  emit "$(json_kv status=env_error detail="REPO_ROOT not a git repo: $REPO_ROOT")"
  exit 2
fi
cd "$REPO_ROOT"

HEAD_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
emit "$(json_kv status=cycle_start kind=mes head="$HEAD_SHA" branch="$BRANCH" market_start="$MES_MARKET_START" market_tf="$MES_MARKET_TF")"

# --- Venv ------------------------------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
  python3.11 -m venv "$VENV_DIR" 2>/dev/null || python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install --quiet --upgrade pip
  "$VENV_DIR/bin/pip" install --quiet -r requirements.txt || true
  emit "$(json_kv status=venv_created path="$VENV_DIR")"
fi
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

# yfinance is required for the MES market-history pull; install on demand.
if ! python -c "import yfinance" 2>/dev/null; then
  emit "$(json_kv status=info detail="installing yfinance for MES market pull")"
  pip install --quiet "yfinance>=0.2" && emit "$(json_kv status=yfinance_ok)" \
    || emit "$(json_kv status=yfinance_warn detail="install failed; regime baseline will be skipped")"
fi

# --- Sync journal from live VM (best-effort) -------------------------------
if bash scripts/ops/sync_trainer_data.sh; then
  emit "$(json_kv status=sync_ok)"
else
  emit "$(json_kv status=sync_warn detail="sync_trainer_data.sh non-zero; using cached journal")"
fi

DB_PATH="${DATA_DIR}/trade_journal.db"

build_journal_family() {
  # build_journal_family <family> <extra k=v ...>
  local family="$1"; shift
  if [ ! -f "$DB_PATH" ]; then
    emit "$(json_kv status=build_skipped family="$family" detail="no trade_journal.db at $DB_PATH")"
    return 0
  fi
  emit "$(json_kv status=building family="$family" symbol="$MES_SYMBOL")"
  if python -m ml build-dataset "$family" \
      --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
      --source trade_journal.db --symbol-scope "$MES_SYMBOL" --overwrite \
      "db_path=${DB_PATH}" "symbol=${MES_SYMBOL}" "$@" \
      >/tmp/mesbld_${family}_$$.out 2>/tmp/mesbld_${family}_$$.err; then
    emit "$(json_kv status=build_ok family="$family")"
  else
    emit "$(json_kv status=build_warn family="$family" detail="$(tail -n1 /tmp/mesbld_${family}_$$.err 2>/dev/null | head -c 200)")"
  fi
  rm -f /tmp/mesbld_${family}_$$.out /tmp/mesbld_${family}_$$.err
}

# --- Build MES-scoped journal datasets -------------------------------------
build_journal_family trade_outcomes
build_journal_family setup_labels "risk_pct=1.0" "r_cap=3.0"
build_journal_family execution_quality "slippage_cap_bps=200.0"

# --- Build MES market history (years of daily bars via yfinance) -----------
export ICT_OFFVM_BUILD_HOST=1
MARKET_RAW_DIR="${DATASETS_ROOT}/market_raw/${MES_SYMBOL}/${MES_MARKET_TF}/${DATASET_VERSION}"
if python -c "import yfinance" 2>/dev/null; then
  emit "$(json_kv status=building family=market_raw symbol="$MES_SYMBOL")"
  ticker_arg=()
  [ -n "$MES_MARKET_TICKER" ] && ticker_arg=("ticker=${MES_MARKET_TICKER}")
  if python -m ml build-dataset market_raw \
      --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
      --source yfinance_offvm --symbol-scope "$MES_SYMBOL" --timeframe "$MES_MARKET_TF" --overwrite \
      "adapter=yfinance_offvm" "symbol=${MES_SYMBOL}" "start=${MES_MARKET_START}" "${ticker_arg[@]}" \
      >/tmp/mesraw_$$.out 2>/tmp/mesraw_$$.err; then
    emit "$(json_kv status=build_ok family=market_raw)"
    if [ -d "$MARKET_RAW_DIR" ]; then
      emit "$(json_kv status=building family=market_features symbol="$MES_SYMBOL")"
      if python -m ml build-dataset market_features \
          --output-dir "$DATASETS_ROOT" --version "$DATASET_VERSION" \
          --source "$MARKET_RAW_DIR" --symbol-scope "$MES_SYMBOL" --timeframe "$MES_MARKET_TF" --overwrite \
          "market_raw_path=${MARKET_RAW_DIR}" "vol_window_n=20" "forward_window_m=5" \
          "vol_threshold=0.01" "trend_threshold=0.01" "n_vol_buckets=3" \
          >/tmp/mesfeat_$$.out 2>/tmp/mesfeat_$$.err; then
        emit "$(json_kv status=build_ok family=market_features)"
      else
        emit "$(json_kv status=build_warn family=market_features detail="$(tail -n1 /tmp/mesfeat_$$.err 2>/dev/null | head -c 200)")"
      fi
    fi
  else
    emit "$(json_kv status=build_warn family=market_raw detail="$(tail -n1 /tmp/mesraw_$$.err 2>/dev/null | head -c 200)")"
  fi
  rm -f /tmp/mesraw_$$.out /tmp/mesraw_$$.err /tmp/mesfeat_$$.out /tmp/mesfeat_$$.err
else
  emit "$(json_kv status=build_skipped family=market_raw detail="yfinance unavailable")"
fi

# --- Train each MES manifest -----------------------------------------------
MANIFESTS=(
  ml/configs/mes-trade-outcome-winrate.yaml
  ml/configs/mes-setup-quality.yaml
  ml/configs/mes-execution-quality.yaml
  ml/configs/mes-regime-classifier.yaml
)
overall_rc=0
for manifest in "${MANIFESTS[@]}"; do
  [ -f "$manifest" ] || { emit "$(json_kv status=manifest_missing manifest="$manifest")"; overall_rc=1; continue; }
  python -m ml train "$manifest" \
    --datasets-root "$DATASETS_ROOT" \
    --experiments-root "$EXPERIMENTS_ROOT" \
    --registry-root "$REGISTRY_ROOT" \
    >/tmp/mestrain_$$.out 2>/tmp/mestrain_$$.err
  rc=$?
  summary="$(grep -E '^\{' /tmp/mestrain_$$.out | tail -n1)"
  [ -z "$summary" ] && summary='{}'
  if [ "$rc" -eq 0 ]; then
    mid="$(printf '%s' "$summary" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("model_id") or "")' 2>/dev/null)"
    emit "$(json_kv status=manifest_ok manifest="$manifest" model_id="$mid")"
  elif [ "$rc" -eq 78 ]; then
    reason="$(printf '%s' "$summary" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("reason") or "empty_dataset")' 2>/dev/null)"
    emit "$(json_kv status=manifest_skipped manifest="$manifest" reason="${reason:-empty_dataset}")"
  else
    emit "$(json_kv status=manifest_failed manifest="$manifest" exit_code="$rc" stderr_tail="$(tail -n1 /tmp/mestrain_$$.err 2>/dev/null | head -c 300)")"
    overall_rc=1
  fi
  rm -f /tmp/mestrain_$$.out /tmp/mestrain_$$.err
done

emit "$(json_kv status=cycle_end kind=mes overall_rc="$overall_rc")"
exit "$overall_rc"
