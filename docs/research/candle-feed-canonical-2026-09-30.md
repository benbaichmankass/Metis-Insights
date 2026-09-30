# Canonical candle feed for the trainer's `data/<SYM>_15m.csv` (2026-09-30)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Decision: Bybit linear perp klines are the canonical feed** for the trainer's
multi-year 15m files. Refresh EXTENDS them from Bybit; Binance USD-M is not a
fallback.

## Measurement (population: trainer `data/{SOL,ETH,XRP}USDT_15m.csv` vs the staged
Binance files in `/home/ubuntu/refresh_15m_20260929` and a fresh Bybit pull of the
last 1,000 old bars; trainer diag #14515, run 36671721567)

| symbol | old vs Binance (full overlap) | Bybit vs old (last 1000 bars) | Bybit vs Binance (same 1000) |
|---|---|---|---|
| SOL | n=163,419, median 1.574 bps, p99 10.5, max 1974.6, vol ratio 2.63, 480 old-only bars, 2 Binance gaps | median 0.000, max 11.2, vol ratio 1.000 | median 1.402, max 6.2, vol ratio 2.61 |
| ETH | n=184,442, median 1.208 bps, p99 9.05, max 648.3, vol ratio 4.46, 0 gaps | median 0.000, max 11.3, vol ratio 1.000 | median 0.538, max 6.9, vol ratio 3.79 |
| XRP | n=178,742, median 0.000, max 12.0, vol ratio 1.000 | median 0.000, max 12.0, vol ratio 1.000 | 0.000 (staged XRP is itself Bybit-derived) |

Reading: the existing files ARE Bybit (median 0.000 bps, volume ratio 1.000).
Binance USD-M differs by ~0.5-1.6 bps median with 2.6-4.5x volume, so appending
it would put two feeds in one file. The trainer reaches Bybit's public REST
(unlike GitHub runners), so the same-venue refresh is available.

Caveats stated, not hidden: the Bybit-vs-old check covers only the last 1,000
bars per symbol (not the full history); max diffs of 11-12 bps on isolated bars
exist even Bybit-vs-old (likely revised bars).

## Also true
- `scripts/ops/fetch_backtest_corpus.py` uses `binance_vision` for crypto with a 90-day
  15m window: a different population under the same filenames. It now refuses to
  shrink an existing longer file (`fetch_backtest_candles.py::shrink_refusal`).
- Refresh: `scripts/ops/refresh_candle_history.py` (extend-only, dedupe on timestamp).
