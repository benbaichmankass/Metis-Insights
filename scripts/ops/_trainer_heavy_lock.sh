#!/usr/bin/env bash
# Shared "one heavy trainer job at a time" QUEUE for the memory-constrained
# trainer VM (1 OCPU / 6 GB).
#
# WHY (2026-07-17, BL-20260715 + BL-20260717-TRAINER-CYCLE-TERM-AT-START):
# the trainer runs several memory-heavy jobs — the daily training cycle
# (`python -m ml train`, ~5 GB peak per manifest), the promotion-readiness
# sweep (~3.2 GB / ~99 min), drift-retrain (dispatches `ml train` when it
# fires), and AD-HOC MANUAL training run by other Claude sessions. On a 6 GB
# box any two of these running at once blows the `MemoryMax=5G` cgroup cap →
# cgroup-OOM, or swap-thrashes the whole VM until a session `sudo systemctl
# stop`s something to recover it (the observed Jul 14-17 failures). Staggering
# the timers helps but cannot coordinate with a human who starts a manual run.
#
# THE FIX = a QUEUE, not a mutex. Every memory-heavy job (timer OR manual)
# acquires ONE shared, BLOCKING lock before it starts heavy work: whoever
# arrives first runs; everyone else WAITS (up to a timeout) and then proceeds
# in turn. Result: work still gets done — just serialized — within the free
# tier we have, instead of thrashing. `flock` on a shared file is the
# right-sized primitive (no daemon, survives crashes — the kernel drops the
# lock when the holder dies).
#
# This is distinct from `run_training_cycle.sh`'s existing `.cycle.lock`
# (a NON-blocking self-lock that only stops TWO cycles overlapping); the heavy
# lock coordinates ACROSS the different job types + manual sessions.
#
# See docs/claude/trainer-resource-protocol.md for the operator/session
# workflow (incl. when to route heavy training to the GPU-burst platform
# instead of the trainer VM).
#
# Usage:
#   # A) sourced from a wrapper that should hold the lock for its whole run:
#   . "$REPO_ROOT/scripts/ops/_trainer_heavy_lock.sh"
#   take_trainer_heavy_lock "training_cycle" || exit 0   # 0 = "busy, skip this run"
#
#   # B) standalone, to run one command under the lock (what trainer_run.sh does):
#   scripts/ops/_trainer_heavy_lock.sh python -m ml train <manifest>

REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"
TRAINER_HEAVY_LOCK_FILE="${TRAINER_HEAVY_LOCK_FILE:-$REPO_ROOT/runtime_logs/trainer/.heavy.lock}"
# Max seconds to wait in the queue before giving up (then the caller skips this
# run and retries on its next timer, or the manual session is told to try
# later / use the GPU burst). Default 1h — long enough to queue behind one
# training cycle, short enough that a wedged holder doesn't pile jobs forever.
TRAINER_HEAVY_LOCK_WAIT_S="${TRAINER_HEAVY_LOCK_WAIT_S:-3600}"

# take_trainer_heavy_lock <label>
#   Opens fd 8 on the shared lock and blocks until it is free (or the timeout
#   elapses). Holds the lock via the open fd for the rest of the calling
#   script — released automatically on exit (or if the process dies). Returns
#   0 on acquire, 75 (EX_TEMPFAIL) on timeout.
take_trainer_heavy_lock() {
  local label="${1:-heavy}"
  mkdir -p "$(dirname "$TRAINER_HEAVY_LOCK_FILE")"
  exec 8>"$TRAINER_HEAVY_LOCK_FILE"
  if flock -w "$TRAINER_HEAVY_LOCK_WAIT_S" 8; then
    printf '{"status":"heavy_lock_acquired","label":"%s"}\n' "$label" >&2
    return 0
  fi
  printf '{"status":"heavy_lock_timeout","label":"%s","waited_s":%s}\n' \
    "$label" "$TRAINER_HEAVY_LOCK_WAIT_S" >&2
  return 75
}

# Standalone mode: run the given command under the heavy lock.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -euo pipefail
  take_trainer_heavy_lock "manual" || {
    echo "trainer heavy-lock busy > ${TRAINER_HEAVY_LOCK_WAIT_S}s; try later or use the GPU burst (gpu-burst-train.yml)." >&2
    exit 75
  }
  exec "$@"
fi
