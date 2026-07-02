"""Standing corpus store — the read-mostly wide-panel catalog + series files (M19 corpus C1b).

The foundation the wide multi-asset corpus writes into and the label-free T1.2
encoder later reads from (design:
[`docs/research/T0-data-corpus-DESIGN.md`](../../docs/research/T0-data-corpus-DESIGN.md)).
Two parts, both trainer-side, read-mostly, **never** `trade_journal.db`:

- **A catalog** (`<root>/catalog.json`) — one entry per series
  (`series_id → {group, source, source_ref, cadence, first_date, last_date,
  row_count, refreshed_at}`) so a builder can see *what's in the corpus* and check
  freshness without opening every file.
- **The series files** (`<root>/<group>/<series_id>.jsonl`) — one append-mostly
  JSONL per series (`{"date": "YYYY-MM-DD", "value": <float>}` ascending). Each
  `write_series` rewrites its own file only (idempotent by series).

**JSONL now, parquet when volume warrants.** The design names parquet as the
eventual bulk format; this C1b foundation ships **stdlib-only JSONL** (no
pandas/pyarrow dependency, works on any build host, unit-testable without the ML
stack) — the wide daily panel is small enough that columnar bulk isn't yet the
bottleneck. Swapping the file backend to parquet later is a `_write_rows` /
`_read_rows` change behind this same API; the catalog + layout are unchanged.

**No new DB table** — the catalog is a JSON file beside the series, so this adds
nothing to the federated `trainer_store.db` schema (and trips no DB-wiring CI
guard). If/when the panel is federated into the Data Explorer, that's a separate,
explicitly-wired step.

Path resolution: `corpus_root()` honours `$CORPUS_ROOT`, else defaults beside the
trainer mirror (`runtime_logs/trainer_mirror/corpus`). Kept dependency-free (no
`src.*` import) so the store is usable from any adapter/producer and testable in
isolation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

CORPUS_ROOT_ENV = "CORPUS_ROOT"
_DEFAULT_ROOT = "runtime_logs/trainer_mirror/corpus"
_CATALOG_NAME = "catalog.json"


def corpus_root(root: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the corpus root: explicit arg → ``$CORPUS_ROOT`` → default.

    Never a bare CWD basename — the default is the repo-relative trainer-mirror
    corpus dir, mirroring where the other trainer artifacts live.
    """
    if root is not None:
        return Path(root)
    env = os.environ.get(CORPUS_ROOT_ENV)
    if env:
        return Path(env)
    return Path(_DEFAULT_ROOT)


def _catalog_path(root: Path) -> Path:
    return root / _CATALOG_NAME


def load_catalog(root: str | os.PathLike[str] | None = None) -> dict[str, dict[str, Any]]:
    """The full series catalog (``{}`` when the corpus is empty / absent)."""
    path = _catalog_path(corpus_root(root))
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _series_path(root: Path, group: str, series_id: str) -> Path:
    return root / group / f"{series_id}.jsonl"


def _clean_rows(
    rows: Iterable[Mapping[str, Any]], *, date_key: str, value_key: str
) -> list[dict[str, Any]]:
    """Ascending-by-date ``[{date, value}]`` with missing/non-finite values dropped."""
    out: list[dict[str, Any]] = []
    for r in rows:
        date = r.get(date_key)
        raw = r.get(value_key)
        if date is None or raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value != value or value in (float("inf"), float("-inf")):  # NaN/inf guard
            continue
        out.append({"date": str(date)[:10], "value": value})
    out.sort(key=lambda x: x["date"])
    return out


def write_series(
    series_id: str,
    group: str,
    source: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    refreshed_at: str,
    root: str | os.PathLike[str] | None = None,
    source_ref: str | None = None,
    cadence: str = "daily",
    date_key: str = "date",
    value_key: str = "value",
) -> dict[str, Any]:
    """Write one series' JSONL file and upsert its catalog entry.

    Rewrites `<root>/<group>/<series_id>.jsonl` (idempotent per series) and updates
    `<root>/catalog.json`. ``refreshed_at`` is caller-supplied (an ISO timestamp) so
    the store never calls a wall clock itself — keeps it deterministic + testable.
    Returns the catalog entry that was written.
    """
    root_path = corpus_root(root)
    clean = _clean_rows(rows, date_key=date_key, value_key=value_key)

    series_file = _series_path(root_path, group, series_id)
    series_file.parent.mkdir(parents=True, exist_ok=True)
    with series_file.open("w", encoding="utf-8") as fh:
        for r in clean:
            fh.write(json.dumps(r) + "\n")

    entry: dict[str, Any] = {
        "group": group,
        "source": source,
        "source_ref": source_ref if source_ref is not None else series_id,
        "cadence": cadence,
        "first_date": clean[0]["date"] if clean else None,
        "last_date": clean[-1]["date"] if clean else None,
        "row_count": len(clean),
        "refreshed_at": refreshed_at,
        "path": str(series_file.relative_to(root_path)),
    }

    catalog = load_catalog(root_path)
    catalog[series_id] = entry
    catalog_file = _catalog_path(root_path)
    catalog_file.parent.mkdir(parents=True, exist_ok=True)
    catalog_file.write_text(json.dumps(catalog, indent=2, sort_keys=True), encoding="utf-8")
    return entry


def read_series(
    series_id: str,
    *,
    root: str | os.PathLike[str] | None = None,
) -> list[dict[str, Any]]:
    """The ascending ``[{date, value}]`` rows of a series (``[]`` if unknown/absent)."""
    catalog = load_catalog(root)
    entry = catalog.get(series_id)
    if not entry:
        return []
    root_path = corpus_root(root)
    path = root_path / entry.get("path", "")
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
