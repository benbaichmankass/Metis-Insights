"""Tests for the prop_signal emitter (src/prop/breakout_notify.py).

Asserts the Breakout ticket fans out as a TYPED prop_signal FCM push + a
Telegram message, that the Telegram leg suppresses the generic FCM mirror (no
double push), that the payload is all-strings (FCM contract), and that each leg
is isolated so one failure never sinks the other or raises.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.prop import breakout_notify
from src.prop.breakout_ticket import BreakoutSignal, TicketConfig, build_ticket


def _ticket():
    sig = BreakoutSignal(
        strategy="squeeze_breakout_4h", symbol="BTCUSDT", direction="long",
        entry=60000.0, sl=58800.0, tp=63600.0, timeframe="4h",
        signal_time=datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc),
    )
    return build_ticket(sig, TicketConfig(account_size_usd=5000.0, risk_pct=0.6))


def test_ticket_to_fields_all_strings_with_text():
    f = breakout_notify.ticket_to_fields(_ticket())
    assert all(isinstance(v, str) for v in f.values())  # FCM string→string contract
    assert f["symbol"] == "BTCUSDT"
    assert f["side"] == "Buy"
    assert f["direction"] == "long"
    assert f["entry"] == "60000"
    assert "BREAKOUT TRADE SETUP" in f["text"]   # full rendered ticket rides in `text`


def test_emit_fans_out_both_legs(monkeypatch):
    import src.runtime.mobile_push as mp
    import src.runtime.notify as notify

    pushed = {}
    tg = {}
    monkeypatch.setattr(mp, "publish_event",
                        lambda kind, payload: pushed.update(kind=kind, payload=payload))
    monkeypatch.setattr(notify, "send_telegram_direct",
                        lambda message, **kw: tg.update(message=message, kw=kw))

    out = breakout_notify.emit_prop_signal(_ticket())

    assert out == {"push": True, "telegram": True}
    assert pushed["kind"] == "prop_signal"
    assert pushed["payload"]["symbol"] == "BTCUSDT"
    # the Telegram leg must NOT also fire the generic FCM mirror (no double push)
    assert tg["kw"].get("mirror_to_fcm") is False
    assert "BREAKOUT TRADE SETUP" in tg["message"]


def test_legs_are_isolated(monkeypatch):
    import src.runtime.mobile_push as mp
    import src.runtime.notify as notify

    def _boom(*a, **k):
        raise RuntimeError("fcm down")

    tg = {}
    monkeypatch.setattr(mp, "publish_event", _boom)
    monkeypatch.setattr(notify, "send_telegram_direct",
                        lambda message, **kw: tg.update(ok=True))

    out = breakout_notify.emit_prop_signal(_ticket())   # must not raise
    assert out["push"] is False        # push failed, isolated
    assert out["telegram"] is True     # telegram still attempted
    assert tg.get("ok") is True


def test_push_only_and_telegram_only(monkeypatch):
    import src.runtime.mobile_push as mp
    import src.runtime.notify as notify
    calls = {"push": 0, "tg": 0}
    monkeypatch.setattr(mp, "publish_event", lambda *a, **k: calls.__setitem__("push", calls["push"] + 1))
    monkeypatch.setattr(notify, "send_telegram_direct", lambda *a, **k: calls.__setitem__("tg", calls["tg"] + 1))

    breakout_notify.emit_prop_signal(_ticket(), telegram=False)
    breakout_notify.emit_prop_signal(_ticket(), push=False)
    assert calls == {"push": 1, "tg": 1}
