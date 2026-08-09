# Sprint Log: S-M20-TREND-ENGINE-RETIREMENT-2026-08-09

## Date Range
- Start: 2026-08-09
- End: 2026-08-09

## Objective
- Primary goal: **retire the second trend engine** (`scripts/research/backtest_trend.py`) behind a convergence guard, closing `BL-20260808-RESEARCH-TREND-ENGINE-RETIREMENT-BLOCKED-BY-TEST-COUPLING` — the deferred half of PR #8633, which converged the FLAGS but left the losing ENGINE running.
- Secondary goals: (a) make the exit-head artifact record its **training window** so a replay can state its own in-sample fraction (`BL-20260808-EXIT-HEAD-MANIFEST-RECORDS-NO-TRAINING-WINDOW`); (b) re-sweep `trend_donchian`'s live trail-decay lever on the converged engine and produce a **Tier-3 proposal** (`BL-20260808-TRAIL-LEVER-TUNED-ON-NON-LIVE-FAITHFUL-TRAIL`) — propose only, never merge a config change.

## Tier
- Tier 1 (items A + B + the C *instrument*); the C **outcome** is a Tier-3 proposal handed to the operator.
- Justification: research tooling, tests, CI guards and docs. No `config/` write, no order path, no DB write, no VM mutation. The three `src/` files touched (`runtime/trail_decay.py`, `runtime/trail_vol.py`, `units/strategies/trend_donchian.py`) are **comment/docstring only** — each corrected a citation that pointed at an engine retired in this same PR. The trainer VM was used **read-only** (relay reads + one research sweep); nothing was deployed.

## Starting Context
- Active roadmap items: M20 (exit refinement) tooling — the harness-convergence workstream.
- Prior sprint reference: [`S-TREND-HARNESS-CONVERGENCE-2026-08-08`](S-TREND-HARNESS-CONVERGENCE-2026-08-08.md) (PR #8633 flag convergence, #8643 trainer venv, #8648 `_REPO_ROOT`, #8657 the training-window backlog row). Its "Next Recommended Sprint" section IS this sprint.
- Known risks at start, carried in from that log and honoured: do **not** blanket-repoint the five coupled test files; grep the **module name**, never the path; CI is the authority for the full suite; `main` moves constantly.

## Repo State Checked
- Branch or commit reviewed: started from `origin/main` @ `10990013` (#8657); branch `claude/m20-trend-harness-workstream-ipa3ce`, PR #8660.
- Deployment state reviewed: trainer VM HEAD confirmed `10990013` via relay (#8661) — i.e. it carries the converged engine. Live VM untouched.
- Canonical docs reviewed: `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md`, `ROADMAP.md` (M20 row), coordination board #6927, `exit-refinement` skill.

## Files and Systems Inspected
- Code files inspected: both `backtest_trend.py` copies **in full** (the entry/exit loops line by line, not summaries), `scripts/research/{trend_harness_divergence,regime_matrix,regime_tag_emitted,regime_adx_cutpoint_sweep}.py`, `scripts/ml/{export_exit_head,exit_head_replay,build_exit_head_dataset,train_exit_head}.py`, `scripts/ci/run_guards.py`, `scripts/check_harness_lever_coupling.py`, the five coupled test files, `tests/test_trend_harness_levers.py`.
- Config files inspected: `config/strategies.yaml::trend_donchian` — **read only, not modified**.
- Deployment files inspected: none.
- Docs inspected: `docs/research/{RESEARCH-CAPABILITY-INDEX,FAITHFUL-BACKTEST-PLATFORM-DESIGN-2026-08-04,exit-refinement-coverage.json}`, `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`.
- Services or timers inspected: `ict-trainer-git-sync` (HEAD confirmed via relay rather than assumed).
- GitHub Actions workflows inspected: `guards.yml` (via `run_guards.py`), `trainer-vm-diag.yml`, `ict-scalp-exit-sweep.yml` (as the candle-acquisition precedent).

## Work Completed

### Item A — the retirement (PR #8660)

**A1. The five coupled tests, decided per file on MEASUREMENT.** Before touching anything I ran both engines over each test file's own tape and compared trade sets. Four files are **engine-independent** — every assertion is entry-side, and both engines enter the same bar — so they were repointed with no expected number moving. One is **genuinely engine-specific** and was ported with re-derived numbers.

`tests/test_vol_conditional_trail_lever.py` is that one: it reads `exit_time` and `r_multiple`. On its tape (42 bars, donchian 10 / atr_period 5 / atr_stop_mult 2.0 / trail_mult 6.0 / timeout_bars 40 / cooldown_bars 0) the trade **set** differs, not just the values:

| run | research (retired) | live-faithful (now) |
|---|---|---|
| base | 1 trade, long 06:00→10:00, r −1.0000 | 2 trades: long 06:00→09:00 r −0.3214, short 10:00→18:00 r +0.4386 |
| `above=0.6 win=20 tight=2.0` | 2 trades: long 06:00→07:00 r +0.6786, then a same-bar **re-entry** the cooldown prevents here | long 06:00→**08:00** r **+1.6786**, short unchanged |

The research engine has no post-exit cooldown, so its tightened trail produced a re-entry the live-faithful engine never takes; conversely the live-faithful engine takes a short on the give-back leg that the research engine's flip exit suppressed. The *contract* survives; the numbers do not.

Ported **with a non-vacuity assertion**: "gated exits no later" and "gated banks ≥ R" are both satisfied by a lever that does nothing. A **negative control** was run — an armed-but-inert lever (`tight_mult == trail_mult`, and a never-filling percentile window) fails the ported assertions. 30 tests before, 30 after.

**A2. IO helpers lifted, not repointed at the engine's own reader.** `_load`/`_resample` moved **verbatim** to `scripts/candle_io.py`. Deliberately *not* repointed at `scripts/backtest_trend.py::_load_candles`: that reader is strictly narrower and raises `KeyError: 'timestamp'` on the JSONL the IBKR pull writes, which `regime_matrix` and `regime_tag_emitted` both document reading. The lift was proved behaviour-preserving (5,000 rows `DataFrame.equals` True; identical resamples at 5m/5min/1h/4h). Filed as `BL-20260809-TWO-CANDLE-READERS-DIVERGE-ON-JSONL` rather than folding a fidelity-pipeline input change into a retirement.

**A3. `regime_matrix.py` engine swap, re-verified.** Its output MOVED. *Population: `data/backtest_candles.csv`, BTCUSDT resampled 1h → 84 bars, 2022-07-23 22:00Z → 2022-07-27 09:00Z; donchian 20 / atr_period 14 / atr_stop_mult 2.5 / trail_mult 5.0.*

| | trades | net R | entry regime |
|---|--:|--:|---|
| BEFORE — retired engine | 1 | **+1.5727** | trending |
| AFTER — live-faithful | 1 | **−0.2246** | transitional |

Sign flip **and** bucket change, **100% attributable to the engine**: `cooldown_bars` ∈ {0,1,3} and `timeout_bars` ∈ {200,10000} all give −0.2246 on this corpus. **n = 1**, so DIRECTION only, never a magnitude. Consequence recorded in the module docstring: any regime-matrix number from before 2026-08-09 was produced on the engine the live strategy does not match.

**A4. Hard-fail shim, not a deletion.** A missed caller gets a migration map instead of a `FileNotFoundError`, and the name stays occupied so a third copy cannot quietly appear. It stays **importable on purpose** so the guard can confirm it exposes no entry point.

**A5. The convergence guard.** `trend_harness_divergence.py` stopped being a two-engine comparison (nothing left to compare) and became `trend-engine-convergence-guard`, wired into `run_guards.py` with `when: None`. It detects a retired copy by the **absence of an engine entry point**, read from the AST — never by parsing prose. It **asserts its own denominator** (if the canonical engine loses `run_backtest`, "no second engine" would be vacuously true, so that is itself a finding), and ships three `--self-test` cases run in CI ahead of it.

**A6. Three stale citations corrected** (`trail_decay.py`, `trail_vol.py`, `trend_donchian.py`) that pointed at the now-retired engine. Comment-only.

### Item B — the exit-head training window (PR #8660)

`scripts/ml/export_exit_head.py` now writes `train_start` / `train_end` / `train_window_coverage` / `train_dataset` beside `trained_at`, via a new pure helper `training_window(rows)` deriving the bound from each row's `bar_t`. Honest-null with its own coverage metric.

`scripts/ml/exit_head_replay.py` gained `split_in_sample()`, emitting `in_sample_bars` / `forward_bars` / `in_sample_trades` / `forward_trades` plus a **forward-only** baseline/replayed/delta gross R — **printed beside the headline delta**, not only into `--json`, because the delta alone is what gets quoted.

Failure modes chosen deliberately: a pre-fix artifact reports `train_window_present:false` and nulls throughout (`trained_at` is never substituted); an unparseable `train_end` degrades to UNKNOWN, never epoch 0 (which would manufacture a fully-out-of-sample claim); a row exactly on `train_end` counts as IN-sample (the conservative direction). **The replay window was NOT widened** — a longer window makes the in-sample fraction *larger*.

12 tests pin the arithmetic, not the source text: on a fixture mirroring the #8653 shape the headline delta is **+9.25** while the forward-only delta is **+2.25**.

### Item C — trail-lever re-sweep (Tier-3 proposal)

Committed `scripts/research/m20_trail_resweep.py` as a re-runnable instrument (indexed in `RESEARCH-CAPABILITY-INDEX.md`) and ran it on the trainer against the converged engine. Full memo: [`M20-trail-decay-resweep-2026-08-09`](../research/M20-trail-decay-resweep-2026-08-09.md).

*POPULATION: BTCUSDT 15m→1h (124,684 native 15m bars), 2023-01-01 → 2026-07-22, **250 baseline trades** (175 IS / 75 OOS at split 2025-07-01), config-exact (donchian 20 / atr_period 14 / atr_stop_mult 2.5 / trail_mult 5.0 / min_confidence 0.7 / long_only), 15 cells × 7 windows = 112 harness runs. Trainer-diag #8662 (sweep) + #8664 (gate table).*

**The live values do not hold.** `arm6.49 / tight2.5` **FAILS** the gate: net_R improves in both windows but **IS maxDD worsens by +0.4220R** (14.7968 → 15.2188). The P4.4 justification that shipped it — *"improves both axes"* — **does not reproduce**: the baselines are different books (IS net_R 42.38 not 51.8; OOS −12.51 not −24.5) and the drawdown sign **flips** on the live-faithful engine. That is exactly the defect the backlog row predicted, now measured rather than argued.

`arm6.49 / tight2.0` **strictly dominates** the live cell on every measured axis (IS dd back to baseline, OOS net_R −10.4847 → −9.8853, folds 2/4 → 3/4) at a cost of −0.30R IS net_R, and is the recommended Tier-3 diff.

**The lever has been inert all year.** In 2026-to-date (35 trades) the largest peak-R reached is **4.593**, below the 6.49 arm — so it provably cannot have fired. Verified two ways (`max_mfe_r`, and an exact identity check against the lever-OFF arm).

Stated limits, because they bound what the proposal can claim: **2 of 15** cells clear even a *non-worsening* reading of the maxDD axis and **zero** clear a strict *beats-both-axes* reading, so selection risk is real at 15 comparisons; and the leg is **OOS-negative either way** (−12.5137 with the lever off), so this is a tuning-basis correction, not a profitability fix.

**No config change is merged.** Three options (retune to 2.0 / remove the lever / hold) are put to the operator with exact YAML diffs; the recommendation is the retune. `BL-20260808-TRAIL-LEVER-TUNED-ON-NON-LIVE-FAITHFUL-TRAIL` stays **open** pending that decision.

The coverage matrix row was also corrected: it read `trail_decay: honest_negative` while the lever has been **live since #6273**.

## Validation Performed
- Tests run: 30 lever tests before → 30 after (no test lost); 12 new training-window tests; 257 tests across `tests/research/` + every suite touching the changed modules. CI `pytest-run` on the PR head.
- Dry-runs or staging checks: per-file two-engine comparison BEFORE migrating each test; negative control on the ported exit-side assertions; `candle_io` equivalence proof; `regime_matrix` before/after decomposition; guard self-test 3/3 **including under a simulated dependency-free environment** (pandas/numpy/lightgbm/yaml blocked via a `meta_path` hook), which is the environment the `guards` CI job actually has.
- Manual code verification: read both engine loops in full rather than trusting the prior session's summary; verified `--confirm-bars` is declared by the canonical engine before correcting the comment that said otherwise.
- Gaps not yet verified: see Risks.

## Documentation Updated
- Rules doc updates: none needed.
- Architecture doc updates: none — no schema/boundary/pipeline-stage change.
- Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`): **not applicable** — no pipeline stage touched (research tooling + trainer-side ML tooling only), so the dashboard Trade Process tab needed no visual verification.
- Roadmap updates: M20 row extended with this sprint.
- GitHub Actions doc updates: the new guard is registered in `run_guards.py`, which is self-documenting; no separate entry needed.
- Subsystem doc updates: `RESEARCH-CAPABILITY-INDEX.md` (retirement + `candle_io` + the new sweep instrument), `FAITHFUL-BACKTEST-PLATFORM-DESIGN-2026-08-04.md` §5f instrument pointer marked superseded, `regime_matrix.py` docstring.
- Historical docs marked superseded: the two-engine divergence measurement is preserved verbatim as the record inside the guard's docstring.

## Contradictions or Drift Found
- **My own board comment was wrong, and I corrected it publicly.** I claimed the predecessor session's DONE comment had a typo (`10990013` vs `1099001`). It did not — those are the 8- and 7-character abbreviations of the same commit. The underlying observation that *was* right: I first read the #8657 backlog row as missing from `main` because I had fetched `origin/main` ~2 minutes before it merged. **Re-fetch before concluding ABSENCE, not just before merging.**
- **`trend_donchian`'s `trail_decay` coverage-matrix status contradicts the live config.** `docs/research/exit-refinement-coverage.json` records `honest_negative` ("tested, failed the gate — hard levers/trail stand") while `config/strategies.yaml` declares `trail_decay_arm_r: 6.49` **live**, and `ROADMAP.md` records that cell as "MERGED + ACTIVATED (#6273)". The row's own `ref` text even carries a "P4.4 UPDATE: passed_unshipped" note — so the ref moved twice and the `status` field never followed. A live lever reading as a closed negative mis-states the milestone's done-condition roll-up. Corrected in this PR (see Item C).
- **A guard I wrote failed its own first CI run, correctly.** Detail under Deferred/Risks — it is the most reusable finding here.

## Risks and Follow-Ups
- Remaining technical risks: `BL-20260809-TWO-CANDLE-READERS-DIVERGE-ON-JSONL` is filed, not fixed — converging `_load_candles` onto `candle_io` would widen its NaN-drop and could move a number on a real corpus, so it needs its own before/after over the fidelity corpora.
- Remaining product decisions (Tier 3): the Item C proposal below. **No config change is merged in this sprint.**
- Blockers: none for the merged work.

## Deferred Items
- **Converging the two OHLCV readers** — deliberately out of scope (above).
- **Re-exporting the live exit head so it carries `train_start`/`train_end`.** Item B makes the exporter write the window, but `exit-head-donchian-1h-v1` on the trainer was exported before the fix and still has none. Until it is re-exported, the replay correctly reports `in-sample split: UNKNOWN` rather than guessing. That re-export is a trainer run, not a code change.

## Next Recommended Sprint
- Suggested next sprint: re-export the exit head with the training window, then re-run the #8653 replay on a **forward-only** window (`start > train_end`) to get the first genuinely out-of-sample exit-head number.
- Why next: Item B made the in-sample fraction *readable*; it did not make any existing measurement out-of-sample. The forward-only replay is what converts the +10.804 headline into evidence or retires it.
- Required verification before starting: confirm `ict-trainer-git-sync` has the merged commit (15-minute timer) and that the replay prints a `== git sha:` matching it — a run on pre-fix source tells you nothing about the fix.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated and the dashboard's Trade Process tab was visually verified. — **N/A: no pipeline stage touched.**
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.

---

## The reusable lesson: my own guard failed its first CI run, and it was right to

The `trend-engine-convergence-guard` went green locally and **failed** on the PR. The `guards` CI job installs no third-party packages, so importing `scripts/backtest_trend.py` raised `ModuleNotFoundError: No module named 'pandas'`. With the canonical engine unreadable the guard had **zero engines in view**, and the denominator assertion I had written refused to report "no second engine" — that verdict would have been vacuous.

Correct outcome, unworkable resting state: a guard that cannot run in CI checks nothing.

**The tempting fix was to soften the denominator assertion until it went green.** That is exactly the green-while-measuring-nothing move `CLAUDE-RULES-CANONICAL` § "Green is not evidence" exists to stop. So the *detection* moved instead of the *assertion*: entry points are now read statically from the AST, which needs no dependencies. The contract was always "absence of an entry point, never prose", and an AST node is a fact about the code in exactly the way a docstring saying RETIRED is not. The import probe is kept as corroboration, and a disagreement between the two reads is reported as its own finding rather than silently resolved.

Two things worth carrying forward:

1. **A guard's self-test is not ceremony.** Mine caught a real bug in the guard on its first run — `declared_flags` and `engine_entry_points` read the real repo instead of the temp tree, so two of three self-tests failed. Without them the guard would have shipped looking green while auditing the wrong directory.
2. **Write the self-test to run where the guard runs.** I verified the final version under a simulated dependency-free environment before pushing, because "passes on my machine with pandas installed" was precisely the assumption that failed.
