"""MI-168 — a close confirmation must be scoped to the CLOSE, not the SYMBOL.

Regression cover for the defect MI-167 measured on ``ib_paper``/MGC on
2026-09-07: ``IBClient.close`` confirmed a close by requiring the whole
**symbol** to reach flat, while the close was of a single **trade**. Whenever
the account legitimately held other lots of that symbol the gate could never be
satisfied however perfectly the close executed, and the failure branch
deliberately left the journal row open.

The measured instance, quoted from the systemd journal in
``docs/claude/diagnoses/MI-167-pipeline-integrity-pass-2.md``:

* 11:45:02Z ``placeOrder MarketOrder(orderId=547, SELL, totalQuantity=43.0)``
* 11:45:06Z ``orderStatus 547: status='Filled', filled=43.0, remaining=0.0,
  avgFillPrice=4392.9``  — the close SUCCEEDED at the venue
* 11:45:09Z ``exchange close failed — leaving DB open … error=close not
  confirmed flat: live_qty=11.0`` — because sibling trade 5353 held 11 lots

Cost: a realised −$17,024.28 absent from the book, a phantom 43-lot open row,
and 43 re-armed sell lots resting over an 11-lot position.

⚠️ ``test_ib_close_of_one_trade_is_confirmed_while_a_sibling_holds_lots``
**FAILS on the pre-MI-168 client** — that is the point of it. A test that
passed before and after would prove nothing. Proof of the pre-fix failure is
recorded in ``docs/design/close-confirmation-scope-DESIGN.md`` § "Proof the
regression test fails on today's code".

The Alpaca half of this file is a **correction** to MI-167's proposed fix, not
an application of it — see the docstrings there and the design memo
§ "Alpaca is a different defect".
"""
from __future__ import annotations

from src.units.accounts.alpaca_client import AlpacaClient
from tests.test_p3_close_wiring import (  # noqa: F401  (fake_ib_module fixture)
    FakeIB,
    _FakePortfolioItem,
    _FakeTrade,
    _ib_client_with,
    fake_ib_module,
)


# ---------------------------------------------------------------------------
# A FakeIB that REDUCES rather than flattens — the shape the real venue had
# ---------------------------------------------------------------------------


class ReducingFakeIB(FakeIB):
    """A venue that fills our close order and leaves the SIBLING lots behind.

    The stock ``FakeIB`` drops the whole portfolio item on ``placeOrder``,
    which models an account holding nothing else — precisely the one case in
    which symbol-flatness and trade-closure coincide, and therefore the one
    case that cannot exercise this defect.

    Here the position goes 54 → 11 and the close order reports ``Filled
    43.0``, exactly as IB reported for order 547.
    """

    def __init__(self, *, symbol, qty_before, close_fills, **kw):
        super().__init__(
            portfolio_items=[
                _FakePortfolioItem(symbol, qty_before, account="DUQ1")
            ],
            **kw,
        )
        self._symbol = str(symbol).upper()
        self._qty_before = float(qty_before)
        self._close_fills = float(close_fills)

    def placeOrder(self, contract, order):  # ib_insync casing
        self.placed.append((contract, order))
        remaining = self._qty_before - self._close_fills
        self._portfolio = [
            _FakePortfolioItem(self._symbol, remaining, account="DUQ1")
        ]
        trade = _FakeTrade(order, contract, status="Filled")
        trade.orderStatus.filled = self._close_fills
        trade.orderStatus.remaining = 0.0
        trade.orderStatus.avgFillPrice = 4392.9
        return trade


# ---------------------------------------------------------------------------
# THE REGRESSION — this is the test that fails on the pre-MI-168 client
# ---------------------------------------------------------------------------


def test_ib_close_of_one_trade_is_confirmed_while_a_sibling_holds_lots(
    monkeypatch,
):
    """THE MI-167 INCIDENT, reproduced.

    Two open journal rows on one symbol: trade 5531 holds 43 MGC lots and
    sibling trade 5353 holds 11, so the account is long 54. We close 5531's 43.
    The venue fills all 43 (``orderStatus.filled = 43.0``) and the symbol
    settles at 11 — the sibling's real, still-open position.

    The close SUCCEEDED and must be reported as such.

    PRE-MI-168 this returns ``retCode 1`` with ``close not confirmed flat:
    live_qty=11.0``, because the gate demanded ``_live_position_qty("MGC") <=
    0`` — unsatisfiable while the sibling exists. The row then stays open, the
    −$17,024.28 never reaches the book, and the protection layer re-arms 43
    lots over an 11-lot position.
    """
    monkeypatch.setenv("IB_CLOSE_CONFIRM_S", "0.5")
    fake_ib = ReducingFakeIB(symbol="MGC", qty_before=54, close_fills=43)
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.close("MGC", "long", 43)

    assert res["retCode"] == 0, (
        "the close filled 43 of 43 at the venue and must be confirmed; a "
        "sibling trade's lots are not evidence that OUR close failed — "
        f"got {res!r}"
    )
    assert res.get("confirm_state", "").startswith("confirmed"), res
    # The order really was transmitted, sized to THIS trade's lots — not to
    # the symbol's 54.
    assert len(fake_ib.placed) == 1
    _contract, order = fake_ib.placed[0]
    assert float(order.totalQuantity) == 43.0


def test_ib_close_confirms_on_the_orders_own_fill_when_the_position_read_lags(
    monkeypatch,
):
    """The order's own fill is sufficient, and is preferred over a position
    delta.

    A position read that has not yet caught up (or that a sibling's concurrent
    fill has muddied) must not veto evidence the venue already gave us about
    OUR order. Here the portfolio still reports the pre-close 54 while
    ``orderStatus.filled`` reads 43.0.
    """
    monkeypatch.setenv("IB_CLOSE_CONFIRM_S", "0.2")
    # close_fills=43 for the ORDER, but the portfolio never moves off 54.
    fake_ib = ReducingFakeIB(symbol="MGC", qty_before=54, close_fills=0)
    client = _ib_client_with(fake_ib, symbol="MGC")

    real_place = fake_ib.placeOrder

    def place_with_fill(contract, order):
        trade = real_place(contract, order)
        trade.orderStatus.filled = 43.0  # venue confirms OUR order
        return trade

    fake_ib.placeOrder = place_with_fill  # type: ignore[assignment]

    res = client.close("MGC", "long", 43)

    assert res["retCode"] == 0, res
    assert res["confirm_state"] == "confirmed_order_filled", res


# ---------------------------------------------------------------------------
# The refusal discipline must survive the re-scoping (BL-20260707)
# ---------------------------------------------------------------------------


def test_ib_close_still_refuses_when_nothing_moved(monkeypatch):
    """BL-20260707 stays dead: an accepted-but-UNFILLED close is still a
    failure. The position did not move and our order filled nothing, so there
    is no trade-scoped evidence of anything — ``retCode 1``, row left open."""
    monkeypatch.setenv("IB_CLOSE_CONFIRM_S", "0.2")
    fake_ib = ReducingFakeIB(symbol="MGC", qty_before=54, close_fills=0)
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.close("MGC", "long", 43)

    assert res["retCode"] == 1, res
    assert res["confirm_state"] == "not_reduced", res
    # LOAD-BEARING: order_monitor string-matches this to arm the retry
    # cooldown (order_monitor.py:1012), and MI-167 named it as the greppable
    # incidence marker.
    assert "not confirmed flat" in res["retMsg"]


def test_ib_close_unreadable_position_is_a_refusal_and_is_named(monkeypatch):
    """"We could not look" is still a refusal — and is now DISTINGUISHABLE
    from "we looked and the lots are still there".

    Both return ``retCode 1`` (MI-167's rule 3: the change narrows what counts
    as success, it must not widen what counts as unknown), but they are
    different facts with different remedies, and before MI-168 they returned
    the same envelope and were indistinguishable afterwards.
    """
    monkeypatch.setenv("IB_CLOSE_CONFIRM_S", "0.2")
    fake_ib = ReducingFakeIB(symbol="MGC", qty_before=54, close_fills=0)
    client = _ib_client_with(fake_ib, symbol="MGC")

    # The Step-0 clamp read succeeds; every read DURING the confirm poll fails.
    calls = {"n": 0}
    real_positions = client.positions

    def flaky_positions(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_positions(*a, **kw)
        raise RuntimeError("gateway read failed")

    client.positions = flaky_positions  # type: ignore[assignment]

    res = client.close("MGC", "long", 43)

    assert res["retCode"] == 1, res
    assert res["confirm_state"] == "unreadable", res
    assert "unreadable" in res["retMsg"]
    assert "not confirmed flat" in res["retMsg"]


# ---------------------------------------------------------------------------
# The PARTIAL-REDUCTION question — an operator risk decision, both behaviours
# ---------------------------------------------------------------------------


def test_ib_close_partial_fill_does_not_confirm_under_the_strict_default(
    monkeypatch,
):
    """DEFAULT (``strict``): 40 of 43 filled does NOT confirm.

    The row stays open and the close is retried. This errs toward a phantom
    OPEN row — recoverable, and the direction the reconcilers already handle.
    """
    monkeypatch.delenv("IB_CLOSE_CONFIRM_PARTIAL", raising=False)
    monkeypatch.setenv("IB_CLOSE_CONFIRM_S", "0.2")
    fake_ib = ReducingFakeIB(symbol="MGC", qty_before=54, close_fills=40)
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.close("MGC", "long", 43)

    assert res["retCode"] == 1, res
    assert res["confirm_state"] == "not_reduced", res


def test_ib_close_partial_fill_confirms_under_reduction_mode(monkeypatch):
    """THE OTHER BEHAVIOUR (``reduction``): any reduction confirms.

    Errs toward journalling trade 5531 fully closed while 3 lots are still
    held — a FABRICATED PnL on the unclosed remainder, which is the
    false-SUCCESS class BL-20260707 exists to kill. Pinned so the operator's
    choice is exercised, NOT because it is recommended.
    """
    monkeypatch.setenv("IB_CLOSE_CONFIRM_PARTIAL", "reduction")
    monkeypatch.setenv("IB_CLOSE_CONFIRM_S", "0.2")
    fake_ib = ReducingFakeIB(symbol="MGC", qty_before=54, close_fills=40)
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.close("MGC", "long", 43)

    assert res["retCode"] == 0, res
    assert res["confirm_state"] == "confirmed_order_filled", res


def test_ib_close_partial_mode_typo_falls_back_to_strict(monkeypatch):
    """An unparseable value falls back to the SAFE default, never the
    permissive one — a typo must not silently arm partial confirmation."""
    monkeypatch.setenv("IB_CLOSE_CONFIRM_PARTIAL", "reduciton")  # typo
    monkeypatch.setenv("IB_CLOSE_CONFIRM_S", "0.2")
    fake_ib = ReducingFakeIB(symbol="MGC", qty_before=54, close_fills=40)
    client = _ib_client_with(fake_ib, symbol="MGC")

    assert client.close("MGC", "long", 43)["retCode"] == 1


# ---------------------------------------------------------------------------
# The sanctioned rollback
# ---------------------------------------------------------------------------


def test_ib_close_confirm_scope_symbol_restores_the_pre_mi168_behaviour(
    monkeypatch,
):
    """``IB_CLOSE_CONFIRM_SCOPE=symbol`` reproduces the old flatness test
    byte-for-byte — one env flip + restart, no redeploy.

    This test asserts the DEFECT, deliberately: under the rollback the
    perfectly-executed close is refused again, which is what a rollback of
    this change means.
    """
    monkeypatch.setenv("IB_CLOSE_CONFIRM_SCOPE", "symbol")
    monkeypatch.setenv("IB_CLOSE_CONFIRM_S", "0.2")
    fake_ib = ReducingFakeIB(symbol="MGC", qty_before=54, close_fills=43)
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.close("MGC", "long", 43)

    assert res["retCode"] == 1, res
    assert res["confirm_state"] == "not_flat", res


def test_ib_close_flatness_still_confirms_when_nothing_else_is_held(
    monkeypatch,
):
    """The one case where symbol-flatness and trade-closure coincide is
    untouched: nothing that used to pass stops passing."""
    monkeypatch.setenv("IB_CLOSE_CONFIRM_S", "0.2")
    fake_ib = FakeIB(portfolio_items=[_FakePortfolioItem("MGC", 3, "DUQ1")])
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.close("MGC", "long", 3)

    assert res["retCode"] == 0, res
    assert res["confirm_state"] in ("confirmed_flat", "confirmed_order_filled")


# ---------------------------------------------------------------------------
# ALPACA — a CORRECTION to MI-167's proposed fix, not an application of it
# ---------------------------------------------------------------------------
#
# MI-167 § "Proposed fix" item 2 asks for "the identical change" at
# alpaca_client.py:842 and :1049, on the grounds that both are the same
# symbol-scoped test in two spellings. The TEST is indeed the same. The
# OPERATION it gates is not, and that is what decides whether the scope is
# wrong:
#
# ⚠️ THE THREE BULLETS BELOW WERE TRUE WHEN THIS FILE WAS WRITTEN AND ARE NOW
# HISTORY, NOT DESCRIPTION — #11337 repaired all three. They are kept because
# the REASONING that follows them is still correct and still binding, and it is
# only readable against the state it was reasoning about:
#
#   * ``AlpacaClient.close(symbol)`` took NO qty. It issued
#     ``DELETE /v2/positions/{sym}`` — Alpaca's whole-symbol liquidation.
#   * ``_close_extended_hours(symbol)`` read ``qty`` from the LIVE POSITION
#     (``pos.get("qty")``), i.e. the whole symbol, and placed a limit for it.
#   * ``execute.close_open_position`` received a per-trade ``qty`` and, on the
#     alpaca branch, DISCARDED it.
#
# So on Alpaca the operation is symbol-scoped and the confirmation is
# symbol-scoped: they MATCH, symbol-flatness is reachable, and the
# false-refusal cannot occur. The two tests below establish that by
# measurement rather than by reading.
#
# ⚠️ APPLYING MI-167's CHANGE HERE WOULD INSTALL A FALSE-SUCCESS ON A
# REAL-MONEY-CAPABLE PATH: confirming a trade-sized reduction while the venue
# in fact liquidated the whole symbol would journal one trade closed and leave
# its sibling's row open with no position behind it — the BL-20260707 class,
# re-introduced. The Alpaca confirmation was therefore left UNCHANGED here.
#
# ⚠️ AND THAT REASONING STILL HOLDS AFTER #11337 — read the ORDER, because it
# is the whole safety property. #11337 did NOT re-scope this confirmation. It
# narrowed the OPERATION, and only then added a reduction-aware gate to the NEW
# partial path, leaving the pre-existing whole-symbol path's strict flatness
# check exactly as it is. The hazard above requires the confirmation to be
# loosened AHEAD of the operation; loosening it for a path whose operation was
# narrowed in the same commit is a different act. The operator was asked this
# as its own Tier-3 question on 2026-09-08 and ACCEPTED the ordering argument,
# with the limit recorded: a future change that loosens an Alpaca confirmation
# WITHOUT narrowing its operation in the same commit is NOT covered by it.


def _regular_hours(monkeypatch):
    monkeypatch.setattr(
        "src.runtime.market_hours.us_equity_session", lambda *a, **k: "regular"
    )
    monkeypatch.setattr(
        "src.units.accounts.alpaca_client.time.sleep", lambda *_: None
    )


def test_alpaca_close_is_not_exposed_to_the_false_refusal(monkeypatch):
    """Two open journal rows on one Alpaca symbol; the close CONFIRMS.

    The precondition that breaks IB — a sibling holding lots of the same
    symbol — does not break Alpaca, because the DELETE removes the whole
    symbol and so symbol-flatness is reachable. This is the positive control
    behind the claim that ``alpaca_live`` is not exposed to THIS defect.
    """
    _regular_hours(monkeypatch)
    monkeypatch.setenv("ALPACA_CLOSE_CONFIRM_S", "0.2")
    state = {"flat": False}

    def fake_request(method, path, json_body=None):
        if method == "GET" and path.startswith("/v2/orders"):
            return {"retCode": 0, "result": []}
        if method == "GET" and path == "/v2/positions":
            if state["flat"]:
                return {"retCode": 0, "result": []}
            # 54 shares: 43 from trade A, 11 from sibling trade B.
            return {"retCode": 0, "result": [
                {"symbol": "TLT", "side": "long", "qty": "54"},
            ]}
        if method == "GET" and path == "/v2/positions/TLT":
            if state["flat"]:
                return {"retCode": 404, "retMsg": "position does not exist"}
            return {"retCode": 0, "result": {"symbol": "TLT", "qty": "54",
                                             "qty_available": "54"}}
        if method == "DELETE" and path.startswith("/v2/positions/TLT"):
            state["flat"] = True  # the venue liquidates the WHOLE symbol
            return {"retCode": 0, "result": {"id": "flatten-ok"}}
        return {"retCode": 0, "result": []}

    cli = AlpacaClient(api_key="k", api_secret="s")
    monkeypatch.setattr(cli, "_request", fake_request)

    res = cli.close("TLT")

    assert res["retCode"] == 0, (
        "Alpaca's close is symbol-scoped in its OPERATION, so a sibling's "
        f"lots cannot make its symbol-scoped confirmation unsatisfiable: {res!r}"
    )


def test_alpaca_close_of_one_trade_liquidates_its_sibling_too(monkeypatch):
    """The defect Alpaca DOES have, and it is not the one MI-167 proposed to
    fix here.

    ``close_open_position`` is called per journal trade with that trade's qty.
    The alpaca branch discards the qty and flattens the symbol, so closing
    trade A silently liquidates sibling trade B's shares while B's journal row
    stays open with nothing behind it — an OVER-CLOSE, the mirror image of
    IB's false refusal.

    Pinned as the current, measured behaviour so the coupling to MI-140 rests
    on evidence: this, not the false refusal, is what arms on ``alpaca_live``
    when it can place orders again. Remedying it is a separate Tier-3
    decision — see the design memo § "Alpaca is a different defect".
    """
    _regular_hours(monkeypatch)
    monkeypatch.setenv("ALPACA_CLOSE_CONFIRM_S", "0.2")
    deleted: list = []
    state = {"flat": False}

    def fake_request(method, path, json_body=None):
        if method == "GET" and path.startswith("/v2/orders"):
            return {"retCode": 0, "result": []}
        if method == "GET" and path == "/v2/positions":
            return {"retCode": 0, "result": [] if state["flat"] else [
                {"symbol": "TLT", "side": "long", "qty": "54"}]}
        if method == "GET" and path == "/v2/positions/TLT":
            if state["flat"]:
                return {"retCode": 404, "retMsg": "gone"}
            return {"retCode": 0, "result": {"symbol": "TLT", "qty": "54",
                                             "qty_available": "54"}}
        if method == "DELETE" and path.startswith("/v2/positions/TLT"):
            deleted.append(path)
            state["flat"] = True
            return {"retCode": 0, "result": {"id": "flatten-ok"}}
        return {"retCode": 0, "result": []}

    cli = AlpacaClient(api_key="k", api_secret="s")
    monkeypatch.setattr(cli, "_request", fake_request)

    # ⚠️ THIS CALL PASSES NO QTY, AND THAT IS NOW THE POINT OF IT.
    # The sentence here used to read "the client's signature cannot express
    # that — it takes only a symbol", which #11337 made false. What the call
    # still demonstrates, and what keeps this test worth running, is the
    # LEGACY UNSCOPED PATH: a caller that names no quantity gets exactly the
    # whole-symbol flatten it always got. The over-close this test is named
    # for is repaired at `execute.close_open_position`, which now forwards the
    # per-trade qty — see tests/test_alpaca_trade_scoped_close.py.
    cli.close("TLT")

    assert deleted, "the flatten was issued"
    assert all("qty" not in p for p in deleted), (
        "the whole-symbol DELETE carries no quantity: all 54 shares go, "
        "including the 11 belonging to the sibling journal row"
    )


def test_alpaca_close_signature_can_now_express_a_trade_scoped_close():
    """⚠️ INVERTED 2026-09-08 — this test asserted the OPPOSITE until #11337.

    It was named ``test_alpaca_close_signature_cannot_express_a_trade_scoped_close``
    and asserted ``params == ["self", "symbol"]``. It was a CHARACTERISATION
    pin on a defect, not a property worth preserving, and the defect has since
    been repaired: ``AlpacaClient.close`` now takes the named trade's ``qty``
    and issues ``DELETE /v2/positions/{sym}?qty=N`` — Alpaca's documented
    partial liquidation — when that quantity is smaller than the live position.

    The OLD NAME IS RECORDED HERE ON PURPOSE: `BL-20260907-ALPACA-CLOSE-OF-ONE-
    TRADE-LIQUIDATES-ITS-SIBLINGS` cites it by name in its
    ``resolution_criteria``, and that row's RESOLVES-WHEN is what #11337
    satisfies. Renaming without leaving the old name findable would break the
    only link between the row and its evidence. Renaming was still necessary:
    a test whose NAME asserts the opposite of what it checks is the
    unprovenanced-diagnostic class this file exists to argue against.

    ⚠️ **#11279's IB-ONLY SCOPING IS STILL CORRECT, AND FOR ITS OWN REASON —
    which this inversion does NOT weaken.** #11279 declined to extend its
    confirmation change to Alpaca because doing so would confirm a trade-sized
    reduction against a venue that had liquidated the whole symbol: the
    BL-20260707 false-SUCCESS, on a real-money-capable path. That hazard is
    about ORDER, not about whether `qty` exists. It requires the CONFIRMATION
    to be loosened while the OPERATION is still whole-symbol, and #11279 could
    only have done it that way round, because on its branch the operation
    could not be scoped at all — which is precisely what the old assertion
    recorded. #11337 narrowed the operation FIRST and left the whole-symbol
    path's strict flatness gate untouched, so it is not the move #11279
    refused. The operator was asked this as its own Tier-3 question on
    2026-09-08 and accepted the ordering, with the limit recorded: loosening
    an Alpaca confirmation WITHOUT narrowing its operation in the same commit
    is still forbidden and is not covered by that approval.
    """
    import inspect

    params = list(inspect.signature(AlpacaClient.close).parameters)
    assert params == ["self", "symbol", "qty"], params
    # …and it is OPTIONAL, so every pre-existing caller keeps the whole-symbol
    # behaviour it had. That is what the two controls below rest on.
    assert inspect.signature(AlpacaClient.close).parameters["qty"].default is None
