# Implementation Plan — Account-level 1.5% sizing + RiskManager confidence modulation

> **Status:** Tier-3 PROPOSE-ONLY. **No code/config changed by the research pass.** The lead
> implements on a branch and opens a **draft PR**; **no merge without operator approval of §3 +
> the §5 backtests.** Origin: the 2026-06-29 optimization investigation (delegated research),
> Rev. 2 (operator decisions folded in).

## Operator decisions (folded in)

1. **Uniform raise to 1.5%** — every account's `risk_pct` → `0.015`. *"The risk per account is
   1.5%, that has to be true in all places at all times."*
2. **Confidence-aware sizing lives IN the RiskManager**, modulating the 1.5% basis by
   `package.confidence` — replacing the per-strategy multiplier's "differentiate trades" role,
   keyed on trade-level confidence and centralized. *"Not every trade has to have the exact same
   risk size, but the basis is per account, and any adjustments toward the trade should be made
   at the risk-manager level, not the strategy level."*
3. **CI guard stays** — per-strategy risk fields remain forbidden; confidence-sizing is
   RiskManager-internal.

> **Provenance caveat:** the tree is a single squashed commit, so historical rationale is from
> in-file YAML comments, not commit archaeology. The ~30 builder-site line numbers in §2.4 must
> be re-confirmed at edit time.

## 1. Root cause (the diagnosis)

Tiny positions are caused by a **silent under-size from a per-strategy `risk_pct` multiplier**:
`config/accounts.yaml::bybit_2.risk_pct = 0.01` (1%) but `config/strategies.yaml::trend_donchian.risk_pct
= 0.3` is **a multiplier**, injected as `meta["strategy_risk_pct"]` (`pipeline.py:335-353`) and
applied as `effective_risk_pct = self.risk_pct × strategy_risk_pct = 0.003` (0.3%) in
`risk.py:590-593`. So the binding constraint is the **0.3% risk budget**, not margin (margin is
~7% utilized at 3× in a worked example). No double-count; the per-trade and account-level (5%
daily/DD) axes are coherent. The genuine issue is a **naming footgun** — the `strategies.yaml`
field is a *multiplier* named identically to the account *percentage*. Most strategies are `0.3`;
real-money exceptions: bybit_2 `eth_pullback_2h` `0.6`. Prop `breakout_1` sizes from
`config/prop_rulesets/breakout_routing.yaml::risk_pct: 1.5` — **NOT** this multiplier (verified).

## 2. Removal design (exact diffs)

- **`risk.py:590-593`** — replace the multiplier read with an account-basis × confidence scalar
  (§2.8):
  ```
  effective_risk_pct = self.risk_pct * self._confidence_scalar(getattr(package, "confidence", 0.0))
  ```
- **`pipeline.py:166-194`** — delete `STRATEGY_RISK_PCT` map + `_strategy_risk_pcts_from_registry`;
  **`pipeline.py:335-353`** — delete the `meta["strategy_risk_pct"]` injection.
- **`intent_multiplexer.py:508-520`** — delete the re-injection.
- **`strategy_signal_builders.py`** — strip every `"strategy_risk_pct": …` meta line (~30 sites;
  re-grep at edit time; after removal `grep -rn strategy_risk_pct src/` must be **0**).
- **`config/strategies.yaml`** — remove the ~40 `risk_pct:` fields.
- **Prop path — NO CHANGE** (sizes from `breakout_routing.yaml`).
- **Docs** — root `CLAUDE.md` no-pos_size history + `accounts.yaml` header + `new-strategy` SKILL.

### 2.8 New `RiskManager._confidence_scalar` (pure, bounded, reductive-only)

Config keys in each account `risk:` block (default-safe): `confidence_sizing: off|linear|threshold`,
`confidence_floor` (default 0.5), `confidence_knee` (default 0.7, threshold mode).
```
f(c): off → 1.0 ; linear → floor + (1-floor)*c ; threshold → ramp to 1.0 at knee, flat above.
Scalar ∈ [floor, 1.0]  → the account basis is the CAP (size only ever scales DOWN), so aggregate
risk stays inside the 5% daily/DD caps regardless of confidence. NaN/inf/out-of-range → 1.0 (fail-safe).
```
`package.confidence` is a real always-present field (`coordinator.py:96`, default 0.0). Applied
*inside* `position_size`, so every downstream gate (daily-loss, margin pre-flight, venue-min) and
the reductive advisory/news/conviction sizers operate unchanged — confidence-sizing only shrinks.

### 2.9 Curve options (operator picks mode + floor + knee)

- **(a) `linear`, floor 0.5 — RECOMMENDED** (basis = cap; low-conf trades risk ≥ 0.75%, high-conf
  up to the 1.5% cap; single knob; provably bounded).
- **(b) centered band — NOT RECOMMENDED** (allows >1.5% → breaches the "basis = max" invariant the
  operator set).
- **(c) `threshold` ramp** (full size only above a knee; two knobs).

**Rollout safety:** ship `confidence_sizing: off` as the merged default → the PR's behavioural
change is exactly the uniform 1.5% raise (flat); enable the curve as a SEPARATE operator-gated
config flip after §5 Arm 2 clears. Two independently-approvable, independently-revertible changes.

## 3. Reconciliation table (before → after at uniform 1.5%, confidence `off`)

| Account (class) | Strategy | Effective today | After (flat 1.5%) | Multiple | Flag |
|---|---|---|---|---|---|
| **bybit_2 (REAL)** | trend_donchian, ict_scalp_5m, fvg_range_15m, htf_pullback_trend_2h | 0.30% | **1.50%** | **5.0×** | 🔴🔴 hard backtest gate |
| **bybit_2 (REAL)** | eth_pullback_2h | 0.60% | **1.50%** | **2.5×** | 🔴 |
| **alpaca_live (REAL)** | ETF sleeve (14) | 0.60% | **1.50%** | **2.5×** | 🔴 (acct basis 2.0%→1.5% drop, but effective rises) |
| **breakout_1 (PROP)** | sol/eth donchian, eth_pullback_(prop_)2h | 1.50% (routing) | 1.50% | 1.0× | 🟢 unchanged |
| bybit_1 / ib_paper / alpaca_paper (paper) | rosters | 0.30–1.0% | 1.50% | up to 5× | 🟡 paper |

`accounts.yaml` diffs: set `risk_pct: 0.015` on bybit_1/bybit_2/ib_paper/alpaca_paper/alpaca_live
(0.02→0.015)/alpaca_options_paper/oanda_practice; breakout_1 already 0.015 (confirm == routing).
Add `confidence_sizing: off` + `confidence_floor: 0.5` to each block. **Every line is Tier-3 →
operator sign-off at merge.** Coherence: at flat 1.5% ~3.3 independent concurrent stop-outs reach
the 5% daily cap (vs ~16 at 0.30%) — materially tighter; the confidence curve widens it back for
low-conf books.

## 4. CI guard

`scripts/check_strategy_risk_field_in_diff.py` + `.github/workflows/strategy-risk-guard.yml`
(mirror `env-gate-guard`): fail a PR that re-adds `risk_pct:` under `config/strategies.yaml` or any
`strategy_risk_pct` token under `src/`. Override `# allow-strategy-risk: <reason>`. Confidence keys
live in `accounts.yaml`/RiskManager → outside the guard. Ships in the same PR + `tests/test_strategy_risk_guard.py`.

## 5. Backtest / validation plan (three arms per affected real-money route)

0. **Baseline** (today's 0.3× effective) — bybit_2 + alpaca_live; record R, maxDD (R/%), profit
   factor, trade count, worst concurrent DD.
1. **Flat uniform 1.5% (`off`)** — R + maxDD% scale by the §3 multiple. **Binding check: OOS maxDD%
   stays inside the 5% daily/intraday caps at the raised size** (esp. bybit_2 at 5×). Reject any arm
   that breaches.
2. **Confidence-sizing (`linear` floor 0.5; + `threshold` knee 0.7)** — does it beat flat 1.5% on
   risk-adjusted return (R/maxDD, Sharpe) and cut maxDD? Sweep floor ∈ {0.3,0.5,0.7}; report the
   realized confidence distribution.
3. **Prop EV/survival regression (breakout_1)** — `account_compat_matrix.py` →
   `montecarlo.run_ev_montecarlo` confirms the unchanged 1.5% routing still clears the 6% static /
   3% daily killers.
4. **Caps-coherence report** + **unit tests** (`_confidence_scalar` pure/bounded/NaN-safe/monotone;
   `position_size` ignores `meta["strategy_risk_pct"]`; guard test; `grep strategy_risk_pct src/`==0).

## 6. Operator decisions required

1. Approve the uniform-1.5% merge after Arm 1 confirms bybit_2 survives 5× within the 5% caps.
2. Pick the confidence curve mode + floor (+ knee) after Arm 2. (b) centered-band is not recommended.
