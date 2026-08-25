"""`protection_coverage` must publish WHO submitted each OCA group.

BL-20260825-OVER-COVER-PAGE-CANNOT-SAY-WHY-THE-GROUPS-ARE-DISJOINT.

Quantity, side and price answer "is protection resting, on the right side,
where we declared?". None answers "why are there TWO groups?" — which is the
question the disjoint-group over-cover page exists to make actionable. IB binds
cancel rights to the SUBMITTING clientId (`cancelOrder` "can only be used to
cancel an order that was placed originally by a client with the same client
ID"), so without this field the page's own remediation line names a leg the API
will refuse to cancel.

Measured live on ib_paper MHG 2026-08-25 (`/api/diag/ib_open_orders`): a 29-lot
long against `oca-protect-465` (STP 6.312, clientId 497 — matching
`trades.stop_loss` 6.31207143) and `oca-protect-446` (STP 6.2625, the PREVIOUS
trail level, clientId 597). The stale group is the trailing amend's own
cancel-and-re-place with the cancel half refused (Error 10147), so groups
accrete one per (clientId rotation, trailing amend) pair.

`ib_insync` is not installed in every environment, so the pure helper is exec'd
from source (the pattern `tests/test_ib_protection_two_sided.py` established)
and the aggregate is checked by reading the method body.
"""
from __future__ import annotations

import ast
import types

import pytest

_SRC = open("src/units/accounts/ib_client.py").read()


def _load(name: str):
    tree = ast.parse(_SRC)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            mod = types.ModuleType("h")
            mod.__dict__["Optional"] = __import__("typing").Optional
            mod.__dict__["Any"] = __import__("typing").Any
            exec(compile(ast.Module(body=[node], type_ignores=[]), "h", "exec"),
                 mod.__dict__)
            return mod.__dict__[name]
    raise AssertionError(f"{name} not found in ib_client.py")


read_id = _load("_readable_client_id")


class _Order:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class TestTheHelperKeepsUnreadableApartFromZero:
    def test_it_reads_the_live_ids(self):
        assert read_id(_Order(clientId=497)) == 497
        assert read_id(_Order(clientId=597)) == 597

    def test_an_absent_id_is_None_not_zero(self):
        """`0` is a real IB client. Defaulting to it would name a session that
        did not place the order — and client 0 is TWS's own manual session, so
        the page would send the operator to exactly the wrong place."""
        assert read_id(_Order()) is None
        assert read_id(_Order(clientId=None)) is None

    def test_a_genuine_zero_survives(self):
        """The converse guard: a real `clientId=0` must not be swallowed by a
        falsy check into 'unreadable'."""
        assert read_id(_Order(clientId=0)) == 0

    def test_an_unparseable_id_is_None_rather_than_raising(self):
        """This runs inside a safety sweep; one malformed order must not break
        the read for the account."""
        assert read_id(_Order(clientId="not-a-number")) is None
        assert read_id(_Order(clientId=object())) is None

    def test_a_numeric_string_is_accepted(self):
        assert read_id(_Order(clientId="497")) == 497


def _coverage_body() -> str:
    src = _SRC[_SRC.index("def _locked_protection_coverage"):]
    return src[:src.index("\n    def ", 10)]


class TestTheAggregatePublishesIt:
    def test_both_new_keys_are_returned(self):
        body = _coverage_body()
        assert '"oca_group_client_ids"' in body
        assert '"reader_client_id"' in body

    def test_it_reads_through_the_shared_helper(self):
        """A second inline `getattr(order, "clientId")` would be free to drift
        from the helper's None-vs-zero discipline."""
        assert "_readable_client_id(" in _coverage_body()

    def test_the_flat_early_return_carries_the_same_keys(self):
        """A consumer branching on these keys must not hit a KeyError only in
        the flat case — the shape of a return must not depend on the answer."""
        body = _coverage_body()
        flat = body[body.index('"source": "flat"') - 400:body.index('"source": "flat"')]
        assert '"oca_group_client_ids"' in flat
        assert '"reader_client_id"' in flat

    def test_the_reader_id_is_the_EFFECTIVE_one(self):
        """A cancel goes out on the effective (rotation-aware) id, so grading
        against the base id would misreport ownership after any rotation —
        which is precisely the condition being diagnosed."""
        assert '"reader_client_id": self._effective_client_id()' in _coverage_body()


class TestTheConsumerIsWired:
    def test_the_sweep_forwards_both_fields_to_the_page(self):
        """A field that is written and never read is the shape
        `provenance-consumer-guard` exists to catch."""
        mon = open("src/runtime/order_monitor.py").read()
        start = mon.index("_emit_stop_over_cover_alert(\n                        account")
        # A fixed window, not `index(")")` — the first paren inside the call
        # belongs to `cov.get(...)`, so slicing on it truncates the very lines
        # under test and the assertion would fail for the wrong reason.
        call = mon[start:start + 1200]
        assert 'cov.get("oca_group_client_ids")' in call
        assert 'cov.get("reader_client_id")' in call

    def test_it_uses_get_not_subscript(self):
        """A coverage dict from a client predating these keys must degrade to an
        'unknown' owner, never raise inside a money-at-risk page."""
        mon = open("src/runtime/order_monitor.py").read()
        assert 'cov["oca_group_client_ids"]' not in mon
        assert 'cov["reader_client_id"]' not in mon
