"""Writer-side closed_at normalisation (src.utils.closed_at) + the one-shot
migration of already-written epoch-ms rows.

Companion to the read-side guard (tests/test_perf_closed_at_epoch_ms.py).
Pure stdlib — no FastAPI import — so it runs anywhere.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
from pathlib import Path

from src.utils.closed_at import normalize_closed_at_value

_MIG = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "migrate_closed_at_to_iso.py"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("_mig_closed_at", _MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_normalize_value_ms_iso_passthrough_and_none():
    assert normalize_closed_at_value("1782128223000").startswith("2026-06-22T11:37:03")
    # numeric input also accepted
    assert normalize_closed_at_value(1782128223000).startswith("2026-06-22T11:37:03")
    assert normalize_closed_at_value("2026-06-22T11:37:03+00:00") == "2026-06-22T11:37:03+00:00"
    assert normalize_closed_at_value("2026-06-22 11:37:03") == "2026-06-22 11:37:03"
    assert normalize_closed_at_value(None) is None
    assert normalize_closed_at_value("") is None
    # a short all-digit value is NOT treated as epoch-ms (passed through)
    assert normalize_closed_at_value("12345") == "12345"


def _seed(db: Path) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE trades(id INTEGER PRIMARY KEY, closed_at TEXT, notes TEXT)")
    conn.executemany(
        "INSERT INTO trades(id, closed_at, notes) VALUES(?,?,?)",
        [
            # epoch-ms closed_at + ms in notes → both migrated
            (1, "1782128223000", json.dumps({"closed_at": "1782128223000", "x": 1})),
            # already ISO → untouched
            (2, "2026-06-22T07:15:35+00:00", json.dumps({"closed_at": "2026-06-22T07:15:35+00:00"})),
            # SQLite CURRENT_TIMESTAMP style → untouched
            (3, "2026-06-20 05:10:12", None),
            # NULL closed_at → ignored
            (4, None, None),
        ],
    )
    conn.commit()
    conn.close()


def test_migration_dry_run_then_apply_idempotent():
    mod = _load_migration_module()
    db = Path(tempfile.mktemp(suffix=".db"))
    _seed(db)

    # dry-run changes nothing
    assert mod.migrate(db, apply=False) == 0
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT closed_at FROM trades WHERE id=1").fetchone()[0] == "1782128223000"
    conn.close()

    # apply rewrites the ms rows (column + notes), leaves ISO rows alone
    assert mod.migrate(db, apply=True) == 0
    conn = sqlite3.connect(str(db))
    rows = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id, closed_at, notes FROM trades")}
    assert rows[1][0].startswith("2026-06-22T11:37:03")
    assert json.loads(rows[1][1])["closed_at"].startswith("2026-06-22T11:37:03")
    assert rows[2][0] == "2026-06-22T07:15:35+00:00"  # untouched
    assert rows[3][0] == "2026-06-20 05:10:12"        # untouched
    assert rows[4][0] is None                          # untouched
    conn.close()

    # idempotent: a second apply finds nothing to change
    assert mod.migrate(db, apply=True) == 0
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT closed_at FROM trades WHERE id=1").fetchone()[0].startswith(
        "2026-06-22T11:37:03"
    )
    conn.close()
    db.unlink()
