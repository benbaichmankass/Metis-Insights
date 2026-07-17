# Direction-aware regime filter — design + backtest plan (Phase 2)

> **Status:** design / launched 2026-07-17. Tier-3 (order-routing-affecting) —
> backtest-gated, operator-approved before any live cell. Pays down
> `BL-20260717-REGIME-COVERAGE-DEBT` (the 35 live strategies with no regime
> cell). Root cause it addresses: the 2026-07-16 losing day.

## The problem (evidence-backed)

The 2026-07-16 review found every executed real-money loss was a **long**
"pullback" entry that stopped out in a tape the regime detector classified as
**"trending"**: `eth_pullback_2h` −$4.04 (−0.98R), `xrp_pullback_2h` −$4.71
(−1.00R SL), `ada_pullback_2h` −$2.00 (−0.42R). The paper book (fleet at scale)
bled −$1.5k/−$4.4k on 07-14/07-15 the same way.

The mechanism: **`src/runtime/regime/detector.py` classifies chop /
transitional / trending purely off ADX-14 *magnitude*** (`CHOP_MAX_ADX=20`,
`TREND_MIN_ADX=25`). ADX measures **trend STRENGTH, not DIRECTION** — a strong
*down*-move has the same high ADX as a strong up-move. So a long-only pullback
buyer sees "ADX ≥ 25 → trending → buy the dip" and catches a falling knife.
The regime router can't help because (a) it only covers 6 of 44 live strategies
(the debt register), and (b) even its cells are keyed on the direction-blind
trend label.

## The proposed signal — add DIRECTION to the regime

Split the ADX trend label by direction using the **Directional Indicators**
already computed inside `wilder_adx` (the `plus_di` / `minus_di` series):

- **`trending_up`** — ADX ≥ `TREND_MIN_ADX` **and** `+DI > −DI`
- **`trending_down`** — ADX ≥ `TREND_MIN_ADX` **and** `−DI > +DI`
- `transitional` / `chop` unchanged (direction is noise below the trend floor).

(`+DI/−DI` sign is the standard, cheap, already-computed direction read; a
midline-slope confirm — `close` vs the strategy's `trend_midline` — is the
secondary option if DI proves noisy on daily bars. Backtest decides.)

The **live intent**: a long-biased strategy (the `*_pullback_2h` family, the
`*_trend_long_1d` fleet) should be **gated OFF in `trending_down`** — that is
exactly the falling-knife cell. Shorts (few today) gate off in `trending_up`.

## Where it hooks (two options — backtest informs, operator approves)

1. **Detector-level (preferred):** `detect_regime()` returns
   `trending_up`/`trending_down` instead of a single `trending`, and
   `regime_policy.yaml` cells gain the direction (e.g. `trend_donchian:
   { trending_down: { long: off } }`). This makes the fix reusable across every
   strategy via the existing regime-router machinery — the natural way to pay
   down the 35-cell debt.
2. **Strategy-entry-level:** each long-only pullback/trend unit adds a
   `−DI < +DI` entry precondition. More local, but re-implements the same check
   N times and doesn't compose with the router. Use only if (1) regresses a
   strategy that legitimately wants counter-trend entries.

Default to (1); it's the same lever the regime debt register is waiting on.

## Backtest plan (the evidence gate)

Prototype the direction filter as a `--direction-filter {off,di,slope}` flag in
the research harnesses (`scripts/backtest_pullback.py`,
`scripts/backtest_trend.py`) — compute `+DI/−DI` on the entry timeframe and skip
long entries when `−DI > +DI` (and symmetrically for shorts). Then, **net of
fees**, WITH vs WITHOUT, per symbol:

| Leg family | Symbols | Harness |
|---|---|---|
| `*_pullback_2h` | ETH, SOL, XRP, ADA, AVAX | `backtest_pullback.py` (2h) |
| `trend_donchian_*_4h` | ETH, SOL, XRP, ADA, AVAX | `backtest_trend.py` (4h) |
| `*_trend_long_1d` / `*_pullback_1d` | SPY/QQQ/IWM/GLD/SLV/… | `backtest_{trend,pullback}.py` (1d, trainer `data/<SYM>_1d.csv`) |

Multi-year sweeps run **on the trainer VM through the new heavy-job queue**
(`scripts/ops/trainer_run.sh python scripts/backtest_pullback.py …`) so they
don't thrash the box. Report per leg: net total R, net win rate, N, expectancy,
maxDD, fee bps, and the date/regime window.

**Go bar (per leg, to author a live OFF cell):** the direction filter must
improve **net expectancy AND cut maxDD** on that leg's own history (the same bar
the vol-gate cells cleared), and clear the per-account compat matrix
(`account_compat_matrix.py`) — survival ≥ 0.90, P(breach) ≤ 0.10 — before any
real-money route. Honest negatives get recorded (like the ETH/SOL vol-gate) and
that leg stays uncovered-but-flagged rather than force-fit.

## Rollout (paying down the debt register)

1. Land the harness `--direction-filter` flag + the `detect_regime` direction
   split behind a **shadow** audit row first (observe-only: log what the
   direction gate *would* do, no order change) — mirrors the regime-router
   phase-2→3 rollout.
2. For each leg that clears the go bar, author its `trending_down` (or
   `trending_up`) OFF cell in `regime_policy.yaml` and **remove it from
   `config/regime_coverage_exemptions.yaml` coverage_debt** (ratcheting
   `debt_ceiling` down — the strategy-coverage guard enforces the accounting).
3. Prioritize the legs that bled most (the `*_pullback_2h` family first).
4. Each cell is a Tier-3 PR: draft + operator approval + live-verify the first
   decision.

## Implementation status

- **Harness flag — DONE (this PR).** `--direction-filter {off,di,slope}` is
  wired into **both** `scripts/backtest_pullback.py` and
  `scripts/backtest_trend.py`. `off` is byte-identical to the pre-change run
  (verified: same trade count + net R + JSON, and the param only stamps when
  active — Prime-Directive default-off posture). DI computation is factored into
  one shared `_directional_indicators()` helper that `_adx()` also consumes, so
  the direction read and the ADX magnitude never diverge.
- **Early finding (synthetic smoke test).** On the breakout follower
  (`backtest_trend.py`) the `di` filter skipped **zero** entries — a confirmed
  Donchian breakout already has `+DI > −DI`, so DI never conflicts with it. This
  confirms the design's premise that the direction filter matters for the
  **pullback family** (counter-move dip entries into a falling tape), NOT the
  breakout follower. Rollout should therefore prioritize the `*_pullback_*`
  legs; the trend-follower legs are a lower-yield check.

## Results — REFUTED for the pullback family (2026-07-17)

Ran the 5 alt `*_pullback_2h` symbols on the trainer's ~5-year 2h feed (15m
resampled → 2h), net of 7.5 bps, at the **exact live `eth_pullback_2h` params
incl. `adx_min: 25`** (`off` = current live behaviour). Go bar = improve net
expectancy **AND** cut maxDD vs `off`.

| Symbol | `off` exp / maxDD | `di` Δexp / ΔmaxDD | `slope` Δexp / ΔmaxDD | Go-bar |
|---|---|---|---|---|
| **ETHUSDT** (pilot) | **+0.358** / 13.6 | −0.091 / −0.8 | −0.130 / +2.6 | ❌ fail |
| SOLUSDT | +0.226 / 18.2 | **+0.048 / −6.3** | −0.132 / +3.7 | ⚠️ di only |
| XRPUSDT | +0.168 / 20.4 | +0.025 / +0.0 | **+0.015 / −3.5** | ⚠️ slope only |
| ADAUSDT | +0.316 / 10.5 | −0.038 / +0.2 | −0.128 / +4.5 | ❌ fail |
| AVAXUSDT | +0.165 / 13.7 | −0.108 / +11.8 | −0.219 / +22.5 | ❌ fail |

**Verdict: the direction filter does NOT clear the go bar for the pullback
family, and fails on the pilot (ETH) outright.** Only SOL/`di` and XRP/`slope`
marginally pass — *inconsistently* (a different lever each) amid 4-of-5 fails;
two marginal passes across 10 arms is consistent with noise, and a live cell off
one symbol when the same filter hurts the other four would be exactly the
overfitting the go bar + compat matrix exist to prevent.

**Why it fails — the mechanism is backwards for a pullback strategy.** A
pullback strategy *buys a dip*: at the entry bar price has just moved **down**,
so the instantaneous `+DI/−DI` (and often the midline slope) reads "down" — which
is the *setup*, not an anti-signal. Gating out "down-direction" entries removes
the valid dip-buys. And the pullback unit **already has a direction filter**: it
only goes long when `close > Donchian midline` (`htf_pullback_trend_2h`'s
`uptrend` test) — ADX only adds *strength* on top of that existing *direction*
gate. A second, faster direction gate subtracts.

**Reframe of the 2026-07-16 losses.** Over ~5 years these legs are
net-**positive** (`off` expectancy +0.16 to +0.36 R). 07-16 was a bad day within
the normal loss distribution of legs that work — not a systematic
regime-recognition failure a per-leg direction gate fixes. The genuine risk that
day was **correlated simultaneous entries** (ETH/XRP/ADA long-dipped and dumped
together — the `eth_pullback_2h` "CORRELATION CAVEAT" already flags this), which
points at **correlation-aware sizing / concurrency limits**, not a direction
filter.

**Disposition:** do NOT author `trending_down` OFF cells for the pullback family
(the `*_pullback_2h` alts stay uncovered-but-flagged in
`config/regime_coverage_exemptions.yaml` `coverage_debt` — now *measured*: the
direction filter was tested and rejected, not merely unaddressed). No Tier-3 PR.
The harness `--direction-filter` flag stays as reusable research tooling. The
open follow-up worth pursuing for 07-16-type clustering is
**correlation-aware sizing across concurrently-open correlated alt legs**, a
separate design.
