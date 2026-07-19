"""RG4 (replay_pregate_live) label-threshold resolution + labeling-join tests.

Pins two distinct, separately-tracked facts about the MES/ETH live-labeling
investigation (MB-20260626-001 #1 / MB-20260627-002 / MB-20260628-RG4-THRESH):

1. **Threshold resolution (MB-20260628-RG4-THRESH).** RG4 must score a regime
   head against the SAME ``vol_threshold`` its training label used, not the old
   hardcoded ``0.003``. The resolver now picks the threshold per-symbol (Bybit
   pairs → 0.005, mirroring ``build_trainer_datasets.sh::build_bybit_pair``;
   ``market_features`` metadata when recorded; explicit ``--vol-threshold``
   wins; ``0.003`` only when the symbol is unknown).

2. **Threshold does NOT cause "unlabeled".** The all-rows-UNLABELED MES symptom
   that blinds RG4 is a *candle-availability / stale-base* issue, NOT a
   threshold issue: ``_forward_label_map`` labels exactly the same set of bars
   at any threshold (the threshold only flips a bar's 0/1 value), and a live row
   is unlabeled iff its ``predicted_at_utc`` has no candle bar within tolerance.
   These tests lock that in so a future change can't conflate the two.

The module imports pandas at load (via ``replay_pregate``); ``importorskip``
keeps the suite clean on a lean sandbox while running fully in CI (pandas is in
requirements.txt / requirements-test.txt).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("pandas")  # replay_pregate imports pandas at module load

import scripts.ml.replay_pregate_live as R  # noqa: E402


# --------------------------------------------------------------------------- #
# 1. Per-symbol vol_threshold resolution (MB-20260628-RG4-THRESH)
# --------------------------------------------------------------------------- #
def _bybit_rows():
    return [{"symbol": "BTCUSDT", "ts": "2026-06-01T00:00:00Z", "close": 100.0}]


def _mes_rows():
    return [{"symbol": "MES", "ts": "2026-06-01T00:00:00Z", "close": 5000.0}]


def test_bybit_symbol_resolves_to_training_threshold():
    """A Bybit head with no explicit threshold gets 0.005 (its build label),
    not the legacy 0.003 default that mis-scored it."""
    vt, src = R._resolve_vol_threshold(
        _bybit_rows(), "x/market_raw/BTCUSDT/5m/v1/data.jsonl", R._VT_UNSET
    )
    assert vt == 0.005
    assert src == "symbol_default"


@pytest.mark.parametrize("sym", ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
def test_all_bybit_pairs_resolve_to_005(sym):
    rows = [{"symbol": sym, "ts": "2026-06-01T00:00:00Z", "close": 1.0}]
    vt, src = R._resolve_vol_threshold(rows, f"x/market_raw/{sym}/1h/v1/data.jsonl",
                                       R._VT_UNSET)
    assert (vt, src) == (0.005, "symbol_default")


def test_explicit_threshold_is_authoritative():
    """An explicit --vol-threshold wins over every default — the precise path
    rg4_vt_sweep.sh uses, and the only correct path for MES's median."""
    vt, src = R._resolve_vol_threshold(
        _bybit_rows(), "x/market_raw/BTCUSDT/5m/v1/data.jsonl", 0.00056
    )
    assert vt == 0.00056
    assert src == "explicit"


def test_mes_falls_back_to_global_default_not_a_stale_hardcode():
    """MES is intentionally NOT in the per-symbol map — its median threshold is
    data-driven and would go stale if hardcoded, so it must be passed explicitly
    (or read from metadata). With neither, resolution is the documented global
    fallback, never a wrong fixed MES number."""
    vt, src = R._resolve_vol_threshold(
        _mes_rows(), "x/market_raw/MES/5m/v1/data.jsonl", R._VT_UNSET
    )
    assert vt == R._DEFAULT_VOL_THRESHOLD == 0.003
    assert src == "global_default"


def test_unknown_symbol_falls_back_to_global_default():
    rows = [{"symbol": "XAUUSD", "ts": "t", "close": 1.0}]
    vt, src = R._resolve_vol_threshold(rows, "p", R._VT_UNSET)
    assert (vt, src) == (0.003, "global_default")


def test_threshold_from_market_features_metadata(tmp_path):
    """When the market_features build records vol_threshold in metadata, the
    resolver reads it (the per-dataset, MES-correct path) ahead of the per-symbol
    default. Mirrors the …/market_raw/<sym>/<tf>/<ver>/ ->
    …/market_features/<sym>/<tf>/<ver>/metadata.json layout."""
    raw_dir = tmp_path / "market_raw" / "MES" / "5m" / "v002"
    feat_dir = tmp_path / "market_features" / "MES" / "5m" / "v002"
    raw_dir.mkdir(parents=True)
    feat_dir.mkdir(parents=True)
    (feat_dir / "metadata.json").write_text(
        json.dumps({"vol_threshold": 0.00056}), encoding="utf-8"
    )
    candles = str(raw_dir / "data.jsonl")
    vt, src = R._resolve_vol_threshold(_mes_rows(), candles, R._VT_UNSET)
    assert vt == 0.00056
    assert src == "market_features_meta"


def test_threshold_from_build_params_metadata(tmp_path):
    """Builder v12 records vol_threshold under build_params, not the top
    level — the miss here made the 2026-07-19 MES read fall to the 0.003
    global default against a build labeled at 0.0003418 (degenerate
    0-positive verdict). The resolver must read build_params.vol_threshold."""
    raw_dir = tmp_path / "market_raw" / "MES" / "5m" / "v002"
    feat_dir = tmp_path / "market_features" / "MES" / "5m" / "v002"
    raw_dir.mkdir(parents=True)
    feat_dir.mkdir(parents=True)
    (feat_dir / "metadata.json").write_text(
        json.dumps({"build_params": {"vol_threshold": 0.0003418,
                                     "forward_window_m": 5}}),
        encoding="utf-8",
    )
    candles = str(raw_dir / "data.jsonl")
    vt, src = R._resolve_vol_threshold(_mes_rows(), candles, R._VT_UNSET)
    assert vt == 0.0003418
    assert src == "market_features_meta"


def test_threshold_from_metadata_notes_freetext(tmp_path):
    """Falls back to parsing ``vol_threshold=<x>`` out of the metadata notes
    blob when there is no structured field (future-proof / defensive)."""
    raw_dir = tmp_path / "market_raw" / "ETHUSDT" / "1h" / "v003"
    feat_dir = tmp_path / "market_features" / "ETHUSDT" / "1h" / "v003"
    raw_dir.mkdir(parents=True)
    feat_dir.mkdir(parents=True)
    (feat_dir / "metadata.json").write_text(
        json.dumps({"notes": "built with vol_threshold=0.0072 trend_threshold=0.0072"}),
        encoding="utf-8",
    )
    candles = str(raw_dir / "data.jsonl")
    rows = [{"symbol": "ETHUSDT", "ts": "t", "close": 1.0}]
    vt, src = R._resolve_vol_threshold(rows, candles, R._VT_UNSET)
    assert vt == 0.0072
    assert src == "market_features_meta"


# --------------------------------------------------------------------------- #
# 2. Threshold does NOT cause "unlabeled"; stale candles do (the MES symptom)
# --------------------------------------------------------------------------- #
def _candle_series(start: datetime, n: int, step_min: int):
    rows = []
    price = 5000.0
    for i in range(n):
        t = start + timedelta(minutes=step_min * i)
        price *= 1.0 + (0.0006 if i % 2 else -0.0004)
        rows.append({"symbol": "MES", "ts": t.isoformat(), "close": round(price, 2)})
    return rows


def test_label_count_is_threshold_invariant():
    """The number of LABELED bars is identical across thresholds — the
    threshold only changes a bar's 0/1 value, never whether it is labeled. So a
    threshold mismatch can never be the cause of all-rows-UNLABELED (that is the
    candle-availability / stale-base issue)."""
    rows = _candle_series(datetime(2026, 6, 12, 20, 0, tzinfo=timezone.utc), 40, 5)
    counts = {
        vt: len(R._forward_label_map(rows, forward_m=5, vol_threshold=vt,
                                     positive_class="volatile"))
        for vt in (0.00056, 0.003, 0.005)
    }
    assert len(set(counts.values())) == 1  # same labeled-bar count at every vt
    assert all(c > 0 for c in counts.values())


def test_live_row_after_candle_base_is_unlabeled():
    """A shadow row whose predicted_at_utc postdates the candle file's last bar
    (the stale-MES-base failure, BL-20260626-MES-BASE-STALE) finds no bar within
    tolerance -> UNLABELED. This is the real mechanism behind RG4 returning {}
    for the MES fleet — NOT a symbol-key or threshold bug."""
    base = datetime(2026, 6, 12, 20, 0, tzinfo=timezone.utc)  # candle base ends here
    rows = _candle_series(base, 40, 5)
    lm = R._forward_label_map(rows, forward_m=5, vol_threshold=0.003,
                              positive_class="volatile")
    keys = sorted(lm.keys())

    when_stale = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)  # weeks later
    assert R._nearest_label(lm, when_stale, keys, tol_seconds=300.0) is None

    # A row landing inside the candle window labels fine — proves the join works
    # when candles cover the row (so the fix is fresh candles, upstream).
    when_in = base + timedelta(minutes=50, seconds=30)
    assert R._nearest_label(lm, when_in, keys, tol_seconds=300.0) in (0, 1)


def test_candle_symbol_extraction():
    assert R._candle_symbol(_bybit_rows()) == "BTCUSDT"
    assert R._candle_symbol([{"ts": "t", "close": 1.0}]) is None  # no symbol stamp
