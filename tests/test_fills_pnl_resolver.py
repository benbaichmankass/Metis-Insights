"""The venue-neutral fills resolver, and the demo branch it un-blocks.

This is the acquisition-side fix. The provenance work made the system honest
about fabrication; this makes it *acquire* the real numbers it was already
collecting and discarding — 198 of 206 fabricated closed rows sat on accounts
whose fills were on disk the whole time
(``BL-20260730-BROKER-TRUTH-COLLECTED-NEVER-READ``).

The two properties under test, because both were previously wrong:

1. **A demo Bybit account resolves.** `account_closed_pnl_for_trade` used to
   `return None` outright for demo, so bybit_1 sat at 47.1% fabricated while
   bybit_2 — same exchange, same code path, not demo — sat at 2.0%. The lookup
   was never *attempted*.
2. **The two modes stay distinct.** IB serves realised PnL per fill; Bybit and
   Alpaca do not. Collapsing them would either invent a `closed_pnl` for Bybit or
   throw IB's real one away.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.runtime.fills_pnl import (
    FILL_EXIT_SOURCE,
    IB_EXIT_SOURCE,
    exit_from_fills,
)
from src.runtime.provenance import MEASURED, classify

_OPEN_MS = int(datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
_CLOSE_MS = _OPEN_MS + 3_600_000

_SCHEMA = """
CREATE TABLE exchange_fills (
    exec_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, symbol TEXT NOT NULL,
    side TEXT NOT NULL, price REAL NOT NULL, qty REAL NOT NULL,
    fee REAL NOT NULL DEFAULT 0, fee_currency TEXT, exec_time TEXT NOT NULL,
    order_id TEXT, is_maker INTEGER NOT NULL DEFAULT 0, raw TEXT,
    inserted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _iso(ms: int) -> str:
    return (
        datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        .isoformat().replace("+00:00", "Z")
    )


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "fills.sqlite"
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()

    def add(exec_id, *, account="bybit_1", symbol="BTCUSDT", side="sell",
            price=64100.0, qty=1.0, offset_ms=1_800_000, realized=None):
        raw = json.dumps({"realized_pnl": realized}) if realized is not None else None
        c = sqlite3.connect(str(path))
        c.execute(
            "INSERT INTO exchange_fills (exec_id, account_id, symbol, side, price,"
            " qty, fee, exec_time, raw) VALUES (?,?,?,?,?,?,?,?,?)",
            (exec_id, account, symbol, side, price, qty, 0.1,
             _iso(_OPEN_MS + offset_ms), raw),
        )
        c.commit()
        c.close()

    ns = type("S", (), {})()
    ns.add = add
    ns.factory = lambda: sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    return ns


def _resolve(store, **kw):
    kw.setdefault("account_id", "bybit_1")
    kw.setdefault("symbol", "BTCUSDT")
    kw.setdefault("direction", "long")
    kw.setdefault("opened_at_ms", _OPEN_MS)
    kw.setdefault("closed_at_ms", _CLOSE_MS)
    kw.setdefault("qty", 1.0)
    return exit_from_fills(conn_factory=store.factory, **kw)


# ------------------------------------------------- mode: no venue realised PnL
def test_bybit_style_fill_yields_a_measured_exit_price(store):
    """The whole point: the exit price becomes a RECORDED FILL, not a mark."""
    store.add("e1", price=64100.0)
    rec = _resolve(store)
    assert rec is not None
    assert rec["avg_exit_price"] == 64100.0
    assert rec["source"] == FILL_EXIT_SOURCE
    # No venue realised PnL exists, so none is invented — the caller computes it.
    assert rec["closed_pnl"] is None


def test_the_fill_exit_source_classifies_as_measured():
    """If this drifts, the fix silently stops counting as an improvement."""
    assert classify(FILL_EXIT_SOURCE) == MEASURED
    assert classify(FILL_EXIT_SOURCE, "exit_price_source") == MEASURED


def test_partial_fills_are_qty_weighted(store):
    store.add("e1", price=64000.0, qty=1.0, offset_ms=1_000_000)
    store.add("e2", price=64400.0, qty=3.0, offset_ms=1_100_000)
    rec = _resolve(store, qty=4.0)
    assert rec["avg_exit_price"] == pytest.approx((64000 + 3 * 64400) / 4)
    assert rec["qty"] == 4.0


# ---------------------------------------------------- mode: venue realised PnL
def test_ib_mode_requires_and_returns_broker_realised_pnl(store):
    store.add("e1", account="ib_paper", symbol="MES", price=5010.0, realized=125.0)
    rec = exit_from_fills(
        account_id="ib_paper", symbol="MES", direction="long",
        opened_at_ms=_OPEN_MS, closed_at_ms=_CLOSE_MS, qty=1.0,
        require_realized=True, conn_factory=store.factory,
    )
    assert rec["closed_pnl"] == 125.0
    assert rec["source"] == IB_EXIT_SOURCE


def test_ib_mode_refuses_when_a_fill_lacks_realised_pnl(store):
    """Summing only the fills that reported would look clean and be too small."""
    store.add("e1", account="ib_paper", symbol="MES", qty=1.0,
              offset_ms=1_000_000, realized=50.0)
    store.add("e2", account="ib_paper", symbol="MES", qty=1.0,
              offset_ms=1_100_000, realized=None)
    assert exit_from_fills(
        account_id="ib_paper", symbol="MES", direction="long",
        opened_at_ms=_OPEN_MS, closed_at_ms=_CLOSE_MS, qty=2.0,
        require_realized=True, conn_factory=store.factory,
    ) is None


def test_non_ib_mode_does_NOT_require_realised_pnl(store):
    """The asymmetry that makes this one function instead of two: Bybit fills
    legitimately carry no realised PnL, and refusing them would leave the 79% of
    fabrication this change exists to fix exactly where it was."""
    store.add("e1", realized=None)
    assert _resolve(store) is not None


# --------------------------------------------------------------- the refusals
def test_qty_mismatch_refuses(store):
    store.add("e1", qty=9.0)
    assert _resolve(store, qty=1.0) is None


def test_unusable_row_refuses_rather_than_being_skipped(store):
    store.add("e1", price=0.0)
    assert _resolve(store, qty=1.0) is None


def test_other_account_symbol_or_side_never_matches(store):
    store.add("e1", account="bybit_2")
    store.add("e2", symbol="ETHUSDT")
    store.add("e3", side="buy")          # a long's OPEN, not its close
    assert _resolve(store) is None


def test_missing_store_returns_none(store):
    def _boom():
        raise sqlite3.OperationalError("no such file")
    assert exit_from_fills(
        account_id="bybit_1", symbol="BTCUSDT", direction="long",
        opened_at_ms=_OPEN_MS, qty=1.0, conn_factory=_boom,
    ) is None


@pytest.mark.parametrize("kw", [
    {"direction": ""}, {"symbol": ""}, {"account_id": ""},
    {"opened_at_ms": "nonsense"},
])
def test_hostile_input_returns_none(store, kw):
    store.add("e1")
    assert _resolve(store, **kw) is None


# ------------------------------------------- the demo branch is no longer dead
def test_a_demo_account_now_reaches_the_resolver(monkeypatch, store):
    """THE regression this whole change is about.

    `account_closed_pnl_for_trade` used to `return None` for any demo account
    before touching a reader, so bybit_1/bybit_portfolio could never be measured.
    It must now fall through to the fills resolver — while still NOT calling the
    closed-pnl endpoint, which is genuinely unreliable on demo
    (BL-20260608-DEMOPNL).
    """
    import src.runtime.fills_pnl as FP
    from src.units.accounts import clients

    seen = {}

    def _spy(**kw):
        seen.update(kw)
        return {"avg_exit_price": 64100.0, "avg_entry_price": None,
                "closed_pnl": None, "qty": 1.0, "side": "sell",
                "closed_at": "2026-07-30T12:30:00Z", "source": FP.FILL_EXIT_SOURCE}

    monkeypatch.setattr(FP, "exit_from_fills", _spy)
    # A bare failure here would mean the endpoint was called for demo.
    monkeypatch.setattr(
        clients, "_bybit_closed_pnl_lookup",
        lambda *a, **k: pytest.fail("the closed-pnl endpoint must not be called for demo"),
        raising=False,
    )

    rec = clients.account_closed_pnl_for_trade(
        {"exchange": "bybit", "account_id": "bybit_1", "demo": "true"},
        symbol="BTCUSDT", direction="long", opened_at_ms=_OPEN_MS,
        closed_at_ms=_CLOSE_MS, qty=1.0,
    )
    assert rec is not None, "a demo account must no longer be dead-ended"
    assert rec["source"] == FP.FILL_EXIT_SOURCE
    assert seen["require_realized"] is False   # Bybit serves no realised PnL
    assert seen["account_id"] == "bybit_1"


# ----------------------------------------------- the ccxt/plain symbol fold
def test_ccxt_stored_symbol_matches_a_plain_journal_symbol(store):
    """THE bug that would have made this whole change inert.

    The Bybit puller stores ccxt form (`BTC/USDT:USDT`); the journal carries the
    plain form (`BTCUSDT`). A `WHERE symbol = ?` equality match returns ZERO rows
    for every Bybit trade — so the resolver would run clean, log nothing, and
    silently change nothing at all. Caught only by looking at the real store
    (diag #8114), not by any test written against my own fixture.
    """
    store.add("e1", symbol="BTC/USDT:USDT", price=64100.0)
    rec = _resolve(store, symbol="BTCUSDT")
    assert rec is not None, "ccxt-stored fills must match a plain journal symbol"
    assert rec["avg_exit_price"] == 64100.0


def test_the_fold_is_symmetric(store):
    """Either side may be in either form — equities/futures are stored plain,
    crypto in ccxt, and the journal is plain throughout."""
    store.add("e1", symbol="XRPUSDT", price=1.08)
    assert _resolve(store, symbol="XRP/USDT:USDT") is not None


def test_the_fold_does_not_collapse_DIFFERENT_instruments(store):
    """Folding must not become 'match anything' — ETH fills may never be
    attributed to a BTC trade."""
    store.add("e1", symbol="ETH/USDT:USDT")
    assert _resolve(store, symbol="BTCUSDT") is None
