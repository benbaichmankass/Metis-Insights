#!/bin/bash
# SRQ-20260618-001/-002 soft regime-weight re-sweep (RQ-20260927-004).
#
# The 2026-06-18 hard-ADX-gate hypothesis for this exact cell family was
# REFUTED by its own holdout check the same day: four PER-CELL TUNED
# thresholds passed every fold at the exact 5-fold/0.40 split and failed at
# k=6, k=7, train_frac 0.35/0.45 (in-sample artifacts). The 2026-08-11
# stale-field correction says the surviving lead is explicitly "a SOFT
# regime weight ... never a hard gate."
#
# This script is the direct sibling of m15_ws_c_kfold.sh (same cells, same
# 5-fold/0.40 walk-forward gate, same 7.5/15 bps fee arms) with ONE change:
# --adx-soft-weight-floor/--adx-soft-weight-ceiling (scripts/research/regime_weight.py)
# replace --adx-min. This is deliberately NOT a per-cell tuned band — every
# cell gets the SAME fixed [15, 25] ramp (centered on the original flat
# adx_min=20 arm, which is the one round of the 06-18 sweep that was NOT
# overfit — round 1's flat-threshold read was directionally real: helped
# ETH/ADA, hurt XRP/SOL/AVAX). A single shared band removes the exact
# degree of freedom (a per-cell threshold pick) that produced the in-sample
# artifacts, so this run cannot fail for the same reason round 2 did.
#
# Pre-registered decision rule: research/queue/RQ-20260927-004.yaml
# (registered BEFORE this script was run).
set -u
cd "$(dirname "$0")/../.."
source /home/ubuntu/ict-trading-bot/.venv/bin/activate 2>/dev/null \
  || source /home/ubuntu/ict-trading-bot/venv/bin/activate 2>/dev/null || true

R=results/m15_ws_c_soft_weight_kfold
mkdir -p "$R"
WF_END="2026-06-11"
FOLDS=5
TRAIN_FRAC=0.4
FLOOR="${SOFT_WEIGHT_FLOOR:-15}"
CEILING="${SOFT_WEIGHT_CEILING:-25}"
WEIGHT_MIN="${SOFT_WEIGHT_MIN:-0.0}"

data_start() { head -2 "$1" | tail -1 | cut -d, -f1 | cut -dT -f1 | cut -d' ' -f1; }

fold_net() {  # fold_net <label> <emit_base> <emit_2x> <wf_start>
  python3 scripts/ops/m15_ws_b_fold_report.py --mode net \
    --emit "$2" --emit-2x "$3" --fee-bps 7.5 \
    --wf-start "$4" --wf-end "$WF_END" --folds "$FOLDS" --train-frac "$TRAIN_FRAC" \
    --label "$1" --json "$R/fold_$1.json" || echo "REPORT_FAILED $1"
}

for SYM in ETHUSDT ADAUSDT SOLUSDT XRPUSDT AVAXUSDT; do
  D="data/${SYM}_15m.csv"
  [ -f "$D" ] || { echo "MISSING $D"; continue; }
  WS=$(data_start "$D")
  for FEE in 7.5 15.0; do
    echo "=== pullback_${SYM}_2h soft-weight[$FLOOR,$CEILING,min=$WEIGHT_MIN] fee=$FEE ==="
    python3 scripts/backtest_pullback.py --data "$D" --resample 2h --timeframe 2h \
      --symbol "$SYM" --fee-bps-roundtrip "$FEE" \
      --adx-soft-weight-floor "$FLOOR" --adx-soft-weight-ceiling "$CEILING" \
      --adx-soft-weight-min "$WEIGHT_MIN" \
      --emit-trades "$R/pullback_${SYM}_2h_fee${FEE}_trades.jsonl" \
      --json "$R/pullback_${SYM}_2h_fee${FEE}.json" || echo "RUN_FAILED pullback_$SYM-$FEE"
    echo "=== trend_${SYM}_4h soft-weight[$FLOOR,$CEILING,min=$WEIGHT_MIN] fee=$FEE ==="
    python3 scripts/backtest_trend.py --data "$D" --resample 4h --timeframe 4h \
      --symbol "$SYM" --fee-bps-roundtrip "$FEE" \
      --adx-soft-weight-floor "$FLOOR" --adx-soft-weight-ceiling "$CEILING" \
      --adx-soft-weight-min "$WEIGHT_MIN" \
      --emit-trades "$R/trend_${SYM}_4h_fee${FEE}_trades.jsonl" \
      --json "$R/trend_${SYM}_4h_fee${FEE}.json" || echo "RUN_FAILED trend_$SYM-$FEE"
  done
  fold_net "pullback_${SYM}_2h_softweight" "$R/pullback_${SYM}_2h_fee7.5_trades.jsonl" \
    "$R/pullback_${SYM}_2h_fee15.0_trades.jsonl" "$WS"
  fold_net "trend_${SYM}_4h_softweight" "$R/trend_${SYM}_4h_fee7.5_trades.jsonl" \
    "$R/trend_${SYM}_4h_fee15.0_trades.jsonl" "$WS"
done

echo "WS_C_SOFT_WEIGHT_KFOLD_DONE floor=$FLOOR ceiling=$CEILING weight_min=$WEIGHT_MIN"
grep -h '"label"\|"verdict"' "$R"/fold_*.json | paste - - || true
