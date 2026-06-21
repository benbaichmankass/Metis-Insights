"""Tests for src.prop.breakout_ticket — the POC trade-setup ticket builder."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.prop.breakout_ticket import BreakoutSignal, TicketConfig, build_ticket, render_ticket

_T0 = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)


def _long_sig(**kw):
    base = dict(strategy="squeeze_breakout_4h", symbol="BTCUSDT", direction="long",
                entry=60000.0, sl=58800.0, tp=63600.0, timeframe="4h", signal_time=_T0)
    base.update(kw)
    return BreakoutSignal(**base)


def test_sizing_risk_dollars_and_qty():
    # 0.6% of $5000 = $30 risk; SL distance 1200 → qty 0.025 (crypto-native cvpp=1)
    t = build_ticket(_long_sig(), TicketConfig(account_size_usd=5000, risk_pct=0.6))
    assert t.risk_usd == pytest.approx(30.0)
    assert t.qty_units == pytest.approx(30.0 / 1200.0)
    assert t.rr == pytest.approx(3.0)  # (63600-60000)/(60000-58800)=3600/1200


def test_entry_band_long_clamped_above_sl():
    t = build_ticket(_long_sig(), TicketConfig(entry_band_frac=0.25))
    # band = 0.25 * 1200 = 300 → [59700, 60300], and min stays above SL
    assert t.entry_min == pytest.approx(59700.0)
    assert t.entry_max == pytest.approx(60300.0)
    assert t.entry_min > _long_sig().sl


def test_entry_band_short_clamped_below_sl():
    sig = _long_sig(direction="short", entry=60000.0, sl=61200.0, tp=56400.0)
    t = build_ticket(sig, TicketConfig(entry_band_frac=0.25))
    assert t.side == "Sell"
    assert t.entry_max == pytest.approx(60300.0)
    assert t.entry_max < sig.sl


def test_ttl_by_timeframe():
    t4 = build_ticket(_long_sig(timeframe="4h"), TicketConfig(ttl_bars=1.0))
    assert t4.valid_until == _T0 + timedelta(minutes=240)
    t15 = build_ticket(_long_sig(timeframe="15m"), TicketConfig(ttl_bars=1.0))
    assert t15.valid_until == _T0 + timedelta(minutes=15)


def test_zero_stop_distance_rejected():
    with pytest.raises(ValueError):
        build_ticket(_long_sig(entry=60000.0, sl=60000.0), TicketConfig())


def test_render_contains_invariants():
    t = build_ticket(_long_sig(), TicketConfig(dxtrade_symbol="BTCUSD"))
    out = render_ticket(t)
    # The hard invariants must travel in the message text.
    assert "BRACKET" in out
    assert "SL" in out and "TP" in out
    # The manual "pause for confirmation" step was intentionally removed — the
    # prop bridge runs as automatically as the executor allows; the bracket +
    # validity guards are the safety net, not a manual confirm.
    assert "confirmation before you submit" not in out
    assert "Pause for my confirmation" not in out
    assert "skipped: stale/out-of-range" in out
    assert "Valid until" in out
    assert "BTCUSD" in out
    # prop context numbers for the $5k account
    assert "$150" in out   # 3% daily
    assert "$300" in out   # 6% static DD floor


def test_render_flags_expired():
    t = build_ticket(_long_sig(), TicketConfig())
    later = t.valid_until + timedelta(minutes=1)
    assert "ALREADY EXPIRED" in render_ticket(t, now=later)
    assert "ALREADY EXPIRED" not in render_ticket(t, now=t.valid_until - timedelta(minutes=1))


def test_render_carries_live_balance_sizing_instruction():
    # The bot can't see the live prop-platform balance, so the ticket must
    # tell the placer to recompute the FINAL size against the live balance:
    # the risk %, the per-unit risk, and the formula must all be present, and
    # the bot's qty must be labelled a SUGGESTION (BL-20260619-PROP-GATE-BALANCE
    # + the prop sizing-framework split).
    t = build_ticket(
        _long_sig(),  # entry 60000, sl 58800 → stop distance 1200
        TicketConfig(account_size_usd=5000, risk_pct=1.5,
                     contract_value_usd_per_point=1.0),
    )
    out = render_ticket(t)
    assert "Sizing" in out
    assert "1.5% of your CURRENT live balance" in out
    assert "FINAL size" in out
    assert "SUGGESTED size at the nominal" in out
    # per-unit risk = stop distance (1200) × contract_value (1.0) = $1200.0000
    assert "1200.0000" in out
