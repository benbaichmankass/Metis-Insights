# CA-A02 — Execution core I: pipeline, intents, sizing, costs, gates

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md) · lane `CA-A02` · findings: [`CA-A02.findings.jsonl`](CA-A02.findings.jsonl)

**The question:** does the code in scope do what its docstrings, tests and the
canonical contracts say it does, and where does it not?

**Short answer:** the two declared execution gates (`mode:`, `execution:`) are
folded correctly on the main dispatch path, and the scoped suites pass (812 +
275 + 169 + 128 tests). The failures are at the edges of that path:

- The **pairs sleeve** runs outside `pipeline.run`, so the halt flag does not
  stop it, and its close/unwind path does not honour `mode: dry_run`.
- The **order-layer guards in `safe_place_order`** have no production caller,
  including the halt check that was wired on 2026-08-16.
- **Three "is there already a position?" readers** report "flat / no" when
  they cannot read the journal.

The open question handed to this lane is settled: PR #13134's
`available_usd=0.00` on `alpaca_paper` is **Reg-T pool exhaustion** (the
venue's `regt_buying_power`). It is not a `cash_settlement` bug. See
"PR #13134's zero-sizing" below.

## Findings by severity

| sev | id (`AUD-20260927-CA-A02-…`) | one line | tier | disposition |
|---|---|---|---|---|
| high | `halt-flag-does-not-stop-pairs-sleeve` | `run_pairs_tick` runs from `src/main.py` outside the only halt check, so live pairs still place while halted | 2 | PI-20260927-ECILBBVH-0001 |
| high | `safe-place-order-guards-have-no-callers` | 0 call sites in `src/` (AST). MAX_DAILY_LOSS / MAX_OPEN_POSITIONS / per-strategy caps / throttle / the 2026-08-16 order-layer halt point never fire, while 4 comments say they do | 2 | PI-20260927-ECILBBVH-0002 |
| high | `net-position-read-failure-reads-flat` | a journal read failure makes `current_net_position_qty` return 0.0 and both has-open helpers return False, so a duplicate full-size open goes out | 2 | PI-20260927-ECILBBVH-0003 |
| medium | `pairs-close-ignores-account-dry-run` | pair close/unwind calls `close_open_position`, which has no mode gate | 2 | PI-20260927-ECILBBVH-0004 |
| medium | `account-without-strategies-key-gets-every-package` | an account with no `strategies:` key receives every package and bypasses `account_scope` (latent: 0 of 11 accounts today) | 2 | PI-20260927-ECILBBVH-0005 |
| medium | `conviction-nan-bypasses-floor` | a NaN conviction reads `below_floor=False`; sizing raises and the callers keep the full qty | 2 | PI-20260927-ECILBBVH-0006 |
| medium | `downsizes-not-requantized-on-whole-unit-venues` | Alpaca: 1 share ×0.5 becomes a venue-min refusal; 3 ×0.85 still places 3 | 3 | PI-20260927-ECILBBVH-0007 |
| medium | `settlement-soak-would-reduce-reads-zero-when-applied` | soak `would_have_reduced_usd` is computed after the replacement, and `applied` means "allowlisted", not "bound" | 1 | PI-20260927-ECILBBVH-0008 |
| medium | `pairs-half-open-not-in-concurrency-gate` | a half-open pair's legs are invisible to the disjoint-legs gate | 2 | PI-20260927-ECILBBVH-0009 |
| low | `settlement-counts-short-covers-as-sale-proceeds` | `recent_sales` counts buy-to-cover as proceeds (live soak sums reproduced to the cent) | 2 | findings file only |
| low | `risk-counters-window-on-open-date-all-accounts` | daily-loss counters window on the open date and pool all accounts; moot while the consumer is dead | 2 | findings file only |
| low | `pairs-unknown-execution-logs-open` | a typo'd `execution` logs `open` but places nothing | 1 | findings file only |
| low | `execution-gate-unknown-name-resolves-live` | an unresolvable strategy name falls through to live; safe today only because roster membership blocks it | 3 | findings file only |
| — | `pr13134-available-usd-zero-is-regt-not-settlement` | settled: Reg-T contention | — | verified-non-issue |
| — | `dry-trade-rows-do-not-pollute-netting-or-settlement` | 127 dry rows, all `rejected` | — | verified-non-issue |
| — | `effective-dry-fold-and-scoped-tests` | gate fold matches the contract; suites green | — | verified-non-issue |

Counts: **0 critical · 3 high · 6 medium · 4 low · 3 verified-non-issue.**
All 9 high and medium findings are in the pipeline. The 4 lows are in the
findings file only, for the manager to route or kill.

## PR #13134's zero-sizing (the open question handed to this lane)

Read live through `diag_fetch.sh 'log_file?name=cash_settlement_soak&lines=400'`
(74 rows, 2026-08-31 → 2026-09-24):

- **alpaca_paper, 40 of 40 rows:** `applied=false`, `apply_scope=not_allowlisted`.
  So `cash_settlement` never replaced `available_usd` on that account.
- **Row at 2026-09-24T13:32:** `venue_cash=64775.15`,
  `venue_buying_power=110266.24`, `available_usd_after=0.0`, `unsettled=0.0`.
  The 0.0 is `AlpacaClient.buying_power()`, which prefers `regt_buying_power`
  (`alpaca_client.py:699`).
- **Six minutes later** the same account read `16642.69`.

That is Reg-T overnight pool contention on a multi-leg margin book. PI-20260926-X3QEGPJL-0004/-0006 can be re-dispositioned on that basis.

## Coverage

**Behavioural (primary):** which contracts were checked against real
behaviour, rather than only read.

| contract | how it was exercised |
|---|---|
| execution gates / `effective_dry` | scoped tests (812 pass), plus a planted accounts.yaml proving the scope bypass |
| halt flag reach | grep plus call-graph reading. **Not run live.** |
| `safe_place_order` reach | AST scan of all of `src/` |
| net-position reader | planted journal with an unreadable table |
| `cash_settlement` | planted `record_observation`; live soak (n=74) cross-checked against live journal trades (1000 rows) |
| downsize / whole-unit interaction | direct calls to `whole_unit_qty` and `legalize_qty` |
| conviction NaN | direct call |
| dry-row isolation | live journal: 127 dry rows, 0 open or closed |
| pairs dry_run, concurrency and typo | sub-lane proof scripts; the concurrency claim was re-read by this lane |

**Reading (secondary).**

Read fully:
- `src/runtime/cash_settlement.py` (570 lines)
- `src/runtime/order_bridge.py` (125)
- `Coordinator.multi_account_execute`, `src/core/coordinator.py:865-2200`
  (the routing, gate, sizing and intent-delta blocks)
- `intents.compute_execution_delta*` (`intents.py:1896-2218`)

Read fully by sub-lanes, with their defects re-verified here by a direct run
or read:
- `conviction`, `conviction_arbitration`, `conviction_inputs`,
  `conviction_sizing`
- `advisory_sizing`, `advisory_influence`, `news_sizing`
- `allocator_corr`, `allocator_ev`, `arbitration_fanout`
- `account_side_filter`, `portfolio_conflicts`, `conflict_taxonomy`
- `execution_costs`, `trade_costs`, `tick_cost`, `broker_cost_attribution`
- `orders`, `market_hours`, `ib_trading_hours`, `operator_flatten_intent`
- `validation`, `risk_counters`, `runtime_flags`, `strategy_roster`,
  `loaded_config`, `pipeline_result`
- `src/units/strategies/pairs_executor.py` (1377) and `config/pairs.yaml` (110)

Partially read:
- `src/runtime/pipeline.py`: 900-1275 only (dispatch and halt), of 1467
- `src/runtime/intent_multiplexer.py`: 720-900, of 1084
- `src/runtime/market_data.py`: about 370-820 by a sub-lane, of 816
- `src/main.py`: the halt and pairs call site only, of 1452
- `src/core/coordinator.py`: outside 865-2200, not read (3871 total)

**Not read:**
- `src/runtime/intents.py` 1-1895 (election, regime gates, flip-policy resolvers)
- the rest of `src/main.py` and `pipeline.py`
- `src/core/coordinator.py` 2200-3871 (the execute and after-fill tail, typed
  dispatch, command handlers)

## What this lane could not settle, and what would settle it

1. **Whether any global cap is configured on the VM.** It is not known whether
   `MAX_DAILY_LOSS_USD` or `MAX_OPEN_POSITIONS` is set in the live unit env, and
   so whether the operator believes the dead guards protect anything.
   *Settles it:* read the trader's environment through the vm-ops relay (the
   `EnvironmentFile` for `ict-trader-live`).
2. **The live values of `CONVICTION_SIZING_MODE` and `NEWS_INFLUENCE_MODE`.**
   These decide whether the NaN and downsize findings are live or latent.
   *Settles it:* the same env read.
3. **How often the journal read fails in production.** This decides how often
   the net-position finding fires.
   *Settles it:* `journalctl?unit=ict-trader-live&lines=…` grepped for
   `current_net_position_qty: read failed`.
4. **Whether a halt ever coincided with a pairs placement.**
   *Settles it:* compare halt-flag mtimes with `pairs` rows in the journal.
5. **The parts not read.** `intents.py` election and regime code, the rest of
   `main.py` and `pipeline.py`, and the tail of `coordinator.py` need a
   follow-up lane.
