"""MI-303 — the DECISION CHANNEL's reader.

The sweep wrote a durable receipt on every run and NOTHING READ IT. Measured
2026-09-17: 46 of the 47 recoverable runs read
``candidates: 4 / failed: 4 / prompted_choice: 0`` while every health field
read good and all three ``held_*`` counters were ZERO — four operator decisions
had reached nobody and no surface said so.

⚠️ THESE TESTS CANNOT ESTABLISH THAT A PROMPT ARRIVES. A harness cannot reach a
Telegram chat, which is the whole of what was failing. What they pin is the
GRADING and the ROUTING decision: that ``all_failed`` is reachable, that it is
never confused with ``nothing_pending``, that it takes a different remedy from
``all_held``, and that the page is latched so it cannot become the desensitised
alarm it exists to replace. The delivery half is
``OI-20260918-THE-DECISION-CHANNEL-IS-REPAIRED-AND-NO-PROMPT-HAS-BEEN-OBSERVED-ARRIVING``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.runtime import alert_cooldown as ac
from src.runtime import decision_channel_alert as A
from src.runtime import telegram_decisions as td

NOW = datetime(2026, 9, 17, 23, 18, tzinfo=timezone.utc)


def _run(**over):
    """One receipt row. Defaults to the LIVE 2026-09-17 signature."""
    row = {
        "checked": True, "reason": None, "candidates": 4,
        "prompted_choice": 0, "prompted_free_text": 0,
        "prompted_choice_shortened": 0,
        "held_write_gate": 0, "held_route": 0, "held_not_polled": 0,
        "failed": 4, "paused": False, "prompt_state_read": "read",
        # collapsed-state: polled_with_handler — FIXTURE DATA, NOT A BRANCH.
        # This file quotes ONE value of `telegram_poll.poll_state` because it
        # reproduces the LIVE 2026-09-17 receipt signature verbatim, where that
        # field genuinely read `polled_with_handler` while nothing was being
        # delivered — which is half of why the defect looked healthy. Nothing
        # here branches on it, and nothing should: the poll_state decision has
        # ONE owner, `telegram_decisions.answerable_route`, which already
        # branches on all three. `decision_channel_alert` passes the value
        # through into the `all_held` operator message without reading it, so
        # adding `token_only_not_polled` / `unknown` cases to THIS file would be
        # the decorative branch this guard exists to prevent.
        "destination": "claude", "poll_state": "polled_with_handler",
        "token_from": "TELEGRAM_CLAUDE_BOT_SECRET",
        "failure_kinds": {td.FAIL_TOO_LONG: 4},
        "failure_detail": [{"key": "WO-X::DEC-Y", "kind": td.FAIL_TOO_LONG,
                            "error": "HTTPError: HTTP Error 400: Bad Request",
                            "body_chars": 5463}],
        "run_at": (NOW - timedelta(minutes=2)).isoformat(),
    }
    row.update(over)
    row.setdefault("sweep_outcome", td.grade_sweep_outcome(row))
    return row


def _receipt(row):
    return {"schema": 1, "updated_at": row.get("run_at"), "last": row,
            "runs": [row]}


# ── the grading ─────────────────────────────────────────────────────────────

def test_the_live_signature_grades_all_failed():
    a = A.assess(_receipt(_run()), "read", now=NOW)
    assert a["outcome"] == td.OUTCOME_ALL_FAILED
    assert a["failing"] == 4 and a["candidates"] == 4
    assert a["failure_kinds"] == {td.FAIL_TOO_LONG: 4}


@pytest.mark.parametrize("row,expected", [
    ({"candidates": 0, "failed": 0}, td.OUTCOME_NOTHING_PENDING),
    ({"candidates": 2, "failed": 0, "prompted_choice": 2},
     td.OUTCOME_DELIVERED),
    ({"candidates": 4, "failed": 4}, td.OUTCOME_ALL_FAILED),
    ({"candidates": 5, "failed": 4, "prompted_choice": 1},
     td.OUTCOME_PARTIAL),
    ({"candidates": 2, "failed": 0, "held_not_polled": 2},
     td.OUTCOME_ALL_HELD),
    ({"checked": False, "paused": True, "candidates": 0},
     td.OUTCOME_NOT_GRADED),
])
def test_every_outcome_is_reachable_through_the_reader(row, expected):
    base = _run(**row)
    base["sweep_outcome"] = td.grade_sweep_outcome(base)
    assert A.assess(_receipt(base), "read", now=NOW)["outcome"] == expected


@pytest.mark.parametrize("state", [A.RECEIPT_ABSENT, A.RECEIPT_UNREADABLE])
def test_an_unreadable_receipt_is_not_graded_never_nothing_pending(state):
    """⚠️ THE READING THAT WOULD HAVE HIDDEN THIS DEFECT AGAIN. *We did not
    look* is a different fact from *we looked and the inbox was empty*, and a
    fresh or mis-deployed VM is in the first state."""
    a = A.assess({}, state, now=NOW)
    assert a["outcome"] == td.OUTCOME_NOT_GRADED
    assert a["outcome"] != td.OUTCOME_NOTHING_PENDING
    assert a["receipt_state"] == state
    assert a["note"]


def test_an_unrecognised_verdict_is_not_re_derived_by_the_consumer():
    """A receipt written before `sweep_outcome` existed grades `not_graded`
    rather than being re-graded here — a second definition of the verdict
    living in the consumer is how the two drift apart."""
    row = _run()
    row["sweep_outcome"] = "something_new"
    a = A.assess(_receipt(row), "read", now=NOW)
    assert a["outcome"] == td.OUTCOME_NOT_GRADED
    assert a["raw_outcome"] == "something_new"


def test_a_receipt_with_no_last_run_is_not_graded():
    a = A.assess({"schema": 1, "runs": []}, "read", now=NOW)
    assert a["outcome"] == td.OUTCOME_NOT_GRADED


# ── a FROZEN sweep must not read as a live one ──────────────────────────────

def test_a_stale_receipt_is_flagged_even_when_its_last_verdict_was_healthy():
    """A sweep that STOPPED leaves its last row frozen, and a frozen
    `delivered` reads byte-identically to a live one — the trap the
    coordination board hit when a comment-capped issue served successful reads
    for ~20h."""
    row = _run(candidates=2, failed=0, prompted_choice=2,
               run_at=(NOW - timedelta(hours=4)).isoformat())
    row["sweep_outcome"] = td.grade_sweep_outcome(row)
    a = A.assess(_receipt(row), "read", now=NOW)
    assert a["outcome"] == td.OUTCOME_DELIVERED
    assert a["sweep_stale"] is True
    assert "STOPPED" in A.describe(a)


def test_a_fresh_receipt_is_not_stale():
    a = A.assess(_receipt(_run()), "read", now=NOW)
    assert a["sweep_stale"] is False


def test_an_undateable_row_reports_staleness_as_unknown_not_false():
    a = A.assess(_receipt(_run(run_at=None)), "read", now=NOW)
    assert a["sweep_stale"] is None      # we could not look, not "it is fresh"


# ── the two faults take DIFFERENT remedies ──────────────────────────────────

def test_all_failed_and_all_held_name_disjoint_remedies():
    """⚠️ THE PAIR THAT MUST NOT COLLAPSE. Both deliver nothing. A refused send
    is fixed in the send path; a held one is fixed by configuration. A session
    told only "nothing went out" chases the wrong one."""
    failed = A.describe(A.assess(_receipt(_run()), "read", now=NOW))
    held_row = _run(candidates=4, failed=0, held_not_polled=4)
    held_row["sweep_outcome"] = td.grade_sweep_outcome(held_row)
    held = A.describe(A.assess(_receipt(held_row), "read", now=NOW))

    assert "send path" in failed and "reached nobody" in failed
    assert "CONFIGURATION" in held
    assert "ict-claude-decision-bot.service" in held
    assert "ict-claude-decision-bot.service" not in failed
    assert failed != held


def test_the_page_names_the_typed_cause_so_it_can_be_acted_on():
    body = A.describe(A.assess(_receipt(_run()), "read", now=NOW))
    assert "too_long" in body
    assert "work_decision_sweep_receipt" in body   # where to read more


def test_nothing_pending_is_reported_as_an_empty_denominator_not_as_health():
    row = _run(candidates=0, failed=0)
    row["sweep_outcome"] = td.grade_sweep_outcome(row)
    body = A.describe(A.assess(_receipt(row), "read", now=NOW))
    assert "EMPTY DENOMINATOR" in body


def test_not_graded_says_we_did_not_look():
    body = A.describe(A.assess({}, A.RECEIPT_UNREADABLE, now=NOW))
    assert "did not look" in body
    assert "healthy" in body          # explicitly denies health


@pytest.mark.parametrize("outcome", td.SWEEP_OUTCOMES)
def test_every_outcome_renders_a_distinct_body(outcome):
    a = A.assess(_receipt(_run()), "read", now=NOW)
    a["outcome"] = outcome
    body = A.describe(a)
    assert body and len(body) > 40


# ── the alert routing ───────────────────────────────────────────────────────

def _check(monkeypatch, row, *, admits=True, skip=False):
    monkeypatch.setattr(A, "read_sweep_receipt",
                        lambda path=None: (_receipt(row), "read"))
    monkeypatch.setattr(A._alert_cooldown, "cooldown_admits",
                        lambda *a, **k: admits)
    if skip:
        monkeypatch.setenv("DECISION_CHANNEL_ALERT_SKIP", "1")
    else:
        monkeypatch.delenv("DECISION_CHANNEL_ALERT_SKIP", raising=False)
    sent = []
    a = A.run_decision_channel_check(now=NOW, alert=sent.append)
    return a, sent


def test_a_dead_channel_pages(monkeypatch):
    a, sent = _check(monkeypatch, _run())
    assert a["alerted"] is True and a["alert_disposition"] == "alerting"
    assert len(sent) == 1 and "DELIVERING NOTHING" in sent[0]


def test_the_latch_suppresses_a_standing_condition(monkeypatch):
    """At a 300s cadence an un-latched page on four stuck decisions is ~12 an
    hour — the desensitised-alarm P1 this repo names in its own right."""
    a, sent = _check(monkeypatch, _run(), admits=False)
    assert a["alerted"] is False
    assert a["alert_disposition"] == "suppressed_cooldown"
    assert sent == []


def test_severity_is_the_undelivered_count_so_a_worsening_breaks_the_window(
        monkeypatch):
    seen = {}

    def spy(kind, key, cooldown, **kw):
        seen["severity"] = kw.get("severity")
        return True

    monkeypatch.setattr(A, "read_sweep_receipt",
                        lambda path=None: (_receipt(_run(candidates=5,
                                                         failed=5)), "read"))
    monkeypatch.setattr(A._alert_cooldown, "cooldown_admits", spy)
    A.run_decision_channel_check(now=NOW, alert=lambda m: None)
    assert seen["severity"] == 5


def test_a_healthy_channel_is_quiet_and_says_which_kind_of_quiet(monkeypatch):
    row = _run(candidates=2, failed=0, prompted_choice=2)
    row["sweep_outcome"] = td.grade_sweep_outcome(row)
    a, sent = _check(monkeypatch, row)
    assert sent == [] and a["alerted"] is False
    assert a["alert_disposition"] == "healthy_delivered"


def test_an_empty_inbox_is_quiet_but_NOT_recorded_as_healthy(monkeypatch):
    """`nothing_pending` says nothing about whether a send would have worked,
    so its disposition must be distinguishable from a proven delivery."""
    row = _run(candidates=0, failed=0)
    row["sweep_outcome"] = td.grade_sweep_outcome(row)
    a, sent = _check(monkeypatch, row)
    assert sent == []
    assert a["alert_disposition"] == "empty_denominator"
    assert a["alert_disposition"] != "healthy_delivered"


def test_a_paused_channel_does_not_page_but_is_graded_apart_from_healthy(
        monkeypatch):
    """A pause is the operator's own declared choice
    (WORK_DECISION_PROMPT_SECONDS <= 0); paging every cadence for a condition
    nobody intends to change is the desensitised alarm."""
    row = _run(checked=False, paused=True, candidates=0, failed=0)
    row["sweep_outcome"] = td.grade_sweep_outcome(row)
    a, sent = _check(monkeypatch, row)
    assert sent == []
    assert a["alert_disposition"] == "ungraded_not_paged"


def test_a_frozen_sweep_pages_even_though_its_outcome_is_ungraded(monkeypatch):
    """The one case where `not_graded` IS loud: a receipt that has stopped
    advancing means the reader itself has gone blind."""
    row = _run(checked=False, paused=True, candidates=0, failed=0,
               run_at=(NOW - timedelta(hours=6)).isoformat())
    row["sweep_outcome"] = td.grade_sweep_outcome(row)
    a, sent = _check(monkeypatch, row)
    assert a["alerted"] is True and len(sent) == 1


def test_skip_scopes_the_page_and_never_the_measurement(monkeypatch):
    """The allowlist discipline this repo holds to: the verdict is still
    GRADED and still readable, so the rows a reviewer needs exist."""
    a, sent = _check(monkeypatch, _run(), skip=True)
    assert sent == []
    assert a["alert_disposition"] == "paging_disabled"
    assert a["outcome"] == td.OUTCOME_ALL_FAILED       # still measured
    assert a["failing"] == 4


def test_alert_disposition_never_collapses_into_a_bare_boolean(monkeypatch):
    """`alerted: False` collapses four facts — paged, latched, not a finding,
    and switched off. The correction `silent_refusal_alert` needed."""
    seen = set()
    for kw, row in [
        ({}, _run()),
        ({"admits": False}, _run()),
        ({"skip": True}, _run()),
        ({}, _run(candidates=2, failed=0, prompted_choice=2,
                  sweep_outcome=td.OUTCOME_DELIVERED)),
        ({}, _run(candidates=0, failed=0,
                  sweep_outcome=td.OUTCOME_NOTHING_PENDING)),
    ]:
        a, _ = _check(monkeypatch, row, **kw)
        seen.add(a["alert_disposition"])
    assert len(seen) == 5, seen


# ── the knobs ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["", "abc", "not-a-number"])
def test_an_unparseable_knob_falls_back_to_the_default_never_to_zero(
        monkeypatch, raw):
    """A typo must not silently switch the only reader of this channel off."""
    monkeypatch.setenv("DECISION_CHANNEL_ALERT_COOLDOWN_HOURS", raw)
    assert A.cooldown_seconds() == A._DEFAULT_COOLDOWN_HOURS * 3600.0
    monkeypatch.setenv("DECISION_CHANNEL_ALERT_STALE_MINUTES", raw)
    assert A.stale_seconds() == A._DEFAULT_STALE_MINUTES * 60.0


def test_the_knobs_are_honoured_when_they_parse(monkeypatch):
    monkeypatch.setenv("DECISION_CHANNEL_ALERT_COOLDOWN_HOURS", "2")
    assert A.cooldown_seconds() == 7200.0
    monkeypatch.setenv("DECISION_CHANNEL_ALERT_STALE_MINUTES", "45")
    assert A.stale_seconds() == 2700.0


# ── the latch is the SHARED one, and inspectable ────────────────────────────

def test_the_latch_is_the_shared_durable_primitive_not_a_copy():
    """A per-process `time.monotonic()` latch is the defect that put 202
    CRITICALs on the operator's channel
    (BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART)."""
    assert A._alert_cooldown is ac
    assert ac.state_path(A.ALERT_KIND).name == "decision_channel_alert_state.json"


def test_the_latch_file_is_readable_over_diag():
    """A latch that suppresses an operator page and cannot be inspected is
    worse than no latch — the exit_loop_health #8778 lesson.

    ⚠️ SKIPS rather than passes where fastapi is absent, so a sandbox without
    the web deps reports *we could not look* instead of a green tick. The
    companion coherence test (``test_every_allowlisted_log_file_is_documented``)
    covers the doc half.
    """
    pytest.importorskip("fastapi")
    from src.web.api.routers.diag import _LOG_FILES
    assert "decision_channel_alert_state" in _LOG_FILES


def test_status_never_raises_and_always_names_a_declared_outcome(monkeypatch):
    monkeypatch.setattr(A, "read_sweep_receipt",
                        lambda path=None: (_ for _ in ()).throw(OSError("x")))
    s = A.status()
    assert s["outcome"] in td.SWEEP_OUTCOMES


def test_a_check_whose_read_explodes_is_reported_not_swallowed(monkeypatch):
    monkeypatch.setattr(A, "read_sweep_receipt",
                        lambda path=None: (_ for _ in ()).throw(OSError("x")))
    a = A.run_decision_channel_check(now=NOW, alert=lambda m: None)
    assert a["alert_disposition"] == "check_failed"
    assert a["outcome"] == td.OUTCOME_NOT_GRADED
