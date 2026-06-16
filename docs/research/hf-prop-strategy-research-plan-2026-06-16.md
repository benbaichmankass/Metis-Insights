# Research plan — a high-frequency strategy that can pass Breakout *fast + safe*

**Opened:** 2026-06-16 · **Status:** active research (Tier-1 — research harness
only; nothing here touches `config/` or the live order path) · **Owner:** Claude

## Why this effort exists

The prop-firm POC this session
(`runtime_logs/prop_eval/2026-06-16/` and `…/2026-06-16-expanded/`) settled one
question precisely:

> A **fast** Breakout 1-Step pass (median days-to-pass ≤ ~60 **and**
> P(survive 6 mo) ≥ 95%) is **not available from the current roster**, and the
> only dense candidate already in the bot — `ict_scalp_5m` — makes it *strictly
> worse*, because its edge on the 2023→2026 BTC 5m feed is **negative**
> (474 trades, **36.9% WR @ 1.5R → −93% solo**; every combo it touches breaches
> at ~100%).

The fastest *durable* pass that exists is `fvg_range_15m @ risk 1.0` at a ~249-day
median (96% 6 mo survival) — ~4× the "fast" bar. So the missing piece is concrete
and falsifiable:

> **We need a higher-frequency strategy that is *also net-profitable* on this
> regime.** Frequency alone is not the problem (ict_scalp proves we can generate
> 887 signals / 474 trades); a **positive edge at that frequency** is.

This doc is the plan to find one — or to prove, with the same rigor, that one
isn't reachable on BTC and we should pivot venue/timeframe.

## The quantified target (the gate every candidate must clear)

Account **$5,000**, ruleset `config/prop_rulesets/breakout.yaml` (1-Step Classic:
**+10% target / 3% daily-loss / 6% STATIC off-start max-DD / 30-day funded soak**).

The binding constraint is the **6% off-start floor** — it sits *closer* (−6%) than
the target (+10%), so the strategy must climb +10% while keeping any running
off-start drawdown < 6% on ≥95% of paths. That demands positive expectancy with a
high return-per-unit-time (Sharpe-like) *and* per-trade risk small enough that
loss clusters stay under the floor.

### Edge requirement (back-of-envelope; the MC rig is the real gate)

Let `r` = per-trade risk (fraction of equity), `f` = trades/day, `D` = days to
target, and per-trade expectancy in R-units `E_R = W·R − (1−W)` (W = win rate,
R = reward:risk). To mark +10% in `D` days: `f·D·r·E_R ≥ 0.10`.

At a representative **r = 0.5%, f = 4 trades/day, D = 45 days**:

```
4 · 45 · 0.005 · E_R ≥ 0.10   →   0.9·E_R ≥ 0.10   →   E_R ≥ ~0.11 R/trade
```

So the design bar is **≥ ~0.11 R/trade net of fees at ~3–5 trades/day**. In
(W, R) terms, any of:

| Style | Win rate | Reward:risk | E_R |
|---|---|---|---|
| balanced | ≥ 45% | 1.5 | +0.125 |
| frequent | ≥ 50% | 1.2 | +0.10 |
| mean-revert | ≥ 55% | 1.0 | +0.10 |

For contrast, `ict_scalp_5m` is `0.37·1.5 − 0.63 = −0.075 R`. **The gap to close
is ~0.19 R/trade** — i.e. lift WR from 37% to ≥45% at 1.5R (or raise R, or trade a
more selective, higher-quality subset).

Worst-case streak check: at `r = 0.5%`, `⌊6%/0.5%⌋ = 12` consecutive losses
breach the floor — so per-trade risk stays small and **the Monte-Carlo
first-passage verdict (`scripts/prop/montecarlo_prop.py`), not the point
backtest, is the pass/fail authority.**

## Candidate families (explore in this order)

**A. Selective displacement-continuation ("ict_scalp, fixed") — highest-leverage.**
Directly attack the −0.075R. Keep ict_scalp's dense sweep→displacement→FVG→
mitigation geometry but prune to the profitable subset: hard-gate on HTF (1h/4h)
trend alignment, restrict to London/NY killzones, require a minimum displacement
strength (ATR-relative), and use ATR-scaled SL/TP instead of a fixed 1.5R. Goal:
WR 37% → ≥45% even at the cost of trade count (a profitable 150-trade subset beats
a losing 474-trade book). Reuses geometry the signal builder already records.

**B. VWAP / band mean-reversion — orthogonal edge.** Fade stretched 5m excursions
back to session VWAP / a band in *ranging* regimes (naturally high WR ~55–60% at
R~1.0). Diversifies away from the roster's all-directional trend/breakout edge —
which is *why* it might survive when the trend members are flat.

**C. Killzone opening-range breakout (intraday).** London/NY killzone OR breakout
on 5m, tight stop, momentum target. ICT-flavored, reuses killzone infra; HF by
construction.

**D. Regime-gated HF ensemble.** Only fire an HF setup in the regime where it's
profitable, using the existing regime heads — convert a marginal all-weather edge
into a strong in-regime one.

## Evaluation loop (with anti-overfit discipline — non-negotiable)

The whole reason ict_scalp's failure is trustworthy is that it was the *real*
`order_package`/`monitor` run through the same engine, not a bespoke sim. New
candidates follow the same path:

1. **Implement** the candidate as a research strategy module exposing the engine
   contract `order_package(cfg, candles_df)` + `monitor(cfg, candles_df, open_pkg)`
   (the `scripts/backtest_system.py::ROSTER` shape) — under `src/units/strategies/`
   or a research path, **not** added to `config/strategies.yaml` (that's the
   Tier-3 live wire, gated on a clean OOS pass).
2. **Split the feed** `~/ict-trader-data/btc_5m.parquet` (2023-01→2026-02):
   **IS = 2023-01 → 2025-02** (design/tune), **OOS = 2025-02 → 2026-02** (holdout,
   untouched until the config is frozen). Walk-forward where a param sweep is used.
3. **Tune on IS only** — coarse grids, low param count, prefer robustness plateaus
   over single-cell peaks. Freeze the config.
4. **Solo backtest** (`backtest_system.py --roster <cand> --risk-pct 0.5`): confirm
   solo `E_R ≥ ~0.11`, the WR/R/frequency targets above, and a max-DD that leaves
   room under 6%.
5. **Prop gate on OOS** — `scripts/prop/evaluate_prop.py` matrix +
   `scripts/prop/montecarlo_prop.py` over the held-out year: a candidate
   "passes" only if a sized cell clears **median days-to-pass ≤ 60 AND
   P(survive 6 mo) ≥ 95%**, both solo and combined with the clean incumbents
   (`fvg_range_15m`, `squeeze_breakout_4h`) — it must *help*, not poison (the
   netted-book test ict_scalp failed).
6. **Decision:** clears OOS → propose live wiring via the `new-strategy` skill
   (Tier-3, operator-gated). Fails → shelve with an honest negative-result note
   under `runtime_logs/prop_eval/` + a one-line `performance-review-backlog`
   entry, exactly as ict_scalp was handled (`PB-20260616-002`).

## Honest priors

Finding a genuinely +0.11R/trade HF edge on liquid BTC is **hard** — that
frequency competes with the most efficient corner of the market, and an
overfit-to-IS curve that dies OOS is the default failure mode (which is why steps
2/5 are strict). A clean *negative* result ("no HF edge survives OOS on BTC 5m →
pivot to a slower durable pass, or a different venue/timeframe") is a fully valid,
publishable outcome of this effort, not a failure of it.
