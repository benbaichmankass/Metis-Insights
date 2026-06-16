"""Tests for src.runtime.conviction_sizing — P2 conviction sizing.

Conviction sizing is **advisory / observe-only**: it computes the would-be
conviction-driven size and logs it, but ALWAYS returns the RiskManager qty
unchanged. There is **no gate / flag** — these tests assert the never-changes-qty
invariant, a sensible would-be computation, fail-permissive behaviour, and that
the module carries no on/off switch.
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


# --------------------------------------------------------------------------- #
# the core invariant: advisory — NEVER changes qty
# --------------------------------------------------------------------------- #


def test_never_changes_qty_high_conviction(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    pkg = _Pkg(conviction=1.0, entry=100.0, sl=90.0)
    out = cs.annotate_conviction_sizing(
        pkg, 0.5, account_name="bybit_1",
        balance_usd=100000.0, available_usd=1_000_000.0,
        total_account_usd=1_000_000.0, leverage=1, market_type="linear",
    )
    assert out == 0.5  # unchanged even though the would-be size is far larger
    # the would-be size is still computed + stamped for the soak
    assert pkg.meta["conviction_sizing_decision"]["would_be_qty"] > 0.5


def test_never_changes_qty_low_conviction(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    pkg = _Pkg(conviction=0.05, entry=100.0, sl=90.0)
    out = cs.annotate_conviction_sizing(
        pkg, 0.5, account_name="bybit_1",
        balance_usd=100000.0, available_usd=1_000_000.0,
        total_account_usd=1_000_000.0, leverage=1, market_type="linear",
    )
    assert out == 0.5  # unchanged even though the would-be size is smaller


def test_missing_conviction_noop(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    pkg = _Pkg(conviction=None)
    out = cs.annotate_conviction_sizing(pkg, 0.5, balance_usd=1000.0)
    assert out == 0.5
    assert "conviction_sizing_decision" not in pkg.meta


def test_zero_sized_qty_untouched(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    pkg = _Pkg(conviction=0.9)
    out = cs.annotate_conviction_sizing(pkg, 0.0, balance_usd=1000.0)
    assert out == 0.0


# --------------------------------------------------------------------------- #
# the would-be computation (compute_conviction_sizing, pure)
# --------------------------------------------------------------------------- #


def test_would_be_within_margin_cap():
    pkg = _Pkg(conviction=1.0, entry=100.0, sl=99.0)  # tight stop → big risk_qty
    final, rec = cs.compute_conviction_sizing(
        pkg, 0.5, balance_usd=100000.0, available_usd=10.0,
        total_account_usd=100000.0, leverage=1, market_type="linear",
    )
    margin_cap = (10.0 * 1) / 100.0
    assert 0.0 <= final <= margin_cap + 1e-9


def test_would_be_enlarges_within_budget():
    pkg = _Pkg(conviction=1.0, entry=100.0, sl=90.0)  # risk_distance=10
    # risk basis = balance 100000; 2% = 2000; risk_qty = 2000/10 = 200
    final, rec = cs.compute_conviction_sizing(
        pkg, 0.5, balance_usd=100000.0, available_usd=1_000_000.0,
        total_account_usd=1_000_000.0, leverage=1, market_type="linear",
    )
    assert final == pytest.approx(200.0, rel=1e-6)  # would-be is larger than 0.5


def test_would_be_throttle_damps():
    pkg = _Pkg(conviction=1.0, entry=100.0, sl=90.0)
    final, rec = cs.compute_conviction_sizing(
        pkg, 0.5, balance_usd=100000.0, available_usd=25_000.0,
        total_account_usd=100_000.0, leverage=10, market_type="linear",
    )
    # desired=200, throttle=0.25 → 50; margin_cap=(25000*10)/100=2500 (not binding)
    assert final == pytest.approx(50.0, rel=1e-6)


def test_would_be_degenerate_levels():
    pkg = _Pkg(conviction=0.9, entry=100.0, sl=100.0)  # entry==sl
    final, rec = cs.compute_conviction_sizing(
        pkg, 0.5, balance_usd=1000.0, total_account_usd=1000.0,
    )
    assert final is None
    assert rec["action"] == "degenerate_levels"


def test_would_be_futures_whole_contract_refusal():
    pkg = _Pkg(conviction=0.5, entry=5000.0, sl=4990.0, symbol="MES")
    final, rec = cs.compute_conviction_sizing(
        pkg, 1.0, balance_usd=100.0, total_account_usd=100.0,
        market_type="futures", qty_precision=0,
    )
    assert final == 0.0  # sub-1-contract → refusal


# --------------------------------------------------------------------------- #
# fail-permissive
# --------------------------------------------------------------------------- #


def test_fail_permissive_on_bad_pkg():
    class _Boom:
        strategy = "x"
        symbol = "BTCUSDT"

        @property
        def meta(self):
            raise RuntimeError("boom")

    out = cs.annotate_conviction_sizing(_Boom(), 0.5, balance_usd=1000.0)
    assert out == 0.5


# --------------------------------------------------------------------------- #
# no gate / no flag — the module carries no on/off switch
# --------------------------------------------------------------------------- #


def test_no_env_gate_in_module():
    import inspect

    src = inspect.getsource(cs)
    # No mode/enabled/disabled gate, no env reads at all — advisory is baseline.
    for forbidden in (
        "CONVICTION_SIZING_MODE", "CONVICTION_SIZING_ENABLED",
        "CONVICTION_SIZING_DISABLED", "CONVICTION_SIZING_ACCOUNTS",
        "os.environ", "os.getenv",
    ):
        assert forbidden not in src, f"unexpected gate/env-read: {forbidden}"


def test_runtime_flags_has_no_conviction_gate():
    import inspect

    from src.runtime import runtime_flags

    src = inspect.getsource(runtime_flags)
    assert "_conviction_sizing_mode" not in src
    assert "_conviction_sizing_accounts" not in src
