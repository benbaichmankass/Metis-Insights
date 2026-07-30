"""Resolve a journal trade's close from the broker fills we ALREADY collect.

WHY THIS EXISTS (2026-07-30, operator-challenged)
-------------------------------------------------
> "We're running an API-based system. There's absolutely no reason for this."

Correct. The provenance work made the system *honest* about how much of its PnL
is manufactured; it did not make the system *acquire* the real numbers. Measured
on the live journal: **198 of the 206 fabricated closed rows (96%) sit on
accounts whose fills are already pulled on a timer** into
``runtime_state/exchange_fills.sqlite`` — and until the IB path landed, the only
consumers of that store were a dashboard endpoint and two offline scripts.

So the venue's truth was being fetched, rendered on a panel, and then the same
trade was priced from a mark six hours later. That is the *identical* defect the
provenance work root-caused — a signal written and never read — one level up:
last time a field, this time an entire store.

Full account: ``docs/research/WHY-BROKER-TRUTH-ISNT-REACHING-THE-JOURNAL-2026-07-30.md``
(``BL-20260730-BROKER-TRUTH-COLLECTED-NEVER-READ``).

TWO MODES, BECAUSE THE VENUES DIFFER IN WHAT THEY SERVE
-------------------------------------------------------
The distinction is deliberate and is the whole reason this is one function
rather than two:

``require_realized=True`` — **IBKR**. Each fill carries
``CommissionReport.realizedPNL``, the venue's own realised figure. The result
carries ``closed_pnl`` and the caller writes it directly. Fully MEASURED.

``require_realized=False`` — **Bybit / Alpaca equities**. Their fills carry no
realised PnL, so this returns a qty-weighted **average exit price built from
actual fills** and the caller computes PnL locally against the instrument's
contract value. The arithmetic is still local; what changes — and this is the
entire point — is that the *exit price* is now a **recorded fill** instead of a
mark read at sweep time. ``pnl_source`` stays ``local_compute`` (it describes the
arithmetic); ``exit_price_source`` becomes :data:`FILL_EXIT_SOURCE`, which is what
``provenance.classify_pnl`` actually grades.

NOT A BROKER CALL
-----------------
This is a **local SQLite read**. The caller runs on the live trader's monitor
tick, where a per-row network fetch is the 2026-06-09 cold-start wedge shape. The
network half is the pullers on their own timers
(``ict-exchange-fills-pull.timer`` daily for Bybit+Alpaca,
``ict-ib-executions-pull.timer`` hourly for IB).

IT REFUSES RATHER THAN APPROXIMATING
------------------------------------
Every ``None`` path below is deliberate. A partial match that *looks* clean and
is quietly wrong is worse than a declared gap — the row then falls through to the
close-time anchor (ESTIMATED) or to an explicit ``unmeasured`` declaration, both
of which are honest. See :func:`exit_from_fills` for the enumerated refusals.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

# The ONE symbol folder. `BTC/USDT:USDT` (how the Bybit puller stores it) and
# `BTCUSDT` (how the journal carries it) are the same instrument; re-deriving
# this mapping locally is how the two halves drift apart.
from src.runtime.broker_cost_attribution import normalize_symbol

logger = logging.getLogger(__name__)

__all__ = [
    "FILL_EXIT_SOURCE",
    "IB_EXIT_SOURCE",
    "exit_from_fills",
]

#: ``exit_price_source`` for an exit price derived from real exchange fills that
#: carry no venue-computed realised PnL (Bybit, Alpaca equities). MEASURED: the
#: price is an actual recorded fill, not a mark.
FILL_EXIT_SOURCE = "exchange_fill"

#: ``exit_price_source`` when the venue served its OWN realised PnL per fill
#: (IBKR ``CommissionReport.realizedPNL``).
IB_EXIT_SOURCE = "ib_execution"

#: Relative tolerance on the qty match, mirroring the Bybit closed-pnl lookup so
#: both readers reject a partial-close cycle the same way.
QTY_TOLERANCE = 0.05

#: Slack on the open bound for sub-second clock skew between the bot and the
#: venue's execution timestamps. Same 60 s the Bybit closed-pnl lookup uses.
_OPEN_SLACK_MS = 60_000


def _f(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso(ms: int) -> str:
    return (
        datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _open_ro(conn_factory: Optional[Callable[[], Any]]):
    if conn_factory is not None:
        return conn_factory()
    import sqlite3  # noqa: PLC0415 — keeps this import-light for mapping-only tests

    from src.runtime.exchange_fills_store import (  # noqa: PLC0415
        get_fills_db_path,
    )

    path = get_fills_db_path()
    if not path.exists():
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def exit_from_fills(
    *,
    account_id: str,
    symbol: str,
    direction: str,
    opened_at_ms: int,
    closed_at_ms: Optional[int] = None,
    qty: Optional[float] = None,
    require_realized: bool = False,
    conn_factory: Optional[Callable[[], Any]] = None,
) -> Optional[Dict[str, Any]]:
    """One journal trade's close, resolved from stored broker fills.

    Returns the same contract as
    :func:`src.units.accounts.clients.account_closed_pnl_for_trade` —
    ``{avg_exit_price, avg_entry_price, closed_pnl, qty, side, closed_at,
    source}`` — or ``None``.

    ``avg_entry_price`` is always ``None``: close-side executions do not carry
    the position's entry, and inventing one is precisely what this module exists
    to stop. ``closed_pnl`` is ``None`` unless *require_realized*, in which case
    the caller must compute PnL locally from the measured exit price.

    **Refuses — returns ``None``, never a partial record — when:**

    * the store is missing/unreadable, or holds no matching close-side fill;
    * cumulative matched qty is off by more than :data:`QTY_TOLERANCE` (a
      partial-close cycle, or a sibling trade's fills bleeding into the window —
      attributing those would be the netting-proration error in a new costume);
    * any matched fill has an unusable price/qty (skipping it would under-count
      the close and mis-price the exit just as quietly);
    * *require_realized* and **any** matched fill lacks broker realised PnL.
      Summing only the subset that reported would look clean and be too small.

    Never raises.
    """
    side = {"long": "sell", "short": "buy"}.get(str(direction or "").lower())
    want_sym = normalize_symbol(symbol)
    acct = str(account_id or "").strip()
    if not side or not want_sym or not acct:
        return None
    try:
        start_ms = int(opened_at_ms) - _OPEN_SLACK_MS
    except (TypeError, ValueError):
        return None
    end_ms = (
        int(closed_at_ms)
        if closed_at_ms
        else int(datetime.now(timezone.utc).timestamp() * 1000)
    )
    if end_ms <= start_ms:
        return None

    try:
        conn = _open_ro(conn_factory)
        if conn is None:
            return None
        try:
            # Symbol is matched on the NORMALISED key, not by equality.
            # The Bybit puller stores ccxt form (`BTC/USDT:USDT`) while the
            # journal carries the plain form (`BTCUSDT`), so `symbol = ?` would
            # match ZERO rows for every Bybit trade — the resolver would look
            # correct, run clean, and silently change nothing. Verified against
            # the live store (diag #8114): 12 symbols, Bybit in ccxt form,
            # equities/futures plain. Filtering happens in Python because SQL
            # cannot call the canonical folder; the window is one trade's
            # lifetime on one account, so the row count is small.
            rows = [
                r for r in conn.execute(
                    "SELECT price, qty, exec_time, raw, symbol, fee "
                    "  FROM exchange_fills "
                    " WHERE account_id = ? AND side = ? "
                    "   AND datetime(exec_time) >= datetime(?) "
                    "   AND datetime(exec_time) <= datetime(?) "
                    " ORDER BY datetime(exec_time) ASC",
                    (acct, side, _iso(start_ms), _iso(end_ms)),
                ).fetchall()
                if normalize_symbol(r[4]) == want_sym
            ]
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001 — a store read never breaks the monitor tick
        return None

    if not rows:
        return None

    target = _f(qty)
    matched: List[tuple] = []
    filled = 0.0
    for price, fqty, exec_time, raw, _sym, _fee in rows:
        p, q = _f(price), _f(fqty)
        if p is None or q is None or p <= 0 or q <= 0:
            return None
        matched.append((p, q, exec_time, raw, _f(_fee) or 0.0))
        filled += q
        if target is not None and filled >= target * (1 - QTY_TOLERANCE):
            break

    if target is not None and target > 0 and abs(filled - target) > target * QTY_TOLERANCE:
        return None
    if filled <= 0:
        return None

    closed_pnl: Optional[float] = None
    if require_realized:
        from src.runtime.exchange_fills_ib import (  # noqa: PLC0415
            realized_pnl_from_raw,
        )

        total = 0.0
        for _p, _q, _t, raw, _fee in matched:
            realized = realized_pnl_from_raw(raw)
            if realized is None:
                return None
            total += realized
        closed_pnl = total

    return {
        "avg_exit_price": sum(p * q for p, q, _t, _r, _fe in matched) / filled,
        # Close-side fees actually charged on these fills. The local compute is
        # fee-BLIND — order_monitor deleted an earlier fee-blind write for
        # exactly this reason, and only the Bybit closed-pnl sweep recovered a
        # "fee-accurate" number. Measured on the live store (diag #8114, 90d,
        # Bybit crypto): fees are 15.6% of gross realised in aggregate and
        # **61.8% on BTC**. Omitting them does not merely round — it biases
        # toward looking PROFITABLE, flipping marginal losers into winners,
        # which is the same direction that corrupts the ML labels
        # (BL-20260730-ML-LABELS-IGNORE-PNL-PROVENANCE). Equities/futures rows
        # carry fee=0.0, so this is a no-op there rather than a special case.
        # Only CLOSE-side fees: the open-side fee belongs to the open, and this
        # resolver only ever matches the closing side.
        "fees": sum(fe for _p, _q, _t, _r, fe in matched),
        "avg_entry_price": None,
        "closed_pnl": closed_pnl,
        "qty": filled,
        "side": side,
        "closed_at": matched[-1][2],
        "source": IB_EXIT_SOURCE if require_realized else FILL_EXIT_SOURCE,
    }
