# Sprint Log: S-STRATEGY-COVERAGE-GUARD-2026-07-17

## Date Range
- Start: 2026-07-17
- End: 2026-07-17

## Objective
- Primary goal: close the structural gap that let 35 of 39 `execution: live`
  strategies trade with **no regime-policy coverage** — the roster grew from the
  original 6 BTC strategies to ~44 but `config/regime_policy.yaml` was never
  extended per new strategy, and nothing detected it until the 2026-07-16
  performance review. Add a **preventer** (a merge-time CI guard) so a new live
  strategy can never again ship without a recorded regime decision.
- Secondary goals: make the existing coverage debt **explicit and enumerated**
  (a ratcheting-down register, not invisible); encode the completeness step into
  the `new-strategy` skill's definition-of-done; close the two live strategies
  (`slv_pullback_1d`, `gdx_pullback_1d`) that had no `strategy_descriptions.json`
  entry.

## Tier
- Tier 1.
- Justification: CI/tooling + docs/skills + a Tier-1 prose-config file
  (`config/regime_coverage_exemptions.yaml`, read only by the guard script, never
  by the live-trader runtime) + two `strategy_descriptions.json` entries
  (Tier-1 per CLAUDE-RULES-CANONICAL.md § Tier 1 examples and the `new-strategy`
  skill). No `src/`, `config/strategies.yaml`, order-path, or regime-cell
  (Tier-3) change. The guard checks **structure** (is a regime decision
  recorded?), never **judgment** (whether the cell is correct — that stays
  Tier-3), so it is the sanctioned guard class per § "Why no new mechanical
  guardrails" (2026-07-09 clarification), not the rejected Tier-3-judgment class.

## Starting Context
- Active roadmap items: none specific — this arose from the 2026-07-16 daily
  `/system-review` (report `RPT-20260717-055600-daily`), which found the regime
  router fired exactly **1** hard-gate row across all of 2026-07-16 because the
  policy table only names the 6 original BTC strategies.
- Prior sprint reference: the daily system-review committed in the same branch
  (`comms/reports/daily/20260717T055600Z/`).
- Known risks at start: (a) a hard-fail guard would red-CI the repo until all 35
  uncovered strategies are triaged — mitigated by grandfathering them into an
  explicit debt register at a ceiling equal to the current count; (b) risk of
  propagating a non-compliant precedent shape by copying an existing
  guard/skill/sprint-log without auditing against canonical rules first
  (operator-flagged mid-session; corrected — rules read before finalizing).

## Repo State Checked
- Branch reviewed: `claude/system-performance-trading-review-duvt29` (off `main`
  @ `39a03d9c`).
- Deployment state: not a runtime change — nothing deploys to the VM. The guard
  runs in CI only; the exemptions file + guard script are never imported by the
  live trader.
- Canonical docs reviewed: `docs/CLAUDE-RULES-CANONICAL.md` (end to end — in
  particular § Generation Discipline, § "Why no new mechanical guardrails" +
  its 2026-07-09 scope clarification, § Permission Tiers), root `CLAUDE.md`,
  and the `sprint-format` + `new-strategy` skills.

## Files and Systems Inspected
- Code files inspected: `src/runtime/regime/detector.py` (ADX-14 classifier),
  `src/runtime/intents.py` (flip/hold + regime gate loops),
  `src/runtime/pipeline.py` (signal-builder registry).
- Config files inspected: `config/strategies.yaml`, `config/regime_policy.yaml`,
  `config/strategy_descriptions.json`, `config/accounts.yaml` (risk blocks).
- Deployment files inspected: none changed.
- Docs inspected: `docs/CLAUDE-RULES-CANONICAL.md`,
  `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`, `.claude/skills/new-strategy/SKILL.md`,
  `.claude/skills/sprint-format/SKILL.md`.
- Services or timers inspected: none.
- GitHub Actions workflows inspected: `.github/workflows/env-gate-guard.yml`,
  `canonical-db-resolver.yml`, `strategy-risk-guard.yml` (mirrored the sanctioned
  guard pattern: `pull_request` trigger, no `paths:` on a required check per
  MB-20260706-CI-MINUTES, fail on non-zero exit).

## Work Completed
- **`scripts/check_strategy_coverage.py`** (new) — the guard. Invariant: every
  `execution: live` strategy must be a `regime_policy.yaml` cell OR an
  `exempt`/`coverage_debt` entry in the new exemptions file, AND must have a
  `strategy_descriptions.json` entry. Ratchet: `coverage_debt` may not exceed
  `debt_ceiling` (down-only) — a new live strategy cannot be parked in debt.
  `--matrix` (re)writes `docs/strategy-coverage-matrix.md`; `--check` (default)
  exits 1 on any violation.
- **`config/regime_coverage_exemptions.yaml`** (new) — seeds the 35
  grandfathered live-uncovered strategies as `coverage_debt` with
  `tracking_id: BL-20260717-REGIME-COVERAGE-DEBT`, `debt_ceiling: 35`, empty
  `exempt`/`description_exempt`.
- **`.github/workflows/strategy-coverage-guard.yml`** (new) — required CI check
  running the guard + a matrix-staleness check.
- **`docs/strategy-coverage-matrix.md`** (new, generated) — 39 live rows: 4
  celled, 35 debt; all described.
- **`config/strategy_descriptions.json`** — added the two missing live entries
  (`slv_pullback_1d`, `gdx_pullback_1d`), cloned from the `gld_pullback_1d`
  shape.
- **`.claude/skills/new-strategy/SKILL.md`** — added step 6b (Regime coverage)
  and done-checklist item 7, making the regime decision + green guard part of the
  definition of done.

## Validation Performed
- Manual code verification: ran `python scripts/check_strategy_coverage.py
  --check --matrix` on the real current config → **exit 0**, "39 live strategies,
  all covered/exempt/debt; debt 35/35".
- Negative tests (proving the guard actually bites, not theater):
  - Ceiling lowered 35→34 (simulating a new strategy dumped in debt) → **exit 1**.
  - Removed one debt entry (`ada_pullback_2h`) simulating an uncovered live
    strategy → **exit 1** with the exact `[regime] live strategy 'ada_pullback_2h'
    has no regime_policy cell …` error.
  - Restored the good file → **exit 0**.
- The regime-coverage count (4 covered / 35 uncovered of 39 live) was computed
  directly from `config/strategies.yaml` ∩ `config/regime_policy.yaml`, not
  inferred.
- Gaps not yet verified: the CI workflow has not yet run on GitHub (it will on
  the PR push — the local guard run is the proof of logic; the YAML is mirrored
  from three in-force guards). The matrix-staleness step depends on the runner's
  `git diff`; verified locally that a fresh `--matrix` run is byte-identical to
  the committed file.

## Documentation Updated
- Rules doc updates: none needed (the guard is consistent with the existing
  § "Why no new mechanical guardrails" sanctioned-guard set; no rule text
  changed).
- Architecture doc updates: none (no schema/pipeline/contract change).
- Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`): none (no pipeline stage
  changed).
- Roadmap updates: to be added at session close (status row for this sprint).
- GitHub Actions doc updates: `docs/github-actions-workflows.md` should gain a
  row for `strategy-coverage-guard.yml` — logged as a follow-up (see Deferred).
- Subsystem doc updates: `.claude/skills/new-strategy/SKILL.md` (step 6b + done
  item 7).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- The roster (44 configured / 39 live) had drifted far past the regime-policy
  table (6 named strategies, 4 of them live) — the core finding this sprint
  addresses. Now made explicit in `docs/strategy-coverage-matrix.md` + the debt
  register.
- Two live strategies (`slv_pullback_1d`, `gdx_pullback_1d`) had no description
  despite the `new-strategy` skill marking descriptions mandatory — fixed.

## Risks and Follow-Ups
- Remaining technical risks: low. The guard is CI-only; worst case a false-fail
  blocks a merge (fixable by an exemptions edit). It never touches runtime.
- Remaining product decisions (Tier 3): authoring the actual regime cells for
  the 35 debt strategies — direction-aware cells (ADX is direction-blind; that
  was the 2026-07-16 root cause) — is backtest-gated Tier-3 work (Phase 2 of the
  operator's 2026-07-17 direction). Each cell authored lowers `debt_ceiling`.
- Blockers: none for this Tier-1 sprint.

## Deferred Items
- Add a `strategy-coverage-guard.yml` row to `docs/github-actions-workflows.md`.
- Phase 2: direction-aware regime cells for the 35 debt strategies (Tier-3,
  backtest-gated) — the debt register is the worklist.
- Phase 1: a promotion-readiness detector so a shadow→advisory-ready model
  surfaces as a standing flag rather than a crisis finding (operator ask
  2026-07-17); needs the trainer soak stats + codified criteria.
- Consider whether the review skills' `backlog_drive` should be mechanically
  validated (drained-or-explicitly-justified) rather than prose-satisfiable.

## Next Recommended Sprint
- Suggested next sprint: Phase 1 — re-enable `ict-trainer.service` (idle,
  0 cycles/24h) + the promotion-readiness detector.
- Why next: the ML layer that would down-weight the losing setups is stranded at
  shadow behind a stalled promotion pipeline; unblocking it is higher-leverage
  than any single strategy tweak.
- Required verification before starting: confirm via the trainer relay whether
  the service is disabled deliberately (memory/disk pressure noted in the prior
  weekly report) or is a dropped loose end.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated and the dashboard's Trade Process tab was visually verified. (N/A — no pipeline stage changed.)
- [x] Roadmap status was checked (status row added at session close).
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly (see Validation § Gaps).
