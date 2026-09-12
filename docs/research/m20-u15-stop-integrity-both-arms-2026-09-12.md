# MI-278 U15 — the stop-out difference-in-differences is 4.3–28.2pp depending on two choices nobody declared, and I published one of them an hour ago

> **Doc status:** `unknown` · category `research` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> **Unit:** MI-278 U15 · object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · cycle `CY-20260906-TRADING-TRUTH`
> **Discharges** `BL-20260911-A-TRAILED-STOP-OUT-IS-ATTRIBUTED-TO-THE-DECLARED-GEOMETRY-THAT-DID-NOT-END-IT` **for the analysis that most needed it — my own.**
> **Tier-1.** One new `scripts/research/` module, read-only over the journal. No `src/`, no config, no order path, nothing enacted.

---

## 0. The answer, in six sentences

`BL-20260911-A-TRAILED-STOP-OUT-IS-ATTRIBUTED-TO-THE-DECLARED-GEOMETRY-THAT-DID-NOT-END-IT` requires that any analysis attributing a stop-out to declared bracket geometry **separate exits at the entry-declared stop from exits at an amended stop and state the split**, and forbids dropping the amended ones silently because the count is load-bearing. **MI-278 U5 published a stop-rate difference-in-differences of +21.2pp forty minutes before this unit started, and it does not state the split.** Applying it: under U5's own row selection the declared-only DiD is **+7.6pp**, so **the split removes about two-thirds of the published effect**. A second, entirely separate degree of freedom turned up while measuring the first — **which fan-out row is `rows[0]`**, which U5 itself identified as undefined for excursion windows and which turns out to move the *adjudication* too — and across the two choices together the DiD ranges **4.3pp to 28.2pp on identical data**. The sign survives every combination, so an e35-specific stop effect is real; its magnitude is a third to a half of what U5 reported, and at these cell sizes (the e35 pre cell holds **2** stop-outs in total) it is not a number to make a Tier-3 decision on. **Trailing tightens the control arm too** — 9 of 31 pre and 8 of 41 post — so this was never an e35 phenomenon, and a split computed on the treated arm alone could not have shown that.

---

## 1. Population, stated first

| | |
|---|---|
| **Source** | `/api/diag/journal` `trades` + `order_packages`, `limit=1000` each, pulled 2026-09-12T16:0xZ |
| **Unit** | **order package**, never the trade row |
| **Population rule / split** | `bleed_attribution_2026_09_11.population`, split on `created_at` vs `2026-08-30T08:53:19+00:00` — both **imported**, not restated |
| **Treated** | `e35` — 18 pre / 17 post packages |
| **Control** | `untouched_control` — 62 pre / 71 post packages |
| **Skipped** | **0 in both arms** — every package graded |
| **Declared stop** | `order_packages.exit_plan.stop.price`. **Never `pkg.sl`**, which may already carry a trailed value — falling back to it would grade an amended stop as the declared one, which is the defect itself |
| **Tolerance** | 0.02 ATR (MI-275's), so a broker tick and a float round-trip both count as unmoved |

**Reproduction control: PASS.** The four-state rule lives inline in `CF.build_units` and cannot be imported, so re-implementing it is forced — and a second copy of a classification is exactly what drifts. `--verify-against-cf` re-derives MI-275's own recorded verdicts through this file's function: **7 shared packages, 0 disagreements.** An empty intersection is reported as `no_overlap_cannot_verify`, never as a pass.

---

## 2. The split, both arms

| arm | era | packages | stop-outs | **at declared** | **at amended** | ungradeable |
|---|---|---|---|---|---|---|
| **e35** | pre | 18 | 2 | 2 | 0 | 0 |
| **e35** | post | 17 | 8 | **6** | **2** | 0 |
| **control** | pre | 62 | 31 | 22 | **9** | 0 |
| **control** | post | 71 | 41 | 33 | **8** | 0 |

Every amended stop-out in both arms is `stop_amended_tighter`; there are **no** wider amendments and **no** ungradeable rows anywhere.

⚠️ **Trailing is not an e35 phenomenon, and only the control arm could show that.** The control amends **29% (9/31)** of its pre-era stop-outs and **20% (8/41)** of its post-era ones — comparable to, and in the pre era far above, the treated arm. MI-275's census, correctly scoped to e35, reported 2 of 7 post and reads as an e35 characteristic; against the control it is simply what trailing does.

---

## 3. The two undeclared choices, and what each is worth

### 3.1 The stop-integrity split

Removing stop-outs the declared geometry did not end:

| arm | Δ rate, all stop-outs | Δ rate, declared only |
|---|---|---|
| e35 | +35.9pp | **+24.2pp** |
| control | +7.7pp | **+11.0pp** |

The treated arm's delta **shrinks** and the control's **grows**, because e35's amended share is concentrated in its post cell (2 of 8) while the control's falls slightly across the split. Both move the DiD the same way.

### 3.2 Which fan-out row is `rows[0]`

MI-278 U5 established that a package's sibling rows do not close together (5 of 35 e35 packages spread over an hour, widest 30.9h) and that `rows[0]` is input order. It treated that as a window question. **It is also an adjudication question**: `adjudicate_exit` grades one row, and which row it grades changes whether the package counts as a stop-out.

Measured across the three defensible selections:

| row selection | DiD, all stop-outs | DiD, declared only |
|---|---|---|
| population order — **what U5 published** | **21.2pp** | **7.6pp** |
| earliest close | 28.2pp | 13.2pp |
| latest close | 17.9pp | 4.3pp |

⚠️ **The treated arm is insensitive to this — 35.9pp under all three.** The entire swing comes through the **control** arm (7.7 / 14.8 / 18.0pp), because fan-out packages with divergent closes are concentrated there. A study that had only the treated arm would have seen a perfectly stable number and had no way to know.

### 3.3 Together

**The headline ranges 4.3pp to 28.2pp on identical data**, a factor of 6.5, across two choices neither of which is a fact about the market and neither of which was declared.

---

## 4. What this means for the published number

**MI-278 U5's +21.2pp should be read as +7.6pp** — same row selection, with the split the backlog row requires. It is not a retraction of the direction: **the sign is positive under all six combinations**, so an e35-specific effect on the stop side is real and this unit does not overturn it.

What changes is what may be built on it:

- The magnitude is **a third to a half** of what was published.
- The **e35 pre cell holds 2 stop-outs in total** (and 2 of 2 at the declared stop). A difference-in-differences whose treated baseline is 2 observations is a direction, not an estimate, and the confidence interval was never computed because at n=2 it would span everything.
- U5's own conclusion is **untouched**: the winner-size collapse was acquitted of e35 on the *excursion* evidence, which is a fixed window from entry and does not depend on stop adjudication at all. This unit corrects the *stop-side* number that sat beside it.

---

## 5. What this does NOT establish

- **It does not clear `BL-20260911-A-TRAILED-STOP-OUT-IS-ATTRIBUTED-TO-THE-DECLARED-GEOMETRY-THAT-DID-NOT-END-IT`.** ⚠️ Its criterion is *"any analysis"*, which is a standing obligation on future work, not a state this unit can reach. What is discharged is the obligation **for the analyses that exist today**; the row is updated, not resolved.
- **It does not say which row selection is right.** Earliest, latest and population order are all defensible and this file does not rule. It says the choice is worth ~10pp and must be declared. Recommending one would need an argument about what a fan-out package's exit *is*, which nobody has made.
- **It does not establish that trailing is harmful.** A tightened stop that ends a trade may have saved a larger loss, and nothing here measures that counterfactual. The *rate* at which amends happen is separately unauditable — `checked: docs/research/RESEARCH-CAPABILITY-INDEX.md` and `checked: scripts/research/` (grep for `modify_open_order`/`trailing`: `exit_census.py`, `r_contamination_audit.py`, `winner_size_collapse_2026_09_12.py` and this file all read the POST-HOC `trades.stop_loss`, none reads an amend event) plus `checked: src/web/api/routers/diag.py` (no amend surface; the nearest is the `protection_repair_*` stamp, which the journal's own contract says deliberately does NOT record an ordinary strategy-driven trailing amend). So what this file measures is **whether the final stop differs from the declared one**, which is an OUTCOME; how many amends produced it is not recoverable from any surface found. That is `BL-20260908-A-TRAILING-AMEND-HAS-NO-DURABLE-RECORD-SO-ITS-RATE-IS-UNMEASURABLE`'s subject, and this unit neither closes nor needs it.
- **It does not touch the excursion result**, for the reason in §4.
- **It is not significant at these n**, and no p-value is offered — one computed on an e35 pre cell of 2 would be a decoration.
- **Nothing about real money.** Both arms are dominated by paper accounts.

---

## 6. Reproducing

```
python3 scripts/research/stop_integrity_both_arms.py --self-test            # 22 controls, no network
python3 scripts/research/stop_integrity_both_arms.py \
    --trades trades.json --packages packages.json \
    --verify-against-cf cf.json --out u15.json
```

`--verify-against-cf` takes a `stop_width_counterfactual_2026_09_11.py --out` JSON and is the reproduction control; run it, because the classification is a second copy and the only thing keeping it honest is that it is checked.
