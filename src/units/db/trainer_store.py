"""Trainer-store sidecar — the federated half of the canonical store.

The live trader owns ``trade_journal.db``. The trainer VM produces its
lifecycle telemetry as JSONL/JSON files that it rsyncs into
``runtime_logs/trainer_mirror/`` on the live VM (see
``scripts/ops/publish_trainer_mirror.sh`` and
``src/web/api/routers/training_center.py``). Historically that data was
only reachable file-by-file through the ``/api/bot/ml/*`` endpoints and
was NOT browsable in the Data Explorer.

This module ingests that mirror into a read-mostly SQLite sidecar
(``trainer_store.db``) sitting next to ``trade_journal.db``. The Data
Explorer federates both DBs, so every producer — live trader AND trainer
— is queryable from one place. The sidecar is deliberately separate from
the money DB so the ingest writers never touch the live trader's journal.

Design:
  * Idempotent full-rebuild per ingest (the sources are small — a few
    thousand rows total). One transaction, so readers see a consistent
    snapshot.
  * Lazy + mtime-gated: ``build_if_stale()`` rebuilds only when a mirror
    file changed since the last ingest, so the Data Explorer is always
    fresh without a dedicated timer. An explicit ``python -m
    src.units.db.trainer_store`` entry point exists for an operator
    action / cron if a push-time ingest is ever preferred.
  * Every table keeps a ``data`` JSON column with the full source record,
    so nothing is lost even when the source schema drifts; a few common
    fields are promoted to first-class columns for filtering.

Tables (all in trainer_store.db):
  training_cycle, dataset_builds, db_pulls, model_registry,
  experiment_runs, backtest_sweeps, plus a private ``_ingest_meta``.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.paths import runtime_logs_dir, trainer_store_db_path

logger = logging.getLogger(__name__)

_MIRROR_SUBPATH = "trainer_mirror"

# Tables the sidecar owns (so federation / clearing knows the set).
TRAINER_STORE_TABLES = (
    "training_cycle",
    "dataset_builds",
    "db_pulls",
    "model_registry",
    "experiment_runs",
    "backtest_sweeps",
)


def _mirror_root() -> Path:
    return runtime_logs_dir() / _MIRROR_SUBPATH


def _iter_jsonl(path: Path):
    if not path.exists():
        return
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    yield rec
    except OSError as exc:
        logger.warning("trainer_store: could not read %s: %s", path, exc)


def _source_signature(mirror: Path) -> str:
    """A cheap fingerprint of the mirror (max mtime + file count) so we
    only rebuild when something actually changed."""
    if not mirror.exists():
        return "absent"
    latest = 0.0
    count = 0
    total_size = 0
    for p in mirror.rglob("*"):
        if p.is_file():
            count += 1
            try:
                st = p.stat()
                latest = max(latest, st.st_mtime)
                total_size += st.st_size
            except OSError:
                pass
    # Include total byte size so an append within the same mtime-second is
    # still detected (mtime granularity can be coarse on some filesystems).
    return f"{latest:.3f}:{count}:{total_size}"


# --- table builders -------------------------------------------------------

def _build_training_cycle(conn: sqlite3.Connection, mirror: Path) -> int:
    conn.execute("DROP TABLE IF EXISTS training_cycle")
    conn.execute(
        "CREATE TABLE training_cycle ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, status TEXT, "
        "head TEXT, data TEXT)"
    )
    rows = [
        (r.get("ts"), r.get("status"), r.get("head"), json.dumps(r, default=str))
        for r in _iter_jsonl(mirror / "training_cycle.jsonl")
    ]
    conn.executemany(
        "INSERT INTO training_cycle (ts, status, head, data) VALUES (?,?,?,?)",
        rows,
    )
    return len(rows)


def _build_dataset_builds(conn: sqlite3.Connection, mirror: Path) -> int:
    conn.execute("DROP TABLE IF EXISTS dataset_builds")
    conn.execute(
        "CREATE TABLE dataset_builds ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, status TEXT, "
        "family TEXT, version TEXT, data TEXT)"
    )
    rows = [
        (r.get("ts"), r.get("status"), r.get("family"), r.get("version"),
         json.dumps(r, default=str))
        for r in _iter_jsonl(mirror / "trainer" / "dataset_builds.jsonl")
    ]
    conn.executemany(
        "INSERT INTO dataset_builds (ts, status, family, version, data) "
        "VALUES (?,?,?,?,?)",
        rows,
    )
    return len(rows)


def _build_db_pulls(conn: sqlite3.Connection, mirror: Path) -> int:
    conn.execute("DROP TABLE IF EXISTS db_pulls")
    conn.execute(
        "CREATE TABLE db_pulls ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, status TEXT, "
        "artifact TEXT, size_bytes INTEGER, lines INTEGER, data TEXT)"
    )
    rows = [
        (r.get("ts"), r.get("status"), r.get("artifact"),
         r.get("size_bytes"), r.get("lines"), json.dumps(r, default=str))
        for r in _iter_jsonl(mirror / "trainer" / "db_pulls.jsonl")
    ]
    conn.executemany(
        "INSERT INTO db_pulls (ts, status, artifact, size_bytes, lines, data) "
        "VALUES (?,?,?,?,?,?)",
        rows,
    )
    return len(rows)


def _build_model_registry(conn: sqlite3.Connection, mirror: Path) -> int:
    conn.execute("DROP TABLE IF EXISTS model_registry")
    conn.execute(
        "CREATE TABLE model_registry ("
        "model_id TEXT PRIMARY KEY, status TEXT, target_deployment_stage TEXT, "
        "model_family TEXT, created_at TEXT, data TEXT)"
    )
    rows = []
    for r in _iter_jsonl(mirror / "registry.jsonl"):
        manifest = r.get("manifest") if isinstance(r.get("manifest"), dict) else {}
        rows.append((
            r.get("model_id"),
            r.get("status"),
            r.get("target_deployment_stage") or manifest.get("target_deployment_stage"),
            manifest.get("model_family"),
            r.get("created_at"),
            json.dumps(r, default=str),
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO model_registry "
        "(model_id, status, target_deployment_stage, model_family, created_at, data) "
        "VALUES (?,?,?,?,?,?)",
        rows,
    )
    return len(rows)


def _build_experiment_runs(conn: sqlite3.Connection, mirror: Path) -> int:
    conn.execute("DROP TABLE IF EXISTS experiment_runs")
    conn.execute(
        "CREATE TABLE experiment_runs ("
        "model_id TEXT, run_id TEXT, metrics TEXT, manifest TEXT, "
        "PRIMARY KEY (model_id, run_id))"
    )
    runs_root = mirror / "experiments-runs"
    rows = []
    if runs_root.is_dir():
        for model_dir in sorted(p for p in runs_root.iterdir() if p.is_dir()):
            for run_dir in sorted(p for p in model_dir.iterdir() if p.is_dir()):
                metrics = _read_json_text(run_dir / "metrics.json")
                manifest = _read_json_text(run_dir / "manifest.json")
                if metrics is None and manifest is None:
                    continue
                rows.append((model_dir.name, run_dir.name, metrics, manifest))
    conn.executemany(
        "INSERT OR REPLACE INTO experiment_runs (model_id, run_id, metrics, manifest) "
        "VALUES (?,?,?,?)",
        rows,
    )
    return len(rows)


def _build_backtest_sweeps(conn: sqlite3.Connection, mirror: Path) -> int:
    conn.execute("DROP TABLE IF EXISTS backtest_sweeps")
    conn.execute(
        "CREATE TABLE backtest_sweeps ("
        "date TEXT PRIMARY KEY, summary_md TEXT, metrics TEXT)"
    )
    sweeps_root = mirror / "backtests"
    rows = []
    if sweeps_root.is_dir():
        for date_dir in sorted(p for p in sweeps_root.iterdir() if p.is_dir()):
            summary = date_dir / "SUMMARY.md"
            summary_md = summary.read_text(encoding="utf-8") if summary.exists() else None
            metrics = _read_json_text(date_dir / "all_metrics.json")
            rows.append((date_dir.name, summary_md, metrics))
    conn.executemany(
        "INSERT OR REPLACE INTO backtest_sweeps (date, summary_md, metrics) "
        "VALUES (?,?,?)",
        rows,
    )
    return len(rows)


def _read_json_text(path: Path) -> Optional[str]:
    """Return the file's text if it parses as JSON, else None."""
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        json.loads(text)  # validate
        return text
    except (OSError, json.JSONDecodeError):
        return None


_BUILDERS = (
    _build_training_cycle,
    _build_dataset_builds,
    _build_db_pulls,
    _build_model_registry,
    _build_experiment_runs,
    _build_backtest_sweeps,
)


def ingest_trainer_store(
    *, mirror_root: Optional[Path] = None, db_path: Optional[str] = None
) -> Dict[str, Any]:
    """(Re)build the trainer-store sidecar from the trainer mirror.

    Idempotent full rebuild in a single transaction. Returns a dict of
    per-table row counts plus the resolved paths. Best-effort: a missing
    mirror yields empty tables (not an error).
    """
    mirror = mirror_root or _mirror_root()
    path = db_path or trainer_store_db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:  # allow-silent: WAL is an optimization; ingest proceeds in rollback-journal mode
            pass
        conn.execute(
            "CREATE TABLE IF NOT EXISTS _ingest_meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        counts: Dict[str, Any] = {}
        conn.execute("BEGIN")
        for builder in _BUILDERS:
            name = builder.__name__.replace("_build_", "")
            counts[name] = builder(conn, mirror)
        conn.execute(
            "INSERT OR REPLACE INTO _ingest_meta (key, value) VALUES ('source_signature', ?)",
            (_source_signature(mirror),),
        )
        from datetime import datetime, timezone
        conn.execute(
            "INSERT OR REPLACE INTO _ingest_meta (key, value) VALUES ('ingested_at', ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()
    counts["db"] = path
    counts["mirror"] = str(mirror)
    return counts


def build_if_stale(*, mirror_root: Optional[Path] = None,
                   db_path: Optional[str] = None) -> bool:
    """Rebuild the sidecar only if the mirror changed since last ingest.

    Returns True if a rebuild ran. Cheap to call on every Data Explorer
    request: it stats the mirror and compares a signature. Best-effort —
    any failure is swallowed so the Explorer never 500s on a stale build.
    """
    mirror = mirror_root or _mirror_root()
    path = db_path or trainer_store_db_path()
    # Never materialize an empty sidecar when there is no mirror to ingest
    # (e.g. dev boxes / CI / tests with no trainer_mirror/ directory). This
    # keeps the Data Explorer from sprouting empty trainer tables and avoids
    # writing a stray trainer_store.db into the repo tree.
    if not mirror.exists():
        return False
    try:
        current = _source_signature(mirror)
        if Path(path).exists():
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            try:
                row = conn.execute(
                    "SELECT value FROM _ingest_meta WHERE key='source_signature'"
                ).fetchone()
            except sqlite3.Error:  # allow-silent: missing/old meta → treat as stale; a full rebuild follows
                row = None
            finally:
                conn.close()
            if row and row[0] == current:
                return False  # already fresh
        ingest_trainer_store(mirror_root=mirror, db_path=path)
        return True
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort freshness; the Data Explorer must not 500 on a stale build
        logger.warning("trainer_store: build_if_stale failed: %s", exc)
        return False


def _main(argv: List[str]) -> int:
    counts = ingest_trainer_store()
    print(json.dumps(counts, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    raise SystemExit(_main(sys.argv))
