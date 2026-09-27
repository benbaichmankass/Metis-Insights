"""FIX-CA-11 (CA-A07-testping-journals-and-can-suppress-real-trade).

``scripts/prop/send_test_ping.py`` (the Tier-1 ``send-prop-test-ping``
system-action) drives the real ``emit_prop_ticket`` path. That path journals
the ticket as ``status='emitted'`` with a live ``valid_until`` — and
``_reticket_suppress_reason`` keys on (account, symbol, direction) only, so the
test ping's row for breakout_1/SOLUSDT/long SUPPRESSED the next genuine
``trend_donchian_sol`` signal until the synthetic ticket expired.

A test ping must journal under a status the suppression scan (and every other
``emitted``-keyed consumer) ignores.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    return tmp_path


def _account_cfg() -> dict:
    return {
        "account_id": "breakout_1",
        "exchange": "breakout",
        "account_class": "prop",
        "risk_pct": 0.015,
    }


@pytest.fixture
def run_test_ping(isolated_env, monkeypatch):
    """Run send_test_ping.main() with the default args (breakout_1 /
    SOLUSDT / long / trend_donchian_sol) and no real notification I/O."""
    from src.config import accounts_loader
    from src.prop import breakout_notify

    monkeypatch.setattr(accounts_loader, "load_accounts_dict",
                        lambda *a, **k: {"breakout_1": _account_cfg()})
    monkeypatch.setattr(breakout_notify, "emit_prop_signal",
                        lambda ticket, **k: {"push": True, "telegram": True})

    path = _REPO / "scripts" / "prop" / "send_test_ping.py"
    spec = importlib.util.spec_from_file_location("send_test_ping", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def _run(*argv: str) -> int:
        monkeypatch.setattr(sys, "argv", ["send_test_ping.py", *argv])
        return mod.main()

    return _run


def test_test_ping_does_not_journal_an_emitted_ticket(run_test_ping):
    from src.prop import prop_journal
    from src.prop.breakout_executor import _reticket_suppress_reason

    assert run_test_ping() == 0
    rows = prop_journal.list_tickets(account_id="breakout_1", limit=10)
    assert rows, "the test ping should still leave an auditable row"
    assert all(r["status"] != "emitted" for r in rows), rows
    assert _reticket_suppress_reason("breakout_1", "SOLUSDT", "long") is None


def test_test_ping_does_not_journal_emitted_on_injected_emitter_path(
        run_test_ping):
    from src.prop import prop_journal
    from src.prop.breakout_executor import _reticket_suppress_reason

    assert run_test_ping("--no-push") == 0
    rows = prop_journal.list_tickets(account_id="breakout_1", limit=10)
    assert all(r["status"] != "emitted" for r in rows), rows
    assert _reticket_suppress_reason("breakout_1", "SOLUSDT", "long") is None


def test_real_signal_after_test_ping_is_not_suppressed(run_test_ping):
    from src.prop import prop_journal
    from src.prop.breakout_executor import emit_prop_ticket

    assert run_test_ping() == 0
    real = {
        "symbol": "SOLUSDT", "direction": "long", "side": "Buy",
        "entry": 150.0, "sl": 145.5, "tp": 175.5,
        "strategy": "trend_donchian_sol",
    }
    trade_id = emit_prop_ticket(real, _account_cfg(), timeframe="1h",
                                _emitter=lambda ticket: None)
    rows = {r["ticket_id"]: r
            for r in prop_journal.list_tickets(account_id="breakout_1",
                                               limit=10)}
    assert rows[trade_id]["status"] == "emitted", rows[trade_id]
    assert rows[trade_id]["strategy"] == "trend_donchian_sol"
