# M27 P0 — Batch-1 crypto cross-symbol transfer findings (2026-07-20)

**Question:** does the proven BTC ict_scalp_5m pipeline — config-exact backtest,
gross→net fees (7.5 bps round-trip taker ≈ 0.20R/trade at scalp stop widths),
decision-time regime stamps against frozen vol edges, per-(trend,vol) OFF-cell
gate, anchored 4-fold k-fold OOS — transfer to the other five Bybit linear
symbols we trade?

**Answer: mostly yes, and the regime gate is symbol-specific.** Four of five
symbols clear the P0 gate ("net gated expectancy > 0 with ≥3/4 folds
positive") in some configuration; the *shape* of the winning configuration
differs per symbol, which is exactly why P4 requires per-symbol cells authored
from the symbol's own k-fold evidence, never a copy of BTC's.

## Rig (attempt 3 — the run of record)

- Data: Bybit linear 5m klines, 2023-01-01 → 2026-07-20 (~373.5k bars/symbol),
  pulled trainer-side by `scripts/research/m27/fetch_bybit_5m.py`; timestamps
  tz-aware `+00:00` (the attempt-2 `merge_asof` aware-vs-naive failure, fixed
  in PR #7199; the five CSVs were sed-fixed in place, #7198).
- Pipeline: `scripts/research/m27/run_symbol_p0.py` (merged #7182) — derives
  per-symbol FROZEN tercile vol edges from the **2023 calendar year only**
  (5m + 15m, pstdev w=20 — the exact live function; no resolution-time leak),
  then `backtest_ict_scalp.py --stamp-regime --sim-breakeven` (live-exit-
  faithful), then `kfold_oos.py --folds 4 --fee-bps-roundtrip 7.5`.
- Raw artifacts: trainer `/home/ubuntu/m27_out/<SYMBOL>/{volspec_5m,volspec_15m,
  backtest,emit,kfold}.json`; full fold-level JSONs mirrored in relay issues
  #7211 (ETH/SOL/XRP) and #7212 (ADA), AVAX below. Driver log:
  `/home/ubuntu/m27_out/driver.log`.

## Gross results (full period, fee-free, R units)

| Symbol | Trades | Win % | ExpR | TotR | MaxDD-R |
|---|---|---|---|---|---|
| ETHUSDT | 765 | 50.46 | 0.144 | 110.1 | 10.5 |
| SOLUSDT | 915 | 52.02 | 0.145 | 132.6 | 12.2 |
| XRPUSDT | 748 | 52.81 | 0.174 | 130.1 | 8.2 |
| ADAUSDT | 1033 | 52.57 | 0.139 | 144.0 | 16.0 |
| AVAXUSDT | 1102 | 54.26 | 0.173 | 190.2 | 11.1 |

Gross expectancy is positive everywhere — consistent with the BTC finding that
the raw edge exists and **fees are the binding constraint**. Everything below
is net of 7.5 bps round-trip.

## k-fold OOS aggregates (NET, anchored 4-fold)

`folds+` = folds with positive net total R (gate needs ≥3/4 plus positive
net expectancy on the gated subset).

| Symbol | baseline | off_cells_5m | calm_only_15m | off+conf070 | Verdict |
|---|---|---|---|---|---|
| ETHUSDT | 3/4, +20.4R, exp .031 | **3/4, +12.1R, exp .059** | 2/4, −4.1R | 2/4, −3.5R | **PASS** (off-cells) — weakest passer; fold 1 barely positive, fold 4 negative |
| SOLUSDT | 4/4, +62.7R, exp .084 | **4/4, +37.7R, exp .112** | 3/4, +16.5R | 2/4, +11.9R | **STRONG PASS** — positive in every fold even ungated |
| XRPUSDT | 2/4, +31.5R, exp .052 | **4/4, +34.3R, exp .144** | 4/4, +19.8R, exp .217 | **4/4, +21.1R, exp .255** | **PASS — gate load-bearing**: ungated fails the fold criterion; every gated variant is 4/4 |
| ADAUSDT | **3/4, +55.1R, exp .064** | 2/4, +3.9R, exp .014 | 2/4, −0.2R | 1/4, −9.2R | **MIXED** — passes UNGATED; the BTC-shape gate destroys the edge. No leg until own-evidence cells (or no gate) are validated |
| AVAXUSDT | **4/4, +60.8R, exp .065** | 2/4, +20.9R, exp .063 | 4/4, +6.4R, exp .074 | **4/4, +19.2R, exp .160** | **PASS** — ungated baseline 4/4 AND off+conf070 4/4; plain off-cells fails the fold criterion (folds 3-4 ≈ 0) |

## What transfers and what doesn't

1. **The raw setup logic transfers.** Positive gross expectancy on all
   completed symbols, win rates ~50–53%, similar win/loss geometry
   (avg win ~0.9R, avg loss ~−0.7R). The FVG+sweep scalp is not a BTC
   idiosyncrasy.
2. **The fee constraint transfers.** Net baseline expectancy lands at
   0.03–0.08R/trade vs 0.14–0.17R gross — the same ~0.20R/trade fee load
   dominates every symbol. The P2 maker-entry study stays the highest-leverage
   cost lever milestone-wide.
3. **The regime gate does NOT transfer as a fixed rule.** It is load-bearing
   for XRP (2/4 → 4/4), additive for SOL/ETH (higher expectancy, fewer
   trades), and value-destroying for ADA (3/4 → 2/4, +55R → +4R). Per-symbol
   cells must be authored from the symbol's own fold table (the P4 rule
   already says this; ADA is the proof it's necessary, not ceremony).
4. **Fitted-confidence remains fragile.** `fitted_conf_oos` is inconsistent
   across symbols (ETH −9.2R, SOL +40.6R, ADA +15.1R) — same conclusion as
   the BTC Phase-0 rejection of fitted min_confidence; only the fixed-grid
   variants are candidate rules.

## Recommended next steps (no Tier-3 tonight — proposals only)

- **SOL and XRP are the strongest P4 candidates** (alt-variant legs
  `ict_scalp_sol_5m` / `ict_scalp_xrp_5m`, shadow-first, cells from their own
  fold tables; XRP's off+conf070 at exp .255 on 83 trades is the single best
  cell-rule in the batch). Each needs the prop account-compat matrix + the
  new-strategy flow before any YAML lands.
- **ETH is a marginal include** — passes, but fold-4 (most recent regime) is
  negative under the gate; consider holding for the P1 15m sweep before
  proposing a leg.
- **ADA: no leg yet.** Re-run the cell authoring on ADA's own per-cell table
  (P1 scope) before deciding; ungated-baseline-only would be a different risk
  profile than every other leg and needs operator discussion.
- Batch 2 (MES/MGC/MHG via IBKR history pulls, session-aware, per-contract
  cost model) is the next data dispatch.

## AVAXUSDT (appended on completion, 23:33Z — relay #7219)

Chain finished `ALL_DONE 23:27Z`. AVAX is the best gross performer of the
batch (1102 trades, 54.26% win, 0.173 expR, +190.2R, maxDD 11.1R) and net
OOS its **ungated baseline is positive in all four folds** (+60.8R, exp
.065) — like ADA, the plain BTC-shape off-cells rule does NOT transfer
(2/4; folds 3–4 ≈ flat), but unlike ADA a gated variant does:
**off_cells+conf070 is 4/4 (+19.2R, exp .160, n=120)** and calm_only_15m is
4/4 weakly (+6.4R, exp .074). Verdict: **PASS** — candidate leg with cells
authored from its own fold table (the conf-qualified off-cell rule, or
ungated with the wider maxDD profile as an operator choice).

Batch-1 closing tally: **4 of 5 symbols PASS** (SOL, XRP, AVAX, ETH — in
descending strength), ADA mixed (ungated-only). The per-symbol divergence in
WHICH rule passes is the batch's central finding — it confirms the P4
"cells from own evidence" requirement is load-bearing, not ceremony.
