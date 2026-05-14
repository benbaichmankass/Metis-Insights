"""Tests for src/runtime/runtime_flags.py (D11)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# is_strategy_paused
# ---------------------------------------------------------------------------

class TestIsStrategyPaused:
    def test_returns_false_when_no_flag(self, tmp_path):
        from src.runtime import runtime_flags
        with patch.object(runtime_flags, "flags_dir", return_value=tmp_path):
            assert runtime_flags.is_strategy_paused("vwap") is False

    def test_returns_true_when_flag_present(self, tmp_path):
        (tmp_path / "pause_vwap").touch()
        from src.runtime import runtime_flags
        with patch.object(runtime_flags, "flags_dir", return_value=tmp_path):
            assert runtime_flags.is_strategy_paused("vwap") is True

    def test_flag_is_strategy_name_specific(self, tmp_path):
        (tmp_path / "pause_vwap").touch()
        from src.runtime import runtime_flags
        with patch.object(runtime_flags, "flags_dir", return_value=tmp_path):
            assert runtime_flags.is_strategy_paused("turtle_soup") is False
            assert runtime_flags.is_strategy_paused("vwap") is True

    def test_missing_flags_dir_returns_false(self, tmp_path):
        absent = tmp_path / "no_such_dir"
        from src.runtime import runtime_flags
        with patch.object(runtime_flags, "flags_dir", return_value=absent):
            assert runtime_flags.is_strategy_paused("vwap") is False

    def test_flag_removed_returns_false(self, tmp_path):
        flag = tmp_path / "pause_vwap"
        flag.touch()
        from src.runtime import runtime_flags
        with patch.object(runtime_flags, "flags_dir", return_value=tmp_path):
            assert runtime_flags.is_strategy_paused("vwap") is True
            flag.unlink()
            assert runtime_flags.is_strategy_paused("vwap") is False


# ---------------------------------------------------------------------------
# list_paused_strategies
# ---------------------------------------------------------------------------

class TestListPausedStrategies:
    def test_empty_when_no_flags(self, tmp_path):
        from src.runtime import runtime_flags
        with patch.object(runtime_flags, "flags_dir", return_value=tmp_path):
            assert runtime_flags.list_paused_strategies() == []

    def test_returns_single_paused_strategy(self, tmp_path):
        (tmp_path / "pause_vwap").touch()
        from src.runtime import runtime_flags
        with patch.object(runtime_flags, "flags_dir", return_value=tmp_path):
            assert runtime_flags.list_paused_strategies() == ["vwap"]

    def test_returns_sorted_list(self, tmp_path):
        (tmp_path / "pause_vwap").touch()
        (tmp_path / "pause_turtle_soup").touch()
        from src.runtime import runtime_flags
        with patch.object(runtime_flags, "flags_dir", return_value=tmp_path):
            result = runtime_flags.list_paused_strategies()
            assert result == sorted(result)
            assert "vwap" in result
            assert "turtle_soup" in result

    def test_ignores_non_pause_files(self, tmp_path):
        (tmp_path / "send_hourly_demo").touch()
        (tmp_path / "some_other_file").touch()
        (tmp_path / "pause_vwap").touch()
        from src.runtime import runtime_flags
        with patch.object(runtime_flags, "flags_dir", return_value=tmp_path):
            assert runtime_flags.list_paused_strategies() == ["vwap"]

    def test_ignores_subdirectories(self, tmp_path):
        # A subdirectory named pause_fake should not appear in the list.
        (tmp_path / "pause_fake_dir").mkdir()
        (tmp_path / "pause_vwap").touch()
        from src.runtime import runtime_flags
        with patch.object(runtime_flags, "flags_dir", return_value=tmp_path):
            assert runtime_flags.list_paused_strategies() == ["vwap"]

    def test_empty_when_flags_dir_absent(self, tmp_path):
        absent = tmp_path / "no_such_dir"
        from src.runtime import runtime_flags
        with patch.object(runtime_flags, "flags_dir", return_value=absent):
            assert runtime_flags.list_paused_strategies() == []


# ---------------------------------------------------------------------------
# Pipeline integration — multiplexed_signal_builder skips paused strategies
# ---------------------------------------------------------------------------

class TestMultiplexerRespectsPauseFlag:
    """Smoke tests that multiplexed_signal_builder skips paused strategies."""

    def _make_builder(self, side, strategy_name):
        def builder(settings):
            return {"symbol": "BTCUSDT", "side": side,
                    "meta": {"strategy_name": strategy_name}}
        return builder

    def test_paused_strategy_is_skipped(self, tmp_path, monkeypatch):
        """When vwap is paused and turtle_soup fires, turtle_soup wins."""
        import src.runtime.pipeline as pl
        from src.runtime import runtime_flags

        (tmp_path / "pause_vwap").touch()
        monkeypatch.setattr(runtime_flags, "flags_dir", lambda: tmp_path)

        monkeypatch.setattr(pl, "STRATEGIES", ["vwap", "turtle_soup"])
        monkeypatch.setattr(pl, "_STRATEGY_BUILDERS", {
            "vwap":        self._make_builder("buy", "vwap"),
            "turtle_soup": self._make_builder("buy", "turtle_soup"),
        })
        monkeypatch.setattr(pl, "is_strategy_paused", runtime_flags.is_strategy_paused)

        result = pl.multiplexed_signal_builder({})
        assert result.get("meta", {}).get("strategy_name") == "turtle_soup"

    def test_unpaused_strategy_fires_normally(self, tmp_path, monkeypatch):
        """No pause flags → first-strategy wins as before."""
        import src.runtime.pipeline as pl
        from src.runtime import runtime_flags

        monkeypatch.setattr(runtime_flags, "flags_dir", lambda: tmp_path)
        monkeypatch.setattr(pl, "STRATEGIES", ["vwap", "turtle_soup"])
        monkeypatch.setattr(pl, "_STRATEGY_BUILDERS", {
            "vwap":        self._make_builder("buy", "vwap"),
            "turtle_soup": self._make_builder("buy", "turtle_soup"),
        })
        monkeypatch.setattr(pl, "is_strategy_paused", runtime_flags.is_strategy_paused)

        result = pl.multiplexed_signal_builder({})
        assert result.get("meta", {}).get("strategy_name") == "vwap"

    def test_all_paused_returns_no_signal(self, tmp_path, monkeypatch):
        """All strategies paused → multiplexer returns side='none'."""
        import src.runtime.pipeline as pl
        from src.runtime import runtime_flags

        (tmp_path / "pause_vwap").touch()
        (tmp_path / "pause_turtle_soup").touch()
        monkeypatch.setattr(runtime_flags, "flags_dir", lambda: tmp_path)
        monkeypatch.setattr(pl, "STRATEGIES", ["vwap", "turtle_soup"])
        monkeypatch.setattr(pl, "_STRATEGY_BUILDERS", {
            "vwap":        self._make_builder("buy", "vwap"),
            "turtle_soup": self._make_builder("buy", "turtle_soup"),
        })
        monkeypatch.setattr(pl, "is_strategy_paused", runtime_flags.is_strategy_paused)

        result = pl.multiplexed_signal_builder({})
        assert result.get("side") == "none"
