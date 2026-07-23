"""M28 P1 — point-in-time valuation-snapshot store (append-only JSONL).

The persistence half of the value feed: takes the rows
:func:`valuation_feed.build_valuation_reads` produces and appends them to a
point-in-time log, and reads them back — the latest per ``(symbol, metric)`` for
the live sleeve, or the full tail for a backtest / read surface.

Mirrors the repo's observe-only soak-log convention (``allocator_soak`` /
``pairs_soak``): a best-effort append-only writer to
``runtime_logs/valuation_snapshots.jsonl`` + pure readers, never raising.

**Point-in-time discipline (the M28 correctness invariant):** the log is
**append-only** — a revised value is a NEW line (new ``observed_at``), never an
overwrite. So a backtest can reconstruct exactly what was known as-of any past
instant, and the live "latest" read is just the newest ``observed_at`` per key.
No order path. The FRED fetch that produces these runs off-VM; this store is the
handoff the live sleeve reads.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

SNAPSHOT_LOG_NAME = "valuation_snapshots.jsonl"

# The producer commits the snapshot log here (an off-VM GitHub-hosted runner
# appends to it and pushes); the live VM picks it up via ``ict-git-sync``. This
# is the point-in-time handoff the value sleeve reads — see
# ``scripts/macro/valuation_snapshot_produce.py`` + the ``macro-valuation-snapshot``
# workflow. Kept as a repo-relative subpath so the resolver can build it off
# ``repo_root()`` without importing anything execution-side.
COMMITTED_SNAPSHOT_SUBPATH = ("comms", "macro", SNAPSHOT_LOG_NAME)


def snapshot_log_path(path: Optional[Any] = None):
    """Resolve the **write**-side snapshot log path (default
    ``runtime_logs/valuation_snapshots.jsonl``).

    The producer passes an explicit ``path`` (the committed ``comms/macro`` file);
    absent one, this stays ``runtime_logs`` — the write default is deliberately
    NOT the committed path (a stray local write must never land in the repo tree)."""
    if path is not None:
        from pathlib import Path
        return Path(path)
    from src.utils.paths import runtime_logs_dir
    return runtime_logs_dir() / SNAPSHOT_LOG_NAME


def committed_snapshot_log_path():
    """The committed producer output: ``<repo>/comms/macro/valuation_snapshots.jsonl``.
    Best-effort — returns ``None`` if the repo root can't be resolved (e.g. a
    relocated file), so a caller falls through to ``runtime_logs``."""
    try:
        from pathlib import Path
        from src.utils.paths import repo_root
        return Path(repo_root()).joinpath(*COMMITTED_SNAPSHOT_SUBPATH)
    except Exception:  # noqa: BLE001
        return None


def _read_snapshot_log_path(path: Optional[Any] = None):
    """Resolve the **read**-side path. An explicit ``path`` always wins. Absent
    one, prefer the committed ``comms/macro`` file **when it exists** (the value
    sleeve reads what the producer committed + git-synced), else fall back to the
    ``runtime_logs`` write default — so a dev/CI host with neither still resolves
    cleanly to an (empty) runtime_logs path."""
    if path is not None:
        from pathlib import Path
        return Path(path)
    committed = committed_snapshot_log_path()
    try:
        if committed is not None and committed.exists():
            return committed
    except OSError:
        pass
    return snapshot_log_path(None)


def write_snapshots(rows: Iterable[dict], *, path: Optional[Any] = None) -> int:
    """Append snapshot rows to the point-in-time log. Best-effort: returns the
    number written; a bad row is skipped, an I/O error is swallowed (logged)."""
    p = snapshot_log_path(path)
    written = 0
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            for row in rows or []:
                try:
                    fh.write(json.dumps(row, default=str) + "\n")
                    written += 1
                except (TypeError, ValueError):
                    continue
    except OSError as exc:
        logger.warning("valuation_store: append failed (%s)", exc)
    return written


def read_snapshot_records(*, path: Optional[Any] = None, limit: Optional[int] = None) -> list[dict]:
    """Read the log **newest-first** (append order reversed). Best-effort:
    missing file / bad lines → skipped, never raises.

    Reads the committed ``comms/macro`` file when it exists (else ``runtime_logs``),
    so ``read_latest_snapshots`` / ``latest_reads_for_symbol`` — and therefore the
    live sleeve's ``read_latest_snapshots()`` at ``thesis_tick.py`` — surface the
    producer's committed snapshots. An explicit ``path`` overrides (backtest fixtures)."""
    p = _read_snapshot_log_path(path)
    out: list[dict] = []
    try:
        with p.open("r", encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        return []
    out.reverse()
    if limit is not None and limit >= 0:
        out = out[:limit]
    return out


def read_latest_snapshots(*, path: Optional[Any] = None) -> dict[tuple[str, str], dict]:
    """The live-read view: the newest row per ``(symbol, metric)`` by
    ``observed_at`` (ISO-8601 UTC, so lexical max == chronological max).
    Rows missing symbol/metric are ignored."""
    latest: dict[tuple[str, str], dict] = {}
    for row in read_snapshot_records(path=path):  # newest-first
        sym, metric = row.get("symbol"), row.get("metric")
        if sym is None or metric is None:
            continue
        key = (sym, metric)
        prev = latest.get(key)
        if prev is None or str(row.get("observed_at", "")) > str(prev.get("observed_at", "")):
            latest[key] = row
    return latest


def latest_reads_for_symbol(symbol: str, *, path: Optional[Any] = None) -> list[dict]:
    """All latest metric reads for one symbol — the sleeve's per-instrument read."""
    return [
        row for (sym, _metric), row in read_latest_snapshots(path=path).items()
        if sym == symbol
    ]
