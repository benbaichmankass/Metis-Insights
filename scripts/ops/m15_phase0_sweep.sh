#!/bin/bash
# M15 Phase 0 — generalization sweep over the candidate-market datasets.
#
# Runs detached on the trainer VM from the m15-phase0 worktree AFTER
# scripts/ops/m15_phase0_fetch.sh has populated ./data/. Sequential by
# design (1-OCPU trainer). Results land as one JSON per run under
# results/m15_phase0/ plus a SUMMARY.md table.
#
# Method (docs/research/market-alternatives-2026-06-10.md §6 Phase 0,
# per the WS-A approach): net-of-fee (2.0 bps roundtrip — conservative
# for FX spread-only and US ETF spreads), train/OOS split, harness
# defaults or live-mirror params — this is generalization SCREENING;
# per-symbol re-tuning only happens for legs that show signal.
#
# Caveat: backtest_ict_scalp.py has NO fee model (gross==net) and no
# --start/--end — windows are pre-split CSVs and per-trade rows are
# emitted for net-of-fee post-processing.
set -u
cd "$(dirname "$0")/../.."
source /home/ubuntu/ict-trading-bot/.venv/bin/activate 2>/dev/null \
  || source /home/ubuntu/ict-trading-bot/venv/bin/activate 2>/dev/null || true

R=results/m15_phase0
mkdir -p "$R"
SPLIT_INTRADAY="2025-01-01"   # 15m/5m data starts 2019 -> 6y train / 1.5y OOS
SPLIT_DAILY="2022-01-01"      # 1d data starts 2010 -> 12y train / 4.5y OOS
FEE="2.0"

run() {  # run <out-name> <cmd...>
  local out="$1"; shift
  echo "=== $out ==="
  "$@" --json "$R/$out.json" || echo "RUN_FAILED $out"
}

split_csv() {  # split_csv <in.csv> <boundary> -> writes <in>_train.csv / <in>_oos.csv
  python3 - "$1" "$2" <<'PYEOF'
import sys, pandas as pd
src, boundary = sys.argv[1], sys.argv[2]
df = pd.read_csv(src, parse_dates=["timestamp"])
base = src[:-4]
df[df["timestamp"] < boundary].to_csv(base + "_train.csv", index=False)
df[df["timestamp"] >= boundary].to_csv(base + "_oos.csv", index=False)
print(f"split {src} at {boundary}: train={len(df[df['timestamp'] < boundary])} oos={len(df[df['timestamp'] >= boundary])}")
PYEOF
}

# ---------- FX leg (15m source; EURUSD / GBPUSD / XAUUSD) ----------
for SYM in EURUSD GBPUSD XAUUSD; do
  D="data/${SYM}_15m.csv"
  [ -f "$D" ] || { echo "MISSING $D — skipping $SYM"; continue; }
  for WIN in train oos; do
    if [ "$WIN" = train ]; then WARGS=(--end "$SPLIT_INTRADAY"); else WARGS=(--start "$SPLIT_INTRADAY"); fi
    run "trend_${SYM}_1h_${WIN}" python3 scripts/backtest_trend.py --data "$D" --resample 1h --timeframe 1h \
      --symbol "$SYM" --fee-bps-roundtrip "$FEE" "${WARGS[@]}"
    run "trend_${SYM}_4h_${WIN}" python3 scripts/backtest_trend.py --data "$D" --resample 4h --timeframe 4h \
      --symbol "$SYM" --fee-bps-roundtrip "$FEE" "${WARGS[@]}"
    run "pullback_${SYM}_2h_${WIN}" python3 scripts/backtest_pullback.py --data "$D" --resample 2h --timeframe 2h \
      --symbol "$SYM" --fee-bps-roundtrip "$FEE" "${WARGS[@]}"
    run "fvg_range_${SYM}_15m_${WIN}" python3 scripts/backtest_fvg_range.py --data "$D" --timeframe 15m \
      --symbol "$SYM" --fee-bps-roundtrip "$FEE" "${WARGS[@]}"
  done
  split_csv "$D" "$SPLIT_INTRADAY"
  for WIN in train oos; do
    run "ict_scalp_${SYM}_15m_${WIN}" python3 scripts/backtest_ict_scalp.py --data "data/${SYM}_15m_${WIN}.csv" \
      --timeframe 15m --symbol "$SYM" --htf-rule 1h --ignore-yaml \
      --emit-trades "$R/ict_scalp_${SYM}_15m_${WIN}_trades.jsonl"
  done
done

# ---------- Equities intraday leg (5m RTH source; QQQ / SPY) ----------
for SYM in QQQ SPY; do
  D="data/${SYM}_5m_rth.csv"
  [ -f "$D" ] || { echo "MISSING $D — skipping $SYM"; continue; }
  split_csv "$D" "$SPLIT_INTRADAY"
  for WIN in train oos; do
    run "ict_scalp_${SYM}_5m_${WIN}" python3 scripts/backtest_ict_scalp.py --data "data/${SYM}_5m_rth_${WIN}.csv" \
      --timeframe 5m --symbol "$SYM" --htf-rule 1h --ignore-yaml \
      --emit-trades "$R/ict_scalp_${SYM}_5m_${WIN}_trades.jsonl"
    if [ "$WIN" = train ]; then WARGS=(--end "$SPLIT_INTRADAY"); else WARGS=(--start "$SPLIT_INTRADAY"); fi
    run "fvg_range_${SYM}_15m_${WIN}" python3 scripts/backtest_fvg_range.py --data "$D" --resample 15m \
      --timeframe 15m --symbol "$SYM" --fee-bps-roundtrip "$FEE" "${WARGS[@]}"
  done
done

# ---------- Daily ETF baseline (live-mirror params) ----------
for SYM in QQQ SPY; do
  D="data/${SYM}_1d.csv"
  [ -f "$D" ] || { echo "MISSING $D — skipping $SYM"; continue; }
  for WIN in train oos; do
    if [ "$WIN" = train ]; then WARGS=(--end "$SPLIT_DAILY"); else WARGS=(--start "$SPLIT_DAILY"); fi
    # mirrors mes_trend_long_1d: donchian 30, stop 2.5x, trail 4.0x, long-only
    run "trend1d_${SYM}_${WIN}" python3 scripts/backtest_trend.py --data "$D" --timeframe 1d --symbol "$SYM" \
      --donchian 30 --atr-stop-mult 2.5 --trail-mult 4.0 --long-only \
      --fee-bps-roundtrip "$FEE" "${WARGS[@]}"
  done
done
for SYM in GLD COPPER; do
  D="data/${SYM}_1d.csv"
  [ -f "$D" ] || { echo "MISSING $D — skipping $SYM"; continue; }
  # mirrors mgc_pullback_1d (frac 0.618) / mhg_pullback_1d (frac 0.5)
  FRAC=0.618; [ "$SYM" = COPPER ] && FRAC=0.5
  for WIN in train oos; do
    if [ "$WIN" = train ]; then WARGS=(--end "$SPLIT_DAILY"); else WARGS=(--start "$SPLIT_DAILY"); fi
    run "pullback1d_${SYM}_${WIN}" python3 scripts/backtest_pullback.py --data "$D" --timeframe 1d --symbol "$SYM" \
      --trend-lookback 40 --pullback-lookback 15 --pullback-frac "$FRAC" \
      --atr-stop-mult 2.0 --trail-mult 4.0 \
      --fee-bps-roundtrip "$FEE" "${WARGS[@]}"
  done
done

# ---------- Summary table ----------
python3 - "$R" <<'PYEOF'
import glob, json, os, sys
rdir = sys.argv[1]
rows = []
for f in sorted(glob.glob(os.path.join(rdir, "*.json"))):
    try:
        d = json.load(open(f))
    except Exception as e:
        rows.append((os.path.basename(f)[:-5], f"LOAD_FAIL {e}"))
        continue
    name = os.path.basename(f)[:-5]
    def g(*keys):
        for k in keys:
            if k in d and d[k] is not None:
                return d[k]
            s = d.get("summary") or {}
            if k in s and s[k] is not None:
                return s[k]
        return ""
    rows.append((name, g("trades", "n_trades", "total_trades"), g("win_rate", "winrate"),
                 g("net_r", "total_net_r", "net_r_total", "total_r"), g("expectancy", "expectancy_r"),
                 g("max_drawdown_r", "max_dd_r", "maxdd_r")))
out = os.path.join(rdir, "SUMMARY.md")
with open(out, "w") as fh:
    fh.write("# M15 Phase 0 sweep summary\n\n")
    fh.write("| run | trades | win% | net R | exp R | maxDD R |\n|---|---|---|---|---|---|\n")
    for r in rows:
        fh.write("| " + " | ".join(str(x) for x in r) + " |\n")
print(f"wrote {out} ({len(rows)} runs)")
PYEOF

echo "SWEEP_ALL_DONE"
