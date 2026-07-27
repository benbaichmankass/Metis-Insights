"""M30 · P5 — tests for src/research/excursions.py (pure excursion math).

Synthetic candle paths only — no I/O. Exhaustive because the module is pure
arithmetic: MFE/MAE in R units, realized-R, giveback, capture ratio, bars-to-MFE,
long/short mirroring, and the tolerant None-filled degradations.
"""

from __future__ import annotations

from src.research import excursions as ex


def _c(high, low, close=None):
    return {"high": high, "low": low, "close": close if close is not None else (high + low) / 2}


def test_long_mfe_mae_and_giveback():
    # entry 100, stop 90 -> risk 10. Path runs to high 130 (MFE 3R) then exits 110.
    candles = [_c(105, 98), _c(130, 108), _c(115, 109)]
    r = ex.compute_excursions(
        candles, entry_price=100, stop_loss=90, side="buy", exit_price=110
    )
    assert r["risk"] == 10.0
    assert r["mfe"] == 30.0 and r["mfe_r"] == 3.0  # high 130 - 100 = 30 = 3R
    assert r["mae"] == 2.0 and r["mae_r"] == 0.2  # low 98 - 100 -> 2 adverse
    assert r["realized_r"] == 1.0  # (110-100)/10
    assert r["giveback_r"] == 2.0  # 3R peak, kept 1R
    assert r["capture_ratio"] == round(1.0 / 3.0, 6)
    assert r["bars_to_mfe"] == 1
    assert r["bars_held"] == 3
    assert r["time_to_mfe_frac"] == round(1 / 2, 6)


def test_short_mirrors_long():
    # short entry 100, stop 110 -> risk 10. Favorable = price falling.
    candles = [_c(101, 95), _c(102, 80), _c(100, 90)]  # low 80 -> 20 favorable = 2R
    r = ex.compute_excursions(
        candles, entry_price=100, stop_loss=110, side="sell", exit_price=90
    )
    assert r["risk"] == 10.0
    assert r["mfe_r"] == 2.0  # entry 100 - low 80 = 20 = 2R
    assert r["mae_r"] == 0.2  # high 102 - 100 = 2 adverse
    assert r["realized_r"] == 1.0  # (100-90)/10 for a short
    assert r["giveback_r"] == 1.0


def test_trade_that_only_went_against_has_zero_mfe():
    # long, price never trades above entry -> MFE excursion is 0, not negative.
    candles = [_c(99, 95), _c(98, 92)]
    r = ex.compute_excursions(
        candles, entry_price=100, stop_loss=90, side="buy", exit_price=93
    )
    assert r["mfe"] == 0.0 and r["mfe_r"] == 0.0
    assert r["mae"] == 8.0 and r["mae_r"] == 0.8  # low 92 -> 8 adverse
    assert r["realized_r"] == -0.7  # (93-100)/10
    assert r["giveback_r"] == 0.0  # mfe_r 0 < realized -0.7 -> max(0, ...) = 0
    assert r["capture_ratio"] is None  # mfe 0 -> undefined


def test_missing_stop_gives_price_excursions_but_no_r():
    candles = [_c(130, 98)]
    r = ex.compute_excursions(candles, entry_price=100, stop_loss=None, side="buy", exit_price=110)
    assert r["mfe"] == 30.0 and r["mae"] == 2.0  # price-unit excursions still computed
    assert r["risk"] is None
    assert r["mfe_r"] is None and r["realized_r"] is None


def test_zero_risk_stop_equals_entry_no_r():
    r = ex.compute_excursions([_c(130, 98)], entry_price=100, stop_loss=100, side="buy")
    assert r["risk"] is None
    assert r["mfe"] == 30.0
    assert r["mfe_r"] is None


def test_no_exit_price_leaves_realized_none_but_mfe_present():
    r = ex.compute_excursions([_c(130, 98)], entry_price=100, stop_loss=90, side="buy")
    assert r["mfe_r"] == 3.0
    assert r["realized_r"] is None
    assert r["giveback_r"] is None
    assert r["capture_ratio"] is None


def test_empty_candles_is_none_filled_not_a_crash():
    r = ex.compute_excursions([], entry_price=100, stop_loss=90, side="buy", exit_price=110)
    assert r["bars_held"] == 0
    assert r["mfe"] is None and r["mae"] is None
    assert "no usable candles" in r["note"]


def test_unresolved_side_or_entry_is_noted():
    assert "unresolved side" in ex.compute_excursions([_c(1, 1)], entry_price=100, stop_loss=90, side="???")["note"]
    assert "missing entry" in ex.compute_excursions([_c(1, 1)], entry_price=None, stop_loss=90, side="buy")["note"]


def test_malformed_candles_are_skipped():
    candles = [{"high": None, "low": 1}, _c(130, 98), "not-a-candle", {"nope": 1}]
    r = ex.compute_excursions(candles, entry_price=100, stop_loss=90, side="buy")
    assert r["bars_held"] == 1  # only the one usable candle counted
    assert r["mfe_r"] == 3.0


def test_feature_names_are_outcomes():
    names = ex.excursion_feature_names()
    assert "mfe_r" in names and "giveback_r" in names and "capture_ratio" in names
