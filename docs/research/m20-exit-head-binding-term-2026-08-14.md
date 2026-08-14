# Which gate term actually binds — and the inference it invites, refuted

**Date:** 2026-08-14 · **Tier:** 1 (measurement over committed artifacts; no live
path, no status flipped, no default changed).

## The question

The M20 `exit_head_ml` programme has recorded 19 negatives across 33 leg-rounds.
"Honest negative" is a verdict, not a diagnosis: it does not say *which* of the
gate's four terms the leg failed. Asking that of the corpus turns out to change
what the negatives mean — and the first answer it suggests is wrong.

## The gate, from source

`scripts/ml/train_exit_head.py::per_leg_summary` — read, not inferred:

```
candidate = (u >= 2
             and mean_auc > 0.55
             and beats_actual_folds * 3 >= u * 2
             and beats_hard_folds  * 3 >= u * 2)
```

`beats_actual` counts folds where the head's best-tau policy returns more net_R
than **the exit the bot actually took**, without a worse max drawdown.
`beats_hard` counts folds where it beats `max(stale_8_0, giveback_1_1)` — the
better of **two hard levers at fixed parameter points**. Note the word *fixed*;
it is load-bearing below.

## Population — 33 leg-rounds

Every row in `docs/research/m20-exit-head-rounds.jsonl`. Not 33 independent
measurements: 27 are `block_unit: family_pooled`, so legs within a family share
fold boundaries. The count is of recorded verdicts, which is the thing being
characterised here.

| failing terms | rounds |
|---|--:|
| *(none — passes gate)* | 14 |
| **`beats_hard` alone** | **6** |
| `auc` + `beats_actual` + `beats_hard` | 6 |
| `beats_actual` + `beats_hard` | 3 |
| `auc` + `beats_actual` | 2 |
| `auc` + `beats_hard` | 1 |
| `auc` alone | 1 |

`beats_hard` fails in **16 of 33** rounds (48%), and it is the sole failing term
in **6 of the 7** single-term failures. `u >= 2` never binds.

## The six, and they are the best-discriminating legs in the corpus

| leg | n_oos | mean_auc |
|---|--:|--:|
| `gdx_pullback_1d` | 81 | 0.6337 |
| `sol_pullback_2h` | 222 | 0.6330 |
| `trend_donchian_eth_4h` | 161 | 0.6285 |
| `trend_donchian_sol_4h` | 157 | 0.6119 |
| `ict_scalp_eth_15m` | 550 | 0.6083 |
| `gld_pullback_1h` | 325 | 0.6010 |

All six above 0.60, in a corpus spanning 0.4895–0.6337. Each clears the AUC bar,
clears `beats_actual` — i.e. **beats what the bot currently does on ≥ ⅔ of
folds** — and fails only against the hard comparator.

## The inference this invites — and why it is wrong

The reading that presents itself: *there is real exit alpha on these six legs,
and a simple hard rule captures it at least as well as the ML head — so ship the
hard lever, drop the head.* It is tidy, it is actionable, and it flatters the
cheaper option.

It is refuted by one lookup. The hard levers' **own** cells on those same legs:

| leg | `stale_stop` | `giveback_stop` |
|---|---|---|
| `sol_pullback_2h` | honest_negative | honest_negative |
| `trend_donchian_eth_4h` | honest_negative | honest_negative |
| `trend_donchian_sol_4h` | honest_negative | honest_negative |
| `gdx_pullback_1d` | honest_negative | honest_negative |
| `gld_pullback_1h` | honest_negative | honest_negative |
| `ict_scalp_eth_15m` | **shipped** | honest_negative |

Eleven of those twelve cells are negatives. The hard levers are not capturing
alpha on five of the six legs — they failed their own gates there.

**The two tests are not the same test, which is exactly how both can fail.**
`beats_hard` is a *per-fold net_R race against two fixed parameter points*
(`stale_8_0`, `giveback_1_1`). The `stale_stop` / `giveback_stop` matrix cells
are verdicts over the *tuned* sweep under their own gate. A fixed configuration
can win a fold-by-fold race while the tuned lever it belongs to fails its own
criterion — different comparator, different aggregation, different bar.

So the honest statement about these six legs is deflationary:

> The head discriminates well and beats current behaviour on most folds, yet is
> edged out fold-by-fold by a fixed hard configuration whose own lever does not
> clear its gate either. **No lever is established as an improvement on these
> legs.** The head's failure mode is losing a race to a rule that also loses.

**`ict_scalp_eth_15m` is the single exception and does not generalise.** There
the `stale_stop` lever genuinely passed and shipped, so on that one leg the
tidy reading holds: a shipped hard rule beats the head, and the head adds
nothing over it.

## Why this is recorded rather than quietly corrected

The tempting version was one sentence from being written into the coverage
matrix as a programme conclusion. What caught it was checking the hard levers'
own cells — the same move that caught the split-margin "correction" earlier
today that was actually a confirmation. Both near-misses share a shape: an
inference that follows from one artifact and dissolves against a second one in
the same repository. Recording the refuted version is the point; a memo that
presents only the surviving claim teaches nothing about how it survived.

## Second finding: the AUC term is not stable at the scale of its own bar

The six 1d pullback legs re-swept tonight each carried a prior measurement from
**one day earlier** (2026-08-13, relay #8963, uncapped). Every AUC moved:

| leg | 08-13 (uncapped) | 08-14 (live parity) | Δ |
|---|--:|--:|--:|
| `gdx_pullback_1d` | 0.5919 | 0.6337 | **+0.042** |
| `ief_pullback_1d` | 0.5376 | 0.5337 | −0.004 |
| `gld_pullback_1d` | 0.5483 | 0.5277 | −0.021 |
| `iaum_pullback_1d` | 0.5927 | 0.5525 | −0.040 |
| `slv_pullback_1d` | 0.5832 | 0.4895 | **−0.094** |
| `tlt_pullback_1d` | 0.6399 | 0.5300 | **−0.110** |

Against a bar of 0.55. `tlt` and `slv` crossed it downward.

The capped numbers are the authoritative ones — they measure the geometry
production actually places, and the prior ones measured a take-profit the live
unit never sets. But **three things changed together**: TP geometry
(uncapped → 0.099 cap), pool size (568 → 629 trades), and fold count
(10 → 11, which re-cuts every block boundary). No component is isolable from
this pair of rounds, and the mixed directions — `gdx` up while five fall — rule
out reading it as a uniform cap effect.

What survives regardless: an AUC in this programme is a point estimate with
movement of this order behind it. Comparing one finely against 0.55 — as the
gate does, and as `iaum`'s 0.0025 margin did tonight — is reading precision the
measurement does not carry.

## Not claimed

- Not that the gate is wrong. `beats_hard` is a defensible bar: a head that
  cannot beat a fixed dumb rule is not worth the machinery.
- Not that the six legs have no alpha. They clear `beats_actual`; that is a real
  signal about current exit behaviour, and it is not what this memo settles.
- Not a proposal to re-weight the terms. Changing a gate after seeing which term
  it fails on is how a gate stops meaning anything.

## Reproduce

Pure read of committed artifacts, no harness run, no VM:
`docs/research/m20-exit-head-rounds.jsonl` (per-round terms) and
`docs/research/exit-refinement-coverage.json` (the `stale_stop` /
`giveback_stop` cells). Re-derive each verdict from the four conditions above
rather than reading the `verdict` field.
