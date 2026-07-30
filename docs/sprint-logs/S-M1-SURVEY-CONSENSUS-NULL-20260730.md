# S-M1-SURVEY-CONSENSUS-NULL-20260730

## Date Range

2026-07-30 (single session).

## Objective

Push the M1/M3 macro-events line to a real verdict, per the operator-approved gate
change: build the missing backfill siblings, validate the PIT expectation model against
captured survey consensus (M3 — the gate's own satisfiability condition), and run the
surprise → forward-return event study at real n. Audit the research skills/tooling so a
research session cannot repeat the day's earlier misunderstandings.

## Tier

Tier-1 throughout — observe-only research tooling, docs, skills, CI guards. No order
path, no live-VM mutation, no `config/strategies.yaml` / `accounts.yaml` / risk change.
One Tier-3 item was **withheld**, not shipped (see Deferred Items).

## Starting Context

The M1 study had been read as "wait for accrual until ~mid-September" at n=7. Two claims
underpinning that were false and had been corrected earlier the same day: the price join
had never returned a bar (`price_bars: 0`), and accrual was never the constraint — the
econ-calendar producer was the one macro producer built forward-only, missing its
backfill sibling.

## Repo State Checked

`main` at `b9cd1c85` at session start; merged through `f4fce04a`. Concurrent sessions were
active on the same backlog file throughout (two merge conflicts resolved, see below).

## Files and Systems Inspected

- `scripts/macro/econ_expectation_validate.py`, `econ_expectation.py`,
  `econ_calendar_snapshot_backfill.py`, `econ_calendar_survey_backfill.py`,
  `econ_event_study.py`
- `.github/workflows/econ-{calendar-backfill,calendar-survey-backfill,event-study}.yml`,
  `artifact-validity-guard.yml`, `bootstrap-labels.yml`
- `src/runtime/intents.py::_decision_vol_regime`, `src/runtime/regime/vol_detector.py`,
  `ml_vol_verdict.py`, `config/regime_policy.yaml`, `config/strategies.yaml`
- `scripts/research/regime_cell_walkforward.py`, `regime_tag_emitted.py`,
  `m20_exit_analysis.py`, `build_exit_panel.py`
- `docs/CLAUDE-RULES-CANONICAL.md`, `CLAUDE.md`, `.claude/skills/{macro-research,backtesting}/SKILL.md`

## Work Completed

- **M3 built and validated** (#8027). The survey side was thin because the forward
  producer had only ever pulled ONE window; FXStreet's calendar API takes an arbitrary
  range, so backfilling it took the joinable overlap **11 → 1263** (12,076 survey rows).
  Verdict: **`model_tracks_survey`** (Spearman 0.5885 ≥ 0.50, sign agreement 0.720 ≥ 0.70).
  `min_honest_n` was never lowered.
- **Naive-random-walk floor added** (#8043). The gate tests *tracking*, not *usefulness*:
  `initial_jobless_claims` is the **best tracker** (0.6312 / 0.791) **and** 1.101× worse
  than "assume no change since last release". Filed for an operator decision.
- **THE HEADLINE — operator-directed option 2: the event study re-run against REAL survey
  consensus, 2015→2026, returns a strong null.** natgas 553 releases / `price_bars` 2911,
  crude 575 / 2910, cpi_yoy 136 — all `no_edge_at_tested_horizons`, and on **both** energy
  kinds the strongest IC has the **wrong sign** versus the pre-registered hypothesis
  (positive, where an inventory build predicts negative).
- **EIA ids settled by measurement** (#8023): both 404 on FRED while ICSA/CCSA/CPIAUCSL
  return 3108/3107/953 obs. `--probe-extra` added so a candidate id can be verified
  *before* being committed. Scope corrected: energy **model-side** coverage, not an M1
  blocker — FXStreet carries real consensus + actuals for both energy kinds.
- **Binding rules landed** (#8050): `CLAUDE-RULES-CANONICAL` obligation 5 ("establish what
  BOUNDS n before waiting for accrual"; never lower a pre-registered bar), the
  "could not measure is its own outcome" corollary, and the same rules into the
  `macro-research` (3 → 5 invariants, frontmatter included) and `backtesting` skills.
- **`scripts/ops/check_workflow_shell.py`** — `bash -n` over every workflow `run:` block.

## Validation Performed

- 105 tests green (40 on the M3 validator, 9 on the shell guard, plus the backfill/probe
  suites). Artifact-validity, research-index, backlog-ref, workflow-shell guards green.
- `canonical-doc-coherence`: 4/4 PASS.
- The three naive-floor ratios were computed **by hand first**, then reproduced by the tool
  to three decimals.
- The shell guard was verified to **statically reproduce** the pre-fix heredoc failure —
  a guard that cannot catch its own motivating incident is decoration.
- Every event-study dispatch parameter was validated against the workflow's own resolution
  code before spending a runner.

## Documentation Updated

- `docs/CLAUDE-RULES-CANONICAL.md` — obligation 5 + the could-not-measure corollary.
- `.claude/skills/macro-research/SKILL.md` (5 invariants + frontmatter),
  `.claude/skills/backtesting/SKILL.md` (§ "Is n enough").
- `CLAUDE.md` — the MCP repo-name rule was the **inverse** of this session's reality
  (scoped to `metis-insights`, denied on `ict-trading-bot`); rewritten to say read the
  session's own allowed list, and that a scope denial is NOT the transient MCP drop.
- `ROADMAP_MACRO.md` — M1 row updated from "remaining: build X" to the concluded outcome.

## Contradictions or Drift Found

- `CLAUDE.md`'s MCP repo-name claim (fixed, above).
- `bootstrap-labels.yml` carried a duplicate `econ-event-study-now` entry — dead text under
  a get-then-skip loop (removed).
- `econ_expectation_validate.py`'s docstring asserted the OLS slope reveals a scale error;
  it does not (`slope = pearson × dispersion`). Corrected at the source, and split into
  `dispersion_ratio` (scale) + `rmse_*` (accuracy).
- One open backlog row from another session had no `severity` — marked `needs-triage`
  rather than assigning a judgement to someone else's live-path finding.

## Risks and Follow-Ups

- `BL-20260730-M3-GATE-TESTS-TRACKING-NOT-USEFULNESS` (high) — **operator decision** on the
  gate wording. Lower urgency now that option 2 landed: the study no longer needs the model
  expectation inside the survey window.
- `BL-20260730-2D-VOL-CELLS-UNAUDITABLE` (high) — scoped, not built. See Deferred Items.
- `BL-20260730-M3-CONTINUING-CLAIMS-NO-TRACK`, `BL-20260730-EIA-SERIES-IDS-NOT-FRED`
  (downgraded — model-side only), `BL-20260730-TRAINER-JOURNAL-PULL-STALE` (raised
  low → medium; it produced a wrong research reading that was attributed to a different cause).

## Deferred Items

- **Tier-3 WITHHELD: the `squeeze_breakout_4h` trending-short OFF-cell proposal.** It
  passed every gate (`short_stable_drag`, 3/4 folds, pooled −3.40R n=26, sign holding
  ex-max-fold at −0.77R) and was still withheld, because `regime_policy.yaml` gates that
  cell in **2-D** (`trending/calm`) while the harness has no vol axis — so an unknown
  fraction of those 26 trades are calm trades live already refuses. Do not resurrect it
  from a stale note; it becomes actionable only once the ML vol axis exists.
- **The vol axis itself** — scoped this session, deliberately not built. All six live 2-D
  cells are BTCUSDT, and live resolves their vol label from the BTC 15m **advisory head's**
  ML `P(volatile)` per symbol; the cells "were authored under the ML label and **LOSE money
  under the frozen label**" (`intents.py::_decision_vol_regime` docstring). Building on
  `vol_detector` would measure an opposite-behaving population — the exact
  population-mismatch bug the row exists to flag. Correct build = replay
  `btc-regime-15m-lgbm-fc-pcv-v1`, a trainer-relay job.

## Next Recommended Sprint

Build the offline ML vol axis per `BL-20260730-2D-VOL-CELLS-UNAUDITABLE` (trainer-relay),
then re-audit the six 2-D cells and revisit the withheld squeeze proposal.

**Outranking all research:** the bybit scalp exit leak is **unaddressed** — −$6,358 over
7 days, 28 of 37 closes exiting `reconciler_filled`. The dispatched fix was a no-op because
the memo's `BYBIT_TPSL_MODE` premise was false
(`BL-20260730-EXITCAPTURE-DEEPDIVE-WRONG-TPSL-PREMISE`). Live money.

## Wrap-Up Check

PRs #8027, #8043, #8050 merged; #8051 in flight; #8052 dispatched to land the committed
scorecards. Backlog rows filed. `doc-freshness` run — `canonical-doc-coherence` 4/4 PASS.
