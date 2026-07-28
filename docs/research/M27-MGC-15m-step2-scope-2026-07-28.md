# M27 — MGC native-15m ict_scalp study, STEP 2 SCOPE (2026-07-28)

> **Status: SCOPE — the executable plan for step 2, pending step-1's data survey
> (trainer relay #7801).** Operator-directed re-target of the venue-blocked
> XAUUSD 15m winner onto IBKR micro-gold futures (MGC), which — unlike XAUUSD's
> shelved OANDA venue — has a **live-tradeable account** (`ib_paper`, the same
> box `mgc_trend_1h` / `mgc_pullback_1d` already run on). No Tier-3 here; this is
> the research plan.

## What step 2 must answer

The XAUUSD 15m study (`M27-P0-batch4-xauusd-findings-2026-07-21.md`) already
proved the `ict_scalp` **price-action edge on clean 15m gold**: ungated k-fold
4/4 folds positive, **+44.35R net** over 240 OOS trades (2020–2026), at a 2.0 bps
cost. That result is on **Dukascopy spot XAU/USD** — deep (178k bars), keyless,
clean. The open question step 2 must answer is narrow:

**Does that validated gold-15m edge survive MGC's *futures* economics
(flat per-contract commission + tick slippage, contract multiplier), and does it
show up on the real MGC instrument's data?**

## Why not just backtest MGC's own bars (the powered-data problem)

Batch-2 (`M27-P0-batch2-futures-findings` + its gap diagnostic) established that
the MGC IBKR series is **not a powered dataset**:

- Only **~1 year** of native IBKR history (`2025-07-29 → 2026-07-20`, ~59k 5m bars).
- **50.8% flat-bar contaminated** at 5m (72% of 20-bar windows), because the pull
  used `useRTH=False` (24h Globex; the micro-gold overnight tape is thin — a
  rolling-window vol calc over a flat run is mathematically 0, which is why the
  frozen 5m vol terciles came back degenerate `q33=0.0`).
- Fired only **~14 trades/yr** at 5m — noise, not evidence (2–4 trades/fold).

A native-15m MGC backtest on that ~1yr series will fire **even fewer** trades
(coarser bars → fewer setups) and inherits the overnight-thinness contamination.
On its own it **cannot reach statistical power** — it would reproduce Batch-2's
"no verdict, underpowered" outcome. So MGC's own bars are a **tradability
cross-check**, not the powered evidence.

## The design: two complementary arms

### Arm A — powered re-cost (PRIMARY, runs now)

Re-cost the **existing** XAUUSD Dukascopy 15m result under **MGC's per-contract
futures cost model**. The `ict_scalp` decision + exit geometry is **level/scale-
invariant** (FVG size in bps, sweep, ATR-relative stops, R-multiple outcomes), so
gold **spot** 15m is a valid price-action proxy for the **MGC underlying** — the
two track ~1:1 modulo a near-constant cost-of-carry basis that does not affect a
scale-invariant setup. Only the **cost model** changes: bps-of-price → flat USD
per contract.

- **Reuses** the committed trainer artifact `/home/ubuntu/m27_out_xau/XAUUSD/emit.json`
  (the per-trade emit stream from the XAUUSD backtest) — **no re-fetch, no
  re-backtest.** Just re-run the k-fold scorer with the futures cost flags.
- **Cost model (MGC, from Batch-2's venue-correct per-contract model):**
  `--fee-usd-roundtrip 3.0 --contract-value-usd 10.0` (≈ $1 commission + ~1 tick/
  side slippage; MGC = 10 troy oz, 1.0 price point = $10). This charges a flat
  USD cost against each trade's own **dollar** risk (`risk_points × 10.0`), not
  bps — the correct futures accounting.

```bash
# Arm A — on the trainer, reusing the existing XAUUSD artifacts.
# kfold_oos.py consumes a PRE-DERIVED volspec (--volspec-15m); it has no
# --derive-window (that flag lives on the volspec-derivation step). The
# XAUUSD run already froze volspec_15m.json, so Arm A is a single re-score:
python scripts/research/ict_scalp_phase0/kfold_oos.py \
    --emit       /home/ubuntu/m27_out_xau/XAUUSD/emit.json \
    --data       <xauusd_15m.csv> \
    --volspec-15m /home/ubuntu/m27_out_xau/XAUUSD/volspec_15m.json \
    --folds 4 \
    --fee-usd-roundtrip 3.0 --contract-value-usd 10.0 \
    --out /tmp/mgc_armA_kfold.json
```

**Expectation:** the edge should survive comfortably — Batch-2 found the futures
fee-load in R is **much smaller** than crypto/bps (~0.02–0.08R vs 0.20R), because
futures stops are wide in dollar terms and the commission is flat. If +44.35R net
at 2 bps holds, it should hold (likely stronger in R) under the MGC dollar model.

### Arm B — real-MGC tradability cross-check (SECONDARY, data-gated)

Run the same rig on the **actual IBKR MGC native-15m bars** to confirm the edge
appears on the real instrument — accepting it is **underpowered** (~1yr, few
trades). This is a directional sanity/tradability check, not the gate.

- **Data source (decided by step-1 relay #7801):**
  1. If Batch-2's native MGC **15m** bars exist at `/home/ubuntu/m27_out_fut/MGC/`
     → resample-free direct use, **after a flat-bar filter** (drop runs of ≥5
     byte-identical closes; the diagnostic's own `diagnose_futures_gaps.py`
     defines the run).
  2. If only 5m exists → resample 5m→15m then flat-bar filter (15m coarsening
     alone materially cuts the contamination the diagnostic measured at 5m).
  3. If neither is clean enough → note that a clean **RTH-only re-pull**
     (`useRTH=True`, a live-VM `pull-ibkr-history` action — NOT trainer) is the
     path to a genuinely clean MGC series, and defer Arm B to that pull.

```bash
# Arm B — once step-1 confirms the MGC 15m CSV path (flat-bar-filtered).
# Three stages (mirroring the XAUUSD rig): derive the MGC 15m volspec from the
# data prefix, run the harness to emit trades, then k-fold with the pre-derived
# volspec. The --derive-window lives on the volspec-derivation step, NOT kfold.
python scripts/research/m27/run_symbol_p0.py \
    --symbol MGC --timeframe 15m \
    --data <mgc_ibkr_15m_flatfiltered.csv> \
    --derive-window prefix:0.2 --emit-out /tmp/mgc_emit.jsonl \
    --volspec-out /tmp/mgc_volspec_15m.json
python scripts/research/ict_scalp_phase0/kfold_oos.py \
    --emit /tmp/mgc_emit.jsonl \
    --data <mgc_ibkr_15m_flatfiltered.csv> \
    --volspec-15m /tmp/mgc_volspec_15m.json \
    --folds 4 \
    --fee-usd-roundtrip 3.0 --contract-value-usd 10.0 \
    --out /tmp/mgc_armB_kfold.json
```
(If `run_symbol_p0.py`'s flags differ on the trainer, the equivalent two-step is
`backtest_ict_scalp.py --timeframe 15m --symbol MGC --data … --stamp-regime
--sim-breakeven` to emit + a volspec derivation, then the same kfold call — the
invariant is: **kfold always takes `--emit/--data/--volspec-15m` + the cost
flags, never a positional or `--derive-window`.**)

Use the **15m vol label** for any regime cut (Batch-2: the 5m futures vol label
is degenerate; 15m is healthy). XAUUSD passed **ungated baseline** anyway, so the
gate is not load-bearing here — baseline is the cell that matters.

## Pass criteria (the gate)

Same bar the 5m alt legs + XAUUSD were held to: **net expectancy > 0 with ≥ 3/4
anchored-walk-forward OOS folds positive**, on the **ungated baseline** cell,
net of the MGC per-contract cost.

## Decision matrix

| Arm A (powered re-cost) | Arm B (real MGC) | Verdict |
|---|---|---|
| PASS (net>0, ≥3/4 folds) | consistent / directionally +ve | **MGC 15m is a promotable leg** → `new-strategy` shadow-soak packet, route to IBKR `ib_paper` (a LIVE venue). Tier-3, operator-gated. |
| PASS | too-thin / n≈0 | Promote to a **paper soak first** on `ib_paper` (gather real MGC fills), decide real-money after. |
| PASS | contradicts (net<0 on real data) | Hold — the basis/instrument difference matters; investigate before any wiring. |
| FAIL under MGC economics | — | No leg (unlikely given the small futures fee-load). |

## Contract / routing facts (for the eventual leg)

- **Instrument:** MGC (Micro Gold Futures, CME), 10 troy oz, tick 0.10 = $1.00,
  1.0 point = $10 → `contract_value_usd = 10.0`.
- **Venue:** IBKR `ib_paper` — **live and reachable** (already runs
  `mgc_trend_1h` / `mgc_pullback_1d`). This is the whole point of the re-target:
  it removes XAUUSD's venue block.
- **Sizing:** futures size in **whole contracts** (`RiskManager` whole-contract
  rule for `market_type: futures`) — a sub-1-contract size is a per-trade refusal,
  so the leg's effective min risk is one MGC contract. Worth noting for the
  account's risk_pct sizing at promotion time.

## Sequencing

1. **Arm A now** — dispatch the re-cost k-fold on the trainer (reuses XAUUSD
   `emit.json`; cheap, single run). This is the powered answer.
2. **Arm B** — scoped by step-1 relay #7801's data survey; run once the MGC 15m
   CSV path + flat-bar filter are settled.
3. Land both in an M27 findings doc (`M27-P0-MGC-15m-findings-*.md`) + the
   research ledger; if the gate passes, a separate Tier-3 `new-strategy` proposal
   for the operator (routed to `ib_paper`).

**No Tier-3 in this study** — it is the research verdict step. Wiring a leg is a
later, separately-approved step.
