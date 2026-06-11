#!/bin/bash
# M15 WS-B — SPY/QQQ intraday promotion validation (k-fold anchored
# walk-forward, net of fee, 2x fee headroom).
#
# Runs detached on the trainer VM from the m15-phase0 worktree against the
# CORRECTED RTH datasets (data/{SPY,QQQ}_5m_rth.csv, pass-2 fetch 7dccf4d).
# Families: ict_scalp 5m (no in-harness fee model — exact per-trade fee in
# the fold report) and fvg_range 15m (in-harness fee; rerun at 2x for the
# headroom leg). Params are the harness defaults the Phase-0 screening ran
# (--ignore-yaml for ict_scalp), so the harness runs ONCE over the full
# series per fee level and the fold report buckets trades by entry_time —
# see scripts/ops/m15_ws_b_fold_report.py for why that equals per-fold
# reruns for fixed params.
#
# Gate (operator-set): net R > 0 in EVERY OOS fold at 2.0 bps AND total
# OOS net R > 0 at 4.0 bps.
set -u
cd "$(dirname "$0")/../.."
source /home/ubuntu/ict-trading-bot/.venv/bin/activate 2>/dev/null \
  || source /home/ubuntu/ict-trading-bot/venv/bin/activate 2>/dev/null || true

R=results/m15_ws_b
mkdir -p "$R"
WF_START="2019-01-01"
WF_END="2026-06-11"
FOLDS=5
TRAIN_FRAC=0.4

for SYM in SPY QQQ; do
  D="data/${SYM}_5m_rth.csv"
  [ -f "$D" ] || { echo "MISSING $D — skipping $SYM"; continue; }

  echo "=== ict_scalp ${SYM} 5m full-series ==="
  python3 scripts/backtest_ict_scalp.py --data "$D" --timeframe 5m --symbol "$SYM" \
    --htf-rule 1h --ignore-yaml \
    --emit-trades "$R/ict_scalp_${SYM}_5m_full_trades.jsonl" \
    --json "$R/ict_scalp_${SYM}_5m_full.json" || echo "RUN_FAILED ict_scalp_$SYM"

  for FEE in 2.0 4.0; do
    echo "=== fvg_range ${SYM} 15m fee=${FEE} ==="
    python3 scripts/backtest_fvg_range.py --data "$D" --resample 15m --timeframe 15m \
      --symbol "$SYM" --fee-bps-roundtrip "$FEE" \
      --emit-trades "$R/fvg_range_${SYM}_15m_fee${FEE}_trades.jsonl" \
      --json "$R/fvg_range_${SYM}_15m_fee${FEE}.json" || echo "RUN_FAILED fvg_range_${SYM}_${FEE}"
  done

  echo "=== fold reports ${SYM} ==="
  python3 scripts/ops/m15_ws_b_fold_report.py --mode ict \
    --emit "$R/ict_scalp_${SYM}_5m_full_trades.jsonl" --fee-bps 2.0 \
    --wf-start "$WF_START" --wf-end "$WF_END" --folds "$FOLDS" --train-frac "$TRAIN_FRAC" \
    --label "ict_scalp_${SYM}_5m" --json "$R/fold_ict_scalp_${SYM}.json" \
    || echo "REPORT_FAILED ict_$SYM"
  python3 scripts/ops/m15_ws_b_fold_report.py --mode net \
    --emit "$R/fvg_range_${SYM}_15m_fee2.0_trades.jsonl" \
    --emit-2x "$R/fvg_range_${SYM}_15m_fee4.0_trades.jsonl" --fee-bps 2.0 \
    --wf-start "$WF_START" --wf-end "$WF_END" --folds "$FOLDS" --train-frac "$TRAIN_FRAC" \
    --label "fvg_range_${SYM}_15m" --json "$R/fold_fvg_range_${SYM}.json" \
    || echo "REPORT_FAILED fvg_$SYM"
done

echo "WS_B_DONE"
grep -h '"verdict"' "$R"/fold_*.json || true
