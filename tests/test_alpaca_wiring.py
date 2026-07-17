"""M15 Phase 2b — Alpaca wiring tests (new-broker checklist §8).

Registry, factory cred handling, executor edges, bracket-order payload
shapes (mocked HTTP, no network), idempotent close, and the inert
``accounts.yaml::alpaca_paper`` gates.
"""
from __future__ import annotations

import pytest
import yaml

from src.units.accounts.alpaca_client import AlpacaClient, MissingCredentialsError
from src.units.accounts.clients import alpaca_client_for
from src.units.accounts.execute import _submit_order
from src.units.accounts.integrator import EXCHANGE_MAP, AlpacaAPI


def test_exchange_map_has_alpaca():
    assert EXCHANGE_MAP["alpaca"] is AlpacaAPI


# ------------------------------------------------------------ factory
def test_factory_returns_none_without_creds(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    assert alpaca_client_for({"exchange": "alpaca"}) is None


def test_factory_builds_paper_client_with_creds(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "k")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "s")
    cli = alpaca_client_for({"exchange": "alpaca"})
    assert isinstance(cli, AlpacaClient)
    assert cli.env == "paper"
    assert "paper-api" in cli.base_url


def test_factory_honors_per_account_key_env_for_concurrent_live(monkeypatch):
    """alpaca_live names its OWN key envs so paper + live run concurrently.

    The paper account (no api_key_env) reads the shared globals; a live
    account names ALPACA_API_KEY_ID_LIVE / ALPACA_API_SECRET_KEY_LIVE +
    alpaca_env: live, so the two resolve to DISTINCT credentials/hosts.
    """
    monkeypatch.setenv("ALPACA_API_KEY_ID", "paper-k")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "paper-s")
    monkeypatch.setenv("ALPACA_API_KEY_ID_LIVE", "live-k")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY_LIVE", "live-s")

    paper = alpaca_client_for({"exchange": "alpaca"})
    live = alpaca_client_for({
        "exchange": "alpaca",
        "api_key_env": "ALPACA_API_KEY_ID_LIVE",
        "api_secret_env": "ALPACA_API_SECRET_KEY_LIVE",
        "alpaca_env": "live",
    })
    assert paper.api_key == "paper-k" and paper.env == "paper"
    assert "paper-api" in paper.base_url
    assert live.api_key == "live-k" and live.api_secret == "live-s"
    assert live.env == "live" and "paper-api" not in live.base_url


def test_factory_none_when_per_account_live_keys_unset(monkeypatch):
    """A live account whose own key env is unset → None (stays inert)."""
    monkeypatch.delenv("ALPACA_API_KEY_ID_LIVE", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY_LIVE", raising=False)
    assert alpaca_client_for({
        "exchange": "alpaca",
        "api_key_env": "ALPACA_API_KEY_ID_LIVE",
        "api_secret_env": "ALPACA_API_SECRET_KEY_LIVE",
    }) is None


# ------------------------------------------------------------ executor
_ORDER = {
    "symbol": "SPY",
    "side": "Buy",
    "qty": 3,
    "entry": 600.0,
    "sl": 594.0,
    "tp": 612.0,
    "strategy": "spy_trend_long_1d",
}
_CFG = {"exchange": "alpaca", "account_id": "alpaca_paper"}


def test_submit_order_missing_client_raises_missing_creds():
    with pytest.raises(MissingCredentialsError):
        _submit_order(None, dict(_ORDER), dict(_CFG))


def test_submit_order_wrong_client_type():
    with pytest.raises(TypeError):
        _submit_order(object(), dict(_ORDER), dict(_CFG))


class _StubAlpaca(AlpacaClient):
    def __init__(self, resp):
        super().__init__(api_key="k", api_secret="s")
        self._resp = resp
        self.last_order = None

    def place(self, order):
        self.last_order = order
        return self._resp


def test_submit_order_success_returns_order_id():
    cli = _StubAlpaca({"retCode": 0, "result": {"orderId": "abc-123"}})
    assert _submit_order(cli, dict(_ORDER), dict(_CFG)) == "abc-123"
    assert cli.last_order["symbol"] == "SPY"


def test_submit_order_reject_raises_runtime_error():
    cli = _StubAlpaca({"retCode": 403, "retMsg": "insufficient buying power"})
    with pytest.raises(RuntimeError, match="insufficient buying power"):
        _submit_order(cli, dict(_ORDER), dict(_CFG))


# ------------------------------------------------------------ client HTTP
class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.content = b"x"

    def json(self):
        return self._payload


def test_client_place_builds_bracket_order(monkeypatch):
    """Body-construction test — disables the post-accept confirm-poll
    (covered separately below) so a single POST is the only call made."""
    monkeypatch.setenv("ALPACA_PLACE_CONFIRM_S", "0")
    captured = {}

    def fake_request(method, url, headers=None, json=None, timeout=None):
        captured.update(method=method, url=url, body=json)
        return _Resp({"id": "ord-9"})

    monkeypatch.setattr(
        "src.units.accounts.alpaca_client.requests.request", fake_request
    )
    cli = AlpacaClient(api_key="k", api_secret="s")
    out = cli.place({"symbol": "spy", "side": "Sell", "qty": 2.4,
                     "sl": 610.456, "tp": 588.1})
    assert out == {"retCode": 0, "result": {"orderId": "ord-9"}}
    b = captured["body"]
    assert b["symbol"] == "SPY"
    assert b["qty"] == "2"  # rounded to whole shares (bracket constraint)
    assert b["side"] == "sell"
    assert b["order_class"] == "bracket"
    assert b["take_profit"]["limit_price"] == "588.10"
    assert b["stop_loss"]["stop_price"] == "610.46"
    assert "/v2/orders" in captured["url"]


# ------------------------------------------ place() post-accept rejection
# BL-20260707-ALPACA-ENTRY-FILL-CONFIRM-GAP: Alpaca's HTTP 2xx on POST
# /v2/orders means the order passed SYNCHRONOUS validation, not that it will
# actually execute — an async risk check can still reject it moments later.
# place() must poll and surface that as a real failure, not journal a
# phantom position for an order the broker never actually executed.

def test_client_place_confirms_no_rejection_when_filled(monkeypatch):
    """The order fills on the FIRST confirmation poll — success, no sleep
    needed."""
    monkeypatch.setattr("src.units.accounts.alpaca_client.time.sleep",
                         lambda *_: pytest.fail("should not need to sleep"))

    def fake_request(method, path, json_body=None):
        if method == "POST":
            return {"retCode": 0, "result": {"id": "ord-filled"}}
        if method == "GET" and path == "/v2/orders/ord-filled":
            return {"retCode": 0, "result": {"status": "filled"}}
        return {"retCode": 0, "result": {}}

    cli = AlpacaClient(api_key="k", api_secret="s")
    monkeypatch.setattr(cli, "_request", fake_request)
    res = cli.place({"symbol": "SPY", "side": "Buy", "qty": 5,
                     "sl": 594.0, "tp": 612.0})
    assert res == {"retCode": 0, "result": {"orderId": "ord-filled"}}


def test_client_place_detects_post_accept_rejection(monkeypatch):
    """The regression case: the order is accepted (2xx), then an async
    Alpaca risk check rejects it moments later — place() must NOT report
    success, or _submit_order/_log_trade_to_journal will journal a phantom
    open position for a trade that never actually happened on the broker."""
    monkeypatch.setenv("ALPACA_PLACE_CONFIRM_S", "0.05")
    monkeypatch.setattr("src.units.accounts.alpaca_client.time.sleep",
                         lambda *_: None)

    def fake_request(method, path, json_body=None):
        if method == "POST":
            return {"retCode": 0, "result": {"id": "ord-rejected"}}
        if method == "GET" and path == "/v2/orders/ord-rejected":
            return {"retCode": 0, "result": {"status": "rejected"}}
        return {"retCode": 0, "result": {}}

    cli = AlpacaClient(api_key="k", api_secret="s")
    monkeypatch.setattr(cli, "_request", fake_request)
    res = cli.place({"symbol": "QQQ", "side": "Buy", "qty": 3,
                     "sl": 700.0, "tp": 730.0})
    assert res["retCode"] != 0
    assert "accepted then rejected" in res["retMsg"].lower()


def test_client_place_still_pending_at_deadline_is_success(monkeypatch):
    """An order still 'new'/pending at the confirm deadline is reported as
    success unchanged — a genuinely-slow-but-good fill must never be treated
    as a failure (that would leave a REAL accepted broker order untracked
    and unjournaled, which is worse than a possibly-stale entry price)."""
    monkeypatch.setenv("ALPACA_PLACE_CONFIRM_S", "0.05")
    monkeypatch.setattr("src.units.accounts.alpaca_client.time.sleep",
                         lambda *_: None)

    def fake_request(method, path, json_body=None):
        if method == "POST":
            return {"retCode": 0, "result": {"id": "ord-pending"}}
        if method == "GET" and path == "/v2/orders/ord-pending":
            return {"retCode": 0, "result": {"status": "new"}}
        return {"retCode": 0, "result": {}}

    cli = AlpacaClient(api_key="k", api_secret="s")
    monkeypatch.setattr(cli, "_request", fake_request)
    res = cli.place({"symbol": "GLD", "side": "Buy", "qty": 2,
                     "sl": 370.0, "tp": 400.0})
    assert res == {"retCode": 0, "result": {"orderId": "ord-pending"}}


def test_client_place_confirm_disabled_restores_legacy_behavior(monkeypatch):
    """ALPACA_PLACE_CONFIRM_S <= 0 restores the pre-fix accept-is-success
    behaviour — no confirmation poll at all."""
    monkeypatch.setenv("ALPACA_PLACE_CONFIRM_S", "0")
    calls = []

    def fake_request(method, path, json_body=None):
        calls.append(method)
        return {"retCode": 0, "result": {"id": "ord-unconfirmed"}}

    cli = AlpacaClient(api_key="k", api_secret="s")
    monkeypatch.setattr(cli, "_request", fake_request)
    res = cli.place({"symbol": "SPY", "side": "Buy", "qty": 1,
                     "sl": 594.0, "tp": 612.0})
    assert res == {"retCode": 0, "result": {"orderId": "ord-unconfirmed"}}
    assert calls == ["POST"]  # no confirm-poll GET


def test_client_requires_creds_and_degrades():
    cli = AlpacaClient(api_key="", api_secret="")
    with pytest.raises(MissingCredentialsError):
        cli.place({"symbol": "SPY", "side": "Buy", "qty": 1})
    assert cli.balance() is None
    # positions() returns None (not []) on a read failure — incl. missing creds —
    # so account_open_positions can distinguish "could not read" from "flat"
    # (BL-20260622-ALPACA-SNAPSHOT-FALSECLOSE).
    assert cli.positions() is None


def test_client_positions_none_on_http_failure(monkeypatch):
    """A non-2xx /v2/positions read returns None (read failure), never []."""
    monkeypatch.setattr(
        "src.units.accounts.alpaca_client.requests.request",
        lambda *a, **k: _Resp({"message": "rate limited"}, status=429),
    )
    cli = AlpacaClient(api_key="k", api_secret="s")
    assert cli.positions() is None


def test_client_positions_empty_list_when_genuinely_flat(monkeypatch):
    """A successful read with no positions returns [] (genuinely flat), distinct
    from the None read-failure case."""
    monkeypatch.setattr(
        "src.units.accounts.alpaca_client.requests.request",
        lambda *a, **k: _Resp([], status=200),
    )
    cli = AlpacaClient(api_key="k", api_secret="s")
    assert cli.positions() == []


# ------------------------------------------------- balance/buying_power(0.0)
# BL-20260707: `equity or cash` / `if bp > 0:` treated a genuinely-zero
# reading the same as "couldn't parse" — the same truthiness shape as the
# already-fixed account_open_positions bug, just one layer up.

def test_client_balance_zero_equity_is_not_dropped(monkeypatch):
    """A real equity=0.0 must be returned as 0.0, not silently swapped for
    a different field (cash) just because 0.0 is falsy."""
    monkeypatch.setattr(
        "src.units.accounts.alpaca_client.requests.request",
        lambda *a, **k: _Resp({"equity": "0", "cash": "149.80"}, status=200),
    )
    cli = AlpacaClient(api_key="k", api_secret="s")
    assert cli.balance() == 0.0


def test_client_balance_falls_back_to_cash_only_when_equity_absent(monkeypatch):
    """Falling back to `cash` is correct when `equity` is genuinely ABSENT
    from the response, not merely zero."""
    monkeypatch.setattr(
        "src.units.accounts.alpaca_client.requests.request",
        lambda *a, **k: _Resp({"cash": "149.80"}, status=200),
    )
    cli = AlpacaClient(api_key="k", api_secret="s")
    assert cli.balance() == 149.80


def test_client_buying_power_zero_is_returned_not_none(monkeypatch):
    """A genuine regt_buying_power=0.0 (fully invested / no free margin) must
    be returned as 0.0 — the caller (Coordinator.multi_account_execute) reads
    None as 'could not determine' and falls back to a MORE PERMISSIVE sizing
    basis, which is backwards for an account with zero free margin."""
    monkeypatch.setattr(
        "src.units.accounts.alpaca_client.requests.request",
        lambda *a, **k: _Resp(
            {"regt_buying_power": "0", "buying_power": "500.00", "cash": "500.00"},
            status=200,
        ),
    )
    cli = AlpacaClient(api_key="k", api_secret="s")
    assert cli.buying_power() == 0.0


def test_client_buying_power_falls_back_when_key_absent(monkeypatch):
    """Falling through to the next key is correct when regt_buying_power is
    ABSENT, not merely zero."""
    monkeypatch.setattr(
        "src.units.accounts.alpaca_client.requests.request",
        lambda *a, **k: _Resp({"buying_power": "500.00"}, status=200),
    )
    cli = AlpacaClient(api_key="k", api_secret="s")
    assert cli.buying_power() == 500.00


def test_client_buying_power_none_when_all_keys_absent_or_unparseable(monkeypatch):
    monkeypatch.setattr(
        "src.units.accounts.alpaca_client.requests.request",
        lambda *a, **k: _Resp({"regt_buying_power": "not-a-number"}, status=200),
    )
    cli = AlpacaClient(api_key="k", api_secret="s")
    assert cli.buying_power() is None


# --- account_status() — BL-20260701-ALPACA-STATUS-VISIBILITY ---------------

def test_client_account_status_surfaces_authorization_flags(monkeypatch):
    """account_status() returns the trade-authorization flags from /v2/account —
    the read that distinguishes 'creds authenticate' from 'account can trade'."""
    monkeypatch.setattr(
        "src.units.accounts.alpaca_client.requests.request",
        lambda *a, **k: _Resp({
            "status": "ACTIVE", "trading_blocked": False,
            "account_blocked": False, "trade_suspended_by_user": False,
            "transfers_blocked": False, "shorting_enabled": True,
            "crypto_status": "ACTIVE", "currency": "USD",
            "equity": "150.09", "cash": "150.09",  # extra fields ignored
        }, status=200),
    )
    cli = AlpacaClient(api_key="k", api_secret="s")
    st = cli.account_status()
    assert st == {
        "status": "ACTIVE", "trading_blocked": False, "account_blocked": False,
        "trade_suspended_by_user": False, "transfers_blocked": False,
        "shorting_enabled": True, "crypto_status": "ACTIVE", "currency": "USD",
    }


def test_client_account_status_reports_restricted_account(monkeypatch):
    """A restricted account (reads OK, orders blocked) surfaces the blocking
    flags — the exact 'reads OK / orders unauthorized' triage signal."""
    monkeypatch.setattr(
        "src.units.accounts.alpaca_client.requests.request",
        lambda *a, **k: _Resp({
            "status": "ACCOUNT_UPDATED", "trading_blocked": True,
            "account_blocked": False,
        }, status=200),
    )
    cli = AlpacaClient(api_key="k", api_secret="s")
    st = cli.account_status()
    assert st["trading_blocked"] is True
    assert st["status"] == "ACCOUNT_UPDATED"


def test_client_account_status_none_on_read_failure(monkeypatch):
    """A non-2xx /v2/account read returns None (read failure), never a partial."""
    monkeypatch.setattr(
        "src.units.accounts.alpaca_client.requests.request",
        lambda *a, **k: _Resp({"message": "unauthorized"}, status=401),
    )
    cli = AlpacaClient(api_key="k", api_secret="s")
    assert cli.account_status() is None


def test_client_account_status_none_without_creds():
    assert AlpacaClient(api_key="", api_secret="").account_status() is None


def test_client_close_idempotent_on_404(monkeypatch):
    monkeypatch.setattr(
        "src.units.accounts.alpaca_client.requests.request",
        lambda *a, **k: _Resp({"message": "position does not exist"}, status=404),
    )
    cli = AlpacaClient(api_key="k", api_secret="s")
    assert cli.close("SPY")["retCode"] == 0


def test_client_close_cancels_resting_orders_first(monkeypatch):
    """BL-20260707: a resting bracket SL/TP leg still holds the full qty as
    held_for_orders, so DELETE /v2/positions/{symbol} alone can be rejected
    with "insufficient qty available for order" — close() must cancel the
    symbol's resting orders BEFORE the flatten, mirroring IBClient.close and
    place_protective's own cancel-before-place idempotency guard."""
    calls = []

    def fake_request(method, path, json_body=None):
        calls.append((method, path))
        if method == "GET":  # _open_orders_for_symbol (cancel pre-pass)
            return {"retCode": 0, "result": [
                {"id": "resting-sl", "symbol": "QQQ", "type": "stop"},
                {"id": "resting-tp", "symbol": "QQQ", "type": "limit"},
            ]}
        return {"retCode": 0, "result": {"id": "flatten-1"}}

    cli = AlpacaClient(api_key="k", api_secret="s")
    monkeypatch.setattr(cli, "_request", fake_request)
    res = cli.close("QQQ")
    assert res["retCode"] == 0
    deletes = [p for (m, p) in calls if m == "DELETE"]
    # Both resting legs cancelled, and BEFORE the position-flatten DELETE.
    assert "/v2/orders/resting-sl" in deletes
    assert "/v2/orders/resting-tp" in deletes
    assert deletes.index("/v2/orders/resting-sl") < deletes.index("/v2/positions/QQQ")
    assert deletes.index("/v2/orders/resting-tp") < deletes.index("/v2/positions/QQQ")
    assert deletes[-1] == "/v2/positions/QQQ"


def test_client_close_no_resting_orders_is_single_call(monkeypatch):
    """No resting orders → the cancel pre-pass is a cheap no-op GET, no
    spurious DELETEs, and the flatten still goes through unchanged."""
    calls = []

    def fake_request(method, path, json_body=None):
        calls.append((method, path))
        if method == "GET":
            return {"retCode": 0, "result": []}
        return {"retCode": 0, "result": {"id": "flatten-2"}}

    cli = AlpacaClient(api_key="k", api_secret="s")
    monkeypatch.setattr(cli, "_request", fake_request)
    res = cli.close("SPY")
    assert res["retCode"] == 0
    assert [p for (m, p) in calls if m == "DELETE"] == ["/v2/positions/SPY"]


def test_client_close_waits_for_cancels_to_settle_before_flatten(monkeypatch):
    """BL-20260708-ALPACA-CLOSE-CANCEL-RACE: Alpaca order cancels are async —
    after DELETE /v2/orders/{id} the leg sits in pending_cancel holding the
    shares. close() must WAIT for the symbol's open orders to clear before the
    position-flatten DELETE, else the flatten races the cancel and Alpaca
    rejects it with 'insufficient qty available (available: 0)'. Here the
    open-orders GET reports the resting leg still present on the first
    post-cancel poll, then clears — the flatten must fire only AFTER it clears."""
    monkeypatch.setattr("src.units.accounts.alpaca_client.time.sleep", lambda *_: None)
    seq = []          # ordered record of (method, path)
    open_reads = {"n": 0}

    def fake_request(method, path, json_body=None):
        seq.append((method, path))
        if method == "GET" and path.startswith("/v2/orders?"):
            # cancel pre-pass (read 0) + settle polls: still present on the
            # first settle poll (read 1), cleared from read 2 on.
            open_reads["n"] += 1
            if open_reads["n"] <= 2:
                return {"retCode": 0, "result": [
                    {"id": "resting-sl", "symbol": "QQQ", "type": "stop"},
                ]}
            return {"retCode": 0, "result": []}
        if method == "GET" and path == "/v2/positions":
            return {"retCode": 0, "result": []}  # confirm loop: flat
        return {"retCode": 0, "result": {"id": "flatten-x"}}

    cli = AlpacaClient(api_key="k", api_secret="s")
    monkeypatch.setattr(cli, "_request", fake_request)
    res = cli.close("QQQ")
    assert res["retCode"] == 0
    order_paths = [p for (m, p) in seq]
    # The position-flatten DELETE fires only after at least one settle-poll GET.
    flatten_idx = order_paths.index("/v2/positions/QQQ")
    settle_polls = [
        i for i, (m, p) in enumerate(seq)
        if m == "GET" and p.startswith("/v2/orders?") and i < flatten_idx
    ]
    # cancel pre-pass GET + >=1 settle-poll GET, all before the flatten.
    assert len(settle_polls) >= 2


def test_client_close_waits_for_qty_available_before_flatten(monkeypatch):
    """BL-20260708-ALPACA-CLOSE-QTY-AVAILABLE (the perpetual QQQ #3269): after
    the cancel, Alpaca drops the order from status=open a beat BEFORE it restores
    the position's `qty_available`, so a DELETE fired on "orders cleared" races
    and fails 'insufficient qty available (available: 0)'. close() must WAIT for
    `qty_available` to actually reach the position size (the real precondition)
    before the flatten — deterministic, not a blind timer. Here qty_available
    starts at 0 and rises to 16; the DELETE must fire only after it does, and
    then succeed on the FIRST attempt."""
    monkeypatch.setattr("src.units.accounts.alpaca_client.time.sleep", lambda *_: None)
    avail_reads = {"n": 0}
    delete_pos = {"n": 0}

    def fake_request(method, path, json_body=None):
        if method == "GET" and path.startswith("/v2/orders?"):
            return {"retCode": 0, "result": [
                {"id": "resting-oco", "symbol": "QQQ", "type": "stop"},
            ]}
        if method == "DELETE" and path.startswith("/v2/orders/"):
            return {"retCode": 0, "result": {"id": "cancelled"}}
        if method == "GET" and path == "/v2/positions/QQQ":
            # Raw single-position read: qty_available released only after a
            # couple of polls (mimics Alpaca's async release lag).
            avail_reads["n"] += 1
            avail = 0 if avail_reads["n"] < 3 else 16
            return {"retCode": 0, "result": {"qty": "16", "qty_available": str(avail)}}
        if method == "DELETE" and path == "/v2/positions/QQQ":
            delete_pos["n"] += 1
            return {"retCode": 0, "result": {"id": "flatten-ok"}}
        if method == "GET" and path == "/v2/positions":
            return {"retCode": 0, "result": []}  # confirm loop: flat
        return {"retCode": 0, "result": []}

    cli = AlpacaClient(api_key="k", api_secret="s")
    monkeypatch.setattr(cli, "_request", fake_request)
    res = cli.close("QQQ")
    assert res["retCode"] == 0
    assert res["result"]["orderId"] == "flatten-ok"
    # The DELETE fired ONCE, only after qty_available reached 16 (>= 3 raw reads:
    # one for want_qty + the poll until release).
    assert delete_pos["n"] == 1
    assert avail_reads["n"] >= 3


def test_client_close_insufficient_qty_gives_up_and_logs_residual(monkeypatch, caplog):
    """If the shares never free within ALPACA_FLATTEN_RETRY_S — and even the
    last-resort cancel_orders=true liquidation can't break the wedge — close()
    returns the broker's error (leaving the DB row open to retry next tick) AND
    logs the residual open orders so the 'won't flatten' failure is
    self-diagnosing."""
    import logging
    monkeypatch.setenv("ALPACA_FLATTEN_RETRY_S", "0.05")
    monkeypatch.setattr("src.units.accounts.alpaca_client.time.sleep", lambda *_: None)

    def fake_request(method, path, json_body=None):
        if method == "GET" and path.startswith("/v2/orders?"):
            return {"retCode": 0, "result": [
                {"id": "resting-oco", "symbol": "QQQ", "type": "stop", "status": "held"},
            ]}
        if method == "DELETE" and path.startswith("/v2/orders/"):
            return {"retCode": 0, "result": {"id": "cancelled"}}
        if method == "GET" and path == "/v2/positions/QQQ":
            # qty_available never recovers — the shares stay held.
            return {"retCode": 0, "result": {"qty": "16", "qty_available": "0"}}
        # Both the plain flatten AND the cancel_orders=true escalation fail —
        # the truly-wedged pending_cancel case that only an operator paper reset
        # can clear (BL-20260716-ALPACA-QQQ-WEDGED-PENDING-CANCEL).
        if method == "DELETE" and path.startswith("/v2/positions/QQQ"):
            return {"retCode": 422, "retMsg": (
                "insufficient qty available for order (requested: 16, available: 0)"
            )}
        return {"retCode": 0, "result": []}

    cli = AlpacaClient(api_key="k", api_secret="s")
    monkeypatch.setattr(cli, "_request", fake_request)
    with caplog.at_level(logging.WARNING, logger="src.units.accounts.alpaca_client"):
        res = cli.close("QQQ")
    assert res["retCode"] != 0
    assert "insufficient qty" in str(res["retMsg"]).lower()
    # The failure surfaced WHAT held the shares (the residual resting order).
    assert any("residual open orders" in r.message for r in caplog.records)
    assert any("resting-oco" in r.getMessage() for r in caplog.records)


def test_client_close_flatten_retry_disabled_is_single_shot(monkeypatch):
    """ALPACA_FLATTEN_RETRY_S <= 0 restores the single-shot flatten DELETE (no
    await, no retry loop — not even the raw qty_available read). The last-resort
    cancel_orders=true escalation still fires once on the give-up path."""
    monkeypatch.setenv("ALPACA_FLATTEN_RETRY_S", "0")
    monkeypatch.setattr("src.units.accounts.alpaca_client.time.sleep", lambda *_: None)
    plain_deletes = {"n": 0}
    forced_deletes = {"n": 0}
    raw_reads = {"n": 0}

    def fake_request(method, path, json_body=None):
        if method == "GET" and path.startswith("/v2/orders?"):
            return {"retCode": 0, "result": []}
        if method == "GET" and path == "/v2/positions/QQQ":
            raw_reads["n"] += 1
            return {"retCode": 0, "result": {"qty": "16", "qty_available": "0"}}
        if method == "DELETE" and path.startswith("/v2/positions/QQQ"):
            if "cancel_orders=true" in path:
                forced_deletes["n"] += 1
            else:
                plain_deletes["n"] += 1
            return {"retCode": 422, "retMsg": "insufficient qty available (available: 0)"}
        return {"retCode": 0, "result": []}

    cli = AlpacaClient(api_key="k", api_secret="s")
    monkeypatch.setattr(cli, "_request", fake_request)
    res = cli.close("QQQ")
    assert res["retCode"] != 0
    assert plain_deletes["n"] == 1   # single shot, no retry loop
    assert forced_deletes["n"] == 1  # one last-resort cancel_orders=true escalation
    assert raw_reads["n"] == 0  # disabled → no qty_available await at all


# --------------------------------------------- close() flatten confirmation
# BL-20260707-ALPACA-CLOSE-NOT-CONFIRMED-FLAT: Alpaca's HTTP 2xx on the
# flatten DELETE means "accepted", not "actually flat" — close() must poll
# positions() and require the symbol to disappear before reporting ok.

def test_client_close_confirms_flat_before_reporting_ok(monkeypatch):
    """The DELETE succeeds and a post-close positions() read shows the
    symbol gone — close() reports ok on the FIRST confirmation poll, no
    sleeping needed."""
    monkeypatch.setattr("src.units.accounts.alpaca_client.time.sleep",
                         lambda *_: pytest.fail("should not need to sleep"))

    def fake_request(method, path, json_body=None):
        if method == "GET" and path == "/v2/orders":
            return {"retCode": 0, "result": []}
        if method == "GET" and path == "/v2/positions":
            return {"retCode": 0, "result": []}  # confirmed flat
        if method == "DELETE" and path == "/v2/positions/QQQ":
            return {"retCode": 0, "result": {"id": "flatten-ok"}}
        return {"retCode": 0, "result": []}

    cli = AlpacaClient(api_key="k", api_secret="s")
    monkeypatch.setattr(cli, "_request", fake_request)
    res = cli.close("QQQ")
    assert res["retCode"] == 0
    assert res["result"]["orderId"] == "flatten-ok"


def test_client_close_not_confirmed_flat_returns_retcode_1(monkeypatch):
    """The regression case: DELETE accepted, but the symbol is STILL open on
    every positions() poll — close() must NOT report success. The old
    accept-is-success behaviour is exactly what let a close accepted right
    after market close fabricate a 'closed' DB row + PnL for a position that
    never actually flattened (the live SLV incident)."""
    monkeypatch.setenv("ALPACA_CLOSE_CONFIRM_S", "0.05")
    monkeypatch.setattr("src.units.accounts.alpaca_client.time.sleep",
                         lambda *_: None)

    def fake_request(method, path, json_body=None):
        if method == "GET" and path == "/v2/orders":
            return {"retCode": 0, "result": []}
        if method == "GET" and path == "/v2/positions":
            # Still open — the flatten never actually filled.
            return {"retCode": 0, "result": [
                {"symbol": "SLV", "side": "short", "qty": "1360",
                 "avg_entry_price": "53.94", "unrealized_pl": "-353.6"},
            ]}
        if method == "DELETE" and path == "/v2/positions/SLV":
            return {"retCode": 0, "result": {"id": "accepted-not-filled"}}
        return {"retCode": 0, "result": []}

    cli = AlpacaClient(api_key="k", api_secret="s")
    monkeypatch.setattr(cli, "_request", fake_request)
    res = cli.close("SLV")
    assert res["retCode"] != 0
    assert "not confirmed flat" in res["retMsg"].lower()


def test_client_close_confirm_read_failure_not_treated_as_flat(monkeypatch):
    """A positions() read failure DURING the confirm poll must not be
    mistaken for 'the position is gone' — mirrors IBClient.close's identical
    rule (a read failure is not confirmation)."""
    monkeypatch.setenv("ALPACA_CLOSE_CONFIRM_S", "0.05")
    monkeypatch.setattr("src.units.accounts.alpaca_client.time.sleep",
                         lambda *_: None)

    def fake_request(method, path, json_body=None):
        if method == "GET" and path == "/v2/orders":
            return {"retCode": 0, "result": []}
        if method == "GET" and path == "/v2/positions":
            return {"retCode": 500, "retMsg": "internal error"}  # read failure
        if method == "DELETE" and path == "/v2/positions/QQQ":
            return {"retCode": 0, "result": {"id": "accepted"}}
        return {"retCode": 0, "result": []}

    cli = AlpacaClient(api_key="k", api_secret="s")
    monkeypatch.setattr(cli, "_request", fake_request)
    res = cli.close("QQQ")
    assert res["retCode"] != 0
    assert "not confirmed flat" in res["retMsg"].lower()


def test_client_close_confirm_disabled_restores_legacy_behavior(monkeypatch):
    """ALPACA_CLOSE_CONFIRM_S <= 0 restores the pre-fix accept-is-success
    behaviour (the documented escape hatch, same shape as IB_CLOSE_CONFIRM_S
    / IB_PROBE_TIMEOUT_S) — no confirmation poll at all."""
    monkeypatch.setenv("ALPACA_CLOSE_CONFIRM_S", "0")
    calls = []

    def fake_request(method, path, json_body=None):
        calls.append((method, path))
        if method == "GET":
            return {"retCode": 0, "result": []}
        return {"retCode": 0, "result": {"id": "accepted-unconfirmed"}}

    cli = AlpacaClient(api_key="k", api_secret="s")
    monkeypatch.setattr(cli, "_request", fake_request)
    res = cli.close("QQQ")
    assert res["retCode"] == 0
    # No confirmation poll AFTER the flatten: the DELETE is the last call, and
    # the confirm-loop list read (GET /v2/positions) never happens. (A
    # pre-DELETE raw single-position read GET /v2/positions/QQQ for the
    # qty_available await is fine — that's the flatten precondition, not the
    # confirm poll.)
    assert calls[-1][0] == "DELETE"
    assert ("GET", "/v2/positions") not in calls


# ------------------------------------------------------------ config gates
def test_accounts_yaml_alpaca_paper_ships_inert():
    acct = yaml.safe_load(open("config/accounts.yaml"))["accounts"]["alpaca_paper"]
    assert acct["exchange"] == "alpaca"
    assert acct["mode"] == "live"  # practice/paper money; flipped 2026-06-11 (set-account-mode)
    # M15 Phase 4 buildout assigned the ETF legs (operator-approved); the
    # ETF-breadth daily sweep (2026-06-20) added iwm/tlt/ief (Tier-3); the
    # intraday ETF pilot (2026-06-20 § 0e) added gld_pullback_1h/slv_trend_1h;
    # intraday ETF rollout 2b (2026-06-20 § 0e) added spy/qqq/tlt 1h pullback +
    # uso 1h long-only trend, completing the intraday ETF sleeve.
    # 2026-06-27 (Tier-3) appended the daily ETF pullback pair slv_pullback_1d +
    # gdx_pullback_1d (same htf_pullback_trend_2h unit as gld_pullback_1d).
    # 2026-06-30 (Tier-3) appended the leveraged Nasdaq-100 ETF trend cells
    # tqqq_trend_long_1d (3x) + qld_trend_long_1d (2x) — paper soak.
    assert acct["strategies"] == [
        "spy_trend_long_1d", "qqq_trend_long_1d", "gld_pullback_1d",
        "iwm_trend_long_1d", "tlt_pullback_1d", "ief_pullback_1d",
        "gld_pullback_1h", "slv_trend_1h",
        "spy_pullback_1h", "qqq_pullback_1h", "tlt_pullback_1h", "uso_trend_1h",
        "slv_pullback_1d", "gdx_pullback_1d",
        "tqqq_trend_long_1d", "qld_trend_long_1d",
        # sub-$100 proxy cells (2026-07-07, Tier-3) — SPLG/IAUM/SCHA paper soak.
        "splg_trend_long_1d", "iaum_pullback_1d", "scha_trend_long_1d",
    ]
    # 2026-06-15: the old `demo: true` category stamp was superseded by
    # account_class (non-Bybit, so demo was only the category marker).
    assert "demo" not in acct
    assert acct["account_class"] == "paper"
    assert acct["symbols"] == ["SPY", "QQQ", "GLD", "IWM", "TLT", "IEF", "SLV", "USO", "GDX", "TQQQ", "QLD", "SPLG", "IAUM", "SCHA"]


# ---------------------------------------- close() wedged-pending-cancel escalation
# BL-20260716-ALPACA-QQQ-WEDGED-PENDING-CANCEL: a resting order stuck in
# `pending_cancel` (Alpaca's own cancel never completes) keeps all the shares
# `held_for_orders`, so the per-order cancels can't free them and every tick's
# plain flatten DELETE returns "insufficient qty available (available: 0)" and
# re-alerts. close() now escalates to Alpaca's atomic cancel-then-liquidate
# (`DELETE /v2/positions/{sym}?cancel_orders=true`) as the last programmatic
# lever, and the post-DELETE confirm still refuses to report a close the broker
# didn't actually make.

def test_client_close_escalates_to_cancel_orders_true_when_wedged(monkeypatch):
    monkeypatch.setenv("ALPACA_CANCEL_SETTLE_S", "0")
    monkeypatch.setenv("ALPACA_FLATTEN_RETRY_S", "0")  # single-shot → give-up path
    monkeypatch.setenv("ALPACA_CLOSE_CONFIRM_S", "0.05")
    monkeypatch.setattr("src.units.accounts.alpaca_client.time.sleep",
                        lambda *_a, **_k: None)

    cli = AlpacaClient(api_key="k", api_secret="s")
    calls = {"plain": 0, "forced": 0}
    # The resting order can't be app-cancelled (already pending_cancel) and
    # stays visible in the open-orders list.
    monkeypatch.setattr(cli, "_cancel_open_orders_for_symbol", lambda *_a: 0)
    monkeypatch.setattr(cli, "_open_orders_for_symbol",
                        lambda *_a: [{"id": "stuck", "status": "pending_cancel"}])
    # After the forced cancel_orders=true liquidation, the position is gone.
    monkeypatch.setattr(cli, "positions", lambda: [])

    def fake_request(method, path, json_body=None):
        if method == "DELETE" and path.startswith("/v2/positions/QQQ"):
            if "cancel_orders=true" in path:
                calls["forced"] += 1
                return {"retCode": 0, "result": {"id": "liq-1"}}
            calls["plain"] += 1
            return {"retCode": 403,
                    "retMsg": "insufficient qty available for order "
                              "(requested: 16, available: 0)"}
        return {"retCode": 0, "result": {}}

    monkeypatch.setattr(cli, "_request", fake_request)

    out = cli.close("QQQ")
    assert out["retCode"] == 0
    assert calls["plain"] >= 1    # the plain flatten was tried and failed
    assert calls["forced"] == 1   # then escalated to cancel_orders=true


def test_client_close_forced_escalation_still_gated_by_flatten_confirm(monkeypatch):
    """If even the cancel_orders=true liquidation doesn't actually flatten, the
    confirm gate returns retCode 1 ('not confirmed flat') so the monitor keeps
    the DB row open — never a fabricated close."""
    monkeypatch.setenv("ALPACA_CANCEL_SETTLE_S", "0")
    monkeypatch.setenv("ALPACA_FLATTEN_RETRY_S", "0")
    monkeypatch.setenv("ALPACA_CLOSE_CONFIRM_S", "0.05")
    monkeypatch.setattr("src.units.accounts.alpaca_client.time.sleep",
                        lambda *_a, **_k: None)

    cli = AlpacaClient(api_key="k", api_secret="s")
    monkeypatch.setattr(cli, "_cancel_open_orders_for_symbol", lambda *_a: 0)
    monkeypatch.setattr(cli, "_open_orders_for_symbol", lambda *_a: [])
    # The position never actually leaves — the forced flatten was accepted but
    # the broker didn't flatten.
    monkeypatch.setattr(cli, "positions",
                        lambda: [{"symbol": "QQQ", "qty": "16"}])

    def fake_request(method, path, json_body=None):
        if method == "DELETE" and path.startswith("/v2/positions/QQQ"):
            if "cancel_orders=true" in path:
                return {"retCode": 0, "result": {"id": "liq-2"}}
            return {"retCode": 403,
                    "retMsg": "insufficient qty available for order "
                              "(requested: 16, available: 0)"}
        return {"retCode": 0, "result": {}}

    monkeypatch.setattr(cli, "_request", fake_request)

    out = cli.close("QQQ")
    assert out["retCode"] == 1
    assert "not confirmed flat" in str(out.get("retMsg") or "")
