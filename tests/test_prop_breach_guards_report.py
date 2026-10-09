"""``breach_guards: enforce | report`` — the replaceable-prop-account switch.

Operator 2026-09-28 (~13:40Z, ~13:45Z, relayed by the manager and confirmed
by the operator in the PROP-EXEC lane's popup): "we can breach the account
and just buy a new one ... We shouldn't reject tickets because the risk will
breach." breakout_1 is declared ``report``; every other account stays
``enforce``.

2026-10-09: the RiskManager's DAILY_LOSS_CAP / INTRADAY_DRAWDOWN refusals
that ``report`` softened no longer exist (folded into the account-level
``daily_dd_switch``, operator directive), so their report-mode tests were
removed. Still proven here: the mode parse (``enforce`` is the default and
the fallback for any typo), dry-run is never softened, and the ticket's "DO NOT PLACE" block
becomes one informational line for a ``report`` account only, and
"cushion unknown" (could not look) is never softened.
"""
from __future__ import annotations

import logging

import pytest

from src.core.coordinator import OrderPackage
from src.prop import prop_risk_gate
from src.units.accounts.risk import RiskManager, breach_guards_mode


def _pkg() -> OrderPackage:
    return OrderPackage(strategy="trend_donchian_sol_prop", symbol="SOLUSDT", direction="long",
                        entry=120.0, sl=118.0, tp=126.0, confidence=1.0,
                        meta={"strategy_name": "trend_donchian_sol_prop"})


def _rm(mode=None, **kw) -> RiskManager:
    cfg = {"max_dd_pct": 0.06, "daily_usd": 150}
    if mode is not None:
        cfg["breach_guards"] = mode
    rm = RiskManager(cfg, account_id="", **kw)
    return rm


@pytest.mark.parametrize("raw,want", [(None, "enforce"), ("report", "report"), ("REPORT ", "report"),
                                      ("enforce", "enforce"), ("reprot", "enforce"), ("", "enforce")])
def test_mode_parse_defaults_to_enforce(raw, want):
    assert breach_guards_mode({} if raw is None else {"breach_guards": raw}) == want
    assert breach_guards_mode(None) == "enforce"


def test_report_never_overrides_dry_run():
    rm = _rm("report", dry_run=True)
    rm.daily_pnl = -200.0
    assert rm.evaluate(_pkg()) == (False, "account_mode_dry_run")


def test_only_breakout_1_is_report_in_the_real_config():
    import yaml
    from pathlib import Path
    data = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "accounts.yaml").read_text())
    accounts = data["accounts"]
    report = sorted(a for a, c in accounts.items()
                    if isinstance(c, dict) and breach_guards_mode(c.get("risk")) == "report")
    assert report == ["breakout_1"]
    # tradeify_1 (TRADEIFY-WIRE) deliberately ENFORCES: manager review of
    # #14672 — "'report' places orders through a breach; it must refuse".
    assert prop_risk_gate.breach_guards_for("tradeify_1") == "enforce"
    assert prop_risk_gate.breach_guards_for("breakout_1") == "report"
    assert prop_risk_gate.breach_guards_for("bybit_2") == "enforce"
    assert prop_risk_gate.breach_guards_for("no_such_account") == "enforce"
    assert prop_risk_gate.breach_guards_for(None) == "enforce"


EXCEEDS = prop_risk_gate.grade_ticket_risk(risk_usd=75.0, distance_to_dd_floor_usd=24.0,
                                           distance_to_daily_loss_usd=150.0, status_freshness="ok")
UNKNOWN = prop_risk_gate.grade_ticket_risk(risk_usd=75.0, status_freshness="stale")


def test_caveat_enforce_keeps_do_not_place():
    blob = "\n".join(prop_risk_gate.caveat_lines(EXCEEDS))
    assert "DO NOT PLACE" in blob


def test_caveat_report_is_informational():
    blob = "\n".join(prop_risk_gate.caveat_lines(EXCEEDS, breach_guards="report"))
    assert "DO NOT PLACE" not in blob and "skip" not in blob.lower()
    assert "accepted, the account is replaceable" in blob and "$24.00" in blob


def test_caveat_report_does_not_soften_could_not_look():
    assert prop_risk_gate.caveat_lines(UNKNOWN, breach_guards="report") == prop_risk_gate.caveat_lines(UNKNOWN)
    assert any("CUSHION UNKNOWN" in ln for ln in prop_risk_gate.caveat_lines(UNKNOWN, breach_guards="report"))


def test_rendered_ticket_uses_the_accounts_mode(monkeypatch):
    from datetime import datetime, timezone

    from src.prop import breakout_ticket as bt
    monkeypatch.setattr(prop_risk_gate, "mode", lambda: "annotate")
    monkeypatch.setattr(prop_risk_gate, "grade_account_ticket_risk", lambda a, risk_usd: EXCEEDS)
    monkeypatch.setattr(prop_risk_gate, "record_ticket_risk_soak", lambda *a, **k: None)
    sig = bt.BreakoutSignal(strategy="s", symbol="SOLUSDT", direction="long", entry=120.0, sl=118.0,
                            tp=126.0, timeframe="1h", signal_time=datetime(2026, 9, 28, tzinfo=timezone.utc))
    t = bt.build_ticket(sig, bt.TicketConfig())
    report = bt.render_ticket(t, account_id="breakout_1")
    assert "DO NOT PLACE" not in report and "accepted, the account is replaceable" in report
    monkeypatch.setattr(prop_risk_gate, "breach_guards_for", lambda a: "enforce")
    assert "DO NOT PLACE" in bt.render_ticket(t, account_id="some_other_prop")


def test_breach_guards_for_reads_the_same_block_as_prop_risk_manager(monkeypatch):
    import src.config.accounts_loader as al
    monkeypatch.setattr(al, "load_accounts_dict", lambda *a, **k: {
        "p_risk": {"risk": {"breach_guards": "report"}},
        "p_flat": {"breach_guards": "report"},          # no risk block → PropRiskManager reads the account
        "p_both": {"breach_guards": "report", "risk": {"max_dd_pct": 0.06}},
    })
    assert prop_risk_gate.breach_guards_for("p_risk") == "report"
    assert prop_risk_gate.breach_guards_for("p_flat") == "report"
    assert prop_risk_gate.breach_guards_for("p_both") == "enforce"   # risk block wins, as in prop_risk.py
