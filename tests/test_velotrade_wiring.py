"""velotrade_1 (VELOTRADE-WIRE, 2026-10-04): a third prop account, wired DRY.

Pins what the held PR promises: the account emits nothing (dry_run, empty
roster), its terminal entry can act on nothing (no lots, no enabled symbol),
its login reaches only its own check (its own env names, own kill switch), the
ruleset's numbers reach the executor, and breakout_1 / tradeify_1 are
unchanged by the one shared-code change (the opt-in day-start basis).
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.prop import prop_executor as pe
from src.prop import prop_rule_guards as prg
from src.prop.platform import load_platform_config
from src.prop.platform.base import AccountSnapshot

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc)


def _accounts():
    return yaml.safe_load((REPO / "config/accounts.yaml").read_text())["accounts"]


# ── accounts.yaml: dry, no roster ─────────────────────────────────────────


def test_velotrade_1_is_live_with_the_fit_roster():
    a = _accounts()["velotrade_1"]
    assert a["mode"] == "live"            # VELOTRADE-GOLIVE: tickets emitted; orders still gated (kill switch + enabled symbols)
    # VELOTRADE-GOLIVE roster (RQ-20261006-002 / RULE-RQ1006-VELOTRADE-FIT)
    assert a["strategies"] == ["trend_donchian_eth_prop", "trend_donchian_sol_prop"]
    assert a["symbols"] == ["ETHUSDT", "SOLUSDT"]
    assert a["exchange"] == "breakout" and a["type"] == "prop" and a["account_class"] == "prop"
    assert a["backtest_ruleset"] == "prop_rulesets/velotrade_classic_1step.yaml"
    assert a["account_size_usd"] == 5000
    assert a["risk"]["risk_pct"] == 0.005
    assert a["risk"]["max_dd_pct"] == 0.07 and a["risk"]["daily_loss_pct"] == 0.04
    assert "breach_guards" not in a["risk"]   # enforce (the default), like tradeify_1


def test_not_the_purged_scaffold_id():
    acc = _accounts()
    assert "prop_velotrade_1" not in acc
    from scripts.ops.backfill_account_class import _HISTORICAL_CLASS_OVERRIDES
    assert "velotrade_1" not in _HISTORICAL_CLASS_OVERRIDES


# ── terminal entry: nothing can be acted on ───────────────────────────────


def test_platform_entry_points_at_velotrade_and_enables_nothing():
    v = load_platform_config("velotrade_1")
    # VELOTRADE-API-EXEC (2026-10-05): the REST order path, not the browser.
    assert v["platform"] == "dxtrade_api"
    assert v["login_url"] == "https://dx.velotrade.com/dxsca-web"
    assert (v["username_env"], v["password_env"]) == ("VELOTRADE_DX_USERNAME", "VELOTRADE_DX_PASSWORD")
    ex = v["executor"]
    assert ex["enabled_venue_symbols"] == ["ETHUSD", "SOLUSD"]   # VELOTRADE-GOLIVE
    # lots are the REST-measured specs (#16616); they arm nothing on their own
    assert set(ex["lots"]) == {"ETHUSD", "SOLUSD", "XRPUSD", "BTCUSD"}
    assert ex["watched_click_max_lots"] == {}
    # Tradeify's measured quirks are NOT inherited
    for k in ("ticket_opener", "limit_price_fill", "search_query_style"):
        assert k not in v


def test_env_keys_are_velotrades_own():
    r = subprocess.run([sys.executable, str(REPO / "scripts/prop/prop_env_keys.py"), "velotrade_1"],
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0
    assert r.stdout.split() == ["VELOTRADE_DX_USERNAME", "VELOTRADE_DX_PASSWORD",
                                "PROP_EXECUTOR_MODE_VELOTRADE_1"]


def test_kill_switch_is_never_inherited():
    assert pe.mode_env_for("velotrade_1") == "PROP_EXECUTOR_MODE_VELOTRADE_1"
    for env in ({"PROP_EXECUTOR_MODE": "live"}, {"PROP_EXECUTOR_MODE_TRADEIFY_1": "live"}, {}):
        assert pe.executor_mode(env, "velotrade_1") == "read_only"


def test_credentials_are_optional_secrets_in_every_sync_step():
    wf = yaml.safe_load((REPO / ".github/workflows/sync-vm-secrets.yml").read_text())
    job = wf["jobs"]["sync-secrets"]
    assert "VELOTRADE_DX_USERNAME VELOTRADE_DX_PASSWORD" in job["env"]["OPTIONAL_SECRETS"]
    assert "VELOTRADE" not in job["env"]["REQUIRED_SECRETS"]
    n = 0
    for step in job["steps"]:
        env = step.get("env") or {}
        if "TRADEIFY_DX_USERNAME" in env:
            n += 1
            assert env.get("VELOTRADE_DX_USERNAME") == "${{ secrets.VELOTRADE_DX_USERNAME }}"
            assert env.get("VELOTRADE_DX_PASSWORD") == "${{ secrets.VELOTRADE_DX_PASSWORD }}"
    assert n == 2   # the presence report and the sync itself


# ── ruleset -> executor config ────────────────────────────────────────────


def test_executor_config_reads_velotrades_numbers():
    c = pe.load_config("velotrade_1")
    assert c.account_size_usd == 5000.0
    assert c.daily_loss_pct == 0.04 and c.max_dd_pct == 0.07
    assert c.daily_reset_utc == "00:30"
    assert c.risk_cap_usd == 25.0
    assert c.daily_loss_reset_basis == "max_balance_equity"
    assert c.daily_loss_amount_basis == "day_start_balance"   # 4% of the day-start value, not of size
    assert c.enabled_venue_symbols == ["ETHUSD", "SOLUSD"]
    assert {s: v["venue"] for s, v in c.symbols.items()} == {
        "ETHUSDT": "ETHUSD", "SOLUSDT": "SOLUSD", "XRPUSDT": "XRPUSD"}
    # Measured lots now reach the executor; enabled_venue_symbols (empty)
    # is what keeps every symbol from being acted on.
    assert all(v["lot_units"] == 1 for v in c.symbols.values())


def test_leverage_caps_resolve_by_bot_symbol():
    """The guards look the cap up by the ticket's (bot) symbol. Keyed by the
    venue name, ETH/SOL fell through to the 2x default."""
    c = pe.load_config("velotrade_1")
    assert prg.leverage_cap_for(c.leverage_caps, "ETHUSDT") == 5.0
    assert prg.leverage_cap_for(c.leverage_caps, "SOLUSDT") == 5.0
    assert prg.leverage_cap_for(c.leverage_caps, "XRPUSDT") == 2.0
    assert prg.leverage_cap_for(c.leverage_caps, "ADAUSDT") == 2.0
    pro = prg.leverage_caps(prg.load_limits(REPO / "config/prop_rulesets/velotrade_pro_1step.yaml"))
    assert pro["ETHUSDT"] == 5.0 and "ETHUSD" not in pro


def test_daily_floor_is_four_percent_of_day_start():
    assert prg.daily_floor(daily_loss_pct=0.04, day_start_balance=5000.0, account_size_usd=5000.0,
                           amount_basis="day_start_balance") == 4800.0


# ── day-start basis: opt-in, and breakout_1 / tradeify_1 unchanged ───────


def test_max_balance_equity_basis_captures_the_higher():
    snap = AccountSnapshot(balance=5000.0, equity=5120.0, unrealized=120.0, realized_today=0.0)
    st: dict = {}
    assert pe.day_start_balance(st, snap, NOW, "00:30", "max_balance_equity") == 5120.0
    # equity below balance: the balance still wins
    st2: dict = {}
    down = AccountSnapshot(balance=5000.0, equity=4900.0, unrealized=-100.0, realized_today=0.0)
    assert pe.day_start_balance(st2, down, NOW, "00:30", "max_balance_equity") == 5000.0


def test_default_basis_is_balance_only_as_before():
    snap = AccountSnapshot(balance=5000.0, equity=5120.0, unrealized=120.0, realized_today=0.0)
    assert pe.day_start_balance({}, snap, NOW, "00:30") == 5000.0
    assert pe.day_start_balance({}, snap, NOW, "00:30", None) == 5000.0


def test_other_accounts_declare_no_reset_basis():
    assert pe.load_config("breakout_1").daily_loss_reset_basis is None
    assert pe.load_config("tradeify_1").daily_loss_reset_basis is None


def test_diag_can_read_velotrade_units():
    s = (REPO / "src/web/api/routers/diag.py").read_text()
    for u in ("ict-prop-feed@velotrade_1.service", "ict-prop-feed@velotrade_1.timer",
              "ict-prop-executor@velotrade_1.service", "ict-prop-executor@velotrade_1.timer"):
        assert f'"{u}"' in s
