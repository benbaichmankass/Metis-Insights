"""`vol_threshold` / `trend_threshold` are REQUIRED — and why that closes a hole.

Operator-approved 2026-07-30
(BL-20260730-MARKET-FEATURES-VOL-THRESHOLD-DEFAULT-DRIFT), from the
diagnostic-provenance defect-class sweep.

These two kwargs DEFINE ``regime_label``. They used to default to 0.003/0.005,
and the 0.003 was ORPHANED: all ten production call sites pass an explicit
value, and they pass FOUR different ones —

    0.001  scripts/ops/run_serious_baseline.sh
    0.005  build_trainer_datasets.sh (Bybit) · gpu_burst · ETH finetf · trainer-offload
    0.01   scripts/ops/run_mes_training.sh
    median scripts/ops/build_trainer_datasets.sh (MES, data-driven)

so the default applied only when a caller FORGOT, producing a dataset whose
``regime_label`` meant something different from the fleet's.

The part that makes a default unfixable rather than merely risky is
:func:`test_defaulted_value_would_be_absent_from_metadata` below:
``DatasetBuilder.build`` records ``effective_build_params`` from the kwargs it
was PASSED, so a defaulted value never reaches ``iter_rows_kwargs`` and never
lands in ``metadata.json``. The dataset dir was self-describing about every
build param EXCEPT the one that silently defaulted — the exact provenance hole
this sweep is about, in the metadata of the labels themselves.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ml.datasets.families.market_features import MarketFeaturesBuilder

_REQUIRED = ("vol_threshold", "trend_threshold")


def _stage_market_raw(tmp_path: Path, n: int = 200) -> Path:
    root = tmp_path / "market_raw" / "BTCUSDT" / "15m" / "v001"
    root.mkdir(parents=True, exist_ok=True)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with (root / "data.jsonl").open("w", encoding="utf-8") as fh:
        for i in range(n):
            close = 100.0 + (i % 7) * 0.9 + i * 0.05
            fh.write(json.dumps({
                "ts": (base + timedelta(seconds=900 * i)).isoformat().replace("+00:00", "Z"),
                "symbol": "BTCUSDT", "timeframe": "15m",
                "open": close, "high": close * 1.001, "low": close * 0.999,
                "close": close, "volume": 100.0, "source": "synthetic",
            }) + "\n")
    return root


def _build(tmp_path: Path, raw: Path, **kw) -> dict:
    out = tmp_path / f"out{len(list(tmp_path.iterdir()))}"
    MarketFeaturesBuilder().build(
        output_dir=out, version="v001", source="synthetic",
        symbol_scope="BTCUSDT", timeframe="15m", overwrite=True,
        market_raw_path=str(raw), **kw)
    md = json.loads(
        (out / "market_features" / "BTCUSDT" / "15m" / "v001" / "metadata.json")
        .read_text(encoding="utf-8"))
    return md.get("build_params") or {}


def test_omitting_either_threshold_raises(tmp_path: Path):
    """An omission must fail LOUDLY, not silently pick a label definition."""
    raw = _stage_market_raw(tmp_path)
    builder = MarketFeaturesBuilder()

    with pytest.raises(TypeError, match="vol_threshold"):
        list(builder.iter_rows(market_raw_path=raw, trend_threshold=0.005))
    with pytest.raises(TypeError, match="trend_threshold"):
        list(builder.iter_rows(market_raw_path=raw, vol_threshold=0.005))
    with pytest.raises(TypeError):
        list(builder.iter_rows(market_raw_path=raw))


def test_explicit_thresholds_land_in_metadata_build_params(tmp_path: Path):
    """The dir is self-describing: the label definition is ON DISK next to it."""
    raw = _stage_market_raw(tmp_path)
    bp = _build(tmp_path, raw, vol_threshold=0.005, trend_threshold=0.005)
    for key in _REQUIRED:
        assert key in bp, f"{key} missing from metadata.json::build_params"
    assert float(bp["vol_threshold"]) == 0.005


def test_a_different_threshold_is_recorded_as_itself(tmp_path: Path):
    """Two dirs built at different thresholds are DISTINGUISHABLE on disk.

    This is the property that would have prevented the parity probe comparing a
    head pinned to a 0.005 dataset against a 0.003 one and calling both
    "TRAINING dataset".
    """
    raw = _stage_market_raw(tmp_path)
    a = _build(tmp_path, raw, vol_threshold=0.005, trend_threshold=0.005)
    b = _build(tmp_path, raw, vol_threshold=0.001, trend_threshold=0.001)
    assert float(a["vol_threshold"]) != float(b["vol_threshold"])


def test_defaulted_value_would_be_absent_from_metadata(tmp_path: Path):
    """The reason a default is unfixable here, pinned as a property.

    ``build`` records only the kwargs it was PASSED. Simulating the old
    behaviour — omit the param, let a default apply inside ``iter_rows`` —
    shows the value never reaches ``build_params``. So a defaulted threshold
    was BOTH different from the fleet's AND unrecorded, which is strictly
    worse than a wrong-but-recorded value: nothing downstream could even
    detect the difference.

    Guards the property, not the old signature: if someone re-adds a default,
    `test_omitting_either_threshold_raises` fails and this documents why.
    """
    raw = _stage_market_raw(tmp_path)
    # `forward_window_m` still legitimately has a default — use it as the
    # stand-in to demonstrate the recording rule without reintroducing one.
    bp_default = _build(tmp_path, raw, vol_threshold=0.005, trend_threshold=0.005)
    assert "forward_window_m" not in bp_default, (
        "a DEFAULTED kwarg is absent from build_params — this is the recording "
        "rule that made a defaulted vol_threshold invisible on disk"
    )
    bp_explicit = _build(tmp_path, raw, vol_threshold=0.005,
                         trend_threshold=0.005, forward_window_m=5)
    assert "forward_window_m" in bp_explicit, (
        "an EXPLICIT kwarg IS recorded — so requiring the thresholds is what "
        "guarantees they always appear"
    )


def test_no_production_caller_relies_on_a_default():
    """Every shell/YAML build site passes both thresholds explicitly.

    Pins the audit that made this change safe. If a new build site is added
    without them, the build now raises TypeError at runtime — but this catches
    it in CI first, before a training cycle burns on it.
    """
    import re
    root = Path(__file__).resolve().parents[3]
    call_re = re.compile(r"(build-dataset|build_family|mes_build)\s+market_features")
    offenders = []
    for pattern in ("scripts/**/*.sh", "scripts/**/*.py", ".github/workflows/*.yml"):
        for path in root.glob(pattern):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            in_doc = False
            for i, line in enumerate(lines):
                in_doc ^= (line.count('"""') % 2 == 1)
                if in_doc or not call_re.search(line):
                    continue
                window = "\n".join(lines[i:i + 14])
                # gpu_burst templates the params in from a defaults dict.
                if "{features_params}" in window:
                    continue
                if not all(k in window for k in _REQUIRED):
                    offenders.append(f"{path.relative_to(root)}:{i + 1}")
    assert not offenders, (
        "market_features build site(s) without an explicit threshold: "
        + ", ".join(offenders)
    )
