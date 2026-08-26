"""Tests for scripts/ops/cancel_ib_order.py — the per-order IB cancel wire.

All broker I/O is monkeypatched: these verify the decision logic (three-state
lookup, protective refusal, trader-clientId refusal, dry-run vs apply,
post-cancel verification) without touching IB.

The load-bearing cases are the ones that keep "we could not look" apart from
"we looked and it is gone" — a gateway outage reading as a successful cancel is
the failure this tool exists to prevent, not a cosmetic nicety.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "cancel_ib_order",
    Path(__file__).resolve().parents[1] / "scripts" / "ops" / "cancel_ib_order.py",
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)  # type: ignore

_IB_ACCT = {"account_id": "ib_paper", "exchange": "interactive_brokers",
            "ib_host": "10.0.0.251", "ib_port": 4002, "ib_client_id": 497}

# The real stranded order: MKT SELL 105 MGC, tif DAY, no OCA, placed by an
# exited ops client in the 9900 band.
_ORDER_6 = {"order_id": 6.0, "perm_id": 111.0, "client_id": 9942.0,
            "symbol": "MGC", "order_type": "MKT", "action": "SELL",
            "total_quantity": 105.0, "oca_group": None, "tif": "DAY",
            "status": "PreSubmitted", "filled": 0.0}
# A protective leg on the same symbol — must never be cancelled by default.
_STOP = {"order_id": 359.0, "perm_id": 222.0, "client_id": 9942.0,
         "symbol": "MGC", "order_type": "STP", "action": "SELL",
         "total_quantity": 105.0, "oca_group": "oca-protect-t4487", "tif": "GTC",
         "status": "Submitted", "filled": 0.0}
# An order owned by the trader's own execution client.
_TRADER_ORDER = {"order_id": 42.0, "perm_id": 333.0, "client_id": 497.0,
                 "symbol": "MES", "order_type": "MKT", "action": "SELL",
                 "total_quantity": 15.0, "oca_group": None, "tif": "DAY",
                 "status": "PreSubmitted", "filled": 0.0}


@pytest.fixture(autouse=True)
def _patch_account(monkeypatch):
    monkeypatch.setattr(mod, "_load_account",
                        lambda aid: _IB_ACCT if aid == "ib_paper" else None)


def _run(monkeypatch, reads, argv, cancel=None):
    """reads: list of successive _read_orders returns (None == could not look)."""
    seq = {"i": 0}

    def _read(cfg):
        i = min(seq["i"], len(reads) - 1)
        seq["i"] += 1
        return reads[i]

    monkeypatch.setattr(mod, "_read_orders", _read)
    monkeypatch.setattr(mod, "_cancel_as_owner",
                        lambda cfg, **kw: cancel or {"retCode": 0, "retMsg": "OK"})
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main(argv)
    return rc, json.loads(buf.getvalue())


def test_requires_an_id():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main(["--account", "ib_paper"])
    assert rc == 2
    assert json.loads(buf.getvalue())["lookup_state"] == "not_requested"


def test_unreadable_is_could_not_look_not_absent(monkeypatch):
    """A failed read must NEVER present as 'no such order'.

    This is the exact condition observed live at 12:37:33Z (read_state
    could_not_look while the breaker was open). If it collapsed to not_found a
    caller would conclude the order had already gone.
    """
    rc, out = _run(monkeypatch, [None], ["--order-id", "6", "--apply"])
    assert rc == 3
    assert out["lookup_state"] == "could_not_look"
    assert "NOT evidence" in out["error"]
    assert "action" not in out  # nothing was attempted


def test_clean_read_with_no_match_is_not_found(monkeypatch):
    rc, out = _run(monkeypatch, [[_STOP]], ["--order-id", "6"])
    assert rc == 0
    assert out["lookup_state"] == "not_found"


def test_protective_order_refused_by_default(monkeypatch):
    rc, out = _run(monkeypatch, [[_STOP]], ["--order-id", "359", "--apply"])
    assert rc == 4
    assert out["action"] == "refused"
    assert any("PROTECTIVE" in b for b in out["blockers"])


def test_protective_order_allowed_with_explicit_force(monkeypatch):
    rc, out = _run(monkeypatch, [[_STOP], []],
                   ["--order-id", "359", "--apply", "--force-protective"])
    assert rc == 0
    assert out["action"] == "cancelled"


def test_trader_owned_order_refused(monkeypatch):
    """Taking over clientId 497 would evict the trader's live IB session."""
    rc, out = _run(monkeypatch, [[_TRADER_ORDER]], ["--order-id", "42", "--apply"])
    assert rc == 4
    assert any("execution band" in b for b in out["blockers"])


def test_dry_run_is_the_default(monkeypatch):
    rc, out = _run(monkeypatch, [[_ORDER_6]], ["--order-id", "6"])
    assert rc == 0
    assert out["action"] == "dry_run"
    assert out["owner_client_id"] == 9942


def test_apply_cancels_and_verifies_gone(monkeypatch):
    rc, out = _run(monkeypatch, [[_ORDER_6], []], ["--order-id", "6", "--apply"])
    assert rc == 0
    assert out["action"] == "cancelled"
    assert out["verify_state"] == "gone"


def test_apply_reports_when_the_order_survives(monkeypatch):
    """An accepted cancel that did not take must not report success."""
    rc, out = _run(monkeypatch, [[_ORDER_6], [_ORDER_6]], ["--order-id", "6", "--apply"])
    assert rc == 1
    assert out["action"] == "cancel_not_effective"
    assert out["verify_state"] == "still_present"


def test_unverifiable_cancel_is_declared_unconfirmed(monkeypatch):
    """Cancel accepted, verification read failed — 'accepted' != 'confirmed'."""
    rc, out = _run(monkeypatch, [[_ORDER_6], None], ["--order-id", "6", "--apply"])
    assert rc == 3
    assert out["action"] == "cancelled_unconfirmed"
    assert out["verify_state"] == "could_not_look"


def test_ambiguous_id_refuses_rather_than_guessing(monkeypatch):
    """orderId is unique only per clientId, so a collision is real."""
    twin = dict(_ORDER_6, client_id=9901.0, perm_id=999.0)
    rc, out = _run(monkeypatch, [[_ORDER_6, twin]], ["--order-id", "6", "--apply"])
    assert rc == 2
    assert out["lookup_state"] == "ambiguous"


def test_missing_client_id_is_unknown_not_zero(monkeypatch):
    """IB not reporting a clientId must not be read as 'client 0'."""
    orphan = dict(_ORDER_6, client_id=None)
    rc, out = _run(monkeypatch, [[orphan]], ["--order-id", "6", "--apply"])
    assert rc == 3
    assert "not reported" in out["error"]


def test_cancel_failure_is_reported(monkeypatch):
    rc, out = _run(monkeypatch, [[_ORDER_6]], ["--order-id", "6", "--apply"],
                   cancel={"retCode": 1, "retMsg": "boom"})
    assert rc == 1
    assert out["action"] == "cancel_failed"


def test_protective_classification_covers_both_signals():
    assert mod._is_protective({"oca_group": "oca-protect-t1", "order_type": "LMT"})
    assert mod._is_protective({"oca_group": None, "order_type": "STP"})
    assert mod._is_protective({"oca_group": None, "order_type": "trail"})
    assert not mod._is_protective({"oca_group": None, "order_type": "MKT"})
    assert not mod._is_protective({"oca_group": "", "order_type": "MKT"})


# --- BL-20260826-CANCEL-IB-ORDER-READS-ERROR-202-AS-A-REFUSAL ---------------
# IBKR sends acceptance and rejection down ONE event channel. Error 202 is
# "Order Canceled - reason:..." — the venue confirming the cancel LANDED. It
# was filed as a refusal, so the tool reported a successful repair as a failure
# and told the operator a retry would not help. Measured live on `ib_paper` MHG
# order 466 (2026-08-26): reported `refused_by_venue` / `still_present`, while a
# re-run from a fresh process read `not_found` with the account's order count
# down 8 -> 6.

_CONFIRMED = {"retCode": 0, "retMsg": "OK (venue CONFIRMED the cancel)",
              "confirmation": {"code": 202, "message": "Order Canceled - reason:"}}
_REFUSED = {"retCode": 1, "retMsg": "venue REFUSED the cancel",
            "refusal": {"code": 10147,
                        "message": "OrderId 466 that needs to be cancelled is not found"}}


def test_venue_confirmation_is_not_a_refusal(monkeypatch):
    """A 202 must never surface as `refused_by_venue`."""
    rc, out = _run(monkeypatch, [[_ORDER_6], []], ["--order-id", "6", "--apply"],
                   cancel=_CONFIRMED)
    assert rc == 0
    assert out["action"] == "cancelled"
    assert out["confirmation"]["code"] == 202


def test_confirmed_cancel_still_on_a_stale_readback_is_its_own_state(monkeypatch):
    """Confirmed by the venue + still on the re-read == contradiction, not failure.

    The verification read runs on a read-only client whose order view is
    accumulated and never learns of a cancel it did not issue
    (BL-20260826-DIAG-IB-OPEN-ORDERS-SERVES-A-STALE-MONOTONIC-ORDER-VIEW), so
    it goes stale in exactly this direction. Collapsing this into `gone` would
    claim an absence nobody observed; collapsing it into `cancel_not_effective`
    is what produced the false failure report.
    """
    rc, out = _run(monkeypatch, [[_ORDER_6], [_ORDER_6]],
                   ["--order-id", "6", "--apply"], cancel=_CONFIRMED)
    assert rc == 0
    assert out["action"] == "cancelled_readback_contradicted"
    assert out["verify_state"] == "contradicted"
    assert out["remaining"]
    assert "fresh process" in out["note"]


def test_genuine_refusal_still_reports_refused(monkeypatch):
    """The fix must not make the tool blind to a real rejection."""
    rc, out = _run(monkeypatch, [[_ORDER_6]], ["--order-id", "6", "--apply"],
                   cancel=_REFUSED)
    assert rc == 1
    assert out["action"] == "refused_by_venue"
    assert out["refusal"]["code"] == 10147


def test_refusal_note_names_the_code_that_came_back(monkeypatch):
    """The 10147 story must not be asserted under an unrelated code."""
    other = {"retCode": 1, "retMsg": "venue REFUSED the cancel",
             "refusal": {"code": 201, "message": "Order rejected - reason:"}}
    _, out = _run(monkeypatch, [[_ORDER_6]], ["--order-id", "6", "--apply"],
                  cancel=other)
    assert "error 201" in out["note"]
    assert "10147" not in out["note"]

    _, out = _run(monkeypatch, [[_ORDER_6]], ["--order-id", "6", "--apply"],
                  cancel=_REFUSED)
    assert "10147" in out["note"]
