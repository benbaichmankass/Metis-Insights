"""breakout_1 is RETIRED (operator decision 2026-10-07).

Verbatim label: "Dead: clear its roster, retire the row (Recommended)".
MEASURED: balance = equity $4,698.00 vs the $4,700 static-drawdown floor since
2026-10-04. The entry stays for history and journal joins; it carries no legs and
never goes live again. breakout_2 is the live Breakout account.
"""
from src.config.accounts_loader import load_accounts_dict


def test_breakout_1_carries_no_legs_and_is_not_live():
    acct = load_accounts_dict()["breakout_1"]
    assert not (acct.get("strategies") or []), "breakout_1 is retired: its roster must stay empty"
    assert acct.get("mode") == "dry_run"


def test_breakout_2_is_untouched_by_the_retirement():
    assert load_accounts_dict()["breakout_2"].get("mode") == "live"


def test_breakout_1_is_flagged_retired_and_hidden_from_hourly_snapshot(monkeypatch):
    """One declarative switch: `retired: true` -> operator surfaces skip the account."""
    from src.prop.prop_identity import is_retired_account
    assert is_retired_account(load_accounts_dict()["breakout_1"])
    assert not is_retired_account(load_accounts_dict()["breakout_2"])
    assert not is_retired_account({"retired": "true"}), "fail-open: only a real bool retires"

    import src.runtime.hourly_report as hr
    import src.bot.data_loaders as dl
    seen = []
    monkeypatch.setattr(dl, "list_accounts", lambda: [
        {"account_id": "breakout_1", "retired": True, "exchange": "breakout"},
        {"account_id": "tradeify_1", "exchange": "breakout"}])
    monkeypatch.setattr(dl, "account_balance", lambda a: seen.append(a["account_id"]))
    monkeypatch.setattr(dl, "account_open_positions", lambda a: None)
    monkeypatch.setattr(hr, "_load_balance_snapshots", lambda: {})
    monkeypatch.setattr(hr, "_record_balance_snapshot_to_db", lambda *a, **k: None)
    monkeypatch.setattr(hr, "_prop_status", lambda aid: {})
    monkeypatch.setattr(hr, "_save_balance_snapshots", lambda *a, **k: None, raising=False)
    rows = hr.account_snapshots()
    assert [r["account_id"] for r in rows] == ["tradeify_1"]
    assert "breakout_1" not in seen


def test_retired_prop_feed_is_never_re_enabled_by_deploy():
    src = open("scripts/install_systemd_units.sh").read()
    line = next(ln for ln in src.splitlines() if ln.startswith("_RETIRED_TIMERS="))
    assert "ict-prop-feed.timer" in line
