"""Firm rule guards declared per ruleset (TRADEIFY-WIRE T2).

Pins: Tradeify's leverage caps (5:1 BTC/ETH, 2:1 altcoins) and its daily floor
(prior close minus 3% of the ACCOUNT SIZE, reset 22:00 UTC) refuse in the
executor and the ticket emitter under ``breach_guards: enforce``; breakout_1
declares neither rule and is unchanged.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.prop import prop_executor as pe
from src.prop import prop_rule_guards as g
from src.prop.platform.base import AccountSnapshot

NOW = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)
CAPS = {"BTCUSDT": 5.0, "ETHUSDT": 5.0, "default": 2.0}


# ── pure ──────────────────────────────────────────────────────────────────


def test_caps_parse_and_fall_back_to_default():
    caps = g.leverage_caps({"leverage_caps": {"ethusdt": 5, "default": 2, "bad": "x"}})
    assert caps == {"ETHUSDT": 5.0, "default": 2.0}
    assert g.leverage_cap_for(caps, "ETHUSDT") == 5.0
    assert g.leverage_cap_for(caps, "XRPUSDT") == 2.0
    assert g.leverage_caps({}) == {} and g.leverage_cap_for({}, "XRPUSDT") is None


def test_xrp_tight_stop_breaches_two_to_one():
    # manager's rerun: XRP entry 0.60, SL 0.599 (0.17% stop), $50 risk
    qty = 50.0 / (0.60 - 0.599)
    why = g.leverage_breach(symbol="XRPUSDT", notional_usd=qty * 0.60, caps=CAPS, basis_usd=10_000.0)
    assert why and "2:1" in why
    # the same size on ETH is within 5:1
    assert g.leverage_breach(symbol="ETHUSDT", notional_usd=30_000.0, caps=CAPS, basis_usd=10_000.0) is None


def test_undeclared_rule_never_refuses_and_unknown_inputs_refuse_when_declared():
    assert g.leverage_breach(symbol="XRPUSDT", notional_usd=1e9, caps={}, basis_usd=1.0) is None
    assert "could not look" in g.leverage_breach(symbol="XRPUSDT", notional_usd=None, caps=CAPS,
                                                 basis_usd=10_000.0)


def test_basis_is_the_smaller_of_nominal_and_live():
    assert g.leverage_basis(10_000.0, (9_500.0, None)) == 9_500.0
    assert g.leverage_basis(10_000.0, (12_000.0,)) == 10_000.0
    assert g.leverage_basis(None, ()) is None


def test_daily_floor_account_size_basis_vs_balance_basis():
    # Tradeify: prior close 10,200 minus 3% of the $10k size = 9,900
    assert g.daily_floor(daily_loss_pct=0.03, day_start_balance=10_200.0, account_size_usd=10_000.0,
                         amount_basis="account_size") == pytest.approx(9_900.0)
    # undeclared: the pre-existing expression, exactly
    assert g.daily_floor(daily_loss_pct=0.03, day_start_balance=10_200.0, account_size_usd=10_000.0,
                         amount_basis=None) == 10_200.0 * (1.0 - 0.03)
    assert g.daily_floor(daily_loss_pct=0.03, day_start_balance=None, account_size_usd=10_000.0,
                         amount_basis="account_size") is None


# ── the ruleset files ─────────────────────────────────────────────────────


def test_tradeify_declares_both_rules_and_breakout_declares_neither():
    t = pe.load_config("tradeify_1")
    assert t.leverage_caps == CAPS and t.daily_loss_amount_basis == "account_size"
    assert t.daily_reset_utc == "22:00" and t.breach_guards == "enforce"
    b = pe.load_config("breakout_1")
    assert b.leverage_caps == {} and b.daily_loss_amount_basis is None
    assert b.breach_guards == "report"


# ── executor guards ───────────────────────────────────────────────────────


def _cfg(**kw):
    base = dict(account_id="tradeify_1", account_size_usd=10_000.0, daily_loss_pct=0.03, max_dd_pct=0.06,
                daily_reset_utc="22:00", safety_margin_usd=5.0, risk_cap_usd=75.0,
                symbols={"XRPUSDT": {"venue": "XRPUSD", "cvpp": 1.0, "lot_units": 1.0, "min_lots": 1.0,
                                     "lot_step": 1.0}},
                leverage_caps=dict(CAPS), daily_loss_amount_basis="account_size")
    base.update(kw)
    return pe.ExecutorConfig(**base)


def _guards(qty, c, ds=10_000.0, equity=10_000.0):
    t = {"ticket_id": "prop-manual-x", "symbol": "XRPUSDT", "direction": "long", "entry": 0.60, "sl": 0.599,
         "tp": 0.61, "qty": qty, "valid_until": (NOW + timedelta(minutes=30)).isoformat()}
    spec, facts, refusal = pe.bracket_from_ticket(t, c)
    return pe.evaluate_guards(ticket=t, spec=spec, facts=facts, refusal=refusal,
                              account=AccountSnapshot(balance=equity, equity=equity, unrealized=0.0,
                                                      realized_today=0.0),
                              day_start_balance=ds, open_risk_usd=0.0, open_risk_state="no_open_positions",
                              cfg=c, now=NOW)


def test_executor_refuses_a_ticket_over_the_leverage_cap():
    v = _guards(50_000, _cfg())               # $30,000 notional > 2 x $10,000
    assert not v.fits and any(r.startswith("leverage: XRPUSDT") for r in v.reasons)
    ok = _guards(30_000, _cfg())              # $18,000 notional: within
    assert ok.fits, ok.reasons
    assert ok.checks["leverage"]["cap"] == 2.0


def test_executor_report_mode_alerts_instead_of_refusing():
    v = _guards(50_000, _cfg(breach_guards="report"))
    assert v.fits and any(r.startswith("leverage:") for r in v.breach_reports)


def test_executor_without_declared_caps_never_checks_leverage():
    v = _guards(50_000, _cfg(leverage_caps={}))
    assert v.fits and "leverage" not in v.checks


def test_executor_daily_floor_uses_account_size_basis():
    # day start 10,200: floor 9,900 (not 9,894). Equity 9,930, ticket risk $30
    # → after-stops 9,900 <= 9,900 + $5 margin: refused on the Tradeify floor,
    # which the balance-based floor (9,894 + 5) would have let through.
    v = _guards(30_000, _cfg(), ds=10_200.0, equity=9_930.0)
    assert v.checks["daily_floor"] == pytest.approx(9_900.0)
    assert any(r.startswith("daily loss:") for r in v.reasons)
    legacy = _guards(30_000, _cfg(daily_loss_amount_basis=None), ds=10_200.0, equity=9_930.0)
    assert legacy.checks["daily_floor"] == pytest.approx(9_894.0)
    assert not any(r.startswith("daily loss:") for r in legacy.reasons)


def test_prop_day_rolls_at_22_utc():
    assert pe.trading_day(datetime(2026, 9, 30, 21, 59, tzinfo=timezone.utc), "22:00") != \
        pe.trading_day(datetime(2026, 9, 30, 22, 0, tzinfo=timezone.utc), "22:00")


# ── ticket emitter ────────────────────────────────────────────────────────


def _unit(path):
    return SimpleNamespace(source=path, account_size_usd=10_000.0, ruleset=None)


def test_emitter_leverage_refusal_for_tradeify_only(monkeypatch):
    from src.prop import breakout_executor as be
    from src.prop import prop_balance

    monkeypatch.setattr(prop_balance, "prop_sizing_balance", lambda a: ("ok", 10_000.0, {}))
    repo = Path(__file__).resolve().parents[1] / "config" / "prop_rulesets"
    tradeify, breakout = _unit(repo / "tradeify_247_1step.yaml"), _unit(repo / "breakout.yaml")
    why = be._leverage_refusal("tradeify_1", tradeify, "XRPUSDT", 50_000, 0.60, 1.0)
    assert why and "2:1" in why
    assert be._leverage_refusal("tradeify_1", tradeify, "XRPUSDT", 30_000, 0.60, 1.0) is None
    assert be._leverage_refusal("tradeify_1", tradeify, "ETHUSDT", 12.0, 2_500.0, 1.0) is None  # $30k < 5:1
    assert be._leverage_refusal("breakout_1", breakout, "XRPUSDT", 50_000, 0.60, 1.0) is None


def test_emitter_basis_shrinks_with_the_live_balance(monkeypatch):
    from src.prop import breakout_executor as be
    from src.prop import prop_balance

    monkeypatch.setattr(prop_balance, "prop_sizing_balance", lambda a: ("ok", 8_000.0, {}))
    unit = _unit(Path(__file__).resolve().parents[1] / "config" / "prop_rulesets" / "tradeify_247_1step.yaml")
    # $18,000 fits 2 x $10,000 but not 2 x $8,000
    assert be._leverage_refusal("tradeify_1", unit, "XRPUSDT", 30_000, 0.60, 1.0)


# ── rule distance (the ticket gate's daily cushion) ───────────────────────


def test_rule_distance_daily_amount_is_three_percent_of_size_for_tradeify(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    from src.prop import prop_reconcile

    status = {"balance": 10_200.0, "equity": 10_200.0, "day_start_balance": 10_200.0,
              "realized_today": 0.0, "unrealized": 0.0}
    t = prop_reconcile.compute_rule_distance("tradeify_1", status=status)
    assert t["daily_loss_limit_usd"] == pytest.approx(300.0)
    b = prop_reconcile.compute_rule_distance("breakout_1", status=status)
    assert b["daily_loss_limit_usd"] == pytest.approx(0.03 * 10_200.0)
