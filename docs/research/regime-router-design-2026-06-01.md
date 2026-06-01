# Regime router — design proposal (2026-06-01)

> **Initiative step 2** of regime-aware routing (`PERF-20260601-002`). The
> evidence (step 1) is `docs/research/regime-roster-matrix-2026-06-01.md`. This
> proposes the mechanism that turns that evidence into a live policy. **Tier-3
> design — operator-gated; nothing here is wired yet.** Operator chose
> "design proposal first" (2026-06-01).

## 1. The problem the matrix exposed

Each strategy's edge is **regime-conditional**, and the roster currently encodes
that only as scattered, per-strategy ADX gates (fade/fvg require ADX<20; trend
has none). The matrix quantified the per-regime, per-direction net-R:

| Strategy | trending | transitional | chop |
|---|---:|---:|---:|
| trend_donchian **long** | **+22** | **+22** | +3 |
| trend_donchian **short** | −28 | −24 | **+16** |
| fade_breakout_4h | — (gated) | +5 | **+14** |
| squeeze_breakout_4h | +5 | +2 | **+11** |
| fvg_range_15m | — (gated) | — | **−17** |
| htf_pullback_trend_2h **long** | **+30** | +13 | −8 |
| htf_pullback_trend_2h **short** | −0.05 | −4 | −4 |

Two structural facts jump out: (1) a strategy/direction that earns in one regime
**loses** in another (trend-short: +16 chop vs −52 trending+transitional), and
(2) the roster's edges are **complementary across regimes** — trend-long owns
trending, the mean-reverters own chop. A router that leans the book toward the
regime-fit cells captures more of each edge than any fixed two-sided config.

## 2. Goal

A single, declarative **regime × strategy × direction policy** evaluated in the
intent layer that gates (and later weights) each strategy/direction by the
**currently detected regime** — replacing the ad-hoc per-strategy gates with one
coherent, auditable policy seeded from the matrix.

Non-goals: it does not change any strategy's entry/exit logic, does not add an
order path, and does not touch account/mode gates. It is a **pre-existing-signal
filter/weight**, same class as the `execution: shadow` and `FLIP_POLICY` gates.

## 3. Building blocks (already in the system)

- **ADX regime primitive** — the same `ADX-14, chop<20 / transitional 20–25 /
  trending≥25` cut the matrix and the live fade/fvg gates use.
- **`regime-classifier-baseline-v0`** — a shadow model already in the registry; a
  drop-in/augment for the ADX threshold detector once validated against it.
- **Intent layer** (`src/runtime/intents.py`) — `StrategyIntent` +
  `aggregate_intents` is the natural, single enforcement point (it already folds
  per-strategy priority + `FLIP_POLICY` before the order package is built).

## 4. Design

### 4.1 RegimeDetector (one source of truth)
A small module that, per `(symbol, timeframe)`, returns the current regime from
ADX-14 on the strategy's own candles (the builders already fetch them). One
detector instead of each strategy recomputing ADX. Output:
`{regime: chop|transitional|trending, adx: float, source: "adx-14"|"classifier-v0"}`.
Logged per tick for observability. The classifier can later replace `source`
without changing consumers.

### 4.2 The policy table (seeded from the matrix)
A declarative map in config (e.g. `config/regime_policy.yaml`), each cell a
**gate** (phase 1) or **weight** (phase 2):

```yaml
# regime: { strategy: { long: on|off|weight, short: on|off|weight } }
trending:
  trend_donchian:        { long: on,  short: off }   # short −28
  squeeze_breakout_4h:   { long: on,  short: on  }   # +5 net
  htf_pullback_trend_2h: { long: on,  short: off }   # long +30, short flat (−0.05)
  fade_breakout_4h:      { long: off, short: off }   # ADX-gated anyway
  fvg_range_15m:         { long: off, short: off }
transitional:
  trend_donchian:        { long: on,  short: off }   # short −24
  squeeze_breakout_4h:   { long: on,  short: on  }
  htf_pullback_trend_2h: { long: on,  short: off }   # long +13, short −4
  fade_breakout_4h:      { long: on,  short: on  }   # +5
chop:
  trend_donchian:        { long: on,  short: on  }   # short +16 ← reclaims the long-only drop
  fade_breakout_4h:      { long: on,  short: on  }   # +14
  squeeze_breakout_4h:   { long: on,  short: on  }   # +11
  htf_pullback_trend_2h: { long: off, short: off }   # long −8 / short −4, same shape as fvg
  fvg_range_15m:         { long: off, short: off }   # −17 loser, keep off everywhere
```

Default for an unlisted cell is **on** (permissive — never strand a capability,
matching the `execution`/`mode` gate philosophy). The table is the matrix's
sign, made executable and reviewable in one place.

### 4.3 Enforcement point
In `aggregate_intents` (or `intent_from_signal`), after a `StrategyIntent` is
built and before priority resolution: look up the detected regime for the
intent's `(symbol, timeframe)`, read the `(strategy, direction)` cell, and:
- **Phase 1 (hard gate):** if the cell is `off`, drop the intent with
  `reason="regime_gated_<regime>"` (mirrors `short_suppressed_long_only`). No new
  order path; the intent simply doesn't compete.
- **Phase 2 (soft weight):** multiply the intent's confidence/size by the cell
  weight before priority resolution, so regime-fit strategies win ties.

### 4.4 This subsumes the per-strategy specials
- `trend_donchian` long-only (shipped #2570) becomes the `short: off` cells in
  trending+transitional **plus** a `short: on` cell in chop — i.e. the router is
  where the chop-only trend-short edge (+16 R) is reclaimed without special-casing
  the strategy. (When the router ships, revisit whether to drop the strategy-level
  `long_only` flag in favour of the table.)
- fade/fvg's hardcoded ADX<20 gates become their `off` cells in trending — one
  policy, not N scattered checks.

## 5. Rollout (phased, low-risk)

1. **Detector + observability (Tier-1/2):** ship `RegimeDetector` + per-tick
   regime logging, **no enforcement**. Confirm the live regime stream matches the
   matrix's base rates (chop ~30% / transitional ~19% / trending ~51%).
2. **Shadow the policy (Tier-2):** evaluate the table per tick and **log** which
   intents *would* be gated, without acting. Compare a week of would-gate
   decisions against actual fills.
3. **Hard gates live (Tier-3):** enable `off`-cell gating on the net-negative
   cells only (the clearest wins: trend-short in trending/transitional, fvg
   everywhere). Operator-approved, behind a `REGIME_ROUTER_ENABLED` gate with a
   one-flag rollback.
4. **Soft weights (Tier-3):** graduate to confidence weights once the gates prove
   out; swap the ADX detector for `regime-classifier-baseline-v0` if it validates.

## 6. Open questions for the operator

1. **Detector timeframe:** per-strategy TF (each strategy's own bars) vs one
   canonical regime TF (e.g. 1h) for the whole book? Per-strategy matches how the
   edges were measured; canonical is simpler to reason about.
2. **Gate vs weight first:** start with hard gates (mechanical, auditable) — yes?
   Soft weights are higher-ceiling but harder to validate.
3. **Keep or retire the strategy-level `long_only` flag** once the table covers
   trend_donchian's short cells?
4. **Boundary hysteresis:** ADX hovering at 20/25 will flip regimes tick-to-tick;
   add a hysteresis band / dwell-time so the router doesn't thrash a strategy
   on/off at the boundary.

## 7. Dependencies / coverage

The table above is decision-grade for trend/fade/squeeze/fvg/htf_pullback
(htf_pullback's row landed 2026-06-01 via #2573 and `scripts/backtest_pullback.py`).
One cell still needs follow-up data before it's trustworthy: **vwap**
(`PERF-20260601-003` — re-run with live selectivity params; the current unfiltered
−3749 R run isn't decision-grade). Until then vwap defaults to permissive (`on`)
in every regime so the router never strands it.
