"""scripts/ops/trainer_dataset_gc.py keep rules (DATA-STORE-PARQUET-GC)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from scripts.ops import trainer_dataset_gc as gc


def _mk(root: Path, rel: str, meta: dict | None = None, old: bool = True) -> Path:
    d = root / rel
    d.mkdir(parents=True)
    (d / "data.jsonl").write_text('{"a": 1}\n')
    (d / "metadata.json").write_text(json.dumps(meta or {}))
    if old:
        t = time.time() - 100 * 86400
        for p in (d / "data.jsonl", d / "metadata.json", d):
            os.utime(p, (t, t))
    return d


def _run(tmp_path: Path, capsys, *extra: str) -> tuple[list[dict], dict]:
    rc = gc.main(["--datasets-root", str(tmp_path / "ds"), "--configs", str(tmp_path / "cfg"),
                  "--builder-scripts", str(tmp_path / "build.sh"),
                  "--ref-roots", str(tmp_path / "repo"), "--keep-versions", "",
                  "--doc-roots", str(tmp_path / "docs"),
                  "--doc-exclude", str(tmp_path / "docs" / "archive"),
                  "--min-age-days", "14", "--list-kept", *extra])
    assert rc == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    return rows, rows[-1]


def _setup(tmp_path: Path) -> Path:
    ds = tmp_path / "ds"
    (tmp_path / "cfg").mkdir()
    (tmp_path / "repo").mkdir()
    (tmp_path / "docs" / "archive").mkdir(parents=True)
    (tmp_path / "cfg" / "m.yaml").write_text(
        "dataset:\n  family: market_features\n  symbol_scope: BTCUSDT\n  timeframe: 15m\n  version: v513\n")
    (tmp_path / "build.sh").write_text('x="${DATASETS_ROOT}/market_raw/MES/15m/v001"\n')
    (tmp_path / "repo" / "unit.yaml").write_text("path: datasets-out/market_features/ETHUSDT/15m/v901\n")
    _mk(ds, "market_features/BTCUSDT/15m/v513",
        meta={"build_params": {"market_raw_path": "/x/datasets-out/market_raw/BTCUSDT/15m/v513",
                               "forecast_path": "datasets-out/forecasts/BTCUSDT/15m/v001"}})
    _mk(ds, "market_raw/BTCUSDT/15m/v513")
    _mk(ds, "forecasts/BTCUSDT/15m/v001")
    _mk(ds, "market_raw/MES/15m/v001")
    _mk(ds, "market_features/ETHUSDT/15m/v901")
    _mk(ds, "market_features/BTCUSDT/15m/v004")       # unreferenced → candidate
    _mk(ds, "market_raw/BTCUSDT/1m/v001")             # unreferenced → candidate
    _mk(ds, "market_features/BTCUSDT/15m/v777", old=False)  # young
    return ds


def test_keep_reasons_and_candidates(tmp_path: Path, capsys):
    _setup(tmp_path)
    rows, summary = _run(tmp_path, capsys)
    kept = {r["path"].split("ds/")[1]: r["reasons"] for r in rows if r["status"] == "gc_kept"}
    cands = {r["path"].split("ds/")[1] for r in rows if r["status"] == "gc_candidate"}
    assert cands == {"market_features/BTCUSDT/15m/v004", "market_raw/BTCUSDT/1m/v001"}
    assert kept["market_features/BTCUSDT/15m/v513"] == ["manifest"]
    assert kept["market_raw/BTCUSDT/15m/v513"][0].startswith("input_of:market_features/BTCUSDT/15m/v513")
    assert kept["forecasts/BTCUSDT/15m/v001"][0].startswith("input_of:")
    assert kept["market_raw/MES/15m/v001"] == ["nightly"]
    assert kept["market_features/ETHUSDT/15m/v901"][0].startswith("repo_ref:")
    assert kept["market_features/BTCUSDT/15m/v777"] == ["young"]
    assert summary["scanned"] == 8 and summary["candidates"] == 2 and summary["mode"] == "report-only"


def test_input_of_a_candidate_is_not_protected(tmp_path: Path, capsys):
    ds = _setup(tmp_path)
    _mk(ds, "market_features/SOLUSDT/15m/v600",
        meta={"build_params": {"market_raw_path": "datasets-out/market_raw/SOLUSDT/15m/v600"}})
    _mk(ds, "market_raw/SOLUSDT/15m/v600")
    rows, _ = _run(tmp_path, capsys)
    cands = {r["path"].split("ds/")[1] for r in rows if r["status"] == "gc_candidate"}
    assert {"market_features/SOLUSDT/15m/v600", "market_raw/SOLUSDT/15m/v600"} <= cands


def test_new_file_in_old_dir_counts_as_young(tmp_path: Path, capsys):
    ds = _setup(tmp_path)
    (ds / "market_features/BTCUSDT/15m/v004/data.jsonl").write_text('{"a": 2}\n')  # rewritten now
    rows, _ = _run(tmp_path, capsys)
    cands = {r["path"].split("ds/")[1] for r in rows if r["status"] == "gc_candidate"}
    assert "market_features/BTCUSDT/15m/v004" not in cands


def test_apply_deletes_only_candidates(tmp_path: Path, capsys):
    ds = _setup(tmp_path)
    _run(tmp_path, capsys, "--apply")
    assert not (ds / "market_features/BTCUSDT/15m/v004").exists()
    assert not (ds / "market_raw/BTCUSDT/1m/v001").exists()
    for keep in ("market_features/BTCUSDT/15m/v513", "market_raw/BTCUSDT/15m/v513",
                 "forecasts/BTCUSDT/15m/v001", "market_raw/MES/15m/v001",
                 "market_features/ETHUSDT/15m/v901", "market_features/BTCUSDT/15m/v777"):
        assert (ds / keep).is_dir(), keep


def test_doc_ref_keeps_and_archive_does_not(tmp_path: Path, capsys):
    ds = _setup(tmp_path)
    _mk(ds, "market_raw/MES/5m/v001")
    _mk(ds, "market_features/BTCUSDT/1h/v001")
    (tmp_path / "docs" / "result.md").write_text(
        "- **Data:** trainer `datasets-out/market_raw/MES/5m/v001/data.jsonl`\n")
    (tmp_path / "docs" / "archive" / "old.md").write_text(
        "Rebuilt market_features/BTCUSDT/1h/v001 with vol_threshold=0.003\n")
    rows, summary = _run(tmp_path, capsys)
    kept = {r["path"].split("ds/")[1]: r["reasons"] for r in rows if r["status"] == "gc_kept"}
    cands = {r["path"].split("ds/")[1] for r in rows if r["status"] == "gc_candidate"}
    assert kept["market_raw/MES/5m/v001"][0].startswith("doc_ref:")
    assert "market_features/BTCUSDT/1h/v001" in cands  # only an archived doc names it
    assert summary["doc_ref_versions"] == 1
