"""Tests for scripts/research/component_edge_report.py — Layer-1a report.

Builds a tiny temp sqlite with ``order_packages`` + ``trades`` fixture rows
(a deliberately MONOTONE component→win relationship in one strategy and a FLAT
one in another), runs the generator against it, and asserts:

  * it writes the per-strategy json + md and the combined index;
  * the monotone component gets verdict ``edge`` (or at least ``weak``);
  * the flat component gets ``none``;
  * an empty / missing DB yields a clean 'insufficient'/empty report (exit 0),
    never a traceback.

Fully offline + hermetic — a tmp_path sqlite, no network, no live DB.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "research" / "component_edge_report.py"


def _load_module():
    """Import the script module by path (scripts/ isn't a package)."""
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    name = "component_edge_report_under_test"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # Register BEFORE exec so dataclass field-type resolution (which looks the
    # module up in sys.modules by __module__) finds it.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


MOD = _load_module()


# ---------------------------------------------------------------------------
# Fixture DB builder
# ---------------------------------------------------------------------------


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            symbol TEXT,
            direction TEXT,
            entry_price REAL,
            exit_price REAL,
            stop_loss REAL,
            position_size REAL,
            pnl REAL,
            status TEXT,
            is_backtest INTEGER DEFAULT 0,
            strategy_name TEXT,
            account_id TEXT,
            is_demo INTEGER DEFAULT 0,
            account_class TEXT,
            order_package_id TEXT,
            closed_at TEXT,
            reconcile_status TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE order_packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_package_id TEXT,
            strategy_name TEXT,
            symbol TEXT,
            confidence REAL,
            signal_logic TEXT,
            linked_trade_id INTEGER,
            updated_at TEXT,
            status TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def _insert(
    conn: sqlite3.Connection,
    *,
    trade_id: int,
    strategy: str,
    symbol: str,
    pnl: float,
    signal_logic: dict,
    confidence: float,
    closed_at: str,
    account_class: str = "real_money",
) -> None:
    pkg_id = f"pkg-{trade_id}"
    entry = 50_000.0
    stop = 49_900.0  # risk = 100 * 1 * contract_value
    conn.execute(
        "INSERT INTO trades (id, timestamp, symbol, direction, entry_price, "
        "exit_price, stop_loss, position_size, pnl, status, is_backtest, "
        "strategy_name, account_id, account_class, order_package_id, closed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            trade_id, closed_at, symbol, "long", entry, entry + pnl, stop, 1.0,
            pnl, "closed", 0, strategy, "acct", account_class, pkg_id, closed_at,
        ),
    )
    conn.execute(
        "INSERT INTO order_packages (order_package_id, strategy_name, symbol, "
        "confidence, signal_logic, linked_trade_id, updated_at, status) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            pkg_id, strategy, symbol, confidence, json.dumps(signal_logic),
            trade_id, closed_at, "closed",
        ),
    )


def _populate(path: Path) -> None:
    """Seed a monotone strategy (vwap) and a flat strategy (turtle_soup).

    vwap: |deviation_std| grows with win probability + R (monotone edge).
    turtle_soup: body_to_range is random wrt outcome (flat / no edge).
    """
    conn = sqlite3.connect(str(path))
    tid = 0
    base_ts = "2026-06-2{day}T12:00:00+00:00"

    # --- vwap: MONOTONE. Low |dev| → losers, high |dev| → winners. ---
    # 30 trades: deviation magnitude bucketed, win + pnl scale with it.
    for i in range(30):
        tid += 1
        dev = -(0.5 + i * 0.2)  # -0.5 .. -6.3, strictly increasing magnitude
        # Winners for the high-deviation half, losers for the low-dev half.
        if i < 10:
            pnl = -100.0          # bucket 0: clean losers, R = -1
        elif i < 20:
            pnl = -20.0 if i % 2 else 60.0   # bucket 1: mixed
        else:
            pnl = 250.0           # bucket 2: clean winners, R = +2.5
        day = 8 + (i % 2)  # recent window (late June)
        _insert(
            conn,
            trade_id=tid,
            strategy="vwap",
            symbol="BTCUSDT",
            pnl=pnl,
            signal_logic={
                "strategy_name": "vwap",
                "deviation_std": dev,
                "std_dev": 120.0,
                "vwap": 50_100.0,
                "policy_threshold": 1.0,
                "regime": "ranging",
                "adx_14": 12.0,
                "vol_regime": "calm",
            },
            confidence=min(abs(dev), 1.0),
            closed_at=base_ts.format(day=day),
        )

    # --- turtle_soup: FLAT. body_to_range uncorrelated with outcome. ---
    for i in range(30):
        tid += 1
        btr = 0.6 + (i % 5) * 0.05  # cycles 0.6..0.8, no trend wrt outcome
        pnl = 80.0 if i % 2 == 0 else -80.0  # alternating, independent of btr
        day = 8 + (i % 2)
        _insert(
            conn,
            trade_id=tid,
            strategy="turtle_soup",
            symbol="BTCUSDT",
            pnl=pnl,
            signal_logic={
                "strategy_name": "turtle_soup",
                "level": 50_000.0,
                "sweep_extreme": 49_700.0,
                "body_to_range": btr,
                "atr": 150.0,
                "regime": "ranging",
                "adx_14": 15.0,
                "vol_regime": "calm",
            },
            confidence=0.5,
            closed_at=base_ts.format(day=day),
        )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_writes_outputs_and_detects_edge_vs_flat(tmp_path):
    db = tmp_path / "trade_journal.db"
    out = tmp_path / "signal_research"
    _make_db(db)
    _populate(db)

    summary = MOD.build_reports(
        str(db), out_dir=out, min_bucket=5, n_buckets=3
    )

    assert summary["db_present"] is True
    assert summary["strategies_analyzed"] == 2
    assert not summary["errors"], summary["errors"]

    # Files written.
    assert (out / "component_edge_index.json").exists()
    assert (out / "component_edge_vwap.json").exists()
    assert (out / "component_edge_vwap.md").exists()
    assert (out / "component_edge_turtle_soup.json").exists()

    vwap = json.loads((out / "component_edge_vwap.json").read_text())
    turtle = json.loads((out / "component_edge_turtle_soup.json").read_text())

    # vwap deviation is monotone → edge (or at worst weak), never none.
    dev = _component(vwap, "vwap_deviation_std")
    assert dev is not None
    assert dev["verdict"] in ("edge", "weak"), dev["verdict_reason"]
    # The bucketing exists and ordered buckets show a monotone mean-R edge
    # (direction depends on the deviation sign — winners sit in the
    # most-negative-deviation bucket here), with a genuine top-vs-bottom spread.
    buckets = dev["bucketing"]["buckets"]
    assert len(buckets) >= 2
    rs = [b["mean_r"] for b in buckets if b["mean_r"] is not None]
    rising = all(b >= a for a, b in zip(rs, rs[1:]))
    falling = all(b <= a for a, b in zip(rs, rs[1:]))
    assert rising or falling
    assert abs(rs[0] - rs[-1]) > 1.0

    # turtle_soup body_to_range is flat → none.
    btr = _component(turtle, "displacement_strength")
    assert btr is not None
    assert btr["verdict"] == "none", btr["verdict_reason"]

    # rCoverage is full (every trade has entry/stop/size).
    assert vwap["rCoverage"] == 1.0
    assert turtle["rCoverage"] == 1.0


def test_index_summary_carries_verdicts(tmp_path):
    db = tmp_path / "trade_journal.db"
    out = tmp_path / "signal_research"
    _make_db(db)
    _populate(db)
    MOD.build_reports(str(db), out_dir=out, min_bucket=5)

    index = json.loads((out / "component_edge_index.json").read_text())
    names = {s["strategy"] for s in index["strategies"]}
    assert {"vwap", "turtle_soup"} <= names
    vwap_entry = next(s for s in index["strategies"] if s["strategy"] == "vwap")
    assert "vwap_deviation_std" in vwap_entry["verdicts"]


def test_missing_db_clean_empty_report(tmp_path):
    db = tmp_path / "nonexistent.db"
    out = tmp_path / "signal_research"
    summary = MOD.build_reports(str(db), out_dir=out)
    assert summary["db_present"] is False
    assert summary["strategies_analyzed"] == 0
    # index still written, with no strategies — no traceback.
    idx = json.loads((out / "component_edge_index.json").read_text())
    assert idx["strategies"] == []


def test_empty_db_clean_insufficient(tmp_path):
    db = tmp_path / "trade_journal.db"
    out = tmp_path / "signal_research"
    _make_db(db)  # schema only, zero rows
    summary = MOD.build_reports(str(db), out_dir=out)
    assert summary["db_present"] is True
    # No closed trades in any cohort → no per-strategy reports, clean index.
    idx = json.loads((out / "component_edge_index.json").read_text())
    assert idx["strategies"] == []


def test_strategy_with_no_closed_trades_insufficient(tmp_path):
    db = tmp_path / "trade_journal.db"
    out = tmp_path / "signal_research"
    _make_db(db)
    _populate(db)
    # Explicit --strategy targeting a name with zero rows → insufficient report.
    summary = MOD.build_reports(str(db), strategy="ghost_strategy", out_dir=out)
    assert summary["strategies_analyzed"] == 1
    rep = json.loads((out / "component_edge_ghost_strategy.json").read_text())
    assert rep["n_closed"] == 0
    assert rep["insufficient_data"] is True
    assert rep["graded_components"] == []


def test_include_paper_separate_section(tmp_path):
    db = tmp_path / "trade_journal.db"
    out = tmp_path / "signal_research"
    _make_db(db)
    # one paper-cohort vwap set
    conn = sqlite3.connect(str(db))
    for i in range(12):
        _insert(
            conn,
            trade_id=1000 + i,
            strategy="vwap",
            symbol="BTCUSDT",
            pnl=50.0 if i % 2 else -50.0,
            signal_logic={"deviation_std": -1.0 - i * 0.1, "vwap": 1.0},
            confidence=0.5,
            closed_at="2026-06-09T12:00:00+00:00",
            account_class="paper",
        )
    conn.commit()
    conn.close()

    # Without --include-paper: real cohort is empty (all paper) → nothing.
    MOD.build_reports(str(db), out_dir=out, min_bucket=4)
    real_cohorts = {
        json.loads(p.read_text())["cohort"]
        for p in out.glob("component_edge_vwap.json")
    }
    assert "paper" not in real_cohorts  # default excludes paper

    # With --include-paper: a paper report is produced.
    out2 = tmp_path / "signal_research_paper"
    MOD.build_reports(
        str(db), include_paper=True, out_dir=out2, min_bucket=4
    )
    rep = json.loads((out2 / "component_edge_vwap.json").read_text())
    assert rep["cohort"] == "paper"
    assert rep["n_closed"] == 12


def test_main_cli_exit_zero(tmp_path, capsys):
    db = tmp_path / "trade_journal.db"
    out = tmp_path / "signal_research"
    _make_db(db)
    _populate(db)
    rc = MOD.main(
        ["--db", str(db), "--out-dir", str(out), "--min-bucket", "5"]
    )
    assert rc == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["strategies_analyzed"] == 2


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _component(report: dict, name: str):
    for c in report.get("graded_components", []):
        if c["component"] == name:
            return c
    return None
