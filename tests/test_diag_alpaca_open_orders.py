"""GET /api/diag/alpaca_open_orders + ``account_alpaca_open_orders``.

The Alpaca half of BL-20260818-NO-BRACKET-READ-SURFACE-FOR-BYBIT-OR-ALPACA, and
the last of the three sibling routes. Measured 2026-08-25 via
``scripts/ops/exit_path_coverage.py``: of 15 open trades whose broker-bracket
state could not be observed, **12 were alpaca** and the other 3 an ``ib_paper``
gateway that was not answering. Alpaca was the entire remaining gap.

Four properties are under test, each of which lets a wrong answer through if it
regresses:

1. **The three-state contract** — ``not_alpaca`` / ``could_not_look`` /
   ``orders_read``. A count must stay ``null`` rather than reporting ``0`` for
   an account that was never reached, since ``0`` reads identically to a
   confirmed-empty book.
2. **The two sub-reads fail INDEPENDENTLY.** The orders read answers the
   protection question; the positions read is context. A positions outage must
   leave the orders answer intact while reporting ``positions: null`` — never
   ``[]``, which would claim the account is flat.
3. **An unset price is ``None``, never ``0.0``** — the same rule the Bybit
   sibling follows, for the same reason: a zero compares against a declared
   level as a huge divergence when the truth is that no level is set.
4. **Nested bracket legs are not lost.** ``nested=true`` attaches an
   un-triggered bracket's children to the parent; a flattener that emitted only
   top-level orders would report a bracketed position as unprotected.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.units.accounts import clients as accounts_clients
from src.web.api import main as api_main

_TOKEN = "t" * 64


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DIAG_READ_TOKEN", _TOKEN)
    monkeypatch.setenv("JWT_SIGNING_KEY", "x" * 64)
    monkeypatch.setenv("ALLOWED_EMAIL", "test@example.com")
    monkeypatch.setenv("WEBAPP_PASSWORD_SHA256", "deadbeef")


@pytest.fixture
def client(env):
    return TestClient(api_main.app, raise_server_exceptions=False)


def _bearer(tok=_TOKEN):
    return {"Authorization": f"Bearer {tok}"}


_ACCOUNTS = [
    {"account_id": "alpaca_live", "exchange": "alpaca", "mode": "dry_run",
     "account_class": "real_money"},
    {"account_id": "alpaca_paper", "exchange": "alpaca", "mode": "live",
     "account_class": "paper"},
    {"account_id": "bybit_2", "exchange": "bybit", "mode": "live"},
]


#: Sentinel so a caller can ask for a genuinely-``None`` positions list. Using
#: ``None`` as the default would make "I did not pass positions" and "positions
#: could not be read" the same argument -- the exact collapse under test.
_UNSET = object()


def _clean(orders=None, positions=_UNSET, positions_state="positions_read"):
    return {"orders": orders or [],
            "positions": [] if positions is _UNSET else positions,
            "positions_state": positions_state,
            "position_level_protection_supported": False}


def _patch_route(monkeypatch, by_id, accounts=None):
    import src.units.ui.data_loaders as dl
    monkeypatch.setattr(dl, "list_accounts", lambda: accounts or _ACCOUNTS)

    def _reader(acc):
        val = by_id.get(acc.get("account_id"), KeyError)
        if val is KeyError:
            raise RuntimeError("boom")
        return val

    monkeypatch.setattr(accounts_clients, "account_alpaca_open_orders", _reader)


def _by_id(payload):
    return {r["account_id"]: r for r in payload["accounts"]}


def _get(client, **kw):
    return client.get("/api/diag/alpaca_open_orders", headers=_bearer(), **kw).json()


# --------------------------------------------------------------------------
# The route: the three-state contract
# --------------------------------------------------------------------------
def test_requires_token(client, monkeypatch):
    _patch_route(monkeypatch, {})
    assert client.get("/api/diag/alpaca_open_orders").status_code == 401


def test_non_alpaca_account_is_not_alpaca_not_a_failure(client, monkeypatch):
    """A bybit account is `not_alpaca` -- nothing to read, NOT a failed read."""
    _patch_route(monkeypatch, {"alpaca_live": _clean(), "alpaca_paper": _clean()})
    row = _by_id(_get(client))["bybit_2"]
    assert row["read_state"] == "not_alpaca"
    assert row["result"] is None
    assert row["position_count"] is None and row["order_count"] is None


def test_could_not_look_reports_null_counts_never_zero(client, monkeypatch):
    """The collapse this route exists to prevent: an unreached account must not
    report `0 orders`, which reads identically to a confirmed-empty book."""
    _patch_route(monkeypatch, {"alpaca_live": None, "alpaca_paper": _clean()})
    row = _by_id(_get(client))["alpaca_live"]
    assert row["read_state"] == "could_not_look"
    assert row["order_count"] is None
    assert row["position_count"] is None


def test_a_clean_empty_read_is_zero_not_null(client, monkeypatch):
    """The other side of the same contract: `[]` from a confirmed read IS
    evidence, and must report 0 rather than inheriting the null."""
    _patch_route(monkeypatch, {"alpaca_live": _clean(), "alpaca_paper": _clean()})
    row = _by_id(_get(client))["alpaca_live"]
    assert row["read_state"] == "orders_read"
    assert row["order_count"] == 0 and row["position_count"] == 0


def test_a_raising_reader_is_could_not_look_with_the_error(client, monkeypatch):
    _patch_route(monkeypatch, {"alpaca_live": _clean()})  # alpaca_paper raises
    row = _by_id(_get(client))["alpaca_paper"]
    assert row["read_state"] == "could_not_look"
    assert row["result"] is None
    assert "RuntimeError" in (row["error"] or "")
    assert row["order_count"] is None


def test_account_id_filter_scopes_the_read(client, monkeypatch):
    _patch_route(monkeypatch, {"alpaca_live": _clean(), "alpaca_paper": _clean()})
    payload = client.get("/api/diag/alpaca_open_orders?account_id=alpaca_live",
                         headers=_bearer()).json()
    assert [r["account_id"] for r in payload["accounts"]] == ["alpaca_live"]
    assert payload["requested_account_id"] == "alpaca_live"


# --------------------------------------------------------------------------
# The two sub-reads fail INDEPENDENTLY
# --------------------------------------------------------------------------
def test_a_positions_outage_does_not_null_the_orders_answer(client, monkeypatch):
    """The orders read answers the protection question. A positions outage must
    not discard it -- nor render the account as flat."""
    payload = _clean(orders=[{"symbol": "GLD", "order_type": "stop",
                              "stop_price": 240.0}],
                     positions=None, positions_state="could_not_look")
    _patch_route(monkeypatch, {"alpaca_live": payload, "alpaca_paper": _clean()})
    row = _by_id(_get(client))["alpaca_live"]
    assert row["read_state"] == "orders_read"      # the answer survives
    assert row["order_count"] == 1
    assert row["position_count"] is None           # ...and is NOT reported as 0
    assert row["result"]["positions"] is None
    assert row["result"]["positions_state"] == "could_not_look"


def test_the_payload_states_alpaca_has_no_position_level_protection(client, monkeypatch):
    """Stated in the payload, not left to be inferred from an absence.

    Bybit's Full mode carries the stop on the position row, so a reader who
    assumed symmetry would look for a level that cannot exist here.
    """
    _patch_route(monkeypatch, {"alpaca_live": _clean(), "alpaca_paper": _clean()})
    row = _by_id(_get(client))["alpaca_live"]
    assert row["result"]["position_level_protection_supported"] is False


# --------------------------------------------------------------------------
# The accessor: prices, nesting, and the read that decides `None`
# --------------------------------------------------------------------------
class _FakeAlpaca:
    def __init__(self, orders_env, positions=None, positions_raises=False):
        self._orders_env = orders_env
        self._positions = positions
        self._positions_raises = positions_raises

    def _request(self, method, path, json_body=None):
        assert method == "GET" and path.startswith("/v2/orders")
        # nested=true is what keeps an un-triggered bracket's children visible.
        assert "nested=true" in path and "status=open" in path
        return self._orders_env

    def positions(self):
        if self._positions_raises:
            raise RuntimeError("positions boom")
        return self._positions


def _accessor(monkeypatch, fake):
    monkeypatch.setattr(accounts_clients, "alpaca_client_for", lambda acc: fake)
    return accounts_clients.account_alpaca_open_orders(
        {"account_id": "alpaca_live", "exchange": "alpaca"})


def test_accessor_refuses_a_non_alpaca_account(monkeypatch):
    assert accounts_clients.account_alpaca_open_orders(
        {"account_id": "bybit_2", "exchange": "bybit"}) is None


@pytest.mark.parametrize("raw,expect", [
    ("240.5", 240.5), (240.5, 240.5),
    # NOT prices. A zero would compare against a declared level as a
    # catastrophic divergence when the truth is that no level is set.
    ("0", None), (0, None), (0.0, None), ("", None), ("  ", None),
    (None, None), ("not-a-number", None),
])
def test_an_unset_price_is_none_never_zero(monkeypatch, raw, expect):
    env = {"retCode": 0, "result": [
        {"id": "o1", "symbol": "GLD", "type": "stop", "stop_price": raw}]}
    got = _accessor(monkeypatch, _FakeAlpaca(env, positions=[]))
    assert got["orders"][0]["stop_price"] == expect


def test_nested_bracket_legs_are_emitted_with_their_parent(monkeypatch):
    """A flattener that emitted only top-level orders would report a bracketed
    position as unprotected -- the inverse of the finding, and worse."""
    env = {"retCode": 0, "result": [{
        "id": "parent", "symbol": "SPY", "type": "market", "side": "buy",
        "legs": [{"id": "leg-stop", "symbol": "SPY", "type": "stop",
                  "stop_price": "400"},
                 {"id": "leg-tp", "symbol": "SPY", "type": "limit",
                  "limit_price": "440"}]}]}
    got = _accessor(monkeypatch, _FakeAlpaca(env, positions=[]))
    by_id = {o["order_id"]: o for o in got["orders"]}
    assert set(by_id) == {"parent", "leg-stop", "leg-tp"}
    assert by_id["leg-stop"]["parent_id"] == "parent"
    assert by_id["leg-tp"]["parent_id"] == "parent"
    assert by_id["parent"]["parent_id"] is None
    assert by_id["leg-stop"]["stop_price"] == 400.0
    assert by_id["leg-tp"]["limit_price"] == 440.0


def test_an_orders_read_failure_is_none_for_the_whole_payload(monkeypatch):
    """The orders read IS the protection answer, so its failure is what makes
    the whole thing 'could not look'."""
    assert _accessor(monkeypatch, _FakeAlpaca({"retCode": -1, "retMsg": "nope"},
                                              positions=[])) is None


def test_a_positions_failure_keeps_the_orders_answer(monkeypatch):
    """...and the positions read is only context, so its failure must not."""
    env = {"retCode": 0, "result": [
        {"id": "o1", "symbol": "GLD", "type": "stop", "stop_price": "240"}]}
    got = _accessor(monkeypatch, _FakeAlpaca(env, positions_raises=True))
    assert got is not None
    assert len(got["orders"]) == 1
    assert got["positions"] is None                     # never []
    assert got["positions_state"] == "could_not_look"


def test_positions_none_from_the_client_is_also_could_not_look(monkeypatch):
    """`AlpacaClient.positions()` already returns None on a read failure rather
    than []. That distinction is preserved, not re-derived."""
    env = {"retCode": 0, "result": []}
    got = _accessor(monkeypatch, _FakeAlpaca(env, positions=None))
    assert got["positions"] is None
    assert got["positions_state"] == "could_not_look"


def test_a_genuinely_flat_account_reads_empty_not_null(monkeypatch):
    """The control: proving the probe above can distinguish its two states."""
    got = _accessor(monkeypatch, _FakeAlpaca({"retCode": 0, "result": []},
                                             positions=[]))
    assert got["orders"] == [] and got["positions"] == []
    assert got["positions_state"] == "positions_read"
