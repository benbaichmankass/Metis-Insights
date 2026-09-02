"""Per-account execution-failure diagnostic ping.

When ``Coordinator.multi_account_execute`` fails to route a strategy's
order package to a live account, the operator needs an immediate
human-readable description of *which* account refused, *what* package
was dropped, and *why*. The previous wiring buried the failure inside
the audit log; this module surfaces it via the existing pending-pings
inbox (``runtime_logs/pending_pings/``) — the same channel the
``ict-telegram-bot`` job-queue tick drains every ~5 s.

Design rules:

- **Asynchronous.** Producers drop a JSON file via ``os.replace`` and
  return; nothing in the order path waits on Telegram. A failed
  enqueue only logs a warning — the order-routing failure is already
  surfaced via the result dict + pipeline audit log, so the diagnostic
  ping is best-effort.
- **No secrets.** The body is plain text limited to fields the operator
  already sees in ``/accounts_status`` (account name, strategy, symbol,
  side, qty) and a short failure reason. No API keys, no balance
  values, no SDK exception payloads beyond ``type(exc).__name__``.
- **Idempotent enough.** Each ping gets a unique filename via
  ``uuid.uuid4`` so duplicates from a flapping pipeline tick don't
  collide. The bot's drainer deletes after send; nothing here needs a
  retry queue.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

PENDING_PINGS_DIR = runtime_logs_dir() / "pending_pings"

# Refusal reasons that are EXPECTED, deliberate policy skips — NOT dispatch
# failures. A ``dry_run``-shelved account declining an order (the risk gate's
# ``account_mode_dry_run``), or a prop account skipping a mission-met /
# session-restricted signal (``SKIP_*``), is the execution gate working exactly
# as designed. Routing a signal at a wired-but-off account and having it bounce
# is not an error — the trade simply isn't sent — so it must never raise an
# operator "execution failed" / "all accounts failed to dispatch" alert
# (operator directive 2026-07-15, after shelving alpaca_live to dry_run made
# every tick fire both banners). The rejection is still journaled for audit; we
# only suppress the *alerting*, not the gate or the record. Matched as a
# substring so both the bare reason (``account_mode_dry_run``) and the wrapped
# RiskBreach message (``Account 'x' rejected order for Y: account_mode_dry_run``)
# are recognised.
EXPECTED_DISPATCH_SKIP_REASONS = (
    "account_mode_dry_run",
    # A sizing refusal (zero_balance / risk_refused) on an account the
    # coordinator had ALREADY resolved to effective-dry (shelved dry_run
    # account, execution:shadow strategy, or process-level dry override).
    # The account could never have placed the order regardless of the
    # sizing outcome, so the refusal is a policy hold, not a dispatch
    # failure. Without this, a dry-shelved account whose funds were
    # deliberately moved out (alpaca_live, shelved + defunded 2026-07-15)
    # alarmed "failed to dispatch: zero_balance" on every signal — the
    # sizer runs before the risk gate's account_mode_dry_run rejection,
    # so the 2026-07-15 suppression never matched (operator report
    # 2026-07-20). The coordinator prefixes this token onto the
    # underlying reason, which stays intact for the journal/audit.
    "dry_run_sizing_skip",
    # The DISPATCH-path sibling of `dry_run_sizing_skip`, and the reason this
    # set needed a second dry token rather than one (2026-08-25,
    # BL-20260825-DECLARED-SKIP-SET-MISSES-THE-DISPATCH-PATH-TOKEN). The two are
    # produced by DIFFERENT code paths for the same declared condition:
    # `dry_run_sizing_skip` is prefixed by the coordinator when the SIZER runs
    # on an already-effective-dry account, while this one is set by
    # `execute.py` when the DISPATCH itself is genuinely dry
    # (`_genuinely_dry` -> `_rej_reason = "dry_run_no_order_placed"`,
    # `_rej_is_dry = True`). Recognising only the first left the second grading
    # as a real refusal.
    #
    # Measured live 2026-08-25: `avax_pullback_2h` (config/strategies.yaml
    # `execution: shadow` — DECLARED OFF) produced 13 rows in the window, all
    # carrying this token, and `dead_leg.verdict_for` graded the leg
    # `signalled_never_placed` — the most alarming verdict this family has, for
    # a strategy the operator switched off on purpose. That is the exact defect
    # #10257 fixed for the sizing path, reaching the same conclusion through the
    # other door.
    #
    # ⚠️ Its NOT-dry sibling `exchange_client_unavailable_no_order_placed` is
    # deliberately ABSENT and must stay absent: `execute.py` picks between the
    # two on `_genuinely_dry` precisely so a gateway-down dispatch is never
    # mistaken for an intentional dry run. The match is a SUBSTRING test, and
    # the two strings do not contain one another, so adding this one cannot
    # silently recognise that one.
    "dry_run_no_order_placed",
    "SKIP_MISSION_MET",
    "SKIP_OVERNIGHT_RESTRICTED",
    "SKIP_WEEKEND_RESTRICTED",
)


def is_expected_dispatch_skip(reason: object) -> bool:
    """True when *reason* is a deliberate, expected policy skip (a shelved
    ``dry_run`` account or a prop mission/session skip) rather than a genuine
    dispatch failure — so the caller can suppress the operator alert while still
    journaling the rejection. Accepts the bare reason or the wrapped RiskBreach
    message (substring match). Never raises."""
    text = str(reason or "")
    return any(tok in text for tok in EXPECTED_DISPATCH_SKIP_REASONS)


#: ``intent_noop:`` IS A DESIGNED NAMESPACE, not a coincidence of naming, and
#: that is what makes a prefix test safe here rather than sloppy.
#: ``coordinator.multi_account_execute`` files every deliberate no-op under it
#: (``at_target``, ``flip_suppressed_hold_policy``,
#: ``hold_to_bracket_reduce_non_derivative``, ``already_flat_and_target_flat``,
#: ``conflict_resolved_by_priority`` …) and deliberately names the FAILURE
#: sibling of the very same branch ``intent_close_flatten_failed:`` — OUTSIDE
#: the namespace, with the comment *"Failure → a real error so the alert DOES
#: fire"*. The prefix already carries the meaning; this predicate only reads it.
#: ``tests/test_policy_hold_predicate.py`` pins that invariant, so a future
#: failure smuggled into the namespace fails CI rather than going quiet.
_POLICY_HOLD_PREFIXES = ("intent_noop:", "reentry_suppressed_netting_guard:")
_POLICY_HOLD_EXACT = ("intent_sub_min_qty_delta",)


def is_policy_hold(reason: object) -> bool:
    """True when *reason* is a declared no-op or hold — the BROAD predicate.

    A strict superset of :func:`is_expected_dispatch_skip`. The two answer
    different questions and both are needed:

    * ``is_expected_dispatch_skip`` — "is this account/venue DECLARED OFF?"
      (a ``dry_run`` shelf, a prop mission/session skip). A property of the
      *account*.
    * ``is_policy_hold`` — "did the system DECIDE not to send this order?"
      Everything above, plus the intent layer resolving to a no-op (the
      position is already at target, ``FLIP_POLICY=hold`` keeping a position,
      a bracket-reduce held for the broker bracket) and the netting guard
      suppressing a re-entry. A property of the *decision*.

    ⚠️ **WHY THIS EXISTS AS A MODULE-LEVEL FUNCTION** (2026-08-25,
    ``BL-20260825-DECLARED-POLICY-HOLDS-GRADE-AS-REFUSALS-IN-THE-DEAD-LEG-VOCABULARY``).
    This rule is not new — it has been the incumbent since 2026-05/07 in
    ``enqueue_all_accounts_failed_dispatch``'s ``_is_hold`` and in
    ``coordinator._is_benign_noop``. But both were NESTED CLOSURES, so nothing
    could import them, and ``dead_leg`` (written later, needing exactly this
    question answered) delegated to the only *importable* predicate in this
    module — the narrower one. Its docstring said it delegated to "the one
    module that owns 'is this refusal deliberate?'", which was true of the
    module and false of the predicate: it picked the narrower of two, and the
    narrower one is a strict subset of what the same module uses for its own
    alerting.

    Measured cost of that gap, live 2026-08-25 (1000-row diag window,
    2026-07-26 → 2026-08-25; population stated because the ratio is the point):
    across all six legs grading ``signalled_never_placed`` — the most alarming
    verdict this family has — **77 of 93 refused rows (82.8%) carried one of
    these declared tokens**, and only 3 were a genuine capability failure.
    ``avax_pullback_2h``, a leg the operator switched off (``execution:
    shadow``), carried that verdict on the strength of a single row that was
    itself ``intent_noop:flip_suppressed_hold_policy``.

    Fail-safe and never raises. Accepts the bare reason or a wrapped
    RiskBreach message.
    """
    text = str(reason or "")
    return (
        text.startswith(_POLICY_HOLD_PREFIXES)
        or text in _POLICY_HOLD_EXACT
        or is_expected_dispatch_skip(text)
    )

# Durable ring of the operator alerts this module raises (2026-07-08). The
# pending-ping files are transient (the Telegram sender consumes + deletes
# them), so they can't back the app's Overview notification banner. Every
# enqueue_* alert also appends a structured row here; ``GET /api/bot/notifications``
# (a DIFFERENT process from the trader) reads the recent tail so a live
# operational condition — a stuck position-close, a naked/orphan flag, a
# failed dispatch — surfaces on the banner, not only in Telegram. Best-effort,
# bounded (trimmed to the last _OPERATOR_ALERTS_KEEP rows); never raises into
# the caller.
OPERATOR_ALERTS_LOG = runtime_logs_dir() / "operator_alerts.jsonl"
_OPERATOR_ALERTS_KEEP = 300


def _append_operator_alert(
    kind: str, priority: str, body: str,
    extra: Optional[dict] = None,
) -> None:
    """Append one operator alert to the durable banner-feed ring (best-effort).

    ``extra`` adds flat scalar fields to the row. It exists for exactly one
    reason and it is worth stating: this file is **the only surface from which a
    page RATE is recoverable** (CLAUDE.md § diag ``log_file``, the
    ``operator_alerts`` row). ``/api/bot/notifications`` renders the CURRENT
    banner and nothing else, and these alerts deliberately do not ride
    ``outcomes.jsonl``, so ``/api/bot/logs?level=error`` returns zero of them.

    So when an alert is DOWNGRADED out of the paging channel, the row must still
    land here — carrying ``route`` — or the downgrade itself becomes
    unmeasurable: "we downgraded it" and "it never fired" would render
    identically, and a reviewer could never establish how often the quiet path
    was taken. Suppressing the row along with the page would destroy the one
    instrument that answers the desensitised-alarm question.
    """
    try:
        from datetime import datetime, timezone

        OPERATOR_ALERTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "priority": str(priority or "high"),
            "body": str(body or "")[:1024],
        }
        # Flat scalars only, and never shadowing a core field: a row whose `kind`
        # or `ts` could be overwritten by a caller would corrupt the one feed a
        # page rate is computed from.
        for k, v in (extra or {}).items():
            if str(k) in row:
                continue
            if v is None or isinstance(v, (str, int, float, bool)):
                row[str(k)] = v
        with OPERATOR_ALERTS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        # Trim the ring when it grows past ~2x the keep target (cheap amortised).
        try:
            with OPERATOR_ALERTS_LOG.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()
            if len(lines) > _OPERATOR_ALERTS_KEEP * 2:
                tmp = OPERATOR_ALERTS_LOG.with_suffix(".jsonl.tmp")
                with tmp.open("w", encoding="utf-8") as fh:
                    fh.writelines(lines[-_OPERATOR_ALERTS_KEEP:])
                os.replace(tmp, OPERATOR_ALERTS_LOG)
        except OSError:
            pass
    except Exception as exc:  # noqa: BLE001 — an alert-feed append must never break a ping
        logger.warning("execution_diagnostics: operator-alert append failed: %s", exc)

# Durable follow-up log of NEW orphan trade rows. The operator's standing
# directive (2026-06-24): an orphan is NEVER an acceptable resting status — it
# is a problem to be reconciled. Every time a row enters an orphan state we
# append a structured event here so the next /health-review (and /system-review)
# drains it into the health-review backlog for follow-up — and fire a loud
# operator red-flag (see enqueue_orphan_created_flag).
ORPHAN_EVENTS_LOG = runtime_logs_dir() / "orphan_events.jsonl"


def enqueue_execution_failure(
    *,
    account: str,
    strategy: str,
    symbol: str,
    side: str,
    qty: Optional[float],
    reason: str,
    priority: str = "high",
    demo: bool = False,
) -> Optional[Path]:
    """Drop a Telegram-ready JSON ping for a per-account execution failure.

    Returns the path of the queued file on success, ``None`` when the
    enqueue itself fails (e.g. read-only filesystem in a sandboxed
    test). Failure to enqueue is logged at WARN — never raises.
    """
    try:
        prefix = "*DEMO TRADER* " if demo else ""
        body = (
            f"{prefix}⚠️ Order execution failed\n"
            f"Account: {account}\n"
            f"Strategy: {strategy}\n"
            f"Symbol: {symbol} | Side: {side} | Qty: {qty if qty is not None else '?'}\n"
            f"Reason: {reason}"
        )[:1024]
        _append_operator_alert("execution_failure", priority, body)
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-execfail.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: enqueue failed for account=%s reason=%r: %s",
            account, reason[:80], exc,
        )
        return None


def enqueue_orphan_created_flag(
    *,
    account: str,
    symbol: str,
    side: str,
    trade_id: Optional[int],
    origin: str,
    reason: Optional[str] = None,
    priority: str = "critical",
) -> Optional[Path]:
    """Record a NEW orphan trade row durably AND fire a loud operator red-flag.

    Two halves, both best-effort (never raises into the order path):

    1. **Follow-up record** — append a structured ``orphan_created`` event to
       ``runtime_logs/orphan_events.jsonl`` so the next ``/health-review`` /
       ``/system-review`` drains it into the health-review backlog. An orphan is
       a problem to solve, not a status to accept — this guarantees it is tracked
       for reconciliation even if the operator misses the ping.
    2. **Red-flag ping** — a CRITICAL Telegram alert telling the operator to
       initiate a ``/system-review`` session so the orphan gets reconciled to its
       real trade / order package (or explicitly marked unreconcilable).

    ``origin`` describes how the row entered the orphan state
    (``adopt_reattached`` / ``adopt_bare`` / ``mark_orphaned`` /
    ``unattributable`` …) for the backlog drain.
    """
    # 1) durable follow-up record
    try:
        ORPHAN_EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        evt = {
            "kind": "orphan_created",
            "ts": datetime.now(timezone.utc).isoformat(),
            "account": account,
            "symbol": symbol,
            "side": side,
            "trade_id": trade_id,
            "origin": origin,
            "reason": reason,
        }
        with ORPHAN_EVENTS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(evt, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: orphan_events append failed account=%s "
            "symbol=%s: %s", account, symbol, exc,
        )

    # 2) loud red-flag ping
    try:
        body = (
            "🚩🚩 ORPHAN TRADE CREATED — needs reconciliation\n"
            f"Account: {account}\n"
            f"Symbol: {symbol} | Side: {side}\n"
            f"Trade id: {trade_id if trade_id is not None else '—'}\n"
            f"Origin: {origin}"
            + (f"\nReason: {reason}" if reason else "")
            + "\n\nOrphan is a problem state, not a status. "
            "▶️ Initiate a /system-review to reconcile this to its real "
            "trade/order package (or mark it explicitly unreconcilable)."
        )[:1024]
        _append_operator_alert("orphan_created", priority, body)
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-orphanflag.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: orphan_created_flag enqueue failed "
            "account=%s symbol=%s: %s", account, symbol, exc,
        )
        return None


def enqueue_close_failure(
    *,
    account: Optional[str],
    symbol: Optional[str],
    side: Optional[str],
    qty: Optional[float],
    consecutive: int,
    error: Optional[str],
    priority: str = "high",
) -> Optional[Path]:
    """Surface a monitor close that has failed N consecutive times.

    The monitor's exchange-first close leaves the DB row OPEN and retries on any
    exchange-close failure (network / rate-limit / venue error). That retry was
    previously SILENT (an ERROR log, no operator ping) — a position that won't
    flatten could be retried forever unnoticed. After N consecutive failures for
    the same (account, symbol, direction) this fires so the operator can act.
    Best-effort; never raises.

    ⚠️ **ONE NARROW CLASS IS ROUTED TO THE DIGEST INSTEAD OF THE PAGER** — see
    :func:`route_close_failure` and :mod:`src.runtime.close_wedge_standing`.
    Everything else, including a close that has failed a hundred times for a
    reason nobody has established, pages exactly as before. The routing keys on
    an EVIDENCED determination, never on the failure repeating.
    """
    try:
        route, transition, reason, share_hold = route_close_failure(
            account=account, symbol=symbol, side=side, error=error,
        )
        if route == "digest":
            body = (
                "🧱 Position CLOSE wedged BROKER-SIDE — carried in the digest\n"
                f"Account: {account}\n"
                f"Symbol: {symbol} | Side: {side} | "
                f"Qty: {qty if qty is not None else '?'}\n"
                f"Consecutive close failures: {consecutive}\n"
                f"share_hold: {share_hold}\n"
                f"{_share_hold_guidance(share_hold)}\n"
                f"Standing: {_standing_phrase(transition)}\n"
                f"Routing: {reason}\n"
                f"Last error: {error}\n"
                "This is NOT resolved and NOT ignored: it is carried in the "
                "rolled-up digest every run until the state changes, and it pages "
                "again the moment it does (cleared, or wedged on new evidence)."
            )[:1024]
        else:
            body = (
                "🛑 Position CLOSE failing — won't flatten\n"
                f"Account: {account}\n"
                f"Symbol: {symbol} | Side: {side} | "
                f"Qty: {qty if qty is not None else '?'}\n"
                f"Consecutive close failures: {consecutive}\n"
                f"share_hold: {share_hold}\n"
                f"{_share_hold_guidance(share_hold)}\n"
                f"Last error: {error}\n"
                "The DB row is left OPEN and retried each tick; the "
                "stuck-strategy watchdog is the backstop."
            )[:1024]

        # THE RING ROW IS WRITTEN ON BOTH ROUTES, DELIBERATELY. It carries
        # `route`, so the downgrade RATE is recoverable from the same file the
        # page rate is — see `_append_operator_alert`. Dropping the row with the
        # page would make "downgraded" and "never fired" identical here, which is
        # the one thing this file is relied on to tell apart.
        _append_operator_alert(
            "close_failure", priority, body,
            extra={
                "route": route,
                "share_hold": share_hold,
                "wedge_transition": transition,
                "route_reason": reason[:400],
            },
        )
        if route == "digest":
            return None
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-closefail.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: close-failure enqueue failed account=%s "
            "symbol=%s: %s", account, symbol, exc,
        )
        return None


def _standing_phrase(transition: str) -> str:
    """How long this wedge has been a wedge, in words.

    ``still_standing`` is THE ONE transition the digest route is reachable on —
    it is the only quiet state — so naming it here is the branch that keeps it
    from being a value nothing reads. Any other transition arriving on this path
    is a routing bug and prints itself rather than being smoothed over.
    """
    if transition == "still_standing":
        return "carried in the digest since this wedge was first paged"
    return f"unexpected transition on the digest route: {transition}"


def _share_hold_guidance(share_hold: str) -> str:
    """What the operator should DO, given why the shares are held.

    ⚠️ **This function is why ``share_hold`` is a state and not a log string.**
    Until this existed, every "won't flatten" page carried the same sentence —
    *investigate the venue/connection* — whether a retry was about to work, was
    provably never going to, or whether we had failed to look at the broker at
    all. Those are four different operator actions and one of them is "do
    nothing, it is already retrying". A page that cannot tell them apart trains
    the reader to skip it, which is the desensitised-alarm P1.

    Every declared state branches here, including the two that mean *we could
    not establish anything* — and those two say so rather than borrowing the
    reassuring wording of a state we did not observe.
    """
    from src.units.accounts.alpaca_client import SHARE_HOLD_NOT_CLASSIFIED

    if share_hold == "broker_cancel_wedged":
        return (
            "WEDGED BROKER-SIDE: an order sits in pending_cancel/pending_replace. "
            "Our cancel was ACCEPTED and the venue never completed it, so no "
            "further app-level cancel and no cancel_orders=true liquidation can "
            "release these shares. This needs OPERATOR or VENUE action."
        )
    if share_hold == "orders_still_resting":
        return (
            "Ordinary cancellable orders are holding the shares — the next tick's "
            "cancel-then-retry may well clear this on its own. Worth watching "
            "before acting."
        )
    if share_hold == "no_residual_orders":
        return (
            "NO resting order is holding these shares, yet they are held — the "
            "cause is something this classifier does not model. Do not assume a "
            "retry helps; check the position and the account's own holds."
        )
    if share_hold == "residual_unreadable":
        return (
            "We could NOT read the symbol's open orders, so WHY the shares are "
            "held is unestablished. This is not evidence that nothing rests, and "
            "it is not evidence that a retry will work."
        )
    if share_hold == SHARE_HOLD_NOT_CLASSIFIED:
        return (
            "Nobody classified this failure — a non-Alpaca venue, or a path that "
            "never reached the classifying branch. WE DID NOT LOOK; treat the "
            "cause as unknown."
        )
    return (
        f"Unrecognised share_hold {share_hold!r} — this build cannot classify it; "
        f"treat the cause as unknown."
    )


def route_close_failure(
    *,
    account: Optional[str],
    symbol: Optional[str],
    side: Optional[str],
    error: Optional[str],
) -> tuple:
    """Decide whether this close failure PAGES or is carried in the digest.

    Returns ``(route, transition, reason, share_hold)`` where ``route`` is
    ``"page"`` or ``"digest"``.

    ⚠️ **"page" IS THE DEFAULT AND EVERY UNCERTAINTY RESOLVES TO IT.** A failure
    with no ``share_hold`` marker (a non-Alpaca venue, or an Alpaca path that
    never reached the give-up branch) reads ``not_classified`` — *we did not
    look* — and pages. An unreadable standing ledger pages. A ledger write
    failure pages. A raise anywhere in here pages. The quiet route is reachable
    only by a positive, evidenced determination that no lever can help, and only
    while that determination is actually being CARRIED somewhere the operator
    will see it.

    Never raises: on any exception the caller gets ``page``.
    """
    try:
        from src.units.accounts.alpaca_client import parse_share_hold
        from src.runtime.close_wedge_standing import (
            NOT_A_WEDGE, Observation, UNCLEARABLE_HOLD_STATE, observe,
        )

        share_hold = parse_share_hold(error)
        if share_hold != UNCLEARABLE_HOLD_STATE:
            return ("page", NOT_A_WEDGE,
                    f"share_hold={share_hold} is not a confirmed-unclearable "
                    f"determination", share_hold)
        decision = observe(Observation(
            account=str(account or ""), symbol=str(symbol or ""),
            side=str(side or ""), share_hold=share_hold,
            detail=str(error or ""),
        ))
        route = "page" if decision.should_page else "digest"
        return (route, decision.transition, decision.reason, share_hold)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: close-failure ROUTING failed (%s) — "
            "defaulting to page", exc,
        )
        return ("page", "routing_failed", f"routing raised {type(exc).__name__}",
                "not_classified")


def enqueue_close_wedge_state_change(decision: Any, priority: str = "high") -> Optional[Path]:
    """Page the operator that a DOWNGRADED close wedge has CHANGED STATE.

    The operator's bargain when a wedge is downgraded to the digest is that it
    stops competing with live emergencies **and** that they hear immediately
    when it stops being true. This is the second half. It pages for every
    transition except ``still_standing`` — see
    :data:`~src.runtime.close_wedge_standing.LOUD_TRANSITIONS`, which is
    computed as the complement of the one quiet state so a transition added
    later is loud by default.

    ⚠️ **A ``vanished_unattributed`` resolution is reported as UNEXPLAINED, and
    the body says so.** The position leaving the close path is not evidence that
    anything repaired it: an operator console action, Alpaca finally completing
    its own cancel, and a package cascade all look identical from here, and so
    does a trader that simply stopped observing. ``CLAUDE.md``'s
    ``PROTECTION_REASSERT_MODE`` row records what it costs to credit an
    unattributed resolution — a gate at ``annotate`` with an empty allowlist got
    read as having fixed a divergence it could not have touched.

    Best-effort; never raises.
    """
    try:
        from src.runtime.close_wedge_standing import LOUD_TRANSITIONS

        transition = str(getattr(decision, "transition", "") or "")
        if transition not in LOUD_TRANSITIONS:
            return None
        entry = getattr(decision, "entry", None) or {}
        headline = {
            "newly_wedged": "🧱 Position CLOSE wedged BROKER-SIDE (new)",
            "evidence_changed": "🧱 Close wedge CHANGED — new evidence, re-paging",
            "cleared_confirmed": "✅ Close wedge CLEARED — confirmed close observed",
            "vanished_unattributed": "❓ Close wedge GONE — cause NOT established",
        }.get(transition, f"Close wedge: {transition}")
        body = (
            f"{headline}\n"
            f"Account: {entry.get('account')} | Symbol: {entry.get('symbol')} | "
            f"Side: {entry.get('side')}\n"
            f"Transition: {transition}\n"
            f"share_hold: {entry.get('share_hold')}\n"
            f"First seen: {entry.get('first_seen')} | Last seen: {entry.get('last_seen')}\n"
            f"Pages suppressed while it stood: {entry.get('pages_suppressed', 0)}\n"
            f"Attribution: {entry.get('attribution') or getattr(decision, 'reason', '')}\n"
            f"Evidence: {entry.get('detail') or entry.get('evidence')}"
        )[:1024]
        _append_operator_alert(
            "close_wedge_state_change", priority, body,
            extra={
                "route": "page",
                "wedge_transition": transition,
                "share_hold": str(entry.get("share_hold") or ""),
                "pages_suppressed": int(entry.get("pages_suppressed") or 0),
            },
        )
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-wedgestate.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: close-wedge state-change enqueue failed: %s", exc,
        )
        return None


def enqueue_stuck_package_sweep(
    *, count: int, priority: str = "high",
) -> Optional[Path]:
    """Alert when the stuck-linked-package sweep force-closes ``count`` rows.

    The sweep is a second-line self-heal: a package left ``status='open'`` after
    its linked trade reached a terminal status blocks the strategy-monocle gate
    (every future signal for that strategy is silently dropped). It previously
    only logged — so the underlying cascade gap stayed invisible. A non-zero
    sweep means a primary cascade path missed; surface it. Best-effort.
    """
    try:
        body = (
            "🧹 Stuck linked-package sweep fired\n"
            f"Force-closed {count} order package(s) whose linked trade was "
            "already terminal but the package stayed open (the strategy-monocle "
            "gate would otherwise stay blocked).\n"
            "This is the second-line self-heal — a non-zero count means a primary "
            "cascade path missed; worth a look."
        )[:1024]
        _append_operator_alert("stuck_package_sweep", priority, body)
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-stucksweep.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: stuck-package-sweep enqueue failed: %s", exc,
        )
        return None


def enqueue_daily_cap_alert(
    *,
    account: str,
    kind: str,
    daily_pnl: Optional[float] = None,
    cap_usd: Optional[float] = None,
    demo: bool = False,
    priority: str = "high",
) -> Optional[Path]:
    """Drop a Telegram ping for a daily-loss-cap state transition.

    ``kind`` is ``"exhausted"`` (the account just hit its daily-loss cap
    and will refuse trades until the next UTC reset) or ``"resumed"`` (the
    cap cleared — new UTC day or a recovering PnL — and the account is
    trading again). Fired at most once per transition by the latching
    state in ``src.runtime.daily_cap_alert``; this function only formats +
    queues. Never raises.
    """
    try:
        prefix = "*DEMO TRADER* " if demo else ""
        pnl_str = f"{daily_pnl:+.2f}" if daily_pnl is not None else "?"
        cap_str = f"{cap_usd:.2f}" if cap_usd is not None else "?"
        if kind == "exhausted":
            body = (
                f"{prefix}⛔ Daily-loss cap hit\n"
                f"Account: {account}\n"
                f"Today's PnL: {pnl_str} USD  (cap: -{cap_str} USD)\n"
                f"No further trades on this account today. Account stays "
                f"live; it auto-resumes at 00:00 UTC."
            )[:1024]
        else:  # resumed
            body = (
                f"{prefix}✅ Daily-loss cap reset\n"
                f"Account: {account}\n"
                f"Today's PnL: {pnl_str} USD  (cap: -{cap_str} USD)\n"
                f"Trading resumed."
            )[:1024]
        _append_operator_alert("daily_cap", priority, body)
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-dailycap.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: daily-cap ping enqueue failed for "
            "account=%s kind=%s: %s",
            account, kind, exc,
        )
        return None


def enqueue_demo_trade_notification(
    *,
    account: str,
    strategy: str,
    symbol: str,
    side: str,
    qty: Optional[float],
    status: str,
    detail: str,
    priority: str = "normal",
) -> Optional[Path]:
    """Drop a *DEMO TRADER* prefixed Telegram ping for a demo-account event.

    Used for successful demo trade submissions so the operator can track
    demo activity without it blending into live-account notifications.
    Never raises.
    """
    try:
        qty_str = f"{qty:.4f}" if qty is not None else "?"
        body = (
            f"*DEMO TRADER* {status.upper()}\n"
            f"Account: {account}\n"
            f"Strategy: {strategy}\n"
            f"Symbol: {symbol} | Side: {side} | Qty: {qty_str}\n"
            f"Detail: {detail}"
        )[:1024]
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-demotrade.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: demo ping enqueue failed for account=%s: %s",
            account, exc,
        )
        return None


def enqueue_orphan_reconciliation(
    *,
    account: str,
    symbol: str,
    side: str,
    db_trade_id: Any,
    linked_package_id: Optional[str],
    reason: str = "reconciler",
    headline: str = "🧹 Monitor reconciler — orphaned trade swept",
    classification: Optional[str] = None,
    classification_note: Optional[str] = None,
    priority: str = "high",
) -> Optional[Path]:
    """Drop a Telegram-ready JSON ping for a monitor-loop orphan match.

    Mirrors :func:`enqueue_execution_failure`'s shape so the bot's
    drainer treats both pings the same way. Fired by
    ``order_monitor._reconcile_open_trades`` whenever the DB shows a
    trade as ``status='open'`` but the exchange's open-positions list
    does not include the matching ``(symbol, side)`` row — meaning the
    exchange independently closed the position without the trader
    seeing the close.

    *headline* controls the first line of the notification. Callers
    should pass a context-appropriate headline:
      - ``"🎯 Stop-loss exit detected by reconciler"`` — SL bracket fired
      - ``"🎯 Take-profit exit detected by reconciler"`` — TP bracket fired
      - ``"🔔 Broker close detected by reconciler"`` — linked trade,
        exit price not at SL/TP (manual close or mid-bracket)
      - ``"🧹 Orphaned trade — no package link"`` — genuinely untracked
        (the alarming case; no linked order package)

    *classification* carries the resolved exit reason (``sl``, ``tp``,
    ``broker_close_unclassified``, ``unlinked_orphan``). Surfaced in
    the body so the operator knows whether to investigate or acknowledge.

    The body is operator-actionable (`/last5` will show the linked
    trade) and intentionally lean — no SDK exception payloads, no
    balance values, just identifiers.
    """
    try:
        lines = [
            headline,
            f"Account: {account}",
            f"Symbol: {symbol} | Side: {side}",
            f"DB trade id: {db_trade_id}",
            f"Package: {linked_package_id or '(unlinked)'}",
            f"Reason: {reason}",
        ]
        if classification:
            lines.append(f"Classification: {classification}")
        if classification_note:
            lines.append(f"Note: {classification_note}")
        body = "\n".join(lines)[:1024]
        _append_operator_alert("orphan_reconciliation", priority, body)
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-reconciler.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: orphan-ping enqueue failed for "
            "account=%s symbol=%s db_trade_id=%s: %s",
            account, symbol, db_trade_id, exc,
        )
        return None


def enqueue_exchange_orphan_adoption(
    *,
    account: str,
    symbol: str,
    side: str,
    size: float,
    entry_price: float,
    db_trade_id: Optional[int],
    policy: str,
    note: Optional[str] = None,
    priority: str = "high",
) -> Optional[Path]:
    """Drop a Telegram-ready JSON ping for an EXCHANGE-SIDE orphan
    adoption — the reverse direction of :func:`enqueue_orphan_reconciliation`.

    Forward orphan (existing): DB shows a trade open, exchange doesn't.
    Reverse orphan (this one):  Exchange shows a position, DB doesn't.

    Fired by ``order_monitor._reconcile_orphan_exchange_positions``
    when ``account_open_positions`` reports a Bybit position for which
    there is no matching ``trades`` row with ``status='open'``. The
    2026-05-11 incident (BTCUSDT bybit_2 vwap LONG opened at 07:17:27Z,
    journal row vanished, position remained live on Bybit) is the
    motivating case: without this ping the operator finds out only by
    coincidence that the bot has stopped tracking a real position.

    *policy* is the resolved ORPHAN_POSITION_POLICY (``detect_only`` /
    ``adopt`` / ``close``) so the alert text matches what actually
    happened — e.g. an ``adopt`` ping confirms a new trade row was
    inserted, while ``detect_only`` makes clear that the operator
    must decide.
    """
    try:
        icon = {"adopt": "🪝", "close": "🛑", "detect_only": "👁"}.get(
            policy, "❓"
        )
        lines = [
            f"{icon} Exchange-side orphan position — policy={policy}",
            f"Account: {account}",
            f"Symbol: {symbol} | Side: {side} | Size: {size}",
            f"Entry (Bybit avgPrice): {entry_price}",
        ]
        if db_trade_id is not None:
            lines.append(f"DB trade id (adopted): {db_trade_id}")
        if note:
            lines.append(f"Note: {note}")
        body = "\n".join(lines)[:1024]
        _append_operator_alert("exchange_orphan_adoption", priority, body)
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-exch-orphan.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: exchange-orphan ping enqueue failed for "
            "account=%s symbol=%s side=%s: %s",
            account, symbol, side, exc,
        )
        return None


def enqueue_all_accounts_failed_dispatch(
    *,
    strategy: str,
    symbol: str,
    side: str,
    results: list,
    priority: str = "high",
) -> Optional[Path]:
    """Aggregate ping for "tried to dispatch this signal, NOTHING landed".

    Background — when a strategy fires a signal and every account in
    ``multi_account_execute`` errors (or is below balance / refused
    by the risk gate), the operator sees N per-account pings. If the
    bot is consistently in this state (e.g. after a Bybit ErrCode
    170131 cascade — trade 875 / 876, 2026-05-08), the per-account
    spam mixes with normal noise and the "trader is silent" signal
    is missed.

    This helper emits one high-priority roll-up after each fully-
    failed dispatch round, summarising the failure reasons inline
    so the operator can see at a glance whether it's a transient
    creds issue, a market-wide rejection, or a balance-floor
    exhaustion.

    *results* is the list returned by ``multi_account_execute``.
    Each entry has ``name``, ``error``, ``trade_id`` keys.

    Returns the queued path on success, ``None`` on enqueue failure.
    Never raises — the dispatch round already returned its results.
    """
    try:
        if not results:
            return None
        attempted = len(results)
        placed = sum(1 for r in results if r.get("trade_id") is not None)

        # Separate genuine failures from benign policy-hold / noop results.
        # A policy hold (flip_suppressed_hold_policy, sub-min-qty delta,
        # netting-guard re-entry suppression) is INTENDED behaviour — listing
        # it alongside a credential failure or exchange rejection under a
        # "🚨 ALL accounts FAILED" headline is misleading. The caller
        # (_is_benign_noop guard in multi_account_execute) already suppresses
        # the alert when ALL results are noops; here we split the list so the
        # message only labels policy holds as holds, not failures.
        # Byte-identical to the four-clause closure this replaces — the rule
        # simply lives at module level now so `dead_leg` can import it instead
        # of re-deriving a narrower one (see `is_policy_hold`). A shelved
        # dry_run account / prop mission-skip is a deliberate policy hold, not
        # a failure (operator directive 2026-07-15), and remains covered via
        # `is_expected_dispatch_skip` inside that predicate.
        def _is_hold(err: str) -> bool:
            return is_policy_hold(err)

        genuine = [r for r in results if not _is_hold(str(r.get("error") or ""))]
        held = [r for r in results if _is_hold(str(r.get("error") or ""))]
        n_failed = len(genuine)

        # Build failure lines from genuine failures only. Cap to 5 lines.
        lines = []
        for r in genuine[:5]:
            name = str(r.get("name") or "?")
            err = str(r.get("error") or "no_trade_placed")
            err_short = err[:120] + ("…" if len(err) > 120 else "")
            lines.append(f"  • {name}: {err_short}")
        suppressed = n_failed - len(lines)
        if suppressed > 0:
            lines.append(f"  • … and {suppressed} more")

        # Headline distinguishes "all genuine failures" from "some held by policy".
        if held:
            headline = f"🚨 {n_failed}/{attempted} accounts failed to dispatch"
            held_names = ", ".join(str(r.get("name") or "?") for r in held[:3])
            held_note = f"\nPolicy holds (not failures): {held_names}"
        else:
            headline = "🚨 ALL accounts failed to dispatch"
            held_note = ""

        body = (
            f"{headline}\n"
            f"Strategy: {strategy} | Symbol: {symbol} | Side: {side}\n"
            f"Accounts attempted: {attempted} | Trades placed: {placed}\n"
            "Failures:\n" + "\n".join(lines) + held_note
        )[:1024]
        _append_operator_alert("all_accounts_failed", priority, body)
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-allfail.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: all-accounts-failed enqueue failed for "
            "strategy=%s symbol=%s: %s",
            strategy, symbol, exc,
        )
        return None


def enqueue_stuck_strategy_alert(
    *,
    strategy: str,
    symbol: str,
    order_package_id: str,
    db_trade_id: Any,
    stuck_minutes: int,
    auto_cleared: bool,
    position_alive: bool = False,
    priority: str = "high",
) -> Optional[Path]:
    """Watchdog ping when the strategy-monocle gate has been blocked by a
    single package past its timeframe-scaled threshold.

    Two distinct cases, two messages (the wording was previously a single
    template that read like a reconciler bug even for the benign case —
    the false-alarm fixed here):

    * *position_alive* True — the watchdog cross-checked the exchange and
      the position is **confirmed still open**. This is NOT an orphan: the
      strategy is patiently holding a live trade past 3× its timeframe (a
      wide-TP trend trade legitimately does this). The watchdog deferred —
      it did **not** touch the trade. Informational, ``normal`` priority,
      no "investigate" call to action.
    * *position_alive* False with *auto_cleared* True — the position read
      **flat** at the exchange, so the watchdog force-closed the stale
      package + cascaded the linked row. This IS the last line of defence
      after the orphan reconciler / stuck-linked sweep / monitor() loop all
      missed it, so the "investigate a reconciler skip" call to action
      stands.

    *auto_cleared* is True when the watchdog force-closed the package +
    cascaded the linked trade row in the same tick.
    """
    try:
        if position_alive:
            # Benign — confirmed alive on the exchange; the strategy is
            # holding it, the watchdog took no action. Informational only.
            eff_priority = "normal"
            body = (
                "🔎 Stuck-strategy watchdog (informational — no action)\n"
                f"Strategy: {strategy} | Symbol: {symbol}\n"
                f"Package: {order_package_id}\n"
                f"DB trade id: {db_trade_id}\n"
                f"Held for: {stuck_minutes} min (≥ 3× its timeframe)\n"
                "Status: position CONFIRMED ALIVE on the exchange — the "
                "strategy is patiently holding it. The watchdog deferred and "
                "did NOT touch the trade; it exits on its SL/TP or an "
                "opposing signal. No reconciler issue."
            )[:1024]
        else:
            eff_priority = priority
            verb = "force-cleared" if auto_cleared else "still stuck"
            body = (
                "🚨 Stuck-strategy watchdog\n"
                f"Strategy: {strategy} | Symbol: {symbol}\n"
                f"Package: {order_package_id}\n"
                f"DB trade id: {db_trade_id}\n"
                f"Stuck for: {stuck_minutes} min\n"
                f"Action: {verb}\n"
                "Investigate: the orphan reconciler + stuck-linked sweep "
                "did NOT catch this — possible exchange-side stale "
                "position or reconciler skip path."
            )[:1024]
        _append_operator_alert("stuck_strategy", eff_priority, body)
        payload = {"priority": eff_priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-stuckstrat.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: stuck-strategy enqueue failed for "
            "strategy=%s pkg=%s: %s",
            strategy, order_package_id, exc,
        )
        return None


def enqueue_naked_position_alert(
    *,
    trade_id: Any,
    account: str,
    symbol: str,
    side: str,
    sl: Optional[float],
    tp: Optional[float],
    priority: str = "critical",
) -> Optional[Path]:
    """Drop a Telegram-ready JSON ping for an open trade without valid SL/TP.

    Fired once per trade by ``_check_naked_positions`` in the monitor loop.
    Priority is critical — a live position without SL/TP is unacceptable.
    """
    try:
        sl_str = f"{sl:.4f}" if isinstance(sl, (int, float)) else "NULL"
        tp_str = f"{tp:.4f}" if isinstance(tp, (int, float)) else "NULL"
        body = (
            "🚨 NAKED POSITION — open trade has no valid SL/TP\n"
            f"Trade id: {trade_id}\n"
            f"Account: {account}\n"
            f"Symbol: {symbol} | Side: {side}\n"
            f"stop_loss={sl_str}  take_profit_1={tp_str}\n"
            "Action: check trade on exchange and set SL/TP manually."
        )[:1024]
        _append_operator_alert("naked_position", priority, body)
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-naked-position.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: naked-position ping enqueue failed for "
            "trade_id=%s symbol=%s: %s",
            trade_id, symbol, exc,
        )
        return None


def enqueue_monitor_blindness_alert(
    *,
    order_package_id: Any,
    strategy: str,
    symbol: str,
    reason: str,
    consecutive_ticks: int,
    priority: str = "high",
) -> Optional[Path]:
    """Drop a Telegram-ready ping for an open position whose DYNAMIC exit (the
    strategy ``monitor()``) has been unable to run for several consecutive
    monitor ticks — module unresolvable, no monitor(), monitor() raising, or
    candles persistently unavailable (exit-coverage Phase 3).

    The broker SL/TP backstop (if armed) still protects the position, but its
    primary, dynamic exit (break-even trail / thesis / level-cross / time-stop)
    is dark. Fired once per blind episode by the monitor loop.
    """
    try:
        body = (
            "⚠️ MONITOR BLIND — open position has no live dynamic exit\n"
            f"Order package: {order_package_id}\n"
            f"Strategy: {strategy} | Symbol: {symbol}\n"
            f"Reason: {reason} (for {consecutive_ticks} consecutive ticks)\n"
            "Broker SL/TP backstop (if any) still holds, but monitor()-driven "
            "exits are NOT running.\n"
            "Action: check the strategy module / candle feed for this symbol."
        )[:1024]
        _append_operator_alert("monitor_blindness", priority, body)
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-monitor-blind.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: monitor-blindness ping enqueue failed for "
            "pkg=%s symbol=%s: %s",
            order_package_id, symbol, exc,
        )
        return None


def enqueue_orphan_rollup(
    *,
    suppressed_count: int,
    priority: str = "high",
) -> Optional[Path]:
    """One roll-up ping summarising orphans the per-orphan cap dropped.

    The reconciler caps individual orphan pings per tick to avoid
    flooding the operator when a long-stale DB has accumulated dozens
    of ghosts. Anything past the cap is summarised here.
    """
    try:
        body = (
            "🧹 Monitor reconciler — additional orphans not individually pinged\n"
            f"Suppressed: {suppressed_count} more orphan(s) this tick. "
            f"See /last5 / /packages for the full list."
        )[:1024]
        _append_operator_alert("orphan_rollup", priority, body)
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-reconciler-rollup.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: orphan-rollup enqueue failed "
            "(suppressed=%d): %s",
            suppressed_count, exc,
        )
        return None


# ── Trade lifecycle pings (open / update / close) ───────────────────────────
#
# Spec §4.2 (docs/TELEGRAM-SPEC.md): each trade event is its own message
# with a clear title that draws the eye plus a collapsible details block
# (the "Details ▾" expand) so the feed stays scannable. These go to the
# trader inbox (@bict_trading_bot). Like every other enqueue here they are
# best-effort and never raise — a ping failure must never touch the order
# path. The HTML body is self-titled, so the payload carries
# ``parse_mode: "HTML"`` and the drainer skips the priority prefix.


def _fmt_amount(value: object) -> str:
    """Plain currency, e.g. ``$1,234.50``. ``—`` when unparseable."""
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_signed(value: object) -> str:
    """Signed currency, e.g. ``+$45.00`` / ``-$10.00``. ``—`` when unset."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{'-' if v < 0 else '+'}${abs(v):,.2f}"


def _enqueue_html_ping(body_html: str, *, kind: str, priority: str) -> Optional[Path]:
    """Atomically enqueue a self-titled HTML ping to the trader inbox."""
    try:
        payload = {"priority": priority, "body": body_html, "parse_mode": "HTML"}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-{kind}.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning("execution_diagnostics: %s ping enqueue failed: %s", kind, exc)
        return None


def enqueue_trade_open(
    *,
    account: str,
    strategy: str,
    symbol: str,
    side: str,
    qty: Optional[float],
    entry: Optional[float] = None,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
    risk_usd: Optional[float] = None,
    order_id: Optional[str] = None,
    demo: bool = False,
    priority: str = "normal",
) -> Optional[Path]:
    """``🟢 TRADE OPENED — <symbol> <SIDE>`` + collapsible details.

    ``demo`` prefixes the title with a 🧪 DEMO marker so a demo-account open
    still reads clearly as demo. This is the SINGLE trade-open notification —
    the separate ``*DEMO TRADER* SUBMITTED`` ping was removed (it duplicated
    this one for demo accounts; operator ask 2026-07-09)."""
    try:
        from src.units.ui.telegram_format import Section, kv_block, render_html

        marker = "🧪 DEMO · " if demo else ""
        title = f"{marker}🟢 TRADE OPENED — {symbol} {str(side or '').upper()}"
        body = render_html(
            header=title,
            sections=[Section(summary="Details", body=kv_block([
                ("Account", account),
                ("Strategy", strategy),
                ("Qty", qty),
                ("Entry", _fmt_amount(entry)),
                ("Stop loss", _fmt_amount(sl)),
                ("Take profit", _fmt_amount(tp)),
                ("Risk $", _fmt_amount(risk_usd) if risk_usd is not None else None),
                ("Order id", order_id),
            ]))],
        )
        return _enqueue_html_ping(body, kind="trade-open", priority=priority)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: trade-open ping build failed "
            "(account=%s symbol=%s): %s", account, symbol, exc,
        )
        return None


def enqueue_trade_update(
    *,
    symbol: str,
    changes: Sequence[str],
    account: Optional[str] = None,
    strategy: Optional[str] = None,
    priority: str = "normal",
) -> Optional[Path]:
    """``✏️ TRADE UPDATED — <symbol>`` + collapsible "what changed" details."""
    try:
        from src.units.ui.telegram_format import Section, kv_block, render_html

        title = f"✏️ TRADE UPDATED — {symbol}"
        change_lines = "\n".join(str(c) for c in (changes or [])) or "(no detail)"
        body = render_html(
            header=title,
            sections=[Section(summary="Details", body=(
                kv_block([("Account", account), ("Strategy", strategy)])
                + f"\n\nChanged:\n{change_lines}"
            ))],
        )
        return _enqueue_html_ping(body, kind="trade-update", priority=priority)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: trade-update ping build failed "
            "(symbol=%s): %s", symbol, exc,
        )
        return None


def enqueue_trade_close(
    *,
    symbol: str,
    account: Optional[str] = None,
    strategy: Optional[str] = None,
    entry: Optional[float] = None,
    exit_price: Optional[float] = None,
    pnl: Optional[float] = None,
    r_multiple: Optional[float] = None,
    duration: Optional[str] = None,
    reason: Optional[str] = None,
    priority: str = "normal",
) -> Optional[Path]:
    """``🔴 TRADE CLOSED — <symbol> ±$X`` (✅ win / ❌ loss) + details."""
    try:
        from src.units.ui.telegram_format import Section, kv_block, render_html

        verdict = ""
        if pnl is not None:
            try:
                verdict = " ✅ win" if float(pnl) >= 0 else " ❌ loss"
            except (TypeError, ValueError):
                verdict = ""
        title = f"🔴 TRADE CLOSED — {symbol} {_fmt_signed(pnl)}{verdict}"
        body = render_html(
            header=title,
            sections=[Section(summary="Details", body=kv_block([
                ("Account", account),
                ("Strategy", strategy),
                ("Entry", _fmt_amount(entry)),
                ("Exit", _fmt_amount(exit_price)),
                ("Realised PnL", _fmt_signed(pnl)),
                ("R", r_multiple),
                ("Duration", duration),
                ("Reason", reason),
            ]))],
        )
        return _enqueue_html_ping(body, kind="trade-close", priority=priority)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: trade-close ping build failed "
            "(symbol=%s): %s", symbol, exc,
        )
        return None
