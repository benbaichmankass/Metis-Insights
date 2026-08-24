# Does a declared bracket target actually exit the trade, or does the clamp get there first?

**Date:** 2026-08-24 · **Tier:** research only, nothing applied · **Scope:**
the four clamp-dominated donchian 4h legs, read off the existing 2,204-row
`e35-bracket-corpus.jsonl`. **No config touched, no model promoted.**

Follows `bracket-expectation-construction-2026-08-23.md` § 6.5, which measured
that on 5 of 10 sentinel legs the 9.9% venue clamp is hit **1.7–3.9× more often
than the trail** — a hard target nobody chose. § 6.5's coherence check proposed
a construction rule from a single cell:

> **declare what the venue was already placing, and tighten the stop until it is
> reachable.**

This tests that rule against every cell already measured, rather than proposing
it from one. Reproduce with
`scripts/research/bracket_reachability_audit.py --corpus docs/research/e35-bracket-corpus.jsonl`.

## 0. The headline

1. **The rule has been GRADED once, not zero times, and it passed.** Of 200
   reachable cells across the four legs, **3 were ever graded** (1.5%). One
   passed: `trend_donchian_sol_4h tp1.5_sm2_to96`, the § 6.5 cell.
2. **Six of the seven Path B passes in the whole corpus declare NO target.** The
   corpus's entire graded case for *declaring* an expectation is that one cell.
3. **15.9% of gradeable declared-target cells are provably cosmetic** — 25.0% on
   the four 4h legs — byte-identical to declaring nothing.
4. **The live stop is not in the corpus at all**, so *"declare a target and change
   nothing else"* is unmeasured on all four legs.
5. **Declaring a target helps on two legs and hurts on two.** There is no
   donchian-4h family answer; there are four leg answers.

## 1. Population, stated

| | |
|---|---|
| corpus rows | 2,204 |
| joint (`tp_r` + `stop_mult`) cells with a book | 1,540 |
| **graded by the gate** | **92 (4.2%)** |
| ungraded — *we did not look* | 2,112 |
| legs with a measured `cap_r` basis | 9 |
| legs without one | 2 (`trend_donchian_{eth,sol}_prop`) |

`xrp_pullback_2h` is in § 6.5's clamp-dominated five but has **0 rows here** — it
is a pullback leg and was never in the donchian corpus. It is also **no longer a
sentinel**: PR #10171 gave it `tp_r: 3.0`, which the clamp then refuses. So this
doc covers **4 of those 5 legs**, and the fifth is unmeasured on this axis rather
than negative.

## 2. Two axes, and they must not be merged

| axis | basis | states |
|---|---|---|
| **truncation** | DERIVED — `tp_r` vs `cap_r`, rescaled from the § 6.5 measured median | `reachable` · `truncated` · `no_cap_basis` · `no_target_declared` |
| **cosmetic** | OBSERVED — byte-identical net_R **and** max_dd to the same stop with no target | `cosmetic` · `not_cosmetic` · `no_baseline` |

`cap_r` is a **median over the leg's trades**. ATR varies per trade, so a `tp_r`
above the median still binds on the low-ATR ones: `truncated` means *clamped on
more than half the trades*, never *never reached*. The cosmetic test needs no
such qualifier — it is an identity.

**The one-way implication is asserted, not assumed:** every cosmetic cell must
also be truncated. It **holds 40/40** on cells that have a cap basis; 9 further
cosmetic cells are **unverifiable** (no measured `cap_r` for their leg) and are
counted as unverifiable, never as holding. Many truncated cells are *not*
cosmetic — the median is not the maximum, which is exactly the qualifier above
showing up in the data.

⚠️ **This checker was wrong first.** Its first version tested
`truncation != "truncated"`, which lumps *we could not look* in with *we looked
and it was reachable* — and it refused on nine cells whose only property was an
unmeasured leg. That is the collapse the module exists to prevent, committed in
the module that prevents it. Fixed, pinned by a self-test that fails against the
old predicate.

## 3. A quarter of the declared targets do nothing

**49 of 308** cells that have a same-stop no-target baseline are cosmetic
(**15.9%**); restricted to the four 4h legs, **28 of 112 (25.0%)**. The other
1,232 joint cells have no baseline to compare against and are graded
`no_baseline` — *we could not look* — not `not_cosmetic`.

A worked instance, `trend_donchian_ada_4h` at `sm1.5` (cap_r 2.62R):

| declared `tp_r` | 1.0 | 1.5 | 2.0 | 2.5 | 3.0 | 4.0 | **6.0** | *(none)* |
|---|---|---|---|---|---|---|---|---|
| net_R | 21.97 | 18.64 | 36.37 | 37.63 | 38.49 | 54.90 | **55.47** | **55.47** |

`tp_r: 6.0` is not a weak expectation. It is **the same run** as declaring
nothing, to the last decimal. This extends the operator's premise rather than
restating it: *a sentinel target is the absence of an expectation* — and **a
target above the clamp is equally absent, while looking like a decision in the
config.**

## 4. Does declaring a target help? Per leg, and the family does not agree

Best non-cosmetic declared-target cell vs the **same stop** with no target
(no-timeout cells only, so the comparison isolates the target):

| leg | sm1.5 | sm2.0 | sm3.0 | sm3.5 | verdict |
|---|---|---|---|---|---|
| `trend_donchian_sol_4h` | **+14.77** | **+13.32** | **+14.24** | **+5.39** | helps at 4/4 stops |
| `trend_donchian_eth_4h` | **+7.37** | **+6.26** | **+2.31** | **+1.56** | helps at 4/4, decaying as the stop widens |
| `trend_donchian_ada_4h` | −0.57 | +2.15 | −1.28 | −1.59 | hurts at 3/4 |
| `trend_donchian_xrp_4h` | +1.76 | −0.71 | −0.34 | +0.10 | flat — and the leg is negative at **every** geometry (−10.8 to −23.5R) |

The eth_4h gradient is the § 6.5 mechanism visible directly: a tighter stop
raises `cap_r`, so more of the declared target is reachable, so declaring it is
worth more. **On xrp_4h there is no expectation to construct** — no geometry in
140 cells makes the leg positive, which is a statement about the leg, not the
bracket.

## 5. The graded evidence is one cell — and the argmax is not it

The seven `path_b_wf_pass` rows are **six distinct configurations**
(`ada_4h sm1.5` and `sm1.5_to400` are byte-identical — the 400-bar timeout is
inert on that leg). **Six declare no target at all.**

| leg | cell | declares a target? | net_R | maxDD_R | MAR | wf |
|---|---|---|---|---|---|---|
| `trend_donchian_sol_4h` | `tp1.5_sm2_to96` | **yes — 1.5R, reachable (cap 1.80)** | 58.34 | 6.92 | **8.43** | 4/6 |
| `trend_donchian_ada_4h` | `sm1.5` | no | 55.47 | 19.01 | 2.92 | 5/6 |
| `trend_donchian_1h` | `sm2_to96` | no | 49.29 | 29.95 | 1.65 | 6/6 |
| `trend_donchian_sol_4h` | `sm1.5` | no | 43.45 | 14.06 | 3.09 | 4/6 |
| `trend_donchian` | `sm2` | no | 42.72 | 26.73 | 1.60 | 5/6 |
| `trend_donchian_ada_4h` | `sm2` | no | 36.14 | 11.04 | 3.27 | 5/6 |

On `sol_4h` the two passing cells are the same leg with and without a declared
target: **MAR 8.43 vs 3.09**. That is the construction rule's only head-to-head,
and it wins it.

Per-leg grading coverage of the reachable cells:

| leg | reachable cells | ever graded | outcome |
|---|---|---|---|
| `trend_donchian_eth_4h` | 60 | 1 | `tp3_sm1.5_to48` → is_oos_fail |
| `trend_donchian_xrp_4h` | 65 | 1 | `tp1.5_sm1.5` → is_oos_fail |
| `trend_donchian_ada_4h` | 40 | **0** | never graded |
| `trend_donchian_sol_4h` | 35 | 1 | `tp1.5_sm2_to96` → **path_b_wf_pass** |

**200 reachable cells, 3 graded.** The rule is not *disproven* on three of four
legs; it is **unmeasured** on them.

## 6. A hypothesis I had, and the data refuted it

`_gate_candidates` picks **top-N per axis by `net_total_r`** — selection on the
argmax, which #10213 had just shown failing (best full-history cell 0/5 splits,
third-best generalizes). I predicted reachable cells would rank systematically
low on net_R, biasing them out of grading.

**Refuted.** Mean percentile of reachable cells within their leg's joint surface:

| leg | eth_4h | xrp_4h | ada_4h | sol_4h |
|---|---|---|---|---|
| mean percentile | 45.7% | 36.1% | 48.7% | **65.1%** |

At or above the middle, and highest on the leg where declaring a target helps
most. The top-5 joint cells are mostly reachable on three of four legs. The real
explanation is duller and needs no bias: the gate grades ~4% of the surface **by
design**, and reachability was never a selection criterion.

## 7. ⚠️ The gap that limits all of it

**The live stop, `atr_stop_mult: 2.5`, is absent from the joint axis on all four
legs** — the grid is `{1.5, 2.0, 3.0, 3.5}`. So every number in § 4 is *"declare
a target **and** move the stop"*. The isolated question — *does declaring a
target at the geometry we actually run help?* — **has never been measured**, on
any of the four.

That is not fatal to § 6.5's rule, which is explicitly a **joint** change ("one
change, two effects, and they are the same change"). It is fatal to reading § 4
as evidence about targets alone. Filed.

## 8. What I am proposing, and what I am not

**Not proposing:** any config change. One graded cell on one leg is not a fleet
rule, and § 7 means the cheapest version of the change is unmeasured.

**Proposing, in order:**

1. **Add `atr_stop_mult: 2.5` to the joint grid** and re-run the four legs. Cheap,
   and it separates "the target helped" from "the stop helped" — currently
   inseparable.
2. **Grade reachable cells explicitly**, rather than relying on a net_R top-N to
   surface them. 3 of 200 is not a sample.
3. **Never ship a cosmetic target.** A declared `tp_r` above the leg's `cap_r`
   should be a config-time refusal or at minimum a warning: it reads as an
   expectation and is byte-identically nothing.

⚠️ **The criterion goes first.** Per the donchian § 6.0b lesson — a shortlist was
withdrawn there precisely because its criterion was fixed after the candidates
were measured — whatever gate decides "this target ships" must be written down
**before** step 1 runs, not chosen from its output.

## 9. Filed

- `PB-20260824-JOINT-GRID-OMITS-THE-LIVE-STOP-SO-TARGET-EFFECT-IS-UNSEPARABLE` (§ 7)
- `PB-20260824-COSMETIC-TARGETS-ABOVE-CAP-R-SHIP-AS-DECLARED-EXPECTATIONS` (§ 3)
