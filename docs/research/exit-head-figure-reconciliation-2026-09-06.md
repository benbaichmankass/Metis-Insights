# Exit-head figure reconciliation — which `ict_scalp` numbers are current, and why they moved

**MI-153** · work object [`WO-20260906-TWO-RECORDS-DISAGREE-ON-EVERY-ICT-SCALP`](../claude/work/objects/) · blocks PR #11140.

⚠️ **Tier-1, read-only investigation + a written finding.** No cell `status` changed (a status change is Tier-3). Nothing armed. No `src/`, `config/`, `deploy/`, workflow or order-path file touched.

---

## The answer, stated first

1. **The current figure set for all three legs is the 2026-08-14 one** — the larger `n_oos`, the one the MI-146 audit table quotes.
2. **The two records were never in disagreement.** Each matrix cell's `ref` contains *both* figure sets, in one append-only string, in chronological order, joined by an explicit `->` arrow. The audit quotes the second clause; the brief for this work quotes the first clause of the same string. **This is one record read at two points, not two records.**
3. **The mechanism behind the change from the first clause to the second is a re-measurement on a LARGER per-leg trade pool.** It is not a stale copy, and it is **not** the fold re-partitioning the brief asked about — that is a separate, later measurement which holds `n_oos` **fixed** and therefore cannot produce a change in `n_oos`.

**It changes the arming recommendation for none of the three legs.** See § 6.

---

## 1. The two clauses, side by side

**MEASURED** — read in full from `docs/research/exit-refinement-coverage.json` (`updated_at 2026-09-06`) this session. **Population: 3 of 3 candidate legs; all three `exit_head_ml` refs read end-to-end, not grepped.**

| leg | clause 1 — `E1 RE-RUN 2026-08-13 (relay #8963)` | clause 2 — `CONFIRMED / LIVE-PARITY RE-MEASUREMENT 2026-08-14` |
|---|---|---|
| `ict_scalp_sol_5m` | n_oos 800 · auc 0.6149 · beats_actual 12/16 · beats_hard 12/16 | **n_oos 1150 · auc 0.6184 · beats_actual 22/23 · beats_hard 16/23** |
| `ict_scalp_xrp_5m` | n_oos 650 · auc 0.613 · beats_actual 12/13 · beats_hard 9/13 | **n_oos 1100 · auc 0.5987 · beats_actual 21/22 · beats_hard 19/22** |
| `ict_scalp_sol_15m` | n_oos 350 · auc 0.5715 · beats_actual 7/7 · beats_hard 5/7 | **n_oos 450 · auc 0.5808 · beats_actual 6/9 · beats_hard 7/9** |

The `sol_5m` cell literally reads `n_oos 800 -> 1150, auc 0.6149 -> 0.6184`. The arrow is in the committed file.

**So the framing "two records disagree" is INFERRED and wrong**, and the inference is cheap to make: the matrix cell has **no structured figure field at all**. Its complete key set is `status`, `ref`, `tp_geometry` — verified on all three cells. Every number lives in one ~4,000-character prose string whose first sentence is the superseded measurement. That is `BL-20260815-EXIT-HEAD-ROUNDS-RUN-IDENTITY-IS-PROSE-NOT-A-FIELD` biting exactly as filed.

---

## 2. Which set is current — established without reading the matrix prose at all

Three independent grounds, none of which is "the newer file wins".

### (a) The structured record of record carries only the 2026-08-14 set

`docs/research/m20-exit-head-rounds.jsonl` is the graded-round evidence store, emitted by `scripts/research/m20_exit_head_round.py` and asserted by four tests (`test_exit_head_round_emits_evidence`, `test_rounds_family_matches_classify`, `test_exit_head_round_geometry_stamp`, `test_rounds_exclude_dispersion_arms`).

**MEASURED. Population: all 33 rows in the file; 6 are `ict_scalp`.** Every `ict_scalp` row is from round `scalp_5m_20260814T151003Z` or `scalp_15m_20260814T135244Z`, and carries the clause-2 figures exactly. **There is no 2026-08-13 row for any leg in the file.**

This is not attrition of an older row: `git log --all` on that path returns **exactly one commit** (`7aa293da`, 2026-08-16, PR #9257), and that first version already carries the clause-2 numbers. The 08-13 figures were never in the structured store.

### (b) Two later, independent rounds reproduce clause 2 exactly

**MEASURED** from `docs/research/m20-fold-dispersion-arms-consolidated.jsonl` (246 arm rows; 5 / 5 / 4 for the three legs):

| leg | reproducing screen (2026-08-15) | n_oos | auc | beats_actual/u |
|---|---|---|---|---|
| `ict_scalp_sol_5m` | `scalp5m_20260815T103529Z` and `m20_5m_wt_20260815T161348Z/off0` | 1150 | 0.6184 | 22/23 |
| `ict_scalp_xrp_5m` | same two | 1100 | 0.5987 | 21/22 |
| `ict_scalp_sol_15m` | `cand15m_20260815T042247Z/…_off0` | 450 | 0.5808 | 6/9 |

Those are separate rounds run a day later. All four terms match to the last digit.

### (c) The pipeline is deterministic, so a moved number is a moved INPUT

`random_state=7` is pinned (`scripts/ml/train_exit_head.py:133`), and the 2026-08-13 session's own block-50 positive control found all six of its legs **byte-identical to relay #8963 across two runs at different HEADs** (`docs/sprint-logs/S-M20-EXIT-COVERAGE-MATRIX-2026-08-12.md` § 18). **INFERRED from that:** run-to-run noise is not available as an explanation; a change in the output is a change in the input.

---

## 3. The mechanism — derived from the generating code

### The derivation

`scripts/ml/train_exit_head.py::fold_blocks` (l.433) cuts test blocks of exactly `block_n` starting at `block_n`, and its docstring (l.484) states the count:

```
u = max(0, floor(N / block_n) - 1)          usable folds
```

`per_leg_summary` accumulates `acc["oos_trades"] += n` once per fold (l.309), and every block is exactly `block_n` trades. `block_n` is `--min-fold-trades`, default **50**, and the 2026-08-13 derivation (`docs/research/M20-E1-block-size-derivation-2026-08-13.md`) recommends leaving it at 50. Therefore:

```
n_oos = 50 × ( floor(N / 50) − 1 )
```

**Arithmetic cross-check, not a re-read:** `n_oos / usable_folds = 50.0` in **6 of 6** `ict_scalp` rows in the rounds file, and in **3 of 3** of the clause-1 figures (800/16, 650/13, 350/7). The identity holds on both sides.

### Inverting it

`n_oos` pins the leg's own trade count `N` to a 50-wide window:

| leg | clause 1: n_oos → u → N | clause 2: n_oos → u → N | pool growth |
|---|---|---|---|
| `ict_scalp_sol_5m` | 800 → 16 → N ∈ [850, 899] | 1150 → 23 → N ∈ [1200, 1249] | **+301 … +399** |
| `ict_scalp_xrp_5m` | 650 → 13 → N ∈ [700, 749] | 1100 → 22 → N ∈ [1150, 1199] | **+401 … +499** |
| `ict_scalp_sol_15m` | 350 → 7 → N ∈ [400, 449] | 450 → 9 → N ∈ [500, 549] | **+51 … +149** |

Corroborated from a source that was not computing this: `docs/research/m20-fold-dispersion-2026-08-15.md:2033` records that the 5m legs "emit **1,209–1,255 trades each** off 5-minute bars" — inside the derived `[1200, 1249]` window for `sol_5m`.

### Why the pool differs: two different pipeline stages ran

- **2026-08-13 (relay #8963) was an E1-ONLY re-run.** `train_exit_head.fold_blocks` was re-run "across every family in BOTH fold modes" over the E0 datasets already on the trainer. No harness backtest, no dataset rebuild.
- **2026-08-14 was a full ROUND.** `m20_exit_head_round.py` stage 1 re-runs the harness with `--emit-trades`; stage 2 rebuilds the E0 dataset; stage 3 trains. New round directories `scalp_5m_20260814T151003Z` / `scalp_15m_20260814T135244Z`.

**So the two measurements ran on two different trade books, and the later book is larger.** That is the mechanism: **a re-measurement on a different (larger) corpus slice**, with the fold count following deterministically from it.

### Two candidate confounders, both REFUTED rather than merely unselected

- **TP geometry did not change for these legs.** The sibling event on the 1d pullback family (`BL-20260814-EXIT-HEAD-AUC-MOVES-MORE-THAN-ITS-OWN-GATE-MARGIN-ACROSS-A-ONE-DAY-RE-MEASUREMENT`) names three simultaneous changes including uncapped → 0.099. For `ict_scalp` the cap is absent on **both** dates: the 08-14 rounds ran the pre-fix driver and passed no `--tp-cap-pct` (`grep -c -- --tp-cap-pct` over the round log = 0, recorded in the `sol_15m` ref and in each row's `provenance`), and the live `ict_scalp` unit carries no clamp (0 occurrences of `_TP_SENTINEL_CAP_PCT` against 4 in `trend_donchian`), so uncapped **is** parity for it. Both sides are `live_parity_uncapped`.
- **Block unit did not change.** `build_exit_head_dataset.family_of` collapses `pullback`/`donchian`/`trend_*` legs into one family directory but scalp legs fall through and keep their own name, so a scalp round is `per_leg` on both dates — `block_unit: per_leg` on all 6 rows, and the dispersion doc records `pooled_legs_ordered: ["ict_scalp_avax_5m"]`, one leg per block. The pooled-vs-per-leg hazard (`BL-20260814-EXIT-HEAD-EVIDENCE-MIXED-POOLED-AND-PER-LEG-BLOCKS-WITH-NOTHING-SAYING-WHICH`) does **not** reach these three cells.

---

## 4. Fold re-partitioning is NOT the mechanism — answering the brief's question directly

The brief asked whether the audit's larger `n_oos` figures are a **pre-re-partitioning snapshot**. **They are not**, and the committed arms settle it.

`--fold-offset` (added 2026-08-15) shifts *where blocking starts* at **fixed `block_n`**; the docstring (l.437) says so and bounds it to `0 <= offset < block_n` because "an offset of `block_n` is not a new partition, it is the same one with a whole block discarded."

**MEASURED** — the four arms of `ict_scalp_sol_5m` under `m20_5m_wt_20260815T161348Z`:

| arm | n_oos | u | auc | beats_hard | verdict |
|---|---|---|---|---|---|
| off0 | **1150** | 23 | 0.6184 | 16 | candidate |
| off4 | **1150** | 23 | 0.6158 | 17 | candidate |
| off8 | **1150** | 23 | 0.6177 | 16 | candidate |
| off12 | **1150** | 23 | 0.6150 | **14** | **honest_negative** |

**`n_oos` and `u` are IDENTICAL across all four arms.** Re-partitioning moves the verdict (via `beats_hard`, which needs `16*3 ≥ 46`; at 14 it is 42 < 46) and leaves the sample size untouched. Since the 08-13 → 08-14 difference *is* a change in `n_oos`, re-partitioning cannot be its cause.

And the direction is the opposite of a "pre-re-partitioning snapshot": **the re-partitioning screen was run ON the larger book, one day after it**, and its off0 arm reproduces the audit's figures exactly. The fragility the matrix warns about attaches to the **current** numbers, not to superseded ones.

---

## 5. Why nothing detected any of this

`exit_head_ml` is a **declared exemption** in `scripts/ci/check_matrix_corpus_agreement.py::CORPUS_EXEMPT_LEVERS`. That guard is the one thing that compares a matrix cell against its evidence store — and it reads only `m20-sweep-corpus.jsonl`, while the exit-head evidence lives in `m20-exit-head-rounds.jsonl`. The exemption's own text says so, and names the fix: *"a reports-directory reader, the same shape as pointing this guard at m20-exit-head-rounds.jsonl, NOT deleting the exemption."*

So the join that would compare the matrix ref against the structured round record **has never been performed for this column**, and the cell carries no structured figure field for a guard to compare against even if it were. Two filed rows, both open, cover the halves: `BL-20260815-EXIT-HEAD-ROUNDS-RUN-IDENTITY-IS-PROSE-NOT-A-FIELD` and the exemption's own `exit_head_ml` clause.

⚠️ For the record, and it cuts the other way: the same exemption text reports a hand-run cross-check of all 33 rows against the matrix — **23 agree, 10 disagreed in sign (9 as of 2026-08-23)**. **None of the three legs in this work order is in that set.** These three cells agree with the corpus; what differed was only which clause of the ref a reader stopped at.

---

## 6. Does this change the arming recommendation? **No — for none of the three legs.**

The reconciliation moves the figures in the *favourable* direction (the current numbers are the larger-sample ones), so nothing gets worse. But the binding constraints on arming are elsewhere and are untouched by it:

1. **A standing operator decision blocks the ship.** All three cells carry a **2026-08-23 `SHIP BLOCKED`** verdict that re-graded them `passed_unshipped → blocked` and **cancelled the queued Tier-3 ship**, with its own stated precondition: *"Re-grade to shipped/passed_unshipped only AFTER a consumer exists in the ict_scalp unit."* **DECIDED** — do not re-litigate here.
2. **`ict_scalp_sol_5m`'s pass does not survive re-partitioning**, and § 4 shows that fragility sits on the *current* book. `beats_hard 16/23` clears by 2 points of 46; one fold the other way fails it, and off12 is that fold.
3. **PR #11140 measures that no leg can be scored today.** `/api/diag/shadow_stats` (2026-09-06) publishes exactly two exit-head artifacts, both `tf: 1h`, against 8 `ict_scalp` legs on 5m/15m — so `maybe_score_exit_head`'s in-distribution guard fail-closes and the consumer records `not_scored`.

**Recommendation: arm nothing.** #11140's annotate-only, disarmed landing is the correct disposition, and this reconciliation removes the reason it was being held.

---

## 7. What I could NOT establish — stated, not papered over

**WHY the harness emitted a larger book on 2026-08-14 than the book the 08-13 E1 re-run inherited.** The round logs and the earlier E0 directories live on the trainer under `runtime_logs/m20_exit_head/`, which is not committed to this repo, and I did not dispatch a trainer relay. From committed artifacts the candidates — a deeper candle CSV, a different `resolve_data` resolution, a different earlier harness invocation — are not distinguishable.

**One thing IS ruled out: an extra day of bars.** A 5m scalp leg with a ~1,200-trade lifetime does not add ~350 trades in 24 hours, and the same shape appears on the 1d pullback pool (568 → 629 in one day, where a 1d leg trades ~20/year). The growth is a change of input book, not elapsed time. Naming *which* input requires the trainer round logs.

I am recording this as the boundary of the finding rather than picking the most plausible candidate and presenting it as the mechanism.

---

## 8. Corrections made in this PR, and one proposal

**Made (Tier-1, figures and provenance only — no `status` touched):**

1. A dated clause appended to each of the three `exit_head_ml` refs, marking the 2026-08-13 figures **explicitly SUPERSEDED**, naming the current set, and naming the mechanism — so a reader who stops at the first sentence is told at the point of use.
2. A dated correction on the MI-146 audit's ship-candidate table (`docs/research/exit-lever-wiring-audit-2026-09-06.md` § Recommendation 2): its heading reads *"live-parity re-measure"* where the measured geometry is **`live_parity_uncapped`**, and the table omits **`beats_hard`**, which is the *binding* term of the E1 gate (`beats_hard*3 ≥ u*2`) and the term on which `sol_5m` clears by 2 points of 46.

**Proposed, NOT applied (needs a decision that is not mine):**

- The three cells stamp `tp_geometry: live_parity` while their evidence is `live_parity_uncapped`. The matrix's `tp_geometry_legend` has **no value for uncapped-is-parity**, so correcting the field means extending the legend, which changes what `m20_coverage_rollup.py` grades staleness from. **Proposal:** add a `live_parity_uncapped` legend value ("measured with no TP clamp, on a unit that applies none — parity for that unit, and NOT poolable with capped rounds") and restamp these three cells. That is a Tier-1 change but a cross-cutting one; it belongs in its own PR, not smuggled into a reconciliation.
- The three cells carry no `basis`. `harness_only` fits them exactly by the legend's own words ("a real measurement, and not evidence about live code"). **Proposal:** stamp `basis: harness_only` on all three. Not applied here because PR #11140 is concurrently editing this file's `basis` fields and a conflict on the matrix is worse than a one-PR delay.

---

*Investigated 2026-09-06 against `origin/main` `957fc81d`, matrix `updated_at 2026-09-06`, rounds file at its only commit `7aa293da`. Every count in this memo states its population.*
