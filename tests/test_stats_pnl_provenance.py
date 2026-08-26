"""`/api/bot/stats` states how much of its own `totalPnL` was MEASURED.

GATE 0 / G3 (`docs/claude/WORKPLAN-2026-08-26.md`): *"A consumer receiving a sum
or a rate must receive its coverage beside it — the `rCoverage`/`pnlCoverage`
discipline, applied to the rest."*

`/stats` is the FIRST number both apps render and it published a sum and a rate
over journal `pnl` with no statement of provenance, while `/performance` has
carried `pnlCoverage` since 2026-07-31.

Measured on the live journal 2026-08-26 — population: closed, non-backtest,
`pnl NOT NULL`, real-money, **n=431** — coverage is 0.768 and `totalPnL` moves
**-23.22 -> -45.63** restricted to measured+estimated. ⚠️ That figure is
population-specific: the same classifier reads 0.425 over all accounts
(n=1,187) and 0.257 over the package-joined population the workplan cites
(n=806). The case for this block is the SUM nearly doubling, not the coverage
number being alarming.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.web.api import main as api_main
from src.web.api.routers import dashboard as dashboard_router

_WITH_NOTES = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT, created_at TEXT, closed_at TEXT, status TEXT, pnl REAL,
    is_backtest INTEGER DEFAULT 0, account_class TEXT, is_demo INTEGER DEFAULT 0,
    strategy_name TEXT, reconcile_status TEXT, setup_type TEXT, notes TEXT
);
CREATE TABLE order_packages (
    order_package_id TEXT PRIMARY KEY, linked_trade_id INTEGER, updated_at TEXT
);
"""
# Same schema minus `notes` — an older journal, where provenance is genuinely
# ungradeable rather than absent.
_NO_NOTES = _WITH_NOTES.replace(", notes TEXT", "")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("JWT_SIGNING_KEY", "x" * 64)
    monkeypatch.setenv("ALLOWED_EMAIL", "test@example.com")
    monkeypatch.setenv("WEBAPP_PASSWORD_SHA256", "deadbeef")
    return TestClient(api_main.app, raise_server_exceptions=False)


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "trade_journal.db"
    monkeypatch.setattr(dashboard_router, "_DB_PATH", p)
    return p


def _seed(path: Path, rows, schema: str = _WITH_NOTES) -> None:
    """rows = [(account_class, pnl, exit_price_source)]"""
    conn = sqlite3.connect(str(path))
    conn.executescript(schema)
    has_notes = "notes" in schema
    for i, (klass, pnl, src) in enumerate(rows, start=1):
        cols = ("timestamp, created_at, closed_at, status, pnl, is_backtest,"
                " account_class, is_demo, strategy_name")
        vals = [f"2026-07-{10+i:02d}T00:00:00Z", f"2026-07-{10+i:02d}T00:00:00Z",
                f"2026-07-{10+i:02d}T12:00:00Z", "closed", pnl, 0, klass,
                1 if klass == "paper" else 0, "s"]
        if has_notes:
            cols += ", notes"
            vals.append('{"exit_price_source": "%s"}' % src if src else "{}")
        conn.execute(
            f"INSERT INTO trades ({cols}) VALUES ({','.join('?' * len(vals))})",
            vals)
    conn.commit()
    conn.close()


def test_coverage_and_the_measured_sum_are_published(client, db):
    _seed(db, [
        ("real_money",  10.0, "bybit_closed_pnl"),   # MEASURED
        ("real_money",  -4.0, "exchange_fill"),      # MEASURED
        ("real_money", 100.0, "candle_at_close"),    # ESTIMATED
        ("real_money", 500.0, "local_markprice"),    # FABRICATED
        ("real_money",   7.0, None),                 # UNVERIFIED
    ])
    b = client.get("/api/bot/stats").json()
    # MEASURED-only, exactly as `/performance` defines it.
    assert b["pnlMeasuredCount"] == 2
    assert b["pnlEstimatedCount"] == 1
    assert b["pnlCoverage"] == pytest.approx(2 / 5)
    # …while the SUM is MEASURED+ESTIMATED. The asymmetry is load-bearing and
    # neither may be harmonised to the other (see /performance's own note).
    assert b["totalPnLMeasured"] == pytest.approx(106.0)
    # The raw total still carries the fabricated + unverified money.
    assert b["totalPnL"] == pytest.approx(613.0)


def test_the_caveat_is_what_makes_the_headline_readable(client, db):
    """The reason this block exists: the sum a consumer renders and the sum
    that was actually measured can differ by most of their own magnitude."""
    _seed(db, [
        ("real_money", -50.0, "bybit_closed_pnl"),
        ("real_money", 500.0, "local_markprice"),
    ])
    b = client.get("/api/bot/stats").json()
    assert b["totalPnL"] == pytest.approx(450.0), "what a consumer renders"
    assert b["totalPnLMeasured"] == pytest.approx(-50.0), "what was measured"
    assert b["pnlCoverage"] == pytest.approx(0.5)


def test_real_and_paper_are_graded_separately_and_never_blended(client, db):
    """P4: real and paper performance are never blended — including their
    provenance. A paper book with perfect coverage must not flatter the
    real-money figure, or the caveat lies in the reassuring direction."""
    _seed(db, [
        ("real_money", 1.0, "local_markprice"),   # FABRICATED
        ("paper",      1.0, "bybit_closed_pnl"),  # MEASURED
        ("paper",      2.0, "bybit_closed_pnl"),
    ])
    b = client.get("/api/bot/stats").json()
    assert b["pnlCoverage"] == 0.0, "real money: nothing measured"
    assert b["pnlMeasuredCount"] == 0
    assert b["paper"]["pnlCoverage"] == 1.0
    assert b["paper"]["pnlMeasuredCount"] == 2


def test_zero_coverage_is_not_the_same_as_no_population(client, db):
    """`0.0` means we looked and nothing was measured. It must be reachable and
    distinguishable from `None`, or the field cannot say which happened."""
    _seed(db, [("real_money", 1.0, "local_markprice")])
    b = client.get("/api/bot/stats").json()
    assert b["pnlCoverage"] == 0.0 and b["pnlMeasuredCount"] == 0


def test_an_ungradeable_schema_says_so_rather_than_reporting_zeros(client, db):
    """An older journal with no `notes` column cannot be graded at all. That is
    "we could not look" — all None — and must NOT read as "nothing measured",
    which is what a zeroed block would assert."""
    _seed(db, [("real_money", 1.0, None)], schema=_NO_NOTES)
    b = client.get("/api/bot/stats").json()
    assert b["pnlCoverage"] is None
    assert b["pnlMeasuredCount"] is None
    assert b["pnlEstimatedCount"] is None
    assert b["totalPnLMeasured"] is None
    # …and the money numbers are untouched: a missing caveat never costs the
    # figures it qualifies.
    assert b["totalPnL"] == pytest.approx(1.0)


def test_the_keys_are_always_present(client, db):
    """A key that vanishes makes a consumer branch on absence, and absence is
    not one of the states."""
    _seed(db, [("real_money", 1.0, None)], schema=_NO_NOTES)
    b = client.get("/api/bot/stats").json()
    for k in ("pnlCoverage", "pnlMeasuredCount", "pnlEstimatedCount",
              "totalPnLMeasured"):
        assert k in b and k in b["paper"], k
