"""A second prop account (tradeify_1) must not change breakout_1 (TRADEIFY-WIRE).

Shared code touched for the second account: the executor's kill switch, symbol
override, ruleset/routing resolution and state dir; the login-check and feed
wrappers' credential keys and state paths. Each test below pins breakout_1 to
what it read before the change, and pins the second account to its OWN inputs.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.prop import prop_executor as pe

REPO = Path(__file__).resolve().parents[1]


# ── kill switch: per account, never inherited ─────────────────────────────


def test_breakout_kill_switch_name_is_unchanged():
    assert pe.mode_env_for("breakout_1") == "PROP_EXECUTOR_MODE"
    assert pe.symbols_env_for("breakout_1") == "PROP_EXECUTOR_SYMBOLS"
    # default argument = breakout_1, exactly as the old signature read it
    assert pe.executor_mode({"PROP_EXECUTOR_MODE": "live"}) == "live"


def test_second_account_never_inherits_the_global_live():
    env = {"PROP_EXECUTOR_MODE": "live"}
    assert pe.mode_env_for("tradeify_1") == "PROP_EXECUTOR_MODE_TRADEIFY_1"
    assert pe.executor_mode(env, "tradeify_1") == "read_only"
    assert pe.executor_mode({"PROP_EXECUTOR_MODE_TRADEIFY_1": "live"}, "tradeify_1") == "live"
    # and arming tradeify never arms breakout
    assert pe.executor_mode({"PROP_EXECUTOR_MODE_TRADEIFY_1": "live"}, "breakout_1") == "read_only"


def test_symbol_override_is_per_account():
    ex = {"enabled_venue_symbols": []}
    env = {"PROP_EXECUTOR_SYMBOLS": "SOLUSD"}
    assert pe.enabled_venues(ex, env=env) == ["SOLUSD"]
    assert pe.enabled_venues(ex, env=env, account_id="tradeify_1") == []


def test_tick_resolves_mode_for_its_own_account():
    from scripts.prop.prop_executor_tick import resolve_mode
    ns = lambda **k: SimpleNamespace(**{"probe_ticket": "", "dry_run": False, "watched_click": False,  # noqa: E731
                                        "account": "tradeify_1", **k})
    assert resolve_mode(ns(watched_click=True), {"PROP_EXECUTOR_MODE": "live"}) == "not_armed"
    assert resolve_mode(ns(watched_click=True), {"PROP_EXECUTOR_MODE_TRADEIFY_1": "live"}) == "live"
    assert resolve_mode(ns(account="breakout_1", watched_click=True), {"PROP_EXECUTOR_MODE": "live"}) == "live"


def test_state_dirs_are_per_account_and_breakout_keeps_its_path():
    from scripts.prop.prop_executor_tick import default_state_dir
    base = Path.home() / ".cache" / "metis-prop-browser"
    assert default_state_dir("breakout_1") == base / "executor"
    assert default_state_dir("tradeify_1") == base / "accounts" / "tradeify_1" / "executor"


# ── ruleset / routing resolution ──────────────────────────────────────────


def test_breakout_rule_paths_are_the_old_constants():
    assert pe.rule_paths_for("breakout_1") == (pe.RULESET_PATH, pe.ROUTING_PATH)


def test_breakout_config_is_unchanged_by_the_refactor():
    """The fields load_config produced before the change, from the same files."""
    import yaml
    c = pe.load_config("breakout_1")
    rules = yaml.safe_load(pe.RULESET_PATH.read_text())
    assert c.account_size_usd == float(rules["account_size_usd"])
    assert c.daily_reset_utc == str(rules["limits"].get("daily_loss_reset_utc") or "00:30")
    assert c.risk_cap_usd == 75.0
    assert c.enabled_venue_symbols == ["ETHUSD", "SOLUSD"]       # B5 (2026-10-01): ETHUSD joined
    assert "SOLUSDT" in c.symbols and c.symbols["SOLUSDT"]["venue"] == "SOLUSD"


def test_other_account_without_a_prop_ruleset_refuses(tmp_path, monkeypatch):
    acc = tmp_path / "accounts.yaml"
    acc.write_text("accounts:\n  other_1:\n    exchange: bybit\n")
    monkeypatch.setattr(pe, "ACCOUNTS_PATH", acc)
    with pytest.raises(KeyError):
        pe.rule_paths_for("other_1")
    with pytest.raises(KeyError):
        pe.rule_paths_for("not_declared")


def test_other_account_reads_its_own_ruleset_and_routing(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    (cfg / "prop_rulesets").mkdir(parents=True)
    (cfg / "prop_rulesets" / "x.yaml").write_text(
        "account_size_usd: 10000\nlimits: {daily_loss_pct: 0.03, max_drawdown_pct: 0.06, "
        "daily_loss_reset_utc: '22:00'}\nsizing: {mode: flat, flat: {max_risk_usd: 50}}\n"
        "routing: prop_rulesets/x_routing.yaml\n")
    (cfg / "prop_rulesets" / "x_routing.yaml").write_text(
        "symbols:\n  XRPUSDT: {dxtrade_symbol: XRPUSD, contract_value_usd_per_point: 1.0}\n")
    (cfg / "accounts.yaml").write_text("accounts:\n  x_1:\n    backtest_ruleset: prop_rulesets/x.yaml\n")
    (cfg / "plat.yaml").write_text(
        "accounts:\n  x_1:\n    platform: dxtrade\n    login_url: https://example.invalid/\n"
        "    executor: {enabled_venue_symbols: []}\n")
    monkeypatch.setattr(pe, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(pe, "ACCOUNTS_PATH", cfg / "accounts.yaml")
    monkeypatch.setattr(pe, "PLATFORMS_PATH", cfg / "plat.yaml")
    c = pe.load_config("x_1")
    assert c.account_size_usd == 10000.0 and c.risk_cap_usd == 50.0 and c.daily_reset_utc == "22:00"
    assert c.symbols["XRPUSDT"]["venue"] == "XRPUSD" and c.symbols["XRPUSDT"]["lot_units"] is None
    assert c.enabled_venue_symbols == []


# ── platform config + env keys ────────────────────────────────────────────


def test_tradeify_platform_entry_never_points_at_breakout():
    from src.prop.platform import load_platform_config
    t = load_platform_config("tradeify_1")
    assert t["platform"] == "dxtrade"
    assert t["login_url"] == "https://dx.tradeify247.co/"
    assert (t["username_env"], t["password_env"]) == ("TRADEIFY_DX_USERNAME", "TRADEIFY_DX_PASSWORD")
    ex = t["executor"]
    # Nothing ENABLED is the arming gate. The only lot entry is the dry-only
    # ETHUSD one (TRADEIFY-DRY, #15846) that lets round-trip-dry reach the
    # form; any other symbol appearing here must be a deliberate, reviewed edit.
    assert ex["enabled_venue_symbols"] == []
    assert ex["lots"] == {
        "ETHUSD": {"lot_units": 1, "lot_step": 0.01, "min_lots": 0.01, "price_step": 0.001},
        "SOLUSD": {"lot_units": 1, "lot_step": 0.01, "min_lots": 0.01, "price_step": 0.001},
        "XRPUSD": {"lot_units": 1, "lot_step": 0.01, "min_lots": 0.01, "price_step": 0.00001}}
    assert ex["watched_click_max_lots"] == {"ETHUSD": 0.01}
    b = load_platform_config("breakout_1")
    assert b["login_url"] == "https://wss.breakoutprop.com/"
    assert (b["username_env"], b["password_env"]) == ("BREAKOUT_DX_USERNAME", "BREAKOUT_DX_PASSWORD")


def _keys(account: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(REPO / "scripts/prop/prop_env_keys.py"), account],
                          capture_output=True, text=True, cwd=str(REPO))


def test_env_keys_breakout_matches_the_old_hardcoded_list():
    r = _keys("breakout_1")
    assert r.returncode == 0
    # the wrappers hardcoded BREAKOUT_DX_USERNAME BREAKOUT_DX_PASSWORD (+ PROP_EXECUTOR_MODE)
    assert r.stdout.split() == ["BREAKOUT_DX_USERNAME", "BREAKOUT_DX_PASSWORD", "PROP_EXECUTOR_MODE"]


def test_env_keys_tradeify_and_unknown_fails_closed():
    r = _keys("tradeify_1")
    assert r.returncode == 0
    assert r.stdout.split() == ["TRADEIFY_DX_USERNAME", "TRADEIFY_DX_PASSWORD", "PROP_EXECUTOR_MODE_TRADEIFY_1"]
    bad = _keys("no_such_account")
    assert bad.returncode == 1 and bad.stdout == ""


def test_tradeify_credentials_are_optional_secrets():
    import yaml
    wf = yaml.safe_load((REPO / ".github/workflows/sync-vm-secrets.yml").read_text())
    blob = str(wf)
    assert "TRADEIFY_DX_USERNAME TRADEIFY_DX_PASSWORD" in blob
    for step in ((wf.get("jobs") or {}).get("sync") or {}).get("steps") or []:
        env = step.get("env") or {}
        if "BREAKOUT_DX_USERNAME" in env:
            assert env.get("TRADEIFY_DX_USERNAME") == "${{ secrets.TRADEIFY_DX_USERNAME }}"
            assert env.get("TRADEIFY_DX_PASSWORD") == "${{ secrets.TRADEIFY_DX_PASSWORD }}"


# ── the shell wrappers keep breakout_1's paths ────────────────────────────


def test_action_wrapper_keeps_breakout_paths_and_scopes_others():
    s = (REPO / "scripts/ops/breakout_login_check_action.sh").read_text()
    assert 'CHECK_KEYS="BREAKOUT_DX_USERNAME BREAKOUT_DX_PASSWORD DASHBOARD_API_TOKEN PROP_EXECUTOR_MODE"' in s
    assert 'FEED_DIR="${BASE}/feed"' in s and 'X_STATE_DIR="${BASE}/executor"' in s
    assert 'FEED_DIR="${BASE}/accounts/${ACCOUNT}/feed"' in s
    assert 'X_STATE_DIR="${BASE}/accounts/${ACCOUNT}/executor"' in s
    # breakout_1 keeps ict-prop-executor.timer; a second account gets its own
    # template instance, never breakout_1's timer (TRADEIFY-EXECUTOR)
    assert 'X_UNIT="ict-prop-executor@${ACCOUNT}"' in s
    assert "sudo -n systemctl enable --now ict-prop-executor.timer" in s


def test_feed_wrapper_keeps_breakout_paths():
    s = (REPO / "scripts/ops/prop_feed_tick.sh").read_text()
    assert 'STATE_DIR="${BASE}/feed"' in s
    assert 'FEED_KEYS="BREAKOUT_DX_USERNAME BREAKOUT_DX_PASSWORD"' in s
    assert 'STATE_DIR="${BASE}/accounts/${ACCOUNT}/feed"' in s


def test_feed_template_is_not_auto_installed():
    # install_systemd_units.sh skips *@* and the timer lives in opt-in/.
    assert (REPO / "deploy/ict-prop-feed@.service").exists()
    assert (REPO / "deploy/opt-in/ict-prop-feed@.timer").exists()
    assert not (REPO / "deploy/ict-prop-feed@.timer").exists()
    inst = (REPO / "scripts/install_systemd_units.sh").read_text()
    assert '"$unit_name" == *@*' in inst
    svc = (REPO / "deploy/ict-prop-feed@.service").read_text()
    assert "Environment=PROP_FEED_ACCOUNT=%i" in svc


def test_mode_key_shell_derivation_matches_python():
    out = subprocess.run(
        ["bash", "-c", "printf '%s' tradeify_1 | tr 'a-z' 'A-Z' | tr -c 'A-Z0-9\\n' '_'"],
        capture_output=True, text=True).stdout
    assert "PROP_EXECUTOR_MODE_" + out == pe.mode_env_for("tradeify_1")
