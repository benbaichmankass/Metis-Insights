"""Regression — early-refusal journal rows must carry the demo flag.

Investigation ``claude/ict-order-rejections-BmqU1`` (2026-05-28) found
that the ``sized_qty<=0`` and ``sizing_failed`` refusal paths in
``Coordinator.multi_account_execute`` journalled with a *minimal*
``_early_account_cfg`` that omitted the ``demo`` key. Because
``_log_trade_to_journal`` derives ``is_demo`` from
``account_cfg.get("demo")``, every demo-account refusal row was written
``is_demo=0``.

Operational impact: the demo account ``bybit_1`` (≈$274k paper balance,
``daily_usd: 100`` cap) trips its daily-loss cap early each UTC day and
then size-refuses every subsequent ``vwap`` signal. Those refusal rows
flooded ``trade_journal.db::trades`` mislabelled ``is_demo=0``, so a
diag pull read them as a LIVE-account rejection cluster (the false
premise that opened the investigation).

The RiskBreach and exchange_rejected paths already journal with the
richer ``account_cfg`` (demo correct); this test pins the early paths to
the same contract.
"""
from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

from src.core.coordinator import Coordinator, OrderPackage


@pytest.fixture(autouse=True)
def _force_execution_live(monkeypatch):
    # vwap is execution: shadow in the live config; pin it live so this
    # test exercises the per-account refusal path, not the strategy gate.
    monkeypatch.setattr(
        "src.strategy_registry.execution_mode", lambda *a, **k: "live"
    )


_DEMO_ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      bybit_demo:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_DEMO
        demo: true
        mode: live
        market_type: linear
        strategies: [vwap]
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
          leverage: 3
""")


def _pkg() -> OrderPackage:
    return OrderPackage(
        strategy="vwap",
        symbol="BTCUSDT",
        direction="short",
        entry=80_000.0,
        sl=80_500.0,
        tp=79_000.0,
        confidence=1.0,
        meta={"strategy_name": "vwap"},
    )


@pytest.fixture()
def coord(tmp_path):
    units_yaml = tmp_path / "units.yaml"
    units_yaml.write_text("units: {}\n")
    return Coordinator(units_path=str(units_yaml))


@pytest.fixture()
def demo_yaml(tmp_path):
    p = tmp_path / "accounts.yaml"
    p.write_text(_DEMO_ACCOUNTS_YAML)
    return str(p)


def test_zero_qty_refusal_on_demo_account_stamps_demo_flag(
    coord, demo_yaml, monkeypatch,
):
    """A ``sized_qty<=0`` refusal on a ``demo: true`` account must
    journal with ``account_cfg['demo'] is True`` so the row is written
    ``is_demo=1`` and not mistaken for a live-account rejection."""
    monkeypatch.setenv("BYBIT_KEY_DEMO", "k")
    monkeypatch.setenv("BYBIT_KEY_DEMO_API_SECRET", "s")

    captured: list[dict] = []

    def _capture(pkg, account_cfg, *, reason, status, sized_qty=None):
        captured.append(
            {"status": status, "reason": reason, "demo": account_cfg.get("demo")}
        )
        return True

    # Zero balance → position_size returns 0.0 (the only balance gate,
    # since the min_balance_usd floor was removed 2026-06-24) → the
    # sized_qty<=0 gate fires and journals via _early_account_cfg.
    with patch(
        "src.units.accounts.execute.log_rejection_to_journal",
        side_effect=_capture,
    ), patch(
        "src.units.accounts.clients.bybit_client_for", return_value=object(),
    ):
        results = coord.multi_account_execute(
            _pkg(),
            accounts_path=demo_yaml,
            balance_fetcher=lambda _a: 0.0,  # no funds to size against
        )

    assert len(results) == 1
    assert results[0]["sized_qty"] == 0.0
    assert results[0]["error"] is not None
    # The refusal was journalled, and it was stamped as a demo row.
    assert captured, "expected a rejection-journal write for the zero-qty refusal"
    assert captured[0]["status"] == "rejected"
    assert captured[0]["demo"] is True, (
        "early-refusal cfg dropped the demo flag → row would be mislabelled "
        f"is_demo=0 (captured={captured[0]!r})"
    )
