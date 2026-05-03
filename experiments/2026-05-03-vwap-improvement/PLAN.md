# VWAP improvement run — 2026-05-03-vwap-improvement

**Workflow:** `docs/claude/training-improvement-workflow.md` (Stage 1 + Stage 2).
**Strategy under study:** `src/units/strategies/vwap.py` (BTCUSDT, 5m, `risk_pct=1.0`, `ENTRY_STD_THRESHOLD=1.0`).
**Operator-approved scope:** five hypotheses, all VWAP-only. Turtle Soup is out of scope this run.

## Stage 1 — current state

### Strategy as it stands today
- **Signal logic.** Symmetric mean-reversion: `side=buy` if price ≤ VWAP − 1σ, `side=sell` if price ≥ VWAP + 1σ.
- **VWAP definition.** Volume-weighted typical price (`(H+L+C)/3`) across **the entire candle window passed in** (not session-anchored). σ is the std-dev of typical price across the same window.
- **Entry / SL / TP.** `entry = last close`. `tp = vwap`. `sl = entry ± sl_std_mult × σ` (default `sl_std_mult = 1.0`). With deviation = 1σ at the threshold, this gives exactly 1:1 R/R at the boundary.
- **Monitor.** Break-even SL after 1R via `monitor_breakeven_sl` in `_base.py`. No partial scale-out, no trail, no time stop.
- **Filters.** None — no HTF gate, no session gate, no regime gate, no volume gate.

### Code observations driving hypotheses
1. **No HTF trend filter.** Symmetric mean-reversion fires equally in trends and ranges. In a strong trend, `tp = vwap` gets run over because the trade has no asymmetric reward and the SL invalidates quickly.
2. **Loose 1σ entry threshold.** High signal frequency, low per-trade quality. The right threshold likely depends on whether the regime is mean-reverting.
3. **No kill-zone filter.** Reversions outside the London/NY high-liquidity windows are noisier (ICT methodology, well-documented in equities literature).
4. **Window-based, not session-anchored, VWAP.** Institutional VWAP anchors to the session open. The current implementation drifts because the rolling window is arbitrary (whatever `lookback_bars` was passed). Anchored VWAP is the canonical reading traders watch.
5. **Locked ~1:1 R/R + no partial profit.** The mean-reversion thesis "price returns to VWAP" gives a clean partial-take target at VWAP touch, with the remainder available to ride further reversion past the mean.

### Recent bug-log signal (VWAP-relevant)
- **BUG-014** (S-015): VWAP timeframe precedence — operator already moved to 5m, signalling active tuning interest.
- **BUG-002** (S-014): vwap → bybit_2 only — strategy isolation is in place, so per-strategy iteration is safe.
- **BUG-033** (S-026): VWAP signal attribution — fixed; not relevant to this run.
- **BUG-034** (recurring-hardening 1): VWAP signals fired but routing was broken — fixed; not relevant to this run.

### Free-source research notes
- ICT methodology consistently emphasises kill-zone session filters and HTF bias confluence. Long-running mean-reversion vs. trend literature on equities (Lo / Ang & Bekaert) supports an HTF-trend gate on VWAP reversions.
- Anchored VWAP (Brian Shannon's canonical formulation) is widely used institutionally; UTC day-open is the natural anchor for crypto perpetuals (no formal session open).
- HuggingFace MCP `paper_search` for "VWAP mean reversion intraday with trend regime filter" surfaced mostly off-topic results; nothing recent enough to add a sixth hypothesis at hypothesis-test scope.
- **Paid sources (Bigdata.com)** intentionally **not used** per `training-improvement-workflow.md` § Stage 1.

## Stage 1 — hypothesis table

| # | Hypothesis | Why we think it helps | How we test it | Success metric |
|---|---|---|---|---|
| **H1** | **HTF trend filter (1h EMA-200)** — only long when 1h close < EMA-200, only short when 1h close > EMA-200. | Symmetric reversion gets killed in trends. HTF-aligned reversion ("buy dips in uptrend, sell rips in downtrend") is a well-documented edge across asset classes. | 12mo BTCUSDT 5m, baseline (current symmetric) vs. variant (HTF-filtered). | Sharpe lift ≥ +0.3, max DD reduction ≥ 20%. |
| **H2** | **Entry threshold sweep** — `ENTRY_STD_THRESHOLD ∈ {1.0, 1.5, 2.0, 2.5}`. | 1σ is loose; expect lots of false reversions. Find the sweet spot between signal frequency and quality. | Same data, four threshold values. | Highest Sharpe at the chosen threshold, with ≥ 30 trades/quarter (avoid overfitting to rare events). |
| **H3** | **Kill-zone session filter** (London 02-05 UTC + NY 13-16 UTC). | ICT methodology core principle; liquidity-rich windows produce cleaner reversions. Cheap to add — just a time mask. | Same signals, masked by hour. | Win-rate +5% AND trade-count drop ≤ 50% (filter shouldn't kill so many trades the edge disappears). |
| **H4** | **Session-anchored VWAP** — re-derive VWAP each UTC day, replacing the rolling-window VWAP. | The current window VWAP is whatever `lookback_bars` the caller passes. Anchored VWAP is the canonical reading institutions trade off — closer to the actual mean the market gravitates to. | Replace `compute_vwap` window with anchored-from-UTC-midnight cumulative VPxV/V. | Sharpe lift ≥ +0.2 vs. window-VWAP baseline. |
| **H5** | **Partial scale-out at VWAP + trail to opposite 1σ band.** | "Price returns to VWAP" gives a clean half-take at VWAP touch. The remaining 50% can ride to the *opposite* 1σ band — capturing more of the mean-reversion overshoot when it materialises. | Replace single-TP exit with two-stage: take 50% at VWAP, move SL to BE, trail remainder; close at opposite 1σ band or timeout. | Expectancy +20% (in R), MFE-capture ratio +30% vs. baseline. |

Rank order: **H1** (highest expected impact, modest cost) → **H2** (cheap mechanical sweep) → **H3** (cheap broadly-applicable filter) → **H4** (structural correctness — re-bases the whole strategy) → **H5** (most code change, but highest tail-capture upside).

## Datasets

- **BTCUSDT 5m**, last 365 days — strategy timeframe.
- **BTCUSDT 1h**, last 365 days — for H1's EMA-200 trend regime.

Both via `scripts/training/data_loader.py` (yfinance → Coinbase public → Bybit public fallback chain). No Binance per `docs/claude/testing-policy.md`. No paid sources per `training-improvement-workflow.md`.

Cached under `experiments/2026-05-03-vwap-improvement/results/_cache/` so re-runs / Stage-4 review don't re-download.

## Compute budget

- `MAX_HOURS = 5.5` (the orchestrator's default; Action timeout is 5h50m).
- Per-hypothesis estimate (5m bars × 365 days ≈ 105k bars):
  - H1: ≤ 30 min (one filter pass over baseline).
  - H2: ≤ 40 min (4 threshold sweeps).
  - H3: ≤ 30 min (one mask).
  - H4: ≤ 40 min (re-anchored VWAP recompute per session).
  - H5: ≤ 60 min (more complex exit logic).
- Total comfortably under the 5.5 h budget; per-hypothesis checkpointing via `run_experiment.py` covers premature termination.

## What success looks like (decision rules for Stage 4)

- **Adopt** if the success metric in the table is met AND no other metric regresses by > 15%.
- **Reject** if the success metric is missed OR drawdown worsens by > 15%.
- **Needs more data** if trade count < 30/quarter (H2 only) OR the variant outperforms baseline only on a single quarter (look for time-windowed consistency in the per-hypothesis `summary.md`).

## Out of scope

- Turtle Soup changes (separate sprint, deferred).
- ML-augmented variants (xLSTM-TS / wavelet denoising — flagged for future "ML strategy" sprint).
- Multi-symbol portfolio rules.
- On-chain / whale-data regime detection.
- Live A/B (this is backtest-only; live validation is a separate decision after Stage 4 approval).

## Cross-references

- `docs/claude/training-improvement-workflow.md` — the workflow this run follows.
- `docs/claude/testing-policy.md` — data-source rules (no Binance, no paid sources for training).
- `src/units/strategies/vwap.py` — the strategy under study.
- `scripts/training/run_experiment.py` — orchestrator the GitHub Action invokes.
- `scripts/training/{data_loader,backtest_helpers}.py` — shared helpers.
- `.github/workflows/training-run.yml` — Stage 3 runner.
- `experiments/2026-05-01-strategy-tuning-dryrun/PLAN.md` — prior dry-run plan that overlapped on H1/H2/H3 conceptually but was never executed.
