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

## First execution step

Harness flag is in. Next: run ETH/SOL/XRP/ADA 2h WITH/WITHOUT (`off` vs `di` vs
`slope`) on the trainer through `trainer_run.sh` on the real multi-year candle
feed, and report the net comparison. If a leg clears the go bar, author the
first `trending_down` cell for `eth_pullback_2h` as the pilot Tier-3 PR.
