"""Single-manifest OOM quarantine for the trainer cycle — the "last gap" guard.

WHY (2026-07-17, BL-20260717-TRAINER-SINGLE-MANIFEST-OOM): the shared heavy-job
queue (`_trainer_heavy_lock.sh` + `trainer_heavy_lock.py`) serializes heavy jobs
so two of them never collide on the 6 GB box. But a queue can only stop
*contention* — it cannot shrink a job that doesn't fit **alone**. If a single
manifest's peak RSS exceeds the `MemoryMax=5G` cgroup cap on its own, it OOMs
every time it runs, contention or not.

`run_training_cycle.sh` already **bounds** that case (BL-20260716-TRAINER-WEDGE):
a manifest that OOM-thrashes or hangs is SIGTERM/SIGKILLed at
`TRAINING_MANIFEST_TIMEOUT_S` (default 30 min), logged `manifest_timeout`, and the
cycle moves on — so an oversized manifest can no longer wedge the box. The
RESIDUAL gap this module closes: the per-day `cycle_progress` file retries a
failed manifest every cycle (and starts fresh each UTC day), so a structurally
oversized manifest is retried **forever** — each retry burning up to 30 min of
the daily window, silently, with no escalation. Rule 3 of the trainer-resource
protocol says "if a single manifest won't fit, that's a flag, not a hack —
raise it as a backlog item with the offending manifest named" — but nothing
did that automatically.

WHAT THIS DOES — a cross-cycle OOM-streak tracker (state file under
`runtime_logs/trainer/`, which is gitignored and survives the cycle's per-run
`git reset --hard origin/main`, so the streak persists across cycles):

- `record_oom_failure` increments a manifest's consecutive OOM/timeout streak.
  At `TRAINER_MANIFEST_OOM_QUARANTINE_AFTER` (default 3) consecutive OOMs it
  **quarantines** the manifest — the cycle then SKIPS it instead of burning the
  window — and returns `just_tripped=True` so the caller emits the loud
  escalation event (which rides the trainer mirror to `/api/bot/ml/cycle`, where
  the next `/ml-review` / `/system-review` session sees it, lands a committed
  backlog item, and decides GPU-burst vs shrink).
- `record_success` clears the streak + quarantine (a manifest that trained fit,
  e.g. after a shrink landed — self-healing, no human toil).
- `quarantine_decision` is consulted BEFORE running a manifest: it returns
  `skip=True` while quarantined, EXCEPT once the quarantine is older than
  `TRAINER_MANIFEST_QUARANTINE_RECHECK_DAYS` (default 7) — then it lets ONE
  re-attempt through so a landed fix auto-clears it (success clears; another OOM
  refreshes the quarantine for another window). So a truly-stuck manifest wastes
  the window at most ~once/week, not every cycle.

SCOPE / SAFETY — this is trainer-VM tooling (Tier-1, autonomous). It never
touches the live order path. Training a manifest is NOT a "required live
capability gated off": skipping a manifest that provably can't fit, loudly and
reversibly, is the *correct* resource decision (Rule 3), and it's default-ON so
nothing is stranded. `TRAINER_MANIFEST_OOM_QUARANTINE_AFTER=0` disables the
mechanism entirely (pure passthrough = the prior bounded-retry behaviour — the
zero-touch rollback). Fail-open throughout: any state-file error degrades to
"run it" (never skips a manifest because of a tracker bug).

CLI (what `run_training_cycle.sh` calls):
    python -m src.utils.trainer_manifest_health decide  <manifest>   # exit 10 => skip
    python -m src.utils.trainer_manifest_health record-oom <manifest> <rc>  # exit 20 => just tripped
    python -m src.utils.trainer_manifest_health record-success <manifest>
    python -m src.utils.trainer_manifest_health list                 # human/diag dump

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
_ENV_QUARANTINE_AFTER = "TRAINER_MANIFEST_OOM_QUARANTINE_AFTER"   # streak → quarantine
_ENV_RECHECK_DAYS = "TRAINER_MANIFEST_QUARANTINE_RECHECK_DAYS"    # self-heal recheck
_ENV_CLEAR = "TRAINER_MANIFEST_QUARANTINE_CLEAR"        # one-shot manual clear (name|all)

_DEFAULT_QUARANTINE_AFTER = 3
_DEFAULT_RECHECK_DAYS = 7.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


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


def _quarantine_after() -> int:
    try:
        return int(os.environ.get(_ENV_QUARANTINE_AFTER, _DEFAULT_QUARANTINE_AFTER))
    except (TypeError, ValueError):
        return _DEFAULT_QUARANTINE_AFTER


def _recheck_days() -> float:
    try:
        return float(os.environ.get(_ENV_RECHECK_DAYS, _DEFAULT_RECHECK_DAYS))
    except (TypeError, ValueError):
        return _DEFAULT_RECHECK_DAYS


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
         "quarantined_at": None, "quarantine_count": 0},
    )


def _maybe_clear_from_env(state: dict) -> bool:
    """Honour a one-shot `TRAINER_MANIFEST_QUARANTINE_CLEAR` (manifest key | 'all').

    Returns True if it changed state. The operator/session sets this to force a
    quarantined manifest back into rotation (e.g. after landing a shrink or
    bumping it to GPU-burst) without editing the state file by hand.
    """
    target = (os.environ.get(_ENV_CLEAR) or "").strip()
    if not target:
        return False
    manifests = state.setdefault("manifests", {})
    changed = False
    if target.lower() == "all":
        for row in manifests.values():
            if row.get("quarantined_at") or row.get("consecutive_oom"):
                row["quarantined_at"] = None
                row["consecutive_oom"] = 0
                changed = True
    else:
        row = manifests.get(manifest_key(target))
        if row and (row.get("quarantined_at") or row.get("consecutive_oom")):
            row["quarantined_at"] = None
            row["consecutive_oom"] = 0
            changed = True
    return changed


def _age_days(iso_ts: Optional[str]) -> Optional[float]:
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(iso_ts)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (_now() - ts).total_seconds() / 86400.0
    except (TypeError, ValueError):
        return None


def quarantine_decision(manifest: str, *, path: Optional[Path] = None) -> dict:
    """Should this manifest be skipped this cycle?

    Returns `{skip, reason, consecutive_oom, quarantined_at, recheck_due}`.
    `skip=True` only when the manifest is quarantined AND the quarantine is not
    yet old enough to warrant a self-healing re-attempt. Fail-open: any error →
    `skip=False` (run it).
    """
    p = path or _state_file()
    try:
        state = _load(p)
        if _maybe_clear_from_env(state):
            _save(p, state)
        row = state.get("manifests", {}).get(manifest_key(manifest))
        if not row or not row.get("quarantined_at"):
            return {"skip": False, "reason": "not_quarantined",
                    "consecutive_oom": (row or {}).get("consecutive_oom", 0),
                    "quarantined_at": None, "recheck_due": False}
        recheck_days = _recheck_days()
        age = _age_days(row.get("quarantined_at"))
        recheck_due = recheck_days > 0 and age is not None and age >= recheck_days
        if recheck_due:
            return {"skip": False, "reason": "quarantine_recheck_due",
                    "consecutive_oom": row.get("consecutive_oom", 0),
                    "quarantined_at": row.get("quarantined_at"), "recheck_due": True}
        return {"skip": True, "reason": "quarantined_oom",
                "consecutive_oom": row.get("consecutive_oom", 0),
                "quarantined_at": row.get("quarantined_at"), "recheck_due": False}
    except Exception:  # noqa: BLE001 — fail-open, never block a manifest on a tracker bug
        return {"skip": False, "reason": "tracker_error", "consecutive_oom": 0,
                "quarantined_at": None, "recheck_due": False}


def record_oom_failure(manifest: str, reason: str, *, path: Optional[Path] = None) -> dict:
    """Record an OOM/timeout-class failure; quarantine at the threshold.

    Returns `{quarantined, just_tripped, consecutive_oom, quarantine_after,
    recommend}`. `just_tripped=True` on the cycle that first crosses the
    threshold (or re-quarantines a recheck that OOM'd again) — the caller emits
    the loud escalation on that signal. Fail-open: a tracker error returns a
    quiet no-quarantine result.
    """
    p = path or _state_file()
    after = _quarantine_after()
    try:
        state = _load(p)
        row = _row(state, manifest_key(manifest))
        was_quarantined = bool(row.get("quarantined_at"))
        row["consecutive_oom"] = int(row.get("consecutive_oom", 0)) + 1
        row["last_reason"] = str(reason)
        row["last_utc"] = _now_iso()

        just_tripped = False
        quarantined = was_quarantined
        # after<=0 disables quarantine entirely (pure passthrough / rollback).
        if after > 0 and row["consecutive_oom"] >= after:
            row["quarantined_at"] = _now_iso()   # (re)stamp — refreshes a failed recheck
            row["quarantine_count"] = int(row.get("quarantine_count", 0)) + (0 if was_quarantined else 1)
            quarantined = True
            # "just tripped" = first crossing OR a recheck attempt that OOM'd again
            # (was quarantined, we let it through, it failed → re-quarantine loudly).
            just_tripped = (not was_quarantined) or (row["consecutive_oom"] > after)
        _save(p, state)
        return {
            "quarantined": quarantined,
            "just_tripped": just_tripped,
            "consecutive_oom": row["consecutive_oom"],
            "quarantine_after": after,
            "recommend": ("route this manifest to the GPU burst (gpu-burst-train.yml, "
                          "within the $10/mo budget) or shrink its peak RSS "
                          "(batch size / dataset chunking) — it OOMs alone on the 6 GB box"),
        }
    except Exception:  # noqa: BLE001 — fail-open
        return {"quarantined": False, "just_tripped": False, "consecutive_oom": 0,
                "quarantine_after": after, "recommend": ""}


def record_success(manifest: str, *, path: Optional[Path] = None) -> dict:
    """Clear a manifest's OOM streak + quarantine after a successful train.

    Returns `{cleared}` (True if it had a streak/quarantine to clear). A manifest
    that trains fit, so this is the self-healing path when a shrink or a config
    change lands. Fail-open.
    """
    p = path or _state_file()
    try:
        state = _load(p)
        row = state.get("manifests", {}).get(manifest_key(manifest))
        if not row:
            return {"cleared": False}
        had = bool(row.get("quarantined_at")) or int(row.get("consecutive_oom", 0)) > 0
        row["consecutive_oom"] = 0
        row["quarantined_at"] = None
        row["last_reason"] = "trained_ok"
        row["last_utc"] = _now_iso()
        _save(p, state)
        return {"cleared": had}
    except Exception:  # noqa: BLE001 — fail-open
        return {"cleared": False}


def quarantined_manifests(*, path: Optional[Path] = None) -> list:
    """List currently-quarantined manifests (for diag / review sessions)."""
    p = path or _state_file()
    state = _load(p)
    out = []
    for name, row in state.get("manifests", {}).items():
        if row.get("quarantined_at"):
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
        d = quarantine_decision(rest[0])
        sys.stdout.write(json.dumps(d) + "\n")
        return 10 if d.get("skip") else 0

    if cmd == "record-oom":
        if len(rest) < 1:
            return 2
        reason = rest[1] if len(rest) > 1 else "oom_or_timeout"
        r = record_oom_failure(rest[0], reason)
        sys.stdout.write(json.dumps(r) + "\n")
        return 20 if r.get("just_tripped") else 0

    if cmd == "record-success":
        if not rest:
            return 2
        r = record_success(rest[0])
        sys.stdout.write(json.dumps(r) + "\n")
        return 0

    if cmd == "list":
        sys.stdout.write(json.dumps({"quarantined": quarantined_manifests()}, indent=2) + "\n")
        return 0

    sys.stderr.write(f"unknown command: {cmd}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
