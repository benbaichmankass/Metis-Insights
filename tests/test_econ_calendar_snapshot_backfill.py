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


# ---------------------------------------------------------------------------
# Units + release-date basis (2026-07-30)
#
# Two defects made the first backfill's 6978 rows unusable, and BOTH produced
# plausible-looking numbers rather than an error:
#   BL-20260730-BACKFILL-UNITS-DIFFER-FROM-SURVEY-FEED       (wrong units/quantity)
#   BL-20260730-BACKFILL-DATE-IS-REFERENCE-PERIOD-NOT-RELEASE (wrong join key)
# The date bug also meant the event study joined prices at the reference period
# instead of the release, measuring a window that mostly PRECEDES the release.
# ---------------------------------------------------------------------------


class TestUnitsTransform:
    def test_identity_is_unchanged(self):
        h = [("2026-01-03", 5.0)]
        assert backfill.apply_transform(h, "identity", period=52) == h
        assert backfill.apply_transform(h, None, period=52) == h

    def test_scale_converts_persons_to_thousands(self):
        """FRED serves 187000; the release convention is 187.0."""
        out = backfill.apply_transform([("2026-01-03", 187000.0)], "scale:0.001", period=52)
        assert out == [("2026-01-03", 187.0)]

    def test_yoy_from_level_drops_the_first_period_rather_than_fabricating(self):
        """No prior year => no row. Never a back-filled zero."""
        lvl = [(f"2026-{m:02d}-01", 300.0 + m) for m in range(1, 13)] + [("2027-01-01", 315.0)]
        out = backfill.apply_transform(lvl, "yoy_pct_from_level", period=12)
        assert len(out) == 1
        assert out[0][0] == "2027-01-01"
        assert out[0][1] == pytest.approx(100 * (315.0 / 301.0 - 1))

    def test_yoy_skips_a_zero_denominator(self):
        """A zero prior-year value would be a divide-by-zero; the row is DROPPED, not
        emitted as an infinity or a fabricated 0%."""
        lvl = [("2026-01-01", 0.0), ("2026-02-01", 1.0), ("2027-01-01", 5.0)]
        # period=2 => the 2027 row compares against history[0] == 0.0 => skipped.
        assert backfill.apply_transform(lvl, "yoy_pct_from_level", period=2) == []

    def test_yoy_emits_when_the_denominator_is_non_zero(self):
        """The companion to the skip case, so 'returns []' can't pass for the wrong
        reason (e.g. the transform silently dropping everything)."""
        lvl = [("2026-01-01", 1.0), ("2026-02-01", 2.0), ("2027-01-01", 5.0)]
        out = backfill.apply_transform(lvl, "yoy_pct_from_level", period=2)
        assert out == [("2027-01-01", pytest.approx(400.0))]

    def test_unknown_transform_RAISES_rather_than_passing_raw_units_through(self):
        """The load-bearing property: a silent pass-through would emit plausible
        numbers in the wrong units — the exact bug being fixed."""
        with pytest.raises(ValueError):
            backfill.apply_transform([("2026-01-03", 1.0)], "bogus", period=52)


class TestReleaseDate:
    def test_release_post_dates_the_reference_period(self):
        assert backfill.release_date_for("2026-01-03", 5) == "2026-01-08"

    def test_claims_lag_lands_on_thursday(self):
        """Week-ending Saturday + 5d = the Thursday DoL publishes."""
        d = date.fromisoformat(backfill.release_date_for("2026-01-03", 5))
        assert d.strftime("%a") == "Thu"

    def test_cpi_reference_month_maps_into_the_following_month(self):
        """June data is published mid-July — the case that made the join empty."""
        assert backfill.release_date_for("2026-06-01", 45) == "2026-07-16"

    def test_zero_lag_is_a_no_op(self):
        assert backfill.release_date_for("2026-06-01", 0) == "2026-06-01"


class TestRowCarriesBothDatesAndProvenance:
    @staticmethod
    def _hist(n=160):
        import math
        s = date(2023, 1, 7)
        return [((s + timedelta(days=7 * i)).isoformat(),
                 200000 + 15000 * math.sin(2 * math.pi * i / 52) + 300 * (i % 5))
                for i in range(n)]

    def _row(self):
        spec = {"cadence": "weekly", "symbol": "ES=F", "fred_series": "ICSA",
                "release_lag_days": 5, "transform": "scale:0.001"}
        rows = backfill.rows_for_kind("initial_jobless_claims", spec, self._hist(),
                                 min_train=60, harmonics=2)
        assert rows, "expected rows from 160 weekly observations"
        return rows[-1]

    def test_reference_period_is_kept_alongside_the_release_date(self):
        r = self._row()
        assert r["reference_period"] < r["scheduled_for"]

    def test_release_date_basis_is_stamped_as_modeled(self):
        """A modeled lag must never read as an observed release timestamp."""
        r = self._row()
        assert r["release_date_basis"] == "modeled_lag"
        assert r["release_lag_days"] == 5

    def test_observed_at_is_the_release_not_the_reference(self):
        r = self._row()
        assert r["observed_at"].startswith(r["scheduled_for"])

    def test_units_are_transformed_and_the_transform_is_recorded(self):
        r = self._row()
        assert r["units_transform"] == "scale:0.001"
        assert 150 < r["realized_outcome"]["actual"] < 260, "should be thousands"

    def test_vintage_basis_still_stamped(self):
        assert self._row()["pit_basis"] == "fred_current_vintage"


class TestShippedConfigIsComplete:
    """Every configured series must declare both fields — an omission would silently
    reintroduce raw units (transform defaults to identity) or a zero lag."""

    @staticmethod
    def _series():
        import yaml
        return yaml.safe_load(open(
            os.path.join(REPO, "config", "macro_econ_series.yaml")))["series"]

    def test_every_kind_declares_a_release_lag(self):
        for k, v in self._series().items():
            assert v.get("release_lag_days") is not None, f"{k} has no release_lag_days"

    def test_every_kind_declares_a_transform(self):
        for k, v in self._series().items():
            assert v.get("transform"), f"{k} has no transform"

    def test_every_declared_transform_is_supported(self):
        for k, v in self._series().items():
            backfill.apply_transform([("2026-01-01", 100.0), ("2026-02-01", 101.0)],
                                v["transform"], period=12)

    def test_cpi_is_declared_as_a_yoy_derivation_not_a_level(self):
        """The kind is named cpi_yoy; it must not emit the CPI index level."""
        assert self._series()["cpi_yoy"]["transform"] == "yoy_pct_from_level"


class TestIdProbe:
    """The runner-side id diagnostic (BL-20260730-EIA-SERIES-IDS-NOT-FRED).

    The sandbox is firewalled from FRED, so the "is this id real?" question can only be
    answered where the network works. The probe exists so that answer comes from a fetch
    rather than from a guess edited into config.
    """

    CSV = b"observation_date,V\n2020-01-01,1.0\n2020-01-08,2.0\n"

    @staticmethod
    def _opener(csv_body):
        import io
        import urllib.error

        class _R(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req, timeout=None):
            url = req if isinstance(req, str) else getattr(req, "full_url", str(req))
            if "GOOD" in url:
                return _R(csv_body)
            if "EMPTY" in url:
                return _R(b"observation_date,V\n")
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

        return fake

    def _probe(self, cfg):
        return {r["kind"]: r
                for r in backfill.probe_series(cfg, urlopen=self._opener(self.CSV))}

    def test_a_resolving_id_reports_span_and_count(self):
        r = self._probe({"k": {"fred_series": "GOOD"}})["k"]
        assert r["resolved"] and r["observations"] == 2
        assert r["first"] == "2020-01-01" and r["last"] == "2020-01-08"

    def test_404_is_reported_as_404_not_as_empty(self):
        """THE load-bearing distinction: a wrong id (404) and a right id serving no data
        (200 + empty) need DIFFERENT config actions, so they must not collapse into one
        message. The shared adapter logs-and-returns-empty, which is why the probe reads
        the HTTP status itself."""
        r = self._probe({"k": {"fred_series": "WNGSTUS"}})["k"]
        assert r["http_status"] == 404
        assert "404" in r["error"]
        assert not r["resolved"]

    def test_200_with_empty_history_is_distinguished_from_404(self):
        r = self._probe({"k": {"fred_series": "EMPTY"}})["k"]
        assert r["http_status"] == 200
        assert "EMPTY" in r["error"]
        assert not r["resolved"]

    def test_a_kind_with_no_series_declared_is_reported(self):
        r = self._probe({"k": {}})["k"]
        assert "no fred_series" in r["error"]

    def test_probe_never_raises_on_a_broken_opener(self):
        def boom(req, timeout=None):
            raise RuntimeError("network on fire")
        rows = backfill.probe_series({"k": {"fred_series": "X"}}, urlopen=boom)
        assert rows[0]["resolved"] is False and rows[0]["error"]

    def test_render_is_readable_and_names_the_failing_id(self):
        rows = backfill.probe_series({"bad": {"fred_series": "WNGSTUS"}},
                                     urlopen=self._opener(self.CSV))
        text = backfill.render_probe(rows)
        assert "WNGSTUS" in text and "404" in text
        assert "CONFIG question" in text, "must point at config, not at a code change"

    def test_probe_is_a_diagnostic_not_a_fallback(self):
        """It must never adopt an alternate id: a try-candidates-until-one-resolves
        fallback would backfill from whatever id happened to work — the same defect class
        one level down.

        Checks the CODE only, with the docstring stripped: a first cut grepped the whole
        source and tripped on the docstring sentence that *explains* the probe does not
        try alternates. A guard that cannot tell an implementation from a comment about
        the implementation is not a guard.

        Behavioural, not just lexical: the probe is handed a config whose single id 404s
        and must report that failure rather than resolving something else.
        """
        import inspect
        src = inspect.getsource(backfill.probe_series)
        doc = backfill.probe_series.__doc__ or ""
        code = src.replace(doc, "")          # strip the docstring body
        for banned in ("candidates", "alternate", "fallback_id"):
            assert banned not in code, f"probe must not implement {banned} adoption"

        rows = backfill.probe_series({"only": {"fred_series": "WNGSTUS"}},
                                     urlopen=self._opener(self.CSV))
        assert len(rows) == 1, "must not invent extra rows for alternate ids"
        assert rows[0]["fred_series"] == "WNGSTUS", "must not swap in a different id"
        assert not rows[0]["resolved"], "must report the failure, not resolve around it"


class TestCandidateIdProbe:
    """`--probe-extra` exists so an id can be VERIFIED BEFORE it is committed.

    Without it, the only way to test a candidate FRED id is to commit it to config and run
    — which is exactly the bug BL-20260730-EIA-SERIES-IDS-NOT-FRED describes (an unverified
    id that looks authoritative). The probe reports; it never adopts.
    """

    class _Resp:
        def __init__(self, body=b"DATE,VALUE\n2020-01-01,1.0\n"):
            self.status = 200
            self._b = body

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    @classmethod
    def _opener(cls, url, timeout=None):
        if "GOOD" in url:
            return cls._Resp()
        err = OSError("not found")
        err.code = 404
        raise err

    def test_a_candidate_is_probed_but_never_adopted_into_the_config(self):
        cfg = {"real_kind": {"fred_series": "GOOD"}}
        rows = backfill.probe_series({**cfg, "candidate:MAYBE": {"fred_series": "GOOD"}},
                              urlopen=self._opener)
        # The candidate resolving must not have mutated the caller's config.
        assert cfg == {"real_kind": {"fred_series": "GOOD"}}
        assert any(r["kind"] == "candidate:MAYBE" and r["resolved"] for r in rows)

    def test_configured_and_candidate_tallies_are_reported_separately(self):
        rows = backfill.probe_series({"cfg_ok": {"fred_series": "GOOD"},
                               "candidate:NOPE": {"fred_series": "BAD"}},
                              urlopen=self._opener)
        out = backfill.render_probe(rows)
        assert "1/1 CONFIGURED ids resolved" in out
        assert "0/1 CANDIDATE ids resolved" in out
        assert "never auto-adopted" in out

    def test_a_failing_candidate_does_not_fail_the_run(self, tmp_path, monkeypatch, capsys):
        """A candidate is a QUESTION. Its failure must not read as a broken config, and its
        success must not mask a configured id that is still broken."""
        cfgfile = tmp_path / "series.yaml"
        cfgfile.write_text("series:\n  cfg_ok:\n    fred_series: GOOD\n", encoding="utf-8")
        monkeypatch.setattr(backfill, "_fetch_history",
                            lambda sid, urlopen=None: [("2020-01-01", 1.0)]
                            if "GOOD" in sid else [])
        import urllib.request as rq
        monkeypatch.setattr(rq, "urlopen", self._opener)
        rc = backfill.main(["--config", str(cfgfile), "--probe-ids", "--probe-extra", "BADCAND"])
        out = capsys.readouterr().out
        assert "candidate:BADCAND" in out
        assert rc == 0, "a failing CANDIDATE must not fail the run; only a configured id does"

    def test_a_broken_configured_id_still_fails_even_when_a_candidate_resolves(
            self, tmp_path, monkeypatch, capsys):
        cfgfile = tmp_path / "series.yaml"
        cfgfile.write_text("series:\n  cfg_broken:\n    fred_series: BAD\n", encoding="utf-8")
        monkeypatch.setattr(backfill, "_fetch_history",
                            lambda sid, urlopen=None: [("2020-01-01", 1.0)]
                            if "GOOD" in sid else [])
        import urllib.request as rq
        monkeypatch.setattr(rq, "urlopen", self._opener)
        rc = backfill.main(["--config", str(cfgfile), "--probe-ids", "--probe-extra", "GOODCAND"])
        capsys.readouterr()
        assert rc == 1, "a resolving candidate must not mask a broken configured id"
