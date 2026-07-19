#!/usr/bin/env bash
# scripts/ops/run_promotion_readiness.sh — trainer-side promotion-readiness
# sweep (S-MLOPT-S18, M14 Phase 4.3).
#
# Runs `python -m ml promotion-readiness` across the whole registry,
# writes report.json + SUMMARY.md under
#   runtime_logs/trainer_mirror/promotion_readiness/<UTC-date>/
# so the existing `publish_trainer_mirror.sh` rsync picks it up and the
# dashboard / Data Explorer can surface it.
#
# **Reports only — never auto-promotes.** When any model crosses the
# ready bar (`python -m ml promotion-readiness` exits 10) AND the trainer
# can reach the live VM via the same SSH key publish_trainer_mirror.sh
# uses, the orchestrator drops a JSON ping into the live VM's
# `runtime_logs/pending_pings/` queue so `ict-telegram-bot.service`
# delivers it to the operator chat. SSH failure is non-fatal — the
# report still lands locally and will mirror at the next publish tick.
#
# Body of `ict-promotion-readiness.service` (default DISABLED — operator
# opts in via the trainer-vm-diag relay). Designed to be safe to run
# stand-alone for ad-hoc sweeps.
#
# Environment knobs (all optional):
#   REPO_ROOT                    defaults to /home/ubuntu/ict-trading-bot
#   VENV_DIR                     defaults to "$REPO_ROOT/.venv"
#   REGISTRY_ROOT                defaults to "$REPO_ROOT/ml/registry-store"
#   DATASETS_ROOT                defaults to "$REPO_ROOT/datasets-out"
#   TRADE_JOURNAL_DB             resolved via scripts/ops/_lib.sh::runtime_db_path
#                                (canonical resolver — honours the systemd drop-in's
#                                 /data/bot-data/ pinning; never a CWD-relative fallback)
#   SHADOW_LOG                   defaults to "$REPO_ROOT/runtime_logs/shadow_predictions.jsonl"
#   BACKFILL_LOG                 defaults to "$REPO_ROOT/runtime_logs/shadow_predictions_backfill.jsonl"
#   READINESS_MIRROR_ROOT        defaults to "$REPO_ROOT/runtime_logs/trainer_mirror/promotion_readiness"
#   LIVE_VM_IP / LIVE_VM_USER    live VM SSH target (set in cloud-init)
#   VM_SSH_KEY                   SSH key for the live VM
#   LIVE_VM_PENDING_PINGS        defaults to /data/bot-data/runtime_logs/pending_pings
#   SKIP_PING                    when truthy, never push a pending_pings file
#
# Exit codes:
#   0  sweep ran; nothing to ping the operator about
#   10 sweep ran; at least one model is promote-ready or demote-proposed
#   1  sweep failed (CLI non-zero with no actionable proposals)
#   2  environment misconfigured (missing venv, repo, etc.)
set -euo pipefail

SCRIPT_NAME="run_promotion_readiness"
REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"
# Resolve TRADE_JOURNAL_DB through the canonical resolver in _lib.sh —
# the inline `${TRADE_JOURNAL_DB:-$REPO_ROOT/trade_journal.db}` idiom is
# forbidden by the canonical-db-resolver CI guard (it misses the systemd
# drop-in's /data/bot-data/ pinning, which is the bug load_runtime_env
# exists to fix).
# shellcheck source=/dev/null
. "$REPO_ROOT/scripts/ops/_lib.sh"

# Shared heavy-job queue: wait for any running training cycle / drift-retrain /
# manual training to finish before this ~3.2 GB sweep starts, so the 6 GB box
# never OOMs from two heavy jobs at once (BL-20260715). Skip this run if the
# queue stays busy past the wait. See docs/claude/trainer-resource-protocol.md.
# shellcheck source=/dev/null
. "$REPO_ROOT/scripts/ops/_trainer_heavy_lock.sh"
if ! take_trainer_heavy_lock "promotion_readiness"; then
  echo '{"status":"heavy_lock_timeout","detail":"trainer queue busy past wait; skipping this promotion-readiness run"}' >&2
  exit 0
fi

VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv}"
REGISTRY_ROOT="${REGISTRY_ROOT:-$REPO_ROOT/ml/registry-store}"
DATASETS_ROOT="${DATASETS_ROOT:-$REPO_ROOT/datasets-out}"
TRADE_JOURNAL_DB="$(runtime_db_path)"
SHADOW_LOG="${SHADOW_LOG:-$REPO_ROOT/runtime_logs/shadow_predictions.jsonl}"
BACKFILL_LOG="${BACKFILL_LOG:-$REPO_ROOT/runtime_logs/shadow_predictions_backfill.jsonl}"
READINESS_MIRROR_ROOT="${READINESS_MIRROR_ROOT:-$REPO_ROOT/runtime_logs/trainer_mirror/promotion_readiness}"
LIVE_VM_IP="${LIVE_VM_IP:-}"
LIVE_VM_USER="${LIVE_VM_USER:-ubuntu}"
VM_SSH_KEY="${VM_SSH_KEY:-$HOME/.ssh/id_ed25519}"
LIVE_VM_PENDING_PINGS="${LIVE_VM_PENDING_PINGS:-/data/bot-data/runtime_logs/pending_pings}"
SKIP_PING="${SKIP_PING:-0}"

iso_now() { date -u +'%Y-%m-%dT%H:%M:%S+00:00'; }
log_err() { printf '[%s] [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SCRIPT_NAME" "$*" >&2; }

if [ ! -d "$REPO_ROOT/.git" ]; then
  log_err "REPO_ROOT $REPO_ROOT is not a git repo"
  exit 2
fi

cd "$REPO_ROOT"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  log_err "venv not found at $VENV_DIR; run trainer_bootstrap.sh first"
  exit 2
fi

UTC_DATE="$(date -u +%Y-%m-%d)"
OUTPUT_DIR="$READINESS_MIRROR_ROOT/$UTC_DATE"
mkdir -p "$OUTPUT_DIR"

# Optional dataset root — forwarding it makes the sweep run a full
# purged-walk-forward oos_edge for EVERY shadow-stage model INLINE in one
# process, which OOM-thrashes the 6 GB trainer at the current registry size
# (~5 GB RSS, D-state, 0-byte outputs — killed twice on 2026-07-19;
# MB-20260719-PROMOREADY-OOSEDGE-OOM). INTERIM: default OFF so the daily
# report always lands; per-head oos_edge evidence comes from the
# single-model `gate-check` CLI, which is where promotion packets are
# assembled anyway (M25 reframe). Set PROMOREADY_OOS_EDGE=on to restore the
# in-sweep oos_edge once per-model subprocess isolation lands.
PROMOREADY_OOS_EDGE="${PROMOREADY_OOS_EDGE:-off}"
DATASETS_ARG=()
case "${PROMOREADY_OOS_EDGE,,}" in
  1|true|yes|on)
    if [ -d "$DATASETS_ROOT" ]; then
      DATASETS_ARG=(--datasets-root "$DATASETS_ROOT")
    else
      log_err "datasets root $DATASETS_ROOT absent; OOS-edge gate will report insufficient_data"
    fi
    ;;
  *)
    log_err "PROMOREADY_OOS_EDGE=off (interim, MB-20260719-PROMOREADY-OOSEDGE-OOM): sweep runs WITHOUT --datasets-root; oos_edge reports insufficient_data — use 'python -m ml gate-check' per head for oos_edge evidence"
    ;;
esac

DB_ARG=()
if [ -f "$TRADE_JOURNAL_DB" ]; then
  DB_ARG=(--db "$TRADE_JOURNAL_DB")
fi

# Run the sweep. Exit codes: 0 (uneventful), 10 (actionable), other = error.
set +e
"$VENV_DIR/bin/python" -m ml promotion-readiness \
  --registry-root "$REGISTRY_ROOT" \
  --shadow-log "$SHADOW_LOG" \
  --backfill-log "$BACKFILL_LOG" \
  --output-dir "$OUTPUT_DIR" \
  "${DB_ARG[@]}" \
  "${DATASETS_ARG[@]}" \
  > "$OUTPUT_DIR/cli_stdout.json" \
  2> "$OUTPUT_DIR/cli_stderr.log"
CLI_RC=$?
set -e

if [ "$CLI_RC" -ne 0 ] && [ "$CLI_RC" -ne 10 ]; then
  log_err "promotion-readiness CLI exited $CLI_RC; see $OUTPUT_DIR/cli_stderr.log"
  exit 1
fi

log_err "wrote report to $OUTPUT_DIR (cli exit $CLI_RC)"

# Operator ping: only when actionable AND not suppressed AND we have a
# live VM SSH target. The trainer's publish_trainer_mirror.sh has the
# same SSH topology — if that's wired up, this is too.
if [ "$CLI_RC" -ne 10 ]; then
  exit 0
fi
case "${SKIP_PING,,}" in
  1|true|yes|on)
    log_err "SKIP_PING set; not delivering operator ping"
    exit 10
    ;;
esac
if [ -z "$LIVE_VM_IP" ]; then
  log_err "LIVE_VM_IP unset; not delivering operator ping (report still on local mirror)"
  exit 10
fi
if [ ! -r "$VM_SSH_KEY" ]; then
  log_err "VM_SSH_KEY $VM_SSH_KEY unreadable; skipping operator ping"
  exit 10
fi

PING_MESSAGE="$("$VENV_DIR/bin/python" - "$OUTPUT_DIR/cli_stdout.json" <<'PY'
import json, sys
with open(sys.argv[1]) as fh:
    payload = json.load(fh)
msg = payload.get("ping_message") or ""
print(msg)
PY
)"

if [ -z "$PING_MESSAGE" ]; then
  log_err "CLI exited 10 but emitted no ping_message; refusing to push an empty ping"
  exit 10
fi

PING_FILE_LOCAL="$(mktemp)"
trap 'rm -f "$PING_FILE_LOCAL"' EXIT
PING_ID="$(date -u +%Y%m%dT%H%M%SZ)-promotion-readiness"
"$VENV_DIR/bin/python" - "$PING_FILE_LOCAL" "$PING_ID" "$PING_MESSAGE" <<'PY'
import json, sys
out, ping_id, message = sys.argv[1:4]
with open(out, "w") as fh:
    json.dump({
        "id": ping_id,
        "priority": "high",
        "source": "trainer.promotion_readiness",
        "message": message,
    }, fh, indent=2, sort_keys=True)
PY

SSH_OPTS=(-i "$VM_SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o BatchMode=yes)
REMOTE_PATH="$LIVE_VM_PENDING_PINGS/${PING_ID}.json"

ssh "${SSH_OPTS[@]}" "${LIVE_VM_USER}@${LIVE_VM_IP}" \
  "mkdir -p '${LIVE_VM_PENDING_PINGS}'" \
  || { log_err "failed to mkdir pending_pings on live VM; skipping push"; exit 10; }

if scp "${SSH_OPTS[@]}" "$PING_FILE_LOCAL" "${LIVE_VM_USER}@${LIVE_VM_IP}:${REMOTE_PATH}"; then
  log_err "pushed operator ping to ${REMOTE_PATH}"
else
  log_err "failed to push operator ping to ${REMOTE_PATH}; report still on local mirror"
fi

exit 10
