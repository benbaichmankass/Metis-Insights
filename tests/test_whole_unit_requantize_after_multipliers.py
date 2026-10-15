"""ORDER-AUDIT-3 / PI-20260927-ECILBBVH-0007 (TIER 3 -- sizing).

The reductive multipliers (conviction / advisory / news) scaled the whole-unit
risk-sized qty with no re-quantize. On a whole-unit venue (Alpaca):
  * 3 shares x 0.85 = 2.55 was placed as 3 (broker rounds) -- downsize erased;
  * 1 share  x 0.5  = 0.5 was refused as below_venue_min -- an unlabelled veto.
After the fix a REDUCED qty is floored to whole units, and a floor to zero is
its own refusal, ``downsized_to_zero``. An unreduced qty is untouched.
"""
from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

from src.core.coordinator import Coordinator, OrderPackage

_YAML = textwrap.dedent("""\
    accounts:
      eq:
        type: regular
        exchange: alpaca
        api_key_env: ALPACA_KEY_OK
        strategies: [spy_trend]
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
""")


@pytest.fixture()
def run(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPACA_KEY_OK", "x")
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "tj.db"))
    p = tmp_path / "accounts.yaml"
    p.write_text(_YAML)

    def _go(risk_qty, factor):
        import src.runtime.news_sizing as ns
        from src.units.accounts.risk import RiskManager
        monkeypatch.setattr(RiskManager, "position_size",
                            lambda self, *a, **k: float(risk_qty))
        monkeypatch.setattr(ns, "apply_news_downsize",
                            lambda pkg, q, **k: q * factor)
        pkg = OrderPackage(strategy="spy_trend", symbol="SPY", direction="long",
                           entry=500.0, sl=490.0, tp=520.0, confidence=1.0,
                           meta={"strategy_name": "spy_trend"})
        with patch("src.units.accounts.execute.execute_pkg",
                   side_effect=lambda pkg, cfg, **kw: f"dry-{cfg['account_id']}"):
            out = Coordinator().multi_account_execute(
                pkg, accounts_path=str(p), dry_run=True,
                balance_fetcher=lambda _a: 100_000.0)
        return pkg, out
    return _go


def _row(out):
    rows = out if isinstance(out, list) else out.get("results", out.get("accounts", []))
    return next(r for r in rows if r.get("name") == "eq")


def test_a_partial_downsize_is_floored_not_rounded_back_up(run):
    pkg, _out = run(3, 0.85)
    assert pkg.meta["sized_qty_by_account"]["eq"] == 2.0


def test_a_downsize_below_one_share_is_its_own_refusal(run):
    _pkg, out = run(1, 0.5)
    assert str(_row(out).get("error") or "").startswith("downsized_to_zero:")


def test_an_unreduced_qty_is_untouched(run):
    pkg, _out = run(3, 1.0)
    assert pkg.meta["sized_qty_by_account"]["eq"] == 3.0
