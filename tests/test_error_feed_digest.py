"""Tests for the error-feed digest — the wiring that hands the trader's error
feed to the `duty` pass.

The properties asserted here are the ones a reader's conclusion depends on: a
feed nobody could read must never render as a quiet one, a digit-varying repeat
of one condition must collapse to one row, and the watermark must advance
without replaying and without marking an unread window as covered.

They duplicate the module's own `--self-test` deliberately. The self-test is
what CI runs on a runner with no pytest collection; this file is what a
developer's suite runs, and the two failing independently is the point.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "error_feed_digest", _ROOT / "scripts" / "ops" / "error_feed_digest.py")
efd = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
# Registered BEFORE exec: `@dataclass` resolves annotations through
# `sys.modules[cls.__module__]`, so a module loaded by spec alone raises
# `AttributeError: 'NoneType' object has no attribute '__dict__'` at import.
sys.modules[_SPEC.name] = efd
_SPEC.loader.exec_module(efd)

_NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


_T0 = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)


def _rows(n: int, *, level: str = "warn", tmpl: str = "leg {i}: no candle data in {ms}ms"):
    """`n` rows of ONE condition, one hour apart, each varying in its digits.

    Stamps are built by timedelta rather than formatted hour-of-day: an
    f-string `{h:02d}` past 23 yields `T24:00:00`, which the module correctly
    drops as undateable — so a >24-row flood silently shrank and the ordering
    test compared against a count that never existed.
    """
    return [{"timestamp": (_T0 + timedelta(hours=i)).isoformat(), "level": level,
             "message": tmpl.format(i=i, ms=i * 7)}
            for i in range(1, n + 1)]


def _feed(name: str, rows: list, state: str = "read") -> "efd.FeedRead":
    return efd.FeedRead(name, state, rows, population=efd._population(rows, 1000))


# ── the three read states ──────────────────────────────────────────────────

def test_the_three_read_states_are_distinguishable():
    assert set(efd.FEED_STATES) == {"read", "unreachable", "absent"}
    with pytest.raises(ValueError):
        efd.FeedRead("x", "quiet")


def test_an_unreachable_feed_does_not_render_as_empty():
    """The `curl … || echo '{}'` class: a failed fetch reading as a clean feed."""
    unreachable = efd.FeedRead("bot_logs", "unreachable", note="ConnectionResetError")
    absent = efd.FeedRead("operator_alerts", "absent",
                          population=efd._population([], 10))

    env = efd.build([unreachable, absent], now=_NOW, since=None, since_note="")

    assert env["verdict"] == "partial"
    assert env["unreachable_feeds"] == ["bot_logs"]
    md = efd.render_markdown(env)
    assert "LOWER BOUND" in md
    assert "bot_logs" in md
    # ... while a feed we DID read and found empty is not a degraded verdict.
    assert efd.verdict_for([absent]) == "all_feeds_read"
    assert efd.verdict_for([unreachable]) == "no_feeds_read"


def test_missing_bearer_is_unreachable_not_absent():
    """No token cannot distinguish an empty ring from a closed door."""
    got = efd.fetch_operator_alerts("https://example.invalid", None)
    assert got.state == "unreachable"
    assert "could not look" in got.note


# ── grouping ───────────────────────────────────────────────────────────────

def test_grouping_collapses_a_digit_varying_repeat():
    feed = _feed("bot_logs", _rows(8))
    groups, coverage = efd.group_rows(feed, None)
    assert len(groups) == 1
    assert groups[0]["count"] == 8
    assert coverage["rows_considered"] == 8


def test_grouping_does_not_collapse_two_different_conditions():
    rows = _rows(4) + [{"timestamp": "2026-09-02T09:00:00+00:00", "level": "error",
                        "message": "bybit_place_order_failed: order_qty:22 > max_qty:11"}]
    groups, _ = efd.group_rows(_feed("bot_logs", rows), None)
    assert len(groups) == 2


def test_a_long_decimal_is_not_eaten_as_a_hex_blob():
    """Regression: the AVAX cause key read `order_qty:<hex> > max_qty:<hex>`,
    hiding that the comparison was a SIZE CAP — the whole finding."""
    cause = efd.normalise_cause(
        "too large, order_qty:2299510000000 > max_qty:2200000000000")
    assert "<hex>" not in cause
    assert "order_qty:N > max_qty:N" in cause
    # A genuine hex blob still collapses, or two occurrences of one condition
    # key differently and the flood is not summarised.
    assert "<hex>" in efd.normalise_cause("Package: pkg-6a8e3fb325464be3")


def test_a_small_error_group_outranks_a_large_warn_flood():
    """The property that surfaces a venue rejection under a no-candle flood.

    Ordering is on the level the PRODUCER stamped, not a severity this module
    assigns — so it is a fact, not a triage decision.
    """
    rows = _rows(40) + [{"timestamp": "2026-09-02T09:00:00+00:00", "level": "error",
                         "message": "bybit_place_order_failed: qty too large"}]
    groups, _ = efd.group_rows(_feed("bot_logs", rows), None)
    ordered = efd.order_groups(groups)
    assert ordered[0]["level"] == "error" and ordered[0]["count"] == 1
    assert ordered[1]["level"] == "warn" and ordered[1]["count"] == 40


def test_an_ungradeable_level_sorts_with_the_errors():
    """`unknown` is 'we could not grade it', which is not 'it is minor'."""
    assert efd._LEVEL_RANK["unknown"] == efd._LEVEL_RANK["error"]
    assert efd._row_level({"level": "banana"}, "bot_logs") == "unknown"
    assert efd._row_level({"priority": "high"}, "operator_alerts") == "error"


def test_an_undateable_row_is_dropped_loudly():
    feed = _feed("bot_logs", [{"level": "error", "message": "no stamp"}])
    groups, coverage = efd.group_rows(feed, None)
    assert groups == []
    assert coverage["undateable_dropped"] == 1


# ── facets ─────────────────────────────────────────────────────────────────

def test_an_event_name_is_not_extracted_as_an_account():
    """A false facet reads as attribution and sends triage somewhere unreal."""
    roster = {"bybit_1", "ib_paper"}
    got = efd.extract_facets(
        "api_call bybit_place_order_failed: ib_target_naked detected", roster, "read")
    assert got["accounts"] == []


def test_an_unreadable_roster_is_stated_not_silently_emptied():
    got = efd.extract_facets("Account: bybit_1", set(), "unreadable: boom")
    assert got["accounts"] == []
    assert got["accounts_state"].startswith("unreadable")


def test_the_json_body_symbol_form_is_extracted():
    """The AVAX rejection carries its symbol ONLY in the request body it echoes."""
    got = efd.extract_facets(
        'POST /v5/order/create: {"symbol": "AVAXUSDT", "side": "Buy"}',
        {"bybit_1"}, "read")
    assert got["symbols"] == ["AVAXUSDT"]


def test_the_roster_is_projected_over_the_canonical_source():
    ids, state = efd.account_roster(_ROOT)
    assert state == "read", state
    assert {"bybit_1", "bybit_2", "ib_paper", "alpaca_live"} <= ids
    assert not any(i.endswith(("_failed", "_naked", "_cover")) for i in ids)


# ── watermark ──────────────────────────────────────────────────────────────

def test_the_watermark_advances_and_does_not_replay():
    feed = _feed("bot_logs", _rows(5))
    mark, _ = efd.next_watermark([feed], None)
    assert mark == _T0 + timedelta(hours=5)
    _, coverage = efd.group_rows(feed, mark)
    assert coverage["rows_after_watermark"] == 0


def test_the_watermark_is_held_when_nothing_was_read():
    """Advancing on a failed fetch marks an unread window as covered — the one
    way this design can lose a signal permanently."""
    prior = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    unreachable = efd.FeedRead("bot_logs", "unreachable")
    held, note = efd.next_watermark([unreachable], prior)
    assert held == prior
    assert "HELD" in note


def test_the_watermark_survives_a_reprovision_because_it_is_committed(tmp_path):
    """It is a field on the committed digest, not a `runtime_logs/` sidecar —
    that path is gitignored and VM-local, so a fresh box would replay the feed.
    """
    assert "runtime_logs" not in str(efd.OUT)
    root = tmp_path
    (root / "docs" / "claude").mkdir(parents=True)
    mark, note = efd.read_watermark(root)
    assert mark is None and "no prior digest" in note

    env = efd.build([_feed("bot_logs", _rows(3))], now=_NOW, since=None, since_note="")
    (root / efd.OUT).write_text(json.dumps(env), encoding="utf-8")
    back, note2 = efd.read_watermark(root)
    assert back == _T0 + timedelta(hours=3), note2


def test_an_unreadable_prior_digest_covers_the_full_page(tmp_path):
    """We could not read the mark, so re-cover rather than skip a window."""
    (tmp_path / "docs" / "claude").mkdir(parents=True)
    (tmp_path / efd.OUT).write_text("{not json", encoding="utf-8")
    mark, note = efd.read_watermark(tmp_path)
    assert mark is None
    assert "unreadable" in note


# ── population ─────────────────────────────────────────────────────────────

def test_a_page_at_the_cap_is_stamped_truncated():
    """operator_alerts holds 300-600 rows — its window is not a constant, so a
    short page must never read as the whole feed."""
    at_cap = efd._population([{"ts": "2026-09-02T00:00:00+00:00"}], 1)
    under = efd._population([{"ts": "2026-09-02T00:00:00+00:00"}], 5)
    assert at_cap["truncated"] is True
    assert under["truncated"] is False
    env = efd.build([_feed("bot_logs", _rows(3))], now=_NOW, since=None, since_note="")
    assert env["feeds"]["bot_logs"]["population"]["oldest_ts"] is not None


def test_the_check_gate_refuses_a_partial_digest_that_names_no_feed(tmp_path):
    (tmp_path / "docs" / "claude").mkdir(parents=True)
    (tmp_path / efd.OUT).write_text(json.dumps({
        "verdict": "partial", "unreachable_feeds": [],
        "generated_at": "2026-09-02T00:00:00+00:00"}), encoding="utf-8")
    assert efd._check(tmp_path) == 1


def test_the_module_self_test_passes():
    assert efd._self_test() == 0
