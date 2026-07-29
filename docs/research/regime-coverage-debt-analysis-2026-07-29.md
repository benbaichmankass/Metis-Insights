# Regime-coverage debt — analysis + evidence-generation plan (2026-07-29)

> **Type:** Tier-1 research/analysis (the autonomous-analysis half of rec #5 in
> `docs/research/roadmap-toolbox-assessment-2026-07-29.md`). It produces the
> **plan + rule** for paying down `BL-20260717-REGIME-COVERAGE-DEBT`; the actual
> `config/regime_policy.yaml` cell edits it authorizes are **Tier-3** (operator-gated).
> Operator-approved 2026-07-29: source the cell evidence via a **free-runner
> backtest matrix** (not the 1-OCPU trainer).

## 1. The debt

`docs/strategy-coverage-matrix.md`: **4 celled · 7 exempt · 33 in debt** (ceiling 35,
down-only). The 33 (`config/regime_coverage_exemptions.yaml::coverage_debt`) are all
variants of **three strategy TYPES**, each with a celled or matrix-measured exemplar:

| Type | Exemplar (evidence-based profile) | Debt variants (33) |
|---|---|---|
| **Donchian trend** | `trend_donchian` (BTC 1h) — matrix: long=trend edge, short only earns in chop; 2-D: long OFF in trending-vol / transitional-calm / chop-calm | `trend_donchian_{ada,avax,eth,eth_4h,sol,sol_4h,xrp_4h}` (7) |
| **Pullback trend-follower** | `htf_pullback_trend_2h` — matrix: **owns trending (+30.1R)**, long-led, **loses chop (−12.2R)**; short flat everywhere | `{ada,avax,eth,eth_prop,sol,xrp}_pullback_2h`, `{gld_1h,qqq_1h,spy_1h,tlt_1h,slv_1h→slv_trend_1h,uso_1h→uso_trend_1h}`, `{gld,gdx,iaum,slv,mgc,mhg,ief,tlt}_pullback_1d` (mixed) |
| **Long-only trend** | *(none celled — needs its own matrix row)* | `{iwm,mes,qqq,qld,scha,splg,spy,tqqq}_trend_long_1d` (8) |

By asset class: **13 crypto** (bybit — paper/prop), **~12 equity/ETF** (Alpaca —
paper, `alpaca_live` held dry), **~8 metals/commodities** (IB/Alpaca), **3 bonds**.

## 2. The enforceability split (why 1-D is the deliverable, 2-D is a follow-up)

The two regime axes resolve their regime very differently, and this decides what a
cell can actually enforce:

- **1-D TREND axis** (`trending`/`transitional`/`chop`) is computed **live from
  ADX-14 on the strategy's own candles** (`src/runtime/regime/detector.py`) — **no
  registered ML head required.** An evidence-based 1-D direction cell is therefore
  **enforceable for every one of the 33**, any symbol. This is the celled exemplars'
  mechanism.
- **2-D vol refinement** (`trend_vol` block) resolves `vol_regime` from a
  **registered shadow-stage regime head per `(symbol, timeframe)`**. **Most debt
  symbols have no such head** (only BTC — and BTC isn't in the debt list). A
  `trend_vol` cell for a headless symbol resolves `vol_regime="unknown"` → a
  **permissive no-op** — the exact "cosmetic cell" the `ict_scalp_{sol,xrp,avax,...}`
  legs were made `exempt` to avoid. So the vol layer is a **per-symbol follow-up as
  heads get registered**, NOT part of this debt-paydown.

**Conclusion:** pay the debt down with **evidence-based 1-D trend×direction cells**.
That is fully enforceable today and takes the debt to 0. Vol refinement is tracked
separately, unlocked per-symbol by a registered regime head.

## 3. The cell-authoring rule (what earns an `off`)

Mirror exactly how the exemplars were earned — **no cosmetic cells**:

- Author `{ long: off }` / `{ short: off }` for a `(strategy, trend_regime, side)`
  **only** when that cell is **net-negative after fees** with an **adequate sample**
  (the roster-matrix bar: net-R < 0 at n large enough to trust — the 2026-06-01 doc
  used ~tens-of-trades-per-cell minimums; a thin/ambiguous cell stays permissive `on`).
- **Default stays permissive `on`** — never strand a direction on weak evidence.
- A strategy whose every measured cell is positive/flat → it graduates from
  `coverage_debt` to a **reasoned `exempt`** (measured, no OFF cell warranted),
  not a fabricated cell.
- Every proposed cell carries its net-R + n as an inline comment (as the current
  cells do), and lands as a **draft Tier-3 PR** for operator approval — merging a
  cell is the live-routing switch.

## 4. Evidence-generation plan (free-runner backtest matrix)

The 2026-06-01 method (`docs/research/regime-roster-matrix-2026-06-01.md`), run
**off-VM on GitHub-hosted runners** (per `docs/claude/vm-resource-management.md` —
the matrix needs no VM-resident state, only repo code + a public candle fetch):

1. For each debt strategy, drive its standalone harness at its **EXACT live params**
   from `config/strategies.yaml` (the min_confidence-reconciliation lesson —
   wrong params give a misleading matrix): `scripts/backtest_trend.py` for the
   Donchian + `*_trend_long_1d` legs, `scripts/backtest_pullback.py` for the
   `*_pullback_*` legs, each with `--emit-trades` + `--fee-bps-roundtrip`.
2. Tag each emitted trade by ADX-14 regime at its entry bar with
   `scripts/research/regime_tag_emitted.py` (the same primitive the live gate uses).
3. Aggregate **net-R per `(strategy, trend_regime, side)`** → the per-cell table.
4. Author the 1-D cells per §3; regenerate `docs/strategy-coverage-matrix.md`
   (`python scripts/check_strategy_coverage.py --matrix`) and lower `debt_ceiling`.

**Batching** (one data source + one harness per batch keeps each run coherent):

| Batch | Symbols | Data source (public, keyless-first) | Harness |
|---|---|---|---|
| **B1 — crypto** (13) | ada/avax/eth/sol/xrp Donchian + pullback_2h | Binance-vision (keyless) or existing crypto CSV cache | `backtest_trend` / `backtest_pullback` |
| **B2 — equity/ETF** (~12) | spy/qqq/iwm/qld/tqqq/splg/scha/mes trend_long + spy/qqq pullback | yfinance→Stooq (keyless) | `backtest_trend` / `backtest_pullback` |
| **B3 — metals/bonds** (~8) | gld/gdx/iaum/slv/mgc/mhg/uso + tlt/ief | Dukascopy (metals/FX) + yfinance (ETF/bond) | `backtest_pullback` / `backtest_trend` |

A new runner workflow (sibling of `research-symbol-p0-build.yml` /
`research-exit-head-build.yml`) drives one batch per dispatch: fetch → per-symbol
harness at live params → regime-tag → emit a per-cell net-R table artifact. The
per-account compat gate (`scripts/prop/account_compat_matrix.py` /
`scripts/ops/etf_account_compat.sh`) remains the **separate** mandatory gate before
any cell is promoted onto a real-money account — it is not replaced by this matrix.

## 5. Prioritization

Real-money-adjacent first (the debt is mostly paper today, but cells should land
where a bad regime×direction could bite hardest if promoted):

1. **B1 crypto** — the `trend_donchian_*` + `*_pullback_2h` legs share code with the
   celled BTC exemplars, so their matrices are directly comparable and the highest
   chance of a clean, defensible OFF cell (e.g. Donchian long in chop-calm, pullback
   any-side in chop). Includes `eth_pullback_prop_2h` (a prop leg).
2. **B2 equity/ETF** — the `*_trend_long_1d` type has no celled exemplar yet, so its
   matrix row is genuinely new signal.
3. **B3 metals/bonds** — thinnest histories / most likely to land as reasoned
   `exempt` (measured-no-OFF) rather than a cell.

## 6. Status / next action

- **This doc + the plan = the autonomous-analysis deliverable (done).**
- **Next (mechanical, autonomous):** build the batch runner workflow, run B1→B3,
  author the 1-D cells, and open the **draft Tier-3 PR** with the `regime_policy.yaml`
  diff + the regenerated matrix + lowered `debt_ceiling`. Best done as a focused
  session with full budget (33 heterogeneous symbols × 2 harnesses × data sourcing).
- Tracked under `BL-20260717-REGIME-COVERAGE-DEBT`.
