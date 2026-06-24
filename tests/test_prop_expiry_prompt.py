"""Tests for the prop ticket-expiry Yes/No prompt (close the manual-bridge loop).

Covers the detector (expired + un-acted + recency-bounded), the per-tick runner
(prompt-once idempotency via the status flip, send-failure retry), the Yes/No
callback handler (status transitions + report-prompt trigger), and the full
Yes→awaiting_report→fill-links-back lifecycle — all against an isolated
``trade_journal.db`` with the notification emitter injected so no Telegram I/O
happens.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    # Deterministic knobs.
    monkeypatch.delenv("PROP_EXPIRY_PROMPT_SECONDS", raising=False)
    monkeypatch.delenv("PROP_EXPIRY_PROMPT_MAX_AGE_HOURS", raising=False)
    return tmp_path


def _emit(ticket_id: str, *, status: str = "emitted",
          valid_until: datetime | None = None,
          signal_time: datetime | None = None) -> None:
    """Record an outbound prop ticket in the isolated journal."""
    from src.prop import prop_journal

    now = datetime.now(timezone.utc)
    vu = valid_until or (now - timedelta(minutes=30))  # already expired
    st = signal_time or (vu - timedelta(hours=1))
    prop_journal.record_ticket({
        "ticket_id": ticket_id, "account_id": "breakout_1",
        "strategy": "trend_donchian_eth", "symbol": "ETHUSDT",
        "direction": "short", "side": "Sell",
        "entry": 1717.0, "sl": 1740.0, "tp": 1650.0, "qty": 0.0167,
        "risk_usd": 75.0,
        "signal_time": st.isoformat(), "valid_until": vu.isoformat(),
        "status": status,
    })


def _status(ticket_id: str) -> str:
    from src.prop import prop_journal

    rows = prop_journal.list_tickets(limit=50)
    for r in rows:
        if r.get("ticket_id") == ticket_id:
            return r.get("status")
    raise AssertionError(f"ticket {ticket_id} not found")


# ── keyboard ───────────────────────────────────────────────────────────

def test_keyboard_callback_data_format() -> None:
    from src.prop.prop_expiry_prompt import build_expiry_keyboard

    kb = build_expiry_keyboard("prop-manual-abc123def456")
    rows = kb["inline_keyboard"]
    datas = [b["callback_data"] for b in rows[0]]
    assert datas == [
        "propexp:y:prop-manual-abc123def456",
        "propexp:n:prop-manual-abc123def456",
    ]
    # Telegram's hard 64-byte callback_data limit.
    assert all(len(d.encode()) <= 64 for d in datas)


# ── detector ─────────────────────────────────────────────────────────────

def test_expired_unacted_ticket_is_detected(isolated_env: Path) -> None:
    from src.prop.prop_expiry_prompt import find_tickets_to_prompt

    _emit("prop-manual-1")
    found = find_tickets_to_prompt()
    assert [t["ticket_id"] for t in found] == ["prop-manual-1"]


def test_already_prompted_ticket_not_redetected(isolated_env: Path) -> None:
    from src.prop.prop_expiry_prompt import find_tickets_to_prompt

    _emit("prop-manual-1", status="expiry_prompted")
    assert find_tickets_to_prompt() == []


def test_still_valid_ticket_not_detected(isolated_env: Path) -> None:
    from src.prop.prop_expiry_prompt import find_tickets_to_prompt

    future = datetime.now(timezone.utc) + timedelta(minutes=30)
    _emit("prop-manual-1", valid_until=future)
    assert find_tickets_to_prompt() == []


def test_ancient_ticket_excluded_by_recency_guard(isolated_env: Path) -> None:
    from src.prop.prop_expiry_prompt import find_tickets_to_prompt

    old_vu = datetime.now(timezone.utc) - timedelta(hours=48)
    _emit("prop-manual-old", valid_until=old_vu)
    # default max-age is 12h → a 48h-stale ticket is too old to ask about.
    assert find_tickets_to_prompt() == []


# ── per-tick runner ───────────────────────────────────────────────────────

def test_run_prompts_and_flips_status(isolated_env: Path) -> None:
    from src.prop.prop_expiry_prompt import run_prop_expiry_prompts

    _emit("prop-manual-1")
    seen = []
    stats = run_prop_expiry_prompts(emitter=lambda t: seen.append(t["ticket_id"]) or True)
    assert stats["prompted"] == 1
    assert seen == ["prop-manual-1"]
    assert _status("prop-manual-1") == "expiry_prompted"

    # Second tick: the flip makes it idempotent — no re-prompt.
    seen.clear()
    stats2 = run_prop_expiry_prompts(emitter=lambda t: seen.append(t["ticket_id"]) or True)
    assert stats2["prompted"] == 0
    assert seen == []


def test_send_failure_leaves_status_emitted_for_retry(isolated_env: Path) -> None:
    from src.prop.prop_expiry_prompt import run_prop_expiry_prompts

    _emit("prop-manual-1")
    stats = run_prop_expiry_prompts(emitter=lambda t: False)  # delivery failed
    assert stats["prompted"] == 0
    assert stats["failed"] == 1
    assert _status("prop-manual-1") == "emitted"  # NOT flipped → retries next tick


def test_paused_via_env(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.prop.prop_expiry_prompt import run_prop_expiry_prompts

    monkeypatch.setenv("PROP_EXPIRY_PROMPT_SECONDS", "0")
    _emit("prop-manual-1")
    stats = run_prop_expiry_prompts(emitter=lambda t: True)
    assert stats["paused"] is True
    assert _status("prop-manual-1") == "emitted"


# ── callback handler ──────────────────────────────────────────────────────

def test_callback_no_marks_expired(isolated_env: Path) -> None:
    from src.prop.prop_expiry_prompt import handle_expiry_callback

    _emit("prop-manual-1", status="expiry_prompted")
    result = handle_expiry_callback("propexp:n:prop-manual-1")
    assert result["answer"] == "no"
    assert result["send_prompt"] is False
    assert _status("prop-manual-1") == "expired"


def test_callback_yes_awaits_report_and_sends_prompt(isolated_env: Path) -> None:
    from src.prop.prop_expiry_prompt import handle_expiry_callback

    _emit("prop-manual-1", status="expiry_prompted")
    result = handle_expiry_callback("propexp:y:prop-manual-1")
    assert result["answer"] == "yes"
    assert result["send_prompt"] is True
    assert _status("prop-manual-1") == "awaiting_report"


def test_callback_ignores_non_propexp() -> None:
    from src.prop.prop_expiry_prompt import handle_expiry_callback

    assert handle_expiry_callback("comms:foo") is None
    assert handle_expiry_callback("propexp:bad") is None
    assert handle_expiry_callback("") is None


def test_send_test_prompt_creates_throwaway_ticket(isolated_env: Path) -> None:
    from src.prop import prop_journal
    from src.prop.prop_expiry_prompt import handle_expiry_callback, send_test_prompt

    sent = []
    tid = send_test_prompt(emitter=lambda t: sent.append(t["ticket_id"]) or True)
    assert tid is not None and tid.startswith("prop-test-")
    assert sent == [tid]
    # The throwaway ticket is journaled as emitted, already expired.
    assert _status(tid) == "emitted"
    # Clicking the buttons drives the same lifecycle on the test ticket only.
    handle_expiry_callback(f"propexp:n:{tid}")
    assert _status(tid) == "expired"


def test_send_test_prompt_returns_none_on_send_failure(isolated_env: Path) -> None:
    from src.prop.prop_expiry_prompt import send_test_prompt

    assert send_test_prompt(emitter=lambda t: False) is None


def test_yes_then_fill_links_back_to_ticket(isolated_env: Path) -> None:
    """Full lifecycle: Yes → awaiting_report → an inbound fill links + flips it."""
    from src.prop import prop_reconcile
    from src.prop.prop_expiry_prompt import handle_expiry_callback

    _emit("prop-manual-1", status="expiry_prompted")
    handle_expiry_callback("propexp:y:prop-manual-1")  # → awaiting_report

    # An inbound open fill (no explicit ticket_id) must still match the
    # awaiting_report ticket by account+symbol+direction.
    matched = prop_reconcile.match_fill_to_ticket({
        "account_id": "breakout_1", "symbol": "ETHUSDT", "direction": "short",
    })
    assert matched == "prop-manual-1"
