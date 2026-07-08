# Sprint Log: S-IBKR-EQUITY-ETF-SUPPORT-2026-07-07

## Date Range
- Start: 2026-07-07 (design doc written 13:05 UTC)
- End: 2026-07-08 (04:56 UTC — step-6 merge confirmed deployed + step-7 verification check)

## Objective
- Primary goal: give IBKR (`ib_paper`) the ability to trade US equity ETFs
  (`STK` contracts) alongside its existing MES/MGC/MHG futures, so the
  Alpaca ETF strategy family can also run on IBKR as a second paper venue
  and, eventually, a real-money route.
- Secondary goals: keep MES/MGC/MHG behaviour byte-identical; gate any
  real routing decision on the mandatory `account_compat_matrix` evidence
  run against `ib_paper`'s own risk ruleset, not a copy-paste of
  `alpaca_paper`'s strategy list.

## Tier
- Tier 1/2 for steps 1-5 (contract-building code, resolver, sizing — no
  live-path behaviour change until routed); Tier 3 for step 6
  (`config/accounts.yaml` wiring — operator-approval-gated).
- Justification: steps 1-5 are purely additive capability (new STK branch,
  new resolver, per-order sizing resolution) with zero effect on any
  account until a strategy is actually routed onto it; step 6 changes
  `ib_paper.strategies`/`.symbols`, a live order-routing surface.

## Starting Context
- Active roadmap items: M15 "Market & Platform Migration" (IBKR/Alpaca/OANDA
  multi-venue buildout).
- Prior sprint reference: the M15 Alpaca ETF rollout sprints
  (S-M15-PHASE0/2/4, S-EXPANSION-ETF-BREADTH) built the ETF strategy family
  on `alpaca_paper`/`alpaca_live`; `ib_paper` had never gained equity support.
- Known risks at start: `IBClient._build_contract` raised `ValueError` for
  any non-MES/MGC/MHG symbol — a hard blocker, not a config gap.

## Repo State Checked
- Branch or commit reviewed: `main` at session start (2026-07-07 AM);
  build on `claude/ibkr-equity-etf-support-fzri2h` (steps 1-5, PR #5871)
  then `claude/ibkr-ib-paper-etf-wiring-followup` (step 6, PR #5914).
- Deployment state reviewed: confirmed live-VM `git_sha` via
  `/api/diag/version` post-merge (issue #5926) — `1f324cb`, one commit
  ahead of the step-6 merge commit `d811bd7`.
- Canonical docs reviewed: `CLAUDE.md` (bot repo), `docs/ARCHITECTURE-CANONICAL.md`,
  `config/instruments.yaml`, `config/accounts.yaml`.

## Files and Systems Inspected
- Code files inspected: `src/units/accounts/ib_client.py`,
  `src/core/coordinator.py`, `src/units/accounts/risk.py`
  (`WHOLE_UNIT_QTY_EXCHANGES`), `src/runtime/strategy_signal_builders.py`
  (market-hours gate).
- Config files inspected: `config/instruments.yaml`, `config/accounts.yaml`,
  `config/strategies.yaml` (the 10 Alpaca ETF cell definitions).
- Deployment files inspected: none (no service/unit changes).
- Docs inspected: `docs/integrations/prop-accounts-architecture-DESIGN.md`
  (compat-matrix precedent), `docs/audits/live-trade-management-contract-2026-06-16.md`.
- Services or timers inspected: none directly; `ict-web-api` diag confirmed
  post-deploy `git_sha`.
- GitHub Actions workflows inspected: `trainer-vm-diag.yml` (dispatched the
  compat-matrix run, issue #5908), `vm-diag-snapshot.yml` (issue #5926).

## Work Completed
- Design doc written: `docs/integrations/ibkr-equity-etf-support-DESIGN.md`
  (4 open questions answered by the operator 2026-07-07: all 10 Alpaca ETFs,
  reuse `ib_paper`, keep the Alpaca/yfinance signal-candle source, real-money
  IBKR is the eventual goal).
- **Step 1 (#5871):** `config/instruments.yaml::instruments.<SYM>.ib` block
  (config-driven `{sec_type: FUT|STK, exchange, primary_exchange, currency}`)
  + `src/units/accounts/ib_instruments.py::ib_instrument_spec()` resolver,
  legacy-FUT-map fallback, 26 tests.
- **Step 2 (#5871):** STK branch in `IBClient._build_contract`
  (`Stock(sym,'SMART','USD',primaryExchange=...)` + `qualifyContracts`),
  symbol-keyed contract cache, equity penny-tick `tick_size_for` resolution.
- **Step 3 (#5871):** `Coordinator.multi_account_execute` resolves
  `market_type`/`whole_units` per order via
  `ib_instruments.ib_order_market_type()` (symbol-aware) instead of trusting
  the account's static `market_type: futures`.
- **Steps 4-5 (#5871):** confirmed no new work needed — equity
  `contract_value_usd` entries already present; the US-equity market-hours
  gate is already strategy-side and applies unchanged to the IB route.
- **Step 6 (#5914, Tier-3):** fixed 2 missing cells
  (`slv_pullback_1d`/`gdx_pullback_1d`) in
  `scripts/ops/etf_account_compat.sh`'s `CELLS` array, then ran the
  mandatory `ACCOUNTS=ib_paper bash scripts/ops/etf_account_compat.sh`
  (issue #5908, trainer VM) across all 16 alpaca-ETF cells. 13/16 scored (3
  skipped — no trainer-VM candle CSV for TQQQ/QLD/GDX); only 4 scored
  ROUTE against `ib_paper`'s own `risk_pct: 0.015`:
  `spy_trend_long_1d` (P(breach)=0.0107), `qqq_trend_long_1d` (0.005),
  `iwm_trend_long_1d` (0.0423), `tlt_pullback_1d` (0.0223) — all
  survival=1.0. Wired only those 4 onto `ib_paper.strategies`/`.symbols`.
- **Step 7 (partial — verification check, not yet observed):** confirmed
  the step-6 merge deployed live (`git_sha=1f324cb`); searched the trades
  journal for a real `ib_paper` fill on SPY/QQQ/IWM/TLT — none yet
  (expected, daily-cadence cells). Stays open.

## Validation Performed
- Tests run: full IB test suite (68 tests) + sizing suite (36 tests) green
  after steps 1-5; 145 tests (`test_ib_integration.py`,
  `test_ib_sizing_and_data.py`, `test_ib_instruments.py`,
  `test_accounts_integration.py`) green after step 6, re-verified across 3
  rebase-onto-`main` cycles as the repo merged concurrently.
- Dry-runs or staging checks: none applicable (config/contract-building
  change, no dry-run harness for IB contract resolution).
- Manual code verification: confirmed MES/MGC/MHG's `_build_contract` path
  byte-identical (same `ContFuture→Future` shape, exchange now sourced from
  the resolver instead of a hardcoded dict literal, same CME/COMEX values).
- Gaps not yet verified: step 7 — no real IB paper equity fill observed
  end-to-end (place → journal → monitor → close) yet; the 4 wired cells are
  daily-cadence and haven't hit a candle close since wiring (checked
  2026-07-08 04:56 UTC, issue #5926).

## Documentation Updated
- Rules doc updates: none (no rule change).
- Architecture doc updates: `docs/ARCHITECTURE-CANONICAL.md` — 2 change-log
  rows (steps 1-5, step 6) added during the PRs; this session's
  doc-freshness pass additionally fixed a stale "Index/metals futures
  (IBKR `ib_paper`)" roster bullet that omitted the new equity cells
  entirely (§ Step 2 strategy roster).
- Trade pipeline doc updates: none.
- Roadmap updates: this entry (M15 addendum + "Last Updated" header).
- GitHub Actions doc updates: none.
- Subsystem doc updates: `docs/integrations/ibkr-equity-etf-support-DESIGN.md`
  status header + §5/§6 updated from "PROPOSAL" to reflect steps 1-6 done,
  step 7 open (this session's doc-freshness pass — was still reading
  "PROPOSAL for operator review (not built)" after merge).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- Contradiction 1: `config/accounts.yaml::ib_paper`'s comment block still
  read "this account trades MES/MGC/MHG only; `IBClient._build_contract`
  raises `ValueError` for any other symbol" — stale after step 6 wired
  4 equity symbols onto the same account. Fixed (comment-only).
- Contradiction 2: the design doc's status line said "PROPOSAL for operator
  review (not built)" after both build PRs had merged. Fixed.
- Code/doc mismatch: `docs/ARCHITECTURE-CANONICAL.md`'s "Current roster, by
  instrument" bullet for `ib_paper` didn't mention the equity ETFs. Fixed.
  (Noted but NOT fixed, pre-existing and out of this session's scope: the
  same section's "12 strategies registered... verified 2026-06-10" line is
  independently stale — the roster has grown well past 12 since; logged to
  the health-review backlog.)

## Risks and Follow-Ups
- Remaining technical risks: if the IB gateway is mid-wedge when the first
  SPY/QQQ/IWM/TLT daily signal fires, that fill could exchange-reject —
  not a bug in the new STK code (an unrelated MGC order hit exactly this
  class of gateway-liveness rejection in the same session, see
  `PB-20260707-IBKR-STK-ETF-SUPPORT`'s latest update) but worth checking
  `/api/diag/ib_state` first if a wired-cell fill looks stuck.
- Remaining product decisions (Tier 3): the 9 SKIP cells + 3 no-data cells
  stay off `ib_paper` pending either a lower `risk_pct` re-score or organic
  evidence from the 4 wired cells; real-money `ib_live` ETF routing is a
  separate, future Tier-3 decision (design doc §5 Q4 — goal confirmed, not
  yet scheduled).
- Blockers: none.

## Deferred Items
- Deferred item 1: step 7 live paper verification (real fill observed
  end-to-end) — re-check after the next US-equity daily close (~21:00 UTC).
- Deferred item 2: TQQQ/QLD/GDX daily candle CSVs on the trainer VM (would
  unblock scoring the 3 no-data cells).

## Next Recommended Sprint
The next `/system-review` or a dedicated diag pull should re-check
`ib_paper` trades for SPY/QQQ/IWM/TLT fills to close out step 7; if a real
fill has landed, verify PnL resolution (whole-share sizing,
`unrealizedPnlSource`) matches the design's expectations.
