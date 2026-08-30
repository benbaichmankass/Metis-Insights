"""T+1 cash-account settlement basis — ONE module owns "how much may we spend?".

WHY THIS EXISTS
---------------
On a CASH account, proceeds from a sale are not usable to buy again until they
SETTLE (T+1 *trading* days). Buying with unsettled funds is a good-faith
violation, and a repeat pattern gets the account restricted by the broker.
``alpaca_live`` is a cash account (``capacity.multiplier == 1``, measured
2026-08-29) holding ~$200, so the whole balance recycles on every trade and a
violation is one unsettled buy away. Backlog:
``BL-20260823-ALPACA-CASH-ACCOUNT-SETTLEMENT-UNMODELLED``.

THE SIZING PATH ALREADY EXISTS; ONLY THE SETTLEMENT TERM WAS MISSING.
``Coordinator.multi_account_execute`` already calls ``client.buying_power()``
and feeds it to ``position_size(available_usd=...)``. This module supplies a
SETTLED basis for that same slot — it does not add an order path.

⚠️ WE DO NOT KNOW WHETHER ALPACA ALREADY NETS UNSETTLED PROCEEDS OUT OF
``buying_power``, AND WE COULD NOT FIND OUT.
Measured 2026-08-29 on the live account: ``cash == buying_power ==
regt_buying_power == equity == 200.10`` — all four identical, because the
account holds nothing and has nothing unsettled, so the state that would
distinguish them does not exist. Both paper mirrors are MARGIN accounts and
cannot exhibit cash-account settlement behaviour either. So this module is
built NOT to depend on the answer:

    basis = min(venue_buying_power, venue_cash - our_unsettled_proceeds)

* If Alpaca DOES net it out, both terms equal settled cash and ``min`` is a
  no-op.
* If Alpaca does NOT, our subtraction supplies the correction.
* If a human sold manually and our journal never saw it, our ``unsettled`` is
  too low — but the venue term still binds, so ``min`` degrades rather than
  lying.

``min`` is therefore correct under every combination of the two unknowns,
which is the point: it lets the gate ship before the broker's semantics are
established, instead of waiting on a state the account has never been in.

⚠️ NEVER FALL BACK TO ``cash``. ``AlpacaClient.buying_power`` resolves
``regt_buying_power -> buying_power -> cash``, and ``cash`` is the one figure
that may INCLUDE unsettled proceeds. An absent preferred key must never widen
the basis — that is the permissive-fallback shape
``BL-20260707-ALPACA-BALANCE-TRUTHINESS`` records one level up (there a
genuine ``0.0`` fell through to a less authoritative key; here an absent key
would fall through to a WIDER one).

SETTLEMENT IS COUNTED IN TRADING DAYS, AND THIS REPO HAS NO CALENDAR.
``src/runtime/market_hours.py`` says so in its own docstring: *"No US holiday
calendar and no half-days — a holiday reads 'open' … revisit before any
equities strategy goes paper-live."* Counting T+1 with that module would credit
funds as settled a day EARLY across a holiday — manufacturing the very
violation this gate exists to prevent. So trading days come from the venue's
own calendar (Alpaca ``/v2/calendar``), and when that cannot be read we fall
back to a rule that is conservative BY CONSTRUCTION rather than approximately
right (see ``_CONSERVATIVE_HOLD_DAYS``).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence

# When the trading calendar is unavailable we cannot compute "T+1 trading
# day", so we hold proceeds for a fixed number of CALENDAR days instead.
#
# The value is chosen to be an UPPER bound on T+1-trading-day, not an average:
# a Friday sale settles Monday (T+3 calendar), and a Monday holiday pushes that
# to Tuesday (T+4). Four days therefore covers the worst ordinary case. It is
# deliberately NOT tuned — being late to credit funds costs a little capital
# efficiency, while being early costs a good-faith violation, and those are not
# symmetric. A longer holiday run (a Thu-Fri closure) could still exceed it,
# which is exactly why this is the FALLBACK and the venue calendar is primary.
_CONSERVATIVE_HOLD_DAYS = 4


@dataclass(frozen=True)
class SettlementBasis:
    """The answer, plus WHICH evidence produced it.

    ``state`` is never collapsed — a reader must be able to tell "we measured
    it" from "we could not look", because the two demand opposite responses
    (trade vs. hold). ``basis_usd`` is ``None`` whenever we could not establish
    a number; it is NEVER 0.0 as a stand-in for unknown, since 0.0 is a real,
    meaningful reading (a fully-invested account) and conflating them would
    make an unreadable account indistinguishable from a spent one.
    """

    state: str
    basis_usd: Optional[float]
    unsettled_usd: Optional[float]
    venue_cash: Optional[float]
    venue_buying_power: Optional[float]
    detail: str = ""

    @property
    def is_measured(self) -> bool:
        return self.state == "measured"


# The states, declared once so producer and consumer cannot drift.
#   measured              - venue figures AND our unsettled total were read;
#                           basis_usd is the settled basis.
#   estimated_no_calendar - everything read EXCEPT the trading calendar, so
#                           unsettled was computed on the conservative
#                           calendar-day rule. Usable, but wider than truth.
#   journal_unreadable    - we could not read our own fills. We do NOT know
#                           what is unsettled, so we cannot correct the venue
#                           figure. NOT the same as "nothing is unsettled".
#   venue_unreadable      - no venue cash/buying-power figure. There is nothing
#                           to subtract FROM; the caller must fall back to its
#                           own conservative buffer, never to `cash`.
STATES = (
    "measured",
    "estimated_no_calendar",
    "journal_unreadable",
    "venue_unreadable",
)


def settlement_date(
    trade_day: date,
    trading_days: Optional[Sequence[date]],
) -> Optional[date]:
    """The day proceeds from a sale on *trade_day* become spendable (T+1).

    *trading_days* is the venue's own calendar. ``None`` (or a calendar that
    does not reach past *trade_day*) returns ``None`` — **we could not look**,
    which the caller must handle as such rather than guessing a date. Returning
    a guessed date here is precisely how a holiday would credit funds early.
    """
    if not trading_days:
        return None
    later = [d for d in trading_days if d > trade_day]
    if not later:
        # The calendar exists but does not extend past this trade — same
        # epistemic state as having no calendar, and must not be rounded to
        # "the next calendar day".
        return None
    return min(later)


def conservative_settlement_date(trade_day: date) -> date:
    """Calendar-day fallback: late by construction, never early."""
    return trade_day + timedelta(days=_CONSERVATIVE_HOLD_DAYS)


@dataclass(frozen=True)
class UnsettledTotal:
    """The unsettled sum, plus whether it can be trusted as COMPLETE.

    ``ungradeable`` counts rows whose proceeds could not be parsed. That is not
    a rounding detail: a skipped row REMOVES money from the unsettled total and
    therefore WIDENS the spendable basis, so silently dropping it fails in the
    permissive direction. When it is non-zero the total is a LOWER BOUND, and
    the caller must degrade to "unknown" rather than spend against it.
    """

    total_usd: float
    used_calendar: bool
    ungradeable: int

    @property
    def is_complete(self) -> bool:
        return self.ungradeable == 0


def unsettled_from_sales(
    sales: Iterable[tuple[date, float]],
    *,
    now: datetime,
    trading_days: Optional[Sequence[date]],
) -> UnsettledTotal:
    """Total proceeds not yet settled as of *now*.

    *sales* is ``(trade_day, proceeds_usd)``. The result carries its own
    completeness so the caller grades it rather than inferring it.

    A row whose proceeds cannot be parsed is COUNTED as ungradeable, not
    quietly dropped — see :class:`UnsettledTotal`. A non-positive figure is
    different: a zero or negative "sale" contributes nothing to settle and is
    legitimately skipped without poisoning the total.
    """
    today = now.astimezone(timezone.utc).date()
    total = 0.0
    ungradeable = 0
    used_calendar = bool(trading_days)
    for trade_day, proceeds in sales:
        try:
            amount = float(proceeds)
        except (TypeError, ValueError):
            ungradeable += 1
            continue
        if amount != amount or amount in (float("inf"), float("-inf")):
            ungradeable += 1
            continue
        if amount <= 0:
            continue
        settles = settlement_date(trade_day, trading_days)
        if settles is None:
            used_calendar = False
            settles = conservative_settlement_date(trade_day)
        if settles > today:
            total += amount
    return UnsettledTotal(
        total_usd=total, used_calendar=used_calendar, ungradeable=ungradeable
    )


def settled_basis(
    *,
    venue_cash: Optional[float],
    venue_buying_power: Optional[float],
    unsettled_usd: Optional[float],
    used_calendar: bool = True,
) -> SettlementBasis:
    """The spendable basis for a CASH account, and the evidence behind it.

    Pure — no I/O, no clock, no config. The policy is arguable in tests rather
    than against a live position, which is the lesson of
    ``BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG``.
    """
    if venue_cash is None and venue_buying_power is None:
        return SettlementBasis(
            state="venue_unreadable",
            basis_usd=None,
            unsettled_usd=unsettled_usd,
            venue_cash=venue_cash,
            venue_buying_power=venue_buying_power,
            detail=(
                "no venue cash or buying-power figure; caller must use its own "
                "conservative buffer and must NOT fall back to `cash`"
            ),
        )

    if unsettled_usd is None:
        # We hold a venue figure but cannot correct it. Report the venue's own
        # number and say plainly that the settlement term is missing — do not
        # pretend the correction was applied.
        candidates = [v for v in (venue_buying_power, venue_cash) if v is not None]
        return SettlementBasis(
            state="journal_unreadable",
            basis_usd=min(candidates),
            unsettled_usd=None,
            venue_cash=venue_cash,
            venue_buying_power=venue_buying_power,
            detail=(
                "could not read our own fills, so the unsettled total is UNKNOWN "
                "— this is not evidence that nothing is unsettled"
            ),
        )

    corrected = None
    if venue_cash is not None:
        corrected = venue_cash - unsettled_usd
    candidates = [v for v in (venue_buying_power, corrected) if v is not None]
    basis = min(candidates)
    # A negative basis means more is unsettled than the venue reports as cash
    # (a manual withdrawal, or a journal row the venue never saw). Clamp at 0:
    # "you may spend nothing" is the honest reading, and a negative available
    # figure would be nonsense to the sizer.
    basis = max(basis, 0.0)
    return SettlementBasis(
        state="measured" if used_calendar else "estimated_no_calendar",
        basis_usd=basis,
        unsettled_usd=unsettled_usd,
        venue_cash=venue_cash,
        venue_buying_power=venue_buying_power,
        detail=(
            "min(venue buying_power, venue cash - unsettled)"
            + ("" if used_calendar else "; unsettled used the conservative calendar-day rule")
        ),
    )


# ---------------------------------------------------------------------------
# I/O half — kept below and separate from the pure decision above, so the
# policy stays arguable in tests without a DB or a broker.
# ---------------------------------------------------------------------------

def _parse_closed_at(raw: object) -> Optional[date]:
    """``trades.closed_at`` as a UTC date, or ``None`` if it cannot be dated.

    The column holds BOTH ISO strings and raw epoch-ms strings — the
    reconciler-filled close path writes the latter, which is the same trap that
    produced the "0 closed trades in 24h while lifetime is non-zero" bug on
    ``/performance``. A row we cannot date is returned as ``None`` and counted
    by the caller as ungradeable, never silently dropped.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit():
        try:
            ms = int(text)
        except ValueError:
            return None
        # Epoch-ms vs epoch-s: anything past ~2001 in ms is > 1e12.
        seconds = ms / 1000.0 if ms > 1_000_000_000_000 else float(ms)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(
            timezone.utc
        ).date()
    except ValueError:
        return None


def recent_sales(
    account_id: str,
    *,
    lookback_days: int = 10,
    now: Optional[datetime] = None,
    db_path: Optional[str] = None,
) -> Optional[list[tuple[Optional[date], float]]]:
    """Closed trades for *account_id* whose proceeds may still be unsettled.

    Returns ``None`` on ANY read failure — we could not look, which is not the
    same as "this account has sold nothing" and must not be rounded to it.

    A row that cannot be dated yields ``(None, proceeds)``; the settlement pass
    treats an undateable row as ungradeable rather than assuming it is old
    enough to have settled, which would be the permissive direction.
    """
    import sqlite3

    from src.utils.paths import trade_journal_db_path

    now = now or datetime.now(timezone.utc)
    path = db_path or str(trade_journal_db_path())
    cutoff = (now - timedelta(days=max(int(lookback_days), 1))).date().isoformat()
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return None
    try:
        cur = conn.execute(
            """
            SELECT closed_at, exit_price, position_size
              FROM trades
             WHERE account_id = ?
               AND status = 'closed'
               AND exit_price IS NOT NULL
               AND position_size IS NOT NULL
               AND closed_at IS NOT NULL
            """,
            (account_id,),
        )
        rows = cur.fetchall()
    except sqlite3.Error:
        return None
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass

    out: list[tuple[Optional[date], float]] = []
    for closed_at, exit_price, qty in rows:
        day = _parse_closed_at(closed_at)
        if day is not None and day.isoformat() < cutoff:
            continue  # long settled; outside the window we care about
        try:
            proceeds = float(exit_price) * float(qty)
        except (TypeError, ValueError):
            out.append((day, float("nan")))  # ungradeable, and stays visible
            continue
        out.append((day, proceeds))
    return out


def settlement_mode() -> str:
    """``off`` / ``annotate`` (default) / ``apply``.

    A ``*_MODE`` knob, not a default-off ``*_ENABLED`` gate (Prime Directive).
    An unparseable value falls back to ``annotate`` — a typo must not silently
    switch the observation off, and certainly must not switch an order-path
    change on.
    """
    import os

    raw = (os.environ.get("ALPACA_CASH_SETTLEMENT_MODE") or "").strip().lower()
    return raw if raw in ("off", "annotate", "apply") else "annotate"


def settlement_accounts() -> frozenset[str]:
    """Accounts the ``apply`` mode may actually bind.

    ⚠️ AN EMPTY ALLOWLIST MEANS **NONE**, deliberately the OPPOSITE of
    ``CONVICTION_SIZING_ACCOUNTS`` / ``NETTING_ATTRIBUTION_ACCOUNTS``, which
    read empty as ALL — a polarity this repo's own CLAUDE.md calls "not a safe
    default, it is the widest one". Those widen a size and a DB write; this one
    CONSTRAINS a live order's size on a real-money account, so an unset
    variable must not arm it everywhere. It copies
    ``PROTECTION_REASSERT_ACCOUNTS``'s polarity on purpose. Do not "harmonise".
    """
    import os

    raw = os.environ.get("ALPACA_CASH_SETTLEMENT_ACCOUNTS") or ""
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


def may_apply(account_id: Optional[str]) -> bool:
    """Whether ``apply`` may bind this account. An unnamed account never can."""
    if not account_id:
        return False
    return settlement_mode() == "apply" and account_id in settlement_accounts()


def resolve_for_account(
    account_id: str,
    client: object,
    *,
    now: Optional[datetime] = None,
) -> SettlementBasis:
    """Tie the readers to the pure decision for one account.

    COST, stated because an unbounded broker call on an order path is the shape
    of both June 2026 wedges: this makes at most TWO broker reads, and only
    when an order is actually being placed for this account — it is NOT on the
    tick loop. ``/v2/account`` must be fresh (cash and buying power move with
    every fill, so caching them would defeat the gate), while ``/v2/calendar``
    is cached for the process because a past window's trading days never
    change.

    Every failure resolves to a NAMED state, never to a permissive number.
    """
    now = now or datetime.now(timezone.utc)

    venue_cash: Optional[float] = None
    venue_bp: Optional[float] = None
    status = None
    try:
        status = client.account_status()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - a broker read must not break dispatch
        status = None
    if isinstance(status, dict):
        cap = status.get("capacity")
        if isinstance(cap, dict):
            for key, target in (("cash", "cash"), ("buying_power", "bp")):
                raw = cap.get(key)
                if raw is None:
                    continue
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    continue
                if target == "cash":
                    venue_cash = value
                else:
                    venue_bp = value

    sales = recent_sales(account_id, now=now)
    if sales is None:
        return settled_basis(
            venue_cash=venue_cash,
            venue_buying_power=venue_bp,
            unsettled_usd=None,
        )

    trading_days = _cached_trading_days(client, now)
    graded: list[tuple[date, float]] = []
    undateable = 0
    for day, proceeds in sales:
        if day is None:
            undateable += 1
            continue
        graded.append((day, proceeds))

    tally = unsettled_from_sales(graded, now=now, trading_days=trading_days)
    if undateable or not tally.is_complete:
        # We hold SOME of the picture. Reporting the partial sum as if it were
        # the whole would let the sizer spend against rows we could not grade.
        return settled_basis(
            venue_cash=venue_cash,
            venue_buying_power=venue_bp,
            unsettled_usd=None,
        )
    return settled_basis(
        venue_cash=venue_cash,
        venue_buying_power=venue_bp,
        unsettled_usd=tally.total_usd,
        used_calendar=tally.used_calendar,
    )


_CALENDAR_CACHE: dict[str, Optional[list[date]]] = {}


def _cached_trading_days(client: object, now: datetime) -> Optional[list[date]]:
    """Trading days around *now*, cached per process per window.

    A FAILED read is cached too — under a distinct key — so a wedged endpoint
    is not re-probed on every order, while a later process still retries. The
    cached value is the raw answer including ``None``; a ``None`` here means
    "we could not look", and the caller's conservative fallback handles it.
    """
    start = (now - timedelta(days=14)).date().isoformat()
    end = (now + timedelta(days=14)).date().isoformat()
    key = f"{start}:{end}"
    if key in _CALENDAR_CACHE:
        return _CALENDAR_CACHE[key]
    days: Optional[list[date]] = None
    getter = getattr(client, "trading_days", None)
    if callable(getter):
        try:
            days = getter(start, end)
        except Exception:  # noqa: BLE001
            days = None
    _CALENDAR_CACHE[key] = days
    return days


def record_observation(
    *,
    account_id: str,
    basis: SettlementBasis,
    available_usd: Optional[float],
    applied: bool,
) -> None:
    """Append one soak row. Best-effort; never raises into dispatch.

    The row carries the EFFECTIVE outcome (``applied``) beside the global
    ``mode`` and an ``apply_scope``, so a held-back row can never read as an
    applied one — the distinction ``NETTING_ATTRIBUTION_MODE`` had to be
    corrected to make. ``would_have_reduced_usd`` is the review figure: how
    much the gate WOULD have taken off the sizer's basis, which is what an
    operator needs before flipping to ``apply``.
    """
    import json
    import os

    try:
        from src.utils.paths import runtime_logs_dir

        mode = settlement_mode()
        allow = settlement_accounts()
        scope = (
            "allowlisted"
            if account_id in allow
            else ("not_allowlisted" if mode == "apply" else "not_apply")
        )
        would_reduce = None
        if basis.basis_usd is not None and available_usd is not None:
            would_reduce = round(max(available_usd - basis.basis_usd, 0.0), 4)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "account_id": account_id,
            "state": basis.state,
            "global_mode": mode,
            "apply_scope": scope,
            "applied": bool(applied),
            "basis_usd": basis.basis_usd,
            "unsettled_usd": basis.unsettled_usd,
            "venue_cash": basis.venue_cash,
            "venue_buying_power": basis.venue_buying_power,
            "available_usd_after": available_usd,
            "would_have_reduced_usd": would_reduce,
            "detail": basis.detail,
        }
        path = os.path.join(str(runtime_logs_dir()), "cash_settlement_soak.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:  # noqa: BLE001 - observability must never break dispatch
        return
