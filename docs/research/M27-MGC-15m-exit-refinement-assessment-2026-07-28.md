# M27 — `ict_scalp_mgc_15m` exit-refinement assessment (2026-07-28)

Follow-up to the merged wiring PR
[#7848](https://github.com/benbaichmankass/ict-trading-bot/pull/7848) ("Wire
`ict_scalp_mgc_15m` (ungated) to `ib_paper`"). That PR filed a `pending`
exit-refinement coverage row for the new leg; this memo is the **P0/P1
assessment** of that row per the `exit-refinement` skill, and it converts the
eight silently-optimistic `pending` cells to honest, evidence-grounded verdicts.

## TL;DR

**The leg cannot be exit-swept today, and that block is family-wide (every
`ict_scalp` leg), not specific to MGC.** Of the eight M20 lever columns:

| Lever | Verdict | Why |
|---|---|---|
| `trail_geometry` | **n/a** | `ict_scalp` is a **fixed SL/TP/timeout bracket**; its `monitor()` only trails to break-even after 1R. No primary trailing stop to reshape. |
| `trail_decay` | **n/a** | Presupposes a trailing stop — none exists. |
| `vol_trail` | **n/a** | Presupposes a trailing stop — none exists. |
| `regime_flip_exit` | **n/a** | Fires on an ADX→OFF regime-policy cell; MGC has **no regime head** + an explicit `regime_coverage_exemptions` entry, so the flip can never fire. (Mechanism is a fleet-wide honest_negative besides.) |
| `stale_stop` | **blocked:no_harness_levers** | Meaningful for a fixed bracket, but neither the harness nor the live monitor supports it yet. |
| `giveback_stop` | **blocked:no_harness_levers** | Same as `stale_stop`. |
| `exit_ladder` | **blocked:no_harness_levers** | No partial-TP path in harness/monitor; fleet-wide "banking parked". |
| `exit_head_ml` | **blocked:native-history-thin** | E0 needs native MGC-15m history; IBKR caps at ~1yr + flat-bar contamination. Proxy is OK for levers, not head training. |

## Evidence

### 1. The `ict_scalp` family has no exit-lever support — at BOTH layers

**Research harness** (`scripts/backtest_ict_scalp.py`): its argument surface is
entry/confidence/timeout only (`--min-confidence`, `--confidence-sweep`,
`--timeout-bars`, `--sim-breakeven`, `--emit-trades`). There is **no**
`--stale*` / `--giveback*` / `--trail*` / `--ladder*` flag. Its `_simulate_exit`
is a fixed SL/TP/timeout walk with an optional break-even move — no trailing,
stale, or giveback logic.

**Fleet sweep** (`scripts/research/m20_fleet_exit_sweep.py`): the family→harness
map is explicit and **excludes `ict_scalp`**:

```python
# families with harness exit-lever support; everything else is reported
# no_harness_levers (vwap/ict_scalp/turtle_soup/fade — pending harness levers)
FAMILY_HARNESS = {"donchian", "pullback", "squeeze", "fvg"}
```

**Live monitor** (`src/units/strategies/ict_scalp.py::monitor`): delegates to
`monitor_breakeven_sl` and nothing else — it trails SL to break-even once price
moves 1R in favour. It does **not** read `stale_exit_bars` / giveback /
trail-decay from cfg. `order_monitor._load_live_strategy_cfgs` *does* pass the
live YAML cfg to `monitor()` for already-open packages (M20 E3), so the plumbing
to deliver a lever exists — but `ict_scalp.monitor()` has no code to enforce one.

**Consequence:** a lever is un-shippable for `ict_scalp` at two layers. Even a
lever that passed a backtest could not be enforced live, so there would be
nothing to declare in YAML. The honest status is `blocked:no_harness_levers`
(for the levers that *could* apply to a fixed bracket) / `n/a` (for the
trailing-only levers that structurally cannot).

### 2. The trailing levers are structurally inapplicable to a fixed-bracket scalp

`ict_scalp` sets `tp = entry ± tp_at_r·risk` at signal time and exits at that
fixed TP, the fixed SL, or `timeout_bars` — plus a break-even nudge after 1R.
`trail_geometry` / `trail_decay` / `vol_trail` all reshape a *primary trailing
stop* that this strategy does not have. Turning the scalp into a trailing
strategy would change its identity, not tune its exit — so these are `n/a`, not
`blocked`.

### 3. `exit_head_ml` is blocked on data, permanently for the native instrument

Per [`M27-P0-MGC-15m-findings-2026-07-28.md`](./M27-P0-MGC-15m-findings-2026-07-28.md):
IBKR serves only ~1yr of MGC intraday history and the 15m series inherits real
flat-bar contamination, so a per-bar E0 exit-head dataset with a purged
walk-forward is structurally underpowered. The Dukascopy spot-XAU proxy that
powered the *entry* decision is fine for a scale-invariant lever sweep but not
for training a head (same call as the `mgc_pullback_1d` row: "proxy OK for
levers, not for head training"). Blocked until a deeper non-IBKR MGC/gold-futures
15m source is wired.

## P1 — live evidence read

Deferred to soak. The leg was wired minutes before this assessment and has no
meaningful closed-trade history in the journal yet. `m20_exit_analysis.py`
(MFE-vs-realized-R, giveback, round-trip fraction) should be run once the
`ib_paper` soak has accrued a real sample — at which point the buildable levers
(`stale_stop`, `giveback_stop`) can be prioritized by the failure mode the live
paths actually show.

## What would unblock the buildable levers

A family-wide M20 extension (tracked as `MB-20260728-ICTSCALP-EXIT-LEVERS`),
which would also unblock `ict_scalp_5m` + `ict_scalp_sol/xrp/avax_5m`:

1. **Tier-1** — add `--stale-exit-bars` / `--stale-exit-below-r` /
   `--giveback-min-mfe-r` / `--giveback-r` to `scripts/backtest_ict_scalp.py`
   (mirroring the `backtest_pullback.py` lever semantics) and register
   `ict_scalp` in `m20_fleet_exit_sweep.FAMILY_HARNESS`.
2. **Compute** — run the config-exact IS/OOS + yearly walk-forward lever sweep
   on `data/XAUUSD_15m_deep.csv` (178,953 bars) under MGC economics
   (`--fee-usd-roundtrip 3.0 --contract-value-usd 10.0`) on the **trainer VM**
   (a single 178k-bar baseline run exceeds 120s; a full sweep is multi-hour and
   must not run on the live-adjacent sandbox). Gate: beat baseline on net_R AND
   maxDD in BOTH IS and OOS.
3. **Tier-3 (only if a lever passes)** — teach `ict_scalp.monitor()` to read +
   enforce the winning lever from cfg, declare it on the leg's YAML, and
   propose to the operator with the exact diff.

The strong M20 fleet prior is that these levers are mostly honest-negatives
(trend-oriented levers on a mean-reverting fixed-bracket scalp are a poor fit),
so step 3 may well never be reached — but the sweep is the honest way to find out.

## Item 2 — real-money `ib_live` route (the other PR #7848 follow-up)

**Blocked on the paper soak, by design.** The wiring PR shipped `ib_paper`
(paper money) precisely because MGC's own 15m data is structurally too thin to
backtest-promote to real money (the powered evidence is the spot-XAU proxy, and
the native Arm-B read leans net-negative). The real-money decision is a separate
later Tier-3 gate that needs a genuine `ib_paper` soak first — there is zero
soak data yet, so this item is correctly not actionable now.
