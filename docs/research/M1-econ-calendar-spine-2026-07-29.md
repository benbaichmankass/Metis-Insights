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

## Data sources — two feeds, one pipeline

The parser/PIT-mapper/store/tests are **source-agnostic** at the `to_event_rows`
boundary, so two sources produce the *same* normalized event dicts:

1. **FMP economic calendar — the AUTONOMOUS feed (primary going forward).**
   Financial Modeling Prep's `/api/v3/economic_calendar` (free tier + a free
   `FMP_API_KEY`), fetched **directly on a GitHub-hosted runner over plain HTTPS**
   — no session dependency, exactly like the FRED valuation producer. Fields:
   date · country · event · previous · **estimate (consensus)** · actual · impact.
   `scripts/macro/econ_calendar_fmp.py` (off-VM-guarded fetch + pure `normalize_fmp`
   + a `.fmp.json` capture writer). **This is what the daily schedule runs.**
2. **Bigdata.com `bigdata_country_tearsheet` MCP — a richer cross-check.** Returns
   the forward calendar + a per-sector Macroeconomic Overview (Actual/Consensus/
   Previous/**Surprise**) sourced from FXStreet, plus the Treasury curve / VIX /
   CFTC positioning. It is **session-bound** (a runner can't call it), so it's a
   Claude-session-dropped `.md` capture, not the load-bearing autonomous feed
   (Bigdata is PAYG, not free). The producer parses both capture formats identically.

The canonical M1 test case verified end-to-end (from the real Bigdata seed;
FMP names the same release "EIA Natural Gas Stocks Change" → same `eia_natgas_storage` kind):

```
EIA Natural Gas Storage Change (2026-07-23): actual 32 · consensus 29
  → surprise = +3.0 (raw) / +10.3% (vendor)   ✓ matches ROADMAP_MACRO §3
```

**Why FMP replaced the scheduled-Claude-session plan:** relying on a Routine to
pull the data was the design's weakest link (session-bound, autonomous-commit +
PAYG). A free keyed HTTP source makes `econ-calendar-produce.yml` a normal
scheduled workflow — `FMP_API_KEY` (free, an Actions secret used **only on the
runner**, never on the live VM). Safe to merge before the key exists: the FMP fetch
step is gated on the secret, so a keyless run just regenerates from existing
captures. One open verification: FMP's free-tier gating of the calendar endpoint
is confirmed by the workflow's first live run (the sandbox has no FMP egress).

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

1. **Autonomous daily (`econ-calendar-produce.yml`, `schedule: 22:30 UTC`).** The
   workflow fetches the FMP calendar on the runner (`econ_calendar_fmp.py`, gated on
   `FMP_API_KEY`), writes a `.fmp.json` capture, regenerates the PIT log from ALL
   committed captures, and lands it via the PAT auto-merge PR. **No Claude session,
   no live-VM touch, no PAYG.** This is the load-bearing feed.
2. **Optional cross-check (Claude session, ad hoc).** Call `bigdata_country_tearsheet`,
   write a `.md` capture, run the producer — for the richer curve/VIX/CFTC context or
   to reconcile FMP vs FXStreet consensus. Not required for accrual.
3. **Re-land / verify.** The same workflow (dispatch / `econ-calendar-produce-now`
   label) regenerates deterministically from committed captures.

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
