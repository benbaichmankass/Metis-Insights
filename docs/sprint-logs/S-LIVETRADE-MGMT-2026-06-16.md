# Sprint Log: S-LIVETRADE-MGMT-2026-06-16

## Date Range
2026-06-16 (single session).

## Objective
Operator report: two `ib_paper` (IBKR paper) trades (MGC #2597, MHG #2578)
showing `$0.00` PnL. Diagnose, fix, and — per operator direction mid-session —
treat it not as a one-off but as the **whole-repo structural gap** it exposed:
the two-sided "live-trade management" contract (every strategy monitors + updates
its live trades; every integration applies those updates + reconciles), which
was implemented only for Bybit.

## Tier
Tier-1 (investigation/docs) + Tier-2/3 (live order-management path) — each
live-path phase operator-reviewed before merge.

## Starting Context
PnL machinery + post-entry management + reconciliation were all Bybit-only;
non-Bybit live accounts (`ib_paper`, `alpaca_paper`, both `mode: live`) could
ENTER trades (broker bracket) but their strategies' update/close verdicts hit
`no_client` and never reached the exchange, and their closed trades never got a
realised PnL. Builds on the 2026-06-15 exit-coverage rebuild row.

## Repo State Checked
`main` of `ict-trading-bot`, `ict-trader-dashboard`, `ict-trader-android`.
Live state pulled via the `vm-diag-snapshot` relay (trades/order_packages
journal, trader journalctl, services).

## Files and Systems Inspected
`order_monitor.py` (reconcilers, senders, sweeps, `_build_account_client`),
`clients.py` (`account_*` primitives), `execute.py` (modify/close), `ib_client.py`,
`coordinator.py`, `dashboard.py` `/positions`; the Streamlit + Android positions
surfaces; `new-strategy`/`new-broker` skills; the canonical docs.

## Work Completed (all merged to `main`)
1. **Local-PnL fallback** (#3761) — `src/runtime/local_pnl.py` + `_sweep_local_pnl_for_unpriced`;
   PnL source is a **declared integration capability** (`BROKER_PNL_READER_EXCHANGES`),
   default-local, broker-truth where declared. Verified live: orphaned MGC #2596
   backfilled to +$2600 (mark-to-market, audited). `coordinator.py` meta-init
   closes the #2578 NULL `order_package_id` link gap.
2. **Paper-open visibility** — dashboard #101 + android #53: paper opens labeled
   on Overview/Accounts (kept strictly separate from real-money per operator).
3. **P1** (#3785) — strategy `monitor()` verdict contract: `strategy_verdict.py`
   schema + validator + CI signature guard + `new-strategy` step.
4. **P2** (#3787) — per-integration management capability layer
   (`EXCHANGE_MANAGEMENT_CAPS` + `account_supports_management`); senders return
   honest `unsupported_op` (not `no_client`) + throttle; Bybit byte-unchanged.
5. **P3** (#3792) — wire IB + Alpaca `close` to the strategy exit path
   (IB: cancel-bracket + opposing reduce sized to live position; Alpaca: native
   flatten); `_build_account_client` builds IB/Alpaca; **no kill-switch**.
6. **P3b** (#3795) — universal **position-snapshot reconciliation** for
   non-order-status integrations (close-on-flat, 2-observation confirm,
   never on read-failure) + **IB gateway-health gate**: `account_open_positions`
   returns `None` (not `[]`) for a logged-out IB gateway (`net_liquidation`
   unpopulated), sealing the sustained-logout false-close hole.
7. **Gate removal** (#3789) — removed `LOCAL_PNL_COMPUTE_DISABLED`; the fallback
   is unconditional baseline correctness (operator directive: no unnecessary
   gates; mirrors the `NAKED_POSITION_AUTOPROTECT`/`MONITOR_RECONCILE_ENABLED`
   removals).
8. **Design + docs** — `docs/audits/live-trade-management-contract-2026-06-16.md`
   (#3775/#3788/#3791), `docs/audits/local-pnl-fallback-2026-06-16.md`.

## Validation Performed
Per-PR test suites green (local_pnl, integration_management_caps,
p3_close_wiring, position_snapshot_reconcile, reverse/forward reconciler,
env-gate-survivor, coordinator); all repo CI guards green; live backfill
verified via the diag relay. IB close + the snapshot-reconcile false-close
paths pinned by tests (logged-out gateway never closes; Bybit byte-unchanged).

## Documentation Updated
This sprint log; ARCHITECTURE-CANONICAL 2026-06-16 change-log row; CLAUDE.md
(env-table `LOCAL_PNL_COMPUTE_DISABLED` row removed + Position-shape
`markprice_local`/local-PnL note restored); env-gate-purge audit (LOCAL_PNL
recorded as considered-and-removed, survivor count → four); `new-broker`
(PnL-source step 2b) + `new-strategy` (monitor step) skills; the two audit docs.

## Contradictions or Drift Found
None in the canonical set (doc-freshness sweep): no canonical doc enshrined the
old "Bybit-only PnL / no local calculator" rule (code-docstring only), so the
fallback contradicts nothing; no dangling `LOCAL_PNL_COMPUTE_DISABLED` refs;
two-gates + tiers + no-new-gate invariants hold.

## Risks and Follow-Ups (logged to health-review-backlog.json)
- Trailing-SL **modify** for IB/Alpaca not yet wired (deferred P3 optimization).
- **P4** real/paper metric separation (bot `/stats` `openTrades` still
  real-money-only; clients show paper opens in lists but no separate paper
  aggregates).
- **P5** CI guards enforcing the contract (every `EXCHANGE_MANAGEMENT_CAPS`
  integration implements management; every `monitor()` schema-valid).
- OANDA management (close/open_positions) before it leaves `dry_run`.
- Streamlit `_position_upnl` client-side fallback is multiplier-blind (moot
  now the API returns the value; dashboard-repo cleanup).

## Deferred Items
The above follow-ups; all are improvements, not money-at-risk gaps (the two
CRITICAL live-management gaps — IB + Alpaca unmanaged — are closed).

## Next Recommended Sprint
S-LIVETRADE-MGMT-S2: trailing-SL modify (IB/Alpaca) → P4 metric separation →
P5 CI enforcement. See the handoff prompt.

## Wrap-Up Check
doc-freshness run (no contradictions); follow-ups logged to the health backlog;
all PRs merged + deployed via git-sync.
