# ROADMAP_MACRO M1 — econ-surprise → forward-price event study (2026-07-29)

**What this is:** the **join + measurement** half of M1's clean-joined-dataset
gate. The calendar/consensus/surprise half is built (`econ_calendar_produce.py` →
`comms/macro/econ_calendar_snapshots.jsonl`, PIT, append-only). This adds the
instrument that *joins* those resolved releases to the traded price series and
measures the thing M2 needs to know: **does the surprise-vs-consensus predict the
forward price move, and at which horizon?**

`scripts/macro/econ_event_study.py` reads the resolved rows for one event `kind`
(default `eia_natgas_storage`), pairs each release with a daily-close panel (NG=F
for gas via the existing `fetch_macro_candles` off-VM fetcher), and reports the
**information coefficient IC(H) = Spearman(surprise, forward return)** across a
range of forward **trading-day** horizons (default `1,3,5,10,21`), plus a Pearson
and a directional sign-hit-rate. It writes
`comms/macro/econ_event_study_scorecard.json`.

## Design decisions (and why)

- **Reuses the existing machinery, doesn't reinvent it.** The price reader is the
  same per-symbol `<SYMBOL>.csv` convention + `load_close_panels` the M28 value
  sleeve uses; the IC t-stat is `horizon_ic_scan.ic_t_stat`; the off-VM price fetch
  is `fetch_macro_candles.symbol_close_pairs`. The harness is pure + injectable
  (panel passed in) exactly like the sibling scripts, so it's unit-testable with no
  network.
- **PIT-safe by construction.** It reads only the `realized_outcome.surprise` that
  the producer already computed on the **never-revised** consensus, keyed on
  `scheduled_for`. Dedup prefers the row with a defined surprise and, on a tie, the
  **earliest** `observed_at` (the first point-in-time read — never a later revised
  consensus). No lookahead: the base price is the close at/before the release, the
  forward is a strictly-later bar.
- **Right-censoring is honest.** A release whose forward bar `H` trading days out
  isn't in the panel yet is **excluded** from that horizon's n — never zero-filled.
- **Honest small-n verdict.** The free calendar feeds start with a shallow
  PIT-consensus window (today: ~6 resolved NG-storage releases). At that n an IC is
  a *lead*, not a result, so `summarize` caps the top-line verdict at
  `insufficient_history` until `max_n` exceeds `--min-honest-n` (default 12). The
  flagged horizon + strongest IC are still reported so the lead is visible. Consensus
  depth accrues going forward as the daily producer runs, so the verdict graduates
  itself over weeks with no code change.
- **Measurement, not a position.** For an inventory BUILD print (EIA storage) a
  bigger-than-consensus surprise is bearish → the *hypothesis* is a **negative** IC.
  The harness reports the raw number and states the expected sign; it asserts no
  direction and takes no trade. Turning a confirmed IC into a sizing/position rule is
  M2+ and Tier-3.

## Observed today (n≈6, provisional)

Against the merged snapshots + a real NG=F fetch the scorecard will show the natgas
storage surprise IC by horizon with `verdict: insufficient_history`. That is the
**correct** state of the world right now — the join works, the price series is
present, and the sample is honestly too thin to conclude. This is the artifact M2
will re-read once history accrues.

## Operating model

`.github/workflows/econ-event-study.yml` runs **weekly** (Sun 23:10 UTC) +
`workflow_dispatch` + issue-label `econ-event-study-now`: fetches NG=F/CL=F off-VM
(keyless yfinance→Stooq), runs the study for natural gas + crude, and lands the
scorecards via the PAT auto-merge PR (same as `econ-calendar-produce` / the
macro-*-backfill cluster). Weekly (not daily) because the study only moves as new
releases land. **Tier-1, observe-only** — no order path, no live-VM mutation; the
price fetch is on the hosted runner.

## Follow-ons

- **Multi-year accrual** — the one genuine constraint. NG price + storage *actuals*
  are available historically; **point-in-time consensus** depth is what the free
  feeds lack. As it accrues the verdict graduates from `insufficient_history`
  automatically. A deeper PIT-consensus source (paid, or a historical archive) would
  jump-start it.
- **M2 event-response backtest** — once an IC is confirmed at honest n, wire the
  surprise → forward-return relation through `thesis_backtest` calibration + the cost
  model with pre-registered thresholds (ROADMAP_MACRO M2).
- **More kinds** — the `KIND_DEFAULT_SYMBOL` map already covers crude/gasoline/CPI/NFP/
  FOMC; each is one line + a price symbol. The workflow already runs crude alongside gas.
