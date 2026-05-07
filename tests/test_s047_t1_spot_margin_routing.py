"""S-047 T1 — accounts.yaml routing for spot-margin (bybit_2).

Three regression goals:

1. The production `config/accounts.yaml` declares ``bybit_2`` as a
   spot-margin account via the existing ``market_type`` routing field.
2. ``bybit_1`` and ``prop_velotrade_1`` routing is unchanged: same
   ``market_type``, same risk-rule defaults, same loader output shape.
3. ``RiskManager`` exposes the three new spot-margin sizing parameters
   (``max_borrow_btc``, ``borrow_fee_apr_pct``, ``liquidation_buffer_pct``)
   with the module-level defaults when no override is set in the
   account's ``risk:`` block, and respects per-account overrides when
   present (same shape as ``min_balance_usd`` / ``risk_pct``).

These three labels are routing identities, **not** refuse-to-trade gates.
The dispatcher's ``live | dry_run`` switch remains the only canonical
execution gate per ``docs/claude/workplan.md`` § "Live / dry-run rule".
"""
from __future__ import annotations

import os
import textwrap

import pytest
import yaml

from src.units.accounts import load_accounts
from src.units.accounts.risk import (
    DEFAULT_BORROW_FEE_APR_PCT,
    DEFAULT_LIQUIDATION_BUFFER_PCT,
    DEFAULT_MAX_BORROW_BTC,
    RiskManager,
)
from src.utils.paths import repo_root


# ---------------------------------------------------------------------------
# Production accounts.yaml — declared routing for the three live accounts
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def production_accounts_raw() -> dict:
    """Load `config/accounts.yaml` from disk as a raw dict (no loader transforms)."""
    path = os.path.join(repo_root(), "config", "accounts.yaml")
    with open(path, "r", encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("accounts") or {}


class TestProductionAccountsYamlRouting:
    """The accounts.yaml schema declares bybit_2 as spot-margin (S-047 T1)."""

    def test_bybit_2_routes_as_spot_margin(self, production_accounts_raw):
        cfg = production_accounts_raw["bybit_2"]
        assert cfg["market_type"] == "spot-margin", (
            "bybit_2 must declare market_type: spot-margin per S-047 T1; "
            f"got {cfg.get('market_type')!r}"
        )

    def test_bybit_1_routing_unchanged(self, production_accounts_raw):
        cfg = production_accounts_raw["bybit_1"]
        assert cfg["market_type"] == "spot", (
            "bybit_1 must remain market_type: spot — non-margin spot cash market"
        )

    def test_prop_velotrade_1_routing_unchanged(self, production_accounts_raw):
        cfg = production_accounts_raw["prop_velotrade_1"]
        # prop_velotrade_1 has never declared market_type; the loader
        # default ("spot") is the contract.
        assert "market_type" not in cfg, (
            "prop_velotrade_1 should not declare market_type — its routing "
            "comes from the loader default and the dxtrade exchange branch"
        )

    def test_no_is_leverage_flag_on_any_account(self, production_accounts_raw):
        for name, cfg in production_accounts_raw.items():
            assert "is_leverage" not in cfg, (
                f"{name}: no `is_leverage` per-account flag is allowed "
                "(S-047 § 5b — routing identity carries the spot-margin "
                "label, not a boolean toggle)"
            )


class TestProductionAccountsLoaderShape:
    """Loaded TradingAccount instances surface market_type unchanged."""

    @pytest.fixture(scope="class")
    def accounts(self):
        path = os.path.join(repo_root(), "config", "accounts.yaml")
        return {a.name: a for a in load_accounts(path)}

    def test_bybit_2_market_type_attribute(self, accounts):
        assert accounts["bybit_2"].market_type == "spot-margin"

    def test_bybit_1_market_type_attribute(self, accounts):
        assert accounts["bybit_1"].market_type == "spot"

    def test_bybit_2_strategies_unchanged(self, accounts):
        assert accounts["bybit_2"].strategies == ["vwap"]

    def test_bybit_1_strategies_unchanged(self, accounts):
        assert accounts["bybit_1"].strategies == ["turtle_soup"]

    def test_prop_velotrade_1_loaded(self, accounts):
        # The loader default for an account that omits market_type is "spot".
        # The account loads regardless of credential state (configured=False
        # is fine — see Velotrade phase-2 hookup checklist in accounts.yaml).
        prop = accounts["prop_velotrade_1"]
        assert prop.market_type == "spot"
        assert prop.account_type == "prop"

    def test_dispatcher_does_not_refuse_on_market_type(self, accounts):
        """The market_type label is routing, not a gate.

        TradingAccount construction does not raise, does not flip
        dry_run, and does not store any "spot-margin disabled" flag —
        the value just propagates as a string for downstream routing.
        ``configured`` reflects only the env-var credential presence
        (Velotrade phase-2) and is unrelated to spot-margin routing.
        """
        bybit_2 = accounts["bybit_2"]
        assert bybit_2.dry_run is False  # mode: live in YAML
        assert bybit_2.exchange == "bybit"
        # bybit_2's configured state is independent of the spot-margin
        # label — bybit_1 and bybit_2 share the same credential pattern
        # and must agree.
        assert bybit_2.configured == accounts["bybit_1"].configured


# ---------------------------------------------------------------------------
# RiskManager — three new spot-margin sizing parameters
# ---------------------------------------------------------------------------


class TestRiskManagerSpotMarginDefaults:
    """The three new params land on every RiskManager instance.

    Defaults come from the module-level constants in `risk.py`. Operator
    overrides via a per-account `risk:` block in accounts.yaml are
    respected — same shape as `min_balance_usd` / `risk_pct`.

    Non-spot-margin accounts hold the same defaults inertly: T2's
    position_size() upgrade consumes them only on spot-margin routing.
    """

    def test_defaults_when_cfg_omits_keys(self):
        rm = RiskManager({})
        assert rm.max_borrow_btc == DEFAULT_MAX_BORROW_BTC
        assert rm.borrow_fee_apr_pct == DEFAULT_BORROW_FEE_APR_PCT
        assert rm.liquidation_buffer_pct == DEFAULT_LIQUIDATION_BUFFER_PCT

    def test_per_account_override_max_borrow(self):
        rm = RiskManager({"max_borrow_btc": 1.25})
        assert rm.max_borrow_btc == 1.25
        # Other defaults still hold.
        assert rm.borrow_fee_apr_pct == DEFAULT_BORROW_FEE_APR_PCT
        assert rm.liquidation_buffer_pct == DEFAULT_LIQUIDATION_BUFFER_PCT

    def test_per_account_override_borrow_fee_apr(self):
        rm = RiskManager({"borrow_fee_apr_pct": 7.5})
        assert rm.borrow_fee_apr_pct == 7.5

    def test_per_account_override_liquidation_buffer(self):
        rm = RiskManager({"liquidation_buffer_pct": 45.0})
        assert rm.liquidation_buffer_pct == 45.0

    def test_liquidation_buffer_default_is_30_per_plan(self):
        """S-047 § 7 specifies 30% as the ship default."""
        assert DEFAULT_LIQUIDATION_BUFFER_PCT == 30.0

    def test_defaults_do_not_introduce_a_refusal_branch(self):
        """The new fields are values, not gates.

        Constructing a RiskManager with the new defaults must not flip
        dry_run, must not change daily-loss handling, and must not
        change approve()/evaluate() outcomes for an order that would
        have been approved before. T2 will consume these values inside
        position_size(); T1 only ships the storage.
        """
        rm = RiskManager({})
        assert rm.dry_run is False
        # daily loss / pos size baselines unchanged
        assert rm.max_daily_loss_usd == 100.0
        assert rm.max_pos_size_usd == 500.0


class TestRiskManagerLoadedFromAccountsYaml:
    """End-to-end: loader + accounts.yaml + RiskManager defaults agree."""

    @pytest.fixture()
    def synthetic_yaml(self, tmp_path):
        """A synthetic accounts.yaml with one spot-margin and one cash-spot."""
        body = textwrap.dedent("""\
            accounts:
              cash_spot:
                type: regular
                exchange: bybit
                api_key_env: TEST_KEY_CASH
                mode: live
                market_type: spot
                strategies: [vwap]
                risk:
                  max_dd_pct: 0.05
                  daily_usd: 100
                  pos_size: 500
                  risk_pct: 0.01
                  min_balance_usd: 50
              margin_spot:
                type: regular
                exchange: bybit
                api_key_env: TEST_KEY_MARGIN
                mode: live
                market_type: spot-margin
                strategies: [vwap]
                risk:
                  max_dd_pct: 0.05
                  daily_usd: 100
                  pos_size: 500
                  risk_pct: 0.01
                  min_balance_usd: 50
                  # Per-account override of one spot-margin param;
                  # the other two keep module defaults.
                  max_borrow_btc: 2.0
        """)
        p = tmp_path / "accounts.yaml"
        p.write_text(body)
        return str(p)

    def test_cash_spot_routes_as_spot(self, synthetic_yaml):
        accounts = {a.name: a for a in load_accounts(synthetic_yaml)}
        assert accounts["cash_spot"].market_type == "spot"

    def test_margin_spot_routes_as_spot_margin(self, synthetic_yaml):
        accounts = {a.name: a for a in load_accounts(synthetic_yaml)}
        assert accounts["margin_spot"].market_type == "spot-margin"

    def test_cash_spot_holds_default_spot_margin_params(self, synthetic_yaml):
        accounts = {a.name: a for a in load_accounts(synthetic_yaml)}
        rm = accounts["cash_spot"].risk_manager
        # Defaults flow through inertly even on non-spot-margin accounts —
        # T2 will gate consumption on the routing label.
        assert rm.max_borrow_btc == DEFAULT_MAX_BORROW_BTC
        assert rm.borrow_fee_apr_pct == DEFAULT_BORROW_FEE_APR_PCT
        assert rm.liquidation_buffer_pct == DEFAULT_LIQUIDATION_BUFFER_PCT

    def test_margin_spot_honours_max_borrow_override(self, synthetic_yaml):
        accounts = {a.name: a for a in load_accounts(synthetic_yaml)}
        rm = accounts["margin_spot"].risk_manager
        assert rm.max_borrow_btc == 2.0
        # Non-overridden params keep defaults.
        assert rm.borrow_fee_apr_pct == DEFAULT_BORROW_FEE_APR_PCT
        assert rm.liquidation_buffer_pct == DEFAULT_LIQUIDATION_BUFFER_PCT

    def test_loader_does_not_refuse_spot_margin_accounts(self, synthetic_yaml):
        """The 'spot-margin' label is routing, not a gate.

        load_accounts() must return both accounts; the spot-margin one
        must not arrive flipped to dry_run or otherwise pre-flight-
        refused on the basis of the routing label. (``configured``
        reflects env-var credential presence, an orthogonal concern —
        if creds are unset, both spot and spot-margin accounts go
        unconfigured the same way.)
        """
        accounts = {a.name: a for a in load_accounts(synthetic_yaml)}
        cash = accounts["cash_spot"]
        margin = accounts["margin_spot"]
        # The spot-margin account is not auto-flipped to dry_run.
        assert margin.dry_run is False
        # ``configured`` agrees between the two accounts — the routing
        # label is not what determines configuration state.
        assert margin.configured == cash.configured
