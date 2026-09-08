# E3.5 — bracket-geometry sweep (tp_r x atr_stop_mult x timeout_bars)

Generated `2026-09-08T09:54:19.497200+00:00` · tp_cap_pct `0.099` · fees `harness default (execution_costs.DEFAULT_FEE_BPS_ROUNDTRIP)`

**Every `net_R` below is NET OF FEES.** A lower take-profit raises turnover, so a fee-free basis would flatter exactly the cells most likely to pass.

⚠️ **A stop-axis `net_R` is unreadable without `leverage x`** — `risk = atr_stop_mult * ATR` and `qty = risk_budget / risk`, so a TIGHTER stop buys its R with MORE leverage. `leverage x > 1` means the gain is contingent on the account being able to size it. Reported, never gated.

## Response surface (full history)

Read `spread` first: it is what the whole bracket dimension is worth on that leg. An argmax over a flat surface is noise.

| leg | exec | tf | base net_R | grid min | grid max | **spread** | best cell | best Δ | n |
|---|---|---|---|---|---|---|---|---|---|
| `qld_trend_long_1d` | live | 1d | 42.7069 | 13.2013 | 58.6113 | **45.41** | `tp3_sm1.5` | 15.9044 | 199/199 |
| `tqqq_trend_long_1d` | live | 1d | 25.953 | 10.3091 | 28.3471 | **18.038** | `tp1.5_sm1.5` | 2.3941 | 199/199 |

## Gate (IS/OOS Path A/B + yearly walk-forward)

| leg | cell | axis | verdict | path | wf | wf effective | leverage x |
|---|---|---|---|---|---|---|---|
| `qld_trend_long_1d` | `tp2` | tp | **wf_fail** | A | 5/6 | 0/6 | — |
| `qld_trend_long_1d` | `tp4` | tp | **is_oos_fail** | — | — | — | — |
| `qld_trend_long_1d` | `tp6` | tp | **is_oos_fail** | — | — | — | — |
| `qld_trend_long_1d` | `sm1.5` | stop | **is_oos_fail** | — | — | — | 1.67x ⚠ |
| `qld_trend_long_1d` | `sm2` | stop | **path_b_wf_pass** | B | 5/6 | 5/6 | 1.26x ⚠ |
| `qld_trend_long_1d` | `sm3` | stop | **is_oos_fail** | — | — | — | 0.83x |
| `qld_trend_long_1d` | `to400` | timeout | **is_oos_fail** | — | — | — | — |
| `qld_trend_long_1d` | `to96` | timeout | **is_oos_fail** | — | — | — | — |
| `qld_trend_long_1d` | `to48` | timeout | **is_oos_fail** | — | — | — | — |
| `qld_trend_long_1d` | `tp3_sm1.5` | tp+stop | **wf_fail** | B | 3/6 | 3/6 | 1.69x ⚠ |
| `tqqq_trend_long_1d` | `tp2` | tp | **is_oos_fail** | — | — | — | — |
| `tqqq_trend_long_1d` | `tp2.5` | tp | **is_oos_fail** | — | — | — | — |
| `tqqq_trend_long_1d` | `tp3` | tp | **is_oos_fail** | — | — | — | — |
| `tqqq_trend_long_1d` | `sm2` | stop | **is_oos_fail** | — | — | — | 1.25x ⚠ |
| `tqqq_trend_long_1d` | `sm1.5` | stop | **is_oos_fail** | — | — | — | 1.68x ⚠ |
| `tqqq_trend_long_1d` | `sm3` | stop | **is_oos_fail** | — | — | — | 0.83x |
| `tqqq_trend_long_1d` | `to96` | timeout | **is_oos_fail** | — | — | — | — |
| `tqqq_trend_long_1d` | `to400` | timeout | **is_oos_fail** | — | — | — | — |
| `tqqq_trend_long_1d` | `to48` | timeout | **is_oos_fail** | — | — | — | — |
| `tqqq_trend_long_1d` | `tp1.5_sm1.5` | tp+stop | **is_oos_fail** | — | — | — | 1.72x ⚠ |

## Base geometry actually measured

| leg | tp_r | source | atr_stop_mult | source | timeout | source | tp_cap |
|---|---|---|---|---|---|---|---|
| `qld_trend_long_1d` | 50 | base_args | 2.5 | base_args | 1000000000 | base_args | 0.099 |
| `tqqq_trend_long_1d` | 50 | base_args | 2.5 | base_args | 1000000000 | base_args | 0.099 |
