# Regime-coverage debt — matrix results & disposition (2026-07-29)

**Tracking:** `BL-20260717-REGIME-COVERAGE-DEBT` · rec #5 of
`docs/research/roadmap-toolbox-assessment-2026-07-29.md` §5.
**Method doc:** `docs/research/regime-coverage-debt-analysis-2026-07-29.md` (the
plan this executes).
**Tier:** analysis only — **no live-config change**. Any OFF-cell that gates
live routing is Tier-3 and would land as a separate draft PR for operator
approval. This document's conclusion is that **no such cell is warranted for the
strategies measured here** (see §4).

## TL;DR

Ran the regime-matrix (exact-live-params backtest → `--emit-trades` →
`regime_tag_emitted.py`) for the **7 faithfully-backtestable crypto-plain debt
strategies**. The result is a **negative finding, and a useful one**:

- **Donchian-4h family (ETH/SOL/ADA/AVAX):** the regime cell where the edge
  lives **flips per symbol** (ETH→trending, SOL→transitional, ADA→chop). There
  is **no consistent, powered per-regime OFF-cell** to author. Cutting any
  regime would kill a *different* symbol's entire edge. Per-cell n is 6–14 —
  below the bar for a single-strategy Tier-3 cut.
- **Pullback-2h family (ETH/SOL/XRP):** the `adx_min` filter already confines
  **every** entry to the *trending* regime, so a `regime_policy.yaml` cell would
  be **cosmetic** — it gates nothing `adx_min` doesn't already. The real signal
  here is **directional, not regime**: the long side is a net drag across all
  three symbols at adequate n. That is a Tier-3 *strategy-logic* question needing
  walk-forward, not a regime-coverage cell.

**Bottom line for the debt register:** the regime-coverage debt is largely a
**classification/bookkeeping** problem, not a hidden-edge problem. Authoring
speculative per-regime cells for these strategies would reproduce exactly the
**cosmetic-cell anti-pattern** the debt register exists to prevent (a cell that
resolves `unknown`/redundant and silently gates nothing). The honest dispositions
are in §5.

## 1. Scope — what could be measured faithfully in-sandbox

The debt register (`config/regime_coverage_exemptions.yaml`) holds **35**
strategies. Only a subset is *faithfully backtestable in this sandbox*:

- **Data reachability.** `scripts/ops/fetch_backtest_candles.py` pulls
  **Binance-vision** (crypto USDⓈ-M only, keyless, dodges the Bybit US-runner
  geoblock). Equity/ETF/futures symbols (SPY, QQQ, TLT, GLD, SLV, GDX, IAUM,
  IEF, QLD, TQQQ, SPLG, SCHA, USO; MES, MGC, MHG) are **not** reachable here —
  they need the yfinance/IBKR feed the rec #3 offload path provides.
- **Param fidelity.** The base harnesses (`backtest_trend.py` /
  `backtest_pullback.py`) model Donchian/pullback entry + ATR stop/trail + a
  no-TP `tp_r`. Variants carrying **extra exit levers** the base harness does
  **not** model — `trend_donchian_eth`/`trend_donchian_sol` (stale-exit +
  exit-head + vol-skip), `trend_donchian_xrp_4h` (stale-exit),
  `ada_pullback_2h`/`avax_pullback_2h`/`qqq_pullback_1h` (vol-skip / trail-vol)
  — are only **approximately** backtestable and are **excluded** from this
  faithful pass.

**Faithful + reachable = 7 strategies:**

| Family | Strategies | Harness |
|---|---|---|
| Donchian-4h (LONG+SHORT) | `trend_donchian_{eth,sol,ada,avax}_4h` | `backtest_trend.py` |
| Pullback-2h (ADX-gated) | `eth_pullback_2h`, `sol_pullback_2h`, `xrp_pullback_2h` | `backtest_pullback.py` |

All runs use the **exact live params** read from `config/strategies.yaml`
(donchian 20 / atr 14 / stop 2.5 / trail 5.0 / min-conf 0.6 [avax 0.7];
pullback lookback 40/10 frac 0.5 / atr 14/2.5/5.0 / adx-min 25 [sol 30]),
fee 7.5bps roundtrip, ~300–400d Binance-vision candles. Regime cut-points match
the roster-matrix convention (`regime_tag_emitted.py`: ADX chop < 20,
transitional 20–25, trending ≥ 25 on the strategy's own timeframe).

## 2. Donchian-4h family — per-(regime) net-R

Net-R by regime (LONG+SHORT combined), then the LONG/SHORT split of the total:

| Strategy | trending | transitional | chop | total | long-R | short-R | trades |
|---|--:|--:|--:|--:|--:|--:|--:|
| `trend_donchian_eth_4h`  | **+13.59** | −0.59 | −0.03 | +12.97 | +7.89 | +5.08 | 30 |
| `trend_donchian_sol_4h`  | +1.22 | **+11.56** | +1.35 | +14.14 | +0.98 | +13.16 | 29 |
| `trend_donchian_ada_4h`  | −7.87 | +0.85 | **+11.28** | +4.26 | −7.48 | +11.74 | 39 |
| `trend_donchian_avax_4h` | **+7.42** | −4.32 | +2.02 | +5.12 | −5.81 | +10.93 | 23 |

**Read:**

- **The winning regime flips per symbol** — ETH's edge is in *trending*, SOL's
  in *transitional*, ADA's in *chop*, AVAX's split trending/chop. There is **no
  regime that is consistently a loser** across the family, so no defensible
  common OFF-cell. Gating "chop" (to help ETH/AVAX marginally) would **delete
  ADA's entire +11.3R**.
- **Per-cell n is 6–14** (each cell is ~1–2 decisive trades). At that power a
  single negative cell is noise, not signal.
- The one *directionally* consistent thread: **short-R dominates** (pooled
  long-R ≈ **−4.4**, short-R ≈ **+40.9** across the 4 symbols) — but ETH is a
  clean long-side counterexample (+7.9), and this is one in-sample window on a
  crypto tape (2025→26) that structurally favored shorts. Not actionable without
  walk-forward (§5).

**Disposition:** no Tier-3 regime cell. Keep as `coverage_debt`.

## 3. Pullback-2h family — the ADX filter already is the regime gate

These strategies carry `adx_min` (25, SOL 30), so entries **only fire when ADX
is already in trending territory**. Every emitted trade lands in the *trending*
bucket — there is no chop/transitional exposure for a regime cell to remove:

| Strategy | regime of all entries | trades | long-R (n) | short-R (n) | total |
|---|---|--:|--:|--:|--:|
| `eth_pullback_2h` | trending (100%) | 46 | −11.96 (21) | +9.21 (25) | −2.75 |
| `sol_pullback_2h` | trending (100%) | 30 | −10.66 (16) | +9.96 (14) | −0.70 |
| `xrp_pullback_2h` | trending (100%) | 41 | −4.99 (20) | +21.22 (21) | +16.23 |

**Read:**

- A `config/regime_policy.yaml` trending-only cell would be **cosmetic** —
  redundant with the `adx_min` these strategies already enforce inline. It would
  add a policy entry that gates nothing, which is precisely the noise the debt
  register forbids.
- The genuine, **powered, cross-symbol** signal is **directional**: the **long
  side is a net drag in all three** (−11.96 / −10.66 / −4.99; pooled **−27.6R**
  over 57 long trades) while the **short side is strongly positive** (+9.21 /
  +9.96 / +21.22; pooled **+40.4R** over 60 short trades). n per direction is
  16–25 — adequate to be interesting.
- **Caveat (honest):** single 300d **in-sample** window, no walk-forward folds.
  The long-drag/short-edge asymmetry is consistent with the sample-period tape
  (crypto pullback-longs failing into continued weakness) and could be a regime
  artifact, not a durable edge. **Not a Tier-3 recommendation** — a
  walk-forward study is the prerequisite.

**Disposition:** these are already regime-gated strategy-locally (via
`adx_min`), the same shape as the `ict_scalp_*` **exempt** entries. The honest
register move is **debt → `exempt`** with the reasoned "regime-gating
implemented locally via `adx_min`, a global cell would be cosmetic" note — not a
new policy cell. Deferred to a follow-up (this PR is evidence-only). Separately,
the long-drag observation is logged for a walk-forward study.

## 4. Why no Tier-3 cell is proposed

The debt-payment plan (`…debt-analysis-2026-07-29.md`) set a **no-cosmetic-cells**
rule: a cell is only worth authoring if it (a) gates a genuinely losing
(regime[,direction]) at adequate power **and** (b) is enforceable — either 1-D
(ADX on the strategy's own candles, no ML head) or 2-D with a registered
per-(symbol,tf) shadow regime head. The evidence says:

- **Donchian-4h:** fails (a) — no consistent losing regime across the family; per
  cell n too small; the "edge regime" is symbol-specific.
- **Pullback-2h:** fails (a) on the regime axis (all trending; the losing axis is
  *direction*, not regime) and would be redundant with `adx_min` on (b).

Authoring cells anyway would be curve-fit noise that gates live routing on 1–2
trades of in-sample luck — the failure mode the register was built to stop.

## 5. Dispositions & follow-ups

| Item | Disposition |
|---|---|
| `trend_donchian_{eth,sol,ada,avax}_4h` | **Keep as `coverage_debt`.** No powered cross-symbol regime signal; no cell. |
| `eth_pullback_2h`, `sol_pullback_2h`, `xrp_pullback_2h` | **Recommend reclassify `coverage_debt` → `exempt`** (already regime-gated locally via `adx_min`; a global cell is cosmetic). Register-bookkeeping only — the strategies run identically either way. Follow-up PR. |
| Pullback-2h **long-side drag** (pooled −27.6R / 57 trades; short +40.4R / 60) | **Walk-forward study** before any Tier-3 action — logged to the performance/ml backlog. Single in-sample window; likely regime-of-sample. |
| Equity/ETF/futures debt (the majority — SPY/QQQ/TLT/GLD/SLV/GDX/IAUM/IEF/QLD/TQQQ/SPLG/SCHA/USO + MES/MGC/MHG) | **Blocked on data reachability.** Not fetchable via Binance-vision. Run their matrices through the **rec #3 offload path** (yfinance/IBKR feed) once that workflow lands (`#7911`). Several also carry extra exit levers → need a harness that models them. |
| Lever-carrying crypto variants (`trend_donchian_eth`/`sol`, `xrp_4h`, `ada`/`avax`_pullback, `qqq_pullback_1h`) | Only **approximately** backtestable by the base harness — excluded here. Need the stale-exit/exit-head/vol-skip levers modeled before a faithful matrix. |

## 6. Method verification (byproduct)

This run also **verified the local regime-matrix pipeline end-to-end** in the
sandbox: Binance-vision fetch (`fetch_backtest_candles.py`) → base harness
`--emit-trades` → `regime_tag_emitted.py --json` all run with no workflow
dependency, so a future session can produce a faithful crypto matrix locally in
minutes. The equity/futures half genuinely needs the offload runner (data), not
more sandbox effort.
