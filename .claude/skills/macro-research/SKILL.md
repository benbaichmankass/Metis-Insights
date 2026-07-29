---
name: macro-research
description: The repeatable pipeline for MACRO / value / event-study research — the ROADMAP_MACRO family (energy event calendars, surprise-vs-consensus, the M28 value sleeve, M29 system-dynamics, COT/crowding, crypto-funding). Turns a raw macro data source into a point-in-time store and an honest edge measurement, the same way `backtesting` / `exit-refinement` / `model-training` do for the technical side. Use when the operator says "work on the macro unit", "wire a macro data source", "run/extend the event study", "build an M28/M29 producer", "does <macro signal> predict returns", or any ROADMAP_MACRO milestone (M1–M2 energy events, M28 valuation, M29 system-dynamics, cross-asset). Owns the binding invariants (off-VM compute, point-in-time / no-lookahead consensus, verify-the-source-before-you-build) + the toolbox map (`src/units/strategies/macro_thesis/`, `scripts/macro/`, the macro workflow cluster, `comms/macro/` artifacts, `config/macro_*.yaml`). NOT for the technical trading strategies (use `new-strategy` / `backtesting` / `exit-refinement`) and NOT for ML model training (use `model-training` / `ml-review`) — this is the macro/value/event research half. Composes with `research-driver` (which dispatches here), `git-actions` (workflow dispatch), and `sprint-format` / `doc-freshness` (landing).
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

## The three binding invariants (violate none)

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
  produce,fxstreet,fmp}.py`, `econ_event_study.py`; value: `valuation_snapshot_
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
