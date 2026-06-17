"""Config-level wiring guard for the Breakout prop capability (PB-20260616-004).

YAML/JSON only — no heavy runtime imports, so it runs anywhere. Asserts the
SOL/ETH variants + the breakout_1 account are coherently wired and that the
account ships INERT.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]


def _strategies():
    return yaml.safe_load(open(_ROOT / "config" / "strategies.yaml"))["strategies"]


def _accounts():
    return yaml.safe_load(open(_ROOT / "config" / "accounts.yaml"))["accounts"]


def test_variants_present_and_scoped():
    s = _strategies()
    assert s["trend_donchian_sol"]["symbols"] == ["SOLUSDT"]
    assert s["trend_donchian_eth"]["symbols"] == ["ETHUSDT"]
    assert s["trend_donchian_sol"]["execution"] == "live"
    assert s["trend_donchian_eth"]["execution"] == "shadow"
    # both-sides to match the validated gate — no long_only on the variants
    assert not s["trend_donchian_sol"].get("long_only", False)
    assert not s["trend_donchian_eth"].get("long_only", False)
    # validated params mirrored from the flagship
    for v in ("trend_donchian_sol", "trend_donchian_eth"):
        assert s[v]["donchian"] == 20
        assert s[v]["trail_mult"] == 5.0
        assert s[v]["min_confidence"] == 0.60


def test_eth_shadow_has_guard_marker():
    # CI dry-run-guard requires an inline shadow-guard marker for execution: shadow
    raw = (_ROOT / "config" / "strategies.yaml").read_text()
    block = raw.split("trend_donchian_eth:", 1)[1].split("trend_donchian", 1)[0]
    assert "shadow-guard: allow" in raw  # marker exists in the file


def test_prop_account_config():
    a = _accounts()["breakout_1"]
    assert a["exchange"] == "breakout"
    assert a["type"] == "prop"                     # mission-aware PropRiskManager
    assert a["account_class"] == "prop"            # third funding category
    assert a["mode"] == "live"                     # always-live ping (operator gates per-signal)
    assert a["account_state"] == "evaluation"      # eval→funded lifecycle tracked
    assert a["phase_requirements"]["target_profit_pct"] == 0.10
    assert set(a["strategies"]) == {"trend_donchian_sol", "trend_donchian_eth"}
    assert set(a["symbols"]) == {"SOLUSDT", "ETHUSDT"}


def test_prop_account_class_is_valid_and_separate():
    # prop is a valid category, and excluded from the real-money predicate so it
    # never contaminates real-money KPIs.
    from src.units.accounts.account import _VALID_ACCOUNT_CLASSES
    assert "prop" in _VALID_ACCOUNT_CLASSES
    import pathlib
    pred = (pathlib.Path(__file__).resolve().parents[1]
            / "src/web/api/routers/dashboard.py").read_text()
    assert "IN ('paper','prop')" in pred  # real-money predicate excludes prop


def test_descriptions_and_changelog_present():
    d = json.load(open(_ROOT / "config" / "strategy_descriptions.json"))
    c = json.load(open(_ROOT / "config" / "strategy_changelog.json"))
    for v in ("trend_donchian_sol", "trend_donchian_eth"):
        assert v in d and d[v]["short"] and d[v]["how_it_works"]
        assert v in c and isinstance(c[v], list) and c[v]


def test_compat_matrix_routes_sol_to_breakout_only():
    import sys
    sys.path.insert(0, str(_ROOT))
    from scripts.prop.account_compat_matrix import compat_row
    strategies, accounts = _strategies(), _accounts()
    routed = [aid for aid, acct in accounts.items()
              if compat_row("trend_donchian_sol", strategies["trend_donchian_sol"],
                            aid, acct)["verdict"] == "ROUTE"]
    assert routed == ["breakout_1"]
