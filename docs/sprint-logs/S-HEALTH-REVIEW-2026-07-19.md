# Sprint Log: S-HEALTH-REVIEW-2026-07-19

## Date Range
- Start: 2026-07-19
- End: 2026-07-19

## Objective
- Primary goal: Run the `/health-review` as full end-to-end QA — hunt, root-cause, and FIX the open Tier-2/3 reconciler-correctness bugs (not just log them), then close them end-to-end (merge → deploy → verify → data cleanup).
- Secondary goals: (a) build a mandatory, non-merge-gated cross-session coordination board; (b) drain the health-review backlog and record every decision in its durable surfaces.

## Tier
- Tier 1 (tooling / tests / docs / coordination board) + Tier 2 (reconciler correctness fixes + DB writeback), all operator-approved where the tier required it.
- Justification: the reconciler write-path fixes touch `order_monitor.py` / `coordinator.py` (runtime, Tier-2) and one reverses a prior operator-approved fix (#2974) — merged only after explicit operator OK. The historical-phantom cleanup is a Tier-2 money-DB writeback — applied only on explicit operator OK, DB backup first.

## Starting Context
- Active roadmap items: none specific — a health-review QA pass over the reconciler + observability surfaces.
- Prior sprint reference: the health-review backlog (`docs/claude/health-review-backlog.json`), 88/88 triaged earlier the same day.
- Known risks at start: BL-20260711 (INTENT-REDUCE phantom-pnl) is a latent real-money exposure on netting `bybit_2`; the fix reverses #2974/BL-20260601-001, so it needed careful review before merge.

## Repo State Checked
- Branch or commit reviewed: `main` @ ab8876c → progressed to bc71bac over the session.
- Deployment state reviewed: live trader `ict-bot-arm` (141.145.193.91), `ict-trader-live.service` active before + after each deploy.
- Canonical docs reviewed: `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md`, `docs/ARCHITECTURE-CANONICAL.md`, `ROADMAP.md`; `canonical-doc-coherence` checker PASS.

## Files and Systems Inspected
- Code files inspected: `src/runtime/order_monitor.py` (`_close_trade_from_order_status`, `_sweep_local_pnl_for_unpriced`, `_sweep_unlinked_packages`, `_reconcile_orphan_exchange_positions`), `src/core/coordinator.py` (`multi_account_execute`), `src/strategy_registry.py` (`execution_mode`), `src/web/api/_clean_trades.py` (`exclude_reduce_leg_predicate`).
- Config files inspected: `config/strategies.yaml` (execution gates), `deploy/*.service`.
- Deployment files inspected: `.github/workflows/system-actions.yml`, `scripts/ops/notify_run.sh`, `scripts/install_systemd_units.sh` (deploy log).
- Docs inspected: `docs/claude/system-actions.md`, `docs/claude/deployment-ops.md`, `docs/claude/health-review-backlog.json`, `docs/claude/session-board.json`.
- Services or timers inspected: `ict-trader-live`, `ict-web-api`, the 18-unit deploy set (via the deploy enumeration).
- GitHub Actions workflows inspected: `system-actions.yml`, `bootstrap-labels.yml`, `pytest-run` CI.

## Work Completed
- **PR #6926 (Tier-2, merged + deployed + live-verified)** — four operator-approved reconciler correctness fixes:
  - **BL-20260711 INTENT-REDUCE phantom-pnl (write-path):** `_close_trade_from_order_status` now defers reduce-leg pnl to NULL (`pnl_source='deferred_intent_reduce'`) instead of attributing the parent's close; `_sweep_local_pnl_for_unpriced` skips reduce legs. Reverses #2974/BL-20260601-001 attribution (its test updated to assert NULL).
  - **BL-20260705 SHADOW-PKG-ORPHAN:** `_sweep_unlinked_packages` relabels `execution: shadow` unlinked packages `shadow_expired` (not `orphaned`).
  - **BL-20260618 RECONCILE-DUP residual:** `_DEFAULT_READOPT_GUARD_SECONDS` 300→1800 (covers the full IBKR reset window) + `CLAUDE.md` env-doc update.
  - **BL-20260626 OPKG-META:** per-account refusal-cause fold into `pkg.meta` (observability).
  - CI fixes: decoupled `test_old_unlinked_package_marked_orphaned` from the vwap config-shadow state; added the two drifted units to the `test_s012_service_consolidation` guard (guard was red on main).
- **Coordination board (Tier-1, merged with #6926)** — GitHub issue #6927 "🤖 Claude Coordination Board" (live, non-merge-gated cross-session comms) + `docs/claude/coordination-board.md` + `session-coordination` SKILL step + `.claude/settings.json` SessionStart **clause 0** (the first framing every session sees) + `claude-coordination` label.
- **PR #6930 (Tier-1 tooling, merged + deployed)** — `scripts/ops/supersede_intent_reduce_phantom_pnl.py` + system-action `supersede-intent-reduce-phantom-pnl` (dry-run default, DB backup on apply, idempotent) + tests + docs.
- **Historical cleanup APPLY (Tier-2, operator-approved, issue #6937)** — void-flagged all **116** historical reduce-leg rows carrying a non-NULL pnl to `reconcile_status='superseded'` (7 real-money `bybit_2` net +$16.75 incl. trade 2491; 109 paper `bybit_1` net −$7,693.23) after a DB backup. Dry-run first (issue #6935).

## Validation Performed
- Tests run: full reconciler + system-actions suites (306 pass), 4 new reduce-leg/shadow tests, 4 new superseder tests; ruff clean; YAML + bash syntax OK.
- Dry-runs or staging checks: the cleanup dry-run (#6935) surfaced the exact 116-row scope before the apply.
- Manual code verification: read `exclude_reduce_leg_predicate` and confirmed reduce legs are masked from every analytics read path (so voiding them moves no KPI); confirmed the phantom entry==exit signature against live rows 2604/2607/2610.
- Gaps not yet verified: none for this scope — deploys confirmed HEAD live via the deploy log; the apply confirmed 116 rows superseded + backup path printed.

## Documentation Updated
- Rules doc updates: `.claude/settings.json` SessionStart clause 0; `.claude/skills/session-coordination/SKILL.md`.
- Architecture doc updates: none required.
- Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`): none (no pipeline-stage contract change; the reconciler write-path change is internal correctness).
- Roadmap updates: none (bug fixes + process infra, no milestone status change — see Contradictions/Drift).
- GitHub Actions doc updates: `docs/claude/system-actions.md` (+ the `supersede-intent-reduce-phantom-pnl` action), `docs/claude/deployment-ops.md` (2 drifted units).
- Subsystem doc updates: `CLAUDE.md` `RECONCILER_READOPT_GUARD_SECONDS` default (300→1800); new `docs/claude/coordination-board.md`.
- Historical docs marked superseded: n/a.

## Contradictions or Drift Found
- **`test_s012_service_consolidation` guard was RED on `main`** — PRs #6859 + #6901 each added a `deploy/*.service` without updating `EXPECTED_SERVICES`. Fixed in #6926 (guard + `deployment-ops.md`).
- **Backlog resolution notes were stamped `resolved` at 09:00 but carried no PR/apply trail** — appended final "SHIPPED/CLEANED" updates citing #6926/#6930/#6937 + the deploy issue #6929 (this doc-freshness pass).
- No canonical doc-vs-doc contradictions (coherence checker PASS).

## Risks and Follow-Ups
- Remaining technical risks: none introduced. A full DB backup (`trade_journal.db.bak-...-20260719T100731Z`) exists on the VM for the cleanup.
- Remaining product decisions (Tier 3): none pending from this session.
- Blockers: none.

## Deferred Items
- None from this session. Two pre-existing open backlog items remain (`BL-20260705-ENV-DIAG-BASE-URL-STALE`, `BL-20260626-MES-BASE-STALE`) — untouched here, left for their owning follow-ups.

## Next Recommended Sprint
- Suggested next sprint: a `/performance-review` pass now that reduce-leg phantoms are void-flagged (the strategy aggregates read cleaner).
- Why next: the analytics surfaces are now consistent with the reduce-leg NULL contract.
- Required verification before starting: none.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated and the dashboard's Trade Process tab was visually verified. — N/A: no pipeline-stage contract changed (reconciler internal correctness only).
- [x] Roadmap status was checked (no milestone moved).
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
