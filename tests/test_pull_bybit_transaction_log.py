"""The transaction-log puller, tested WITHOUT the SDK via an injected client.

pybit is a VM-only dependency, so a puller that could only be tested against a
live venue would not be tested at all. The client is injectable for exactly
that reason.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import src.runtime.bybit_wallet_truth as wt
from src.runtime.exchange_fills_store import list_transaction_log

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "pull_txnlog", ROOT / "scripts/ops/pull_bybit_transaction_log.py"
)
puller = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(puller)


class FakeClient:
    """Serves pages the way Bybit's cursor pagination does."""

    def __init__(self, pages, boom=None):
        self.pages = pages
        self.boom = boom
        self.calls = []

    def get_transaction_log(self, **params):
        self.calls.append(params)
        if self.boom:
            raise self.boom
        idx = 0
        if params.get("cursor"):
            idx = int(params["cursor"])
        page = self.pages[idx]
        nxt = str(idx + 1) if idx + 1 < len(self.pages) else ""
        return {"result": {"list": page, "nextPageCursor": nxt}}


def _r(i, typ="TRADE", change="-10.0"):
    return {"id": f"t{i}", "type": typ, "currency": "USDT", "change": change,
            "transactionTime": 1_760_000_000_000 + i}


def test_follows_the_cursor_across_pages():
    c = FakeClient([[_r(1), _r(2)], [_r(3)]])
    rows = puller.fetch_transaction_log(c)
    assert [r["id"] for r in rows] == ["t1", "t2", "t3"]


def test_pagination_is_bounded():
    """An unbounded `while cursor` is how a puller becomes an infinite loop."""
    class Endless:
        def get_transaction_log(self, **p):
            return {"result": {"list": [_r(1)], "nextPageCursor": "0"}}

    rows = puller.fetch_transaction_log(Endless(), max_pages=5)
    assert len(rows) == 5, "must stop at max_pages, not spin"


def test_a_transport_failure_RAISES_rather_than_returning_empty():
    """Returning [] on an exception would collapse 'could not look' into
    'the account was flat' -- the exact conflation this family exists to stop."""
    c = FakeClient([[]], boom=RuntimeError("bybit 10003"))
    with pytest.raises(RuntimeError):
        puller.fetch_transaction_log(c)


def test_end_to_end_pull_then_compute(tmp_path):
    db = tmp_path / "s.sqlite"
    c = FakeClient([[_r(1, change="-100"), _r(2, "TRANSFER_IN", "5000")]])
    n = puller.pull_one_account(
        "bybit_2", "k", "s", demo=False, days=7, store_path=db,
        client_factory=lambda *a, **kw: c, now_ms=1_760_000_500_000,
    )
    assert n == 2
    v = wt.compute_wallet_truth("bybit_2", list_transaction_log("bybit_2", path=db))
    assert v.realized_usd == -100.0, "the deposit must not become profit"


def test_repeated_runs_do_not_move_the_figure(tmp_path):
    """An hourly puller with a 7-day lookback re-sees the same rows constantly."""
    db = tmp_path / "s.sqlite"
    def mk():
        return FakeClient([[_r(1, change="-100"), _r(2, change="-25")]])

    for _ in range(3):
        puller.pull_one_account(
            "bybit_2", "k", "s", demo=False, days=7, store_path=db,
            client_factory=lambda *a, **kw: mk(), now_ms=1_760_000_500_000,
        )
    v = wt.compute_wallet_truth("bybit_2", list_transaction_log("bybit_2", path=db))
    assert v.realized_usd == -125.0, "three runs must not treble the loss"


def test_demo_flag_reaches_the_client_factory(tmp_path):
    seen = {}

    def factory(k, s, demo):
        seen["demo"] = demo
        return FakeClient([[]])

    puller.pull_one_account(
        "bybit_1", "k", "s", demo=True, days=1,
        store_path=tmp_path / "s.sqlite", client_factory=factory,
    )
    assert seen["demo"] is True, "a demo account must not be dialled on mainnet"


def test_window_is_passed_to_the_venue(tmp_path):
    c = FakeClient([[]])
    puller.pull_one_account(
        "bybit_2", "k", "s", demo=False, days=3, store_path=tmp_path / "s.sqlite",
        client_factory=lambda *a, **kw: c, now_ms=1_000_000_000_000,
    )
    p = c.calls[0]
    assert p["endTime"] == 1_000_000_000_000
    assert p["startTime"] == 1_000_000_000_000 - 3 * 86_400_000
    assert p["accountType"] == "UNIFIED"
