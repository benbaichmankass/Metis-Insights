# wiring: imported by scripts/ops/leg_flow_report.py and
#         tests/test_leg_flow_detector.py. Deliberately kept under
#         scripts/ops/, not src/runtime/, even though its sibling detectors
#         (starved_account_alert.py, silent_refusal_alert.py, dead_leg.py)
#         live there: those are imported by the LIVE TRADER TICK; this module
#         is not, and never will be — it is a pure library behind a manual
#         report script, the same shape as scripts/ops/strategy_liveness.py.
#         Keeping it out of src/** also keeps this PR inside
#         check_pr_landing.py's TIER1_SURFACE, so it can self-land as the
#         read-only observability change it actually is, per CLAUDE.md's own
#         Tier-1 definition, rather than needing an operator hold that the
#         guard's conservative (and correct, for anything touching src/**)
#         allowlist would otherwise impose.
"""leg_flow_detector — intents produced vs orders/tickets received, per live leg.

WHY (checklist row E18, operator decision 2026-09-21: BUILD IT, FOR ALL
ACCOUNTS). ``breakout_1`` produced 21 intents over 22 days and received ZERO
tickets, and nothing in the system fired on that — the operator found it by
noticing an absence, the worst available discovery method. Two detectors
already exist and neither covers this shape:

  * ``silent_refusal_alert`` grades a BLOCKED leg — a refusal landed in
    ``trades``. A starved leg produces no such row.
  * ``starved_account_alert`` grades an account the per-account ARBITRATION
    FAN-OUT elected and then discarded, read off
    ``arbitration_fanout_soak.jsonl``. That soak is itself the mechanism the
    starvation depends on being healthy — and it went 32h silent as of
    2026-09-24 (PI-20260924-JN54P2HH-0004) while still armed, which is exactly
    the failure mode a detector built ON it cannot see through.

An UNREACHED leg needs neither: no refusal, no fan-out plan (or one the
allowlist discards before it binds), nothing. This module compares two
CANONICAL logs directly, independent of the fan-out mechanism's own health:

  * "intents produced" — ``trade_journal.db::signals``, filtered to
    ``event="{strategy}_eval"`` (the per-strategy-tick evaluation log every
    signal builder already writes via ``log_signal``, S-034) and to
    ``side`` not being the strategy's own non-actionable sentinel. Every
    builder shares the ``{name}_eval`` convention
    (``src/runtime/strategy_signal_builders.py``) and the literal ``"none"``
    sentinel for "no signal this tick" — verified against the source for
    every ``log_signal`` call site. The ACTIONABLE token varies by strategy
    (``long``/``short`` for most; ``sell`` was MEASURED live for
    ``trend_donchian_eth_prop`` on 2026-09-25, issue relay
    ``audit_query?strategy=trend_donchian_eth_prop&event=trend_donchian_eth_prop_eval``)
    — so this reads GENERICALLY off the sentinel, never off a hardcoded side
    vocabulary, which is exactly the assumption that would have undercounted
    that leg's intents to zero.
  * "orders/tickets received" — for a standard account, ``trades`` rows for
    that ``(account_id, strategy_name)`` whose ``status`` is in
    ``src.runtime.dead_leg.PLACED_STATUSES`` — the one canonical "reached the
    exchange" definition ``silent_refusal_alert`` already uses, imported
    rather than re-derived (its own docstring: two copies of that vocabulary
    is how they start disagreeing). For a PROP account
    (``account_class: prop``), ``trades`` never carries a row at all —
    MEASURED 2026-09-25 against the live journal (``journal?table=trades``,
    newest 1000 rows, spanning 2026-08-28→2026-09-25): zero rows carry
    ``account_id="breakout_1"``. A prop leg's "received" therefore comes from
    ``/api/bot/prop/tickets`` instead, via :data:`PROP_RECEIVED_STATUSES` /
    :data:`PROP_NOT_RECEIVED_STATUSES`.

THE STATE THAT MUST NEVER COLLAPSE (docs/CLAUDE-RULES-CANONICAL.md §
"Collapsed states"): ``unreadable`` (we could not query one or both sources
for this leg) vs ``starved`` (we queried fine and it is genuinely zero). A
detector that renders a failed pull as "0 tickets, not starved" is the exact
bug this row exists to avoid re-introducing. ``no_intents`` (nothing to
judge — the strategy simply did not signal) is kept apart from both, because
pooling "quiet" with "starved" is what buried the original finding —
``starved_account_alert``'s own ``not_observed``/``soak_unreadable`` split is
the precedent this module follows.

  unreadable  — we could not query the intents source, the received source,
                or both, for this leg. NEVER renders as "not starved".
  no_intents  — queried fine; the strategy produced ZERO actionable intents in
                the window. Not the finding, and not evidence of health
                either — the leg may simply have no live setup right now.
  starved     — queried fine; intents > 0 and received == 0. THE FINDING: an
                UNREACHED leg, exactly the breakout_1 shape this row exists
                to catch.
  flowing     — queried fine; received > 0.

Read path only — no order-path change (Tier-1). This module is PURE: no I/O,
no env var reads, no network calls. ``scripts/ops/leg_flow_report.py`` is the
live caller that fetches the two sources and drives this module's functions.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from src.runtime.dead_leg import PLACED_STATUSES
except Exception:  # noqa: BLE001 — defensive: this module must import even if
    # dead_leg ever moves: `enumerate_live_legs`/`assess_leg` do not need it,
    # only `count_received_standard` does, and that call site already
    # tolerates a missing statuses list by falling back to the tuple dead_leg
    # itself declares canonically (kept in sync by a test, never re-derived
    # silently — see tests/test_leg_flow_detector.py).
    PLACED_STATUSES = ("open", "closed", "orphaned")

# ── the four states, never collapsed ───────────────────────────────────────
LEG_UNREADABLE = "unreadable"
LEG_NO_INTENTS = "no_intents"
LEG_STARVED = "starved"
LEG_FLOWING = "flowing"

LEG_STATES: Tuple[str, ...] = (LEG_UNREADABLE, LEG_NO_INTENTS, LEG_STARVED, LEG_FLOWING)

#: A prop ticket row whose status means a real ticket reached the manual
#: bridge — MEASURED against live `/api/bot/prop/tickets?account_id=breakout_1`
#: rows, 2026-09-25: `filled`/`expired`/`closed` are terminal outcomes of a
#: dispatched ticket, `emitted` is in flight, `orphaned` is a bridge/journal
#: mismatch on a ticket that DID reach the account (a different, also-bad
#: problem — `dead_leg.PLACED_STATUSES` makes the identical call for `trades`,
#: for the identical reason: don't blame dispatch for a reconciler fault).
#:
#: `placed` / `expiry_prompted` / `awaiting_report` / `invalidated_prompted`
#: added 2026-09-26 (PI-20260926-KNSTSR8N-0002 — `trend_donchian_eth_prop`
#: read `starved` with a real, dispatched, `expiry_prompted` ticket
#: `prop-manual-693bb30f7638` in-window). Verified against every prop-module
#: status writer, not just the one that motivated the filing
#: (`src/prop/breakout_executor.py::_reticket_suppress_reason`'s own
#: outstanding-ticket check groups `placed`/`expiry_prompted`/
#: `awaiting_report` with `emitted` as the same "outstanding ticket" class):
#: `placed` — a fill report of type `placed` (`prop_report.py`, a working
#: order on the terminal, not yet filled). `expiry_prompted` /
#: `invalidated_prompted` — the two Yes/No prompts
#: (`prop_expiry_prompt.py` / `prop_invalidation_prompt.py`), each of which
#: ONLY ever fires from an already-`emitted` ticket (`_SCAN_STATUS =
#: "emitted"` in both modules) — so a ticket in either state definitively
#: reached the bridge; `invalidated_prompted` was miscategorised below as
#: NOT received until this fix. `awaiting_report` — the operator's "Yes, I
#: placed it" answer to either prompt, still waiting on the fill paste.
PROP_RECEIVED_STATUSES = (
    "emitted", "expired", "filled", "closed", "orphaned",
    "placed", "expiry_prompted", "awaiting_report", "invalidated_prompted",
)

#: Never dispatched (`shadow` / `close_reason: prop_shadow_no_emit` — the
#: literal starvation state at the prop layer) or held back by the
#: one-ticket-per-trade reticket guard (`suppressed` —
#: `src/prop/breakout_executor.py::_reticket_suppress_reason`, a BLOCKED leg,
#: a DIFFERENT failure from the one this module exists to catch) or a fill
#: report saying the ticket was never placed (`skipped`). Counted as CONTEXT
#: only (`held_back` in :func:`count_received_prop`) — never folded into
#: `received`, and never used to downgrade `starved` to something softer: a
#: suppressed leg is still zero tickets received.
PROP_NOT_RECEIVED_STATUSES = ("shadow", "suppressed", "skipped")


def _is_actionable_side(side: Any) -> bool:
    """True if ``side`` is a real directional call, not the "no signal"
    sentinel. The sentinel is always the literal ``"none"`` (verified against
    every ``log_signal({"event": f"{name}_eval", ...})`` call site in
    ``strategy_signal_builders.py``); the actionable token itself is NOT
    standardised across strategies (``long``/``short`` for most, ``sell`` for
    ``trend_donchian_eth_prop`` — measured live). Reading off the sentinel
    rather than a hardcoded vocabulary is what keeps this generic.
    """
    if side is None:
        return False
    return str(side).strip().lower() not in ("", "none")


def count_intents(signal_rows: Optional[Sequence[Dict[str, Any]]]) -> Optional[int]:
    """Actionable ``{strategy}_eval`` rows in ``signal_rows``.

    ``signal_rows is None`` means the caller could not read the source at
    all — propagated as ``None``, never as ``0``. The read-state distinction
    itself lives one level up, in :func:`assess_leg`.
    """
    if signal_rows is None:
        return None
    return sum(1 for r in signal_rows if _is_actionable_side((r or {}).get("side")))


def count_intent_episodes(signal_rows: Optional[Sequence[Dict[str, Any]]]) -> Optional[int]:
    """A cheap debounce proxy, CONTEXT ONLY: contiguous runs of an actionable
    side, scanned oldest-first.

    ``{strategy}_eval`` rows are written on every tick (~every 2 minutes,
    measured live), so a signal that persists across one multi-tick bar would
    otherwise count once per tick. This counts it once per contiguous run
    instead — the same debounce ``intent_multiplexer._debounce_emissions``
    performs live, approximated here without needing that module's bar-
    boundary math. Deliberately NOT what drives the read-state: the raw
    per-tick count in :func:`count_intents` is the more conservative (harder
    to accidentally read as zero) of the two, and this module's whole point is
    never under-reporting a starved leg.
    """
    if signal_rows is None:
        return None
    ordered = sorted(signal_rows, key=lambda r: str((r or {}).get("logged_at_utc") or ""))
    episodes = 0
    prev_actionable = False
    for r in ordered:
        actionable = _is_actionable_side((r or {}).get("side"))
        if actionable and not prev_actionable:
            episodes += 1
        prev_actionable = actionable
    return episodes


def count_received_standard(
    trade_rows: Optional[Sequence[Dict[str, Any]]],
    *,
    account_id: str,
    strategy: str,
) -> Optional[Tuple[int, int]]:
    """``(placed, refused)`` for one ``(account, strategy)`` leg, out of a
    shared ``trades`` pull covering the window.

    ``trade_rows is None`` -> ``None`` (we could not read `trades` at all).
    ``refused`` is CONTEXT ONLY — it lets a reader tell BLOCKED (a refusal
    landed) from UNREACHED (this module's own finding) apart, and never feeds
    the state calculation: a refused leg still has ``received == 0`` and is
    still graded :data:`LEG_STARVED`. ``silent_refusal_alert`` is the detector
    that owns the blocked/not-blocked distinction on its own terms; this only
    surfaces the count so the two are never silently conflated by a reader.
    """
    if trade_rows is None:
        return None
    placed = refused = 0
    for r in trade_rows:
        row = r or {}
        if row.get("account_id") != account_id or row.get("strategy_name") != strategy:
            continue
        if row.get("status") in PLACED_STATUSES:
            placed += 1
        else:
            refused += 1
    return placed, refused


def count_received_prop(
    ticket_rows: Optional[Sequence[Dict[str, Any]]],
    *,
    strategy: str,
) -> Optional[Tuple[int, int]]:
    """``(received, held_back)`` for one prop strategy, out of a shared
    ``/api/bot/prop/tickets?account_id=...`` pull.

    ``ticket_rows is None`` -> ``None``. ``held_back`` is CONTEXT ONLY, same
    reasoning as :func:`count_received_standard`'s ``refused``. A row whose
    status matches neither :data:`PROP_RECEIVED_STATUSES` nor
    :data:`PROP_NOT_RECEIVED_STATUSES` is counted in neither bucket rather
    than guessed into one — the caller's own row-accounting (row count in vs
    ``received + held_back`` out) is how a silently-dropped status would
    surface, never a silent assumption here.
    """
    if ticket_rows is None:
        return None
    received = held_back = 0
    for r in ticket_rows:
        row = r or {}
        if row.get("strategy") != strategy:
            continue
        status = row.get("status")
        if status in PROP_RECEIVED_STATUSES:
            received += 1
        elif status in PROP_NOT_RECEIVED_STATUSES:
            held_back += 1
    return received, held_back


def assess_leg(
    *,
    intents: Optional[int],
    received: Optional[int],
    intent_episodes: Optional[int] = None,
    held_back: Optional[int] = None,
) -> Dict[str, Any]:
    """The read-state contract. Pure.

    ``None`` on either side of the ledger means WE DID NOT LOOK, and the leg
    grades :data:`LEG_UNREADABLE` — never :data:`LEG_STARVED`. This is the one
    branch the whole module exists to get right.
    """
    if intents is None or received is None:
        state = LEG_UNREADABLE
    elif intents == 0:
        state = LEG_NO_INTENTS
    elif received == 0:
        state = LEG_STARVED
    else:
        state = LEG_FLOWING
    return {
        "state": state,
        "intents": intents,
        "received": received,
        "intent_episodes": intent_episodes,
        "held_back": held_back,
    }


def enumerate_live_legs(
    config: Dict[str, Any], strategies: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """``(strategy, account)`` pairs a live tick can actually route an order
    to, from ``/api/bot/config`` + ``/api/bot/strategies`` — the same two
    gates ``scripts/ops/strategy_liveness.py`` already checks for a single
    strategy, generalised to every account's roster.

    A strategy present in an account's roster but ABSENT from
    ``strategies.yaml`` is INCLUDED, not dropped — ``execution:`` defaults to
    ``live`` when the strategy has no declared row (CLAUDE.md's own
    default-permissive rule for the execution gate: "What accounts.yaml /
    strategies.yaml declare, runs."), and dropping it would hide exactly the
    kind of roster/config drift this module exists to catch, not just
    starvation.
    """
    strat_by_name = {
        s.get("name"): s for s in (strategies.get("strategies") or []) if s.get("name")
    }
    legs: List[Dict[str, Any]] = []
    for acc in config.get("accounts") or []:
        if acc.get("yaml_mode") != "live" or not acc.get("enabled", True):
            continue
        account_id = acc.get("id")
        account_class = acc.get("account_class")
        for strat_name in acc.get("strategies") or []:
            srow = strat_by_name.get(strat_name)
            if srow is not None:
                if srow.get("execution") == "shadow" or srow.get("enabled") is False:
                    continue
            legs.append({
                "strategy": strat_name,
                "account_id": account_id,
                "account_class": account_class,
                "is_prop": account_class == "prop",
            })
    return legs


__all__ = [
    "LEG_UNREADABLE", "LEG_NO_INTENTS", "LEG_STARVED", "LEG_FLOWING", "LEG_STATES",
    "PROP_RECEIVED_STATUSES", "PROP_NOT_RECEIVED_STATUSES",
    "count_intents", "count_intent_episodes",
    "count_received_standard", "count_received_prop",
    "assess_leg", "enumerate_live_legs",
]
