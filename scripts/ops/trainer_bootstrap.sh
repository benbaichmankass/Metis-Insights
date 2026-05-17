#!/usr/bin/env bash
# Trainer-VM bootstrap (S-TRAINER-BT-1, 2026-05-17).
#
# Idempotently ensures the trainer's Python environment is ready for
# backtest sweeps + ML training cycles. Re-runs are safe.
#
# Aligns with the existing convention in
# scripts/ops/run_training_cycle.sh — same VENV path
# ($REPO_ROOT/.venv), same python3.11 baseline. The difference is that
# this script additionally installs requirements-backtest.txt
# (pyarrow + requests + python-dateutil), which the training cycle
# does not need but the backtest sweep + qashdev/btc fetcher do.
#
# What this does:
#   1. Creates $REPO_ROOT/.venv if missing (python3.11 -m venv).
#   2. Upgrades pip + wheel inside the venv.
#   3. Installs requirements.txt + requirements-backtest.txt.
#   4. Prints the resolved version manifest so the next session can
#      grep it from the diag-relay output.
#
# Invocation (from any trainer-vm-diag relay):
#
#     cmd: |
#       cd /home/ubuntu/ict-trading-bot && git pull --ff-only && \
#         bash scripts/ops/trainer_bootstrap.sh
#
# scripts/ops/run_backtest_sweep.sh calls this first to guarantee the
# env is ready before kicking off a sweep.
#
# Runbook: docs/runbooks/trainer-backtest.md.

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv}"

echo "=== trainer_bootstrap.sh starting ==="
echo "  repo:      $REPO_ROOT"
echo "  venv:      $VENV_DIR"
echo "  python:    $(command -v python3.11 || command -v python3)"
date -u +'  time-utc: %Y-%m-%dT%H:%M:%SZ'

if [ ! -d "$REPO_ROOT/.git" ]; then
    echo "FATAL: repo not found at $REPO_ROOT" >&2
    exit 2
fi

if ! command -v python3.11 >/dev/null 2>&1; then
    echo "FATAL: python3.11 not on PATH — cloud-init bootstrap incomplete." >&2
    echo "Re-provision the trainer VM or install python3.11 manually." >&2
    exit 2
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "+ creating venv at $VENV_DIR"
    python3.11 -m venv "$VENV_DIR"
else
    echo "+ venv already exists, reusing"
fi

PIP="$VENV_DIR/bin/pip"
PY="$VENV_DIR/bin/python"

echo "+ upgrading pip + wheel"
"$PIP" install --quiet --upgrade pip wheel

echo "+ installing requirements.txt + requirements-backtest.txt"
"$PIP" install --quiet -r "$REPO_ROOT/requirements.txt" \
                       -r "$REPO_ROOT/requirements-backtest.txt"

echo
echo "=== resolved manifest ==="
"$PY" - <<'PYEOF'
import importlib.metadata as m
for pkg in ("pandas", "numpy", "pyarrow", "requests", "python-dateutil", "ccxt"):
    try:
        print(f"  {pkg:18s} {m.version(pkg)}")
    except m.PackageNotFoundError:
        print(f"  {pkg:18s} MISSING")
import sys
print(f"  python             {sys.version.split()[0]}")
PYEOF

echo
echo "=== trainer_bootstrap.sh complete ==="
