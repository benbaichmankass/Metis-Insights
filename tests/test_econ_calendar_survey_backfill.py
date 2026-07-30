"""The SURVEY-side calendar backfill.

M3 compares the PIT expectation model against real survey consensus on their overlap. The
model side has 75 years (6,966 rows); the survey side had **11 joinable rows** — not because
anything was capped, but because the forward producer had only ever pulled ONE window.
`fetch_calendar(frm, to)` takes an arbitrary range and is keyless, so the survey window was a
scheduling artifact, not a data limit. This script closes that.

The load-bearing property is `TestNeverReadsAsAModelRow`: these rows carry a REAL survey
consensus, so if they were classified as model rows they would be dropped from the survey side
and M3 would quietly compare the model against itself.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "macro"))

sb = pytest.importorskip("econ_calendar_survey_backfill")


class TestDateChunks:
    def test_covers_the_range_inclusively(self):
        c = sb.date_chunks("2026-01-01", "2026-06-30", 90)
        assert c[0][0] == "2026-01-01" and c[-1][1] == "2026-06-30"

    def test_chunks_are_contiguous_and_non_overlapping(self):
        """A gap loses releases; an overlap double-counts them."""
        c = sb.date_chunks("2020-01-01", "2021-06-30", 90)
        for a, b in zip(c, c[1:]):
            assert dt.date.fromisoformat(b[0]) == dt.date.fromisoformat(a[1]) + dt.timedelta(days=1)

    def test_a_single_day_range_is_one_chunk(self):
        assert sb.date_chunks("2026-01-01", "2026-01-01", 90) == [("2026-01-01", "2026-01-01")]

    def test_reversed_range_raises(self):
        with pytest.raises(ValueError):
            sb.date_chunks("2026-06-01", "2026-01-01")

    def test_a_decade_chunks_without_blowing_up(self):
        c = sb.date_chunks("2015-01-01", "2026-07-30", 90)
        assert 40 < len(c) < 60


class TestUsableConsensusFilter:
    @staticmethod
    def _row(cons, act):
        return {"expected": {"consensus": cons},
                "realized_outcome": {"consensus": cons, "actual": act}}

    def test_both_present_is_usable(self):
        assert sb._has_usable_consensus(self._row(10.0, 12.0))

    def test_no_consensus_is_dropped(self):
        """No survey number => no survey surprise. Dropped, not emitted as a null a
        consumer might read as zero."""
        assert not sb._has_usable_consensus(self._row(None, 12.0))

    def test_an_upcoming_event_is_dropped(self):
        assert not sb._has_usable_consensus(self._row(10.0, None))

    def test_a_bool_is_not_a_number(self):
        assert not sb._has_usable_consensus(self._row(True, 12.0))


class TestNeverReadsAsAModelRow:
    """THE load-bearing property."""

    def test_expectation_source_is_survey_not_model(self):
        r = sb.stamp({"kind": "k"}, observed_at="2026-07-30T00:00:00Z")
        assert r["expectation_source"] == "survey:fxstreet"
        assert not r["expectation_source"].startswith("model:")

    def test_the_validator_classifies_it_as_survey_side(self):
        """End-to-end against the real discriminator, not a restatement of it."""
        sys.path.insert(0, os.path.join(REPO, "scripts", "macro"))
        v = pytest.importorskip("econ_expectation_validate")
        r = sb.stamp({"kind": "k", "scheduled_for": "2026-07-16",
                      "expected": {"consensus": 10.0},
                      "realized_outcome": {"consensus": 10.0, "actual": 12.0}},
                     observed_at="2026-07-30T00:00:00Z")
        assert r["backfilled"] is True
        assert not v.is_model_row(r), "a backfilled SURVEY row must not read as a model row"


class TestProvenanceHonesty:
    def test_pit_basis_admits_the_actual_may_be_revised(self):
        r = sb.stamp({}, observed_at="2026-07-30T00:00:00Z")
        assert r["pit_basis"] == "fxstreet_current_state"

    def test_consensus_basis_records_that_the_survey_is_pre_release(self):
        """The two halves have DIFFERENT PIT standing: consensus is genuinely pre-release,
        the actual may be a revision. Both are stamped so neither is over-trusted."""
        assert sb.stamp({}, observed_at="x")["consensus_basis"] == "pre_release_survey"

    def test_observed_at_is_the_fetch_time_not_backdated(self):
        r = sb.stamp({}, observed_at="2026-07-30T12:00:00Z")
        assert r["observed_at"] == "2026-07-30T12:00:00Z"

    def test_backfilled_flag_is_set(self):
        assert sb.stamp({}, observed_at="x")["backfilled"] is True


class TestDedupe:
    def test_overlapping_chunks_do_not_double_count(self):
        rows = [{"kind": "a", "scheduled_for": "2026-01-01", "event_name": "X"},
                {"kind": "a", "scheduled_for": "2026-01-01", "event_name": "X"}]
        assert len(sb.dedupe(rows)) == 1

    def test_distinct_events_on_one_day_are_kept(self):
        rows = [{"kind": "a", "scheduled_for": "2026-01-01", "event_name": "X"},
                {"kind": "b", "scheduled_for": "2026-01-01", "event_name": "Y"}]
        assert len(sb.dedupe(rows)) == 2

    def test_output_is_date_ordered(self):
        rows = [{"kind": "a", "scheduled_for": "2026-03-01", "event_name": "X"},
                {"kind": "a", "scheduled_for": "2026-01-01", "event_name": "X"}]
        assert [r["scheduled_for"] for r in sb.dedupe(rows)] == ["2026-01-01", "2026-03-01"]


def _fake_opener(payload):
    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=None):
        return _R(json.dumps(payload).encode())
    return fake


class TestEndToEnd:
    PAYLOAD = [{
        "countryCode": "US", "name": "Initial Jobless Claims",
        "dateUtc": "2026-07-16T12:30:00Z", "volatility": "MEDIUM",
        "actual": 220.0, "consensus": 215.0, "previous": 218.0,
    }, {
        "countryCode": "US", "name": "Initial Jobless Claims",
        "dateUtc": "2026-07-23T12:30:00Z", "volatility": "MEDIUM",
        "actual": None, "consensus": 221.0, "previous": 220.0,   # upcoming -> dropped
    }]

    def test_fetch_window_keeps_only_released_rows_with_consensus(self):
        rows, raw_n = sb.fetch_window("2026-07-01", "2026-07-31",
                                      urlopen=_fake_opener(self.PAYLOAD),
                                      observed_at="2026-07-30T00:00:00Z")
        assert raw_n == 2, "raw count reports what the source returned"
        assert len(rows) == 1, "the upcoming row has no actual, so no survey surprise"
        assert rows[0]["expectation_source"] == "survey:fxstreet"

    def test_an_empty_fetch_yields_no_rows_and_reports_zero_raw(self):
        rows, raw_n = sb.fetch_window("2026-07-01", "2026-07-31",
                                      urlopen=_fake_opener([]),
                                      observed_at="x")
        assert rows == [] and raw_n == 0

    def test_main_FAILS_when_nothing_was_fetched(self, monkeypatch, tmp_path):
        """A run that reconstructed no consensus must not write an empty file that reads as
        'no data available' — the same contract as the model-side backfill."""
        monkeypatch.setattr(sb, "fetch_window", lambda *a, **k: ([], 0))
        rc = sb.main(["--start", "2026-01-01", "--end", "2026-03-01",
                      "--out", str(tmp_path / "o.jsonl"), "--dry-run"])
        assert rc == 2
        assert not (tmp_path / "o.jsonl").exists()

    def test_main_writes_stamped_rows_on_success(self, monkeypatch, tmp_path):
        row = sb.stamp({"kind": "initial_jobless_claims", "scheduled_for": "2026-07-16",
                        "event_name": "Initial Jobless Claims",
                        "expected": {"consensus": 215.0},
                        "realized_outcome": {"consensus": 215.0, "actual": 220.0}},
                       observed_at="2026-07-30T00:00:00Z")
        monkeypatch.setattr(sb, "fetch_window", lambda *a, **k: ([row], 1))
        out = tmp_path / "o.jsonl"
        assert sb.main(["--start", "2026-07-01", "--end", "2026-07-31",
                        "--out", str(out)]) == 0
        written = [json.loads(x) for x in out.read_text().splitlines() if x.strip()]
        assert written and written[0]["expectation_source"] == "survey:fxstreet"
