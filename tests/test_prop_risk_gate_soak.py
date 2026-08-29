"""The prop ticket risk-gate's soak row — the evidence trail for the Tier-3
``PROP_TICKET_RISK_GATE_MODE=enforce`` flip.

Regression cover for the 2026-08-29 ``/system-review`` finding: the module's
MODE block had promised since 2026-08-25 that at ``annotate`` "a soak row is
written", and no row was ever written — the file contained no ``open()`` at
all. So the one gate standing between the bot and an account that can be
PERMANENTLY DISABLED produced nothing for the operator to decide ``enforce`` on.
"""
from __future__ import annotations

import json

import pytest

from src.prop import prop_risk_gate


@pytest.fixture()
def soak(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.utils.paths.runtime_logs_dir", lambda: tmp_path, raising=False)
    return tmp_path / "prop_ticket_risk_soak.jsonl"


def _rows(path):
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def test_annotate_writes_a_row(soak, monkeypatch):
    monkeypatch.setenv("PROP_TICKET_RISK_GATE_MODE", "annotate")
    verdict = prop_risk_gate.grade_ticket_risk(
        risk_usd=75.0, distance_to_dd_floor_usd=55.0, status_freshness="ok")
    assert verdict["state"] == prop_risk_gate.EXCEEDS
    prop_risk_gate.record_ticket_risk_soak("breakout_1", verdict, annotated=True)

    rows = _rows(soak)
    assert len(rows) == 1
    r = rows[0]
    assert r["state"] == "exceeds_cushion"
    assert r["account_id"] == "breakout_1"
    assert r["global_mode"] == "annotate"
    assert r["annotated"] is True
    # The field the operator reads before flipping to `enforce`.
    assert r["would_have_capped"] is True
    assert r["risk_usd"] == 75.0 and r["cushion_usd"] == 55.0
    assert r["overshoot_usd"] == 20.0


def test_off_writes_nothing(soak, monkeypatch):
    """`off` must stay byte-for-byte the pre-2026-08-25 behaviour, on disk too."""
    monkeypatch.setenv("PROP_TICKET_RISK_GATE_MODE", "off")
    verdict = prop_risk_gate.grade_ticket_risk(
        risk_usd=75.0, distance_to_dd_floor_usd=55.0, status_freshness="ok")
    prop_risk_gate.record_ticket_risk_soak("breakout_1", verdict, annotated=True)
    assert _rows(soak) == []


def test_unknown_cushion_is_null_never_zero(soak, monkeypatch):
    """*We could not look* and *the account is AT its floor* are opposite
    statements. A fabricated 0.0 would report a terminal account as measured."""
    monkeypatch.setenv("PROP_TICKET_RISK_GATE_MODE", "annotate")
    verdict = prop_risk_gate.grade_ticket_risk(
        risk_usd=75.0, distance_to_dd_floor_usd=55.0, status_freshness="stale")
    assert verdict["state"] == prop_risk_gate.UNKNOWN
    prop_risk_gate.record_ticket_risk_soak("breakout_1", verdict, annotated=True)

    r = _rows(soak)[0]
    assert r["state"] == "cushion_unknown"
    assert r["cushion_usd"] is None, "a stale cushion must not be reported as 0.0"
    assert r["would_have_capped"] is False


def test_annotated_records_the_effect_not_the_request(soak, monkeypatch):
    """A graded ticket whose caveat never reached the operator must not read as
    one that did — the distinction NETTING_ATTRIBUTION_MODE had to be corrected
    for on 2026-08-09."""
    monkeypatch.setenv("PROP_TICKET_RISK_GATE_MODE", "annotate")
    verdict = prop_risk_gate.grade_ticket_risk(
        risk_usd=75.0, distance_to_dd_floor_usd=55.0, status_freshness="ok")
    prop_risk_gate.record_ticket_risk_soak("breakout_1", verdict, annotated=False)
    r = _rows(soak)[0]
    assert r["global_mode"] == "annotate" and r["annotated"] is False


def test_writer_never_raises_into_the_ticket_path(monkeypatch):
    """A ticket must never be lost over its observability row."""
    monkeypatch.setenv("PROP_TICKET_RISK_GATE_MODE", "annotate")

    def boom():
        raise OSError("disk gone")

    monkeypatch.setattr("src.utils.paths.runtime_logs_dir", boom, raising=False)
    prop_risk_gate.record_ticket_risk_soak("breakout_1", {"state": "within_cushion"})


def test_soak_name_is_on_the_diag_allowlist():
    """Shipped in the SAME commit as the writer — a gate whose evidence cannot
    be inspected is the exit_loop_health #8778 shape."""
    from src.web.api.routers import diag
    assert "prop_ticket_risk_soak" in diag._LOG_FILES
    assert diag._LOG_FILES["prop_ticket_risk_soak"].name == "prop_ticket_risk_soak.jsonl"
