#!/usr/bin/env bash
# Manual-session entry point for memory-heavy trainer-VM work.
#
# Run ANY memory-heavy trainer command through this wrapper so it joins the
# shared heavy-job QUEUE instead of colliding with a running training cycle /
# promotion-readiness sweep / drift-retrain and thrashing the 6 GB box:
#
#   scripts/ops/trainer_run.sh python -m ml train ml/configs/<manifest>.yaml
#   scripts/ops/trainer_run.sh python -m ml build-dataset <family>
#
# If the queue is busy longer than the wait timeout it exits 75 and tells you
# to try later OR route the run to the GPU-burst platform (gpu-burst-train.yml,
# within the $10/mo budget) — see docs/claude/trainer-resource-protocol.md.
#
# Trainer-VM only, autonomous (trainer systemd + ops are in scope per
# docs/claude/trainer-vm-mode.md).
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"

if [[ $# -eq 0 ]]; then
  echo "usage: scripts/ops/trainer_run.sh <command> [args...]" >&2
  exit 2
fi

# shellcheck source=/dev/null
. "$REPO_ROOT/scripts/ops/_trainer_heavy_lock.sh"

take_trainer_heavy_lock "manual:$1" || exit 75
exec "$@"
