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
    # ETH promoted shadow → live 2026-06-17 (operator-approved, PB-20260616-004)
    assert s["trend_donchian_eth"]["execution"] == "live"
    # directional discipline from the long-only A/B (PB-20260616-004):
    # SOL's edge holds long-only → long_only:true; ETH's edge is short-side-
    # dependent (long-only flips it negative) → stays two-sided.
    assert s["trend_donchian_sol"].get("long_only", False) is True
    assert not s["trend_donchian_eth"].get("long_only", False)
    # validated params mirrored from the flagship
    for v in ("trend_donchian_sol", "trend_donchian_eth"):
        assert s[v]["donchian"] == 20
        assert s[v]["trail_mult"] == 5.0
        assert s[v]["min_confidence"] == 0.60


def test_both_variants_live_no_shadow_marker_needed():
    # ETH was promoted shadow → live 2026-06-17, so both prop variants are
    # execution: live and neither needs a `shadow-guard: allow` marker. (The
    # CI dry-run-guard only requires the marker on an execution: shadow line.)
    s = _strategies()
    assert s["trend_donchian_sol"]["execution"] == "live"
    assert s["trend_donchian_eth"]["execution"] == "live"


def test_prop_account_config():
    a = _accounts()["breakout_1"]
    assert a["exchange"] == "breakout"
    assert a["type"] == "prop"                     # mission-aware PropRiskManager
    assert a["account_class"] == "prop"            # third funding category
    assert a["mode"] == "live"                     # always-live ping (operator gates per-signal)
    assert a["account_state"] == "evaluation"      # eval→funded lifecycle tracked
    assert a["phase_requirements"]["target_profit_pct"] == 0.10
    # 2026-06-25 (Tier-3, operator-approved): the swap-robust variant
    # eth_pullback_prop_2h promoted to live + the original eth_pullback_2h routed
    # to the prop account, both +EV at Breakout's real 0.033%/day swap.
    # 2026-06-29 (Unit C Phase 0, DRAFT Tier-3): the two swap-robust trend EXIT
    # variants trend_donchian_sol_prop/_eth_prop added as execution: shadow
    # (observe-only soak) — prop-EV-gated before any shadow->live promotion.
    assert set(a["strategies"]) == {
        "trend_donchian_sol", "trend_donchian_eth",
        "eth_pullback_prop_2h", "eth_pullback_2h",
        "trend_donchian_sol_prop", "trend_donchian_eth_prop"}
    assert set(a["symbols"]) == {"SOLUSDT", "ETHUSDT"}


def test_prop_account_class_is_valid_and_separate():
    # prop is a valid category, and excluded from the real-money predicate so it
    # never contaminates real-money KPIs.
    from src.units.accounts.account import _VALID_ACCOUNT_CLASSES
    assert "prop" in _VALID_ACCOUNT_CLASSES
    # The real-money predicate is now defined ONCE in the canonical
    # src.web.api._clean_trades helper (was copy-pasted across routers). Assert
    # the BEHAVIOR — prop is excluded — not the source text of any one router.
    from src.web.api._clean_trades import not_paper_predicate
    assert "IN ('paper','prop')" in not_paper_predicate("")  # excludes prop


def test_descriptions_and_changelog_present():
    d = json.load(open(_ROOT / "config" / "strategy_descriptions.json"))
    c = json.load(open(_ROOT / "config" / "strategy_changelog.json"))
    for v in ("trend_donchian_sol", "trend_donchian_eth"):
        assert v in d and d[v]["short"] and d[v]["how_it_works"]
        assert v in c and isinstance(c[v], list) and c[v]


def test_breakout_account_resolves_to_prop_unit():
    # The canonical account→ruleset binding: breakout_1 resolves to a PROP unit
    # sized at 1.5% of the $5k breakout ruleset (drives the executor leg).
    import sys
    sys.path.insert(0, str(_ROOT))
    from src.prop.account_rulesets import unit_for_account
    unit = unit_for_account("breakout_1", _accounts()["breakout_1"])
    assert unit.kind == "prop"
    assert unit.account_class == "prop"
    assert abs(unit.risk_pct - 1.5) < 1e-9          # 0.015 → 1.5%
    assert unit.account_size_usd == 5000.0
