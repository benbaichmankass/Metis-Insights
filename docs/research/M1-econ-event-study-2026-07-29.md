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

## Update 2026-08-01 — powered result (supersedes the n≈6 note above)

The **R2 EIA-bulk backfill** (keyless `api.eia.gov/bulk`; crude `PET.WCESTUS1.W`,
gas `NG.NW2_EPG0_SWO_R48_BCF.W`) replaced the shallow free-feed window with the full
weekly history, so the study crossed `--min-honest-n` and the verdict graduated on
its own — no code change, exactly as designed. Scorecard from
`econ-event-study-now` (issue #8280, run against
`comms/macro/econ_calendar_snapshots_backfill.jsonl`):

| kind | symbol | releases (n) | strongest IC | horizon | t | verdict |
|---|---|--:|--:|--:|--:|---|
| `eia_natgas_storage` | NG=F | 789 | **−0.1058** | **21d** | **−2.98** | `surprise_predicts_forward_return` (flagged) |
| `eia_crude_stocks` | CL=F | 2211 | +0.0216 | 5d | 0.72 | `no_edge_at_tested_horizons` |

The **natgas 21d IC is negative** — consistent with the pre-stated hypothesis (a
bigger-than-consensus storage BUILD is bearish → negative IC). Crude shows no edge
at any tested horizon.

**This is a candidate, NOT a confirmed tradable edge — three honest caveats
(state the population):**

1. **Multiple comparisons.** 5 horizons × 2 series = **10 IC tests** at a |t|≥2.0
   flag. Under the null ~0.5 spurious flags are expected, so a *single* flagged
   horizon is suggestive, not decisive.
2. **Overlapping-window autocorrelation inflates the t.** The flagged horizon is the
   **longest** tested (21 trading days) while natgas storage releases **weekly** —
   ~4–5 releases sit inside each 21-day forward window, so the forward returns are
   heavily overlapping and the effective independent-sample count is far below 789.
   A Newey–West / non-overlapping-horizon treatment would shrink |t| materially.
3. **Longest-horizon-only.** The signal appears only at 21d, not at 1/3/5/10d — the
   shape most vulnerable to (1)+(2), and the least useful for a short-hold sleeve.

**Disposition:** worth a proper **M2 event-response follow-up** — non-overlapping
horizons, HAC (Newey–West) t-stats, an OOS split, pre-registered thresholds — before
any position rule. Turning it into sizing/direction is **M2+/Tier-3**; nothing here
trades. It also **validates R2 end-to-end**: the EIA wiring now feeds a real, powered
study (the crude n jumped from ~6 to 2211).

## Update 2026-08-01 (later) — overlap correction lands; natgas flag does NOT survive it

Caveat #2 above is no longer just a footnote — `econ_event_study.py` now computes an
**overlap-corrected t** (`effective_n` → `n_eff = n / max(1, horizon/release_spacing_td)`,
`ic_t_eff = ic_t_stat(ic, n_eff)`) and the **verdict trusts the corrected t, not the raw
one**. This is the honest sibling of the raw rank-correlation t — an effective-sample
rule-of-thumb, explicitly *not* a rigorous Newey–West HAC on the rank statistic (a full
HAC remains the M2 refinement).

For natgas the release spacing is ~5 trading days (weekly) and the flagged horizon is
21 td, so `overlap_factor ≈ 21/5 ≈ 4.2` → `n_eff ≈ 789/4.2 ≈ 188` and the corrected
`ic_t_eff ≈ −1.45` (analytic estimate; the next runner recompute reports the exact
trading-day spacing). **|−1.45| < 2.0, so the flag does NOT survive overlap correction.**
The verdict for the natgas 21d shape therefore moves from `surprise_predicts_forward_return`
to the new **`flagged_overlap_uncorrected_only`** state: significant on the
autocorrelation-inflated raw t only. Combined with caveat #1 (this was 1 flag out of 10
tests), the honest read is: **natgas storage surprise → forward return is NOT an
established edge** — the 08-01 "powered result" was an artifact of overlapping
21-day windows on weekly releases, exactly the failure the caveat named.

**This RECONCILES an apparent contradiction.** The 08-01 flag came from the study run
against the **model-expectation backfill** (`econ_calendar_snapshots_backfill.jsonl` —
the FRED-backfilled seasonal-AR expectation, a *worse* predictor than survey per M3,
`BL-20260730-M3-GATE-TESTS-TRACKING-NOT-USEFULNESS`). But the earlier **2026-07-30 run
against the real survey consensus** (2015→2026, natgas 553 releases) already read
`no_edge_at_tested_horizons` with the strongest IC on the **wrong sign**
(`docs/sprint-logs/S-M1-SURVEY-CONSENSUS-NULL-20260730.md`; the M1 roadmap row's
"CONCLUDED — HONEST NEGATIVE"). The two runs looked like they disagreed (model-side
flagged vs survey-side null); the overlap correction shows they don't — the model-side
flag never cleared the overlap bar. **Do not re-litigate without a new hypothesis**
(different horizons, conditioning, or a non-surprise formulation), per that conclusion.

What this changes: the M2 event-response backtest is **no longer motivated by a live
lead** — it would be building on a corrected-away signal. The correction is the more
valuable deliverable than the signal was. `crude` was already `no_edge`; nothing else
flagged. The overlap correction now guards **every** future kind the study runs, so the
next weekly run can't re-publish an overlap-inflated flag. (Shipped with tests:
`test_m1_econ_event_study.py::test_summarize_downgrades_a_flag_that_survives_only_on_the_raw_t`
et al.; the committed scorecards regenerate with the `overlap_factor`/`n_eff`/`ic_t_eff`
fields on the next `econ-event-study` run.)

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
