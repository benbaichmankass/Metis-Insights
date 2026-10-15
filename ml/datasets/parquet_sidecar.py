"""Parquet sidecar beside a dataset's ``data.jsonl`` (DATA-STORE-PARQUET-GC).

A dataset version dir holds ``data.jsonl`` + ``metadata.json``. For families
that opt in (``DatasetBuilder.parquet_sidecar = True`` — today only
``market_features``, 7.9 of the trainer's 9.6 GB datasets-out on 2026-10-14)
the builder also writes ``data.parquet`` from the JSONL it just wrote, and the
readers (``load_dataset_rows``) prefer it: a column-projected Parquet read
skips the per-line ``json.loads`` of every column the manifest never uses.

**JSONL stays the source of truth.** The sidecar is derived from it, records
the byte size of the JSONL it was derived from, and the reader uses it only
while that size still matches (or the JSONL is absent). A stale or broken
sidecar can therefore never be read in place of fresher JSONL, and every
failure here falls back to the JSONL path the loaders always used.

Exact parity is a construction rule, not a hope. ``write_sidecar`` refuses
(returns a reason, writes nothing) unless:
  - every row has the SAME key set (a missing key and a null would otherwise
    both read back as ``None`` — old versions built before a schema widening
    can be ragged; they stay JSONL-only);
  - every column's non-null values are ONE Python type among str / float /
    int / bool (int within int64).
Under those rules a row read back from Parquet equals the ``json.loads`` row:
same keys, same values, same Python types (float64 round-trips exactly; NaN
is kept). ``verify_parity`` proves it on a given dir, row by row.

``pyarrow`` is optional: absent, the writer reports ``pyarrow unavailable``
and the readers use JSONL — never an import error on a box without it.

CLI (wiring: called by DatasetBuilder.build for opted-in families; the CLI is
manual-only — backfill a frozen version, or prove parity on one)::

    python -m ml.datasets.parquet_sidecar convert <version_dir> [...]
    python -m ml.datasets.parquet_sidecar verify  <version_dir> [...]
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

JSONL_NAME = "data.jsonl"
PARQUET_NAME = "data.parquet"
_META_SRC_BYTES = b"metis.source_jsonl_bytes"
_META_ROWS = b"metis.row_count"
_BATCH_ROWS = 50_000
_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1


def _pa():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return None, None
    return pa, pq


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line:
                yield json.loads(line)


def _scan_columns(path: Path) -> tuple[list[str] | None, dict[str, type], int, str]:
    """One pass over the JSONL: (columns, per-column type, rows, refusal)."""
    cols: list[str] | None = None
    keyset: frozenset[str] | None = None
    types: dict[str, type] = {}
    n = 0
    for obj in _iter_jsonl(path):
        if not isinstance(obj, dict):
            return None, {}, n, f"line {n + 1} is not an object"
        if keyset is None:
            cols = list(obj.keys())
            keyset = frozenset(cols)
        elif obj.keys() != keyset:
            return None, {}, n, f"ragged rows: line {n + 1} key set differs from line 1"
        for k, v in obj.items():
            if v is None:
                continue
            t = type(v)
            if t not in (str, float, int, bool):
                return None, {}, n, f"column {k!r} holds {t.__name__}"
            if t is int and not (_INT64_MIN <= v <= _INT64_MAX):
                return None, {}, n, f"column {k!r} int outside int64"
            prev = types.setdefault(k, t)
            if prev is not t:
                return None, {}, n, f"column {k!r} mixes {prev.__name__} and {t.__name__}"
        n += 1
    return cols, types, n, ""


def remove_sidecar(dataset_dir: Path) -> None:
    try:
        (Path(dataset_dir) / PARQUET_NAME).unlink()
    except FileNotFoundError:
        pass


def write_sidecar(dataset_dir: Path) -> dict[str, Any]:
    """Derive ``data.parquet`` from ``data.jsonl``. Never raises.

    Returns ``{"status": "written"|"refused"|"failed", ...}``; on anything but
    ``written`` no ``data.parquet`` is left behind.
    """
    dataset_dir = Path(dataset_dir)
    src = dataset_dir / JSONL_NAME
    dst = dataset_dir / PARQUET_NAME
    tmp = dataset_dir / (PARQUET_NAME + ".tmp")
    pa, pq = _pa()
    if pa is None:
        remove_sidecar(dataset_dir)
        return {"status": "refused", "reason": "pyarrow unavailable"}
    if not src.is_file():
        remove_sidecar(dataset_dir)
        return {"status": "refused", "reason": f"no {JSONL_NAME}"}
    try:
        src_bytes = src.stat().st_size
        cols, types, n, why = _scan_columns(src)
        if why or cols is None:
            remove_sidecar(dataset_dir)
            return {"status": "refused", "reason": why or "empty dataset", "rows": n}
        tok = {str: pa.string(), float: pa.float64(), int: pa.int64(), bool: pa.bool_()}
        # An all-null column has no observed type: store it as string (reads
        # back None either way).
        schema = pa.schema(
            [pa.field(c, tok[types.get(c, str)]) for c in cols],
            metadata={_META_SRC_BYTES: str(src_bytes).encode(),
                      _META_ROWS: str(n).encode()},
        )
        with pq.ParquetWriter(str(tmp), schema, compression="zstd") as w:
            batch: list[dict[str, Any]] = []
            for obj in _iter_jsonl(src):
                batch.append(obj)
                if len(batch) >= _BATCH_ROWS:
                    w.write_table(pa.Table.from_pylist(batch, schema=schema))
                    batch = []
            if batch:
                w.write_table(pa.Table.from_pylist(batch, schema=schema))
        if src.stat().st_size != src_bytes:
            raise RuntimeError("data.jsonl changed while the sidecar was written")
        os.replace(tmp, dst)
        return {"status": "written", "rows": n, "columns": len(cols),
                "jsonl_bytes": src_bytes, "parquet_bytes": dst.stat().st_size}
    except Exception as exc:  # noqa: BLE001 — the sidecar is optional; JSONL stays
        for p in (tmp, dst):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        return {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}


def fresh_sidecar(dataset_dir: Path) -> Path | None:
    """The sidecar path when it may be read in place of the JSONL, else None."""
    dataset_dir = Path(dataset_dir)
    pq_path = dataset_dir / PARQUET_NAME
    if not pq_path.is_file():
        return None
    pa, pq = _pa()
    if pa is None:
        return None
    try:
        meta = pq.read_schema(str(pq_path)).metadata or {}
        recorded = int(meta.get(_META_SRC_BYTES, b"-1"))
    except Exception:  # noqa: BLE001 — unreadable sidecar = not fresh
        return None
    src = dataset_dir / JSONL_NAME
    if src.is_file() and src.stat().st_size != recorded:
        return None
    return pq_path


def dataset_present(dataset_dir: Path) -> bool:
    d = Path(dataset_dir)
    return (d / JSONL_NAME).is_file() or fresh_sidecar(d) is not None


def _load_parquet(path: Path, keep: frozenset[str] | set[str] | None) -> list[dict[str, Any]]:
    _pa_mod, pq = _pa()
    pf = pq.ParquetFile(str(path))
    names = pf.schema_arrow.names
    columns = None
    if keep:
        hit = [c for c in names if c in keep]
        # Same fail-open rule as the JSONL loader: a keep-set that misses
        # every column loads everything.
        columns = hit or None
    intern = sys.intern
    rows: list[dict[str, Any]] = []
    for batch in pf.iter_batches(batch_size=_BATCH_ROWS, columns=columns):
        cols = [intern(c) for c in batch.schema.names]
        data = [batch.column(i).to_pylist() for i in range(batch.num_columns)]
        short_str = [batch.schema.field(i).type == _pa_mod.string()
                     for i in range(batch.num_columns)]
        for j in range(batch.num_rows):
            row: dict[str, Any] = {}
            for i, c in enumerate(cols):
                v = data[i][j]
                if short_str[i] and columns is not None and v is not None and len(v) <= 24:
                    v = intern(v)
                row[c] = v
            rows.append(row)
    return rows


def load_dataset_rows(
    dataset_dir: Path,
    keep: frozenset[str] | set[str] | None = None,
) -> list[dict[str, Any]]:
    """Rows of a dataset version dir: the fresh Parquet sidecar if there is
    one, else ``data.jsonl`` via the runner's projecting JSONL loader.

    Raises ``FileNotFoundError`` when neither is present.
    """
    dataset_dir = Path(dataset_dir)
    side = fresh_sidecar(dataset_dir)
    if side is not None:
        try:
            return _load_parquet(side, keep)
        except Exception as exc:  # noqa: BLE001 — fall back to the source of truth
            print(json.dumps({"status": "parquet_sidecar_read_failed",
                              "dir": str(dataset_dir), "reason": repr(exc)}),
                  file=sys.stderr)
    src = dataset_dir / JSONL_NAME
    if not src.is_file():
        raise FileNotFoundError(src)
    from ..experiments.runner import _load_jsonl  # local import: avoids a cycle

    return _load_jsonl(src, keep=keep)


def _same(a: Any, b: Any) -> bool:
    if type(a) is not type(b):
        return False
    if type(a) is float and math.isnan(a):
        return math.isnan(b)
    return a == b


def verify_parity(dataset_dir: Path) -> dict[str, Any]:
    """Row-by-row, type-exact comparison of ``data.parquet`` vs ``data.jsonl``."""
    dataset_dir = Path(dataset_dir)
    side = fresh_sidecar(dataset_dir)
    if side is None:
        return {"status": "no_fresh_sidecar", "dir": str(dataset_dir)}
    pq_rows = iter(_load_parquet(side, None))
    n = 0
    for js in _iter_jsonl(dataset_dir / JSONL_NAME):
        pr = next(pq_rows, None)
        if pr is None:
            return {"status": "MISMATCH", "dir": str(dataset_dir), "row": n,
                    "detail": "parquet has fewer rows"}
        if js.keys() != pr.keys():
            return {"status": "MISMATCH", "dir": str(dataset_dir), "row": n,
                    "detail": "key sets differ"}
        for k, v in js.items():
            if not _same(v, pr[k]):
                return {"status": "MISMATCH", "dir": str(dataset_dir), "row": n,
                        "detail": f"{k}: jsonl {v!r} ({type(v).__name__}) vs "
                                  f"parquet {pr[k]!r} ({type(pr[k]).__name__})"}
        n += 1
    if next(pq_rows, None) is not None:
        return {"status": "MISMATCH", "dir": str(dataset_dir), "row": n,
                "detail": "parquet has more rows"}
    return {"status": "PARITY", "dir": str(dataset_dir), "rows": n,
            "jsonl_bytes": (dataset_dir / JSONL_NAME).stat().st_size,
            "parquet_bytes": side.stat().st_size}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] not in ("convert", "verify"):
        print(__doc__)
        return 2
    rc = 0
    for d in argv[1:]:
        if argv[0] == "convert":
            out = {"dir": d, **write_sidecar(Path(d))}
            rc |= 0 if out["status"] == "written" else 1
        else:
            out = verify_parity(Path(d))
            rc |= 0 if out["status"] == "PARITY" else 1
        print(json.dumps(out))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
