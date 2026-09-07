# `data/ohlcv/` — fetched on demand, never committed

**This directory is deliberately empty in git.** `.gitignore` line 75 declares
`data/ohlcv/*.csv` ignored; the candle files that belong here are **fetched**,
used, and thrown away.

## What was here before, and why it is gone (MI-157, 2026-09-07)

Five constant-price **placeholder** files were force-added past that
`.gitignore` rule in `29014899` (*"feat(s006-m1): add ICT multi-symbol validate
manifest and placeholder OHLCV data"*) so that the paths named in
`data/ict_validate_manifest.csv` would resolve:

| file | rows | distinct close | non-zero returns |
|---|---|---|---|
| `btc_5m_2026.csv` | 300 | **1** (95000.0) | **0 of 299** |
| `eth_5m_2026.csv` | 300 | **1** (3200.0) | **0 of 299** |
| `qqq_15m_2026.csv` | 300 | **1** (490.0) | **0 of 299** |
| `spy_15m_2026.csv` | 300 | **1** (580.0) | **0 of 299** |
| `spy_5m_2026.csv` | 300 | **1** (580.0) | **0 of 299** |

`open`, `high` and `low` were constant too — the entire OHLC bar, not just the
close. **A backtest over a series that does not move produces confident numbers
that mean nothing**: every return is exactly 0.0, so expectancy, correlation and
rho are *undefined* rather than merely weak.

The defect was never that placeholders existed — it was that **nothing at the
call site distinguished them from a real series.** The placeholder-ness lived
only in that commit message. `tests/test_backtest_ict_cli.py` ran the full ICT
backtest across all four manifest pairs and asserted it succeeded, and it passed
— green over a flat line, for months. That is the class this repo has already
paid for once: the phantom −$6,358 "exit leak" that did not exist
(`docs/sprint-logs/S-PROVENANCE-EXITLEAK-ROOTCAUSE-2026-07-30.md`).

They were **deleted rather than replaced** because `.gitignore` already says
they do not belong in git; because the one designed workflow that reads them
(`notebooks/ict_multi_symbol_backtest.ipynb`, Cell 4) *fetches over them* before
Cell 5 backtests anything, so they served only to make a path exist; and because
replacing some of them with real history would leave a mixed population you
cannot tell apart without measuring — which is the original defect with better
numbers.

## How to populate it

The manifest `data/ict_validate_manifest.csv` stays: it is the declaration of
*which* symbol/timeframe pairs to validate, and it is not itself candle data.

```bash
# crypto (BTCUSDT, ETHUSDT) — data.binance.vision, no key needed
BACKTEST_DATA_PATH=data/ohlcv/btc_5m_2026.csv \
  python3 scripts/ops/fetch_backtest_candles.py \
    --symbol BTCUSDT --timeframe 5m --source binance_vision

# equities (SPY, QQQ) — yfinance
BACKTEST_DATA_PATH=data/ohlcv/spy_15m_2026.csv \
  python3 scripts/ops/fetch_backtest_candles.py \
    --symbol SPY --timeframe 15m --source yfinance
```

Or run `notebooks/ict_multi_symbol_backtest.ipynb` Cell 4, which fetches every
manifest pair to its declared path.

## What now happens if you don't

Both candle readers refuse a flat series at the read, and a missing file is a
named per-pair error rather than a silent zero-trade "success":

- `scripts/candle_io.py::load_candles` → `assert_non_degenerate`
- `bin/backtest_ict.py::_load_ohlcv`

Both import the single grader `scripts/candle_variance.py::grade_close_variance`
rather than re-deriving the rule, and `scripts/ci/check_candle_fixture_variance.py`
fails CI if a degenerate candle file is ever committed again.
