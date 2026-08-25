"""`/v2/account` already answers "how much can this account place?" — read it.

BL-20260825-ALPACA-CAPACITY-FIELDS-FETCHED-THEN-DISCARDED.

`account_status()` fetched the whole `/v2/account` payload and kept eight
authorization keys, throwing the rest away. So the one field a gross-exposure
CEILING depends on had no read surface anywhere: `multiplier` is the
cash-vs-margin discriminator (1 = cash, 2 = Reg-T, 4 = PDT day-trading), and a
cash account can never exceed 1.0x gross — a 2.0x ceiling there is decorative,
while on Reg-T the same value binds.

MEASURED 2026-08-25 on the live fleet, which is why this blocks a real
decision: `alpaca_live` holds equity $200.10 with ZERO gross notional across 91
consecutive soak rows (22.7h), while its paper mirrors run a continuous ~2.0x
book — `alpaca_portfolio` $98,618.60 at 2.0057x, `alpaca_paper` $83,405.94 at
1.9949x. The mirrors are the only available basis for `alpaca_live`'s ceiling
and they cannot be transferred until this field says whether 2.0x is even
reachable on the target.

STEP 1 of BL-20260824-ALPACA-LIVE-GOLIVE-SEQUENCE is "declare a bound ... the
VALUE needs a basis". This is the read the basis needs.
"""
from __future__ import annotations

import pathlib

from src.units.accounts.alpaca_client import AlpacaClient

_REPO = pathlib.Path(__file__).resolve().parents[1]

# A realistic /v2/account body: the eight authorization keys plus the capacity
# fields Alpaca actually returns.
_ACCOUNT = {
    "status": "ACTIVE", "trading_blocked": False, "account_blocked": False,
    "trade_suspended_by_user": False, "transfers_blocked": False,
    "shorting_enabled": False, "crypto_status": "INACTIVE", "currency": "USD",
    "multiplier": "1", "buying_power": "200.10", "regt_buying_power": "200.10",
    "daytrading_buying_power": "0", "cash": "200.10", "equity": "200.10",
    "last_equity": "200.10", "pattern_day_trader": False, "daytrade_count": 0,
    "id": "unused", "account_number": "unused",
}


def _client(payload):
    c = AlpacaClient(api_key="k", api_secret="s")
    c._request = lambda *a, **k: {"retCode": 0, "result": payload}
    return c


class TestTheCapacityBlockIsReadable:
    def test_multiplier_is_exposed(self):
        """The whole point: cash-vs-margin must be READ, not inferred."""
        out = _client(_ACCOUNT).account_status()
        assert out["capacity"]["multiplier"] == "1"

    def test_the_pdt_fields_ride_along(self):
        """The other structural bound on a sub-$25k account: FINRA caps a
        non-PDT margin account at 3 day trades per 5 days, modelled nowhere."""
        cap = _client(_ACCOUNT).account_status()["capacity"]
        assert cap["pattern_day_trader"] is False
        assert cap["daytrade_count"] == 0

    def test_the_buying_power_family_is_exposed(self):
        cap = _client(_ACCOUNT).account_status()["capacity"]
        for k in ("buying_power", "regt_buying_power",
                  "daytrading_buying_power", "cash", "equity"):
            assert k in cap, k


class TestTheAuthorizationFlagsAreUNCHANGED:
    """The eight keys keep meaning exactly 'may this account trade at all?'.
    Capacity is a SECOND question and is nested so the name stays honest."""

    def test_the_eight_flags_are_still_top_level(self):
        out = _client(_ACCOUNT).account_status()
        for k in ("status", "trading_blocked", "account_blocked",
                  "trade_suspended_by_user", "transfers_blocked",
                  "shorting_enabled", "crypto_status", "currency"):
            assert k in out, k

    def test_capacity_does_not_leak_into_the_flags(self):
        out = _client(_ACCOUNT).account_status()
        assert "multiplier" not in out
        assert "buying_power" not in out

    def test_unrelated_payload_keys_are_still_dropped(self):
        """The extraction stays an allowlist — it must not become a passthrough
        that ships account ids onto a diag surface."""
        out = _client(_ACCOUNT).account_status()
        assert "id" not in out and "account_number" not in out
        assert "id" not in out["capacity"]


class TestAbsenceIsVisibleNotFabricated:
    def test_a_missing_multiplier_is_OMITTED_not_defaulted(self):
        """A consumer must not read an absent `multiplier` as 'cash' — that is
        the collapse this field exists to prevent, one level up."""
        payload = {k: v for k, v in _ACCOUNT.items() if k != "multiplier"}
        cap = _client(payload).account_status()["capacity"]
        assert "multiplier" not in cap

    def test_no_capacity_fields_at_all_omits_the_block(self):
        """An empty `capacity: {}` would read as 'we asked and it is empty'."""
        payload = {k: v for k, v in _ACCOUNT.items()
                   if k in ("status", "trading_blocked", "currency")}
        assert "capacity" not in _client(payload).account_status()

    def test_a_read_failure_still_returns_None(self):
        c = AlpacaClient(api_key="k", api_secret="s")
        c._request = lambda *a, **k: {"retCode": 403, "retMsg": "forbidden"}
        assert c.account_status() is None

    def test_missing_creds_still_returns_None(self):
        assert AlpacaClient(api_key="", api_secret="").account_status() is None


def test_the_live_shape_reproduces():
    """The exact alpaca_live flags read from /api/diag/broker_account_status on
    2026-08-25, so the block sits beside a payload known to be real."""
    out = _client(_ACCOUNT).account_status()
    assert out["shorting_enabled"] is False and out["status"] == "ACTIVE"
    assert out["capacity"]["cash"] == "200.10"
