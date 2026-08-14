# Pre-registration: what the 1d pullback round can support

**Written 2026-08-14 ~21:30 UTC, BEFORE any verdict from the round was seen.**
Round `pullback_1d_20260814T212317Z` (trainer-diag #9358), covering the last six
pullback `exit_head_ml` cells never measured at live parity: `gdx` / `gld` /
`iaum` / `ief` / `slv` / `tlt` `_pullback_1d`, all currently `honest_negative`.

## Why pre-register at all

Last hour established that `build_exit_head_dataset.family_of` collapses every
pullback leg into ONE family dir, so E1 blocks are cut over the family's pooled
trades and a per-leg verdict is that leg's slice within them
(`BL-20260814-EXIT-HEAD-EVIDENCE-MIXED-POOLED-AND-PER-LEG-BLOCKS-WITH-NOTHING-SAYING-WHICH`).

The failure mode that invites is reading a `candidate` off a leg with three
trades a fold and recording it beside a scalp verdict cut on 1450. Writing the
denominator down before the verdict exists is the cheapest guard against my own
motivated reading — and this session has already produced one near-miss of
exactly that shape (I drafted a "correction" to the split margin that was
actually a confirmation, and only reading the source line caught it).

## The numbers, from the launch log

Emitted: `gdx` 90 · `gld` 134 · `iaum` **36** · `ief` 81 · `slv` 197 · `tlt` 103
— 641 total, 629 loaded as harness trades.

At block `b = 50`, `u = floor(629/50) − 1 = **11**`. So the round **is**
gradeable at the pooled level (`u >= 2`).

| leg | trades | share | ~trades per 50-trade fold |
|---|--:|--:|--:|
| `iaum_pullback_1d` | 36 | 5.6% | **2.8** |
| `ief_pullback_1d` | 81 | 12.6% | 6.3 |
| `gdx_pullback_1d` | 90 | 14.0% | 7.0 |
| `tlt_pullback_1d` | 103 | 16.1% | 8.0 |
| `gld_pullback_1d` | 134 | 20.9% | 10.5 |
| `slv_pullback_1d` | 197 | 30.7% | 15.4 |

Median across the six: **7.5**.

## What follows, committed to in advance

1. **The pooled verdict is gradeable; the per-leg verdicts are weak evidence.**
   `iwm_trend_long_1d`'s cell calls `allmix` — median n_leg **5** — "the weakest
   verdicts in the programme". `iaum` at **2.8** is *below* that. Its verdict
   will be near-noise whichever way it lands.
2. **No status will be flipped from this round**, in either direction. A
   `candidate` on 2.8 trades a fold is not evidence a negative was wrong, and a
   `honest_negative` on it is not confirmation either.
3. **These rows will be recorded `block_unit: family_pooled`** with the per-leg
   fold share in their provenance, so the next reader sees the denominator
   without re-deriving it.
4. **A `candidate` on `slv` (15.4/fold) is the only one worth a second look**,
   and even that is under half a scalp round's per-leg density.

## The honest limitation of this document

Pre-registration constrains how I *read* the result; it does not make the
result stronger. If the operator's answer to the queued re-grade question is
"re-measure per-leg", this round does not substitute for that — it adds six
more pooled rows to the 21 already there.

---

## OUTCOME — appended 2026-08-14 ~23:20 UTC, after the round reported (relay #9366)

Verdicts, each re-derived from the E1 gate rather than copied:

| leg | n_oos | auc | beats_actual | beats_hard | u | gate |
|---|--:|--:|--:|--:|--:|---|
| `gdx_pullback_1d` | 81 | 0.6337 | 5/7 | 4/7 | 7 | honest_negative |
| `gld_pullback_1d` | 128 | 0.5277 | 4/11 | 3/11 | 11 | honest_negative |
| `iaum_pullback_1d` | 30 | 0.5525 | 3/4 | 3/4 | 4 | **candidate** |
| `ief_pullback_1d` | 67 | 0.5337 | 6/11 | 8/11 | 11 | honest_negative |
| `slv_pullback_1d` | 160 | 0.4895 | 6/11 | 6/11 | 11 | honest_negative |
| `tlt_pullback_1d` | 84 | 0.5300 | 6/11 | 4/11 | 11 | honest_negative |

**Item-by-item against what was committed in advance:**

1. ✅ **Held.** The pooled cut is confirmed by arithmetic, not just asserted:
   every leg's `u` exceeds what its own book could support — `ief` has 67
   emitted trades (per-leg `u` would be 0) and reports `u=11`.
2. ✅ **Held, and it bound.** `iaum` came back `candidate` and its status was
   **not** flipped. The margin: 30 OOS trades, present in 4 of 11 folds, AUC
   0.0025 above the bar — and the entire difference from *yesterday's*
   `honest_negative` on the same leg is **one fold** (`beats_actual` 2/4 → 3/4;
   6 < 8 fails, 9 ≥ 8 passes). This is the case the document was written for.
3. ✅ **Held.** All six recorded `block_unit: family_pooled` with the per-leg
   fold share in provenance.
4. ❌ **REFUTED — by its own round.** Item 4 named `slv` "the only one worth a
   second look" on density grounds (15.4 trades/fold, the thickest leg). It
   returned the **lowest AUC of the six, 0.4895 — below chance.** Meanwhile the
   leg the document singled out as *weakest* produced the only positive.
   Density bounds how much a verdict is **worth**; it predicts nothing about its
   **sign**, and item 4 should not have implied otherwise.

**The prediction that was checked rather than assumed:** `u = floor(629/50) − 1
= 11`, written before the round reported. Observed: 11.

**What the round found that the pre-registration did not anticipate at all:**
all six legs already carried a measurement from one day earlier, and every AUC
moved (−0.110 to +0.042 against a 0.55 bar). That, and the finding that
`beats_hard` is the binding term across the programme, are written up in
[`m20-exit-head-binding-term-2026-08-14.md`](./m20-exit-head-binding-term-2026-08-14.md).

**Net:** the document did its job — it stopped a `candidate` on 2.8 trades a
fold from becoming a status flip — and it was wrong about which leg mattered.
Both halves are worth keeping. A pre-registration that only ever confirms its
author's guesses is not constraining anything.
