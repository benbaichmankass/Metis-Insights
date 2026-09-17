# M20 U50 — two of the three "payments" never touched the surface that publishes the number

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-278 U50** · RESEARCH lane · Tier-1 · works `BL-20260911-A-RATE-PER-TRADE-ROW-COUNTS-ACCOUNT-FANOUT-AS-INDEPENDENT-AND-INFLATES-N-UNEQUALLY` (performance backlog, `severity: high`, `status: open`).

Extends [`scripts/research/fanout_denominator_survey.py`](../../scripts/research/fanout_denominator_survey.py) — **no new instrument**; the survey is the row's own inventory and the payment axis belongs on it. 12 new self-test controls + 14 pytest controls in [`tests/test_fanout_denominator_payment_state.py`](../../tests/test_fanout_denominator_payment_state.py); 4 planted defects, 4 caught.

---

## 1. How this unit started, and the near-miss is the point

The row's `resolution_criteria` ends: *"`e35_break_attribution.py` (a one-sided BINOMIAL p on a row count, the same class) **is the next and is NOT done**."*

**It is done.** MI-278 U32 paid it on 2026-09-13, after U31 wrote that sentence. Had that sentence been acted on, a session would have rebuilt `e35_binomial_package_denominator.py` — the `RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED` class, whose prevention is a one-line existence check.

⚠️ **The machine-readable registry was right the whole time.** `fanout_denominator_survey.DECLARED` never claimed those surfaces were paid; the hand-written sentence inside `resolution_criteria` did. **Field beats comment**, applied to this row's own progress tracking.

## 2. And the prose was wrong in a second, larger way

Checking *what* U31 and U32 actually changed:

| surface | `order_package_id` | any package token | what happened |
|---|--:|--:|---|
| `stop_width_counterfactual_2026_09_11.py` (U19) | 5 | yes | **paid in place** |
| `bleed_attribution_2026_09_11.py` (U31) | **0** | **none** | companion memo only |
| `e35_break_attribution.py` (U32) | **0** | **none** | companion memo only |

**U31 and U32 re-derived their statistics in separate instruments and left the producing surfaces completely untouched.** Anyone who runs `bleed_attribution_2026_09_11.py` today gets the row-denominator rate, with nothing on the surface pointing at the re-derivation.

That is not cosmetic. **U31's own finding was that the e35 pre/post Fisher goes from `p = 8e-05` on rows to `p = 0.05703` on packages — it crosses α.** The surface still prints the significant one.

So the registry gains **`rederived_by`**, which makes the companion discoverable *from the surface list* rather than only from a memo nobody re-reads, and a new **`rederived_elsewhere`** state that is **not** a weaker `paid`. A test pins that such a surface stays counted as exposed.

## 3. The probe that would have manufactured a false finding

The obvious mechanical check is *"does this file mention `order_package_id`?"*. Run against the 5 surfaces that declare a package denominator, it returns **one false positive**:

`scripts/ops/dead_leg_audit.py` declares `states_package_count: True` and **never names that column** — because it counts packages by querying the **`order_packages` TABLE** (`FROM order_packages`, line ~216). The declaration is correct and the probe was wrong.

⚠️ **I nearly filed that as a false declaration.** What stopped it was reading the field instead of trusting the one-liner — a negative from a probe is not proof of absence without a positive control. Widening the probe to the table, `per_package`, `n_packages`, `distinct_packages` and `package_count` takes the false positives to **zero**, and the narrow form is now a **named regression control** in both suites: narrowing it back fails two self-test controls and three pytest controls by name.

## 4. The asymmetry, which is why this can never be a guard

`package_notion()` is deliberately one-directional:

* **`False` is sound and strong.** A file that names a package in no form cannot be stating a package denominator. That is mechanically checkable and needs no human.
* **`True` is necessary and *not* sufficient.** The token can sit in a comment, in an unrelated query, or beside the rate rather than in it. It says nothing about whether the *rate* uses it — the judgement the row itself says *"no checker can read which question is being asked."*

⚠️ **An earlier draft of this module ignored its own docstring.** It named the positive state `paid_in_place` and graded **6 of the 21** exposed surfaces with it — publishing exactly the overclaim the asymmetry exists to prevent. The state is now `package_notion_present_rate_unverified`, and a test asserts `PAID_IN_PLACE` cannot come back.

## 5. The measured state of the debt

Over the **21** exposed surfaces (`price_path` or `mixed`, not deduping by package):

| payment state | surfaces |
|---|--:|
| `package_notion_present_rate_unverified` — ***we did not look*** | 6 |
| `rederived_elsewhere` — companion exists, **surface unchanged** | 2 |
| `no_package_notion` — the sound negative | **13** |
| `unreadable` | 0 |

`declaration_contradictions` returns **0** — and that is one direction only; it does not mean every declaration is correct.

**Three of the 21 are live SPA routes** (`/api/bot/stats` winRate, `/api/bot/performance`, `attribution.py`) and remain **Tier-2** — changing a number the operator reads is a decision and is not proposed here.

## 6. What this does NOT establish

* **No rate is restated and no conclusion is revised.** The payment axis says *where the debt is*, not what any number should be.
* **`package_notion_present_rate_unverified` is not a pass and not a failure** — it is 6 surfaces nobody has read at the rate site.
* **Clause (1) is not cleared.** The row asks that the distinction be *stated where rates are produced*; 13 surfaces still state nothing, and 2 more have a companion they do not point at.
* **Nothing about the 9 `undeclared` surfaces is settled**, including `margin_bound_sizing_basis.py` (this lane's own U45). They stay `undeclared`, which the survey already calls the finding rather than a pass.
