"""ROADMAP_MACRO M1 — PIT release backfill (`econ_calendar_snapshot_backfill.py`).

The backfill sibling that converts the M1 event study from n=7 (forward-only
accrual, "no verdict until ~mid-September") to n in the hundreds/thousands over
FRED history — the 2026-07-30 "backfill before you wait" correction.

The load-bearing suites here are:
* `TestSchemaContract` — the rows must be parseable by `econ_event_study`'s own
  loader. Without this the two scripts can drift into a silent mismatch where the
  backfill "succeeds" and the study reads zero releases.
* `TestProvenance` — a MODEL expectation must never be presentable as an archived
  survey consensus.
"""
from __future__ import annotations

import math
import os
import sys
from datetime import date, timedelta

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "macro"))
sys.path.insert(0, REPO)

backfill = pytest.importorskip("econ_calendar_snapshot_backfill")
study = pytest.importorskip("econ_event_study")

SPEC = {"fred_series": "WNGSTUS", "cadence": "weekly", "symbol": "NG=F"}


def _weekly_history(n=200, level=2000.0, amp=800.0):
    d0 = date(2021, 1, 1)
    return [
        ((d0 + timedelta(weeks=t)).isoformat(),
         level + amp * math.sin(2.0 * math.pi * (t % 52) / 52))
        for t in range(n)
    ]


class TestRowGeneration:
    def test_emits_rows_past_the_warmup_head(self):
        rows = backfill.rows_for_kind("eia_natgas_storage", SPEC, _weekly_history())
        assert rows, "no rows emitted from 200 weekly observations"
        assert len(rows) < 200, "warm-up head must be excluded, not fabricated"

    def test_thin_history_emits_nothing_rather_than_guessing(self):
        assert backfill.rows_for_kind("eia_natgas_storage", SPEC, _weekly_history(n=10)) == []

    def test_surprise_is_near_zero_on_a_perfectly_anticipated_series(self):
        """A noiseless seasonal series has no unanticipated component."""
        rows = backfill.rows_for_kind("eia_natgas_storage", SPEC, _weekly_history())
        surprises = [abs(r["realized_outcome"]["surprise"]) for r in rows[-20:]]
        assert max(surprises) < 1.0, max(surprises)

    def test_actual_matches_the_source_observation(self):
        hist = _weekly_history()
        rows = backfill.rows_for_kind("eia_natgas_storage", SPEC, hist)
        by_date = {d: v for d, v in hist}
        for r in rows[:25]:
            assert r["realized_outcome"]["actual"] == by_date[r["scheduled_for"]]

    def test_surprise_equals_actual_minus_consensus(self):
        rows = backfill.rows_for_kind("eia_natgas_storage", SPEC, _weekly_history())
        for r in rows[:25]:
            ro = r["realized_outcome"]
            assert ro["surprise"] == pytest.approx(ro["actual"] - ro["consensus"])

    def test_monthly_cadence_uses_a_monthly_period(self):
        rows = backfill.rows_for_kind(
            "cpi_yoy", {"fred_series": "CPIAUCSL", "cadence": "monthly", "symbol": "ES=F"},
            _weekly_history(n=120),
        )
        assert rows and rows[0]["expectation_period"] == 12


class TestProvenance:
    """A model expectation must never pass as an archived survey poll."""

    def test_rows_declare_backfilled_and_the_expectation_spec(self):
        r = backfill.rows_for_kind("eia_natgas_storage", SPEC, _weekly_history())[-1]
        assert r["backfilled"] is True
        assert r["expectation_source"].startswith("model:")
        from econ_expectation import SPEC_VERSION
        assert SPEC_VERSION in r["expectation_source"]

    def test_rows_declare_the_vintage_basis(self):
        """Keyless FRED serves the CURRENT vintage — never present it as a first print."""
        r = backfill.rows_for_kind("eia_natgas_storage", SPEC, _weekly_history())[-1]
        assert r["pit_basis"] == backfill.PIT_BASIS_FRED_CURRENT

    def test_source_names_the_fred_series(self):
        r = backfill.rows_for_kind("eia_natgas_storage", SPEC, _weekly_history())[-1]
        assert r["source"] == "fred:WNGSTUS"


class TestSchemaContract:
    """The study's own loader must parse these rows — else the two drift silently."""

    def test_study_loader_reads_the_backfilled_rows(self, tmp_path):
        import json

        rows = backfill.rows_for_kind("eia_natgas_storage", SPEC, _weekly_history())
        p = tmp_path / "snap.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

        loaded = study.load_resolved_events(str(p), "eia_natgas_storage")
        assert len(loaded) == len(rows), (len(loaded), len(rows))
        assert all(e["surprise"] is not None for e in loaded)
        assert all(e["actual"] is not None for e in loaded)
        # ascending by date, as the study's forward-return walk requires
        assert [e["date"] for e in loaded] == sorted(e["date"] for e in loaded)

    def test_status_and_kind_are_what_the_loader_filters_on(self):
        r = backfill.rows_for_kind("eia_natgas_storage", SPEC, _weekly_history())[0]
        assert r["status"] == "resolved"
        assert r["kind"] == "eia_natgas_storage"
        assert len(r["scheduled_for"]) == 10

    def test_loader_finds_nothing_for_an_unrelated_kind(self, tmp_path):
        import json

        rows = backfill.rows_for_kind("eia_natgas_storage", SPEC, _weekly_history())
        p = tmp_path / "s.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        assert study.load_resolved_events(str(p), "eia_crude_stocks") == []


class TestConfig:
    def test_shipped_config_declares_every_required_field(self):
        series = backfill.load_series_config(
            os.path.join(REPO, "config", "macro_econ_series.yaml"))
        assert series
        for kind, spec in series.items():
            assert spec.get("fred_series"), f"{kind}: missing fred_series"
            assert spec.get("cadence") in {"weekly", "monthly", "quarterly"}, kind
            assert spec.get("symbol"), f"{kind}: missing symbol"

    def test_canonical_m1_test_case_is_present(self):
        series = backfill.load_series_config(
            os.path.join(REPO, "config", "macro_econ_series.yaml"))
        assert "eia_natgas_storage" in series, "the canonical M1 case must be configured"
