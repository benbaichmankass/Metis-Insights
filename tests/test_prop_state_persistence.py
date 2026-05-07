"""Velotrade phase-2b — runtime_state/prop_state.json persistence.

Covers:

  - prop_state_io.write_prop_state / load_prop_state round-trip.
  - PropRiskManager.record_trade_result writes through atomically.
  - PropRiskManager.__init__ seeds from JSON when present, falls back
    to YAML when JSON is absent (or has no entry for the account).
  - load_accounts() passes account_name through so the manager can
    read its own section.
  - Restart simulation: counters survive a fresh PropRiskManager
    construction.
  - Best-effort: write failure does NOT raise into the caller.
  - Tests don't pollute the production runtime_state/prop_state.json
    (every test redirects to tmp_path).
"""
from __future__ import annotations

import json

import pytest

from src.units.accounts.prop_risk import PropRiskManager
from src.units.accounts.prop_state_io import (
    get_prop_state_path,
    load_prop_state,
    set_prop_state_path,
    write_prop_state,
)


@pytest.fixture(autouse=True)
def _redirect_prop_state(tmp_path):
    """Every test points at a fresh tmp prop-state file."""
    p = tmp_path / "prop_state.json"
    set_prop_state_path(p)
    try:
        yield p
    finally:
        set_prop_state_path(None)


def _cfg(**overrides):
    base = {
        "account_state": "evaluation",
        "phase_requirements": {"target_profit_pct": 0.05, "min_active_days": 4},
        "prop_state": {
            "cumulative_pnl_pct": 0.0,
            "active_days": 0,
            "entry_date": None,
        },
        "risk": {
            "max_dd_pct": 0.05, "daily_usd": 100, "pos_size": 500,
            "risk_pct": 0.01, "min_balance_usd": 50,
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# prop_state_io
# ---------------------------------------------------------------------------


class TestPropStateIO:
    def test_load_returns_none_when_file_absent(self, tmp_path):
        # Fresh tmp dir, no file yet.
        assert load_prop_state("prop_velo") is None

    def test_load_returns_none_when_account_missing(self, tmp_path,
                                                    _redirect_prop_state):
        write_prop_state("other_account", {"cumulative_pnl_pct": 0.1})
        assert load_prop_state("prop_velo") is None

    def test_write_then_load_roundtrip(self):
        write_prop_state("prop_velo", {
            "cumulative_pnl_pct": 0.0123,
            "active_days": 3,
            "entry_date": "2026-05-03",
        })
        loaded = load_prop_state("prop_velo")
        assert loaded == {
            "cumulative_pnl_pct": 0.0123,
            "active_days": 3,
            "entry_date": "2026-05-03",
        }

    def test_per_account_isolation(self):
        write_prop_state("prop_a", {"cumulative_pnl_pct": 0.05, "active_days": 1})
        write_prop_state("prop_b", {"cumulative_pnl_pct": -0.02, "active_days": 7})
        assert load_prop_state("prop_a")["active_days"] == 1
        assert load_prop_state("prop_b")["active_days"] == 7
        # Updating prop_a doesn't clobber prop_b.
        write_prop_state("prop_a", {"cumulative_pnl_pct": 0.10, "active_days": 2})
        assert load_prop_state("prop_a")["active_days"] == 2
        assert load_prop_state("prop_b")["active_days"] == 7

    def test_empty_account_name_returns_none(self):
        assert load_prop_state("") is None
        assert write_prop_state("", {"cumulative_pnl_pct": 0.1}) is False

    def test_corrupt_file_returns_empty(self, _redirect_prop_state):
        _redirect_prop_state.parent.mkdir(parents=True, exist_ok=True)
        _redirect_prop_state.write_text("{ this is not json")
        # Read tolerates corruption — returns None for the account
        # so the caller falls back to the YAML seed.
        assert load_prop_state("prop_velo") is None
        # And we can recover by writing fresh state.
        assert write_prop_state("prop_velo", {"cumulative_pnl_pct": 0.0})

    def test_path_resolution_uses_env_var(self, monkeypatch, tmp_path):
        # Reset module override so the env var is consulted.
        set_prop_state_path(None)
        custom = tmp_path / "custom_state.json"
        monkeypatch.setenv("PROP_STATE_PATH", str(custom))
        assert get_prop_state_path() == custom


# ---------------------------------------------------------------------------
# PropRiskManager — seeding + write-through
# ---------------------------------------------------------------------------


class TestPropRiskManagerSeeding:
    def test_yaml_seed_used_when_no_json(self):
        cfg = _cfg(prop_state={
            "cumulative_pnl_pct": 0.025,
            "active_days": 2,
            "entry_date": "2026-05-01",
        })
        pm = PropRiskManager(cfg, account_name="prop_velo")
        assert pm.cumulative_pnl_pct == 0.025
        assert pm.active_days == 2
        assert pm._entry_date_iso == "2026-05-01"

    def test_json_overrides_yaml_seed(self):
        write_prop_state("prop_velo", {
            "cumulative_pnl_pct": 0.040,
            "active_days": 5,
            "entry_date": "2026-05-03",
        })
        cfg = _cfg(prop_state={
            "cumulative_pnl_pct": 0.025,  # YAML stale — should be ignored
            "active_days": 2,
            "entry_date": "2026-05-01",
        })
        pm = PropRiskManager(cfg, account_name="prop_velo")
        assert pm.cumulative_pnl_pct == 0.040
        assert pm.active_days == 5
        assert pm._entry_date_iso == "2026-05-03"

    def test_no_account_name_falls_back_to_yaml_seed(self):
        # JSON exists but the manager doesn't know its name → ignore.
        write_prop_state("prop_velo", {"cumulative_pnl_pct": 0.99})
        cfg = _cfg(prop_state={"cumulative_pnl_pct": 0.01, "active_days": 0})
        pm = PropRiskManager(cfg)  # no account_name
        assert pm.cumulative_pnl_pct == 0.01

    def test_partial_json_section_still_overrides_specified_keys(self):
        # JSON has only cumulative_pnl_pct → that key wins; the rest
        # of the seed comes from YAML (dict.update semantics).
        write_prop_state("prop_velo", {"cumulative_pnl_pct": 0.080})
        cfg = _cfg(prop_state={
            "cumulative_pnl_pct": 0.0,
            "active_days": 4,
            "entry_date": "2026-05-01",
        })
        pm = PropRiskManager(cfg, account_name="prop_velo")
        assert pm.cumulative_pnl_pct == 0.080
        assert pm.active_days == 4
        assert pm._entry_date_iso == "2026-05-01"


class TestPropRiskManagerWriteThrough:
    def test_record_trade_writes_through(self, _redirect_prop_state):
        cfg = _cfg()
        pm = PropRiskManager(cfg, account_name="prop_velo")
        pm.current_equity = 10_000.0
        pm.record_trade_result(50.0, starting_equity_usd=10_000.0)

        assert _redirect_prop_state.exists()
        on_disk = json.loads(_redirect_prop_state.read_text())
        section = on_disk["prop_velo"]
        # cumulative_pnl_pct = 50 / 10000 = 0.005
        assert abs(section["cumulative_pnl_pct"] - 0.005) < 1e-9
        # active_days bumped from 0 → 1 (today's first trade).
        assert section["active_days"] == 1
        assert section["entry_date"] is not None

    def test_restart_resumes_state(self):
        # Simulate three trades on Day-1 then a trader restart.
        cfg = _cfg()
        first = PropRiskManager(cfg, account_name="prop_velo")
        first.current_equity = 10_000.0
        first.record_trade_result(50.0, starting_equity_usd=10_000.0)
        first.record_trade_result(25.0, starting_equity_usd=10_000.0)
        snapshot_pct = first.cumulative_pnl_pct
        snapshot_days = first.active_days

        # Restart: brand-new instance from the same YAML, JSON wins.
        revived = PropRiskManager(cfg, account_name="prop_velo")
        assert abs(revived.cumulative_pnl_pct - snapshot_pct) < 1e-9
        assert revived.active_days == snapshot_days

    def test_write_failure_does_not_raise(self, monkeypatch):
        # Force write_prop_state to raise; record_trade_result must
        # still update in-process state and return cleanly.
        from src.units.accounts import prop_state_io as psio

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(psio, "write_prop_state", _boom)
        cfg = _cfg()
        pm = PropRiskManager(cfg, account_name="prop_velo")
        pm.current_equity = 10_000.0
        # No exception escapes:
        pm.record_trade_result(50.0, starting_equity_usd=10_000.0)
        # …and the in-process counters still moved.
        assert pm.cumulative_pnl_pct == pytest.approx(0.005)
        assert pm.active_days == 1

    def test_no_account_name_skips_write(self, _redirect_prop_state):
        cfg = _cfg()
        pm = PropRiskManager(cfg)  # nameless
        pm.current_equity = 10_000.0
        pm.record_trade_result(50.0, starting_equity_usd=10_000.0)
        # File is never created (no name → no write).
        assert not _redirect_prop_state.exists()


# ---------------------------------------------------------------------------
# Loader integration — load_accounts passes account_name through
# ---------------------------------------------------------------------------


_YAML_PROP = """
accounts:
  prop_velo:
    type: prop
    exchange: velotrade
    api_key_env: VELOTRADE_API_KEY_X
    strategies: []
    account_state: evaluation
    phase_requirements:
      target_profit_pct: 0.05
      min_active_days: 4
    prop_state:
      cumulative_pnl_pct: 0.01
      active_days: 1
      entry_date: "2026-05-01"
    overnight_restricted: false
    risk:
      max_dd_pct: 0.02
      daily_usd: 50
      pos_size: 200
      risk_pct: 0.005
      min_balance_usd: 50
"""


class TestLoaderIntegration:
    def test_loader_seeds_from_yaml_when_no_json(self, tmp_path):
        p = tmp_path / "accounts.yaml"
        p.write_text(_YAML_PROP)
        from src.units.accounts import load_accounts
        accs = load_accounts(str(p))
        prop = next(a for a in accs if a.name == "prop_velo")
        assert prop.risk_manager.account_name == "prop_velo"
        assert prop.risk_manager.cumulative_pnl_pct == 0.01
        assert prop.risk_manager.active_days == 1

    def test_loader_seeds_from_json_when_present(self, tmp_path):
        # Pre-write JSON state — should beat the YAML seed.
        write_prop_state("prop_velo", {
            "cumulative_pnl_pct": 0.075,
            "active_days": 6,
            "entry_date": "2026-05-03",
        })
        p = tmp_path / "accounts.yaml"
        p.write_text(_YAML_PROP)
        from src.units.accounts import load_accounts
        accs = load_accounts(str(p))
        prop = next(a for a in accs if a.name == "prop_velo")
        assert prop.risk_manager.cumulative_pnl_pct == 0.075
        assert prop.risk_manager.active_days == 6
        assert prop.risk_manager._entry_date_iso == "2026-05-03"

    def test_full_round_trip_through_loader(self, tmp_path):
        p = tmp_path / "accounts.yaml"
        p.write_text(_YAML_PROP)
        from src.units.accounts import load_accounts
        accs = load_accounts(str(p))
        prop = next(a for a in accs if a.name == "prop_velo")
        prop.risk_manager.current_equity = 10_000.0
        prop.risk_manager.record_trade_result(
            100.0, starting_equity_usd=10_000.0,
        )

        # Re-load — counters survive.
        accs2 = load_accounts(str(p))
        prop2 = next(a for a in accs2 if a.name == "prop_velo")
        # cumulative_pnl_pct = 0.01 (yaml seed) + 0.01 (100/10000)
        assert prop2.risk_manager.cumulative_pnl_pct == pytest.approx(0.02)
        # active_days bumped exactly once (one day, no matter how
        # many trades the same UTC date).
        assert prop2.risk_manager.active_days == 2
