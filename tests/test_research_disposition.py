"""The disposition ledger: does a landed research result ever get READ?

The load-bearing test here is `test_superseded_is_not_reported_as_unread`. If
those two states ever collapse, the detector reports 288 findings on its first
run instead of 92 — and an alarm that fires on everything is the desensitized-
alarm P1 this repo files as its own bug class.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.research import research_disposition as rd  # noqa: E402


def test_selftest_passes():
    r = subprocess.run([sys.executable, "scripts/research/research_disposition.py",
                        "--selftest"], cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_selftest_is_not_vacuous(tmp_path):
    """Plant the collapse the design forbids; the self-test must go red.

    Without this, a self-test that always exits 0 would look identical to one
    that checks something — the exact failure `stuck_automation_branches.py` was
    caught on earlier the same day.
    """
    src = (REPO / "scripts/research/research_disposition.py").read_text()
    planted = src.replace(
        "    if latest_by_leg.get((corpus, leg)) != stamp:\n"
        "        return SUPERSEDED_UNREAD\n", "")
    assert planted != src, "the plant did not apply - the test is testing nothing"
    victim = tmp_path / "research_disposition.py"
    victim.write_text(planted)
    r = subprocess.run([sys.executable, str(victim), "--selftest"],
                       capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 1


def test_superseded_is_not_reported_as_unread():
    units, latest = {("m20", "leg")}, {("m20", "leg"): "T2"}
    assert rd.state_for_unit(("m20", "T2", "leg"), units, set(), latest) == rd.UNREAD
    assert rd.state_for_unit(("m20", "T1", "leg"), units, set(), latest) == rd.SUPERSEDED_UNREAD


def test_a_missing_store_is_unreadable_never_empty(monkeypatch):
    """`could not look` must never be served as `nothing to report`."""
    monkeypatch.setitem(
        rd.CORPORA, "m20", (Path("/nope/absent.jsonl"), "sweep_generated_at", "leg"))
    assert rd.load_units("m20")[0] == rd.CORPUS_UNREADABLE


def test_an_absent_ledger_is_readable_and_empty(tmp_path):
    """Distinct from the above: nothing dispositioned YET is a true day-one state."""
    state, seen = rd.load_ledger(tmp_path / "none.jsonl")
    assert (state, seen) == ("read", set())


def test_an_unreadable_ledger_is_not_an_empty_one(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json\n")
    assert rd.load_ledger(bad)[0] == rd.CORPUS_UNREADABLE


@pytest.mark.parametrize("reason", [
    "No new evidence bearing on this leg; carried forward unchanged.",
    "Same as the previous run, nothing changed here at all.",
    "short",
])
def test_a_vacuous_reason_is_refused(tmp_path, reason):
    with pytest.raises(ValueError):
        rd.append({"corpus": "m20", "run_stamp": "T", "leg": "l",
                   "verdict": "no_action_warranted", "reason": reason},
                  ledger=tmp_path / "l.jsonl")


def test_a_real_reason_is_accepted(tmp_path):
    """The non-vacuity control for the gate above: it must not refuse everything."""
    led = tmp_path / "l.jsonl"
    rd.append({"corpus": "m20", "run_stamp": "T", "leg": "l", "verdict": "underpowered",
               "reason": "OOS book is 33 trades against a declared floor of 49; "
                         "converting to a data-acquisition task."}, ledger=led)
    assert json.loads(led.read_text())["verdict"] == "underpowered"


def test_actioned_must_name_its_actions(tmp_path):
    body = {"corpus": "m20", "run_stamp": "T", "leg": "l", "verdict": "actioned",
            "reason": "Both folds clear the gate at n=49; shipping the cell."}
    with pytest.raises(ValueError):
        rd.append(body, ledger=tmp_path / "l.jsonl")
    rd.append({**body, "actions": ["BL-XXXX"]}, ledger=tmp_path / "l.jsonl")


def test_non_reasons_come_from_the_review_validator():
    """One definition, not two.

    If someone re-inlines `_NON_REASONS` into `_validate_review_coverage`, this
    import breaks loudly instead of the two copies drifting quietly apart.
    """
    from scripts.reports.render_system_report import _NON_REASONS
    assert "carried forward" in _NON_REASONS
    assert rd._non_reasons() == tuple(_NON_REASONS)


# ---------------------------------------------------------------------------
# Registering a corpus is part of LANDING it (2026-08-30).
#
# gld_compat was produced, landed and asserted-on-main by a chain built the same
# day — and was invisible here, because CORPORA knew only e35/m20 and the leg
# field was hardcoded "leg" while those rows carry `account_id`. `load_units`
# skips a row missing stamp-or-leg, so the corpus would have contributed ZERO
# units with read_state="read": reporting nothing unread while nobody had read a
# row. That is the R1–R6 gap this module exists to close (the chain ends at
# `landed`) reproduced one level down, inside the fix for it.
# ---------------------------------------------------------------------------


def test_every_registered_corpus_names_a_leg_field_its_rows_actually_carry():
    """A leg field the rows do not have makes the corpus SILENTLY empty.

    Not a style check. `read_state` would still be `read`, so the emptiness is
    indistinguishable from a corpus nobody has produced into yet — the exact
    "we did not look" vs "we looked and found nothing" collapse.
    """
    import json as _json

    for corpus, (path, stamp_field, leg_field) in rd.CORPORA.items():
        if not path.exists():
            continue  # a store not yet produced into is not this test's subject
        first = None
        with path.open() as fh:
            for line in fh:
                if line.strip():
                    first = _json.loads(line)
                    break
        if first is None:
            continue
        assert leg_field in first, (
            f"{corpus}: registered leg field {leg_field!r} is absent from its own "
            f"rows (which carry {sorted(first)[:8]}...) — load_units would skip "
            "every row and the corpus would read as fully dispositioned"
        )
        assert stamp_field in first, (
            f"{corpus}: registered stamp field {stamp_field!r} is absent from its rows"
        )


def test_a_registered_corpus_that_exists_yields_units():
    """The positive control for the refusal above: registration actually works.

    Without this, the assertion above is satisfiable by registering nothing.
    """
    produced = [c for c in rd.CORPORA if rd.CORPORA[c][0].exists()]
    assert produced, "no corpus is present — this test would be vacuous"
    for corpus in produced:
        state, units = rd.load_units(corpus)
        assert state == "read", f"{corpus}: {state}"
        assert units, (
            f"{corpus}: present and readable but yielded ZERO units — the silent-skip "
            "shape; check the registered leg/stamp fields against the rows"
        )


def test_n_field_is_declared_for_every_corpus():
    """A missing key is a KeyError at read time, on a path nobody exercises often."""
    assert set(rd.N_FIELD) == set(rd.CORPORA)
