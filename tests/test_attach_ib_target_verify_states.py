"""``attach-ib-target``'s post-place verification must distinguish FILLED from ABSENT.

``BL-20260818-ATTACH-IB-TARGET-VERIFY-CANNOT-EXPRESS-FILLED``.

The old predicate asked only *"is a target RESTING?"*, so ``still_absent`` carried two
OPPOSITE outcomes — "never placed" and "placed and already filled". Measured 2026-08-18
(issue #9929) on the MGC repair: the action reported ``place_not_effective`` /
``still_absent`` and exited 1 while the position was in fact GONE — a SELL LMT 105 @
4297.66 into a ~4420 market is marketable and filled instantly.

⚠️ The danger is the direction of the error. A red on a FILLED sell invites the
obviously-reasonable retry, and the retry places a SECOND sell against a now-flat book —
a naked SHORT with no bracket. These tests pin the distinction that prevents it.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "attach_ib_target",
    Path(__file__).resolve().parents[1] / "scripts/ops/attach_ib_target.py",
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


def _drive(monkeypatch, capsys, *, targets_after, positions, declared_qty=105.0):
    """Run main(--apply) with everything before the place stubbed to 'ready'."""
    trade = {"id": 4487, "direction": "long", "take_profit_1": 4297.66,
             "position_size": declared_qty, "stop_loss": 4200.0}
    monkeypatch.setattr(mod, "_load_account", lambda a: {"account_id": a,
                                                         "exchange": "interactive_brokers"})
    monkeypatch.setattr(mod, "_open_trade", lambda a, s: trade)
    # before: one stop, no target (the target-naked precondition)
    stop = {"symbol": "MGC", "order_type": "STP", "total_quantity": declared_qty,
            "oca_group": "oca-protect-357", "order_id": 359}
    calls = {"n": 0}

    def _read(cfg):
        calls["n"] += 1
        return [stop] if calls["n"] == 1 else list(targets_after)

    monkeypatch.setattr(mod, "_read_orders", _read)
    monkeypatch.setattr(mod, "_attach", lambda *a, **k: {
        "retCode": 0, "result": {"orderId": 381, "ocaGroup": "oca-protect-357"},
        "retMsg": "OK"})
    from src.units.accounts import clients as _clients
    monkeypatch.setattr(_clients, "account_open_positions", lambda cfg: positions,
                        raising=False)
    rc = mod.main(["--account", "ib_paper", "--symbol", "MGC", "--apply"])
    payload = json.loads(capsys.readouterr().out)
    return rc, payload


_RESTING_TARGET = [{"symbol": "MGC", "order_type": "LMT", "total_quantity": 105.0,
                    "order_id": 381, "oca_group": "oca-protect-357"}]


def test_a_resting_target_is_the_protective_success(monkeypatch, capsys):
    rc, out = _drive(monkeypatch, capsys, targets_after=_RESTING_TARGET,
                     positions=[{"symbol": "MGC", "size": 105.0}])
    assert rc == 0
    assert out["verify_state"] == "target_resting"
    assert out["action"] == "placed"


def test_a_filled_target_is_a_SUCCESS_not_a_failure(monkeypatch, capsys):
    """The measured MGC case: no resting target, position GONE."""
    rc, out = _drive(monkeypatch, capsys, targets_after=[], positions=[])
    assert out["verify_state"] == "target_filled", (
        "a flat position after an accepted place means the order FILLED"
    )
    assert rc == 0, "exit 1 here is the red that invites the naked-short retry"
    assert out["action"] == "placed_and_filled"


def test_a_partial_fill_counts_as_filled(monkeypatch, capsys):
    rc, out = _drive(monkeypatch, capsys, targets_after=[],
                     positions=[{"symbol": "MGC", "size": 40.0}])
    assert out["verify_state"] == "target_filled"
    assert rc == 0


def test_a_standing_position_with_no_target_is_the_genuine_failure(monkeypatch, capsys):
    rc, out = _drive(monkeypatch, capsys, targets_after=[],
                     positions=[{"symbol": "MGC", "size": 105.0}])
    assert out["verify_state"] == "absent_unexplained"
    assert rc == 1, "this is the one case the non-zero exit is for"


def test_an_unreadable_position_is_could_not_look_not_a_verdict(monkeypatch, capsys):
    """``None`` from the position reader must NOT be graded as flat OR as absent.

    Grading it ``absent_unexplained`` would invite exactly the retry this row is about;
    grading it ``target_filled`` would report a success nobody observed.
    """
    rc, out = _drive(monkeypatch, capsys, targets_after=[], positions=None)
    assert out["verify_state"] == "could_not_look"
    assert rc == 3, "distinct from both the success (0) and the failure (1) exits"
    assert "do NOT" in out.get("note", "")


def test_still_absent_is_gone_because_it_collapsed_two_outcomes(monkeypatch, capsys):
    """The retired state must not come back — it is the defect, by name."""
    for pos in ([], [{"symbol": "MGC", "size": 105.0}], None):
        _rc, out = _drive(monkeypatch, capsys, targets_after=[], positions=pos)
        assert out["verify_state"] != "still_absent"


def test_the_verdict_publishes_the_evidence_it_used(monkeypatch, capsys):
    """The position read is a SECOND, independent signal and must be visible.

    A verdict a reader cannot check is the shape this row is about — the old one said
    ``still_absent`` and showed only ``targets_after: []``, which is consistent with
    both outcomes. Every non-resting verdict now carries ``position_after``.
    """
    for positions, expect_state in (([], "flat"),
                                    ([{"symbol": "MGC", "size": 105.0}], "open"),
                                    (None, "could_not_look")):
        _rc, out = _drive(monkeypatch, capsys, targets_after=[], positions=positions)
        assert "position_after" in out, "the verdict must show the evidence it used"
        assert out["position_after"]["state"] == expect_state


def test_a_flat_read_from_a_logged_out_gateway_cannot_fake_a_fill(monkeypatch, capsys):
    """The IB logged-out-but-connected case must arrive as None, not [].

    ``account_open_positions`` owns that guard; this pins that we consume its None
    rather than treating an empty snapshot as proof of a fill. If that guard ever
    regressed to returning [], a never-placed order would report ``target_filled``.
    """
    _rc, out = _drive(monkeypatch, capsys, targets_after=[], positions=None)
    assert out["verify_state"] == "could_not_look"
    assert out["verify_state"] != "target_filled"
