"""Workplan §0.WS-5 — tests for the macro-producer cron liveness monitor.

No network, no repo install: the script under test is stdlib-only and loaded via
importlib (same pattern as test_m28_macro_sources.py). Exercises the freshness
math, the missing/unreadable/no-timestamp verdicts, per-ledger thresholds, and
the exit-code contract.
"""

from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime, timezone

_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro"
)


def _load(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(_DIR, name + ".py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


liveness = _load("check_producer_liveness")

_NOW = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)


def _write_ledger(tmp_path, name, stamps):
    """Write a JSONL ledger with one row per observed_at stamp."""
    p = tmp_path / name
    lines = []
    for i, ts in enumerate(stamps):
        row = {"symbol": f"S{i}", "metric": "m", "value": 1.0}
        if ts is not None:
            row["observed_at"] = ts
        lines.append(json.dumps(row))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _parse_iso_utc
# ---------------------------------------------------------------------------


def test_parse_iso_accepts_z_offset_and_naive():
    z = liveness._parse_iso_utc("2026-07-26T09:28:15Z")
    off = liveness._parse_iso_utc("2026-07-26T09:28:15+00:00")
    naive = liveness._parse_iso_utc("2026-07-26T09:28:15")
    assert z == off == naive
    assert z.tzinfo is not None


def test_parse_iso_rejects_garbage():
    assert liveness._parse_iso_utc("") is None
    assert liveness._parse_iso_utc("not-a-date") is None
    assert liveness._parse_iso_utc(None) is None


# ---------------------------------------------------------------------------
# newest_observed_at — max across rows, unsorted-safe
# ---------------------------------------------------------------------------


def test_newest_scans_for_max_not_last_line(tmp_path):
    # Out-of-order rows: the freshest is in the MIDDLE, not the last line.
    p = _write_ledger(
        tmp_path,
        "l.jsonl",
        [
            "2026-07-20T00:00:00Z",
            "2026-07-26T09:28:15Z",  # newest
            "2026-07-22T00:00:00Z",
        ],
    )
    newest, rows = liveness.newest_observed_at(p)
    assert rows == 3
    assert newest == datetime(2026, 7, 26, 9, 28, 15, tzinfo=timezone.utc)


def test_newest_ignores_unparseable_rows_but_counts_them(tmp_path):
    p = tmp_path / "l.jsonl"
    p.write_text(
        "not json\n"
        + json.dumps({"observed_at": "2026-07-25T00:00:00Z"})
        + "\n"
        + json.dumps({"no_stamp": True})
        + "\n",
        encoding="utf-8",
    )
    newest, rows = liveness.newest_observed_at(p)
    assert rows == 3  # all non-blank lines counted
    assert newest == datetime(2026, 7, 25, 0, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# check_ledger — the verdict states
# ---------------------------------------------------------------------------


def test_fresh_within_threshold(tmp_path):
    p = _write_ledger(tmp_path, "l.jsonl", ["2026-07-27T00:00:00Z"])  # 12h old
    r = liveness.check_ledger(p, 48.0, now=_NOW, allow_missing=False)
    assert r["status"] == "fresh"
    assert r["age_hours"] == 12.0
    assert not liveness._is_bad(r["status"])


def test_stale_beyond_threshold(tmp_path):
    p = _write_ledger(tmp_path, "l.jsonl", ["2026-07-24T00:00:00Z"])  # 84h old
    r = liveness.check_ledger(p, 48.0, now=_NOW, allow_missing=False)
    assert r["status"] == "stale"
    assert r["age_hours"] == 84.0
    assert liveness._is_bad(r["status"])


def test_missing_is_failure_by_default(tmp_path):
    r = liveness.check_ledger(
        tmp_path / "nope.jsonl", 48.0, now=_NOW, allow_missing=False
    )
    assert r["status"] == "missing"
    assert liveness._is_bad(r["status"])


def test_missing_ok_with_allow_missing(tmp_path):
    r = liveness.check_ledger(
        tmp_path / "nope.jsonl", 48.0, now=_NOW, allow_missing=True
    )
    assert r["status"] == "missing_ok"
    assert not liveness._is_bad(r["status"])


def test_no_timestamp_is_failure(tmp_path):
    p = _write_ledger(tmp_path, "l.jsonl", [None, None])  # rows but no observed_at
    r = liveness.check_ledger(p, 48.0, now=_NOW, allow_missing=False)
    assert r["status"] == "no_timestamp"
    assert r["rows"] == 2
    assert liveness._is_bad(r["status"])


def test_boundary_exactly_at_threshold_is_fresh(tmp_path):
    # Exactly max_age_hours old → fresh (strict > for stale).
    p = _write_ledger(tmp_path, "l.jsonl", ["2026-07-25T12:00:00Z"])  # 48h old
    r = liveness.check_ledger(p, 48.0, now=_NOW, allow_missing=False)
    assert r["status"] == "fresh"


# ---------------------------------------------------------------------------
# _parse_ledger_arg
# ---------------------------------------------------------------------------


def test_parse_ledger_arg_with_and_without_threshold():
    assert liveness._parse_ledger_arg("a/b.jsonl:24") == ("a/b.jsonl", 24.0)
    path, hours = liveness._parse_ledger_arg("a/b.jsonl")
    assert path == "a/b.jsonl"
    assert hours == liveness.DEFAULT_MAX_AGE_HOURS
    # Non-numeric suffix → treated as part of the path, default threshold.
    assert liveness._parse_ledger_arg("a/b.jsonl")[1] == liveness.DEFAULT_MAX_AGE_HOURS


# ---------------------------------------------------------------------------
# main — exit-code contract
# ---------------------------------------------------------------------------


def test_main_exit_zero_when_fresh(tmp_path, capsys):
    p = _write_ledger(tmp_path, "fresh.jsonl", ["2026-07-27T00:00:00Z"])
    code = liveness.main(["--ledger", str(p) + ":100000", "--json"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True


def test_main_exit_one_when_stale(tmp_path):
    p = _write_ledger(tmp_path, "stale.jsonl", ["2020-01-01T00:00:00Z"])
    code = liveness.main(["--ledger", str(p) + ":48"])
    assert code == 1


def test_main_exit_one_when_missing(tmp_path):
    code = liveness.main(["--ledger", str(tmp_path / "nope.jsonl")])
    assert code == 1
