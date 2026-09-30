"""The open-package gate, applied PER ACCOUNT (PI-20260930-QZSE4AMA-0001).

Until 2026-09-30 ``_has_open_package_for_strategy`` blocked a strategy+symbol
on EVERY account while any account held its open package (#387, operator
directive 2026-05-03 — an anti-stacking rule written before the Stage-1/Stage-2
split). Measured on the live journal that day: pkg-5e636e3adddf4a83, an IEF
short from 2026-07-29, is open because alpaca_paper (141 sh) and
alpaca_portfolio (152 sh) still hold it; alpaca_live's leg was rejected and it
is flat — so the next ief_pullback_1d long would have been skipped on the
REAL-MONEY account because of a PAPER position.

Contracts pinned here, against a real temp journal:

1. The IEF case exactly: paper + mirror hold, live is flat → the round is
   NARROWED to exclude the holders; alpaca_live still dispatches.
2. A live account's own leg still blocks that account (anti-stacking kept).
3. Stage-2 mirror pairing: the mirror follows its primary (a primary's leg
   keeps the mirror out); a mirror's own leg never blocks the primary.
4. An open package with no live leg anywhere still blocks the whole strategy
   (the 2026-05-09 retry-storm case).
"""
from __future__ import annotations

import pytest

import src.runtime.pipeline as pl
from src.runtime import strategy_monocle as sm
from src.units.db.database import Database

STRAT, SYM = "ief_pullback_1d", "IEF"


@pytest.fixture
def journal(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    return Database(db_path=str(db_path))


def _pkg(db, pkg_id, *, strategy=STRAT, symbol=SYM, status="open", direction="short"):
    db.insert_order_package({
        "order_package_id": pkg_id, "strategy_name": strategy, "symbol": symbol,
        "direction": direction, "entry": 95.0, "sl": 97.0, "tp": 80.0,
        "confidence": 0.5, "status": status, "linked_trade_id": None, "meta": {},
    })


def _leg(db, pkg_id, account, status="open", *, symbol=SYM, strategy=STRAT):
    return db.insert_trade({
        "order_package_id": pkg_id, "account_id": account, "status": status,
        "symbol": symbol, "setup_type": strategy, "direction": "short",
        "timestamp": "2026-07-29T18:50:00+00:00",
        "entry_price": 95.0, "position_size": 1.0, "account_class": "paper",
        "is_backtest": 0,
    })


def _sig(strategy=STRAT, symbol=SYM, side="buy"):
    return {"symbol": symbol, "side": side, "meta": {"strategy_name": strategy}}


@pytest.fixture
def audit(monkeypatch):
    rows = []
    monkeypatch.setattr(pl, "log_signal", rows.append)
    return rows


# ── 1. the IEF case ─────────────────────────────────────────────────────────

def test_ief_paper_package_open_live_long_allowed(journal, audit):
    """pkg-5e636e3adddf4a83's exact shape: paper + mirror open, live rejected."""
    _pkg(journal, "pkg-5e636e3adddf4a83")
    _leg(journal, "pkg-5e636e3adddf4a83", "alpaca_paper")
    _leg(journal, "pkg-5e636e3adddf4a83", "alpaca_portfolio")
    _leg(journal, "pkg-5e636e3adddf4a83", "alpaca_live", status="rejected")

    gate, scope = pl._open_package_round(_sig(), None)

    assert gate is None
    assert "alpaca_live" in scope
    assert "alpaca_paper" not in scope and "alpaca_portfolio" not in scope
    # The round is narrowed to exactly the declared accounts minus the holders.
    assert scope == sm._accounts_declaring(STRAT) - {"alpaca_paper", "alpaca_portfolio"}
    assert audit and audit[-1]["event"] == "open_package_scoped"
    assert audit[-1]["held_by_account"] == {
        "alpaca_paper": "pkg-5e636e3adddf4a83",
        "alpaca_portfolio": "pkg-5e636e3adddf4a83",
    }


def test_ief_case_with_an_elected_scope(journal, audit):
    _pkg(journal, "pkg-ief")
    _leg(journal, "pkg-ief", "alpaca_paper")
    _leg(journal, "pkg-ief", "alpaca_portfolio")
    gate, scope = pl._open_package_round(
        _sig(), frozenset({"alpaca_live", "alpaca_paper", "alpaca_portfolio"}))
    assert gate is None and scope == frozenset({"alpaca_live"})


def test_the_old_strategy_wide_helper_would_have_blocked_it(journal):
    """The regression this fixes, stated against the helper it replaces."""
    _pkg(journal, "pkg-ief")
    _leg(journal, "pkg-ief", "alpaca_paper")
    assert sm._has_open_package_for_strategy(STRAT, SYM) == "pkg-ief"


# ── 2. a live account's own leg still blocks it ─────────────────────────────

def test_live_package_open_blocks_live(journal, audit):
    _pkg(journal, "pkg-live", direction="long")
    _leg(journal, "pkg-live", "alpaca_live")
    gate, scope = pl._open_package_round(
        _sig(), frozenset({"alpaca_live", "alpaca_portfolio"}))
    assert scope is None
    assert gate["status"] == "skipped" and gate["reason"] == "open_package_exists"
    assert gate["open_package_id"] == "pkg-live"
    assert audit[-1]["event"] == "open_package_blocked"


def test_live_leg_blocks_live_but_not_an_unrelated_paper_account(journal, audit):
    _pkg(journal, "pkg-live", direction="long")
    _leg(journal, "pkg-live", "alpaca_live")
    gate, scope = pl._open_package_round(
        _sig(), frozenset({"alpaca_live", "alpaca_portfolio", "alpaca_paper"}))
    assert gate is None and scope == frozenset({"alpaca_paper"})


def test_nothing_open_is_byte_for_byte_the_old_path(journal, audit):
    assert pl._open_package_round(_sig(), None) == (None, None)
    s = frozenset({"alpaca_live"})
    assert pl._open_package_round(_sig(), s) == (None, s)
    assert audit == []


def test_a_closed_leg_does_not_hold_its_account(journal, audit):
    _pkg(journal, "pkg-x")
    _leg(journal, "pkg-x", "alpaca_paper")
    _leg(journal, "pkg-x", "alpaca_live", status="closed")
    gate, scope = pl._open_package_round(
        _sig(), frozenset({"alpaca_live", "alpaca_paper"}))
    assert gate is None and scope == frozenset({"alpaca_live"})


def test_other_symbol_and_strategy_are_independent(journal, audit):
    _pkg(journal, "pkg-tlt", symbol="TLT")
    _leg(journal, "pkg-tlt", "alpaca_live", symbol="TLT")
    _pkg(journal, "pkg-other", strategy="slv_pullback_1d")
    _leg(journal, "pkg-other", "alpaca_live", strategy="slv_pullback_1d")
    assert pl._open_package_round(_sig(), None) == (None, None)


# ── 3. Stage-2 mirror pairing ───────────────────────────────────────────────

def test_mirror_follows_its_primary(journal, audit):
    """bybit_2 holds → bybit_portfolio must not open a trade bybit_2 is not taking."""
    _pkg(journal, "pkg-b2", strategy="xrp_pullback_2h", symbol="XRPUSDT")
    _leg(journal, "pkg-b2", "bybit_2", symbol="XRPUSDT", strategy="xrp_pullback_2h")
    gate, _ = pl._open_package_round(
        _sig("xrp_pullback_2h", "XRPUSDT"), frozenset({"bybit_2", "bybit_portfolio"}))
    assert gate["reason"] == "open_package_exists"
    assert gate["held_by_account"] == {"bybit_2": "pkg-b2", "bybit_portfolio": "pkg-b2"}


def test_mirror_own_leg_never_blocks_the_primary(journal, audit):
    _pkg(journal, "pkg-bp", strategy="xrp_pullback_2h", symbol="XRPUSDT")
    _leg(journal, "pkg-bp", "bybit_portfolio", symbol="XRPUSDT", strategy="xrp_pullback_2h")
    gate, scope = pl._open_package_round(
        _sig("xrp_pullback_2h", "XRPUSDT"), frozenset({"bybit_2", "bybit_portfolio"}))
    assert gate is None and scope == frozenset({"bybit_2"})


def test_mirror_map_matches_the_declared_stage2_pairs():
    from scripts.ops.mandate_resolver import MIRROR_OF
    assert sm.STAGE2_MIRROR_OF == {m: p for p, m in MIRROR_OF.items()}
    import yaml
    accts = yaml.safe_load(open("config/accounts.yaml"))["accounts"]
    for mirror, primary in sm.STAGE2_MIRROR_OF.items():
        assert mirror in accts and primary in accts


# ── 4. unattributable open package keeps the strategy-wide block ────────────

def test_open_package_without_a_live_leg_blocks_everything(journal, audit):
    """2026-05-09: every account refused, the row stayed 'open' unlinked, and
    dropping the gate for it re-dispatched every minute. Still blocked."""
    _pkg(journal, "pkg-unlinked")
    _leg(journal, "pkg-unlinked", "alpaca_live", status="rejected")
    gate, scope = pl._open_package_round(_sig(), None)
    assert scope is None and gate["open_package_id"] == "pkg-unlinked"


def test_terminal_statuses_cover_the_monitors():
    from src.runtime.order_monitor import _TERMINAL_TRADE_STATUSES
    assert set(_TERMINAL_TRADE_STATUSES) <= set(sm._TERMINAL_LEG_STATUSES)


# ── the round loop uses it ──────────────────────────────────────────────────

def test_monocle_gate_skips_the_strategy_wide_check_when_already_scoped(monkeypatch):
    monkeypatch.setattr(pl, "_has_open_package_for_strategy",
                        lambda s, symbol=None: "pkg-held-elsewhere")
    monkeypatch.setattr(pl, "_same_bar_entry_for_strategy", lambda *a, **k: None)
    monkeypatch.setattr(pl, "_recent_refusal_for_strategy", lambda *a, **k: None)
    monkeypatch.setattr(pl, "_empty_sizing_refusal_for_signal", lambda *a, **k: None)
    monkeypatch.setattr(pl, "signal_key_for_signal", lambda *a, **k: "k")
    assert pl._monocle_gate(_sig(), {})["reason"] == "open_package_exists"
    assert pl._monocle_gate(_sig(), {}, open_package_checked=True) is None
