# CA-A04 — code audit, `src/units/accounts/**` (brokers, executors, risk)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · audit lane CA-A04, measured 2026-09-27 at `da9e68d` (findings only; no code changed). `unknown` is the generator's value for an unreviewed audit.

Lane CA-A04 of the 2026-09-27 code audit (`docs/plans/SYSTEM-AUDIT-PROPOSAL-2026-09-27.md`).
Session `session_01L6oxNwzTguNk5ZKRaKE1tG`, dispatched by the manager session
`session_01KAQRJxRbRwpYTkPgBjNVyQ`. Findings, one JSON object per line:
[`CA-A04.findings.jsonl`](CA-A04.findings.jsonl).

**The question:** does the code in `src/units/accounts/` do what its docstrings,
tests and the canonical docs say it does, and where does it not?

> **Status: FINAL.** There are 16 findings: 3 high, 8 medium and 5 low. All 3
> HIGH findings are filed in the pipeline.
>
> Two read-only sonnet sub-agents made first reads of the broker clients
> (`clients.py` and its companions; `ib_client.py` and `alpaca_client.py`). The
> lane re-ran or re-read every finding it kept, and each finding's
> `disposition` says which.

## Findings by severity

| sev | id (`AUD-20260927-CA-A04-…`) | one line | disposition |
|---|---|---|---|
| **high** | `daily-loss-cap-by-open-date` | The daily-loss cap counts realized PnL by the day the trade **opened**. 145 of 603 live closes (24%) are cross-day and never count toward any day's cap. Planted: a -6000 loss against a 2500 cap reads `daily_pnl=0.0` and the gate returns `evaluate=(True, None)` | pipeline `PI-20260927-KRAKE1TG-0001` |
| **high** | `monitor-dry-run-gate-reads-absent-attribute` | The monitor's `mode == "dry_run"` short-circuit on close, modify and partial-close can never fire. `_build_account_client` reads `acc.mode`, which `TradingAccount` does not have, so all 11 accounts resolve to `live`, ib_live and oanda_practice included. Inert until a dry_run account holds a row | pipeline `PI-20260927-KRAKE1TG-0002` |
| **high** | `ib-close-precancel-read-failure-collapsed` | `IBClient.close()` flattens even when its pre-cancel could not read the order book. `_open_trades` turns an exception into `[]`, and `_locked_close` ignores the cancel result. A bracket left over a flat book can later open a reverse position | pipeline `PI-20260927-KRAKE1TG-0003` |
| medium | `exposure-notional-ignores-contract-multiplier` | Futures exposure is `qty × price` with no contract multiplier, so it reads MHG 2500× too low (planted: 99 vs 247,500). The effect is latent because no ceiling is declared anywhere | filed (jsonl) |
| medium | `fabricated-order-id-alpaca-oanda-ib` | The alpaca, oanda and IB branches of `_submit_order` still invent a `uuid4().hex` id when an accept carries no id. Bybit's branch was fixed for this in BL-20260830. Planted on all three; 0 of 1000 live rows show it | filed (jsonl) |
| medium | `options-snapshots-unfiltered-first-page` | 3 of 3 live options expressions were refused `fewer_than_two_quotable_strikes`. Snapshots are fetched unfiltered with `limit=200`. The refusal is MEASURED; the mechanism is PLAUSIBLE | filed (jsonl) |
| medium | `prop-risk-manager-inert` | `PropRiskManager.record_trade_result` has 0 runtime callers, and breakout_1 writes 0 trades rows. The result: mission counters and base caps never move, and the UI renders the unmeasured progress as `0.0` | filed (jsonl) |
| medium | `dup-key-check-drops-custom-secret-env` | The duplicate-key check never forwards `api_secret_env`, so all 4 Alpaca accounts (alpaca_live included) are silently excluded. Planted: a shared key between alpaca_live and alpaca_options_paper gives `[]` | filed (jsonl) |
| medium | `ib-cancel-helpers-not-account-scoped` | The IB cancel and coverage helpers match on symbol only, never on account. Dormant: the two IB accounts use separate Gateways today | filed (jsonl) |
| medium | `alpaca-modify-partial-success-reported-as-failure` | A mixed-result leg PATCH returns a bare failure, so the journal SL/TP drifts from the venue | filed (jsonl) |
| medium | `alpaca-defer-messages-miss-monitor-defer-match` | Two of the retCode-2 "DEFERRED" messages match none of the monitor's defer substrings. They count as close failures and can trigger the won't-flatten alarm | filed (jsonl) |
| low | `per-symbol-notional-backtest-filter-mismatch` | `COALESCE(is_backtest,1)` vs `COALESCE(is_backtest,0)` in two queries whose docstring says they mirror each other. 0 NULL rows live | filed (jsonl) |
| low | `fetch-balance-direction-claim-unimplemented` | The spot direction-aware balance in the docstring is never implemented, and a read failure collapses to `0.0` | filed (jsonl) |
| low | `options-shadow-rows-record-equity-qty` | Shadow rows on `alpaca_options_paper` journal ~1500-share equity sizes. The soak cannot see the options expression | filed (jsonl) |
| low | `broker-env-knobs-undocumented` | 10 broker env knobs, including rollback levers, are missing from `env-vars.md` | filed (jsonl) |
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
| `alpaca_client.py` | 2224 | lane: `place` (507–640). Sub-agent read fully: 397–620, 854–1420, 1646–2224; skimmed: 1–397, 620–854, 1420–1646 |
| `ib_client.py` | 4852 | lane: `place` (1583–1830), `_open_trades`, and the close Step 1/2 at 2630–2660. Sub-agent read fully: 1–350, 480–760, 1500–2250, 2473–4260, 4300–4570, 4730–4760; skimmed: connect/warm-up (760–1500), 2250–2470, 4570–4730 |
| `oanda_client.py` | 299 | `place` return contract only |
| `clients.py` (3316), `integrator.py` (223), `account.py` (215), `__init__.py` (243), `dup_key_check.py` (95), `context_snapshot.py` (330) | — | sub-agent read fully. It found EXCHANGE_MAP vs EXCHANGE_MANAGEMENT_CAPS consistent and every config key it checked present in accounts.yaml; the lane did not re-check those two clean results |
| `ib_instruments.py` (137) | — | sub-agent read fully |
| `prop_state_io.py` (146), `alpaca_options_exec.py` (344), `oanda_client.py` beyond `place` | — | **not read** |

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
3. **Finding `ib-close-precancel-read-failure-collapsed` is proven as a code defect but not as an incident.** Settling it needs a journalctl search for close() calls where Step 1 logged nothing while `openTrades` failed. There is no marker to search for today, which is part of the fix.
4. **Whether IBKR actually returns cross-account rows** on a shared login (`ib-cancel-helpers-not-account-scoped`). Settling it needs a paper Gateway with two sub-accounts.
5. **An options spread limit order that rests unfilled.** `execute_pkg` journals
   the trade row as `open` as soon as the order is accepted. This never occurred
   in the window, because 0 spreads were placed. Settling it needs the first
   placed spread plus `/v2/orders` status.
