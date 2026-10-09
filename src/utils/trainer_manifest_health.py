"""Single-manifest OOM/timeout streak tracker for the trainer cycle — retry, never halt.

WHY (2026-07-17, BL-20260717-TRAINER-SINGLE-MANIFEST-OOM): the shared heavy-job
queue (`_trainer_heavy_lock.sh` + `trainer_heavy_lock.py`) serializes heavy jobs
so two of them never collide on the 6 GB box. But a queue can only stop
*contention* — it cannot shrink a job that doesn't fit **alone**. If a single
manifest's peak RSS exceeds the `MemoryMax=5G` cgroup cap on its own, it OOMs
every time it runs, contention or not.

`run_training_cycle.sh` BOUNDS that case (BL-20260716-TRAINER-WEDGE): a manifest
that OOM-thrashes or hangs is SIGTERM/SIGKILLed at `TRAINING_MANIFEST_TIMEOUT_S`
(default 30 min), logged `manifest_timeout`, and the cycle moves on — so an
oversized manifest can no longer wedge the box.

NO HALTING (operator directive 2026-10-09, Prime Directive rule 7 — *"Under
absolutely no circumstances does any pipeline halt training on its own without
getting verbatim permission from me, the operator. ... There is no halting."*).
Until 2026-10-09 this module QUARANTINED a manifest after 3 consecutive
OOM/timeouts and the cycle then SKIPPED it for 7 days unless someone set
`TRAINER_MANIFEST_QUARANTINE_CLEAR`. That was a self-halt of training, and it is
gone. What remains (state file under `runtime_logs/trainer/`, gitignored, so it
survives the cycle's per-run `git reset --hard origin/main`):

- `retry_decision` is consulted BEFORE running a manifest and ALWAYS says run.
  It exists to (a) report the current streak so the cycle can log that it is
  retrying a manifest that OOM'd last time, and (b) DROP a stale `quarantined_at`
  left by the pre-2026-10-09 code, with a log line — a leftover quarantine is
  never honoured.
- `record_oom_failure` increments the manifest's consecutive OOM/timeout streak
  and stores the DIAGNOSIS (exit code meaning, timeout cap, stderr tail). On the
  streak reaching `TRAINER_MANIFEST_OOM_FLAG_AFTER` (default 3) it returns
  `red_flag=True` exactly ONCE per failure episode — the caller emits ONE
  `manifest_oom_red_flag` cycle event carrying the evidence. Later failures in
  the same episode stay quiet (no ping every cycle) and the manifest keeps being
  retried every cycle.
- `record_success` clears the streak; if the episode had been red-flagged it
  returns `recovered=True` so the caller announces recovery ONCE.

There is no resource-reduction lever to retry with: the only memory knob in the
trainer path (`ml/experiments/runner.py` column projection) is already ON by
default, and `python -m ml train` has no batch/rows/n_jobs flag. So the retry is
a plain retry, and the red flag says so and names the dispositions (shrink /
GPU burst / drop — trainer-resource-protocol.md Rule 3).

SCOPE / SAFETY — trainer-VM tooling. Never touches the live order path.
Fail-open throughout: any state-file error degrades to "run it, no flag".

CLI (what `run_training_cycle.sh` calls):
    python -m src.utils.trainer_manifest_health decide  <manifest>              # always exit 0; JSON says streak
    python -m src.utils.trainer_manifest_health record-oom <manifest> <rc> [timeout_s] [stderr_file]  # exit 20 => red flag (once)
    python -m src.utils.trainer_manifest_health record-success <manifest>       # exit 30 => recovered (once)
    python -m src.utils.trainer_manifest_health list                            # human/diag dump

See docs/claude/trainer-resource-protocol.md § Rule 3.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ENV_STATE_FILE = "TRAINER_MANIFEST_OOM_STATE_FILE"     # state-file path override (tests)
_ENV_FLAG_AFTER = "TRAINER_MANIFEST_OOM_FLAG_AFTER"     # streak → ONE red flag

_DEFAULT_FLAG_AFTER = 3

# Exit-code meanings for the two codes the cycle routes here (GNU timeout(1)).
_RC_MEANING = {
    124: "SIGTERM at the per-manifest wall-clock cap (hang or OOM-thrash)",
    137: "SIGKILL (cgroup OOM-kill, or timeout --kill-after escalation)",
}

_RECOMMEND = (
    "retried every cycle (no halt). No reduced-resource retry lever exists in the "
    "trainer path (column projection is already default-on; `ml train` has no "
    "batch/rows/n_jobs flag), so a human/session must pick a Rule-3 disposition: "
    "(a) shrink its peak RSS, (b) route it to the GPU burst (gpu-burst-train.yml, "
    "within the monthly budget), or (c) drop/split it — it OOMs alone on the 6 GB box"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _log(msg: str) -> None:
    """One log line to stderr (the cycle captures it; tests read it)."""
    try:
        sys.stderr.write(f"[trainer_manifest_health] {msg}\n")
    except Exception:  # noqa: BLE001 — logging must never raise
        pass


def _repo_root() -> Path:
    env = os.environ.get("REPO_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def _state_file() -> Path:
    override = os.environ.get(_ENV_STATE_FILE)
    if override:
        return Path(override)
    return _repo_root() / "runtime_logs" / "trainer" / "manifest_oom_state.json"


def manifest_key(manifest: str) -> str:
    """Normalize a manifest reference to a stable key (its basename).

    So `ml/configs/foo.yaml`, `./foo.yaml`, and `foo.yaml` all map to the same
    streak row regardless of how the caller passes it.
    """
    return os.path.basename(str(manifest).strip()) or str(manifest).strip()


def _flag_after() -> int:
    """Streak length that raises the ONE red flag. Never disables the flag:
    a non-positive or unparseable value falls back to the default."""
    try:
        n = int(os.environ.get(_ENV_FLAG_AFTER, _DEFAULT_FLAG_AFTER))
    except (TypeError, ValueError):
        return _DEFAULT_FLAG_AFTER
    return n if n > 0 else _DEFAULT_FLAG_AFTER


def _load(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("manifests"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"manifests": {}}


def _save(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except OSError:
        pass  # fail-open: a tracker write failure must never abort the cycle


def _row(state: dict, key: str) -> dict:
    return state.setdefault("manifests", {}).setdefault(
        key,
        {"consecutive_oom": 0, "last_reason": None, "last_utc": None,
         "last_diagnosis": None, "flagged_at": None, "flag_count": 0},
    )


def _drop_stale_quarantines(state: dict) -> list:
    """Remove any `quarantined_at` left by the pre-2026-10-09 quarantine code.

    Returns the manifest keys it cleared (each gets a log line). The streak
    itself is kept — it is still a true count of consecutive failures — and a
    row that was quarantined is treated as already red-flagged, because the old
    code already raised its `manifest_quarantine_tripped` escalation for it.
    """
    cleared = []
    for key, row in state.get("manifests", {}).items():
        if not isinstance(row, dict) or "quarantined_at" not in row:
            continue
        stamp = row.pop("quarantined_at", None)
        row.pop("quarantine_count", None)
        if stamp:
            if not row.get("flagged_at"):
                row["flagged_at"] = stamp
                row["flag_count"] = int(row.get("flag_count", 0) or 0) + 1
            cleared.append(key)
            _log(f"ignored + removed stale quarantine for {key} (quarantined_at={stamp}); "
                 "quarantine is retired — the manifest is retried every cycle (NO-HALT 2026-10-09)")
    return cleared


def retry_decision(manifest: str, *, path: Optional[Path] = None) -> dict:
    """Pre-run check. ALWAYS `skip=False` — there is no quarantine any more.

    Returns `{skip, reason, consecutive_oom, flagged, stale_quarantine_cleared}`.
    `reason` is `retry_after_oom` when the manifest's last attempt(s) OOM'd (the
    cycle logs that it is retrying), else `ok`. Clears any stale quarantine left
    by the old code for EVERY manifest in the file, with a log line each.
    Fail-open: any error → run it.
    """
    p = path or _state_file()
    try:
        state = _load(p)
        cleared = _drop_stale_quarantines(state)
        if cleared:
            _save(p, state)
        row = state.get("manifests", {}).get(manifest_key(manifest)) or {}
        streak = int(row.get("consecutive_oom", 0) or 0)
        return {"skip": False,
                "reason": "retry_after_oom" if streak > 0 else "ok",
                "consecutive_oom": streak,
                "flagged": bool(row.get("flagged_at")),
                "stale_quarantine_cleared": cleared}
    except Exception:  # noqa: BLE001 — fail-open, never block a manifest on a tracker bug
        return {"skip": False, "reason": "tracker_error", "consecutive_oom": 0,
                "flagged": False, "stale_quarantine_cleared": []}


# Back-compat name: older callers/tests imported `quarantine_decision`.
quarantine_decision = retry_decision


def build_diagnosis(rc: str, *, timeout_s: Optional[str] = None,
                    stderr_tail: Optional[str] = None) -> dict:
    """The OOM/timeout diagnosis attached to the streak record and the red flag."""
    try:
        code = int(str(rc).split("(")[0])
    except (TypeError, ValueError):
        code = None
    diag = {"exit_code": code,
            "meaning": _RC_MEANING.get(code, "OOM/timeout-class exit"),
            "observed_utc": _now_iso()}
    if timeout_s not in (None, ""):
        try:
            diag["timeout_seconds"] = int(timeout_s)
        except (TypeError, ValueError):
            diag["timeout_seconds"] = timeout_s
    if stderr_tail:
        diag["stderr_tail"] = str(stderr_tail)[-500:]
    return diag


def record_oom_failure(manifest: str, reason: str, *, diagnosis: Optional[dict] = None,
                       path: Optional[Path] = None) -> dict:
    """Record an OOM/timeout-class failure. Never skips, never quarantines.

    Returns `{red_flag, consecutive_oom, flag_after, diagnosis, recommend}`.
    `red_flag=True` exactly once per failure episode — on the attempt whose
    streak first reaches `flag_after`. Later failures in the same episode return
    `red_flag=False` (bounded alert volume); a success ends the episode.
    Fail-open: a tracker error returns a quiet no-flag result.
    """
    p = path or _state_file()
    after = _flag_after()
    diag = diagnosis if isinstance(diagnosis, dict) else build_diagnosis(reason)
    try:
        state = _load(p)
        _drop_stale_quarantines(state)
        row = _row(state, manifest_key(manifest))
        row["consecutive_oom"] = int(row.get("consecutive_oom", 0) or 0) + 1
        row["last_reason"] = str(reason)
        row["last_utc"] = _now_iso()
        row["last_diagnosis"] = diag

        red_flag = False
        if row["consecutive_oom"] >= after and not row.get("flagged_at"):
            row["flagged_at"] = _now_iso()
            row["flag_count"] = int(row.get("flag_count", 0) or 0) + 1
            red_flag = True
        _save(p, state)
        return {"red_flag": red_flag,
                "consecutive_oom": row["consecutive_oom"],
                "flag_after": after,
                "diagnosis": diag,
                "recommend": _RECOMMEND}
    except Exception:  # noqa: BLE001 — fail-open
        return {"red_flag": False, "consecutive_oom": 0, "flag_after": after,
                "diagnosis": diag, "recommend": ""}


def record_success(manifest: str, *, path: Optional[Path] = None) -> dict:
    """Clear a manifest's OOM streak after a successful train.

    Returns `{cleared, recovered, prior_streak}`. `recovered=True` only when the
    episode had raised its red flag — the caller announces recovery ONCE.
    Fail-open.
    """
    p = path or _state_file()
    try:
        state = _load(p)
        _drop_stale_quarantines(state)
        row = state.get("manifests", {}).get(manifest_key(manifest))
        if not row:
            return {"cleared": False, "recovered": False, "prior_streak": 0}
        prior = int(row.get("consecutive_oom", 0) or 0)
        recovered = bool(row.get("flagged_at"))
        row["consecutive_oom"] = 0
        row["flagged_at"] = None
        row["last_reason"] = "trained_ok"
        row["last_utc"] = _now_iso()
        _save(p, state)
        return {"cleared": prior > 0 or recovered, "recovered": recovered,
                "prior_streak": prior}
    except Exception:  # noqa: BLE001 — fail-open
        return {"cleared": False, "recovered": False, "prior_streak": 0}


def failing_manifests(*, path: Optional[Path] = None) -> list:
    """Manifests currently on an OOM/timeout streak (for diag / review sessions)."""
    p = path or _state_file()
    state = _load(p)
    out = []
    for name, row in state.get("manifests", {}).items():
        if isinstance(row, dict) and int(row.get("consecutive_oom", 0) or 0) > 0:
            out.append({"manifest": name, **row})
    return out


def _main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.stderr.write("usage: decide|record-oom|record-success|list ...\n")
        return 2
    cmd, rest = argv[0], argv[1:]

    if cmd == "decide":
        if not rest:
            return 2
        sys.stdout.write(json.dumps(retry_decision(rest[0])) + "\n")
        return 0  # never a skip code: there is no halting

    if cmd == "record-oom":
        if len(rest) < 1:
            return 2
        reason = rest[1] if len(rest) > 1 else "oom_or_timeout"
        timeout_s = rest[2] if len(rest) > 2 else None
        stderr_tail = None
        if len(rest) > 3:
            try:
                with open(rest[3], encoding="utf-8", errors="replace") as fh:
                    stderr_tail = fh.read()[-2000:]
            except OSError:
                stderr_tail = None
        diag = build_diagnosis(reason, timeout_s=timeout_s, stderr_tail=stderr_tail)
        r = record_oom_failure(rest[0], reason, diagnosis=diag)
        sys.stdout.write(json.dumps(r) + "\n")
        return 20 if r.get("red_flag") else 0

    if cmd == "record-success":
        if not rest:
            return 2
        r = record_success(rest[0])
        sys.stdout.write(json.dumps(r) + "\n")
        return 30 if r.get("recovered") else 0

    if cmd == "list":
        sys.stdout.write(json.dumps({"failing": failing_manifests()}, indent=2) + "\n")
        return 0

    sys.stderr.write(f"unknown command: {cmd}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
