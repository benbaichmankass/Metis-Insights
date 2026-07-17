#!/usr/bin/env bash
# scripts/ops/run_drift_retrain.sh — drift-triggered retraining
# orchestrator (S-MLOPT-S16, M14 Phase 4.1).
#
# Runs `python -m ml drift-retrain` to ADWIN-scan every deployed head's
# real-time shadow-prediction scores. For each head the detector flags
# as drifting AND that resolves to a manifest under ml/configs/, fires
# `python -m ml train <manifest>` so the trainer rebuilds the model
# under whatever `sample_weight.half_life_days` (S-MLOPT-S2) the
# manifest already declares — naturally down-weighting the stale tail
# ADWIN told us to forget.
#
# **Conservative + logged**:
#   - RETRAIN_PLAN_ONLY=1 (the default) writes the JSONL log + prints the
#     dispatch list but does NOT execute any `ml train` subprocess.
#     Toggle RETRAIN_PLAN_ONLY=0 once the operator has eyeballed a few cycles.
#   - Every decision (drift or not) lands as a JSON line in
#     runtime_logs/drift_retrain.jsonl.
#   - The trainer can only register up to `live_approved` and the
#     shadow→advisory flip stays Tier-3, so a runaway retrain loop is
#     bounded — at worst it fills the registry with new candidate runs
#     that `promotion-readiness` still has to clear.
#
# Body of `ict-drift-retrain.service` (default DISABLED — operator opts
# in via the trainer-vm-diag relay).
#
# Environment knobs (all optional):
#   REPO_ROOT             defaults to /home/ubuntu/ict-trading-bot
#   VENV_DIR              defaults to "$REPO_ROOT/.venv"
#   REGISTRY_ROOT         defaults to "$REPO_ROOT/ml/registry-store"
#   DATASETS_ROOT         defaults to "$REPO_ROOT/datasets-out"
#   EXPERIMENTS_ROOT      defaults to "$REPO_ROOT/ml/experiments-runs"
#   CONFIGS_ROOT          defaults to "$REPO_ROOT/ml/configs"
#   SHADOW_LOG            defaults to "$REPO_ROOT/runtime_logs/shadow_predictions.jsonl"
#   DRIFT_LOG_PATH        defaults to "$REPO_ROOT/runtime_logs/drift_retrain.jsonl"
#   ADWIN_DELTA           defaults to 0.002
#   ADWIN_MIN_WINDOW      defaults to 10
#   ADWIN_MAX_WINDOW      defaults to 10000
#   RETRAIN_PLAN_ONLY               defaults to 1 (true) — set 0 to actually fire retrains
#
# Exit codes:
#   0   scan ran; no dispatch fired (no drift or dry run)
#   11  scan ran; at least one dispatch fired (or would have, in RETRAIN_PLAN_ONLY)
#   1   one or more dispatched `ml train` runs failed
#   2   environment misconfigured
set -euo pipefail

SCRIPT_NAME="run_drift_retrain"
REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"

# Shared heavy-job queue: this scan can dispatch `ml train` (~5 GB), so wait
# for any running training cycle / promotion-readiness / manual training before
# starting, and skip this run if the queue stays busy past the wait — so the
# 6 GB box never runs two heavy jobs at once. See
# docs/claude/trainer-resource-protocol.md.
# shellcheck source=/dev/null
. "$REPO_ROOT/scripts/ops/_trainer_heavy_lock.sh"
if ! take_trainer_heavy_lock "drift_retrain"; then
  echo '{"status":"heavy_lock_timeout","detail":"trainer queue busy past wait; skipping this drift-retrain run"}' >&2
  exit 0
fi

VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv}"
REGISTRY_ROOT="${REGISTRY_ROOT:-$REPO_ROOT/ml/registry-store}"
DATASETS_ROOT="${DATASETS_ROOT:-$REPO_ROOT/datasets-out}"
EXPERIMENTS_ROOT="${EXPERIMENTS_ROOT:-$REPO_ROOT/ml/experiments-runs}"
CONFIGS_ROOT="${CONFIGS_ROOT:-$REPO_ROOT/ml/configs}"
SHADOW_LOG="${SHADOW_LOG:-$REPO_ROOT/runtime_logs/shadow_predictions.jsonl}"
DRIFT_LOG_PATH="${DRIFT_LOG_PATH:-$REPO_ROOT/runtime_logs/drift_retrain.jsonl}"
ADWIN_DELTA="${ADWIN_DELTA:-0.002}"
ADWIN_MIN_WINDOW="${ADWIN_MIN_WINDOW:-10}"
ADWIN_MAX_WINDOW="${ADWIN_MAX_WINDOW:-10000}"
RETRAIN_PLAN_ONLY="${RETRAIN_PLAN_ONLY:-1}"

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

SCAN_OUT="$(mktemp)"
trap 'rm -f "$SCAN_OUT"' EXIT

set +e
"$VENV_DIR/bin/python" -m ml drift-retrain \
  --registry-root "$REGISTRY_ROOT" \
  --shadow-log "$SHADOW_LOG" \
  --configs-root "$CONFIGS_ROOT" \
  --delta "$ADWIN_DELTA" \
  --min-window "$ADWIN_MIN_WINDOW" \
  --max-window "$ADWIN_MAX_WINDOW" \
  --log-path "$DRIFT_LOG_PATH" \
  > "$SCAN_OUT"
SCAN_RC=$?
set -e

if [ "$SCAN_RC" -ne 0 ] && [ "$SCAN_RC" -ne 11 ]; then
  log_err "drift-retrain CLI exited $SCAN_RC; aborting"
  cat "$SCAN_OUT" >&2 || true
  exit 1
fi

DISPATCH_COUNT="$("$VENV_DIR/bin/python" -c "
import json, sys
payload = json.load(open('$SCAN_OUT'))
print(len(payload['summary']['dispatch']))
")"

log_err "scan complete: dispatch_count=$DISPATCH_COUNT cli_exit=$SCAN_RC plan_only=$RETRAIN_PLAN_ONLY"

if [ "$SCAN_RC" -ne 11 ]; then
  exit 0
fi

case "${RETRAIN_PLAN_ONLY,,}" in
  1|true|yes|on)
    log_err "RETRAIN_PLAN_ONLY active (plan-only); logged ${DISPATCH_COUNT} would-have-dispatched manifests, no ml train"
    exit 11
    ;;
esac

OVERALL_RC=11
MANIFESTS="$("$VENV_DIR/bin/python" -c "
import json
payload = json.load(open('$SCAN_OUT'))
for d in payload['decisions']:
    if d['action'] == 'dispatch':
        print(d['manifest_path'])
")"

if [ -z "$MANIFESTS" ]; then
  log_err "exit 11 but no dispatch manifests resolved; nothing to do"
  exit 11
fi

while IFS= read -r MANIFEST; do
  [ -z "$MANIFEST" ] && continue
  log_err "drift retrain: $MANIFEST"
  set +e
  "$VENV_DIR/bin/python" -m ml train "$MANIFEST" \
    --datasets-root "$DATASETS_ROOT" \
    --experiments-root "$EXPERIMENTS_ROOT" \
    --registry-root "$REGISTRY_ROOT"
  RC=$?
  set -e
  if [ "$RC" -ne 0 ] && [ "$RC" -ne 78 ]; then
    # 78 is the empty-dataset skip code from `_cmd_train`; treat any
    # other non-zero as a retrain failure (overall_rc=1) but keep
    # going on the rest so one bad manifest doesn't strand the cycle.
    log_err "ml train $MANIFEST exited $RC"
    OVERALL_RC=1
  fi
done <<< "$MANIFESTS"

exit "$OVERALL_RC"
