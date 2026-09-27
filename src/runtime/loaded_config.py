"""E31 — what the RUNNING PROCESS holds, as distinct from what the FILE says.

THE DEFECT THIS MODULE EXISTS TO REMOVE (E31, 2026-09-22). Every surface that
claimed to report the trader's runtime state was re-reading ``config/``:

* ``src/web/runtime_status.py::build_status`` — the ONE artifact the trading
  process itself writes — computed ``live`` as ``_read_live_per_account(
  accounts_yaml)`` and ``strategies`` as ``_read_strategy_names(
  strategies_yaml)``. Both OPEN AND PARSE the YAML at write time.
* ``/api/bot/config``'s ``trading_mode.note`` asserted *"live_per_account is
  the pipeline's runtime view"*, which is stronger than what that code does.
* ``/api/bot/strategies`` published a ``loaded`` column documented as *"the
  names the running process actually loaded"*, derived from the same re-read.

So ``the pipeline wrote it`` was being read as ``the pipeline is using it``,
and the two are not the same statement. ``ict-git-sync`` pulls ``main`` every
~5 min, which LOOKS like deployment: the file on the VM moves, every surface
re-reads it and shows the new value, and the process may still be running the
old one. On a live-money book that gap is a leg the evidence bar just cut
still trading real money with every surface showing the cut.

**merged ≠ deployed ≠ observed.** This module is the ``observed`` half.

WHAT IS ACTUALLY HELD IN MEMORY, measured 2026-09-22 by reading the code, and
it is ASYMMETRIC — which is exactly why a single "is it current?" boolean
would be wrong:

| config | how the trading process holds it | so a file edit takes effect |
|---|---|---|
| ``config/strategies.yaml`` | ``src.strategy_registry._cache`` is populated on the FIRST ``load_strategies()`` and never invalidated; ``pipeline.STRATEGY_ROSTER``/``STRATEGIES`` are module-level, resolved at IMPORT | **only on process restart** |
| ``config/accounts.yaml`` | ``src.units.accounts.load_accounts()`` re-opens the file on every call, and ``main._resolve_tick_symbols`` calls it every tick | on the next tick, no restart needed |

Neither of those facts was observable from outside the process. Both are now:
each loader stamps what it loaded (path, digest of the exact bytes, UTC
timestamp, and the resolved values), the stamp is published into
``runtime_status.json`` by the per-tick writer, and a reader compares the
stamped digest against the file on disk.

**READ-ONLY, and deliberately so.** Nothing here decides anything. The
stamping call sites are wrapped so a failure to OBSERVE can never perturb what
trades (``Coordinator.multi_account_execute`` calls ``load_accounts``), and no
consumer of this module may gate an order on it. A surface built to detect a
divergence must never be able to cause one.

THE TWO THREE-STATE CONTRACTS (docs/CLAUDE-RULES-CANONICAL.md § "Collapsed
states"; both registered in ``scripts/ci/check_collapsed_states.py``):

``process_state`` — can the reader see the process's own state at all?

| state | meaning |
|---|---|
| ``observed`` | the status artifact was read AND carries a process block |
| ``not_written`` | the artifact was read and carries NO process block — an older build, or a process that has not ticked since boot |
| ``unreadable`` | **we could not look** — missing, truncated, or malformed artifact |

⚠️ ``not_written`` and ``unreadable`` must never collapse, and NEITHER may
render as an empty roster: *"the trader holds no strategies"* is a real and
alarming measurement of the world, while *"we have not been told what the
trader holds"* says nothing about it at all. ``names`` is ``None`` — never
``[]`` — in both.

``reload_state`` — has the file moved out from under the process?

| state | meaning |
|---|---|
| ``current`` | the digest the process LOADED equals the digest on disk now |
| ``pending_reload`` | they DIFFER — the file moved and this process has not taken it |
| ``unknown`` | one side is missing, so no comparison was made |

⚠️ ``unknown`` is not ``current``. Defaulting an unestablished comparison to
"fine" is the whole bug class this module was built for; a reader that cannot
get both digests must say so rather than report agreement it never checked.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# --- process_state -----------------------------------------------------------
PROCESS_OBSERVED = "observed"
PROCESS_NOT_WRITTEN = "not_written"
PROCESS_UNREADABLE = "unreadable"
PROCESS_STATES = (PROCESS_OBSERVED, PROCESS_NOT_WRITTEN, PROCESS_UNREADABLE)

# --- reload_state ------------------------------------------------------------
RELOAD_CURRENT = "current"
RELOAD_PENDING = "pending_reload"
RELOAD_UNKNOWN = "unknown"
RELOAD_STATES = (RELOAD_CURRENT, RELOAD_PENDING, RELOAD_UNKNOWN)

# --- digest read-state -------------------------------------------------------
#: Not a registered collapsed-state contract of its own: every consumer reaches
#: it through ``reload_state``, whose ``unknown`` is the state that carries
#: "we could not look" onward. Kept as three values so the REASON survives into
#: the payload for a human reading it.
DIGEST_READ = "read"
DIGEST_ABSENT = "absent"
DIGEST_UNREADABLE = "unreadable"

#: How the process holds a config file. ``never_loaded`` is what makes an
#: un-loaded registry distinguishable from an empty one.
HELD_LOADED = "loaded"
HELD_NEVER_LOADED = "never_loaded"

_BOOT_UTC = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def digest_text(text: str) -> str:
    """``sha256:<hex>`` over the exact bytes a loader parsed."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DigestReading:
    """A file's digest, and whether we actually got one.

    ``digest`` is ``None`` for BOTH ``absent`` and ``unreadable`` — which is
    why ``state`` exists and why no caller may infer the condition from the
    digest being falsy.
    """

    state: str
    digest: Optional[str] = None
    mtime_utc: Optional[str] = None
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "digest_state": self.state,
            "digest": self.digest,
            "mtime_utc": self.mtime_utc,
            "error": self.error,
        }


def digest_file(path: Path | str) -> DigestReading:
    """Digest *path* as it is on disk right now. Never raises."""
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return DigestReading(state=DIGEST_ABSENT, error=f"{p} does not exist")
    except Exception as exc:  # noqa: BLE001
        return DigestReading(state=DIGEST_UNREADABLE, error=f"{type(exc).__name__}: {exc}")
    try:
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        mtime_utc = mtime.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except Exception:  # noqa: BLE001
        mtime_utc = None
    return DigestReading(state=DIGEST_READ, digest=digest_text(raw), mtime_utc=mtime_utc)


@dataclass
class LoadStamp:
    """What a loader actually loaded, stamped at the moment it loaded it.

    ``values`` is the loader's own resolved view — the strategy names and
    execution gates the registry cached, or the per-account ``dry_run`` and
    routed-strategy lists the account builder produced. It is the answer to
    *"what does the process hold"*, and it is recorded BY the code that holds
    it rather than reconstructed by a reader.
    """

    path: str
    digest: str
    loaded_at_utc: str
    values: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "held_state": HELD_LOADED,
            "path": self.path,
            "digest": self.digest,
            "loaded_at_utc": self.loaded_at_utc,
            **self.values,
        }


def never_loaded(path: str, detail: str) -> Dict[str, Any]:
    """The sub-block for a config THIS PROCESS has not loaded.

    Every value-bearing key is ``None``, never ``[]`` / ``{}`` / ``0``: an
    un-loaded registry and an empty one are opposite findings and a reader
    must not be able to confuse them.
    """
    return {
        "held_state": HELD_NEVER_LOADED,
        "path": path,
        "digest": None,
        "loaded_at_utc": None,
        "detail": detail,
    }


def new_stamp(path: str, digest: str, **values: Any) -> LoadStamp:
    return LoadStamp(path=path, digest=digest, loaded_at_utc=_utc_now(), values=values)


# ---------------------------------------------------------------------------
# Producer: assemble the block the per-tick writer publishes.
# ---------------------------------------------------------------------------
def process_snapshot() -> Dict[str, Any]:
    """The calling process's own loaded state. Never raises, never loads.

    ⚠️ **This function must not trigger a load.** It reports what the process
    already holds; calling ``load_strategies()`` here would manufacture the
    very freshness it exists to measure, and the answer would be "current" by
    construction on every tick.
    """
    block: Dict[str, Any] = {
        "pid": os.getpid(),
        "boot_utc": _BOOT_UTC,
        "snapshot_utc": _utc_now(),
    }
    try:
        from src.strategy_registry import cache_stamp
        stamp = cache_stamp()
        block["strategies_yaml"] = (
            stamp.as_dict() if stamp is not None
            else never_loaded(
                "config/strategies.yaml",
                "this process has not loaded the strategy registry since boot",
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("loaded_config: registry stamp unavailable: %s", exc)
        block["strategies_yaml"] = never_loaded(
            "config/strategies.yaml", f"stamp unavailable: {type(exc).__name__}: {exc}",
        )
    try:
        from src.units.accounts import accounts_stamp
        stamp = accounts_stamp()
        block["accounts_yaml"] = (
            stamp.as_dict() if stamp is not None
            else never_loaded(
                "config/accounts.yaml",
                "this process has not built account objects since boot",
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("loaded_config: accounts stamp unavailable: %s", exc)
        block["accounts_yaml"] = never_loaded(
            "config/accounts.yaml", f"stamp unavailable: {type(exc).__name__}: {exc}",
        )
    return block


# ---------------------------------------------------------------------------
# Reader: what a surface outside the trading process may conclude.
# ---------------------------------------------------------------------------
def read_process_block(status_path: Path | str) -> tuple[str, Optional[Dict[str, Any]], str]:
    """``(process_state, block_or_None, why)`` from a runtime-status artifact.

    The three outcomes are the ``process_state`` contract, and the caller is
    expected to branch on all three — ``not_written`` and ``unreadable`` have
    different remedies (deploy the build / fix the artifact) and neither is
    "the trader holds nothing".
    """
    p = Path(status_path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return (PROCESS_UNREADABLE, None,
                f"{p} does not exist — we could not look at the process's state")
    except Exception as exc:  # noqa: BLE001
        return (PROCESS_UNREADABLE, None,
                f"{p} unreadable ({type(exc).__name__}: {exc}) — we could not look")
    if not isinstance(raw, dict):
        return (PROCESS_UNREADABLE, None, f"{p} is not a JSON object")
    block = raw.get("process")
    if not isinstance(block, dict):
        return (PROCESS_NOT_WRITTEN, None,
                "the status artifact was read and carries no `process` block — "
                "the running build predates E31, or has not ticked since boot. "
                "This is NOT `the trader holds no strategies`.")
    return (PROCESS_OBSERVED, block, "the trading process published its loaded state")


def reload_state(loaded_digest: Optional[str], on_disk: DigestReading) -> Dict[str, Any]:
    """Compare what the process LOADED against what is on disk NOW.

    Returns the ``reload_state`` contract plus both sides, so a reader can see
    why the verdict is what it is. ``unknown`` whenever either side is
    missing — never ``current`` by default.
    """
    if loaded_digest is None or on_disk.state != DIGEST_READ or on_disk.digest is None:
        if loaded_digest is None and on_disk.state == DIGEST_READ:
            why = ("the process has not loaded this file, so there is nothing "
                   "to compare the on-disk digest against")
        elif loaded_digest is not None:
            why = f"the file on disk could not be digested ({on_disk.state}): {on_disk.error}"
        else:
            why = "neither side could be established"
        return {
            "reload_state": RELOAD_UNKNOWN,
            "loaded_digest": loaded_digest,
            "on_disk_digest": on_disk.digest,
            "why": why,
        }
    if loaded_digest == on_disk.digest:
        return {
            "reload_state": RELOAD_CURRENT,
            "loaded_digest": loaded_digest,
            "on_disk_digest": on_disk.digest,
            "why": "the process loaded the bytes that are on disk now",
        }
    return {
        "reload_state": RELOAD_PENDING,
        "loaded_digest": loaded_digest,
        "on_disk_digest": on_disk.digest,
        "why": ("the file on disk has changed since this process loaded it — "
                "the merge reached the VM, the process has not taken it"),
    }
