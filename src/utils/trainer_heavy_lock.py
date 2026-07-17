"""Enforced heavy-job queue for the trainer VM — the Python (CLI) side.

WHY (2026-07-17, BL-20260717-TRAINER-QUEUE-ENFORCE): the shared heavy-job lock
(`scripts/ops/_trainer_heavy_lock.sh` + `runtime_logs/trainer/.heavy.lock`)
serializes the trainer's memory-heavy jobs so they don't thrash the 6 GB box.
But that lock was **voluntary** — the timer wrappers take it, yet a session
running a **bare** `python -m ml train` / `build-dataset` bypassed it entirely
and could still collide with a running cycle. A queue only works if it can't be
bypassed. So the ``ml`` CLI now acquires the SAME lock itself for its heavy
subcommands (`train`, `build-dataset`), making enforcement baseline rather than
opt-in — matching this repo's "a required capability must not sit behind a
voluntary path" philosophy.

SCOPE — trainer VM only. Enforcement fires ONLY when the trainer role marker
(`/etc/ict-trainer-vm.role`, written at trainer bootstrap; or `/etc/ict-vm-role`
== ``trainer``) is present. In CI / dev / the live VM / a web sandbox the marker
is absent, so this is a pure no-op — a bare `python -m ml train` there runs
exactly as before (no lock file, no wait).

RE-ENTRANCY — the timer wrappers + `trainer_run.sh` already hold the shell flock
for their whole run and export ``TRAINER_HEAVY_LOCK_HELD=1`` before invoking the
CLI; when that env is set the CLI skips acquisition, so the wrapper path never
double-locks (which would self-deadlock: parent holds the fd, child waits for
it).

FAIL-OPEN — any *infrastructure* error (marker unreadable, lock dir uncreatable,
open() fails) proceeds WITHOUT locking. Training is a required capability and
must never be blocked by a bug in the lock helper. Only a *clean* queue-timeout
(the box is genuinely busy past the wait) refuses the run — that's the whole
point of the queue, and it exits 75 (EX_TEMPFAIL) telling the caller to retry
later or route to the GPU burst.

See docs/claude/trainer-resource-protocol.md for the operator/session workflow.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Optional

# Heavy `ml` subcommands that must queue (the ~5 GB jobs). A per-strategy
# research backtest is NOT here — it's light and runs direct (see the protocol).
HEAVY_COMMANDS = frozenset({"train", "build-dataset"})

_ENV_HELD = "TRAINER_HEAVY_LOCK_HELD"        # a parent wrapper already holds it
_ENV_WAIT = "TRAINER_HEAVY_LOCK_WAIT_S"      # queue wait before giving up (s)
_ENV_FILE = "TRAINER_HEAVY_LOCK_FILE"        # lock-file path override (tests)
_ENV_DISABLE = "TRAINER_HEAVY_LOCK_DISABLED"  # explicit escape hatch
_ENV_FORCE = "TRAINER_HEAVY_LOCK_FORCE"      # force-enable off-trainer (tests)

_DEFAULT_WAIT_S = 3600.0
_POLL_S = 2.0


def _truthy(v: Optional[str]) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"} if v is not None else False


def _repo_root() -> Path:
    env = os.environ.get("REPO_ROOT")
    if env:
        return Path(env)
    # src/utils/trainer_heavy_lock.py -> parents[2] == repo root
    return Path(__file__).resolve().parents[2]


def _lock_file() -> Path:
    override = os.environ.get(_ENV_FILE)
    if override:
        return Path(override)
    return _repo_root() / "runtime_logs" / "trainer" / ".heavy.lock"


def _holder_file() -> Path:
    return _lock_file().parent / "heavy_lock_holder.json"


def on_trainer_vm() -> bool:
    """True on the trainer VM (role marker present), else False.

    The marker is `/etc/ict-trainer-vm.role` (written at trainer bootstrap) or
    `/etc/ict-vm-role` == ``trainer``. Absent in CI / dev / live / sandbox.
    `TRAINER_HEAVY_LOCK_FORCE` overrides for tests. Fail-safe: any read error
    reads as "not the trainer" so enforcement never fires off-box.
    """
    if _truthy(os.environ.get(_ENV_FORCE)):
        return True
    try:
        if Path("/etc/ict-trainer-vm.role").exists():
            return True
        role = Path("/etc/ict-vm-role")
        if role.exists() and role.read_text(encoding="utf-8").strip() == "trainer":
            return True
    except OSError:
        return False
    return False


def _write_holder(label: str) -> None:
    """Best-effort coordination flag: record who holds the queue + since when.

    Lets any session / diag read "the trainer is busy with <label>" before
    dispatching more heavy work. Advisory only — the flock is the real gate;
    a reader treats a holder whose `pid` is dead as stale.
    """
    try:
        payload = {
            "pid": os.getpid(),
            "label": label,
            "since_utc": datetime.now(timezone.utc).isoformat(),
        }
        hf = _holder_file()
        hf.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass  # advisory — never fail the run over the holder file


def read_holder() -> Optional[dict]:
    """Return the current heavy-lock holder record, or None.

    Returns None when the file is absent/garbled OR the recorded `pid` is no
    longer alive (a stale holder from a crashed job). Coordination read for
    sessions/diag — never raises.
    """
    try:
        hf = _holder_file()
        if not hf.exists():
            return None
        rec = json.loads(hf.read_text(encoding="utf-8"))
        pid = rec.get("pid")
        if isinstance(pid, int) and pid > 0:
            try:
                os.kill(pid, 0)  # liveness probe (no signal sent)
            except ProcessLookupError:
                return None       # holder died — stale
            except PermissionError:
                pass              # alive but not ours — still a live holder
        return rec
    except (OSError, ValueError):
        return None


def acquire_heavy_lock(label: str) -> Optional[IO]:
    """Acquire the shared heavy-job lock for a memory-heavy trainer job.

    No-op (returns None) when: explicitly disabled, a parent wrapper already
    holds it, or we're not on the trainer VM. Fail-open (returns None) on any
    infrastructure error. On a clean queue-timeout raises ``SystemExit(75)``.

    On success returns the open lock file object; keep the reference alive for
    the process lifetime (the flock releases when the fd closes / the process
    exits). Sets ``TRAINER_HEAVY_LOCK_HELD=1`` so nested subprocesses skip.
    """
    if _truthy(os.environ.get(_ENV_DISABLE)):
        return None
    if _truthy(os.environ.get(_ENV_HELD)):
        return None  # a wrapper already holds it — re-entrant skip
    if not on_trainer_vm():
        return None  # CI / dev / live / sandbox — inert

    try:
        lf = _lock_file()
        lf.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lf, "w", encoding="utf-8")  # noqa: SIM115 — held for process life
    except OSError as exc:
        sys.stderr.write(
            json.dumps({"status": "heavy_lock_infra_error", "label": label, "error": str(exc)}) + "\n"
        )
        return None  # fail-open: never block training on a lock-infra bug

    try:
        wait_s = float(os.environ.get(_ENV_WAIT, _DEFAULT_WAIT_S))
    except (TypeError, ValueError):
        wait_s = _DEFAULT_WAIT_S
    deadline = time.monotonic() + max(0.0, wait_s)

    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() >= deadline:
                sys.stderr.write(
                    json.dumps({"status": "heavy_lock_timeout", "label": label, "waited_s": wait_s}) + "\n"
                )
                sys.stderr.write(
                    "trainer heavy-lock busy past the wait; try later OR route this run to the "
                    "GPU burst (gpu-burst-train.yml, within the $10/mo budget). "
                    "See docs/claude/trainer-resource-protocol.md.\n"
                )
                fh.close()
                raise SystemExit(75)
            time.sleep(_POLL_S)

    os.environ[_ENV_HELD] = "1"  # nested subprocesses (this run) skip
    _write_holder(label)
    sys.stderr.write(json.dumps({"status": "heavy_lock_acquired", "label": label}) + "\n")
    return fh
