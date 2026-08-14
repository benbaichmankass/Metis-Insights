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
