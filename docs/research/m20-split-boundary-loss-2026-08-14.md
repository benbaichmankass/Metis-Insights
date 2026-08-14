# The split margin, measured — boundary loss over the derived-split corpus

**Date:** 2026-08-14 · **Tier:** 1 (measurement over a committed corpus; no live
path, no default changed) · **Closes criterion (2) of**
`BL-20260814-SPLIT-TARGETS-EXACTLY-THE-FLOOR-SO-BOUNDARY-LOSS-ALWAYS-FAILS`.

## The question

`m20_fleet_exit_sweep.derive_split` places the IS/OOS boundary at
`stamps[-target_oos][:10]` — the entry date of the `target_oos`-th trade from
the end. The harness then windows **candles**, not trades, so the OOS run needs
warmup and produces slightly different trades near the boundary. Realized OOS
therefore differs from the target.

When the target IS the floor (`MIN_OOS_TRADES = 25`), **any** loss puts the leg
under the floor and it is refused as *"waiting for trades"* — which is how two
SHIPPED real-money cells with 407 and 527 lifetime trades got that message. The
designed fix is `max(derive(target = floor + margin), fixed_window)`. This memo
supplies the margin.

## Population — stated, because it is the whole caveat

`docs/research/m20-sweep-corpus.jsonl`, 922 rows. **18 carry
`split_target_oos`** (the rest predate derived splits and ran `split_mode=date`).

A boundary event is `(leg, split, target)` — **not a row**. Several lever cells
share one derivation, so counting rows would inflate n the same way
double-counting `_prop` legs inflated the symbol test earlier today. 18 rows →
**14 distinct events**; 3 are `leg_too_thin` fallbacks (no derivation happened,
excluded) → **11 derived events across 7 distinct legs.**

Seven legs is the honest independence denominator, and three of them
(`iwm`/`scha`/`splg` `_trend_long_1d`) are near-identical — same family, same
timeframe, lifetimes 64/65 — and behave identically at both targets. So this is
closer to **five** independent observations than eleven.

## The distribution

| statistic | value |
|---|---|
| n (events / distinct legs) | 11 / 7 |
| values | 0, 1, 1, 1, 1, 1, 2, 2, 4, 4, 4 |
| min / median / mean / **max** | 0 / 1 / 1.91 / **4** |
| losses (> 0) | 10 of 11 |
| **gains (< 0)** | **0** |

**Loss is never negative.** Worth stating because the opposite was plausible on
the code: `[:10]` truncates the boundary to a DATE, so windowing from midnight
should sweep in same-day trades that sit *before* the boundary trade and push
realized ABOVE target. It never does — warmup dominates, and the effect is
strictly one-directional. That is a refuted hypothesis, not an unexamined one.

## It tracks target/lifetime, not target

| leg | target | lifetime | target/life | loss |
|---|---|---|---|---|
| iwm_trend_long_1d | 35 | 64 | 0.55 | **4** |
| scha_trend_long_1d | 35 | 65 | 0.54 | **4** |
| splg_trend_long_1d | 35 | 65 | 0.54 | **4** |
| trend_donchian_eth_prop | 35 | 944 | 0.04 | 2 |
| tlt_pullback_1h | 45 | 527 | 0.09 | 2 |
| iwm / scha / splg | 28 | 64–65 | 0.43–0.44 | 1 |
| ict_scalp_eth_15m | 35 | 385 | 0.09 | 1 |
| trend_donchian_xrp_4h | 40 | 142 | 0.28 | 0 |

The same three legs lose **4** at ratio 0.55 and **1** at ratio 0.44. Target
alone does not predict it (35 spans losses 1→4); the ratio does. A boundary
pushed deep into a short history lands in sparser data, where warmup eats more.

**The three worst observations come from a regime the clamp now prevents.** The
clamp added 2026-08-14 caps the target at `lifetime // 2`, so a 64-trade leg can
no longer run at 35 (it clamps to 32) and the ratio cannot exceed 0.50. Every
observation at ratio ≤ 0.5 lost **≤ 2**.

## The answer: margin 5 → target 30

- Covers the observed max (4) with one spare.
- Covers it including the pre-clamp ratio-0.55 regime that can no longer occur —
  conservative in the right direction for a floor.
- Post-clamp the observed max is 2, so 5 carries better than 2× headroom.

**This CONFIRMS the illustrative value rather than replacing it.** The sprint log
proposed "a target of 30 clears 25 with room" as *"an illustration on n=4, not a
proposal"*. Measured on 11 events across 7 legs, target 30 is the same number
with a distribution behind it.

I record that plainly because I nearly wrote the opposite. Mid-analysis I built a
tidy story that the illustrative 30 came from mis-counting the three
`leg_too_thin` rows, whose apparent "loss" is 30/31 — a striking numeric match.
It is a coincidence of magnitude: 30 was the **target** (floor 25 + margin 5),
never a margin, and those fallback rows are the fixed-date cliff the clamp
already fixes, not boundary loss. The match survived only until I read the source
line. A confirmation is a duller result than a correction, and that is exactly
when the pull toward the correction is worth distrusting.

## What this does NOT do

Does not change `--split-target-oos`'s default. Flipping it moves recorded
verdicts fleet-wide (`htf` 95→24, `tlt` 56→22, `mhg` 7→24 when measured both
ways), so the default is the operator's call, queued with this memo as its basis.

Does not claim 5 is optimal. It is the smallest value covering every observation
in a 7-leg sample. A leg at ratio > 0.5 cannot arise while the clamp holds; if
the clamp is ever relaxed, this margin needs re-measuring against that regime.

## Reproduce

Pure read of the committed corpus — no harness run, no VM:
`docs/research/m20-sweep-corpus.jsonl`, filter `split_target_oos is not None`,
group by `(leg, split, split_target_oos)`, drop rows carrying `split_fallback`,
compute `split_target_oos - base_trades_OOS`.
