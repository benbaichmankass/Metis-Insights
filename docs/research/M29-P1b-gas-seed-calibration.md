# M29 P1b — calibrating the `gas_storage_price_v1` seed on real data

**Status:** built 2026-07-23 · observe-only research · Tier-1 (no live influence).

M29 P1a shipped the pure system-dynamics engine (`src/sysdyn/engine.py`), the
system-identification harness (`identify.py` + `walk_forward_stability`), the
legible causal-structure descriptor (`structure.py`), and the seed model
(`seed_gas.py::gas_storage_price_v1` — EIA weekly NG-storage → MNG price with the
B1 balancing loop). Everything there is pure + injected-input; the seed docstring
flags the remaining P1 step as *"real runs inject point-in-time EIA/weather
history."* **P1b is that step** — the calibrate-on-real-data harness + its own
scorecard (the SD analogue of the M28 P4 value gate).

## What P1b does

`scripts/macro/sysdyn_gas_data.py` (injected reader) + `sysdyn_gas_calibrate.py`
(runner):

1. Pull the **real weekly Henry Hub NG price** (`WHHNGSP`, keyless FRED, ~1997→)
   through the existing off-VM-guarded `fred_adapter` fetcher.
2. Build the exogenous drivers (`heating_demand`, `injection_season`)
   **deterministically from each observation's calendar week-of-year** (a cosine
   season peaking in deep winter). Demand *amplitude* is arbitrary — the fitted
   `wd_rate` absorbs it — so only the calendar *shape* is injected.
3. Fit the free params on the head of the window, **score the held-out tail**
   (out-of-sample fit), and run **`walk_forward_stability`** for
   identifiability (how far the STRUCTURAL params move fold-to-fold).
4. Emit `comms/macro/sysdyn_gas_scorecard.json` and land it on `main`
   (`.github/workflows/sysdyn-gas-calibrate.yml`, off-VM, PAT auto-merge).

`base_price` is fit per-run/-fold as a **local price-level anchor** (real NG price
is non-stationary — the 2020 crash, the 2022 spike) and is **excluded from the
identifiability verdict** (its cross-decade spread is expected, not a structural
claim). The verdict judges the four structural free params
(`inj_rate`, `wd_rate`, `price_k`, `price_feedback`).

## First result (8-year window, 418 weekly obs, 2018-07 → 2026-07)

```
OOS      : rmse≈1.54  r2≈0.001   (holdout 104 of 418)
stability: structural_max_rel_spread≈1.55  (threshold 0.5)
verdict  : equifinal_no_edge   identifiable=False   oos_edge=False
```

**Honest reading:** the seed, driven by a **calendar-seasonal demand proxy**,
does *not* explain real weekly NG price (OOS R² ≈ 0) and its structural params are
*not* identifiable from price alone (they rail to their bounds; fold-to-fold spread
≫ threshold). This is **the expected, informative outcome — not a harness bug**
(the synthetic round-trip test reproduces its own series at OOS R² > 0.9, proving
the calibration machinery is correct). The result says plainly: **price + a
calendar season is too thin a signal for this structure.** The lever that matters
is the *real* data the seed was designed around — observed storage + weather.

## The scope line, and what P1c is

P1b calibrates against the keyless real **price** with a *calendar-seasonal*
demand proxy, so the seed can only reach the **seasonal** component of NG price —
the secular level and the **cold-snap *surprise*** shocks (exactly where the B1
loop's research edge lives) are out of reach here.

**P1c** injects the two real series that close that gap:
- **Observed EIA weekly working-gas-in-storage** as a second calibration target
  (`storage_series` predictor) — anchors the stock trajectory instead of letting
  it float, making the fit well-posed.
- **Weather HDD** (population/gas-weighted heating degree days) as the real,
  surprise-carrying `heating_demand` driver, replacing the calendar proxy.

Both need a stable EIA source. EIA's weekly storage is **not** keyless (FRED
carries only monthly; EIA's weekly feed is signed-URL / API-key gated), so P1c
needs a free **`EIA_API_KEY`** added to Actions secrets — a genuine operator
hand-off (the one class of thing a session can't self-serve). The P1b code is
structured so P1c only *adds* series + a second predictor, not a rewrite.

## The decision gate this feeds

The strategic question behind Phase B was whether to invest more in SD
lever-mapping before more value calibration. P1b's answer is a clean **"not on
price alone."** The go/no-go on deeper M29 investment should be taken *after* P1c
(real storage + weather), where the seed finally has the inputs its structure was
built for — that is the first run whose scorecard can fairly judge whether the
mechanistic model beats the static M28 value read.
