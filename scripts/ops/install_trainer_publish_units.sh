#!/usr/bin/env bash
# scripts/ops/install_trainer_publish_units.sh — install the trainer-mirror
# publisher systemd units on an already-running trainer VM.
#
# Cloud-init writes these units for newly provisioned trainers (see
# `deploy/training-vm-cloud-init.yaml`), but the existing trainer was
# provisioned before this PR, so its systemd is missing them. This
# script materializes the unit files, reloads systemd, and enables the
# 2-minute heartbeat timer.
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

systemctl daemon-reload
systemctl enable --now ict-trainer-publish.timer

systemctl status --no-pager ict-trainer-publish.timer 2>&1 | head -10 || true
echo
echo "ict-trainer-publish units installed and timer started."
