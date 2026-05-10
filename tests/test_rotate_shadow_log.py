"""Tests for `scripts/ops/rotate_shadow_log.py` (S-AI-WS7-FU)."""
from __future__ import annotations

import gzip
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from scripts.ops import rotate_shadow_log as rsl  # noqa: E402


def _capture(fn, *args, **kwargs) -> tuple[int, list[dict]]:
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        rc = fn(*args, **kwargs)
    finally:
        sys.stdout = saved
    events = []
    for line in buf.getvalue().splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return rc, events


def _write(path: Path, content: bytes | str = b"") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        content = content.encode()
    path.write_bytes(content)


# ---------------------------------------------------------------------------
# rotate()
# ---------------------------------------------------------------------------


class TestNoop:
    def test_missing_log_is_noop(self, tmp_path: Path):
        log = tmp_path / "shadow.jsonl"
        rc, events = _capture(
            rsl.rotate, log,
            max_bytes=100, max_age_seconds=86_400, gzip_rotated=False,
        )
        assert rc == 0
        assert events[-1]["status"] == "noop"
        assert events[-1]["reason"] == "missing"

    def test_empty_log_is_noop(self, tmp_path: Path):
        log = tmp_path / "shadow.jsonl"
        _write(log, b"")
        rc, events = _capture(
            rsl.rotate, log,
            max_bytes=100, max_age_seconds=86_400, gzip_rotated=False,
        )
        assert rc == 0
        assert events[-1]["status"] == "noop"
        assert events[-1]["reason"] == "empty"

    def test_fresh_small_log_is_noop(self, tmp_path: Path):
        log = tmp_path / "shadow.jsonl"
        _write(log, b'{"ok": 1}\n')
        rc, events = _capture(
            rsl.rotate, log,
            max_bytes=10_000_000, max_age_seconds=86_400, gzip_rotated=False,
        )
        assert rc == 0
        assert events[-1]["status"] == "noop"
        assert events[-1]["reason"] == "fresh"

    def test_missing_log_dir_emits_error_but_exits_0(self, tmp_path: Path):
        log = tmp_path / "nonexistent" / "shadow.jsonl"
        rc, events = _capture(
            rsl.rotate, log,
            max_bytes=100, max_age_seconds=86_400, gzip_rotated=False,
        )
        assert rc == 0
        assert events[-1]["status"] == "error"
        assert events[-1]["reason"] == "log_dir_missing"


class TestSizeRotation:
    def test_oversize_log_rotates(self, tmp_path: Path):
        log = tmp_path / "shadow.jsonl"
        _write(log, b"x" * 200)
        rc, events = _capture(
            rsl.rotate, log,
            max_bytes=100, max_age_seconds=86_400, gzip_rotated=False,
        )
        assert rc == 0
        terminal = events[-1]
        assert terminal["status"] == "rotated"
        assert terminal["reason"].startswith("size>=")
        # Original log re-touched empty.
        assert log.is_file()
        assert log.stat().st_size == 0
        # Rotated file exists with the date-suffixed name.
        rotated = Path(terminal["target"])
        assert rotated.is_file()
        assert rotated.stat().st_size == 200

    def test_rotated_filename_carries_utc_date(self, tmp_path: Path):
        log = tmp_path / "shadow.jsonl"
        _write(log, b"x" * 200)
        now = datetime(2026, 5, 10, 22, 0, tzinfo=timezone.utc)
        rc, events = _capture(
            rsl.rotate, log,
            max_bytes=100, max_age_seconds=86_400, gzip_rotated=False, now=now,
        )
        terminal = events[-1]
        assert "shadow.2026-05-10.jsonl" in terminal["target"]

    def test_collision_with_existing_rotated_name(self, tmp_path: Path):
        log = tmp_path / "shadow.jsonl"
        _write(log, b"x" * 200)
        # Seed an existing rotated file from earlier today.
        existing = tmp_path / "shadow.2026-05-10.jsonl"
        _write(existing, b"older")
        now = datetime(2026, 5, 10, 22, 0, tzinfo=timezone.utc)
        rc, events = _capture(
            rsl.rotate, log,
            max_bytes=100, max_age_seconds=86_400, gzip_rotated=False, now=now,
        )
        terminal = events[-1]
        # Numeric suffix avoids overwrite.
        assert terminal["target"].endswith("shadow.2026-05-10.1.jsonl")
        # The pre-existing file is untouched.
        assert existing.read_bytes() == b"older"


class TestAgeRotation:
    def test_old_log_rotates(self, tmp_path: Path):
        log = tmp_path / "shadow.jsonl"
        _write(log, b"abc")
        # Backdate the mtime to 10 days ago.
        ten_days_ago = time.time() - 10 * 86_400
        os.utime(log, (ten_days_ago, ten_days_ago))
        rc, events = _capture(
            rsl.rotate, log,
            max_bytes=10_000_000, max_age_seconds=86_400, gzip_rotated=False,
        )
        assert rc == 0
        terminal = events[-1]
        assert terminal["status"] == "rotated"
        assert terminal["reason"].startswith("age>=")


class TestGzipMode:
    def test_gzip_rotation_produces_gz_file(self, tmp_path: Path):
        log = tmp_path / "shadow.jsonl"
        _write(log, b'{"ok": 1}\n' * 50)  # large enough to rotate
        rc, events = _capture(
            rsl.rotate, log,
            max_bytes=10, max_age_seconds=86_400, gzip_rotated=True,
        )
        assert rc == 0
        terminal = events[-1]
        assert terminal["status"] == "rotated"
        assert terminal["gzipped"] is True
        gz_path = Path(terminal["target"])
        assert gz_path.suffix == ".gz"
        assert gz_path.is_file()
        # The plain rotated file was deleted post-gzip.
        plain_target = gz_path.with_suffix("")
        assert not plain_target.exists()
        # And the gz roundtrips.
        with gzip.open(gz_path, "rb") as fh:
            content = fh.read()
        assert content.startswith(b'{"ok": 1}\n')


# ---------------------------------------------------------------------------
# main(argv)
# ---------------------------------------------------------------------------


class TestMainEntrypoint:
    def test_defaults_work_with_explicit_log(self, tmp_path: Path):
        log = tmp_path / "shadow.jsonl"
        # Don't seed — main should report noop.
        rc, events = _capture(rsl.main, ["--log", str(log)])
        assert rc == 0
        assert events[-1]["status"] == "noop"

    def test_max_bytes_flag(self, tmp_path: Path):
        log = tmp_path / "shadow.jsonl"
        _write(log, b"x" * 100)
        rc, events = _capture(
            rsl.main,
            ["--log", str(log), "--max-bytes", "50"],
        )
        assert rc == 0
        assert events[-1]["status"] == "rotated"

    def test_max_age_days_flag(self, tmp_path: Path):
        log = tmp_path / "shadow.jsonl"
        _write(log, b"abc")
        five_days = time.time() - 5 * 86_400
        os.utime(log, (five_days, five_days))
        # max-age-days=3 → should rotate
        rc, events = _capture(
            rsl.main,
            ["--log", str(log), "--max-age-days", "3"],
        )
        assert rc == 0
        assert events[-1]["status"] == "rotated"

    def test_gzip_flag(self, tmp_path: Path):
        log = tmp_path / "shadow.jsonl"
        _write(log, b"x" * 100)
        rc, events = _capture(
            rsl.main,
            ["--log", str(log), "--max-bytes", "50", "--gzip"],
        )
        assert rc == 0
        assert events[-1]["gzipped"] is True
