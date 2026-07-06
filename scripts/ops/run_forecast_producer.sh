#!/usr/bin/env bash
# scripts/ops/run_forecast_producer.sh — body of ict-trainer-forecast.service
# (M19 Track-1, fc-head live serve).
#
# Regenerates the live TSFM forecast-serve artifacts
# (runtime_logs/trainer_mirror/forecasts/<SYMBOL>.json) for the live symbols by
# running scripts/ml/publish_live_forecasts.py under the trainer venv. The
# SEPARATE ict-trainer-publish.timer (2-min) rsyncs those artifacts to the live
# VM's mirror, where src/runtime/forecast_live.py serves the fc_* row to the
# shadow / per-bar regime scorer. This script only PRODUCES; publish MIRRORS.
#
# Cadence: 15 min (the 15m bar the fc head scores). chronos-bolt-tiny is a 9M
# CPU model, sub-second per symbol; the real per-run cost is the per-symbol
# Bybit candle fetch. Best-effort: a fetch / dep failure logs and exits
# non-zero (the timer just retries next cycle) without touching anything the
# live path reads — a stale/absent artifact makes forecast_live return None
# (fail-permissive), never a fabricated row.
#
# Env knobs:
#   REPO_ROOT           — defaults to /home/ubuntu/ict-trading-bot
#   VENV_DIR            — defaults to "$REPO_ROOT/.venv"
#   FORECAST_SYMBOLS    — comma-separated live symbols (default
#                         BTCUSDT,ETHUSDT,SOLUSDT — SOL added 2026-07-06 for
#                         the 3rd fc-family leg after the SOL fc-vs-base
#                         purged-CV win, operator-approved; see
#                         docs/research/SOL-fc-family-AB-evidence-2026-07-06.md)
#   FORECAST_TIMEFRAME  — candle timeframe (default 15m)
#
# Idempotent: re-running re-forecasts the current bar and atomically rewrites
# each artifact (write_forecast_artifact does a tmp+rename). Trainer-VM only,
# autonomous (trainer systemd is in scope per docs/claude/trainer-vm-mode.md).
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv}"
FORECAST_SYMBOLS="${FORECAST_SYMBOLS:-BTCUSDT,ETHUSDT,SOLUSDT}"
FORECAST_TIMEFRAME="${FORECAST_TIMEFRAME:-15m}"

cd "$REPO_ROOT"

# shellcheck source=/dev/null
if [ -f "$VENV_DIR/bin/activate" ]; then
  source "$VENV_DIR/bin/activate"
fi

exec python -m scripts.ml.publish_live_forecasts \
  --symbols "$FORECAST_SYMBOLS" \
  --timeframe "$FORECAST_TIMEFRAME"
