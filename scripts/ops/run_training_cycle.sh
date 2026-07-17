#!/usr/bin/env bash
# scripts/ops/run_training_cycle.sh — body of ict-trainer.service
# (S-AI-WS9 follow-up).
#
# Runs a single training cycle on the training-center VM:
#   1. Pull latest main.
#   2. Activate the venv (creating if missing).
#   3. Sync label feedstock from the live VM (best-effort).
#   4. Rebuild all dataset families from the synced data (best-effort).
#   5. For each manifest listed in TRAINING_MANIFESTS (env var, space-
#      separated; defaults to all baseline manifests in ml/configs/),
#      run `python -m ml train <manifest>`. Each manifest writes a
#      timestamped experiment dir and registers the result at the stage
#      declared in the manifest's `target_deployment_stage` field
#      (2026-05-19: every baseline manifest declares `shadow`).
#   6. Print a one-line summary per manifest (model_id, key metric).
#   7. Exit 0 if every manifest ran. Exit non-zero on the first failure
#      (after logging); manifests with 0-row datasets are skipped with a
#      warning rather than aborting the cycle.
#
# Idempotent: training writes to a fresh experiment dir each time
# (timestamp-keyed). The registry is append-only by design (S-AI-WS4),
# so re-runs accumulate candidates without overwriting history.
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
#   TRAINING_CYCLE_FORCE_RESTART — set truthy to ignore today's checkpoint
#                           file and start the cycle clean (see below).
#
# Checkpoint / resume (2026-07-02, BL-20260702-TRAINER-OOM): a mid-cycle kill
# (OOM or otherwise) used to strand every not-yet-trained manifest for a full
# day — the manifest list was always the full fresh glob, with no record of
# what had already completed. Each invocation now reads/writes
# runtime_logs/trainer/cycle_progress_<UTC-date>.json (one entry per manifest:
# pending/running/done/skipped/failed) and only trains manifests NOT already
# done/skipped today, so a second same-day invocation (the new
# ict-trainer-catchup.timer, or a manual re-run) picks up exactly where the
# last one stopped instead of retraining everything. A `flock` on
# runtime_logs/trainer/.cycle.lock stops the catchup run from ever racing a
# still-in-progress primary run.
#
# Exit codes:
#   0   every manifest succeeded (or was already done/skipped on a resume)
#   1   one or more manifests failed
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

# --- Concurrency guard -------------------------------------------------------
# Stops the new catch-up timer (ict-trainer-catchup.timer, ~05:00 UTC) from
# ever racing a still-running primary cycle (or two manual invocations
# overlapping). A run that can't acquire the lock exits 0 immediately —
# "another cycle is already handling this" is not a failure.
mkdir -p "$REPO_ROOT/runtime_logs/trainer"
exec 9>"$REPO_ROOT/runtime_logs/trainer/.cycle.lock"
if ! flock -n 9; then
  emit "$(printf '{"ts":"%s","status":"cycle_locked","detail":"another run_training_cycle.sh invocation holds the lock; exiting"}' "$(iso_now)")"
  exit 0
fi

# --- Shared heavy-job queue --------------------------------------------------
# Beyond "no two CYCLES at once" (above), serialize against the OTHER
# memory-heavy trainer jobs (promotion-readiness, drift-retrain) and manual
# session training so they never thrash the 6 GB box concurrently. Blocks (a
# queue) until the shared lock is free; skips this run if the queue stays busy
# past the timeout. See docs/claude/trainer-resource-protocol.md.
# shellcheck source=/dev/null
. "$REPO_ROOT/scripts/ops/_trainer_heavy_lock.sh"
if ! take_trainer_heavy_lock "training_cycle"; then
  emit "$(printf '{"ts":"%s","status":"heavy_lock_timeout","detail":"another heavy trainer job held the queue past the wait; skipping this cycle, will retry next timer"}' "$(iso_now)")"
  exit 0
fi

# --- Pull latest -----------------------------------------------------------
# Self-heal onto a clean `main` every cycle. Past interactive sessions have
# left this checkout parked on stale `claude/*` session branches (with broken
# upstreams), which makes a manual `git pull` fail and leaves the box off-main
# between cycles. A plain `reset --hard origin/main` fixes the *content* but
# resets whatever branch happens to be checked out — so force-checkout `main`
# instead: content AND branch land on origin/main regardless of what was left
# behind, so a subsequent manual `git pull` Just Works.
git fetch --quiet origin main
git checkout --quiet --force -B main origin/main
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

# --- Data sync (best-effort) -----------------------------------------------
# Pull fresh trade_journal.db + signal_audit.jsonl from the live VM so
# each cycle trains on current data.  A sync failure is logged but does
# not abort training — existing datasets from the previous cycle are
# still usable.
if bash scripts/ops/sync_trainer_data.sh; then
  emit "$(printf '{"ts":"%s","status":"sync_ok"}' "$(iso_now)")"
else
  emit "$(printf '{"ts":"%s","status":"sync_warn","detail":"sync_trainer_data.sh returned non-zero; training will use cached data"}' "$(iso_now)")"
fi

# --- Dataset build (best-effort) -------------------------------------------
# Rebuild all families from the freshly synced feedstock.  A build failure
# is also non-fatal — the training loop will fail on whichever manifest
# can't find its dataset, and that manifest will be skipped (see below).
if DATASETS_ROOT="$DATASETS_ROOT" bash scripts/ops/build_trainer_datasets.sh; then
  emit "$(printf '{"ts":"%s","status":"datasets_ok"}' "$(iso_now)")"
else
  emit "$(printf '{"ts":"%s","status":"datasets_warn","detail":"build_trainer_datasets.sh returned non-zero; some datasets may be stale"}' "$(iso_now)")"
fi

# --- Manifest list ---------------------------------------------------------
if [ -z "${TRAINING_MANIFESTS:-}" ]; then
  # Default: every YAML under ml/configs/. Sorted for deterministic order.
  mapfile -t TRAINING_MANIFEST_LIST < <(find ml/configs -maxdepth 1 -type f -name '*.yaml' | sort)
  # Opt-in nightly rotation (MB-20260709): with TRAINING_MANIFEST_ROTATE=1, train
  # every other manifest by UTC day-parity so the torch-heavy heads (TCN, SSL-MAE)
  # and the LightGBM fleet alternate nights — halving a single cycle's cumulative
  # memory + runtime pressure. Safe because the checkpoint/resume + catch-up timer
  # already make a partial night fine: the untrained half simply retrains next
  # cycle. Default OFF (unset) → behaviour unchanged (train the full fleet).
  if [ "${TRAINING_MANIFEST_ROTATE:-0}" = "1" ]; then
    _parity="$(( 10#$(date -u +%j) % 2 ))"
    _i=0
    _rotated=()
    for _m in "${TRAINING_MANIFEST_LIST[@]}"; do
      [ "$(( _i % 2 ))" = "$_parity" ] && _rotated+=("$_m")
      _i=$(( _i + 1 ))
    done
    # Never let a parity split empty the list (e.g. a 1-manifest dir); fall back
    # to the full list if the rotation would train nothing this cycle.
    [ "${#_rotated[@]}" -gt 0 ] && TRAINING_MANIFEST_LIST=("${_rotated[@]}")
  fi
else
  # Split on whitespace.
  read -r -a TRAINING_MANIFEST_LIST <<<"$TRAINING_MANIFESTS"
fi

if [ "${#TRAINING_MANIFEST_LIST[@]}" -eq 0 ]; then
  emit "$(printf '{"ts":"%s","status":"no_manifests","detail":"TRAINING_MANIFESTS empty and ml/configs/ has no yaml"}' "$(iso_now)")"
  exit 2
fi

emit "$(printf '{"ts":"%s","status":"cycle_start","manifest_count":%d,"head":"%s"}' "$(iso_now)" "${#TRAINING_MANIFEST_LIST[@]}" "$HEAD_SHA")"

# --- Checkpoint / resume ----------------------------------------------------
# One progress file per UTC date. load_or_init_progress.py loads it (unless
# TRAINING_CYCLE_FORCE_RESTART is set or the file is missing/corrupt, in which
# case it (re)initialises fresh), merges in any manifest from today's list
# that the file doesn't know about yet (e.g. a new manifest landed since this
# morning), persists the result, and prints the newline-separated list of
# manifests still needing a run this cycle (any status other than
# done/skipped — a prior failure IS retried, since it's cheap and the cause
# may have been transient).
CYCLE_DATE="$(date -u +%Y-%m-%d)"
PROGRESS_FILE="$REPO_ROOT/runtime_logs/trainer/cycle_progress_${CYCLE_DATE}.json"
mapfile -t TO_RUN_LIST < <(
  python - "$PROGRESS_FILE" "$CYCLE_DATE" "$HEAD_SHA" "${TRAINING_CYCLE_FORCE_RESTART:-}" "${TRAINING_MANIFEST_LIST[@]}" <<'PY'
import json, sys
from datetime import datetime, timezone

path, cycle_date, head_sha, force = sys.argv[1:5]
manifests = sys.argv[5:]
force = force.strip().lower() in ("1", "true", "yes", "on")

state = None
if not force:
    try:
        with open(path, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if loaded.get("date") == cycle_date and isinstance(loaded.get("manifests"), dict):
            state = loaded
    except (OSError, json.JSONDecodeError, AttributeError):
        state = None

now = datetime.now(timezone.utc).isoformat()
if state is None:
    state = {"date": cycle_date, "head_sha": head_sha, "started_at": now,
              "updated_at": now, "status": "in_progress", "manifests": {}}

# Merge in any manifest the file doesn't know about yet (never drop rows for
# a manifest that vanished from today's list — keep its history).
for m in manifests:
    state["manifests"].setdefault(m, {"status": "pending", "started_at": None,
                                        "finished_at": None, "rc": None, "model_id": None})
state["status"] = "in_progress"
state["updated_at"] = now

import os
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as fh:
    json.dump(state, fh, indent=2)

for m in manifests:
    if state["manifests"].get(m, {}).get("status") not in ("done", "skipped"):
        print(m)
PY
)

resumed_count=$(( ${#TRAINING_MANIFEST_LIST[@]} - ${#TO_RUN_LIST[@]} ))
if [ "$resumed_count" -gt 0 ]; then
  emit "$(printf '{"ts":"%s","status":"cycle_resumed","already_done":%d,"to_run":%d}' "$(iso_now)" "$resumed_count" "${#TO_RUN_LIST[@]}")"
fi

progress_mark() {
  # progress_mark <manifest> <status> [key=value ...] — update one manifest's
  # row in PROGRESS_FILE. Best-effort: a write failure is logged, never fatal
  # (checkpoint/resume degrades to "start over next time", not a cycle abort).
  local manifest="$1" status="$2"; shift 2
  python - "$PROGRESS_FILE" "$manifest" "$status" "$@" <<'PY' 2>&1 || true
import json, sys
from datetime import datetime, timezone

path, manifest, status = sys.argv[1:4]
extra = dict(kv.split("=", 1) for kv in sys.argv[4:] if "=" in kv)

try:
    with open(path, encoding="utf-8") as fh:
        state = json.load(fh)
except (OSError, json.JSONDecodeError):
    sys.exit(0)  # progress file missing/corrupt — resume just degrades, don't fail the cycle

now = datetime.now(timezone.utc).isoformat()
row = state.setdefault("manifests", {}).setdefault(manifest, {})
row["status"] = status
if status == "running":
    row["started_at"] = now
elif status in ("done", "skipped", "failed"):
    row["finished_at"] = now
row.update(extra)
state["updated_at"] = now

with open(path, "w", encoding="utf-8") as fh:
    json.dump(state, fh, indent=2)
PY
}

if [ "${#TO_RUN_LIST[@]}" -eq 0 ]; then
  emit "$(printf '{"ts":"%s","status":"cycle_already_complete","detail":"every manifest already done/skipped today"}' "$(iso_now)")"
fi

# Mirror current state to the live VM before training starts so the
# dashboard reflects "cycle in progress" within seconds. Failure here
# is non-fatal — training proceeds and the post-cycle publish below
# will re-attempt.
bash scripts/ops/publish_trainer_mirror.sh >/dev/null 2>&1 \
  && emit "$(printf '{"ts":"%s","status":"publish_pre_ok"}' "$(iso_now)")" \
  || emit "$(printf '{"ts":"%s","status":"publish_pre_warn"}' "$(iso_now)")"

# --- Train each manifest --------------------------------------------------
overall_rc=0
# Per-manifest wall-clock cap (BL-20260716-TRAINER-WEDGE). Without this a single
# manifest that hangs or OOM-thrashes (btc-regime-5m-lgbm-flow-v1 wedged the
# 6 GB trainer for 18.7h in D-state on 2026-07-15/16, swap-thrashing at ~52s CPU,
# blocking the whole cycle AND every other ML session) runs unbounded. A normal
# manifest trains in ~1-5 min, so a 30-min default turns an 18-hour wedge into a
# bounded blip: on timeout the manifest is SIGTERM'd (SIGKILL 30s later), logged
# as manifest_timeout, and the cycle moves on. `0` disables (GNU timeout: 0 = no
# limit). Overridable via TRAINING_MANIFEST_TIMEOUT_S on the trainer unit.
TRAINING_MANIFEST_TIMEOUT_S="${TRAINING_MANIFEST_TIMEOUT_S:-1800}"

for manifest in "${TO_RUN_LIST[@]}"; do
  if [ ! -f "$manifest" ]; then
    emit "$(printf '{"ts":"%s","status":"manifest_missing","manifest":"%s"}' "$(iso_now)" "$manifest")"
    overall_rc=1
    continue
  fi
  # --- Single-manifest OOM quarantine (BL-20260717-TRAINER-SINGLE-MANIFEST-OOM) -
  # The heavy-job queue stops two jobs colliding, but can't shrink a manifest
  # that can't fit the 5 GB cgroup ALONE — that one OOMs every cycle. The cycle
  # already BOUNDS it (30-min cap below → continue), but the per-day progress
  # file retries it forever, burning up to TRAINING_MANIFEST_TIMEOUT_S each run.
  # This guard SKIPS a manifest that has crossed the OOM-streak quarantine
  # threshold so the window isn't wasted; it self-heals (one recheck lets it back
  # in after the recheck window, and a successful train clears it). Fail-open:
  # `decide` exits 0 on any tracker error, so the manifest runs normally.
  set +e
  python -m src.utils.trainer_manifest_health decide "$manifest" >/dev/null 2>&1
  q_rc=$?
  set -e
  if [ "$q_rc" -eq 10 ]; then
    emit "$(printf '{"ts":"%s","status":"manifest_quarantined","manifest":"%s","detail":"skipped: repeatedly OOMs alone on the 6 GB box — route to GPU burst (gpu-burst-train.yml) or shrink its peak RSS. trainer-resource-protocol.md Rule 3."}' "$(iso_now)" "$manifest")"
    progress_mark "$manifest" skipped reason=quarantined_oom
    continue
  fi
  start="$(iso_now)"
  progress_mark "$manifest" running
  # --- Observe-only build-time dataset audit (BL-20260628-XA-TRAINING-ZERO class) ---
  # Audit this manifest's built dataset for dead/constant feature columns and
  # single-class labels; append one row to dataset_audit.jsonl. OBSERVE-ONLY:
  # a flagged dataset is logged + emitted to the cycle log but STILL TRAINED,
  # so we can confirm the audit doesn't false-positive on a legitimately-sparse
  # feature before it ever gates. To ENFORCE (skip a quarantined manifest),
  # change the FLAGGED branch below to `continue`. Fully fail-open: any audit
  # error logs `audit_error` and falls through to the normal train step.
  AUDIT_LOG="${DATASET_AUDIT_LOG:-$REPO_ROOT/runtime_logs/trainer/dataset_audit.jsonl}"
  mkdir -p "$(dirname "$AUDIT_LOG")" 2>/dev/null || true
  audit_verdict="$(python - "$manifest" "$DATASETS_ROOT" "$AUDIT_LOG" <<'PY' 2>/dev/null || echo OK
import json, sys, datetime
from pathlib import Path
try:
    from ml.manifest import TrainingManifest
    from ml.datasets.audit import audit_dataset
    manifest_path, datasets_root, audit_log = sys.argv[1:4]
    m = TrainingManifest.from_yaml(Path(manifest_path))
    data = m.dataset.path_under(Path(datasets_root)) / "data.jsonl"
    rows = []
    if data.is_file():
        with data.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    report = audit_dataset(rows, m)
except Exception as exc:
    report = {"ok": True, "quarantine": False, "audit_error": f"{type(exc).__name__}: {exc}"}
report["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
report["manifest_path"] = sys.argv[1]
try:
    with open(sys.argv[3], "a", encoding="utf-8") as fh:
        fh.write(json.dumps(report) + "\n")
except Exception:
    pass
print("FLAGGED" if report.get("quarantine") else "OK")
PY
)"
  if [ "$audit_verdict" = "FLAGGED" ]; then
    emit "$(printf '{"ts":"%s","status":"manifest_audit_flagged","manifest":"%s","detail":"dataset audit flagged dead feature(s)/degenerate label (observe-only, still training) — see dataset_audit.jsonl"}' "$(iso_now)" "$manifest")"
  fi
  set +e
  # `timeout ... 0s` (TRAINING_MANIFEST_TIMEOUT_S=0) means no limit in GNU
  # coreutils, so the wrapper is unconditional and 0 opts out cleanly.
  timeout --kill-after=30s --signal=TERM "${TRAINING_MANIFEST_TIMEOUT_S}s" \
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
    progress_mark "$manifest" done
    # Trained fit → clear any OOM streak/quarantine for this manifest (self-heal).
    python -m src.utils.trainer_manifest_health record-success "$manifest" >/dev/null 2>&1 || true
  elif [ "$rc" -eq 78 ]; then
    # Exit 78 (BSD EX_CONFIG) — `python -m ml train` raised
    # EmptyDatasetError (reason=empty_dataset: dataset built but 0 rows yet —
    # live trader hasn't produced enough closed-trade history / no
    # health-review answers exist) OR its DatasetMissingError subclass
    # (reason=dataset_absent: the dataset file was never built, e.g. an orphan
    # manifest whose dataset family the daily build doesn't produce —
    # MB-20260606-001). BOTH are clean skips, not failures: overall_rc stays
    # unchanged so the cycle reports green when every manifest either trained
    # or was correctly skipped. The `reason` field below disambiguates them.
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
    "status": "manifest_skipped",
    "manifest": manifest,
    "started": start,
    "reason": summary.get("reason", "empty_dataset"),
    "dataset_path": summary.get("dataset_path"),
    "detail": summary.get("detail"),
}))
' "$(iso_now)" "$manifest" "$start" "$summary")"
    progress_mark "$manifest" skipped
    rm -f "/tmp/train_$$.out" "/tmp/train_$$.err"
    continue
  elif [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
    # timeout(1) exits 124 when it SIGTERM'd the run at the wall-clock cap,
    # 137 (128+9) when it had to SIGKILL after --kill-after. Either way the
    # manifest hung / OOM-thrashed past TRAINING_MANIFEST_TIMEOUT_S — it is
    # killed, logged, and the cycle proceeds to the next manifest instead of
    # wedging the box (BL-20260716-TRAINER-WEDGE).
    emit "$(python -c '
import json, sys
ts, manifest, start, rc, tmo = sys.argv[1:6]
print(json.dumps({
    "ts": ts,
    "status": "manifest_timeout",
    "manifest": manifest,
    "started": start,
    "exit_code": int(rc),
    "timeout_seconds": int(tmo),
    "detail": "killed at the per-manifest wall-clock cap (hang/OOM-thrash) — cycle continued",
}))
' "$(iso_now)" "$manifest" "$start" "$rc" "$TRAINING_MANIFEST_TIMEOUT_S")"
    progress_mark "$manifest" failed "rc=$rc(timeout)"
    overall_rc=1
    # Single-manifest OOM streak (BL-20260717-TRAINER-SINGLE-MANIFEST-OOM): count
    # this OOM/timeout. On crossing the quarantine threshold, escalate LOUDLY —
    # the trainer can't commit a backlog item itself (it resets --hard each
    # cycle), so this cycle event IS the durable signal (rides the mirror →
    # /api/bot/ml/cycle), where the next ml-/system-review routes the manifest to
    # the GPU burst or shrinks it. `record-oom` exits 20 on the trip.
    set +e
    q_oom="$(python -m src.utils.trainer_manifest_health record-oom "$manifest" "$rc" 2>/dev/null)"
    q_oom_rc=$?
    set -e
    if [ "$q_oom_rc" -eq 20 ]; then
      emit "$(printf '{"ts":"%s","status":"manifest_quarantine_tripped","manifest":"%s","detail":"repeatedly OOMs/timeouts ALONE on the 6 GB box — QUARANTINED from the cycle. ROUTE TO GPU BURST (gpu-burst-train.yml) or shrink its peak RSS. Auto-rechecks after the recheck window; a successful train clears it. trainer-resource-protocol.md Rule 3.","streak":%s}' "$(iso_now)" "$manifest" "$q_oom")"
    fi
    rm -f "/tmp/train_$$.out" "/tmp/train_$$.err"
    continue
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
    progress_mark "$manifest" failed "rc=$rc"
    overall_rc=1
    rm -f "/tmp/train_$$.out" "/tmp/train_$$.err"
    continue
  fi
  rm -f "/tmp/train_$$.out" "/tmp/train_$$.err"
done

emit "$(printf '{"ts":"%s","status":"cycle_end","overall_rc":%d}' "$(iso_now)" "$overall_rc")"

python - "$PROGRESS_FILE" <<'PY' 2>&1 || true
import json, sys
from datetime import datetime, timezone
path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as fh:
        state = json.load(fh)
except (OSError, json.JSONDecodeError):
    sys.exit(0)
state["status"] = "complete"
state["updated_at"] = datetime.now(timezone.utc).isoformat()
with open(path, "w", encoding="utf-8") as fh:
    json.dump(state, fh, indent=2)
PY

# --- Confidence calibrators (best-effort) ----------------------------------
# Fit per-strategy confidence calibrators over the validated multiyear history
# (unified-confidence design § 4a/4b) and write artifacts/calibration/{calibrators,report}.json.
# Run AFTER training + BEFORE the post-cycle publish so the fresh calibrators
# ride the same mirror push to the live VM (publish_trainer_mirror.sh sends
# calibration/calibrators.json; the live observe-only conviction loader picks it
# up read-only). Best-effort: fit_calibrators.sh always exits 0, and even a
# dispatch failure here is non-fatal — it must NOT flip overall_rc (mirrors the
# datasets_ok / publish best-effort style above).
bash scripts/ops/fit_calibrators.sh >/dev/null 2>&1 \
  && emit "$(printf '{"ts":"%s","status":"calibrators_ok"}' "$(iso_now)")" \
  || emit "$(printf '{"ts":"%s","status":"calibrators_warn"}' "$(iso_now)")"

# Mirror final state to the live VM so the dashboard reflects the
# cycle's outcome (and any new registry rows). Non-fatal — the 2-min
# heartbeat timer will pick it up on the next tick if this fails.
bash scripts/ops/publish_trainer_mirror.sh >/dev/null 2>&1 \
  && emit "$(printf '{"ts":"%s","status":"publish_post_ok"}' "$(iso_now)")" \
  || emit "$(printf '{"ts":"%s","status":"publish_post_warn"}' "$(iso_now)")"

exit "$overall_rc"
