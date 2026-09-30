#!/usr/bin/env bash
# wiring: manual-only - dispatched through the trainer-vm-diag relay (see below); a one-shot installer, not a recurring job.
# scripts/ops/install_trainer_candle_refresh.sh — install the extend-only candle
# refresh timer on an already-running trainer VM (PI-20260929-FCJRWVAK-0002).
#
# Units are copied from THIS repo checkout (deploy/trainer/), the single source,
# so a reinstall always matches git. Idempotent.
#
#   labels: ["trainer-vm-diag-request"]
#   body:
#     cmd: |
#       cd /home/ubuntu/ict-trading-bot
#       git pull --ff-only origin main
#       sudo bash scripts/ops/install_trainer_candle_refresh.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "ERROR: must run as root (use sudo)" >&2; exit 2; }
SRC="$(cd "$(dirname "$0")/../.." && pwd)/deploy/trainer"
for u in ict-candle-refresh.service ict-candle-refresh.timer; do
  [ -f "$SRC/$u" ] || { echo "ERROR: missing $SRC/$u" >&2; exit 1; }
  install -m 0644 -o root -g root "$SRC/$u" "/etc/systemd/system/$u"
done
systemctl daemon-reload
systemctl enable --now ict-candle-refresh.timer
systemctl list-timers ict-candle-refresh.timer --no-pager
echo "ict-candle-refresh timer installed. Run once now: sudo systemctl start ict-candle-refresh.service"
