"""Tests for the prop ticket price-invalidation prompt.

When an emitted-but-unreported prop ticket's price leaves its ``[SL, TP]`` band,
the bot proactively warns "do NOT place it if you haven't already" and re-asks
the Yes/No — before the slower ``valid_until`` timeout path would. Covers the
bracket-crossing predicate, the detector (emitted + recency-bounded + has a
bracket), the per-tick runner (prompt only when price is beyond a bracket,
prompt-once idempotency via the ``invalidated_prompted`` status flip, send-failure
retry), and that the shared ``propexp:*`` Yes/No callback still drives it — all
against an isolated ``trade_journal.db`` with the price fetch + emitter injected
so no market or Telegram I/O happens.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    monkeypatch.delenv("PROP_INVALIDATION_PROMPT_SECONDS", raising=False)
    monkeypatch.delenv("PROP_INVALIDATION_PROMPT_MAX_AGE_HOURS", raising=False)
    return tmp_path


def _emit(ticket_id: str, *, status: str = "emitted",
          signal_time: datetime | None = None,
          sl: float | None = 1740.0, tp: float | None = 1650.0) -> None:
    """Record an outbound SHORT ETHUSDT prop ticket in the isolated journal."""
    from src.prop import prop_journal

    now = datetime.now(timezone.utc)
    st = signal_time or (now - timedelta(minutes=5))
    prop_journal.record_ticket({
        "ticket_id": ticket_id, "account_id": "breakout_1",
        "strategy": "trend_donchian_eth", "symbol": "ETHUSDT",
        "direction": "short", "side": "Sell",
        "entry": 1717.0, "sl": sl, "tp": tp, "qty": 0.0167,
        "risk_usd": 75.0,
        "signal_time": st.isoformat(),
        "valid_until": (now + timedelta(hours=1)).isoformat(),  # NOT yet expired
        "status": status,
    })


def _status(ticket_id: str) -> str:
    from src.prop import prop_journal

    for r in prop_journal.list_tickets(limit=50):
        if r.get("ticket_id") == ticket_id:
            return r.get("status")
    raise AssertionError(f"ticket {ticket_id} not found")


# ── bracket-crossing predicate ───────────────────────────────────────────

def test_bracket_invalidation_short() -> None:
    from src.prop.prop_invalidation_prompt import bracket_invalidation

    # SHORT entry 1717, SL 1740, TP 1650.
    assert bracket_invalidation("short", 1700.0, 1740.0, 1650.0) is None   # inside band
    assert bracket_invalidation("short", 1745.0, 1740.0, 1650.0) == "sl"   # ran up to SL
    assert bracket_invalidation("short", 1648.0, 1740.0, 1650.0) == "tp"   # ran down to TP


def test_bracket_invalidation_long() -> None:
    from src.prop.prop_invalidation_prompt import bracket_invalidation

    # LONG entry 100, SL 90, TP 120.
    assert bracket_invalidation("long", 105.0, 90.0, 120.0) is None
    assert bracket_invalidation("long", 89.0, 90.0, 120.0) == "sl"
    assert bracket_invalidation("long", 121.0, 90.0, 120.0) == "tp"


def test_bracket_invalidation_missing_bracket() -> None:
    from src.prop.prop_invalidation_prompt import bracket_invalidation

    # A missing/zero bracket can't be crossed.
    assert bracket_invalidation("short", 1745.0, None, 1650.0) is None  # no SL to cross
    assert bracket_invalidation("short", 1648.0, 1740.0, None) is None  # no TP to cross
    assert bracket_invalidation("short", 1745.0, 0, 1650.0) is None


# ── detector ─────────────────────────────────────────────────────────────

def test_detector_finds_emitted_with_bracket(isolated_env: Path) -> None:
    from src.prop.prop_invalidation_prompt import find_tickets_to_check

    _emit("t-emitted")
    _emit("t-placed", status="placed")            # not 'emitted' → skipped
    _emit("t-nobracket", sl=None, tp=None)        # no bracket → skipped
    ids = {t["ticket_id"] for t in find_tickets_to_check()}
    assert ids == {"t-emitted"}


def test_detector_recency_bound(isolated_env: Path,
                                monkeypatch: pytest.MonkeyPatch) -> None:
    from src.prop.prop_invalidation_prompt import find_tickets_to_check

    monkeypatch.setenv("PROP_INVALIDATION_PROMPT_MAX_AGE_HOURS", "12")
    _emit("t-fresh", signal_time=datetime.now(timezone.utc) - timedelta(hours=1))
    _emit("t-ancient", signal_time=datetime.now(timezone.utc) - timedelta(hours=48))
    ids = {t["ticket_id"] for t in find_tickets_to_check()}
    assert ids == {"t-fresh"}  # ancient one is the timeout path's job


# ── runner ───────────────────────────────────────────────────────────────

def test_runner_prompts_and_flips_when_beyond_bracket(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.prop import prop_invalidation_prompt as mod

    _emit("t1")
    sent: list = []

    def emitter(ticket, price, which):
        sent.append((ticket["ticket_id"], price, which))
        return True

    # Price ran up to the SHORT's SL (1740) → invalidated. Inject the price
    # fetch deterministically so no market I/O happens.
    monkeypatch.setattr(mod, "_fetch_current_price", lambda symbol, settings: 1745.0)
    stats = mod.run_prop_invalidation_prompts(settings={}, emitter=emitter)

    assert stats["invalidated"] == 1
    assert stats["prompted"] == 1
    assert sent and sent[0][0] == "t1" and sent[0][2] == "sl"
    assert _status("t1") == "invalidated_prompted"


def test_runner_no_prompt_when_inside_band(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.prop import prop_invalidation_prompt as mod

    _emit("t2")
    calls: list = []
    monkeypatch.setattr(mod, "_fetch_current_price",
                        lambda symbol, settings: 1700.0)  # inside [1650,1740]
    stats = mod.run_prop_invalidation_prompts(
        settings={}, emitter=lambda t, p, w: calls.append(1) or True)
    assert stats["checked"] == 1
    assert stats["invalidated"] == 0
    assert not calls
    assert _status("t2") == "emitted"  # untouched — still a live setup


def test_runner_send_failure_leaves_emitted(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.prop import prop_invalidation_prompt as mod

    _emit("t3")
    monkeypatch.setattr(mod, "_fetch_current_price",
                        lambda symbol, settings: 1745.0)  # beyond SL
    stats = mod.run_prop_invalidation_prompts(
        settings={}, emitter=lambda t, p, w: False)  # delivery failed
    assert stats["invalidated"] == 1
    assert stats["failed"] == 1
    assert _status("t3") == "emitted"  # not flipped → retried next tick


def test_runner_paused(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.prop import prop_invalidation_prompt as mod

    monkeypatch.setenv("PROP_INVALIDATION_PROMPT_SECONDS", "0")
    _emit("t4")
    stats = mod.run_prop_invalidation_prompts(settings={}, emitter=lambda *a: True)
    assert stats["paused"] is True
    assert _status("t4") == "emitted"


def test_prompted_ticket_not_re_detected(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once flipped to invalidated_prompted, it drops out of the emitted-only scan."""
    from src.prop import prop_invalidation_prompt as mod

    _emit("t5")
    monkeypatch.setattr(mod, "_fetch_current_price", lambda symbol, settings: 1745.0)
    mod.run_prop_invalidation_prompts(settings={}, emitter=lambda *a: True)
    assert _status("t5") == "invalidated_prompted"
    # Second pass finds nothing (idempotent — no longer 'emitted').
    assert [t["ticket_id"] for t in mod.find_tickets_to_check()] == []


# ── Yes/No callback reuse (shared propexp:* handler) ──────────────────────

def test_no_marks_expired(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.prop import prop_invalidation_prompt as mod
    from src.prop.prop_expiry_prompt import handle_expiry_callback

    _emit("t6")
    monkeypatch.setattr(mod, "_fetch_current_price", lambda symbol, settings: 1745.0)
    mod.run_prop_invalidation_prompts(settings={}, emitter=lambda *a: True)
    res = handle_expiry_callback("propexp:n:t6")
    assert res and res["answer"] == "no"
    assert _status("t6") == "expired"


def test_yes_moves_to_awaiting_report(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.prop import prop_invalidation_prompt as mod
    from src.prop.prop_expiry_prompt import handle_expiry_callback

    _emit("t7")
    monkeypatch.setattr(mod, "_fetch_current_price", lambda symbol, settings: 1745.0)
    mod.run_prop_invalidation_prompts(settings={}, emitter=lambda *a: True)
    res = handle_expiry_callback("propexp:y:t7")
    assert res and res["answer"] == "yes" and res["send_prompt"] is True
    assert _status("t7") == "awaiting_report"


def test_directly_pasted_fill_links_to_invalidated_prompted(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fill pasted while still invalidated_prompted must link (operator ignored buttons)."""
    from src.prop import prop_invalidation_prompt as mod
    from src.prop.prop_reconcile import match_fill_to_ticket

    _emit("t8")
    monkeypatch.setattr(mod, "_fetch_current_price", lambda symbol, settings: 1745.0)
    mod.run_prop_invalidation_prompts(settings={}, emitter=lambda *a: True)
    assert _status("t8") == "invalidated_prompted"
    linked = match_fill_to_ticket({
        "account_id": "breakout_1", "symbol": "ETHUSDT",
        "direction": "short", "status": "open",
    })
    assert linked == "t8"
