#!/bin/bash
# M15 WS-C follow-up — k-fold anchored walk-forward on the robust alt
# cells (the WS-B promotion gate: positive in EVERY OOS fold at 7.5 bps
# + total positive at 15 bps), in priority order per
# docs/research/m15-ws-c-alt-sweep-2026-06-11.md:
#   1. pullback 2h: ETH, ADA, SOL, XRP
#   2. trend 4h:    ETH, ADA, AVAX, SOL, XRP
#   3. ict_scalp 5m (research continuation): ADA, AVAX, ETH, LINK
#
# Params are the WS-C screening params (fixed — no per-fold fitting), so
# each harness runs ONCE full-series with --emit-trades and
# m15_ws_b_fold_report.py buckets by entry_time. wf-start is derived per
# symbol from the data's first row (late listings would otherwise get
# empty early folds and an unfair FAIL).
set -u
cd "$(dirname "$0")/../.."
source /home/ubuntu/ict-trading-bot/.venv/bin/activate 2>/dev/null \
  || source /home/ubuntu/ict-trading-bot/venv/bin/activate 2>/dev/null || true

R=results/m15_ws_c_kfold
mkdir -p "$R"
WF_END="2026-06-11"
FOLDS=5
TRAIN_FRAC=0.4

data_start() { head -2 "$1" | tail -1 | cut -d, -f1 | cut -dT -f1 | cut -d' ' -f1; }

fold_net() {  # fold_net <label> <emit_base> <emit_2x> <wf_start>
  python3 scripts/ops/m15_ws_b_fold_report.py --mode net \
    --emit "$2" --emit-2x "$3" --fee-bps 7.5 \
    --wf-start "$4" --wf-end "$WF_END" --folds "$FOLDS" --train-frac "$TRAIN_FRAC" \
    --label "$1" --json "$R/fold_$1.json" || echo "REPORT_FAILED $1"
}

# ---- priority 1+2: pullback 2h + trend 4h (fast, 15m-resampled) ----
for SYM in ETHUSDT ADAUSDT SOLUSDT XRPUSDT AVAXUSDT; do
  D="data/${SYM}_15m.csv"
  [ -f "$D" ] || { echo "MISSING $D"; continue; }
  WS=$(data_start "$D")
  for FEE in 7.5 15.0; do
    echo "=== pullback_${SYM}_2h fee=$FEE ==="
    python3 scripts/backtest_pullback.py --data "$D" --resample 2h --timeframe 2h \
      --symbol "$SYM" --fee-bps-roundtrip "$FEE" \
      --emit-trades "$R/pullback_${SYM}_2h_fee${FEE}_trades.jsonl" \
      --json "$R/pullback_${SYM}_2h_fee${FEE}.json" || echo "RUN_FAILED pullback_$SYM-$FEE"
    echo "=== trend_${SYM}_4h fee=$FEE ==="
    python3 scripts/backtest_trend.py --data "$D" --resample 4h --timeframe 4h \
      --symbol "$SYM" --fee-bps-roundtrip "$FEE" \
      --emit-trades "$R/trend_${SYM}_4h_fee${FEE}_trades.jsonl" \
      --json "$R/trend_${SYM}_4h_fee${FEE}.json" || echo "RUN_FAILED trend_$SYM-$FEE"
  done
  fold_net "pullback_${SYM}_2h" "$R/pullback_${SYM}_2h_fee7.5_trades.jsonl" \
    "$R/pullback_${SYM}_2h_fee15.0_trades.jsonl" "$WS"
  fold_net "trend_${SYM}_4h" "$R/trend_${SYM}_4h_fee7.5_trades.jsonl" \
    "$R/trend_${SYM}_4h_fee15.0_trades.jsonl" "$WS"
done

# ---- priority 3: ict_scalp 5m (heavy; research continuation) ----
for SYM in ADAUSDT AVAXUSDT ETHUSDT LINKUSDT; do
  D="data/${SYM}_5m.csv"
  [ -f "$D" ] || { echo "MISSING $D"; continue; }
  WS=$(data_start "$D")
  echo "=== ict_scalp_${SYM}_5m full-series ==="
  python3 scripts/backtest_ict_scalp.py --data "$D" --timeframe 5m --symbol "$SYM" \
    --htf-rule 1h --ignore-yaml \
    --emit-trades "$R/ict_scalp_${SYM}_5m_full_trades.jsonl" \
    --json "$R/ict_scalp_${SYM}_5m_full.json" || echo "RUN_FAILED ict_$SYM"
  python3 scripts/ops/m15_ws_b_fold_report.py --mode ict \
    --emit "$R/ict_scalp_${SYM}_5m_full_trades.jsonl" --fee-bps 7.5 \
    --wf-start "$WS" --wf-end "$WF_END" --folds "$FOLDS" --train-frac "$TRAIN_FRAC" \
    --label "ict_scalp_${SYM}_5m" --json "$R/fold_ict_scalp_${SYM}_5m.json" \
    || echo "REPORT_FAILED ict_$SYM"
done

echo "WS_C_KFOLD_DONE"
grep -h '"label"\|"verdict"' "$R"/fold_*.json | paste - - || true
