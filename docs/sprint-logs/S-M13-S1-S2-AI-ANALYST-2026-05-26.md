# Sprint Log: S-M13-S1-S2-AI-ANALYST-2026-05-26

## Date Range
- Start: 2026-05-26 ~17:25 UTC (first M13 commit on this session's branch)
- End:   2026-05-26 ~22:00 UTC (sprint closure)

## Objective
- **Primary goal:** Stand up a Tier-1, read-only **AI Analyst** that emits
  natural-language insights + structured grades over the live trading data,
  surfaced as a FastAPI router at `/api/bot/insights/*` and read by the
  Streamlit dashboard + M12 Android app. NOT a registry artifact; never
  influences the order path.
- **Secondary goals (added mid-sprint as the operator's needs sharpened):**
  1. Provider-flexible architecture: don't lock to Anthropic. Support
     a deterministic rule-based mode (no API key) + Anthropic + Gemini,
     all behind one `INSIGHTS_MODEL_MODE` env switch.
  2. Two-tier cadence (15-min fast, 60-min slow) so the better/scarcer
     model can be reserved for the deeper per-strategy narratives.
  3. Activate + verify end-to-end on the live VM, **autonomously**, via
     `system-actions` — never tell the operator "SSH to the VM and …".

## Tier
- **Tier 1** throughout. No file in `src/runtime/orders.py`,
  `src/runtime/risk_counters.py`, `src/core/coordinator.py`,
  `src/units/strategies/`, `config/strategies.yaml`,
  `config/accounts.yaml`, or `config/risk_caps.yaml` was touched. The
  generator process owns one tiny write surface (cache files +
  `insights_history` + `insights_usage`); the FastAPI router serving
  the result never imports the `anthropic` SDK — enforced by a
  dedicated invariant test that asserts `anthropic` is absent from
  `sys.modules` after the router import.

## Starting Context
- **Active roadmap items at start:** M12 S1 (Android) on a parallel
  session. M13 had a pre-staged ROADMAP row (PR #2071) but no code.
- **Prior sprint reference:** None — first M13 sprint.
- **Known risks at start:**
  - Anthropic API runaway spend → mitigated by cache-only router +
    monthly cost gate.
  - Hallucinated trades (LLM inventing setups) → mitigated by static
    system-prompt rule "every claim must cite an id from the input rows".
  - Ship-Autonomously Rule violations: any "operator: SSH to the VM"
    instruction is the documented anti-pattern. This sprint hit + corrected
    that exactly once (see § Contradictions).

## Repo State Checked
- **Branches:** Each PR cut a fresh feature branch off latest `main`
  (`claude/m13-{*}-5EP04`). Dashboard surface lived on the standing
  preview branch `claude/web-app-preview`.
- **Deployment state checked:**
  - `ict-git-sync.timer` (every 5 min) auto-pulled each PR after merge.
  - `scripts/deploy_pull_restart.sh` ran `install_systemd_units.sh`
    which installed the new `ict-insights-generator.{service,timer}`
    units + drop-ins.
  - `enable-insights-generator` system-action confirmed the timer
    was already `is-enabled=enabled, is-active=active` on first run
    (idempotent no-op — the deploy-restart enumeration had already
    enabled it). Verified via the `inspect-insights` action three
    times across the sprint.
- **Canonical docs reviewed:** `CLAUDE.md`,
  `docs/CLAUDE-RULES-CANONICAL.md` (Permission Tiers,
  Ship-Autonomously Rule), `docs/ARCHITECTURE-CANONICAL.md`,
  `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`,
  `.claude/skills/health-review/SKILL.md`, the three-way review-split
  doc landed mid-sprint (PR #2086 / #2098), and the existing
  system-actions documentation pattern at
  `docs/claude/system-actions.md`.

## Files and Systems Inspected
- **Code files read whole:**
  - `src/web/api/main.py` (router-mount order).
  - `src/web/api/routers/devices.py`, `health_snapshots.py`
    (router-style precedent).
  - `src/units/db/database.py` (canonical schema bootstrap pattern;
    mirrored for the two new `insights_*` tables).
  - `src/utils/paths.py` (env-then-DATA_DIR-then-repo precedence for
    `runtime_logs_dir` / `trade_journal_db_path`).
  - `tests/test_devices_router.py`, `tests/fixtures/real_schema_db.py`
    (test patterns).
  - `tests/test_s012_service_consolidation.py` (canonical-services guard
    — caught the missing `EXPECTED_SERVICES` entry).
  - `tests/ops/test_system_actions_workflow.py` (system-action allowlist
    guard — caught missing entries each time a new action landed).
  - `scripts/check_env_gate_in_diff.py` (the `*_ENABLED` suspect-pattern
    guard — caught `INSIGHTS_ENABLED` env-read without the
    `# allow-silent:` annotation).
  - `scripts/ops/_lib.sh` (`load_runtime_env`, `runtime_db_path`).
  - `scripts/ops/enable_mobile_push.sh`, `disable_mobile_push.sh`,
    `inspect_closed_pnl_action.sh`, `notify_run.sh` (templates).
  - `.github/workflows/system-actions.yml`, `vm-diag-snapshot.yml`
    (dispatch path).
- **Config files inspected:** `requirements.txt` — confirmed
  `anthropic>=0.40.0` + `httpx>=0.27.0` already present. No new deps
  for Gemini integration (REST over the existing `httpx`).
- **Docs updated:** `CLAUDE.md` (REST API table), `ROADMAP.md` (M13
  row flipped to S1+S2 ✅), `docs/claude/system-actions.md` (5 new
  allowlist rows), `docs/claude/deployment-ops.md` (new units),
  `docs/runbooks/insights.md` (new, then expanded across both
  sprints), `docs/sprint-plans/ROADMAP-AI-ANALYST-2026-05-26.md` (new).
- **Services / timers introduced:**
  - `ict-insights-generator.service` (oneshot, fast tier).
  - `ict-insights-generator.timer` (was 10-min; **now 15-min** after S2).
  - `ict-insights-generator-strategies.service` (oneshot, slow tier).
  - `ict-insights-generator-strategies.timer` (60-min).
- **GitHub Actions workflows edited:**
  `.github/workflows/system-actions.yml` — added 4 new action entries
  (`enable-insights-generator`, `disable-insights-generator`,
  `inspect-insights`, `kick-insights`).

## Work Completed

**Eight feature PRs + four hotfix PRs across two repos, all merged by S2 close:**

### Bot repo (`benbaichmankass/ict-trading-bot`)

| PR | Title | Role |
|---|---|---|
| #2076 (PR A) | `docs(M13): AI Analyst sprint roadmap` | Sprint plan + ROADMAP row flip to "S1 in flight" |
| #2080 (PR B) | `feat(insights): /api/bot/insights/* router skeleton` | New router; cache-miss → 200 placeholder; the "router never imports anthropic" invariant test pins the contract |
| #2081 (PR C) | `feat(insights): generator + history + cost gate` | `src/runtime/insights/` package: `cache.py`, `data_sources.py`, `prompts.py`, `history.py`, `usage.py`, `generator.py`. Two new tables in `database.py`. 12 tests. |
| #2083 (PR D) | `feat(insights): systemd timer + cycle wrapper + runbook` | `deploy/ict-insights-generator.{service,timer}` + `scripts/ops/run_insights_cycle.sh` + first version of the runbook. The PR's initial commit told the operator "SSH and `systemctl enable --now`" — caught and corrected in the same PR's follow-on commit (see § Contradictions). |
| #2091 (PR F) | `feat(insights): /api/bot/insights/{history,usage} read endpoints` | Surfaces the persistent-side tables for the dashboard |
| #2092 (PR H) | `feat(insights): Tier-1 inspect-insights system-action` | Read-only state-dump action: cache dir + cache file samples + history count + usage spend + timer state + journal tail in one issue comment |
| #2103 (PR I) | `feat(insights): rule-based template analyst — default provider-free mode` | New `template_analyst.py` + `INSIGHTS_MODEL_MODE={template,anthropic}`. Made `template` the default. Grade rules: good/watch/concern. Signals: drawdown_threshold / low_win_rate / losing_streak / exit_reason_skew / no_activity / health_failing / stale_snapshot. +11 tests, +7 router tests from PR F. 229 total in-scope tests pass. |
| #2108 (PR J) | `feat(insights): Gemini provider + two-tier cadence split` | `_call_gemini()` REST via `httpx`. `INSIGHTS_MODEL_MODE=gemini` valid. Fast tier (every 15 min, 2.0-flash) = summary+recent+health. Slow tier (every 60 min, 2.5-flash) = 6 strategies. +4 Gemini tests. |
| #2114 (PR K_) | `feat(insights): Tier-1 kick-insights manual-fire system-action` | Operator's "verify provider changes without waiting for the next timer fire." Runs the oneshot synchronously, then dumps the journal tail + 5 newest `insights_usage` + `insights_history` rows. |
| **Hotfixes** | | |
| #2095 | `fix(insights): __main__.py shim + correct trades-table column names` | The cycle's `python -m src.runtime.insights` failed (no `__main__.py`); `data_sources.py` queried `trades.opened_at` / `trades.closed_at` which don't exist. |
| #2097 | `fix(insights): install data-dir drop-in for ict-insights-generator.service` | Without the drop-in, the Python subprocess resolved `trade_journal_db_path()` to repo-relative `<repo>/trade_journal.db` (empty file) instead of `/data/bot-data/trade_journal.db`. |
| #2113 | `fix(insights): log Gemini error body + retry once on 429` | First Gemini cycle returned 429 with no body in the log. PR captures the JSON error body so the next 429 surfaces the actual quota name. |

### Dashboard repo (`benbaichmankass/ict-trader-dashboard`)

| PR | Branch | Role |
|---|---|---|
| #80 (PR G) | `claude/web-app-preview` | Insights tab + Overview "Latest Analyst Read" card. Then in a follow-on commit on the same branch: top-of-tab **usage panel** (monthly spend / budget / tokens / calls + per-endpoint split) + per-endpoint **history expanders** (last 20 runs in a collapsible drill-in). Lazy-degrades when the bot returns `table_present: false`. |

### Live-VM activation events (in chronological order)

| Time UTC | Action | Result |
|---|---|---|
| 19:31:38 | issue #2088 `enable-insights-generator` | Timer was already enabled+active; action was a no-op (correct outcome for an idempotent wrapper). |
| 19:32:19 | issue #2089 `vm-diag-request journalctl?unit=…` | Failed — `&` in title got HTML-entity-encoded; not used after this. |
| 19:33:46 | issue #2090 `[diag-request] services` | Confirmed `ict-web-api`, `ict-trader-live`, `ict-telegram-bot` etc. are active (insights units not in the diag allowlist — fine, the system-action wrapper had already verified them). |
| 19:50:21 | issue #2093 `inspect-insights` | Failed exit 127 — `inspect_insights.sh` not yet on the VM (PR H still rolling). |
| 19:54:15 | issue #2094 `inspect-insights` retry | Succeeded — timer enabled+active, next fire 20:14:19, but **caches empty + every endpoint failing** with `__main__.py` missing + `no such table: trades`. → Drove PR #2095. |
| 20:10:31 | issue #2096 `inspect-insights` | After #2095 merged + rolled: `__main__.py` resolved, but every Anthropic call returned `400 credit balance is too low`. Surfaced the `trades`-table-missing issue (canonical DB not resolved) → drove PR #2097. |
| 21:09:31 | issue #2099 `inspect-insights` | After #2097 merged + rolled: **9 real cache files on disk**, 33 `insights_history` rows, all `model_id=template:v1`. The template-mode analyst was now writing grounded prose. |
| 21:31:48 | dispatched `set-env GEMINI_API_KEY=…` + `set-env INSIGHTS_MODEL_MODE=gemini` | Operator linked an AI Studio API key; the env was written but a billing-account-attach step on the GCP project was still pending. |
| 21:48:36 | issue #2118 `kick-insights` (Gemini) | All three calls returned 429 — the new error-body logging from PR #2113 surfaced `Quota exceeded ... limit:0` on three free-tier metrics. Diagnosed as missing billing-account link on the GCP project. |
| 21:53:45 | issue #2119 `kick-insights` (after operator attached card) | Still 429 — Paid Tier propagation slow OR cost vs free-tier-quotas was still being figured out. |
| 22:00:00 | operator decision: flip back to `template` for $0 | issue #2120 `set-env INSIGHTS_MODEL_MODE=template` → reverted. Gemini integration stays installed but unused; can be re-enabled by flipping the env back. |

## Validation Performed

- **Tests run:**
  - `pytest tests/test_insights_router.py` — 20 (13 PR B + 7 PR F).
  - `pytest tests/test_insights_generator.py` — 36 (12 PR C + 16 PR I refixturing + 4 PR J Gemini + 4 PR #2113 retry/error-body tests).
  - `pytest tests/test_insights_template_analyst.py` — 11 PR I.
  - `pytest tests/test_s012_service_consolidation.py` — passes with both new services in `EXPECTED_SERVICES`.
  - `pytest tests/ops/test_system_actions_workflow.py` — 190 (added entries for `enable-insights-generator`, `disable-insights-generator`, `inspect-insights`, `kick-insights`).
  - Full repo guard sweep — green on every PR by merge time.
- **CI guards exercised on every PR:** `ruff-lint`, `env-gate-guard`,
  `canonical-db-resolver`, `canonical-config-loaders`, `arch-doc-guard`,
  `pytest-collect`, `pytest-run`, `repo-inventory`, `secret-scan`,
  `silent-empty-guard`, `dry-run-guard`. Two transient ruff failures
  (PR I: unused `packages` local; PR #2113 fakeresp missing
  `status_code`) — caught + fixed within minutes of the failed check.
- **Live-VM verification, autonomous via `inspect-insights`:**
  Captured 4 distinct states across the sprint (see table above).
  Final state (21:09 UTC, post-#2097-rollout) showed 9 cache files,
  33 history rows, grounded prose. Each cache row carries verifiable
  metrics that match the underlying DB (e.g. vwap: 56/20 closed,
  -$13.64, 5% win rate over 20 — every claim grounded in
  `trades` rows that exist).
- **Gaps not yet verified:**
  - **Gemini prose quality**: never observed a real 200 response;
    project's GCP billing flow needs a Paid Tier upgrade + propagation
    window. **Operator action item for AM:** monitor whether the Paid
    Tier quota lifts, optionally flip `INSIGHTS_MODEL_MODE=gemini`
    after setting a budget cap.
  - **First eval of grade quality on template-mode output**: the prose
    is grounded by construction (no LLM), but the operator's
    eyeballing of the dashboard panels is the final UX check.
  - **Per-strategy hourly cadence end-to-end** on the new
    `ict-insights-generator-strategies.timer`: it's `is-enabled=enabled`
    after deploy, but the first 60-min fire hadn't landed before
    sprint close. Will fire by ~22:30 UTC on its own.

## Documentation Updated

- **Rules doc updates (`docs/CLAUDE-RULES-CANONICAL.md`):** None.
- **Architecture doc updates (`docs/ARCHITECTURE-CANONICAL.md`):**
  None — the analyst is a read-only observer; canonical persistence
  model already covered.
- **Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`):** None
  (no pipeline stages touched).
- **Roadmap updates:** Flipped the M13 row to `S1 + S2 ✅ COMPLETE
  2026-05-26` with the full PR ladder + the "S3 deferred" note.
- **GitHub Actions doc updates:** `docs/claude/system-actions.md`
  gained 4 new allowlist rows.
- **Subsystem doc updates:**
  - `CLAUDE.md` — added `/api/bot/insights/*` to the Dashboard REST API
    table + architecture diagram + Key Directories listing.
  - `docs/runbooks/insights.md` — new runbook for the operator (env
    toggles, two-tier cadence math, Gemini vs Anthropic vs template
    modes, troubleshooting, cost ceiling math).
  - `docs/claude/deployment-ops.md` — added both new services.
- **Historical docs marked superseded:** None.

## Contradictions or Drift Found

- **PR D's initial commit told the operator to SSH** to the VM and
  `sudo systemctl enable --now ict-insights-generator.timer` — the exact
  anti-pattern called out in `docs/CLAUDE-RULES-CANONICAL.md`
  Ship-Autonomously Rule. **Caught + corrected** in the same PR's
  follow-on commit (`011f508`) which replaced the SSH instruction
  with the two new `enable-insights-generator` /
  `disable-insights-generator` system-actions + their wrapper scripts +
  a rewritten runbook section. No remaining documented contradictions.
- **PR #2082 .env regression (out-of-scope but flagged):** the live
  VM's `.env` carries the FCM service-account JSON spilled raw across
  lines 45–56 from before #2082 was meant to externalize it. Every
  script that does `source .env` (including this sprint's wrapper)
  throws shell parse errors on lines 45–56. Benign for M13 (systemd's
  `EnvironmentFile=` skips invalid lines independently, and the
  drop-in supplies `DATA_DIR`/`TRADE_JOURNAL_DB`), but it's noisy in
  every journal tail. Belongs in the M12 mobile-push track.

## Risks and Follow-Ups

- **Remaining technical risks:**
  - **Gemini activation** is deferred pending the GCP Paid Tier
    propagation + a cost vs quality decision. Template mode covers
    the dashboard surface in the interim with zero spend, so this is
    not blocking the milestone.
  - **`insights_history` retention** — no TTL. At 4 endpoints × 96
    cycles/day fast + 6 × 24 slow = 528 rows/day, ~190k rows/year.
    SQLite handles that fine; a rolling-truncate is a future S3
    candidate.
  - **`.env` parse pollution** — non-blocking but visible in every
    journal tail. Fix lives in the M12 mobile-push track (PR #2082
    follow-up).
- **Remaining product decisions (Tier 3):** None — every M13 surface
  is Tier-1 by design.
- **Blockers:** None for S2 closure. Gemini activation is opt-in
  (just flip the env once the operator commits to the cost).

## Deferred Items

- **M13 S3 — Gemini-driven `/health-review` + `/performance-review` +
  `/ml-review` every 6 hours** with Telegram + FCM push, results
  logged to `insights_history` with `endpoint=review_<type>`.
  Operator decided 2026-05-26 to defer entirely until they evaluate
  template-mode dashboard prose first.
- **History-table retention policy.** TBD.
- **Anthropic credit top-up.** Optional fallback provider — not
  needed at present since template + Gemini cover the cases.
- **Dashboard repo `main` merge of `claude/web-app-preview`.** The
  operator manages that branch.

## Next Recommended Sprint

- **Suggested next sprint:** `S-M13-S3-INSIGHTS-EVAL-2026-06-02` (or
  whenever 7 days of template-mode data has accumulated). Re-dispatch
  `inspect-insights`, review the cache files + history + dashboard,
  decide on:
  - Gemini activation (with budget cap),
  - history-table retention,
  - whether the 6-hour Gemini-review tier is worth the spend now that
    template-mode prose is visible.
- **Why next:** S1+S2 stood up the surface; S3's purpose is the first
  empirical check on whether the prose is actually useful + whether
  the operator wants to upgrade to LLM-quality narratives.
- **Required verification before starting:**
  - At least 7 days of `insights_history` rows on template mode.
  - Operator's eyeballed verdict on the dashboard's Insights tab +
    Overview card content.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred from summaries.
- [x] Documentation reviewed + updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md`
  was updated — N/A (no pipeline stages touched).
- [x] Roadmap status checked + updated (M13 row → S1+S2 ✅).
- [x] Contradictions recorded (PR D initial SSH instruction +
  PR #2082 .env regression).
- [x] Remaining unknowns stated clearly (Gemini prose quality eval,
  retention policy, S3 cost decision).
