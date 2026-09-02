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
# On-demand `/decisions` — the operator PULLS the inbox
#
# The operator's ask, 2026-09-02: "if there's any decisions that are still
# waiting for me that aren't answered so they can all pop up at once. In case I
# don't get them as they come in."
# ═════════════════════════════════════════════════════════════════════════════

def _tree(state="synced", **over):
    from src.runtime.manager_status import TreeProvenance

    base = {"state": state, "head_sha": "0b52157", "main_sha": "0b52157",
            "behind_commits": 0, "note": "level"}
    base.update(over)
    return TreeProvenance(**base)


def _pull_inbox(requests=None, *, accepts=True, edges=None, **summary_over):
    requests = requests if requests is not None else []
    by_state = {"not_submitted": 0, "in_transit": 0, "committed": 0,
                "unreadable": 0}
    for r in requests:
        by_state[r["answerState"]] = by_state.get(r["answerState"], 0) + 1
    summary = {
        "awaitingOperator": by_state["not_submitted"] + by_state["unreadable"],
        "awaitingCommit": by_state["in_transit"],
        "decided": by_state["committed"],
        "byAnswerState": by_state,
        "requestCount": len(requests),
        "malformedRequestsDropped": 0,
        "unanswerableOperatorEdgeCount": len(edges or []),
        "staleOpenWindows": 0,
        "staleAfterSeconds": 3600,
    }
    summary.update(summary_over)
    gate = {"state": "open" if accepts else "closed_no_token",
            "acceptsWrites": accepts, "note": ""}
    return {
        "present": True, "reason": None, "requests": requests,
        "unanswerableOperatorEdges": edges or [],
        "summary": summary,
        "transit": {"state": "read", "error": None, "path": "/x", "rowsRead": 0},
        "writeGate": gate,
    }


def _stub_inbox(monkeypatch, inbox, error=None):
    monkeypatch.setattr(td, "fetch_inbox", lambda: (inbox, error))


def _pending(n, **over):
    out = []
    for i in range(n):
        out.append(_request(
            id=f"DEC-{i:03d}", objectId=f"WO-{i:03d}", answerState="not_submitted",
            **over))
    return out


# ── zero / one / several ────────────────────────────────────────────────────

def test_zero_waiting_says_so_explicitly(monkeypatch):
    """Silence is indistinguishable from a broken command."""
    _stub_inbox(monkeypatch, _pull_inbox([]))
    out = td.build_on_demand_decisions(tree=_tree())
    assert len(out) == 1, "no prompts, just the summary"
    text, keyboard = out[0]
    assert keyboard is None
    assert "Nothing is waiting for you" in text
    assert "No decision request is unanswered on your side" in text


def test_one_waiting_sends_the_summary_and_exactly_one_prompt(monkeypatch):
    _stub_inbox(monkeypatch, _pull_inbox(_pending(1)))
    out = td.build_on_demand_decisions(tree=_tree())
    assert len(out) == 2
    assert "WAITING ON YOU: 1" in out[0][0]
    assert out[1][1] is not None, "the one prompt carries its keyboard"


def test_several_waiting_all_pop_up_at_once(monkeypatch):
    """The operator's words: 'so they can all pop up at once'."""
    _stub_inbox(monkeypatch, _pull_inbox(_pending(5)))
    out = td.build_on_demand_decisions(tree=_tree())
    assert len(out) == 6
    assert "WAITING ON YOU: 5" in out[0][0]
    assert all(kb is not None for _, kb in out[1:])
    sent_objects = {o["objectId"] for o in
                    [{"objectId": f"WO-{i:03d}"} for i in range(5)]}
    body = "\n".join(t for t, _ in out)
    for obj in sent_objects:
        assert obj in body


def test_only_not_submitted_requests_are_sent(monkeypatch):
    """`in_transit` has an open window; `committed` is decided; `unreadable` is
    *we could not look* — none of the three is a question to re-ask."""
    reqs = (_pending(2)
            + [_request(id="D-T", objectId="W-T", answerState="in_transit"),
               _request(id="D-C", objectId="W-C", answerState="committed"),
               _request(id="D-U", objectId="W-U", answerState="unreadable")])
    _stub_inbox(monkeypatch, _pull_inbox(reqs))
    out = td.build_on_demand_decisions(tree=_tree())
    assert len(out) == 3, "2 prompts + 1 summary"
    body = "\n".join(t for t, _ in out[1:])
    for absent in ("W-T", "W-C", "W-U"):
        assert absent not in body


def test_the_cap_is_stated_rather_than_silently_truncating(monkeypatch):
    _stub_inbox(monkeypatch, _pull_inbox(_pending(td.MAX_ON_DEMAND_PROMPTS + 3)))
    out = td.build_on_demand_decisions(tree=_tree())
    assert len(out) == td.MAX_ON_DEMAND_PROMPTS + 1
    summary = out[0][0]
    assert "OMITTED" in summary
    assert f"3 of {td.MAX_ON_DEMAND_PROMPTS + 3}" in summary


# ── the two populations are never pooled ────────────────────────────────────

def test_awaiting_operator_and_awaiting_commit_are_reported_separately(monkeypatch):
    """An `in_transit` question is unanswered but waits on a COMMITTER.

    Pooling them would put work on the operator's plate that is not theirs.
    """
    reqs = _pending(2) + [
        _request(id=f"D-T{i}", objectId=f"W-T{i}", answerState="in_transit")
        for i in range(3)
    ]
    _stub_inbox(monkeypatch, _pull_inbox(reqs))
    summary = td.build_on_demand_decisions(tree=_tree())[0][0]

    assert "WAITING ON YOU: 2" in summary
    assert "WAITING ON A COMMITTER: 3" in summary
    assert "nothing for you to do" in summary
    # The pooled figure must appear nowhere.
    assert "WAITING ON YOU: 5" not in summary


def test_awaiting_commit_is_reported_even_when_zero(monkeypatch):
    """A line that VANISHES makes a reader branch on absence."""
    _stub_inbox(monkeypatch, _pull_inbox(_pending(1)))
    assert "WAITING ON A COMMITTER: 0" in td.build_on_demand_decisions(
        tree=_tree())[0][0]


def test_ungradeable_requests_are_broken_out_of_awaiting_operator(monkeypatch):
    """`awaitingOperator` is not_submitted + unreadable, and only the first is sent."""
    reqs = _pending(1) + [
        _request(id="D-U", objectId="W-U", answerState="unreadable")]
    _stub_inbox(monkeypatch, _pull_inbox(reqs))
    out = td.build_on_demand_decisions(tree=_tree())
    summary = out[0][0]
    assert "WAITING ON YOU: 2" in summary
    assert "1 unanswered (sent below)" in summary
    assert "could NOT be graded" in summary
    assert len(out) == 2, "only the not_submitted one is sent"


# ── the write gate ──────────────────────────────────────────────────────────

def test_a_closed_write_gate_sends_no_buttons_and_says_why(monkeypatch):
    """Buttons whose taps 503 read as 'dealt with' while nothing landed."""
    _stub_inbox(monkeypatch, _pull_inbox(_pending(2), accepts=False))
    out = td.build_on_demand_decisions(tree=_tree())
    summary, prompts = out[0][0], out[1:]
    assert "ANSWERING IS CLOSED" in summary
    assert "503" in summary and "fail-closed by design" in summary
    assert all(kb is None for _, kb in prompts), "no tappable buttons"
    # The questions are still SHOWN, so a blocked decision is not invisible.
    assert len(prompts) == 2


def test_an_unknown_write_gate_is_not_reported_as_open(monkeypatch):
    inbox = _pull_inbox(_pending(1))
    inbox["writeGate"] = {"state": "unknown", "acceptsWrites": None, "note": ""}
    _stub_inbox(monkeypatch, inbox)
    out = td.build_on_demand_decisions(tree=_tree())
    assert "Write gate UNKNOWN" in out[0][0]


def test_no_answerable_route_omits_buttons_and_says_so(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    _stub_inbox(monkeypatch, _pull_inbox(_pending(1)))
    out = td.build_on_demand_decisions(tree=_tree())
    assert "No answerable bot resolved" in out[0][0]
    assert out[1][1] is None


# ── the gaps worth surfacing ────────────────────────────────────────────────

def test_unanswerable_operator_edges_are_surfaced(monkeypatch):
    """A question the operator blocks on that nobody wrote down as answerable."""
    edges = [{"objectId": "WO-20260901-PHASE-H", "ref": "DEC-READ-GATE",
              "since": "2026-09-01T21:22Z", "objectTitle": "Phase H"}]
    _stub_inbox(monkeypatch, _pull_inbox([], edges=edges))
    summary = td.build_on_demand_decisions(tree=_tree())[0][0]
    assert "NOT ANSWERABLE FROM HERE" in summary
    assert "WO-20260901-PHASE-H" in summary
    assert "DEC-READ-GATE" in summary


def test_no_edges_means_no_edge_block(monkeypatch):
    _stub_inbox(monkeypatch, _pull_inbox([]))
    assert "NOT ANSWERABLE FROM HERE" not in td.build_on_demand_decisions(
        tree=_tree())[0][0]


def test_stale_transit_windows_are_flagged(monkeypatch):
    reqs = [_request(id="D-T", objectId="W-T", answerState="in_transit")]
    _stub_inbox(monkeypatch, _pull_inbox(reqs, staleOpenWindows=1))
    assert "OPEN WINDOW, not a decision" in td.build_on_demand_decisions(
        tree=_tree())[0][0]


# ── failure is never reported as emptiness ──────────────────────────────────

def test_an_unreadable_inbox_is_never_reported_as_nothing_waiting(monkeypatch):
    _stub_inbox(monkeypatch, None, error="HTTP 503")
    out = td.build_on_demand_decisions(tree=_tree())
    assert len(out) == 1
    text = out[0][0]
    assert "could not read the decision inbox" in text
    assert "do not read it as a clear inbox" in text
    assert "Nothing is waiting" not in text


def test_an_absent_inbox_is_not_a_claim_that_nothing_is_waiting(monkeypatch):
    _stub_inbox(monkeypatch, {"present": False, "reason": "work store absent"})
    text = td.build_on_demand_decisions(tree=_tree())[0][0]
    assert "not a claim that no decision is waiting" in text


def test_a_raising_inbox_still_produces_a_reply(monkeypatch):
    def boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(td, "fetch_inbox", boom)
    out = td.build_on_demand_decisions(tree=_tree())
    assert len(out) == 1 and "we could not look" in out[0][0]


# ── the stale-tree caveat (manager_status.tree_state) ───────────────────────

def test_a_behind_main_tree_warns_that_answered_questions_may_read_unanswered(
        monkeypatch):
    """The measured 2026-09-02 failure: `in_transit` for minutes after commit."""
    _stub_inbox(monkeypatch, _pull_inbox(_pending(1)))
    summary = td.build_on_demand_decisions(
        tree=_tree("behind_main", behind_commits=4, main_sha="a1b2c3d"))[0][0]
    assert "GRADED AGAINST A STALE TREE" in summary
    assert "4 commit(s) behind main" in summary
    assert "may already have been answered and committed" in summary


def test_an_unknown_tree_is_not_reported_as_current(monkeypatch):
    _stub_inbox(monkeypatch, _pull_inbox(_pending(1)))
    summary = td.build_on_demand_decisions(
        tree=_tree("unknown", behind_commits=None, note="git unreadable"))[0][0]
    assert "COULD NOT BE ESTABLISHED" in summary
    assert "we could not\nlook" in summary or "we could not " in summary
    assert "GRADED AGAINST A STALE TREE" not in summary


def test_a_synced_tree_says_synced(monkeypatch):
    _stub_inbox(monkeypatch, _pull_inbox(_pending(1)))
    summary = td.build_on_demand_decisions(tree=_tree("synced"))[0][0]
    assert "tree: synced" in summary
    assert "STALE TREE" not in summary


# ── the marker interaction: the decision this change had to make ────────────

def test_on_demand_neither_reads_nor_writes_the_prompted_marker(
        monkeypatch, tmp_path):
    """The marker file must be byte-identical across a `/decisions` call.

    Writing markers here would SUPPRESS the periodic sweep for a question the
    operator pulled; removing them would make it re-ask everything. Neither is
    acceptable, so on-demand touches the file not at all.
    """
    marker = tmp_path / "work_decision_prompted.json"
    td.write_prompt_state({"WO-000::DEC-000": {"prompted_at": "2026-09-02T09:00:00Z"}},
                          marker)
    before = marker.read_bytes()

    monkeypatch.setattr(td, "prompt_state_path", lambda: marker)
    _stub_inbox(monkeypatch, _pull_inbox(_pending(2)))
    td.build_on_demand_decisions(tree=_tree())

    assert marker.read_bytes() == before, (
        "the on-demand pull must not disturb the sweep's idempotency marker")


def test_an_already_prompted_question_is_still_sent_on_demand(monkeypatch, tmp_path):
    """That is the entire point of the ask: 'in case I don't get them as they
    come in'. The sweep asks ONCE; `/decisions` re-sends regardless."""
    marker = tmp_path / "work_decision_prompted.json"
    reqs = _pending(1)
    key = td.marker_key(reqs[0]["objectId"], reqs[0]["id"])
    td.write_prompt_state({key: {"prompted_at": "2026-09-02T09:00:00Z"}}, marker)
    monkeypatch.setattr(td, "prompt_state_path", lambda: marker)
    _stub_inbox(monkeypatch, _pull_inbox(reqs))

    out = td.build_on_demand_decisions(tree=_tree())
    assert len(out) == 2, "already prompted, and sent again anyway"
    assert reqs[0]["objectId"] in out[1][0]


def test_the_sweep_still_asks_once_after_an_on_demand_pull(monkeypatch, tmp_path):
    """The accepted consequence, pinned: on-demand does not suppress the sweep.

    A duplicate PROMPT is recoverable; a SUPPRESSED one is not. And it cannot
    produce a duplicate DECISION -- the route answers a second submission with
    409, rendered as `already answered`.
    """
    marker = tmp_path / "work_decision_prompted.json"
    reqs = _pending(1)
    monkeypatch.setattr(td, "prompt_state_path", lambda: marker)
    _stub_inbox(monkeypatch, _pull_inbox(reqs))

    td.build_on_demand_decisions(tree=_tree())

    sent = []
    stats = td.run_decision_prompt_sweep(
        sender=lambda text, kb: sent.append(text) or True, state_path=marker)
    assert stats["prompted_choice"] == 1, (
        "the sweep must still deliver its own one-time prompt")
    # ...and only once thereafter.
    stats2 = td.run_decision_prompt_sweep(
        sender=lambda text, kb: sent.append(text) or True, state_path=marker)
    assert stats2["candidates"] == 0


# ── one builder, not two ────────────────────────────────────────────────────

def test_on_demand_reuses_the_sweeps_own_keyboard_builder(monkeypatch):
    """Two copies of the callback_data construction is how the 64-byte budget
    and the key-digest scheme drift apart."""
    reqs = _pending(1)
    _stub_inbox(monkeypatch, _pull_inbox(reqs))
    out = td.build_on_demand_decisions(tree=_tree())

    assert out[1][1] == td.build_decision_keyboard(reqs[0])
    assert out[1][0] == td.render_decision_prompt(reqs[0])
    for row in out[1][1]["inline_keyboard"]:
        for button in row:
            assert len(button["callback_data"].encode("utf-8")) <= (
                td.TELEGRAM_CALLBACK_DATA_MAX_BYTES)


def test_the_summary_says_it_does_not_change_what_the_sweep_will_ask(monkeypatch):
    _stub_inbox(monkeypatch, _pull_inbox(_pending(1)))
    assert "does not change what the periodic sweep will ask" in " ".join(
        td.build_on_demand_decisions(tree=_tree())[0][0].split())


def test_a_failing_tree_read_degrades_the_caveat_not_the_reply(monkeypatch):
    """A git failure must not cost the operator the whole decisions reply.

    And it must degrade to a STATED `unknown`, not to a missing stamp: an
    absent caveat reads as a tree nobody had to qualify.
    """
    import src.runtime.manager_status as ms

    def boom(*a, **k):
        raise RuntimeError("git is on fire")

    monkeypatch.setattr(ms, "read_tree_provenance", boom)
    _stub_inbox(monkeypatch, _pull_inbox(_pending(2)))

    out = td.build_on_demand_decisions()
    assert len(out) == 3, "the questions still arrive"
    summary = out[0][0]
    assert "COULD NOT BE ESTABLISHED" in summary
    assert "WAITING ON YOU: 2" in summary
