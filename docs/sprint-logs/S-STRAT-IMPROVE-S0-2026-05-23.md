# Sprint Log: S-STRAT-IMPROVE-S0

## Date Range
- Start: 2026-05-23
- End:   2026-05-23

## Objective
- Primary goal: Kickoff of the Strategy Improvement Program. Inspect the
  repo deeply, confirm canonical paths and live wiring, confirm the
  repo-driven comms path, characterize the current strategy-performance
  problem, and produce an actionable multi-sprint execution plan.
- Secondary goals: record any code/doc drift found during inspection;
  set up a clean handoff so the next session can start S1/S2 cleanly.

## Tier
- Tier 1.
- Justification: documentation + planning only. No code, config,
  workflow, or deployment files were changed. New files are a program
  plan (`docs/sprint-plans/STRATEGY-IMPROVEMENT-PROGRAM-2026-05-23.md`)
  and this sprint log, plus a roadmap pointer. Zero live-path impact.

## Starting Context
- Active roadmap items: M11 (multi-strategy refactor) COMPLETE; MES
  paper trading live since 2026-05-22. Next milestone was TBD. This
  program maps onto M7 (Strategy review gate) + M8 (Strategy tuning) +
  the weekly Strategy Improvement Review recurring session.
- Prior sprint reference: `S-VWAP-POLICY-INVESTIGATION-2026-05-19`
  (concluded vwap losses are structural, not regime-gate fixable; next
  step = strategy-params investigation with a long/short split) and
  `S-TRAINER-BT-1` (backtest sweep infra; vwap revert to SL=0.5 deployed
  2026-05-17). Also `S-MES-GOLIVE-2026-05-22`.
- Known risks at start: strategy/risk changes are Tier-3; the live
  trader is real money on bybit_2; repo `main` may differ from live VM
  runtime state.

## Repo State Checked
- Branch or commit reviewed: `claude/strategy-improvement-program-EZi1X`
  (both repos), clean working tree, tracking origin. Bot repo HEAD at
  `461bcb0` (delete-merged-branches relay). Dashboard repo HEAD at
  `33a727d`.
- Deployment state reviewed: confirmed deploy flow is merge→`main` →
  `ict-git-sync.timer` (5 min) → service reload, with explicit ops via
  `operator-actions.yml`. Did NOT pull live VM runtime state this sprint
  (deferred to S2 per the sprint discipline — S0 is mapping, S2 is the
  performance audit).
- Canonical docs reviewed (end-to-end): root `CLAUDE.md`,
  `docs/CLAUDE-RULES-CANONICAL.md`, `docs/ARCHITECTURE-CANONICAL.md`,
  `ROADMAP.md`, `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`.

## Files and Systems Inspected
- Code files inspected: `src/units/strategies/vwap.py` (module
  constants + R:R contract block), `scripts/ops/strategy_performance_audit.py`
  (full read), `src/units/accounts/risk.py` (signatures), strategy module
  inventory under `src/units/strategies/`, `src/backtest/` inventory.
- Config files inspected: `config/strategies.yaml` (full),
  `config/accounts.yaml` (full), `config/instruments.yaml` (listed).
- Deployment files inspected: `deploy/` unit/timer set via
  ARCHITECTURE-CANONICAL § Deployment; `.github/workflows/operator-actions.yml`
  allowlist (action→script mapping).
- Docs inspected: `docs/sprint-plans/CURRENT-SPRINT.md`,
  `S-VWAP-POLICY-INVESTIGATION-2026-05-19.md`, comms README/schema,
  the existing `REQ-*.json` artifact.
- Services or timers inspected: ict-git-sync, ict-trader-live,
  ict-web-api, ict-telegram-bot, liveness-watchdog, heartbeat (via docs).
- GitHub Actions workflows inspected: `operator-actions.yml` (allowlist),
  references to `vm-diag-snapshot.yml`, `trainer-vm-diag.yml`,
  `vwap_backtest_sweep_action.sh`, `strategy_performance_audit_action.sh`.

## Work Completed
- Mapped the full system: 3 live strategies (turtle_soup, vwap,
  ict_scalp_5m), 2 live symbols (BTCUSDT/Bybit, MES/IB paper), 1
  real-money account (bybit_2, vwap only).
- Confirmed the canonical paths table (entrypoint, strategies, risk,
  config, backtest, deploy, comms).
- Confirmed the existing tooling for the program: `strategy-performance-audit`,
  `vwap-backtest-sweep`, `bybit-account-audit`, `inspect-closed-pnl`
  operator actions; `vm-diag-snapshot` + `trainer-vm-diag` relays;
  `pull-and-deploy` + `restart-bot-service` for rollout.
- Characterized the primary problem: bybit_2 vwap ~18% WR, long/short
  asymmetry (~10.9% long vs ~40.9% short), regime-policy tuning shown
  flat — structural strategy params/exits are the suspected cause.
- Wrote the program plan
  (`docs/sprint-plans/STRATEGY-IMPROVEMENT-PROGRAM-2026-05-23.md`): 7
  sprints (S0–S6), tier mapping, tool inventory, comms flow, safety
  constraints.
- Added a ROADMAP.md pointer + ledger row for this program.

## Validation Performed
- Tests run: none (documentation-only sprint).
- Dry-runs or staging checks: none.
- Manual code verification:
  - `config/strategies.yaml` read whole — confirmed the 3 strategies'
    live params (turtle_soup: TP1@1R/TP2@3R/partial 0.25/trail 1.2 ATR/
    BE@0.75R; vwap: BE@1R + vwap_cross gates; ict_scalp_5m: TP@1.5R,
    HTF bias on, single TP).
  - `config/accounts.yaml` read whole — confirmed only bybit_2 is
    real-money live (vwap only); bybit_1 + ib_paper are paper-live;
    ib_live + prop_velotrade_1 inert.
  - `vwap.py:224` confirmed `SL_STD_MULT_DEFAULT = 0.3` with a
    `TIER-3: Ben must approve before deploy` note.
  - `operator-actions.yml` allowlist confirmed (lines 381–411) — all
    audit/backtest/deploy actions this program needs are present.
- Gaps not yet verified: actual live VM runtime SHA + the live
  SL_STD_MULT value (0.3 vs 0.5) — deferred to S2's first action via
  the diag relay. Live per-strategy metrics — that IS the S2 audit.

## Documentation Updated
- Rules doc updates: none.
- Architecture doc updates: none (no system-shape change).
- Trade pipeline doc updates: none (live pipeline untouched).
- Roadmap updates: added a program pointer + an `S-STRAT-IMPROVE-S0`
  ledger row in `ROADMAP.md`.
- GitHub Actions doc updates: none.
- Subsystem doc updates: new program plan under `docs/sprint-plans/`.
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- **vwap.py R:R comment drift (record, do not fix this sprint).** The
  worked-example comment at `src/units/strategies/vwap.py:200-208` still
  cites `SL_STD_MULT_DEFAULT=0.5σ → risk:reward 1:2`, but the live field
  at line 224 is `0.3` (actual R:R 3.33:1, acknowledged at line 221).
  Per the field-vs-comment precedence rule the field is truth and the
  comment is stale. NOT fixed here because (a) S0 is mapping-only and
  (b) the line carries a Tier-3 approval note, so the surrounding block
  needs verification against live state + git history before any edit.
  Carried into S2 as a reconciliation item.
- **Live-vs-repo SL_STD_MULT ambiguity.** Repo `main` has 0.3 (pending
  per its own comment); S-TRAINER-BT-1 deployed 0.5 on 2026-05-17.
  Cannot determine from the repo alone what is running live. Resolve via
  diag relay in S2 before any vwap analysis.
- Two low-impact comment drifts previously noted in
  `S-VWAP-POLICY-INVESTIGATION-2026-05-19` (vwap_backtest_sweep_action.sh
  header; operator-actions.yml:228-230 bt_mode comment) remain open —
  not in this sprint's scope; flagged for a future hygiene pass.

## Risks and Follow-Ups
- Remaining technical risks: real-money bybit_2 continues to bleed on
  vwap longs until S2→S6 produce an approved fix; nothing is changed by
  this planning sprint.
- Remaining product decisions (Tier 3): all live changes downstream
  (S3/S4/S6) require operator approval; none proposed yet.
- Blockers: none for S1/S2.

## Deferred Items
- Live VM state pull + SL_STD_MULT live-vs-repo reconciliation → S2
  first action.
- vwap.py R:R comment hygiene fix → S2 (after live verification).
- The long-vs-short split in the backtest aggregate (carried from
  S-VWAP-POLICY-INVESTIGATION) → S3.

## Next Recommended Sprint
- Suggested next sprint: **S1 — confirm the communication path**
  (Tier 1), then immediately **S2 — full strategy + symbol performance
  audit** (Tier 1, the evidence linchpin).
- Why next: S1 ensures the approval channel works before any Tier-3
  recommendation needs it; S2 produces the ranked loss-driver evidence
  every downstream sprint depends on.
- Required verification before starting S2: pull live VM SHA + runtime
  state via `vm-diag-snapshot`; reconcile the SL_STD_MULT flag; confirm
  the `strategy-performance-audit` action runs against each live account.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline-stage changes, so `docs/TRADE-PIPELINE.md` did not need
      updating; Trade Process tab not affected.
- [x] Roadmap status was checked and a program pointer + ledger row added.
- [x] Contradictions were recorded (vwap R:R comment drift; live-vs-repo
      SL_STD_MULT ambiguity; two pre-existing low-impact comment drifts).
- [x] Remaining unknowns were stated clearly (live runtime SHA + SL value;
      live per-strategy metrics = the S2 audit).
