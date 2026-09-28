"""Every declared prop account consults the prop status snapshot on EVERY path.

PI-20260921-E16-BALANCE-FETCHER-PROP-BRANCH-RARELY-REACHED; operator "Fix it",
2026-09-28. Until this change the stale-balance refusal
(`src.prop.prop_balance.prop_sizing_balance`) lived inside
`Coordinator._default_balance_fetcher` and was reached only when the live lookup
was attempted, returned None, and no `cached_balance_usd` was set. And for a
breakout bridge account the fetcher was never called at all, so for
`breakout_1` it was reached on ZERO paths.

The three balance states the item names: CACHED (`cached_balance_usd` set),
LIVE-FETCHED (the dispatch round's `get_account_balances()` returned a number),
FALLBACK (no live row, no cache). A prop account must refuse on a stale
snapshot in all three, and still emit on a fresh one. A non-prop control must
be byte-for-byte unchanged: it sizes off cached / live / fallback and never
reads the prop snapshot.
"""
from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

from src.core.coordinator import Coordinator, OrderPackage

_ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      breakout_1:
        type: prop
        exchange: breakout
        account_class: prop
        strategies: [trend_donchian_sol]
        risk:
          max_dd_pct: 0.05
          daily_usd: 150
          pos_size: 500
          risk_pct: 0.015
          min_balance_usd: 0
      bybit_ctl:
        type: regular
        exchange: bybit
        strategies: [trend_donchian_sol]
        risk:
          max_dd_pct: 0.05
          daily_usd: 200
          pos_size: 1000
""")

STATES = ("cached", "live", "fallback")


def _pkg() -> OrderPackage:
    return OrderPackage(
        strategy="trend_donchian_sol", symbol="SOLUSDT", direction="long",
        entry=150.0, sl=145.5, tp=175.5, confidence=0.68,
        meta={"strategy_name": "trend_donchian_sol",
              "entry_reason": "trend_donchian_sol signal"},
    )


@pytest.fixture()
def env(tmp_path, monkeypatch):
    p = tmp_path / "accounts.yaml"
    p.write_text(_ACCOUNTS_YAML)
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    import src.core.coordinator as coord_mod
    monkeypatch.setattr(coord_mod, "_log_new_order_package", lambda pkg: None)
    units = tmp_path / "units.yaml"
    units.write_text("units: {}\n")
    return {"path": str(p), "coord": Coordinator(units_path=str(units)),
            "mp": monkeypatch}


def _arrange(env, state: str, snapshot: str):
    """Put BOTH accounts in `state`, and the prop snapshot in `snapshot`."""
    mp = env["mp"]
    from src.units.accounts import load_accounts
    accounts = load_accounts(env["path"])
    rows = []
    for a in accounts:
        if state == "cached":
            a.cached_balance_usd = 7_000.0
        elif state == "live":
            rows.append({"account_id": a.name, "total_usdt": 8_000.0})
    import src.units.accounts as accounts_mod
    mp.setattr(accounts_mod, "load_accounts", lambda path=None: accounts)
    import src.units.ui.processor as proc
    mp.setattr(proc, "get_account_balances", lambda *a, **k: rows)

    calls = []
    import src.prop.prop_balance as pb

    def fake_snapshot(account_id):
        calls.append(account_id)
        if snapshot == "ok":
            return "ok", 5_000.0, {"account_id": account_id, "source": "equity"}
        return snapshot, None, {"account_id": account_id, "max_age_hours": 24.0,
                                "reason": "snapshot 30.0h old"}

    mp.setattr(pb, "prop_sizing_balance", fake_snapshot)
    mp.setattr(pb, "note_refusal", lambda *a, **k: False)

    # Capture the balance each NON-bridge account is sized off.
    sized = {}
    ctl = next(a for a in accounts if a.name == "bybit_ctl")
    orig = ctl.risk_manager.position_size

    def spy(pkg, balance, *a, **k):
        sized["bybit_ctl"] = balance
        return orig(pkg, balance, *a, **k)

    mp.setattr(ctl.risk_manager, "position_size", spy)
    return calls, sized


def _run(env, **kw):
    with patch("src.prop.breakout_executor.emit_prop_ticket",
               return_value="prop-manual-deadbeef") as emit:
        results = env["coord"].multi_account_execute(
            _pkg(), accounts_path=env["path"], dry_run=True, **kw)
    by = {r["name"]: r for r in results}
    return by, emit


@pytest.mark.parametrize("state", STATES)
def test_prop_account_refuses_on_a_stale_snapshot_in_every_state(env, state):
    calls, _ = _arrange(env, state, snapshot="stale")
    by, emit = _run(env)
    assert "breakout_1" in calls, f"{state}: prop snapshot was never consulted"
    assert by["breakout_1"]["trade_id"] is None
    assert "prop_balance_stale" in (by["breakout_1"]["error"] or "")
    assert not by["breakout_1"].get("sized_qty")  # refused rows report 0.0
    assert emit.call_count == 0


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("snapshot", ["absent", "error"])
def test_every_refusing_snapshot_state_refuses_in_every_balance_state(env, state, snapshot):
    calls, _ = _arrange(env, state, snapshot=snapshot)
    by, emit = _run(env)
    assert "breakout_1" in calls
    assert by["breakout_1"]["trade_id"] is None
    assert "prop_balance_" in (by["breakout_1"]["error"] or "")


@pytest.mark.parametrize("state", STATES)
def test_prop_account_still_proceeds_on_a_fresh_snapshot_in_every_state(env, state):
    calls, _ = _arrange(env, state, snapshot="ok")
    by, _emit = _run(env)
    assert "breakout_1" in calls
    assert "prop_balance_" not in (by["breakout_1"]["error"] or "")
    # Reached the bridge sizing sentinel: the ruleset size downstream is
    # unchanged by this fix (dry_run, so no ticket is emitted here).
    assert by["breakout_1"].get("sized_qty") == 1.0


def test_a_caller_balance_fetcher_cannot_pre_empt_the_prop_check(env):
    calls, _ = _arrange(env, "fallback", snapshot="stale")
    by, _emit = _run(env, balance_fetcher=lambda _a: 10_000.0)
    assert "breakout_1" in calls
    assert "prop_balance_stale" in (by["breakout_1"]["error"] or "")


def test_a_pkg_meta_balance_override_cannot_pre_empt_the_prop_check(env):
    calls, _ = _arrange(env, "fallback", snapshot="stale")
    pkg = _pkg()
    pkg.meta["account_balances_usd"] = {"breakout_1": 10_000.0, "bybit_ctl": 10_000.0}
    with patch("src.prop.breakout_executor.emit_prop_ticket",
               return_value="prop-manual-deadbeef"):
        results = env["coord"].multi_account_execute(
            pkg, accounts_path=env["path"], dry_run=True)
    by = {r["name"]: r for r in results}
    assert "breakout_1" in calls
    assert "prop_balance_stale" in (by["breakout_1"]["error"] or "")


@pytest.mark.parametrize("state,expected", [("cached", 7_000.0), ("live", 8_000.0),
                                            ("fallback", 0.0)])
def test_non_prop_control_is_unchanged(env, state, expected):
    """The control never reads the prop snapshot and sizes off exactly the
    value the fetcher's cached / live / fallback branch yields, as before."""
    calls, sized = _arrange(env, state, snapshot="stale")
    _run(env)
    assert "bybit_ctl" not in calls
    assert sized.get("bybit_ctl") == expected
