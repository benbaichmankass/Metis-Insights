# PB-20260822-AVAX-SCALP-SIZED-OFF-MARGIN-NOT-RISK — sizing fix, 2026-09-28

> Doc status: `live` · category `research` · operator directive: "Fix risk sizing now"

## The finding this closes/advances

`ict_scalp_avax_5m`'s order size is frequently the account's margin ceiling
(`max_qty_by_margin`) rather than a risk-derived number — see the pipeline
item's full history (n=2 → n=32 → n=30, measured across three prior
sessions between 2026-08-22 and 2026-09-17).

## Architecture check (done first, before touching any config)

Read `src/units/accounts/risk.py::RiskManager.__init__`: `risk_pct` and
`leverage` are **account-level** fields, read once from `config/accounts.yaml`
(`bybit_1.risk.risk_pct: 0.015`, `bybit_1.risk.leverage: 3`), shared by
every one of the ~26 strategies that account runs. A 2026-06-29 operator
directive explicitly removed the per-strategy `risk_pct` multiplier: "sizing
is the RiskManager's sole responsibility." **There is no per-leg risk_pct or
leverage override to set "for this leg."**

The one leg-level parameter that DOES participate in `position_size()`'s
`risk_per_unit` is `atr_sl_buffer_mult`: `ict_scalp`'s stop is
`sweep_extreme ± atr_sl_buffer_mult * ATR`
(`src/units/strategies/ict_scalp.py`), so widening the buffer widens
`risk_per_unit`, which lowers the risk-derived qty for a fixed dollar risk
budget — pushing it further from the margin ceiling.

The margin-ceiling **bind condition** (derived from
`position_size()`, `bybit_1`'s `risk_pct=0.015`, `leverage=3`,
`_MARGIN_SAFETY_BUFFER=0.9`):

```
stop_distance / entry  <=  risk_pct / (leverage * buffer)  =  0.015 / 2.7  =  0.5556%
```

## Evidence: 365d AVAXUSDT 5m backtest, this lever only

Data: `data.binance.vision`, fetched this session
(`scripts/ops/fetch_backtest_candles.py --symbol AVAXUSDT --interval 5 --days 365`).
Harness: `scripts/backtest_ict_scalp.py --strategy-name ict_scalp_avax_5m`
(reads the leg's live YAML config; only `atr_sl_buffer_mult` changed between
runs). Cost stack: fee 7.5bps + venue-aware slippage/funding for AVAXUSDT
(3.0/1.0bps roundtrip, `src/runtime/execution_costs.py`).

| | `atr_sl_buffer_mult=0.20` (current) | `atr_sl_buffer_mult=0.50` (proposed) |
|---|--:|--:|
| n trades | 251 | 250 |
| margin-ceiling bind rate | 45/251 = 17.9% | 33/250 = 13.2% |
| win rate | 52.19% | 53.2% |
| net_total_r (net of full cost) | **-8.2064R** | **+7.0285R** |
| net_r fee-only | 4.7813R | 16.5257R |
| max_drawdown_r | 27.5068 | 16.5524 |
| sharpe_r | 0.1489 | 0.1678 |

Both effects are real: the margin-bind rate falls (a modest, diminishing-returns
reduction — even `atr_sl_buffer_mult=1.20`, a 6x buffer, only reaches 8.4%,
confirming the sweep-extreme distance dominates stop placement, not this
buffer), **and** the backtest's net return flips from failing to passing —
which was not predicted going in and is the more consequential result.

⚠️ **This is the RAW FULL-WINDOW number, not the OOS-folded
`RULE-D1-STAGE0-NET-OF-FULL-COST` statistic** the roster-promotion gate
reads (`comms/strategy_evidence/ict_scalp_avax_5m.json`). Before this leg's
Stage-0 verdict can be updated anywhere, re-run
`scripts/ops/build_strategy_evidence.py --strategy ict_scalp_avax_5m --days 365 --folds 4`
against the widened config and commit the refreshed record. That is
follow-up work, not done in this pass.

## What this does NOT establish

- Not a claim that the margin-ceiling bind CAUSES the loss on the tighter
  stop — the mechanism connecting the two was explicitly tested and
  withdrawn by evidence in the 2026-09-17 finding (MI-278 U45): a pooled
  bound-vs-unbound trade comparison did not cleanly separate. What changed
  here is different — the WHOLE population's stop geometry, not a subset
  comparison — and it is this test, not that one, that shows the effect.
- Not a complete fix to the margin-ceiling finding. 13.2% of trades still
  hit the ceiling; only a genuine per-leg risk fraction (which does not
  exist today) could close that gap to zero without also giving up on the
  leg's entry logic (the sweep-extreme term).
- Not yet folded into any Stage-0 evidence record or promotion decision.

## Disposition

Tier-3 proposal, landing held for operator/manager approval. Diff:
`config/strategies.yaml` `ict_scalp_avax_5m.atr_sl_buffer_mult: 0.20 -> 0.50`.
