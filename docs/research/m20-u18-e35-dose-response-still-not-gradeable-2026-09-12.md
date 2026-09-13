# MI-278 U18 — the venue was asked, and the e35 dose-response is still not gradeable: the observed p=0.0486 is inside a band that contains the null

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> **Unit:** MI-278 U18 · object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · cycle `CY-20260906-TRADING-TRUTH`
> **Answers the question MI-278 U11 left on** `BL-20260911-BOTH-OF-THE-E35-EXPERIMENTS-DESIGNED-CONTROLS-HAVE-ZERO-OBSERVATIONS`, **and corrects that row's `next_action`, which still points at the exit U11 refuted.**

## 1. The question, and why it was worth asking

The row offers two exits: **(a)** record e35 as unfalsifiable on its declared controls, or **(b)** find a control that can report. **U11 refused both** and moved the binding constraint off the control and onto the **outcome variable** — a reportable within-family control exists and has been trading throughout, while most treated closes carry `reconciler_filled` or `netting_attributed`, labels that record *that* a row closed and not *what* closed it.

**U10** then measured that the venue holds the answer, on `/api/diag/bybit_raw_order_history`, at the cost of a match step that lost 6 of 17.

So one arithmetic question remained: **does venue recovery return enough of the unnamed closes to make the dose-response gradeable?** This unit asks it. The answer is **no**, and the shape of the no is the finding.

## 2. Population

- **Trades:** `/api/diag/journal?table=trades&limit=1000`, pulled **2026-09-12T20:0xZ** — 1000 rows spanning `2026-08-18T02:07:30Z` → `2026-09-12T20:00:15Z`. ⚠️ A **newer tail** than U11's; **these are not the same rows and U11's counts are not restated as if they were.**
- **Treated set:** derived from the ship commit `892c9a2c8`'s **own diff**, not from the comments beside the values — **9 legs, 10 field edits**, reproducing U11 exactly and independently.
- **Control:** `trend_donchian_eth`, `trend_donchian_sol` — U11's reportable within-family controls, **re-verified held at `atr_stop_mult: 2.5` at `892c9a2c8^`, at `892c9a2c8` and at `HEAD`**. A control that moved is not a control.
- **Split:** on `created_at` at the ship commit's own instant `2026-08-30T08:53:19Z`, because a trade opened before it carries the old geometry however late it closes. ⚠️ **Merge is not deploy** — `ict-git-sync` pulls on a ~5-minute timer, so a trade opened inside that window is labelled treated while carrying control geometry. That biases **toward the null** and is stated rather than assumed away.
- **Kept:** treated **27** closes (118 seen, 63 not closed, 28 opened before the ship instant), control **12** (17 seen, 5 not closed). Every exclusion is counted by the instrument, so a filter cannot silently shrink an arm.
- **Venue:** 14 `bybit_raw_closed_pnl` windows + 14 `bybit_raw_order_history` windows, **28 of 28 fetched, 0 errors, 0 truncations**; 10 returned `rows_returned`, 4 `no_rows`. **Every arm row is on a Bybit account**, so venue recovery is applicable to all 21 unnamed closes — there is no leg it structurally cannot reach.

## 3. Venue recovery works, and it works far less well here than where it was measured

| | unnamed closes | recovered | rate |
|---|---|---|---|
| **U10's population** (unattributable-price winners) | 17 | 11 | **65%** |
| **this population, both arms** | 21 | 7 | **33%** |
| — treated | 17 | 4 | **24%** |
| — control | 4 | 3 | **75%** |
| — like-for-like (`reconciler_filled` on `bybit_1` only) | 12 | 6 | 50% |
| — — treated | 8 | 3 | 38% |
| — — control | 4 | 3 | 75% |

The failure is at the **match** step, not the order-history step: **12 of the 13** unrecovered treated closes grade `unnameable_no_match` (no corroborated closed-pnl row to join from) and only 1 grades `unnameable_venue_silent`.

Split by the label being recovered from, `reconciler_filled` recovers 6 of 16 (38%) and **`netting_attributed` recovers 1 of 5 (20%)** — consistent with U16's finding that a `netting_attributed` row's `position_size` is *assigned* by `_reconcile_netting_partial_closes` rather than read from a fill, so for such a row there may be **no venue close to find at all**. That is a structural reason, not a fetch problem.

⚠️ **The recovery is asymmetric between the arms, and that is worse than uniformly poor**: the missingness that survives is concentrated in exactly the arm whose outcome is in question. It narrows under a like-for-like restriction (38% vs 75%) but does not vanish — and at n=8 against n=4 that narrowing is not itself a result.

## 4. The result: p = 0.0486, and it is not a finding

| arm | n | gradeable from the journal | + venue | still unnameable | stops | stop rate |
|---|---|---|---|---|---|---|
| treated | 27 | 10 | **14** | **13 (48%)** | 11 | **0.786** |
| control | 12 | 8 | **11** | 1 (8%) | 4 | **0.364** |

Two-sided Fisher on the gradeable subset: **p = 0.0486**.

**Imputing the unnameable closes at both extremes:**

- treated stop rate ∈ **[0.407, 0.889]** (n=27)
- control stop rate ∈ **[0.333, 0.417]** (n=12)
- the bands **overlap** — treated-low `0.407` sits *below* control-high `0.417`
- p ranges **0.0009 … 1.0000**; in the worst case the **sign reverses** and p is flat 1.0

So **the dose-response is still not gradeable.** Venue recovery moved treated gradeability from 10 to 14 of 27, and that is not enough: the 13 remaining unnameable treated closes are sufficient, on their own, to reverse the direction of the effect.

⚠️ **`p = 0.0486` is precisely the number a reader would lift out.** It sits just under 0.05, it has the sign MI-275's dose test had, and it is computed over a population selected by *whether anyone could name the close* — while an unlabelled stop is exactly what a `reconciler_filled` close looks like. The instrument therefore refuses to print it without the band, and grades the verdict `not_gradeable_missingness_dominates`.

The criterion is **sign-survival**, deliberately the weakest reasonable test: it invents no alpha and asks only that the direction hold under the extremes the missing data permit. Failing *that* is decisive rather than a matter of where a threshold was put.

## 5. What this changes on the backlog row

⚠️ **The row's `next_action` is wrong and steers the next reader into the refuted exit.** It reads *"Record the e35 experiment as unfalsifiable on its declared controls (option a); do not propose routing the shadow leg live"* — while the row's own newest `updates` entry (U11) says **"NOT EXIT (a)"** and names the control that can report. `next_action` is the field a session reads first. It is corrected in this change.

**Neither offered exit is taken here either, and the reason is now measured rather than argued:**

- **not (a)** — the experiment is not unfalsifiable on its controls; the control reports, and after recovery it is 11 of 12 gradeable.
- **not (b)** — a different control is not what is missing. The control arm is nearly fully attributable; **the treated arm is not.**

**What the exit now is:** the dose-response becomes gradeable when the treated arm's unnameable share falls far enough that the band excludes the null — which needs the exit mechanism **persisted at close time**, not recovered afterwards. That is U10's Tier-2 proposal (`order_monitor`, persist `stop_order_type` + `cancel_type` beside `exit_price_source`, with a consumer), and this unit is **not** evidence for it in the sense that row's criterion requires: a retrospective join is explicitly excluded there. What this unit adds is a **measured ceiling on the retrospective route** — 33% overall, 24% on the arm that matters — so the Tier-2 build can no longer be deferred on the theory that a join would do.

## 6. What this does not establish

- **It does not clear** `BL-20260912-FOR-55-PERCENT-OF-WINNERS-THAT-MISSED-TARGET-THE-MECHANISM-THAT-STOPPED-THE-RUN-IS-NOT-IN-THE-JOURNAL`, and must not be cited toward it. That row requires the field persisted at close time and branched on by a reader; a retrospective re-grade is excluded by name.
- **It does not say the e35 stop-side effect is absent.** MI-278 U15 found the sign positive in all six combinations of its two degrees of freedom, and MI-275's dose test inverted. This says only that *this* comparison, on *this* window, cannot adjudicate it.
- **It proposes no config change.** Routing `htf_pullback_trend_2h` to `execution: live` would make the widened arm report and is Tier-3; it is not proposed here, exactly as the row already says.
- **n is 27 and 12 throughout**, and the like-for-like recovery comparison is n=8 against n=4. Nothing here would survive being quoted without those denominators.
