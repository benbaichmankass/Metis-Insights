"""ONE owner for what a Claude-channel ping LOOKS like and WHETHER it is sent.

Operator, 2026-09-01: "we need to start figuring out which pings and how they're
formatted." This module is that decision, in one place.

WHY IT IS A MODULE AND NOT A CONVENTION. ``send_ping.enqueue`` takes a free-text
``body``, so every producer invents its own shape — four producers reach the
Claude channel today and no two format alike. A vocabulary with no owner drifts;
this repo watched that happen THE SAME DAY to "is the WIP ceiling enforced",
which lived in two files and disagreed with itself on a deployed page for ~20
minutes (src/utils/work_facts.py exists for that reason). Same move, same reason.

THE FORMAT — "B", chosen by the operator from three drafted against real events:

    ✅ MERGED #10666 · alpaca extended-hours close · Tier-2 · deployed ab6985b3
       Close-fail pages drop ~160 → 7 per wedged session. Not yet proven live.

Line 1 is WHAT, scannable on a phone. Line 2 is WHAT CHANGED FOR YOU. The
one-line variant was rejected as too thin cold — "MERGED #10666" a day later
tells the reader nothing without opening a link they will not open on a phone.

⚠️ ``unproven`` IS A FIRST-CLASS FIELD, not a nicety. Today produced a
recurrence of a bug marked RESOLVED on exactly this distinction: a fix shipped
for one call site, the row was closed, and the sibling path stayed broken for
seven weeks (BL-20260901-ALPACA-EXT-HOURS-QTY-AVAILABLE). A ping that says
"deployed" without saying "not yet observed working" invites the same close.

THREE CLASSES, and they are gated differently on purpose
--------------------------------------------------------
``DECISION``      something needs the operator, or a choice was recorded.
                  ⚠️ NEVER rate-limited and NEVER gated off. Suppressing a
                  "this needs your approval" is the desensitized-alarm failure
                  INVERTED — silence exactly when action is required.
``STATE_CHANGE``  the world moved: merged, deployed, incident fixed, verdict
                  written, ceiling hit. Rate-limited per class.
``LIFECYCLE``     a session/sub-session started or ended, the lease changed
                  hands. ⚠️ This IS activity, not a change in the world, and
                  it is the category most likely to train a reader to skim. The
                  operator asked for it while the operating layer is being stood
                  up; it therefore sits behind ITS OWN switch
                  (CLAUDE_PING_LIFECYCLE) so turning it off later is one flag,
                  not a refactor.

⚠️ THE RATE LIMIT IS DURABLE, NOT PER-PROCESS. A module-global counter resets on
every restart, which is how one un-latched alarm put 202 of 376 CRITICALs on the
operator's channel in a single window
(BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART). State lives on disk
beside the other alert latches.

⚠️ AN UNREADABLE LIMITER SENDS. Failing loud is the only safe direction on a
notification path: a broken limiter that suppressed would be indistinguishable
from a quiet day, whereas one that over-sends announces itself.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Final, Optional

from src.utils.paths import runtime_logs_dir

DECISION: Final = "decision"
STATE_CHANGE: Final = "state_change"
LIFECYCLE: Final = "lifecycle"

CLASSES: Final[tuple[str, ...]] = (DECISION, STATE_CHANGE, LIFECYCLE)

#: Per-class minimum seconds between pings. DECISION is deliberately absent —
#: see the module docstring; a decision is never held back.
_MIN_INTERVAL_S: Final[dict[str, float]] = {
    STATE_CHANGE: 0.0,      # each is a real change; the CLASS is the filter
    LIFECYCLE: 300.0,       # a fan-out of six sub-sessions is one line, not six
}

_STATE: Final = "claude_ping_state.json"


def _state_path() -> Path:
    return Path(runtime_logs_dir()) / _STATE


def lifecycle_enabled() -> bool:
    """LIFECYCLE's own switch. Default ON — the operator asked for it while the
    operating layer is being stood up — and one flag to turn off."""
    raw = (os.environ.get("CLAUDE_PING_LIFECYCLE") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def format_ping(headline: str, why: str, *, unproven: str | None = None,
                icon: str = "•") -> str:
    """Render Format B: one line of WHAT, one line of WHAT CHANGED.

    ``unproven`` is appended to the why-line rather than given its own line —
    it belongs to the same thought ("this shipped, and here is what that does
    NOT yet mean"), and a third line starts to read like the structured format
    the operator rejected as too heavy for a phone.
    """
    head = " ".join(str(headline or "").split())
    body = " ".join(str(why or "").split())
    if not head:
        raise ValueError("headline must be non-empty — a ping with no WHAT is noise")
    if not body:
        raise ValueError(
            "why must be non-empty — Format B's whole point is the second line; "
            "if there is nothing to say about what changed, the event is "
            "probably activity and should not ping at all"
        )
    if unproven:
        body = f"{body} {' '.join(str(unproven).split())}"
    return f"{icon} {head}\n   {body}"


def _read_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Unreadable → send. See the module docstring: on a notification path
        # the only safe failure is the loud one.
        return {}


def _write_state(state: dict) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass  # a limiter that cannot persist must not block the ping


def admits(cls: str, *, now: Optional[float] = None) -> tuple[bool, str]:
    """May a ping of *cls* be sent right now? Returns (admit, reason).

    The reason is returned rather than logged so a caller can record WHY a ping
    was withheld — "we suppressed it" and "there was nothing to say" are
    different facts and a bare False cannot tell them apart.
    """
    if cls not in CLASSES:
        raise ValueError(f"unknown ping class {cls!r}; must be one of {CLASSES}")
    if cls == DECISION:
        return True, "decision — never rate-limited"
    if cls == LIFECYCLE and not lifecycle_enabled():
        return False, "lifecycle pings disabled (CLAUDE_PING_LIFECYCLE)"

    interval = _MIN_INTERVAL_S.get(cls, 0.0)
    if interval <= 0:
        return True, "no minimum interval for this class"

    now = time.time() if now is None else now
    state = _read_state()
    last = state.get(cls, {}).get("last_sent_at")
    if not isinstance(last, (int, float)):
        return True, "no prior send recorded"
    waited = now - float(last)
    if waited >= interval:
        return True, f"{waited:.0f}s since last (>= {interval:.0f}s)"
    return False, f"rate-limited: {waited:.0f}s since last (< {interval:.0f}s)"


def record_sent(cls: str, *, now: Optional[float] = None) -> None:
    """Record that a ping of *cls* went out. Call only on a CONFIRMED send —
    recording an attempt would let a failed send suppress the retry."""
    if cls not in CLASSES:
        raise ValueError(f"unknown ping class {cls!r}")
    state = _read_state()
    state.setdefault(cls, {})["last_sent_at"] = time.time() if now is None else now
    _write_state(state)
