# Crypto-book per-leg edge audit (2026-07-30)

**Operator directive:** the whole crypto book is red at every scale (real
`bybit_2` −$30/14d, paper mirror `bybit_portfolio` −$12.6k/30d). Diagnose each
live crypto leg — real edge vs overfit, and *why* it's losing now — **before**
any demote (the `drift-remediation` / performance-review "diagnose-don't-demote"
rule). Method: 2-yr walk-forward, **exact live params**, net-of-fee, contiguous
OOS time-folds (`backtest_*.py --emit-trades` → `direction_walkforward.py`).

## Per-leg verdicts

| leg | 2yr net-R (net-of-fee) | shape | verdict |
|---|--:|---|---|
| `eth_pullback_2h` | **+18.43** | long +8.86 / short +9.58; folds 6–8 (recent) adverse | **KEEP** — real edge, adverse regime; regime-tune candidate |
| `xrp_pullback_2h` | **+31.82** | long +9.30 / short +22.52 | **KEEP** — real edge |
| `sol_pullback_2h` | **−5.43** | **long −12.39** / short +6.96 | **FINE-TUNE the LONG side** — short is fine; SOL-long is the single genuine negative (single-symbol effect, not a family property) |
| `trend_donchian` (BTC 1h) | **+1.12** (base geometry) | long-only; **2025 +9.6 / 2026 −9.3**; folds 6–8 adverse | **WATCH / regime-tune** — thin base edge, but the live **ML exit head** (`exit-head-donchian-1h-v1`, M20-shipped precisely because this leg's base exits are bad) lifts the live version above this number. Not a demote; regime-gate the adverse 2026 slice. |
| `ict_scalp_5m` (BTC) | **+26.63** (1yr) | 187 tr, 49% win; **stable_edge=TRUE**; short +18.8 / long +7.8; folds 4/5/6 (recent 2026) all positive | **KEEP** — strong, stable, *currently*-positive backtest edge. The live loss (mirror −$4k) is the **cleanest research→results-gap case**: execution (the bracket bug, fixed today) + **slippage** the harness doesn't model ("fills at the level"). Validates the exit fix; measure slippage next. |
| `trend_donchian_xrp_4h` | **+5.29** | short **+7.34** / long **−2.05** (stable_drag on the long side, 4/6 folds); 2026 −1.6 | **FINE-TUNE the LONG side** — short carries it; long is a persistent drag (same shape as sol_pullback) |

Source WFs: pullback family — `pullback-2h-direction-walkforward-2026-07-29.md`;
trend_donchian — trainer relay #7957 (this session); ML-exit-head context —
`exit-refinement-coverage.json` + M20 exit-head rounds.

## Cross-cutting read (the important part)

- **No leg is genuinely dead (all 6 audited).** ict_scalp / eth / xrp pullback
  have strong durable edge; trend_donchian is thin-but-regime-driven with a
  value-adding exit head; sol_pullback and trend_donchian_xrp_4h are one-sided
  (**long-side**) problems. **The reflexive "demote the red strategies" would
  have been wrong on all six.**
- **Cross-cutting pattern — the alt-crypto LONG side is a persistent drag.** Both
  `sol_pullback_2h` (long −12.4R) and `trend_donchian_xrp_4h` (long −2.05R,
  stable_drag 4/6 folds) lose on longs while their shorts carry them — a real,
  actionable regime signature (bearish alt tape), not noise. A short-bias /
  long-gate variant is the fine-tune to test (leg-specific: the eth/xrp
  *pullback-2h* WF refuted a directional gate for those, so this is not a
  blanket rule).
- **The bleed is dominated by (a) an adverse recent regime** hitting legs that
  are net-positive across history, **and (b) the structural cost/execution gaps**
  in `research-to-results-gap-2026-07-30.md` (net-of-fee-only gate, no
  funding/slippage, the now-fixed bracket bug) — NOT by a roster of dead
  strategies. This is why the fix is regime-tuning + the cost-model/gate rebuild,
  not a demotion sweep.

## Measured cost decomposition (2026-07-30, relay #7961) — slippage is NOT the culprit

Entry slippage (intended signal price vs actual fill, bps, 45d, crypto book):
**small and often favorable.** `ict_scalp_5m` bybit_2 (n=13): **abs 3.45 bps,
adverse −0.95** (fills slightly *better* than intended); sol_5m −3.4 adverse,
xrp_5m −1.6, eth_pullback −1.07. Worst was `ada_pullback_2h` bybit_2 at +7.16
adverse (not a keeper leg). The backtest's "fills at the level" assumption is
**roughly correct for entries** — entry slippage does not explain the live loss.

**So the scalp's research→results gap is dominated by EXECUTION** (the
`BYBIT_TPSL_MODE=full` bracket bug: trades held 6–14h giving back MFE), **not by
slippage or funding** (funding near-zero per broker-truth; the M24 funding-
visibility gap remains but is second-order for this tiny account). This is the
value of *measuring* the hypothesised cost rather than assuming it: the big lever
was the exit fix (deployed today), not a cost-model rebuild. The cost-model /
net-of-full-cost gate is still worth building as a **methodology** gate (it closes
the structural hole for future legs + models exit-side slippage, which this
entry-only measure doesn't cover), but it is not recovering large measured
dollars on the current book.

## Next

1. ~~Finish `ict_scalp_5m` + `trend_donchian_xrp_4h` WFs.~~ **DONE — all 6 legs audited.**
2. The structural build (research-to-results-gap plan): funding+slippage into the
   crypto harnesses → re-gate net-of-full-cost → adopt the paper-mirror-net-positive
   retention gate.
3. Regime-tune proposals (Tier-3, to operator) for the legs whose only problem is
   the adverse regime slice: a vol/chop guard on eth_pullback + trend_donchian;
   the SOL-long fix on sol_pullback.
