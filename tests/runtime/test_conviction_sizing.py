"""Tests for src.runtime.conviction_sizing — P2 conviction-driven sizing.

Covers the no-op cases (mode=off / account not allowed / conviction missing /
sized_qty<=0), annotate-never-changes-qty, apply staying within [0, margin_cap],
fail-permissive, and the env-gate drift guard (the flag is a MODE, not a
``*_ENABLED`` gate).
"""

from __future__ import annotations

import pytest

from src.runtime import conviction_sizing as cs


class _Pkg:
    """Minimal OrderPackage stand-in for the sizer."""

    def __init__(self, *, entry=100.0, sl=90.0, conviction=0.8, symbol="BTCUSDT",
                 direction="long", strategy="vwap", meta=None):
        self.entry = entry
        self.sl = sl
        self.symbol = symbol
        self.direction = direction
        self.strategy = strategy
        if meta is None:
            meta = {}
            if conviction is not None:
                meta["conviction"] = {"conviction": conviction}
        self.meta = meta


def _set_flags(monkeypatch, *, mode="off", accounts=""):
    monkeypatch.setenv("CONVICTION_SIZING_MODE", mode)
    monkeypatch.setenv("CONVICTION_SIZING_ACCOUNTS", accounts)


# --------------------------------------------------------------------------- #
# no-op cases
# --------------------------------------------------------------------------- #


def test_mode_off_is_noop(monkeypatch):
    _set_flags(monkeypatch, mode="off", accounts="bybit_1")
    pkg = _Pkg(conviction=0.9)
    out = cs.apply_conviction_sizing(
        pkg, 0.5, account_name="bybit_1", balance_usd=1000.0,
    )
    assert out == 0.5


def test_account_not_allowed_is_noop(monkeypatch):
    # mode on but the allowlist names a different account
    _set_flags(monkeypatch, mode="apply", accounts="bybit_1")
    pkg = _Pkg(conviction=0.9)
    out = cs.apply_conviction_sizing(
        pkg, 0.5, account_name="bybit_2", balance_usd=1000.0,
    )
    assert out == 0.5


def test_empty_allowlist_is_noop(monkeypatch):
    # P2: empty allowlist is strict (no-op), not permissive
    _set_flags(monkeypatch, mode="apply", accounts="")
    pkg = _Pkg(conviction=0.9)
    out = cs.apply_conviction_sizing(
        pkg, 0.5, account_name="bybit_1", balance_usd=1000.0,
    )
    assert out == 0.5


def test_missing_conviction_is_noop(monkeypatch):
    _set_flags(monkeypatch, mode="apply", accounts="bybit_1")
    pkg = _Pkg(conviction=None)  # no conviction stamp
    out = cs.apply_conviction_sizing(
        pkg, 0.5, account_name="bybit_1", balance_usd=1000.0,
    )
    assert out == 0.5


def test_zero_sized_qty_is_noop(monkeypatch):
    # a RiskManager refusal (qty<=0, e.g. daily cap exhausted) is never resurrected
    _set_flags(monkeypatch, mode="apply", accounts="bybit_1")
    pkg = _Pkg(conviction=0.9)
    out = cs.apply_conviction_sizing(
        pkg, 0.0, account_name="bybit_1", balance_usd=1000.0,
    )
    assert out == 0.0


# --------------------------------------------------------------------------- #
# annotate never changes qty (but logs)
# --------------------------------------------------------------------------- #


def test_annotate_never_changes_qty(monkeypatch, tmp_path):
    _set_flags(monkeypatch, mode="annotate", accounts="bybit_1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    pkg = _Pkg(conviction=0.9, entry=100.0, sl=90.0)
    out = cs.apply_conviction_sizing(
        pkg, 0.5, account_name="bybit_1",
        balance_usd=10000.0, available_usd=10000.0, total_account_usd=10000.0,
        leverage=1, market_type="linear", qty_precision=3,
    )
    assert out == 0.5  # unchanged
    # the would-be decision is stamped on meta + logged
    assert pkg.meta["conviction_sizing_decision"]["mode"] == "annotate"
    assert pkg.meta["conviction_sizing_decision"]["final"] >= 0.0


# --------------------------------------------------------------------------- #
# apply scales within [0, margin_cap]
# --------------------------------------------------------------------------- #


def test_apply_bounded_by_margin_cap(monkeypatch):
    _set_flags(monkeypatch, mode="apply", accounts="bybit_1")
    # tiny available margin → margin_cap is the binding constraint
    pkg = _Pkg(conviction=1.0, entry=100.0, sl=99.0)  # tight stop → big risk_qty
    out = cs.apply_conviction_sizing(
        pkg, 0.5, account_name="bybit_1",
        balance_usd=100000.0,
        available_usd=10.0,  # margin_cap = 10*1/100 = 0.1
        total_account_usd=100000.0,
        leverage=1, market_type="linear", qty_precision=3,
    )
    margin_cap = (10.0 * 1) / 100.0
    assert 0.0 <= out <= margin_cap + 1e-9


def test_apply_enlarges_within_budget(monkeypatch):
    # conviction sizing CAN grow the order vs the risk-based qty (no margin bind)
    _set_flags(monkeypatch, mode="apply", accounts="bybit_1")
    pkg = _Pkg(conviction=1.0, entry=100.0, sl=90.0)  # risk_distance=10
    # risk basis = balance_usd 100000; 2% = 2000 risk_usd; risk_qty = 2000/10 = 200
    out = cs.apply_conviction_sizing(
        pkg, 0.5, account_name="bybit_1",
        balance_usd=100000.0,
        available_usd=1_000_000.0,  # huge → margin not binding
        total_account_usd=1_000_000.0,
        leverage=1, market_type="linear", qty_precision=3,
    )
    # throttle = available/total = 1.0 here (capped at 1.0); final ~ 200
    assert out == pytest.approx(200.0, rel=1e-6)
    assert out > 0.5  # enlarged


def test_throttle_damps_with_low_free_margin(monkeypatch):
    _set_flags(monkeypatch, mode="apply", accounts="bybit_1")
    pkg = _Pkg(conviction=1.0, entry=100.0, sl=90.0)  # risk_qty = 200 at 2% of 100k
    # free margin = 25% of total → throttle 0.25; but margin_cap may bind too
    out = cs.apply_conviction_sizing(
        pkg, 0.5, account_name="bybit_1",
        balance_usd=100000.0,
        available_usd=25_000.0,
        total_account_usd=100_000.0,
        leverage=10, market_type="linear", qty_precision=3,
    )
    # desired=200, throttle=0.25 → 50; margin_cap=(25000*10)/100=2500 (not binding)
    assert out == pytest.approx(50.0, rel=1e-6)


def test_no_trade_floor_inert_at_zero(monkeypatch):
    # default floor 0 → a positive conviction is never floored to a refusal
    _set_flags(monkeypatch, mode="apply", accounts="bybit_1")
    pkg = _Pkg(conviction=0.01, entry=100.0, sl=90.0)
    out = cs.apply_conviction_sizing(
        pkg, 0.5, account_name="bybit_1",
        balance_usd=100000.0, available_usd=1_000_000.0,
        total_account_usd=1_000_000.0, leverage=1, market_type="linear",
    )
    assert out >= 0.0  # not a hard refusal at the inert floor


# --------------------------------------------------------------------------- #
# fail-permissive
# --------------------------------------------------------------------------- #


def test_fail_permissive_on_bad_pkg(monkeypatch):
    _set_flags(monkeypatch, mode="apply", accounts="bybit_1")

    class _Boom:
        strategy = "x"
        symbol = "BTCUSDT"

        @property
        def meta(self):
            raise RuntimeError("boom")

    out = cs.apply_conviction_sizing(
        _Boom(), 0.5, account_name="bybit_1", balance_usd=1000.0,
    )
    assert out == 0.5  # unchanged on any error


def test_degenerate_levels_noop(monkeypatch):
    _set_flags(monkeypatch, mode="apply", accounts="bybit_1")
    pkg = _Pkg(conviction=0.9, entry=100.0, sl=100.0)  # entry==sl → no risk distance
    out = cs.apply_conviction_sizing(
        pkg, 0.5, account_name="bybit_1", balance_usd=1000.0,
        total_account_usd=1000.0,
    )
    assert out == 0.5


def test_futures_whole_contract_refusal(monkeypatch):
    _set_flags(monkeypatch, mode="apply", accounts="ib_paper")
    # MES: tiny budget → sub-1-contract → refusal (0.0), never bumped up
    pkg = _Pkg(conviction=0.5, entry=5000.0, sl=4990.0, symbol="MES")
    out = cs.apply_conviction_sizing(
        pkg, 1.0, account_name="ib_paper",
        balance_usd=100.0, total_account_usd=100.0,
        market_type="futures", qty_precision=0,
    )
    assert out == 0.0  # whole-contract refusal


# --------------------------------------------------------------------------- #
# env-gate drift guard: the flag is a MODE, not a *_ENABLED gate
# --------------------------------------------------------------------------- #


def test_flag_is_mode_not_enabled_gate():
    import inspect

    from src.runtime import runtime_flags

    src = inspect.getsource(runtime_flags)
    # The Prime Directive forbids a default-off *_ENABLED / *_DISABLED gate in
    # front of this capability — it must be a tri-state mode like NEWS_INFLUENCE_MODE.
    assert "CONVICTION_SIZING_ENABLED" not in src
    assert "CONVICTION_SIZING_DISABLED" not in src
    assert "CONVICTION_SIZING_MODE" in src


def test_mode_reader_tristate(monkeypatch):
    from src.runtime.runtime_flags import _conviction_sizing_mode

    monkeypatch.delenv("CONVICTION_SIZING_MODE", raising=False)
    assert _conviction_sizing_mode({}) == "off"
    assert _conviction_sizing_mode({"CONVICTION_SIZING_MODE": "annotate"}) == "annotate"
    assert _conviction_sizing_mode({"CONVICTION_SIZING_MODE": "apply"}) == "apply"
    assert _conviction_sizing_mode({"CONVICTION_SIZING_MODE": "bogus"}) == "off"


def test_accounts_reader(monkeypatch):
    from src.runtime.runtime_flags import _conviction_sizing_accounts

    monkeypatch.delenv("CONVICTION_SIZING_ACCOUNTS", raising=False)
    assert _conviction_sizing_accounts({}) == frozenset()
    assert _conviction_sizing_accounts(
        {"CONVICTION_SIZING_ACCOUNTS": "bybit_1, bybit_2"}
    ) == frozenset({"bybit_1", "bybit_2"})
