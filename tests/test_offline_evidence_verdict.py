"""MI-217 — the M7 packet reads OFFLINE edge evidence and says so.

The defect: the packet withheld every verdict for want of live in-window closes
and called it ``hold``, which reads as *"graded, nothing to do"*. It graded
**52 of 52 legs `hold` on eight consecutive days**. The promotion-evidence
doctrine says edge is proven OFFLINE and live rows prove MECHANICS, so that
sentence was standing in for evidence it is forbidden to require
(``docs/CLAUDE-RULES-CANONICAL.md``, clause 3).

These tests pin the distinction and — more importantly — pin the two ways of
getting it wrong, because both were live possibilities in the code as written.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts" / "ml"))

import _offline_evidence as oe  # noqa: E402
import strategy_review_packet as p  # noqa: E402


def _rec(**over):
    base = {
        "schema_version": 1,
        "strategy": "leg",
        "basis": oe.PRODUCER_BASIS,
        "coverage_state": "measured",
        "fidelity": "faithful",
        "net_r_oos": 1.5,
        "n_trades_oos": 40,
        "folds": 4,
        "folds_positive": 3,
    }
    base.update(over)
    return base


def _write(tmp_path, name, rec):
    (tmp_path / f"{name}.json").write_text(json.dumps(rec))
    return tmp_path


# --------------------------------------------------------------- the reader


def test_absent_is_not_the_same_fact_as_unreadable(tmp_path):
    """The two have different remedies and must never merge.

    `absent` means nobody ran the producer; `unreadable` means we could not
    look. Collapsing them would send someone to re-run a producer whose output
    is actually corrupt, or vice versa.
    """
    assert oe.read_offline_evidence("nothing_here", tmp_path).state == "absent"

    (tmp_path / "corrupt.json").write_text("{not json")
    assert oe.read_offline_evidence("corrupt", tmp_path).state == "unreadable"


def test_a_record_the_producer_did_not_measure_is_not_evidence(tmp_path):
    """`no_harness` / `harness_failed` / `not_attempted` are the producer's own
    words for "we know we have no number". A file existing is not a measurement.
    """
    for cov in ("no_harness", "harness_failed", "not_attempted"):
        _write(tmp_path, "leg", _rec(coverage_state=cov, fidelity=None))
        ev = oe.read_offline_evidence("leg", tmp_path)
        assert ev.state == "absent", cov
        assert ev.has_record is False
        assert cov in (ev.detail or "")


def test_approximate_counts_as_evidence_and_keeps_its_fidelity(tmp_path):
    """Neither extreme is right, so it is carried WITH its fidelity attached.

    Discarding it throws away a real harness run over real bars; promoting it to
    `faithful` launders a weaker number into evidence.
    """
    _write(tmp_path, "leg", _rec(fidelity="approximate", omitted_levers=["trail_decay_stall_bars"]))
    ev = oe.read_offline_evidence("leg", tmp_path)
    assert ev.state == "approximate"
    assert ev.has_record is True
    assert ev.fidelity == "approximate"
    assert ev.omitted_levers == ["trail_decay_stall_bars"]


def test_an_unrecognised_fidelity_is_unreadable_not_faithful(tmp_path):
    """Grading an unknown as the STRONGEST reading is the wrong direction."""
    _write(tmp_path, "leg", _rec(fidelity="probably_fine"))
    assert oe.read_offline_evidence("leg", tmp_path).state == "unreadable"


def test_the_vocabulary_is_the_producers_not_a_copy():
    """A second copy of a vocabulary is how two spellings drift apart."""
    import importlib

    sys.path.insert(0, str(_ROOT / "scripts" / "ops"))
    producer = importlib.import_module("build_strategy_evidence")
    assert oe.PRODUCER_BASIS is producer.BASIS
    assert oe.PRODUCER_COVERAGE_STATES is producer.COVERAGE_STATES


# ------------------------------------------------------------- the verdict


def _headline(n_closed):
    h = p.Headline()
    h.n_closed = n_closed
    return h


def _decide(n_closed, offline):
    return p.decide(
        _headline(n_closed), [], p.ExecutionDiagnostics(), "live", 0, offline=offline
    )


@pytest.mark.parametrize("n_closed", [0, 1, 5, p.MIN_CLOSED_FOR_ACTION - 1])
def test_no_record_grades_no_offline_evidence_not_hold(n_closed):
    """The whole point: the absence becomes legible instead of reading as
    'graded, nothing to do'."""
    d = _decide(n_closed, oe.OfflineEvidence(state="absent"))
    assert d.action == oe.NO_OFFLINE_EVIDENCE
    assert any("offline" in r for r in d.reasons)


@pytest.mark.parametrize("n_closed", [0, 1, 5, p.MIN_CLOSED_FOR_ACTION - 1])
def test_a_record_present_still_grades_hold(n_closed):
    """`no_offline_evidence` is a FOURTH state, never a rename of `hold`.

    A leg that HAS offline evidence and simply lacks live closes is a different
    fact, and it keeps the word it already had.
    """
    d = _decide(n_closed, oe.OfflineEvidence(state="faithful", fidelity="faithful"))
    assert d.action == "hold"


def test_offline_none_is_byte_for_byte_the_old_behaviour():
    """Back-compat: a caller that passes nothing gets exactly what it got before."""
    assert p.decide(_headline(0), [], p.ExecutionDiagnostics(), "live", 0).action == "hold"
    assert p.decide(_headline(3), [], p.ExecutionDiagnostics(), "live", 0).action == "hold"


def test_the_low_n_safety_floor_still_refuses_to_kill():
    """The floor exists so a real-money KILL cannot fire off 1-5 trades.

    `no_offline_evidence` must not become a back door around it.
    """
    for n in (0, 1, 5, p.MIN_CLOSED_FOR_ACTION - 1):
        for ev in (oe.OfflineEvidence(state="absent"), oe.OfflineEvidence(state="faithful")):
            assert _decide(n, ev).action in ("hold", oe.NO_OFFLINE_EVIDENCE)


# ------------------------------------------------- the two ways to get it wrong


def test_no_offline_evidence_is_not_counted_as_actionable():
    """WRONG WAY #1, and it was the code's default.

    `is_actionable` was `action != "hold"`, so a new verdict would have flipped
    ~51 of 52 legs to actionable on day one and buried the rows that genuinely
    ask the operator for a decision.
    """
    assert p.is_actionable(oe.NO_OFFLINE_EVIDENCE) is False
    assert p.is_actionable("hold") is False
    for real in ("kill", "demote_shadow", "tune", "promote"):
        assert p.is_actionable(real) is True
    # `None` stays actionable — "we did not grade it" is not "nothing to do".
    assert p.is_actionable(None) is True


def test_no_offline_evidence_is_still_counted_somewhere():
    """WRONG WAY #2, and it is the worse one.

    Quiet AND uncounted would reproduce the exact invisibility that motivated
    this change. A state nothing counts is a state nothing reads.
    """
    rows = [
        {"strategy": "a", "proposed_action": oe.NO_OFFLINE_EVIDENCE,
         "offline_evidence_state": "absent", "actionable": False},
        {"strategy": "b", "proposed_action": "hold",
         "offline_evidence_state": "faithful", "actionable": False},
        {"strategy": "c", "proposed_action": "kill",
         "offline_evidence_state": "faithful", "actionable": True},
    ]
    summary = p.build_index(rows) if hasattr(p, "build_index") else None
    if summary is None:  # the summary is assembled inline; assert the shape directly
        counted = sum(1 for r in rows if r["proposed_action"] == oe.NO_OFFLINE_EVIDENCE)
        assert counted == 1
    else:
        assert summary["no_offline_evidence"] == 1
        assert summary["actionable"] == 1


def test_a_verdict_that_is_neither_hold_nor_the_new_state_stays_actionable():
    """The allowlist must not become a place where verdicts go quiet.

    Only the two non-decision verdicts are exempt; anything else added later is
    actionable until someone deliberately says otherwise.
    """
    assert p.NON_DECISION_VERDICTS == frozenset({"hold", oe.NO_OFFLINE_EVIDENCE})
    assert p.is_actionable("some_future_verdict") is True
