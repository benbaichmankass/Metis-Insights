# Sprint Log: S-PROFIT-GAPS-2026-05-31

## Date Range
- Start: 2026-05-31
- End: 2026-05-31

## Objective
- Primary goal: Close profitability gaps after the flip-policy work — consolidate the backtest harnesses, build a strategy *selection* gate, prep the compute-gated evidence runs, and research new complementary strategies. Plus: answer an operator-flagged live-trading question (a confusing `squeeze_breakout_4h` Telegram sequence) and a sweep-ETA question.
- Secondary goals: run `/health-review`; ship a safe perf fix surfaced by profiling.

## Tier
- Mostly Tier 1 (research tooling, tests, docs, observability). Two Tier-2 items (live-path files) shipped via operator-approved PRs: notification fixes (#2476) + the regime-vol perf fix (#2485). No Tier-3 (no strategy params / risk caps / account modes / live promotions touched).
- Justification: consolidation + selection-gate + scaffolds are all research/tooling that never wire into the live order path; the two Tier-2 PRs touch `coordinator.py` / `execute.py` / `regime_shadow.py` and were merged with operator approval in-session.

## Starting Context
- Active roadmap items: M7/M8 (strategy selection + profitability), post S-FLIP-HOLD-DEFAULT (#2451, `FLIP_POLICY=hold` now live default).
- Prior sprint reference: `S-INTENT-HOLD-2026-05-31.md`, `S-STRAT-FVG-RANGE-2026-05-30.md`.
- Known risks at start: heavy backtest work is gated on a single-core trainer VM busy with an operator sweep.

## Repo State Checked
- Branch/commit reviewed: `main` @ `e057d90` → advanced to `dbb1e4e` → (+#2476 `4a4efb8`) → (+#2485) over the session.
- Deployment state reviewed: live VM healthy, sha matched main, all services active (via diag relays).
- Canonical docs reviewed: CLAUDE.md, ARCHITECTURE-CANONICAL.md, ROADMAP.md, CLAUDE-RULES-CANONICAL.md.

## Files and Systems Inspected
- Code: `sim/{engine,models,sweep,account}.py`, `src/runtime/{intents,regime_shadow,execution_diagnostics}.py`, `src/core/coordinator.py`, `src/units/accounts/execute.py`, `ml/predictors/{shadow,lightgbm}.py`, `scripts/backtest_{system,trend}.py`.
- Config: `config/strategies.yaml` (roster + execution gates), read-only.
- Live state: trade_journal.db (trades/order_packages via diag relay), services, status, audit tail.
- Trainer VM: sim sweep PID 604328 (status/progress/ETA via trainer-vm-diag relay).

## Work Completed
- **#2465 — Harness consolidation + strategy selection gate (merged).** `sim/` is the canonical integrated harness; added the optional `$` account layer (`sim/account.py`, Phase-5). New `scripts/strategy_gate.py` (M7) — judges a strategy on in-system, net-of-fee, recent contribution; proposes promote/demote, never flips. 67 + 9 tests.
- **#2466 — prem-tier evidence-run kit (DRAFT, open).** `scripts/ops/prem_runs/{01_reproduce_check,02_demotion_evidence,03_spx_retune}.sh` + `_common.sh` + `reproduce_diff.py` + README. Throttled (nice/ionice), idempotent. Gated on prem cores.
- **New-strategy research + top-2 scaffolds (in #2466 branch / merged scaffolds).** Ranked report `docs/research/new-strategy-candidates-2026-05-31.md`. Rank-1 `session_breakout_trend` + Rank-2 `htf_pullback_trend_2h` scaffolded (`src/units/strategies/` + `scripts/backtest_{session,pullback}.py` + tests) — **inert, not wired** (inertness test guards it). Ranks 3-4 (funding_carry, equity_leadlag) documented only (blocked on data acquisition).
- **#2475 — health-review backlog log (merged).** Logged the 2 notification false-positives.
- **#2476 — notification fixes (merged, Tier-2, live).** (a) reduce-only legs no longer fire the "TRADE OPENED — SHORT" ping with the parent long's SL/TP; (b) the "ALL accounts failed to dispatch" 🚨 no longer fires when every account is a benign intent_noop (flip-suppressed/at-target). 11 new tests.
- **#2482 — ml-review backlog finding (merged).** `MB-20260531-001`: sim regime-gate scoring ~0.5s/bar.
- **#2485 — regime-vol perf fix (Tier-2; merging at session end).** `rolling_log_return_vol` O(n)→O(window) reverse-scan, byte-identical (4000-case fuzz + reversal to preserve float summation order).
- **`/health-review` — grade HEALTHY.** All 7 strategies emitting evals, services/heartbeat/deploy ok, pipeline plumbing intact. Completion ping sent to the Claude channel.

## Validation Performed
- Tests: 67 sim (incl. 18 Phase-5 account) + 9 strategy_gate + 10 scaffold + 11 notification + 21 perf-equivalence/edge; plus 143 dispatch/intent/coordinator regression for the notification fix and 57 sim/regime/classifier regression for the perf fix. All green; ruff clean on every PR.
- Live verification: the operator-flagged `squeeze_breakout_4h` sequence cross-checked against live DB (trades 1945-1952, order_packages pkg-838fe5ef) — confirmed the bot traded correctly; the two messages were display-only bugs.
- Profiling: local sim per-bar cost measured (no-model ~1.66 ms/bar/strategy); the O(n²) closes/vol path measured (~6 min over 60k); the dominant ~0.5s/bar isolated to single-row LightGBM `Booster.predict` (could NOT be measured locally — no model/lightgbm in checkout; inferred from code + LightGBM's known per-call overhead).
- Gaps not verified: the end-to-end reproduce-check (sim vs backtest_system on full 5m data) and the demotion-evidence numbers — both need prem-tier VM compute. The batched-inference fix needs the model + on-VM profiling.

## Documentation Updated
- This sprint log (the session record that was otherwise only in PR bodies).
- Backlogs: `health-review-backlog.json` (BL-20260531-001/002, resolved by #2476), `ml-review-backlog.json` (MB-20260531-001).
- Research report: `docs/research/new-strategy-candidates-2026-05-31.md`.
- Consolidation decision: `docs/sprint-plans/CONSOLIDATION-ROADMAP-2026-05-31.md` (from #2465).
- ROADMAP/ARCHITECTURE change-log rows for this session's merges: **DEFERRED** (see below) — the canonical invariants are consistent; summary rows are a completeness add, not a contradiction fix.

## Contradictions or Drift Found
- None. The two execution gates, permission tiers, and `FLIP_POLICY=hold` default are stated consistently across CLAUDE.md / ARCHITECTURE / ROADMAP and match the merged code. The only gap is completeness (no per-merge change-log rows yet), not contradiction.

## Risks and Follow-Ups
- **No real sweep speedup yet.** #2485 fixed the ~6-min O(n²) component; the dominant ~9.5h cost (single-row LightGBM predict per bar) is UNFIXED. A model-in-loop full-history sweep is still an overnight job. Fix = batch/precompute inference; needs VM + model + profiler (`MB-20260531-001`).
- **Everything heavy is gated on prem-tier cores.** The reproduce-check, demotion-evidence run, SPX re-tune, and candidate testing all want trainer-VM compute.
- Remaining Tier-3 prizes (operator-gated): demote fade_breakout_4h / turtle_soup / ict_scalp_5m off live money once the demotion-evidence run formalizes the audit finding; activate a new candidate (shadow, bybit_1) if it clears the gate.

## Deferred Items
- ROADMAP.md + ARCHITECTURE-CANONICAL.md change-log rows for #2465 / #2476 / #2485 / scaffolds / prem-run kit (completeness; this log covers the gap for now).
- `scripts/backtest_system.py` retirement — blocked on the reproduce-check.
- PR #2466 (prem-run kit + scaffolds) is still DRAFT — intentionally, until the runs actually execute on the VM.

## Next Recommended Sprint
- Suggested next: **the batched-inference perf fix (`MB-20260531-001`)** on the trainer VM — it unblocks fast model-in-loop sweeps, which in turn unblocks the reproduce-check, demotion-evidence, and candidate testing. *Alternative if VM compute is free first:* run the no-model candidate sweeps (`session_breakout_trend`, `htf_pullback_trend_2h`) — those are minutes, not hours, and produce fresh strategy evidence immediately.
- Why next: every downstream profitability step is gated on either the speed fix or raw VM compute; the speed fix is the highest-leverage unblock.
- Required verification before starting: install lightgbm + load a real regime model on the VM, profile `Booster.predict` single-row vs batched, and hold any optimization to a byte-equivalence test (the #2485 pattern).

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [ ] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated — N/A: the notification fixes changed which Telegram pings fire, not the trade pipeline stages; no pipeline-stage semantics changed.
- [x] Roadmap status was checked (consistent; per-merge rows deferred).
- [x] Contradictions were recorded (none found).
- [x] Remaining unknowns were stated clearly (sweep speed, prem-core gating).
