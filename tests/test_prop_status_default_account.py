from src.web.api.routers import prop


def test_status_default_account_is_the_live_breakout_account_not_retired_breakout_1():
    assert prop._DEFAULT_ACCOUNT == "breakout_2"
