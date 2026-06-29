"""Tests for S-007 #119: scripts/validate_registry_vm.py."""
from __future__ import annotations

import os
import sys
import types


# ---------------------------------------------------------------------------
# Import the validate module (not a test package, just a script)
# ---------------------------------------------------------------------------

import importlib.util

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "validate_registry_vm.py")

def _load_validate_module():
    spec = importlib.util.spec_from_file_location("validate_registry_vm", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


val = _load_validate_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SENTINEL = object()

def _make_strategy(name, service=None, model=None, signal_prefixes=_SENTINEL):
    return {
        "name": name,
        "service": service or f"ict-trader-{name}",
        "model": model,
        "signal_prefixes": [name] if signal_prefixes is _SENTINEL else signal_prefixes,
    }


# ---------------------------------------------------------------------------
# _check_service_prefix
# ---------------------------------------------------------------------------

def test_service_prefix_valid():
    ok, msg = val._check_service_prefix(_make_strategy("vwap", service="ict-trader-vwap"))
    assert ok


def test_service_prefix_invalid():
    ok, msg = val._check_service_prefix(_make_strategy("vwap", service="trader-vwap"))
    assert not ok
    assert "ict-trader-" in msg


# ---------------------------------------------------------------------------
# _check_signal_prefixes
# ---------------------------------------------------------------------------

def test_signal_prefixes_present():
    ok, _ = val._check_signal_prefixes(_make_strategy("ict", signal_prefixes=["fvg", "ob"]))
    assert ok


def test_signal_prefixes_empty_fails():
    ok, msg = val._check_signal_prefixes(_make_strategy("vwap", signal_prefixes=[]))
    assert not ok
    assert "empty" in msg.lower()


# ---------------------------------------------------------------------------
# _check_model_path
# ---------------------------------------------------------------------------

def test_model_path_none_passes():
    ok, msg = val._check_model_path(_make_strategy("vwap", model=None))
    assert ok
    assert "skip" in msg


def test_model_path_exists_passes(tmp_path, monkeypatch):
    model_file = tmp_path / "btc_v1.joblib"
    model_file.write_bytes(b"fake")

    fake_reg = types.ModuleType("src.strategy_registry")
    fake_reg.model_path = lambda name: str(model_file)
    monkeypatch.setitem(sys.modules, "src.strategy_registry", fake_reg)

    s = _make_strategy("breakout_confirmation", model="btc_v1.joblib")
    ok, msg = val._check_model_path(s)
    assert ok
    assert "exists" in msg


def test_model_path_missing_fails(tmp_path, monkeypatch):
    fake_reg = types.ModuleType("src.strategy_registry")
    fake_reg.model_path = lambda name: str(tmp_path / "missing.joblib")
    monkeypatch.setitem(sys.modules, "src.strategy_registry", fake_reg)

    s = _make_strategy("breakout_confirmation", model="missing.joblib")
    ok, msg = val._check_model_path(s)
    assert not ok
    assert "missing" in msg.lower() or "absent" in msg.lower() or "artifact" in msg.lower()


# ---------------------------------------------------------------------------
# run_checks — integration with real registry
# ---------------------------------------------------------------------------

def test_run_checks_returns_list():
    results = val.run_checks()
    assert isinstance(results, list)
    assert len(results) > 0


def test_run_checks_registry_loads_passes():
    results = val.run_checks()
    registry_check = next(r for r in results if r["check"] == "registry_loads")
    assert registry_check["ok"], registry_check["detail"]


def test_run_checks_all_services_pass():
    """Every service_prefix check must pass for the real registry."""
    results = val.run_checks()
    failures = [r for r in results if r["check"] == "service_prefix" and not r["ok"]]
    assert not failures, f"Service prefix failures: {failures}"


def test_run_checks_all_signal_prefixes_pass():
    """Every signal_prefixes check must pass for the real registry."""
    results = val.run_checks()
    failures = [r for r in results if r["check"] == "signal_prefixes" and not r["ok"]]
    assert not failures, f"Signal prefix failures: {failures}"


def test_run_checks_strategies_match_roster():
    """Registry must match the current production roster in config/strategies.yaml."""
    from src.strategy_registry import load_strategies
    names = {s["name"] for s in load_strategies()}
    # ict_scalp_5m added after S-012 PR B1; trend_donchian at S8 go-live;
    # fade_breakout_4h + squeeze_breakout_4h at S9 (2026-05-24, execution:
    # shadow data-collectors); fvg_range_15m at 2026-05-30 (execution: shadow
    # range member); htf_pullback_trend_2h + trend_donchian_1h + mes_trend_long_1d
    # at 2026-06-01; mgc_pullback_1d + mhg_pullback_1d at 2026-06-02 (the WS-A
    # metals sleeve — Micro Gold / Micro Copper daily HTF-pullback diversifiers
    # on IBKR ib_paper, execution: live on PAPER money); xauusd_trend_1h at
    # 2026-06-11 (M15 Phase 3 — gold 1h trend on OANDA practice, execution:
    # shadow until the creds + smoke test land).
    assert names == {
        "turtle_soup", "vwap", "ict_scalp_5m", "trend_donchian", "fade_breakout_4h",
        "squeeze_breakout_4h", "fvg_range_15m", "htf_pullback_trend_2h", "trend_donchian_1h",
        "mes_trend_long_1d", "mgc_pullback_1d", "mhg_pullback_1d",
        "xauusd_trend_1h", "mgc_trend_1h",
        "spy_trend_long_1d", "qqq_trend_long_1d", "gld_pullback_1d",
        # ETF-breadth daily sweep (2026-06-20, Tier-3) — 3 new daily-ETF cells:
        "iwm_trend_long_1d", "tlt_pullback_1d", "ief_pullback_1d",
        # Intraday ETF pilot (2026-06-20 § 0e, Tier-3) — 2 new INTRADAY ETF cells:
        "gld_pullback_1h", "slv_trend_1h",
        # Intraday ETF rollout 2b (2026-06-20 § 0e, Tier-3) — 4 cells completing
        # the intraday ETF sleeve (SPY/QQQ/TLT 1h pullback + USO 1h long-only trend):
        "spy_pullback_1h", "qqq_pullback_1h", "tlt_pullback_1h", "uso_trend_1h",
        "eth_pullback_2h",
        "trend_donchian_sol", "trend_donchian_eth",
        # 9 paper_ready alt cells on bybit_1 DEMO (2026-06-18, Tier-3):
        "trend_donchian_eth_4h", "trend_donchian_sol_4h", "trend_donchian_xrp_4h",
        "trend_donchian_ada_4h", "trend_donchian_avax_4h",
        "sol_pullback_2h", "xrp_pullback_2h", "ada_pullback_2h", "avax_pullback_2h",
        # swap-robust prop variant — breakout_1 shadow soak (DRAFT, Tier-3, 2026-06-25):
        "eth_pullback_prop_2h",
        # daily ETF pullback pair on alpaca_paper + alpaca_live (2026-06-27, Tier-3):
        "slv_pullback_1d", "gdx_pullback_1d",
        # Unit C prop EXIT variants — breakout_1 shadow soak (DRAFT, Tier-3, 2026-06-29):
        "trend_donchian_sol_prop", "trend_donchian_eth_prop",
    }


# ---------------------------------------------------------------------------
# main() exit code
# ---------------------------------------------------------------------------

def test_main_exits_zero_when_all_pass(monkeypatch):
    """main() returns 0 when run_checks() has no failures."""
    monkeypatch.setattr(val, "run_checks", lambda: [
        {"check": "registry_loads", "ok": True, "detail": "4 strategies loaded"},
        {"check": "service_prefix", "strategy": "vwap", "ok": True, "detail": "ok"},
    ])
    rc = val.main([])
    assert rc == 0


def test_main_exits_one_when_failures(monkeypatch):
    """main() returns 1 when any check fails."""
    monkeypatch.setattr(val, "run_checks", lambda: [
        {"check": "registry_loads", "ok": True, "detail": "ok"},
        {"check": "model_path", "strategy": "breakout_confirmation",
         "ok": False, "detail": "missing"},
    ])
    rc = val.main([])
    assert rc == 1


def test_main_json_output(monkeypatch, capsys):
    """--json flag produces parseable JSON output."""
    import json as _json
    monkeypatch.setattr(val, "run_checks", lambda: [
        {"check": "registry_loads", "ok": True, "detail": "ok"},
    ])
    val.main(["--json"])
    captured = capsys.readouterr().out
    data = _json.loads(captured)
    assert isinstance(data, list)
    assert data[0]["check"] == "registry_loads"
