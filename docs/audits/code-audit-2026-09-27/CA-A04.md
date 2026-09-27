# CA-A04 — code audit, `src/units/accounts/**` (brokers, executors, risk)

Lane CA-A04 of the 2026-09-27 code audit (`docs/plans/SYSTEM-AUDIT-PROPOSAL-2026-09-27.md`).
Session `session_01L6oxNwzTguNk5ZKRaKE1tG`, dispatched by the manager session
`session_01KAQRJxRbRwpYTkPgBjNVyQ`. Findings, one JSON object per line:
[`CA-A04.findings.jsonl`](CA-A04.findings.jsonl).

**The question:** does the code in `src/units/accounts/` do what its docstrings,
tests and the canonical docs say it does, and where does it not?

> **Status: FIRST LANDING (partial).** This PR carries the findings from the
> risk/sizing, executor and options paths. The broker-client findings
> (`clients.py`, `ib_client.py`, `alpaca_client.py`, `integrator.py`) land in
> the second PR, which updates this file.

## Findings by severity

| sev | id (`AUD-20260927-CA-A04-…`) | one line | disposition |
|---|---|---|---|
| **high** | `daily-loss-cap-by-open-date` | The daily-loss cap counts realized PnL by the day the trade **opened**. 145 of 603 live closes (24%) are cross-day and never count toward any day's cap. Planted: a -6000 loss against a 2500 cap reads `daily_pnl=0.0` and the gate returns `evaluate=(True, None)` | pipeline `PI-20260927-KRAKE1TG-0001` |
| medium | `exposure-notional-ignores-contract-multiplier` | Futures exposure is `qty × price` with no contract multiplier, so it reads MHG 2500× too low (planted: 99 vs 247,500). The effect is latent because no ceiling is declared anywhere | filed (jsonl) |
| medium | `fabricated-order-id-alpaca-oanda-ib` | The alpaca, oanda and IB branches of `_submit_order` still invent a `uuid4().hex` id when an accept carries no id. Bybit's branch was fixed for this in BL-20260830. Planted on all three; 0 of 1000 live rows show it | filed (jsonl) |
| medium | `options-snapshots-unfiltered-first-page` | 3 of 3 live options expressions were refused `fewer_than_two_quotable_strikes`. Snapshots are fetched unfiltered with `limit=200`. The refusal is MEASURED; the mechanism is PLAUSIBLE | filed (jsonl) |
| medium | `prop-risk-manager-inert` | `PropRiskManager.record_trade_result` has 0 runtime callers, and breakout_1 writes 0 trades rows. The result: mission counters and base caps never move, and the UI renders the unmeasured progress as `0.0` | filed (jsonl) |
| low | `per-symbol-notional-backtest-filter-mismatch` | `COALESCE(is_backtest,1)` vs `COALESCE(is_backtest,0)` in two queries whose docstring says they mirror each other. 0 NULL rows live | filed (jsonl) |
| low | `fetch-balance-direction-claim-unimplemented` | The spot direction-aware balance in the docstring is never implemented, and a read failure collapses to `0.0` | filed (jsonl) |
| low | `options-shadow-rows-record-equity-qty` | Shadow rows on `alpaca_options_paper` journal ~1500-share equity sizes. The soak cannot see the options expression | filed (jsonl) |
| low | `order-dependent-accounts-tests` | 6 accounts tests pass only when an earlier test has warmed `precision._LIVE_CACHE`. They fail in isolation or as the accounts subset | filed (jsonl) |

Every medium and low finding sits in the JSONL with its evidence, a detector and
the proposed fix. The one HIGH is also a pipeline item, as the lane
instructions require.

## Coverage — both ways

**Behavioural (primary): what was exercised against real behaviour, not only read**

- **Daily-loss gate**: exercised on the real `RiskManager` against a planted
  journal. It was also measured against the live journal: the last 1000 trades
  from `journal?table=trades&limit=2000`, which served ids 5226–6225 covering
  2026-08-29 to 2026-09-27.
- **Exposure observation**: exercised on the real `RiskManager` against a
  planted open futures row.
- **`_submit_order` alpaca/oanda branches**: exercised with fake clients. The IB
  branch was checked by reading only, because it has the identical line.
- **Options expression**: outcome measured on live rows (14 rows, 3 live
  attempts). The venue-side mechanism was **not** exercised, because this
  sandbox has no Alpaca options credentials.
- **Fabricated ids**: live rows measured, 0 of 1000 show a 32-hex broker id.
- **Tests**: `python -m pytest` over the 158 test files that import
  `src.units.accounts` gave **2781 passed, 6 failed**. The 6 failures are the
  order-dependent finding above.

**Reading (secondary)**

| file | lines | read |
|---|---|---|
| `risk.py` | 1342 | fully |
| `execute.py` | 3393 | fully: 1–700, 773–860, 1197–1700, 1940–2180, 3102–3393. **Not read**: 860–1196 (margin-field readers), 1700–1940 (170134 diag, leg resize), 2181–3101 (journal writer, partial-tpsl helpers, `modify_open_order`) |
| `options_overlay.py` | 305 | fully |
| `options_selector.py` / `options_sizing.py` / `alpaca_options_data.py` | 195 / 154 / 189 | partially (the selection, sizing and snapshot paths) |
| `options_lifecycle.py` | 170 | header only |
| `qty_legalize.py` | 580 | `legalize_qty` fully; the rest not read |
| `precision.py` | 355 | 1–110 and 309–320 |
| `prop_risk.py` | 400 | partially (docstring, config keys, `record_trade_result`) |
| `exposure.py` | 190 | `exposure_verdict` fully |
| `alpaca_client.py` | 2224 | `place` fully (507–640); remainder in the second PR |
| `ib_client.py` | 4852 | `place` fully (1583–1720); remainder in the second PR |
| `oanda_client.py` | 299 | `place` return contract only |
| `clients.py`, `integrator.py`, `account.py`, `__init__.py`, `dup_key_check.py`, `context_snapshot.py`, `prop_state_io.py`, `ib_instruments.py`, `alpaca_options_exec.py` | — | second PR, or not read (stated there) |

## What this lane could not settle, and what would settle it

1. **Which 200 snapshots Alpaca returns first**, the mechanism behind the
   options refusals. One authenticated `GET /v1beta1/options/snapshots/SLV?limit=200`
   from the trainer VM or a runner would settle it: check whether any
   21–60 DTE contract is present in that page.
2. **Whether the cross-day attribution ever let a real-money account trade past
   its cap.** The losses are on paper accounts today. On bybit_2 the cross-day
   losses are 10 rows totalling -34.22, well inside its cap. The question needs
   `balance_snapshots.json` equity per account-day, which is not served by any
   diag route read here.
3. **An options spread limit order that rests unfilled.** `execute_pkg` journals
   the trade row as `open` as soon as the order is accepted. This never occurred
   in the window, because 0 spreads were placed. Settling it needs the first
   placed spread plus `/v2/orders` status.
