# Strategy research harnesses (overnight campaign, 2026-06-01)

Standalone, net-of-fee backtest tooling used by the autonomous overnight
strategy-research session. Results report: `docs/research/overnight-strategy-research-2026-06-01.md`.

- `research_momentum.py` — new-idea harness: time-series momentum (`tsmom`) and
  fast/slow MA-cross (`macross`) entries with an ATR-Chandelier trail exit.
  Same JSON schema as `scripts/backtest_trend.py` (net_total_r,
  net_total_r_long/short, by_year, max_drawdown_r). Long/short or `--long-only`.
- `sweep_wave1_families.py` — orchestrates the existing harnesses (trend,
  pullback, fade, squeeze) across param grids × timeframes × walk-forward
  windows (full / IS 2021-2023 / OOS 2024-2026), recording to
  `/tmp/research/results.jsonl`.
- `sweep_wave2_momentum.py` — same, for `research_momentum.py`.
- `rank_walkforward.py` — pivots results.jsonl by config and ranks by
  out-of-sample net R, flagging configs net-positive in BOTH windows.

Tier-1 research tooling — reads OHLCV, writes only under `/tmp`. Not wired to live.
