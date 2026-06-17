# Per-account compatibility — `trend_donchian` (12-mo)

_Generated 2026-06-17T07:37:19.792930+00:00; 245 ledger trades; data /home/user/ict-trader-data/solusdt_perp_5m.parquet_

| account | kind | class | risk% | size$ | metric | value | extra | verdict |
|---|---|---|---|---|---|---|---|---|
| ib_paper | standard | paper | 1.0 | 2000 | end_return_mean | 35.9% | P(breach)=0.9023 | **ROUTE** |
| ib_live | standard | real_money | 1.0 | 2000 | end_return_mean | 35.9% | P(breach)=0.9023 | **ROUTE** |
| oanda_practice | standard | paper | 0.5 | 2000 | end_return_mean | 28.8% | P(breach)=0.478 | **ROUTE** |
| alpaca_paper | standard | paper | 0.5 | 2000 | end_return_mean | 28.8% | P(breach)=0.478 | **ROUTE** |
| bybit_1 | standard | paper | 1.0 | 500 | end_return_mean | 21.8% | P(breach)=1.0 | **ROUTE** |
| bybit_2 | standard | real_money | 1.0 | 500 | end_return_mean | 21.8% | P(breach)=1.0 | **ROUTE** |

Verdict: **ROUTE** = positive under the account's own ruleset (prop: +EV @ P(net>0) ≥ threshold; standard: positive mean end-return). Prop verdicts are research on the configured feed — revalidate on the account's real venue data before live wiring (Tier-3).