#!/usr/bin/env bash
# Real-data prop validation driver (PB-20260616-004) — runs on the TRAINER VM
# via the trainer-vm-diag relay (the sandbox can't reach Bybit or run pandas).
#
# Fetches REAL Bybit linear-perp 5m candles + funding history for each alt and
# runs scripts/prop/validate_alt_prop.py (full-period funded cost-aware EV +
# per-alt walk-forward). Writes results under $OUT_DIR and prints the verdicts.
#
# Tier-1 research only: no live order path, no config writes. Intended to run
# from a detached git worktree so the live trainer's main checkout is untouched.
#
# Env knobs (all optional):
#   SYMBOLS      space list (default "SOLUSDT ETHUSDT BNBUSDT")
#   START_DATE   YYYY-MM-DD (default 2023-01-01 — matches the research window)
#   END_DATE     YYYY-MM-DD (default 2026-02-28)
#   STRATEGY     default trend_donchian
#   OUT_DIR      default runtime_logs/prop_eval/<UTC-date>-validate-real
#   DATA_DIR_LOCAL  where to drop the fetched csv (default $HOME/ict-trader-data)
#   PYTHON       python interpreter (auto-detected if unset)
set -uo pipefail

SYMBOLS="${SYMBOLS:-SOLUSDT ETHUSDT BNBUSDT}"
START="${START_DATE:-2023-01-01}"
END="${END_DATE:-2026-02-28}"
STRATEGY="${STRATEGY:-trend_donchian}"
OUT="${OUT_DIR:-runtime_logs/prop_eval/$(date -u +%Y-%m-%d)-validate-real}"
DATADIR="${DATA_DIR_LOCAL:-$HOME/ict-trader-data}"

# --- pick a python with pandas/numpy (the trainer's training venv) -----------
pick_python() {
  if [ -n "${PYTHON:-}" ] && "$PYTHON" -c 'import pandas,numpy' 2>/dev/null; then
    echo "$PYTHON"; return 0; fi
  for c in \
      "$PWD/.venv/bin/python" "$HOME/ict-trading-bot/.venv/bin/python" \
      "$HOME/.venv/bin/python" "$HOME/venv/bin/python" \
      "/opt/ict-trading-bot/.venv/bin/python" python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import pandas,numpy' 2>/dev/null; then
      echo "$c"; return 0; fi
  done
  return 1
}
PY="$(pick_python)" || { echo "FATAL: no python with pandas/numpy found"; exit 3; }
echo "using python: $PY ($($PY -c 'import pandas,numpy;print("pandas",pandas.__version__,"numpy",numpy.__version__)'))"

mkdir -p "$OUT" "$DATADIR"
echo "window $START -> $END | strategy $STRATEGY | out $OUT"

for S in $SYMBOLS; do
  s="$(echo "$S" | tr '[:upper:]' '[:lower:]')"
  echo "================ $S : candles ================"
  "$PY" scripts/ops/fetch_backtest_candles.py --symbol "$S" --interval 5 \
      --start-date "$START" --end-date "$END" --output "$DATADIR/${s}_5m.csv" \
      || { echo "CANDLE FETCH FAILED $S"; continue; }
  echo "================ $S : funding ================"
  "$PY" scripts/ops/fetch_bybit_funding.py --symbol "$S" \
      --start-date "$START" --end-date "$END" --output "$DATADIR/${s}_funding.csv" \
      || echo "FUNDING FETCH FAILED $S (will fall back to constant rate)"
  fundarg=""
  [ -s "$DATADIR/${s}_funding.csv" ] && fundarg="--funding $DATADIR/${s}_funding.csv"
  # GATE: Breakout venue cost model — flat CFD-style daily swap (~0.09%/day).
  echo "================ $S : validate (Breakout daily-swap GATE) ================"
  "$PY" scripts/prop/validate_alt_prop.py --symbol "$S" \
      --data "$DATADIR/${s}_5m.csv" \
      --cost-model daily_swap --swap-rate-daily "${SWAP_RATE_DAILY:-0.0009}" \
      --strategy "$STRATEGY" --out-dir "$OUT/breakout-swap" \
      || echo "VALIDATE(swap) FAILED $S"
  # COMPARISON: lighter Bybit 8h perp funding (real series), for context only.
  echo "================ $S : validate (Bybit perp-funding compare) ================"
  "$PY" scripts/prop/validate_alt_prop.py --symbol "$S" \
      --data "$DATADIR/${s}_5m.csv" $fundarg \
      --cost-model perp_funding \
      --strategy "$STRATEGY" --out-dir "$OUT/bybit-funding" \
      || echo "VALIDATE(funding) FAILED $S"
done

echo "================ SUMMARY (GATE = Breakout daily-swap) ================"
grep -H "VERDICT" "$OUT"/breakout-swap/*.md 2>/dev/null || echo "(no gate verdict files)"
echo "---- comparison (Bybit perp-funding) ----"
grep -H "VERDICT" "$OUT"/bybit-funding/*.md 2>/dev/null || echo "(no compare verdict files)"
echo "ALL DONE -> $OUT"
