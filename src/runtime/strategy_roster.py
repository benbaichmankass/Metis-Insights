"""Three-state resolution of the strategy roster (E21).

``config/strategies.yaml`` is the roster. Reading it can fail — a syntax
error landing on ``main`` reaches the live trader inside ~5 minutes via
``ict-git-sync``, and ``pyyaml`` is optional on the VM image. Until
2026-09-22 that failure resolved to a hardcoded **value**::

    return ["turtle_soup", "vwap"]   # src/runtime/pipeline.py

with a comment claiming the pair "matches the production roster". MEASURED
2026-09-22 by parsing ``config/strategies.yaml`` (55 declared) and
``config/accounts.yaml`` (11 accounts) at commit ``f3746ab``: **52 distinct
strategies are routed to at least one ``mode: live`` account, and the
fallback covers 0 of them** — ``turtle_soup`` is ``execution: shadow`` and
``vwap`` is ``enabled: false`` **and** ``execution: shadow``, so neither is
routed anywhere live. The failure was a single ``logger.warning`` at import.

⚠️ **The bug is the VALUE, not the fallback.** A wrong roster is worse than
no roster, because every downstream consumer reads a list of names and has
no way to ask whether that list is the roster or a guess. So this module
returns a **state** alongside the names and callers branch on it:

| state | meaning |
|---|---|
| ``ok`` | the registry was read and declares these names |
| ``empty`` | the registry was read and declares **nothing** |
| ``unreadable`` | we **could not look** — the names are ``()`` because we have none, not because there are none |

``empty`` and ``unreadable`` are the pair that must never collapse
(docs/CLAUDE-RULES-CANONICAL.md § "Collapsed states"): *"the roster declares
no strategies"* is a real and alarming measurement of the world; *"we could
not read the roster"* says nothing about the world at all, and folding the
second into the first is how a trader ends up confidently running a roster
it never read.

**What each side of the trader does with `unreadable` is deliberately
different, and that asymmetry is the point (E21, operator-approved
2026-09-22):**

* The **entry** side has no roster, so it opens nothing. That is not the
  trader switching itself off (Prime Directive § 2): no account ``mode:`` is
  written, the process keeps ticking, and the condition is surfaced per-tick
  and on the health snapshot rather than latched. It is the same shape as
  ``pipeline.strategy_builders()``, which is deliberately un-wrapped because
  *"a rollback that cannot resolve its roster must fail loudly rather than
  resolve to an empty dict and silently trade nothing"*.
* The **exit** side does not consult this state to decide WHAT to monitor at
  all — see ``order_monitor.exit_population()``. An open package is monitored
  because it is OPEN, never because its name appears in a list. A roster we
  cannot read must not strand a real-money position.

Stdlib + an optional ``yaml`` (imported inside ``src.strategy_registry``, and
only on the call) so ``src/runtime/health.py`` can import this module without
taking on the pipeline's pandas/ccxt chain.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# The three states. Do not collapse `ROSTER_EMPTY` into `ROSTER_UNREADABLE`.
ROSTER_OK = "ok"
ROSTER_EMPTY = "empty"
ROSTER_UNREADABLE = "unreadable"

ROSTER_STATES: Tuple[str, str, str] = (ROSTER_OK, ROSTER_EMPTY, ROSTER_UNREADABLE)


@dataclass(frozen=True)
class RosterResolution:
    """The roster, and whether we actually read it.

    ``names`` is empty for BOTH ``empty`` and ``unreadable`` — which is
    exactly why ``state`` exists and why no caller may infer the condition
    from ``len(names)``.
    """

    state: str
    names: Tuple[str, ...] = ()
    error: Optional[str] = None

    @property
    def readable(self) -> bool:
        """True when the registry was READ (``ok`` or ``empty``).

        Deliberately not named ``ok``: an ``empty`` roster is readable and
        alarming, and a caller that wants "are there names" asks ``names``.
        """
        return self.state != ROSTER_UNREADABLE

    def describe(self) -> str:
        if self.state == ROSTER_UNREADABLE:
            return f"strategies.yaml unreadable: {self.error}"
        if self.state == ROSTER_EMPTY:
            return "strategies.yaml read and declares 0 strategies"
        return f"strategies.yaml read: {len(self.names)} strategies"


def resolve_roster(path: Optional[str] = None, *, fresh: bool = False) -> RosterResolution:
    """Read the registry and say which of the three states we are in.

    Never raises — a roster read that crashed the import would take the whole
    trader down, and the Prime Directive requires it to come up (§ 5, "Boot
    always starts the trader live").

    ``fresh=True`` bypasses ``strategy_registry``'s cache. A long-lived reader
    asking *"is the roster readable RIGHT NOW"* must pass it, or it will keep
    reporting the last good read forever — which would make the health check
    that exists to catch an unreadable roster structurally unable to fire.
    """
    try:
        from src.strategy_registry import load_strategies, reload_strategies
        if fresh:
            rows = reload_strategies(path) if path else reload_strategies()
        else:
            rows = load_strategies(path) if path else load_strategies()
        names = tuple(str(s["name"]) for s in rows)
    except Exception as exc:  # noqa: BLE001
        # ERROR, not warning: one `logger.warning` is what let the hardcoded
        # two-name fallback sit unnoticed. The health snapshot's
        # `strategy_roster` check is the surface that goes red on this.
        logger.error(
            "strategy_roster: registry UNREADABLE — no roster resolved, "
            "entry side will open nothing until this clears: %s", exc,
        )
        return RosterResolution(state=ROSTER_UNREADABLE, names=(), error=str(exc))
    if not names:
        logger.error(
            "strategy_roster: registry READ and declares ZERO strategies — "
            "this is a measurement of the config, not a read failure",
        )
        return RosterResolution(state=ROSTER_EMPTY, names=())
    return RosterResolution(state=ROSTER_OK, names=names)
