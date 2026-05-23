# Sprint Log: S-STRAT-IMPROVE-S2

## Date Range
- Start: 2026-05-23
- End:   2026-05-23

## Objective
- Primary goal: Build the evidence base for the Strategy Improvement
  Program — pull live performance for every live strategy × symbol ×
  account, identify where losses concentrate, and produce a ranked
  loss-driver report.
- Secondary goals: resolve the S0 SL_STD_MULT live-vs-repo flag; confirm
  live VM SHA vs main HEAD; characterize each strategy's exit mechanism.

## Tier
- Tier 1.
- Justification: read-only live pulls (diag snapshot + read-only
  `strategy-performance-audit` operator actions, which are Tier-1 per
  the operator-actions contract) + a new audit report doc + sprint log.
  No code, config, strategy, or risk change. No order/deploy action.

## Starting Context
- Active roadmap items: Strategy Improvement Program. S0 (architecture)
  and S1 (comms path) done 2026-05-23 (PR #1778).
- Prior sprint reference: `S-STRAT-IMPROVE-S0/S1`;
  `S-VWAP-POLICY-INVESTIGATION-2026-05-19` (vwap losses structural, not
  regime-gate fixable; asked for a long/short backtest split).
- Known risks at start: live-vs-repo SL_STD_MULT ambiguity (S0); the
  Bybit-only audit can't cover MES; turtle_soup/ict_scalp frequency
  unknown.

## Repo State Checked
- Branch reviewed: `claude/strategy-improvement-program-EZi1X` at
  `146d368`.
- Deployment state reviewed (LIVE, via diag relay #1779): VM `git_sha
  461bcb0` == `main` HEAD; heartbeat `running` (age 52s); uptime ~1.6h;
  `ib_paper=true`, `ib_live=false`; ticking normally.
- Canonical docs reviewed: program plan; `scripts/ops/strategy_performance_audit.py`
  + its action wrapper + the operator-actions body parser.

## Files and Systems Inspected
- Code files inspected: `scripts/ops/strategy_performance_audit.py`,
  `scripts/ops/strategy_performance_audit_action.sh`,
  `.github/workflows/operator-actions.yml` (account/days body parsing,
  lines 198–254, 463–464), `src/units/strategies/vwap.py` (SL constant +
  R:R comment block).
- Config files inspected: none changed (referenced `config/accounts.yaml`
  for account roster).
- GitHub Actions workflows inspected: `operator-actions.yml`,
  `vm-diag-snapshot.yml` (via relay).
- Live systems pulled (read-only): live VM `/api/diag/snapshot`,
  `strategy-performance-audit` for bybit_2 + bybit_1.

## Work Completed
- Fired three read-only relays (issues #1779/#1780/#1781) and read the
  results.
- Resolved the S0 SL_STD_MULT flag: **0.3 is live** (SHA match +
  recent restart + empirical R:R 3.48). Recorded the Tier-3 governance
  flag (live value contradicts the in-code "approve before deploy" note).
- Produced the ranked loss-driver report:
  `docs/audits/strategy-loss-drivers-2026-05-23.md`.
- Established the dominant loss driver with evidence: vwap (real-money
  bybit_2) has a thin positive **gross** edge (+$11.25/7d) buried by
  **fees (418% of gross)** from overtrading (~24 trades/day); 74% of
  exits are `reconciler_filled` stop-runs at 17.9% WR; long-side bias
  (longs 20% WR vs shorts 35%, 79% of loss).

## Validation Performed
- Tests run: none (analysis sprint; the audit tool is existing
  production code run on the VM).
- Live data verification (primary):
  - bybit_2 (#1780, run 26327051057): 167 trades, 25.1% WR, net −$35.82,
    R:R 3.48, fee drag 418%, exit-reason + hour + direction breakdowns.
  - bybit_1 (#1781, run 26327051441): 18 paired, vwap long 0/14, 100%
    reconciler_filled, fee drag 480% — corroborates the bybit_2 pattern.
  - snapshot (#1779, run 26327050769): VM SHA, heartbeat, modes, 84
    BTCUSDT + 27 MES recent trades (MES active).
- Cross-check: empirical R:R 3.48 ⇒ SL=0.3 live (independent of SHA).
- Gaps not yet verified: per-strategy/pnl breakdown for MES and for
  turtle_soup/ict_scalp (low-N; need journal pull / more accumulation —
  filed as S2-B). `deviation_std` not captured in trade notes.

## Documentation Updated
- Rules doc updates: none.
- Architecture doc updates: none.
- Trade pipeline doc updates: none.
- Roadmap updates: added the `S-STRAT-IMPROVE-S2` ledger row.
- GitHub Actions doc updates: none.
- Subsystem doc updates: program plan (S2 marked done + handoff to S3);
  new audit report under `docs/audits/`.
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- **SL_STD_MULT governance contradiction (Tier-3, surfaced not fixed):**
  `vwap.py:223` says "Ben must approve before this value is deployed to
  the live bot," but `0.3` is live (confirmed empirically). Plus the
  stale R:R worked-example at `vwap.py:200-208` (still `0.5σ`). Both
  need an operator ruling before any edit (the line is Tier-3 +
  carries a recent operator-approval history that must be checked).
- No new code/doc drift introduced by this sprint.

## Risks and Follow-Ups
- Remaining technical risks: the real-money vwap account continues to
  bleed (net −$36/7d) until an approved fix lands. Nothing changed here.
- Remaining product decisions (Tier 3): SL_STD_MULT governance ruling;
  all S3/S4 live changes.
- Blockers: none for S3.

## Deferred Items
- **S2-B:** journal-based per-strategy pull for turtle_soup / ict_scalp
  (low-N on the Bybit audit) + MES once it accumulates ~1–2 weeks.
- **Instrumentation:** capture `deviation_std` in trade notes (Tier-2)
  so S3 can bucket by signal stretch.

## Next Recommended Sprint
- Suggested next sprint: **S3 — selectivity / rule-tightening
  experiments** (Tier-1 analysis; Tier-3 to ship).
- Why next: overtrading→fee-drag is the dominant, evidence-backed loss
  driver; cutting trade count is the highest-ROI, lowest-risk lever and
  directly attacks it. Start with the long/short backtest split + session
  gating + entry-threshold/direction filters, measured net-of-fee.
- Required verification before starting S3: re-confirm the live SHA (in
  case of intervening deploys); use the existing `vwap-backtest-sweep`
  action (key `bt_mode:`) and trainer-VM sweeps; require n≥3 windows
  positive on both legs before proposing any live change.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline-stage changes, so `docs/TRADE-PIPELINE.md` did not need
      updating; Trade Process tab not affected.
- [x] Roadmap status was checked and an S2 ledger row added.
- [x] Contradictions were recorded (SL_STD_MULT governance + stale R:R
      comment — surfaced for operator, not edited).
- [x] Remaining unknowns were stated clearly (turtle_soup/ict_scalp/MES
      low-N; deviation_std not captured).
