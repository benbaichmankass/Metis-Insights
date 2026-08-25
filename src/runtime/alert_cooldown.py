"""The durable alert cooldown — ONE implementation, shared by every caller.

Rate-limits a repeating operator page to one per window per key, on WALL CLOCK
and in a file, so it survives the process. Both halves are load-bearing.

WHY IT IS ITS OWN MODULE (2026-08-25). It was written for `target_naked`, then
generalised in place for `stop_over_cover`, and a THIRD caller now needs it —
``src/runtime/pipeline.py``'s per-strategy builder-exception page. Copying a
latch is precisely how the defect it was fixed for comes back in the copy:
``target_naked``'s first implementation keyed a module global on
``time.monotonic()`` — both per-PROCESS — against a condition that outlives any
process, and since the trader restarts on every merge to ``main`` the cooldown
re-armed constantly and the alert became **202 of 376 rows, 53.7% of the whole
operator ERROR+/CRITICAL feed** (BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART
— kept on one line so the id stays greppable). Living in ``order_monitor``, a
module the trading loop has no business importing, made copying the path of
least resistance. It does not live there now.

**Per-process latching is not always wrong**, and this module is not the answer
to every rate limit: ``exit_loop_health`` alerts once per PROCESS, correctly,
because ``max_interval_ms`` IS a per-process measurement. Use this one when the
CONDITION outlives the process — a resting bracket, a missing target, a
strategy that cannot fetch its bars.

**An unreadable latch ALERTS rather than suppressing.** Failing loud is the
only safe direction on a safety page, and it makes a permanently-broken latch
announce itself as spam instead of as silence. A caller for whom the flood
itself is the harm inherits that behaviour deliberately rather than getting a
quieter variant — one behaviour, one module, no drift.

``path_resolver`` exists so a caller can keep its own module-level path
function (and the tests that patch it) while sharing this logic. It is a seam
for wiring, never for a second definition of the file layout.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger(__name__)

# Entries older than this are dropped on write so the file cannot grow without
# bound; well past any caller's cooldown, so pruning can never un-suppress a
# live one.
DEFAULT_STATE_TTL_S: float = 7 * 24 * 3600.0

def state_path(kind: str, path_resolver: Optional[Callable[[str], Any]] = None):
    """``runtime_logs/<kind>_alert_state.json``, or whatever *path_resolver* says.

    The filename shape reproduces ``target_naked_alert_state.json``
    byte-for-byte for the original caller. That is deliberate and load-bearing:
    renaming it would orphan the LIVE latch file on the trader and silently
    re-arm a cooldown that is currently suppressing a CRITICAL.
    """
    if path_resolver is not None:
        return path_resolver(kind)
    from src.utils.paths import runtime_logs_dir

    return runtime_logs_dir() / f"{kind}_alert_state.json"


def load_state(kind: str, path_resolver: Optional[Callable[[str], Any]] = None) -> Tuple[dict, bool]:
    """Return ``(state, readable)`` for a durable alert cooldown.

    ``readable`` is the *"did we look?"* axis and is deliberately NOT collapsed
    into an empty dict: "the latch has never fired" and "we could not read the
    latch" are both ``{}`` and must not be treated the same way. The caller
    ALERTS when ``readable`` is False — suppressing a CRITICAL safety page
    because a file read failed is the wrong direction to fail, and a
    permanently unreadable latch then announces itself as spam instead of as
    silence.
    """
    p = state_path(kind, path_resolver)
    try:
        if not p.exists():
            return {}, True  # we looked; nothing has fired yet
        data = json.loads(p.read_text(encoding="utf-8"))
        return (data if isinstance(data, dict) else {}), True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "%s cooldown state unreadable (%s) - alerting rather "
            "than suppressing",
            kind, exc,
        )
        return {}, False


def save_state(kind: str, state: dict, path_resolver: Optional[Callable[[str], Any]] = None) -> None:
    try:
        p = state_path(kind, path_resolver)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s cooldown state save failed: %s", kind, exc)


SEV_SUFFIX = "|sev="


def cooldown_admits(kind: str, key: str, cooldown_s: float,
                    ttl_s: float = DEFAULT_STATE_TTL_S,
                    severity: Optional[int] = None,
                    path_resolver: Optional[Callable[[str], Any]] = None) -> bool:
    """Should ``key`` alert now under ``kind``'s durable cooldown?

    Returns True AND commits the new timestamp when the alert may fire.
    WALL CLOCK, never ``time.monotonic()``: monotonic is meaningless across
    processes, and every condition this rate-limits persists across restarts.

    **``severity`` lets a WORSENING break the cooldown**
    (BL-20260825-OVER-COVER-LATCH-CANNOT-SEE-A-WORSENING-CONDITION). Without
    it, a key that has fired is silent for the whole window however much worse
    the condition gets — measured live 2026-08-25, the over-cover page fired at
    12:27:44Z for ib_paper/MHG at 2 disjoint OCA groups / 200% and said nothing
    when the SAME symbol reached 3 groups / 300% two hours later. "X is
    over-covered" and "X is over-covered by half again as much" are different
    facts, and the second is the one saying the condition is being PRODUCED
    rather than merely standing.

    The rule is deliberately **one-directional**: a strictly HIGHER severity
    than any live latch for the same ``key`` pages; an equal or LOWER one is
    suppressed as usual. Both halves matter and neither is decoration —

    * pages on worse, so a growing condition is never silent;
    * silent on better, so a condition that is *improving* cannot generate
      CRITICALs. That is not hypothetical for the target-naked sibling, whose
      coverage moves in both directions: a position going from no target to a
      partial one is an improvement, and paging on it is exactly the
      desensitized-alarm P1 the cooldown exists to prevent. That asymmetry is
      why the two callers share this primitive instead of each inventing a key
      scheme, and why over-cover's group count could NOT simply be pasted into
      the key.

    An UNCHANGED severity keeps today's volume exactly: one page per window.

    ``severity`` rides in the stored KEY rather than the stored VALUE so the
    on-disk shape stays ``{key: float_timestamp}``. That is load-bearing: the
    live trader holds ``runtime_logs/target_naked_alert_state.json`` right now,
    the TTL prune filters on the value being a number, and the durable-cooldown
    tests bind that shape — changing it would orphan a latch that is currently
    suppressing a CRITICAL.
    """
    now = time.time()
    state, readable = load_state(kind, path_resolver)
    stored_key = key if severity is None else f"{key}{SEV_SUFFIX}{int(severity)}"
    if readable:
        # Every live latch for this key, across severities.
        live_sevs = []
        suppress = False
        for k, v in state.items():
            if k != key and not k.startswith(f"{key}{SEV_SUFFIX}"):
                continue
            try:
                last = float(v)
            except (TypeError, ValueError):
                continue
            # A FUTURE-dated entry (clock skew, a restored file) yields a
            # negative delta and must not suppress forever, so the window is
            # bounded below by 0 as well as above by the cooldown.
            if not (0.0 <= (now - last) < cooldown_s):
                continue
            if severity is None:
                if k == key:
                    suppress = True
                continue
            if k == key:
                # A pre-severity entry from an older build. It says the
                # condition alerted recently and nothing about how bad it was,
                # so it suppresses — "we do not know it got worse" must not
                # become "it got worse".
                suppress = True
                continue
            try:
                live_sevs.append(int(k.rsplit(SEV_SUFFIX, 1)[1]))
            except (IndexError, ValueError):
                continue
        if suppress:
            return False
        if severity is not None and live_sevs and max(live_sevs) >= int(severity):
            return False
    state = {
        k: v
        for k, v in state.items()
        if isinstance(v, (int, float)) and (now - float(v)) < ttl_s
    }
    state[stored_key] = now
    save_state(kind, state, path_resolver)
    return True


