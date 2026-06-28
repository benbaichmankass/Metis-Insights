"""Tests for src.runtime.conviction_sizing.apply_conviction_sizing — Design B.

The APPLY path is the NEW, flag-gated conviction-sizing influence (separate from
the flagless observe-only annotator). Gated by:
  * CONVICTION_SIZING_MODE      ∈ {off (default), annotate, apply}
  * CONVICTION_SIZING_ACCOUNTS  comma-list allowlist (empty = all)
  * CONVICTION_SIZING_DIRECTION ∈ {reductive (default), symmetric}

Default-off → a byte-for-byte no-op on the order path. These tests cover mode
gating, the account allowlist, the sizing math (reusing compute_conviction_sizing
assertions), direction sub-modes (reductive=min / symmetric=can exceed up to
budget), the below-floor journaled refusal, the daily-loss clamp, composition
order, fail-inert behaviour, and env-gate-guard compliance (*_MODE / *_DIRECTION,
never *_ENABLED).
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


_LARGE = dict(
    balance_usd=100000.0, available_usd=1_000_000.0,
    total_account_usd=1_000_000.0, leverage=1, market_type="linear",
)


# --------------------------------------------------------------------------- #
# mode gating
# --------------------------------------------------------------------------- #


def test_mode_off_is_noop(monkeypatch, tmp_path):
    """Default-off → the apply path returns sized_qty UNCHANGED (no-op)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CONVICTION_SIZING_MODE", raising=False)
    pkg = _Pkg(conviction=1.0)
    out = cs.apply_conviction_sizing(pkg, 0.5, account_name="bybit_1", **_LARGE)
    assert out == 0.5
    # no apply decision stamped when off
    assert "conviction_apply_decision" not in pkg.meta


def test_mode_off_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "off")
    pkg = _Pkg(conviction=1.0)
    out = cs.apply_conviction_sizing(pkg, 0.5, account_name="bybit_1", **_LARGE)
    assert out == 0.5


def test_mode_annotate_does_not_resize(monkeypatch, tmp_path):
    """annotate → compute the would-be size, stamp it, return UNCHANGED."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "annotate")
    pkg = _Pkg(conviction=1.0)
    out = cs.apply_conviction_sizing(pkg, 0.5, account_name="bybit_1", **_LARGE)
    assert out == 0.5  # UNCHANGED
    dec = pkg.meta["conviction_apply_decision"]
    assert dec["mode"] == "annotate"
    assert dec["would_be_qty"] == pytest.approx(200.0, rel=1e-6)
    assert dec["final_qty"] == 0.5  # not resized
    assert dec["resized"] is False


def test_mode_apply_resizes(monkeypatch, tmp_path):
    """apply → replaces sized_qty with the conviction-driven size."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "apply")
    monkeypatch.setenv("CONVICTION_SIZING_DIRECTION", "symmetric")
    pkg = _Pkg(conviction=1.0)  # risk_qty = 2%*100000/10 = 200
    out = cs.apply_conviction_sizing(pkg, 0.5, account_name="bybit_1", **_LARGE)
    assert out == pytest.approx(200.0, rel=1e-6)
    dec = pkg.meta["conviction_apply_decision"]
    assert dec["mode"] == "apply"
    assert dec["resized"] is True


# --------------------------------------------------------------------------- #
# account allowlist
# --------------------------------------------------------------------------- #


def test_account_not_in_allowlist_noop(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "apply")
    monkeypatch.setenv("CONVICTION_SIZING_DIRECTION", "symmetric")
    monkeypatch.setenv("CONVICTION_SIZING_ACCOUNTS", "bybit_1, bybit_2")
    pkg = _Pkg(conviction=1.0)
    out = cs.apply_conviction_sizing(pkg, 0.5, account_name="alpaca_paper", **_LARGE)
    assert out == 0.5  # not on the allowlist → unchanged
    assert "conviction_apply_decision" not in pkg.meta


def test_account_in_allowlist_applies(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "apply")
    monkeypatch.setenv("CONVICTION_SIZING_DIRECTION", "symmetric")
    monkeypatch.setenv("CONVICTION_SIZING_ACCOUNTS", "bybit_1")
    pkg = _Pkg(conviction=1.0)
    out = cs.apply_conviction_sizing(pkg, 0.5, account_name="bybit_1", **_LARGE)
    assert out == pytest.approx(200.0, rel=1e-6)


def test_empty_allowlist_means_all(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "apply")
    monkeypatch.setenv("CONVICTION_SIZING_DIRECTION", "symmetric")
    monkeypatch.setenv("CONVICTION_SIZING_ACCOUNTS", "")
    pkg = _Pkg(conviction=1.0)
    out = cs.apply_conviction_sizing(pkg, 0.5, account_name="any_account", **_LARGE)
    assert out == pytest.approx(200.0, rel=1e-6)


# --------------------------------------------------------------------------- #
# direction sub-mode
# --------------------------------------------------------------------------- #


def test_reductive_never_enlarges(monkeypatch, tmp_path):
    """reductive → final = min(conviction_qty, sized_qty); never larger."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "apply")
    monkeypatch.setenv("CONVICTION_SIZING_DIRECTION", "reductive")
    pkg = _Pkg(conviction=1.0)  # conviction_qty = 200, but sized_qty = 0.5
    out = cs.apply_conviction_sizing(pkg, 0.5, account_name="bybit_1", **_LARGE)
    assert out == 0.5  # min(200, 0.5) — never enlarges


def test_reductive_shrinks_when_conviction_smaller(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "apply")
    monkeypatch.setenv("CONVICTION_SIZING_DIRECTION", "reductive")
    # conviction 0.5 → risk_qty 200 * 0.5 = 100; sized_qty 150 → min = 100
    pkg = _Pkg(conviction=0.5)
    out = cs.apply_conviction_sizing(pkg, 150.0, account_name="bybit_1", **_LARGE)
    assert out == pytest.approx(100.0, rel=1e-6)


def test_reductive_default_when_direction_unset(monkeypatch, tmp_path):
    """Unset direction → reductive (the safe default; never enlarges)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "apply")
    monkeypatch.delenv("CONVICTION_SIZING_DIRECTION", raising=False)
    pkg = _Pkg(conviction=1.0)
    out = cs.apply_conviction_sizing(pkg, 0.5, account_name="bybit_1", **_LARGE)
    assert out == 0.5  # reductive default — would-be 200 clamped to 0.5


def test_symmetric_can_exceed_up_to_budget(monkeypatch, tmp_path):
    """symmetric → may exceed sized_qty, bounded by the 2% budget."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "apply")
    monkeypatch.setenv("CONVICTION_SIZING_DIRECTION", "symmetric")
    pkg = _Pkg(conviction=1.0)
    out = cs.apply_conviction_sizing(pkg, 0.5, account_name="bybit_1", **_LARGE)
    # exceeds 0.5 but is exactly the 2% budget qty (200), never more
    assert out == pytest.approx(200.0, rel=1e-6)
    assert out > 0.5


def test_symmetric_bounded_by_margin_cap(monkeypatch, tmp_path):
    """symmetric never breaches the margin ceiling (hard upper bound)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "apply")
    monkeypatch.setenv("CONVICTION_SIZING_DIRECTION", "symmetric")
    pkg = _Pkg(conviction=1.0, entry=100.0, sl=99.0)  # tight stop → big risk_qty
    out = cs.apply_conviction_sizing(
        pkg, 0.5, account_name="bybit_1",
        balance_usd=100000.0, available_usd=10.0,
        total_account_usd=100000.0, leverage=1, market_type="linear",
    )
    margin_cap = (10.0 * 1) / 100.0  # = 0.1
    assert 0.0 <= out <= margin_cap + 1e-9


# --------------------------------------------------------------------------- #
# below-floor → journaled refusal + qty 0
# --------------------------------------------------------------------------- #


def test_below_floor_journals_refusal_and_returns_zero(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "apply")
    monkeypatch.setenv("CONVICTION_SIZING_DIRECTION", "symmetric")
    # Force a positive no-trade floor so a low conviction trips it.
    monkeypatch.setattr(cs, "NO_TRADE_FLOOR", 0.5)

    calls: list[dict] = []

    def _fake_log(pkg, account_cfg, *, reason, status, sized_qty=None):
        calls.append(
            {"account_cfg": account_cfg, "reason": reason,
             "status": status, "sized_qty": sized_qty}
        )
        return True

    monkeypatch.setattr(
        "src.units.accounts.execute.log_rejection_to_journal", _fake_log
    )

    pkg = _Pkg(conviction=0.1)  # below the 0.5 floor
    out = cs.apply_conviction_sizing(pkg, 0.5, account_name="bybit_1", **_LARGE)
    assert out == 0.0
    assert len(calls) == 1
    assert calls[0]["status"] == "rejected"
    assert "no_trade_floor" in calls[0]["reason"]
    assert calls[0]["sized_qty"] == 0.0


def test_below_floor_in_annotate_does_not_resize(monkeypatch, tmp_path):
    """In annotate mode a below-floor conviction stamps but does NOT resize."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "annotate")
    monkeypatch.setattr(cs, "NO_TRADE_FLOOR", 0.5)
    pkg = _Pkg(conviction=0.1)
    out = cs.apply_conviction_sizing(pkg, 0.5, account_name="bybit_1", **_LARGE)
    assert out == 0.5  # unchanged — annotate never resizes/refuses


# --------------------------------------------------------------------------- #
# daily-loss clamp
# --------------------------------------------------------------------------- #


def test_daily_loss_clamp_caps_enlargement(monkeypatch, tmp_path):
    """effective_risk_pct caps the conviction-implied risk fraction.

    Full 2% budget would size 200; a throttled effective_risk_pct of 1% caps it
    to half that (100) so the daily-loss-throttled account isn't re-inflated.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "apply")
    monkeypatch.setenv("CONVICTION_SIZING_DIRECTION", "symmetric")
    pkg = _Pkg(conviction=1.0)
    out = cs.apply_conviction_sizing(
        pkg, 0.5, account_name="bybit_1", effective_risk_pct=0.01, **_LARGE
    )
    assert out == pytest.approx(100.0, rel=1e-6)  # 1%/2% * 200
    dec = pkg.meta["conviction_apply_decision"]
    assert dec["daily_loss_clamp"]["cap_qty"] == pytest.approx(100.0, rel=1e-6)


def test_daily_loss_clamp_none_skips(monkeypatch, tmp_path):
    """effective_risk_pct=None → no extra clamp (full 2% budget)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "apply")
    monkeypatch.setenv("CONVICTION_SIZING_DIRECTION", "symmetric")
    pkg = _Pkg(conviction=1.0)
    out = cs.apply_conviction_sizing(
        pkg, 0.5, account_name="bybit_1", effective_risk_pct=None, **_LARGE
    )
    assert out == pytest.approx(200.0, rel=1e-6)


def test_daily_loss_clamp_no_inflation_above_budget(monkeypatch, tmp_path):
    """An effective_risk_pct ABOVE 2% never lifts the cap past the 2% budget."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "apply")
    monkeypatch.setenv("CONVICTION_SIZING_DIRECTION", "symmetric")
    pkg = _Pkg(conviction=1.0)
    out = cs.apply_conviction_sizing(
        pkg, 0.5, account_name="bybit_1", effective_risk_pct=0.10, **_LARGE
    )
    assert out == pytest.approx(200.0, rel=1e-6)  # min(2%, 10%) = 2%


def test_daily_loss_clamp_bad_value_fail_inert(monkeypatch, tmp_path):
    """A non-numeric effective_risk_pct never inflates — fail-inert."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "apply")
    monkeypatch.setenv("CONVICTION_SIZING_DIRECTION", "symmetric")
    pkg = _Pkg(conviction=1.0)
    out = cs.apply_conviction_sizing(
        pkg, 0.5, account_name="bybit_1", effective_risk_pct="oops", **_LARGE
    )
    # bad value is swallowed; full budget applies (never larger than budget)
    assert out == pytest.approx(200.0, rel=1e-6)


# --------------------------------------------------------------------------- #
# composition order — advisory/news still shrink a conviction-sized base
# --------------------------------------------------------------------------- #


def test_composition_advisory_news_shrink_conviction_base(monkeypatch, tmp_path):
    """Conviction produces the base; advisory + news reducers still shrink it.

    Simulates the coordinator composition order:
        base = apply_conviction_sizing(...)  # symmetric → 200
        base = apply_advisory_downsize(...)  # ×0.5      → 100
        base = apply_news_downsize(...)      # ×0.5      → 50
    """
    from src.runtime import advisory_sizing, news_sizing

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "apply")
    monkeypatch.setenv("CONVICTION_SIZING_DIRECTION", "symmetric")

    pkg = _Pkg(conviction=1.0)
    base = cs.apply_conviction_sizing(pkg, 0.5, account_name="bybit_1", **_LARGE)
    assert base == pytest.approx(200.0, rel=1e-6)

    # advisory + news are reductive multipliers on top of the conviction base.
    monkeypatch.setattr(
        advisory_sizing, "apply_advisory_downsize",
        lambda p, qty, *, account_name="": qty * 0.5,
    )
    monkeypatch.setattr(
        news_sizing, "apply_news_downsize",
        lambda p, qty, *, account_name="": qty * 0.5,
    )
    after_advisory = advisory_sizing.apply_advisory_downsize(
        pkg, base, account_name="bybit_1"
    )
    after_news = news_sizing.apply_news_downsize(
        pkg, after_advisory, account_name="bybit_1"
    )
    assert after_advisory == pytest.approx(100.0, rel=1e-6)
    assert after_news == pytest.approx(50.0, rel=1e-6)  # both reducers shrank it


# --------------------------------------------------------------------------- #
# fail-inert
# --------------------------------------------------------------------------- #


def test_fail_inert_on_exception(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "apply")

    class _Boom:
        strategy = "x"
        symbol = "BTCUSDT"

        @property
        def meta(self):
            raise RuntimeError("boom")

    out = cs.apply_conviction_sizing(_Boom(), 0.5, account_name="bybit_1", **_LARGE)
    assert out == 0.5  # unchanged on any error


def test_fail_inert_none_conviction(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "apply")
    monkeypatch.setenv("CONVICTION_SIZING_DIRECTION", "symmetric")
    pkg = _Pkg(conviction=None)  # no conviction stamped
    out = cs.apply_conviction_sizing(pkg, 0.5, account_name="bybit_1", **_LARGE)
    assert out == 0.5  # unchanged — missing conviction is fail-inert
    assert "conviction_apply_decision" not in pkg.meta


def test_zero_sized_qty_untouched(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "apply")
    monkeypatch.setenv("CONVICTION_SIZING_DIRECTION", "symmetric")
    pkg = _Pkg(conviction=0.9)
    out = cs.apply_conviction_sizing(pkg, 0.0, account_name="bybit_1", **_LARGE)
    assert out == 0.0  # RiskManager refusal left untouched


def test_unknown_mode_degrades_to_off(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONVICTION_SIZING_MODE", "garbage")
    pkg = _Pkg(conviction=1.0)
    out = cs.apply_conviction_sizing(pkg, 0.5, account_name="bybit_1", **_LARGE)
    assert out == 0.5  # unknown → off → no-op


# --------------------------------------------------------------------------- #
# settings-dict override (parity with the env path)
# --------------------------------------------------------------------------- #


def test_settings_dict_overrides_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CONVICTION_SIZING_MODE", raising=False)
    pkg = _Pkg(conviction=1.0)
    out = cs.apply_conviction_sizing(
        pkg, 0.5, account_name="bybit_1",
        settings={"CONVICTION_SIZING_MODE": "apply",
                  "CONVICTION_SIZING_DIRECTION": "symmetric"},
        **_LARGE,
    )
    assert out == pytest.approx(200.0, rel=1e-6)


# --------------------------------------------------------------------------- #
# coordinator integration — wiring + composition order + mode=off no-op
# --------------------------------------------------------------------------- #


def test_coordinator_wires_apply_before_advisory_and_news():
    """The coordinator inserts apply_conviction_sizing per Design B Option A:
    position_size → apply_conviction_sizing → advisory → news → annotate."""
    import inspect

    from src.core import coordinator

    src = inspect.getsource(coordinator.Coordinator.multi_account_execute)
    i_size = src.index("position_size(")
    i_apply = src.index("apply_conviction_sizing(")
    i_advisory = src.index("apply_advisory_downsize(")
    i_news = src.index("apply_news_downsize(")
    i_annotate = src.index("annotate_conviction_sizing(")
    # base produced by position_size, conviction applies first, advisory + news
    # downsizes reduce on top, observe-only annotator stays last.
    assert i_size < i_apply < i_advisory < i_news < i_annotate


def test_coordinator_apply_call_is_byte_for_byte_noop_when_off(monkeypatch, tmp_path):
    """The exact kwargs the coordinator passes are a no-op when mode=off, so the
    integration leaves the sized qty identical to today's behaviour."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CONVICTION_SIZING_MODE", raising=False)

    class _RM:
        leverage = 1
        min_qty = 0.0
        qty_precision = 3
        risk_pct = 0.01

    pkg = _Pkg(conviction=1.0)
    rm = _RM()
    # Mirror the coordinator call site exactly.
    out = cs.apply_conviction_sizing(
        pkg, 0.5, account_name="bybit_1",
        balance_usd=100000.0,
        available_usd=1_000_000.0,
        total_account_usd=1_000_000.0,
        leverage=getattr(rm, "leverage", 0),
        market_type="linear",
        min_qty=getattr(rm, "min_qty", 0.0),
        qty_precision=getattr(rm, "qty_precision", 3),
        effective_risk_pct=getattr(rm, "risk_pct", None),
    )
    assert out == 0.5  # identical to the RiskManager-sized qty
    assert "conviction_apply_decision" not in pkg.meta


# --------------------------------------------------------------------------- #
# env-gate-guard compliance — *_MODE / *_DIRECTION, never *_ENABLED
# --------------------------------------------------------------------------- #


def test_flag_helpers_are_mode_and_direction_not_enabled():
    from src.runtime import runtime_flags

    # tri-state mode
    assert runtime_flags._conviction_sizing_mode({"CONVICTION_SIZING_MODE": "apply"}) == "apply"
    assert runtime_flags._conviction_sizing_mode({"CONVICTION_SIZING_MODE": "annotate"}) == "annotate"
    assert runtime_flags._conviction_sizing_mode({"CONVICTION_SIZING_MODE": "off"}) == "off"
    assert runtime_flags._conviction_sizing_mode({"CONVICTION_SIZING_MODE": "typo"}) == "off"
    assert runtime_flags._conviction_sizing_mode({}) == "off"

    # direction sub-mode
    assert runtime_flags._conviction_sizing_direction(
        {"CONVICTION_SIZING_DIRECTION": "symmetric"}
    ) == "symmetric"
    assert runtime_flags._conviction_sizing_direction(
        {"CONVICTION_SIZING_DIRECTION": "typo"}
    ) == "reductive"
    assert runtime_flags._conviction_sizing_direction({}) == "reductive"

    # allowlist
    assert runtime_flags._conviction_sizing_accounts(
        {"CONVICTION_SIZING_ACCOUNTS": "a, b ,c"}
    ) == ["a", "b", "c"]
    assert runtime_flags._conviction_sizing_accounts({}) == []
