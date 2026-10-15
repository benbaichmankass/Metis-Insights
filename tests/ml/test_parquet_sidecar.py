"""DATA-STORE-PARQUET-GC: data.parquet sidecar beside data.jsonl — exact
parity, freshness, fail-open, and the readers that prefer it."""
from __future__ import annotations

import json
import math
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, ClassVar

import pytest

pytest.importorskip("pyarrow")

from ml.datasets import parquet_sidecar as ps
from ml.datasets.builder import DatasetBuilder


class _Toy(DatasetBuilder):
    family: ClassVar[str] = "toy"
    builder_version: ClassVar[str] = "v1"
    schema: ClassVar[Mapping[str, type]] = {
        "ts": str, "x": float, "n": int, "flag": bool, "label": str,
    }
    parquet_sidecar: ClassVar[bool] = True

    def iter_rows(self, *, rows: list[dict[str, Any]], **_: Any) -> Iterator[Mapping[str, Any]]:
        yield from rows


def _rows(k: int = 7) -> list[dict[str, Any]]:
    out = []
    for i in range(k):
        out.append({"ts": f"2026-10-0{1 + i % 9}T00:00:00Z", "x": i / 3.0,
                    "n": i * 10**12, "flag": i % 2 == 0,
                    "label": None if i == 3 else f"lab{i}"})
    out[2]["x"] = float("nan")
    if k > 4:
        out[4]["x"] = 1e-308
    return out


def _jsonl(d: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (d / "data.jsonl").read_text().splitlines() if line]


def _same_rows(a, b) -> bool:
    if len(a) != len(b):
        return False
    for ra, rb in zip(a, b):
        if ra.keys() != rb.keys():
            return False
        for k in ra:
            if not ps._same(ra[k], rb[k]):
                return False
    return True


def test_build_writes_sidecar_with_exact_parity(tmp_path: Path):
    paths = _Toy().build(tmp_path, "v001", source="t", rows=_rows())
    assert paths.parquet.is_file()
    assert ps.verify_parity(paths.root)["status"] == "PARITY"
    got = ps.load_dataset_rows(paths.root)
    assert _same_rows(got, _jsonl(paths.root))
    assert math.isnan(got[2]["x"]) and type(got[0]["n"]) is int and type(got[1]["flag"]) is bool


def test_projection_matches_jsonl_projection(tmp_path: Path):
    from ml.experiments.runner import _load_jsonl

    paths = _Toy().build(tmp_path, "v001", source="t", rows=_rows())
    keep = frozenset({"ts", "label"})
    assert ps.load_dataset_rows(paths.root, keep=keep) == _load_jsonl(paths.data, keep=keep)
    # fail-open: a keep-set that misses every column loads everything
    assert _same_rows(ps.load_dataset_rows(paths.root, keep=frozenset({"zzz"})), _jsonl(paths.root))


def test_stale_sidecar_is_not_read(tmp_path: Path):
    paths = _Toy().build(tmp_path, "v001", source="t", rows=_rows())
    with paths.data.open("a") as fh:  # JSONL changes after the sidecar was derived
        fh.write(json.dumps({"ts": "z", "x": 0.0, "n": 1, "flag": True, "label": "new"}, sort_keys=True) + "\n")
    assert ps.fresh_sidecar(paths.root) is None
    assert ps.load_dataset_rows(paths.root)[-1]["label"] == "new"


def test_overwrite_build_drops_old_sidecar_first(tmp_path: Path, monkeypatch):
    paths = _Toy().build(tmp_path, "v001", source="t", rows=_rows())
    assert paths.parquet.is_file()
    monkeypatch.setattr(ps, "_pa", lambda: (None, None))  # sidecar cannot be rewritten
    _Toy().build(tmp_path, "v001", source="t", rows=_rows(3), overwrite=True)
    assert not paths.parquet.exists()
    assert len(ps.load_dataset_rows(paths.root)) == 3


def test_ragged_rows_refused_and_jsonl_still_read(tmp_path: Path):
    d = tmp_path / "v"
    d.mkdir()
    (d / "data.jsonl").write_text('{"a": 1}\n{"a": 2, "b": 3}\n')
    r = ps.write_sidecar(d)
    assert r["status"] == "refused" and "ragged" in r["reason"]
    assert not (d / "data.parquet").exists()
    assert ps.load_dataset_rows(d) == [{"a": 1}, {"a": 2, "b": 3}]


@pytest.mark.parametrize("lines,why", [
    ('{"a": 1}\n{"a": 1.5}\n', "mixes"),
    ('{"a": [1]}\n', "holds list"),
    ('{"a": 99999999999999999999}\n', "int64"),
])
def test_unrepresentable_columns_refused(tmp_path: Path, lines, why):
    d = tmp_path / "v"
    d.mkdir()
    (d / "data.jsonl").write_text(lines)
    r = ps.write_sidecar(d)
    assert r["status"] == "refused" and why in r["reason"]


def test_parquet_only_dir_is_present_and_readable(tmp_path: Path):
    paths = _Toy().build(tmp_path, "v001", source="t", rows=_rows())
    expected = _jsonl(paths.root)
    paths.data.unlink()
    assert ps.dataset_present(paths.root)
    assert _same_rows(ps.load_dataset_rows(paths.root), expected)


def test_missing_everything_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        ps.load_dataset_rows(tmp_path)
    assert not ps.dataset_present(tmp_path)


def test_market_features_opts_in():
    from ml.datasets.families.market_features import MarketFeaturesBuilder

    assert MarketFeaturesBuilder.parquet_sidecar is True
    assert DatasetBuilder.parquet_sidecar is False


def test_cli_convert_and_verify(tmp_path: Path, capsys):
    d = tmp_path / "v"
    d.mkdir()
    (d / "data.jsonl").write_text('{"a": 1, "b": "x"}\n{"a": 2, "b": null}\n')
    assert ps.main(["convert", str(d)]) == 0
    assert ps.main(["verify", str(d)]) == 0
    out = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert out[0]["status"] == "written" and out[1]["status"] == "PARITY"
