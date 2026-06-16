#!/usr/bin/env bash
# scripts/ops/fit_calibrators.sh — fit per-strategy confidence calibrators on
# the TRAINER VM (unified-confidence design § 4a/4b).
#
# Builds the confidence-calibration corpus by running each per-strategy backtest
# harness with `--emit-trades` over the validated multiyear history, then fits
# per-strategy calibrators with `scripts/ml/fit_confidence_calibrators.py`,
# writing:
#
#   artifacts/calibration/calibrators.json   ← per-strategy Calibrator dicts
#   artifacts/calibration/report.json        ← per-strategy reliability report
#
# These are the artifacts the live observe-only conviction stamp loads (read-only,
# fail-permissive) — once published onto the trainer mirror by
# `scripts/ops/publish_trainer_mirror.sh` (calibration/calibrators.json), the live
# loader `src/runtime/conviction_inputs.py::load_calibrators_cached` picks them up.
#
# Data files (trainer-side, design § "Real calibration fit"):
#   - 1h/2h/4h strategies (trend_donchian / fade_breakout / squeeze_breakout /
#     htf_pullback) fit on the 1h multiyear history.
#   - 5m strategies (ict_scalp + fvg_range) fit on the 5m history — this is how
#     the 5m ict_scalp calibrator (and a from-5m fvg_range pass) gets folded in.
#
# Best-effort: every step is wrapped so a single harness failure (or a missing
# data file) is logged and counted but never aborts the cycle. The script always
# exits 0 — the caller (`run_training_cycle.sh`) treats this as a best-effort step
# that must NOT flip the cycle's `overall_rc` (mirrors the datasets_ok / publish
# best-effort pattern).
#
# Environment knobs (defaults match the rest of scripts/ops/):
#   REPO_ROOT        — defaults to /home/ubuntu/ict-trading-bot
#   VENV_DIR         — defaults to "$REPO_ROOT/.venv"
#   CAL_DATA_1H      — 1h multiyear candle CSV (default data/btc_1h_multiyear.csv)
#   CAL_DATA_5M      — 5m candle CSV          (default data/backtest_BTCUSDT_5m.csv)
#   CAL_OUT_DIR      — artifact dir           (default "$REPO_ROOT/artifacts/calibration")
#   CAL_CORPUS_DIR   — corpus jsonl dir       (default "$CAL_OUT_DIR/corpus")
#   CAL_LOG_PATH     — JSONL status log       (default "$REPO_ROOT/runtime_logs/trainer/calibration.jsonl")
#   CAL_SYMBOL       — symbol passed to harnesses (default BTCUSDT)
#
# Exit codes:
#   0   always (best-effort; failures are logged, not propagated)
set -uo pipefail

REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv}"
CAL_DATA_1H="${CAL_DATA_1H:-data/btc_1h_multiyear.csv}"
CAL_DATA_5M="${CAL_DATA_5M:-data/backtest_BTCUSDT_5m.csv}"
CAL_OUT_DIR="${CAL_OUT_DIR:-$REPO_ROOT/artifacts/calibration}"
CAL_CORPUS_DIR="${CAL_CORPUS_DIR:-$CAL_OUT_DIR/corpus}"
CAL_LOG_PATH="${CAL_LOG_PATH:-$REPO_ROOT/runtime_logs/trainer/calibration.jsonl}"
CAL_SYMBOL="${CAL_SYMBOL:-BTCUSDT}"

iso_now() { date -u +'%Y-%m-%dT%H:%M:%S+00:00'; }

emit() {
  # emit <event-json> — append a JSONL row to CAL_LOG_PATH AND echo to stdout.
  local payload="$1"
  mkdir -p "$(dirname "$CAL_LOG_PATH")"
  printf '%s\n' "$payload" >> "$CAL_LOG_PATH"
  printf '%s\n' "$payload"
}

# --- Env checks (soft: log + exit 0, never hard-fail the caller) -----------
if [ ! -d "$REPO_ROOT/.git" ]; then
  emit "$(printf '{"ts":"%s","status":"env_error","detail":"REPO_ROOT is not a git repo: %s"}' \
    "$(iso_now)" "$REPO_ROOT")"
  exit 0
fi
if [ ! -d "$VENV_DIR" ]; then
  emit "$(printf '{"ts":"%s","status":"env_error","detail":"venv not found at %s"}' \
    "$(iso_now)" "$VENV_DIR")"
  exit 0
fi

cd "$REPO_ROOT"
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

mkdir -p "$CAL_CORPUS_DIR"

emit "$(printf '{"ts":"%s","status":"fit_start","corpus_dir":"%s","out_dir":"%s"}' \
  "$(iso_now)" "$CAL_CORPUS_DIR" "$CAL_OUT_DIR")"

# run_harness <slug> <script> <data-file> — emit one corpus jsonl, best-effort.
# Logs ok/warn/skipped and the trade-row count; never flips any rc.
run_harness() {
  local slug="$1" script="$2" data="$3"
  local emit_path="${CAL_CORPUS_DIR}/${slug}.jsonl"
  if [ ! -f "$data" ]; then
    emit "$(printf '{"ts":"%s","status":"skipped","strategy":"%s","detail":"data file not found: %s"}' \
      "$(iso_now)" "$slug" "$data")"
    return 0
  fi
  set +e
  python "$script" --data "$data" --symbol "$CAL_SYMBOL" --emit-trades "$emit_path" \
    >"/tmp/cal_${slug}_$$.out" 2>"/tmp/cal_${slug}_$$.err"
  local rc=$?
  set -e
  local n=0
  [ -f "$emit_path" ] && n="$(wc -l < "$emit_path" 2>/dev/null | tr -d ' ')"
  if [ "$rc" -eq 0 ]; then
    emit "$(printf '{"ts":"%s","status":"ok","strategy":"%s","data":"%s","trades":%s}' \
      "$(iso_now)" "$slug" "$data" "${n:-0}")"
  else
    local err; err="$(tail -n 3 "/tmp/cal_${slug}_$$.err" 2>/dev/null | tr '\n' ' ' | head -c 400)"
    emit "$(python3 -c "import json,sys; print(json.dumps({'ts':sys.argv[1],'status':'warn','strategy':sys.argv[2],'data':sys.argv[3],'exit_code':int(sys.argv[4]),'trades':int(sys.argv[5]),'stderr_tail':sys.argv[6]}))" \
      "$(iso_now)" "$slug" "$data" "$rc" "${n:-0}" "$err")"
  fi
  rm -f "/tmp/cal_${slug}_$$.out" "/tmp/cal_${slug}_$$.err"
  return 0
}

# --- Build the corpus -------------------------------------------------------
# 1h/2h/4h strategies fit on the 1h multiyear history.
run_harness trend_donchian      scripts/backtest_trend.py     "$CAL_DATA_1H"
run_harness fade_breakout       scripts/backtest_fade.py      "$CAL_DATA_1H"
run_harness squeeze_breakout    scripts/backtest_squeeze.py   "$CAL_DATA_1H"
run_harness htf_pullback        scripts/backtest_pullback.py  "$CAL_DATA_1H"

# 5m strategies fit on the 5m history. ict_scalp is 5m-native; fvg_range gets a
# from-5m pass here (folding the 5m ict_scalp calibrator in — design § 4b).
run_harness ict_scalp           scripts/backtest_ict_scalp.py "$CAL_DATA_5M"
run_harness fvg_range           scripts/backtest_fvg_range.py "$CAL_DATA_5M"

# --- Fit calibrators over the whole corpus ---------------------------------
# The fitter groups by the `strategy` field inside each row (each harness stamps
# its own slug), so a single --emit-dir pass produces one calibrator per strategy.
set +e
python scripts/ml/fit_confidence_calibrators.py \
  --emit-dir "$CAL_CORPUS_DIR" \
  --out-calibrators "$CAL_OUT_DIR/calibrators.json" \
  --out-report "$CAL_OUT_DIR/report.json" \
  >"/tmp/cal_fit_$$.out" 2>"/tmp/cal_fit_$$.err"
fit_rc=$?
set -e
if [ "$fit_rc" -eq 0 ]; then
  n_cal=0
  [ -f "$CAL_OUT_DIR/calibrators.json" ] && \
    n_cal="$(python3 -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" "$CAL_OUT_DIR/calibrators.json" 2>/dev/null || echo 0)"
  emit "$(printf '{"ts":"%s","status":"fit_ok","calibrators":%s,"calibrators_path":"%s","report_path":"%s"}' \
    "$(iso_now)" "${n_cal:-0}" "$CAL_OUT_DIR/calibrators.json" "$CAL_OUT_DIR/report.json")"
else
  err="$(tail -n 3 "/tmp/cal_fit_$$.err" 2>/dev/null | tr '\n' ' ' | head -c 400)"
  emit "$(python3 -c "import json,sys; print(json.dumps({'ts':sys.argv[1],'status':'fit_warn','exit_code':int(sys.argv[2]),'stderr_tail':sys.argv[3]}))" \
    "$(iso_now)" "$fit_rc" "$err")"
fi
rm -f "/tmp/cal_fit_$$.out" "/tmp/cal_fit_$$.err"

emit "$(printf '{"ts":"%s","status":"fit_end"}' "$(iso_now)")"
exit 0
