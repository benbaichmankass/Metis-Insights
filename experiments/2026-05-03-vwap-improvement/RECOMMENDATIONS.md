# VWAP improvement — recommendations (Stage 4 writeup)

**Run id:** `2026-05-03-vwap-improvement`
**Plan PR:** #348 (`TRAINING-PLAN: 2026-05-03-vwap-improvement`)
**Results PR:** #349 (`TRAINING-RESULTS: 2026-05-03-vwap-improvement`)
**Strategy under review:** `src/units/strategies/vwap.py` (BTCUSDT, 5m).

---

## Executive summary

The current production VWAP is **unprofitable** as it stands today: over 365 days of BTCUSDT 5-minute bars it produces **946 trades**, **negative expectancy** (-0.002 R per trade), **negative Sharpe** (-0.12), and a **-21.2 R max drawdown**. The strategy's win-rate (65%) is misleading because the 1.0σ entry threshold lets it take far too many low-quality reversion setups against trends.

One single-line config change — **raising `ENTRY_STD_THRESHOLD` from 1.0σ to 2.0σ** — flips the strategy from losing to strongly profitable: **Sharpe 1.71**, **expectancy +0.044 R per trade**, **max DD reduced 4×** (from -21.2 R to -5.2 R), with 336 trades over the year (still ~1 per day, plenty of sample). This is the headline recommendation.

The remaining four hypotheses we tested either need more work before adoption (H3, H4, H5) or did not justify standalone adoption (H1). They are recommended as **follow-up runs**, not blockers.

---

## Per-hypothesis decisions

| # | Hypothesis | Result | Decision |
|---|---|---|---|
| **H2** | Entry threshold sweep `{1.0, 1.5, 2.0, 2.5}σ` | Best at **2.0σ** — Sharpe **1.71** vs baseline -0.12; expectancy **+0.044R**; 336 trades; max DD **-5.2R** vs baseline -21.2R. Monotonic improvement up to 2.0σ. | **ADOPT** |
| **H1** | HTF trend filter (1h EMA-200 alignment) | Sharpe lift +0.35 (0.23 vs -0.12) — *just* hits the +0.3 target. Expectancy barely positive (+0.004R), win-rate drops 1.2pts, drawdown halved. | **REJECT as standalone** — re-test as additive to H2 |
| **H3** | Kill-zone session filter (London 02-05 + NY 13-16 UTC) | Sharpe 1.17 looks great, but trade count drops **71%** (fails ≤ 50% guardrail). Filter is too narrow. | **NEEDS MORE DATA** — re-run with wider sessions |
| **H4** | Session-anchored VWAP (UTC day-open) | **Crashed** on a `pd.NA` → `astype(float)` bug in the hypothesis module. No metric produced. | **NEEDS MORE DATA** — fix the bug, re-run |
| **H5** | Partial scale-out at VWAP + trail to opposite 1σ band | Aggregate Sharpe lift +0.94 looks promising, but the metric is **misleading**: `simple_backtest` reports the final-leg r_mult only; the partial-take leg is not folded in. The 65% → 37% win-rate drop is an artifact of this. | **NEEDS MORE DATA** — needs blended-leg backtest helper before judging |

---

## Proposed strategy-level change (trader language, no code)

**One change. Rule:** "Only take a VWAP mean-reversion trade when price is at least **2 standard deviations** away from VWAP — not 1 like today."

**What it does on the live tape:**

- The strategy fires far less often (~1 trade per day on BTCUSDT instead of ~2–3).
- Every trade it does take has a much higher quality bar — 2σ deviations only happen during sharp dislocations, not routine noise around the mean.
- The reward-to-risk math improves at the boundary: at the entry the deviation is 2σ, the SL is 1σ further out, so R:R at entry is 2:1 vs. today's 1:1.
- The strategy stops bleeding equity in normal conditions: the 21-R max drawdown collapses to ~5R because the noise trades that produced most of those losses no longer fire.

**Live rollout trade-off:** trade frequency drops by ~65% (from 946 to 336 over a year). If operator wants more bot activity, the answer is *not* to soften the threshold — it's to layer in additional strategies (or run the same VWAP rule on more symbols).

---

## Why we should make this change

1. **Empirical.** The threshold sweep shows monotonic Sharpe improvement from 1.0σ (-0.12) → 1.5σ (0.79) → 2.0σ (1.71), with a small regression at 2.5σ (1.42, only 177 trades). 2.0σ is a clean, non-overfit local maximum.
2. **Mechanical.** A 1σ deviation in the typical-price window is, by construction, a one-standard-deviation move — i.e. the kind of move that happens routinely. Calling that a "mean-reversion setup" treats normal noise as a tradable edge. 2σ deviations are roughly tail events; reversion is more likely to materialise.
3. **Risk-asymmetric.** At the 1σ threshold the trade carries 1:1 R:R at entry, which means a 50% win-rate is required just to break even. At 2.0σ the entry R:R is ~2:1, so a 33% win-rate breaks even — and the realised win-rate (62%) is well above that.
4. **Drawdown.** Cutting max DD from -21R to -5R has compounding effects on per-account `RiskManager` budgeting (daily loss caps activate less often) and on operator nerves.

---

## Expected impact on live

- **Trade frequency:** ~336/year on BTCUSDT 5m, vs. ~946/year today. Roughly 1 trade/day.
- **Per-trade expectancy:** **+0.044 R**, vs. ~zero today. With the per-account `risk_pct = 1.0%` config in `config/strategies.yaml`, that's roughly **+4.4 bps of equity per trade in expectation**.
- **Annualised expectancy:** ~**+15% on capital** (336 × 0.044 × 1.0%), before fees/funding. Real number after costs is lower; we'd want a live A/B before claiming this.
- **Max drawdown:** roughly **4× smaller** in R-units. In equity terms, the worst drawdown shrinks proportionally.
- **Sharpe on the backtest sample:** **1.71** (was -0.12). Backtest Sharpe overstates live; expect 0.7–1.2 realised after slippage and capital efficiency.

---

## Risks / what could go wrong post-deploy

1. **Sample horizon.** 365 days of 5m BTCUSDT covers one drawdown phase (mid-2025 chop) and one trending phase (early 2026). If the next year is dominated by a regime we haven't seen — sustained one-way trend with no 2σ pullbacks — the strategy fires very rarely or gets run over by the trend. The H1 (HTF filter) result hints at this, even though we didn't adopt H1 today.
2. **Selection bias on the threshold.** We swept `{1.0, 1.5, 2.0, 2.5}` and picked the winner. Walk-forward validation on rolling out-of-sample windows would be more robust. Within scope of the next training run.
3. **Backtest assumes first-touch fills** with no slippage, no funding costs, no exchange-side rejection. Realised Sharpe will be lower; be prepared for the live numbers to be ~50–70% of backtest.
4. **No regime detection.** If volatility regime shifts (low-vol drift vs high-vol whipsaw), the σ definition itself changes — the 2σ threshold will fire more or less often than the backtest implies. Acceptable risk; can be addressed by H4 (anchored VWAP, which inherently rebases σ each session) once that hypothesis is fixed and re-run.
5. **Live A/B still required before scale-up.** Recommendation: deploy at the existing `risk_pct = 1.0%` for one month, monitor the hourly report's per-strategy block, and only scale up after the live numbers track backtest within tolerance.

---

## Follow-up sprints (if approved)

In rough priority order, **after** the IMPLEMENT PR ships:

1. **Fix H4 + re-run anchored VWAP.** One-line bug fix in the hypotheses module (`replace(0, pd.NA)` → `.where(... > 0)` mask), then re-dispatch the GitHub Action. Anchored VWAP is the canonical institutional reading and may stack additively on top of H2.
2. **H1 + H2 stacked test.** HTF trend filter on top of the 2.0σ threshold. If the +0.35 Sharpe lift from H1 carries over, the combined strategy could push toward a 2.0+ Sharpe.
3. **H3 with wider sessions.** Re-test the kill-zone filter with extended windows (02-08 UTC + 13-19 UTC, i.e. full London/NY sessions instead of just the kill-zones). Goal: keep the Sharpe lift while satisfying the ≤ 50% trade-drop guardrail.
4. **H5 with `multi_leg_backtest`.** Add a blended-leg helper to `scripts/training/backtest_helpers.py` that aggregates partial + remainder r_mults natively, then re-evaluate the partial-scale-out hypothesis against an honest blended metric.
5. **Workflow infra:** investigate why the `paths:` filter on `training-run.yml` did not auto-trigger on our push, and why `gh pr create` silently failed at the end of the manual dispatch (#349 had to be opened by hand). Both broke the autonomous Stage 3 → Stage 4 handoff.

---

## Implementation note (for the IMPLEMENT PR, after approval)

The actual code change is a single line in `src/units/strategies/vwap.py`:

```
# before
ENTRY_STD_THRESHOLD = 1.0
# after
ENTRY_STD_THRESHOLD = 2.0
```

Plus an update to `config/strategies.yaml` if the operator prefers exposing this as a per-strategy config knob:

```
vwap:
  ...
  entry_std_threshold: 2.0   # was implicitly 1.0 via module default
```

The IMPLEMENT PR would also:
- Update tests pinning the threshold-derived signal behaviour (`tests/test_*vwap*` — TBD which exact files).
- Add a one-line note to `docs/claude/bug-log.md` linking the 1.0σ default → unprofitable backtest finding (BUG-035 or whatever the next id is) so the architectural lesson is captured.

The IMPLEMENT PR triggers the existing PM-review gate per `CLAUDE.md` § Merging Rules (touches `src/units/strategies/`).

---

## Cross-references

- `docs/claude/training-improvement-workflow.md` — Stage 4 spec.
- `experiments/2026-05-03-vwap-improvement/PLAN.md` — original hypothesis table.
- `experiments/2026-05-03-vwap-improvement/results/SUMMARY.md` — Action-generated aggregate.
- `experiments/2026-05-03-vwap-improvement/results/H{1..5}/` — per-hypothesis metrics + summaries.
- PR #348 — TRAINING-PLAN.
- PR #349 — TRAINING-RESULTS.
