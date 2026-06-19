"""Tests for PropRiskManager — generic prop-account risk gates.

Covers:
  - State machine: evaluation + mission complete → SKIP_MISSION_MET.
  - State machine: evaluation + behind plan → allow.
  - State machine: funded → behaves like base RiskManager.
  - Time windows: overnight + weekend → SKIP_OVERNIGHT_RESTRICTED /
    SKIP_WEEKEND_RESTRICTED.
  - Base risk gates still trip with structured reasons.
  - Coordinator routing: skip reasons surface in result['error'].
  - Loader: type=prop → PropRiskManager; type=regular → RiskManager.
  - Loader: enabled=False accounts are filtered out at load.
  - Deprecated breakout exchange stays inert.
  - Config wiring: the dead velotrade integration is purged and every
    account carries a valid account_class.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.core.coordinator import OrderPackage
from src.units.accounts.risk import RiskManager
from src.units.accounts.prop_risk import PropRiskManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pkg(symbol: str = "BTCUSDT", entry: float = 100.0, sl: float = 99.0,
         tp: float = 102.0, direction: str = "long") -> OrderPackage:
    return OrderPackage(
        strategy="vwap",
        symbol=symbol,
        direction=direction,
        entry=entry,
        sl=sl,
        tp=tp,
        meta={},
    )


def _base_cfg(**overrides):
    cfg = {
        "account_state": "evaluation",
        "phase_requirements": {
            "target_profit_pct": 0.05,
            "min_active_days": 4,
            "min_daily_profit_pct": 0.005,
        },
        "prop_state": {
            "cumulative_pnl_pct": 0.0,
            "active_days": 0,
            "entry_date": None,
        },
        "overnight_restricted": False,
        "weekend_restricted": False,
        "risk": {
            "max_dd_pct": 0.02,
            "daily_usd": 50,
            "pos_size": 200,
            "risk_pct": 0.005,
            "min_balance_usd": 50,
        },
    }
    cfg.update(overrides)
    return cfg


# ---------------------------------------------------------------------------
# Mission state machine
# ---------------------------------------------------------------------------


class TestMissionGate:
    def test_evaluation_allowed_when_behind_plan(self):
        rm = PropRiskManager(_base_cfg())
        ok, reason = rm.evaluate(_pkg())
        assert ok is True
        assert reason is None

    def test_evaluation_skipped_when_mission_complete(self):
        cfg = _base_cfg(prop_state={
            "cumulative_pnl_pct": 0.06,   # past +5% target
            "active_days": 5,             # past 4-day minimum
            "entry_date": "2026-04-29",
        })
        rm = PropRiskManager(cfg)
        ok, reason = rm.evaluate(_pkg())
        assert ok is False
        assert reason == "SKIP_MISSION_MET"

    def test_evaluation_allowed_when_only_profit_met(self):
        # profit hit but days short → still need to satisfy time-in-seat.
        cfg = _base_cfg(prop_state={
            "cumulative_pnl_pct": 0.10,
            "active_days": 1,
            "entry_date": "2026-05-02",
        })
        rm = PropRiskManager(cfg)
        ok, reason = rm.evaluate(_pkg())
        assert ok is True
        assert reason is None

    def test_evaluation_allowed_when_only_days_met(self):
        cfg = _base_cfg(prop_state={
            "cumulative_pnl_pct": 0.01,
            "active_days": 6,
            "entry_date": "2026-04-27",
        })
        rm = PropRiskManager(cfg)
        ok, reason = rm.evaluate(_pkg())
        assert ok is True

    def test_funded_skips_mission_check(self):
        # A funded account that would have "mission complete" if it were
        # an evaluation MUST still trade (that's the whole point of being
        # funded).
        cfg = _base_cfg(
            account_state="funded",
            prop_state={"cumulative_pnl_pct": 0.10, "active_days": 99,
                        "entry_date": None},
        )
        rm = PropRiskManager(cfg)
        ok, reason = rm.evaluate(_pkg())
        assert ok is True
        assert reason is None


# ---------------------------------------------------------------------------
# Time windows
# ---------------------------------------------------------------------------


class TestTimeWindows:
    def test_overnight_window_blocks(self):
        rm = PropRiskManager(_base_cfg(
            overnight_restricted=True,
            overnight_window=[22, 6],
        ))
        # 23:00 UTC on a Wednesday → inside the wrap-around window.
        midnight = datetime(2026, 5, 6, 23, 0, tzinfo=timezone.utc)
        ok, reason = rm.evaluate(_pkg(), now=midnight)
        assert ok is False
        assert reason == "SKIP_OVERNIGHT_RESTRICTED"

    def test_overnight_window_allows_outside(self):
        rm = PropRiskManager(_base_cfg(
            overnight_restricted=True,
            overnight_window=[22, 6],
            weekend_restricted=False,  # so 14:00 isn't a weekend trip
        ))
        midday = datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc)
        ok, reason = rm.evaluate(_pkg(), now=midday)
        assert ok is True
        assert reason is None

    def test_overnight_window_wrap_pre_midnight(self):
        rm = PropRiskManager(_base_cfg(
            overnight_restricted=True,
            overnight_window=[22, 6],
        ))
        late_evening = datetime(2026, 5, 6, 22, 30, tzinfo=timezone.utc)
        ok, reason = rm.evaluate(_pkg(), now=late_evening)
        assert ok is False
        assert reason == "SKIP_OVERNIGHT_RESTRICTED"

    def test_overnight_window_wrap_post_midnight(self):
        rm = PropRiskManager(_base_cfg(
            overnight_restricted=True,
            overnight_window=[22, 6],
        ))
        early_am = datetime(2026, 5, 6, 5, 30, tzinfo=timezone.utc)
        ok, reason = rm.evaluate(_pkg(), now=early_am)
        assert ok is False
        assert reason == "SKIP_OVERNIGHT_RESTRICTED"

    def test_weekend_blocks(self):
        # Saturday 2026-05-09
        rm = PropRiskManager(_base_cfg(weekend_restricted=True))
        sat = datetime(2026, 5, 9, 14, 0, tzinfo=timezone.utc)
        ok, reason = rm.evaluate(_pkg(), now=sat)
        assert ok is False
        assert reason == "SKIP_WEEKEND_RESTRICTED"

    def test_sunday_blocks(self):
        rm = PropRiskManager(_base_cfg(weekend_restricted=True))
        sun = datetime(2026, 5, 10, 14, 0, tzinfo=timezone.utc)
        ok, reason = rm.evaluate(_pkg(), now=sun)
        assert ok is False
        assert reason == "SKIP_WEEKEND_RESTRICTED"

    def test_weekday_allowed(self):
        rm = PropRiskManager(_base_cfg(
            overnight_restricted=False,
            weekend_restricted=True,
        ))
        wed = datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc)
        ok, _ = rm.evaluate(_pkg(), now=wed)
        assert ok is True

    def test_disabled_overnight_does_not_block(self):
        rm = PropRiskManager(_base_cfg(overnight_restricted=False))
        midnight = datetime(2026, 5, 6, 3, 0, tzinfo=timezone.utc)
        ok, _ = rm.evaluate(_pkg(), now=midnight)
        assert ok is True


# ---------------------------------------------------------------------------
# Base gate inheritance
# ---------------------------------------------------------------------------


class TestBaseGateInheritance:
    def test_daily_loss_cap_trips_with_reason(self):
        rm = PropRiskManager(_base_cfg())
        rm.daily_pnl = -100.0  # past -50 cap
        ok, reason = rm.evaluate(_pkg())
        assert ok is False
        assert reason == "DAILY_LOSS_CAP"

    def test_position_size_cap_trips_with_reason(self):
        rm = PropRiskManager(_base_cfg())
        pkg = _pkg()
        pkg.meta["estimated_value"] = 5000.0  # past 200 cap
        ok, reason = rm.evaluate(pkg)
        assert ok is False
        assert reason == "POSITION_SIZE_CAP"

    def test_intraday_drawdown_trips_with_reason(self):
        rm = PropRiskManager(_base_cfg())
        rm.update_equity(1000.0)
        rm.update_equity(900.0)  # 10% drawdown vs 2% cap
        ok, reason = rm.evaluate(_pkg())
        assert ok is False
        assert reason == "INTRADAY_DRAWDOWN"

    def test_smoke_test_bypasses_all_gates(self):
        # mission complete + overnight + weekend, but smoke-test wins.
        cfg = _base_cfg(
            overnight_restricted=True,
            weekend_restricted=True,
            prop_state={"cumulative_pnl_pct": 0.5, "active_days": 99,
                        "entry_date": None},
        )
        rm = PropRiskManager(cfg)
        pkg = _pkg()
        pkg.meta["is_test"] = True
        sat_midnight = datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc)
        ok, reason = rm.evaluate(pkg, now=sat_midnight)
        assert ok is True
        assert reason is None


class TestBaseRiskManagerEvaluate:
    """Base RiskManager.evaluate() returns the same skip vocabulary so
    coordinator code is uniform across regular + prop accounts."""

    def test_base_evaluate_returns_tuple(self):
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 100, "pos_size": 500})
        ok, reason = rm.evaluate(_pkg())
        assert ok is True
        assert reason is None

    def test_base_evaluate_daily_loss_reason(self):
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 100, "pos_size": 500})
        rm.daily_pnl = -200.0
        ok, reason = rm.evaluate(_pkg())
        assert ok is False
        assert reason == "DAILY_LOSS_CAP"

    def test_base_approve_still_returns_bool(self):
        # Existing callers (TradingAccount.place_order legacy path) must
        # continue to get a plain bool from approve().
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 100, "pos_size": 500})
        assert rm.approve(_pkg()) is True
        rm.daily_pnl = -200.0
        assert rm.approve(_pkg()) is False


# ---------------------------------------------------------------------------
# State updates
# ---------------------------------------------------------------------------


class TestStateUpdates:
    def test_record_trade_result_updates_cumulative_pnl_pct(self):
        rm = PropRiskManager(_base_cfg())
        rm.update_equity(10_000.0)
        rm.record_trade_result(100.0)
        assert rm.cumulative_pnl_pct == pytest.approx(0.01)

    def test_record_trade_result_with_explicit_seed(self):
        rm = PropRiskManager(_base_cfg())
        rm.record_trade_result(50.0, starting_equity_usd=5_000.0)
        assert rm.cumulative_pnl_pct == pytest.approx(0.01)

    def test_active_days_increments_per_calendar_day(self):
        rm = PropRiskManager(_base_cfg())
        rm.record_trade_result(10.0, starting_equity_usd=10_000.0)
        first = rm.active_days
        # Same UTC day → no increment.
        rm.record_trade_result(10.0, starting_equity_usd=10_000.0)
        assert rm.active_days == first


# ---------------------------------------------------------------------------
# Journal-sourced daily-risk state (BL-20260617-PROP-RISK-ACCOUNT-ID)
# ---------------------------------------------------------------------------


class TestJournalSourcedDailyRisk:
    """PropRiskManager wires ``account_name`` through as the base
    RiskManager's ``account_id``, so its daily-loss cap is rebuilt from the
    canonical journal (and survives a restart) instead of resetting to 0 in
    memory. Regular accounts already got this via
    ``RiskManager(..., account_id=name)`` in the loader; prop accounts were
    dropping it (the in-memory-only bug)."""

    def test_account_name_wires_through_to_account_id(self):
        rm = PropRiskManager(_base_cfg(), account_name="prop_x")
        assert rm.account_id == "prop_x"

    def test_no_account_name_stays_in_memory(self):
        # Nameless construction (unit tests / one-off callers) keeps the
        # in-memory contract — account_id="" disables the journal rebuild.
        rm = PropRiskManager(_base_cfg())
        assert rm.account_id == ""

    def _seed(self, db_path, account_id, pnl):
        from datetime import datetime, timezone
        from src.units.db.database import Database
        today = datetime.now(timezone.utc).date()
        Database(db_path=str(db_path)).insert_trade({
            "timestamp": f"{today}T12:00:00+00:00",
            "symbol": "BTCUSDT", "direction": "long",
            "entry_price": 100.0, "position_size": 1.0,
            "status": "closed", "pnl": pnl, "is_backtest": 0,
            "account_id": account_id, "created_at": f"{today} 12:00:00",
        })

    def test_daily_cap_engages_from_journal(self, tmp_path, monkeypatch):
        db = tmp_path / "trade_journal.db"
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(db))
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data-root"))
        # _base_cfg daily_usd is 50 → a -200 realized loss is well past it.
        self._seed(db, "prop_x", -200.0)

        rm = PropRiskManager(_base_cfg(), account_name="prop_x")
        # daily_pnl rebuilt from the journal, not poked in memory.
        assert rm.daily_pnl == pytest.approx(-200.0)
        ok, reason = rm.evaluate(_pkg())
        assert ok is False
        assert reason == "DAILY_LOSS_CAP"

    def test_state_survives_restart(self, tmp_path, monkeypatch):
        db = tmp_path / "trade_journal.db"
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(db))
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data-root"))
        self._seed(db, "prop_x", -200.0)
        # A fresh instance (process "restart") rebuilds the same breach
        # state — the bug this fix closes was the cap resetting to 0.
        revived = PropRiskManager(_base_cfg(), account_name="prop_x")
        assert revived.daily_pnl == pytest.approx(-200.0)
        ok, reason = revived.evaluate(_pkg())
        assert ok is False
        assert reason == "DAILY_LOSS_CAP"

    def test_clean_journal_allows(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data-root"))
        # Positive control: no seeded loss → the cap is NOT tripped, so the
        # rework can't be passing only because the gate always refuses.
        rm = PropRiskManager(_base_cfg(), account_name="prop_clean")
        ok, reason = rm.evaluate(_pkg())
        assert ok is True
        assert reason is None


# ---------------------------------------------------------------------------
# Loader: type=prop instantiates PropRiskManager
# ---------------------------------------------------------------------------


_YAML_BODY = """
accounts:
  bybit_1:
    type: regular
    exchange: bybit
    api_key_env: BYBIT_API_KEY_1
    strategies: [vwap]
    risk:
      max_dd_pct: 0.05
      daily_usd: 100
      pos_size: 500
      risk_pct: 0.01

  prop_velo:
    type: prop
    exchange: prop_broker
    api_key_env: PROP_API_KEY_1
    strategies: [vwap]
    enabled: true
    account_state: evaluation
    phase_requirements:
      target_profit_pct: 0.05
      min_active_days: 4
    overnight_restricted: false
    weekend_restricted: false
    risk:
      max_dd_pct: 0.02
      daily_usd: 50
      pos_size: 200

  prop_disabled:
    type: prop
    exchange: prop_broker
    api_key_env: PROP_API_KEY_2
    strategies: []
    enabled: false
    risk:
      max_dd_pct: 0.02
      daily_usd: 50
      pos_size: 200
"""


@pytest.fixture()
def accounts_yaml(tmp_path):
    p = tmp_path / "accounts.yaml"
    p.write_text(_YAML_BODY)
    return str(p)


class TestLoader:
    def test_loader_picks_prop_risk_manager_for_prop_type(self, accounts_yaml):
        from src.units.accounts import load_accounts

        accounts = load_accounts(accounts_yaml)
        names = {a.name for a in accounts}
        # prop_disabled is filtered out at load time.
        assert names == {"bybit_1", "prop_velo"}

        regular = next(a for a in accounts if a.name == "bybit_1")
        prop = next(a for a in accounts if a.name == "prop_velo")

        assert type(regular.risk_manager) is RiskManager
        assert isinstance(prop.risk_manager, PropRiskManager)
        assert prop.risk_manager.account_state == "evaluation"
        assert prop.risk_manager.target_profit_pct == 0.05
        assert prop.risk_manager.min_active_days == 4

    def test_loader_skips_disabled_accounts(self, accounts_yaml):
        from src.units.accounts import load_accounts

        accounts = load_accounts(accounts_yaml)
        assert "prop_disabled" not in {a.name for a in accounts}


# ---------------------------------------------------------------------------
# Coordinator routing — skip reason surfaces in the result row
# ---------------------------------------------------------------------------


class TestCoordinatorRouting:
    def test_skip_reason_in_error_field(self, accounts_yaml, monkeypatch):
        from src.core.coordinator import Coordinator
        # _YAML_BODY uses BYBIT_API_KEY_1 / PROP_API_KEY_1.
        # Without env vars the accounts are configured=False and
        # _eligible_for_dispatch drops them — results list comes back empty.
        # _derive_secret_env("BYBIT_API_KEY_1") → "BYBIT_API_SECRET_1" so
        # both key + secret must be set.
        monkeypatch.setenv("BYBIT_API_KEY_1", "test-key")
        monkeypatch.setenv("BYBIT_API_SECRET_1", "test-secret")
        monkeypatch.setenv("PROP_API_KEY_1", "test-key")
        monkeypatch.setenv("PROP_API_SECRET_1", "test-secret")
        # Force mission complete on prop_velo so the gate fires.
        from src.units.accounts import load_accounts
        accounts = load_accounts(accounts_yaml)
        prop = next(a for a in accounts if a.name == "prop_velo")
        prop.risk_manager.cumulative_pnl_pct = 0.10
        prop.risk_manager.active_days = 99

        # Patch load_accounts inside multi_account_execute to return
        # our pre-tweaked instances (otherwise it reloads from YAML).
        import src.core.coordinator as coord_mod
        monkeypatch.setattr(coord_mod, "_log_new_order_package",
                            lambda pkg: None)

        import src.units.accounts as accounts_mod
        monkeypatch.setattr(accounts_mod, "load_accounts",
                            lambda path=None: accounts)

        coord = Coordinator()
        pkg = _pkg()
        pkg.meta["account_balances_usd"] = {
            "bybit_1": 10_000.0,
            "prop_velo": 10_000.0,
        }

        results = coord.multi_account_execute(pkg, dry_run=True)
        prop_result = next(r for r in results if r["name"] == "prop_velo")
        assert prop_result["trade_id"] is None
        assert prop_result["error"] is not None
        assert "SKIP_MISSION_MET" in prop_result["error"]

        # Regular account still trades.
        bybit_result = next(r for r in results if r["name"] == "bybit_1")
        assert bybit_result["trade_id"] is not None


# ---------------------------------------------------------------------------
# Deprecated breakout exchange — still inert
# ---------------------------------------------------------------------------


class TestDeprecatedExchanges:
    def test_execute_pkg_breakout_emits_manual_ticket(self):
        # Breakout is the manual browser-bridge ticket emitter (REVIVED
        # 2026-06-17, PB-20260616-004 — no longer a deprecated inert stub):
        # _submit_order builds the per-account leg + emits prop_signal and
        # returns a prop-manual-<uuid> marker (no live exchange position).
        from src.units.accounts.execute import _submit_order
        tid = _submit_order(
            client=None,
            order={"symbol": "SOLUSDT", "side": "Buy", "direction": "long",
                   "entry": 150.0, "sl": 145.0, "tp": 162.0, "qty": 0.0,
                   "strategy": "trend_donchian_sol", "account_id": "breakout_1"},
            account_cfg={"exchange": "breakout", "account_id": "breakout_1",
                         "risk": {"risk_pct": 0.015}},
        )
        assert tid.startswith("prop-manual-")


# ---------------------------------------------------------------------------
# Config wiring sanity (the real config/accounts.yaml)
# ---------------------------------------------------------------------------


class TestRealAccountsYaml:
    def test_velotrade_purged_and_accounts_carry_account_class(self):
        # The dead prop_velotrade_1 / velotrade integration was purged
        # (2026-06-15). Assert it's gone and that every remaining account
        # carries the required account_class funding category.
        import yaml
        p = Path(__file__).resolve().parents[1] / "config" / "accounts.yaml"
        raw = yaml.safe_load(p.read_text())
        accounts = raw["accounts"]
        assert "prop_velotrade_1" not in accounts
        for name, cfg in accounts.items():
            assert cfg.get("exchange") != "velotrade", (
                f"{name}: velotrade exchange should be purged"
            )
            assert cfg.get("account_class") in ("paper", "real_money", "prop"), (
                f"{name}: missing/invalid account_class"
            )


# ---------------------------------------------------------------------------
# Nominal-equity sizing (BL-20260619-PROP-GATE-BALANCE)
# ---------------------------------------------------------------------------


class TestNominalSizing:
    """Prop accounts have no live balance API, so the coordinator passes
    balance_usd=0.0; position_size must size off the nominal account equity
    instead of refusing on below_min_balance — otherwise no ticket ever
    emits (the prop-account no-trades cause)."""

    def test_sizes_off_nominal_when_no_live_balance(self):
        cfg = _base_cfg(account_size_usd=5000)
        cfg["risk"]["min_balance_usd"] = 100  # the real prop floor
        rm = PropRiskManager(cfg)
        # balance_usd=0.0 is exactly what _fetch_balance(breakout) returns.
        qty = rm.position_size(_pkg(), 0.0)
        assert qty > 0, "prop trade must size off the $5k nominal, not refuse on $0"

    def test_refuses_when_no_nominal_and_no_balance(self):
        # No account_size_usd and no live balance → no sizing basis → refuse.
        # (We never fabricate a size out of nothing.)
        cfg = _base_cfg()  # account_size_usd defaults to 0.0
        cfg["risk"]["min_balance_usd"] = 100
        rm = PropRiskManager(cfg)
        assert rm.position_size(_pkg(), 0.0) == 0.0

    def test_real_below_min_balance_still_refuses(self):
        # The nominal substitution triggers ONLY when balance is missing
        # (<=0). A genuine live balance below the floor still refuses — we
        # don't blanket-bypass the min-balance gate.
        cfg = _base_cfg(account_size_usd=5000)
        cfg["risk"]["min_balance_usd"] = 100
        rm = PropRiskManager(cfg)
        assert rm.position_size(_pkg(), 10.0) == 0.0

    def test_nominal_daily_cap_prescreen(self):
        # The nominal basis means the base daily-loss cap still pre-screens:
        # an already-exhausted daily budget refuses even with $0 live balance.
        cfg = _base_cfg(account_size_usd=5000)
        cfg["risk"]["min_balance_usd"] = 100
        rm = PropRiskManager(cfg)
        rm.daily_pnl = -10_000.0  # blow past any nominal daily cap
        assert rm.position_size(_pkg(), 0.0) == 0.0
