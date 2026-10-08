# Pairs strategies stocktake — 2026-10-07

> **Doc status:** `live` · category `evidence` · last verified `2026-10-07` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> Lane `PAIRS-STOCKTAKE` (session_011BUSVxr9kPZvkNn6TXSaL6), dispatched by Manager
> Session 2026-10-01 (session_01MM8o5js6TcDFeNAPBY4Ntv) on pipeline item
> `PI-20261006-APBY4NTV-0004`. Operator, 2026-10-06 ~08:15Z, verbatim: *"where do
> we stand on all of the pairs strategies? Is anything there ready to go live,
> and is anything there feasible for a prop account?"*

Every number below is marked **MEASURED** (with where it lives), **INFERRED**
(from named measurements) or **DECIDED** (operator). Re-run the locators rather
than quoting the figures.

## The answer in one table

| leg | stage | committed Stage-0 evidence | live / soak record | live-ready (S1→S2)? | prop-feasible? | the gap → unit |
|---|---|---|---|---|---|---|
| `pairs_sol_eth` (SOL/ETH 1h) | Stage 1, `execution: live` on `bybit_1` paper | **none** — no `comms/strategy_evidence/pairs_*` record (E37). Only prior numbers: G2 2026-07-16, fee-free fixed-β **+$1279**, at 7.5 bps taker **−$3443** (sprint log, not a record) | 305 closed legs since 2026-08-01, 153 a / 152 b; soak 09-30→10-07: 19 opens, 17 closes, 1 `half_open` | **NO** — no Stage-0 record; no Gate-1 cost-fidelity cell; not on any `accounts.yaml` roster so the mandate resolver cannot see it | **NO** on all three venues — no 2-leg prop order path; legs size to ~0.30 SOL / 0.013 ETH at $25 risk (ETH floors to 0.01, ~23 % hedge error) | Stage-0 at configured params: **RQ-20261007-005**; prop fit: **blocked/RQ-20261007-009** |
| `pairs_bnb_btc` (BNB/BTC 1h) | Stage 1, `execution: live` on `bybit_1` paper | **none**. G2: fee-free **+$357**, at taker **−$3409** | 286 closed legs since 08-01, **145 a / 141 b** (4 unmatched); soak 09-30→10-07: 23 opens, 22 closes, 2 `open_failed`, 4 `skip_size` | **NO** — same gaps | **NO** — BTC leg sizes to ~$1 notional at $25 risk (0.00001 BTC); BTCUSD is not in `breakout_2` / `tradeify_1` lot tables and is below `velotrade_1`'s 0.001 min | **RQ-20261007-006**; prop: **blocked/RQ-20261007-009** |
| `pairs_sol_btc` (SOL/BTC 1h) | Stage 1 **shadow** (`execution: shadow`) | **none**. G2: fee-free **−$295**, at taker **−$3179** | no placements by design; soak 09-30→10-07: 18 `shadow_open` | **NO** — no record, no soak placements | **NO** — BTC leg unplaceable at prop risk (as above) | **RQ-20261007-007** |
| `pairs_eth_btc` (ETH/BTC 1h) | Stage 1 **shadow** | **none**. G2: fee-free **−$855**, at taker **−$5194** | soak 09-30→10-07: 4 `shadow_open` | **NO** | **NO** — BTC leg unplaceable at prop risk | **RQ-20261007-008** |
| GLD/GDX 1d (not a configured leg) | Stage 0 only | `research/results/RQ-20260929-105/37188716030.jsonl`: net **+18.72 R**, n 144, harness defaults, one pooled 10-year window | none — no equity pairs executor exists | **NO** — not a leg; one pass at defaults, no IS/OOS, no executor | n/a (equities; no prop venue here trades GLD/GDX) | confirmatory IS/OOS unit is owed: `PI-20261004-P24XMV4V-0003` / `PI-20261004-UQ9JJPRQ-0002` (already filed) |
| SPY/QQQ 1h (not a configured leg) | Stage 0 only | `research/results/RQ-20260929-106/37205970522.jsonl`: net **−121.96 R**, n 178 (FAIL, first pass) | none | **NO** | n/a | confirmatory run owed per convention (same rows as above) |

**Short answer.** Nothing in the pairs family is live-ready, and nothing is
prop-feasible today. Not because the measurements say no — because for the four
configured pairs **the measurement that the promotion mandate reads does not
exist**, and for the prop venues **the order path does not exist**. Both are
tasks, and both are now registered (below). No question goes to the operator
except the one no data settles (§ 4, Velotrade's "arbitrage" clause).

## 1. Inventory — what exists, measured this session

**Configuration (MEASURED, `config/pairs.yaml`, read 2026-10-07):** one isolated
2-leg sleeve, top-level `account_id: bybit_1` (paper), four pairs, all 1h,
lookback 15 / entry_z 2.0 / exit_z 0.5 / stop_z 2.0 / max_hold_bars 20 /
`hedge_beta: rolling`. `pairs_sol_eth` and `pairs_bnb_btc` are `execution:
live`; `pairs_sol_btc` and `pairs_eth_btc` are `execution: shadow`. The live
pair was **DECIDED** by the operator on 2026-07-16 on the fee-free G2 figure
("if it's not negative, trade it on paper, but not on real money").

**Order path (MEASURED, `src/units/strategies/pairs_executor.py`):**
`run_pairs_tick` is called once per tick from `src/main.py`, never via
`multi_account_execute`. It resolves the account **per pair**
(`pair.get("account_id") or default_account`, line ~1185), builds a client only
when the account's exchange is `bybit` (`_client_for`), places both legs through
`execute_pkg` with `qty_override`, unwinds leg A if leg B fails, and journals the
legs as `pairs_<name>_a` / `_b` linked by `pairs_group_id`. There is no prop,
Alpaca or IB client in this path.

**Evidence records (MEASURED):** `ls comms/strategy_evidence | grep -i pairs`
returns nothing. `scripts/ops/build_strategy_evidence.py` and
`regime_debt_matrix.py` contain no `pairs` family, so the producer cannot write
one. Checklist **E37** (queued) is exactly this. The Gate-1 cost-fidelity
record `comms/research/r3_cost_fidelity/2026-09-25.json` grades 9 cells, none
pairs, although its `__rows.jsonl` carries `pairs_sol_eth_a/_b` fills — the
grader has the fills and emits no cell (filed, § 5).

**Research units that touch pairs (MEASURED, `grep -li pairs research/queue`):**
`RQ-20260929-105` GLD/GDX PASS and `RQ-20260929-106` SPY/QQQ FAIL (both at
harness defaults via `research-harness-dispatch.yml`); `RQ-20260922-011`
(blocked: "is there ANY pair that survives fees") whose `blocked_on` said
*nothing routes `scripts/backtest_pairs.py`* — stale since E7 landed the pairs
harness (run 37188716030 proves it); corrected in this PR, rule untouched.
Manager relay 2026-10-07 18:39Z (lane HARNESS-AUDIT-FIXES, CA-B04): the
universe scan that unit names (`scripts/research/pairs_universe_scan.py`)
screens cointegration at an ADF critical value of −2.86 instead of the
Engle-Granger 2-variable ~−3.34 and fits its OOS vector on the full sample, so
`RQ-20260922-011` **must not run until the HARNESS-AUDIT-FIXES PR lands**; no
landed pairs verdict used that script (0 of 334 `produced_by` records), and
nothing registered here does either — `RQ-20261007-005..008` use
`scripts/backtest_pairs.py` only.

**Live record (MEASURED, `GET /api/bot/trades/closed?account_id=bybit_1&since=2026-08-01&include_paper=true&include_demo=true`, paged, read 2026-10-07 ~18:10Z):**
1,034 closed `bybit_1` rows, 591 of them `pairs_*` legs: `pairs_sol_eth_a` 153,
`_b` 152, `pairs_bnb_btc_a` 145, `_b` 141. `closeReason`: `other` 511,
`reconciler` 80. `pnlProvenance`: estimated 345, measured 129, unverified 115,
null 2. Summed `realizedPnl` (this field's own basis; fee treatment not
established here): `pairs_sol_eth` **+$294** over 305 legs, `pairs_bnb_btc`
**−$294** over 286 legs. **This is a paper-mechanics record, not an edge
claim** — CLAUDE.md § ladder: a paper book never establishes edge. Two mechanics
findings stand out and are filed (§ 5): the a/b leg counts do not match (4
unmatched on bnb_btc, 1 on sol_eth), and 80 closes came from the reconciler.

**Soak (MEASURED, `GET /api/bot/pairs/soak?limit=1000`, 2026-09-30T00:05Z → 2026-10-07T18:07Z):**
`pairs_bnb_btc` 23 `open` / 22 `close` / 2 `open_failed` / 4 `skip_size`;
`pairs_sol_eth` 19 / 17 / 0 / 0, 1 `half_open`; shadow pairs 18 and 4
`shadow_open`. Position mode on the venue is hedge (`position_idx_state:
hedge_long/hedge_short` on every open row).

## 2. Live-ready — the bar and the exact gap

`MD-PROMOTE-S1-S2` (`config/mandates.yaml`, armed, `cost_tolerance_bps: 2.0`)
fires only for a leg **already on the Stage-1 soak roster for its exchange**
with **a committed Stage-0 record passing B1's four clauses** whose **Gate-1
cost-fidelity verdict reads `consistent`**, under the 25 % auto-share cap, onto
a `real_money` account and its mirror. For every pair:

1. **No Stage-0 record** (clause fails as `no_record`, which is not a pass).
   The 2026-07-16 numbers are fee-free, in dollars, at the then-balance, and
   live in a sprint log; at taker fees every pair was deeply negative. **The
   data task:** `RQ-20261007-005..008` — one unit per pair, the pairs engine at
   the **configured** parameters, net of fee + slippage + per-leg funding,
   rule registered before the run (PASS iff net_total_r > 0 and n ≥ 39),
   mechanically graded, and landing a committed per-pair-trade ledger. Routed
   through `research-script-run.yml` and a new allowlisted command,
   `scripts/research/pairs_configured_stage0.py` (self-tested; the dispatcher
   dry run shows all four `would_dispatch route=runner`).
2. **No Gate-1 cost-fidelity cell** for any pairs leg (`no_record` is not a
   pass). Filed as a pipeline row with the grader as `rerun` (§ 5).
3. **Not a rostered leg.** The mandate resolver reads `config/accounts.yaml`
   rosters and `config/strategies.yaml`; the pairs sleeve lives in
   `config/pairs.yaml`, so even with (1) and (2) satisfied the mandate has no
   leg to evaluate. A promotion would be a Tier-3 proposal naming the pair,
   not a mandate fire — stated here so nobody waits for one.
4. For the two shadow pairs, additionally **no Stage-1 placements**, by
   design; their G2 fee-free sign was already negative. They stay in the
   Stage-0 units because the configured-parameter, full-cost read has never
   been taken for them either.

**INFERRED (from G2's three-layer teardown and the cost stack):** the
configured sleeve's gross edge at fixed-β execution is thin (~$0.5/trade at the
2026-07 basis) against ~$1.1–1.8/trade of two-leg taker fees. The
configured-parameter runs will measure whether the rolling-β harness edge
survives the full cost stack at all; if `RQ-20261007-005..008` FAIL twice, the
right proposal is to move the two live paper pairs to `shadow` (Tier-3,
operator) and let `RQ-20260922-011` (universe scan / maker arm) decide the
sleeve's future.

## 3. Prop-feasible — per venue

Both legs of a pair must be (a) tradeable instruments, (b) permitted as a
simultaneous long/short, (c) placeable atomically on that venue's order path,
and (d) sized under the ruleset's daily-loss line.

| check | `tradeify_1` (DXtrade web) | `breakout_2` (phone terminal) | `velotrade_1` (DXtrade REST) |
|---|---|---|---|
| (a) instruments listed in the executor's lot table (MEASURED, `config/prop_platforms.yaml`) | ETHUSD, SOLUSD, XRPUSD — **no BNB, no BTC** | ETHUSD, SOLUSD — **no BNB, no BTC** | ETHUSD, SOLUSD, XRPUSD, BTCUSD (BTC min 0.001) — **no BNB** |
| (b) hedge / opposite-position rule | **no hedging** = no long+short *in the same instrument* and no cross-account hedge (`tradeify_247_1step.yaml` header; feasibility memo 2026-09-30). A SOL-long/ETH-short pair is two instruments — not the banned shape. 20-second minimum hold. | Breakout hedging article (intercom 11644103, read 2026-10-07): hedge mode on, same-asset long+short allowed in one account; cross-account and cross-trader hedging banned; **silent on cross-asset pairs and on arbitrage**. | hedging **allowed within one account**; Terms 8.2 **ban "arbitrage"** (ruleset header). Whether a statistical-arbitrage spread is "arbitrage" under those terms is **not a measurement** — § 4. |
| (c) 2-leg atomic order path | **none** — the prop bridge emits one ticket per leg (`emit_prop_ticket`), executed as independent tickets; `pairs_executor` never reaches it (`_client_for` returns `None` for a non-Bybit exchange) | **none** — one phone ticket per leg by construction (a ticket carries one symbol, `src/prop/phone_executor.py`); no unwind-on-partial | **none** — REST executor is single-ticket; no leg-imbalance unwind |
| (d) sizing under the daily-loss line (INFERRED from the two 2026-10-07 soak `open` rows, `budget_usd` 2056 → leg notionals, scaled to flat risk) | $50/ticket vs $300/day line: SOL/ETH legs ≈ 0.59 SOL / 0.026 ETH (ETH floors to 0.02, ~23 % hedge error); BNB/BTC's BTC leg ≈ $2 notional — unlisted anyway | $25 vs $150/day: ≈ 0.30 SOL / 0.013 ETH (ETH floors to 0.01, ~23 % error); BTC-quote pairs unlisted | $50 vs $200/day: SOL step 0.1 → 0.5 SOL (16 % under), ETH 0.02; BTC leg 0.00003 BTC vs 0.001 min → **unplaceable** |

**Verdict:** no pair is prop-feasible on any venue today. The binding gap on
every venue is **(c)** — there is no 2-leg order path for a prop account, and
building one is a Tier-3 build, not a config edit. The BTC-quote pairs are
additionally **(a)/(d)**-infeasible at prop risk. `pairs_sol_eth` is the only
pair whose two legs are listed on all three venues; its prop economics are the
data task `blocked/RQ-20261007-009` (prop_ev_sim arms for the three rulesets at
the accounts' flat risk, plus a per-leg lot clause the simulator cannot see),
blocked on a Stage-0 PASS ledger from `RQ-20261007-005`.

## 4. What goes to the operator, and why only this

Per the 2026-09-29 directive, a data gap is a task, not a question. Everything
above is a task now. One item is a genuine preference no data settles:
**Velotrade's Terms 8.2 "arbitrage" ban** — whether a market-neutral crypto
spread counts, and whether we would run one there at all. It does not block
anything today (no path exists), so it is recorded here for the next daily
sync's "decisions for you", not raised as a popup.

## 5. Findings fixed or filed this session

- **FIXED (this PR, Tier-1):** `scripts/ci/check_roster_promotion_evidence.py`
  read only the **top-level** `config/pairs.yaml::account_id`, while the
  executor honours a **per-pair** `account_id`. An edit adding `account_id:
  bybit_2` under one live pair would have armed it on real money without
  touching the line the guard watched — the money-at-risk class the guard
  exists for. `pairs_state` now resolves the effective account per pair; new
  self-test cases P7c/P7d and three real-config tests in
  `tests/test_roster_promotion_evidence.py` (the real `config/pairs.yaml` is
  planted with a `bybit_2` override and the finding is asserted).
- **CORRECTED:** `research/queue/blocked/RQ-20260922-011.yaml::blocked_on`
  (stale "nothing routes the harness"), and checklist **E36**'s premise ("no
  per-pair account assignment") — the executor reads `pair.get("account_id")`
  today; E36's note now says so (its state is the manager's to change).
- **FILED (pipeline):** Gate-1 cost-fidelity cell absent for pairs legs while
  fills exist; pairs leg-close asymmetry (145 a / 141 b, 153 / 152) and 80
  `reconciler` closes on the paper pairs since 08-01; the pairs paper soak's
  alarm (`clears_when` tied to `RQ-20261007-005..008` landing).
- **REGISTERED (research/queue):** `RQ-20261007-005..008`,
  `blocked/RQ-20261007-009`.

## Locators

- Config: `config/pairs.yaml`, `config/mandates.yaml` (MD-PROMOTE-S1-S2), `config/prop_platforms.yaml`, `config/prop_rulesets/{breakout_turbo_1step,tradeify_247_1step,velotrade_classic_1step}.yaml`.
- Code: `src/units/strategies/pairs_executor.py` (`run_pairs_tick`, `_client_for`, `_place_pair`), `scripts/backtest_pairs.py`, `scripts/research/pairs_dollar_lots.py`.
- Live reads (2026-10-07): `/api/bot/trades/closed` (paged, bybit_1, since 2026-08-01), `/api/bot/pairs/soak?limit=1000`.
- Prior evidence: `docs/research/pairs-sleeve-real-money-readiness-2026-07-16.md` § G2, `docs/sprint-logs/S-M22-D2-PAIRS-READINESS-2026-07-16.md`, `research/results/RQ-20260929-105/`, `research/results/RQ-20260929-106/`.
- Breakout hedging rule: https://intercom.help/breakoutprop/en/articles/11644103 (read 2026-10-07).
