"""P0.1 (2026-07-31 audit) — the read-side pnl-provenance filter FIRES.

These tests exist because a filter that is green-but-never-exercised is
indistinguishable from no filter (the "green is not evidence" class): each
one builds a journal with one TRUSTED row (measured exit provenance) and one
FABRICATED row (sweep-time mark) and asserts the fabricated row is excluded
by default and admitted only via the explicit escape hatch.

Covered here: ``setup_candidates._load_live_trades`` (M23 real-money
holdout) and the ``conviction_meta`` family's live scan. The attribution
loader has its own test in ``test_attribution.py``; the shell wiring is
pinned by ``tests/test_build_trainer_datasets_provenance_flags.py``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

MEASURED_NOTES = '{"exit_price_source": "recorded_exit_price"}'
FABRICATED_NOTES = '{"exit_price_source": "local_markprice"}'


def _seed_journal(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, timestamp TEXT, "
        "symbol TEXT, direction TEXT, status TEXT, pnl REAL, "
        "pnl_percent REAL, entry_price REAL, stop_loss REAL, "
        "position_size REAL, notes TEXT, is_backtest INT, is_demo INT, "
        "account_class TEXT, order_package_id TEXT)"
    )
    conn.execute(
        "CREATE TABLE order_packages (order_package_id TEXT PRIMARY KEY, "
        "strategy_name TEXT, symbol TEXT, direction TEXT, confidence REAL, "
        "signal_logic TEXT, meta TEXT, model_scores TEXT, created_at TEXT)"
    )
    rows = [
        # (id, pnl, notes) — one trusted win, one fabricated win
        (1, 10.0, MEASURED_NOTES),
        (2, 99.0, FABRICATED_NOTES),
    ]
    for tid, pnl, notes in rows:
        conn.execute(
            "INSERT INTO trades VALUES (?, '2026-07-01T00:00:00+00:00', "
            "'BTCUSDT', 'long', 'closed', ?, 1.0, 60000.0, 59000.0, 0.001, "
            "?, 0, 0, 'real_money', ?)",
            (tid, pnl, notes, f"pkg-{tid}"),
        )
        conn.execute(
            "INSERT INTO order_packages VALUES (?, 'strat', 'BTCUSDT', "
            "'long', 0.7, NULL, NULL, NULL, '2026-07-01T00:00:00+00:00')",
            (f"pkg-{tid}",),
        )
    conn.commit()
    conn.close()


def test_setup_candidates_live_loader_filters_fabricated(tmp_path: Path):
    from ml.datasets.families.setup_candidates import _load_live_trades

    db = tmp_path / "j.db"
    _seed_journal(db)
    default = _load_live_trades(db, "BTCUSDT")
    assert [t["pnl"] for t in default] == [10.0]
    unfiltered = _load_live_trades(db, "BTCUSDT", exclude_untrusted_pnl=False)
    assert sorted(t["pnl"] for t in unfiltered) == [10.0, 99.0]


def test_conviction_meta_filters_fabricated_when_flag_on(tmp_path: Path):
    from ml.datasets.families.conviction_meta import ConvictionMetaBuilder

    db = tmp_path / "j.db"
    _seed_journal(db)
    builder = ConvictionMetaBuilder()
    filtered = list(
        builder.iter_rows(db_path=db, exclude_fabricated_pnl=True)
    )
    assert [r["pnl"] for r in filtered] == [10.0]
    # Default (flag off) stays byte-for-byte the legacy population — the
    # production flip lives in build_trainer_datasets.sh, pinned by
    # tests/test_build_trainer_datasets_provenance_flags.py.
    legacy = list(builder.iter_rows(db_path=db))
    assert sorted(r["pnl"] for r in legacy) == [10.0, 99.0]
