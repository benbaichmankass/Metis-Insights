"""Tests for the poll registry — is a tap on this bot actually received?

The claims worth pinning are the ones that decide whether a decision prompt
ships DEAD BUTTONS, which is the failure mode with no external symptom: the
prompt arrives, renders, and highlights on tap while nothing happens.

* ``unknown`` (we could not look) is NEVER collapsed into
  ``token_only_not_polled`` (we looked and nothing polls it), in either
  direction — the two are wrong in opposite ways.
* ``answerable`` is true for exactly ONE state, so absent evidence fails closed.
* a claim EXPIRES: a process that dies stops refreshing and its assertion dies
  with it, rather than outliving the thing it asserts.
* no token VALUE ever reaches a note, a banner or a persisted entry.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.runtime import telegram_poll_registry as reg


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Point the registry at a scratch root and clear the in-process claims.

    ``_LOCAL`` is a module global, so without this a claim from one test would
    silently answer another's question — the test-suite analogue of the stale
    claim this module exists to expire.
    """
    monkeypatch.setattr(reg, "runtime_logs_dir", lambda: tmp_path)
    reg._LOCAL.clear()
    yield
    reg._LOCAL.clear()


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


# ── the happy path ──────────────────────────────────────────────────────────

def test_a_process_that_records_a_poll_reads_back_as_polled():
    reg.record_poll("TELEGRAM_CLAUDE_BOT_SECRET", ["wdec"], service="svc")
    ev = reg.poll_state("TELEGRAM_CLAUDE_BOT_SECRET", prefix="wdec")
    assert ev.state == reg.POLLED_WITH_HANDLER
    assert ev.answerable is True
    assert ev.source == "in_process"


def test_the_claim_is_visible_to_a_DIFFERENT_process():
    """The sweep and the poll loop are different services — this is the point."""
    reg.record_poll("TELEGRAM_CLAUDE_BOT_SECRET", ["wdec"], service="svc")
    reg._LOCAL.clear()  # simulate a reader that never made the claim
    ev = reg.poll_state("TELEGRAM_CLAUDE_BOT_SECRET", prefix="wdec")
    assert ev.state == reg.POLLED_WITH_HANDLER
    assert ev.source == "heartbeat"
    assert ev.service == "svc"


# ── we LOOKED and nothing polls it ──────────────────────────────────────────

def test_no_claim_at_all_is_not_polled_not_unknown():
    ev = reg.poll_state("TELEGRAM_CLAUDE_BOT_SECRET", prefix="wdec")
    assert ev.state == reg.TOKEN_ONLY_NOT_POLLED
    assert ev.answerable is False


def test_a_stale_heartbeat_expires_the_claim():
    """A process that died stops refreshing; its assertion must not outlive it."""
    reg.record_poll("T", ["wdec"], service="svc", now=NOW - timedelta(hours=2))
    reg._LOCAL.clear()
    ev = reg.poll_state("T", prefix="wdec", now=NOW)
    assert ev.state == reg.TOKEN_ONLY_NOT_POLLED
    assert "not running" in ev.note


def test_polled_but_with_no_handler_for_THIS_prefix_is_not_answerable():
    reg.record_poll("T", ["propexp"], service="svc", now=NOW)
    ev = reg.poll_state("T", prefix="wdec", now=NOW)
    assert ev.state == reg.TOKEN_ONLY_NOT_POLLED
    # The sub-condition is never lost, even though the STATE is shared.
    assert "no handler" in ev.note and "wdec" in ev.note


def test_no_token_variable_answered_is_a_looked_and_found_nothing():
    ev = reg.poll_state(None, prefix="wdec")
    assert ev.state == reg.TOKEN_ONLY_NOT_POLLED
    assert "no token variable" in ev.note


# ── we could NOT look ───────────────────────────────────────────────────────

def test_a_malformed_claim_is_unknown_never_not_polled(tmp_path):
    path = reg.entry_path("T")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    ev = reg.poll_state("T", prefix="wdec")
    assert ev.state == reg.UNKNOWN
    assert ev.answerable is False


def test_an_undateable_claim_is_unknown_never_not_polled(tmp_path):
    """A claim we cannot DATE is a claim we cannot judge — the poller may be fine."""
    path = reg.entry_path("T")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"token_var": "T", "prefixes": ["wdec"], "heartbeat_at": "not-a-time"}),
        encoding="utf-8")
    ev = reg.poll_state("T", prefix="wdec")
    assert ev.state == reg.UNKNOWN


def test_an_unreachable_registry_root_is_unknown_never_not_polled(monkeypatch):
    """Absence is only evidence if the probe could have found a positive."""
    monkeypatch.setattr(reg, "runtime_logs_dir",
                        lambda: __import__("pathlib").Path("/nonexistent/xyz"))
    ev = reg.poll_state("T", prefix="wdec")
    assert ev.state == reg.UNKNOWN
    assert "not reachable" in ev.note


def test_a_non_object_claim_is_unknown():
    path = reg.entry_path("T")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert reg.poll_state("T", prefix="wdec").state == reg.UNKNOWN


# ── the three states are genuinely three ────────────────────────────────────

def test_only_one_state_is_answerable_so_unknown_fails_closed():
    seen = {}
    for state in reg.POLL_STATES:
        seen[state] = reg.PollEvidence(
            state=state, token_var="T", prefix="wdec", note="",
        ).answerable
    assert seen == {
        reg.POLLED_WITH_HANDLER: True,
        reg.TOKEN_ONLY_NOT_POLLED: False,
        reg.UNKNOWN: False,
    }


def test_the_producer_can_emit_every_declared_state(tmp_path, monkeypatch):
    """Producer integrity: a contract naming a state nothing emits is a dead claim."""
    emitted = set()
    # polled
    reg.record_poll("T", ["wdec"], service="s")
    emitted.add(reg.poll_state("T", prefix="wdec").state)
    # looked, not polled
    reg._LOCAL.clear()
    emitted.add(reg.poll_state("OTHER", prefix="wdec").state)
    # could not look
    p = reg.entry_path("BAD")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{", encoding="utf-8")
    emitted.add(reg.poll_state("BAD", prefix="wdec").state)
    assert emitted == set(reg.POLL_STATES)


# ── never log a token ───────────────────────────────────────────────────────

def test_no_token_value_reaches_a_note_a_banner_or_the_entry(caplog):
    secret = "8123456789:AAH-this-is-a-token-value"
    reg.record_poll("TELEGRAM_CLAUDE_BOT_SECRET", ["wdec"], service="svc")
    ev = reg.poll_state("TELEGRAM_CLAUDE_BOT_SECRET", prefix="wdec")
    banner = reg.log_poll_banner(
        "TELEGRAM_CLAUDE_BOT_SECRET", ["wdec"], service="svc")
    stored = reg.entry_path("TELEGRAM_CLAUDE_BOT_SECRET").read_text(encoding="utf-8")
    for blob in (ev.note, ev.describe(), banner, stored):
        assert secret not in blob
    # ...and the VARIABLE name is what is reported instead.
    assert "TELEGRAM_CLAUDE_BOT_SECRET" in banner


def test_the_banner_is_loud_when_there_is_no_token_at_all(caplog):
    with caplog.at_level("ERROR"):
        line = reg.log_poll_banner(None, ["wdec"], service="svc")
    assert "polls nothing" in line
    assert any(r.levelname == "ERROR" for r in caplog.records)


# ── the knobs cannot be typo'd into disabling the check ─────────────────────

@pytest.mark.parametrize("raw", ["", "   ", "abc", "0", "-5"])
def test_an_unusable_staleness_value_falls_back_to_the_default(monkeypatch, raw):
    """A zero window would expire every heartbeat and reroute every prompt."""
    monkeypatch.setenv("TELEGRAM_POLL_STALE_SECONDS", raw)
    assert reg.stale_after_seconds() == reg._DEFAULT_STALE_SECONDS


def test_a_usable_staleness_value_is_honoured(monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLL_STALE_SECONDS", "42")
    assert reg.stale_after_seconds() == 42.0


# ── failure to persist degrades the SCOPE, never the bot ────────────────────

def test_an_unwritable_registry_still_leaves_the_process_its_own_answer(monkeypatch):
    monkeypatch.setattr(reg, "runtime_logs_dir",
                        lambda: __import__("pathlib").Path("/proc/nope/nope"))
    ev = reg.record_poll("T", ["wdec"], service="svc")
    assert ev.state == reg.POLLED_WITH_HANDLER and ev.source == "in_process"
    # The claim is still true HERE, which is what keeps the sweep working.
    assert reg.poll_state("T", prefix="wdec").state == reg.POLLED_WITH_HANDLER


def test_forget_poll_clears_both_halves():
    reg.record_poll("T", ["wdec"], service="svc")
    reg.forget_poll("T")
    assert reg.poll_state("T", prefix="wdec").state == reg.TOKEN_ONLY_NOT_POLLED
