"""Tests for scripts/reports/backlog_counts.py — the /system-review roll-up.

BL-20260816-BACKLOG-COUNTER-COUNTS-KEPT-OPEN-AS-RESOLVED.

This counter is the review-coverage gate's own input: `/system-review` copies
its output straight into `consolidated.backlog_summary`. It allowlisted OPEN
statuses and treated everything unrecognised as RESOLVED, which is the exact
inverse of the policy its own docstring declares. Measured at the moment of
the fix, 96 genuinely-open items across the three live backlogs were reported
as resolved — including every `kept_open`, the status the drain protocol
MANDATES for a legitimately-deferred item.

The property under test is the POLARITY: unknown must fail toward open.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

_MOD = Path(__file__).resolve().parents[1] / "scripts" / "reports" / "backlog_counts.py"
_spec = importlib.util.spec_from_file_location("backlog_counts", _MOD)
bc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bc)


# --- the polarity itself ------------------------------------------------------

@pytest.mark.parametrize("status", [
    "kept_open",                 # THE regression: the drain protocol's own status
    "open",
    "in_progress",
    "partially-resolved",
    "",
    None,
    "snoozed",
    "some_status_nobody_has_written_yet",   # unknown -> open, by policy
    "mitigated",                            # a workaround; root cause outstanding
    "fix_landed_monitoring",
    "resolved_pending_live_verification",
    "likely_already_fixed_unverified",
    "root_cause_fixed_backlog_remains",
    "open — RECURRED 2026-08-14, measured across 3 consecutive pushes",
    "open (criterion (2) MEASURED and closed; the default flip stays queued)",
    "resolved (guard); follow-up OPEN, see resolution_criteria (3)",
    "resolved (spread MEASURED; the two cells it surfaced are queued for the operator)",
])
def test_these_are_open(status):
    assert bc.is_open_status(status) is True, f"{status!r} must count as OPEN"


@pytest.mark.parametrize("status", [
    "resolved",
    "RESOLVED",
    "  resolved  ",
    "superseded",
    "invalid",
    "wont_fix",
    "fixed",
    "measured_no_action",
    "measured_hypothesis_falsified",
    "resolved_refuted",
    "resolved_verified_live",
])
def test_these_are_closed(status):
    assert bc.is_open_status(status) is False, f"{status!r} must count as CLOSED"


def test_unknown_status_fails_toward_open_not_closed():
    """The declared policy, stated as its own test.

    Failing toward CLOSED is how 96 items went missing from the roll-up: a
    status nobody had taught the counter about silently became 'resolved'.
    """
    assert bc.is_open_status("brand_new_status_v2") is True


# --- counting over a file -----------------------------------------------------

def _write(tmp_path: Path, items: list[dict]) -> Path:
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"items": items}))
    return p


def test_kept_open_is_counted_open_end_to_end(tmp_path):
    """The end-to-end shape of the regression, at the counter's own API."""
    path = _write(tmp_path, [
        {"id": "a", "status": "kept_open"},
        {"id": "b", "status": "kept_open"},
        {"id": "c", "status": "resolved", "resolved_at": "2026-08-15T00:00:00+00:00"},
    ])
    now = dt.datetime(2026, 8, 16, tzinfo=dt.timezone.utc)
    out = bc.count_one(path, dt.datetime(2026, 8, 14, tzinfo=dt.timezone.utc), now)
    assert out["total"] == 3
    assert out["open"] == 2          # pre-fix this was 0
    assert out["resolved"] == 1      # pre-fix this was 3
    assert out["drained"] == 1


def test_open_plus_resolved_always_equals_total(tmp_path):
    path = _write(tmp_path, [
        {"id": str(i), "status": s} for i, s in enumerate(
            ["kept_open", "resolved", "open", "superseded", "weird", "", "mitigated"]
        )
    ])
    now = dt.datetime(2026, 8, 16, tzinfo=dt.timezone.utc)
    out = bc.count_one(path, None, now)
    assert out["open"] + out["resolved"] == out["total"] == 7
    assert out["open"] == 5   # kept_open, open, weird, "", mitigated


def test_drained_counts_only_within_the_window(tmp_path):
    path = _write(tmp_path, [
        {"id": "in", "status": "resolved", "resolved_at": "2026-08-15T12:00:00+00:00"},
        {"id": "before", "status": "resolved", "resolved_at": "2026-08-01T12:00:00+00:00"},
        {"id": "none", "status": "resolved"},
    ])
    now = dt.datetime(2026, 8, 16, tzinfo=dt.timezone.utc)
    out = bc.count_one(path, dt.datetime(2026, 8, 14, tzinfo=dt.timezone.utc), now)
    assert out["drained"] == 1
    assert out["resolved"] == 3


def test_a_still_open_item_is_never_drained(tmp_path):
    """An item can carry a stale resolved_at and be re-opened; open wins."""
    path = _write(tmp_path, [
        {"id": "x", "status": "kept_open", "resolved_at": "2026-08-15T12:00:00+00:00"},
    ])
    now = dt.datetime(2026, 8, 16, tzinfo=dt.timezone.utc)
    out = bc.count_one(path, dt.datetime(2026, 8, 14, tzinfo=dt.timezone.utc), now)
    assert out["open"] == 1
    assert out["drained"] == 0


def test_missing_file_is_absent_not_empty(tmp_path):
    out = bc.count_one(tmp_path / "nope.json", None, dt.datetime.now(dt.timezone.utc))
    assert out["present"] is False
    assert out["total"] == 0
