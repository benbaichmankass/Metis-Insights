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


# --------------------------------------------------------------------------
# The terminal-verdict refusal: a unit the ADMISSION GATE accepted as
# `accruing` cannot be recorded as having answered its question.
#
# These call `append` for real and let it read a patched `load_units`, rather
# than re-implementing the predicate — a test that recomputes the rule proves
# only that the test and the code agree with themselves.
# --------------------------------------------------------------------------
import contextlib  # noqa: E402


@contextlib.contextmanager
def _unit_state(power_state):
    """Patch the corpus reader so the accrual check is deterministic."""
    original = rd.load_units
    rd.load_units = lambda corpus: (
        "read", {("T", "l"): {"rows": 1, "n_oos": 50,
                              "power_state": power_state, "research_unit": "RQ-X"}})
    try:
        yield
    finally:
        rd.load_units = original


def _body(**kw):
    b = {"corpus": "m20", "run_stamp": "T", "leg": "l",
         "verdict": "no_action_warranted",
         "reason": "Both folds clear the gate and no cell moves; nothing to ship."}
    b.update(kw)
    return b


@pytest.mark.parametrize("verdict", ["actioned", "no_action_warranted"])
@pytest.mark.parametrize("admitted", ["accruing", "underpowered", "infeasible"])
def test_a_data_shortfall_unit_cannot_be_closed_with_a_terminal_verdict(
        tmp_path, verdict, admitted):
    """⚠️ THE COMPENSATING HALF OF THE 2026-08-31 LENIENCY DIRECTIVE.

    `underpowered` and `infeasible` became RUNNABLE that day. This refusal is
    the only thing that then stands between a deliberately-thin run and a
    ledger entry claiming it answered something — so it is parametrized over
    the whole shortfall set rather than just `accruing`, and
    tests/test_research_queue.py pins the two modules' sets equal so neither
    can widen alone.
    """
    body = _body(verdict=verdict)
    if verdict == "actioned":
        body["actions"] = ["BL-XXXX"]
    with _unit_state(admitted):
        with pytest.raises(ValueError, match=admitted):
            rd.append(body, ledger=tmp_path / "l.jsonl")


@pytest.mark.parametrize("verdict", ["underpowered", "superseded"])
def test_an_accruing_unit_may_still_be_recorded_as_an_honest_non_answer(tmp_path, verdict):
    """The non-vacuity control, and the half that keeps the gate usable.

    Refusing these too would leave a reviewer no legal way to write down what
    they found, which is how a gate teaches people to route around it.
    """
    led = tmp_path / "l.jsonl"
    with _unit_state(rd.ACCRUING_STATE):
        rd.append(_body(verdict=verdict), ledger=led)
    assert json.loads(led.read_text())["verdict"] == verdict


def test_a_cleared_unit_closes_normally_and_is_stamped_clear(tmp_path):
    led = tmp_path / "l.jsonl"
    with _unit_state("cleared"):
        rd.append(_body(), ledger=led)
    assert json.loads(led.read_text())["accrual_check"] == "clear"


def test_an_override_admits_the_close_but_must_state_a_real_reason(tmp_path):
    led = tmp_path / "l.jsonl"
    with _unit_state(rd.ACCRUING_STATE):
        with pytest.raises(ValueError, match="non-reason"):
            rd.append(_body(accrual_override_reason=
                            "no new evidence bearing on this item, carried forward"),
                      ledger=led)
        rd.append(_body(accrual_override_reason=
                        "the post-raise run landed n=57 per leg, so this unit IS "
                        "decidable now despite its accrual admission."), ledger=led)
    assert json.loads(led.read_text())["accrual_check"] == "accruing_overridden"


def test_an_unreadable_corpus_admits_the_write_but_records_that_it_could_not_look(tmp_path):
    """Fail-OPEN, and stamp it. This gates a bookkeeping RECORD, not an order,
    so blocking every disposition during a corpus outage would suppress honest
    reading to prevent a rare dishonest one. What must not happen is the
    distinction vanishing into `clear`."""
    led = tmp_path / "l.jsonl"
    original = rd.load_units
    rd.load_units = lambda corpus: ("corpus_unreadable", {})
    try:
        rd.append(_body(), ledger=led)
    finally:
        rd.load_units = original
    assert json.loads(led.read_text())["accrual_check"] == "corpus_unreadable"


def test_a_unit_absent_from_the_corpus_is_not_recorded_as_clear(tmp_path):
    led = tmp_path / "l.jsonl"
    original = rd.load_units
    rd.load_units = lambda corpus: ("read", {})
    try:
        rd.append(_body(), ledger=led)
    finally:
        rd.load_units = original
    assert json.loads(led.read_text())["accrual_check"] == "unit_absent"


def test_the_accrual_state_string_matches_the_queue_module(tmp_path):
    """The two modules duplicate the literal deliberately (research_disposition
    must stay readable when the queue package is not importable). Pin them."""
    from scripts.research.research_queue import ACCRUING
    assert rd.ACCRUING_STATE == ACCRUING


def test_the_survey_carries_the_admission_fields_a_reader_needs():
    """Written-and-never-read control: `load_units` recorded these two fields
    from 2026-08-31 and NOTHING read them back until the same day."""
    s = rd.survey()
    assert s["units"], "the live stores must yield units, or this proves nothing"
    for u in s["units"]:
        assert "power_state" in u and "research_unit" in u


# ---------------------------------------------------------------------------
# The WRITE half's CLI surface (added 2026-08-31).
#
# `append` shipped complete and well-guarded, and `main` exposed only the read
# flags — so recording a disposition meant importing the module from an ad-hoc
# snippet, which is how all 75 pre-existing ledger entries were written. A
# mechanism whose supported path is "hand-roll a snippet" is one whose
# validation is one forgotten import away from being skipped, so these tests
# assert the guards are reachable THROUGH THE CLI, not merely present in the
# module.
# ---------------------------------------------------------------------------

def _corpus(tmp_path, rows):
    p = tmp_path / "c.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def _cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, "scripts/research/research_disposition.py", *args],
        cwd=cwd or REPO, capture_output=True, text=True)


def test_record_is_reachable_from_the_cli_at_all():
    """The regression this file exists to prevent: no write flag on the CLI."""
    r = _cli("--help")
    assert r.returncode == 0
    for flag in ("--record", "--corpus", "--run-stamp", "--leg", "--verdict",
                 "--reason", "--dry-run", "--force"):
        assert flag in r.stdout, f"{flag} is not exposed on the CLI"


def test_record_refuses_a_unit_the_corpus_does_not_hold(tmp_path, monkeypatch):
    """A disposition for a nonexistent unit reads as COVERAGE of nothing.

    `_accrual_check` grades this `unit_absent`, but only INSIDE `append` — after
    the row is committed — and `state_for_unit` checks ledger membership FIRST,
    so such a row reports `dispositioned` forever. The check must run BEFORE the
    write.
    """
    r = _cli("--record", "--dry-run", "--corpus", "e35", "--leg", "no_such_leg_xyz",
             "--run-stamp", "1999-01-01T00:00:00+00:00", "--verdict", "underpowered",
             "--reason", "a reason long enough to clear the twenty character floor")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "reads as COVERAGE of something that does not exist" in r.stderr


def test_the_absent_unit_check_is_skippable_only_on_the_record(tmp_path):
    """--force is allowed, but stamps the skip onto the entry.

    An escape hatch that leaves no trace is how the check gets routed around.
    """
    led = tmp_path / "led.jsonl"
    rd.append({"corpus": "e35", "run_stamp": "T", "leg": "l",
               "verdict": "underpowered", "unit_absent_override": True,
               "reason": "a reason long enough to clear the twenty character floor"},
              ledger=led, non_reasons=("carried forward unchanged",))
    row = json.loads(led.read_text().splitlines()[0])
    assert row["unit_absent_override"] is True
    assert row["accrual_check"] == "unit_absent"


def test_cli_refuses_a_non_reason_rather_than_recording_it():
    """The non-reason vocabulary must bite through the CLI, not just in-module."""
    r = _cli("--record", "--dry-run", "--corpus", "e35", "--leg", "x",
             "--run-stamp", "T", "--verdict", "underpowered",
             "--reason", "no new evidence bearing on this item, carried forward unchanged")
    assert r.returncode != 0
    assert "non-reason" in r.stderr or "does not exist" in r.stderr


def test_cli_refuses_closing_an_accruing_unit_terminally():
    """The R4 admission promise, reachable from the CLI.

    Uses a REAL accruing unit from the committed corpus, so this fails if the
    accrual gate ever stops being consulted on the write path.
    """
    import scripts.research.research_disposition as m
    state, units = m.load_units("e35")
    if state != "read":
        pytest.skip("e35 corpus unreadable in this checkout")
        return
    accruing = [(s, leg) for (s, leg), meta in units.items()
                if meta.get("power_state") in m.DATA_SHORTFALL_STATES]
    if not accruing:
        pytest.skip("no accruing unit in the committed corpus")
        return
    stamp, leg = sorted(accruing)[0]
    r = _cli("--record", "--dry-run", "--corpus", "e35", "--leg", leg,
             "--run-stamp", stamp, "--verdict", "no_action_warranted",
             "--reason", "the bracket cells read acceptable on inspection of the rows")
    assert r.returncode != 0, r.stdout
    assert "cannot be closed" in r.stderr


def test_dry_run_writes_nothing():
    before = (REPO / "docs/research/research-disposition-ledger.jsonl")
    n_before = len(before.read_text().splitlines()) if before.exists() else 0
    state, units = rd.load_units("e35")
    if state != "read" or not units:
        pytest.skip("e35 corpus unreadable")
        return
    stamp, leg = sorted(units)[0]
    r = _cli("--record", "--dry-run", "--corpus", "e35", "--leg", leg,
             "--run-stamp", stamp, "--verdict", "underpowered",
             "--reason", "a reason long enough to clear the twenty character floor")
    n_after = len(before.read_text().splitlines()) if before.exists() else 0
    assert n_after == n_before, "--dry-run appended to the real ledger"
    assert r.returncode == 0, r.stdout + r.stderr
