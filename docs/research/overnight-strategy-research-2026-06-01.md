# Overnight strategy research — 2026-06-01

**Operator ask (2026-05-31, end of S-PROFIT-GAPS session):** run a long autonomous
research session to find good strategy leads on BTC / MES (or other ideas);
formulate strategies, do variation backtesting, have results by morning.

**Method (the bar that cleared today):** formulate hypotheses → param-sweep
variations across timeframes → **net-of-fee (7.5 bps round-trip)** →
**walk-forward** (in-sample 2021–2023, out-of-sample 2024–2026; SPX/MES IS from
2020/2025) → keep only configs **net-positive in BOTH windows** → rank by
out-of-sample net R → flag the robust ones. Everything is autonomous via the
trainer-VM diag relay; all raw results in `/tmp/research/results.jsonl` on the VM.

**Markets / data (trainer VM):**
- BTC — `market_raw/BTCUSDT/5m/v002` (525,888 bars, 2021-05 → 2026-05), resampled.
- SPX — `data/SPX500_1m.parquet` (2.15M 1m bars, 2020 → 2026-05).
- MES — `market_raw/MES/5m/v001` (only 2025-01 → 2026-05; too short for a clean
  walk-forward, see the S-PROFIT-GAPS MES caveat — used for spot checks only).

**Harnesses swept (standalone, net-of-fee, shared JSON schema):**
`backtest_trend.py` (Donchian breakout), `backtest_pullback.py` (HTF-pullback
trend), `backtest_fade.py` (mean-reversion), `backtest_squeeze.py` (vol
breakout), and a new `research_momentum.py` (time-series momentum + MA-cross —
pure momentum entry, ATR-Chandelier trail exit).

---

## Headline leads
<!-- FILLED FROM THE SWEEP -->
_TBD — pending sweep completion._

## Full walk-forward leaderboard
<!-- FILLED FROM /tmp/research/results.jsonl -->
_TBD._

## What did NOT work
_TBD._

## Honest caveats
- In-sample param selection over a grid carries overfitting risk on the
  *magnitude*; the walk-forward (separate OOS window) + cross-parameter
  consistency are the guards on the *sign* and rough size.
- Single fee assumption (7.5 bps round-trip); single market per result; R-based
  (risk-normalized) accounting, not $-with-slippage.
- A walk-forward pass is a *candidate*, not a deployable strategy — the next step
  for any lead is a finer multi-fold walk-forward, max-DD / return-correlation
  to the live roster, then `execution: shadow` (Tier-3, operator-gated).

## Recommended next actions
_TBD._
