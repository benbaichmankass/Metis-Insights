# Real money 0-for-13 — instrument repair, then loss attribution (2026-09-06)

**Status: IN PROGRESS.** Parent object `WO-20260906-REAL-MONEY-WENT-0-FOR-13-THE`.
Tier-3 findings are PROPOSED only — nothing in `config/strategies.yaml` is touched.

## Deliverable 1 — the R instrument is not trustworthy (MEASURED)

**Population: `trades` where `status='closed'` AND NOT `is_backtest` AND `pnl IS NOT NULL`
— n = 1451, drawn from all 5516 `trades` rows, all accounts.** Read live
2026-09-06 from `/api/bot/db/table/trades` (`filter_state: not_requested`, `total: 5516`,
5516 fetched — full table, not a page).

R-provenance via `src/runtime/r_provenance.py::classify_r` (imported, not re-derived):

| state | n | share |
|---|---|---|
| `contaminated` | 124 | 8.5% |
| `confirmed_initial` | 174 | 12.0% |
| `unverified` | 1132 | 78.0% |
| `no_basis` | 21 | 1.4% |

**8.5% of rows carry 98.8% of the R.**

| subset | n | sum R | mean R |
|---|---|---|---|
| `contaminated` | 124 | **+4020.27** | **+32.42** |
| `confirmed_initial` | 174 | **−92.16** | **−0.53** |
| all R-measurable | 1430 | +4067.52 | +2.84 |

The sign flips. On the rows whose risk basis is *confirmed to be the initial risk*,
expectancy is **−0.53R**. The published +2.84R is an artifact of the other 8.5%.

**Positive control for the discriminator:** `|R| > 10` occurs **35 times among the 124
contaminated rows and 0 times among the 174 confirmed_initial rows.** Max contaminated
R is **+3672.3** on a single row. The separation is total, so this is not a threshold
chosen to fit.

**Mechanism (read from the code, not inferred).** `_clean_trades.r_multiple` computes
`pnl / (|entry − stop| · |qty| · contract_value)`. The `abs()` is load-bearing: a stop
on the *wrong side of entry* still yields a positive risk, so the row produces a finite
R instead of being refused. `trades.stop_loss` holds the **final** stop, and
`order_monitor._apply_update` writes trailing amends into it — so a trade that trailed
its stop through breakeven stores a stop beyond entry, `|entry − stop|` collapses toward
zero, and R explodes. R is defined against *entry-time* risk; the column holds *exit-time*
stop.

**Consequence: R feeds the promotion gates, so every promote/demote verdict computed
from `expectancyR` over a population containing contaminated rows is unsafe.** No claim
in Deliverable 2 rests on R.

## Deliverable 2 — attribution
_in progress_
