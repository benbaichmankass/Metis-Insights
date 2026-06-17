# Prop-firm evaluation — EXPANDED roster (+ict_scalp_5m, +turtle_soup), 2026-06-16

This is the **expanded** re-run of `runtime_logs/prop_eval/2026-06-16/` after
adding the high-frequency `ict_scalp_5m` (5m-native) and `turtle_soup` (15m)
members to the portfolio-engine combo search. The earlier 4-strategy run
concluded that the roster was **too low-frequency** to pass Breakout 1-Step
"fast" (median days-to-pass ≤ ~60) while keeping P(survive 6mo) ≥ 95%, and that
"a genuinely fast pass would need a higher-frequency strategy." This run tests
exactly that hypothesis with the densest signal source in the live roster.

The original 4-strategy artifacts under `2026-06-16/` are **untouched**; this
directory is a separate, additive run so nothing is clobbered.

## What changed

- `scripts/prop/evaluate_prop.py::DEFAULT_COMBO_ROSTER` and
  `scripts/prop/montecarlo_prop.py::{DEFAULT_ROSTER_MEMBERS,DEFAULT_COMBOS}`
  now include `ict_scalp_5m` (+ `turtle_soup`). Both strategies were **already
  registered in `scripts/backtest_system.py::ROSTER`** with the engine's
  `order_package(cfg, candles_df)` + `monitor(cfg, candles_df, open_pkg)`
  contract; the engine even special-cases `ict_scalp_5m`'s 1h-EMA HTF-bias
  injection in `generate_signal_stream`. The combo-search scripts had simply
  gated them out via hardcoded member lists. **No engine code change** was
  needed — wiring them in was a one-line roster edit each, exactly as the
  `backtest_system` COVERAGE note predicted.

## Wired in cleanly?

**Yes, both.** Signal-stream generation succeeded for both on the full
2023-01→2026-02 5m feed:

- `ict_scalp_5m` — **887 signals** generated (~20 min, cached). Solo through the
  portfolio engine: **474 closed trades** — by far the densest ledger (vs ~50
  for squeeze, ~100-200 for the others). The frequency hypothesis is satisfied.
- `turtle_soup` — **754 signals** generated (~21 min, cached). Single-TF live
  adapter (15m setup frame; its legacy 1m-entry confirmation is not in the
  `order_package` path), fits the single-clock engine fine.

Both used the engine's existing per-strategy `order_package`/`monitor` calls —
nothing was hacked.

## Feed / ruleset / method

Same as the base run: `/home/user/ict-trader-data/btc_5m.parquet`
(2023-01-01 → 2026-02-28, 332,624 5m bars), `$5,000` account,
`config/prop_rulesets/breakout.yaml` (1-Step Classic: +10% target, 3% daily
loss, **6% STATIC off-start DD**, 30-day funded soak), `--clock-tf 1h`,
`flip_policy hold`, `reentry suppress`. Matrix at risk_pct 0.3; Monte-Carlo
(block-bootstrap, 5000 paths, block_len 8, seed 1234) over risk_pct
{0.3, 0.5, 0.6, 0.75, 1.0}, base-risk 0.5, horizons 3/6/12mo.

## THE ANSWER — does adding ict_scalp_5m unlock a fast + safe pass?

**No. ict_scalp_5m HURTS — it is the opposite of the fix.** No combo containing
ict_scalp_5m meets `median days-to-pass ≤ 60 AND P(survive 6mo) ≥ 95%`. Not
close: **every ict_scalp-bearing cell has P(breach) = 100% and median
end-return ≈ −6.3%** (the static-DD floor). ict_scalp fires fast and *can* mark
+10% in ~13–50 days at higher risk, but its edge on this feed is **negative**,
so the account almost always breaches first — P(pass) is 0–7% and
P(survive 6mo) ≤ 2.8% for every ict_scalp cell (vs ≥ 95% for the clean
non-ict_scalp combos).

### The root cause is a real negative edge, NOT a wiring bug (verified)

`scripts/backtest_system.py --roster ict_scalp_5m --risk-pct 0.5` solo:

```
bal 5000 -> 357   net=$-4643 (-92.85%)   maxDD 93.0%
trades=474  WR=36.92%  exits={'tp':148,'sl':324,'flip':2}
```

474 trades, **36.9% win rate**, 148 TP vs **324 SL**. With a 1.5R take-profit
the break-even win rate (pre-fees) is ~40%; ict_scalp's sweep→displacement→FVG
→mitigation setup resolves to mostly stops on the 2023–2026 BTC feed when run
through the engine's next-bar-open fill + intrabar-SL-first model. The wiring is
correct (signals, SL/TP, monitor all fire); the *strategy* loses money here.

### ict_scalp POISONS the previously-clean winners

The single combo that cleanly passed in the base run —
`squeeze_breakout_4h,fvg_range_15m` (pass day 673, off-start DD 0.2%, funded
survive, +$504) — collapses to a **−$3,551 breach (off-start DD 71.7%, first
breach 2023-10-03)** the moment ict_scalp_5m is added (matrix rank 24). Because
the engine NETS all members into ONE shared BTCUSDT position, ict_scalp's
high-frequency losing entries dominate the shared book and bleed the account
straight through the 6% floor. Adding it to ANY combo drags off-start DD from
< 8% to **18–78%**.

### turtle_soup: also a net loser, but tame (doesn't breach as violently)

`turtle_soup` alone: P(pass) 0–1%, median end-return ≈ −6.3%, but it's the
*least* destructive new member — at risk 0.3 it still shows P(surv 6mo) 49% /
12mo 13% (it trades rarely enough that the static-DD floor isn't hit on most
paths). It does not pass either; it just doesn't actively poison combos the way
ict_scalp does. Not a fix.

### The fast/safe frontier is UNCHANGED from the base run

The non-ict_scalp combos behave identically to the base run (same cached
ledgers). The best survival-weighted cells remain:

| combo | risk | P(pass) | days→pass (med) | P(surv 6mo) | P(surv 12mo) | end ret (med) |
|---|---|---|---|---|---|---|
| `fvg_range_15m` | 0.75 | 90% | 315 | 98.5% | 97% | +22.4% |
| `fvg_range_15m` | 1.0 | 90% | 249 | 96.2% | 93.7% | +30.0% |
| `squeeze_breakout_4h,fvg_range_15m` | 0.6 | 64% | 327 | 99.5% | 95.2% | +11.0% |

Still **no cell with median ≤ 60 days AND P(surv 6mo) ≥ 95%.** The fastest
durable pass remains `fvg_range_15m @ 1.0` at ~249 days (96% 6mo survival) —
~4× the 60-day "fast" bar. The conclusion of the base run **stands and is
strengthened**: speed at acceptable survival is not available from this roster,
and the obvious high-frequency candidate (ict_scalp_5m) makes it strictly
worse, not better, because its edge on this feed is negative.

### How far off "fast + safe" is the best ict_scalp cell?

Closest ict_scalp cell to "fast" is `ict_scalp_5m @ 0.5` (median 31 days) — but
its P(survive 6mo) is **0.58%** (needs ≥ 95%) and P(pass) ≈ 0%. So it misses the
survival bar by ~94 percentage points. There is no sizing of any ict_scalp
combo that closes that gap: lowering risk only delays the same near-certain
breach; raising risk passes a few more transient marks but breaches faster.

## Honest caveats (carried from the base run)

1. **Daily-loss is OPTIMISTIC** (realised-only per-trade buckets, no intraday
   open-position swing) — so the already-100% ict_scalp breach rates are if
   anything *under*-stated. This only strengthens the negative finding.
2. **Bootstrap reuses the historical trade distribution** — a different BTC
   regime could change the numbers. But ict_scalp's losing edge is structural
   (37% WR at 1.5R), not a single bad draw.
3. **Backtest ≠ funded reality** — slippage/fills/funding/Breakout's exact
   equity accounting differ. This is a relative, probabilistic filter.
4. Ledgers generated at base risk_pct 0.5, rescaled per cell via
   sizing-independent R-multiples (one engine run per combo, seed 1234, 5000
   paths each). Signal streams cached under
   `runtime_logs/system_backtest/signals/`.

## Bottom line

> **Adding ict_scalp_5m does NOT unlock a fast+safe Breakout pass — it removes
> the only clean pass that existed.** ict_scalp_5m's frequency is real
> (887 signals / 474 engine trades) but its edge on the 2023–2026 BTC feed is
> strongly negative (37% WR @ 1.5R → −93% solo), so every combo it touches
> breaches with ~100% probability. turtle_soup is also a net loser (tamer, but
> no pass). The recommendation is unchanged from the base run:
> **`squeeze_breakout_4h,fvg_range_15m` or `fvg_range_15m` at risk 0.6–1.0** —
> slow (≈ 250–430-day median) but the only durably-surviving passes. A fast
> Breakout pass would need a higher-frequency strategy that is **also
> profitable** on this regime; ict_scalp_5m as currently configured is not it.
> (A separate Tier-3 line of work — re-tuning ict_scalp's TP/SL/mitigation to
> lift its win rate above break-even — would be the prerequisite before it
> could help here; that is strategy-param work, out of scope for this research
> run.)
