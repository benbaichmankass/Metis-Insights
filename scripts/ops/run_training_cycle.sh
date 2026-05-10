#!/usr/bin/env bash
# scripts/ops/run_training_cycle.sh — body of ict-trainer.service
# (S-AI-WS9 follow-up).
#
# Runs a single training cycle on the training-center VM:
#   1. Pull latest main.
#   2. Activate the venv (creating if missing).
#   3. For each manifest listed in TRAINING_MANIFESTS (env var, space-
#      separated; defaults to all baseline manifests in ml/configs/),
#      run `python -m ml train <manifest>`. Each manifest writes a
#      timestamped experiment dir and registers the result as
#      `target_deployment_stage: research_only` in the local registry.
#   4. Print a one-line summary per manifest (model_id, key metric).
#   5. Exit 0 if every manifest ran. Exit non-zero on the FIRST failure
#      so systemd journal carries the error.
#
# Idempotent: training writes to a fresh experiment dir each time
# (timestamp-keyed). The registry is append-only by design (S-AI-WS4),
# so re-runs accumulate candidates without overwriting history.
#
# Datasets are NOT built here — the trainer assumes datasets already
# exist under DATASETS_ROOT. Cross-VM data sync (DB rsync from live VM)
# is a separate follow-up; until then, the operator must seed
# DATASETS_ROOT manually with the first build.
#
# Environment knobs:
#   REPO_ROOT             — defaults to /home/ubuntu/ict-trading-bot
#   VENV_DIR              — defaults to "$REPO_ROOT/.venv"
#   DATASETS_ROOT         — defaults to "$REPO_ROOT/datasets-out"
#   EXPERIMENTS_ROOT      — defaults to "$REPO_ROOT/ml/experiments-runs"
#   REGISTRY_ROOT         — defaults to "$REPO_ROOT/ml/registry-store"
#   TRAINING_MANIFESTS    — defaults to every yaml under ml/configs/
#   TRAINING_LOG_PATH     — defaults to "$REPO_ROOT/runtime_logs/training_cycle.jsonl"
#                           One JSON line per manifest:
#                           {ts, manifest, model_id, exit_code, metrics_path}
#
# Exit codes:
#   0   every manifest succeeded
#   1   one or more manifests failed (first failure short-circuits)
#   2   environment misconfigured (missing venv tooling, repo, etc.)
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv}"
DATASETS_ROOT="${DATASETS_ROOT:-$REPO_ROOT/datasets-out}"
EXPERIMENTS_ROOT="${EXPERIMENTS_ROOT:-$REPO_ROOT/ml/experiments-runs}"
REGISTRY_ROOT="${REGISTRY_ROOT:-$REPO_ROOT/ml/registry-store}"
TRAINING_LOG_PATH="${TRAINING_LOG_PATH:-$REPO_ROOT/runtime_logs/training_cycle.jsonl}"

iso_now() {
  date -u +'%Y-%m-%dT%H:%M:%S+00:00'
}

emit() {
  # emit <event-json> — append a JSONL row to TRAINING_LOG_PATH AND echo to stdout.
  local payload="$1"
  mkdir -p "$(dirname "$TRAINING_LOG_PATH")"
  printf '%s\n' "$payload" >> "$TRAINING_LOG_PATH"
  printf '%s\n' "$payload"
}

if [ ! -d "$REPO_ROOT/.git" ]; then
  emit "$(printf '{"ts":"%s","status":"env_error","detail":"REPO_ROOT %s is not a git repo"}' "$(iso_now)" "$REPO_ROOT")"
  exit 2
fi

cd "$REPO_ROOT"

# --- Pull latest -----------------------------------------------------------
git fetch --quiet origin main
git reset --hard --quiet origin/main
HEAD_SHA="$(git rev-parse --short HEAD)"
emit "$(printf '{"ts":"%s","status":"pulled","head":"%s"}' "$(iso_now)" "$HEAD_SHA")"

# --- Venv ------------------------------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
  python3.11 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install --quiet --upgrade pip
  "$VENV_DIR/bin/pip" install --quiet -r requirements.txt
  emit "$(printf '{"ts":"%s","status":"venv_created","path":"%s"}' "$(iso_now)" "$VENV_DIR")"
fi
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

# --- Manifest list ---------------------------------------------------------
if [ -z "${TRAINING_MANIFESTS:-}" ]; then
  # Default: every YAML under ml/configs/. Sorted for deterministic order.
  mapfile -t TRAINING_MANIFEST_LIST < <(find ml/configs -maxdepth 1 -type f -name '*.yaml' | sort)
else
  # Split on whitespace.
  read -r -a TRAINING_MANIFEST_LIST <<<"$TRAINING_MANIFESTS"
fi

if [ "${#TRAINING_MANIFEST_LIST[@]}" -eq 0 ]; then
  emit "$(printf '{"ts":"%s","status":"no_manifests","detail":"TRAINING_MANIFESTS empty and ml/configs/ has no yaml"}' "$(iso_now)")"
  exit 2
fi

emit "$(printf '{"ts":"%s","status":"cycle_start","manifest_count":%d,"head":"%s"}' "$(iso_now)" "${#TRAINING_MANIFEST_LIST[@]}" "$HEAD_SHA")"

# --- Train each manifest --------------------------------------------------
overall_rc=0
for manifest in "${TRAINING_MANIFEST_LIST[@]}"; do
  if [ ! -f "$manifest" ]; then
    emit "$(printf '{"ts":"%s","status":"manifest_missing","manifest":"%s"}' "$(iso_now)" "$manifest")"
    overall_rc=1
    break
  fi
  start="$(iso_now)"
  set +e
  python -m ml train "$manifest" \
    --datasets-root "$DATASETS_ROOT" \
    --experiments-root "$EXPERIMENTS_ROOT" \
    --registry-root "$REGISTRY_ROOT" \
    > "/tmp/train_$$.out" 2>"/tmp/train_$$.err"
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    # `python -m ml train` prints a JSON summary on success
    # ({"model_id": "...", "metrics_path": "...", ...}). Grab the last
    # non-empty line and shove it into the event.
    summary="$(tail -n 50 "/tmp/train_$$.out" | grep -E '^\{' | tail -n 1 || true)"
    if [ -z "$summary" ]; then summary='{}'; fi
    emit "$(python -c '
import json, sys
ts, manifest, start, summary_raw = sys.argv[1:5]
try:
    summary = json.loads(summary_raw)
except Exception:
    summary = {}
print(json.dumps({
    "ts": ts,
    "status": "manifest_ok",
    "manifest": manifest,
    "started": start,
    "model_id": summary.get("model_id"),
    "metrics_path": summary.get("metrics_path"),
}))
' "$(iso_now)" "$manifest" "$start" "$summary")"
  else
    err_tail="$(tail -n 5 "/tmp/train_$$.err" 2>/dev/null | tr '\n' ' ' | head -c 500)"
    emit "$(python -c '
import json, sys
ts, manifest, start, rc, err = sys.argv[1:6]
print(json.dumps({
    "ts": ts,
    "status": "manifest_failed",
    "manifest": manifest,
    "started": start,
    "exit_code": int(rc),
    "stderr_tail": err,
}))
' "$(iso_now)" "$manifest" "$start" "$rc" "$err_tail")"
    overall_rc=1
    rm -f "/tmp/train_$$.out" "/tmp/train_$$.err"
    break  # short-circuit on first failure
  fi
  rm -f "/tmp/train_$$.out" "/tmp/train_$$.err"
done

emit "$(printf '{"ts":"%s","status":"cycle_end","overall_rc":%d}' "$(iso_now)" "$overall_rc")"
exit "$overall_rc"
