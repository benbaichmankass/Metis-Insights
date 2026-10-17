"""NETTED-SLICES end to end through ``Coordinator.multi_account_execute``.

Before this change a second strategy's same-side signal on a held symbol came
back ``noop at_target`` (delta against the ACCOUNT net) — the measured reason
two strategies could never share a symbol. On a ``position_model: slices``
account the delta basis is the strategy's OWN slice, the slice is sized inside
the symbol budget, and the add is refused per trade when the ledger does not
reconcile.
"""
from __future__ import annotations

import sqlite3
import textwrap

import pytest

from src.core.coordinator import OrderPackage
from src.runtime.intents import INTENT_MODE_META_KEY, INTENT_MODE_META_VALUE
from src.core.coordinator import Coordinator
from tests.test_intent_delta_dispatch import _init_trade_journal, _patch_dispatch_deps

SLICE_ACCOUNTS = textwrap.dedent("""\
    accounts:
      bybit_2:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_API_KEY_2
        mode: live
        market_type: linear
        position_model: slices
        strategies: [trend_donchian_eth_4h, trend_donchian_eth_prop]
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
          leverage: 3
""")


@pytest.fixture()
def trade_db(tmp_path, monkeypatch):
    path = str(tmp_path / "trade_journal.db")
    _init_trade_journal(path)
    monkeypatch.setenv("TRADE_JOURNAL_DB", path)
    monkeypatch.setenv("BYBIT_API_KEY_2", "test-key")
    monkeypatch.setenv("BYBIT_API_SECRET_2", "test-secret")
    return path


@pytest.fixture()
def coord(tmp_path):
    units_path = tmp_path / "units.yaml"
    units_path.write_text("units: {}\n")
    return Coordinator(units_path=str(units_path))


@pytest.fixture()
def slice_yaml(tmp_path):
    p = tmp_path / "accounts.yaml"
    p.write_text(SLICE_ACCOUNTS)
    return str(p)


def _seed(path, strategy, qty, sl, direction="long"):
    with sqlite3.connect(path) as c:
        c.execute(
            "INSERT INTO trades (timestamp, symbol, direction, entry_price, stop_loss,"
            " position_size, status, is_backtest, account_id, strategy_name)"
            " VALUES ('2026-10-15T00:00:00Z','BTCUSDT',?,50000.0,?,?,'open',0,'bybit_2',?)",
            (direction, sl, qty, strategy),
        )


def _pkg(strategy, direction="long", weight=None, contenders=None):
    meta = {INTENT_MODE_META_KEY: INTENT_MODE_META_VALUE, "aggregated_target_qty": 0.0}
    if weight is not None:
        meta["slice_allocation"] = {"bybit_2": {"weight": weight,
                                                "contenders": contenders or [strategy]}}
    return OrderPackage(
        strategy=strategy, symbol="BTCUSDT", direction=direction, entry=50_000.0,
        sl=49_500.0 if direction == "long" else 50_500.0,
        tp=51_500.0 if direction == "long" else 48_500.0, meta=meta,
    )


@pytest.fixture()
def venue(monkeypatch):
    """Venue net the admission reads; tests set ``venue['net']``."""
    state = {"net": 0.0, "mode": "partial"}
    import src.runtime.positions as adm

    def _positions(cfg):
        n = state["net"]
        if n is None:
            return None
        return [] if n == 0 else [{"symbol": "BTCUSDT", "side": "Buy" if n > 0 else "Sell",
                                   "size": abs(n)}]
    monkeypatch.setattr(adm, "_default_positions_reader", _positions)
    monkeypatch.setattr(adm, "_default_tpsl_mode", lambda: state["mode"])
    monkeypatch.setattr(adm, "_default_hedge", lambda a, s: False)
    return state


def _run(coord, slice_yaml, pkg):
    return coord.multi_account_execute(pkg, accounts_path=slice_yaml,
                                       balance_fetcher=lambda a: 10_000.0)


def test_second_strategy_same_side_opens_its_own_slice(coord, slice_yaml, trade_db, venue, monkeypatch):
    captured = []
    _patch_dispatch_deps(monkeypatch, captured)
    # A holds 0.2 with its stop already at break-even (holds no budget).
    _seed(trade_db, "trend_donchian_eth_4h", 0.2, sl=50_000.0)
    venue["net"] = 0.2
    pkg = _pkg("trend_donchian_eth_prop")
    res = _run(coord, slice_yaml, pkg)
    assert res[0]["error"] is None, res
    assert len(captured) == 1 and captured[0]["reduce_only"] is False
    # RiskManager size = 10_000 * 1% / 500 = 0.2 — the slice's full share.
    assert captured[0]["qty_override"] == pytest.approx(0.2, abs=1e-3)
    assert pkg.meta["execution_delta"]["action"] == "open"
    assert pkg.meta["slice_admission"]["ok"] is True


def test_allocation_weight_sizes_the_slice(coord, slice_yaml, trade_db, venue, monkeypatch):
    captured = []
    _patch_dispatch_deps(monkeypatch, captured)
    pkg = _pkg("trend_donchian_eth_4h", weight=0.25,
               contenders=["trend_donchian_eth_4h", "trend_donchian_eth_prop"])
    _run(coord, slice_yaml, pkg)
    assert captured[0]["qty_override"] == pytest.approx(0.05, abs=1e-3)


def test_other_strategy_at_risk_consumes_the_budget(coord, slice_yaml, trade_db, venue, monkeypatch):
    captured = []
    _patch_dispatch_deps(monkeypatch, captured)
    _seed(trade_db, "trend_donchian_eth_4h", 0.2, sl=49_500.0)  # one full trade at risk
    venue["net"] = 0.2
    res = _run(coord, slice_yaml, _pkg("trend_donchian_eth_prop"))
    assert captured == []
    assert res[0]["error"] == "slice_budget_exhausted"


def test_same_strategy_cannot_double_enter(coord, slice_yaml, trade_db, venue, monkeypatch):
    captured = []
    _patch_dispatch_deps(monkeypatch, captured)
    _seed(trade_db, "trend_donchian_eth_prop", 0.05, sl=50_000.0)
    venue["net"] = 0.05
    res = _run(coord, slice_yaml, _pkg("trend_donchian_eth_prop"))
    assert captured == []
    assert res[0]["error"] == "reentry_suppressed_netting_guard:increase"


def test_opposite_slice_on_one_way_book_is_refused(coord, slice_yaml, trade_db, venue, monkeypatch):
    captured = []
    _patch_dispatch_deps(monkeypatch, captured)
    _seed(trade_db, "trend_donchian_eth_4h", 0.2, sl=50_000.0)
    venue["net"] = 0.2
    res = _run(coord, slice_yaml, _pkg("trend_donchian_eth_prop", direction="short"))
    assert captured == []
    assert res[0]["error"] == "opposite_slice_one_way_book"


def test_divergent_ledger_refuses_the_add_and_the_account_stays_live(
        coord, slice_yaml, trade_db, venue, monkeypatch):
    captured = []
    _patch_dispatch_deps(monkeypatch, captured)
    _seed(trade_db, "trend_donchian_eth_4h", 0.2, sl=50_000.0)
    venue["net"] = 0.1  # the venue holds less than the ledger claims
    res = _run(coord, slice_yaml, _pkg("trend_donchian_eth_prop"))
    assert captured == [] and res[0]["error"] == "slice_ledger_divergent"
    # Next tick, reconciled: the same add goes through. No latch.
    venue["net"] = 0.2
    res2 = _run(coord, slice_yaml, _pkg("trend_donchian_eth_prop"))
    assert res2[0]["error"] is None and len(captured) == 1


def test_own_slice_is_never_resized_by_a_later_signal(coord, slice_yaml, trade_db, venue, monkeypatch):
    captured = []
    _patch_dispatch_deps(monkeypatch, captured)
    # Own slice larger than today's risk target -> the account-net path would
    # send a reduce-only trim; a slice rides its own bracket instead.
    _seed(trade_db, "trend_donchian_eth_prop", 0.5, sl=49_500.0)
    venue["net"] = 0.5
    res = _run(coord, slice_yaml, _pkg("trend_donchian_eth_prop"))
    assert captured == []
    assert res[0]["error"] == "intent_noop:slice_hold_to_bracket_reduce"


def test_non_slice_account_is_unchanged(coord, tmp_path, trade_db, venue, monkeypatch):
    """Without position_model the second strategy still noops against the net."""
    captured = []
    _patch_dispatch_deps(monkeypatch, captured)
    p = tmp_path / "plain.yaml"
    p.write_text(SLICE_ACCOUNTS.replace("    position_model: slices\n", ""))
    _seed(trade_db, "trend_donchian_eth_4h", 0.2, sl=50_000.0)
    res = coord.multi_account_execute(_pkg("trend_donchian_eth_prop"), accounts_path=str(p),
                                      balance_fetcher=lambda a: 10_000.0)
    assert captured == []
    assert str(res[0]["error"]).startswith("intent_noop:at_target")
