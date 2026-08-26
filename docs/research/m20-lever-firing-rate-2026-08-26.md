# B2 — "the M20 levers fire 1.3%": correct-and-rare, and the framing is wrong twice

**Answer: NOT a wiring gap. The levers fire rarely because almost nothing
declares them** — 4 of 52 enabled legs — and two of the four lever families are
declared by **nobody**. The mechanism works; adoption is the variable.

Two separate corrections to how the figure has been quoted follow, because both
change what the number means.

## Where the figure came from

The workplan's B2 row says *"CLAUDE.md records 17 lever firings ever"*.
**`CLAUDE.md` contains no such figure.** The source is
`docs/sprint-logs/S-SYSTEM-REVIEW-STRUCTURAL-2026-08-24.md:70` —
`| M20 levers | 17 closes ever (1.3%) |` — whose denominator is the 1,324
closes given in the row above it. The wording also differs: **"17 closes"**, not
"17 lever firings". A citation fixed here, not a defect in the measurement.

## Correction 1 — the count is a count of TWO levers, structurally

M20 has four lever families. Only two can **ever** appear in `exit_reason`:

| lever | produces a close reason? | can appear in `exit_reason`? |
|---|---|---|
| `stale_stop` | `{"action":"close","reason":"stale_stop"}` | yes |
| `giveback_stop` | `{"action":"close","reason":"giveback_stop"}` | yes |
| `vol_trail` | **no close path at all** — `src/runtime/trail_vol.py` resolves a trailing multiplier and returns it | **never** |
| `be_touch_arm` | arms break-even; the exit books as a stop hit | **never** |

`vol_trail` and `be_touch_arm` change *geometry*; their effect is booked as `sl`
or `sl_cross`. So **any "the M20 levers fire X%" read off `exit_reason` is
measuring two of the four families**, and no amount of extra data changes that —
it is a property of where the label is written, not of the sample.

Verified by reading `src/runtime/trail_vol.py`: it defines `resolve_vol_trail_mult`
and helpers, and contains no `action: close` branch.

## Correction 2 — the rate reproduces, the count does not

Re-measured on the live journal 2026-08-26, **after** the G1 exit-label backfill.

**Population: `/api/bot/performance?window=all`, `perExitPath`. Real money
n=407 closes across 14 paths; paper n=622 across 16 paths. `paperPortfolio`
(n=58) is a SUBSET of paper and is deliberately not added.**

| | real money | paper | combined |
|---|--:|--:|--:|
| `stale_stop` | 3 | 7 | 10 |
| `giveback_stop` | 0 | 3 | 3 |
| **lever closes** | **3** | **10** | **13** |
| closes in population | 407 | 622 | 1,029 |
| lever share | 0.74% | 1.61% | **1.26%** |

Cross-checked independently against the Data Explorer
(`/api/bot/db/table/trades`, `filter_state: applied` asserted on both queries):
`stale_stop` **10**, `giveback_stop` **3**. Two surfaces, exact agreement.

⚠️ **The RATE reproduces (1.26% vs 1.3%); the COUNT does not (13 vs 17), and the
denominators differ (1,029 vs 1,324).** These are therefore not the same
population and the two figures must not be quoted as one measurement re-taken.
I have not reconciled the 295-row difference; the sprint log's population is not
stated in enough detail to reproduce, and inventing a reconciliation would be
worse than recording the gap.

## The actual answer: adoption, not wiring

**Population: `config/strategies.yaml`, all 52 strategies not marked
`enabled: false`.**

| lever family | legs declaring it | of 52 |
|---|--:|--:|
| stale (`stale_exit_bars` / `stale_exit_below_r`) | **3** | 5.8% |
| giveback (`giveback_r` / `giveback_min_mfe_r` / `giveback_ladder`) | **1** | 1.9% |
| `vol_trail` (`trail_vol_above_pctl` / `trail_vol_below_pctl`) | **0** | 0.0% |
| `be_touch_arm` | **0** | 0.0% |
| **any M20 lever** | **4** | **7.7%** |

The declaring legs are `ict_scalp_eth_15m`, `trend_donchian_eth_prop`,
`trend_donchian_xrp_4h` (stale) and `uso_trend_1h` (giveback).

The levers are **YAML-declared and default-OFF** by design
(`src/units/strategies/ict_scalp.py`: *"M20 stale-stop (YAML-declared,
default-OFF)"*). 13 firings from 4 declaring legs is the expected order of
magnitude. **There is no evidence here of a lever that is declared and failing
to fire**, which is what a wiring gap would look like.

⚠️ **Two coverage-matrix lever columns are measuring something nothing uses.**
`vol_trail` and `be_touch_arm` are declared by zero legs, so their cells grade a
capability with no live consumer. That is not wrong — the matrix exists to
decide whether to adopt — but a reader treating those columns as describing
live behaviour would be reading them backwards.

## What this does NOT establish

⚠️ **Whether the 13 firings were GOOD is unanswerable from this surface**, and
the reason is provenance, not sample size. `pnlCoverage` on the lever paths:
real-money `stale_stop` **0.3333** (1 of 3 measured), paper `stale_stop`
**0.0** (0 of 7), paper `giveback_stop` **0.0** (0 of 3) — so **1 of the 13
lever closes (7.7%) has a measured PnL**. Any claim about whether the levers
made or lost money rests on one trade.

So "correct-and-rare" is established for the *firing rate*. It is **not**
established for the *outcome*, and the next honest step is exit-price provenance
on those paths, not a larger sample of the same unmeasured closes.

---
_Generated by [Claude Code](https://claude.ai/code)_
