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

def test_run_notifies_once_and_expires_the_ticket(isolated_env: Path) -> None:
    # Operator directive 2026-10-07: expired with no trade logged = not placed.
    # The manual ticket ends `expired` (not `expiry_prompted`) after one notice.
    from src.prop.prop_expiry_prompt import run_prop_expiry_prompts

    _emit("prop-manual-1")
    seen = []
    stats = run_prop_expiry_prompts(emitter=lambda t: seen.append(t["ticket_id"]) or True)
    assert stats["prompted"] == 1
    assert seen == ["prop-manual-1"]
    assert _status("prop-manual-1") == "expired"

    # Second tick: the ticket is terminal — no second notice.
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


def test_paused_via_env_still_expires_silently(isolated_env: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    # The pause knob pauses the NOTICE only; the not-placed disposition holds.
    from src.prop.prop_expiry_prompt import run_prop_expiry_prompts

    monkeypatch.setenv("PROP_EXPIRY_PROMPT_SECONDS", "0")
    _emit("prop-manual-1")
    sent = []
    stats = run_prop_expiry_prompts(emitter=lambda t: sent.append(t) or True)
    assert stats["paused"] is True and sent == []
    assert stats["expired"] == 1
    assert _status("prop-manual-1") == "expired"


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


def test_ticket_emit_attaches_yes_no_buttons(monkeypatch: pytest.MonkeyPatch) -> None:
    """A freshly-emitted prop ticket carries the Yes/No place-decision buttons."""
    from datetime import datetime, timezone

    from src.prop import breakout_notify
    from src.prop.breakout_ticket import BreakoutSignal, TicketConfig, build_ticket

    sig = BreakoutSignal(
        strategy="trend_donchian_sol", symbol="SOLUSDT", direction="long",
        entry=150.0, sl=145.5, tp=175.5, timeframe="1h",
        signal_time=datetime.now(timezone.utc))
    ticket = build_ticket(sig, TicketConfig(account_size_usd=5000.0, risk_pct=1.5))

    captured = {}

    def _fake_send(text, **kwargs):
        captured["reply_markup"] = kwargs.get("reply_markup")
        return True

    monkeypatch.setattr("src.runtime.notify.send_telegram_direct", _fake_send)

    out = breakout_notify.emit_prop_signal(
        ticket, push=False, telegram=True,
        account_id="breakout_1", ticket_id="prop-manual-deadbeef0001")
    assert out["telegram"] is True
    kb = captured["reply_markup"]
    assert kb is not None
    datas = [b["callback_data"] for b in kb["inline_keyboard"][0]]
    assert datas == [
        "propexp:y:prop-manual-deadbeef0001",
        "propexp:n:prop-manual-deadbeef0001",
    ]


def test_send_test_prompt_creates_throwaway_ticket(isolated_env: Path) -> None:
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


# ── VELOTRADE-TICKET: a REST-executed account has no human to ask ──────


def _emit_for(ticket_id: str, account_id: str, *, expired_ago: timedelta = timedelta(minutes=10),
              status: str = "emitted") -> None:
    from src.prop import prop_journal

    now = datetime.now(timezone.utc)
    vu = now - expired_ago
    prop_journal.record_ticket({
        "ticket_id": ticket_id, "account_id": account_id,
        "strategy": "trend_donchian_eth_prop", "symbol": "ETHUSDT",
        "direction": "short", "side": "Sell",
        "entry": 2672.0, "sl": 2701.107, "tp": 2497.357, "qty": 1.7178,
        "risk_usd": 50.0,
        "signal_time": (vu - timedelta(hours=1)).isoformat(),
        "valid_until": vu.isoformat(), "status": status,
    })


def _platforms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.prop.platform as plat

    p = tmp_path / "prop_platforms.yaml"
    p.write_text(
        "accounts:\n"
        "  breakout_1: {platform: dxtrade}\n"
        "  velotrade_1: {platform: dxtrade_api, login_url: https://x.example/dxsca-web}\n"
        "phone_accounts:\n"
        "  breakout_2: {platform: breakout_phone}\n")
    monkeypatch.setattr(plat, "PLATFORMS_PATH", p)


def test_rest_account_ticket_is_not_prompted_and_left_to_its_executor(
        isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Inside MACHINE_EXPIRY_GRACE the REST executor gets the first word (its
    # `skipped expired unplaced; …` report, #16947); the ticket stays emitted.
    from src.prop.prop_expiry_prompt import run_prop_expiry_prompts

    _platforms(isolated_env, monkeypatch)
    _emit_for("prop-manual-rest", "velotrade_1")
    sent = []
    stats = run_prop_expiry_prompts(emitter=lambda t: sent.append(t) or True)
    assert sent == [] and stats["prompted"] == 0
    assert stats["rest_left_to_executor"] == 1
    assert _status("prop-manual-rest") == "emitted"


def test_rest_account_expired_ticket_no_longer_blocks_the_next_signal(
        isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.prop.breakout_executor import _reticket_suppress_reason
    from src.prop.prop_expiry_prompt import run_prop_expiry_prompts

    _platforms(isolated_env, monkeypatch)
    _emit_for("prop-manual-rest", "velotrade_1")
    run_prop_expiry_prompts(emitter=lambda t: True)
    assert _reticket_suppress_reason("velotrade_1", "ETHUSDT", "short") is None


@pytest.mark.parametrize("account_id", ["velotrade_1", "breakout_2"])
def test_machine_account_ticket_past_the_grace_is_expired_without_telegram(
        isolated_env: Path, monkeypatch: pytest.MonkeyPatch, account_id: str) -> None:
    # REST executor off/read_only, or a phone that never claimed: the trader
    # ends the ticket `expired` (terminal, not placed) with no prompt.
    from src.prop.breakout_executor import _reticket_suppress_reason
    from src.prop.prop_expiry_prompt import MACHINE_EXPIRY_GRACE, run_prop_expiry_prompts

    _platforms(isolated_env, monkeypatch)
    _emit_for("prop-manual-m", account_id, expired_ago=MACHINE_EXPIRY_GRACE + timedelta(minutes=1))
    sent = []
    stats = run_prop_expiry_prompts(emitter=lambda t: sent.append(t) or True)
    assert sent == [] and stats["machine_expired"] == 1
    assert _status("prop-manual-m") == "expired"
    assert _reticket_suppress_reason(account_id, "ETHUSDT", "short") is None


@pytest.mark.parametrize("status", ["expiry_prompted", "invalidated_prompted"])
def test_the_stuck_velotrade_prompt_is_released(
        isolated_env: Path, monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    # The live case (prop-manual-4fa7266cfcf0): a REST ticket already parked in
    # a prompt state before this change is ended `expired` on the next tick.
    from src.prop.prop_expiry_prompt import run_prop_expiry_prompts

    _platforms(isolated_env, monkeypatch)
    _emit_for("prop-manual-4fa7266cfcf0", "velotrade_1", status=status)
    stats = run_prop_expiry_prompts(emitter=lambda t: True)
    assert stats["machine_expired"] == 1
    assert _status("prop-manual-4fa7266cfcf0") == "expired"


def test_manual_account_gets_the_notice_and_the_ticket_stops_blocking(
        isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.prop.breakout_executor import _reticket_suppress_reason
    from src.prop.prop_expiry_prompt import run_prop_expiry_prompts

    _platforms(isolated_env, monkeypatch)
    _emit_for("prop-manual-man", "breakout_1")
    sent = []
    stats = run_prop_expiry_prompts(emitter=lambda t: sent.append(t) or True)
    assert [t["ticket_id"] for t in sent] == ["prop-manual-man"] and stats["prompted"] == 1
    assert _status("prop-manual-man") == "expired"
    assert _reticket_suppress_reason("breakout_1", "ETHUSDT", "short") is None


def test_unreadable_platform_file_treats_every_account_as_manual(
        isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.prop.platform as plat
    from src.prop.prop_expiry_prompt import run_prop_expiry_prompts

    monkeypatch.setattr(plat, "PLATFORMS_PATH", isolated_env / "missing.yaml")
    _emit_for("prop-manual-rest", "velotrade_1")
    stats = run_prop_expiry_prompts(emitter=lambda t: True)
    assert stats["prompted"] == 1 and _status("prop-manual-rest") == "expired"
