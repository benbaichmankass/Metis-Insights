"""Boot-audit observability ping (Sprint S-021, PR 2 of 3).

Three contracts under test:

1. **No open packages → log only, no Telegram.**
   Empty DB → ``report_open_packages_on_boot`` returns ``{}`` or all-zeros,
   ``send_telegram_direct`` is NOT called, log captures "0 open".
2. **Open linked packages → Telegram ping.**
   DB seeded with linked open packages across two strategies →
   ``send_telegram_direct`` called once with the counts in the body.
3. **DB unavailable → no exception, returns ``{}``.**
   Non-existent ``db_path`` → function returns ``{}``, nothing raises.
"""
from __future__ import annotations

import logging

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _insert_pkg(db, *, pkg_id, strategy="vwap", status="open",
                linked_trade_id=None):
    db.insert_order_package({
        "order_package_id": pkg_id,
        "strategy_name": strategy,
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry": 80_000.0,
        "sl": 79_500.0,
        "tp": 80_500.0,
        "confidence": 0.5,
        "status": status,
        "linked_trade_id": linked_trade_id,
        "meta": {},
    })


@pytest.fixture
def tmp_journal(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    from src.units.db.database import Database
    return Database(db_path=str(db_path))


# ---------------------------------------------------------------------------
# Contract 1: no open packages → log only, no Telegram
# ---------------------------------------------------------------------------


def test_no_open_packages_logs_only(tmp_journal, monkeypatch, caplog):
    """Empty DB: function returns all-zero counts, Telegram is not called."""
    telegram_calls: list[str] = []

    def _fake_send(msg, *, parse_mode=None):
        telegram_calls.append(msg)

    monkeypatch.setattr("src.runtime.boot_audit.send_telegram_direct", _fake_send,
                        raising=False)
    monkeypatch.setattr(
        "src.runtime.boot_audit._load_strategy_names",
        lambda: ["vwap", "turtle_soup"],
    )

    from src.runtime.boot_audit import report_open_packages_on_boot

    with caplog.at_level(logging.INFO, logger="src.runtime.boot_audit"):
        result = report_open_packages_on_boot()

    total = sum(result.values())
    assert total == 0, f"Expected 0 open packages, got {result}"
    assert not telegram_calls, "Telegram must NOT be called when there are no open packages"
    assert "0 open package" in caplog.text.lower() or "boot_audit" in caplog.text


# ---------------------------------------------------------------------------
# Contract 2: open linked packages → Telegram ping with counts
# ---------------------------------------------------------------------------


def test_open_packages_pings_telegram(tmp_journal, monkeypatch):
    """Seeded DB with linked packages → send_telegram_direct called once."""
    _insert_pkg(tmp_journal, pkg_id="pkg-vwap-1", strategy="vwap",
                linked_trade_id=1)
    _insert_pkg(tmp_journal, pkg_id="pkg-turtle-1", strategy="turtle_soup",
                linked_trade_id=2)

    telegram_calls: list[str] = []

    def _fake_send(msg, *, parse_mode=None):
        assert parse_mode is None, "boot_audit must use plain-text (no parse_mode)"
        telegram_calls.append(msg)

    monkeypatch.setattr("src.runtime.boot_audit._load_strategy_names",
                        lambda: ["vwap", "turtle_soup"])

    import importlib
    import src.runtime.boot_audit as _ba
    monkeypatch.setattr(_ba, "_send_boot_ping",
                        lambda counts, total: _fake_send(
                            "\n".join([f"{s}: {n}" for s, n in counts.items()]),
                            parse_mode=None,
                        ))

    from src.runtime.boot_audit import report_open_packages_on_boot
    result = report_open_packages_on_boot()

    assert result.get("vwap", 0) == 1, f"Expected vwap=1, got {result}"
    assert result.get("turtle_soup", 0) == 1, f"Expected turtle_soup=1, got {result}"
    assert len(telegram_calls) == 1, "Expected exactly one Telegram call"
    body = telegram_calls[0]
    assert "vwap" in body
    assert "turtle_soup" in body


def test_open_packages_telegram_parse_mode_is_none(tmp_journal, monkeypatch):
    """Telegram call must use parse_mode=None — CLAUDE.md § 'Always do'."""
    _insert_pkg(tmp_journal, pkg_id="pkg-vwap-2", strategy="vwap",
                linked_trade_id=10)

    captured_kwargs: list[dict] = []

    def _fake_send(msg, *, parse_mode="SENTINEL"):
        captured_kwargs.append({"parse_mode": parse_mode})

    monkeypatch.setattr("src.runtime.boot_audit._load_strategy_names",
                        lambda: ["vwap"])
    import src.runtime.boot_audit as _ba
    monkeypatch.setattr(_ba, "_send_boot_ping",
                        lambda counts, total: _fake_send("ping", parse_mode=None))

    from src.runtime.boot_audit import report_open_packages_on_boot
    report_open_packages_on_boot()

    assert captured_kwargs, "send_telegram_direct was not called"
    assert captured_kwargs[0]["parse_mode"] is None


# ---------------------------------------------------------------------------
# Contract 3: DB unavailable → no exception, returns {}
# ---------------------------------------------------------------------------


def test_db_unavailable_no_raise(tmp_path, monkeypatch):
    """Non-existent DB path: function returns {} and does not raise."""
    bad_path = tmp_path / "does_not_exist" / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(bad_path))
    monkeypatch.setattr("src.runtime.boot_audit._load_strategy_names",
                        lambda: ["vwap"])

    from src.runtime.boot_audit import report_open_packages_on_boot
    result = report_open_packages_on_boot()

    assert isinstance(result, dict), "Must return a dict even on DB failure"
