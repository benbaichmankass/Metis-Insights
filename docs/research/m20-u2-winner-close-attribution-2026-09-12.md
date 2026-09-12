# U2 — which mechanism actually ended each winning trade

> **Doc status:** `live` · category `research` · last verified `2026-09-12` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

**Unit:** MI-278 U2 · `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER`
**Cycle priority:** `CY-20260906-TRADING-TRUTH`
**Predecessors:** [`m20-u1-winner-close-inventory-2026-09-12.md`](m20-u1-winner-close-inventory-2026-09-12.md) (U1, PR #11864) · [`winner-size-collapse-2026-09-12.md`](winner-size-collapse-2026-09-12.md) (MI-277, merged #11848)
**Reproduce:** `python3 scripts/research/m20_u2_winner_close_attribution.py --trades <t.json> --packages <p.json> --sensitivity`

U1 established what *can* end a winner per leg. This asks what *did*.

---

## 0. The verdict, in four sentences

**No exit lever is cutting winners short, and the reason we can say so is also the reason we cannot say much more: for 55% of the winners that fell short of their own target, the mechanism that stopped the run is NOT KNOWN from the journal.** Over the 49 post-2026-08-27 winners, **23–27 (47–55%) can be attributed** to a named per-leg mechanism — a range, because it is the only quantity that moves with the label-recovery tolerance at all — and of those, **18 ended at their declared take-profit** while the entire lever family accounts for **3** (two `giveback_stop`, one `exit_head`), of which the two `giveback_stop` fires banked **+$2,471.70**, the largest lever contribution in the window and plainly the lever working rather than failing. **The unattributable share does not depend on the tolerance choice**: `unattributable_price` (9) and `contaminated` (7) are invariant across the whole range, so *"we cannot say what ended a fifth of these"* is a measurement, not an artifact of a threshold I picked. And MI-277 §2.2's cell — the one sub-cell that moves *against* the collapse — turns out **not to be decision-grade in either direction**.

---

## 1. Population

| | |
|---|---|
| **Source** | `/api/diag/journal` trades + order_packages, `limit=1000`, pulled **2026-09-12T05:2xZ** direct over `https://ict-bot.duckdns.org`; ids **4716–5715** |
| **Base population** | MI-277's `population()`, restated identically: `closed` · NOT `is_backtest` · `pnl IS NOT NULL` · pairs sleeve excluded |
| **Date cut** | `created_at >= 2026-08-27` — MI-271's bleed-start date, per this unit's brief (**not** MI-277's 2026-08-30T08:53Z split; where §4 compares against MI-277 it uses MI-277's split, and says so) |
| **n** | **183 closes · 49 winners** across `bybit_1` 40 · `alpaca_portfolio` 3 · `alpaca_paper` 2 · `bybit_portfolio` 2 · `bybit_2` 2 |
| **Risk basis** | `order_packages.entry`/`.sl` — **entry-frozen**. `trades.stop_loss` is the TRAILED stop and must never be a denominator (MI-277 §1.2) |
| **Positive control** | `provenance.classify_pnl` over the 183 returns `{measured: 99, estimated: 84}` — the probe demonstrably finds more than one bucket, so a quiet cell below is a real negative |

---

## 2. The label recovery, and why it gets a curve rather than a constant

U1 measured that `_classify_broker_exit` refuses on **27 of 27** broker-truth `reconciler_filled` rows because each fill lands a median **0.55–0.79 bp short** of the level its strict inequality tests. This unit re-grades those rows with a **tolerance**.

⚠️ **A tolerance is a threshold, and choosing the one that maximises recovery is exactly what `CLAUDE-RULES-CANONICAL` forbids** (*"never lower a pre-registered bar to manufacture a verdict"*). Three things keep it honest:

* **It is expressed in R, not in bp or ticks.** ⚠️ **I tried to derive the venue tick and could not, and that is reported rather than fudged**: computed SL/TP levels carry float noise, so a decimal-places probe over the price grid returns 10–16 decimals for most symbols — it measures the arithmetic, not the venue. Only `AVAXUSDT` (0.001) and `XRPUSDT` (0.0001) yielded a plausible tick from fill prices alone. R is self-normalising and needs no venue data.
* **The distances have a natural break**, so the choice is not knife-edge. Sorted, the 27 sit at `0.00077 … 0.0593`, then jump to `0.1057`, `0.1978`, then `0.5368 / 0.5470 / 0.5498`.
* **The sensitivity is published beside every verdict** (`--sensitivity`), and anything that moves across the range is reported as **ungradeable**.

---

## 3. The attribution

Five classes, **never pooled** — the distinctions are the finding, per § "Collapsed states":

| class | meaning |
|---|---|
| `attributed` | a named per-leg mechanism from U1's catalog ended it |
| `account_level` | an account/venue closer ended it — real, but says nothing about levers |
| `unattributable_price` | the exit price is an **estimated anchor** (`candle_at_close`) — ***we could not look*** |
| `unattributable_level` | a **broker-truth** fill sitting near no declared level — a real measurement that it was *not* a bracket exit |
| `contaminated` | `netting_attributed` — 100% ESTIMATED and `OI-20260908`'s own population, a **live unfixed defect** |

### 3.1 It is stable where it matters

| tolerance (R) | attributed | account-level | unattr (price) | unattr (level) | contaminated |
|--:|--:|--:|--:|--:|--:|
| 0.000 – 0.020 | **23** | 5 | 9 | 5 | 7 |
| 0.050 – 0.250 | **27** | 5 | 9 | 1 | 7 |

**Only one boundary moves** — four rows shifting between `attributed` and `unattributable_level`. `account_level` (5), `unattributable_price` (9) and `contaminated` (7) are **invariant across the entire range**. So *"21 of 49 winners (43%) are unattributable-or-contaminated"* is a measurement that owes nothing to the threshold, and the attributed share is **47–55%**, quoted as a range.

### 3.2 By mechanism, at 0.05R

| n | mechanism | pnl |
|--:|---|--:|
| 12 | `tp` | +9,892.60 |
| 10 | `reconciler_filled` *(unattributed)* | +2,751.44 |
| 7 | `netting_attributed` *(contaminated)* | +1,588.67 |
| 6 | `tp_cross` | +2,370.44 |
| 4 | `sl` | +739.02 |
| 3 | `exchange_flat_reconciled` | +122.74 |
| 2 | `sl_cross` | +60.37 |
| 2 | **`giveback_stop`** | **+2,471.70** |
| 1 | `intent_reduce` | +733.00 |
| 1 | `exit_head` | +34.07 |
| 1 | `stuck_strategy_watchdog` | +241.15 |

**The lever family is 3 of 49 winners.** Two `giveback_stop` fires (both `uso_trend_1h`, the one leg U1 found it armed on) banked **+$2,471.70** — the largest lever contribution in the window, and the lever doing precisely its job. One `exit_head` closed at **0.6%** of its declared target for **+$34.07**, which is a scratch.

**So the M20 hypothesis that a lever is cutting winners short is not supported on this population.** That is consistent with MI-277 and now rests on an attribution rather than on an absence.

---

## 4. Did the winner reach its own target?

**18 reached (≥98% of declared `tp_r`) · 31 fell short · 0 ungradeable.** Of the 31:

| n | what ended it | median achieved / declared `tp_r` | pnl |
|--:|---|--:|--:|
| 10 | `reconciler_filled` *(unknown)* | 0.163 | +2,751.44 |
| 7 | `netting_attributed` *(contaminated)* | 0.321 | +1,588.67 |
| 4 | `sl` | 0.125 | +739.02 |
| 2 | `sl_cross` | 0.120 | +60.37 |
| 2 | `tp_cross` | 0.966 | +66.06 |
| 2 | `giveback_stop` | 0.238 | +2,471.70 |
| 1 | `intent_reduce` | 0.336 | +733.00 |
| 1 | `exchange_flat_reconciled` | 0.082 | +50.13 |
| 1 | `exit_head` | 0.006 | +34.07 |
| 1 | `stuck_strategy_watchdog` | 0.185 | +241.15 |

⚠️ **17 of the 31 (55%) carry an unattributable or contaminated label.** For more than half of the winners that did not reach their target, **the journal does not record what stopped the run.** That is this unit's headline, and it is the answer to the brief's *"if a mechanism cannot be attributed, say so and name what surface would be needed."*

The **6 stop-family exits** (`sl` 4, `sl_cross` 2) reached a median **12–13%** of the declared target while still booking a profit — the signature of a stop that had trailed above entry, i.e. a winner banked small. That is a real, attributed, in-scope M20 mechanism, and it is **6 of 49**.

---

## 5. MI-277 §2.2 is not decision-grade, in either direction

Its §2.2 restricts to *measured winners that reached a declared target* (`tp`/`tp_cross`, pooled `bybit_*`, MI-277's own 08-30 split) and reports achieved R **nearly doubling**, 4.835 → 9.841 — the one sub-cell moving *against* the collapse. Reproduced and then re-run with recovered labels:

| tolerance | pre n | pre median R | post n | post median R | **post/pre** |
|---|--:|--:|--:|--:|--:|
| none *(as MI-277)* | 7 | 5.281 | 9 | 10.169 | **1.93** |
| 0.002 – 0.020 | 8 | 5.131 | 9 | 10.169 | **1.98** |
| 0.050 – 0.100 | 10 | 7.122 | 11 | 8.427 | **1.18** |
| 0.250 | 11 | 5.281 | 11 | 8.427 | **1.60** |

**The ratio swings 1.18 – 1.98 on the tolerance choice, at n ≈ 10.** So the honest reading is **not** *"the doubling disappears"* — which is what I first wrote, and it is wrong — but that **this cell cannot support either claim.** Its members' R values span 1.4 to 33.9 on n=7–11, so a median moves several R when two rows enter. The recovered rows that move it are named in the script's output (`5170` at R=14.156, `5059` at 8.962, `4798` at 3.918 on the pre side; `5636` at 4.041, `5299` at 8.427 on the post side).

⚠️ **I could NOT reproduce MI-277 §3.3's `cap_r` figures** (it reports 16.7% / 0.0% of winners reaching a tenth of the cap; my basis gives 55% / 38%), so **I do not adjudicate that table** — a correction I cannot reproduce is not a correction. What I measured on my own stated basis is separate and stands on its own: against the **declared `tp_r`**, `bybit_1` winners reached ≥98% of target in **18 of 42 (43%) pre** and **11 of 34 (32%) post**, with median achieved/declared `tp_r` falling **0.848 → 0.328**.

---

## 6. What this does NOT establish

* **That the unattributable closes were not bracket exits.** `unattributable_price` means the exit price is an estimated anchor, so the question cannot be asked of those 9 rows at all. Only the single `unattributable_level` row is a positive measurement that a broker-truth fill sat near no level.
* **That the 7 `netting_attributed` winners are real.** They are `OI-20260908`'s population on a live unfixed defect, 100% ESTIMATED. This unit did **not** run the venue-side read that would adjudicate them — that is `OI-20260908`'s own criterion.
* **That the 0.05R tolerance is correct.** It is a stated choice inside a plateau, published with its curve. Whether a tolerance ships in `_classify_broker_exit` is **Tier-2** and belongs to U4.
* **Anything about `ib_paper`, the pairs sleeve, or pre-2026-08-27 trades.** Excluded by the population.
* **That 49 winners is enough.** It is not, for anything per-leg. Every per-mechanism count here is single digits outside `tp`.

---

## 7. What this changes about U3

**U3's brief says "for the mechanism(s) U2 implicates". U2 implicates no lever — so the honest U3 is a different counterfactual, and running the briefed one would be measuring a mechanism this unit found idle.**

The only mechanism with enough attributed mass to test is the **take-profit**: 18 of 49 winners ended exactly at their declared `tp_r`, and the stop family banked another 6 at 12–13% of target. So the U3 counterfactual is on **target geometry and the trailing stop that precedes it** — on the config-exact harness, IS/OOS with a yearly walk-forward, at the `exit-refinement` gate (Path A or Path B), not at a correlation bar.

⚠️ **And it must be run knowing that 43% of this window's winners are unattributable or contaminated**, so a live-population estimate of any lever's firing rate is a **lower bound** by construction.

## 8. Rows filed

| id | register |
|---|---|
| `BL-20260912-FOR-55-PERCENT-OF-WINNERS-THAT-MISSED-TARGET-THE-MECHANISM-THAT-STOPPED-THE-RUN-IS-NOT-IN-THE-JOURNAL` | performance |
| `BL-20260912-MI-277-SECTION-2-2-CELL-IS-NOT-DECISION-GRADE-ITS-RATIO-SWINGS-1-18-TO-1-98-ON-A-TOLERANCE-CHOICE` | performance |
