# Positive control on the M20 exit-lever sweep — the harness DETECTS a planted effect

**MI-145** · work object [`WO-20260906-POSITIVE-CONTROL-ON-THE-EXIT-SWEEP-320`](../claude/work/objects/WO-20260906-POSITIVE-CONTROL-ON-THE-EXIT-SWEEP-320.yaml) · registry key `pending-20260906T120641Z` · branch `claude/sweep-positive-control-20260906`.

⚠️ **PROPOSE-ONLY on anything Tier-3.** No `src/`, no `config/`, no exit geometry, no lever declared or flipped. The control writes nothing to the coverage matrix and its cells never enter it.

## Verdict, in one line

**Outcome 1 of the object's three: the harness DETECTS it.** A planted, provably-dominant cell was graded **`PASS`** by the sweep's own detector — `beats()` in both windows and **6/6** walk-forward folds, `inert_wins: 0`. The detector is not blind, and the three arms together show why: the wrapper is faithful, the two arms are isolated, and a real effect propagates end-to-end.

**But the object's premise needs correcting in a way that matters more than the control does.** The 320 `honest_negative` cells were never all unfalsifiable. The sweep's own corpus already contains **91 passing cells**, and **35 (leg, lever) pairs the sweep PASSED read `honest_negative` in the matrix.** The word `honest_negative` does not mean *"the sweep found nothing"* — see § 3.

---

## 1 · The planted control

### What is under test

Not the market, and not any lever: the **detector** — the chain `run_cell → beats → walkforward → verdict` inside [`scripts/research/m20_fleet_exit_sweep.py`](../../scripts/research/m20_fleet_exit_sweep.py). Every one of those is **imported and called unmodified** by [`scripts/research/exit_sweep_positive_control.py`](../../scripts/research/exit_sweep_positive_control.py). The only new code is a wrapper harness, [`scripts/research/_control_oracle_harness.py`](../../scripts/research/_control_oracle_harness.py), which runs the real harness as a subprocess, reads the real per-trade book out of `--emit-trades`, and applies one declared transform to each trade's realised net R.

### The leg, and why it

`trend_donchian` · BTCUSDT · 1h · `execution: live` · `enabled: true`.

- **Its unit consumes the lever family.** `src.runtime.pipeline.monitor_unit_for("trend_donchian")` → `"trend_donchian"`, and that unit contains `giveback_verdict`. **Resolved by calling the production resolver in this session**, not taken from MI-146 on trust.
- **It is the census's highest-value leg** and it is the one MI-146 flags as carrying live-parity counter-evidence already.
- ⚠️ **It DOES have the no-reachable-take-profit property the brief asks about.** `tp_r: 50.0` — one of the 15 sentinel legs whose placed target is set by `TP_VENUE_CAP_PCT = 0.099` rather than by any expectation. **This is deliberate and it is a virtue here, not a confound:** the planted transform acts on realised R directly and does not depend on a target being reachable, so unlike a giveback or trailing lever this control cannot fail for the sentinel-target reason. It is the one control shape that is *immune* to the confound MI-146 identified.

### The arms — pre-registered in the source before any run

| arm | comparison | expectation, written down first |
|---|---|---|
| **N1** wrapper faithfulness | wrapper(identity) vs the real harness | identical `net_total_r` / `max_drawdown_r` within rounding |
| **N2** inert-lever null | real harness: base vs base + a giveback stop armed at 10,000R | `d_net_r == 0.0` and `d_max_dd == 0.0` **exactly**; verdict `is_oos_fail` |
| **O1** loss-free oracle | wrapper(identity) vs wrapper(loss_free), `net_r' = max(net_r, 0)` | **PASS** — `beats()` in both windows, `ok` in every usable fold |

**Why `loss_free` and not "exit at MFE".** Exiting at maximum favourable excursion is the intuitive oracle and it is **not provably dominant on drawdown** — the drawdown term is path-dependent, so a book in which every trade improves can still carry a larger drawdown, and the control would then fail for a reason that says nothing about the detector. `loss_free` pins *both* terms of the gate: every trade's R weakly improves (so `net_total_r ≥` base, strictly `>` with one loser present) **and** every R is `≥ 0` (so the equity curve is monotone and `max_drawdown_r` is exactly `0.0`). All three clauses of `beats()`'s `cn >= bn and cd <= bd and (cn > bn or cd < bd)` hold by construction.

⚠️ **Nothing was tuned.** The transform was fixed before the run and run once. The expectations above are in the driver's docstring in the commit that predates the run.

### The result

**MEASURED** — trainer VM, full-history run, **trainer-diag [#11134](https://github.com/benbaichmankass/Metis-Insights/issues/11134)** (workflow run `34032720336`), 2026-09-06T12:19Z. Data `data/BTCUSDT_15m.csv` resampled to 1h, lifetime **366 trades**, IS/OOS boundary **2025-11-23** derived by the sweep's own `resolve_split` (`oos-trades`, target 50) → **IS n=316, OOS n=49**.

**N1 — wrapper is faithful.**

| | real harness | wrapper(identity) | Δ |
|---|---:|---:|---:|
| `net_total_r` | 30.9833 | 30.9828 | **−0.0005** |
| `max_drawdown_r` | 29.6506 | 29.6508 | **+0.0002** |
| trades | 366 | 366 | 0 |

**N2 — the arms are isolated.** A giveback stop no trade can arm changes the book by **exactly zero** in both windows:

| window | base n | cell n | `d_net_r` | `d_max_dd` | verdict |
|---|---:|---:|---:|---:|---|
| IS | 316 | 316 | **0.0** | **0.0** | `is_oos_fail` (`tie_no_improvement`) |
| OOS | 49 | 49 | **0.0** | **0.0** | `is_oos_fail` (`tie_no_improvement`) |

This is the arm most people would skip and it is the one that makes O1 meaningful: it shows the two arms differ *only* by the lever, so a measured delta is attributable.

**O1 — the detector sees it.**

| window | base `net_r` | cell `net_r` | `d_net_r` | base `maxDD` | cell `maxDD` | `d_max_dd` | `beats` |
|---|---:|---:|---:|---:|---:|---:|:--:|
| IS | 57.6383 | 293.7001 | **+236.0618** | 22.2798 | 0.0 | −22.2798 | ✅ |
| OOS | −26.1865 | 13.6048 | **+39.7913** | 29.5677 | 0.0 | −29.5677 | ✅ |

Walk-forward **6/6**, `summary_effective` **6/6**, `inert_wins` **0** — every fold moved the book and none was a `0 >= 0` free pass:

| fold | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|
| `d_net_r` | +20.59 | +55.14 | +61.66 | +51.32 | +59.41 | +25.77 |
| `d_max_dd` | −9.32 | −15.06 | −22.28 | −15.30 | −12.51 | −17.88 |

**Verdict: `PASS`.**

### One thing the control found about itself

On its first smoke run (locally, 3.5 days of BTC candles) both windows produced **zero trades**, and `beats()` returned `False` with `reason: tie_no_improvement` — **byte-identical to a measured negative**. A control that reports `is_oos_fail` on an empty book would have manufactured exactly the false verdict it exists to detect. The driver now reports `could_not_test: empty_base_book` instead. The sweep itself guards this correctly (`insufficient_base`, 182 corpus rows), so this is a note about writing controls, not a defect in the sweep.

---

## 2 · What the control does and does not establish

**Establishes.** The detector chain is sound and not blind. A cell that genuinely dominates propagates from the harness through `beats`, the candidate gate, the walk-forward and the verdict to `PASS`, with no stage swallowing it.

⚠️ **Does NOT establish sensitivity.** The planted effect is enormous — **+236R IS** against real levers that move the book by **1–8R**. A control is a **floor** on an instrument's credibility, never a ceiling. A detector could pass this and still be unable to resolve a realistic effect.

**But that gap is closed from the other direction, by the sweep's own record.** **POPULATION: all 1,376 `kind=cell` rows of [`docs/research/m20-sweep-corpus.jsonl`](m20-sweep-corpus.jsonl)** — 51 legs, 110 distinct cell names, 218 run ids, sweep dates 2026-08-10 → 2026-08-17. Of the 91 rows carrying a passing verdict and a readable `d_net_r_OOS`:

| | `d_net_r_OOS` |
|---|---:|
| minimum passed | **+0.0010 R** |
| p10 | +0.3823 R |
| median | +1.7950 R |
| maximum | +15.5321 R |
| passes under 1.0R \| under 0.5R | **21** \| **10** |

**So the sweep has passed a cell at one thousandth of an R.** Resolution is not its problem. Planted control (plumbing) plus this record (resolution) together are a reasonable basis for trusting the instrument on this axis.

---

## 3 · The premise needs correcting — `honest_negative` does not mean "the sweep found nothing"

This is the larger finding and it changes what the 320 cells mean.

**POPULATION as above (1,376 corpus cells, 225 distinct `(leg, lever)` pairs), joined by `leg → matrix.rows[].strategy` to `docs/research/exit-refinement-coverage.json` (`updated_at 2026-08-30`).**

| | n |
|---|---:|
| corpus cells graded `PASS` | 42 |
| corpus cells graded `path_b_wf_pass` | 49 |
| **total passing cells** | **91 of 1,376 = 6.6%** |
| cells clearing the Path A candidate gate in both windows (`is_oos_pass`) | 97 |
| cells that reached a walk-forward at all (`wf_ran`) | 134 |
| **distinct `(leg, lever)` pairs with ≥1 passing cell** | **50 of 225 = 22.2%** |

**Matrix status of those 50 pairs:**

| matrix status | n |
|---|---:|
| **`honest_negative`** | **35** |
| `shipped` | 7 |
| `passed_unshipped` | 4 |
| `shipped_gate_failed` | 1 |
| `blocked:insufficient_oos_base_at_derived_split` | 1 |
| `blocked:insufficient_lifetime_trades` | 1 |
| no matrix cell | 1 |

The 35, with the best OOS delta the sweep measured on each: `gld_pullback_1h trail_decay` +8.326 · `tlt_pullback_1d trail_decay` +8.316 · `gld_pullback_1h vol_trail` +8.267 · `trend_donchian_eth trail_decay` +4.123 · `trend_donchian_sol trail_geometry` +4.160 · `qqq_pullback_1h trail_geometry` +3.824 · `eth_pullback_prop_2h trail_decay` +3.783 … down to `scha_trend_long_1d vol_trail` +0.127.

⚠️ **This is NOT a guard failure and NOT dishonesty.** `scripts/ci/check_matrix_corpus_agreement.py` run this session reports `OK — 220 live cell(s) checked against 1379 corpus row(s); no unacknowledged disagreement`. Every one of the 35 carries its counter-evidence **in the cell's `ref` prose**, and the guard's `ACK` regex requires it. The matrix is doing something deliberate and defensible: *a passing CELL is not a passing LEVER disposition, and changing a live leg's disposition is Tier-3 and belongs to the operator.*

**The defect is that the status WORD cannot carry the distinction.** `honest_negative` today spans two states that are not the same fact:

- *the sweep measured this lever on this leg and it lost* — and
- *the sweep measured it, it WON on ≥1 cell across a walk-forward, and the disposition is withheld pending an operator decision.*

That is a **collapsed state** in this repo's own canonical sense (`docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed states"): two distinct states sharing one value, with the dangerous one missing. And the consequence is concrete, not stylistic — **every aggregate over the matrix reads the status word, not the prose.** The 68.4% `honest_negative` headline in this object's own `why:`, and MI-146's *"`vol_trail`: honestly failed everywhere … nothing to build"*, are both computed off the status column. Measured against the corpus, `vol_trail` passed a cell on **13** legs.

⚠️ **State the population on that last claim:** 13 of the 35 pairs above carry `lever == "vol_trail"`, from the same 1,376-row corpus. It does **not** say `vol_trail` should ship anywhere — a Path B pass in particular buys net R by spending drawdown, and `beats()`'s own docstring records that `path_b_wf_pass` was **0-for-3** on the drawdown exchange rate in one measured run. It says the one-word summary is lossy in the direction that closes questions.

**Proposed remedy (Tier-1, doc/status only, NOT applied here):** a distinct status — e.g. `negative_disposition_withheld` — or the `basis` field of § 4 extended to carry `corpus_pass: true`. Either makes the roll-up count it correctly without touching a live leg's disposition, which stays the operator's.

---

## 4 · Second deliverable — MI-146's recommendation #1, verified independently

### 4.1 The mislabels: 31 confirmed, 2 NOT CHECKED

MI-146 enumerates **33** cells graded as tested for a lever with no runtime consumer in that leg's own unit. **I re-derived this rather than copying the list**, using two imported production functions — `src.runtime.pipeline.monitor_unit_for` for leg→unit and `scripts.ops.exit_mechanism_coverage.module_implements` for unit→lever — neither re-implemented.

**POPULATION: every `(leg, lever)` cell in the matrix's 52 rows whose status is one of `honest_negative` / `shipped` / `passed_unshipped` / `shipped_gate_failed`, restricted to the 4 of 9 lever columns that have a canonical detector in `exit_mechanism_coverage.MECHANISMS`.**

**Result: 31 cells confirmed.**

| lever column | n | unit |
|---|---:|---|
| `exit_head_ml` | 17 | `htf_pullback_trend_2h` |
| `giveback_stop` | 8 | `ict_scalp` |
| `exit_head_ml` | 2 | `ict_scalp` |
| `giveback_stop` | 1 | `squeeze_breakout_4h` |
| `giveback_stop` | 1 | `fvg_range_15m` |
| `stale_stop` | 1 | `squeeze_breakout_4h` |
| `stale_stop` | 1 | `fvg_range_15m` |
| **total** | **31** | |

⚠️ **The other 2 are NOT REFUTED — they are UNCHECKED, and the reason is itself the finding.** `MECHANISMS` has entries for exactly four levers (`stale_stop`, `giveback_stop`, `exit_head`, `trail_decay`). MI-146's remaining two are **1 `vol_trail`** and **1 `exit_ladder passed_unshipped`**, and **no canonical detector exists for either**, so this probe cannot speak to them in either direction. `31 + 2 = 33` reconciles exactly with MI-146's count, which is a real cross-check of both readings — but *"we did not look"* is not *"we looked and found nothing"*.

### 4.1b ⚠️ MI-146's recommendation, applied literally, would have been WRONG on 11 of the 31 — and CI would have caught it

Before editing I asked whether each of the 31 cells has rows in the sweep corpus. **11 of them do.**

**POPULATION: the 31 verified cells, joined by `(leg, lever)` to the 1,376-row corpus.**

| | n | correct status |
|---|---:|---|
| **no corpus rows** — nothing was ever measured, and the unit cannot run it | **20** | `blocked:no_lever_consumer_in_unit` ✅ |
| **has corpus rows** — the sweep DID measure it, on the harness's own engine | **11** | stays `honest_negative` + `basis: harness_only` |

The 11: `squeeze_breakout_4h` (`stale_stop`, `giveback_stop`), `fvg_range_15m` (`stale_stop`, `giveback_stop`), and `giveback_stop` on seven `ict_scalp_*` legs.

**Why the literal application is wrong, on two independent grounds.** Substantively, `blocked:no_lever_consumer_in_unit` asserts *nothing was measured*, and for these 11 something was — the harness families (`backtest_ict_scalp.py`, `backtest_squeeze.py`, `backtest_fvg_range.py`) implement the lever even though the live unit does not. Re-statusing them would delete a real measurement to fix a labelling problem. Mechanically, `scripts/ci/check_matrix_corpus_agreement.py` refuses *"a blocked/pending cell the corpus has already graded"*, so the edit would have failed CI — which is the guard doing exactly its job.

**So the correct edit is 20 re-statuses, not 33, and the remaining 11 need the `basis` field instead — which is why § 4.3 is applied here rather than merely proposed.** This is the difference the brief's *"verify each yourself, do not copy the list on trust"* was asking for, and it only surfaced because the check was run.

### 4.2 The matrix edit is ratchet-safe — MEASURED, not assumed

The brief warns that editing the matrix can loosen the `tp_geometry` ratchet while looking innocent (`BL-20260823-TP-GEOMETRY-RATCHET-LOOSENS-WHEN-THE-LIVE-DENOMINATOR-SHRINKS`). I simulated the exact edit and ran the real validator.

**MEASURED** — `scripts/research/m20_coverage_rollup.py::_validate_tp_geometry` imported and called on the current matrix and on a deep copy carrying the 31 re-statuses:

| | live cells | `unstamped` | declared `_unstamped_ceiling` | validator |
|---|---:|---:|---:|---|
| before | 396 | 229 | 229 | clean |
| after 31 re-statuses | 396 | **229** | 229 | **clean** |

**Effect: exactly zero, in both directions.** `cells(matrix, live_only=True)` enumerates cells by the **row's** live-ness, not by status, so a status change moves neither numerator nor denominator.

⚠️ **This holds ONLY for a status-only edit that preserves each cell's `tp_geometry` field.** Dropping `tp_geometry` when demoting a cell to `blocked` — the natural thing to do — would push `unstamped` **above** 229 and the guard would correctly fail. The loosening direction needs a leg demoted out of `live`, which this edit does not do.

### 4.3 The `basis` field — APPLIED, on the cells that were verified, and only those

`basis` separates a negative measured on code that runs in production from one measured on a lever with no runtime, so the ~87 defensible research-before-build negatives (`exit_ladder` 45, `regime_flip_exit` 42) stay `honest_negative`, correctly, and a reader can see what they rest on.

Vocabulary added to the matrix as `basis_legend`: `live_lever` · `harness_only` · `no_runtime_consumer`.

⚠️ **It is stamped on the 31 cells this session VERIFIED, and on nothing else.** A cell with no `basis` has not been assessed on this axis — *we did not look* — and the legend says so explicitly (`ABSENT_means_unstamped`). Back-filling `live_lever` across the other 437 cells would assert 437 checks nobody ran, which is the same fabrication as writing `0.0` for an unmeasured quantity.

### 4.3b What actually changed in the matrix

**MEASURED — all 468 cells (52 rows × 9 lever columns), before and after:**

| status | before | after |
|---|---:|---:|
| `honest_negative` | 320 (68.4%) | **300 (64.1%)** |
| `blocked:no_lever_consumer_in_unit` | 5 | **25** |
| `shipped` | 39 | 39 (unchanged) |

Guards run after the edit, all green: `check_matrix_corpus_agreement` (OK, 220 live cells vs 1,379 corpus rows), `check_matrix_config_agreement` (OK, shipped-ness agrees), `check_matrix_bracket_values` (OK), `check_lever_reachability` (OK), `check_lever_wiring` (clean, 5 levers), `m20_coverage_rollup` (exit 0), and `tests/test_m20_tp_geometry_guard.py` + `tests/test_corpus_lever_in_baseline.py` (18 passed).

### 4.4 The CI check (PROPOSAL) — with its coverage stated up front

Fail a cell graded `honest_negative` / `shipped` whose lever has no consumer in the leg's `monitor_unit_for` unit. **Build it on the two functions above rather than a fresh grep** — `BL-20260818-CAPABILITY-AUDITS-GREP-ONE-FILE-AND-MISS-SHARED-LEVERS` is what a fresh grep costs.

⚠️ **It would cover 4 of 9 lever columns, and it must SAY so on every run.** `MECHANISMS` has four entries; a guard that prints `OK` while silently skipping five columns is the "green while measuring nothing" failure this repo already names. Print the not-checked columns as its own line, the way `check_matrix_corpus_agreement.py` already does (*"NOT CHECKED (no corpus rows for the lever, declared): 176 live cell(s) across 4 column(s)"*).

⚠️ **And give it a planted self-test.** `scripts/ci/check_lever_wiring.py` already does this — it injects a `_planted` lever named `no_such_lever` and asserts the guard fires. That is the same discipline as this memo, one level down, and it is the precedent to copy.

---

## 5 · A separable observation: 212 cells whose lever never moved the book

**POPULATION: the same 1,376 corpus cells; 1,376 carry a readable `d_net_r_OOS`.** **212 of them (15.4%) are exactly `0.0`.** The sweep already withholds some inert cells (`cells_withheld_inert`) and `walkforward` already flags inert folds (`is_inert`, `BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS`), so this is the *window*-level residue of a class the repo has already named at the *fold* level.

⚠️ I have **not** established that all 212 are unarmed levers rather than levers that fired to a net-zero effect — that needs the trade counts and `d_max_dd` per row, which I did not join. Stated as an observation with its limit, not as a finding.

---

*Control executed on the trainer VM via trainer-diag [#11134](https://github.com/benbaichmankass/Metis-Insights/issues/11134) (workflow run 34032720336), 2026-09-06T12:19Z. Corpus and matrix read at `origin/main` `782dce55`. Detector: `scripts/research/m20_fleet_exit_sweep.py`, imported unmodified.*
