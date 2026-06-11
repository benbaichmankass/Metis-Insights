#!/bin/bash
# M15 WS-C — Bybit alt generalization sweep: the existing BTC roster at its
# LIVE-MIRROR params across ETH/SOL/BNB/XRP/ADA/LINK/AVAX USDT perps,
# net of 7.5 bps roundtrip (Bybit taker), train/OOS split at 2025-01-01.
#
# Runs detached on the trainer VM AFTER scripts/ops/m15_ws_c_fetch.sh.
# Generalization SCREENING (the Phase-0 method) — no per-symbol tuning.
#
# Families + params (the Phase-0 generalization pattern per the handoff
# brief — harness defaults for trend, live-mirror for the rest):
#   trend 1h + 4h          harness defaults (donchian 20 / stop 2.5 /
#                          trail 3.0, bidirectional, min_confidence 0) —
#                          the Phase-0 screening params; the BTC live
#                          long-only + 0.60 floor is a BTC-specific M8
#                          tune that deliberately does NOT carry
#   htf_pullback 2h        lookback 40/10 / frac 0.5 / stop 2.5 / trail 5.0
#                          (harness defaults == live config)
#   fade_breakout 4h       donchian 20 / stop-buffer 0.5 / trail 3.5 /
#                          adx_max 20 / exit trail
#   squeeze_breakout 4h    bb 20/2.0 / kc 1.0 / stop 2.5 / trail 3.5
#   fvg_range 15m          harness defaults (== live config BTC-scale widths)
#   ict_scalp 5m           harness defaults (--ignore-yaml), fee applied in
#                          post-processing (the harness has no fee model)
# turtle_soup has no standalone research harness — out of scope, noted in
# the evidence doc.
set -u
cd "$(dirname "$0")/../.."
source /home/ubuntu/ict-trading-bot/.venv/bin/activate 2>/dev/null \
  || source /home/ubuntu/ict-trading-bot/venv/bin/activate 2>/dev/null || true

R=results/m15_ws_c
mkdir -p "$R"
SPLIT="2025-01-01"
FEE="7.5"

run() {  # run <out-name> <cmd...>
  local out="$1"; shift
  echo "=== $out ==="
  "$@" --json "$R/$out.json" || echo "RUN_FAILED $out"
}

split_csv() {  # split_csv <in.csv> <boundary>
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

for SYM in ETHUSDT SOLUSDT BNBUSDT XRPUSDT ADAUSDT LINKUSDT AVAXUSDT; do
  D="data/${SYM}_15m.csv"
  [ -f "$D" ] || { echo "MISSING $D — skipping $SYM"; continue; }
  for WIN in train oos; do
    if [ "$WIN" = train ]; then WARGS=(--end "$SPLIT"); else WARGS=(--start "$SPLIT"); fi
    run "trend_${SYM}_1h_${WIN}" python3 scripts/backtest_trend.py --data "$D" --resample 1h --timeframe 1h \
      --symbol "$SYM" --fee-bps-roundtrip "$FEE" "${WARGS[@]}"
    run "trend_${SYM}_4h_${WIN}" python3 scripts/backtest_trend.py --data "$D" --resample 4h --timeframe 4h \
      --symbol "$SYM" --fee-bps-roundtrip "$FEE" "${WARGS[@]}"
    run "pullback_${SYM}_2h_${WIN}" python3 scripts/backtest_pullback.py --data "$D" --resample 2h --timeframe 2h \
      --symbol "$SYM" --trend-lookback 40 --pullback-lookback 10 --pullback-frac 0.5 \
      --atr-stop-mult 2.5 --trail-mult 5.0 --fee-bps-roundtrip "$FEE" "${WARGS[@]}"
    run "fade_${SYM}_4h_${WIN}" python3 scripts/backtest_fade.py --data "$D" --resample 4h --timeframe 4h \
      --symbol "$SYM" --donchian 20 --atr-stop-buffer 0.5 --exit-style trail --trail-mult 3.5 \
      --adx-max 20.0 --timeout-bars 48 --fee-bps-roundtrip "$FEE" "${WARGS[@]}"
    run "squeeze_${SYM}_4h_${WIN}" python3 scripts/backtest_squeeze.py --data "$D" --resample 4h --timeframe 4h \
      --symbol "$SYM" --bb-period 20 --bb-std 2.0 --kc-mult 1.0 --atr-stop-mult 2.5 \
      --trail-mult 3.5 --timeout-bars 48 --fee-bps-roundtrip "$FEE" "${WARGS[@]}"
    run "fvg_range_${SYM}_15m_${WIN}" python3 scripts/backtest_fvg_range.py --data "$D" --timeframe 15m \
      --symbol "$SYM" --fee-bps-roundtrip "$FEE" "${WARGS[@]}"
  done

  D5="data/${SYM}_5m.csv"
  if [ -f "$D5" ]; then
    split_csv "$D5" "$SPLIT"
    for WIN in train oos; do
      run "ict_scalp_${SYM}_5m_${WIN}" python3 scripts/backtest_ict_scalp.py \
        --data "data/${SYM}_5m_${WIN}.csv" --timeframe 5m --symbol "$SYM" --htf-rule 1h \
        --ignore-yaml --emit-trades "$R/ict_scalp_${SYM}_5m_${WIN}_trades.jsonl"
    done
  fi
done

# ict_scalp exact net-of-fee at the Bybit taker roundtrip
python3 scripts/ops/m15_net_ict_scalp.py --fee-bps-roundtrip "$FEE" "$R"/ict_scalp_*_trades.jsonl || true

# ---------- Summary table ----------
python3 - "$R" <<'PYEOF'
import glob, json, os, sys
rdir = sys.argv[1]
lines = ["| run | trades | gross R | net R | net exp R | win% | maxDD R |",
         "|---|---|---|---|---|---|---|"]
for f in sorted(glob.glob(os.path.join(rdir, "*.json"))):
    if f.endswith(".net.json"):
        continue
    name = os.path.basename(f)[:-5]
    try:
        d = json.load(open(f))
    except Exception as e:
        lines.append(f"| {name} | LOAD_FAIL {e} | | | | | |")
        continue
    def g(*keys):
        for k in keys:
            for src in (d, d.get("summary") or {}):
                if isinstance(src, dict) and src.get(k) is not None:
                    return src[k]
        return ""
    lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
        name, g("total_trades", "trades"), g("gross_total_r", "total_r"),
        g("net_total_r"), g("net_expectancy_r", "expectancy_r"),
        g("net_win_rate", "win_rate"), g("max_drawdown_r", "net_max_drawdown_r")))
for f in sorted(glob.glob(os.path.join(rdir, "*_trades.jsonl.net.json"))):
    d = json.load(open(f))
    name = os.path.basename(f).replace("_trades.jsonl.net.json", "")
    lines.append("| {} (NET {}bps) | {} | {} | {} | {} | {} | |".format(
        name, d.get("fee_bps_roundtrip"), d.get("trades"), d.get("gross_r_total"),
        d.get("net_r_total"), d.get("net_expectancy_r"), d.get("net_win_rate")))
out = os.path.join(rdir, "SUMMARY.md")
open(out, "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
PYEOF

echo "WS_C_SWEEP_DONE"
