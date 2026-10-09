"""PROP-B2-STARVED (2026-10-09): a phone-flow prop account emits without a
fresh balance snapshot; every other prop account still refuses.

MEASURED on the live trader (journalctl ict-trader-live, 2026-10-07 01:59 ..
2026-10-08 16:00Z): all 7 trend_donchian_eth_prop signals that produced
tradeify_1 / velotrade_1 tickets refused breakout_2 with
``prop_balance_absent: breakout_2 has no operator-reported account status``.
breakout_2's snapshot source is the phone (``kind: account_status``), which reads
the account panel only while foregrounded on the terminal, and it foregrounds
only for a waiting ticket -- so the refusal could never clear by itself.

These tests use the REAL config/prop_platforms.yaml, so they also pin that
breakout_2 is the phone account.
"""
from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

from src.core.coordinator import Coordinator, OrderPackage

_ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      breakout_2:
        type: prop
        exchange: breakout
        account_class: prop
        strategies: [trend_donchian_eth_prop]
        risk: {max_dd_pct: 0.03, daily_usd: 150, risk_pct: 0.005, min_balance_usd: 0}
      tradeify_1:
        type: prop
        exchange: breakout
        account_class: prop
        strategies: [trend_donchian_eth_prop]
        risk: {max_dd_pct: 0.03, daily_usd: 300, risk_pct: 0.005, min_balance_usd: 0}
      velotrade_1:
        type: prop
        exchange: breakout
        account_class: prop
        strategies: [trend_donchian_eth_prop]
        risk: {max_dd_pct: 0.03, daily_usd: 300, risk_pct: 0.005, min_balance_usd: 0}
""")


def _pkg() -> OrderPackage:
    return OrderPackage(
        strategy="trend_donchian_eth_prop", symbol="ETHUSDT", direction="short",
        entry=2471.31, sl=2532.16, tp=2226.65, confidence=1.0,
        meta={"strategy_name": "trend_donchian_eth_prop",
              "entry_reason": "trend_donchian_eth_prop signal"},
    )


@pytest.fixture()
def env(tmp_path, monkeypatch):
    p = tmp_path / "accounts.yaml"
    p.write_text(_ACCOUNTS_YAML)
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    from src.units.db.database import Database
    Database(db_path=str(tmp_path / "trade_journal.db"))  # the live journal has `trades`
    import src.core.coordinator as coord_mod
    monkeypatch.setattr(coord_mod, "_log_new_order_package", lambda pkg: None)
    units = tmp_path / "units.yaml"
    units.write_text("units: {}\n")
    pages = []
    import src.prop.prop_balance as pb
    monkeypatch.setattr(pb, "note_refusal", lambda *a, **k: pages.append(a) or True)
    return {"path": str(p), "coord": Coordinator(units_path=str(units)),
            "mp": monkeypatch, "pages": pages}


def _snapshots(env, by_account):
    import src.prop.prop_balance as pb

    def fake(account_id):
        state = by_account.get(account_id, "ok")
        if state == "ok":
            return "ok", 10_000.0, {"account_id": account_id, "source": "equity"}
        return state, None, {"account_id": account_id, "max_age_hours": 24.0,
                             "reason": "no prop_account_status row"}

    env["mp"].setattr(pb, "prop_sizing_balance", fake)


def _run(env, dry_run=False):
    with patch("src.prop.breakout_executor.emit_prop_ticket",
               side_effect=lambda order, cfg, **k: f"prop-manual-{cfg.get('account_id')}") as emit:
        results = env["coord"].multi_account_execute(
            _pkg(), accounts_path=env["path"], dry_run=dry_run)
    emitted = sorted(c.args[1].get("account_id") for c in emit.call_args_list)
    return {r["name"]: r for r in results}, emitted


def test_breakout_2_is_the_phone_flow_account_in_the_real_config():
    from src.prop.platform import FLOW_PHONE, ticket_flow
    assert ticket_flow("breakout_2") == FLOW_PHONE
    assert ticket_flow("tradeify_1") != FLOW_PHONE
    assert ticket_flow("velotrade_1") != FLOW_PHONE


@pytest.mark.parametrize("snapshot", ["absent", "stale", "error"])
def test_signal_emits_breakout_2_alongside_tradeify_1_and_velotrade_1(env, snapshot):
    """THE REGRESSION: the live shape on 2026-10-08 15:26Z (breakout_2 has no
    snapshot, the two DXtrade accounts have fresh feeds)."""
    _snapshots(env, {"breakout_2": snapshot})
    by, emitted = _run(env)
    assert emitted == ["breakout_2", "tradeify_1", "velotrade_1"]
    assert by["breakout_2"]["trade_id"] == "prop-manual-breakout_2"
    assert "prop_balance_" not in (by["breakout_2"].get("error") or "")
    # NO-HALT: it still raises ONE red flag (the refusal page) while emitting.
    assert [a[1] for a in env["pages"] if a[0] != "ok"] == ["breakout_2"]


@pytest.mark.parametrize("snapshot", ["absent", "stale"])
def test_a_non_phone_prop_account_still_refuses_on_a_missing_snapshot(env, snapshot):
    """NEGATIVE CONTROL: the per-trade refusal is unchanged for an account whose
    snapshot comes from a VM feed (tradeify_1), so the exception is phone-only."""
    _snapshots(env, {"tradeify_1": snapshot})
    by, emitted = _run(env)
    assert emitted == ["breakout_2", "velotrade_1"]
    assert by["tradeify_1"]["trade_id"] is None
    assert f"prop_balance_{snapshot}" in (by["tradeify_1"]["error"] or "")


def test_a_dry_dispatch_still_emits_nothing_for_breakout_2(env):
    _snapshots(env, {"breakout_2": "absent"})
    by, emitted = _run(env, dry_run=True)
    assert emitted == []
    assert [a for a in env["pages"] if a[0] != "ok"] == []
