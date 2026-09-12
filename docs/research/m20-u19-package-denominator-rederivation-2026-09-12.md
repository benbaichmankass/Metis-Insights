# MI-278 U19 — the dose result re-derived per package: the conclusion survives, its strength does not, and my own U18 verdict flips on a 0.004 margin

> **Doc status:** `unknown` · category `research` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> **Unit:** MI-278 U19 · object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · cycle `CY-20260906-TRADING-TRUTH`
> **Pays clause 1 of** `BL-20260911-A-RATE-PER-TRADE-ROW-COUNTS-ACCOUNT-FANOUT-AS-INDEPENDENT-AND-INFLATES-N-UNEQUALLY` **for the one surface MI-278 U12 named as most load-bearing, and audits its own author's just-published result.**

## 1. The debt, and who named it

U12 discharged that row's second clause (name the surfaces) and left the first — *a price-behaviour rate states its distinct package count beside its row count, or is computed per package* — explicitly unpaid, naming where it matters most:

> `stop_width_counterfactual_2026_09_11.py`'s docstring states *"Every rate in this script is therefore reported per package"* … and `dose_response()` computes `win_rate = wins/n` and `stop_rate` with `n = len(rs)` (ROWS) while printing the distinct package count on the line immediately above. **That is the function that produced the inverted dose result** which `BL-20260911-BOTH-OF-THE-E35-EXPERIMENTS-DESIGNED-CONTROLS-HAVE-ZERO-OBSERVATIONS` cites as evidence against a stop-width mechanism. … a p<0.0001 on a row denominator inside a file promising package denominators **should be re-derived before it is leaned on again.**

**Verified at `origin/main` `331bd2039` before acting**, not inherited: line 87 carries the promise, `dose_response()` sets `n = len(rs)`, and `"packages"` is printed on the adjacent line. Nobody had re-derived it.

## 2. Population and method

- Trades: `/api/diag/journal?table=trades&limit=1000`, pulled **2026-09-12T20:0xZ** (1000 rows, `2026-08-18T02:07:30Z` → `2026-09-12T20:00:15Z`).
- **The bucketing, population, era split and adjudicator are CF's/BA's own, imported unmodified** (`population`, `group_of`, `era_of`, `adjudicate_exit`, `E35_LEGS`, `wilson`, `fisher_2x2`). A difference below is therefore **the denominator** and cannot be a different population.
- **The reduction is the delicate part and is a first-class output, not an implementation detail.** Collapsing a package to one observation means choosing among its rows — and MI-278 U15 measured that *which* row you choose swings a headline by 4.3–28.2pp. So this never picks: a package is `unanimous` (its gradeable rows agree), `disagreement` (they do not — **reported, never arbitrated**, and in neither numerator nor denominator), or `ungradeable`. An *ungradeable* row beside a gradeable one does not manufacture a disagreement: *we could not look* is not a competing opinion.

## 3. The named cell, both ways

`tightened_to_2_from_2.5 (ratio 0.80)` — the cell that carried the p<0.0001:

| | rows | packages | inflation | stop rate (rows) | stop rate (packages) |
|---|---|---|---|---|---|
| pre | 17 | **9** | **1.889×** | 0/17 = **0.000** | 0/7 = **0.000** |
| post | 15 | **7** | **2.143×** | 12/15 = **0.800** | 5/7 = **0.714** |
| Fisher p | | | | **≈ 0** (reported `0.0`) | **0.021** |

**The conclusion survives and its strength does not** — the p moves by roughly two orders of magnitude and lands just inside 0.05. That is exactly the *"robustness finding, not a refutation"* the backlog row predicted, now measured for the specific cell that was being leaned on.

**And the dose INVERSION survives**, which is the part that matters for `BL-20260911-BOTH-OF-THE-E35-EXPERIMENTS-DESIGNED-CONTROLS-HAVE-ZERO-OBSERVATIONS`:

| dose | inflation | stop rate (packages) |
|---|---|---|
| `tightened_to_1.5_from_2.5` (40% tighter) | **1.000×** | 0/7 → 2/8 = **0.250** |
| `tightened_to_2_from_2.5` (20% tighter) | 1.889 / 2.143× | 0/7 → 5/7 = **0.714** |

The 20% tightening still shows the larger effect than the 40% one. **So MI-275's inverted dose — the evidence against a stop-width mechanism — is not an artifact of fan-out.** ⚠️ Note the arms of *that* comparison are themselves inflated maximally unequally (1.000× against ~2×), which is precisely why it needed checking rather than assuming.

⚠️ **A finding row-counting hides completely: the PRE cell holds 2 of 9 packages whose own rows DISAGREE about the exit.** The perfect `0/17` zero sits in the most inflated cell in the table, and two of its packages contain rows that contradict each other about what ended the trade. Per row those contradictions are simply counted twice on opposite sides of nothing; per package they are visible and excluded.

## 4. My own U18, re-derived — and the two criteria disagree

MI-278 U18 published `p = 0.0486` and a verdict of `not_gradeable_missingness_dominates` roughly forty minutes before this unit began. Its arms are inflated **maximally unequally**: treated **1.421×** (27 rows / 19 packages, 5 spanning >1 account), control **1.000×** (12 / 12, 0 cross-account).

Re-derived with U18's **own** adjudication (journal label first, venue second — swapping the adjudicator as well as the denominator would make the difference unattributable):

| | treated | control | Fisher p | verdict |
|---|---|---|---|---|
| **published, rows** | 11/14 = 0.786 | 4/11 = 0.364 | **0.0486** | `not_gradeable_missingness_dominates` |
| **re-derived, packages** | 8/11 = **0.727** | 4/11 = 0.364 | **0.1984** | sign survives — `gradeable` |

- band, treated **[0.421, 0.842]** · control **[0.333, 0.417]** · **sign survives by +0.0044**

⚠️ **I predicted the opposite on the coordination board and I was wrong, in one of the two directions.** I said shrinking the treated n would make the comparison *less* gradeable and leave U18's headline strengthened. The p did weaken as expected (0.049 → 0.198, no longer nominally significant). But the **band NARROWED** — one unnameable *package* is one unit of uncertainty instead of up to three rows of it — and sign-survival now passes.

**The two criteria therefore disagree, and that disagreement is the real finding.** U18's sign-survival test was chosen because it invents no alpha; this shows it is **also insensitive to n**, so it can pass on a comparison with no power (here, 11 against 11 at p = 0.20, on a margin of four thousandths). **Neither number is quotable alone.** The honest reading is that the e35 dose comparison remains ungradeable — but for a *different* reason than U18 gave: not that missingness reverses the sign, but that there is no power at the package denominator. Filed as its own row rather than buried here.

## 5. What ships

- `scripts/research/package_denominator_rederive.py` — the re-derivation, **29 self-test controls**, imports every upstream instrument unmodified.
- `scripts/research/stop_width_counterfactual_2026_09_11.py::dose_response` — gains `per_package` and a `row_denominator_note` **beside** the existing per-row keys. **Proven additive**: the pre-patch and post-patch functions were both loaded and compared key-by-key over the same population — every pre-existing key is identical, only the two new keys appear. No published figure moves, which is what the row's clause 1 asks for.

## 6. What this does not do

- **It does not touch the three live dashboard routes U12 named** (`/api/bot/stats` `winRate`, `/api/bot/performance`, `/api/bot/attribution`). Those are `src/web/`, Tier-2, and changing a number the operator reads is a decision, not a side effect of a re-derivation.
- **It does not clear the backlog row.** Clause 1 asks that *such rates* state their package count; two surfaces now do, and U12 measured **21** that publish a price-rate over a row denominator. This is progress against a sized list, not completion.
- **It does not re-grade MI-271's headline** — only `dose_response`'s cell, which is what was named.
- **n is 7–9 packages per cell** in the named comparison and 11 against 11 in U18's. Nothing here survives being quoted without those denominators, which is the whole point of the unit.
