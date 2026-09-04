"""MI-109 — the decision sweep reported a confirmed send and recorded nowhere
that it went.

Two defects, deliberately NOT collapsed, and this file pins one claim per half:

(a) THE MARKER RECORDED NO DESTINATION. Its entry was
    ``{prompted_at, object_id, request_id, kind}``, while ``answerable_route()``
    may legitimately fall back to the TRADER bot — so a prompt delivered to the
    wrong chat was PERMANENTLY CONSUMED by a once-only marker and
    unattributable afterwards. The tests below assert the destination is
    recorded, that it names the token VARIABLE and never a value, and that a
    prompt which did NOT reach the preferred destination can be re-sent
    EXACTLY ONCE once routing is fixed.

(b) THE ONE LINE NAMING THE DESTINATION EVAPORATED. It was logged, to the
    systemd journal only, whose measured retention is ~30 minutes — the
    14:05:48 send was already unreachable 45 minutes later. The tests below
    assert a durable receipt is stamped on EVERY outcome (including the early
    returns), that it is a bounded ring rather than one slot, and that it is
    allowlisted on the diag read surface.

⚠️ WHAT IS DELIBERATELY *NOT* ASSERTED HERE, because the work object rules it
out: that the sweep re-sends unconditionally. Three suppressing verdicts are
each pinned below precisely because an unconditional re-send would spam a
question the operator may already have answered.

⚠️ AND A GREEN RUN OF THIS FILE IS NOT EVIDENCE THE FLEET IS FIXED. A harness
cannot reach the VM, and arming this needs an ``ict-telegram-bot.service``
restart (Tier-2). The honest state until a prompt is OBSERVED carrying its
destination is `landed_unproven`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.runtime import telegram_decisions as td
from src.runtime import telegram_poll_registry as poll_registry


# ── fixtures ────────────────────────────────────────────────────────────────

def _request(**over):
    base = {
        "id": "DEC-20260903-SUNSET-DISPOSITION-POLICY",
        "objectId": "WO-20260903-SUNSET",
        "objectTitle": "Sunset disposition policy",
        "question": "Retire the ten candidates, or hold them?",
        "options": [
            {"key": "retire", "label": "Retire", "implication": "irreversible"},
            {"key": "hold", "label": "Hold", "implication": "keeps trading"},
        ],
        "allowsFreeText": False,
        "urgency": "blocking",
        "context": None,
        "answer": None,
        "answerState": "not_submitted",
    }
    base.update(over)
    return base


def _inbox(requests, *, write_open=True):
    return {
        "present": True,
        "requests": requests,
        "writeGate": {"state": "open" if write_open else "closed_no_token",
                      "acceptsWrites": write_open},
    }


def _sender(sent, *, ok=True):
    def send(text, keyboard):
        sent.append((text, keyboard))
        return ok
    return send


def _route(destination, *, state=poll_registry.POLLED_WITH_HANDLER):
    """A hand-built route. ``token`` is a value; nothing may persist it."""
    token_from = ("TELEGRAM_CLAUDE_BOT_SECRET"
                  if destination == "claude" else "TELEGRAM_BOT_TOKEN")
    return td.AnswerableRoute(
        token="SUPER-SECRET-BOT-TOKEN-VALUE",
        token_from=token_from,
        chat_id="365546917",
        chat_from="TELEGRAM_CHAT_ID",
        note="hand-built for a test",
        poll=poll_registry.PollEvidence(
            state=state, token_var=token_from, prefix=td.CB_PREFIX,
            note="test", source="test"),
        destination=destination,
    )


@pytest.fixture
def routed(monkeypatch):
    """Pin ``answerable_route`` so a test states the destination it means."""
    def _set(destination, **kw):
        monkeypatch.setattr(td, "answerable_route",
                            lambda: _route(destination, **kw))
    return _set


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "trader-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "365546917")


def _marker(state, req, key=None):
    return json.loads((state).read_text())["prompted"][
        key or td.marker_key(req["objectId"], req["id"])]


# ═════════════════════════════════════════════════════════════════════════════
# (a) the marker records the DESTINATION
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("destination", ["claude", "trader_fallback"])
def test_marker_records_where_the_prompt_actually_went(
        tmp_path, monkeypatch, routed, destination):
    """The defect verbatim: a confirmed send that records nowhere it went."""
    req = _request()
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([req]), None))
    routed(destination)
    state = tmp_path / "prompted.json"

    td.run_decision_prompt_sweep(sender=_sender([]), state_path=state)

    row = _marker(state, req)
    assert row["destination"] == destination
    assert row["poll_state"] == poll_registry.POLLED_WITH_HANDLER
    # Which VARIABLE answered — enough to attribute the send afterwards.
    assert row["token_from"] == (
        "TELEGRAM_CLAUDE_BOT_SECRET" if destination == "claude"
        else "TELEGRAM_BOT_TOKEN")
    assert row["chat_from"] == "TELEGRAM_CHAT_ID"


def test_marker_never_persists_a_token_VALUE(tmp_path, monkeypatch, routed):
    """`never a token value` is a done_condition clause, so it is asserted.

    The marker file is allowlisted on /api/diag/log_file, so a token landing in
    it would be a credential on a read surface.
    """
    req = _request()
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([req]), None))
    routed("claude")
    state = tmp_path / "prompted.json"
    td.run_decision_prompt_sweep(sender=_sender([]), state_path=state)

    raw = state.read_text()
    assert "SUPER-SECRET-BOT-TOKEN-VALUE" not in raw
    assert "trader-token" not in raw
    # The chat id is not a secret, but the marker names the variable anyway.
    assert "TELEGRAM_CLAUDE_BOT_SECRET" in raw


def test_marker_destination_state_keeps_unrecorded_apart_from_both_others():
    """`we did not look` is a THIRD state, not a synonym for either verdict."""
    assert td.marker_destination_state(
        {"destination": "claude"}) == td.MARKER_REACHED_PREFERRED
    assert td.marker_destination_state(
        {"destination": "trader_fallback"}) == td.MARKER_WRONG_DESTINATION
    assert td.marker_destination_state(
        {"destination": "none"}) == td.MARKER_WRONG_DESTINATION
    # A marker written before this field existed — the live population.
    assert td.marker_destination_state(
        {"prompted_at": "2026-09-03T14:05:48.798642Z"}
    ) == td.MARKER_DESTINATION_UNRECORDED
    assert td.marker_destination_state(
        {"destination": "   "}) == td.MARKER_DESTINATION_UNRECORDED
    assert td.marker_destination_state(None) == td.MARKER_DESTINATION_UNRECORDED


# ── the re-send, and its three hard stops ───────────────────────────────────

@pytest.mark.parametrize("prior,reason", [
    ({"destination": "trader_fallback"}, "redeliver_wrong_destination"),
    ({"prompted_at": "2026-09-03T14:05:48Z"}, "redeliver_unrecorded_destination"),
])
def test_a_prompt_that_missed_the_preferred_bot_is_re_sent_once(
        tmp_path, monkeypatch, routed, prior, reason):
    req = _request()
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([req]), None))
    state = tmp_path / "prompted.json"
    key = td.marker_key(req["objectId"], req["id"])
    row = {"object_id": req["objectId"], "request_id": req["id"],
           "kind": "choice", "prompted_at": "2026-09-03T14:05:48Z"}
    row.update(prior)
    td.write_prompt_state({key: row}, state)

    routed("claude")
    sent = []
    s1 = td.run_decision_prompt_sweep(sender=_sender(sent), state_path=state)
    assert s1["redelivered"] == 1 and len(sent) == 1

    stored = _marker(state, req)
    assert stored["destination"] == "claude"
    assert stored["redelivered_count"] == 1
    assert stored["redelivery_reason"] == reason
    # It records what it was rescued FROM, so the re-send is itself
    # attributable rather than reading like a first ask.
    assert stored["redelivered_from"] == td.marker_destination_state(row)
    # ⚠️ The ORIGINAL ask time is preserved. Overwriting it would erase the
    # only record of when the operator first should have seen the question.
    assert stored["prompted_at"] == "2026-09-03T14:05:48Z"

    # AND ONCE MEANS ONCE — this is the bound that keeps it from being the
    # unconditional re-send the work object rules out.
    s2 = td.run_decision_prompt_sweep(sender=_sender(sent), state_path=state)
    assert s2["suppressed_already_redelivered"] == 1
    assert s2["redelivered"] == 0 and len(sent) == 1


def test_a_prompt_that_reached_the_preferred_bot_is_never_re_sent(
        tmp_path, monkeypatch, routed):
    req = _request()
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([req]), None))
    state = tmp_path / "prompted.json"
    key = td.marker_key(req["objectId"], req["id"])
    td.write_prompt_state({key: {"destination": "claude"}}, state)

    routed("claude")
    sent = []
    stats = td.run_decision_prompt_sweep(sender=_sender(sent), state_path=state)
    assert stats["suppressed_reached_preferred"] == 1
    assert sent == [] and stats["redelivered"] == 0


def test_no_re_send_while_the_route_is_still_the_wrong_one(
        tmp_path, monkeypatch, routed):
    """Re-asking on the same fallback would consume the ONE retry in the same
    wrong chat. It must stay pending until routing actually improves."""
    req = _request()
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([req]), None))
    state = tmp_path / "prompted.json"
    key = td.marker_key(req["objectId"], req["id"])
    td.write_prompt_state({key: {"destination": "trader_fallback"}}, state)

    routed("trader_fallback")
    sent = []
    stats = td.run_decision_prompt_sweep(sender=_sender(sent), state_path=state)
    assert stats["suppressed_route_not_better"] == 1
    assert sent == []
    # Still eligible — the retry was NOT spent.
    assert _marker(state, req).get("redelivered_count") is None

    routed("claude")
    stats = td.run_decision_prompt_sweep(sender=_sender(sent), state_path=state)
    assert stats["redelivered"] == 1 and len(sent) == 1


def test_a_failed_re_send_does_not_spend_the_one_retry(
        tmp_path, monkeypatch, routed):
    """`flip only on a confirmed send`, applied to the redelivery too."""
    req = _request()
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([req]), None))
    state = tmp_path / "prompted.json"
    key = td.marker_key(req["objectId"], req["id"])
    td.write_prompt_state({key: {"destination": "trader_fallback"}}, state)

    routed("claude")
    stats = td.run_decision_prompt_sweep(
        sender=_sender([], ok=False), state_path=state)
    assert stats["failed"] == 1 and stats["redelivered"] == 0
    assert _marker(state, req).get("redelivered_count") is None

    sent = []
    stats = td.run_decision_prompt_sweep(sender=_sender(sent), state_path=state)
    assert stats["redelivered"] == 1 and len(sent) == 1


def test_an_answered_request_is_never_re_sent_however_it_was_delivered(
        tmp_path, monkeypatch, routed):
    """The re-send rides the SAME `not_submitted` gate as a first ask, so it
    can never re-ask a question the operator has already answered."""
    state = tmp_path / "prompted.json"
    req = _request(answerState="committed")
    key = td.marker_key(req["objectId"], req["id"])
    td.write_prompt_state({key: {"destination": "trader_fallback"}}, state)
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([req]), None))
    routed("claude")

    sent = []
    stats = td.run_decision_prompt_sweep(sender=_sender(sent), state_path=state)
    assert sent == [] and stats["redelivered"] == 0


# ═════════════════════════════════════════════════════════════════════════════
# (b) the per-run stats land on a DURABLE surface
# ═════════════════════════════════════════════════════════════════════════════

def test_receipt_records_the_destination_the_journal_used_to_own(
        tmp_path, monkeypatch, routed):
    req = _request()
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([req]), None))
    routed("trader_fallback")
    state = tmp_path / "prompted.json"

    td.run_decision_prompt_sweep(sender=_sender([]), state_path=state)

    receipt, read_state = td.read_sweep_receipt(
        tmp_path / "work_decision_sweep_receipt.json")
    assert read_state == "read"
    last = receipt["last"]
    assert last["destination"] == "trader_fallback"
    assert last["poll_state"] == poll_registry.POLLED_WITH_HANDLER
    assert last["token_from"] == "TELEGRAM_BOT_TOKEN"
    assert last["prompted_choice"] == 1
    assert last["run_at"]


@pytest.mark.parametrize("setup,expect", [
    # PAUSED — an early return, and a state the operator has been in.
    ("paused", lambda r: r["paused"] is True),
    # INBOX UNREADABLE — the sweep cannot even see the questions.
    ("inbox", lambda r: "inbox unreadable" in (r["reason"] or "")),
    # PROMPT-STATE UNREADABLE — we did not look, so we held.
    ("state", lambda r: r["prompt_state_read"] == "unreadable"),
])
def test_receipt_is_stamped_on_every_outcome_not_only_on_a_send(
        tmp_path, monkeypatch, setup, expect):
    """A receipt written only on success cannot tell a DEAD sweep from a
    FAILING one — the `work_digest_receipt` lesson, applied to the early
    returns that used to leave nothing behind at all."""
    state = tmp_path / "prompted.json"
    if setup == "paused":
        monkeypatch.setenv("WORK_DECISION_PROMPT_SECONDS", "0")
    elif setup == "inbox":
        monkeypatch.setattr(td, "fetch_inbox", lambda: (None, "connection refused"))
    else:
        monkeypatch.setattr(
            td, "fetch_inbox", lambda: (_inbox([_request()]), None))
        state.write_text("{ not json")

    td.run_decision_prompt_sweep(sender=_sender([]), state_path=state)

    receipt, read_state = td.read_sweep_receipt(
        tmp_path / "work_decision_sweep_receipt.json")
    assert read_state == "read", f"{setup} left NO durable record"
    assert expect(receipt["last"])


def test_receipt_is_stamped_even_when_the_sweep_raises(tmp_path, monkeypatch):
    def _boom():
        raise RuntimeError("inbox exploded")
    monkeypatch.setattr(td, "fetch_inbox", _boom)

    td.run_decision_prompt_sweep(
        sender=_sender([]), state_path=tmp_path / "prompted.json")

    receipt, read_state = td.read_sweep_receipt(
        tmp_path / "work_decision_sweep_receipt.json")
    assert read_state == "read"
    assert "inbox exploded" in receipt["last"]["reason"]


def test_receipt_is_a_bounded_ring_not_one_slot(tmp_path, monkeypatch, routed):
    """⚠️ THE RING IS LOAD-BEARING. This sweep fires every 300s, so a one-slot
    receipt would retain FIVE MINUTES — worse than the ~30 of journal it
    replaces, and the MI-109 send was needed 45 minutes later."""
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([]), None))
    routed("claude")
    state = tmp_path / "prompted.json"
    receipt_p = tmp_path / "work_decision_sweep_receipt.json"

    for _ in range(td._RECEIPT_RUNS_KEPT + 25):
        td.run_decision_prompt_sweep(sender=_sender([]), state_path=state)

    receipt, _ = td.read_sweep_receipt(receipt_p)
    assert len(receipt["runs"]) == td._RECEIPT_RUNS_KEPT
    assert receipt["runs"][-1] == receipt["last"]
    # Many runs are retained, so history outlives the cadence.
    assert td._RECEIPT_RUNS_KEPT > 1

    # At the default 300s cadence the window must beat the journal's ~30 min.
    window_minutes = td._RECEIPT_RUNS_KEPT * td._DEFAULT_PROMPT_SECONDS / 60.0
    assert window_minutes > 45, (
        f"{window_minutes:.0f} min of history does not outlive the 45-minute "
        "gap MI-109 measured between the send and anyone looking for it")


def test_a_receipt_write_failure_never_takes_the_sweep_down(
        tmp_path, monkeypatch, routed):
    """Observability must not become an outage — the prop-prompt contract."""
    req = _request()
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([req]), None))
    routed("claude")
    sent = []
    stats = td.run_decision_prompt_sweep(
        sender=_sender(sent), state_path=tmp_path / "prompted.json",
        # A directory is not a writable file — os.replace raises.
        receipt_path=tmp_path,
    )
    assert stats["prompted_choice"] == 1 and len(sent) == 1


def test_read_sweep_receipt_keeps_absent_apart_from_unreadable(tmp_path):
    """`nothing has run` and `we could not look` are opposite facts."""
    assert td.read_sweep_receipt(tmp_path / "nope.json") == ({}, "absent")
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    assert td.read_sweep_receipt(bad)[1] == "unreadable"


def test_receipt_is_allowlisted_on_the_diag_read_surface():
    """⚠️ A state file that ships WITHOUT a read surface is the recurrence
    BL-20260825-ALERT-AND-CADENCE-STATE-FILES-SHIP-WITHOUT-A-READ-SURFACE.
    The allowlist entry ships in the SAME commit as the writer.

    Read as TEXT, following `test_work_digest_hourly_carrier`: importing
    `diag` drags in fastapi, and the claim here is about the allowlist table,
    not about the app booting.
    """
    diag = Path("src/web/api/routers/diag.py").read_text(encoding="utf-8")
    assert '"work_decision_sweep_receipt": _WORK_DECISION_SWEEP_RECEIPT' in diag
    # ⚠️ The reader must name the WRITER's real path or it reports an
    # eternally-absent file (BL-20260611-M15-2). The writer resolves through
    # runtime_logs_dir(), exactly as its sibling marker does.
    assert (
        '_WORK_DECISION_SWEEP_RECEIPT = (\n'
        '    runtime_logs_dir() / "work_decision_sweep_receipt.json"\n'
        ')'
    ) in diag
    assert '_WORK_DECISION_PROMPTED_STATE = runtime_logs_dir() /' in diag
    # And the basename the reader names is the one the writer writes.
    assert td._SWEEP_RECEIPT_BASENAME == "work_decision_sweep_receipt.json"


@pytest.mark.parametrize("state,fragment", [
    (poll_registry.TOKEN_ONLY_NOT_POLLED, "no process is polling it"),
    (poll_registry.UNKNOWN, "could NOT be read"),
])
def test_a_held_run_records_WHY_it_held_durably(
        tmp_path, monkeypatch, routed, state, fragment, caplog):
    """The two non-answerable poll states take DIFFERENT operator actions —
    `token_only_not_polled` is fixed by STARTING A SERVICE, `unknown` by
    looking at the VM's disk — and until MI-109 the only place that
    distinction survived was the systemd journal, i.e. ~30 minutes.

    Held runs send nothing, so they were the runs that left NO trace at all.
    The receipt must carry the state, and it must carry the right one.
    """
    req = _request()
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([req]), None))
    routed("claude", state=state)
    sent = []
    monkeypatch.setattr(td, "_default_sender", _sender(sent))

    # sender=None so the real poll gate applies, per the sweep's own contract.
    with caplog.at_level("WARNING"):
        stats = td.run_decision_prompt_sweep(
            sender=None, state_path=tmp_path / "prompted.json")

    assert stats["held_not_polled"] == 1
    # ⚠️ NOT pooled with `held_route`: "nowhere to send" and "somewhere to send
    # whose buttons are dead" are different faults with different fixes.
    assert stats["held_route"] == 0
    assert sent == []
    assert fragment in caplog.text

    receipt, read_state = td.read_sweep_receipt(
        tmp_path / "work_decision_sweep_receipt.json")
    assert read_state == "read"
    assert receipt["last"]["poll_state"] == state
    assert receipt["last"]["held_not_polled"] == 1


# ═════════════════════════════════════════════════════════════════════════════
# what MUST NOT have changed
# ═════════════════════════════════════════════════════════════════════════════

def test_answerable_route_fallback_policy_is_untouched(monkeypatch):
    """⚠️ A recorded decision, not an oversight: whether a wrong-chat send
    beats holding was already decided. This object is about SEEING which
    happened. The trader fallback must still fire."""
    monkeypatch.delenv("TELEGRAM_CLAUDE_BOT_SECRET", raising=False)
    monkeypatch.setattr(
        poll_registry, "poll_state",
        lambda var, prefix=None: poll_registry.PollEvidence(
            state=poll_registry.POLLED_WITH_HANDLER, token_var=var,
            prefix=prefix, note="test", source="test"))
    monkeypatch.setattr(
        td, "poll_state",
        lambda var, prefix=None: poll_registry.PollEvidence(
            state=poll_registry.POLLED_WITH_HANDLER, token_var=var,
            prefix=prefix, note="test", source="test"))
    route = td.answerable_route()
    assert route.destination == "trader_fallback"
    assert route.deliverable


def test_the_on_demand_pull_still_neither_reads_nor_writes_the_marker():
    """⚠️ `/decisions` is the working escape hatch and the only reason MI-109
    was recoverable. Making it read the marker is explicitly forbidden — not
    reading is the point of the operator's ask (it must re-send what the sweep
    already consumed), and not WRITING keeps the sweep's once-only behaviour
    intact.

    Scoped to the two on-demand functions by source, not to the whole module:
    the module's own `__all__` names these helpers, and a substring scan over
    the file would fire on that and prove nothing.
    """
    import inspect

    pull = "".join(
        inspect.getsource(fn) for fn in
        (td.build_on_demand_decisions, td.render_decisions_summary)
    )
    for forbidden in ("read_prompt_state", "write_prompt_state",
                      "marker_key", "redelivery_verdict",
                      "marker_destination_state", "prompt_state_path"):
        assert forbidden not in pull, (
            f"the /decisions pull now touches {forbidden} — it must not")
