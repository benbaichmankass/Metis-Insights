# M20: the headline reads 100% and the milestone is not done

**Date:** 2026-08-13 · **Tier:** 1 (research + matrix bookkeeping; no live lever touched)

## Read this first

```
HEADLINE        360/360 = 100.0%
DONE-CONDITION   37 cells remain  (0 pending + 37 blocked)
```

Both numbers are correct and they are not in tension — `blocked` counts as
*closed* for the headline and *open* for the done-condition, deliberately. But
the two have now separated as far as they arithmetically can, so the risk of
someone quoting "M20 is at 100%" is at its maximum today. **It is not done.**

What actually changed overnight is that **every remaining cell moved from
`pending` to `blocked`** — from *"nobody has looked"* to *"we looked and here is
the specific thing standing in the way"*. That is real progress in knowledge and
**zero** progress toward the done-condition, which sat at 37 before the night's
work and sits at 37 now.

**The operational consequence: there is no measurement left that I can run to
move M20.** Every one of the 37 needs a decision, a code change, or elapsed time.
That is the handover.

## What each of the 37 needs, and who owns it

| | bucket | cells | owner |
|---|---|---|---|
| **A** | fold standard for daily-bar legs | **14** | **operator — decision** |
| **B** | OOS-base floor (n=3–21 vs a floor of 25) | **9** | **operator — decision, or time** |
| **E** | live-arm accrual on the scalp legs | **7** | time |
| **C** | data absent | **5** | work (untested route) / accept |
| **D** | harness cannot express the lever | **2** | work (low value — see below) |

Reconciles to 37 against `scripts/research/m20_coverage_rollup.py`; zero
unclassified.

### A — the fold standard (14 cells) · the big one

Measured last night in `m20-1d-fleet-pooling-2026-08-13.md`: pooling within
family, exactly as the design permits, yields **zero usable folds** for both 1d
families (donchian largest fold 33, pullback 42, bound 50). This is not a near
miss and pooling is not an escape.

**The decision is not "pick a lower number".** The two families are unequally far
from the bound, so no single value treats both honestly, and every value low
enough to help lands per-fold samples where `beats()` — which has **no
minimum-n** — is near a coin flip. The real question is whether "gradeable"
should mean something different for daily-bar legs than for intraday ones.

### B — the OOS floor (9 cells)

Seven `vol_trail` cells on the 1d equity legs plus two `giveback_stop` cells,
all blocked on OOS base: **n=3–6 at the corpus-standard 2025-07-01 split**,
n=13–21 at 2023-01-01, against a floor of 25, over leg lifetimes of 33–79
trades. Same shape as A and probably the same decision — these legs simply do
not trade often enough to fill an intraday-calibrated sample.

### E — live-arm accrual (7 cells) · **read the finding below**

Six scalp legs cannot evaluate the E1→E2 gate's live arm at all
(`live_trades: 0`). The seventh, `ict_scalp_5m`, could — and it contradicts the
harness arm. That is the most consequential thing in this matrix and it is
written up separately below.

### C — data absent (5 cells)

Measured (trainer-diag #8928, with the positive control asserted first): no
native MES/MGC/MHG file exists anywhere on the trainer, and no E0 dataset was
ever built for those legs. The same sweep found the proxies it was told to
expect (`ES_F_1d` 2515 rows, `GC_F_1d` 2513, `GC_F_1h` 13748, `HG_F_1d` 2514),
so the absence is a finding and not a broken probe.

**This is the one bucket with an untested route out.** `connector_for_symbol`
returns `IBMarketData` for all three, so a live IBKR fetch is possible in
principle and **was not attempted**. CLAUDE.md's *"IBKR historical-candle
coverage is 0%"* is about fetching **one** bar on the live trader via
`exit_anchor.bar_close_at` — a different question from bulk daily history for
offline dataset building, and it must not be read as answering this one. Someone
should try the fetch before treating these as permanent.

### D — harness cannot express the lever (2 cells)

`squeeze_breakout_4h`'s `exit_ladder` and `vol_trail`. Verified by AST over the
harness CLI (with a positive control): `backtest_squeeze.py` declares 28 flags
including seven exit levers, but neither `--bank-frac` nor `--bank-at-r`. Only 3
of 13 harnesses can express banking at all.

**Low value, stated so nobody burns a day on it.** Partial-TP banking is
`honest_negative` on 45 other rows citing memo §7.2 (*banking loses net_R*).
Building the flag would measure, on one leg, a lever already measured negative
45 times. It is listed because it is genuinely unblockable by work — not because
the work is worth doing.

---

## The finding that outgrows M20: the harness arm does not predict live policy

This currently exists only as a ref inside a JSON cell. It should not.

On `ict_scalp_5m`, the E1→E2 gate's live arm became evaluable **for the first
time in the entire E1 program** — and it disagreed with the harness arm
decisively:

| | harness arm | live arm |
|---|---|---|
| verdict | **candidate** — beats_actual 3/3, beats_hard 3/3 | every τ **worse than doing nothing** |
| AUC | 0.5929 (533 OOS trades) | **0.6333** (24 trades) |
| best policy | fold-2024 net_R +19.49 vs actual −12.58 | best τ +1.32 vs actual **+14.58** |
| mean hold | ~18.7 bars | **83.7 bars** |
| base rate | frac_pos 0.21–0.32 | **0.56** |

**Ranking transfers; policy does not.** AUC is *higher* live. The model orders
trades correctly and then exits at 11.5 bars, cutting precisely the long holds
the live edge comes from — a model fitted to a 1-in-4 base rate applied to a
better-than-even one. Both hard rules also beat every τ. The mechanism is
visible in the numbers, not inferred.

### Why this matters beyond one cell

**Every other exit-head verdict in the matrix rests on the harness arm.** The one
time the live arm could be checked, the harness arm was wrong in the dimension
that decides whether a lever ships.

And there is an asymmetry in how sub-floor live evidence has been treated. Both
of these sit **below** the `MIN_OOS_TRADES` floor of 25:

- `ict_scalp_5m` at **n=24** → correctly `blocked`, "the sample cannot carry a
  verdict".
- The three **live, shipped** donchian 1h heads were promoted on evidence that
  included *"the n=15 live set agrees in sign"* (PR #6211, 2026-07-12,
  operator-approved).

**I am not claiming those promotions were improper.** Their primary evidence was
a purged walk-forward winning 5/5 folds on net_R, maxDD and net_R/pos-day, and
the gate as written asks only that the live set *agree in sign*, which n=15 did.
The observation is narrower and still worth acting on: **the live arm of the
E1→E2 gate has no minimum-n, the same gap `beats()` has** — so it counts as
confirmation when it agrees at n=15 and is (rightly) discounted when it
disagrees at n=24.

### The check that should happen, and has not

PR #6211 committed to two follow-ups: *"first head-driven exit gets a mandatory
health-review mechanics check"*, and the realized `future_r_delta` record
accruing for ongoing `/ml-review`. **I have not verified that either happened.**
Those heads have been live since 2026-07-12 — about a month — so the live arm
that was n=15 at promotion should now have materially more behind it.

**The concrete ask: re-evaluate the live arm for the three shipped donchian
heads at their accrued n.** If the sign still agrees at a larger sample, this
closes cleanly and the `ict_scalp_5m` result is a scalp-specific hold-time
mismatch. If it does not, three live heads are trimming winners on real money and
that is a Tier-3 conversation.

**Do not read the scalp result across to donchian on its own.** Different family,
different timeframe, different hold-time distribution — the mechanism is
plausibly general but is measured on exactly one leg. That is why the answer
needs measuring rather than assuming, in either direction.

Filed as `BL-20260813-E1E2-LIVE-ARM-NO-MINIMUM-N`.

## What I did not do

No live lever was flipped, no threshold changed, no standard rewritten, no
config touched. Every change last night was to research docs, the coverage-matrix
JSON, and the backlog. The 13 queued Tier-3 / research-design items are
unchanged and still yours.
