#!/usr/bin/env bash
# scripts/ops/train_and_register_ws5_baselines.sh — one-shot trainer-VM
# bootstrap (S-AI-WS5 + S-AI-WS7 unblock).
#
# Trains every WS5 baseline manifest, registers each in the model
# registry, and walks the promotion ladder up to a configurable
# target stage (default: `shadow`, the minimum that lets the WS7
# harness load a model).
#
# Difference from scripts/ops/run_training_cycle.sh:
#   - run_training_cycle.sh is the recurring systemd-driven loop
#     (timer-triggered). It trains everything but stops at
#     `research_only` (the default registration state).
#   - This script is the **bootstrap** — run once after the trainer
#     VM comes up, to seed the registry with every WS5 baseline at a
#     stage that lets the WS7 harness load it. After this runs once,
#     the recurring run_training_cycle.sh keeps producing fresh
#     candidates without further intervention.
#
# Autonomous-Claude per docs/claude/trainer-vm-mode.md § 3.a. The
# promotion ladder is fully autonomous up to `live_approved`; this
# script defaults to `shadow` because (a) baselines are research-grade
# by definition and don't deserve `live_approved` automatically, and
# (b) `shadow` is the gate the WS7 factory enforces, so anything below
# it is unloadable. Override TARGET_STAGE to push further.
#
# Environment knobs (env vars):
#   REPO_ROOT           — defaults to /home/ubuntu/ict-trading-bot
#   VENV_DIR            — defaults to "$REPO_ROOT/.venv"
#   DATASETS_ROOT       — defaults to "$REPO_ROOT/datasets-out"
#   EXPERIMENTS_ROOT    — defaults to "$REPO_ROOT/ml/experiments-runs"
#   REGISTRY_ROOT       — defaults to "$REPO_ROOT/ml/registry-store"
#   TARGET_STAGE        — defaults to `shadow`. Must be one of:
#                         research_only | candidate | backtest_approved |
#                         shadow | advisory | limited_live | live_approved
#   PROMOTION_BY        — defaults to `claude-trainer`. Recorded as the
#                         actor in every StatusEvent the registry
#                         appends.
#   PROMOTION_REASON    — defaults to a structured boilerplate citing
#                         the trainer charter. Override to attach a
#                         specific sprint or session reference.
#   LOG_PATH            — defaults to
#                         "$REPO_ROOT/runtime_logs/trainer/ws5_baseline_kickoff.jsonl"
#   MANIFESTS           — defaults to every yaml under ml/configs/. Space-
#                         separated override.
#   PYTHON_BIN          — defaults to the venv's python after activate.
#
# Idempotency: each training run produces a new timestamp-keyed
# experiment + new model_id (the registry is append-only by WS4 rule).
# Re-running this script registers a fresh batch — no de-duplication.
# Operator's responsibility to not run this twice by accident.
#
# Exit codes:
#   0   every manifest trained + every promotion succeeded
#   1   one or more manifests failed at training OR promotion (first
#       failure short-circuits)
#   2   environment misconfigured (missing venv, missing repo, etc.)

# NOTE on shell flags: we deliberately do NOT use `set -e` because we
# need to capture exit codes from python sub-processes and emit JSONL
# events on failure. Each command is followed by an explicit rc check.
set -uo pipefail

REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv}"
DATASETS_ROOT="${DATASETS_ROOT:-$REPO_ROOT/datasets-out}"
EXPERIMENTS_ROOT="${EXPERIMENTS_ROOT:-$REPO_ROOT/ml/experiments-runs}"
REGISTRY_ROOT="${REGISTRY_ROOT:-$REPO_ROOT/ml/registry-store}"
TARGET_STAGE="${TARGET_STAGE:-shadow}"
PROMOTION_BY="${PROMOTION_BY:-claude-trainer}"
PROMOTION_REASON="${PROMOTION_REASON:-S-AI-WS5 bootstrap — autonomous-Claude per docs/claude/trainer-vm-mode.md § 3.a}"
LOG_PATH="${LOG_PATH:-$REPO_ROOT/runtime_logs/trainer/ws5_baseline_kickoff.jsonl}"

# Canonical promotion ladder, ordered. The script walks every step
# from `research_only` (the auto-registered initial state) up to
# TARGET_STAGE inclusive.
LADDER=(research_only candidate backtest_approved shadow advisory limited_live live_approved)

iso_now() {
  date -u +'%Y-%m-%dT%H:%M:%S+00:00'
}

emit() {
  # emit <event-json> — append to LOG_PATH AND echo to stdout.
  local payload="$1"
  mkdir -p "$(dirname "$LOG_PATH")"
  printf '%s\n' "$payload" >> "$LOG_PATH"
  printf '%s\n' "$payload"
}

ladder_index() {
  # ladder_index <stage> → echoes 0-based index in LADDER, or 255 if
  # not found.
  local target="$1"
  local i
  for i in "${!LADDER[@]}"; do
    if [ "${LADDER[$i]}" = "$target" ]; then
      printf '%s' "$i"
      return 0
    fi
  done
  return 255
}

# --- Validate inputs ------------------------------------------------------
if ! TARGET_INDEX="$(ladder_index "$TARGET_STAGE")"; then
  emit "$(printf '{"ts":"%s","status":"env_error","detail":"TARGET_STAGE %q not in promotion ladder"}' "$(iso_now)" "$TARGET_STAGE")"
  exit 2
fi

if [ ! -d "$REPO_ROOT/.git" ]; then
  emit "$(printf '{"ts":"%s","status":"env_error","detail":"REPO_ROOT %s is not a git repo"}' "$(iso_now)" "$REPO_ROOT")"
  exit 2
fi

cd "$REPO_ROOT"

# --- Venv -----------------------------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
  python3.11 -m venv "$VENV_DIR"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    emit "$(printf '{"ts":"%s","status":"env_error","detail":"venv creation failed (rc=%d)"}' "$(iso_now)" "$rc")"
    exit 2
  fi
  "$VENV_DIR/bin/pip" install --quiet --upgrade pip
  "$VENV_DIR/bin/pip" install --quiet -r requirements.txt
  emit "$(printf '{"ts":"%s","status":"venv_created","path":"%s"}' "$(iso_now)" "$VENV_DIR")"
fi
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
PYTHON_BIN="${PYTHON_BIN:-$VENV_DIR/bin/python}"

# --- Manifest list --------------------------------------------------------
if [ -z "${MANIFESTS:-}" ]; then
  mapfile -t MANIFEST_LIST < <(find ml/configs -maxdepth 1 -type f -name 'baseline-*.yaml' | sort)
else
  read -r -a MANIFEST_LIST <<<"$MANIFESTS"
fi

if [ "${#MANIFEST_LIST[@]}" -eq 0 ]; then
  emit "$(printf '{"ts":"%s","status":"no_manifests","detail":"MANIFESTS empty and ml/configs/ has no baseline-*.yaml"}' "$(iso_now)")"
  exit 2
fi

emit "$(python -c '
import json, sys
ts, target_stage, count = sys.argv[1:]
print(json.dumps({"ts": ts, "status": "bootstrap_start", "target_stage": target_stage, "manifest_count": int(count)}))
' "$(iso_now)" "$TARGET_STAGE" "${#MANIFEST_LIST[@]}")"

# --- For each manifest: train, then promote up the ladder -----------------
overall_rc=0
for manifest in "${MANIFEST_LIST[@]}"; do
  if [ ! -f "$manifest" ]; then
    emit "$(printf '{"ts":"%s","status":"manifest_missing","manifest":"%s"}' "$(iso_now)" "$manifest")"
    overall_rc=1
    break
  fi

  # --- Train --
  train_start="$(iso_now)"
  set +e
  "$PYTHON_BIN" -m ml train "$manifest" \
    --datasets-root "$DATASETS_ROOT" \
    --experiments-root "$EXPERIMENTS_ROOT" \
    --registry-root "$REGISTRY_ROOT" \
    > "/tmp/train_$$.out" 2>"/tmp/train_$$.err"
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    err_tail="$(tail -n 5 "/tmp/train_$$.err" 2>/dev/null | tr '\n' ' ' | head -c 500)"
    emit "$(python -c '
import json, sys
ts, manifest, started, rc, err = sys.argv[1:6]
print(json.dumps({
    "ts": ts,
    "status": "manifest_failed",
    "phase": "train",
    "manifest": manifest,
    "started": started,
    "exit_code": int(rc),
    "stderr_tail": err,
}))
' "$(iso_now)" "$manifest" "$train_start" "$rc" "$err_tail")"
    overall_rc=1
    rm -f "/tmp/train_$$.out" "/tmp/train_$$.err"
    break
  fi

  # `python -m ml train` prints a JSON summary on stdout — find the
  # last brace-line and extract model_id.
  model_id="$(tail -n 200 "/tmp/train_$$.out" \
    | python -c '
import json, sys
buf = sys.stdin.read()
# The summary is a single multi-line JSON object printed with
# indent=2. Try to parse the trailing well-formed JSON block.
start = buf.rfind("{")
end = buf.rfind("}")
if start == -1 or end == -1 or end < start:
    sys.exit("no JSON object in training stdout")
try:
    summary = json.loads(buf[start:end+1])
except json.JSONDecodeError as exc:
    sys.exit(f"JSON parse failed: {exc}")
mid = summary.get("model_id")
if not mid:
    sys.exit("training summary missing model_id (registration disabled?)")
print(mid)
' 2>/dev/null)"
  if [ -z "$model_id" ]; then
    emit "$(printf '{"ts":"%s","status":"manifest_failed","phase":"parse","manifest":"%s","detail":"could not extract model_id from training stdout"}' "$(iso_now)" "$manifest")"
    overall_rc=1
    rm -f "/tmp/train_$$.out" "/tmp/train_$$.err"
    break
  fi

  emit "$(python -c '
import json, sys
ts, manifest, model_id = sys.argv[1:]
print(json.dumps({
    "ts": ts,
    "status": "manifest_trained",
    "manifest": manifest,
    "model_id": model_id,
}))
' "$(iso_now)" "$manifest" "$model_id")"
  rm -f "/tmp/train_$$.out" "/tmp/train_$$.err"

  # --- Promote step-by-step up to TARGET_STAGE --
  # `python -m ml train` auto-registers at `research_only` (index 0).
  # We walk indices 1..TARGET_INDEX, calling promote for each.
  promote_failed=0
  for ((i = 1; i <= TARGET_INDEX; i++)); do
    next_stage="${LADDER[$i]}"
    set +e
    "$PYTHON_BIN" -m ml promote "$model_id" "$next_stage" \
      --registry-root "$REGISTRY_ROOT" \
      --by "$PROMOTION_BY" \
      --reason "$PROMOTION_REASON" \
      --gates-acknowledged \
      > "/tmp/promote_$$.out" 2>"/tmp/promote_$$.err"
    rc=$?
    set -e
    if [ "$rc" -ne 0 ]; then
      err_tail="$(tail -n 5 "/tmp/promote_$$.err" 2>/dev/null | tr '\n' ' ' | head -c 500)"
      emit "$(python -c '
import json, sys
ts, model_id, next_stage, rc, err = sys.argv[1:6]
print(json.dumps({
    "ts": ts,
    "status": "promote_failed",
    "model_id": model_id,
    "next_stage": next_stage,
    "exit_code": int(rc),
    "stderr_tail": err,
}))
' "$(iso_now)" "$model_id" "$next_stage" "$rc" "$err_tail")"
      overall_rc=1
      promote_failed=1
      rm -f "/tmp/promote_$$.out" "/tmp/promote_$$.err"
      break
    fi
    emit "$(python -c '
import json, sys
ts, model_id, next_stage = sys.argv[1:]
print(json.dumps({
    "ts": ts,
    "status": "promoted",
    "model_id": model_id,
    "stage": next_stage,
}))
' "$(iso_now)" "$model_id" "$next_stage")"
    rm -f "/tmp/promote_$$.out" "/tmp/promote_$$.err"
  done

  if [ "$promote_failed" -eq 1 ]; then
    break
  fi

  emit "$(python -c '
import json, sys
ts, manifest, model_id, target_stage = sys.argv[1:]
print(json.dumps({
    "ts": ts,
    "status": "manifest_done",
    "manifest": manifest,
    "model_id": model_id,
    "final_stage": target_stage,
}))
' "$(iso_now)" "$manifest" "$model_id" "$TARGET_STAGE")"
done

emit "$(printf '{"ts":"%s","status":"bootstrap_end","overall_rc":%d}' "$(iso_now)" "$overall_rc")"
exit "$overall_rc"
