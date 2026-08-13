# Deriving E1's `--min-fold-trades` — measured, 2026-08-13

**Operator decision 2026-08-13:** derive the block size from a stated
statistical target rather than leave it an undefended default, and *accept
whatever it says, including stricter than 50.*

**Result, stated first: no block size rescues the short legs, and 50 is not
the defect.** At the effect size the fleet actually exhibits, the per-fold
beat/no-beat vote is close to a coin flip, and the block size trades per-fold
reliability against the number of folds without ever buying a test that is
both powered and specific for a leg under ~300 trades. The recommendation is
therefore **not to change the value** — and to stop treating "raise/lower the
block" as the lever that unblocks `exit_head_ml`.

---

## 1. What the number actually does

`fold_blocks(h_trades, mode="trades", block_n, …)` slices the leg's trades
sequentially into test blocks of exactly `block_n`, starting at `block_n` so
the first block is training:

```
u = max(0, floor(N / block_n) - 1)          usable folds
```

`per_leg_summary`'s gate then requires

```
candidate = u >= 2
            AND mean_auc > 0.55
            AND beats_actual_folds * 3 >= u * 2      (>= 2/3 of folds)
            AND beats_hard_folds   * 3 >= u * 2
```
plus `oos_trades >= MIN_OOS_TRADES` (25, operator-set 2026-08-11).

So `block_n` does **two** jobs in the default mode: it is the test-block size
*and* the minimum each block is checked against — and because blocks are built
at exactly `block_n`, the `thin_test` check passes by construction. It is a
block-size choice wearing a minimum's name.

**Consequence worth stating on its own:** `u >= 2` needs `N >= 3 * block_n`.
At the default that is **150 trades, not 100.** 100 yields exactly one fold,
which the gate rejects. (Now stated in the `fold_blocks` docstring, which was
silent on it.)

## 2. The measurement

Per-trade rows are not persisted, but per-fold aggregates are, so the paired
difference is recoverable: for a fold of `n` trades,
`sum(d_i) = best_tau_net_r − actual_net_r`, hence `d̄ = that / n`.

Pulled every `e1_report.json` on the trainer (relay #9066):
**21 reports · 262 folds, all 262 carrying both arms · 15 (family, timeframe)
groups.** Fold sizes: min 15, median 50, max 343; 63.4% have `n >= 50`.

Within a group, `Var(d̄) ≈ σ_d² / n`, so `σ_d ≈ sd(d̄) · √n̄`:

| group | folds | mean n | mean d̄ | σ_d | δ = d̄/σ_d |
|---|---:|---:|---:|---:|---:|
| ict_scalp_5m 5m | 12 | 50 | +0.168 | 0.520 | **+0.324** |
| ict_scalp_avax_5m 5m | 20 | 50 | +0.117 | 0.671 | +0.175 |
| ict_scalp_sol_15m 15m | 7 | 50 | +0.073 | 0.462 | +0.158 |
| ict_scalp_xrp_5m 5m | 13 | 50 | +0.117 | 0.747 | +0.156 |
| ict_scalp_xrp_15m 15m | 6 | 50 | +0.077 | 0.535 | +0.143 |
| ict_scalp_sol_5m 5m | 16 | 50 | +0.117 | 0.961 | +0.122 |
| ict_scalp_eth_15m 15m | 6 | 50 | +0.057 | 0.519 | +0.109 |
| uso_trend_1h 1h | 3 | 50 | +0.071 | 0.678 | +0.105 |
| donchian 1h | 22 | 214.5 | +0.022 | 1.375 | +0.016 |
| donchian 4h | 66 | 27.0 | +0.034 | 5.287 | +0.006 |
| pullback 1h | 28 | 50 | +0.021 | 1.533 | +0.014 |
| pullback 2h | 15 | 193.6 | −0.082 | 2.006 | −0.041 |
| pullback 1d | 35 | 15.0 | −0.135 | 2.420 | −0.056 |
| allmix 1d | 9 | 60 | −0.146 | 1.582 | −0.092 |
| slv_trend_1h 1h | 4 | 50 | −0.166 | 0.522 | −0.317 |

**σ_d median 0.747R** (range 0.462–5.287). **δ median 0.105**, range
−0.317 … +0.324 — and it straddles zero: five of fifteen groups have a
*negative* mean effect, i.e. the model arm loses.

### The measurement is biased in the model's favour

`_best_tau` takes the **max over ~7 tau arms** per fold, and that is what the
gate uses, so measuring the selected arm measures the right decision — but it
means `d̄` is a maximum of several correlated draws and is biased **upward**.
Every power figure below is therefore **optimistic**: the block sizes derived
are *lower* bounds on what the true effect would require.

## 3. The derivation

A fold votes "beats" when `sum(d_i) > 0`, so
`P(correct vote) = Φ(√b · δ)`, and inverting for a target `p` gives
`b = (z_p / δ)²`. At the median δ = 0.105:

| target per-fold reliability | required block |
|---|---:|
| 0.75 | 41 |
| 0.80 | 64 |
| 0.90 | 149 |

At the current **b = 50**, per-fold reliability is **Φ(√50 · 0.105) = 0.771**.
So 50 sits between the 0.75 and 0.80 targets — a defensible place to be, and
*not* an outlier in either direction.

### But per-fold reliability is not the objective

The gate needs ≥2/3 of `u` folds, and `u` shrinks as `b` grows. Modelling the
whole gate (`P_detect` at δ = 0.105; `P_false` at δ = 0, i.e. a model with no
edge at all):

| N | b=20 | b=25 | b=30 | b=40 | b=50 | b=60 | b=75 |
|---|---|---|---|---|---|---|---|
| **98** | .759 / **.500** | .490/.250 | .515/.250 | — | — | — | — |
| **150** | .708/.344 | .529/.188 | .682/.312 | .558/.250 | .595/.250 | — | — |
| **200** | .684/.254 | .648/.227 | .564/.188 | .733/.312 | .867 / **.500** | .627/.250 | — |
| **300** | .521/.090 | .570/.113 | .768/.254 | .825/.344 | .677/.188 | .807/.312 | .913 / **.500** |
| **600** | .547/.031 | .619/.047 | .726/.084 | .732/.090 | .770/.113 | .903/.254 | .882/.227 |

(cells are `P_detect / P_false`; "—" = ungradeable, `u < 2`)

**Three things fall out, and the second is the important one.**

1. **`P_detect` is not monotonic in `b`.** It zig-zags, because
   `need = ceil(2u/3)` steps discontinuously as `u` changes.

2. **The high-power cells are the high-false-positive cells.** Every apparent
   optimum above — N=98/b=20, N=200/b=50, N=300/b=75 — carries
   `P_false = 0.500`. Those are the configurations where `u` makes `2/3` cheap
   to hit by luck (`u=3` needs 2, `u=2` needs 2). **Maximising `P_detect` over
   `b` would therefore select precisely the settings that are easiest to pass
   by chance** — selection on the outcome, one level up from picking a block
   to unblock legs. This is why the derivation cannot be "pick the b with the
   best power".

3. **For short legs nothing works.** At N=98 the only gradeable options are
   b ∈ {20, 25, 30}, and they deliver either 0.49 power or a 50%
   false-positive rate. No block size gives a test that is both powered and
   specific on ~100 trades at δ ≈ 0.1.

### What `P_false` here is and is not

It is the false-positive rate of the **fold-vote condition alone**. The gate
also requires `mean_auc > 0.55` and an independent `beats_hard` majority, so
the **joint** false-positive rate is materially lower than the table's
`P_false` column. These numbers upper-bound one of three conditions; they do
not describe the gate's overall specificity, and should not be quoted as if
they did.

## 4. Conclusion

- **Keep `--min-fold-trades = 50`.** At the measured effect it delivers 0.771
  per-fold reliability, between the 0.75 and 0.80 targets; nothing in the data
  argues for moving it, and the alternatives that *look* better are the ones
  with 50% single-condition false-positive rates.
- **Give it a stated basis** rather than leaving it bare — that basis is this
  document: `b = (z_p/δ)²` at δ ≈ 0.105 puts the 0.75–0.80 reliability band at
  41–64 trades, and 50 is inside it.
- **Stop treating block size as the `exit_head_ml` unblock lever.** The seven
  1d equity legs are not blocked by an arbitrary number; at their lifetimes
  (31–72 trades, projecting to 50–104 on full history) there is no block size
  that yields a trustworthy verdict.
- **The real finding is upstream:** δ ≈ 0.105 with five of fifteen groups
  negative means the exit head's edge over the actual replay is small and
  inconsistent across families. That is a question about the head, not about
  how it is validated.

## 5. What this does not establish

- **The true δ.** The best-tau selection biases it upward, so the honest
  reading is "δ ≤ 0.105 typical", and every block figure here is a lower
  bound. Quantifying the selection bias needs per-tau fold arms, which the
  reports do carry — a follow-up, not done here.
- **Whether σ_d is stable per leg.** It is estimated per (family, tf) group
  from as few as 3 folds (`uso_trend_1h`), and `donchian 4h` sits at 5.287,
  seven times the median. A per-leg block would be defensible on that spread
  and is deliberately not proposed — verdicts graded at different blocks are
  not comparable, which is a cost this document does not price.
- **The gate's joint specificity**, per § 3 above.
- **Whether an exit head is worth having on a leg trading ~4×/year** — likely
  the more useful question than how to grade one.
