# Exit-capture deep-dive — MFE-vs-realized, 14-day live evidence (2026-07-30)

**Operator directive (2026-07-30):** "We're bleeding… trades get very close to
take-profit then snap back to the stop-loss instead… come up with a metric for
max unrealized PnL vs actual PnL and figure out what we're doing wrong to not
capture the value." This memo is the P1 evidence read of the
[`exit-refinement`](../../.claude/skills/exit-refinement/SKILL.md) pipeline.

Tool: `scripts/research/m20_exit_analysis.py --since-days 14` on the trainer VM
(the box with the `datasets-out/market_raw` candle store). Real / paper / prop
are **never blended**; reconciler/superseded/adopted-orphan artifact rows
excluded. R is multiplier-aware.

## The metric (what "capture" means here)

Per closed trade, over the trade's own bar path (entry→close):
- **MFE** = maximum favorable excursion in R (how close to TP it got).
- **giveback** = `MFE − realized_R` (value reached but not kept).
- **round-tripper** = went `MFE ≥ 1.0R` in favor then **closed negative** — the
  literal "near-TP then snap to SL" trade the operator described.
- **hold_h**, **chop_frac**, **time-to-MFE** = context.

## Headline (14 days)

| book | n | sum R | mean giveback | round-trippers |
|---|---|---|---|---|
| **real money** | 16 | **+8.9R** | 3.8R | 18.8% |
| **paper** | 82 | **−79.1R** | 2.4R | 20.7% |

**The money-at-risk book is positive.** The bleed is in the paper/soak legs.
But the exit leak the operator described is real and system-wide: **~1 in 5
trades round-trips**, and average giveback is 2.4–3.8R.

## Where the leak concentrates — the altcoin scalps

| leg (paper) | n | sum R | round-trippers | avg giveback | avg hold |
|---|---|---|---|---|---|
| `ict_scalp_avax_5m` | 6 | **−12.0R** | **50%** | 9.05R | 12.7h |
| `ict_scalp_xrp_5m` | 6 | **−19.0R** | **50%** | 5.13R | 5.8h |
| `ict_scalp_sol_5m` | 3 | +0.4R | 33% | 3.85R | 14.2h |
| **`ict_scalp_5m` (real, BTC)** | 3 | **+21.8R** | **0%** | 1.72R | 3.2h |

A **5-minute scalp held 6–14 hours** is the smoking gun: the TP/SL bracket is
not executing per-trade — the position sits open until a reconciler/time event
closes it, long after MFE has round-tripped.

## Root cause (high confidence): `BYBIT_TPSL_MODE=full` shared-bracket replacement

`BYBIT_TPSL_MODE` is at its default **`full`** on the live VM. Under `full`,
Bybit one-way netting gives the whole netted position **one** position-level
TP/SL, and **each new same-symbol open REPLACES it** (`BL-20260720-ICTSCALP-PASTSTOP-EXITS`).
On the soak account (`bybit_1`) the scalps fire constantly on the same symbol —
`xrp_5m`+`xrp_15m` share XRPUSDT, `sol_5m`+`sol_15m` share SOLUSDT, and even a
single leg firing repeatedly collides with itself — so older trades lose their
bracket, sit open, and give back their MFE.

**The control that proves it:** `ict_scalp_5m` on real-money `bybit_2` has **no
same-symbol sibling** (it's the only scalp on bybit_2). Its bracket survives —
**0% round-trippers, +21.8R, 3.2h hold.** Same strategy, clean exits, wins.

The fix — **`BYBIT_TPSL_MODE=partial`** (qty-scoped bracket per trade) — is
already built (`src/units/accounts/execute.py`) and Tier-3-gated on the
`validate-partial-tpsl` demo action. It is **not deployed** (default `full`).

## Counterfactual: a time-stop "recovers" +14.6R in paper…

A 4h flat time-stop recovers +14.6R in paper, concentrated in the leaking legs
(`avax_5m` +5.84R, `xrp_5m` +4.8R). **This is a symptom-cut, not the fix** — it
only helps because the trades are stuck open for hours. Fixing the bracket
(partial mode) removes the stuck-open state at the source; a time/stagnation
stop is a candidate *secondary* lever to sweep afterward (`MB-20260728`).

## Not an exit problem for the trend/pullback fleet

The M20 coverage matrix (`docs/research/exit-refinement-coverage.json`) already
records `honest_negative` for trail/stale/giveback/ladder on most trend and
pullback legs — mechanical exit levers there don't beat baseline. Their bleed is
**entry-selection**, not exits. Don't chase exit levers on that fleet.

## Plan (maximize capture)

1. **Deploy `BYBIT_TPSL_MODE=partial`** — highest leverage. `validate-partial-tpsl`
   on bybit_1 demo → operator flips `set-env BYBIT_TPSL_MODE=partial` (Tier-3).
   Expected: hold times collapse to minutes, round-trippers drop, scalps capture
   their MFE.
2. **Standing capture metric** — wire `roundtrippers% / mean_giveback / mean_hold_h`
   per strategy into `/performance-review` + `/system-review` (source:
   `m20_exit_analysis`). Watch capture continuously, not ad hoc.
3. **Re-soak & re-measure** the alt scalps on clean brackets, then graduate the
   legs that clear the ≥20–30-trade gate (`PB-20260721`). Operator chose
   **fix-exits-first** over graduating now (2026-07-30).
4. **Scalp exit-lever sweep** (`MB-20260728`, currently `blocked:no_harness_levers`)
   — partial-TP ladder / giveback-cap for the residual giveback.
5. *(Secondary, entries)* `slv_trend_1h` fired conf 0.06/0.15 "should-skip"
   losers → confidence floor / regime gate. Lower priority than exits.

## Graduation status (`SRQ-20260728`)

Held. The alt-scalp paper soak is thin (n=3–6 « the 20–30 gate) **and** currently
negative with the exit leak. Graduating now would ship the known bracket bug to
real money. Sequence: fix exits (step 1) → re-soak → graduate on clean evidence.
The BTC scalp already on real money is the proof the strategy works with clean
exits.
