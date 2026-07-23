"""M28 P1 — tests for the historical point-in-time valuation-snapshot backfill.

No network: the FRED fetch is exercised through an injected fake ``urlopen``.
"""

from __future__ import annotations

import importlib.util
import os

from src.units.strategies.macro_thesis.fred_adapter import (
    fetch_fred_series_history_dated,
    parse_fredgraph_csv_dated,
)

_BACKFILL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "macro", "valuation_snapshot_backfill.py",
)
_spec = importlib.util.spec_from_file_location("valuation_snapshot_backfill", _BACKFILL_PATH)
backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill)

# A dated FRED body: rising real yield 1.0 → 3.0 across five monthly observations.
_FRED_CSV = (
    "DATE,VALUE\n"
    "2026-01-01,1.0\n"
    "2026-02-01,1.5\n"
    "2026-03-01,2.0\n"
    "2026-04-01,.\n"          # missing obs — must be skipped, not fabricated
    "2026-05-01,3.0\n"
)


class _FakeResp:
    def __init__(self, text):
        self._t = text

    def read(self):
        return self._t.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(url, timeout=None):
    return _FakeResp(_FRED_CSV)


# --------------------------------------------------------------------------
# dated parse + fetch
# --------------------------------------------------------------------------

def test_parse_dated_keeps_dates_and_skips_missing():
    rows = parse_fredgraph_csv_dated(_FRED_CSV)
    assert rows == [("2026-01-01", 1.0), ("2026-02-01", 1.5), ("2026-03-01", 2.0), ("2026-05-01", 3.0)]


def test_fetch_dated_injected_urlopen():
    out = fetch_fred_series_history_dated(["DFII10"], urlopen=_fake_urlopen)
    assert out["DFII10"][0] == ("2026-01-01", 1.0)
    assert out["DFII10"][-1] == ("2026-05-01", 3.0)


# --------------------------------------------------------------------------
# backfill_rows — point-in-time correctness
# --------------------------------------------------------------------------

_CFG = {
    "instruments": {
        "TLT": {"asset_class": "bond", "metrics": [
            {"metric": "real_yield_10y", "inputs": {"series": "DFII10"}, "higher_is_cheaper": False},
        ]},
    },
}


def _series():
    return {"DFII10": parse_fredgraph_csv_dated(_FRED_CSV)}


def test_backfill_value_is_as_of_or_prior_never_future():
    # Daily cadence: on 2026-03-15 the as-of value must be the 03-01 obs (2.0),
    # NOT the future 05-01 obs (3.0) — leakage-safe by construction.
    rows = backfill.backfill_rows(_CFG, _series(), cadence_days=1)
    by_date = {r["observed_at"]: r for r in rows}
    assert by_date["2026-03-15"]["value"] == 2.0
    assert by_date["2026-03-15"]["observed_at"] == by_date["2026-03-15"]["as_of"]
    # 04-01 is a missing obs → an as-of date inside that gap carries the last
    # real obs (03-01 = 2.0) forward, never the future 05-01 (as-of-or-prior).
    assert by_date["2026-04-15"]["value"] == 2.0
    # The backfill stops at the last real observation — it never fabricates
    # snapshot dates past the data.
    assert max(by_date) == "2026-05-01"


def test_backfill_stamps_source_and_spans_history():
    rows = backfill.backfill_rows(_CFG, _series(), cadence_days=7)
    assert rows, "expected reconstructed rows"
    assert all(r["source"] == "fred_backfill" for r in rows)
    obs = sorted({r["observed_at"] for r in rows})
    assert obs[0] == "2026-01-01"        # earliest observation
    assert obs[-1] <= "2026-05-01"       # within the fetched span


def test_backfill_label_becomes_known_once_history_accrues():
    # Early on there is too little history for a cheap/rich read (unknown);
    # by the last as-of date the full rising history makes 3.0 a RICH read
    # (higher_is_cheaper=False → high real yield = duration rich).
    rows = backfill.backfill_rows(_CFG, _series(), cadence_days=1)
    last = [r for r in rows if r["observed_at"] == "2026-05-01"][0]
    assert last["value"] == 3.0 and last["label"] == "rich"


def test_backfill_empty_history_is_empty():
    assert backfill.backfill_rows(_CFG, {}, cadence_days=7) == []


# --------------------------------------------------------------------------
# run_backfill — full regen (truncate) + summary
# --------------------------------------------------------------------------

def test_run_backfill_writes_and_is_idempotent(tmp_path):
    out = tmp_path / "bf.jsonl"
    s1 = backfill.run_backfill(out_path=out, cadence_days=7, urlopen=_fake_urlopen)
    n1 = out.read_text().count("\n")
    assert s1["rows"] > 0 and s1["written"] == s1["rows"] and n1 == s1["rows"]
    # Re-run: full regen (truncate), NOT append — identical row count, no doubling.
    s2 = backfill.run_backfill(out_path=out, cadence_days=7, urlopen=_fake_urlopen)
    assert out.read_text().count("\n") == n1 == s2["rows"]


def test_run_backfill_dry_run_writes_nothing(tmp_path):
    out = tmp_path / "bf.jsonl"
    s = backfill.run_backfill(out_path=out, cadence_days=7, dry_run=True, urlopen=_fake_urlopen)
    assert s["rows"] > 0 and s["written"] == 0 and not out.exists()
