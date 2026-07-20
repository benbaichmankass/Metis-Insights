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


def _append_operator_alert(kind: str, priority: str, body: str) -> None:
    """Append one operator alert to the durable banner-feed ring (best-effort)."""
    try:
        from datetime import datetime, timezone

        OPERATOR_ALERTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "priority": str(priority or "high"),
            "body": str(body or "")[:1024],
        }
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
    """
    try:
        body = (
            "🛑 Position CLOSE failing — won't flatten\n"
            f"Account: {account}\n"
            f"Symbol: {symbol} | Side: {side} | "
            f"Qty: {qty if qty is not None else '?'}\n"
            f"Consecutive close failures: {consecutive}\n"
            f"Last error: {error}\n"
            "The DB row is left OPEN and retried each tick — investigate the "
            "venue/connection; the stuck-strategy watchdog is the backstop."
        )[:1024]
        _append_operator_alert("close_failure", priority, body)
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
        def _is_hold(err: str) -> bool:
            return (
                err.startswith("intent_noop:")
                or err == "intent_sub_min_qty_delta"
                or err.startswith("reentry_suppressed_netting_guard:")
                # A shelved dry_run account / prop mission-skip is a deliberate
                # policy hold, not a failure (operator directive 2026-07-15).
                or is_expected_dispatch_skip(err)
            )

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
