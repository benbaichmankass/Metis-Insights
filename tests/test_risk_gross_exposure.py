"""RiskManager gross-exposure ceiling — the per-account exposure policy.

Covers the behaviour that motivated it (BL-20260807-ALPACA-PAPER-ZERO-BUYING-POWER-REFUSES-ALL):
seven independently-correct trades summed to 2.03x equity against a 2.00x
broker limit, because the RiskManager owned LOSS risk but delegated EXPOSURE to
the broker's ``available_usd`` — a wall, not a gradient.

The invariants under test:
  * unset ceiling changes nothing (every existing account);
  * an account already at/over its multiple is REFUSED by evaluate();
  * a trade that would merely cross the ceiling is DOWNSIZED, not refused;
  * unmeasurable exposure never refuses and never clamps (fail-open — an
    unreadable journal must not become a self-inflicted trading outage);
  * the broker margin cap still wins when it binds tighter.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.core.coordinator import OrderPackage
from src.units.accounts.risk import RiskManager


def close_to(actual: float, expected: float, tol: float = 1e-6) -> bool:
    """Explicit tolerance instead of ``pytest.approx``.

    NOT a style preference. ``pytest.approx.__eq__`` branches on
    ``sys.modules.get("numpy")``; this repo's test env leaves a truthy stub
    there when numpy is absent, so ``approx`` raises inside ``is_bool`` rather
    than comparing — turning a correct value into a failure, but ONLY when
    another test in the same session has triggered the stub. That makes it a
    heisenbug: these assertions pass file-alone and fail in a full run.
    Filed as BL-20260807-PYTEST-APPROX-NUMPY-STUB.
    """
    return abs(float(actual) - float(expected)) <= tol


def _pkg(entry: float = 100.0, sl: float = 95.0) -> OrderPackage:
    # BTCUSDT so contract_value_usd resolves to 1.0 — keeps the notional
    # arithmetic in the assertions equal to qty x price.
    return OrderPackage(
        strategy="vwap", symbol="BTCUSDT", direction="long",
        entry=entry, sl=sl, tp=entry * 1.1, confidence=1.0,
        meta={"strategy_name": "vwap"},
    )


@pytest.fixture()
def journal(tmp_path, monkeypatch):
    """A minimal trades table the RiskManager can read exposure from."""
    db = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, "
        "status TEXT, position_size REAL, entry_price REAL, pnl REAL, "
        "created_at TEXT, is_backtest BOOLEAN DEFAULT 1)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(RiskManager, "_risk_db_path", staticmethod(lambda: str(db)))
    return db


def _open_position(db, account: str, qty: float, price: float) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO trades (account_id, status, position_size, entry_price, "
        "is_backtest) VALUES (?, 'open', ?, ?, 0)", (account, qty, price),
    )
    conn.commit()
    conn.close()


def _rm(journal, *, pct: float, equity: float = 10_000.0) -> RiskManager:
    cfg = {"risk_pct": 0.015, "min_qty": 0.001, "qty_precision": 3}
    if pct:
        cfg["max_gross_exposure_pct"] = pct
    rm = RiskManager(cfg, account_id="acct_test")
    rm.current_equity = equity
    rm.daily_high_equity = equity
    return rm


# ── the ceiling is opt-in ────────────────────────────────────────────────────

def test_unset_ceiling_is_a_noop(journal):
    """No declared ceiling => no exposure state, no gate, no clamp."""
    _open_position(journal, "acct_test", qty=1_000, price=100.0)  # 10x equity
    rm = _rm(journal, pct=0.0)
    assert rm.gross_exposure() is None
    assert rm.exposure_headroom_usd() is None
    ok, reason = rm.evaluate(_pkg())
    assert ok and reason is None
    assert rm.report()["exposure"] is None


# ── measurement ──────────────────────────────────────────────────────────────

def test_gross_exposure_is_absolute_and_entry_priced(journal):
    """Offsetting positions still consume the ceiling — gross, not net."""
    _open_position(journal, "acct_test", qty=50, price=100.0)    # +5,000
    _open_position(journal, "acct_test", qty=-30, price=100.0)   # |-3,000|
    rm = _rm(journal, pct=2.0, equity=10_000.0)
    notional, equity, multiple = rm.gross_exposure()
    assert close_to(notional, 8_000.0)   # NOT 2,000 (net)
    assert close_to(equity, 10_000.0)
    assert close_to(multiple, 0.8)


def test_other_accounts_do_not_count(journal):
    _open_position(journal, "acct_test", qty=10, price=100.0)
    _open_position(journal, "someone_else", qty=900, price=100.0)
    rm = _rm(journal, pct=2.0)
    assert close_to(rm.gross_exposure()[0], 1_000.0)


def test_closed_positions_do_not_count(journal):
    _open_position(journal, "acct_test", qty=10, price=100.0)
    conn = sqlite3.connect(journal)
    conn.execute(
        "INSERT INTO trades (account_id, status, position_size, entry_price, "
        "is_backtest) VALUES ('acct_test', 'closed', 500, 100.0, 0)"
    )
    conn.commit()
    conn.close()
    rm = _rm(journal, pct=2.0)
    assert close_to(rm.gross_exposure()[0], 1_000.0)


# ── the gate: refuse only AT the boundary ────────────────────────────────────

def test_evaluate_refuses_at_or_over_the_ceiling(journal):
    _open_position(journal, "acct_test", qty=200, price=100.0)   # 20,000 = 2.0x
    rm = _rm(journal, pct=2.0, equity=10_000.0)
    ok, reason = rm.evaluate(_pkg())
    assert not ok
    assert reason == "GROSS_EXPOSURE_CAP"


def test_evaluate_allows_below_the_ceiling(journal):
    _open_position(journal, "acct_test", qty=100, price=100.0)   # 10,000 = 1.0x
    rm = _rm(journal, pct=2.0, equity=10_000.0)
    ok, reason = rm.evaluate(_pkg())
    assert ok and reason is None


# ── the gradient: downsize rather than refuse ────────────────────────────────

def test_position_size_clamps_into_remaining_headroom(journal):
    """The core fix: a crossing trade is TRIMMED, not rejected.

    Headroom is 2.0x*10,000 - 19,000 = 1,000 USD, so at entry 100 the most
    that may be opened is 10 units — regardless of what risk-based sizing
    wanted.
    """
    _open_position(journal, "acct_test", qty=190, price=100.0)   # 19,000 = 1.9x
    rm = _rm(journal, pct=2.0, equity=10_000.0)
    assert close_to(rm.exposure_headroom_usd(), 1_000.0)
    qty = rm.position_size(_pkg(entry=100.0, sl=95.0), balance_usd=10_000.0)
    assert qty > 0, "a crossing trade must be downsized, never zeroed"
    assert qty <= 10.0 + 1e-9


def test_position_size_returns_zero_when_headroom_below_min_lot(journal):
    _open_position(journal, "acct_test", qty=199.9999, price=100.0)
    rm = _rm(journal, pct=2.0, equity=10_000.0)
    assert rm.position_size(_pkg(entry=100.0, sl=95.0), balance_usd=10_000.0) == 0.0


def test_unclamped_when_well_below_ceiling(journal):
    """Plenty of headroom => the exposure clamp must not bind at all."""
    _open_position(journal, "acct_test", qty=1, price=100.0)
    rm_capped = _rm(journal, pct=2.0, equity=10_000.0)
    rm_free = _rm(journal, pct=0.0, equity=10_000.0)
    pkg = _pkg(entry=100.0, sl=95.0)
    assert close_to(rm_capped.position_size(pkg, balance_usd=10_000.0), 
        rm_free.position_size(pkg, balance_usd=10_000.0)
    )


# ── fail-open on unmeasurable exposure ───────────────────────────────────────

def test_unreadable_journal_does_not_refuse_or_clamp(journal, monkeypatch):
    """An unreadable journal must not become a trading outage."""
    monkeypatch.setattr(
        RiskManager, "_open_gross_notional_from_db", lambda self: None
    )
    rm = _rm(journal, pct=2.0, equity=10_000.0)
    assert rm.gross_exposure() is None
    assert rm.exposure_headroom_usd() is None
    ok, reason = rm.evaluate(_pkg())
    assert ok and reason is None


def test_unknown_equity_does_not_refuse(journal, monkeypatch):
    _open_position(journal, "acct_test", qty=1_000, price=100.0)
    rm = _rm(journal, pct=2.0)
    rm.current_equity = None
    monkeypatch.setattr(
        RiskManager, "_account_equity_from_snapshot", lambda self: None
    )
    assert rm.gross_exposure() is None
    ok, _ = rm.evaluate(_pkg())
    assert ok, "unmeasurable equity must not refuse — report it, don't guess"


def test_report_distinguishes_unmeasured_from_flat(journal, monkeypatch):
    """`measured: False` is not the same statement as an exposure of 0."""
    monkeypatch.setattr(
        RiskManager, "_open_gross_notional_from_db", lambda self: None
    )
    rm = _rm(journal, pct=2.0, equity=10_000.0)
    exposure = rm.report()["exposure"]
    assert exposure["measured"] is False
    assert exposure["exposure_multiple"] is None
    assert exposure["max_gross_exposure_pct"] == 2.0


# ── composition with the broker's own ceiling ────────────────────────────────

def test_broker_margin_cap_still_binds_when_tighter(journal):
    """Our policy layers INSIDE the venue's mechanical limit, never over it."""
    _open_position(journal, "acct_test", qty=1, price=100.0)  # ample headroom
    rm = _rm(journal, pct=10.0, equity=10_000.0)
    qty = rm.position_size(
        _pkg(entry=100.0, sl=95.0), balance_usd=10_000.0, available_usd=200.0
    )
    assert qty <= 2.0 + 1e-9, "broker available_usd must still cap the size"
