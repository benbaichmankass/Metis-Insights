"""Tests for the boot-time strategy_versions snapshot
(``src.runtime.boot_audit.snapshot_strategy_versions_on_boot``).

Wires the previously-dead ``strategy_versions`` table to a real producer:
each distinct config/strategies.yaml content becomes one row, keyed by a
content hash so re-running on an unchanged config is a no-op.
"""
from __future__ import annotations

import sqlite3

from src.runtime.boot_audit import snapshot_strategy_versions_on_boot


def test_snapshot_inserts_then_idempotent(tmp_path, monkeypatch):
    db = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db))

    first = snapshot_strategy_versions_on_boot()
    second = snapshot_strategy_versions_on_boot()

    assert first is not None        # first boot writes a row
    assert second is None           # unchanged config → idempotent no-op

    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT version_name, config FROM strategy_versions"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0][0].startswith("strategies-")
    # config column holds the parsed YAML serialized as JSON.
    assert rows[0][1]  # non-empty
