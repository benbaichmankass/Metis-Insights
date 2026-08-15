# Pre-registration: how the fold-offset dispersion arms will be read

**Written 2026-08-15 ~00:55 UTC, BEFORE any arm reported.** Run
`dispersion_*` (trainer-diag #9377): the 1d pullback family at
`--fold-offset` 0/10/20/30/40, block size fixed at 50, same pool, same
`--tp-cap-pct 0.099`.

## Why pre-register this one

The result is the input to a **Tier-3** question — whether the E1 gate's 0.55
AUC bar is being read to a precision the measurement carries. A number I
compute *after* forming a view on that is worth less than one I commit to
reading a fixed way first. The 1d-round pre-registration earlier tonight
earned its keep twice: it stopped a `candidate` on 2.8 trades/fold from
becoming a status flip, and it refuted its own directional hint.

## The control, stated first

**Offset 0 must reproduce**: gdx `0.6337` · gld `0.5277` · iaum `0.5525` ·
ief `0.5337` · slv `0.4895` · tlt `0.5300`.

If it does not, **the arms are not comparable and no dispersion number is
reported.** A mismatch means the feature branch changed E0 emission for this
family, and the spread would then be measuring that instead. This is a
stop condition, not a caveat to note and continue past.

## The statistic

Per leg, over the 5 arms: **spread = max(mean_auc) − min(mean_auc)**.

Reported beside each leg's `u` and `n_oos`, because a spread on `iaum` (u=4,
present in 4 of 11 folds) is not the same claim as one on `slv` (u=11, 160
OOS trades). The headline is the **median spread across the six legs**, not
the max — the max is one leg's worst draw and will be quoted separately.

## What follows, committed in advance

1. **This family is the THIN one, so whatever comes back is an UPPER BOUND**
   on what a well-powered family would show. `u` ranges 4–11 here against 26
   for `pullback_1h`. A large spread does not by itself establish that
   well-powered verdicts are unstable; a small one is the stronger result,
   because it would hold *a fortiori* for thicker families.
2. **No status is flipped from this run, in either direction.** It measures
   the instrument, not the legs.
3. **No gate change is proposed from this run regardless of outcome.**
   Changing a gate after seeing which term it fails on is how a gate stops
   meaning anything. If the spread is large the deliverable is the *number*,
   handed to the operator; the Tier-3 decision is theirs.
4. **Interpretation thresholds, fixed now so the result cannot pick them:**
   - median spread **< 0.01** → the AUC term is stable at the scale of its
     bar; the one-day −0.110 movement was *not* boundary placement, and the
     backlog row should be re-scoped to the other two candidates (TP geometry,
     pool size).
   - **0.01–0.05** → material but smaller than the observed one-day movement;
     boundary placement is a contributor, not the explanation.
   - **> 0.05** → boundary placement alone can move a verdict across the
     0.55 bar, and every AUC-alone `honest_negative` in the matrix is a
     decision taken inside that noise.
5. **`iaum`'s `candidate` is the specific thing to watch.** It cleared the bar
   by 0.0025 at offset 0. If it fails to clear at any other offset, that is a
   direct demonstration — one leg, one gate term, five draws — and it is worth
   more than the aggregate spread.

## The honest limitation

Five offsets out of 50 legal ones is a sample of the partition space, not a
census. The arms are also not independent — they share the same trades, so
this measures re-partitioning sensitivity, **not** sampling error over new
data. Those are different quantities and the readout will not conflate them.
