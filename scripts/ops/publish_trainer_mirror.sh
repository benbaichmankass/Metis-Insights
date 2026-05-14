#!/usr/bin/env bash
# scripts/ops/publish_trainer_mirror.sh — push trainer-VM state to the live VM.
#
# Runs on the **trainer VM** (`ict-trainer-vm`). Builds a single
# `trainer_status.json` blob describing the trainer's current state
# (systemd units, last cycle, registry counts, dataset-build health,
# data-pull freshness) and rsyncs it — alongside the raw artifact
# files the live-VM dashboard wants to surface — into a mirror
# directory on the live VM.
#
# Destination on the live VM (read by the FastAPI router at
# src/web/api/routers/training_center.py):
#
#   /home/ubuntu/ict-trading-bot/runtime_logs/trainer_mirror/
#     trainer_status.json
#     training_cycle.jsonl
#     registry.jsonl
#     trainer/dataset_builds.jsonl
#     trainer/db_pulls.jsonl
#     experiments-runs/<model_id>/<run_id>/metrics.json
#
# Architecture rationale (S-AI-WS8-PART-2):
# The training center is autonomous and write-isolated from the live
# trader. To give the Streamlit dashboard transparency we mirror
# **read-only state** back over SSH. The trainer already holds an SSH
# key authorized on the live VM (used by `sync_trainer_data.sh` for
# the read-only DB pull); the same key is used here for the rsync
# push. No new credentials and no new attack surface — only
# JSON/JSONL artifacts move; nothing under `config/`, `src/`, or
# `runtime_logs/signal_audit.jsonl` is touched.
#
# Invocation:
#   - At the end of every training cycle (called from
#     `run_training_cycle.sh`).
#   - On a 2-minute heartbeat timer (`ict-trainer-publish.timer`)
#     so dashboard liveness reflects the trainer even between cycles.
#
# Environment knobs (with defaults matching the rest of scripts/ops/):
#   REPO_ROOT              — /home/ubuntu/ict-trading-bot
#   LIVE_VM_IP             — 158.178.210.252
#   LIVE_VM_USER           — ubuntu
#   LIVE_VM_MIRROR_PATH    — /home/ubuntu/ict-trading-bot/runtime_logs/trainer_mirror
#   VM_SSH_KEY             — ~/.ssh/ict-bot-ovm-private.key
#   TRAINER_VM_IP          — 158.178.209.121 (informational, written to JSON)
#   TRAINING_LOG_PATH      — $REPO_ROOT/runtime_logs/training_cycle.jsonl
#   REGISTRY_ROOT          — $REPO_ROOT/ml/registry-store
#   EXPERIMENTS_ROOT       — $REPO_ROOT/ml/experiments-runs
#   PUBLISH_LOG_PATH       — $REPO_ROOT/runtime_logs/trainer/publish.jsonl
#
# Exit codes:
#   0   status built and rsync succeeded
#   1   rsync failed (status JSON still written locally)
#   2   environment misconfigured (missing repo, missing key)
set -uo pipefail

REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"
LIVE_VM_IP="${LIVE_VM_IP:-158.178.210.252}"
LIVE_VM_USER="${LIVE_VM_USER:-ubuntu}"
# Live VM resolves `runtime_logs_dir()` (src/utils/paths.py) through the
# `DATA_DIR` umbrella env var set in `/etc/ict-trader/web-api.env` to
# `/data/bot-data`, putting runtime_logs on the attached storage volume.
# The trainer-mirror reader (`src/web/api/routers/training_center.py`)
# uses the same helper, so we MUST publish into the same canonical
# location — not into the repo-relative path. If `DATA_DIR` ever changes
# on the live VM, update this default to match (or set
# LIVE_VM_MIRROR_PATH at the systemd-unit level).
LIVE_VM_MIRROR_PATH="${LIVE_VM_MIRROR_PATH:-/data/bot-data/runtime_logs/trainer_mirror}"
VM_SSH_KEY="${VM_SSH_KEY:-$HOME/.ssh/ict-bot-ovm-private.key}"
TRAINER_VM_IP="${TRAINER_VM_IP:-158.178.209.121}"
TRAINING_LOG_PATH="${TRAINING_LOG_PATH:-$REPO_ROOT/runtime_logs/training_cycle.jsonl}"
REGISTRY_ROOT="${REGISTRY_ROOT:-$REPO_ROOT/ml/registry-store}"
EXPERIMENTS_ROOT="${EXPERIMENTS_ROOT:-$REPO_ROOT/ml/experiments-runs}"
PUBLISH_LOG_PATH="${PUBLISH_LOG_PATH:-$REPO_ROOT/runtime_logs/trainer/publish.jsonl}"

iso_now() { date -u +'%Y-%m-%dT%H:%M:%S+00:00'; }

mkdir -p "$(dirname "$PUBLISH_LOG_PATH")"

emit() {
  local payload="$1"
  printf '%s\n' "$payload" >> "$PUBLISH_LOG_PATH"
  printf '%s\n' "$payload"
}

if [ ! -d "$REPO_ROOT/.git" ]; then
  emit "$(printf '{"ts":"%s","status":"env_error","detail":"REPO_ROOT %s is not a git repo"}' "$(iso_now)" "$REPO_ROOT")"
  exit 2
fi
if [ ! -f "$VM_SSH_KEY" ]; then
  emit "$(printf '{"ts":"%s","status":"env_error","detail":"VM_SSH_KEY not found: %s"}' "$(iso_now)" "$VM_SSH_KEY")"
  exit 2
fi

STATUS_PATH="$REPO_ROOT/runtime_logs/trainer_status.json"
mkdir -p "$(dirname "$STATUS_PATH")"

# --- Build trainer_status.json --------------------------------------------
# Python stdlib only; produces a single JSON blob describing trainer state.
# Reads what's locally available; absent files become null fields rather
# than errors, so the dashboard can render a "trainer empty" view.
if ! python3 - "$REPO_ROOT" "$TRAINING_LOG_PATH" "$REGISTRY_ROOT" "$TRAINER_VM_IP" "$STATUS_PATH" <<'PY'
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

repo_root, training_log, registry_root, trainer_ip, out_path = sys.argv[1:6]
repo_root = Path(repo_root)
training_log = Path(training_log)
registry_root = Path(registry_root)
out_path = Path(out_path)

now = datetime.now(timezone.utc)
cutoff_24h = now - timedelta(hours=24)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def systemctl_show(unit: str) -> dict:
    """Return a small dict of systemd properties for unit. Empty on failure."""
    out = {
        "unit": unit,
        "active_state": None,
        "sub_state": None,
        "unit_file_state": None,
        "active_enter_iso": None,
        "next_elapse_iso": None,
        "last_trigger_iso": None,
    }
    try:
        proc = subprocess.run(
            [
                "systemctl",
                "show",
                unit,
                "--property=ActiveState",
                "--property=SubState",
                "--property=UnitFileState",
                "--property=ActiveEnterTimestamp",
                "--property=NextElapseUSecRealtime",
                "--property=LastTriggerUSec",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (subprocess.TimeoutExpired, OSError):
        return out
    if proc.returncode != 0:
        return out
    for line in proc.stdout.splitlines():
        k, _, v = line.partition("=")
        v = v.strip()
        if not v:
            continue
        if k == "ActiveState":
            out["active_state"] = v
        elif k == "SubState":
            out["sub_state"] = v
        elif k == "UnitFileState":
            out["unit_file_state"] = v
        elif k == "ActiveEnterTimestamp":
            out["active_enter_iso"] = v or None
        elif k == "NextElapseUSecRealtime":
            out["next_elapse_iso"] = v or None
        elif k == "LastTriggerUSec":
            out["last_trigger_iso"] = v or None
    return out


def safe_tail_jsonl(path: Path, n: int) -> list:
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows[-n:]


# --- VM facts --------------------------------------------------------------
uname = None
uptime_s = None
load_1m = None
try:
    uname = subprocess.check_output(["uname", "-srm"], text=True, timeout=2).strip()
except (subprocess.SubprocessError, OSError):
    pass
try:
    with open("/proc/uptime") as fh:
        uptime_s = int(float(fh.read().split()[0]))
except OSError:
    pass
try:
    load_1m = os.getloadavg()[0]
except OSError:
    pass

role = None
try:
    role = Path("/etc/ict-trainer-vm.role").read_text(encoding="utf-8").splitlines()[0].strip()
except (OSError, IndexError):
    pass

head_sha = None
try:
    head_sha = subprocess.check_output(
        ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
        text=True,
        timeout=2,
    ).strip()
except (subprocess.SubprocessError, OSError):
    pass

# --- Systemd state ---------------------------------------------------------
service = systemctl_show("ict-trainer.service")
timer = systemctl_show("ict-trainer.timer")

# --- Training cycle history ------------------------------------------------
cycle_rows = safe_tail_jsonl(training_log, 500)
last_cycle = None
last_cycle_outcome = None
cycles_24h = 0
manifests = Counter()
for row in cycle_rows:
    status = row.get("status")
    ts = parse_iso(row.get("ts"))
    if status == "cycle_end":
        if ts and ts >= cutoff_24h:
            cycles_24h += 1
        last_cycle = row
        last_cycle_outcome = row.get("overall_rc")
    elif status in {"manifest_ok", "manifest_failed", "manifest_missing"}:
        if ts and ts >= cutoff_24h:
            manifests[status] += 1

# --- Dataset build history ------------------------------------------------
build_rows = safe_tail_jsonl(repo_root / "runtime_logs" / "trainer" / "dataset_builds.jsonl", 500)
builds = Counter()
last_build_end = None
for row in build_rows:
    status = row.get("status")
    ts = parse_iso(row.get("ts"))
    if status in {"ok", "failed", "skipped"} and ts and ts >= cutoff_24h:
        builds[status] += 1
    if status == "build_end":
        last_build_end = row

# --- DB pull freshness ----------------------------------------------------
pull_rows = safe_tail_jsonl(repo_root / "runtime_logs" / "trainer" / "db_pulls.jsonl", 200)
last_pull_ok = None
last_pull_lines = None
for row in reversed(pull_rows):
    if row.get("status") == "sync_done" and row.get("overall_rc") == 0:
        last_pull_ok = row.get("ts")
        break
for row in reversed(pull_rows):
    if row.get("artifact") == "signal_audit.jsonl" and row.get("status") == "ok":
        last_pull_lines = row.get("lines")
        break

# --- Registry state -------------------------------------------------------
registry_path = registry_root / "registry.jsonl"
registry_rows = safe_tail_jsonl(registry_path, 10000)
model_ids: set = set()
stages: Counter = Counter()
for row in registry_rows:
    mid = row.get("model_id")
    if mid:
        model_ids.add(mid)
    # Stage may live at top-level or as the latest stage_history event.
    stage = row.get("target_deployment_stage") or row.get("stage")
    if not stage:
        history = row.get("stage_history") or []
        if history and isinstance(history, list):
            last = history[-1] if isinstance(history[-1], dict) else None
            if last:
                stage = last.get("to_stage") or last.get("stage")
    if stage:
        stages[stage] += 1

payload = {
    "ts": iso(now),
    "trainer_vm": {
        "ip": trainer_ip or None,
        "role": role,
        "uname": uname,
        "uptime_seconds": uptime_s,
        "load_1m": load_1m,
        "head_sha": head_sha,
    },
    "service": service,
    "timer": timer,
    "last_cycle": last_cycle,
    "last_cycle_outcome": last_cycle_outcome,
    "cycles_24h": cycles_24h,
    "manifests_24h": {
        "ok": manifests.get("manifest_ok", 0),
        "failed": manifests.get("manifest_failed", 0),
        "missing": manifests.get("manifest_missing", 0),
    },
    "dataset_builds_24h": {
        "ok": builds.get("ok", 0),
        "failed": builds.get("failed", 0),
        "skipped": builds.get("skipped", 0),
        "last_overall_rc": (last_build_end or {}).get("overall_rc"),
        "last_ts": (last_build_end or {}).get("ts"),
    },
    "registry": {
        "models": len(model_ids),
        "rows": len(registry_rows),
        "stages": dict(stages),
        "path_present": registry_path.exists(),
    },
    "data_pulls": {
        "last_ok_ts": last_pull_ok,
        "last_signal_audit_lines": last_pull_lines,
    },
    "publish": {
        "publisher": "publish_trainer_mirror.sh",
        "publisher_version": "1",
    },
}

out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
print(f"trainer_status_built {out_path}")
PY
then
  emit "$(printf '{"ts":"%s","status":"status_build_failed"}' "$(iso_now)")"
  exit 1
fi

emit "$(printf '{"ts":"%s","status":"status_built","path":"%s"}' "$(iso_now)" "$STATUS_PATH")"

# --- Push to live VM via rsync --------------------------------------------
SSH_OPTS="-i ${VM_SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o BatchMode=yes"

# Ensure mirror dirs exist on the live VM. mkdir -p is idempotent;
# permission of ubuntu user is sufficient (the dest is under ~ubuntu).
ssh ${SSH_OPTS} "${LIVE_VM_USER}@${LIVE_VM_IP}" \
  "mkdir -p '${LIVE_VM_MIRROR_PATH}/trainer' '${LIVE_VM_MIRROR_PATH}/experiments-runs'" \
  || { emit "$(printf '{"ts":"%s","status":"mkdir_failed"}' "$(iso_now)")"; exit 1; }

push_one() {
  local src="$1"
  local dst="$2"
  if [ ! -e "$src" ]; then return 0; fi
  rsync -az --no-perms --no-owner --no-group \
    -e "ssh ${SSH_OPTS}" \
    "$src" \
    "${LIVE_VM_USER}@${LIVE_VM_IP}:${LIVE_VM_MIRROR_PATH}/${dst}"
}

overall_rc=0
push_one "$STATUS_PATH"                                       "trainer_status.json"          || overall_rc=1
push_one "$TRAINING_LOG_PATH"                                  "training_cycle.jsonl"        || overall_rc=1
push_one "$REGISTRY_ROOT/registry.jsonl"                       "registry.jsonl"              || overall_rc=1
push_one "$REPO_ROOT/runtime_logs/trainer/dataset_builds.jsonl" "trainer/dataset_builds.jsonl" || overall_rc=1
push_one "$REPO_ROOT/runtime_logs/trainer/db_pulls.jsonl"      "trainer/db_pulls.jsonl"       || overall_rc=1

# Experiments tree: rsync recursively, only the metrics + manifest JSON
# (no raw model_state.json blobs unless small — see --include below).
if [ -d "$EXPERIMENTS_ROOT" ]; then
  rsync -az --no-perms --no-owner --no-group \
    -e "ssh ${SSH_OPTS}" \
    --include='*/' \
    --include='metrics.json' \
    --include='manifest.json' \
    --exclude='*' \
    "${EXPERIMENTS_ROOT}/" \
    "${LIVE_VM_USER}@${LIVE_VM_IP}:${LIVE_VM_MIRROR_PATH}/experiments-runs/" \
    || overall_rc=1
fi

if [ "$overall_rc" -eq 0 ]; then
  emit "$(printf '{"ts":"%s","status":"published","dest":"%s@%s:%s"}' \
    "$(iso_now)" "$LIVE_VM_USER" "$LIVE_VM_IP" "$LIVE_VM_MIRROR_PATH")"
else
  emit "$(printf '{"ts":"%s","status":"publish_partial_failure","dest":"%s@%s:%s"}' \
    "$(iso_now)" "$LIVE_VM_USER" "$LIVE_VM_IP" "$LIVE_VM_MIRROR_PATH")"
fi

exit "$overall_rc"
