"""Sizing balance for a PROP account, which has no broker API by design.

WHY THIS EXISTS. `Coordinator._default_balance_fetcher` sources a sizing balance
from the broker. A prop account (Breakout manual bridge) **has no broker socket
at all** — it "executes" by emitting a Telegram/FCM ticket a supervised assistant
places — so that lookup can only ever return ``None``, and the fetcher then
raises::

    balance() returned None for breakout_1 (exchange=breakout):
    API error or credentials missing — account unreachable

**Every clause of that message is wrong for a prop account.** There is no API to
error, no credentials to miss, and the account is not unreachable. Measured
2026-08-13: this is 5 of the 7 lifetime rejections on `trend_donchian_sol` and it
is why the leg has never emitted a prop ticket.

The designed source is the operator's own report — ``POST /api/bot/prop/report``
with ``kind: "account_status"`` — persisted to ``prop_account_status``. That is
the same snapshot the rule-distance guard and the balance nudge already read, so
sizing now agrees with them rather than asking a socket that does not exist.

FOUR STATES, NEVER COLLAPSED (CLAUDE.md § "Collapsed states"). A sizing input on
a money path must distinguish *we could not look* from *we looked and there is
nothing* — they call for opposite operator actions (fix the reader vs send a
balance), and the existing `_status_age_hours` helper collapses both to ``None``,
which is why this does not reuse it:

  ``ok``      — fresh snapshot; size off it
  ``stale``   — snapshot exists but older than the freshness threshold
  ``absent``  — no snapshot has ever been reported
  ``error``   — the read itself failed

Only ``ok`` yields a number. The other three refuse, each with its own message —
**this module can never cause a trade to be sized off a guess.** The single
behaviour change is that a prop account with a fresh operator-reported balance
can now size, which is the path the manual bridge was built for.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Same default and env key the balance nudge and the periodic status request
# use, so "fresh enough to size" and "fresh enough not to nag" never disagree.
_DEFAULT_MAX_AGE_H = 24.0
_ENV_MAX_AGE = "PROP_STATUS_REQUEST_MAX_AGE_HOURS"


def max_age_hours() -> float:
    """Freshness threshold in hours. ``<= 0`` disables the staleness check.

    An unparseable value falls back to the default rather than disabling the
    check — a typo must not silently widen what counts as a fresh balance on a
    money path.
    """
    raw = os.environ.get(_ENV_MAX_AGE)
    try:
        return float(raw) if raw not in (None, "") else _DEFAULT_MAX_AGE_H
    except (TypeError, ValueError):
        return _DEFAULT_MAX_AGE_H


def _age_hours(row: Dict[str, Any]) -> Optional[float]:
    ts = row.get("reported_at") or row.get("created_at")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0)


def prop_sizing_balance(account_id: str) -> Tuple[str, Optional[float], Dict[str, Any]]:
    """``(state, balance_usd, meta)`` for a prop account's sizing basis.

    ``balance_usd`` is non-None **only** when ``state == "ok"``.

    Prefers ``equity`` over ``balance``: equity is the live account value the
    prop firm's drawdown rules are measured against, so sizing off it agrees
    with the rule-distance guard. Falls back to ``balance`` when equity was not
    reported, and records which was used — a sizing input must state its own
    derivation.
    """
    meta: Dict[str, Any] = {"account_id": account_id,
                            "max_age_hours": max_age_hours()}
    if not account_id:
        return "absent", None, {**meta, "reason": "no account_id"}

    try:
        from src.prop import prop_journal
        row = prop_journal.latest_account_status(account_id)
    except Exception as exc:  # noqa: BLE001 — a read failure is its own state
        logger.warning("prop_balance: status read failed for %s: %s",
                       account_id, exc)
        return "error", None, {**meta, "reason": f"{type(exc).__name__}: {exc}"}

    if not row:
        return "absent", None, {**meta, "reason": "no prop_account_status row"}

    age = _age_hours(row)
    meta["age_hours"] = age
    limit = meta["max_age_hours"]
    # age is None => the row carries no readable timestamp. Treat as stale
    # rather than fresh: an undateable snapshot cannot be shown to be current,
    # and the fail-safe direction on a sizing input is to refuse.
    if limit > 0 and (age is None or age >= limit):
        return "stale", None, {**meta,
                               "reason": ("undateable snapshot" if age is None
                                          else f"snapshot {age:.1f}h old")}

    for key in ("equity", "balance"):
        raw = row.get(key)
        if raw in (None, ""):
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if val > 0:
            return "ok", val, {**meta, "source": key}

    return "absent", None, {**meta,
                            "reason": "snapshot carries no positive equity/balance"}


def refusal_message(state: str, account_id: str, meta: Dict[str, Any]) -> str:
    """The operator-facing cause, naming what to DO about it.

    Deliberately never says "API error or credentials missing" — a prop account
    has neither, and a diagnostic that names a cause no code path tested is the
    failure this module exists to remove.
    """
    if state == "stale":
        return (f"prop_balance_stale: {account_id} last reported "
                f"{meta.get('reason')} (limit {meta.get('max_age_hours')}h) — "
                f"send a fresh `bal <balance> <equity>` to re-arm sizing")
    if state == "absent":
        return (f"prop_balance_absent: {account_id} has no operator-reported "
                f"account status ({meta.get('reason')}) — this account has NO "
                f"broker API by design; send `bal <balance> <equity>`")
    if state == "error":
        return (f"prop_balance_unreadable: could not read {account_id}'s "
                f"account status ({meta.get('reason')}) — this is a reader "
                f"fault, NOT a missing balance")
    return f"prop_balance_{state}: {account_id}"
