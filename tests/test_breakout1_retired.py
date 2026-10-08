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
