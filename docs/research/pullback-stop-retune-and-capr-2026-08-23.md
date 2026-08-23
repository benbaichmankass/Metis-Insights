# Retuning the stop beats refusing the trade — crypto pullback family, 2026-08-23

**Operator directive that produced this run** (2026-08-23): *"this sounds like it
also needs to be retuned before we can make a decision — we shouldn't just reject
trades instead of actually enhancing the strategy mechanisms."*

The prior session had proposed refusing any signal whose `cap_r < 1` — i.e. where
the venue's 9.9% take-profit ceiling sits closer to entry than the volatility-scaled
stop, so no bracket exists whose upside exceeds its downside. That is a rejection
rule bolted onto unchanged geometry. This run tests the alternative: move the
geometry.

## The arithmetic being exploited

```
cap_r = (tp_cap_pct × entry) / risk        risk = atr_stop_mult × ATR
```

`cap_r` is the R:R the venue ceiling permits. It is inversely proportional to
`atr_stop_mult`, so the stop multiplier is a direct lever on how often a coherent
bracket exists — a lever nobody had pulled, because `atr_stop_mult` had never been
swept on these legs (only `tp_r` had, earlier the same day).

## ALWAYS STATE THE POPULATION

Backtest trades, not live rows. `scripts/backtest_pullback.py` over the 2h corpora
in `/tmp/corpora/` (13,212 bars per symbol), net-of-cost at 7.5 bps round-trip,
single-split walk-forward with `--oos-start 2026-01-01`. Every other parameter is
pinned to the leg's **live** `config/strategies.yaml` block. `n` below is emitted
trades over the FULL sample; the net_R figures are OOS.

## Measurement

| leg / `atr_stop_mult` | n | `cap_r < 1` | median `cap_r` | min | OOS net_R | train net_R |
|---|---|---|---|---|---|---|
| ADA 2.5 (live) | 153 | **18 (11.8%)** | 2.00 | 0.39 | +5.088 | −3.092 |
| ADA 1.5 (proposed) | 172 | **3 (1.7%)** | 3.34 | 0.65 | **+9.143** | **+2.962** |
| XRP 2.5 (live, and its own optimum) | 182 | 19 (10.4%) | 2.40 | 0.44 | +4.379 | +2.023 |

**ADA:** tightening the stop removes **~85% of the incoherent brackets** and is
*simultaneously* the more profitable setting — OOS net_R nearly doubles and the
train half flips sign. The 18 refused-by-the-rejection-rule trades are not
discarded; they become tradeable. maxDD rises 6.261 → 8.508, which is the cost.
`beats_baseline: True`, `train_oos_consistent: True`, `action: propose_value`.

**XRP:** the sweep returns `hold_current` — 2.5 IS the optimum and every tightening
loses money (1.5 → OOS −2.790, 2.0 → −1.736). So XRP's 10.4% residual is **not**
repaired by this axis, and this document does not claim otherwise. What it rules out
is the general form of the rejection argument: the incoherence is a *tunable*
property of the geometry, not an inherent property of the venue ceiling.

## Two defects this run exposed in the sweep tooling itself

1. **`strategy_tune_result/v1` did not record the candle file.** It recorded
   `fixed_args` — which carries `--symbol`, a **label** — and nothing else about the
   input. Re-running a leg without `--data` silently fell back to the harness's own
   `BACKTEST_DATA_PATH`: every OOS row came back **0 trades** and the tool published
   `action: no_profitable_value` for `xrp_pullback_2h`, the one leg whose sweep had
   actually shipped a value hours earlier. Diagnostic-provenance **sub-class B**
   (implicit input selection). Fixed — results now carry a `data` block with three
   never-collapsed states: `explicit` · `harness_default` (*we did not look*) ·
   `unreadable`. The fix was validated by re-running with `--data` and confirming the
   2.500 row reproduces the recorded baseline **to the last digit** (OOS 36 /
   2.159 / 0.060 / 6.041, train 1.154 / 0.008).

2. **`beats_baseline: False` without `--current-value` means "we did not look".**
   The first pass omitted it, so `baseline_row` was `null` on all six legs and every
   row read `beats_baseline: False` — indistinguishable from a real comparison that
   failed. Re-run with the baseline pinned; ADA then reads `True` and XRP reads
   `hold_current`.

Both are the same class as the finding they were chasing: a confident label over a
quantity the code did not compute.

## Disposition

- `ada_pullback_2h::atr_stop_mult 2.5 → 1.5` — **Tier-3, proposed, not applied.**
- `xrp_pullback_2h::atr_stop_mult` — hold 2.5 (its own optimum, confirmed).
- The `cap_r < 1` rejection rule is **not** built. It was the wrong shape of fix.
