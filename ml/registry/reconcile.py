"""Reconcile a registered model's ``runs`` list against its experiment dir.

Walks ``<experiments_root>/<model_id>/`` and rebuilds the registry
entry's ``runs`` tuple from every run found on disk, sorted by run_id
(which is a sortable UTC timestamp produced by ``run_experiment``).
Idempotent — running twice yields the same result.

Why this exists
---------------
Entries written before #1133 had no ``runs`` field. When those entries
got re-registered after #1133 merged, ``register()`` appended the new
RunRecord to an empty list, so today's history shows ``runs=1`` even
though there were prior runs visible on disk under ``ml/experiments-runs/``.

This module reads each experiment dir's ``metrics.json`` and rebuilds
the full ``runs`` list. Top-level fields (``status``, ``created_at``,
``stage_history``, ``history``, the *current* ``metrics`` /
``code_revision`` / ``model_state_path``) are preserved exactly —
only ``runs`` changes.

Limitations
-----------
``code_revision`` is not persisted in the experiment dir, only the
registry. Reconstructed RunRecords therefore use ``"unknown"`` for
``code_revision`` (and an explanatory ``by`` field so the gap is
self-documenting). The last-run's ``code_revision`` is preserved
because we mirror it from the entry's existing latest RunRecord.

CLI
---
::

    python -m ml.registry.reconcile \\
        --registry-root  ml/registry-store \\
        --experiments-root ml/experiments-runs \\
        [--model-id MODEL_ID]   # optional; default = all entries
        [--dry-run]             # report what would change without writing
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .model_registry import ModelRegistry, RegistryEntry, RunRecord


_UNKNOWN_REVISION = "unknown"
_BACKFILL_BY = "registry-reconcile (backfilled from disk)"


@dataclass(frozen=True)
class ReconcileResult:
    model_id: str
    before_runs: int
    after_runs: int
    added_run_ids: tuple[str, ...]
    wrote: bool


def _list_run_dirs(model_root: Path) -> list[Path]:
    if not model_root.is_dir():
        return []
    return sorted(p for p in model_root.iterdir() if p.is_dir())


def _load_metrics(run_dir: Path) -> dict[str, float]:
    metrics_path = run_dir / "metrics.json"
    if not metrics_path.is_file():
        return {}
    try:
        raw = json.loads(metrics_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: float(v) for k, v in raw.items() if isinstance(v, (int, float))}


def _run_dir_mtime_utc(run_dir: Path) -> datetime:
    return datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc)


def _build_runs_from_disk(
    entry: RegistryEntry,
    model_root: Path,
) -> tuple[RunRecord, ...]:
    """Reconstruct ``runs`` for one model from its experiment dir.

    Strictly additive: every RunRecord already in ``entry.runs`` is
    preserved verbatim (matched by ``run_id``), even if its run dir is
    missing from disk. Any run dir on disk *without* a matching
    RunRecord is synthesized with ``code_revision="unknown"`` and
    ``by="registry-reconcile ..."`` so the gap is self-documenting.
    Result is sorted by ``run_id`` (a sortable UTC timestamp).
    """
    existing_by_run_id = {r.run_id: r for r in entry.runs}
    seen: set[str] = set()
    out: list[RunRecord] = []
    for run_dir in _list_run_dirs(model_root):
        run_id = run_dir.name
        seen.add(run_id)
        if run_id in existing_by_run_id:
            out.append(existing_by_run_id[run_id])
            continue
        out.append(
            RunRecord(
                run_id=run_id,
                model_state_path=str((run_dir / "model_state.json").resolve()),
                metrics=_load_metrics(run_dir),
                code_revision=_UNKNOWN_REVISION,
                at=_run_dir_mtime_utc(run_dir),
                by=_BACKFILL_BY,
            )
        )
    # Preserve any RunRecord whose dir no longer exists on disk.
    for run_id, record in existing_by_run_id.items():
        if run_id not in seen:
            out.append(record)
    out.sort(key=lambda r: r.run_id)
    return tuple(out)


def reconcile_model(
    *,
    model_id: str,
    registry: ModelRegistry,
    experiments_root: Path,
    dry_run: bool = False,
) -> ReconcileResult:
    entry = registry.get(model_id)
    new_runs = _build_runs_from_disk(entry, experiments_root / model_id)
    before_ids = {r.run_id for r in entry.runs}
    after_ids = {r.run_id for r in new_runs}
    added = tuple(sorted(after_ids - before_ids))
    if not added:
        return ReconcileResult(
            model_id=model_id,
            before_runs=len(entry.runs),
            after_runs=len(new_runs),
            added_run_ids=(),
            wrote=False,
        )
    if not dry_run:
        new_entry = RegistryEntry(
            model_id=entry.model_id,
            status=entry.status,
            manifest=entry.manifest,
            model_state_path=entry.model_state_path,
            metrics=entry.metrics,
            code_revision=entry.code_revision,
            created_at=entry.created_at,
            history=entry.history,
            notes=entry.notes,
            target_deployment_stage=entry.target_deployment_stage,
            stage_history=entry.stage_history,
            runs=new_runs,
        )
        registry._write(new_entry)  # noqa: SLF001 — same package
    return ReconcileResult(
        model_id=model_id,
        before_runs=len(entry.runs),
        after_runs=len(new_runs),
        added_run_ids=added,
        wrote=not dry_run,
    )


def reconcile_all(
    *,
    registry_root: Path,
    experiments_root: Path,
    model_ids: Iterable[str] | None = None,
    dry_run: bool = False,
) -> list[ReconcileResult]:
    registry = ModelRegistry(registry_root)
    if model_ids is None:
        targets = [e.model_id for e in registry.list()]
    else:
        targets = list(model_ids)
    return [
        reconcile_model(
            model_id=mid,
            registry=registry,
            experiments_root=experiments_root,
            dry_run=dry_run,
        )
        for mid in targets
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ml.registry.reconcile")
    parser.add_argument(
        "--registry-root",
        type=Path,
        default=Path("ml/registry-store"),
        help="path to the registry JSON store (default: ml/registry-store)",
    )
    parser.add_argument(
        "--experiments-root",
        type=Path,
        default=Path("ml/experiments-runs"),
        help="path to the experiment-run dirs (default: ml/experiments-runs)",
    )
    parser.add_argument(
        "--model-id",
        action="append",
        default=None,
        help="reconcile only this model_id (repeatable); default = all",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing",
    )
    args = parser.parse_args(argv)

    results = reconcile_all(
        registry_root=args.registry_root,
        experiments_root=args.experiments_root,
        model_ids=args.model_id,
        dry_run=args.dry_run,
    )
    for r in results:
        verb = "would add" if args.dry_run else "added"
        if r.added_run_ids:
            print(
                f"  {r.model_id}: runs {r.before_runs} -> {r.after_runs}  "
                f"{verb} {list(r.added_run_ids)}"
            )
        else:
            print(f"  {r.model_id}: runs {r.before_runs}  (in sync)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
