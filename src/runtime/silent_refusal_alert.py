"""Latched alert for an account that READS UP while refusing every signal.

WHY THIS EXISTS (Tier-2, 2026-08-14, `docs/research/WORKPLAN-2026-08-14.md`
Lane 0). Two live checks already watch for a dead strategy, and BOTH measure
something other than "can this account actually get a position on the venue":

  * `/health-review`'s **strategy-silence** check asks whether an enabled
    strategy emits per-tick `*_eval` events. A leg that evaluates fine, signals
    fine, and then has every order refused is NOT silent — it is loudly failing
    at the last step, and grades `ok`.
  * `account_reachability_alert` probes `account_open_positions`, i.e.
    `positions()`. An account whose `positions()` answers while `balance()`
    returns None reads **UP** while refusing every signal routed to it
    (`BL-20260814-REACHABILITY-PROBES-POSITIONS-NOT-BALANCE`).

So "declared live, evaluates, signals, places NOTHING" sits in the gap between
them, and it is not hypothetical. Measured on the live journal 2026-08-14:
`alpaca_live` produced **120 `zero_balance` refusals across 16 separate days**,
3–5/hr, and nothing alerted once. `BL-20260813-ALPACA-BALANCE-NONE-WHILE-ACCOUNT-READS-ACTIVE`
records that its own class was "found incidentally while verifying an unrelated
fix — which is the only reason it was found at all".

**THIS DOES NOT PROBE THE BROKER.** `account_reachability_alert`'s docstring
declares "No new exchange round-trip pattern", and that invariant stands: the
obvious fix — have the reachability probe also call `balance()` — would add a
per-account broker round-trip to a live tick, which is the shape of both June
2026 wedges. This reads the journal the trader has *already written*. It opens
one read-only SQLite connection on its own cadence and no socket at all, so it
cannot slow, block, or wedge the loop it observes.

WHAT IT WATCHES. Per ACCOUNT, not per leg — deliberately. `alpaca_live` routes
16 live strategies; a per-leg alert would have fired 16 pings for one cause,
and an alarm that arrives 16-at-a-time is the desensitized-alarm P1 this repo
already paid for once (`MB-20260719-DATASET-AUDIT-NOISE`). One ping names the
account, the dominant cause, and the affected legs in its detail.

THE STATES ARE NOT COLLAPSED. Three conditions look identical in a count of
placed orders and mean opposite things:

  * **no rows at all** — the account was routed nothing, or the market was
    quiet. *We observed nothing*; it is not graded and never alerts.
  * **rows, all refused** — `signalled_never_placed`. This is the finding.
  * **rows, some placed** — the account works; a refusal rate is a tuning
    question, not an outage.
  * **rows, all refused BY DECLARATION** — `refusing_by_declaration`
    (2026-08-24). The account is `mode: dry_run`, so refusing is the execution
    gate working as designed, not a fault. This state was MISSING and collapsed
    into `signalled_never_placed`: `alpaca_live` (dry_run, `real_money` class,
    16 legs routed) latched `alerting: true` on 2026-08-21 and held it for three
    days on correct behaviour. The repo had already ruled on this exact account
    in `execution_diagnostics.EXPECTED_DISPATCH_SKIP_REASONS` (operator
    directive 2026-07-15); this detector shipped without consulting it. The
    predicate is imported, never re-derived — see `dead_leg`.

The verdict comes from `src.runtime.dead_leg`, the same module the offline
`scripts/ops/dead_leg_audit.py` grades with, so the live alert and the report
can never disagree about one row.

Knobs are cadence/threshold only — no default-off `*_ENABLED` gate in front of
a required observability capability (Prime Directive), and an unparseable value
falls back to the default rather than pausing, so a typo cannot silently switch
the watch off:

  `SILENT_REFUSAL_CHECK_SECONDS`  cadence between reads (default 3600)
  `SILENT_REFUSAL_WINDOW_HOURS`   lookback (default 24)
  `SILENT_REFUSAL_MIN_ROWS`       refusals before it is a pattern (default 5)
  `SILENT_REFUSAL_SKIP`           CSV escape hatch, mirrors ACCOUNT_DOWN_ALERT_SKIP

Latched per `(account, cause)`: one 🔴 on crossing in, one 🟢 on recovery. The
cause is part of the latch key because an account that stops refusing for
`zero_balance` and starts refusing for `venue_max` has a NEW problem, and
folding both into one latch would swallow the second alert entirely.

Best-effort throughout: every path swallows its own exceptions and this runs
once per trader tick. Worst case is a missed or duplicated alert, never a
blocked tick.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.runtime.dead_leg import bucket_for, verdict_for
from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

_STATE_FILENAME = "silent_refusal_alert_state.json"
_LAST_CHECK_KEY = "__last_check__"

#: Refusal causes, matched against `trades.entry_reason` in order. Each is a
#: DISTINCT operator action, which is why they are not one "refused" bucket:
#: an unfunded account, a venue size ceiling, and an unreadable balance are
#: three different fixes. The final `other` bucket is not a match — it is what
#: a cause we have never seen falls into, so a new refusal string surfaces as
#: itself rather than being mislabelled as the nearest known one.
_CAUSE_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("balance_unreadable", r"balance\(\)\s+returned\s+None"),
    ("zero_balance", r"zero_balance"),
    ("venue_max_qty", r"max[_ ]?qty|110007|qty exceeds"),
    ("below_min_qty", r"rejected_too_small|below_venue_min_qty|min[_ ]?qty"),
    ("risk_refused", r"risk_refus|daily_loss|drawdown|exposure"),
    ("sizing_failed", r"sizing_failed"),
)


def _state_path():
    return runtime_logs_dir() / _STATE_FILENAME


def _load_state() -> dict:
    try:
        p = _state_path()
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("silent_refusal_alert: state load failed: %s", exc)
        return {}


def _save_state(state: dict) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as exc:  # noqa: BLE001
        logger.debug("silent_refusal_alert: state save failed: %s", exc)


def _int_knob(name: str, default: int, *, minimum: int = 0) -> int:
    """Read an int knob, falling back to the DEFAULT on garbage.

    Never falls back to 0/disabled: a typo in a cadence must not silently
    switch off the only thing watching for this failure class.
    """
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        val = int(float(raw))
    except (TypeError, ValueError):
        return default
    return max(minimum, val)


def _skip_set() -> frozenset:
    raw = os.environ.get("SILENT_REFUSAL_SKIP", "") or ""
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def classify_cause(entry_reason: Optional[str]) -> str:
    """Bucket one `entry_reason` string into a refusal cause.

    Returns `"unknown"` for a null/blank reason (**we have no reason text**,
    which is not the same as a reason we did not recognise) and `"other"` for a
    non-empty reason that matched no pattern. Keeping those apart matters: the
    first is a journal gap, the second is a refusal string worth adding here.
    """
    if entry_reason is None or not str(entry_reason).strip():
        return "unknown"
    text = str(entry_reason)
    for name, pattern in _CAUSE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    return "other"


def _read_window(db_path: str, hours: int) -> List[sqlite3.Row]:
    """Rows in the window. Read-only, SELECT only, no broker call."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT account_id, strategy_name, status, entry_reason
              FROM trades
             WHERE is_backtest = 0
               AND COALESCE(created_at, timestamp) >= datetime('now', ?)
            """,
            (f"-{int(hours)} hours",),
        ).fetchall()
    finally:
        conn.close()


def assess(rows: List[Any], *, min_rows: int) -> Dict[str, Dict[str, Any]]:
    """Grade each account in `rows`. Pure — no I/O, no env, no notification.

    An account appears in the result only if it produced rows in the window;
    an account absent from `rows` was NOT observed and is deliberately absent
    from the output rather than being graded healthy.
    """
    acc: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        aid = str((r["account_id"] if not isinstance(r, dict) else r.get("account_id")) or "?")
        status = r["status"] if not isinstance(r, dict) else r.get("status")
        strategy = r["strategy_name"] if not isinstance(r, dict) else r.get("strategy_name")
        reason = r["entry_reason"] if not isinstance(r, dict) else r.get("entry_reason")

        a = acc.setdefault(aid, {
            "account_id": aid, "placed": 0, "refused": 0, "other": 0,
            "policy_skipped": 0, "causes": {}, "strategies": set(),
        })
        # The REASON is passed so a declared policy skip (a dry_run-shelved
        # account) is separated from a real refusal. Without it every deliberate
        # skip counts as a fault — see the module docstring.
        bucket = bucket_for(status, reason)
        a[bucket] += 1
        if bucket == "refused":
            cause = classify_cause(reason)
            a["causes"][cause] = a["causes"].get(cause, 0) + 1
            if strategy:
                a["strategies"].add(str(strategy))

    out: Dict[str, Dict[str, Any]] = {}
    for aid, a in acc.items():
        a["total_rows"] = (
            a["placed"] + a["refused"] + a["other"] + a["policy_skipped"])
        a["verdict"] = verdict_for(a)
        a["strategies"] = sorted(a["strategies"])
        # The dominant cause names the fix. Ties break on the cause name so the
        # latch key is stable across ticks — an alternating tie would otherwise
        # re-fire the alert every cadence window.
        a["cause"] = (
            max(sorted(a["causes"]), key=lambda c: a["causes"][c])
            if a["causes"] else None
        )
        # `min_rows` is what separates a PATTERN from a single bad order. Below
        # it the account is still reported (with its verdict) but never alerts.
        a["alerting"] = bool(
            a["verdict"] == "signalled_never_placed" and a["refused"] >= min_rows
        )
        # Why we are NOT alerting is itself a state worth naming: "the account
        # is fine", "the account is switched off", and "too few rows to call it
        # a pattern" are three different facts, and collapsing them into a bare
        # False is what hid the dry_run case in the first place.
        if a["alerting"]:
            a["alert_disposition"] = "alerting"
        elif a["verdict"] == "refusing_by_declaration":
            a["alert_disposition"] = "suppressed_declared_dry_run"
        elif a["verdict"] == "signalled_never_placed":
            a["alert_disposition"] = "below_min_rows"
        else:
            a["alert_disposition"] = "not_a_finding"
        out[aid] = a
    return out


def _send_alert(message: str) -> None:
    """One Telegram + one typed WARNING push — the same shape (and the same
    loud channel) `account_reachability_alert` uses, so this lands beside its
    sibling rather than inventing a second alert style for one class."""
    try:
        from src.runtime.notify import send_telegram_direct
        send_telegram_direct(message, parse_mode=None, mirror_to_fcm=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("silent_refusal_alert: telegram send failed: %s", exc)
    try:
        from src.runtime.mobile_push import publish_event
        from src.runtime.mobile_push.event_kinds import WARNING
        publish_event(WARNING, {"text": message})
    except Exception as exc:  # noqa: BLE001
        logger.debug("silent_refusal_alert: fcm WARNING publish failed: %s", exc)


_CAUSE_HINTS = {
    "balance_unreadable": (
        "balance() returned None while the account itself may read healthy — "
        "check /api/diag/broker_account_status?account_id={acct}, which "
        "resolves the same credentials and reports the venue's own flags."),
    "zero_balance": (
        "the account has no funded balance to size against, so every signal "
        "is refused before it reaches the venue. This is a FUNDING state, not "
        "a fault — but the account is contributing nothing while it holds."),
    "venue_max_qty": (
        "orders are exceeding the venue's max order size. Check the sizing "
        "path's max_qty clamp for this symbol."),
    "below_min_qty": (
        "orders are sizing below the venue minimum — equity or risk-per-trade "
        "may be too small for this instrument."),
    "risk_refused": (
        "the risk manager is refusing every order. Check the account's daily "
        "loss / drawdown / exposure state."),
}


def _describe(a: Dict[str, Any], hours: int) -> str:
    acct = a["account_id"]
    legs = a["strategies"]
    leg_txt = ", ".join(legs[:6]) + (f" (+{len(legs) - 6} more)" if len(legs) > 6 else "")
    hint = _CAUSE_HINTS.get(a["cause"] or "", "")
    return (
        f"\U0001F534 [ALERT] Account placing NOTHING: {acct}\n"
        f"{a['refused']} order(s) refused in the last {hours}h and ZERO reached "
        f"the exchange. The account may read reachable — positions() answering "
        f"is not evidence it can trade.\n"
        f"Dominant cause: {a['cause']} ({a['causes'].get(a['cause'], 0)} of "
        f"{a['refused']})\n"
        f"Legs affected: {leg_txt or '—'}\n"
        + (hint.format(acct=acct) if hint else
           "Run scripts/ops/dead_leg_audit.py for the per-leg breakdown.")
    )


def silent_accounts() -> Dict[str, Dict[str, Any]]:
    """`{account_id: latch}` for accounts currently LATCHED as placing nothing.

    Read-only view for the review skills, mirroring
    `account_reachability_alert.down_accounts()`. Never raises.
    """
    try:
        state = _load_state()
        return {
            aid: st for aid, st in state.items()
            if aid != _LAST_CHECK_KEY and isinstance(st, dict) and st.get("alerting")
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("silent_refusal_alert: silent_accounts failed: %s", exc)
        return {}


def run_silent_refusal_check(
    *,
    now: Optional[datetime] = None,
    rows: Optional[List[Any]] = None,
    force: bool = False,
) -> dict:
    """One tick. Cadence-gated internally; call once per trader tick.

    Returns a small summary dict (`checked`, `alerted`, `recovered`,
    `assessed`). `rows` is a test seam — passing it skips the DB read.
    """
    now = now or datetime.now(timezone.utc)
    interval = _int_knob("SILENT_REFUSAL_CHECK_SECONDS", 3600)
    hours = _int_knob("SILENT_REFUSAL_WINDOW_HOURS", 24, minimum=1)
    min_rows = _int_knob("SILENT_REFUSAL_MIN_ROWS", 5, minimum=1)

    state = _load_state()
    if interval <= 0:
        return {"checked": False, "reason": "paused"}
    if not force and rows is None:
        last = state.get(_LAST_CHECK_KEY)
        if last:
            try:
                prev = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                if prev.tzinfo is None:
                    prev = prev.replace(tzinfo=timezone.utc)
                if (now - prev).total_seconds() < interval:
                    return {"checked": False, "reason": "cadence"}
            except (TypeError, ValueError):
                pass  # unparseable stamp ⇒ check now rather than never

    if rows is None:
        try:
            from src.utils.paths import trade_journal_db_path
            rows = _read_window(str(trade_journal_db_path()), hours)
        except Exception as exc:  # noqa: BLE001 — a read failure is not a finding
            # Deliberately NOT treated as "no refusals". We could not look, so
            # nothing is latched, nothing recovers, and the next tick retries.
            logger.warning("silent_refusal_alert: journal read failed: %s", exc)
            return {"checked": False, "reason": "read_failed"}

    assessed = assess(rows, min_rows=min_rows)
    state[_LAST_CHECK_KEY] = now.isoformat()
    alerted: List[str] = []
    recovered: List[str] = []
    skip = _skip_set()

    for aid, a in assessed.items():
        if aid in skip:
            continue
        prev = state.get(aid) if isinstance(state.get(aid), dict) else {}
        was_alerting = bool(prev.get("alerting"))
        prev_cause = prev.get("cause")
        # The latch key is (account, cause): a NEW cause on an already-latched
        # account is a NEW problem and must not be swallowed by the old latch.
        if a["alerting"] and (not was_alerting or prev_cause != a["cause"]):
            _send_alert(_describe(a, hours))
            alerted.append(aid)
        elif was_alerting and not a["alerting"]:
            # The recovery message must name the reason it recovered. Since
            # 2026-08-24 an account can leave the alerting state WITHOUT having
            # placed anything — a declared dry_run refuses everything, correctly
            # — and the old wording ("is placing orders again (0 reached the
            # exchange)") states the opposite of what happened, on its own
            # numbers. A recovery ping nobody can trust is worse than none.
            if a["verdict"] == "refusing_by_declaration":
                body = (f"its refusals are all DECLARED policy skips "
                        f"(dry_run / prop session) — the account is switched "
                        f"off, not broken. {a['policy_skipped']} skipped in "
                        f"the last {hours}h, none placed.")
            else:
                body = (f"{a['placed']} order(s) reached the exchange in the "
                        f"last {hours}h.")
            _send_alert(f"\U0001F7E2 [OK] {aid} is no longer placing nothing: {body}")
            recovered.append(aid)
        state[aid] = {
            "alerting": a["alerting"], "cause": a["cause"],
            "refused": a["refused"], "placed": a["placed"],
            "verdict": a["verdict"], "updated_at": now.isoformat(),
        }

    _save_state(state)
    return {"checked": True, "alerted": alerted, "recovered": recovered,
            "assessed": len(assessed)}


__all__ = ["run_silent_refusal_check", "silent_accounts", "assess",
           "classify_cause"]
