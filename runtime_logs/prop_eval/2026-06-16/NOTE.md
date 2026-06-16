# NOTE — this baseline matrix is a PLUMBING smoke run, not a real evaluation

This `matrix.{md,json}` was produced by running
`scripts/prop/evaluate_prop.py --combos all` against the only candle file
available in the build sandbox, `data/backtest_candles.csv`.

**That file spans only ~3.5 days** (5,000 1-minute bars,
2022-07-23 → 2022-07-27). The roster strategies run on 2h / 4h / 15m
timeframes and the portfolio engine requires **260 warm bars on each
strategy's own timeframe** before it emits any signal:

| TF | resampled bars in the file | warm bars needed |
|----|----|----|
| 15m | 334 | 260 |
| 2h  | 43  | 260 |
| 4h  | 22  | 260 |

So the 2h/4h members never clear warmup at all, and the 15m member has
~74 usable bars over 3.5 days — nowhere near enough to reach a +10%
target. Every combo therefore shows **0 trades / $0 net / "EVAL NOT
REACHED"**. That is a **data-length limitation, not a bug**: the full
pipeline (combo enumeration → portfolio engine in-process → evaluator →
ranked Markdown/JSON) runs end-to-end correctly.

**To produce a meaningful matrix**, re-run against a multi-year BTCUSDT
5m feed (the design assumes ~2021→2026), e.g.:

```
python scripts/prop/evaluate_prop.py \
  --ruleset config/prop_rulesets/breakout.yaml \
  --data <multi-year-5m.csv> --combos all
```

The ruleset itself is also flagged **UNCONFIRMED** (two fields not on the
Breakout plan card) — see the banner in `matrix.md` and the design doc
`docs/research/prop-firm-testing-tool-DESIGN.md` §9.
