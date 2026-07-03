"""Tests for the `corpus_panel` dataset family (M19 T2 C3).

Stdlib-only: a synthetic corpus store on a tmp root → the aligned date × series
panel. The load-bearing assertions are the LEAKAGE contract — one-day lag,
forward-fill, and no backfill.
"""
from __future__ import annotations

import json
from pathlib import Path

from ml.datasets import corpus_store
from ml.datasets.families.corpus_panel import CorpusPanelBuilder
from ml.datasets.registry import get_builder

# --- synthetic corpus -------------------------------------------------------
# 3 series across 2 groups, DIFFERENT start dates + gaps.
#
#   macro/fred_vix    : 2020-01-01=10.0, 2020-01-03=12.0, 2020-01-06=15.0
#   macro/fred_dxy    : 2020-01-01=90.0,                  2020-01-05=95.0
#   rates/fred_ust10y :               2020-01-02=1.5, 2020-01-04=1.7
#
# Union grid = 2020-01-01 .. 2020-01-06 (6 dates; 01-01..01-06 minus nothing —
# every calendar day 01..06 appears in some series).
VIX = {"2020-01-01": 10.0, "2020-01-03": 12.0, "2020-01-06": 15.0}
DXY = {"2020-01-01": 90.0, "2020-01-05": 95.0}
UST = {"2020-01-02": 1.5, "2020-01-04": 1.7}

EXPECTED_GRID = [
    "2020-01-01",
    "2020-01-02",
    "2020-01-03",
    "2020-01-04",
    "2020-01-05",
    "2020-01-06",
]


def _write_corpus(root: Path) -> None:
    def rows(m: dict[str, float]) -> list[dict[str, object]]:
        return [{"date": d, "value": v} for d, v in sorted(m.items())]

    corpus_store.write_series(
        "fred_vix", "macro", "test", rows(VIX), refreshed_at="2020-01-07T00:00:00Z", root=root
    )
    corpus_store.write_series(
        "fred_dxy", "macro", "test", rows(DXY), refreshed_at="2020-01-07T00:00:00Z", root=root
    )
    corpus_store.write_series(
        "fred_ust10y", "rates", "test", rows(UST), refreshed_at="2020-01-07T00:00:00Z", root=root
    )


def _build_panel(tmp_path: Path, **iter_kwargs: object) -> list[dict]:
    corpus = tmp_path / "corpus"
    _write_corpus(corpus)
    out_root = tmp_path / "out"
    paths = CorpusPanelBuilder().build(
        output_dir=out_root,
        version="v001",
        source="corpus_store",
        corpus_root=str(corpus),
        overwrite=True,
        **iter_kwargs,
    )
    return [json.loads(ln) for ln in paths.data.read_text().splitlines() if ln]


def _by_date(rows: list[dict]) -> dict[str, dict]:
    return {r["date"]: r["values"] for r in rows}


def test_family_is_registered():
    assert isinstance(get_builder("corpus_panel"), CorpusPanelBuilder)


def test_grid_is_sorted_union_of_all_dates(tmp_path: Path):
    rows = _build_panel(tmp_path)
    dates = [r["date"] for r in rows]
    # (a) grid = sorted union of every series' dates, ascending.
    assert dates == EXPECTED_GRID
    assert dates == sorted(dates)


def test_one_day_lag_value_appears_next_grid_date_not_its_own(tmp_path: Path):
    """(b) ANTI-LOOKAHEAD: a value dated D shows up in the row dated D+1, NOT D."""
    panel = _by_date(_build_panel(tmp_path))

    # fred_vix=12.0 is dated 2020-01-03. The next grid date is 2020-01-04.
    # It MUST appear at 01-04, and MUST NOT appear in its own 01-03 row.
    assert panel["2020-01-04"]["fred_vix"] == 12.0
    assert panel["2020-01-03"]["fred_vix"] != 12.0
    # The 01-03 row instead carries the PRIOR (2020-01-01=10.0) lagged value.
    assert panel["2020-01-03"]["fred_vix"] == 10.0

    # Same guarantee on another series: fred_ust10y=1.5 dated 2020-01-02 must
    # appear at 2020-01-03, never in its own 2020-01-02 row.
    assert panel["2020-01-03"]["fred_ust10y"] == 1.5
    assert panel["2020-01-02"]["fred_ust10y"] != 1.5


def test_forward_fill_carries_last_known_value(tmp_path: Path):
    """(c) FORWARD-FILL: after a series' lagged value, later grid dates carry it."""
    panel = _by_date(_build_panel(tmp_path))

    # fred_vix=12.0 (dated 01-03) becomes visible 01-04 and is carried forward to
    # 01-05 (next update 01-06 is only visible from the non-existent 01-07).
    assert panel["2020-01-04"]["fred_vix"] == 12.0
    assert panel["2020-01-05"]["fred_vix"] == 12.0

    # fred_dxy=90.0 (dated 01-01) forward-fills across 01-02, 01-03, 01-04 until
    # 95.0 (dated 01-05) becomes visible at 01-06.
    for d in ("2020-01-02", "2020-01-03", "2020-01-04", "2020-01-05"):
        assert panel[d]["fred_dxy"] == 90.0
    assert panel["2020-01-06"]["fred_dxy"] == 95.0


def test_no_backfill_before_first_value_is_none(tmp_path: Path):
    """(d) NO BACKFILL: grid dates before a series' first lagged value are None."""
    panel = _by_date(_build_panel(tmp_path))

    # fred_ust10y's first obs is 2020-01-02 → first visible 2020-01-03.
    # 2020-01-01 and 2020-01-02 must be None, never future-filled with 1.5.
    assert panel["2020-01-01"]["fred_ust10y"] is None
    assert panel["2020-01-02"]["fred_ust10y"] is None

    # fred_vix's first obs is 2020-01-01 → first visible 2020-01-02, so its own
    # 2020-01-01 row is None (one-day lag AND pre-first coincide here).
    assert panel["2020-01-01"]["fred_vix"] is None
    # And every series is None on the very first grid date (nothing strictly
    # prior exists for any of them).
    assert all(v is None for v in panel["2020-01-01"].values())


def test_final_grid_date_value_is_never_emitted(tmp_path: Path):
    """A value on the LAST grid date can't leak — its availability is the next
    (non-existent) step. fred_vix=15.0 dated 2020-01-06 appears nowhere."""
    rows = _build_panel(tmp_path)
    for r in rows:
        assert r["values"]["fred_vix"] != 15.0


def test_series_allowlist_and_group_filter(tmp_path: Path):
    # series= narrows to a single column set.
    panel = _by_date(_build_panel(tmp_path, series="fred_vix"))
    assert all(set(v.keys()) == {"fred_vix"} for v in panel.values())

    # groups= narrows to the macro group (fred_vix + fred_dxy), excluding rates.
    panel = _by_date(_build_panel(tmp_path, groups="macro"))
    assert all(set(v.keys()) == {"fred_dxy", "fred_vix"} for v in panel.values())


def test_metadata_records_leakage_and_convention(tmp_path: Path):
    corpus = tmp_path / "corpus"
    _write_corpus(corpus)
    paths = CorpusPanelBuilder().build(
        output_dir=tmp_path / "out",
        version="v001",
        source="corpus_store",
        corpus_root=str(corpus),
    )
    meta = json.loads(paths.metadata.read_text())
    assert meta["leakage_test_status"] == "passed"
    assert meta["label_version"] == "corpus_panel_past_only_ffill_1d_lag_v1"
    assert meta["schema"] == {"date": "str", "values": "dict"}
