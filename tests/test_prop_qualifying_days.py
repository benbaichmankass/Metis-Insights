"""Velotrade qualifying-day counter: a day counts only at >= 0.8% of INITIAL balance
(ruleset phases.evaluation.qualifying_day_profit_pct = 0.008 -> $40 on $5,000)."""
from __future__ import annotations

from datetime import datetime, timezone

from src.prop import qualifying_days as qd

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def _f(pnl, closed, status="closed"):
    return {"pnl": pnl, "closed_at": closed, "status": status}


def _run(fills):
    return qd.count_qualifying_days(fills, initial_balance=5000.0, threshold_pct=0.008,
                                    required_days=5, now=NOW)


def test_day_just_under_and_over_threshold():
    out = _run([_f(39.99, "2026-10-05T10:00:00Z"), _f(40.0, "2026-10-06T10:00:00Z")])
    st = {r["day"]: r["status"] for r in out["recent_days"]}
    assert st == {"2026-10-05": "below", "2026-10-06": "qualifying"}
    assert out["qualifying_days"] == 1 and out["days_remaining"] == 4
    assert out["min_days_met"] is False


def test_fills_net_within_day_and_reset_boundary():
    # 00:29Z belongs to the PREVIOUS firm day; 00:31Z to the new one.
    out = _run([_f(30.0, "2026-10-05T23:00:00Z"), _f(15.0, "2026-10-06T00:29:00Z"),
                _f(50.0, "2026-10-06T00:31:00Z")])
    d = {r["day"]: r["pnl_usd"] for r in out["recent_days"]}
    assert d == {"2026-10-05": 45.0, "2026-10-06": 50.0}
    assert out["qualifying_days"] == 2


def test_null_pnl_is_unmeasured_not_zero_or_disqualified():
    out = _run([_f(None, "2026-10-05T10:00:00Z"), _f(100.0, "2026-10-05T11:00:00Z")])
    r = out["recent_days"][0]
    assert r["status"] == "unmeasured" and r["pnl_usd"] is None
    assert out["qualifying_days"] == 0 and out["count_is_lower_bound"] is True


def test_unplaceable_fill_makes_lower_bound():
    out = _run([_f(60.0, None), _f(60.0, "2026-10-05T10:00:00Z")])
    assert out["unplaceable_fills"] == 1 and out["count_is_lower_bound"] is True
    assert "+" in qd.standing_line({**out})


def test_in_progress_day_not_in_settled_count_and_open_fills_ignored():
    out = _run([_f(80.0, "2026-10-07T09:00:00Z"), _f(500.0, "2026-10-06T09:00:00Z", status="open")])
    assert out["qualifying_days"] == 0
    assert out["today_in_progress"]["status"] == "qualifying"


def test_not_declared_and_unreadable_are_null_not_zero():
    assert qd.count_qualifying_days([], initial_balance=5000.0, threshold_pct=None,
                                    required_days=5)["qualifying_days"] is None
    assert qd.standing_line({"state": "not_declared"}) is None
    assert "(unreadable)" in qd.standing_line({"state": "unreadable", "qualifying_days": None})


def test_no_fills_is_measured_zero_with_threshold():
    out = _run([])
    assert out["state"] == "measured" and out["qualifying_days"] == 0
    assert out["threshold_usd"] == 40.0
    assert qd.standing_line(out).startswith("qualifying days 0/5 (≥$40.00/day")


def test_effective_dates_binding_flag():
    out = qd.effective_dates_block({"minimum_trading_days": "2026-08-22", "later": "2027-01-01",
                                    "bad": "x"}, NOW)
    assert out["minimum_trading_days"]["binding"] is True
    assert out["later"]["binding"] is False and out["bad"]["binding"] is None


def test_compute_on_real_velotrade_ruleset(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    out = qd.compute("velotrade_1", NOW)
    assert out["state"] == "measured" and out["threshold_usd"] == 40.0
    assert out["required_days"] == 5 and out["qualifying_days"] == 0
    assert out["rule_effective_dates"]["minimum_trading_days"]["binding"] is True
    assert qd.compute("breakout_1", NOW)["state"] == "not_declared"
