# Sprint Log: S-SYSTEM-REVIEW-20260821

## Date Range
2026-08-21 06:46Z → 2026-08-21 17:10Z (single session, `dcf5220b`)

## Objective
Run `/system-review` with the operator's stated emphases, in their order: (1)
inspect the LIVE trades first and establish whether anything already built and
merged should have fired and did not; (2) read those trades against the
principles the system has been trying to improve; (3) a thorough performance
review across real, paper and prop; (4) a full technical pipeline verification;
(5) go deep on strategies and ML, including what is due for promotion or
demotion. Then act on the operator's decisions and land a forward work plan.

## Tier
Tier-1 throughout, plus **two Tier-2 changes operator-approved in chat** and
carried through to verified production state.

## Starting Context
Newest committed report was `RPT-20260816-092500` — **five days stale**. The
2026-08-20 review had also not produced one. Nothing was failing; the report
step had simply been skipped twice and no detector exists for that.

## Repo State Checked
`main` at `144dc9d` at session start; `9616928` at close, after five other
sessions merged during the window. Live VM at `9f8aff9` → `0b6a371`.

## Files and Systems Inspected
- Live journal, `/api/bot/{performance,positions,trades/closed,accounts/balances}`,
  `/api/diag/{tick_cost,version,services,journalctl,log_file}`.
- `src/units/strategies/pairs_executor.py`, `src/runtime/order_monitor.py`,
  `scripts/deploy_pull_restart.sh`, `scripts/ops/pull_and_deploy.sh`,
  `scripts/reports/render_system_report.py`, `scripts/ci/check_unwired_artifacts.py`.
- `config/{accounts,strategies,pairs}.yaml`; the Bybit order path (`pybit`, not ccxt).

## Work Completed
- **81 closed trades graded** → `comms/claude_strategy_scores.jsonl` (2987 → 3068).
  Histogram A1 / B22 / C54 / D4.
- **16 findings filed** across the three review backlogs.
- **Tier-2 #1** — `deploy/ict-exchange-fills-pull.timer` daily `00:20` → hourly
  `*:20`. `_LOCAL_PNL_BROKER_DEFER_MS` is 6h, so a daily pull leaves the fills
  store structurally empty at close-resolution time and rows land ESTIMATED.
- **Tier-2 #2** — `pairs_executor.py`: the half-open safety check moved **above**
  the once-per-bar dedup. Measured exposure gaps of 62/63/64/6 minutes on a
  condition the module's own alert grades CRITICAL.
- **`alpaca_live` silenced** per operator decision — both skip keys written and
  read back live.
- **The consolidated report rendered and merged** — `RPT-20260821-130500`,
  passing the renderer's `--strict` review-coverage guard.
- **`docs/claude/WORKPLAN-2026-08-21.md`** — one forward plan for the whole
  programme, four waves, each item carrying its own done-condition.
- New tool `scripts/research/tp_recovery_counterfactual.py` (`manual-only`).

## Validation Performed
- Guard suite **PASS 40 / FAIL 0** on the main PR; **PASS 18 / FAIL 0** on the
  follow-ups — each run **after committing**, because `run_guards.py` scopes
  relevance to a commit range and says so explicitly when work is uncommitted.
- **The pairs regression test was verified to FAIL against the old ordering.**
  Its first version passed against the planted bug — the harness stubbed
  `_load_decision_bars` to a fresh `{}` each tick so the dedup never fired. The
  harness now mirrors the real file-backed round-trip.
- **Both Tier-2 changes verified in PRODUCTION, not on `main`:** the fills
  service fired at **13:27:30Z** (prior fires `00:24`, `00:21` — off the daily
  schedule); the trader genuinely restarted (PID `1554351 → 1558534`, with
  `Stopping`/`Started`).
- `get-env` read-back: `ACCOUNT_DOWN_ALERT_SKIP` **and** `SILENT_REFUSAL_SKIP`
  both `process == declared == alpaca_live`.
- Report self-verification: `backlog_summary` equals `backlog_counts.py` exactly;
  all 12 UNWIRED paths exist; every UNWIRED name escalated into `flags_raised`;
  all 21 class member ids and 8 deferred ids resolve across **all three** backlogs.

## Documentation Updated
- `CLAUDE.md` — the provenance section claimed IBKR was *"hourly, not daily like
  `ict-exchange-fills-pull`"*. The timer change falsifies it; corrected in the
  same commit, because leaving it would read as if the Bybit sibling were still
  daily — the dangerous direction.
- `comms/reports/index.json` + the report artifacts.
- `docs/claude/WORKPLAN-2026-08-21.md` (new).

## Contradictions or Drift Found
- **The report gap was two sessions wide**, not one, and nothing detects it.
- **12 of 33 scripts** shipped since the previous report already have no runner
  and no `# wiring: manual-only` declaration — 36% of one window's output;
  repo-wide the unwired set is **150**.
- **The decision grader scored 56 of 81 closes `tp/sl_appropriate` while 1 of 81
  exited via a declared bracket.** The grades flatter the system.
- **The registry reports `status: candidate` for all 95 models** while
  `deployment_bucket` correctly reports LIVE 3 / SHADOW 28 / OFFLINE 64.

## Risks and Follow-Ups
Everything open is in `docs/claude/WORKPLAN-2026-08-21.md`. Highest: exit
evaluation breaching its own 60s requirement on **32.4%** of cycles (third
consecutive review, worse each time), and only **8.1%** of closes exiting via a
declared bracket.

## Deferred Items
- 333 open health rows were read **for CLASS**, not dispositioned individually —
  seven classes with structural fixes recorded. Claiming a 100% triage would
  have been a number this system exists to distrust.
- Four Tier-3 items answered by the operator but not executed: they are Wave 0.

## Next Recommended Sprint
Work `docs/claude/WORKPLAN-2026-08-21.md` top-down. Wave 0 is decided; Waves 1→4
in order. Handoff prompt is one line: *read the plan and continue.*

## Wrap-Up Check
- [x] Every claim in this log verified this session, not carried from a prior one
- [x] Two mistakes recorded rather than quietly fixed — see below
- [x] Report rendered, merged, and readable at `/api/bot/reports`
- [x] Tier-2 post-state verified on the VM, not inferred from a merge
- [x] Board `✅ DONE`, merge slot released

### Two errors this session made, kept because the reasoning is the transferable part
1. **A phantom high-severity defect.** I saw one `pull-and-deploy` install
   nothing and concluded unit-file changes can never deploy — announcing it on
   the coordination board and telling other sessions to re-check their work.
   False: `ict-git-sync` runs the same script, branches on the SHA moving, and
   had installed the timer ~15 minutes earlier. **One no-op is not evidence of a
   broken mechanism — check whether another actor already did the work.**
   Retracted publicly and in the row.
2. **The review's own headline recommendation was refuted by measuring it.** An
   `ict_scalp` take-profit close path was called *"the highest-leverage single
   change in the exit program"*; measured, net **+$6,237** is the residue of 29
   trades that exited worse (+$20,137) against 23 that exited better (−$13,899)
   — 1.45:1, over 16% of trades, of which **$1.81 is real money**.
