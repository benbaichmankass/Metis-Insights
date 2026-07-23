# M29 P1c — the fair test: dual-target gas calibration (observed storage + real weather)

**Status:** built 2026-07-23 · observe-only research · Tier-1 (no live influence).

P1b calibrated the `gas_storage_price_v1` seed against the keyless real **price**
with a *calendar-seasonal* demand proxy and got `equifinal_no_edge` — OOS price
R² ≈ 0, structural params not identifiable from price alone. Its own writeup named
the lever: **"price + a calendar season is too thin a signal for this structure.
The lever that matters is the *real* data the seed was designed around — observed
storage + weather."** **P1c is that fair test** — it injects the two real series
the seed's structure was actually built around, so the mechanistic model finally
has the inputs to prove (or disprove) itself against.

## What changed from P1b

Two real data injections + a joint objective, everything else reused:

1. **Observed EIA weekly working-gas-in-storage as the anchor 2nd calibration
   target.** In P1b the stock (`storage`) floated freely from an arbitrary
   `initial=2000` and was never checked against reality. P1c pulls the real weekly
   Lower-48 working-gas series (EIA v2 API, `NG.NW2_EPG0_SWO_R48_BCF.W`) and:
   - anchors `initial_storage` = the **first observed** storage value,
   - sets `storage_normal` = the **mean observed** storage (the price-gap
     reference level), and
   - fits against the **real stock trajectory** (`seed_gas.storage_series`
     predictor), so injection/withdrawal rates are identified from the actual
     Bcf path, not left to float.
   EIA's weekly feed is key-gated (FRED carries only monthly) — the free
   `EIA_API_KEY` Actions secret (the operator hand-off P1b flagged) unblocks it.

2. **Real weather HDD as the `heating_demand` driver, replacing the calendar
   proxy.** The demand driver is now national **heating degree days** computed
   from Open-Meteo's keyless historical daily-temperature archive over a
   gas-heating-demand city basket (Chicago/NYC/Boston/Minneapolis/… weight-
   averaged), `HDD = max(0, 65°F − mean_temp)`, aggregated to the weekly storage
   clock. This is what carries the **cold-snap *surprise*** the B1 balancing
   loop's research edge lives in — exactly what a smooth calendar cosine could
   never reach. (`injection_season` stays calendar: there is no clean observed
   injection-intent series, and only the demand side needs the weather upgrade.)

3. **A joint (dual-target) fit.** `identify` gained a backward-compatible `steps`
   override so a **stacked, mean-normalised** predictor can fit storage **and**
   price residuals together (each series divided by its own train mean so neither
   unit dominates the SSE). The four structural params + `base_price` are fit on
   the train weeks; both targets are then scored in/out-of-sample on one full
   simulation. `src/sysdyn/*` stays import-linter-pure (6 contracts kept); all IO
   is in `scripts/macro/`.

## The scorecard + the decision gate

`scripts/macro/sysdyn_gas_calibrate.py --mode dual` →
`comms/macro/sysdyn_gas_dual_scorecard.json`, reporting:

- **storage fit** — in/OOS RMSE + R² against observed storage (the anchor target).
- **price readout fit** — in/OOS RMSE + R² of the model's price observation (the
  **tradeable** quantity — this is what P1b measured at ~0).
- **identifiability** — `walk_forward_stability` on the storage target: how far the
  structural params move fold-to-fold.
- **verdict + `go_no_go`.**

**The decisive go/no-go turns on the price readout, not the storage fit.** A model
can fit the stock trajectory well and still have no tradeable edge, so:

| condition | label | go/no-go |
|---|---|---|
| price readout predicts OOS **and** identifiable | `mechanistic_edge` | **`invest_deeper`** |
| storage fits OOS but price doesn't | `storage_fits_no_price_edge` | `park_deeper_investment` |
| price predicts OOS but equifinal | `price_edge_but_equifinal` | `park_deeper_investment` |
| neither | `no_mechanistic_edge` | `park_deeper_investment` |

`invest_deeper` requires the storage-anchored + weather-driven model's **price
readout to beat P1b's ~0 OOS AND** the structure to be identifiable — i.e. the
mechanism adds signal a static M28 value read doesn't. Anything else parks deeper
M29 investment: the mechanistic model does not beat static, and the next lever is
elsewhere (this is the same "signals too thin → richer inputs" thread the value-P4
and gas-P1b null results already trace).

## Why this is the fair test (and its honest limits)

P1b could only reach the *seasonal* component of price. P1c gives the seed its
designed inputs — the real stock it balances and the real weather that draws it
down — so a null result here is a **real** verdict on the mechanistic thesis, not
an artifact of a starved proxy. Honest limits, stated not hidden:

- The joint fit can be **equifinal** (a `wd_rate`/`price_feedback` trade-off gives
  a near-equal fit) — which is *why* identifiability is a first-class verdict, not
  an afterthought. The synthetic round-trip test reproduces both targets OOS but
  deliberately does **not** assert exact param recovery, mirroring the P1b stance.
- `injection_season` is still calendar (only demand got the weather upgrade).
- FRED price + EIA storage are latest-revision reads; NG price is an unrevised
  market rate → PIT-clean, and storage revisions are small vs the signal.

## Files

- `scripts/macro/sysdyn_gas_data.py` — EIA v2 storage reader (`fetch_eia_storage_dated`
  / `parse_eia_v2_series`), Open-Meteo weather-HDD readers (`national_daily_hdd` /
  `parse_open_meteo_daily` / `daily_hdd_from_temp` / `weekly_hdd_ending`), and the
  point-in-time `build_dual_calibration_series` (all off-VM-guarded + injectable).
- `scripts/macro/sysdyn_gas_calibrate.py` — `run_dual_calibration` + `--mode dual`.
- `src/sysdyn/identify.py` — the pure `steps` override enabling the stacked joint fit.
- `.github/workflows/sysdyn-gas-calibrate.yml` — runs P1b + P1c, lands both
  scorecards on `main` via the shared `commit-to-main` PAT auto-merge action.
- `tests/test_m29_sysdyn_gas_p1c.py` — parser/aggregator unit tests + the synthetic
  dual round-trip.

The real numbers (and the go/no-go call on deeper M29 investment) come from the
workflow run against the live EIA + Open-Meteo + FRED data.
