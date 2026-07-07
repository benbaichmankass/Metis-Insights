#!/usr/bin/env bash
# scripts/ops/etf_account_compat.sh — daily/intraday ETF account-compatibility matrix.
#
# Runs the per-account compatibility matrix (scripts/prop/account_compat_matrix.py)
# for every Alpaca ETF cell against the Alpaca accounts (paper + the future
# real-money sibling), so a cell is never wired live on an account it wasn't
# evaluated against under that account's own rules. This is the MANDATORY gate
# before an Alpaca REAL-MONEY (alpaca_live) promotion — the daily/equity
# extension of the prop compat gate (PB-20260618-012).
#
# For each cell it:
#   1. runs the correct standalone harness (trend vs pullback) at that cell's
#      EXACT config/strategies.yaml params + an equity round-trip fee, emitting a
#      shared --emit-trades JSONL ({strategy, entry_time, direction, gross_r,
#      net_r, confidence});
#   2. feeds that ledger to account_compat_matrix.py with the cell's --symbol and
#      --base-risk-pct, scoring it against alpaca_paper + alpaca_live (both resolve
#      to the `standard` net-of-fee + survival gate).
#
# Output: runtime_logs/prop_eval/<UTC-date>/compat_<cell>.{json,md} (one pair per
# cell). Tier-1 research/eval tooling — NO live order path is touched.
#
# DATA: expects the per-symbol candle CSVs at $DATA_DIR/<SYM>_1d.csv (daily cells)
# and $DATA_DIR/<SYM>_1h.csv (intraday cells). These are TRAINER-VM-resident
# (the orchestrator runs on the trainer VM, where the daily/intraday ETF CSVs
# live). A missing CSV for a cell is logged + skipped, not fatal.
#
# Usage:
#   scripts/ops/etf_account_compat.sh                 # all cells
#   scripts/ops/etf_account_compat.sh iwm_trend_long_1d gld_pullback_1d   # a subset
#
# Environment knobs:
#   REPO_ROOT      — defaults to the repo this script lives in
#   VENV_DIR       — python venv to activate (default $REPO_ROOT/.venv if present)
#   DATA_DIR       — where the <SYM>_<tf>.csv candle CSVs live (default $REPO_ROOT/data)
#   ACCOUNTS       — CSV of account ids to score (default alpaca_paper,alpaca_live)
#   FEE_BPS        — equity round-trip fee in bps (default 1.0; Alpaca commission-free,
#                    a small bps covers spread/slippage — matches the cells' fee-robust
#                    sweep provenance)
#   MIN_SURVIVAL   — standard ROUTE survival floor (default 0.90; matrix default)
#   MAX_P_BREACH   — standard ROUTE max P(breach) (default 0.10; matrix default)
#   OUT_DIR        — override the matrix output dir (default its date-stamped default)
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
DATA_DIR="${DATA_DIR:-$REPO_ROOT/data}"
ACCOUNTS="${ACCOUNTS:-alpaca_paper,alpaca_live}"
FEE_BPS="${FEE_BPS:-1.0}"
MIN_SURVIVAL="${MIN_SURVIVAL:-0.90}"
MAX_P_BREACH="${MAX_P_BREACH:-0.10}"

cd "$REPO_ROOT"

# Activate a venv if one is present (trainer VM convention).
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv}"
if [[ -f "$VENV_DIR/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
fi
PY="${PYTHON:-python3}"

# ---------------------------------------------------------------------------
# Cell → (harness, timeframe, symbol, risk_pct, params...) mapping.
#
# One row per Alpaca ETF cell in config/strategies.yaml. Params are pinned to
# the EXACT values in that cell's YAML block (keep in sync if the YAML changes).
#   harness  : trend  → scripts/backtest_trend.py    (Donchian trend-follower)
#              pullback→ scripts/backtest_pullback.py (HTF pullback)
#   long_only: "1" appends --long-only (trend cells that are long-only); "" omits.
#
# Trend cells params:    donchian atr_stop_mult trail_mult
# Pullback cells params: trend_lookback pullback_lookback pullback_frac atr_stop_mult trail_mult
# (atr_period is 14 everywhere → the harness default; min_confidence 0.0 → default.)
# Fields: cell|harness|tf|symbol|risk_pct|long_only|p1|p2|p3|p4|p5
# ---------------------------------------------------------------------------
CELLS=(
  # --- daily ETF family ---
  "spy_trend_long_1d|trend|1d|SPY|0.3|1|30|2.5|4.0||"
  "qqq_trend_long_1d|trend|1d|QQQ|0.3|1|30|2.5|4.0||"
  # leveraged Nasdaq-100 trend cells (2026-06-30) — TQQQ 3x + QLD 2x
  "tqqq_trend_long_1d|trend|1d|TQQQ|0.3|1|30|2.5|4.0||"
  "qld_trend_long_1d|trend|1d|QLD|0.3|1|30|2.5|4.0||"
  "iwm_trend_long_1d|trend|1d|IWM|0.3|1|30|2.5|4.0||"
  # sub-$100 proxy cells (2026-07-07) — cheap-share equivalents of the
  # expensive index/gold legs (SPLG≈SPY, IAUM≈GLD, SCHA≈IWM). Same unit +
  # params as their sibling; needs data/<SYM>_1d.csv on the trainer.
  "splg_trend_long_1d|trend|1d|SPLG|0.3|1|30|2.5|4.0||"
  "scha_trend_long_1d|trend|1d|SCHA|0.3|1|30|2.5|4.0||"
  "gld_pullback_1d|pullback|1d|GLD|0.3||40|15|0.618|2.0|4.0"
  "iaum_pullback_1d|pullback|1d|IAUM|0.3||40|15|0.618|2.0|4.0"
  "tlt_pullback_1d|pullback|1d|TLT|0.3||40|10|0.618|2.5|5.0"
  "ief_pullback_1d|pullback|1d|IEF|0.3||30|10|0.5|2.5|5.0"
  # --- intraday (1h) ETF sleeve ---
  "gld_pullback_1h|pullback|1h|GLD|0.3||60|12|0.5|2.5|4.0"
  "slv_trend_1h|trend|1h|SLV|0.3||24|2.5|4.0||"
  "spy_pullback_1h|pullback|1h|SPY|0.3||60|12|0.618|2.5|5.0"
  "qqq_pullback_1h|pullback|1h|QQQ|0.3||60|12|0.618|2.5|5.0"
  "tlt_pullback_1h|pullback|1h|TLT|0.3||60|12|0.5|2.5|4.0"
  "uso_trend_1h|trend|1h|USO|0.3|1|24|2.5|4.0||"
  # gld_pullback_1d siblings (2026-06-27), mirrored params — added here
  # 2026-07-07 so the mandatory coverage gate reaches every alpaca_paper
  # ETF cell, not just the 14 present before this pass.
  "slv_pullback_1d|pullback|1d|SLV|0.3||40|15|0.618|2.0|4.0"
  "gdx_pullback_1d|pullback|1d|GDX|0.3||40|15|0.618|2.0|4.0"
)

# Optional subset filter: any positional args restrict the run to those cells.
WANT=("$@")
want_cell() {
  local c="$1"
  [[ ${#WANT[@]} -eq 0 ]] && return 0
  local w
  for w in "${WANT[@]}"; do [[ "$w" == "$c" ]] && return 0; done
  return 1
}

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

ran=0; skipped=0; failed=0
for spec in "${CELLS[@]}"; do
  IFS='|' read -r cell harness tf symbol risk_pct long_only p1 p2 p3 p4 p5 <<<"$spec"
  want_cell "$cell" || continue

  data_csv="$DATA_DIR/${symbol}_${tf}.csv"
  if [[ ! -f "$data_csv" ]]; then
    echo "[etf-compat] SKIP $cell — missing candle CSV $data_csv" >&2
    skipped=$((skipped + 1))
    continue
  fi

  emit="$TMP_DIR/${cell}.jsonl"
  echo "[etf-compat] ==> $cell ($harness $tf $symbol, risk ${risk_pct}%, fee ${FEE_BPS}bps)" >&2

  if [[ "$harness" == "trend" ]]; then
    # p1=donchian p2=atr_stop_mult p3=trail_mult
    lo_flag=()
    [[ "$long_only" == "1" ]] && lo_flag=(--long-only)
    if ! "$PY" scripts/backtest_trend.py \
        --data "$data_csv" --symbol "$symbol" --timeframe "$tf" \
        --donchian "$p1" --atr-stop-mult "$p2" --trail-mult "$p3" \
        --fee-bps-roundtrip "$FEE_BPS" --emit-trades "$emit" \
        "${lo_flag[@]}" >&2; then
      echo "[etf-compat] FAIL $cell — backtest_trend.py errored" >&2
      failed=$((failed + 1)); continue
    fi
  else
    # pullback: p1=trend_lookback p2=pullback_lookback p3=pullback_frac p4=atr_stop_mult p5=trail_mult
    if ! "$PY" scripts/backtest_pullback.py \
        --data "$data_csv" --symbol "$symbol" --timeframe "$tf" \
        --trend-lookback "$p1" --pullback-lookback "$p2" --pullback-frac "$p3" \
        --atr-stop-mult "$p4" --trail-mult "$p5" \
        --fee-bps-roundtrip "$FEE_BPS" --emit-trades "$emit" >&2; then
      echo "[etf-compat] FAIL $cell — backtest_pullback.py errored" >&2
      failed=$((failed + 1)); continue
    fi
  fi

  if [[ ! -s "$emit" ]]; then
    echo "[etf-compat] SKIP $cell — harness emitted no trades (empty ledger)" >&2
    skipped=$((skipped + 1)); continue
  fi

  out_args=()
  [[ -n "${OUT_DIR:-}" ]] && out_args=(--out-dir "$OUT_DIR")
  "$PY" scripts/prop/account_compat_matrix.py \
    --ledger "$emit" --strategy "$cell" --symbol "$symbol" \
    --accounts "$ACCOUNTS" --base-risk-pct "$risk_pct" \
    --fee-bps-roundtrip "$FEE_BPS" \
    --min-survival "$MIN_SURVIVAL" --max-p-breach "$MAX_P_BREACH" \
    "${out_args[@]}"
  ran=$((ran + 1))
done

echo "[etf-compat] done — ran=$ran skipped=$skipped failed=$failed" >&2
[[ "$failed" -eq 0 ]]
