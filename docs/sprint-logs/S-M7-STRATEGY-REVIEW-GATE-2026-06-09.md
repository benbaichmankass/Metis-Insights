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

---

## Addendum: on-VM activation + accuracy hardening (2026-06-09 PM session)

The original sprint log captured the framework + tests + first Tier-3
application (VWAP demotion to `enabled: false`, PR #3100). After the
operator merged + deployed that, the session continued through a
ladder of small fixes that brought the gate from "shipped" to "fully
accurate against live data". This addendum is the closeout.

### What shipped after the initial PR

| PR | Scope | Tier |
|---|---|---|
| #3100 | M7 framework + tests + `/api/bot/strategies/{name}/review` + vwap kill | Tier 1 + Tier 3 |
| ict-trader-dashboard #85 | Strategies tab renders packets inline (M7 review packet card per strategy) | Tier 1 |
| #3102 | `generate-strategy-review-packets` system-action allowlist + wrapper (Claude-driven on-VM activation path) | Tier 2 (allowlist add) → Tier 1 fire |
| #3108 | `print_packets: true` flag — wrapper also cats packet Markdown in the issue-comment reply, so a sandbox session can read the gate's reasons without waiting for the dashboard | Tier 1 |
| #3113 | Exclude orphaned packages from `n_closed` — htf_pullback false-positive fix (15 closes pre-fix vs 2 honest closes; matrix saw catastrophic where there was none) | Tier 1 |
| #3118 | Read regime stamps from `order_packages.meta` instead of `signals.meta` (the JOIN never matched because the eval-row dual-write doesn't carry `order_package_id`) | Tier 1 |
| #3121 | Normalize YAML 1.1 boolean `off`/`on` at `regime_policy_cell_for` boundary + direction-aware policy lookup | Tier 1 |
| #3126 | Same normalization at `load_regime_policy` boundary (caught a second drift hazard from the prior PR) | Tier 1 |

### Verified live state

Final on-VM run dispatched via
issue #3130 (`generate-strategy-review-packets all_btc:true window_days:7
shadow_soak_days:16 print_packets:true`):

```
fade_breakout_4h               hold     (n=0)
fvg_range_15m                  hold     chop/unknown cell, policy:off
htf_pullback_trend_2h          hold     trending/unknown cell, policy:unknown (vol axis mixed),
                                        50% WR over n_closed=2
ict_scalp_5m                   hold     chop/volatile + trending/unknown, 100% WR over n_closed=2
squeeze_breakout_4h            hold     (n=0)
trend_donchian                 hold     (n=0)
trend_donchian_1h              hold     (enabled:false)
turtle_soup                    hold     (n=0)
vwap                           hold     9 cells, all policy:off, n_closed=0
```

The matrix can now correctly read:
- YAML's unquoted `off` as the string `"off"` (was: bool `False` → string `"false"` → "unknown").
- Direction-specific policy when all packages in a cell share a direction (was: rolled up to "unknown" when long/short policies differed in the YAML).
- Regime / vol_regime stamps directly from `order_packages.meta` (was: JOIN through `signals.meta` that never matched).
- Filled-and-closed trades only, excluding orphaned shadow packages (was: pkg_status='closed' OR-branch inflated n_closed with orphans).

### Honest residual

Two `unknown` strands remain in the live packets — both correctly
fail-safe (gate stays conservative) and both narrow follow-ups, not
session blockers:

1. **vol axis stamping gap.** Many packages carry a `regime` value but
   `vol_regime: null` (rendered as `vol: unknown` in the cells). The
   matrix doesn't read the vol axis yet, so this is observation-only;
   a future sprint can audit the stamp coverage in
   `src.runtime.regime.vol_detector` if the vol axis becomes
   load-bearing.
2. **ict_scalp_5m has no rows in `config/regime_policy.yaml`.** Every
   ict_scalp cell renders `policy: unknown` because the helper returns
   "unknown" when the strategy is absent from the policy table. This is
   intentional default-permissive behavior, but a future sprint could
   author rows for ict_scalp once enough closed trades accrue to
   characterize per-regime PnL.

Both logged to `docs/claude/performance-review-backlog.json` as
follow-ups (PB-20260609-001 / 002).

### M7 status

M7 deliverables fully shipped + verified on the live VM. ROADMAP M7
flipped IN PROGRESS → ✅ DONE.

### Next session

M8 — Strategy Tuning. The gate's `tune` action carries a `tune_recipe`
pointer that M8 will make executable. Kickoff prompt for the new
session lives at the end of this sprint log under "M8 kickoff prompt".

---

## M8 kickoff prompt (paste into a fresh session)

> **M8 — Strategy Tuning**
>
> Start M8. M7 (Strategy Review Gate) is ✅ DONE — the gate is live,
> verified, and the framework reserves a `tune_recipe` seam in the
> packet for exactly this milestone.
>
> ### Read first
> - `docs/CLAUDE-RULES-CANONICAL.md` (tiers + session discipline)
> - `ROADMAP.md` § M7/M8 rows — M7 done, M8 next
> - `docs/strategy-review-gate.md` — especially § M8 hook: `tune` recipe
>   pointer (the `tune_recipe` block shape)
> - `scripts/ml/strategy_review_packet.py` — see `Decision.action ==
>   "tune"` and where it currently slots into the matrix
> - `docs/sprint-logs/S-M7-STRATEGY-REVIEW-GATE-2026-06-09.md` § Addendum
>   — full ladder of what M7 shipped, live state, the two backlog
>   residuals
> - `scripts/backtest_*.py` (especially `backtest_squeeze.py`,
>   `backtest_fade.py`, `backtest_trend.py`,
>   `src/backtest/run_backtest_vwap.py`) — the existing per-strategy
>   research harnesses M8 will likely orchestrate
> - `runtime_logs/trainer_mirror/backtests/<UTC-date>/SUMMARY.md` (most
>   recent) — the shape the trainer publishes for completed sweeps
> - The `backtesting` skill (`.claude/skills/backtesting/SKILL.md`) — M8
>   is the **production sweep harness**; it does NOT replace
>   `backtesting` (which is the on-demand research path), it makes the
>   gate's tune action executable
>
> ### Context (from M7's live verdicts)
> The gate currently has zero strategies in `tune` territory because
> the live 7-day window is too quiet (most strategies have n_decisions
> = 0). When n_closed crosses the mid-n band (30 ≤ n < 100) with
> win_rate 40-50% and expectancy near zero, the matrix emits
> `proposed_action: tune` with a `tune_recipe` block that names:
> - `target`: e.g. `config/strategies.yaml::vwap.threshold`
> - `current_value`: the live value
> - `search_space`: e.g. `log-uniform [0.001, 0.05]`
> - `harness`: e.g. `scripts/backtest_vwap.py`
> - `evidence_window_days`: 90
>
> M8 makes that pointer **executable** — a sweep runner takes the
> `tune_recipe`, runs the harness over the search space on the trainer
> VM, and proposes the best variant back as a Tier-3 PR with the sweep
> evidence attached.
>
> ### Scope of this session
>
> 1. **`docs/strategy-tuning.md`** — the canonical M8 doc. Defines:
>    - The sweep runner contract: read `tune_recipe`, fan out across
>      the search space, evaluate each variant on
>      `evidence_window_days` of historical data, return ranked
>      candidates with net-of-fee PnL + variance + walk-forward stability.
>    - Decision matrix on TOP of the sweep results — when to ship the
>      best variant as a Tier-3 PR, when to file as "no clear winner",
>      when to escalate "no variant beats current" → `demote_shadow`
>      proposal handed back to M7.
>    - Search-space conventions per `param_kind` (log-uniform for
>      thresholds, integer-grid for lookbacks, etc.)
>    - Robustness checks: walk-forward consistency, fee headroom,
>      per-regime cell PnL slice (re-uses M7's slicer).
>    - Bounded SLA: when M7 emits `tune` and M8 runs, the sweep result
>      ships as a draft Tier-3 PR within 14 days.
>
> 2. **`scripts/ml/run_tune_recipe.py`** — Tier-1 tool that takes a
>    `tune_recipe` (read from a packet, or supplied via CLI), dispatches
>    the per-variant runs (via existing `scripts/backtest_*.py`
>    harnesses), aggregates results, writes the sweep summary to
>    `runtime_logs/strategy_reviews/<date>/<strategy>.tune.md` next to
>    the packet, and emits a `proposed_variant` block.
>
> 3. **First application** — pick whichever strategy has the most
>    informative live signal:
>    - `vwap` is `enabled: false` so it can't accrue more live data;
>      a sweep should re-validate the kill verdict against deeper
>      history (the original regime-roster matrix already characterized
>      it as a loser, so a `tune` recommendation seems unlikely — but
>      run it and document)
>    - `htf_pullback_trend_2h` has 2 closes — too few for `tune`,
>      should hold
>    - **`trend_donchian`** is the natural first M8 sweep — already
>      operator-tuned twice (S9 1h→2h, then 2h→1h trail=5.0), live with
>      real money on bybit_2. The donchian-period × trail-mult grid is
>      well-understood; M8's job is to make that sweep reproducible
>      from the gate's perspective.
>
> 4. **Tests** for the sweep runner (mock the harness subprocess, verify
>    the recipe parser + aggregation + proposed_variant emission).
>
> 5. **ROADMAP M8** → IN PROGRESS; sprint log written per
>    `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`.
>
> ### Constraints
> - **Don't modify the live order path.** `src/runtime/orders.py`,
>   `src/runtime/risk_counters.py`, `src/runtime/intents.py` are not in
>   scope.
> - **Sweep execution runs on the trainer VM.** M8 doesn't run
>   subprocesses on the live trader; it dispatches via
>   `trainer-vm-diag` (autonomous, no operator gate). The packet output
>   is mirrored back to the live VM via the existing
>   `runtime_logs/trainer_mirror/` path.
> - **Tier-3 strategy parameter changes ship as draft PRs.** The sweep
>   runner *proposes* the best variant; the operator approves the
>   merge.
> - **Reuse M7's regime slicer** (`compute_regime_cells`) so per-regime
>   PnL is comparable between the gate's packet and the sweep's
>   robustness check.
>
> ### Definition of done
> - M8 doc merged.
> - Sweep runner runs end-to-end on the trainer VM and produces a
>   sweep summary for ≥1 strategy.
> - Draft Tier-3 PR opened for the first application's
>   `proposed_variant` (or a clean "no winner found" packet if the
>   sweep result doesn't beat current).
> - ROADMAP M8 → IN PROGRESS.
> - Sprint log written.
>
> End with: what shipped, the first-application recommendation, and
> what M9-or-later needs.
