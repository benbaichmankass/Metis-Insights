# M20 U41 — the 2026-08-30 break: the market travelled worse for legs e35 never touched (this half stands), and an e35 stop-out term on top that does NOT survive resampling (see the superseding correction below)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-278 U41** · RESEARCH lane · Tier-1 · answers the discriminating measurement
`OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30-AND-THE-CAUSE-IS-UNATTRIBUTED`
asks for.

Instrument: [`scripts/research/m20_u41_break_attribution_verdict.py`](../../scripts/research/m20_u41_break_attribution_verdict.py)
(44 self-test controls; 30 pytest controls in
[`tests/test_break_attribution_verdict.py`](../../tests/test_break_attribution_verdict.py)).

---

## ⚠️ SUPERSEDING CORRECTION, added 2026-09-18 when this memo finally landed

**This memo was written 2026-09-13 and sat unmerged for five days. In that
window MI-301 re-ran the stop term against SAMPLING and one half of the verdict
below did not survive. Read this section before the verdict, not after it.**

MI-301's finding (`docs/research/mi301-stop-term-robustness-2026-09-17.md`,
31 self-test + 19 pytest controls, already on `main`), recorded in
`OI-20260911`'s FIFTH READING:

- **The `+11.6pp` stop-out term is not computed by this instrument.** It arrives
  as a hand-typed `--stop-did-pp` CLI scalar from `stop_integrity_both_arms`, and
  `verdict()` branches on nothing but its **sign**. That sign is the whole
  difference between `both_contribute` and `market_regime`.
- **The sign is knife-edge under sampling.** On MI-301's pull the declared-only
  DiD reads **+5.31pp**, and **ONE additional stop-out in the treated pre cell
  flips it**. That cell holds two stop-outs and both are the same leg on the same
  symbol (`ada_pullback_2h`/ADAUSDT); its Wilson 95% CI is (7.15, 59.07) and
  **contains the control's pre rate**, so the two arms are not distinguishable in
  the era the whole difference-in-differences is measured from.
- **The term also decays.** `/api/diag/journal` takes `table`+`limit` only — no
  offset, no date filter — and trade ids are monotonic, so rolling the newest-1000
  window forward strips the PRE era preferentially: the e35 pre cell fell 18 → 8
  packages (−55.6%) between 09-13 and 09-17. Filed as
  `BL-20260917-THE-E35-PRE-ERA-BASELINE-IS-ERODED-BY-THE-1000-ROW-JOURNAL-CLAMP-SO-A-TIER-3-QUESTION-GETS-LESS-ANSWERABLE-OVER-TIME`.

**What survives, and it is the half this memo was for.** The **excursion term
stands** — it is provenance-immune by construction (candles over a fixed window
from entry; no exit price, no stop, no pnl) and its control arm is gradeable. The
market travelled worse for legs e35 never touched.

**What does not.** The **e35-on-top half is `cannot_discriminate` with a
knife-edge sign**, not the settled `+11.6pp` the title of this memo asserts.

⚠️ **Do NOT read that as "it was the market."** `OI-20260911` forecloses that
answer by name, and a fragile sign forecloses **both** candidates on that term
rather than choosing between them. ⚠️ **And do NOT read it as exonerating e35.**

**The recommendation is unchanged and is strengthened, not weakened, by this
correction: do not revert e35 on this evidence.** This memo already declined to
propose a parameter change; MI-301 reaches the same place by a different route —
the term implicating e35 cannot be distinguished from zero.

**This memo's own numbers are left exactly as they were measured on 2026-09-13.**
They are not restated against MI-301's pull, because the two are overlapping
populations rather than a time series of one quantity, and silently refreshing
them would hide the instability that is itself the finding.

---

## The verdict

**`both_contribute`.** Neither candidate can be reverted away alone.

Excursion fell in **both** arms — including the control arm e35 never touched,
which is a market read no geometry change can produce — **and** the stop-out
rate rose in the e35 arm relative to that control.

> ⚠️ **THIS IS NOT "the market chopped".** The row forecloses that answer
> without a non-e35 control, and the control is the reason the verdict is not
> `e35_geometry`. It is equally not "e35 did it": reverting the geometry would
> leave the control arm's deterioration — and `ict_scalp_*`, which e35 never
> touched — unexplained, which the row also names by hand.

## Population, stated

- **214 package ids** — closed, non-backtest, `pnl NOT NULL`, non-pairs — out of
  **1000** `order_packages` read. Skipped: 329 `pairs_sleeve_excluded`
  (that sleeve owns its own order path), 457 `outside_decision_population`.
- **0 packages could not be placed in time.** No row was defaulted into an era.
- Event boundary **2026-08-30T08:53:19+00:00** (commit `892c9a2c`), instant-is-POST.
- Candle interval 15m; windows 4/12/24/48h; the **24h** window carries the verdict.

| arm | pre | post |
|---|---|---|
| `e35` | 59 | 52 |
| `control_same_family` | 44 | 40 |
| `control_other` | 293 | 183 |

⚠️ **The excursion cells are much smaller than the package cells** (e35 18/15 at
24h, not 59/52): a package contributes an excursion row only where candles for
its symbol could be fetched. **GLD, MGC, QQQ, SLV, SPY, TLT and USO returned 0
candles** — the candle source is Binance Vision, which has no equities, ETFs or
CME futures. So the excursion half of this verdict rests on the **crypto** legs
only, and that is a limit of the measurement, not a property of the fleet.

## The two terms

**Stop-out rate, difference-in-differences, declared-only, package unit: +11.6pp.**
(Treated +24.18, control +12.61 — U15's declared-vs-amended split, so an amended
stop is not counted as the entry geometry stopping out.)

**Excursion, MFE:MAE in ATR — geometry-FREE by construction**, so e35 cannot move
it. At the 24h verdict window:

| arm | mean pre → post | Δmean | median pre → post | Δmedian | state |
|---|---|---|---|---|---|
| `e35` | 1.253 → 0.735 (n=18/15) | −0.5175 | 0.942 → 0.425 | **−0.5167** | `measured` |
| `control_same_family` | 4.040 → 1.185 (n=5/16) | — | 0.890 → 0.297 | −0.5934 | `insufficient_n` |
| `control_other` | 27.457 → 3.737 (n=60/58) | −23.72 | 2.107 → 0.784 | −1.3229 | `measured` |
| **POOLED control** | (n=65/74) | **−22.4707** | | **−1.4313** | `measured` |

The pooled control is what the verdict keys on — the row's demand is *e35 legs vs
NON-e35 legs*, and `control_same_family` alone is `insufficient_n` at every
window (n=5 pre), so pooling is what makes the control readable at all.

## ⚠️ The mean overstates the control's fall ~16×, and the sign is what survives

`control_other`'s mean falls **−23.72** while its median falls **−1.3229**.
MFE:MAE is bounded below by 0 and **unbounded above** (MAE → 0 sends it to
infinity), so an arm mean can be carried by a couple of near-zero-MAE packages —
and a pre-era mean of 27.457, for a quantity whose typical value here is 1–3, is
exactly that shape.

**Do not quote −22.47 as the size of the market move.** The defensible statement
is the median: favourable-to-adverse travel roughly halved in both arms
(e35 0.942 → 0.425; `control_other` 2.107 → 0.784).

This is checked rather than asserted. `term_state` compares the delta of MEANS
against the delta of MEDIANS and returns **`sign_unstable`** when they disagree —
a fourth term state that reaches `verdict()` as **`cannot_discriminate`**, so it
can never be spent as evidence the control did *not* fall (which would return
`e35_geometry` and license a revert on a term nobody established). A median delta
of exactly 0 against a large mean delta counts as disagreement: that is the
outlier case, not a tie to wave through.

**The gate fired on live data, at the 4h window:** e35's mean *rose* (+1.017)
while its median *fell* (−0.118) → `sign_unstable`, no verdict. At 24h, the two
statistics agree to three decimal places on the e35 arm (−0.5175 vs −0.5167),
which is why that window can carry one.

## What this does and does not discharge

**MET** — `OI-20260911`'s attribution clause: per-leg stop-out rate and
excursion, e35 vs non-e35, before vs after 2026-08-30, verdict and population
recorded. Per that row's own text this also discharges
`OI-20260830-E35-GEOMETRY-SHIPPED-TO-9-LEGS-NOT-YET-LIVE-VERIFIED`.

**NOT MET, and deliberately not folded in** — the row's final clause: *"nothing
alarmed on fifteen consecutive losing days; a sustained-losing-streak detector is
owed whatever the attribution turns out to be."* That detector is **built**
(`src/runtime/losing_streak_alert.py`, MI-276) and **has never fired on the
fleet** — tracked by
`OI-20260911-THE-TWO-DETECTORS-ARE-BUILT-AND-NEITHER-HAS-EVER-FIRED-ON-THE-FLEET`.
Built is not armed and armed is not exercised. **`OI-20260911` therefore stays
OPEN**, with the attribution half recorded.

**No parameter change is proposed here.** The verdict says a revert of e35 alone
would not restore the pre-period, and the pre-period baseline is itself withdrawn
as ungradeable
(`BL-20260911-THE-PROFITABLE-FORTNIGHT-BASELINE-RESTS-ON-A-WEEK-WITH-ZERO-MEASURED-ROWS`).
Any geometry change remains **Tier-3** and belongs to the operator.

## Reproduce

```
python3 scripts/research/m20_u41_break_attribution_verdict.py \
    --packages <order_packages pull> --trades <trades pull> \
    --stop-did-pp 11.6 --window 24
```

⚠️ **The `--trades` pull must be the WIDE one.** A 400-row tail (ids 5328–5727)
yields 92 usable packages against this run's 214, and because ids are
monotonic in time, truncating to newer ids strips the **pre** era
preferentially — the era split is then biased by the pull, not by the market.
This run used a 1000-row pull spanning ids **4728–5727**.
