---
name: macro-research
description: The repeatable pipeline for MACRO / value / event-study research — the ROADMAP_MACRO family (energy event calendars, surprise-vs-consensus, the M28 value sleeve, M29 system-dynamics, COT/crowding, crypto-funding). Turns a raw macro data source into a point-in-time store and an honest edge measurement, the same way `backtesting` / `exit-refinement` / `model-training` do for the technical side. Use when the operator says "work on the macro unit", "wire a macro data source", "run/extend the event study", "build an M28/M29 producer", "does <macro signal> predict returns", or any ROADMAP_MACRO milestone (M1–M2 energy events, M28 valuation, M29 system-dynamics, cross-asset). Owns the five binding invariants (off-VM compute, point-in-time / no-lookahead consensus, verify-the-source-before-you-build, establish-what-bounds-n-before-waiting-for-accrual, beat-the-naive-floor-not-just-the-correlation-bar) + the toolbox map (`src/units/strategies/macro_thesis/`, `scripts/macro/`, the macro workflow cluster, `comms/macro/` artifacts, `config/macro_*.yaml`). NOT for the technical trading strategies (use `new-strategy` / `backtesting` / `exit-refinement`) and NOT for ML model training (use `model-training` / `ml-review`) — this is the macro/value/event research half. Composes with `research-driver` (which dispatches here), `git-actions` (workflow dispatch), and `sprint-format` / `doc-freshness` (landing).
---

# macro-research — the point-in-time macro/value research pipeline

Codified 2026-07-29 (roadmap-toolbox assessment rec #6). Macro/value work
(ROADMAP_MACRO) had been running ad hoc under `research-driver` — it landed
coherently when a disciplined session followed good precedent, but the pattern
had to be rediscovered each time (exactly what `docs/CLAUDE-RULES-CANONICAL.md`'s
"precedents are not authoritative" warns against). This skill is the binding
reference a macro session inherits instead. The canonical plan lives in
[`ROADMAP_MACRO.md`](../../../ROADMAP_MACRO.md) — read its milestone ledger + §6
risk register first; this skill is *how* you execute a milestone, not *what* the
milestones are.

## When this skill applies (scope gate)

Use for any research whose subject is a **macro / value / event** signal:
energy event calendars + surprise-vs-consensus (M1/M2), the asset-class value
sleeve (M28), system-dynamics gas mispricing (M29), COT/crowding, crypto
funding/OI, cross-asset conditioning, VIX term structure, credit curves,
seasonality. NOT the ICT/technical strategies (→ `new-strategy` / `backtesting` /
`exit-refinement`), NOT ML model lifecycle (→ `model-training` / `ml-review`).
`research-driver` dispatches here once it recognizes a session is macro-shaped.

## The five binding invariants (violate none)

1. **Off-VM compute (ROADMAP_MACRO §1c).** All heavy fetch/compute runs OFF the
   live trading VM — a scheduled GitHub-runner workflow or the trainer VM — and
   writes point-in-time snapshots the live tick only *reads*, cadence-gated. Never
   add a macro fetch to the live pipeline tick. Every producer/harness script is
   **off-VM-guarded** (`ICT_OFFVM_BUILD_HOST` gate in `_resolve_fetchers`-style
   code) so it refuses to open a data socket on the money box.
2. **Point-in-time, no lookahead (§6 risk #1 — the classic event-study bug).**
   `surprise = actual − consensus` keys on the consensus **published before the
   release** — **never a revised figure**. Every row is stamped `observed_at`;
   the store is append-only (a revision is a new line, never an in-place edit); a
   later revision of the *prior* value goes to `previous_original` for reference
   only. If a source only offers revised/current consensus, **stop and re-scope
   the source** — the whole study is unsafe otherwise.
3. **Verify the source before you build.** Do NOT write a parser against an
   assumed schema. Probe the real endpoint on a runner (the sandbox can't reach
   external hosts; the proxy 403s), capture a real response fixture, then build
   the adapter against it. This is the hard lesson of the FMP-403 pivot
   (`docs/research/M1-econ-calendar-source-probe-2026-07-29.md`): FMP's free
   calendar 403'd, so an empirical probe across every free candidate picked
   FXStreet (keyless) instead. A 200 is not enough — capture the field shapes.

4. **Before waiting for n to accrue, establish what BOUNDS it** (added 2026-07-30,
   binding — `CLAUDE-RULES-CANONICAL.md` § "Green is not evidence", obligation 5).
   "Not enough rows yet, check back in N weeks" is a claim about the *source*, and it
   needs verifying like any other. Ask whether the producer is forward-only **by
   nature** or only **by schedule** — a source that takes an arbitrary date range has
   no accrual limit, it has a **missing backfill sibling**.

   This fired twice in one day on both sides of the same join: the model side read
   n=7 ("no verdict until mid-September") against a FRED history of 75 years →
   **6,966 rows**; the survey side read n=11, one short of `min_honest_n=12`, purely
   because the forward producer had ever pulled ONE window from a range-based keyless
   API → **12,076 rows / 1,263 joinable**, 115× the supposed ceiling.

   **Never lower a pre-registered bar or an honesty floor to manufacture a verdict.**
   Raise the sample or report `insufficient_*` and say what would raise it. At n=11
   the M3 correlation read 0.7364/0.909; at n=1263 it read 0.5885/0.720 — the small
   sample was *optimistic*, so lowering the floor would have published an inflated
   estimate. Existing backfill siblings to copy: `macro-valuation-backfill`,
   `cot-positioning-backfill`, `crypto-signals-backfill`,
   `econ-calendar-backfill` (model side), `econ-calendar-survey-backfill` (survey side).

5. **Beat the naive floor, not just the correlation bar** (added 2026-07-30).
   An expectation model that tracks a survey can still be *worse than no model*. Check
   it against "assume no change since last release" on the same information set — M3
   does this per kind (`rmse_ratio_model_over_naive`, `kinds_worse_than_naive`), and
   found `initial_jobless_claims` to be the **best tracker** (Spearman 0.6312) **and**
   1.101× worse than the naive walk. Tracking and usefulness are different axes;
   a correlation bar tests only the first
   (`BL-20260730-M3-GATE-TESTS-TRACKING-NOT-USEFULNESS`).

## The two-source join contract (added 2026-07-30 — learned the hard way)

Macro work constantly compares **two independently-sourced views of the same release**
(a backfilled model expectation vs a captured survey consensus; a reconstructed history vs
a forward producer). Before comparing them, verify all three of these. Skipping any one
produced a real failure on 2026-07-30 and each failure looked like a plausible result
rather than an error.

1. **Date basis — what does the date field MEAN in each source?**
   Keyless FRED dates an observation by its **reference period**; a calendar feed dates it
   by the **release date**. CPI for reference `2026-06-01` publishes ~`07-15`. Getting this
   wrong gave a **zero-row join** *and* made the event study measure forward returns over a
   window that mostly **precedes** the release. Emit both (`reference_period` +
   `scheduled_for`) and stamp the basis (`release_date_basis: modeled_lag`) so a modeled
   date can never be read as an observed one.
   **A fixed lag only works for a fixed-weekday series.** Weekly claims/EIA land exactly;
   the BLS CPI release drifts (~10th–15th), so monthly joins need a **tolerance window** +
   a reported offset distribution, or a partial join reads as a small sample rather than a
   systematic miss (`BL-20260730-MONTHLY-RELEASE-DATE-DRIFT`).

2. **Units — same quantity, or merely a similar-looking number?**
   FRED served claims as persons (`187000`) against a survey convention of thousands
   (`187.0`), and `cpi_yoy` as the CPI **index level** (`332.568`) against YoY percent
   (`3.5`). The second is not a scale error, it is **the wrong quantity under the right
   name**. Note a scale **cancels in a correlation but not in a slope or a percent**, so it
   cannot be waved off. Declare a per-kind `transform`, apply it **before** fitting any
   expectation (fitting a level and converting after forecasts the wrong series), and make
   an unknown transform **raise** — a silent pass-through emits plausible numbers in the
   wrong units.

3. **Vintage basis** — already invariant 2 above; keyless FRED = current vintage, never
   first prints.

**Then prove the join is non-empty before you consume it.** A joined dataset of zero rows
computes a verdict just as happily as a real one.

## Verify offline before you spend a runner

When a check can be run against **already-committed** data, run it locally first. On
2026-07-30 a local join test against the committed forward feed caught a wrong CPI lag
(45d, off by 2 days) *before* a runner was spent — and would have caught it even if the run
had come back green, because a green run with a partial join looks like thin data.

Corollary for the sandbox: **FRED/most data hosts are firewalled here** (`fredgraph.csv`
and `WebFetch` both fail). So "is this series id real?" is **not answerable locally** — do
not settle it by guessing an id into config, which is how two EIA `dnav` codes ended up in a
FRED-series config and 404'd for the producer's life. Use a **runner-side probe** that
reports and writes nothing (`econ_calendar_snapshot_backfill.py --probe-ids`), and make it
distinguish **404** (wrong id/source) from **200 + empty** (right id, data question) —
those need different fixes and the shared adapter collapses both into one message.
A probe must never try candidates and adopt whichever resolves: that backfills from
whatever happened to work.

## The repeatable pipeline (data → PIT store → honest edge)

Mirror the existing producers; don't invent a new shape.

1. **Source adapter (pure + injectable).** A `scripts/macro/<x>_data.py`-style
   module with pure parsers that normalize the raw feed to the shared shape, plus
   an off-VM fetch entrypoint whose network fn is injectable for tests. Keep the
   **source-agnostic boundary**: multiple feeds normalize to one intermediate
   shape, then one mapper produces the store schema (e.g. `econ_calendar_data.
   to_event_rows` maps FXStreet/FMP/Bigdata captures alike into `macro_events`
   PIT rows).
2. **Point-in-time store.** Append-only JSONL under `comms/macro/` (e.g.
   `econ_calendar_snapshots.jsonl`, `valuation_snapshots.jsonl`,
   `cot_snapshots.jsonl`), every row `observed_at`-stamped. A full-regen from
   committed captures must be **idempotent** (same captures → same log).
3. **Measurement harness → scorecard.** Reuse the P4 machinery — `thesis_replay.
   build_replay_entries` + `thesis_backtest.run_thesis_backtest` (calibration
   rank, conviction spread), `horizon_ic_scan` (IC-by-horizon + `ic_t_stat`),
   `thesis_backtest_run` (`load_close_panels` / `make_price_at`), and
   `econ_event_study` (surprise → forward-return IC). Prices come from the
   existing off-VM `fetch_macro_candles` (yfinance→Stooq, keyless). Write a
   `comms/macro/<x>_scorecard.json`.
4. **Honest verdict.** Report the real edge state — including **small-n / no-edge
   / park** as first-class outcomes, never dressed up. Cap a verdict at
   `insufficient_history` while n is thin (free feeds start shallow; the verdict
   self-graduates as history accrues). Right-censored observations are excluded
   from n, never zero-filled. An honest-negative (`park_deeper_investment`) is a
   result, not a failure — it saves the M2/execution build from wiring a null.
5. **Land it (off-VM workflow).** A `.github/workflows/<x>.yml` on a schedule +
   `workflow_dispatch` + issue-label (owner-gated), which fetches off-VM,
   regenerates, and lands via `./.github/actions/commit-to-main` (PAT auto-merge
   PR — same as the `macro-valuation-*` / `econ-calendar-produce` /
   `econ-event-study` cluster). Register the label in `bootstrap-labels.yml`.

## Toolbox map (what already exists — reuse, don't rebuild)

- **Engine (`src/units/strategies/macro_thesis/`)** — `event_calendar` (build/
  resolve scheduled events, surprise), `event_store` (append-only PIT events),
  `event_resolver` (predicate DSL), `thesis_replay` (PIT no-lookahead replay),
  `thesis_backtest` (calibration rank + conviction spread + equity/maxDD),
  `valuation_feed` / `valuation` / `valuation_store` (M28 value core),
  `fred_adapter` (free FRED series), `thesis_tick` / `thesis_engine` (the
  read-only live tick). **Purity is locked by import-linter**: `macro_thesis`
  (and `src/sysdyn`) must NEVER import Execution / a broker / the order path —
  keep new modules on the pure side of that contract.
- **Producers / harnesses (`scripts/macro/`)** — calendar: `econ_calendar_{data,
  produce,fxstreet,fmp}.py`, **`econ_calendar_snapshot_backfill.py`** (the PIT
  release-history backfill sibling; `--probe-ids` is its runner-side id diagnostic),
  **`econ_expectation.py`** (the pinned `seasonal_ar_ols_v1` PIT expectation model),
  **`econ_expectation_validate.py`** (**M3** — the model-vs-survey overlap validation the
  M1 gate names as its own satisfiability condition), `econ_event_study.py`; value: `valuation_snapshot_
  {produce,backfill}.py`, `value_construction_sweep.py`; system-dynamics:
  `sysdyn_gas_{data,calibrate}.py`, `sysdyn_mispricing.py`; positioning/crypto:
  `cot_data.py`, `crypto_signals_data.py`; shared: `fetch_macro_candles.py`,
  `thesis_backtest_run.py`, `horizon_ic_scan.py`, `macro_sources.py`, the `*_probe.py`
  source validators.
- **Workflows** — `macro-valuation-snapshot`, `macro-valuation-backfill`,
  `econ-calendar-produce`, `econ-event-study`, `sysdyn-gas-calibrate`,
  `macro-producer-liveness` (staleness alarm — `check_producer_liveness.py`).
- **Configs** — `config/macro_valuation.yaml` (M28 seed universe → value
  metrics), `config/macro_theses.yaml`, `config/macro_events.yaml`,
  `config/economic_calendar.yaml` (**live-path** — read by the news-influence
  layer; a cadence must NEVER auto-emit it; population is operator-gated Tier-3),
  `config/cross_asset.yaml`, `config/instruments.yaml`.
- **Landed artifacts (`comms/macro/`)** — the PIT snapshots + scorecards + the
  captures dirs. These are the deliverables; the live tick + the dashboard read them.
- **Design docs (`docs/research/`)** — `M1-*`, `M28-*`, `M29-*`. Every non-trivial
  build gets one.

## Tiers & landing

- **Tier-1, observe-only** is the default and covers almost all macro-research:
  reads + off-VM compute + scorecards + docs → commit to `main` (via the auto-merge
  workflow). No order path, no live-VM mutation.
- **Tier-3** is any change that could *influence a live order* — populating
  `config/economic_calendar.yaml`, graduating a `c_macro` conviction overlay into
  sizing, wiring the P5 macro order path (ROADMAP_MACRO M2/M3/M5). Propose the
  exact change; the operator approves before merge. The measurement (this skill)
  is what *earns* that proposal — never wire execution for a null.
- **Land the decision durably** (composes with `doc-freshness` / `sprint-format`):
  update the ROADMAP_MACRO milestone row + a `## Change log` entry, and write/append
  the `docs/research/` design note. A macro session that doesn't update
  ROADMAP_MACRO has left a loose end.

## What to report

The edge state, honestly: the milestone + gate, what the scorecard says (IC /
calibration / conviction spread + n + t-stat, with the small-n / overlap caveats
stated not hidden), the verdict (`edge` / `no_edge` / `insufficient_history` /
`park`), what it unblocks or blocks next (e.g. M2 waits on M1 data accrual), and
where it landed (PR + ROADMAP_MACRO + doc). If a data source failed the
verify-before-build probe, report *that* — it's the most valuable finding.

---

## Definition of done — a capability is not shipped until something RUNS it

*(Operator directive 2026-08-20, binding on every build skill: "we don't keep
building things out half way and then leaving them to rust while the system
chugs along with bad structure.")*

Merging is not shipping. Before you call any capability from this skill done,
all four must hold — and the ones you cannot satisfy get **said out loud**, not
left implied:

1. **A RUNNER exists.** A workflow, a systemd unit, a call site in `src/`, an
   entry in `run_guards.py`, or a documented cadence. A tool that is genuinely
   manual-only declares it in its own file:
   `# wiring: manual-only — <who runs it, when>`. Verify with
   **`python3 scripts/ci/check_unwired_artifacts.py`** — if your new file
   appears in its output, it is not done.
2. **A CONSUMER exists.** Anything the capability *writes* must be *read* by
   something that acts on it. A signal written and never read is worse than a
   missing one — reviewers see the field and assume something acts on it
   (`provenance-consumer-guard` exists for exactly this).
3. **A DETECTOR exists.** Something fails if this silently stops working. A
   test, a guard, an alert, or an invariant in
   `scripts/ops/system_invariants.py`. "We'll notice" is not a detector.
4. **It has been OBSERVED working on real data** — not only in a test. Cite the
   evidence (a diag pull, a log line, a row) or state plainly that it has not
   yet been observed and what would settle it.
5. **The LIVE environment matches the repo's declaration.** If your change adds
   or depends on an env var, a service, a timer, a path or a routing entry,
   **read it back from the VM** (`get-env`, `/api/diag/services`,
   `/api/bot/config`, the relay) and confirm the running value is the declared
   one. *"The repo says X"* is not evidence that the VM does X — the two drift,
   and this repo has the scars: a `FLIP_CONFIDENCE_THRESHOLD` running live for a
   day with no record behind it, a `DIAG_BASE_URL` still pointing at a VM
   terminated 2026-06-16 while the doc-coherence guard passed (it checks the
   docs, not the environment), and a `BYBIT_TPSL_MODE` "flip" that was a no-op
   re-assertion of a value already live.
6. **The change is CONCENTRATED.** Count the files you had to touch. If a
   *routine* addition of this kind cost more than the source-of-truth files plus
   tests and docs, say so — every hand-maintained registry you had to update in
   lockstep is a place the next person half-applies the change. Measured
   2026-08-20: wiring one strategy leg touched **17 files**, of which three were
   `src/` maps holding facts `strategies.yaml` already contains. **A file you
   edited only to keep a derived map in sync is a design finding, not a chore** —
   record it (audit skill § 3.7 MODULARITY) even when you cannot fix it here.

**The measured cost of skipping this:** 161 of 384 tools under `scripts/` have
no runner (2026-08-20). `scripts/ops/trainer_dataset_gc.py` — the retention
tool for a 12 G dataset tree — had no caller, no timer and **0 mentions across
7,442 cycle-log rows** while the disk it was written for reached **93 %**.
`exchange_fills_ib.closed_pnl_from_fills` has **zero production callers**, so
IBKR's own realized PnL is pulled hourly and never read. Every one was found by
accident, months later.

`/system-review` now enumerates everything shipped since the previous review and
grades each `running` / `wired_not_yet_exercised` / **`UNWIRED`** /
`unverifiable` (`review_coverage.since_last_build_verification`, enforced by
`render_system_report.py --strict`). **Your work will be graded against this
list.** Leave it wired, or leave it declared.
