# Sprint Log: S-LIVETRADE-MGMT-S2

## Date Range
Start: 2026-06-16 · End: 2026-06-16 (single session, continuation of
S-LIVETRADE-MGMT-2026-06-16).

## Objective
Drain the five `BL-20260616-LTMGMT-*` / `DASH-UPNL` follow-ups the
live-trade-management contract sprint logged, each as its own
operator-reviewed PR, reusing the #3761 declared-capability pattern (no new
gates). Secondary (mid-session, operator directive): **consolidate the open
PR queue across this stream + two retired sessions** (unified-confidence #3796;
mobile-push #3807/#3808) and merge everything cleanly so `main` stays linear and
nothing races.

## Tier
Tier-1 (P5 CI guards, P4 read path, docs/backlog) + Tier-2/3 (IB/Alpaca/OANDA
live order-management path). Each live-path PR was operator-reviewed before
merge; all merged with explicit operator approval ("merge in order").

## Starting Context
Builds directly on S-LIVETRADE-MGMT-2026-06-16 (the two-sided contract made real
for IB/Alpaca: PnL resolution + close + reconcile as declared per-integration
capabilities, baseline-ON, no kill-switches — `BROKER_PNL_READER_EXCHANGES`,
`EXCHANGE_MANAGEMENT_CAPS`). Deferred there: trailing-SL **modify**, P4 metric
separation, P5 CI enforcement, OANDA wiring, and a multiplier-blind dashboard
fallback. M3 (Stream B conviction roadmap: P3 arbitration → P4 real-money sizing
→ P5 fusion) remains Tier-3 / operator-gated, blocked on the conviction soak
maturing — **not started this session.**

## Repo State Checked
`main` of `ict-trading-bot` (started at `dfc9c03`, ended at `a119f0c`) and
`ict-trader-dashboard` (`cb46411`). Live state pulled via the `vm-diag-snapshot`
relay (direct diag egress blocked this session): confirmed `ib_paper` holds a
live long MGC position `mgc_pullback_1d` monitors (the trailing-SL no-op target)
and `oanda_practice` is `account_mode_dry_run`. Canonical docs reviewed:
`docs/CLAUDE-RULES-CANONICAL.md`, `docs/audits/live-trade-management-contract-2026-06-16.md`,
the prior sprint log, CLAUDE.md.

## Files and Systems Inspected
`src/units/accounts/clients.py` (`EXCHANGE_MANAGEMENT_CAPS`,
`account_open_positions`), `src/units/accounts/execute.py`
(`modify_open_order` / `close_open_position`), `src/units/accounts/ib_client.py`
(`place_protective` / `close` / `_cancel_resting_orders_for_symbol`),
`src/units/accounts/alpaca_client.py`, `src/units/accounts/oanda_client.py`,
`src/runtime/order_monitor.py` (`_send_modify_to_exchange` / `_apply_update` /
`_build_account_client`), `src/web/api/routers/dashboard.py` (`_pnl_stats`,
`/stats`), `src/runtime/strategy_verdict.py`, `src/units/accounts/integrator.py`
(`EXCHANGE_MAP`); `ict-trader-dashboard/streamlit_app.py`
(`_position_upnl`/`_open_upnl`/`_render_trade_card`). Tests across the
management-caps / p3-close / snapshot-reconcile / s067 suites.

## Work Completed
1. **#3801 `BL-…-MODIFY`** (merged `4d98e40`) — IB/Alpaca SL/TP modify.
   `IBClient.modify_protective` (cancel resting OCA legs → re-arm GTC OCA via
   `place_protective`); `AlpacaClient.modify_protective` (PATCH the resting
   stop/limit legs); `execute.modify_open_order` routes both with new
   `side`/`qty`/`cur_sl`/`cur_tp` kwargs that merge the changed leg with the
   unchanged one's current value. Bybit `set_trading_stop` byte-unchanged. `modify`
   added to IB/ib/alpaca caps. `tests/test_ltmgmt_modify_wiring.py`.
2. **#3802 `BL-…-P4METRICS`** (merged `578ece5`) — `/api/bot/stats` keeps the
   headline real-money-only and adds an additive `paper` sub-block +
   `paperOpenTrades` (never blended). `_pnl_stats_for(predicate)` + the inverse
   `_PAPER_PREDICATE`; CLAUDE.md BotStats shape updated.
   `tests/test_ltmgmt_p4_metric_separation.py`.
3. **#3803 `BL-…-P5CI`** (merged `c31e005`) — CI guards: every `EXCHANGE_MAP`
   integration must declare an explicit caps entry (fixed the undeclared
   `breakout` → `frozenset()`); every strategy `monitor()` returns schema-valid
   verdicts on representative input. `tests/test_ltmgmt_p5_contract_ci.py`.
4. **#3804 `BL-…-OANDA`** (merged `a119f0c`) — OANDA caps `{close, open_positions}`,
   `account_open_positions` oanda branch (dry/no-creds → None), close routed to
   `OandaClient.close` (v20), client built in `_build_account_client`. Ships inert
   while `oanda_practice` is dry_run. `tests/test_ltmgmt_oanda_wiring.py` +
   snapshot-reconcile now includes OANDA (re-pointed its skip test at a
   genuinely-uncapped exchange).
5. **dashboard #103 `BL-…-DASH-UPNL`** (OPEN — held for preview) — `_position_upnl`
   / `_open_upnl` trust the API's multiplier-aware `unrealizedPnl`; dropped the
   `(price-entry)*qty` recompute + the now-dead `last_price` plumbing (Positions
   per-render candle fetch, `_render_trade_card` param). Pushed to
   `claude/web-app-preview`; awaiting operator preview before merge to `main`.
6. **PR-queue consolidation (M1)** — merged, in order, rebasing each onto fresh
   `main` (strict up-to-date branch protection): #3801 → #3802 → #3803 → #3807
   (adopted mobile-push gate scrub) → #3796 (adopted conviction sizing) → #3808
   (adopted backlog) → #3804. Resolved the additive #3804↔#3801 (alpaca-modify /
   oanda) and #3804↔#3803 (breakout/oanda caps) conflicts; `git rerere` cached the
   recurring one. Closed #3257 + #3173 as superseded (their content already on
   `main`; `git diff main...branch` empty — nothing lost).
7. **This wrap-up** — marked the four bot `BL-…-LTMGMT-*` items `resolved` (DASH-UPNL
   annotated open-pending-preview) in `health-review-backlog.json`; this sprint log;
   doc-freshness sweep.

## Validation Performed
Per-PR suites green and CI 12/12 on every merge: `test_ltmgmt_modify_wiring`,
`test_ltmgmt_p4_metric_separation`, `test_ltmgmt_p5_contract_ci`,
`test_ltmgmt_oanda_wiring`, plus the updated `test_integration_management_caps` /
`test_p3_close_wiring` / `test_position_snapshot_reconcile` / `test_s067_*`. Broad
local sweep 940 passed (5 pre-existing `sklearn`-missing failures in
`tests/ml/calibration`, unrelated). #3796 rebase re-verified locally (16
conviction tests incl. the no-gate guard). Dashboard #103 `ruff check .` +
`py_compile` clean. Each rebased branch's auto-merge result re-tested before push.
- **Gaps not yet verified:** dashboard #103 is NOT rendered/verified live (Streamlit
  can't run in CI/sandbox — that's the preview-app step, with the operator).
  IB/Alpaca/OANDA modify/close were not exercised against a live broker socket this
  session (unit-pinned only; live exercise needs a real fill); OANDA ships inert
  until `oanda_practice` is promoted off dry_run.

## Documentation Updated
`docs/audits/live-trade-management-contract-2026-06-16.md` (decision-2 modify DONE
row + P5 CI-guards-DONE note); CLAUDE.md (BotStats shape: `paper` block +
`paperOpenTrades`); `clients.py` / `execute.py` / `ib_client.py` /
`alpaca_client.py` docstrings; `health-review-backlog.json` (4 resolved + 1
annotated); this sprint log. The adopted #3796/#3807 carried their own doc updates
(conviction design §10 correction; mobile-push runbook + system-actions allowlist).

## Contradictions or Drift Found
None new in the canonical set. The S2 changes flipped several test assertions that
*codified* the prior Bybit-only / unwired-IB-Alpaca-OANDA reality (e.g.
`test_integration_management_caps`, `test_p3_close_wiring`,
`test_position_snapshot_reconcile`) — updated in-PR to the new declared reality, not
routed around. `breakout` was an EXCHANGE_MAP integration with no caps declaration
(the gap the P5 guard now catches) — declared `frozenset()`.

## Risks and Follow-Ups
- **Dashboard #103** awaits operator preview verification before merge (only open
  S2 item).
- **P4 client side** — dashboard/android consuming the new `/stats` `paper`
  aggregate block as its own section is additive follow-up in those repos (lists
  already label paper rows; not money-at-risk).
- IB/Alpaca **modify**: brief cancel→re-arm window (same as naked-autoprotect/close;
  strategy re-emits next tick on failure). OANDA modify/partial_close/order_status
  remain unwired (later follow-up; entry bracket + close cover the baseline).
- **Stream B M3** (conviction P3 arbitration → P4 real-money sizing → P5 v1→v2
  fusion) is Tier-3 / operator-gated, blocked on the conviction soak maturing (v2
  `conviction-meta-v1` degenerate at n≈65). Do not start without operator direction.

## Deferred Items
The follow-ups above; Stream B M3. All are improvements, not money-at-risk gaps —
the two CRITICAL live-management gaps (IB + Alpaca unmanaged) were already closed in
S1, and S2 closes modify + OANDA + the enforcement/metric gaps.

## Next Recommended Sprint
Merge dashboard #103 once previewed; then a Stream-B planning checkpoint for M3 P3
(conviction arbitration, demo) — but only after the conviction soak accrues enough
multi-input rows (the `sweep_conviction_weights.py` ≥150-row trigger / a non-
degenerate v2). Verify both with a fresh diag pull before any Tier-3 sizing change.

## Wrap-Up Check
- [x] Code inspected directly (real paths above), not inferred.
- [x] Canonical docs reviewed + updated (audit doc, CLAUDE.md, backlog).
- [x] No TRADE-PIPELINE stage changed shape (management-layer wiring; order
      placement/pipeline contract unchanged) — no TRADE-PIPELINE edit needed.
- [x] ROADMAP.md checked — M16/M14 rows already summarize the contract +
      conviction program; no dedicated S2 row required (this sprint log + the
      audit doc are the detail record).
- [x] Contradictions recorded (stale test assertions updated in-PR, breakout gap).
- [x] Unknowns stated (dashboard #103 not live-verified; brokers not socket-exercised).
- [x] Merge queue cleared, `main` linear (`dfc9c03`→`a119f0c`), zero open bot PRs.
