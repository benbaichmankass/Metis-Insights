#!/usr/bin/env bash
# Trainer-VM backtest sweep orchestrator (S-TRAINER-BT-1, 2026-05-17).
#
# Runs the post-incident validation backtest end-to-end. Composes:
#   1. trainer_bootstrap.sh   — ensure venv + deps
#   2. fetch_qashdev_btc_archive.py — refresh the 3-year parquet cache
#   3. experiments/2026-05-17-post-incident-validation/scripts/run.py
#      — vwap V_BASELINE + V_PROD, turtle_soup TS_PROD + extended T3
#      sweep, turtle_soup 5m naive variant
#   4. scripts/backtest_ict_scalp.py — ict_scalp_5m re-validation
#
# All output written to $ICT_TRADER_DATA_ROOT/backtests/<UTC-date>/
# (default /home/ubuntu/ict-trader-data/backtests/<UTC-date>/). The
# SUMMARY.md table is also tail'd to stdout so the diag-relay comment
# carries it.
#
# Idempotent: re-runs against the same UTC date overwrite the
# previous run's outputs. The qashdev cache is incremental. The venv
# install is no-op when deps are already satisfied.
#
# Invocation (from any trainer-vm-diag relay):
#
#     cmd: |
#       cd /home/ubuntu/ict-trading-bot && git pull --ff-only && \
#         bash scripts/ops/run_backtest_sweep.sh
#
# Runbook: docs/runbooks/trainer-backtest.md.

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv}"
DATA_ROOT="${ICT_TRADER_DATA_ROOT:-/home/ubuntu/ict-trader-data}"
TODAY_UTC="$(date -u +%Y-%m-%d)"
OUT_DIR="$DATA_ROOT/backtests/$TODAY_UTC"

echo "=== run_backtest_sweep.sh starting ==="
echo "  repo:      $REPO_ROOT"
echo "  venv:      $VENV_DIR"
echo "  data:      $DATA_ROOT"
echo "  out:       $OUT_DIR"
date -u +'  time-utc: %Y-%m-%dT%H:%M:%SZ'

mkdir -p "$OUT_DIR"

# 1. Bootstrap the venv (idempotent)
echo
echo "--- step 1: trainer_bootstrap.sh ---"
bash "$REPO_ROOT/scripts/ops/trainer_bootstrap.sh"

# 2. Refresh the qashdev/btc parquet cache (incremental)
echo
echo "--- step 2: fetch_qashdev_btc_archive.py ---"
"$VENV_DIR/bin/python" "$REPO_ROOT/scripts/ops/fetch_qashdev_btc_archive.py"

# 3. Main experiment harness — vwap + turtle_soup
echo
echo "--- step 3: post-incident-validation harness ---"
ICT_TRADER_DATA_ROOT="$DATA_ROOT" \
    "$VENV_DIR/bin/python" \
    "$REPO_ROOT/experiments/2026-05-17-post-incident-validation/scripts/run.py" \
    2>&1 | tee "$OUT_DIR/harness_stdout.log"

# 4. ict_scalp_5m re-validation — fed the same 3-year qashdev parquet
# (converted to CSV since the existing CLI is CSV-only).
echo
echo "--- step 4: ict_scalp_5m re-validation ---"
ICT_SCALP_CSV="$OUT_DIR/btc_5m_for_ict_scalp.csv"
ICT_SCALP_JSON="$OUT_DIR/ict_scalp_metrics.json"

# Parquet → CSV with the columns ict_scalp expects (open/high/low/close
# required, timestamp optional but useful for the date-range report).
echo "  converting parquet -> CSV for ict_scalp's CSV-only loader..."
"$VENV_DIR/bin/python" - <<PYEOF
import pandas as pd
df = pd.read_parquet("$DATA_ROOT/btc_5m.parquet")
df = df[["timestamp", "open", "high", "low", "close", "volume"]]
df.to_csv("$ICT_SCALP_CSV", index=False)
print(f"  wrote {len(df):,} rows -> $ICT_SCALP_CSV ({pd.io.common.file_path_to_url('$ICT_SCALP_CSV')})")
print(f"  range: {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}")
PYEOF

"$VENV_DIR/bin/python" "$REPO_ROOT/scripts/backtest_ict_scalp.py" \
    --data "$ICT_SCALP_CSV" \
    --timeframe 5m \
    --json "$ICT_SCALP_JSON" \
    2>&1 | tee "$OUT_DIR/ict_scalp_stdout.log" || \
    echo "WARN: ict_scalp backtest exited non-zero (output retained for diagnosis)"

echo
echo "--- step 4 result ---"
if [ -s "$ICT_SCALP_JSON" ]; then
    "$VENV_DIR/bin/python" - <<PYEOF
import json
d = json.load(open("$ICT_SCALP_JSON"))
print(json.dumps(d, indent=2, default=str)[:2000])
PYEOF
else
    echo "no ict_scalp_metrics.json produced"
fi

echo
echo "=== run_backtest_sweep.sh complete ==="
echo "outputs:"
ls -la "$OUT_DIR" | tail -n +2 | head -20
echo
echo "=== SUMMARY.md ==="
if [ -f "$OUT_DIR/SUMMARY.md" ]; then
    cat "$OUT_DIR/SUMMARY.md"
fi
