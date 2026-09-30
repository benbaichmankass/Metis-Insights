"""A side_filter-suppressed package names its cause ON THE JOURNAL ROW.

PI-20260928-SVBNZOVH-0001. ``accounts.yaml::side_filter`` demotes a suppressed
direction to dry (``Coordinator.multi_account_execute``, "Per-ACCOUNT
directional gate"), deliberately keeping the would-be trade as a counterfactual
row. Until this fix that row was an ordinary ``dry_run_no_order_placed`` row:
on ``alpaca_live`` (``mode: live``) a suppressed short was indistinguishable in
the DB from a shadow or mode demotion, and the only record of the cause was a
logger line journald keeps for ~30 minutes.

These tests run a real package through ``multi_account_execute`` and the REAL
``execute_pkg`` dry branch into a tmp ``trade_journal.db``, then read
``notes.dry_cause`` off the written row.
"""
from __future__ import annotations

import json
import sqlite3
import textwrap

import pytest

from src.core.coordinator import Coordinator, OrderPackage

_YAML = textwrap.dedent("""\
    accounts:
      alpaca_x:
        type: regular
        exchange: alpaca
        api_key_env: ALPACA_KEY_X
        mode: {mode}
        strategies: [spy_trend_long_1d]
        risk:
          max_dd_pct: 0.05
          daily_usd: 200
          pos_size: 1000
""")


def _pkg(direction):
    return OrderPackage(
        strategy="spy_trend_long_1d", symbol="SPY", direction=direction,
        entry=500.0, sl=490.0 if direction == "long" else 510.0,
        tp=530.0 if direction == "long" else 470.0, confidence=0.7,
        meta={"strategy_name": "spy_trend_long_1d"},
    )


class _Client:
    def account_status(self):
        return {"shorting_enabled": True}


@pytest.fixture()
def run(tmp_path, monkeypatch):
    db = tmp_path / "tj.db"
    monkeypatch.setenv("ALPACA_KEY_X", "k")
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db))
    import src.core.coordinator as cm
    monkeypatch.setattr(cm, "_log_new_order_package", lambda pkg: None)
    monkeypatch.setattr(cm, "_has_open_position", lambda *a, **k: False)
    import src.units.accounts.clients as clients
    monkeypatch.setattr(clients, "alpaca_client_for", lambda cfg: _Client())
    import src.runtime.positions as positions
    monkeypatch.setattr(positions, "current_net_position_qty", lambda *a, **k: 0.0)
    units = tmp_path / "units.yaml"
    units.write_text("units: {}\n")

    def _go(direction, *, side_filter="both", execution="live", mode="live",
            dry_run=None):
        p = tmp_path / "accounts.yaml"
        p.write_text(_YAML.format(mode=mode))
        import src.runtime.account_side_filter as asf
        monkeypatch.setattr(asf, "account_side_filter", lambda _id: side_filter)
        import src.strategy_registry as reg
        monkeypatch.setattr(reg, "execution_mode", lambda _s: execution)
        coord = Coordinator(units_path=str(units))
        coord.multi_account_execute(
            _pkg(direction), accounts_path=str(p), dry_run=dry_run,
            balance_fetcher=lambda _a: 10_000.0)
        if not db.exists():
            return []
        con = sqlite3.connect(str(db))
        try:
            rows = con.execute("SELECT notes FROM trades").fetchall()
        except sqlite3.OperationalError:  # no row was ever written
            rows = []
        finally:
            con.close()
        return [json.loads(r[0] or "{}") for r in rows]

    return _go


def test_side_filter_suppressed_short_names_its_cause_on_the_row(run):
    """The clears_when of PI-20260928-SVBNZOVH-0001, verbatim in shape: a short
    through multi_account_execute on a side_filter:long account, the cause read
    off the journal row."""
    rows = run("short", side_filter="long")
    assert len(rows) == 1, rows
    assert rows[0]["reason"] == "dry_run_no_order_placed"
    assert rows[0]["is_dry"] is True
    assert rows[0]["dry_cause"] == "side_filter:long"


def test_shadow_and_mode_demotions_are_distinguishable_from_side_filter(run):
    shadow = run("short", execution="shadow")
    assert [r.get("dry_cause") for r in shadow] == ["execution:shadow"]


def test_account_mode_dry_run_was_already_distinguishable(run):
    """A ``mode: dry_run`` account is refused earlier, by the RiskManager, with
    its own reason token — so that case never needed the stamp. Pinned so the
    three causes stay mutually distinguishable from the row alone."""
    rows = run("long", mode="dry_run")
    assert [r.get("reason") for r in rows] == ["account_mode_dry_run"]


def test_process_override_is_named(run):
    rows = run("long", dry_run=True)
    assert [r.get("dry_cause") for r in rows] == ["process_override"]


def test_a_permitted_direction_is_not_demoted(run, monkeypatch):
    """Control: side_filter:long leaves a LONG untouched — no dry row, so the
    cause stamp cannot be riding on every package."""
    import src.units.accounts.execute as ex
    seen = []
    monkeypatch.setattr(
        ex, "execute_pkg",
        lambda pkg, acc, exchange_client=None, dry_run=None, **kw:
            seen.append((bool(dry_run), acc.get("dry_cause"))) or "live-1",
    )
    run("long", side_filter="long")
    assert seen == [(False, None)]
