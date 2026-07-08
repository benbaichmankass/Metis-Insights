"""Tests for the prop monitoring pulse (still-monitoring heartbeat).

Covers open-position derivation from ``prop_fills``, the per-position cadence
gate, immediate first-seen pulse, interval skipping, and closed-position state
pruning — all against an isolated ``trade_journal.db`` + runtime-logs dir, with
the notification emitter injected so no FCM / Telegram I/O happens.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    # Redirect runtime_logs (where the pulse state file lives) into tmp.
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    return tmp_path


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


# ── open-position derivation ──────────────────────────────────────────

def test_open_fill_is_returned(isolated_env: Path) -> None:
    from src.prop import prop_journal, prop_monitor_pulse

    prop_journal.insert_fill({
        "account_id": "breakout_1", "ticket_id": "prop-1",
        "symbol": "BTCUSDT", "direction": "long", "qty": 0.01,
        "entry_price": 80000, "status": "open",
    })
    positions = prop_monitor_pulse.find_open_prop_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "BTCUSDT"
    assert positions[0]["status"] == "open"


def test_closed_fill_excluded(isolated_env: Path) -> None:
    from src.prop import prop_journal, prop_monitor_pulse

    prop_journal.insert_fill({
        "account_id": "breakout_1", "ticket_id": "prop-1",
        "symbol": "BTCUSDT", "direction": "long", "status": "closed",
        "pnl": 12.0,
    })
    assert prop_monitor_pulse.find_open_prop_positions() == []


def test_latest_status_wins(isolated_env: Path) -> None:
    """An open fill later superseded by a close is no longer open."""
    from src.prop import prop_journal, prop_monitor_pulse

    prop_journal.insert_fill({
        "account_id": "breakout_1", "ticket_id": "prop-1",
        "symbol": "BTCUSDT", "direction": "long", "status": "open",
    })
    prop_journal.insert_fill({
        "account_id": "breakout_1", "ticket_id": "prop-1",
        "symbol": "BTCUSDT", "direction": "long", "status": "closed",
    })
    assert prop_monitor_pulse.find_open_prop_positions() == []


def test_levels_enriched_from_ticket(isolated_env: Path) -> None:
    from src.prop import prop_journal, prop_monitor_pulse

    prop_journal.record_ticket({
        "ticket_id": "prop-1", "account_id": "breakout_1",
        "symbol": "BTCUSDT", "direction": "long",
        "entry": 80000, "sl": 79000, "tp": 82000, "status": "filled",
    })
    prop_journal.insert_fill({
        "account_id": "breakout_1", "ticket_id": "prop-1",
        "symbol": "BTCUSDT", "direction": "long", "status": "open",
    })
    pos = prop_monitor_pulse.find_open_prop_positions()[0]
    assert pos["sl"] == 79000
    assert pos["tp"] == 82000


# ── direction-alias collapse (BL-20260708-PROP-PULSE-DIRECTION-ALIAS) ──

def test_buy_open_then_long_close_is_not_open(isolated_env: Path) -> None:
    """A position opened as ``buy`` and closed as ``long`` must NOT read open.

    The manual bridge reports the open in broker vocabulary (``buy``) and the
    close in order-package vocabulary (``long``). Before the canonicalization
    fix these landed under different akd keys, so the stale ``buy`` open fill
    lingered as a phantom-open pulse — the 2026-07-08 ETHUSDT "still monitoring
    a closed trade" incident.
    """
    from src.prop import prop_journal, prop_monitor_pulse

    # id 1: opened, reported as "buy" (broker vocabulary).
    prop_journal.insert_fill({
        "account_id": "breakout_1", "ticket_id": "prop-1",
        "symbol": "ETHUSDT", "direction": "buy", "qty": 1.57,
        "entry_price": 1767.71, "status": "filled",
    })
    # id 2 (newest): closed, reported as "long" (order-package vocabulary).
    prop_journal.insert_fill({
        "account_id": "breakout_1", "ticket_id": "prop-1",
        "symbol": "ETHUSDT", "direction": "long", "status": "closed",
        "pnl": -78.76,
    })
    assert prop_monitor_pulse.find_open_prop_positions() == []


def test_buy_and_long_open_fills_collapse_to_one_position(isolated_env: Path) -> None:
    """``buy`` + ``long`` open fills for one symbol are ONE position, not two."""
    from src.prop import prop_journal, prop_monitor_pulse

    prop_journal.insert_fill({
        "account_id": "breakout_1", "ticket_id": "prop-1",
        "symbol": "ETHUSDT", "direction": "buy", "status": "filled",
    })
    prop_journal.insert_fill({
        "account_id": "breakout_1", "ticket_id": "prop-1",
        "symbol": "ETHUSDT", "direction": "long", "status": "open",
    })
    positions = prop_monitor_pulse.find_open_prop_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "ETHUSDT"


def test_canonical_direction_maps_aliases() -> None:
    from src.prop import prop_monitor_pulse as p

    assert p._canonical_direction("buy") == "long"
    assert p._canonical_direction("SELL") == "short"
    assert p._canonical_direction("Long") == "long"
    assert p._canonical_direction("short") == "short"
    assert p._canonical_direction(None) == ""


# ── cadence gate ──────────────────────────────────────────────────────

def test_fires_on_first_seen_then_skips_within_interval(isolated_env: Path) -> None:
    from src.prop import prop_journal, prop_monitor_pulse

    prop_journal.insert_fill({
        "account_id": "breakout_1", "ticket_id": "prop-1",
        "symbol": "BTCUSDT", "direction": "long", "status": "open",
    })
    fired: list = []
    t0 = _utc("2026-06-22T12:00:00")

    s1 = prop_monitor_pulse.run_prop_monitor_pulse(
        now=t0, interval_seconds=900, emitter=lambda p: fired.append(p) or {})
    assert s1["fired"] == 1 and s1["open"] == 1

    # 5 min later — still inside the 15-min window → no new pulse.
    s2 = prop_monitor_pulse.run_prop_monitor_pulse(
        now=t0 + timedelta(minutes=5), interval_seconds=900,
        emitter=lambda p: fired.append(p) or {})
    assert s2["fired"] == 0 and s2["skipped"] == 1
    assert len(fired) == 1


def test_fires_again_after_interval(isolated_env: Path) -> None:
    from src.prop import prop_journal, prop_monitor_pulse

    prop_journal.insert_fill({
        "account_id": "breakout_1", "ticket_id": "prop-1",
        "symbol": "BTCUSDT", "direction": "long", "status": "open",
    })
    fired: list = []
    t0 = _utc("2026-06-22T12:00:00")

    def emit(p):
        fired.append(p)
        return {}

    prop_monitor_pulse.run_prop_monitor_pulse(now=t0, interval_seconds=900, emitter=emit)
    s = prop_monitor_pulse.run_prop_monitor_pulse(
        now=t0 + timedelta(minutes=16), interval_seconds=900, emitter=emit)
    assert s["fired"] == 1
    assert len(fired) == 2


def test_paused_when_interval_non_positive(isolated_env: Path) -> None:
    from src.prop import prop_journal, prop_monitor_pulse

    prop_journal.insert_fill({
        "account_id": "breakout_1", "ticket_id": "prop-1",
        "symbol": "BTCUSDT", "direction": "long", "status": "open",
    })
    fired: list = []
    s = prop_monitor_pulse.run_prop_monitor_pulse(
        interval_seconds=0, emitter=lambda p: fired.append(p) or {})
    assert s["paused"] is True and s["fired"] == 0
    assert fired == []


def test_closed_position_pruned_from_state(isolated_env: Path) -> None:
    from src.prop import prop_journal, prop_monitor_pulse

    prop_journal.insert_fill({
        "account_id": "breakout_1", "ticket_id": "prop-1",
        "symbol": "BTCUSDT", "direction": "long", "status": "open",
    })
    t0 = _utc("2026-06-22T12:00:00")

    def emit(p):
        return {}

    prop_monitor_pulse.run_prop_monitor_pulse(now=t0, interval_seconds=900, emitter=emit)
    # Consolidated model: state carries a single global timestamp key.
    assert prop_monitor_pulse._CONSOLIDATED_KEY in prop_monitor_pulse._load_state()

    # Position closes — nothing open → the consolidated state resets to empty.
    prop_journal.insert_fill({
        "account_id": "breakout_1", "ticket_id": "prop-1",
        "symbol": "BTCUSDT", "direction": "long", "status": "closed",
    })
    s = prop_monitor_pulse.run_prop_monitor_pulse(
        now=t0 + timedelta(minutes=20), interval_seconds=900, emitter=emit)
    assert s["open"] == 0
    assert prop_monitor_pulse._load_state() == {}


def test_consolidated_single_ping_lists_all_open(isolated_env: Path) -> None:
    """Two open prop positions → ONE emitter call receiving both."""
    from src.prop import prop_journal, prop_monitor_pulse

    for sym in ("BTCUSDT", "ETHUSDT"):
        prop_journal.insert_fill({
            "account_id": "breakout_1", "ticket_id": f"prop-{sym}",
            "symbol": sym, "direction": "long", "status": "open",
        })
    calls: list = []
    s = prop_monitor_pulse.run_prop_monitor_pulse(
        now=_utc("2026-06-22T12:00:00"), interval_seconds=3600,
        emitter=lambda positions: calls.append(positions) or {})
    assert s["fired"] == 1 and s["open"] == 2
    # Exactly one emitter call, receiving the FULL list of open positions.
    assert len(calls) == 1
    assert {p["symbol"] for p in calls[0]} == {"BTCUSDT", "ETHUSDT"}


def test_default_emitter_is_breakout_notify(isolated_env: Path, monkeypatch) -> None:
    """With no injected emitter, it routes through the consolidated emitter (stubbed)."""
    from src.prop import prop_journal, prop_monitor_pulse
    from src.prop import breakout_notify

    prop_journal.insert_fill({
        "account_id": "breakout_1", "ticket_id": "prop-1",
        "symbol": "BTCUSDT", "direction": "long", "status": "open",
    })
    calls: list = []
    monkeypatch.setattr(breakout_notify, "emit_prop_monitor_consolidated",
                        lambda positions, **k: calls.append(positions) or {"push": True, "telegram": True})
    s = prop_monitor_pulse.run_prop_monitor_pulse(interval_seconds=3600)
    assert s["fired"] == 1 and len(calls) == 1
