"""M26 P1 — TF-ratio conflict-taxonomy soak tests (observe-only writer).

Covers: the pure ratio classifier (incl. the 4x boundary), the held-trade
journal lookup, the record builder's honest-null behaviour, the append/read
round-trip, and that the writer never raises on an unwritable log.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.runtime import conflict_taxonomy as ct


# ---------------------------------------------------------------------------
# classify_tf_ratio (pure)
# ---------------------------------------------------------------------------


class TestClassifyTfRatio:
    @pytest.mark.parametrize(
        "held,opp,expected_class,expected_ratio",
        [
            # 2h held vs 5m opposing -> 24x cross-clock -> coexist
            (120, 5, ct.CLASS_COEXIST, 24.0),
            # exactly 4x IS coexist (>= threshold, matching the P0 miner)
            (60, 15, ct.CLASS_COEXIST, 4.0),
            # 2h vs 1h -> 2x same/near-clock -> transition vote
            (120, 60, ct.CLASS_TRANSITION_VOTE, 2.0),
            # same clock -> 1x -> transition vote
            (60, 60, ct.CLASS_TRANSITION_VOTE, 1.0),
            # ratio is symmetric: fast held vs slow opposing still 24x
            (5, 120, ct.CLASS_COEXIST, 24.0),
        ],
    )
    def test_boundaries(self, held, opp, expected_class, expected_ratio):
        out = ct.classify_tf_ratio(held, opp)
        assert out["conflict_class"] == expected_class
        assert out["tf_ratio"] == pytest.approx(expected_ratio)

    @pytest.mark.parametrize("held,opp", [(None, 60), (60, None), (None, None), (0, 60)])
    def test_unknown_when_unresolvable(self, held, opp):
        out = ct.classify_tf_ratio(held, opp)
        assert out["conflict_class"] == ct.CLASS_UNKNOWN
        assert out["tf_ratio"] is None


class TestTimeframeMinutes:
    def test_known_frames(self):
        assert ct.timeframe_minutes("5m") == 5
        assert ct.timeframe_minutes("1h") == 60
        assert ct.timeframe_minutes("2H") == 120  # case-insensitive
        assert ct.timeframe_minutes(" 1d ") == 1440  # whitespace-tolerant

    def test_unknown_frames(self):
        assert ct.timeframe_minutes("7m") is None
        assert ct.timeframe_minutes(None) is None
        assert ct.timeframe_minutes("") is None


# ---------------------------------------------------------------------------
# held-trade lookup + record builder
# ---------------------------------------------------------------------------


def _make_journal(tmp_path, rows):
    """Minimal trades table with the columns the lookup reads."""
    db = tmp_path / "trade_journal.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE trades ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " account_id TEXT, symbol TEXT, strategy_name TEXT,"
            " direction TEXT, position_size REAL, status TEXT,"
            " is_backtest INTEGER DEFAULT 0)"
        )
        conn.executemany(
            "INSERT INTO trades (account_id, symbol, strategy_name, direction,"
            " position_size, status, is_backtest) VALUES (?,?,?,?,?,?,?)",
            rows,
        )
    return str(db)


class TestHeldOpenTrade:
    def test_finds_most_recent_open(self, tmp_path):
        db = _make_journal(tmp_path, [
            ("bybit_2", "BTCUSDT", "old_strat", "long", 0.5, "closed", 0),
            ("bybit_2", "BTCUSDT", "trend_donchian", "long", 0.001, "open", 0),
        ])
        held = ct._held_open_trade("bybit_2", "BTCUSDT", db_path=db)
        assert held == {
            "strategy": "trend_donchian", "direction": "long", "qty": 0.001,
        }

    def test_excludes_backtest_and_other_accounts(self, tmp_path):
        db = _make_journal(tmp_path, [
            ("bybit_2", "BTCUSDT", "bt_strat", "long", 1.0, "open", 1),
            ("other", "BTCUSDT", "other_strat", "long", 1.0, "open", 0),
        ])
        assert ct._held_open_trade("bybit_2", "BTCUSDT", db_path=db) is None

    def test_missing_db_returns_none(self, tmp_path):
        assert ct._held_open_trade(
            "a", "S", db_path=str(tmp_path / "nope.db")
        ) is None


class TestBuildConflictRecord:
    def test_full_row(self, tmp_path, monkeypatch):
        db = _make_journal(tmp_path, [
            ("bybit_2", "BTCUSDT", "htf_pullback_trend_2h", "long", 0.01, "open", 0),
        ])
        monkeypatch.setattr(
            ct, "_strategy_tf_map",
            lambda: {"htf_pullback_trend_2h": 120, "ict_scalp_5m": 5},
        )
        rec = ct.build_conflict_record(
            account_id="bybit_2", symbol="BTCUSDT",
            opposing_strategy="ict_scalp_5m", opposing_side="short",
            opposing_confidence=0.7, current_signed_qty=0.01,
            suppression_reason="flip_suppressed_hold_policy: …",
            db_path=db,
        )
        assert rec["held_strategy"] == "htf_pullback_trend_2h"
        assert rec["held_tf_min"] == 120
        assert rec["opposing_tf_min"] == 5
        assert rec["tf_ratio"] == pytest.approx(24.0)
        assert rec["conflict_class"] == ct.CLASS_COEXIST
        assert rec["coexist_threshold"] == ct.COEXIST_TF_RATIO
        assert rec["opposing_side"] == "short"
        assert rec["ts"]

    def test_honest_null_on_unknown_held(self, tmp_path, monkeypatch):
        """Journal miss -> row still built, held fields null, class unknown."""
        monkeypatch.setattr(ct, "_strategy_tf_map", lambda: {"s1": 60})
        rec = ct.build_conflict_record(
            account_id="acct", symbol="ETHUSDT",
            opposing_strategy="s1", opposing_side="long",
            db_path=str(tmp_path / "missing.db"),
        )
        assert rec is not None
        assert rec["held_strategy"] is None
        assert rec["held_tf_min"] is None
        assert rec["conflict_class"] == ct.CLASS_UNKNOWN


# ---------------------------------------------------------------------------
# writer + reader round-trip
# ---------------------------------------------------------------------------


class TestRecordAndRead:
    def test_roundtrip(self, tmp_path, monkeypatch):
        from src.utils import paths

        monkeypatch.setattr(paths, "runtime_logs_dir", lambda: tmp_path)
        db = _make_journal(tmp_path, [
            ("bybit_2", "SOLUSDT", "trend_donchian", "short", 1.0, "open", 0),
        ])
        monkeypatch.setattr(
            ct, "_strategy_tf_map",
            lambda: {"trend_donchian": 60, "sol_pullback_2h": 120},
        )
        rec = ct.record_conflict(
            account_id="bybit_2", symbol="SOLUSDT",
            opposing_strategy="sol_pullback_2h", opposing_side="long",
            db_path=db,
        )
        assert rec is not None
        assert rec["conflict_class"] == ct.CLASS_TRANSITION_VOTE

        on_disk = [
            json.loads(line)
            for line in (tmp_path / ct.SOAK_LOG_NAME).read_text().splitlines()
        ]
        assert len(on_disk) == 1
        assert on_disk[0]["conflict_class"] == ct.CLASS_TRANSITION_VOTE

        env = ct.read_soak_records()
        assert env["present"] is True
        assert env["count"] == 1
        assert env["summary"]["by_class"] == {ct.CLASS_TRANSITION_VOTE: 1}

    def test_read_absent_log(self, tmp_path, monkeypatch):
        from src.utils import paths

        monkeypatch.setattr(paths, "runtime_logs_dir", lambda: tmp_path)
        env = ct.read_soak_records()
        assert env["present"] is False
        assert env["records"] == []

    def test_writer_never_raises_on_unwritable_dir(self, tmp_path, monkeypatch):
        from src.utils import paths

        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file, not a dir")
        monkeypatch.setattr(
            paths, "runtime_logs_dir", lambda: blocked / "sub"
        )
        monkeypatch.setattr(ct, "_strategy_tf_map", lambda: {})
        # Must not raise even though mkdir/open will fail.
        rec = ct.record_conflict(
            account_id="a", symbol="S", opposing_strategy=None,
            opposing_side=None, db_path=str(tmp_path / "no.db"),
        )
        assert rec is not None  # the record itself was still built
