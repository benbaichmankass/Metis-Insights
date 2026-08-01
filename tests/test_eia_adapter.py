"""Unit tests for the keyless EIA bulk adapter (macro-M1 energy kinds).

Pure-parse + a fake-urlopen network test (no network), mirroring the fred_adapter
test style. Backs R2 / BL-20260730-EIA-SERIES-IDS-NOT-FRED.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from src.units.strategies.macro_thesis import eia_adapter as ea


def test_dataset_of() -> None:
    assert ea.dataset_of("PET.WCESTUS1.W") == "PET"
    assert ea.dataset_of("NG.NW2_EPG0_SWO_R48_BCF.W") == "NG"


def test_parse_orders_ascending_and_skips_bad_rows() -> None:
    # newest-first `data` (as EIA serves it), a null value, and a non-day (monthly)
    # stamp — the parser must reorder ascending and drop the two bad obs.
    line = json.dumps({
        "series_id": "PET.WCESTUS1.W",
        "name": "U.S. Ending Stocks excluding SPR of Crude Oil, Weekly",
        "data": [["20260724", 404508], ["20260717", None], ["202606", 400000], ["20260710", 403000]],
    })
    category = json.dumps({"category_id": 1, "name": "Petroleum"})  # no series_id/data
    text = category + "\n" + line + "\n"
    got = ea.parse_eia_bulk_ndjson(text, ["PET.WCESTUS1.W"])
    assert got["PET.WCESTUS1.W"] == [("2026-07-10", 403000.0), ("2026-07-24", 404508.0)]


def test_parse_ignores_unrequested_ids() -> None:
    text = json.dumps({"series_id": "PET.OTHER.W", "data": [["20260724", 1]]})
    got = ea.parse_eia_bulk_ndjson(text, ["PET.WCESTUS1.W"])
    assert got == {"PET.WCESTUS1.W": []}


def _fake_urlopen_factory(zips: dict[str, bytes]):
    """A urlopen returning the dataset zip whose name appears in the URL."""
    class _Resp:
        def __init__(self, blob: bytes) -> None:
            self._blob = blob

        def read(self) -> bytes:
            return self._blob

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    calls: list[str] = []

    def _open(url, timeout=None):
        calls.append(url)
        for dataset, blob in zips.items():
            if f"/{dataset}.zip" in url:
                return _Resp(blob)
        raise AssertionError(f"unexpected url {url}")

    _open.calls = calls  # type: ignore[attr-defined]
    return _open


def _make_zip(member: str, records: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member, "\n".join(json.dumps(r) for r in records))
    return buf.getvalue()


def test_fetch_downloads_each_dataset_once_and_extracts() -> None:
    pet = _make_zip("PET.txt", [
        {"series_id": "PET.WCESTUS1.W", "data": [["20260724", 404508], ["20260717", 411710]]},
        {"series_id": "PET.NOISE.W", "data": [["20260724", 1]]},
    ])
    ng = _make_zip("NG.txt", [
        {"series_id": "NG.NW2_EPG0_SWO_R48_BCF.W", "data": [["20260724", 3084]]},
    ])
    fake = _fake_urlopen_factory({"PET": pet, "NG": ng})
    got = ea.fetch_eia_series_history_dated(
        ["PET.WCESTUS1.W", "NG.NW2_EPG0_SWO_R48_BCF.W"], urlopen=fake
    )
    assert got["PET.WCESTUS1.W"] == [("2026-07-17", 411710.0), ("2026-07-24", 404508.0)]
    assert got["NG.NW2_EPG0_SWO_R48_BCF.W"] == [("2026-07-24", 3084.0)]
    # one HTTP request per dataset (grouping), not per series.
    assert len(fake.calls) == 2  # type: ignore[attr-defined]


def test_offvm_guard_blocks_real_network_without_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ICT_OFFVM_BUILD_HOST", raising=False)
    with pytest.raises(RuntimeError, match="off-VM only"):
        ea.fetch_eia_series_history_dated(["PET.WCESTUS1.W"])
