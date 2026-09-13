# M20 U31 — the bleed record's own rates, re-derived per order package

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Row worked:** `BL-20260911-A-RATE-PER-TRADE-ROW-COUNTS-ACCOUNT-FANOUT-AS-INDEPENDENT-AND-INFLATES-N-UNEQUALLY`, clause 1
**Object:** `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · MI-278 U31

<!-- input-provenance
source: /api/diag/journal?table=trades&limit=1000
pulled_at: 2026-09-13T01:41Z
id_field: id
n_rows: 1000
id_first: 5731
id_last: 4732
n_duplicate_ids: 0
n_rows_without_id: 0
rowset_digest: sha256:1752f298bbd639395817aee0581c284561eef361787f4f6f6a03fd4a17058d3b
order_digest: sha256:b073ee2cd3f571022c802c446db077d5e9d2bc2a30daf695ae90574fe32f1a1f
fields_present_not_covered: []
-->

---

## 1. The one sentence

**On the POOLED population `bleed_attribution_2026_09_11.py` actually reports, the e35 pre/post stop-rate contrast goes `p = 8e-05` on rows to `p = 0.05703` on packages — it crosses α and is the only conclusion in the table that changes. On a `bybit_1`-scoped reading it is byte-identical on both denominators, because fan-out is CROSS-ACCOUNT.**

## 2. ⚠️ What is NOT re-derived, and why that is the likelier mistake

**−$38,851.81 IS AN ACCOUNT FACT AND IS CORRECTLY ROW-DENOMINATED.** Each fanned-out row is a real position on a real account that really lost that money. Collapsing siblings to one package would **understate money actually lost**.

A session reading *"the bleed record was re-derived per package"* and revising the headline **downward** would be making the same error in the opposite direction — and that is the likelier mistake here, because a package denominator sounds uniformly more careful. The backlog row states the rule and it is a rule about the **question**, not the table:

> *the row count is the right denominator for questions about ACCOUNTS (exposure, per-account PnL, routing) and the wrong one only for questions about the PRICE PATH … What is owed is that the distinction is STATED where rates are produced.*

So the deliverable is the **partition**, plus a re-derivation of the price-path side only.

| basis | keys | n |
|---|---|--:|
| `price_path` — a package is one price path | `win_rate`, `win_rate_ci95`, `wins`, `stop_rate_adjudicated`, `stop_rate_ci95`, `target_rate_adjudicated`, `stops_adjudicated`, `gradeable_n`, `ungradeable_n`, `adjudication` | 10 |
| `account_fact` — **rows are correct** | `pnl_sum`, `pnl_sum_measured_only`, `pnl_mean`, `pnl_median`, `loss_mean`, `win_mean`, `measured_n`, `pnl_provenance`, `pnl_coverage` | 9 |
| `both_stated` | `n` | 1 |
| **`undeclared` — the finding, never a pass** | — | **0** |

The census is taken by **calling `summarise`**, never by reading its source, so a key added tomorrow surfaces as `undeclared`.

## 3. The premise, measured with a positive control

`order_package_id` appears **0** times in `bleed_attribution_2026_09_11.py`; `account_id` appears **2**. It reads the journal and never the package id.

And **it does not scope by account** — read off the script, not off prose: it has no `--account` argument, and `account_id` is touched only at line 549, inside a per-account *breakdown* printed beside arms that are computed over the whole population. **So its arms are pooled, and the pooled arms are the exposed ones.**

## 4. The result

**Population:** `/api/diag/journal?table=trades&limit=1000`, pulled **2026-09-13T01:41Z**, ids **5731 → 4732**, 0 duplicate ids, 0 rows without one. `population()` (closed · non-backtest · `pnl NOT NULL` · pairs excluded) keeps **292** rows. Tolerance 0.001. Adjudication, grouping, era split, Wilson and Fisher are all the imported originals.

### 4a. Pooled — what the script reports

| | rows | packages | inflation | disagreements |
|---|--:|--:|--:|--:|
| overall | 292 | 213 | **1.3709×** | **17** (8.0%) |

| rate | per row | per package |
|---|--:|--:|
| `win_rate` | 0.3733 | 0.3615 |
| `stop_rate_adjudicated` | 0.5086 (n=291) | 0.5000 (n=196) |

| arm | rows `p` | packages `p` | significance | strength |
|---|--:|--:|---|---|
| **`e35`** | **8e-05** `[2,26]→[15,25]` | **0.05703** `[2,16]→[8,17]` | **`lost`** | weaker |
| `b4_geometry` | 0.3043 `[6,7]→[5,10]` | 0.46429 `[3,3]→[3,5]` | `neither_significant` | weaker |
| `other` | 1.0 `[8,16]→[16,35]` | 0.13166 `[6,9]→[8,23]` | `neither_significant` | stronger |
| `untouched_control` | 0.22025 `[41,81]→[55,91]` | 0.10528 `[28,59]→[40,64]` | `neither_significant` | stronger |

**Exactly one conclusion changes**, and it is the e35 one.

⚠️ **The mechanism is not the unequal inflation this row was filed about — on THIS window the e35 arms are inflated almost equally (pre 1.444×, post 1.471×).** What moves the p is (a) roughly a third fewer independent observations in both arms, and (b) the post arm's stop-outs collapsing **disproportionately**: 15 rows → 8 packages, a **1.875×** reduction against the arm's own 1.471× inflation, so post stop-outs were concentrated in fanned packages. The post stop rate falls 0.600 → 0.4706.

### 4b. `bybit_1` — and this is the useful half

| | rows | packages | inflation | disagreements |
|---|--:|--:|--:|--:|
| overall | 182 | 172 | **1.0581×** | 6 |

| arm | rows `p` | packages `p` | significance |
|---|--:|--:|---|
| `e35` | 0.0538 `[2,16]→[7,15]` | **0.0538 `[2,16]→[7,15]`** | `neither_significant` |
| `other` | 1.0 | 1.0 | `neither_significant` |
| `untouched_control` | 0.47951 | 0.18711 | `neither_significant` |

**The e35 arm is byte-identical on both denominators**, because **fan-out is cross-account**: a single account almost never holds two legs of one package. So a per-account reading of this analysis is essentially immune, and the whole exposure sits in the pooled arms.

**That is the actionable statement of this unit:** it says *which reading is safe*, rather than declaring the analysis good or bad.

## 5. ⚠️ Four things this is NOT

1. **It does not reproduce the memo's published numbers, and does not try to.** This is a **fresh 1000-row tail** (`BL-20260912-A-1000-ROW-TAIL-PULL-CHANGES-A-FANOUT-PACKAGES-MEMBERSHIP-WITHIN-HOURS-SO-A-PACKAGE-LEVEL-RATE-OVER-A-TAIL-IS-UNSTABLE-BY-CONSTRUCTION`), not the memo's window: its e35 table is `2/30 → 13/23`, mine is `2/26 → 15/25`. **Different rows.** What is re-run is the memo's *unmodified code*, so the comparison is denominator-to-denominator on one population — which is the only comparison that isolates the effect.
2. **The memo's `p = 0.015` is a different arm** — the **measured-only win** contrast (`0/5 → 6/7`). It is not the stop-rate Fisher graded here and must not be quoted as having moved.
3. **Neither `pnl_sum` here is the bleed record.** Pooled reads **+197,403.71** — a fleet number dominated by the `ib_paper` fabrication class this repo already documents — and `bybit_1` reads **−21,843.48** over this window. The **−$38,851.81** figure is a 17-day `closed_at`-bucketed measurement over a different window. **Three different numbers answering three different questions; none is a correction of another.**
4. **No live figure is re-graded.** The three dashboard routes in U12's list (`/api/bot/stats` `winRate`, `/api/bot/performance`, `/api/bot/attribution`) are `src/web/` — **Tier-2** — and changing a number the operator reads is a decision, not a side effect of a re-derivation. Untouched.

## 6. The reduction never picks a winner

**17 of 213 pooled packages (8.0%) have rows that DISAGREE about the exit.** They are excluded from *both* arms and counted, never resolved by taking the first row, the real-money row, or the majority — MI-278 U15 measured that choosing swings a headline **4.3–28.2pp**, so any of those would be a choice wearing the clothes of a fact. `package_verdict` is U19's, imported unmodified.

## 7. A mislabel of my own, caught by running it

The first verdict was a single string, and on live data it returned **`loses_significance` for `b4_geometry`** — `p` 0.3043 → 0.4643, significance it **never had** — and **`strengthens`** for two arms that stayed comfortably non-significant.

That is UNPROVENANCED DIAGNOSTIC OUTPUT **sub-class A**: the label names a quantity the code did not compute. Per this repo's own remedy the fix is to **branch on the actual condition, not reword the label**, so the verdict is now two orthogonal facts that are never collapsed:

* `significance` ∈ `both_significant` · `lost` · `gained` · `neither_significant` · `not_computable`
* `strength` ∈ `weaker` · `stronger` · `unchanged` · `not_computable`

An arm can weaken a great deal and change nothing, or move barely and cross the line. Only the join says what happened. Both live mislabels are now regression tests naming their own arm and figures.

## 8. Verification

* **25 controls** in `--self-test`, **38** in `tests/test_bleed_record_package_denominator.py`.
* **Four defects planted, all four caught**: a disagreement arbitrated into an arm (1) · `pnl_sum` reclassified as `price_path` (2) · the two verdict axes re-collapsed (3) · an empty rate returning `0.0` instead of `None` (3).
* **The null control passes**: with no fan-out both denominators are identical, so every difference above is the reduction and not the arithmetic.
* ⚠️ **A fixture bug found by running, not reading**: my synthetic rows omitted `direction`, which the imported adjudicator requires, so every one graded `ungradeable_no_direction` and three tests failed. Had I asserted a *negative* they would have passed vacuously. They assert positives — `disagreement == 1`, package denominator strictly smaller — which is why they failed loudly instead.

## 9. What remains on the row

**17 of the 21 surfaces are still unpaid** (U19 two, U31 two: `bleed_attribution_2026_09_11.py` and, by the same partition, anything importing its `summarise`). `e35_break_attribution.py` — a one-sided **binomial** p on a row count, the same class — is the next one and is **not** done here.

⚠️ **And three of the remaining are Tier-2** (`/api/bot/stats`, `/api/bot/performance`, `/api/bot/attribution`). Changing a number the operator reads is a decision; a re-derivation does not get to make it.

---

## Standing

`OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30-AND-THE-CAUSE-IS-UNATTRIBUTED` is `loud: true` and unchanged: **17 consecutive losing days, −$38,851.81**, onset **2026-08-27**, bounded below by a +$4,172.64 winning day on 08-26.

⚠️ **This unit touches that row's evidence and does not weaken its headline.** What it narrows is one supporting claim: the e35 stop-rate contrast, the row's own *"leading candidate"*, does not clear α on a package denominator over the pooled population. That makes the row's existing framing **more** right, not less — it already records that the bleed **predates** e35 by three days and that *"the three-day gap is evidence AGAINST e35 being the whole story."* The row's `clears_when` is unaffected: it still needs the discriminating per-leg stop-out-rate and MFE-at-stop measurement, e35 legs against non-e35 legs, before and after 2026-08-30.
