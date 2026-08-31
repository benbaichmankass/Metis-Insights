"""Bybit **wallet truth** from the venue's own transaction log — the LIVE
replacement for a hand-pasted UM export.

WHY THIS EXISTS (operator directive, 2026-08-31). ``src.runtime.broker_truth``
correctly identified the only trustworthy figure for a netting account that
mixes spot + perp: the **account-level wallet delta**, "Bybit UM ``Change``
minus transfers, which nets fees + funding + conversions". It then sourced that
figure from *an operator pasting a CSV export*, so the authoritative number for
a real-money account froze on 2026-07-13 while the account kept trading — 59
closed real-money trades with no wallet-truth counterpart
(``BL-20260830-BROKER-TRUTH-LEDGER-STALE-59-REAL-MONEY-CLOSES-UNRECONCILED``).

The operator's ruling is that this is not an acceptable design: *"You cannot
manage a trade system based on the fact that I'm gonna occasionally give you a
CSV of trade data."* We hold live API credentials for every Bybit account. The
same quantity the export produces is served by ``/v5/account/transaction-log``
(pybit ``HTTP.get_transaction_log``) — so it is pulled, stored and recomputed
continuously, exactly like fills.

THIS MODULE IS PURE. It takes ROWS and returns a verdict; it opens no socket and
touches no DB, so the definition of wallet truth is arguable in tests rather
than against a live account. The puller and the store are separate
(``scripts/ops/pull_bybit_transaction_log.py`` /
``exchange_fills_store``) — the same split ``exit_anchor`` and
``protection_reassert`` use, and for the same reason.

⚠️ **``change`` IS THE WALLET DELTA — NOT ``cashFlow``, NOT ``closedPnl``.**
Bybit's transaction log carries several money columns and they answer different
questions. ``change`` is the signed net movement of the wallet for that row
(what the UM export's "Change" column shows, and what the hand ledger summed);
``cashFlow`` excludes fees. Summing the wrong column reproduces the shape of
number this whole family exists to stop trusting.

⚠️ **TRANSFERS ARE NOT P&L.** ``TRANSFER_IN`` / ``TRANSFER_OUT`` are deposits and
withdrawals; including them makes a funded account look profitable. They are
excluded via :data:`NON_PNL_TYPES`, which is an explicit named constant so the
choice is reviewable and testable rather than buried in a comprehension.

⚠️ **NEVER SUM ACROSS CURRENCIES.** A UNIFIED account can carry USDT, USDC and
coin rows. Adding them yields a number in no unit at all. Rows are grouped by
currency; only the stablecoin bucket becomes ``realized_usd``, and any non-USD
row is COUNTED and REPORTED (``non_usd_rows`` / ``currencies_seen``) rather than
dropped, so a partial answer can never read as a complete one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

#: Transaction-log ``type`` values that move the wallet but are NOT trading P&L.
#: Deposits/withdrawals between the funding and unified accounts, and between
#: sub-accounts. The 2026-07-13 hand reconciliation used exactly this exclusion
#: ("UM Change minus transfers"), so matching it keeps the API figure
#: comparable to the historical ledger rather than silently redefining it.
NON_PNL_TYPES: frozenset[str] = frozenset({
    "TRANSFER_IN",
    "TRANSFER_OUT",
})

#: Currencies treated as 1:1 USD for the ``realized_usd`` roll-up. Deliberately
#: a small explicit set: a coin-margined row is reported, never converted, because
#: converting would need a rate we do not have at row time and would manufacture
#: precision (the FABRICATED bucket in ``src.runtime.provenance``).
USD_STABLES: frozenset[str] = frozenset({"USDT", "USDC", "USD", "DAI"})

# ── read states, never collapsed ──────────────────────────────────────────────
#: A confirmed read that produced rows in the window.
STATE_MEASURED = "measured_api"
#: The venue was asked and the window genuinely holds no rows. Distinct from
#: "we did not ask" — a flat window is a real observation, an unpulled one is not.
STATE_NO_ROWS = "no_rows_in_window"
#: We could not look (creds missing, SDK error, HTTP failure). NEVER 0.0.
STATE_UNREADABLE = "unreadable"
#: Nothing has ever been pulled for this account. Emphatically NOT "no P&L".
STATE_NOT_PULLED = "not_pulled"

ALL_STATES = (STATE_MEASURED, STATE_NO_ROWS, STATE_UNREADABLE, STATE_NOT_PULLED)


def _f(v: Any) -> Optional[float]:
    """Coerce a venue numeric (often a string) to float; None when unusable."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class WalletTruth:
    """The verdict for ONE account over ONE window.

    ``realized_usd`` is ``None`` — never ``0.0`` — whenever the state is not
    ``measured_api``. Zero is a real and meaningful reading (a window in which
    the account traded to exactly flat); "we could not look" is not, and a
    fabricated zero here would flow straight into a P&L headline.
    """

    account_id: str
    state: str
    realized_usd: Optional[float] = None
    fees_usd: Optional[float] = None
    funding_usd: Optional[float] = None
    rows_counted: int = 0
    rows_excluded_transfers: int = 0
    non_usd_rows: int = 0
    currencies_seen: tuple[str, ...] = ()
    window_start_ms: Optional[int] = None
    window_end_ms: Optional[int] = None
    reason: Optional[str] = None
    by_type: dict[str, float] = field(default_factory=dict)

    @property
    def is_measured(self) -> bool:
        return self.state == STATE_MEASURED

    def as_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "state": self.state,
            "realized_usd": self.realized_usd,
            "fees_usd": self.fees_usd,
            "funding_usd": self.funding_usd,
            "rows_counted": self.rows_counted,
            "rows_excluded_transfers": self.rows_excluded_transfers,
            "non_usd_rows": self.non_usd_rows,
            "currencies_seen": list(self.currencies_seen),
            "window_start_ms": self.window_start_ms,
            "window_end_ms": self.window_end_ms,
            "reason": self.reason,
            "by_type": dict(self.by_type),
        }


def compute_wallet_truth(
    account_id: str,
    rows: Optional[Iterable[Mapping[str, Any]]],
    *,
    window_start_ms: Optional[int] = None,
    window_end_ms: Optional[int] = None,
    unreadable_reason: Optional[str] = None,
) -> WalletTruth:
    """Wallet-truth realized P&L for *account_id* over the given rows.

    ``rows`` is ``None`` to mean **we could not look** — that is a different
    input from an empty iterable, which means we looked and the window is empty.
    Collapsing the two is the defect this signature exists to prevent.
    """
    if unreadable_reason is not None or rows is None:
        return WalletTruth(
            account_id=account_id,
            state=STATE_UNREADABLE,
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
            reason=unreadable_reason or "rows_is_none",
        )

    counted = 0
    excluded = 0
    non_usd = 0
    realized = 0.0
    fees = 0.0
    funding = 0.0
    currencies: set[str] = set()
    by_type: dict[str, float] = {}

    for row in rows:
        ttype = str(row.get("type") or "").strip().upper()
        cur = str(row.get("currency") or "").strip().upper()
        if cur:
            currencies.add(cur)

        if ttype in NON_PNL_TYPES:
            excluded += 1
            continue

        if cur and cur not in USD_STABLES:
            # Reported, never converted and never silently dropped.
            non_usd += 1
            continue

        change = _f(row.get("change"))
        if change is None:
            # A row whose wallet delta cannot be read is not a zero-delta row.
            # Count it as non-USD-style "seen but unusable" via the reason path
            # rather than folding a 0.0 into the sum.
            non_usd += 1
            continue

        counted += 1
        realized += change
        by_type[ttype] = round(by_type.get(ttype, 0.0) + change, 10)

        fee = _f(row.get("fee"))
        if fee is not None:
            fees += fee
        fund = _f(row.get("funding"))
        if fund is not None:
            funding += fund

    if counted == 0:
        # We looked. Either the window is genuinely empty, or every row in it
        # was a transfer / non-USD. Both are "no measurable USD P&L here", and
        # the counts beside the state say which.
        return WalletTruth(
            account_id=account_id,
            state=STATE_NO_ROWS,
            rows_counted=0,
            rows_excluded_transfers=excluded,
            non_usd_rows=non_usd,
            currencies_seen=tuple(sorted(currencies)),
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
        )

    return WalletTruth(
        account_id=account_id,
        state=STATE_MEASURED,
        realized_usd=round(realized, 8),
        fees_usd=round(fees, 8),
        funding_usd=round(funding, 8),
        rows_counted=counted,
        rows_excluded_transfers=excluded,
        non_usd_rows=non_usd,
        currencies_seen=tuple(sorted(currencies)),
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
        by_type=by_type,
    )
