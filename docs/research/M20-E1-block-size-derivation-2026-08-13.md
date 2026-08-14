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

> **⚠️ Two corrections landed the same day, both from measurements this document
> called for and did not have. Read §§ 6–7 before quoting anything above them.**
> The recommendation (keep 50) survives both. What does **not** survive is the
> reliability figure in § 3: **0.771 is true only for single-leg families**, and
> the § 5 caveat that best-tau selection "biases δ upward" turned out to
> understate it by a wide margin — outside the scalp family, selection is not a
> bias on the effect, it **is** the effect.
>
> **⚠️ §§ 10–11 (2026-08-14) close the causal interval § 9 left open, and they
> disagree with each other by family — which is the point.** Measured with a
> *contemporaneous* τ rule (a holdout carved from each fold's own training
> window): **donchian-1h +0.137R, 0.22 SE from zero** — the credited edge was the
> selection. **Scalp +3.848R, 6.4 SE from zero, 5 of 7 legs passing a two-sided
> test** — the edge is in the head. **Do not quote a fleet-wide nested figure;
> there isn't one.** § 5's "biases δ upward" caveat is right for donchian and
> wrong for scalp, and § 9's own prediction that a contemporaneous rule would
> beat PREV/EXPAND is confirmed (scalp two-sided passes went 2/7 → 5/7).
>
> **⛔ § 12 (2026-08-14) CORRECTS HOW §§ 10–11 WERE BEING APPLIED. Read it
> before quoting either against the three LIVE `exit_head_ml` cells.** Those
> cells do not select a τ — they run a fixed conditional arm
> (`below_half_r @ τ=0.10`), and on a re-sweep of it **2 of 3 PASS the
> two-sided test** (ETH +1.810R, SOL +1.589R) while **BTC fails by −1.801R vs
> actual**. The earlier "0 of 3 clear the bar" was a statement about
> *selection* and was never true of the shipped lever.

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

---

## 6. CORRECTION — the per-fold vote is cast on `n_leg`, not on `b`

*Measured 2026-08-13, relay #9074, 31 reports.*

§ 3 models the fold vote as `P(correct) = Φ(√b · δ)`. That substitutes the
**block** size for the number of trades the vote is actually computed over, and
the two are the same thing only when the family has ONE leg.

`fold_blocks` cuts blocks over the **family's** pooled trades, but
`per_leg_summary` casts **one vote per LEG per fold**, on that leg's own
`n_trades` within the block. In a multi-leg family the block is split across
legs, so `n_leg ≪ b`:

| family (fold_mode, b) | legs | votes | `n_leg` min / median / max |
|---|---:|---:|---|
| `pullback` (trades, 15) | 6 | 169 | **1 / 3 / 8** |
| `allmix` (calendar) | 13 | 106 | **1 / 5 / 11** |
| `pullback` (trades, 50) | 4 | 112 | **6 / 12 / 22** |
| `donchian` (calendar) | 2 | 14 | 19 / 30 / 38 |
| `donchian` (trades, 15) | 1 | 61 | 15 / 15 / 15 |
| every `ict_scalp_*`, `slv_trend_1h`, `uso_trend_1h` (trades, 50) | 1 | 3–20 | **50 / 50 / 50** |

So the split is clean and it is **family arity, not timeframe**: single-leg
families sit exactly on the block, multi-leg families sit far below it.
Re-running § 3's own formula on the measured `n_leg` instead of `b`:

| population | trades per vote | `Φ(√n · δ)` at δ = 0.105 |
|---|---:|---:|
| single-leg families (as § 3 assumed) | 50 | **0.771** |
| `pullback` b=50 | 12 (median) | **0.642** |
| `allmix` | 5 (median) | **0.593** |
| `pullback` b=15 | 3 (median) | **0.572** |

**The legs this whole document is about are in the multi-leg families.** Every
1d equity leg lives in `pullback` or `allmix`, so their votes are being cast at
0.57–0.64 reliability, not 0.771 — barely distinguishable from a coin flip.
`ief_pullback_1d` is graded `candidate` on 34 votes whose median vote rests on
**three trades**.

**This strengthens rather than reverses § 4.** Raising `b` in a multi-leg family
raises `n_leg` only by that leg's *share* of the block, so buying reliability
costs proportionally more folds there than the § 3 model implied. There is still
no block size that makes these legs gradeable. But the honest statement of the
current gate is that a multi-leg per-leg verdict is weaker evidence than a
single-leg one at the same nominal block, and **nothing in the report says
which kind you are reading.**

## 7. CORRECTION — outside the scalp family, the edge IS the selection

*Measured 2026-08-13, relays #9074 + #9075, 31 reports, per fold-geometry ×
family.* § 5 left this as "a follow-up, not done here". Done here.

The gate credits the head with `best_tau_net_r − actual_net_r`. Ask instead what
a tau chosen **without hindsight** is worth — the median of the 7 arms — and the
sign flips everywhere except the scalp legs:

| group | folds | edge (best − actual) | **median-arm edge** | selection premium |
|---|---:|---:|---:|---:|
| `ict_scalp_5m` | 12 | +8.408R | **+6.037R** | +2.371R |
| `ict_scalp_xrp_5m` | 13 | +5.834R | **+3.353R** | +2.481R |
| `ict_scalp_avax_5m` | 20 | +5.866R | **+3.069R** | +2.797R |
| `ict_scalp_sol_5m` | 16 | +5.856R | **+2.966R** | +2.890R |
| `ict_scalp_sol_15m` | 7 | +3.654R | **+2.331R** | +1.323R |
| `ict_scalp_xrp_15m` | 6 | +3.837R | **+2.178R** | +1.658R |
| `ict_scalp_eth_15m` | 6 | +2.823R | **+0.672R** | +2.152R |
| `pullback` (trades, 50) | 112 | +1.362R | **−1.379R** | +2.741R |
| `pullback` (trades, 15) | 169 | −0.027R | **−0.962R** | +0.935R |
| `donchian` (trades, 15) | 61 | +0.670R | **−1.554R** | +2.223R |
| `allmix` (calendar) | 106 | +0.295R | **−1.534R** | +1.830R |
| `donchian` (calendar) | 14 | +0.426R | **−4.399R** | +4.825R |

Every non-scalp group has a **negative median-arm edge**. In those groups the
selection premium exceeds the whole measured edge, and 23–50% of folds are
"flips" — the median arm loses to doing nothing while the selected arm wins, so
the fold's `beats_actual` vote is carried entirely by which of seven arms
happened to land.

**Three things this does NOT say**, because the ratio column is easy to
over-read and I am not quoting it above for that reason:

1. **"% of edge" is unstable near a zero denominator** and must not be quoted.
   `pullback` b=15 computes to −3421% purely because its edge is −0.027R. The
   load-bearing column is **median-arm edge**, which needs no denominator.
2. **The median arm is not the deployment expectation.** It is the expected edge
   of a tau picked at *random*, which bounds a sensibly-chosen tau from below
   and a badly-chosen one from above. A real deployment picks tau by some rule,
   and the truth sits between the median-arm and best-arm columns. Locating it
   needs **nested tau selection inside the walk-forward** — pick tau on the
   training half of each fold, score it on the test half. These reports do not
   contain that, and it is the single measurement that would settle whether the
   scalp result is real.
3. **`slv_trend_1h` (4 folds) and `uso_trend_1h` (3 folds) are too thin to
   carry weight** and are excluded from the reading above.

**What it does say:** the E1 `candidate` verdicts outside the scalp family are
resting on a quantity that goes negative the moment hindsight is removed. That
is consistent with — and a mechanism for —
`BL-20260813-EXIT-HEAD-HARNESS-PASS-DOES-NOT-SURVIVE-THE-LIVE-BOOK`, where
`ict_scalp_5m` passed 3/3 harness folds and every tau then lost to doing nothing
on the live book.

## 8. The head also loses to the FIXED rules, in every fold geometry

*Measured 2026-08-13, relays #9071 + #9072, 35 leg-rows over 27 distinct legs.*

The gate has two independent majority conditions: `beats_actual` (does the head
beat doing nothing?) and `beats_hard` (does it beat the best of `stale_8_0` /
`giveback_1_1`?). Both need ≥ 2/3 of usable folds. Pooled per fold geometry:

| geometry | legs | usable folds | `beats_actual` | `beats_hard` |
|---|---:|---:|---:|---:|
| calendar-folds | 15 | 120 | 61.7% ✗ | **58.3% ✗** |
| trade-folds b=15 | 7 | 230 | 68.7% ✓ | **62.2% ✗** |
| trade-folds b=50 | 13 | 199 | 69.8% ✓ | **61.8% ✗** |

`beats_hard` is **below the 2/3 bar in all three geometries** while
`beats_actual` clears it in two — so the fixed-rule comparison is the binding
condition, fleet-wide. A head that beats doing nothing but loses to an
eight-bar stale-stop is not worth its complexity, and that is where the fleet
currently sits in aggregate.

Two honesty notes on that table: these are **fold-pooled** rates, whereas the
gate is applied **per leg**, so the pooled figure is a summary and not the gate
— the per-leg verdicts are 12 `candidate` / 21 `honest_negative` /
2 `insufficient_base` across the 35 rows. And 8 legs appear under both fold
geometries; they agree on 7 and disagree on one (`iaum_pullback_1d`:
`honest_negative` under trade-folds, `candidate` under calendar-folds), which is
the report's own "not comparable evidence" warning showing up as an actual
verdict flip.

## 9. Hindsight-free τ selection — the measurement §§ 5 and 7 asked for

*Measured 2026-08-13, relay #9077, 31 reports, 514 scored folds.* § 7 measured
the *random*-τ bound; this measures actual **causal selection rules**. `folds`
is built sequentially by `fold_blocks`, so per leg it is chronological, and a
selection rule using only prior folds is computable **with no retraining**:

- **PREV** — the τ with the highest `net_r` on the leg's previous fold.
- **EXPAND** — the τ with the highest cumulative `net_r` over all prior folds.

Fold 1 of each leg has no prior and is excluded (reported as `skip1st`, so the
denominator is never silently short).

**Fleet totals, 514 folds:**

| τ chosen by | mean vs actual | median | folds positive |
|---|---:|---:|---:|
| **best arm (what the gate credits — HINDSIGHT)** | **+1.217R** | +1.220R | **70.2%** |
| EXPAND (causal) | **−0.341R** | +0.285R | **54.1%** |
| PREV (causal) | **−0.674R** | +0.065R | **50.8%** |
| EXPAND vs `stale_8_0` | −0.163R | +0.320R | 54.9% |
| PREV vs `stale_8_0` | −0.496R | +0.160R | 53.3% |

**The fleet-level edge is the hindsight.** Choose τ causally and the mean goes
negative and the hit rate falls to a coin flip.

**But the family split survives both causal rules, which is the finding that
matters.** Every scalp group stays positive vs actual under PREV *and* EXPAND
(`ict_scalp_5m` +5.638 / +6.304, `xrp_5m` +4.525 / +4.980, `avax_5m` +3.538 /
+3.284, `sol_15m` +3.027 / +3.477, `xrp_15m` +3.118 / +3.578, `sol_5m` +2.714 /
+3.102, `eth_15m` +0.630 / +0.066 — the weakest). Every non-scalp group is
negative under both (`allmix` −1.707 / −1.250, `pullback` b=50 −1.350 / −0.919,
`donchian` b=15 −1.488 / −0.723, `donchian` calendar −2.628 / −2.537).

**Against the hard levers, even scalp mostly does not clear.** PREV vs
`stale_8_0`, per scalp leg: `sol_15m` **+2.723**, `ict_scalp_5m` **+1.564**,
`sol_5m` +0.340, `avax_5m` +0.229, `xrp_15m` −0.010, `xrp_5m` **−0.470**,
`eth_15m` **−1.658**. So **2 of 7** scalp legs show a meaningful edge over an
eight-bar stale-stop once τ is picked without hindsight; the rest sit at or
below zero.

### What this does and does not license

- **It does not say the deployed head would be negative.** PREV and EXPAND are
  *lower* bounds on a well-designed selection: they pick τ from earlier folds
  only, so they eat regime drift between folds, and EXPAND already beats PREV
  (−0.341 vs −0.674), which says selection quality matters and better rules
  exist. Picking τ on the *training half of the same fold* would be
  contemporaneous and should do better. **The achievable value lies between
  EXPAND and best**, and where in that interval is still unmeasured.
- **It does say the gate's figure cannot be read as a deployment expectation.**
  The interval `[−0.341R, +1.217R]` straddles zero and is wider than the effect
  being claimed. A `candidate` verdict currently means "some τ beat the
  baselines on this test fold", not "this head is expected to help".
- **Read mean and median together.** They diverge (PREV: mean −0.674, median
  +0.065), so the mean carries a negative tail of a few bad folds. `frac_positive`
  is the robust statistic here and it is ~51–55% for every causal rule against
  every baseline — a coin flip.
- **`slv_trend_1h` (3 scored), `uso_trend_1h` (2), `eth_15m` / `xrp_15m` (5),
  `sol_15m` (6) are too thin to weigh individually** and none of the conclusions
  above rests on them.

This is the direct mechanism behind
`BL-20260813-EXIT-HEAD-HARNESS-PASS-DOES-NOT-SURVIVE-THE-LIVE-BOOK`:
`ict_scalp_5m` passed 3/3 harness folds on the best-arm figure and then had
*every* τ lose on the live book. A gate scored on the max of seven arms will do
that whenever the arm ordering is unstable — and § 7's flip rate (23–50% of
non-scalp folds) says it is.

---

## 10. NESTED τ selection — the interval § 9 left open, now measured

§ 9 closed by saying the achievable value "lies between EXPAND and best, and
where in that interval is still unmeasured", and named the rule that would
measure it: pick τ on the **training half of the same fold**. That rule now
exists in the harness (`train_exit_head._select_tau_holdout`, PR #9048) and has
been run.

**POPULATION — read this before quoting any number below.** This is the
**donchian-1h family ONLY**: 3 legs (`trend_donchian` BTC, `trend_donchian_eth`,
`trend_donchian_sol`), `fold_mode: trades`, `min_fold_trades: 50`, 22 folds, of
which **21 selected and 1 returned `no_validation_block`** — so every figure
below is over **63 leg-folds** (21 × 3), not 64 and not 66. It is **not** the
514-fold fleet population of § 9, and it is **not** the same config as § 9's
donchian rows (which are b=15 and calendar). Do not merge the two tables.
*(Trainer relay #9101, 2026-08-14.)*

The selector carves a validation block from the **tail of the fold's own
training window**, refits on train-minus-validation under the same `EMBARGO_S`,
and picks τ there — contemporaneous with the fold, blind to its test block. A
fold whose training window is too thin to carve one **refuses** (`state:
no_validation_block`) rather than falling back to a default; that is the 1 of 22
excluded above, and the refusal is why the denominator is honest.

**Pooled, 63 leg-folds:**

| τ chosen by | mean vs actual | leg-folds positive |
|---|---:|---:|
| **NESTED holdout (causal, contemporaneous)** | **+0.137R** | 57.1% |
| median arm (a τ-blind control) | −0.367R | 55.6% |
| **best arm (what the gate credits — HINDSIGHT)** | **+2.788R** | 71.4% |
| NESTED vs `stale_8_0` | +0.323R | 52.4% |

**The interval closes near its bottom: +0.137R causal against the +2.788R the
gate credits — an order of magnitude apart.** The nested rule does beat the
τ-blind median-arm control (+0.137 vs −0.367), which confirms the selector is
doing *something* real; it is simply nowhere near the headline.

> **Quote the two absolute figures, not their ratio.** +0.137/+2.788 = 4.9% is
> arithmetically correct and is **not a usable statistic** — not because the
> denominator is near zero (it is comfortably large) but because the
> **numerator** is. The three per-leg means span −1.118 to +0.837, so the pooled
> mean's own uncertainty is of order ±0.5R at this n; a ratio built on it swings
> through zero and well past 20%. This is the same caution the backlog row
> already carries about "% of edge" ratios, and it applies here for a different
> reason, so it is restated rather than assumed.

**Per leg — and the pooled figure hides a sign split:**

| leg | n | vs actual | pos% | vs `stale_8_0` | vs `giveback_1_1` | median arm | best (hindsight) |
|---|---:|---:|---:|---:|---:|---:|---:|
| `trend_donchian` (BTC) | 21 | **−1.118R** | 52.4% | **+1.351R** | +0.253R | −0.995R | +0.791R |
| `trend_donchian_eth` | 21 | **+0.692R** | 57.1% | −0.070R | +1.162R | +0.069R | +3.918R |
| `trend_donchian_sol` | 21 | **+0.837R** | 61.9% | −0.311R | −0.415R | −0.174R | +3.655R |

### The finding that decides the cell

**No leg beats BOTH the actual exit and the cheap deterministic lever.** BTC
beats `stale_8_0` by +1.351R but *loses to doing nothing* by −1.118R; ETH and
SOL beat doing nothing (+0.692 / +0.837) but sit at or below the eight-bar
stale-stop (−0.070 / −0.311). Three legs, three different ways of failing the
same two-sided test. A lever that cannot clear both comparisons on any leg has
not earned an order-path change — and the alternative it loses to on two of
three legs is a **fixed rule with no model, no training, and no τ**.

### What this does and does not license

- **It does not license re-grading the three live `exit_head_ml` cells.** That
  is operator-gated, and this measurement covers one family at one block size.
- **It does not say the head is worthless.** +0.137R over median-arm's −0.367R
  is a real, if small, signal, and the ETH/SOL vs-actual figures are positive.
- **It does say the gate's number is not a deployment expectation** — the same
  conclusion as § 9, now with the *upper* end of the interval measured rather
  than assumed. § 9 hedged that a contemporaneous rule "should do better" than
  EXPAND; it does (+0.137 vs −0.341 on different populations, so directionally
  only), and it still lands near zero.
- **63 leg-folds is small, and they are not 63 independent observations** —
  walk-forward folds share training windows, so the effective n is lower than
  the count. 57.1% positive is a coin flip with a lean.
- **`no_validation_block` fired once.** If a future config makes that the
  common case, the pooled figure silently becomes a different population — read
  `selected_tau_state` beside any re-run.

---

## 11. SCALP nested τ — the family split is real, and it survives honest selection

§ 10 measured the donchian family and found the credited edge was almost entirely
τ-selection hindsight. **The scalp family behaves completely differently, and this
is the measurement that decides whether `exit_head_ml` is a capability or an
artifact.** *(Trainer relay #9103 ran the round, #9108 read it, 2026-08-14.)*

**POPULATION.** All **7 scalp legs**, each its own **single-leg family** — so per
§ 6 `n_leg` = `b` = **50 exactly**, making these the *strongest* per-fold votes in
the fleet rather than the weakest. `fold_mode: trades`, `min_fold_trades: 50`.
80 folds total; **7 returned `no_validation_block` — exactly one per leg**, which
is each leg's *first* fold (thinnest training window, nothing to carve). So the
figures are over **73 leg-folds**, and the refusals are a coherent pattern rather
than scattered failures. Each leg ran against a **symlink to the original
`rows.jsonl`**, so this isolates the τ-selection change from any dataset rebuild.

**Pooled, 73 leg-folds:**

| τ chosen by | mean vs actual | positive |
|---|---:|---:|
| **NESTED holdout (causal, contemporaneous)** | **+3.848R** | 79.5% |
| median arm (τ-blind control) | **+3.475R** | 72.6% |
| best arm (hindsight) | +6.022R | 90.4% |
| **NESTED vs `stale_8_0`** | **+0.668R** | 56.2% |
| NESTED vs `giveback_1_1` | +3.522R | 79.5% |

### The contrast with § 10 is the whole finding

| | causal | credited | causal ÷ credited | distance from zero |
|---|---:|---:|---:|---|
| **scalp** (7 legs) | **+3.848R** | +6.022R | **63.9%** | **6.4 SE** |
| **donchian-1h** (3 legs) | +0.137R | +2.788R | 4.9% | **0.22 SE** |

*(SE computed between legs — `sd(leg means)/√n_legs`: scalp 1.583/√7 = 0.598;
donchian 1.089/√3 = 0.629. This is why the ratio is quotable for scalp and, per
§ 10's warning box, is **not** for donchian: same denominator quality, completely
different numerator stability.)*

**Read the τ-blind control row, because it is the sharpest statement of the
split.** On scalp the median arm alone scores **+3.475R** — nearly the whole
causal figure — so τ choice barely matters and the edge lives in the *head*. On
donchian the median arm was **−0.367R**, so what the gate was crediting there
*was* the τ choice. Same instrument, opposite readings:

> **On scalp the edge is in the model. On donchian the "edge" was the selection.**

### Per leg, and the two-sided test

A leg passes only if it beats **both** the actual exit and the cheap
deterministic `stale_8_0` lever — the bar § 10 established:

| leg | leg-folds | vs actual | pos% | vs `stale_8_0` | two-sided |
|---|---:|---:|---:|---:|:--|
| `ict_scalp_5m` | 11 | +5.771R | 90.9% | +1.696R | **PASS** |
| `ict_scalp_avax_5m` | 19 | +3.698R | 84.2% | +0.388R | **PASS** |
| `ict_scalp_sol_5m` | 15 | +3.306R | 66.7% | +0.932R | **PASS** |
| `ict_scalp_sol_15m` | 6 | +3.108R | 83.3% | +2.805R | **PASS** |
| `ict_scalp_xrp_15m` | 5 | +3.640R | 100.0% | +0.512R | **PASS** |
| `ict_scalp_xrp_5m` | 12 | +4.782R | 83.3% | **−0.213R** | fail |
| `ict_scalp_eth_15m` | 5 | +0.670R | 40.0% | **−1.618R** | fail |

**5 of 7 pass — against 2 of 7 under § 9's PREV rule.** That is § 9's own
prediction confirmed: it flagged PREV/EXPAND as *lower* bounds that "eat regime
drift between folds" and said a contemporaneous rule "should do better". It does,
and the gap is large enough to change the verdict on three legs.

### Caveats that bound this

- **The 15m legs are thin.** `sol_15m` and `xrp_15m` pass on **6 and 5**
  leg-folds. Their per-fold votes are full 50-trade blocks (§ 6), which is why
  they are worth reporting at all — but two passes resting on five and six
  observations must not be weighted like `avax_5m`'s nineteen.
- **The margin over `stale_8_0` is thin in aggregate** — +0.668R, **56.2%**
  positive, a coin flip with a lean. It is carried by `sol_15m` (+2.805) and
  `ict_scalp_5m` (+1.696); four of the remaining five sit between −0.2 and +0.9.
  The head clearly beats *doing nothing*; beating the eight-bar stale-stop is a
  much closer contest.
- **`eth_15m` is the weakest leg on every axis** (+0.670 vs actual at 40%
  positive, −1.618 vs stale) — and it is the leg that **already has a shipped
  stale-stop** (`S-M20-ICTSCALP-ETH15M-STALE-SHIP-2026-07-29`). The cheap lever
  winning there is coherent with what is already deployed, not a contradiction.
- **73 leg-folds are not 73 independent observations** — walk-forward folds share
  training windows. The 6.4-SE figure above is computed *between legs* (n=7)
  precisely to avoid leaning on the within-leg count.
- **This grades no cell.** Whether `exit_head_ml` becomes family-scoped is an
  operator decision (`BL-20260813-EXIT-HEAD-EDGE-SMALL-AND-INCONSISTENT` item 3);
  this section supplies the evidence it was waiting on and takes nothing.

---

## 12. CORRECTION — §§ 10–11 do not speak to the three SHIPPED cells

**§§ 10–11 measured τ-SELECTION. The three live `exit_head_ml` cells do not
select a τ — they run a FIXED, pre-committed arm.** Reporting the nested
selection figure as if it graded those cells was a category error on my part,
and it pointed the wrong way. *(Trainer relay #9121, 2026-08-14.)*

The live head is **`below_half_r @ τ=0.10`** — verified in
`src/runtime/exit_head_shadow.py:5` and `config/strategy_changelog.json`, not
inferred from the matrix ref. In `train_exit_head.py` that is a **conditional
shape** under `model_cond` (`_SHAPES["below_half_r"]`, key
`below_half_r_tau_0.1`), **not** `model["tau_0.1"]` — which is the
*unconditional* head at the same τ and is a different lever.

> **This was nearly a second wrong number.** My first probe read
> `model["tau_0.1"]` on the strength of the τ matching, and produced a
> plausible, publishable table (ETH +2.097R, SOL +2.192R). Reading
> `train_exit_head.py:418` vs `:423` before publishing is what caught it —
> the same class as the `per_leg_summary`-is-a-function-name miss earlier the
> same night, and the reason the probe below prints its `model_cond` key
> inventory as a positive control.

**The shipped arm, 22 folds per leg (no validation block needed, so all 22
count — the nested read's n was 21):**

| leg | vs actual | pos% | vs `stale_8_0` | vs uncond. τ=0.10 | two-sided |
|---|---:|---:|---:|---:|:--|
| `trend_donchian` (BTC) | **−1.801R** | 40.9% | +1.213R | −0.252R | **fail** |
| `trend_donchian_eth` | **+1.810R** | 68.2% | **+1.263R** | −0.288R | **PASS** |
| `trend_donchian_sol` | **+1.589R** | 68.2% | **+0.603R** | −0.603R | **PASS** |

Pooled: **+0.532R** vs actual (59.1% positive), **+1.026R** vs `stale_8_0`
(68.2%).

### What this changes

- **Two of the three shipped cells are supported by a re-sweep; one is not.**
  ETH and SOL clear both baselines. **BTC loses to doing nothing by −1.801R**
  at 40.9% positive — the head fires and the trade would have done better
  untouched.
- **The earlier framing — "0 of 3 clear the bar" — was wrong for these cells**
  and must not be quoted. It was true of nested *selection* (§ 10) and remains
  true of that; it was never a statement about the fixed arm.
- **This is a re-sweep of a STALE DECISION.** All three cells carry
  `newest-ref 2026-07-12`, and `m20_coverage_rollup.py --stale-decisions`
  flags them among 8 such cells with the note that a stale *shipped* cell
  "costs MONEY — it changes exit behaviour on a real-money leg now, on a number
  never reproduced under the geometry the bot actually places." The base rate
  for stale-decision re-sweeps moves from **1 of 1 failing** (`trend_donchian`
  `trail_decay` → `shipped_gate_failed`) to **2 of 4 failing** — and both
  failures are on the same leg, `trend_donchian` (BTC).

### The conditional gate costs net_R on this book

`vs uncond.` is negative on all three legs (−0.252 / −0.288 / −0.603): the
`below_half_r` condition makes the head **worse on net_R** than the plain
unconditional head at the same τ. That is not automatically an argument to drop
it — the condition was chosen for a *behavioural* reason (`_SHAPES` comments
cite live trade 3344: a running trend should never be truncated by a low score
alone), which is a drawdown/holding argument rather than a net_R one. But the
net_R cost is real, measured, and was not previously stated anywhere.

### Still not measured

- **Whether BTC's failure reproduces off this one book.** 22 folds, one family,
  one block size. A leg-level demotion wants more than a single re-sweep.
- **The drawdown side of the conditional gate.** `vs uncond.` is net_R only;
  the condition's stated justification is about *not truncating winners*, which
  this table cannot see.
- **Nothing here is a re-grade.** All three cells remain `shipped`; the
  disposition is Tier-3 and operator-gated.
