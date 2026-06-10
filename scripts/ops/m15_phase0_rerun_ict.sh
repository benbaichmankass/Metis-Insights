#!/bin/bash
# M15 Phase 0 — second pass: corrected-RTH equity legs + enriched
# ict_scalp emits + exact net-of-fee post-processing.
#
# Run AFTER the QQQ/SPY refetch (corrected --rth-only DST windows,
# commit 7dccf4d) completes. Re-runs:
#   - ict_scalp on QQQ/SPY 5m (corrected data) and EURUSD/GBPUSD/XAUUSD
#     15m (same data, enriched emit fields) — train/OOS pre-splits
#   - fvg_range on QQQ/SPY 15m (corrected data)
# then computes exact net-of-fee for every ict_scalp leg and regenerates
# the summary table including a NET ict_scalp section.
set -u
cd "$(dirname "$0")/../.."
source /home/ubuntu/ict-trading-bot/.venv/bin/activate 2>/dev/null \
  || source /home/ubuntu/ict-trading-bot/venv/bin/activate 2>/dev/null || true

R=results/m15_phase0
SPLIT="2025-01-01"
FEE="2.0"

split_csv() {
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

# corrected equity data -> fresh splits
for SYM in QQQ SPY; do
  split_csv "data/${SYM}_5m_rth.csv" "$SPLIT"
done

for SYM in QQQ SPY; do
  for WIN in train oos; do
    echo "=== ict_scalp_${SYM}_5m_${WIN} (corrected RTH) ==="
    python3 scripts/backtest_ict_scalp.py --data "data/${SYM}_5m_rth_${WIN}.csv" \
      --timeframe 5m --symbol "$SYM" --htf-rule 1h --ignore-yaml \
      --emit-trades "$R/ict_scalp_${SYM}_5m_${WIN}_trades.jsonl" \
      --json "$R/ict_scalp_${SYM}_5m_${WIN}.json" || echo "RUN_FAILED ict_scalp_${SYM}_${WIN}"
    if [ "$WIN" = train ]; then WARGS=(--end "$SPLIT"); else WARGS=(--start "$SPLIT"); fi
    echo "=== fvg_range_${SYM}_15m_${WIN} (corrected RTH) ==="
    python3 scripts/backtest_fvg_range.py --data "data/${SYM}_5m_rth.csv" --resample 15m \
      --timeframe 15m --symbol "$SYM" --fee-bps-roundtrip "$FEE" "${WARGS[@]}" \
      --json "$R/fvg_range_${SYM}_15m_${WIN}.json" || echo "RUN_FAILED fvg_range_${SYM}_${WIN}"
  done
done

# FX ict_scalp re-runs purely for the enriched emit fields (same data)
for SYM in EURUSD GBPUSD XAUUSD; do
  for WIN in train oos; do
    echo "=== ict_scalp_${SYM}_15m_${WIN} (enriched emit) ==="
    python3 scripts/backtest_ict_scalp.py --data "data/${SYM}_15m_${WIN}.csv" \
      --timeframe 15m --symbol "$SYM" --htf-rule 1h --ignore-yaml \
      --emit-trades "$R/ict_scalp_${SYM}_15m_${WIN}_trades.jsonl" \
      --json "$R/ict_scalp_${SYM}_15m_${WIN}.json" || echo "RUN_FAILED ict_scalp_${SYM}_${WIN}"
  done
done

echo "=== net-of-fee post-processing (ict_scalp) ==="
python3 scripts/ops/m15_net_ict_scalp.py --fee-bps-roundtrip "$FEE" "$R"/ict_scalp_*_trades.jsonl

echo "=== regenerate summary ==="
python3 - "$R" <<'PYEOF'
import glob, json, os, sys
rdir = sys.argv[1]
rows = []
for f in sorted(glob.glob(os.path.join(rdir, "*.json"))):
    if f.endswith(".net.json"):
        continue
    try:
        d = json.load(open(f))
    except Exception as e:
        rows.append((os.path.basename(f)[:-5], f"LOAD_FAIL {e}", "", "", "", ""))
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
nets = []
for f in sorted(glob.glob(os.path.join(rdir, "*_trades.jsonl.net.json"))):
    d = json.load(open(f))
    nets.append((os.path.basename(f).replace("_trades.jsonl.net.json", ""),
                 d["trades"], d["net_win_rate"], d["gross_r_total"], d["net_r_total"],
                 d["net_expectancy_r"], d["skipped_no_prices"]))
out = os.path.join(rdir, "SUMMARY.md")
with open(out, "w") as fh:
    fh.write("# M15 Phase 0 sweep summary (pass 2 — corrected RTH equities)\n\n")
    fh.write("| run | trades | win% | net R | exp R | maxDD R |\n|---|---|---|---|---|---|\n")
    for r in rows:
        fh.write("| " + " | ".join(str(x) for x in r) + " |\n")
    fh.write("\n## ict_scalp NET of fee (exact, from enriched trade rows)\n\n")
    fh.write("| run | trades | net win% | gross R | NET R | net exp R | skipped |\n|---|---|---|---|---|---|---|\n")
    for r in nets:
        fh.write("| " + " | ".join(str(x) for x in r) + " |\n")
print(f"wrote {out} ({len(rows)} runs, {len(nets)} net rows)")
PYEOF

echo "RERUN_ALL_DONE"
