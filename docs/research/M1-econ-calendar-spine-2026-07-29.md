# ROADMAP_MACRO M1 — economic-calendar + surprise-vs-consensus DATA SPINE

**Status:** built 2026-07-29 (Tier-1, observe-only, data-only). Recommendation #1
of [`roadmap-toolbox-assessment-2026-07-29.md`](./roadmap-toolbox-assessment-2026-07-29.md).
Turns the macro-event calendar from **inert** (`config/economic_calendar.yaml →
events: []`, all macro data FRED-only) into a **point-in-time economic-event
spine** the existing M28 macro engine reads — the calendar/consensus/surprise
half of M1's "clean joined dataset" gate. The MNG price join + the M2
event-response backtest are the follow-ons; the P5 order path stays unwired.

## What was built

| Piece | Path | Role |
|---|---|---|
| Pure parser + PIT mapper | `scripts/macro/econ_calendar_data.py` | Bigdata.com country-tearsheet markdown → structured events → `macro_events`-schema PIT rows (`event_id`/`scheduled_for`/`realized_outcome{actual,consensus,surprise,…}`/`observed_at`). Offline, fully tested. |
| Producer runner | `scripts/macro/econ_calendar_produce.py` | Committed captures → full-regen `econ_calendar_snapshots.jsonl` (append-only PIT log) + `econ_calendar_upcoming.json` (forward calendar) + `[--emit-config]` `economic_calendar.yaml`. Idempotent. |
| Deterministic land workflow | `.github/workflows/econ-calendar-produce.yml` | Re-parse-and-land the PIT log from committed captures (PAT auto-merge PR). Not scheduled, never `--emit-config`. |
| Tests | `tests/test_m1_econ_calendar_{data,produce}.py` | 20 tests — parsing quirks, the canonical EIA case, PIT stamping, idempotent regen, config gating. |
| Real seed | `comms/macro/econ_calendar_captures/US-20260729T063800Z.md` → `comms/macro/econ_calendar_snapshots.jsonl` | 110 PIT rows (40 scheduled + 70 resolved, 54 with a computed surprise) from a real US tearsheet, incl. the ROADMAP_MACRO canonical case. |

## Data source

Bigdata.com's **`bigdata_country_tearsheet`** MCP tool (`content-economic-calendar`
access, PAYG) returns — in one call — a forward **Economic Calendar - Upcoming
Events** table (date, event, impact, **consensus**) and a **Macroeconomic
Overview** of already-printed releases per sector (**Actual / Consensus / Previous
/ Surprise**), sourced by Bigdata from FXStreet. The canonical M1 test case
verified end-to-end:

```
EIA Natural Gas Storage Change (2026-07-23): actual 32 · consensus 29
  → surprise = +3.0 (raw) / +10.3% (vendor)   ✓ matches ROADMAP_MACRO §3
```

## The compute + point-in-time invariants (how they're honored)

- **Compute invariant (§1c).** The Bigdata MCP is **session-bound** — a GitHub
  runner can't call it (unlike the keyless-HTTP feeds). So the design splits
  **fetch** (a Claude session calls the MCP, writes a raw capture) from
  **parse → PIT-map → land** (this pure, committed, CI-reproducible script +
  workflow). The live tick only ever *reads* the pre-computed
  `econ_calendar_snapshots.jsonl`; it never fetches or parses on the money box.
- **Point-in-time consensus — the #1 correctness rule (§6, "never use revised
  consensus").** `surprise = actual − consensus`, and the consensus is FXStreet's
  **pre-release forecast** — the consensus column is never revised (only
  *Previous*/prior is, shown as `(Original X)`, which we preserve verbatim but do
  **not** key the surprise on). Every row carries the fetch instant `observed_at`;
  a revision is a NEW line, never an overwrite (append-only, mirrors
  `valuation_store`/`thesis_replay.as_of_snapshot_rows`).
- **Honest-null.** A missing cell (`–`/`—`/blank) parses to `None`, never a
  fabricated 0; a non-numeric actual is preserved verbatim with a `None` surprise.

## Operating model — how the spine accrues going forward

1. **Fetch (Claude session).** Call `bigdata_country_tearsheet(country)`, write the
   markdown to `comms/macro/econ_calendar_captures/<COUNTRY>-<ISO8601Z>.md` (with a
   `<!-- country: … observed_at: … -->` header), run
   `scripts/macro/econ_calendar_produce.py`, commit. This session did one real US run.
2. **Recurring (PROPOSED — operator-gated).** A **scheduled Claude session** (a
   Routine / `create_trigger` with a fresh session per fire) is the honest
   recurring "off-VM producer" given the MCP is session-bound. It re-runs step 1
   on a cadence (daily/weekly is plenty for a weeks-horizon event study). Not
   auto-created here — a recurring autonomous repo-committing job + PAYG spend is
   an operating-model decision. **$-budget:** each tearsheet call is a small PAYG
   charge (Bigdata balance was ~$987 at build time); one call/country/day is
   negligible, but it must stay observable — surface it alongside `insights_usage`
   when the cadence is turned on.
3. **Re-land / verify (`econ-calendar-produce` workflow).** Regenerates the PIT log
   from committed captures deterministically — the CI-reproducible, non-MCP path.

## Live-path change requiring operator approval (Tier-3, in this draft PR)

`config/economic_calendar.yaml` is read by the **news-influence layer**
(`src/news/news_events.py`, `event_risk`). The producer's `--emit-config` populated
its `events:` with the forward high/medium-impact events mapped to news classes
(fomc/pce/gdp in the current forward window). It parses cleanly through the live
loader. **This is the one live-path-adjacent change** — the whole PR is a draft; the
config population needs operator sign-off before merge (the news layer only *acts*
on it when `NEWS_INFLUENCE_MODE`/`NEWS_VETO` are active, but it is still a live-path
config). The scheduled producer deliberately does **not** emit config, so a cadence
never silently mutates it.

## Follow-ons (not in this spine)

- **MNG price join** (M1 gate's other half) — join the EIA-storage surprise history
  to the MNG/NG price series via the existing candle fetchers.
- **M2 event-response backtest** — surprise → forward returns at several horizons
  through `thesis_backtest` calibration + the cost model (pre-registered thresholds).
- **Non-US coverage** — the producer is multi-country (tested); add EMU/DE captures
  for the eventual EU-ETS carbon work (§ M4).
- **Curve / VIX / CFTC** — the same tearsheet also carries the Treasury curve, VIX,
  and CFTC positioning; a natural additive context block, deferred to keep v1 scoped
  to the calendar.
- **`direction` orientation** — left `None` in `realized_outcome` (a thesis-side
  concern the `event_resolver` DSL + `TradeThesis.on_outcome` rules own).
