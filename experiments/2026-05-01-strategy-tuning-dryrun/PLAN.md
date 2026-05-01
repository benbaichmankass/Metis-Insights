# Training/improvement run 2026-05-01-strategy-tuning-dryrun

**Status:** dry-run validation of `docs/claude/training-improvement-workflow.md`. The hypotheses below are real and worth testing — but the operator should approve before this notebook is actually executed in Colab.

## Stage 1 — current state

### Strategies under study
- `src/units/strategies/turtle_soup.py` — sweep + reversal at 15m (BTCUSDT, ETHUSDT, `risk_pct=0.5`).
- `src/units/strategies/vwap.py` — symmetric mean-reversion on BTCUSDT at 5m (`risk_pct=1.0`, `ENTRY_STD_THRESHOLD=1.0`).

### Code observations driving hypotheses
1. **Turtle Soup adapter dropped the legacy 1m entry confirmation.** The legacy `TurtleSoupMTFv1` was multi-TF (15m setup → 1m trigger). The current adapter operates on the 15m close only — entry is `last_close`, no intra-bar confirmation. (See module docstring lines 18-31 of `turtle_soup.py`.)
2. **Turtle Soup ignores its own scale-out / trailing params.** `partial_close_pct: 0.25`, `trail_atr_mult: 1.2`, `be_at_r: 0.75`, `tp2_at_r: 3.0` are declared in `config/strategies.yaml` but the adapter only uses `tp1_at_r=1.25` (tp2 is carried in `meta` for downstream consumers that don't exist). The strategy throws away ~70% of its expected upside on winners.
3. **VWAP has no trend filter.** Symmetric mean-reversion fires equally in trending and ranging tape. In a strong trend the trade gets run over because TP = current VWAP and SL = `entry + (entry - VWAP)` — i.e. ~1:1 R:R with no asymmetric reward.
4. **VWAP entry threshold is loose** (`ENTRY_STD_THRESHOLD=1.0` std-dev). High signal frequency, low per-trade quality.
5. **Neither strategy uses an ICT session filter** (London 02-05 UTC, NY 13-16 UTC kill zones). Sweeps and VWAP reversions outside high-liquidity windows are noisier.

### Recent bug-log signal
- BUG-014 (S-015): VWAP timeframe precedence bug. Operator already moved to 5m, suggesting active tuning interest in this strategy.
- BUG-002 (S-014): turtle_soup → bybit_1, vwap → bybit_2. Per-strategy isolation means we can iterate independently.

### Free-source research (HuggingFace MCP + web)
- `paper_search("VWAP mean reversion intraday with trend regime filter")` — most results were off-topic (solar wind, weather), but one relevant: ["An Evaluation of Deep Learning Models for Stock Market Trend Prediction" (2024)](https://hf.co/papers/2408.12408) — xLSTM-TS with wavelet denoising as a candidate trend filter. **Out of scope for this run** (too heavy for hypothesis-test scope) but flagged for a future "ML-augmented strategy" sprint.
- `paper_search("liquidity sweep stop hunt reversal crypto trading strategy")` — most relevant: ["Forecasting Bitcoin volatility spikes from whale transactions" (2022)](https://hf.co/papers/2211.08281) suggests whale/CryptoQuant data improves drawdown — would inform a future regime-aware version of turtle_soup. **Out of scope** for this run.
- Web/general knowledge: ICT methodology consistently emphasises kill-zone session filters and HTF bias confluence. The ~30 yrs of mean-reversion vs. trend literature on equities (e.g. Lo / Ang & Bekaert) supports an HTF-trend gate on VWAP reversions.

## Stage 1 — hypothesis table (3-5, ranked by expected impact / cost)

| # | Hypothesis | Why we think it helps | How we test it | Success metric |
|---|---|---|---|---|
| **H1** | **Turtle Soup: scale out 25% at 1R, trail remainder to 3R** (wire up the params already in YAML) | Adapter currently exits 100% at 1.25R, throwing away the right tail. The legacy class scaled out and trailed — measured better expectancy. Lowest-risk change because params already exist. | Run 12 months of BTCUSDT 15m + 1m; compare exit-at-1.25R baseline vs. scale-out+trail variant on the same setups. | Expectancy (E[R] per setup) +20% with no more than +5% drawdown. |
| **H2** | **VWAP: add HTF trend filter** (only short above 1h-EMA-200, only long below) | Symmetric reversion gets killed in trends. HTF-aligned reversion ("buy dips in uptrend") is a well-documented edge. | Run 12 months of BTCUSDT 5m. Baseline = current symmetric. Variant = require HTF alignment. | Sharpe lift ≥ +0.3, max drawdown reduction ≥ 20%. |
| **H3** | **VWAP: sweep ENTRY_STD_THRESHOLD ∈ {1.0, 1.5, 2.0, 2.5}** | Threshold of 1.0 std-dev is loose; expect lots of false reversions. Find the sweet spot. | Same backtest, 4 threshold values. | Per-threshold table; pick the one with highest sharpe AND ≥ 30 trades / quarter (avoid overfit on rare events). |
| **H4** | **Both strategies: kill-zone session filter** (only trade London 02-05 UTC + NY 13-16 UTC) | ICT methodology core principle; liquidity-rich windows produce cleaner sweeps and reversions. Cheap to add (just a time mask). | Backtest with and without filter on the **same** signals. | Win-rate lift ≥ +5%; trade count drop ≤ 50% (the filter shouldn't kill so many trades the strategy stops being profitable). |
| **H5** | **Turtle Soup: restore 1m entry confirmation** | The single-frame simplification was an explicit shortcut; the legacy class confirmed entries on a 1m trigger after the 15m setup. Hypothesis: 1m confirmation reduces bad-fill drawdown. | Replicate `max_entry_wait_bars_1m: 20` from YAML. Backtest needs 1m candles for the entry window — most expensive of the five tests because of data volume. | Drawdown reduction ≥ 15% with no more than 30% trade-count drop. |

Rank order: H1 (cheapest, most leverage on existing infra) → H2 (clear theoretical basis) → H3 (mechanical sweep) → H4 (cheap, broadly applicable) → H5 (most expensive — 1m data required).

## Datasets

- BTCUSDT 5m + 15m + 1h, last 365 days. **Source: HuggingFace dataset under our org** (no Binance per `testing-policy.md`). Specific dataset name is filled in by the notebook; if absent the notebook falls back to Bybit public REST + caches into `experiments/<run-id>/results/_cache/`.
- BTCUSDT 1m only for H5 (loaded lazily; if data fetch fails, H5 is skipped with a logged note).
- ETHUSDT 15m for H1 cross-symbol validation (one-shot, not a separate hypothesis).

## Compute budget

- `MAX_HOURS = 6` per the notebook template default.
- Per-hypothesis: ≤ 60 min compute; H1 + H4 ≤ 30 min each.
- Drive checkpointing per-hypothesis so Colab disconnects don't lose work.

## What success looks like for each hypothesis

Per the table. The notebook's `SUMMARY.md` aggregates: hypothesis × baseline metric × variant metric × delta × pass/fail vs. success criterion.

## Out of scope

- ML-augmented strategies (deferred — separate sprint).
- Regime detection via on-chain / whale data (deferred).
- Strategy params not declared in `config/strategies.yaml` (e.g. multi-symbol portfolio rules).
- Live A/B (this is backtest-only; live validation is a separate decision after Stage 4 approval).

## Cross-references

- `docs/claude/training-improvement-workflow.md` — the workflow this run validates.
- `notebooks/training/2026-05-01-strategy-tuning-dryrun.ipynb` — the run notebook.
- `docs/claude/testing-policy.md` — data-source rules (no Binance).
