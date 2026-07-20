"""Per-strategy execution gate (S9, operator-approved 2026-05-24).

`config/strategies.yaml::execution: live|shadow` is the per-STRATEGY
execution gate, complementing the per-ACCOUNT `mode: live|dry_run`:

  - `live` (default)  — order packages are eligible to execute on the
                        accounts that route the strategy.
  - `shadow`          — the strategy still RUNS and LOGS its order
                        packages everywhere (data collection) but never
                        sends a live order: it is treated as dry on every
                        account, regardless of the account's `mode: live`.

Enforced in `Coordinator.multi_account_execute` by folding
`execution == "shadow"` into the same `effective_dry` resolution as
`mode:` — reusing the dry-run short-circuit (no new order path). On a
LIVE account the account's RiskManager.dry_run is False, so the risk gate
APPROVES and `execute_pkg` is reached with `dry_run=True` → a dry
trade_id, no exchange order, no client built.

Fully offline — synthetic YAML + patched execute_pkg/client.
"""
from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

import src.strategy_registry as reg
from src.core.coordinator import Coordinator, OrderPackage


# ---------------------------------------------------------------------------
# Registry — execution_mode resolution
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path, content: str) -> str:
    p = tmp_path / "strategies.yaml"
    p.write_text(textwrap.dedent(content))
    return str(p)


def test_execution_mode_defaults_to_live(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies:
          alpha:
            enabled: true
    """)
    assert reg.execution_mode("alpha", path) == "live"


def test_execution_mode_reads_shadow(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies:
          alpha:
            enabled: true
            execution: shadow
    """)
    assert reg.execution_mode("alpha", path) == "shadow"


def test_execution_mode_unknown_value_falls_back_to_live(tmp_path):
    # A typo must NOT silently park a strategy in a non-executing state.
    path = _write_yaml(tmp_path, """
        strategies:
          alpha:
            enabled: true
            execution: paper_only_typo
    """)
    assert reg.execution_mode("alpha", path) == "live"


def test_execution_mode_unknown_strategy_is_live():
    # Permissive default for a name not in the registry.
    assert reg.execution_mode("does_not_exist_zzz") == "live"


def test_load_strategies_surfaces_execution(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies:
          alpha:
            enabled: true
            execution: shadow
          beta:
            enabled: true
    """)
    rows = {s["name"]: s for s in reg.load_strategies(path)}
    assert rows["alpha"]["execution"] == "shadow"
    assert rows["beta"]["execution"] == "live"


def test_real_yaml_vwap_is_shadow_others_live():
    # The live config: vwap (data-only) and turtle_soup (DEMOTED 2026-07-07,
    # Tier-3 #5850 — net-negative money-loser at every stop on BTC) are
    # shadow; the rest execute. ict_scalp_5m was demoted 2026-07-14 but
    # RE-PROMOTED shadow -> live 2026-07-20 (Tier-3, operator-approved
    # Phase-4 packet — the demotion's -467R baseline proved unreproducible
    # and the live record was netting-misattribution; gated by two trend_vol
    # OFF cells in config/regime_policy.yaml. See
    # docs/research/ict_scalp_5m-phase4-regime-gate-PROPOSAL-2026-07-20.md).
    assert reg.execution_mode("vwap") == "shadow"
    assert reg.execution_mode("ict_scalp_5m") == "live"
    assert reg.execution_mode("turtle_soup") == "shadow"
    assert reg.execution_mode("trend_donchian") == "live"


# ---------------------------------------------------------------------------
# Coordinator — shadow folds into effective_dry on a LIVE account
# ---------------------------------------------------------------------------


_LIVE_ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      bybit_live:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_LIVE
        mode: live
        strategies: [vwap, trend_donchian]
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
""")


@pytest.fixture(autouse=True)
def _stub_creds(monkeypatch):
    monkeypatch.setenv("BYBIT_KEY_LIVE", "test-value")
    monkeypatch.setenv("BYBIT_KEY_LIVE_API_SECRET", "test-value")


@pytest.fixture()
def coord(tmp_path):
    units_yaml = tmp_path / "units.yaml"
    units_yaml.write_text("units: {}\n")
    return Coordinator(units_path=str(units_yaml))


@pytest.fixture()
def live_yaml(tmp_path):
    p = tmp_path / "accounts.yaml"
    p.write_text(_LIVE_ACCOUNTS_YAML)
    return str(p)


def _pkg(strategy: str) -> OrderPackage:
    return OrderPackage(
        strategy=strategy,
        symbol="BTCUSDT",
        direction="long",
        entry=80_000.0,
        sl=79_500.0,
        tp=80_500.0,
        confidence=0.42,
        meta={"strategy_name": strategy},
    )


def _capture():
    captured = []

    def _stub(pkg, account_cfg, **kw):
        captured.append({"account_id": account_cfg["account_id"], **kw})
        return f"trade-{account_cfg['account_id']}"

    return captured, _stub


def test_shadow_strategy_on_live_account_logs_but_does_not_execute(coord, live_yaml):
    """A shadow strategy on a LIVE account: execute_pkg is reached with
    dry_run=True (so the package is logged, not sent), and NO exchange
    client is constructed."""
    captured, stub = _capture()
    with patch(
        "src.units.accounts.execute.execute_pkg", side_effect=stub,
    ), patch(
        "src.units.accounts.clients.bybit_client_for",
        side_effect=AssertionError("client must NOT be built for a shadow strategy"),
    ), patch(
        "src.strategy_registry.execution_mode", return_value="shadow",
    ):
        results = coord.multi_account_execute(
            _pkg("vwap"),
            accounts_path=live_yaml,
            balance_fetcher=lambda _a: 10_000.0,
        )

    assert len(results) == 1
    assert results[0]["error"] is None          # success-shaped (data-only)
    assert len(captured) == 1
    assert captured[0]["dry_run"] is True        # logged, NOT executed


def test_live_strategy_on_live_account_still_executes(coord, live_yaml):
    """Regression: with execution=live the gate is a no-op — the live
    path is unchanged (client built, execute_pkg dry_run=False)."""
    captured, stub = _capture()
    with patch(
        "src.units.accounts.execute.execute_pkg", side_effect=stub,
    ), patch(
        "src.units.accounts.clients.bybit_client_for", return_value=object(),
    ) as client_factory, patch(
        "src.strategy_registry.execution_mode", return_value="live",
    ):
        results = coord.multi_account_execute(
            _pkg("trend_donchian"),
            accounts_path=live_yaml,
            balance_fetcher=lambda _a: 10_000.0,
        )

    assert len(results) == 1
    assert results[0]["error"] is None
    client_factory.assert_called_once()
    assert len(captured) == 1
    assert captured[0]["dry_run"] is False       # genuinely live
