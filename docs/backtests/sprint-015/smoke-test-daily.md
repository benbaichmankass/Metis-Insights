# S-015 — Daily-resolution smoke test (NOT a baseline)

_⚠️ HARNESS VALIDATION ONLY. The data here is **daily reference**
**rates** from coinmetrics/data — not 5m / 15m intraday bars.
DO NOT use these numbers to tune live strategy parameters._


- Generated: `2026-04-30T21:17:09+00:00`
- Folds: **5** stratified, disjoint
- Recency window: **last 36 months**
- Sampler seed: **42**
- Slippage: 2 bps round-trip (default), plus a 0/2/10 sweep


## BTCUSDT

- source: `github_raw` (provenance: keyless github mirror)
- bars in full series: **1826** (2021-04-30 00:00:00+00:00 → 2026-04-29 00:00:00+00:00)
- bars per fold: [239, 246, 216, 211, 184]
- recency mix per fold (recent / mid / old): [(3, 5, 0), (3, 5, 0), (2, 5, 0), (2, 5, 0), (2, 4, 0)]

### Slippage sweep (threshold=1.0σ, lookback=20)

| slippage_bps | aggregate_pnl | sharpe | max_dd |
|---:|---:|---:|---:|
| 0.0 | -216642.49 | -0.7687 | 65504.77 |
| 2.0 | -216752.12 | -0.7691 | 65520.13 |
| 10.0 | -217190.65 | -0.7706 | 65581.57 |

### Per-fold breakdown (2 bps slippage)

| fold | n_trades | realised_pnl | win_rate | sharpe | max_dd |
|---:|---:|---:|---:|---:|---:|
| 0 | 3 | -36986.72 | 0.333 | -0.6687 | 39393.99 |
| 1 | 2 | -51397.74 | 0.500 | -0.6454 | 65520.13 |
| 2 | 2 | -49300.61 | 0.500 | -0.9494 | 50615.54 |
| 3 | 2 | -45891.41 | 0.500 | -0.7117 | 55188.33 |
| 4 | 2 | -33175.64 | 0.500 | -0.8705 | 0.00 |

## ETHUSDT

- source: `github_raw` (provenance: keyless github mirror)
- bars in full series: **1826** (2021-04-30 00:00:00+00:00 → 2026-04-29 00:00:00+00:00)
- bars per fold: [239, 246, 216, 211, 184]
- recency mix per fold (recent / mid / old): [(3, 5, 0), (3, 5, 0), (2, 5, 0), (2, 5, 0), (2, 4, 0)]

### Slippage sweep (threshold=1.0σ, lookback=20)

| slippage_bps | aggregate_pnl | sharpe | max_dd |
|---:|---:|---:|---:|
| 0.0 | 7969.72 | -0.6657 | 673.41 |
| 2.0 | 7957.28 | -0.6674 | 673.85 |
| 10.0 | 7907.54 | -0.6743 | 675.60 |

### Per-fold breakdown (2 bps slippage)

| fold | n_trades | realised_pnl | win_rate | sharpe | max_dd |
|---:|---:|---:|---:|---:|---:|
| 0 | 5 | 2402.08 | 0.600 | 0.7873 | 307.16 |
| 1 | 8 | 6051.18 | 0.875 | 0.9893 | 165.54 |
| 2 | 5 | 1054.62 | 0.600 | 0.4339 | 538.98 |
| 3 | 4 | -466.95 | 0.500 | -0.3152 | 673.85 |
| 4 | 2 | -1083.65 | 0.000 | -5.2325 | 438.27 |

## What this proves

1. The github-raw adapter reaches `coinmetrics/data` from inside this sandbox and returns real daily bars.
2. The recency-weighted month-bucket sampler produces 5 disjoint folds with a balanced recency mix.
3. The harness runs a strategy adapter end-to-end and computes per-fold realised P&L, Sharpe, win rate, and max drawdown.
4. The 2 bps slippage model degrades P&L monotonically vs the 0 bps reference run.

## What this does NOT prove

1. Anything about VWAP or turtle_soup at 5m / 15m — those run on real intraday bars, not daily reference rates with synthesised OHLC.
2. Anything about parameter tuning — operator hard rule: do not learn parameters from incorrect-resolution data.
3. Anything about live P&L — the slippage model is a stylised constant, not a Bybit fill simulator.


## Machine-readable

```json
[
  {
    "symbol": "BTCUSDT",
    "source": "github_raw",
    "bars_total": 1826,
    "date_range": [
      "2021-04-30 00:00:00+00:00",
      "2026-04-29 00:00:00+00:00"
    ],
    "fold_bar_counts": [
      239,
      246,
      216,
      211,
      184
    ],
    "fold_recency_summary": [
      {
        "recent": 3,
        "mid": 5,
        "old": 0
      },
      {
        "recent": 3,
        "mid": 5,
        "old": 0
      },
      {
        "recent": 2,
        "mid": 5,
        "old": 0
      },
      {
        "recent": 2,
        "mid": 5,
        "old": 0
      },
      {
        "recent": 2,
        "mid": 4,
        "old": 0
      }
    ],
    "slippage_sweep": [
      {
        "slippage_bps": 0.0,
        "aggregate_pnl": -216642.49,
        "aggregate_sharpe": -0.7687,
        "aggregate_max_dd": 65504.77
      },
      {
        "slippage_bps": 2.0,
        "aggregate_pnl": -216752.12,
        "aggregate_sharpe": -0.7691,
        "aggregate_max_dd": 65520.13
      },
      {
        "slippage_bps": 10.0,
        "aggregate_pnl": -217190.65,
        "aggregate_sharpe": -0.7706,
        "aggregate_max_dd": 65581.57
      }
    ],
    "per_fold_at_2bps": [
      {
        "fold": 0,
        "n_trades": 3,
        "realised_pnl": -36986.72,
        "win_rate": 0.333,
        "sharpe": -0.6687,
        "max_drawdown": 39393.99
      },
      {
        "fold": 1,
        "n_trades": 2,
        "realised_pnl": -51397.74,
        "win_rate": 0.5,
        "sharpe": -0.6454,
        "max_drawdown": 65520.13
      },
      {
        "fold": 2,
        "n_trades": 2,
        "realised_pnl": -49300.61,
        "win_rate": 0.5,
        "sharpe": -0.9494,
        "max_drawdown": 50615.54
      },
      {
        "fold": 3,
        "n_trades": 2,
        "realised_pnl": -45891.41,
        "win_rate": 0.5,
        "sharpe": -0.7117,
        "max_drawdown": 55188.33
      },
      {
        "fold": 4,
        "n_trades": 2,
        "realised_pnl": -33175.64,
        "win_rate": 0.5,
        "sharpe": -0.8705,
        "max_drawdown": 0.0
      }
    ]
  },
  {
    "symbol": "ETHUSDT",
    "source": "github_raw",
    "bars_total": 1826,
    "date_range": [
      "2021-04-30 00:00:00+00:00",
      "2026-04-29 00:00:00+00:00"
    ],
    "fold_bar_counts": [
      239,
      246,
      216,
      211,
      184
    ],
    "fold_recency_summary": [
      {
        "recent": 3,
        "mid": 5,
        "old": 0
      },
      {
        "recent": 3,
        "mid": 5,
        "old": 0
      },
      {
        "recent": 2,
        "mid": 5,
        "old": 0
      },
      {
        "recent": 2,
        "mid": 5,
        "old": 0
      },
      {
        "recent": 2,
        "mid": 4,
        "old": 0
      }
    ],
    "slippage_sweep": [
      {
        "slippage_bps": 0.0,
        "aggregate_pnl": 7969.72,
        "aggregate_sharpe": -0.6657,
        "aggregate_max_dd": 673.41
      },
      {
        "slippage_bps": 2.0,
        "aggregate_pnl": 7957.28,
        "aggregate_sharpe": -0.6674,
        "aggregate_max_dd": 673.85
      },
      {
        "slippage_bps": 10.0,
        "aggregate_pnl": 7907.54,
        "aggregate_sharpe": -0.6743,
        "aggregate_max_dd": 675.6
      }
    ],
    "per_fold_at_2bps": [
      {
        "fold": 0,
        "n_trades": 5,
        "realised_pnl": 2402.08,
        "win_rate": 0.6,
        "sharpe": 0.7873,
        "max_drawdown": 307.16
      },
      {
        "fold": 1,
        "n_trades": 8,
        "realised_pnl": 6051.18,
        "win_rate": 0.875,
        "sharpe": 0.9893,
        "max_drawdown": 165.54
      },
      {
        "fold": 2,
        "n_trades": 5,
        "realised_pnl": 1054.62,
        "win_rate": 0.6,
        "sharpe": 0.4339,
        "max_drawdown": 538.98
      },
      {
        "fold": 3,
        "n_trades": 4,
        "realised_pnl": -466.95,
        "win_rate": 0.5,
        "sharpe": -0.3152,
        "max_drawdown": 673.85
      },
      {
        "fold": 4,
        "n_trades": 2,
        "realised_pnl": -1083.65,
        "win_rate": 0.0,
        "sharpe": -5.2325,
        "max_drawdown": 438.27
      }
    ]
  }
]
```
