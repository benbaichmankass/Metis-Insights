"""Prop account-status request — ask the operator for a fresh snapshot when the
rule-distance guard is flying blind.

The prop account is a **manual bridge**: the bot only knows the account's
balance/equity when the operator reports it back (``prop_report.ingest_report``
with ``kind:"account_status"``). The rule-distance safety panel (distance to
the daily-loss limit and the static-DD floor, ``prop_reconcile.compute_rule_distance``)
is computed from the LATEST ``prop_account_status`` row — with no row, or a
stale one, it can never warn before an account-killer is breached.

This module closes the loop from the bot's side: whenever the latest
account-status snapshot for a declared prop account is absent or older than
``PROP_STATUS_REQUEST_MAX_AGE_HOURS``, it pings the operator on the prop bot
with a **paste-ready reply template** (both accepted formats — the ``bal``
one-liner and the JSON block — exactly what ``telegram_report_handler``
parses), then waits ``PROP_STATUS_REQUEST_COOLDOWN_HOURS`` before asking again.

**THE TRIGGER IS ACTIVITY, NOT THE WALL CLOCK** (operator-directed 2026-09-09).
Until this change the ask fired on the **age of the last snapshot and nothing
else**, so an account nothing had touched was indistinguishable from one that
moved and was never reported. The operator was pinged about ``breakout_1`` with
**no open position** and a **230h** snapshot and said, correctly:

    "This ping in the prop channel is unessacry, the account snapshot is
    updated when a trade closes, if there are no prop trades than the account
    state didn't change"

Pinging about the first trains the operator to ignore the second, which is the
desensitized-alarm P1 this repo has an explicit rule about.

⚠️ **THE OPERATOR'S PREMISE IS HALF FALSE, AND THE HALF THAT IS FALSE DOES NOT
CHANGE THE ANSWER — say which, rather than inheriting the whole sentence.**
A close produces a **fill** report; a snapshot lands only if the operator also
reports numbers, so a close *prompts* a snapshot and never guarantees one.
MEASURED on the live journal 2026-09-09T09:54Z (``/api/bot/prop/fills?limit=500``
and ``/api/bot/prop/status``, both read direct over the Caddy host): the newest
snapshot for ``breakout_1`` is ``id 19`` at ``2026-08-30T19:33:29Z`` and the
newest fill is ``id 41`` at ``2026-08-30T19:39:00Z`` — the last close is **5.5
minutes NEWER than the snapshot it supposedly produced**. So "a close updates
the snapshot" is **false as a mechanism**. The operator's *conclusion* is
nonetheless right, for a different and stronger reason: **zero** fills in the
230h since, **zero** open positions, **zero** unacted tickets.

**WHY A REPORTED FILL IS NOT A REASON TO ASK — this is the load-bearing bit.**
:func:`src.prop.prop_reconcile.reconstruct_equity` is deliberately **asymmetric**:
an unplaceable **loss** is always applied, an unplaceable **gain** is withheld
and counted (``fills_withheld_unplaceable_gain``). So a reported fill can only
ever make the cushion **conservative**, never optimistic — the dangerous
direction is closed by construction. Fill ``id 41`` above is exactly that case
(a ``+35.28`` withheld gain: the panel shows an ``$87.34`` cushion to the
``$4,700`` floor where the true one is ``~$122.62``). Asking about it buys
precision, not safety.

**THE PRE-2026-08-14 FLAT BAIL IS NOT RESTORED, AND MUST NOT BE.** This module
used to ``if not positions: return []`` and prune its cadence state to accounts
holding an open position — so the moment the book went flat the bot stopped
asking entirely and the snapshot aged without bound. That is wrong for the guard
it feeds, because the two prop limits are not both position-scoped:

* the **daily-loss limit** is a per-day drawdown on the account;
* the **static DD floor** (``config/prop_rulesets/breakout.yaml``:
  ``drawdown_type: static``, ``$4,700`` on the ``$5,000`` account) is an
  **account-level** line that binds while the account is FLAT.

The 2026-08-14 concern was precisely *"flat is exactly when the next ticket is
sized against a cushion nobody has measured."* That concern is **preserved and
now OBSERVED rather than assumed**: :data:`ASK_TICKET_SINCE_SNAPSHOT` fires on a
real (non-``suppressed``) ticket created after the snapshot, which is the moment
new exposure is actually taken. A ``suppressed`` row is excluded deliberately —
it produces no exposure by construction (``breakout_executor`` journals it and
returns before routing), so counting it would re-create the noise while adding
no safety.

**AND THE THRESHOLD IS NOT TOUCHED.** ``PROP_STATUS_REQUEST_MAX_AGE_HOURS`` has
**four** consumers — this ask, the folded balance nudge on every fill ack,
:func:`src.prop.prop_balance.max_age_hours` (the **sizing** freshness gate), and
the ``status_freshness`` verdict on ``/api/bot/prop/status``. One definition of
"too old to trust" is deliberate, so sizing can never run off a balance the
safety panel has written off. The **trigger** changed; the threshold did not.

⚠️ **RESIDUAL, STATED RATHER THAN HIDDEN — going quiet while flat is only safe
because SIZING refuses on a stale balance, and that refusal reaches nobody.**
``prop_sizing_balance`` returns ``stale`` and ``Coordinator.multi_account_execute``
raises, but that branch only calls ``log_rejection_to_journal`` + ``logger``; the
operator-facing ``_emit_execution_failure_ping`` is on the LATER ``execute_pkg``
branch and is never reached. So a prop account blocked by a stale balance stops
trading with no operator-facing signal. Filed as
``BL-20260909-PROP-SIZING-REFUSAL-ON-A-STALE-BALANCE-IS-JOURNALED-BUT-NEVER-PINGED``;
deliberately NOT fixed here (one PR, one concern), and it is why
:data:`ASK_TICKET_SINCE_SNAPSHOT` exists rather than trusting the gate alone.

So the ask is now driven by :func:`src.prop.prop_identity.declared_prop_account_ids`
(``live_only=True`` — a ``dry_run`` prop account has no exposure to protect and
nagging about it is noise), unioned with any account that actually holds an
open prop position, so an id the config no longer declares is still covered.
When positions ARE open they ride along as context in the message.

Design notes (mirrors ``prop_monitor_pulse``):

- **Open-position detection is reused**, not re-derived —
  :func:`src.prop.prop_monitor_pulse.find_open_prop_positions`. A scan
  *failure* is passed down as ``None`` rather than ``[]``: "we could not look"
  and "the book is flat" are opposite statements and the ping says which.
  ⚠️ **That helper ALSO swallows its own ``list_fills`` failure into ``[]``**
  (``prop_monitor_pulse.py``), so the ``None`` discipline here only catches an
  exception that *escapes* it. :func:`assess_activity` therefore refuses to read
  ``[]`` as evidence of quiescence unless the ticket read *also* succeeded —
  a silent ``[]`` must never become :data:`QUIESCENT`, which is the one verdict
  that suppresses the ask.
- **Cadence state is a small JSON file**
  (``runtime_logs/prop_status_request.json``): ``{account_id: last_request_iso}``,
  pruned to the accounts we may ask about (declared ∪ position-holding), not
  to the ones currently holding a position.
- **Baseline, not gated.** No default-off enable flag (Prime Directive). Knobs:
  ``PROP_STATUS_REQUEST_MAX_AGE_HOURS`` (default 24; ``<= 0`` pauses the
  feature) and ``PROP_STATUS_REQUEST_COOLDOWN_HOURS`` (default 12). The
  cooldown is what bounds the cost of asking while flat: one ping per account
  per 12h, only while the snapshot is actually stale.
- **Best-effort + isolated.** Every path swallows its own exceptions; called
  once per trader tick from ``src/main.py``.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.prop.prop_identity import declared_prop_account_ids

logger = logging.getLogger(__name__)

_DEFAULT_MAX_AGE_HOURS = 24.0
_DEFAULT_COOLDOWN_HOURS = 12.0
_STATE_FILENAME = "prop_status_request.json"

# ── activity verdicts — NEVER COLLAPSED ───────────────────────────────
# Four states, and the distinction that matters is between the last two:
# QUIESCENT is "we looked and nothing has happened", UNKNOWN is "we could not
# look". Only QUIESCENT suppresses the ask; UNKNOWN asks, because a read
# failure must never be able to silence a safety prompt. (docs/CLAUDE-RULES-
# CANONICAL.md § "Collapsed states".)
ASK_NO_SNAPSHOT = "no_snapshot"
#: An open prop position. Its UNREALIZED drawdown is invisible to the cushion
#: — nothing reports it — so it can breach the static DD floor with no fill
#: ever being written. This is the one state where a stale snapshot is
#: genuinely unsafe.
ASK_POSITION_OPEN = "position_open"
#: A real (non-``suppressed``) ticket created after the snapshot: new exposure
#: was taken against this cushion. This is the 2026-08-14 "before a new ticket
#: is sized" concern, OBSERVED rather than assumed.
ASK_TICKET_SINCE_SNAPSHOT = "ticket_since_snapshot"
#: We looked and nothing has happened. The ONLY verdict that suppresses.
QUIESCENT = "quiescent"
#: We could NOT look. Asks, deliberately.
UNKNOWN = "unknown"

ACTIVITY_STATES = (
    ASK_NO_SNAPSHOT, ASK_POSITION_OPEN, ASK_TICKET_SINCE_SNAPSHOT,
    QUIESCENT, UNKNOWN,
)

#: Ticket statuses that took NO exposure. ``suppressed`` is journaled by
#: ``breakout_executor`` and returns BEFORE routing, so the ticket never
#: reaches a venue and never becomes a position — counting it would recreate
#: the wall-clock noise while adding no safety. Anything else (including an
#: unrecognised or blank status) counts as real: on an ALARM the fail-safe
#: direction is to ask, the opposite polarity to an order gate.
NON_EXPOSING_TICKET_STATUSES = frozenset({"suppressed"})


def assess_activity(
    *,
    snapshot_reported_at: Optional[str],
    open_positions: Optional[List[Dict[str, Any]]],
    tickets: Optional[List[Dict[str, Any]]],
) -> tuple:
    """``(state, detail)`` — has anything HAPPENED since the snapshot?

    Pure: it takes the already-loaded rows rather than reading the DB, so the
    policy is arguable in tests instead of against a live prop account.

    ``open_positions``/``tickets`` are ``None`` for *we could not look*, which
    is NOT ``[]``. A ``[]`` from a reader that silently swallowed its own error
    would otherwise become :data:`QUIESCENT` and suppress the ask — see the
    ``find_open_prop_positions`` caveat in the module docstring.
    """
    if open_positions is None or tickets is None:
        return UNKNOWN, {
            "reason": "positions_unreadable" if open_positions is None
                      else "tickets_unreadable",
        }
    # An open position outranks everything: unrealized drawdown is invisible.
    if open_positions:
        return ASK_POSITION_OPEN, {"open_positions": len(open_positions)}
    snap_at = _parse_iso(snapshot_reported_at)
    if snap_at is None:
        # No snapshot, or an undateable one. "Since the snapshot" has no anchor,
        # so quiescence cannot be established — and with no cushion at all the
        # account cannot size. Never QUIESCENT.
        return ASK_NO_SNAPSHOT, {"reason": "no dateable snapshot to measure from"}
    exposing = 0
    for t in tickets:
        if str(t.get("status") or "").strip().lower() in NON_EXPOSING_TICKET_STATUSES:
            continue
        created = _parse_iso(t.get("created_at") or t.get("signal_time"))
        # An undateable ticket cannot be shown to predate the snapshot; count it
        # (fail-safe toward asking).
        if created is None or created > snap_at:
            exposing += 1
    if exposing:
        return ASK_TICKET_SINCE_SNAPSHOT, {"tickets_since_snapshot": exposing}
    return QUIESCENT, {
        "open_positions": 0,
        "tickets_since_snapshot": 0,
        "note": ("reported fills are deliberately NOT a trigger: "
                 "reconstruct_equity applies losses and withholds gains, so a "
                 "reported fill can only make the cushion conservative"),
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _hours_knob(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _state_path() -> str:
    from src.utils.paths import runtime_logs_dir

    return str(runtime_logs_dir() / _STATE_FILENAME)


def last_request_iso(entry: Any) -> Optional[str]:
    """The last-asked timestamp from a cadence entry, legacy or current.

    ⚠️ THE ENTRY SHAPE CHANGED (2026-09-09) AND THE OLD ONE MUST KEEP WORKING.
    It was ``{account_id: "<iso>"}``; it is now
    ``{account_id: {"last_request": "<iso>", "last_verdict": ..., ...}}``.
    The live VM holds a file in the OLD shape at the moment this deploys, so a
    reader that assumed a dict would read every account as never-asked and
    re-ask the whole fleet on the first tick after the deploy — the cooldown
    silently reset by a refactor. A bare string is therefore still accepted.
    """
    if isinstance(entry, dict):
        return entry.get("last_request")
    if isinstance(entry, str):
        return entry or None
    return None


def _load_state() -> Dict[str, Any]:
    try:
        with open(_state_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        path = _state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except OSError as exc:
        logger.warning("prop_status_request: state save failed: %s", exc)


def _latest_status_row(account_id: str) -> Optional[Dict[str, Any]]:
    """Newest ``prop_account_status`` row, or ``None``.

    ⚠️ ``None`` here is BOTH "no row" and "the read failed" — the caller must
    not read it as "nothing has happened". :func:`assess_activity` grades a
    missing anchor as :data:`ASK_NO_SNAPSHOT`, never :data:`QUIESCENT`, so the
    ambiguity can only ever cause an ASK.
    """
    try:
        from src.prop import prop_journal

        return prop_journal.latest_account_status(account_id)
    except Exception as exc:  # noqa: BLE001 — a read failure must not raise
        logger.warning("prop_status_request: status read failed: %s", exc)
        return None


def _status_age_hours(account_id: str, now: datetime,
                      row: Optional[Dict[str, Any]] = None) -> Optional[float]:
    """Age of the newest ``prop_account_status`` row, or ``None`` when absent.

    ``row`` may be passed by a caller that already loaded it, so one pass does
    not read the same row twice.
    """
    if row is None:
        row = _latest_status_row(account_id)
    if not row:
        return None
    ts = _parse_iso(row.get("reported_at") or row.get("created_at") or row.get("ts"))
    if ts is None:
        return None
    return max(0.0, (now - ts).total_seconds() / 3600.0)


def run_prop_status_request(now: Optional[datetime] = None) -> List[str]:
    """One tick of the status-request loop. Returns account_ids pinged."""
    max_age_h = _hours_knob("PROP_STATUS_REQUEST_MAX_AGE_HOURS", _DEFAULT_MAX_AGE_HOURS)
    if max_age_h <= 0:  # paused without a redeploy
        return []
    cooldown_h = _hours_knob(
        "PROP_STATUS_REQUEST_COOLDOWN_HOURS", _DEFAULT_COOLDOWN_HOURS)
    now = now or _now()

    # Positions are CONTEXT for the ping, never the trigger — see the module
    # docstring. `None` is kept distinct from `[]`: a failed scan must not be
    # reported to the operator as "the account is flat".
    by_account: Optional[Dict[str, List[Dict[str, Any]]]]
    try:
        from src.prop.prop_monitor_pulse import find_open_prop_positions

        positions = find_open_prop_positions() or []
        by_account = {}
        for pos in positions:
            acct = str(pos.get("account_id") or pos.get("account") or "").strip()
            if acct:
                by_account.setdefault(acct, []).append(pos)
    except Exception as exc:  # noqa: BLE001
        logger.warning("prop_status_request: open-position scan failed: %s", exc)
        by_account = None

    # Tickets, for the "new exposure was taken against this cushion" half of
    # the trigger. `None` is "we could not look" and is kept distinct from an
    # empty list, exactly as the position scan above is.
    tickets_by_account: Optional[Dict[str, List[Dict[str, Any]]]]
    try:
        from src.prop import prop_journal as _pj

        tickets_by_account = {}
        for tk in _pj.list_tickets(limit=500) or []:
            acct = str(tk.get("account_id") or "").strip()
            if acct:
                tickets_by_account.setdefault(acct, []).append(tk)
    except Exception as exc:  # noqa: BLE001
        logger.warning("prop_status_request: ticket scan failed: %s", exc)
        tickets_by_account = None

    declared = declared_prop_account_ids(live_only=True)
    if declared is None:
        # Could not read accounts.yaml. Say so rather than proceeding as if the
        # system declares no prop accounts — the position-holding set below is
        # then the only coverage we have, and it is a strict subset.
        logger.warning(
            "prop_status_request: prop-account enumeration unavailable "
            "(accounts.yaml unreadable); covering only accounts with a "
            "currently-open prop position")
        declared = []

    # Accounts we may ask about: every declared live prop account, plus any
    # account actually holding an open prop position (covers an id the config
    # no longer declares — a position we can see is a position to protect).
    candidates = list(dict.fromkeys([*declared, *(by_account or {}).keys()]))
    if not candidates:
        _save_state({})
        return []

    state = _load_state()
    pinged: List[str] = []
    for acct in candidates:
        snap = _latest_status_row(acct)
        age_h = _status_age_hours(acct, now, row=snap)
        if age_h is not None and age_h < max_age_h:
            continue  # snapshot fresh enough — nothing to ask
        last_req = _parse_iso(last_request_iso(state.get(acct)))
        if last_req and (now - last_req).total_seconds() < cooldown_h * 3600.0:
            continue  # asked recently — don't nag
        open_positions = None if by_account is None else by_account.get(acct, [])

        # ── THE TRIGGER (2026-09-09) ──────────────────────────────────
        # A stale snapshot is necessary but NOT sufficient. Ask only when
        # something has actually happened, or when we could not establish
        # that it hasn't. See the module docstring for why a reported fill
        # is deliberately not on this list.
        verdict, detail = assess_activity(
            snapshot_reported_at=(
                None if snap is None
                else (snap.get("reported_at") or snap.get("created_at"))
            ),
            open_positions=open_positions,
            tickets=(None if tickets_by_account is None
                     else tickets_by_account.get(acct, [])),
        )
        # ⚠️ RECORD THE VERDICT WHETHER OR NOT WE ASK — this is what makes the
        # suppression OBSERVABLE. Measured 2026-09-09T11:3xZ on the live VM
        # right after MI-214 deployed: `prop_status_request` appeared ZERO
        # times in 400 journal lines while the tick was demonstrably alive (24
        # heartbeat mentions), because QUIESCENT was logged at DEBUG. So "the
        # module ran and correctly stayed quiet" was indistinguishable from
        # "it crashed on import" and from "it never ran" — the collapsed-state
        # failure this repo names, in the instrument rather than the data.
        # A journal line is NOT sufficient on its own either: this branch is
        # reached at most once per COOLDOWN (12h) and journald retention here
        # was measured at ~30 minutes, so the line is gone before anyone looks.
        # The stamp is durable and already has a read surface
        # (/api/diag/log_file?name=prop_status_request), which is why it goes
        # here rather than into a new file nothing is allowlisted to read
        # (BL-20260825-ALERT-AND-CADENCE-STATE-FILES-SHIP-WITHOUT-A-READ-SURFACE).
        prior = state.get(acct)
        entry: Dict[str, Any] = {
            "last_request": last_request_iso(prior),
            "last_verdict": verdict,
            "last_assessed_at": now.isoformat(),
            "last_detail": detail,
            "last_age_hours": age_h,
        }
        state[acct] = entry
        if verdict == QUIESCENT:
            logger.info(
                "prop_status_request: %s snapshot is %.1fh old but QUIESCENT "
                "(%s) — not asking", acct,
                age_h if age_h is not None else -1.0, detail)
            continue
        logger.info(
            "prop_status_request: %s asking — verdict=%s %s", acct, verdict, detail)
        try:
            from src.prop.breakout_notify import emit_prop_status_request

            emit_prop_status_request(acct, open_positions, age_hours=age_h)
            entry["last_request"] = now.isoformat()
            state[acct] = entry
            pinged.append(acct)
        except Exception as exc:  # noqa: BLE001 — notification never fatal
            logger.warning(
                "prop_status_request: emit failed for %s: %s", acct, exc)

    # Prune to the accounts we may ask about — NOT to the ones holding a
    # position, which is what let a flat account's cadence state (and with it
    # the ask itself) disappear.
    _save_state({k: v for k, v in state.items() if k in set(candidates)})
    return pinged


__all__ = [
    "run_prop_status_request",
    "last_request_iso",
    "assess_activity",
    "ACTIVITY_STATES",
    "ASK_NO_SNAPSHOT",
    "ASK_POSITION_OPEN",
    "ASK_TICKET_SINCE_SNAPSHOT",
    "QUIESCENT",
    "UNKNOWN",
    "NON_EXPOSING_TICKET_STATUSES",
]
