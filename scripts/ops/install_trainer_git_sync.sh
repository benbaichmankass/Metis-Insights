#!/usr/bin/env bash
# ============================================================================
# Install + enable the trainer code-sync timer on the trainer VM.
#
# Idempotent: copies deploy/ict-trainer-git-sync.{service,timer} into
# /etc/systemd/system, reloads systemd, enables + starts the timer, and runs one
# immediate sync. Safe to re-run. Trainer-only (autonomous territory); does
# NOT touch the live VM. Fixes BL-20260718-TRAINER-GITSYNC-STALE (the trainer
# had no dedicated auto-sync and drifted 495 commits behind main).
#
# Run on the trainer VM (via the trainer-vm-diag relay):
#   sudo bash scripts/ops/install_trainer_git_sync.sh
# ============================================================================
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"
UNIT_DIR="/etc/systemd/system"

echo "[install-trainer-git-sync] installing units from $REPO_ROOT/deploy/"
sudo install -m 0644 "$REPO_ROOT/deploy/ict-trainer-git-sync.service" "$UNIT_DIR/ict-trainer-git-sync.service"
sudo install -m 0644 "$REPO_ROOT/deploy/ict-trainer-git-sync.timer" "$UNIT_DIR/ict-trainer-git-sync.timer"
chmod +x "$REPO_ROOT/scripts/ops/trainer_git_sync.sh" || true

echo "[install-trainer-git-sync] daemon-reload + enable --now"
sudo systemctl daemon-reload
sudo systemctl enable --now ict-trainer-git-sync.timer

echo "[install-trainer-git-sync] one immediate sync"
sudo systemctl start ict-trainer-git-sync.service || true

echo "[install-trainer-git-sync] state:"
systemctl is-enabled ict-trainer-git-sync.timer || true
systemctl is-active ict-trainer-git-sync.timer || true
systemctl list-timers --all 2>/dev/null | grep -i trainer-git-sync || true
echo "[install-trainer-git-sync] done"
