#!/usr/bin/env bash
# ============================================================================
# Trainer code-sync — keep the trainer VM's checkout on a clean `origin/main`.
#
# WHY THIS EXISTS (BL-20260718-TRAINER-GITSYNC-STALE). The trainer had NO
# dedicated auto-sync: its ONLY path to current code was run_training_cycle.sh's
# once-daily `git checkout --force -B main origin/main` — which sits AFTER a
# heavy-lock early-exit, so a stuck/long heavy job (or a failing cycle) skips the
# git step entirely and the box silently runs stale code. On 2026-07-18 the
# trainer was found **495 commits behind main**, which broke the 15-min forecast
# producer (scripts/ops/run_forecast_producer.sh didn't exist in the stale
# checkout → exit 127 every tick → fc_* features frozen), and meant every
# gate-check / training run was on weeks-old logic.
#
# This decouples code-sync from the training cycle: a tiny, frequent,
# lock-free force-sync so "keep the code current" can never be blocked by "run
# training". CODE-ONLY — the trainer has no live trader/web-api to restart, so
# (unlike the live VM's ict-git-sync → deploy_pull_restart.sh) this restarts
# NOTHING. Python deps still ride the daily cycle's venv step; a code-only sync
# is the minimal fix for the actual failure (a missing script), and avoids a
# pip run hanging the 1-OCPU box every 15 min.
#
# Force-checkout (not a plain `reset --hard`) so the checkout also lands on the
# `main` BRANCH regardless of any stale `claude/*` session branch left behind —
# the same reasoning run_training_cycle.sh documents at its own sync step.
#
# Fail-safe: any error (network blip, transient auth) exits 0 so the timer just
# retries next tick — a sync failure must never leave a failed unit alarming.
# ============================================================================
set -uo pipefail

REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"
cd "$REPO_ROOT" 2>/dev/null || exit 0

git fetch --quiet origin main 2>/dev/null || exit 0
git checkout --quiet --force -B main origin/main 2>/dev/null || exit 0

exit 0
