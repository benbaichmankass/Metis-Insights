"""Per-LEG flat risk on a prop account (PROP-PER-LEG-SIZING, PI-20261005-YUVCGTMJ-0006).

``sizing.flat.per_leg_risk_usd: {strategy: usd}`` overrides the account's
risk_pct for the named legs only. Unlisted legs are untouched, and a value can
never exceed ``sizing.flat.max_risk_usd`` (fail-closed at load, independent of
the runtime risk-gate mode).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.prop import prop_sizing
from tests.test_prop_sizing_mode import (  # noqa: F401 — fixtures + helpers
    _SOL, _breakout_1_cfg, _emit, _isolated,
)

_TURBO = Path(__file__).resolve().parents[1] / "config" / "prop_rulesets" / "breakout_turbo_1step.yaml"
_ETH = dict(_SOL, symbol="ETHUSDT", entry=3000.0, sl=2950.0, tp=3120.0,
            strategy="trend_donchian_eth_prop")


def _ruleset(tmp_path: Path, **flat) -> Path:
    data = yaml.safe_load(_TURBO.read_text())
    data["sizing"]["flat"].update(flat)
    p = tmp_path / "prop_rulesets" / "x.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data))
    return p


def test_default_is_no_override(tmp_path):
    d = prop_sizing.resolve("breakout_2", ruleset_path=_TURBO, risk_pct=0.5,
                            strategy="trend_donchian_sol_prop")
    assert d == prop_sizing.SizingDecision(mode="flat", cap_usd=25.0)


def test_listed_leg_gets_its_value_unlisted_gets_account_value(tmp_path):
    p = _ruleset(tmp_path, max_risk_usd=50, per_leg_risk_usd={"trend_donchian_sol_prop": 50})
    sol = prop_sizing.resolve("a", ruleset_path=p, risk_pct=0.5, strategy="trend_donchian_sol_prop")
    assert (sol.risk_usd, sol.cap_usd) == (50.0, 50.0)
    eth = prop_sizing.resolve("a", ruleset_path=p, risk_pct=0.5, strategy="trend_donchian_eth_prop")
    assert eth.risk_usd is None and eth.cap_usd == 50.0
    assert prop_sizing.resolve("a", ruleset_path=p, risk_pct=0.5).risk_usd is None


@pytest.mark.parametrize("flat,why", [
    ({"max_risk_usd": 25, "per_leg_risk_usd": {"s": 25.01}}, "exceeds"),
    ({"max_risk_usd": 25, "per_leg_risk_usd": {"s": 0}}, "> 0"),
    ({"max_risk_usd": 25, "per_leg_risk_usd": {"s": -5}}, "> 0"),
    ({"max_risk_usd": 25, "per_leg_risk_usd": {"s": "abc"}}, "not a number"),
    ({"max_risk_usd": 25, "per_leg_risk_usd": {"s": True}}, "> 0"),
    ({"max_risk_usd": 25, "per_leg_risk_usd": [1]}, "mapping"),
    ({"max_risk_usd": None, "per_leg_risk_usd": {"s": 10}}, "max_risk_usd"),
])
def test_invalid_per_leg_refuses_at_load(tmp_path, flat, why):
    p = _ruleset(tmp_path, **flat)
    with pytest.raises(ValueError, match=why):
        prop_sizing.load_sizing_config(p)


def test_per_leg_in_room_mode_refuses(tmp_path):
    data = yaml.safe_load(_TURBO.read_text())
    data["sizing"]["mode"] = "room"
    data["sizing"]["flat"]["per_leg_risk_usd"] = {"s": 10}
    p = tmp_path / "r.yaml"
    p.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="flat-mode only"):
        prop_sizing.load_sizing_config(p)


def _breakout_2_cfg(tmp_path, **flat):
    from src.config.accounts_loader import load_accounts_dict
    acct = dict(load_accounts_dict()["breakout_2"])
    acct["account_id"] = "breakout_2"
    acct["backtest_ruleset"] = str(_ruleset(tmp_path, **flat))
    return acct


@pytest.mark.parametrize("gm", ["off", "annotate", "enforce"])
def test_executor_tickets_carry_per_leg_risk(tmp_path, monkeypatch, gm):
    monkeypatch.setenv("PROP_TICKET_RISK_GATE_MODE", gm)
    acct = _breakout_2_cfg(tmp_path, max_risk_usd=50,
                           per_leg_risk_usd={"trend_donchian_eth_prop": 25,
                                             "trend_donchian_sol_prop": 50})
    _, sol = _emit(dict(_SOL), acct)
    _, eth = _emit(dict(_ETH), acct)
    assert sol.risk_usd == 50.0 and abs(sol.qty_units - 50.0 / 5.0) < 1e-9
    assert eth.risk_usd == 25.0 and abs(eth.qty_units - 25.0 / 50.0) < 1e-9


def test_executor_unlisted_leg_keeps_account_value_even_under_higher_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("PROP_TICKET_RISK_GATE_MODE", "enforce")
    acct = _breakout_2_cfg(tmp_path, max_risk_usd=50,
                           per_leg_risk_usd={"trend_donchian_sol_prop": 50})
    _, eth = _emit(dict(_ETH), acct)
    assert eth.risk_usd == 25.0  # account risk_pct 0.5% x $5k, NOT the $50 cap


def test_per_leg_journal_row_records_the_override(tmp_path, monkeypatch):
    from src.prop import prop_journal
    rows = []
    monkeypatch.setattr(prop_journal, "record_ticket", lambda row: rows.append(row))
    acct = _breakout_2_cfg(tmp_path, max_risk_usd=50,
                           per_leg_risk_usd={"trend_donchian_sol_prop": 50})
    _emit(dict(_SOL), acct)
    em = [r for r in rows if r["status"] == "emitted"]
    assert em[0]["meta"]["sizing"]["per_leg_risk_usd"] == 50.0


def test_shipped_config_declares_no_per_leg_override_yet():
    """The $25/$50 breakout_2 split is a HELD Tier-3 proposal (see PR body)."""
    cfg = prop_sizing.load_sizing_config(_TURBO)
    assert cfg.per_leg_risk_usd == {} and cfg.flat_max_risk_usd == 25.0
