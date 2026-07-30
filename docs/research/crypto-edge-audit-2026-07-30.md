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
| `ict_scalp_5m` (BTC) | *pending* (harness re-run) | — | pending; M27 backtest showed net-of-fee edge, live leak was the now-fixed bracket bug |
| `trend_donchian_xrp_4h` | *not yet run* | — | pending |

Source WFs: pullback family — `pullback-2h-direction-walkforward-2026-07-29.md`;
trend_donchian — trainer relay #7957 (this session); ML-exit-head context —
`exit-refinement-coverage.json` + M20 exit-head rounds.

## Cross-cutting read (the important part)

- **No leg audited so far is genuinely dead.** eth/xrp pullback have strong
  durable edge; sol-pullback is a one-sided (long) problem; trend_donchian is
  thin-but-regime-driven with a value-adding exit head. **The reflexive "demote
  the red strategies" would have been wrong on every one.**
- **The bleed is dominated by (a) an adverse recent regime** hitting legs that
  are net-positive across history, **and (b) the structural cost/execution gaps**
  in `research-to-results-gap-2026-07-30.md` (net-of-fee-only gate, no
  funding/slippage, the now-fixed bracket bug) — NOT by a roster of dead
  strategies. This is why the fix is regime-tuning + the cost-model/gate rebuild,
  not a demotion sweep.

## Next

1. Finish `ict_scalp_5m` + `trend_donchian_xrp_4h` WFs.
2. The structural build (research-to-results-gap plan): funding+slippage into the
   crypto harnesses → re-gate net-of-full-cost → adopt the paper-mirror-net-positive
   retention gate.
3. Regime-tune proposals (Tier-3, to operator) for the legs whose only problem is
   the adverse regime slice: a vol/chop guard on eth_pullback + trend_donchian;
   the SOL-long fix on sol_pullback.
