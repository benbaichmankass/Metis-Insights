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
| `stale_stop` | **honest_negative** | Harness lever built + swept 2026-07-28 (see P2 below): cuts −7.4R/−6.1R vs baseline, fails the gate in both windows. |
| `giveback_stop` | **honest_negative** | Harness lever built + swept 2026-07-28: −1.0R IS / no-op OOS, fails the gate. |
| `exit_ladder` | **blocked:no_harness_levers** | No partial-TP path in harness/monitor (NOT built this round); fleet-wide "banking parked". |
| `exit_head_ml` | **blocked:native-history-thin** | E0 needs native MGC-15m history; IBKR caps at ~1yr + flat-bar contamination. Proxy is OK for levers, not head training. |

> **Note (same-day update):** the "blocked:no_harness_levers" state below was the
> *starting* state. Following operator greenlight, the stale/giveback harness
> levers were **built** (`scripts/backtest_ict_scalp.py`, PR #7849) and the
> config-exact P2 sweep **run** — both cells came back **honest_negative** (§ P2).
> The trailing-family / regime / ML-head / ladder verdicts are unchanged.

## P2 — config-exact IS/OOS lever sweep (2026-07-28)

Harness levers `--stale-exit-bars` / `--stale-exit-below-r` / `--giveback-min-mfe-r` /
`--giveback-r` were added to `scripts/backtest_ict_scalp.py` (stop-first, fire at
bar close, mirroring `backtest_pullback.py`; default off = baseline unchanged;
tests in `tests/test_ict_scalp_exit_levers.py`). Driver:
`scripts/research/m27/ict_scalp_exit_sweep.py`, config-exact
(`--symbol MGC --timeframe 15m --sim-breakeven`, `ict_scalp_5m` YAML params — the
mgc leg is a copy), on the committed Dukascopy XAU 15m proxy
(`data/XAUUSD_15m_deep.csv`, 178,953 bars), split 2025-07-01.

**Gate:** a cell must beat the baseline on net_R **AND** maxDD in **both** windows.

| Cell | IS ΔnetR | IS ΔmaxDD | OOS ΔnetR | OOS ΔmaxDD | Verdict |
|---|---|---|---|---|---|
| baseline | +51.18R (n=229) | 3.89R | +12.39R (n=56) | 2.18R | — |
| stale8 (<0R) | −7.43 | −0.02 | −5.32 | −0.00 | honest_negative |
| stale12 (<0R) | −6.12 | +0.15 | −4.06 | +0.13 | honest_negative |
| giveback 1R after MFE≥1R | −1.04 | +0.12 | +0.00 | +0.00 | honest_negative |
| giveback 1R after MFE≥2R | +0.00 | +0.00 | +0.00 | +0.00 | honest_negative |

**Read.** None passes. A **stale time-exit** chops trades that recover under the
fixed 1.5R-TP + break-even-after-1R bracket (it removes tail winners without
meaningfully cutting drawdown). **Giveback** barely fires: with TP pinned at
1.5R, MFE rarely reaches the 2R arm (the MFE≥2R cell is a near-no-op), and the
MFE≥1R arm just clips a little net_R with slightly worse maxDD. The result is
robust to fees — the harness R is fee-free and every cell already loses or ties
on R (stale8 even *adds* a trade). This matches the fleet-wide M20 prior:
trend-oriented time/giveback exits don't help a mean-reverting fixed-bracket
scalp. **No lever advances to the walk-forward step; no Tier-3 live-monitor
declare is warranted.** Raw: `verdicts.json` from the driver run.

## P2b — crypto legs (2026-07-28)

Operator directive ("do the crypto leg first") — the harness build unblocked the
whole `ict_scalp` family, so all seven live crypto legs were swept. Because this
sandbox can't reach Bybit and the 1-core trainer would take ~15h, the sweep ran
as a **GitHub Actions matrix** (`.github/workflows/ict-scalp-exit-sweep.yml`,
run 30384837775) — one 4-core runner per leg, in parallel (~15 min wall), each
`scp`-ing its config-exact M27 candle CSV from the trainer's `m27_data` and
running the same IS/OOS driver. Verdicts posted back as PR #7849 comments.

| Leg | Account | Baseline (IS / OOS net_R) | IS/OOS verdict |
|---|---|---|---|
| `ict_scalp_5m` (BTCUSDT) | **bybit_2 real-money** + bybit_1/portfolio | +88.70R / +40.18R | all honest_negative |
| `ict_scalp_sol_5m` | bybit_1 | +81.33R / +51.27R | all honest_negative |
| `ict_scalp_xrp_5m` | bybit_1 | +106.01R / +24.10R | all honest_negative |
| `ict_scalp_avax_5m` | bybit_1 | +147.04R / +43.14R | all honest_negative |
| `ict_scalp_xrp_15m` | bybit_1 | +20.87R / +15.71R | all honest_negative |
| `ict_scalp_sol_15m` | bybit_1 | +23.65R / +20.46R | all honest_negative |
| **`ict_scalp_eth_15m`** | bybit_1 | +36.27R / +12.82R | **stale8 + stale12 CANDIDATE** |

**Read.** 6 of 7 crypto legs (including the real-money BTC-5m leg) are clean
honest_negatives — the levers typically help one window and hurt the other, so
they fail the both-windows gate. **ETH-15m is the sole exception:** a stale-stop
beats baseline on net_R AND maxDD in both windows at the IS/OOS pre-filter
(stale8 IS +12.63R/OOS +0.26R; stale12 IS +8.09R/OOS +2.92R). The giveback cells
are honest_negative everywhere (the MFE≥2R arm is a no-op fleet-wide — TP at 1.5R).

**ETH-15m walk-forward.** The IS/OOS pass is only the pre-filter; the M20 gate
requires the yearly walk-forward (beat-or-tie net_R AND maxDD in ≥ ceil(2/3)
usable folds). Run via `--walkforward` (see the coverage-matrix `ict_scalp_eth_15m`
`stale_stop` cell for the outcome). A survivor would be a **Tier-3** proposal
(teach `ict_scalp.monitor()` to enforce a stale-stop + declare on the ETH-15m
YAML) — never an autonomous change.

## Evidence (starting state, pre-build)

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

## What was built + swept, and what remains

The family-wide M20 extension (tracked as `MB-20260728-ICTSCALP-EXIT-LEVERS`):

1. **Tier-1 — DONE (PR #7849).** `--stale-exit-bars` / `--stale-exit-below-r` /
   `--giveback-min-mfe-r` / `--giveback-r` added to
   `scripts/backtest_ict_scalp.py` (mirroring `backtest_pullback.py`), plus a
   `scalp` family registered in `m20_fleet_exit_sweep.py`
   (classify + `FAMILY_HARNESS` + a `base_args` branch). Unit tests in
   `tests/test_ict_scalp_exit_levers.py`.
2. **Compute — DONE for MGC (§ P2).** The config-exact IS/OOS sweep ran on the
   committed XAU proxy → **all cells honest_negative**. (The fleet infra's
   `GC_F` proxy-file naming can't reach the deep `XAUUSD_15m_deep.csv`, so the
   dedicated `scripts/research/m27/ict_scalp_exit_sweep.py` driver was used for
   the MGC run; a yearly walk-forward was unnecessary since nothing passed the
   IS/OOS pre-filter.)
3. **Tier-3 — NOT reached.** No lever passed, so `ict_scalp.monitor()` is left
   unchanged (still break-even-after-1R only). This matches the M20 prior:
   trend-oriented time/giveback exits don't help a mean-reverting fixed-bracket
   scalp.

**Remaining (open under the backlog item):** the OTHER `ict_scalp` legs
(`ict_scalp_5m` BTC + `sol/xrp/avax/eth` 5m/15m) are now lever-capable in the
fleet infra and should get their own config-exact sweeps **on the trainer VM**,
where their native data lives (`m20_fleet_exit_sweep.py` resolves e.g.
`SOLUSDT_5m.csv` there; this sandbox only carries the XAU proxy, which is why
the `--list` above showed them `data_missing`). `exit_ladder` (partial-TP
banking) is still un-built and fleet-wide parked; `exit_head_ml` stays blocked
on native history.

## Item 2 — real-money `ib_live` route (the other PR #7848 follow-up)

**Blocked on the paper soak, by design.** The wiring PR shipped `ib_paper`
(paper money) precisely because MGC's own 15m data is structurally too thin to
backtest-promote to real money (the powered evidence is the spot-XAU proxy, and
the native Arm-B read leans net-negative). The real-money decision is a separate
later Tier-3 gate that needs a genuine `ib_paper` soak first — there is zero
soak data yet, so this item is correctly not actionable now.
