"""ORDER-AUDIT-2 item 9 (AUD-20260927-CA-A15-db-loaders-stray-trade-journal-fallback).

Three modules resolved the journal as ``next(p for p in [canonical, <repo>/trade_journal.db]
if exists(p))``. With DATA_DIR set but ``$DATA_DIR/trade_journal.db`` not yet
present (fresh deploy, mount race) and a stray ``<repo>/trade_journal.db`` on disk,
they silently read the stray file. Each must now resolve to the canonical path.
"""
from __future__ import annotations

import importlib

import pytest

import src.utils.paths as paths

MODULES = [
    ("src.units.ui.data_loaders", "TRADE_JOURNAL_DB"),
    ("src.backtest.run_backtest", "DB_PATH"),
    ("src.bot.telegram_query_bot", "DB_PATH"),
]


@pytest.mark.parametrize("modname,attr", MODULES)
def test_empty_data_dir_does_not_fall_back_to_stray_repo_root_journal(
        tmp_path, monkeypatch, modname, attr):
    try:
        mod = importlib.import_module(modname)
    except Exception as exc:  # noqa: BLE001 — optional deps (telegram) absent
        pytest.skip(f"{modname} not importable here: {exc}")
    data_dir = tmp_path / "data"
    data_dir.mkdir()                              # mounted, but no journal yet
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    (fake_repo / "trade_journal.db").write_bytes(b"stray")   # the stray file
    monkeypatch.delenv("TRADE_JOURNAL_DB", raising=False)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(paths, "repo_root", lambda: str(fake_repo))
    try:
        mod = importlib.reload(mod)
        got = getattr(mod, attr)
    finally:
        monkeypatch.undo()
        importlib.reload(mod)
    assert got == str(data_dir / "trade_journal.db")
    assert not got.startswith(str(fake_repo))
