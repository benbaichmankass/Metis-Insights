# Sprint Log: S-M7-STRATEGY-REVIEW-GATE-2026-06-09

## Date Range
- Start: 2026-06-09
- End:   2026-06-09

## Objective
- Primary goal: open the M7 milestone — ship the **Strategy Review
  Gate**: the mechanical decision rubric (`promote | hold | tune |
  demote_shadow | kill`) that consumes per-strategy aggregate stats +
  regime-cell PnL slice + execution diagnostics and proposes Tier-3
  actions on a bounded SLA. Apply the gate to **VWAP** as the proving
  case.
- Secondary goals:
  - Stand up the M8 seam (the gate's `tune` action points at the
    M8 sweep recipe; M8 itself stays out of scope).
  - Surface the new packets on the dashboard's read surface
    (`GET /api/bot/strategies/{name}/review`).

## Tier
- Tier 1 + Tier 3 (mixed). The gate doc, packet script, API route, and
  tests are Tier 1 (read paths + tooling). The VWAP `enabled: true →
  false` flip in `config/strategies.yaml` is Tier 3 — that commit ships
  in this branch but the PR is opened **as draft** and only merges
  after explicit operator approval.
- Justification: M7 is a process gate, not a runtime change; the gate
  itself never writes YAML. The vwap kill is the gate's first
  *application* and inherits Tier 3 because it touches
  `config/strategies.yaml`.

## Starting Context
- Active roadmap items: M7 / M8 both ✱NOT STARTED. M14 (ML optimisation
  Phase 2) shipped 2026-06-04 — its per-bar regime scoring + regime
  policy provided the **trend × vol cell** vocabulary M7 reuses.
- Prior sprint reference: most recent applicable evidence is the
  `/performance-review` rotation captured in
  [`S-RINA-REVIEW-2026-06-04.md`](S-RINA-REVIEW-2026-06-04.md) (caution
  verdict; 19 graded decisions; 18 vwap D-grades), and the regime-roster
  matrix doc [`docs/research/regime-roster-matrix-2026-06-01.md`] which
  documents vwap's net-of-fee verdict across all three trend cells
  (`trending -6179 / transitional -1903 / chop -2642`).
- Known risks at start: (a) any Tier-3 proposal must be re-verified per
  the PR #1358 protocol — comments are not authoritative, the live
  field is; (b) live VM not reachable from this sandbox — packet
  end-to-end against the live DB must happen on-VM after merge.

## Repo State Checked
- Branch: `claude/youthful-tesla-tise45` (this branch).
- Deployment state: live trader is on its 2026-06-09 HEAD per the
  liveness watchdog hardening covered in
  [`S-CI-STORAGE-BUDGET-2026-06-07.md`] and the PERF-20260601-006
  regime-router phase-3 follow-up in
  [`S-MLOPT-CLOSEOUT-2026-06-07.md`]. No deploy fires in this sprint —
  the kill PR ships as draft.
- Canonical docs reviewed:
  - `docs/CLAUDE-RULES-CANONICAL.md` (re-read end-to-end —
    Generation Discipline § Skill-first lookup is binding;
    Documentation Hygiene & Premise Verification 2026-05-17).
  - `docs/ARCHITECTURE-CANONICAL.md` (mode-mutation contract
    untouched).
  - `ROADMAP.md` — M7 / M8 rows confirmed NOT STARTED before edit.
  - `.claude/skills/performance-review/SKILL.md` — M7 reuses its
    rubric; this gate sits ABOVE it (per-strategy verdicts), not in
    place of it (per-decision A-F grading remains there).

## Files and Systems Inspected
- Code files inspected:
  - `src/web/api/main.py` (router registration model).
  - `src/web/api/routers/strategies.py` (shape of the existing
    Strategies surface).
  - `src/web/api/routers/order_packages.py` (claudeScore JSONL read
    pattern — re-used).
  - `src/utils/paths.py::trade_journal_db_path` / `runtime_logs_dir`
    (the canonical path resolvers — used in the packet script).
  - `src/units/db/database.py` (trades / order_packages / signals
    schema — drove the SQL pulls).
  - `tests/fixtures/real_schema_db.py` (the canonical DB fixture
    factory — reused for the slicer end-to-end test).
- Config files inspected:
  - `config/strategies.yaml` (vwap block confirmed at `execution:
    shadow` since S9, the M7 kill flips `enabled` instead).
  - `config/regime_policy.yaml` (vwap OFF in `trending`,
    `transitional`, `chop`).
- Deployment files inspected: none mutated this sprint.
- Docs inspected: regime-roster matrix
  (`docs/research/regime-roster-matrix-2026-06-01.md`) for the
  multi-year vwap PnL slice; performance-review backlog
  (`docs/claude/performance-review-backlog.json`) for PB-20260607-001
  resolved-by-PR-#2982 context.
- Services or timers inspected: none.
- GitHub Actions workflows inspected: none (no CI changes this sprint).

## Work Completed
- **`docs/strategy-review-gate.md`** — the canonical M7 doc. Defines
  the review packet schema, the threshold table (rows for each of n=0,
  1≤n<30, 30≤n<100, n≥100), the four overrides (execution-mode
  mismatch / degenerate confidence / already-at-shadow / promote
  cohort time), the five actions, and the bounded SLA (`demote_shadow`
  + `kill` proposals must ship within 7 days; operator decision within
  another 7).
- **`scripts/ml/strategy_review_packet.py`** — the packet generator.
  Pulls from `trade_journal.db::{order_packages, trades, signals}`
  (read-only `mode=ro`), groups decisions by `(trend, vol)` regime
  stamp from the `signals.meta` dual-write, computes the threshold
  matrix verdict, writes packet JSON + Markdown summary to
  `runtime_logs/strategy_reviews/<UTC-date>/<strategy>.{json,md}`.
  CLI: `--strategy NAME` (repeatable) or `--all-btc-strategies`;
  `--window-days N`; `--db-path PATH`; `--shadow-soak-days N` for the
  promote cohort gate.
- **`src/web/api/routers/strategy_review.py`** —
  `GET /api/bot/strategies/{name}/review` Tier-1 read route serving
  the most-recent packet under `runtime_logs/strategy_reviews/`. Mounted
  in `src/web/api/main.py`. Returns `present: false` cleanly when no
  packet exists so the dashboard can render the empty state without a
  500.
- **`tests/test_strategy_review_gate.py`** — 28 tests covering:
  - every row of the threshold table at boundary `n` / `win_rate` /
    `expectancy` / regime-cell-policy combinations;
  - all four overrides;
  - the `regime_policy_cell_for` helper (both-on / both-off / mixed /
    absent / direction-hinted);
  - a populated-DB end-to-end run through the slicer + packet builder
    asserting per-cell PnL attribution, headline aggregation, and the
    catastrophic-all-off → `kill` and the shadow-anomaly → `hold`
    paths.
  All 28 pass locally on Python 3.11 + pyyaml 6.0.1.
- **`config/strategies.yaml::vwap.enabled: true → false`** — the M7
  gate's first application. Tier 3. Evidence path: the regime-roster
  matrix (n=40,650 multiyear) shows vwap is **net loser in every
  regime cell** even with the live gates threaded; the live
  shadow-soak window produced ~133 orphaned packages / 24h all
  D-graded; `config/regime_policy.yaml` already lists vwap OFF in
  every 1-D cell. The gate's mechanical verdict is `kill`. **PR
  opens as draft** pending operator approval.
- **`ROADMAP.md`** — M7 flipped NOT STARTED → IN PROGRESS, pointing
  at this sprint log and naming the open follow-ups (dashboard
  wiring; backtest_anchor block; 2-D `trend_vol` gate extension). M8
  row annotated with the seam M7 reserved.

## Validation Performed
- Tests run:
  - `python -m pytest tests/test_strategy_review_gate.py` — 28
    passed in 0.37 s.
- Dry-runs or staging checks:
  - Built a synthetic 30-package `vwap` fixture (orphaned shadow,
    degenerate confidence) via the in-test factory and ran
    `build_packet` end-to-end → `hold` with the
    `degenerate confidence` + `no closed trades` reasons rendered in
    the Markdown twin. Output verified to match the doc's render
    schema.
  - Imported `src/web/api/routers/strategy_review.py` cleanly;
    `router.routes[0].path == "/api/bot/strategies/{name}/review"`.
- Manual code verification:
  - `decide` matrix re-walked row-by-row against
    `docs/strategy-review-gate.md` § Threshold table (canonical doc
    + tests verify the same boundary conditions).
  - vwap field-precedence check per the 2026-05-17 hygiene rule —
    the live YAML field at line 316 was `enabled: true` (verified
    by direct read), the surrounding comment block dating to the S9
    demotion describes `execution: shadow` not `enabled`. No
    field/comment contradiction created (the comment block was
    re-written to describe the kill action; the old S9 framing was
    incorporated as the prior-state context, not silently deleted).
- Gaps not yet verified:
  - The packet has not been run against the **live VM**'s
    `trade_journal.db` — the sandbox cannot reach the VM directly
    and no diag relay exists for the script's output. The script
    runs end-to-end on a synthetic real-schema DB (the 28th test);
    on-VM verification rides the next `/performance-review` rotation
    once the script is deployed.
  - `backtest_anchor` is emitted as `null` (the trainer-mirror lookup
    is reserved as a small follow-up; the gate's matrix doesn't
    depend on it for `kill`).

## Documentation Updated
- Rules doc updates: none — M7 sits within the existing skill-first
  lookup + Tier-3 protocols. No rule conflicts.
- Architecture doc updates: none — M7 adds a read-only API surface
  and a script; no runtime / order-path / mode-mutation change.
- Trade pipeline doc updates: none.
- Roadmap updates: M7 → IN PROGRESS; M8 annotated with the M7 seam.
- GitHub Actions doc updates: none.
- Subsystem doc updates:
  - **NEW** `docs/strategy-review-gate.md` — the canonical M7 doc.
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- The vwap YAML field-vs-comment audit (per the 2026-05-17 hygiene
  rule): the comment block surrounding `vwap.execution: shadow`
  dated to S9 and described the execution-only demotion. No
  contradiction with the field; the comment was rewritten to
  describe the M7 kill action AND retain the S9 context. Old
  comment language preserved as the rollback-path note (per "Field
  beats comment: when … the field is the truth" — but here the
  field is changing, so the comment is updated to match).
- No other contradictions surfaced.

## Risks and Follow-Ups
- Remaining technical risks:
  - **Packet has not run against live data this sprint.** First
    on-VM run lands when the kill PR (or a Tier-1-only split of it)
    deploys. Mitigation: deploy the Tier-1 packet generator
    independently and run it before the kill PR merges — packet
    output then becomes the embedded justification.
  - **`backtest_anchor` follow-up.** Tier-1, naturally pairs with
    the dashboard wiring sprint.
- Remaining product decisions (Tier 3):
  - **VWAP kill operator approval.** PR is draft; merge gates on
    operator review. Rollback = revert the YAML to `enabled: true`
    (one-line flip; no deploy beyond the existing pull-and-deploy).
- Blockers: none.

## Deferred Items
- Dashboard wiring of `/api/bot/strategies/{name}/review` into the
  Streamlit Strategies tab (separate session in
  `ict-trader-dashboard`).
- `tune_recipe` integration with the M8 sweep harness (M8 owns).
- 2-D `trend_vol` cells in the gate matrix — the script consumes the
  axis observe-only; matrix extension waits on 2-D cells being
  authored in `regime_policy.yaml`.
- `backtest_anchor` block — wire the trainer-mirror SUMMARY.md
  lookup in a small follow-up.
- MES strategies — out of scope per the operator's separate
  delayed-CME-data investigation. First packet run = BTC strategies
  only.

## Next Recommended Sprint
- Suggested next sprint: **S-M8-STRATEGY-TUNING-S0** — define the
  canonical sweep harness M8 owns. The gate's `tune` action already
  carries a `tune_recipe` block; M8 makes it executable.
- Why next: the gate without M8 can only `kill` / `demote_shadow` /
  `hold`. `tune` is the most common matrix output at mid-`n`; without
  a recipe runner the gate stalls there.
- Required verification before starting:
  - On-VM run of `strategy_review_packet.py --all-btc-strategies
    --window-days 7` to populate the first real packet set
    (after the Tier-1 piece of this PR merges).
  - Dashboard wiring of the new endpoint so the operator can read
    packets without inspecting the JSON directly.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage — n/a, no pipeline
      change.
- [x] Roadmap status was checked and updated.
- [x] Contradictions were recorded (vwap comment rewrite captured).
- [x] Remaining unknowns were stated clearly (live-VM packet run
      pending deploy; backtest_anchor follow-up).
