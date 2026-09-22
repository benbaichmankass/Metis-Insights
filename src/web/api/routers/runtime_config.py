"""E31 — ``GET /api/bot/runtime-config``: the roster the PROCESS holds.

Tier 1 (unauthenticated read — see ``docs/api-tier-policy.md``). Read-only:
this router opens two config files to DIGEST them and one JSON artifact to
read it. It writes nothing, mutates nothing, and no order path consults it.

**Why this route exists rather than another field on ``/api/bot/config``.**
Every pre-existing surface answers *"what does the file on the VM say"* and
several of them said so in the language of runtime truth. ``/api/bot/config``
is honest in its field name (``yaml_mode``) and was wrong in its note;
``/api/bot/strategies`` published ``loaded`` documented as *"the names the
running process actually loaded"*. Both were reading
``runtime_status.json``'s ``live`` / ``strategies``, which the trading process
computes by re-parsing ``config/``. Adding a seventh field to a payload
readers already misread would not fix that. This route answers a different
question and says which one it is answering.

**The question it answers:** has the trading process taken the config that is
on the VM's disk right now? Not *is the file correct* (that is layer 5 / 6 of
``docs/reference/verifying-what-is-trading.md``), and not *did a leg trade*
(layer 2). Just: **file moved, process moved — or not.**

⚠️ **A roster is a LIST fact, not a behaviour fact** (operator, 2026-09-22):
*"just because something's not trading doesn't mean it's not on the roster —
it could just be sidelined."* So this route never infers roster membership
from trades, and a silent leg is not evidence of anything here.

⚠️ **This route reports, it never decides.** ``pending_reload`` is an
observation for a human or a checklist row, not a gate. Nothing in the order
path may branch on it — a surface built to detect a divergence must not be
able to cause one.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter

from src.runtime.loaded_config import (
    PROCESS_NOT_WRITTEN,
    PROCESS_OBSERVED,
    PROCESS_UNREADABLE,
    RELOAD_CURRENT,
    RELOAD_PENDING,
    RELOAD_UNKNOWN,
    digest_file,
    read_process_block,
    reload_state,
)
from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_ACCOUNTS_YAML = _REPO_ROOT / "config" / "accounts.yaml"
_STRATEGIES_YAML = _REPO_ROOT / "config" / "strategies.yaml"

# Same freshness window as ``/api/bot/strategies``. A stamp from a process
# that stopped ticking an hour ago is still a true record of what that process
# loaded — but it is not evidence about *now*, so the tick age rides along
# with every answer rather than being folded into it.
_TICK_FRESH_S = 300


def _tick_age_seconds(last_tick_utc: Any) -> Optional[float]:
    if not isinstance(last_tick_utc, str):
        return None
    try:
        ts = datetime.fromisoformat(last_tick_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - ts).total_seconds()


def _status_raw(path: Path) -> Dict[str, Any]:
    """The whole status artifact, for the tick timestamp. ``{}`` on any failure.

    Read-state for the artifact is carried by ``process_state``, which comes
    from ``read_process_block`` over the same file — so an empty dict here is
    never the thing a caller concludes from.
    """
    import json
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  (read-state is carried by process_state)
        return {}
    return raw if isinstance(raw, dict) else {}


def build_runtime_config(
    status_json: Optional[Path] = None,
    accounts_yaml: Optional[Path] = None,
    strategies_yaml: Optional[Path] = None,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Compose the payload. File I/O isolated behind the three path args."""
    s_path = status_json or (runtime_logs_dir() / "runtime_status.json")
    a_yaml = accounts_yaml or _ACCOUNTS_YAML
    s_yaml = strategies_yaml or _STRATEGIES_YAML
    now = now_utc or datetime.now(timezone.utc)

    process_state, block, why = read_process_block(s_path)

    # The on-disk side of the comparison. Digesting a file is not the same as
    # re-deriving the roster from it: the roster below comes only from the
    # process's own stamp, and these two digests exist solely to be compared
    # against the digests the process recorded when it loaded them.
    on_disk = {
        "strategies_yaml": digest_file(s_yaml),
        "accounts_yaml": digest_file(a_yaml),
    }

    raw = _status_raw(s_path)
    last_tick = raw.get("last_tick_utc")
    tick_age = _tick_age_seconds(last_tick)

    # --- branch on all three process states, and never emit a roster we did
    # --- not observe. `[]` here would say "the trader holds nothing", which is
    # --- a measurement we have not made.
    if process_state == PROCESS_OBSERVED and isinstance(block, dict):
        held: Optional[Dict[str, Any]] = {
            "pid": block.get("pid"),
            "boot_utc": block.get("boot_utc"),
            "snapshot_utc": block.get("snapshot_utc"),
            "strategies_yaml": block.get("strategies_yaml"),
            "accounts_yaml": block.get("accounts_yaml"),
        }
        reload_report = {
            key: reload_state(
                ((block.get(key) or {}).get("digest")
                 if isinstance(block.get(key), dict) else None),
                on_disk[key],
            )
            for key in ("strategies_yaml", "accounts_yaml")
        }
    else:
        # No roster, and the REASON is branched on explicitly rather than
        # left to an `else`: `not_written` (deploy the build / wait for a
        # tick) and `unreadable` (fix the artifact) have opposite remedies,
        # and an unrecognised state must not quietly inherit either.
        held = None
        if process_state == PROCESS_NOT_WRITTEN:
            why_unknown = ("the running process publishes no loaded-config "
                           "stamp, so there is nothing to compare the file "
                           "against")
        elif process_state == PROCESS_UNREADABLE:
            why_unknown = ("the runtime-status artifact could not be read — "
                           "we could not look")
        else:
            why_unknown = (f"unrecognised process_state {process_state!r} — "
                           f"treated as `we could not look`, never as current")
        reload_report = {
            key: {
                "reload_state": RELOAD_UNKNOWN,
                "loaded_digest": None,
                "on_disk_digest": on_disk[key].digest,
                "why": why_unknown,
            }
            for key in ("strategies_yaml", "accounts_yaml")
        }

    states = {r["reload_state"] for r in reload_report.values()}
    if RELOAD_PENDING in states:
        verdict = (
            "PENDING RELOAD — at least one config file on this VM has changed "
            "since the trading process loaded it. What is on disk is NOT what "
            "is trading."
        )
    elif states == {RELOAD_CURRENT}:
        verdict = (
            "CURRENT — the trading process loaded the exact bytes that are on "
            "disk now. `merged` and `deployed` agree for both config files; "
            "whether the merge reached this VM at all is layer 6."
        )
    else:
        verdict = (
            "UNKNOWN — the comparison could not be made for at least one file. "
            "This is NOT `current`: nothing here says the process and the disk "
            "agree."
        )

    return {
        "as_of": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "process_state": process_state,
        "process_state_why": why,
        "verdict": verdict,
        "last_tick_utc": last_tick,
        "tick_age_seconds": round(tick_age, 1) if tick_age is not None else None,
        "bot_ticking": (tick_age is not None and tick_age <= _TICK_FRESH_S),
        "git_sha": raw.get("git_sha"),
        # `null`, not `{}`: we publish the process's roster only when we
        # actually observed it.
        "loaded": held,
        "on_disk": {k: v.as_dict() for k, v in on_disk.items()},
        "reload": reload_report,
        "note": (
            "`loaded` is what the RUNNING PROCESS holds, stamped by the loaders "
            "themselves at load time. `on_disk` is the file on this VM right "
            "now. `/api/bot/config` and `/api/bot/strategies` report the FILE. "
            "See docs/reference/verifying-what-is-trading.md."
        ),
    }


@router.get("/runtime-config")
def get_runtime_config() -> Dict[str, Any]:
    return build_runtime_config()
