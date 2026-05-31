#!/usr/bin/env bash
# scripts/ops/prem_runs/01_reproduce_check.sh
#
# PURPOSE. Confirm the consolidated sim Phase-5 account layer reproduces the
# retiring scripts/backtest_system.py on FULL 5m history, so backtest_system.py
# can be deleted with evidence rather than on faith.
#
# WHY SINGLE-STRATEGY. The two harnesses handle a multi-TF roster differently
# (backtest_system resamples a 5m base per-strategy; sim consumes one
# pre-resampled TF). To keep the comparison apples-to-apples we reproduce the
# ONE proven winner, trend_donchian @ 2h — the strategy whose live seat we are
# keeping. A roster-wide match is a known harness difference, not a goal here.
#
# Both harnesses run with the SAME live-aligned account params (the
# backtest_system defaults: $10k / 0.3% risk / 3% daily-loss / reverse flip /
# 7.5bps). reproduce_diff.py then compares net_pnl, return/DD, trade count etc.
# within a 5% band.
#
# PREREQS:  $DATA_5M -> full-history 5m BTC CSV/parquet (the trainer-VM file).
#           Optional $DATA_2H -> pre-resampled 2h CSV for the sim side; if
#           unset, derive it once from $DATA_5M (see below).
#
# Tier-1, read-only. Throttled. Idempotent (timestamped output dir).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

STRAT="trend_donchian"
require_file DATA_5M
OUT="$(mk_outdir reproduce)"
log "reproduce-check -> $OUT (strategy=$STRAT)"

# --- sim side needs 2h candles. Derive from the 5m base once if not provided.
DATA_2H="${DATA_2H:-$OUT/btc_2h.csv}"
if [[ ! -f "$DATA_2H" ]]; then
  log "resampling 5m -> 2h for the sim side: $DATA_2H"
  throttle "$PY" - "$DATA_5M" "$DATA_2H" <<'PYEOF'
import sys, pandas as pd
src, dst = sys.argv[1], sys.argv[2]
df = pd.read_parquet(src) if src.endswith(".parquet") else pd.read_csv(src)
cols = {c.lower(): c for c in df.columns}
ts = cols.get("ts") or cols.get("timestamp") or cols.get("time")
df[ts] = pd.to_datetime(df[ts], unit="ms", errors="ignore")
df = df.rename(columns={ts: "ts"}).set_index(pd.to_datetime(df[ts]))
o = df[cols["open"]].resample("2h").first()
h = df[cols["high"]].resample("2h").max()
l = df[cols["low"]].resample("2h").min()
c = df[cols["close"]].resample("2h").last()
v = df[cols.get("volume", cols.get("close"))].resample("2h").sum()
out = pd.DataFrame({"ts": o.index.astype("int64")//10**6, "open": o, "high": h,
                    "low": l, "close": c, "volume": v}).dropna()
out.to_csv(dst, index=False)
print(f"wrote {len(out)} 2h bars", file=sys.stderr)
PYEOF
fi

SIM_JSON="$OUT/sim_${STRAT}.json"
BTS_JSON="$OUT/bts_${STRAT}.json"

# --- sim Phase-5 (account layer ON, live-aligned params) ---
log "running sim Phase-5 ..."
throttle "$PY" -m sim run \
  --candles "$DATA_2H" --strategies "$STRAT" --timeframe 2h \
  --initial-balance 10000 --risk-pct 0.3 --daily-loss-pct 3.0 \
  --flip-policy reverse --fee-bps 7.5 \
  --out-root "$OUT/sim" --run-id reproduce > "$OUT/sim_stdout.txt" 2>&1 || true
# sim writes its result JSON under out-root; surface it at a stable path.
found="$(find "$OUT/sim" -name '*.json' -type f 2>/dev/null | head -1 || true)"
[[ -n "$found" ]] && cp "$found" "$SIM_JSON" || log "WARN: no sim JSON produced (see sim_stdout.txt)"

# --- backtest_system (the retiring harness), same roster of one ---
log "running backtest_system ..."
throttle "$PY" -m scripts.backtest_system \
  --data "$DATA_5M" --roster "$STRAT" \
  --initial-balance 10000 --risk-pct 0.3 --daily-loss-pct 3.0 \
  --flip-policy reverse --fee-bps-roundtrip 7.5 \
  --json "$BTS_JSON" > "$OUT/bts_stdout.txt" 2>&1 || log "WARN: backtest_system nonzero (see bts_stdout.txt)"

# --- compare ---
if [[ -f "$SIM_JSON" && -f "$BTS_JSON" ]]; then
  rc=0
  throttle "$PY" "$REPO_ROOT/scripts/ops/prem_runs/reproduce_diff.py" \
    "$SIM_JSON" "$BTS_JSON" --tol-pct 5 --json-out "$OUT/reproduce_verdict.json" || rc=$?
  log "verdict written: $OUT/reproduce_verdict.json (diff rc=$rc)"
  notify "prem-reproduce" "$rc" "trend_donchian e2e reproduce"
  exit "$rc"
else
  log "could not run comparison — one side produced no JSON"
  notify "prem-reproduce" 1 "missing harness output"
  exit 1
fi
