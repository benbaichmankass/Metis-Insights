"""DXTRADE-PROBE-ACCOUNT: the REST probe's fixed account choice + env-absent read_state."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("dx_probe", ROOT / "scripts/prop/velotrade_api_probe.py")
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

PLATFORMS = yaml.safe_load((ROOT / "config/prop_platforms.yaml").read_text())


def test_velotrade_resolves_from_config():
    r = probe.resolve_account("velotrade_1", PLATFORMS)
    assert r["base"] == "https://dx.velotrade.com/dxsca-web"
    assert (r["username_env"], r["password_env"], r["domain_env"]) == (
        "VELOTRADE_DX_USERNAME", "VELOTRADE_DX_PASSWORD", "VELOTRADE_DX_DOMAIN")


def test_tradeify_resolves_browser_style_login_url_to_rest_base():
    r = probe.resolve_account("tradeify_1", PLATFORMS)
    assert r["base"] == "https://dx.tradeify247.co/dxsca-web"
    assert r["username_env"] == "TRADEIFY_DX_USERNAME"
    assert r["domain_env"] == "TRADEIFY_DX_DOMAIN"


@pytest.mark.parametrize("account", ["breakout_1", "bybit_1", "evil.example.com", ""])
def test_account_outside_the_fixed_choice_is_refused(account):
    with pytest.raises(probe.AccountRefused):
        probe.resolve_account(account, PLATFORMS)


def test_non_dxtrade_platform_refused():
    plats = {"accounts": {"tradeify_1": {"platform": "tradovate", "login_url": "https://dx.tradeify247.co/",
                                          "username_env": "A_USERNAME", "password_env": "A_PASSWORD"}}}
    with pytest.raises(probe.AccountRefused, match="not a DXtrade platform"):
        probe.resolve_account("tradeify_1", plats)


def test_config_cannot_redirect_the_host():
    plats = {"accounts": {"tradeify_1": {"platform": "dxtrade", "login_url": "https://attacker.example/",
                                          "username_env": "T_USERNAME", "password_env": "T_PASSWORD"}}}
    with pytest.raises(probe.AccountRefused, match="fixed"):
        probe.resolve_account("tradeify_1", plats)


def test_argparse_rejects_free_text_account(capsys):
    with pytest.raises(SystemExit):
        probe.main(["--account", "dx.evil.example"])


def test_env_absent_is_its_own_read_state_not_a_login_failure(monkeypatch, capsys):
    for k in ("TRADEIFY_DX_USERNAME", "TRADEIFY_DX_PASSWORD", "TRADEIFY_DX_DOMAIN"):
        monkeypatch.delenv(k, raising=False)

    def boom(*a, **k):  # no request may be made without credentials
        raise AssertionError("network call with no credentials")

    monkeypatch.setattr(probe, "_call", boom)
    assert probe.main(["--account", "tradeify_1"]) == 2
    out = capsys.readouterr().out
    assert "read_state: env_absent" in out
    assert "login_rejected" not in out and "rest_login_failed" not in out
    assert '"login_ok": null' in out and '"reachable": null' in out


def test_default_account_stays_velotrade(monkeypatch, capsys):
    for k in ("VELOTRADE_DX_USERNAME", "VELOTRADE_DX_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert probe.main([]) == 2
    assert '"account": "velotrade_1"' in capsys.readouterr().out


def test_login_ok_summary_has_booleans_and_no_balance(monkeypatch, capsys):
    monkeypatch.setenv("TRADEIFY_DX_USERNAME", "u-secret-name")
    monkeypatch.setenv("TRADEIFY_DX_PASSWORD", "p-secret-word")
    acct = {"accountCode": "ACCT-SECRET-1", "isPositionBased": True}

    def fake(method, path, token=None, body=None):
        if path == "/login":
            return 200, {"sessionToken": "tok-secret", "timeout": 1}, ""
        if path.startswith("/users/"):
            return 200, {"accounts": [acct]}, ""
        if path.endswith("/metrics"):
            return 200, {"metrics": [{"balance": 98765.4, "equity": 1}]}, ""
        if "/instruments/" in path and "query" not in path:
            sym = path.rsplit("/", 1)[1].replace("%2F", "")
            return 200, {"symbol": sym, "type": "CURRENCY"}, ""
        return 200, [], ""

    monkeypatch.setattr(probe, "_call", fake)
    assert probe.main(["--account", "tradeify_1"]) == 0
    out = capsys.readouterr().out
    assert '"account_model": "positionBased"' in out
    assert '"symbols_present": {"ETH": true, "SOL": true, "XRP": true}' in out
    for secret in ("98765", "ACCT-SECRET-1", "tok-secret", "u-secret-name", "p-secret-word"):
        assert secret not in out


def test_call_choke_point_still_refuses_orders():
    with pytest.raises(RuntimeError):
        probe._call("GET", "/accounts/X/orders")
    with pytest.raises(RuntimeError):
        probe._call("POST", "/accounts/X/orders")


def test_workflow_and_action_script_pin_the_same_choice():
    wf = (ROOT / ".github/workflows/system-actions.yml").read_text()
    m = re.search(r'if \[ "\$\{ACTION\}" = "velotrade-api-probe" \]; then\n\s+case "\$\{ACCOUNT_ID:-\}" in\n\s+(.*?) : ;;', wf)
    assert m and m.group(1) == '""|velotrade_1|tradeify_1)'
    sh = (ROOT / "scripts/ops/velotrade_api_probe_action.sh").read_text()
    assert "velotrade_1|tradeify_1) :" in sh and '--account "${PROBE_ACCOUNT}"' in sh
