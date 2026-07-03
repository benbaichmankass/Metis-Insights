#!/usr/bin/env bash
# scripts/ops/install_trainer_publish_units.sh — install the trainer-mirror
# publisher + live-forecast-producer systemd units on an already-running
# trainer VM.
#
# Cloud-init writes these units for newly provisioned trainers (see
# `deploy/training-vm-cloud-init.yaml`), but an existing trainer provisioned
# before a given unit was added is missing it. This script materializes the
# unit files, reloads systemd, and enables:
#   - ict-trainer-publish.timer  (2-min mirror-publish heartbeat)
#   - ict-trainer-forecast.timer (15-min M19 fc_* forecast producer)
#
# Idempotent: re-running is safe. Each unit file is rewritten from this
# script, daemon-reload is fired, and the timer is left enabled+running.
#
# Runs on the trainer VM. Claude dispatches it autonomously via the
# trainer-vm-diag relay (trainer-VM systemd is in scope per
# `docs/claude/trainer-vm-mode.md` § 3.a — no operator approval).
# The dispatch is a single issue:
#
#   labels: ["trainer-vm-diag-request"]
#   body:
#     cmd: |
#       cd /home/ubuntu/ict-trading-bot
#       git pull --ff-only origin main
#       sudo bash scripts/ops/install_trainer_publish_units.sh
#
# Exit codes:
#   0   units installed/refreshed and timer enabled
#   1   systemd reload failed
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: must run as root (use sudo)" >&2
  exit 2
fi

PUB_SERVICE=/etc/systemd/system/ict-trainer-publish.service
PUB_TIMER=/etc/systemd/system/ict-trainer-publish.timer
FC_SERVICE=/etc/systemd/system/ict-trainer-forecast.service
FC_TIMER=/etc/systemd/system/ict-trainer-forecast.timer

cat >"$PUB_SERVICE" <<'UNIT'
# Trainer-mirror publisher (S-AI-WS8-PART-2).
[Unit]
Description=Publish trainer mirror to live VM
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu/ict-trading-bot
ExecStart=/bin/bash scripts/ops/publish_trainer_mirror.sh
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT
chmod 0644 "$PUB_SERVICE"
chown root:root "$PUB_SERVICE"

cat >"$PUB_TIMER" <<'UNIT'
# 2-minute heartbeat for trainer-mirror publisher.
[Unit]
Description=Periodic trainer mirror publish

[Timer]
OnBootSec=30sec
OnUnitActiveSec=2min
Persistent=true

[Install]
WantedBy=timers.target
UNIT
chmod 0644 "$PUB_TIMER"
chown root:root "$PUB_TIMER"

# M19 Track-1: the live TSFM forecast producer. Regenerates the fc_* serve
# artifacts every 15 min (the 15m bar); the publish timer above mirrors them to
# the live VM where forecast_live.py serves them to the shadow/regime scorer.
cat >"$FC_SERVICE" <<'UNIT'
# Live TSFM forecast producer (M19 Track-1, fc-head serve).
[Unit]
Description=Produce live TSFM forecast-serve artifacts (fc_*)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu/ict-trading-bot
# Nice: it imports torch/chronos — keep it off the training cycle's back on the
# 1-OCPU trainer. Inference is sub-second; only yields under real contention.
Nice=10
ExecStart=/bin/bash scripts/ops/run_forecast_producer.sh
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT
chmod 0644 "$FC_SERVICE"
chown root:root "$FC_SERVICE"

cat >"$FC_TIMER" <<'UNIT'
# 15-minute cadence for the live TSFM forecast producer (matches the 15m bar).
[Unit]
Description=Periodic live TSFM forecast production

[Timer]
OnBootSec=90sec
OnUnitActiveSec=15min
Persistent=true

[Install]
WantedBy=timers.target
UNIT
chmod 0644 "$FC_TIMER"
chown root:root "$FC_TIMER"

systemctl daemon-reload
systemctl enable --now ict-trainer-publish.timer
systemctl enable --now ict-trainer-forecast.timer
# Fire one production immediately so the first artifact exists without waiting
# out the 90s boot delay (best-effort).
systemctl start ict-trainer-forecast.service || true

systemctl status --no-pager ict-trainer-publish.timer 2>&1 | head -10 || true
systemctl status --no-pager ict-trainer-forecast.timer 2>&1 | head -10 || true
echo
echo "ict-trainer-publish + ict-trainer-forecast units installed and timers started."
