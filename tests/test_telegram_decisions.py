"""Tests for the Telegram half of the decision round-trip.

The claims worth pinning are the ones a live test could not cheaply re-check:

* ``callback_data`` NEVER exceeds Telegram's 64-byte cap, for ANY id length.
* the button carries a digest of the option KEY, so reordering or renaming the
  options fails LOUDLY instead of silently selecting a different answer.
* the confirmation after a tap says ``submitted, not decided`` and NEVER says
  committed/decided/answered — the forward failure the transit contract refuses.
* every refusal path states that nothing was submitted, and the one genuinely
  ambiguous case (no HTTP response) says it does NOT know.
* the sweep asks once, only for `not_submitted`, and HOLDS rather than sending
  buttons that would 503.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.runtime import telegram_decisions as td


# ── fixtures ────────────────────────────────────────────────────────────────

def _request(**over):
    base = {
        "id": "DEC-20260901-READ-GATE-SEQUENCING",
        "objectId": "WO-20260901-PHASE-H",
        "objectTitle": "Phase H — the control half",
        "question": "Build the read gate now, or wait for the retirement?",
        "options": [
            {"key": "build_now", "label": "Build it now", "implication": "risk"},
            {"key": "wait", "label": "Wait", "implication": "slower"},
        ],
        "allowsFreeText": True,
        "urgency": "blocking",
        "context": None,
        "answer": None,
        "answerState": "not_submitted",
    }
    base.update(over)
    return base


# ── the 64-byte bound ───────────────────────────────────────────────────────

@pytest.mark.parametrize("n", [1, 10, 64, 200, 2000])
def test_callback_data_is_always_under_the_telegram_cap(n):
    data = td.encode_callback("O" * n, "R" * n, "K" * n)
    assert len(data.encode("utf-8")) <= td.TELEGRAM_CALLBACK_DATA_MAX_BYTES
    # Fixed width, not merely "small enough for these ids".
    assert len(data.encode("utf-8")) == 26


def test_real_world_ids_round_trip_through_the_digests():
    req = _request()
    data = td.encode_callback(req["objectId"], req["id"], "build_now")
    decoded = td.decode_callback(data)
    assert decoded == (
        td.request_digest(req["objectId"], req["id"]),
        td.option_digest("build_now"),
    )


def test_ids_are_nul_joined_so_concatenation_cannot_collide():
    # ("ab","c") and ("a","bc") concatenate identically; they must not digest so.
    assert td.request_digest("ab", "c") != td.request_digest("a", "bc")


def test_decode_ignores_foreign_callbacks():
    for foreign in ("propexp:y:t1", "prop:prompt", "menu:home", "", "wdec", "wdec:x"):
        assert td.decode_callback(foreign) is None


def test_decode_rejects_malformed_digests():
    assert td.decode_callback("wdec:short:005c2659") is None
    assert td.decode_callback("wdec:1f05747131ca:zzzzzzzz") is None
    assert td.decode_callback("wdec:1f05747131ca:005c2659:extra") is None


# ── option identity is a KEY digest, not an index ───────────────────────────

def test_reordering_options_does_not_change_which_option_a_button_names():
    req = _request()
    data = td.encode_callback(req["objectId"], req["id"], "wait")
    rd, od = td.decode_callback(data)

    reordered = _request(options=list(reversed(req["options"])))
    res = td.resolve_callback([reordered], rd, od)
    assert res.outcome == ""
    # A positional index would have selected "build_now" here.
    assert res.option["key"] == "wait"


def test_a_removed_option_fails_loudly_rather_than_selecting_a_neighbour():
    req = _request()
    rd, od = td.decode_callback(td.encode_callback(req["objectId"], req["id"], "wait"))
    edited = _request(options=[{"key": "build_now", "label": "Build it now"}])
    res = td.resolve_callback([edited], rd, od)
    assert res.outcome == td.OPTION_GONE
    assert res.option is None


def test_a_missing_request_is_request_gone():
    rd, od = td.decode_callback(td.encode_callback("O", "R", "K"))
    assert td.resolve_callback([_request()], rd, od).outcome == td.REQUEST_GONE


def test_two_requests_behind_one_button_are_refused_not_guessed():
    req = _request()
    rd, od = td.decode_callback(
        td.encode_callback(req["objectId"], req["id"], "wait"))
    # Same identity twice — the ambiguity a first-match resolver would hide.
    res = td.resolve_callback([req, _request()], rd, od)
    assert res.outcome == td.AMBIGUOUS
    assert res.request is None and res.option is None


def test_two_options_with_the_same_digest_are_refused():
    req = _request(options=[
        {"key": "wait", "label": "A"},
        {"key": "wait", "label": "B"},
    ])
    rd, od = td.decode_callback(
        td.encode_callback(req["objectId"], req["id"], "wait"))
    assert td.resolve_callback([req], rd, od).outcome == td.AMBIGUOUS


# ── keyboards ───────────────────────────────────────────────────────────────

def test_keyboard_has_one_button_per_declared_option_and_no_option_text_in_data():
    kb = td.build_decision_keyboard(_request())
    rows = kb["inline_keyboard"]
    assert [r[0]["text"] for r in rows] == ["Build it now", "Wait"]
    for row in rows:
        data = row[0]["callback_data"]
        assert data.startswith("wdec:")
        assert "build_now" not in data and "wait" not in data
        assert len(data.encode("utf-8")) <= td.TELEGRAM_CALLBACK_DATA_MAX_BYTES


def test_a_request_with_no_options_gets_no_keyboard():
    # An empty keyboard would render a question that looks answerable.
    assert td.build_decision_keyboard(_request(options=[])) is None


def test_an_option_without_a_key_is_dropped_not_synthesised():
    kb = td.build_decision_keyboard(_request(options=[
        {"key": "wait", "label": "Wait"},
        {"label": "no key here"},
    ]))
    assert len(kb["inline_keyboard"]) == 1


# ── the prompt text ─────────────────────────────────────────────────────────

def test_prompt_states_the_question_the_options_and_the_ids():
    text = td.render_decision_prompt(_request())
    assert "Build the read gate now" in text
    assert "Build it now" in text and "Wait" in text
    assert "WO-20260901-PHASE-H" in text
    assert "DEC-20260901-READ-GATE-SEQUENCING" in text


def test_a_free_text_only_request_says_so_instead_of_pretending_to_be_tappable():
    text = td.render_decision_prompt(_request(options=[]))
    assert "FREE-TEXT" in text
    assert "Tap an option" not in text


# ── the confirmation must NOT overstate the state ───────────────────────────

_FORBIDDEN = ("committed", "decided.", "is now the decision", "answered!")


def test_success_reply_says_submitted_not_decided():
    reply = td.render_callback_reply(
        td.SUBMITTED, request=_request(), option={"key": "wait", "label": "Wait"})
    assert "Submitted" in reply
    assert "NOT yet decided" in reply
    assert "UNANSWERED" in reply
    assert "docs/claude/work/objects/WO-20260901-PHASE-H.yaml" in reply
    low = reply.lower()
    assert "committed" not in low


@pytest.mark.parametrize("outcome", [
    td.ALREADY_ANSWERED, td.OPTION_GONE, td.REQUEST_GONE, td.AMBIGUOUS,
    td.WRITE_CLOSED, td.UNAUTHORIZED, td.NOT_PERSISTED, td.REFUSED,
    td.INBOX_UNREADABLE,
])
def test_every_refusal_states_that_nothing_was_submitted(outcome):
    reply = td.render_callback_reply(outcome, request=_request())
    assert "nothing was submitted" in reply.lower()


def test_unknown_does_not_claim_either_way():
    reply = td.render_callback_reply(td.UNKNOWN, request=_request())
    low = reply.lower()
    # The one case where we genuinely cannot say. Claiming "nothing was
    # submitted" here would be manufacturing a certainty nobody has.
    assert "nothing was submitted" not in low
    assert "do not know" in low
    assert "duplicate" in low


def test_already_answered_points_at_the_repo_not_at_a_retry():
    reply = td.render_callback_reply(td.ALREADY_ANSWERED, request=_request())
    assert "Already answered" in reply
    assert "docs/claude/work/objects/WO-20260901-PHASE-H.yaml" in reply


def test_every_declared_outcome_renders_something():
    for outcome in td.CALLBACK_OUTCOMES:
        assert td.render_callback_reply(outcome, request=_request()).strip()


# ── submit_answer maps the route's OWN refusals ─────────────────────────────

def _stub_http(monkeypatch, status, body=None, error=None):
    calls = []

    def fake(url, **kw):
        calls.append({"url": url, **kw})
        return td.HttpResult(status, body, error)

    monkeypatch.setattr(td, "_http_json", fake)
    return calls


def test_submit_without_a_token_is_write_closed_and_never_calls_the_api(monkeypatch):
    monkeypatch.delenv("DASHBOARD_API_TOKEN", raising=False)
    calls = _stub_http(monkeypatch, 200, {})
    outcome, detail = td.submit_answer(
        object_id="O", request_id="R", chosen="wait")
    assert outcome == td.WRITE_CLOSED
    assert "DASHBOARD_API_TOKEN" in detail
    assert calls == []


@pytest.mark.parametrize("status,body,expected", [
    (200, {"answerState": "in_transit"}, td.SUBMITTED),
    (409, {"detail": "already recorded in the repo"}, td.ALREADY_ANSWERED),
    (401, {"detail": "bad token"}, td.UNAUTHORIZED),
    (503, {"detail": "decision endpoint is not configured to accept writes"},
     td.WRITE_CLOSED),
    (503, {"detail": "submission did not persist: disk full"}, td.NOT_PERSISTED),
    (400, {"detail": "chosen must be one of ['build_now']"}, td.REFUSED),
    (404, {"detail": "no such work object"}, td.REFUSED),
    (500, {"detail": "boom"}, td.REFUSED),
])
def test_submit_maps_each_route_status(monkeypatch, status, body, expected):
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "tok")
    _stub_http(monkeypatch, status, body)
    outcome, _ = td.submit_answer(object_id="O", request_id="R", chosen="wait")
    assert outcome == expected


def test_the_two_503s_are_not_collapsed(monkeypatch):
    """They mean opposite things about the operator's next move."""
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "tok")
    _stub_http(monkeypatch, 503, {"detail": "not configured to accept writes"})
    gate, _ = td.submit_answer(object_id="O", request_id="R", chosen="w")
    _stub_http(monkeypatch, 503, {"detail": "submission did not persist: EIO"})
    persist, _ = td.submit_answer(object_id="O", request_id="R", chosen="w")
    assert gate == td.WRITE_CLOSED and persist == td.NOT_PERSISTED


def test_no_http_response_is_unknown_never_refused(monkeypatch):
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "tok")
    _stub_http(monkeypatch, None, None, "connection refused")
    outcome, detail = td.submit_answer(object_id="O", request_id="R", chosen="w")
    assert outcome == td.UNKNOWN
    assert "connection refused" in detail


def test_submit_sends_the_bearer_and_the_declared_option_key(monkeypatch):
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "tok")
    calls = _stub_http(monkeypatch, 200, {"answerState": "in_transit"})
    td.submit_answer(object_id="WO-1", request_id="DEC-1", chosen="wait")
    assert calls[0]["headers"]["Authorization"] == "Bearer tok"
    assert calls[0]["payload"]["chosen"] == "wait"
    assert calls[0]["payload"]["object_id"] == "WO-1"
    assert calls[0]["method"] == "POST"


# ── the tap handler end to end (with the API stubbed) ───────────────────────

def test_tap_submits_and_reports_in_transit(monkeypatch):
    req = _request()
    monkeypatch.setattr(td, "fetch_inbox", lambda: ({"requests": [req]}, None))
    monkeypatch.setattr(
        td, "submit_answer", lambda **kw: (td.SUBMITTED, None))
    data = td.encode_callback(req["objectId"], req["id"], "wait")
    out = td.handle_decision_callback(data)
    assert out["outcome"] == td.SUBMITTED
    assert out["chosen"] == "wait"
    assert "NOT yet decided" in out["reply"]


def test_tap_on_a_foreign_callback_falls_through(monkeypatch):
    assert td.handle_decision_callback("propexp:y:t1") is None


def test_tap_when_the_inbox_is_unreadable_submits_nothing(monkeypatch):
    monkeypatch.setattr(td, "fetch_inbox", lambda: (None, "curl 7"))
    called = []
    monkeypatch.setattr(td, "submit_answer",
                        lambda **kw: called.append(kw) or (td.SUBMITTED, None))
    out = td.handle_decision_callback(td.encode_callback("O", "R", "K"))
    assert out["outcome"] == td.INBOX_UNREADABLE
    assert called == []


def test_a_stale_button_on_a_committed_request_reads_as_already_answered(monkeypatch):
    req = _request(answerState="committed", answer={"chosen": "wait"})
    monkeypatch.setattr(td, "fetch_inbox", lambda: ({"requests": [req]}, None))
    called = []
    monkeypatch.setattr(td, "submit_answer",
                        lambda **kw: called.append(kw) or (td.SUBMITTED, None))
    out = td.handle_decision_callback(
        td.encode_callback(req["objectId"], req["id"], "wait"))
    assert out["outcome"] == td.ALREADY_ANSWERED
    assert called == []          # no pointless round trip
    assert "Already answered" in out["reply"]


def test_a_409_from_the_route_still_renders_as_already_answered(monkeypatch):
    """The route stays the authority even if our local grade disagrees."""
    req = _request()             # locally reads not_submitted
    monkeypatch.setattr(td, "fetch_inbox", lambda: ({"requests": [req]}, None))
    monkeypatch.setattr(td, "submit_answer",
                        lambda **kw: (td.ALREADY_ANSWERED, "already recorded"))
    out = td.handle_decision_callback(
        td.encode_callback(req["objectId"], req["id"], "wait"))
    assert out["outcome"] == td.ALREADY_ANSWERED
    assert "nothing was submitted" in out["reply"].lower()


# ── the sweep ───────────────────────────────────────────────────────────────

def _inbox(requests, *, write_open=True):
    return {
        "present": True,
        "requests": requests,
        "writeGate": {"state": "open" if write_open else "closed_no_token",
                      "acceptsWrites": write_open},
    }


@pytest.fixture(autouse=True)
def _answerable(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "trader-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "365546917")


def _sender(sent):
    def send(text, keyboard):
        sent.append((text, keyboard))
        return True
    return send


def test_sweep_prompts_once_and_marks_it(tmp_path, monkeypatch):
    req = _request()
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([req]), None))
    state = tmp_path / "prompted.json"
    sent = []
    s1 = td.run_decision_prompt_sweep(sender=_sender(sent), state_path=state)
    assert s1["prompted_choice"] == 1 and s1["prompted_free_text"] == 0
    assert sent[0][1]["inline_keyboard"]

    s2 = td.run_decision_prompt_sweep(sender=_sender(sent), state_path=state)
    assert s2["candidates"] == 0
    assert len(sent) == 1          # asked once, not once per cadence


def test_sweep_marker_survives_a_restart(tmp_path, monkeypatch):
    """A module-global marker would re-ask everything on every bot restart."""
    req = _request()
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([req]), None))
    state = tmp_path / "prompted.json"
    td.run_decision_prompt_sweep(sender=_sender([]), state_path=state)
    stored = json.loads(state.read_text())["prompted"]
    assert td.marker_key(req["objectId"], req["id"]) in stored


@pytest.mark.parametrize("answer_state", ["in_transit", "committed", "unreadable"])
def test_sweep_only_asks_about_unanswered_requests(tmp_path, monkeypatch, answer_state):
    req = _request(answerState=answer_state)
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([req]), None))
    sent = []
    stats = td.run_decision_prompt_sweep(
        sender=_sender(sent), state_path=tmp_path / "p.json")
    assert stats["candidates"] == 0 and sent == []


def test_sweep_holds_rather_than_sending_buttons_that_would_503(tmp_path, monkeypatch):
    req = _request()
    monkeypatch.setattr(
        td, "fetch_inbox", lambda: (_inbox([req], write_open=False), None))
    sent = []
    state = tmp_path / "p.json"
    stats = td.run_decision_prompt_sweep(sender=_sender(sent), state_path=state)
    assert stats["held_write_gate"] == 1
    assert sent == []
    # NOT marked: once the gate opens the question is asked, not lost.
    assert json.loads(state.read_text())["prompted"] == {}


def test_sweep_holds_when_no_polled_bot_can_carry_the_buttons(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    req = _request()
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([req]), None))
    sent = []
    stats = td.run_decision_prompt_sweep(
        sender=_sender(sent), state_path=tmp_path / "p.json")
    assert stats["held_route"] == 1 and sent == []


def test_a_failed_send_is_not_marked_so_it_retries(tmp_path, monkeypatch):
    req = _request()
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([req]), None))
    state = tmp_path / "p.json"
    stats = td.run_decision_prompt_sweep(
        sender=lambda *_: False, state_path=state)
    assert stats["failed"] == 1
    assert json.loads(state.read_text())["prompted"] == {}


def test_choice_and_free_text_populations_are_counted_separately(tmp_path, monkeypatch):
    reqs = [_request(), _request(id="DEC-2", options=[])]
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox(reqs), None))
    stats = td.run_decision_prompt_sweep(
        sender=_sender([]), state_path=tmp_path / "p.json")
    assert stats["prompted_choice"] == 1
    assert stats["prompted_free_text"] == 1


def test_free_text_only_request_is_sent_without_a_keyboard(tmp_path, monkeypatch):
    monkeypatch.setattr(
        td, "fetch_inbox", lambda: (_inbox([_request(options=[])]), None))
    sent = []
    td.run_decision_prompt_sweep(sender=_sender(sent), state_path=tmp_path / "p.json")
    assert sent[0][1] is None
    # ...and the write gate is irrelevant to it: nothing will be tapped.


def test_free_text_only_is_sent_even_when_the_write_gate_is_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        td, "fetch_inbox",
        lambda: (_inbox([_request(options=[])], write_open=False), None))
    sent = []
    stats = td.run_decision_prompt_sweep(
        sender=_sender(sent), state_path=tmp_path / "p.json")
    assert stats["prompted_free_text"] == 1 and stats["held_write_gate"] == 0
    assert len(sent) == 1


def test_sweep_sends_nothing_when_the_inbox_is_unreadable(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "fetch_inbox", lambda: (None, "conn refused"))
    sent = []
    stats = td.run_decision_prompt_sweep(
        sender=_sender(sent), state_path=tmp_path / "p.json")
    assert stats["checked"] is False and sent == []
    assert "unreadable" in stats["reason"]


def test_an_unreadable_marker_file_holds_rather_than_re_asking_everything(
    tmp_path, monkeypatch
):
    state = tmp_path / "p.json"
    state.write_text("{ not json")
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([_request()]), None))
    sent = []
    stats = td.run_decision_prompt_sweep(sender=_sender(sent), state_path=state)
    assert stats["prompt_state_read"] == "unreadable"
    assert sent == []


def test_absent_and_unreadable_marker_states_are_not_collapsed(tmp_path):
    absent, s1 = td.read_prompt_state(tmp_path / "nope.json")
    assert (absent, s1) == ({}, "absent")
    bad = tmp_path / "bad.json"
    bad.write_text("{{{")
    assert td.read_prompt_state(bad)[1] == "unreadable"


def test_sweep_is_paused_by_a_non_positive_cadence(tmp_path, monkeypatch):
    monkeypatch.setenv("WORK_DECISION_PROMPT_SECONDS", "0")
    sent = []
    assert td.run_decision_prompt_sweep(
        sender=_sender(sent), state_path=tmp_path / "p.json")["paused"] is True
    assert sent == []


def test_an_unparseable_cadence_falls_back_to_the_default_not_to_zero(monkeypatch):
    """A typo must not silently switch the decision channel off."""
    monkeypatch.setenv("WORK_DECISION_PROMPT_SECONDS", "five minutes")
    assert td.prompt_interval_seconds() == 300.0


def test_sweep_never_raises_on_a_broken_inbox(monkeypatch, tmp_path):
    def boom():
        raise RuntimeError("kaboom")
    monkeypatch.setattr(td, "fetch_inbox", boom)
    stats = td.run_decision_prompt_sweep(state_path=tmp_path / "p.json")
    assert stats["checked"] is False and "kaboom" in stats["reason"]


# ── marker pruning ──────────────────────────────────────────────────────────

def test_a_marker_absent_from_the_inbox_is_kept_until_the_retain_window(monkeypatch):
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    recent = {"k": {"prompted_at": td._iso(now - timedelta(days=1))}}
    assert td._prune(recent, set(), now=now) == recent
    old = {"k": {"prompted_at": td._iso(now - timedelta(days=40))}}
    assert td._prune(old, set(), now=now) == {}
    # Still live in the inbox → kept regardless of age.
    assert td._prune(old, {"k"}, now=now) == old


def test_an_undateable_marker_is_kept_rather_than_re_asked():
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    rows = {"k": {"prompted_at": None}}
    assert td._prune(rows, set(), now=now) == rows


# ── destination ─────────────────────────────────────────────────────────────

def test_the_answerable_route_is_the_polled_trader_bot(monkeypatch):
    route = td.answerable_route()
    assert route.deliverable is True
    assert route.token_from == "TELEGRAM_BOT_TOKEN"
    assert "polled" in route.note


def test_the_dedicated_claude_bot_is_not_used_because_nothing_polls_it(monkeypatch):
    """claude_route() would pick this up; a button sent there goes nowhere."""
    monkeypatch.setenv("TELEGRAM_CLAUDE_BOT_SECRET", "dedicated-but-unpolled")
    assert td.answerable_route().token == "trader-token"


def test_no_token_is_not_deliverable_and_says_why(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    route = td.answerable_route()
    assert route.deliverable is False
    assert "TELEGRAM_BOT_TOKEN" in route.note


def test_describe_never_contains_a_token_value():
    assert "trader-token" not in td.answerable_route().describe()


# ── the API base ────────────────────────────────────────────────────────────

def test_api_base_defaults_to_loopback_and_strips_a_trailing_slash(monkeypatch):
    monkeypatch.delenv("WORK_DECISION_API_BASE", raising=False)
    assert td.api_base() == "http://127.0.0.1:8001"
    monkeypatch.setenv("WORK_DECISION_API_BASE", "https://ict-bot.duckdns.org/")
    assert td.api_base() == "https://ict-bot.duckdns.org"


# ═════════════════════════════════════════════════════════════════════════════
# THE ROUTING CHANGE — decisions belong on the dedicated Claude bot, but ONLY
# once something actually polls it. Operator, 2026-09-02: "that's supposed to
# be showing up in Cloudbot. Right? Not on the trader one, the decisions."
#
# The claim these pin is the one a green harness otherwise cannot reach: that
# preferring the Claude bot requires POSITIVE evidence of a poller, so the
# change cannot ship buttons that look healthy and go nowhere.
# ═════════════════════════════════════════════════════════════════════════════

from src.runtime import telegram_poll_registry as reg  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_poll_registry(tmp_path, monkeypatch):
    """No poller exists unless a test says so, and claims never leak between tests."""
    monkeypatch.setattr(reg, "runtime_logs_dir", lambda: tmp_path / "rl")
    (tmp_path / "rl").mkdir(parents=True, exist_ok=True)
    reg._LOCAL.clear()
    yield
    reg._LOCAL.clear()


def _claude_is_polled():
    reg.record_poll("TELEGRAM_CLAUDE_BOT_SECRET", [td.CB_PREFIX], service="cdb")


def _trader_is_polled():
    reg.record_poll("TELEGRAM_BOT_TOKEN", [td.CB_PREFIX], service="tb")


# ── preference: the Claude bot, on evidence ─────────────────────────────────

def test_the_claude_bot_wins_once_something_actually_polls_it(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CLAUDE_BOT_SECRET", "dedicated")
    _claude_is_polled()
    route = td.answerable_route()
    assert route.destination == "claude"
    assert route.token_from == "TELEGRAM_CLAUDE_BOT_SECRET"
    assert route.answerable is True


def test_the_dedicated_bot_is_STILL_refused_while_nothing_polls_it(monkeypatch):
    """The pre-existing behaviour, and the reason this PR is two halves.

    A token that resolves is DELIVERY. Answerability needs a poller, and
    sending here without one would ship buttons that go nowhere.
    """
    monkeypatch.setenv("TELEGRAM_CLAUDE_BOT_SECRET", "dedicated-but-unpolled")
    _trader_is_polled()
    route = td.answerable_route()
    assert route.token == "trader-token"
    assert route.destination == "trader_fallback"
    assert "not confirmed polled" in route.note


def test_the_fallback_says_WHY_rather_than_reading_as_success(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CLAUDE_BOT_SECRET", "dedicated-but-unpolled")
    _trader_is_polled()
    note = td.answerable_route().note
    assert "FALLBACK" in note
    assert reg.TOKEN_ONLY_NOT_POLLED in note


def test_a_claude_route_that_is_merely_the_shared_trader_token_is_not_claude():
    """`isolated`, not `deliverable`: a fallback there is the trader token
    wearing the Claude route's name, and treating it as the dedicated bot would
    report the separation as done while both channels sat on one token."""
    _trader_is_polled()
    route = td.answerable_route()
    assert route.destination == "trader_fallback"
    assert "TELEGRAM_CLAUDE_BOT_SECRET is unset" in route.note


def test_unknown_poll_evidence_does_NOT_promote_the_claude_bot(monkeypatch, tmp_path):
    """`unknown` must fail closed — we could not look is not a poller."""
    monkeypatch.setenv("TELEGRAM_CLAUDE_BOT_SECRET", "dedicated")
    path = reg.entry_path("TELEGRAM_CLAUDE_BOT_SECRET")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    _trader_is_polled()
    route = td.answerable_route()
    assert route.destination == "trader_fallback"
    # `route.poll` describes the SELECTED destination (the trader, which IS
    # polled); the Claude bot's unreadable verdict is carried in the note, so
    # "we could not look" is reported rather than silently promoted.
    assert route.poll.state == reg.POLLED_WITH_HANDLER
    assert reg.UNKNOWN in route.note


def test_deliverable_and_answerable_stay_DIFFERENT_questions(monkeypatch):
    """The distinction the whole module is built on, asserted rather than assumed."""
    route = td.answerable_route()          # trader token set, nothing polls it
    assert route.deliverable is True       # we can SEND
    assert route.answerable is False       # a TAP would not be received


def test_describe_still_never_contains_a_token_value(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CLAUDE_BOT_SECRET", "dedicated-secret-value")
    _claude_is_polled()
    described = td.answerable_route().describe()
    assert "dedicated-secret-value" not in described
    assert "trader-token" not in described
    assert "TELEGRAM_CLAUDE_BOT_SECRET" in described


# ── the sweep holds rather than sending dead buttons ────────────────────────

def _default_sender_spy(monkeypatch, sent, *, ok=True):
    """Patch the DEFAULT sender, so the sweep's real poll gate is exercised.

    Passing `sender=` would bypass that gate by design: an injected sender
    delivers somewhere the sweep cannot see, so gating it on this route's
    evidence would be a claim about an unknown destination.
    """
    def _send(text, keyboard):
        sent.append((text, keyboard))
        return ok
    monkeypatch.setattr(td, "_default_sender", _send)


def test_sweep_HOLDS_when_no_bot_is_confirmed_polled(tmp_path, monkeypatch):
    """The dead-button state, made reportable instead of silent."""
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([_request()]), None))
    sent = []
    _default_sender_spy(monkeypatch, sent)
    stats = td.run_decision_prompt_sweep(state_path=tmp_path / "p.json")
    assert stats["held_not_polled"] == 1
    assert sent == []
    # NOT marked prompted: once a poller exists the question is asked, not lost.
    assert json.loads((tmp_path / "p.json").read_text())["prompted"] == {}


def test_the_hold_is_LOUD_and_names_the_state(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([_request()]), None))
    _default_sender_spy(monkeypatch, [])
    with caplog.at_level("WARNING"):
        td.run_decision_prompt_sweep(state_path=tmp_path / "p.json")
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("NOT confirmed polled" in m for m in warnings)
    assert any(reg.TOKEN_ONLY_NOT_POLLED in m for m in warnings)


def test_held_not_polled_is_NOT_pooled_with_held_route(tmp_path, monkeypatch):
    """Different faults, different fixes: start a poller vs set a token."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([_request()]), None))
    _default_sender_spy(monkeypatch, [])
    stats = td.run_decision_prompt_sweep(state_path=tmp_path / "p.json")
    assert stats["held_route"] == 1 and stats["held_not_polled"] == 0


def test_sweep_SENDS_once_the_claude_bot_is_polled(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CLAUDE_BOT_SECRET", "dedicated")
    _claude_is_polled()
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([_request()]), None))
    sent = []
    _default_sender_spy(monkeypatch, sent)
    stats = td.run_decision_prompt_sweep(state_path=tmp_path / "p.json")
    assert stats["prompted_choice"] == 1 and stats["held_not_polled"] == 0
    assert stats["destination"] == "claude"
    assert len(sent) == 1 and sent[0][1] is not None   # a real keyboard went out


def test_sweep_falls_back_to_the_trader_bot_rather_than_going_silent(
        tmp_path, monkeypatch):
    """A wrong-chat prompt is a noise complaint; no prompt at all is an outage."""
    monkeypatch.setenv("TELEGRAM_CLAUDE_BOT_SECRET", "dedicated-but-unpolled")
    _trader_is_polled()
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([_request()]), None))
    sent = []
    _default_sender_spy(monkeypatch, sent)
    stats = td.run_decision_prompt_sweep(state_path=tmp_path / "p.json")
    assert stats["prompted_choice"] == 1
    assert stats["destination"] == "trader_fallback"   # reported, not silent
    assert len(sent) == 1


def test_the_sweep_stats_carry_the_poll_state_for_a_reader(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "fetch_inbox", lambda: (_inbox([_request()]), None))
    _default_sender_spy(monkeypatch, [])
    stats = td.run_decision_prompt_sweep(state_path=tmp_path / "p.json")
    assert stats["poll_state"] == reg.TOKEN_ONLY_NOT_POLLED
    assert stats["destination"] == "trader_fallback"


def test_the_write_gate_hold_still_wins_over_the_poll_hold(tmp_path, monkeypatch):
    """Both are correct holds; the write gate is checked first and stays counted
    on its own, so a closed gate is never reported as a polling problem."""
    _trader_is_polled()
    monkeypatch.setattr(
        td, "fetch_inbox", lambda: (_inbox([_request()], write_open=False), None))
    _default_sender_spy(monkeypatch, [])
    stats = td.run_decision_prompt_sweep(state_path=tmp_path / "p.json")
    assert stats["held_write_gate"] == 1 and stats["held_not_polled"] == 0
