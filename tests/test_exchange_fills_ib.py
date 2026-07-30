"""Tests for the IBKR executions → exchange_fills mapping.

Deliberately ``ib_insync``-free: the adapter is duck-typed and its fetcher is
injected, so the mapping is verifiable on a host with no IB dependency and no
gateway (the same contract ``test_exchange_fills_alpaca``-style adapters keep).

The assertions that matter most are the NEGATIVE ones — a fill that cannot be
mapped cleanly must be DROPPED, and an absent ``realizedPNL`` must stay ``None``
rather than becoming ``0.0``. Fabricating a plausible number in place of a
missing measurement is the exact defect this whole module exists to end.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.runtime.exchange_fills_ib import (
    coverage_summary,
    fetch_ib_executions,
    ib_fill_to_row,
    ib_side_to_row_side,
    realized_pnl_from_raw,
)


class _Contract:
    def __init__(self, symbol="MES", local_symbol="MESM6", multiplier="5",
                 sec_type="FUT"):
        self.symbol = symbol
        self.localSymbol = local_symbol
        self.multiplier = multiplier
        self.secType = sec_type


class _Execution:
    def __init__(self, exec_id="0001", side="BOT", price=5100.25, shares=2.0,
                 time=None, acct="DUQ325724", order_id="77", perm_id=999,
                 order_ref="mes_trend", last_liquidity=2):
        self.execId = exec_id
        self.side = side
        self.price = price
        self.shares = shares
        self.time = time if time is not None else datetime(
            2026, 7, 30, 14, 5, 0, tzinfo=timezone.utc
        )
        self.acctNumber = acct
        self.orderId = order_id
        self.permId = perm_id
        self.orderRef = order_ref
        self.lastLiquidity = last_liquidity


class _Report:
    def __init__(self, commission=0.62, currency="USD", realized_pnl=None):
        self.commission = commission
        self.currency = currency
        self.realizedPNL = realized_pnl


_UNSET = object()


class _Fill:
    def __init__(self, contract=_UNSET, execution=_UNSET, report=None):
        # Sentinel-defaulted (not ``None``-defaulted) so a test can pass an
        # EXPLICIT ``None`` to exercise the missing-attribute paths.
        self.contract = _Contract() if contract is _UNSET else contract
        self.execution = _Execution() if execution is _UNSET else execution
        self.commissionReport = report


# --------------------------------------------------------------------------
# Side normalisation
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("BOT", "buy"), ("bot", "buy"), ("BUY", "buy"),
    ("SLD", "sell"), ("sld", "sell"), ("SELL", "sell"),
])
def test_side_normalises(raw, expected):
    assert ib_side_to_row_side(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "UNKNOWN", "X"])
def test_unknown_side_is_none_not_guessed(raw):
    """An unrecognised side must NOT be coerced — a mis-signed fill would
    corrupt every downstream FIFO pairing."""
    assert ib_side_to_row_side(raw) is None


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------

def test_maps_a_complete_fill():
    fill = _Fill(report=_Report(commission=0.62, realized_pnl=-12.5))
    row = ib_fill_to_row(fill, "ib_paper")
    assert row is not None
    assert row["exec_id"] == "0001"
    assert row["account_id"] == "ib_paper"
    assert row["side"] == "buy"
    assert row["price"] == 5100.25
    assert row["qty"] == 2.0
    assert row["fee"] == 0.62
    assert row["fee_currency"] == "USD"
    assert row["exec_time"] == "2026-07-30T14:05:00Z"
    assert row["is_maker"] == 0  # lastLiquidity 2 = removed liquidity


def test_symbol_is_the_generic_root_not_the_localsymbol():
    """BL-20260613-IBPOS: emitting ``MESM6`` means the fill can never join back
    to a journal trade recorded against ``MES``."""
    row = ib_fill_to_row(_Fill(), "ib_paper")
    assert row["symbol"] == "MES"
    assert row["raw"]["local_symbol"] == "MESM6"


def test_maker_flag_from_last_liquidity():
    fill = _Fill(execution=_Execution(last_liquidity=1))
    assert ib_fill_to_row(fill, "ib_paper")["is_maker"] == 1


# --------------------------------------------------------------------------
# Broker-truth realised PnL — the point of the module
# --------------------------------------------------------------------------

def test_realized_pnl_is_carried_through_raw():
    fill = _Fill(report=_Report(realized_pnl=-243.75))
    row = ib_fill_to_row(fill, "ib_paper")
    assert row["raw"]["realized_pnl"] == -243.75
    assert realized_pnl_from_raw(row["raw"]) == -243.75


def test_missing_commission_report_leaves_realized_none_not_zero():
    """An opening fill realises nothing — IB attaches no report. ``None`` must
    survive: absence of a measurement is not a break-even measurement."""
    row = ib_fill_to_row(_Fill(report=None), "ib_paper")
    assert row["raw"]["realized_pnl"] is None
    assert row["fee"] == 0.0  # typed column is NOT NULL; raw keeps the truth


def test_ib_sentinel_realized_pnl_is_treated_as_absent():
    """Some IB builds send a float-max sentinel for 'not applicable' rather than
    omitting the field. Recording it verbatim would book a 1.8e308 loss."""
    row = ib_fill_to_row(_Fill(report=_Report(realized_pnl=1.7976931348623157e308)),
                         "ib_paper")
    assert row["raw"]["realized_pnl"] is None


def test_realized_pnl_from_raw_accepts_json_string():
    """The store persists ``raw`` as a JSON string; the reader must round-trip."""
    assert realized_pnl_from_raw('{"realized_pnl": -12.5}') == -12.5
    assert realized_pnl_from_raw('{"realized_pnl": null}') is None
    assert realized_pnl_from_raw("not json") is None
    assert realized_pnl_from_raw(None) is None


def test_zero_realized_pnl_is_preserved_as_zero():
    """A genuine break-even close is data, not a missing value."""
    row = ib_fill_to_row(_Fill(report=_Report(realized_pnl=0.0)), "ib_paper")
    assert row["raw"]["realized_pnl"] == 0.0
    assert realized_pnl_from_raw(row["raw"]) == 0.0


# --------------------------------------------------------------------------
# Drop, never coerce
# --------------------------------------------------------------------------

@pytest.mark.parametrize("execution", [
    _Execution(exec_id=""),            # no exec id -> no primary key
    _Execution(side="???"),            # unmappable direction
    _Execution(price=None),            # no price
    _Execution(shares=0),              # zero qty
    _Execution(shares=-1),             # negative qty
    _Execution(time="not-a-time"),     # undatable
])
def test_unmappable_fills_are_dropped(execution):
    assert ib_fill_to_row(_Fill(execution=execution), "ib_paper") is None


def test_fill_without_execution_is_dropped():
    assert ib_fill_to_row(_Fill(execution=None), "ib_paper") is None


def test_naive_timestamp_is_treated_as_utc():
    """A tz-less stamp read as local time would silently mis-window the row."""
    fill = _Fill(execution=_Execution(time=datetime(2026, 7, 30, 14, 5, 0)))
    assert ib_fill_to_row(fill, "ib_paper")["exec_time"] == "2026-07-30T14:05:00Z"


def test_ib_wire_format_timestamp_parses():
    fill = _Fill(execution=_Execution(time="20260730-14:05:00"))
    assert ib_fill_to_row(fill, "ib_paper")["exec_time"] == "2026-07-30T14:05:00Z"


# --------------------------------------------------------------------------
# Fetch + coverage
# --------------------------------------------------------------------------

def test_fetch_passes_an_ib_formatted_since_and_maps_all():
    seen = {}

    def _fetch(since):
        seen["since"] = since
        return [
            _Fill(execution=_Execution(exec_id="a")),
            _Fill(execution=_Execution(exec_id="b", side="SLD")),
        ]

    rows = fetch_ib_executions(
        _fetch, account_id="ib_paper", days=2,
        now=datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert seen["since"] == "20260728-12:00:00"
    assert [r["exec_id"] for r in rows] == ["a", "b"]
    assert [r["side"] for r in rows] == ["buy", "sell"]


def test_fetch_drops_bad_rows_without_failing_the_batch():
    def _fetch(since):
        return [_Fill(execution=_Execution(exec_id="ok")),
                _Fill(execution=_Execution(side="???"))]

    rows = fetch_ib_executions(_fetch, account_id="ib_paper", days=1)
    assert len(rows) == 1


def test_empty_venue_response_is_an_empty_list():
    assert fetch_ib_executions(lambda since: [], account_id="ib_paper", days=1) == []


def test_coverage_summary_reports_the_real_reach_not_an_assumed_window():
    """IBKR's execution retention is short and unverifiable from here, so every
    run must report what the venue ACTUALLY served."""
    rows = [
        ib_fill_to_row(_Fill(
            execution=_Execution(
                exec_id="a", time=datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc)),
            report=_Report(realized_pnl=-5.0)), "ib_paper"),
        ib_fill_to_row(_Fill(
            execution=_Execution(
                exec_id="b", time=datetime(2026, 7, 30, 15, 0, tzinfo=timezone.utc)),
            report=None), "ib_paper"),
    ]
    cov = coverage_summary(rows, raw_fill_count=3)
    assert cov["mapped"] == 2
    assert cov["raw_fills"] == 3
    assert cov["dropped"] == 1
    assert cov["oldest_exec_time"] == "2026-07-30T09:00:00Z"
    assert cov["newest_exec_time"] == "2026-07-30T15:00:00Z"
    # Only the fill carrying broker-truth realised PnL can make a close MEASURED.
    assert cov["realized_pnl_count"] == 1


def test_coverage_summary_on_empty_pull():
    cov = coverage_summary([], raw_fill_count=0)
    assert cov == {
        "mapped": 0, "raw_fills": 0, "dropped": 0,
        "oldest_exec_time": None, "newest_exec_time": None,
        "realized_pnl_count": 0,
    }
