#!/usr/bin/env python3
"""Rotate `runtime_logs/shadow_predictions.jsonl` (S-AI-WS7 follow-up).

The shadow audit log grows unbounded — every `ShadowPredictor.predict`
call appends a JSON line. Once shadow mode is active in production
with multiple models per strategy, the file can hit gigabyte scale
in weeks. This script handles rotation:

  - If size > `--max-bytes` (default 100 MiB) OR mtime older than
    `--max-age-days` (default 7), rotate.
  - Rotation:
    1. Rename `<log>` → `<log_stem>.YYYY-MM-DD.jsonl`.
    2. If `--gzip`, gzip the rotated copy (atomic: gzip then unlink).
    3. Touch a fresh empty `<log>` so the writer reopens cleanly.
  - Idempotent: if the active log is missing or already small enough,
    no-op.
  - JSONL events to stdout for systemd journal observability.

Designed to run from a systemd timer (`deploy/ict-shadow-log-rotate
.service` / `.timer`). Always exits 0 unless invocation arguments
are invalid — log rotation MUST NOT crash the system, even on
disk-full or permission-denied; those are logged as `error` events
and the script returns 0 so the timer continues to fire.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_LOG = Path("runtime_logs/shadow_predictions.jsonl")
_DEFAULT_MAX_BYTES = 100 * 1024 * 1024  # 100 MiB
_DEFAULT_MAX_AGE_DAYS = 7


def emit(status: str, **fields) -> None:
    print(json.dumps({"status": status, **fields}))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _should_rotate(
    log: Path,
    *,
    max_bytes: int,
    max_age_seconds: float,
) -> tuple[bool, str]:
    """Return (rotate, reason). reason is a short tag for logging."""
    if not log.is_file():
        return False, "missing"
    size = log.stat().st_size
    if size == 0:
        return False, "empty"
    if size >= max_bytes:
        return True, f"size>={max_bytes}"
    age = time.time() - log.stat().st_mtime
    if age >= max_age_seconds:
        return True, f"age>={max_age_seconds:.0f}s"
    return False, "fresh"


def _next_rotated_path(log: Path, *, now: datetime) -> Path:
    """Compute `<stem>.<YYYY-MM-DD>.<ext>` next to `log`. If that
    path already exists (rotation ran twice in one UTC day), append
    `.N` where N is the next available integer."""
    stem = log.stem
    ext = log.suffix or ".jsonl"
    date = now.strftime("%Y-%m-%d")
    base = log.with_name(f"{stem}.{date}{ext}")
    if not base.exists():
        return base
    n = 1
    while True:
        candidate = log.with_name(f"{stem}.{date}.{n}{ext}")
        if not candidate.exists():
            return candidate
        n += 1


def rotate(
    log: Path,
    *,
    max_bytes: int,
    max_age_seconds: float,
    gzip_rotated: bool,
    now: datetime | None = None,
) -> int:
    """Rotate `log` if needed. Returns 0 on every outcome (errors
    are reported via JSONL events, never raised)."""
    now = now or _utc_now()
    if not log.parent.is_dir():
        emit("error", reason="log_dir_missing", path=str(log.parent))
        return 0
    rotate_now, reason = _should_rotate(
        log, max_bytes=max_bytes, max_age_seconds=max_age_seconds,
    )
    if not rotate_now:
        emit("noop", reason=reason, path=str(log))
        return 0
    target = _next_rotated_path(log, now=now)
    try:
        # Atomic rename within the same filesystem.
        os.replace(log, target)
    except OSError as exc:
        emit("error", reason="rename_failed", err=str(exc),
             source=str(log), target=str(target))
        return 0
    # Touch a fresh empty log so the writer reopens cleanly.
    try:
        log.touch()
    except OSError as exc:
        emit("error", reason="touch_failed", err=str(exc), path=str(log))
        return 0
    final_target = target
    if gzip_rotated:
        gz_target = target.with_suffix(target.suffix + ".gz")
        try:
            with target.open("rb") as src, gzip.open(gz_target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            target.unlink()
            final_target = gz_target
        except OSError as exc:
            emit("warning", reason="gzip_failed", err=str(exc),
                 rotated=str(target))
            # Keep the un-gzipped rotated file; rotation still succeeded.
    emit(
        "rotated",
        source=str(log),
        target=str(final_target),
        reason=reason,
        gzipped=final_target.suffix == ".gz",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rotate_shadow_log",
        description=(
            "Rotate runtime_logs/shadow_predictions.jsonl when it "
            "exceeds size or age thresholds."
        ),
    )
    parser.add_argument(
        "--log", type=Path, default=_DEFAULT_LOG,
        help=f"path to the active log (default: {_DEFAULT_LOG})",
    )
    parser.add_argument(
        "--max-bytes", type=int, default=_DEFAULT_MAX_BYTES,
        help=f"rotate when size exceeds this (default: {_DEFAULT_MAX_BYTES})",
    )
    parser.add_argument(
        "--max-age-days", type=float, default=_DEFAULT_MAX_AGE_DAYS,
        help=f"rotate when mtime older than N days (default: {_DEFAULT_MAX_AGE_DAYS})",
    )
    parser.add_argument(
        "--gzip", action="store_true",
        help="gzip the rotated file (default: keep plain JSONL)",
    )
    args = parser.parse_args(argv)
    return rotate(
        Path(args.log),
        max_bytes=args.max_bytes,
        max_age_seconds=args.max_age_days * 86_400,
        gzip_rotated=args.gzip,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
