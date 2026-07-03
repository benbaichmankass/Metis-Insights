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
# src/web/api/routers/training_center.py AND the WS7 shadow factory
# at ml/shadow/factory.py):
#
#   /data/bot-data/runtime_logs/trainer_mirror/
#     trainer_status.json                  ← dashboard
#     training_cycle.jsonl                 ← dashboard
#     registry.jsonl                       ← dashboard (synthesized
#                                            from models/ each cycle)
#     trainer/dataset_builds.jsonl         ← dashboard
#     trainer/db_pulls.jsonl               ← dashboard
#     experiments-runs/<model_id>/<run_id>/metrics.json  ← dashboard
#     models/<model_id>.json               ← shadow factory
#     backtests/<UTC-date>/SUMMARY.md       ← dashboard (Backtesting tab)
#     backtests/<UTC-date>/all_metrics.json ← dashboard (Backtesting tab)
#
# The `models/` subdir is the WS7 shadow factory's registry root —
# its `*.json` glob would otherwise pick up sibling artifacts at
# the parent level (notably `trainer_status.json`) and fail to
# parse them as `RegistryEntry` rows.
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
#   LIVE_VM_IP             — 141.145.193.91 (Ampere live trader; was 158.178.210.252 pre-2026-06-14)
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
LIVE_VM_IP="${LIVE_VM_IP:-141.145.193.91}"
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
# Backtest sweeps (strategy-improvement / validation harness output).
# `run_backtest_sweep.sh` writes <UTC-date>/{SUMMARY.md,all_metrics.json,
# *_metrics.json,...} here, OUTSIDE the repo. Mirror only the small
# JSON/MD artifacts (never the multi-MB candle CSVs the harness caches
# alongside them) — see the rsync filter below.
ICT_TRADER_DATA_ROOT="${ICT_TRADER_DATA_ROOT:-/home/ubuntu/ict-trader-data}"
BACKTESTS_ROOT="${BACKTESTS_ROOT:-$ICT_TRADER_DATA_ROOT/backtests}"
# Confidence calibrators (unified-confidence design § 4a/4b). Fit each cycle by
# scripts/ops/fit_calibrators.sh into $REPO_ROOT/artifacts/calibration/. Mirror
# them to the live VM under <mirror>/calibration/ so the live observe-only
# conviction loader (src/runtime/conviction_inputs.py::load_calibrators_cached)
# reads the fitted calibrators instead of falling back to raw normalization.
CALIBRATION_ROOT="${CALIBRATION_ROOT:-$REPO_ROOT/artifacts/calibration}"
# Live TSFM forecast-serve rows (M19 Track-1). scripts/ml/publish_live_forecasts.py
# writes the latest fc_* row per live symbol to
# runtime_logs/trainer_mirror/forecasts/<SYMBOL>.json. Mirror them to the live VM
# under <mirror>/forecasts/ so a FUTURE live reader (PR 1b) can serve the row
# with train==live parity. Empty/absent → the push below is a fail-permissive
# no-op (no forecasts have been produced yet).
FORECASTS_ROOT="${FORECASTS_ROOT:-$REPO_ROOT/runtime_logs/trainer_mirror/forecasts}"

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

# --- Synthesize registry.jsonl from per-model JSONs -----------------------
# The S-AI-WS9-FU-2 refactor switched the on-disk layout to per-model
# JSON files (`<model_id>.json`) but this publisher and its downstream
# consumer (`src/web/api/routers/training_center.py`) still read
# `registry.jsonl`. Synthesize it from the per-model JSONs each
# publish, atomic write-then-rename so a concurrent reader sees
# either the old file or the new one but never a partial.
#
# Doing this here (and not in `python -m ml train`) keeps the
# registry's external contract — one JSON per `model_id` — unchanged
# and avoids any new write coupling between the trainer's hot loop
# and a synthesized index file. The publisher is the only writer of
# registry.jsonl on the trainer.
if [ -d "$REGISTRY_ROOT" ]; then
  python3 - "$REGISTRY_ROOT" <<'PY' || \
    emit "$(printf '{"ts":"%s","status":"registry_synth_failed"}' "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)")"
import json
import os
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1])
out = root / "registry.jsonl"
models = 0
with tempfile.NamedTemporaryFile(
    mode="w", dir=str(root), suffix=".tmp", delete=False, encoding="utf-8"
) as fh:
    tmp_path = fh.name
    for f in sorted(root.glob("*.json")):
        if f.name == "registry.jsonl":
            continue
        try:
            entry = json.load(open(f, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
        models += 1
os.replace(tmp_path, out)
print(f"registry_synth_ok models={models} path={out}")
PY
fi

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
    elif status in {"manifest_ok", "manifest_failed", "manifest_missing", "manifest_skipped"}:
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
        "skipped": manifests.get("manifest_skipped", 0),
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
# `models/` is read by the WS7 shadow factory; `trainer/` +
# `experiments-runs/` are read by the dashboard router.
ssh ${SSH_OPTS} "${LIVE_VM_USER}@${LIVE_VM_IP}" \
  "mkdir -p '${LIVE_VM_MIRROR_PATH}/trainer' '${LIVE_VM_MIRROR_PATH}/experiments-runs' '${LIVE_VM_MIRROR_PATH}/models' '${LIVE_VM_MIRROR_PATH}/calibration' '${LIVE_VM_MIRROR_PATH}/forecasts'" \
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

# Confidence calibrators + reliability report (unified-confidence § 4a/4b).
# push_one no-ops if the file is absent (a cycle where fit_calibrators.sh hasn't
# run yet), so this never fails an otherwise-clean publish.
push_one "$CALIBRATION_ROOT/calibrators.json"                  "calibration/calibrators.json" || overall_rc=1
push_one "$CALIBRATION_ROOT/report.json"                       "calibration/report.json"      || overall_rc=1

# Per-model registry JSONs — the WS7 shadow factory in
# `ml/shadow/factory.py` reads `<DATA_DIR>/runtime_logs/trainer_mirror/models`
# on the live VM (resolved from `DATA_DIR=/data/bot-data` in
# `deploy/dropins/data-dir.conf`). The factory's `ModelRegistry(<that
# path>)` globs `*.json`, so the registry must land as concrete
# per-model files — the synthesized `registry.jsonl` above is for the
# dashboard's `/api/bot/ml/registry`, not for the strategy factory.
#
# The `models/` subdirectory isolates per-model JSONs from sibling
# artifacts like `trainer_status.json` at the parent level, which
# would otherwise be picked up by the registry's `*.json` glob and
# fail `RegistryEntry.from_dict()`. --delete-after is safe because
# nothing other than per-model JSONs lives under `models/`.
if [ -d "$REGISTRY_ROOT" ]; then
  rsync -az --no-perms --no-owner --no-group --delete-after \
    -e "ssh ${SSH_OPTS}" \
    --include='*.json' \
    --exclude='registry.jsonl' \
    --exclude='*' \
    "${REGISTRY_ROOT}/" \
    "${LIVE_VM_USER}@${LIVE_VM_IP}:${LIVE_VM_MIRROR_PATH}/models/" \
    || overall_rc=1
fi

# Experiments tree: rsync recursively. Includes metrics.json +
# manifest.json (consumed by the dashboard's run-detail drill-down)
# AND model_state.json (consumed by the WS7 shadow factory — the
# registry entry's `model_state_path` points at this file). Without
# the model_state.json include the factory's `_load_model_state`
# falls back via the mirror resolver in ml/shadow/factory.py but
# still finds nothing, leaving the strategy with an empty predictor
# list and no shadow predictions on the live VM. Baseline models are
# all small JSON dicts (few KB), not heavy weights — safe to mirror.
if [ -d "$EXPERIMENTS_ROOT" ]; then
  rsync -az --no-perms --no-owner --no-group \
    -e "ssh ${SSH_OPTS}" \
    --include='*/' \
    --include='metrics.json' \
    --include='manifest.json' \
    --include='model_state.json' \
    --exclude='*' \
    "${EXPERIMENTS_ROOT}/" \
    "${LIVE_VM_USER}@${LIVE_VM_IP}:${LIVE_VM_MIRROR_PATH}/experiments-runs/" \
    || overall_rc=1
fi

# Backtest sweeps: rsync the <UTC-date>/ dirs the strategy-improvement
# harness writes. Mirror ONLY the small text artifacts the dashboard's
# Backtesting tab renders — SUMMARY.md (the comparable table),
# all_metrics.json (per-variant Metrics for drill-down), any sibling
# *_metrics.json (e.g. ict_scalp_metrics.json), and the small stdout
# logs. The `--exclude='*'` after the includes drops everything else,
# notably the multi-MB candle CSVs (e.g. btc_5m_for_ict_scalp.csv) the
# harness caches alongside them — those must never cross the SSH push.
# Read on the live VM by `src/web/api/routers/backtests.py`
# (`/api/bot/backtests/sweeps`).
if [ -d "$BACKTESTS_ROOT" ]; then
  rsync -az --no-perms --no-owner --no-group \
    -e "ssh ${SSH_OPTS}" \
    --include='*/' \
    --include='SUMMARY.md' \
    --include='*_metrics.json' \
    --include='all_metrics.json' \
    --include='*_stdout.log' \
    --include='harness_stdout.log' \
    --exclude='*' \
    "${BACKTESTS_ROOT}/" \
    "${LIVE_VM_USER}@${LIVE_VM_IP}:${LIVE_VM_MIRROR_PATH}/backtests/" \
    || overall_rc=1
fi

# Live TSFM forecast-serve rows (M19 Track-1): rsync the per-symbol
# `forecasts/<SYMBOL>.json` artifacts written by
# scripts/ml/publish_live_forecasts.py. A future live reader (PR 1b) serves the
# latest fc_* row for train==live parity. `--include='*.json' --exclude='*'`
# mirrors the models/ push (only the small JSON files cross the SSH hop). The
# `-d` guard makes this a fail-permissive no-op until the first forecast run.
if [ -d "$FORECASTS_ROOT" ]; then
  rsync -az --no-perms --no-owner --no-group \
    -e "ssh ${SSH_OPTS}" \
    --include='*.json' \
    --exclude='*' \
    "${FORECASTS_ROOT}/" \
    "${LIVE_VM_USER}@${LIVE_VM_IP}:${LIVE_VM_MIRROR_PATH}/forecasts/" \
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
