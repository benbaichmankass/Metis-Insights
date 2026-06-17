# Sprint Log: S-AUDIT-COMPLIANCE-2026-06-17

## Date Range
- Start: 2026-06-17
- End: 2026-06-17

## Objective
- Primary goal: A two-part repo compliance & bug audit across all three repos
  (`ict-trading-bot`, `ict-trader-dashboard`, `ict-trader-android`). **Part 1** —
  make the governance/instruction layer (canonical docs + skills) internally
  coherent so a fresh session never gets contradictory instructions. **Part 2** —
  audit the actual code/config/infra against that now-coherent rule set
  (structural compliance, dead/zombie infrastructure, single-source-of-truth,
  Prime-Directive gates in code) + code-level bug hunting + structural patches.
- Secondary goals: close the cross-repo governance gap (android had no
  `CLAUDE.md`); add mechanical anti-drift enforcement; bring android CI to
  parity with the bot's gating.

## Tier
- Tier 1 / 2 / 3 mix.
- Justification: governance docs, dead-code removal, CI guards, doc-drift fixes
  are Tier 1. The native IB-gateway unit removal is Tier 2. The
  `total_account_usd` sizing fix (#3865) and the binance order-path connector
  removal (#3866) are Tier 3 — both merged **with explicit operator approval**
  ("merge whatever's green … let's get that implemented", 2026-06-17).

## Starting Context
- Active roadmap items: stability/hardening cycle; M13–M16 era.
- Prior reference: the 2026-06-10 audit-blindspot retro
  (`docs/audits/audit-blindspot-zombies-2026-06-10.md`) — consistency checks
  alone miss retired-but-present integrations; the `full-system-audit` skill's
  liveness axis was built for exactly this.
- Known risks at start (operator-reported): "gates that shouldn't exist" despite
  the canonical docs; Claude asking the operator to do things manually it should
  do via GitHub Actions; data not wired to a single source of truth.

## Repo State Checked
- Branch: `main` at session start (bot `a5b3b40` region; advanced through the
  session as PRs merged; final wrap-up cut from `0a0b30a` = #3865 merge).
- Deployment: live trader `ict-bot-arm` / `141.145.193.91`
  (`ict-trader-live.service` + `ict-web-api.service` active per diag relay);
  `ict-git-sync.timer` deploys from `main` every 5 min.
- Canonical docs reviewed in full: `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md`,
  `docs/ARCHITECTURE-CANONICAL.md`; plus the dashboard & android `CLAUDE.md`.

## Files and Systems Inspected
- Governance: `CLAUDE.md` (×3 repos), `docs/CLAUDE-RULES-CANONICAL.md`,
  `docs/ARCHITECTURE-CANONICAL.md`, all `.claude/skills/*/SKILL.md`,
  `.claude/commands/*.md`, `docs/runbooks/*`, `docs/claude/*`.
- Brokers/gates/transports: `src/units/accounts/integrator.py::EXCHANGE_MAP`,
  `src/units/accounts/clients.py`, every `*_ENABLED/*_DISABLED/*_SOURCE/*_MODE`
  env-gate, Cloudflare/Vercel/tunnel remnants.
- Units/workflows: `deploy/*.service|*.timer`, `scripts/install_systemd_units.sh`,
  `src/web/api/routers/diag.py::_CANONICAL_UNITS`, `.github/workflows/*`,
  `system-actions.yml` allowlist.
- Live-money paths: `src/units/accounts/risk.py`, `prop_risk.py`,
  `src/core/coordinator.py`, `src/runtime/{orders,order_monitor,intents,pipeline,market_data}.py`,
  `src/units/accounts/execute.py`, `src/main.py`.
- Consumers: dashboard `streamlit_app.py` (every `/api/*` call site), android
  `core/network/BotApi.kt` (all 25 endpoints) + `AppPrefs.kt` + CI workflows.
- Live runtime via diag relay: `/api/diag/services` (issue #3862),
  `/api/diag/snapshot?limit=120` (issue #3864).

## Work Completed
**Part 1 — governance coherence (all merged):**
- bot **#3860**: single-sourced VM topology (purged the terminated micro
  `158.178.210.252` from active docs), 3-stage ML ladder across skills/docs,
  routed 5 runbooks back to `system-actions` workflows, reconciled the
  instruction-hierarchy mirror, fixed broken canonical links — **plus a new
  `scripts/ci/check_canonical_doc_coherence.py` guard + workflow** (dead-IP,
  removed-gate, 7-stage-ladder, hierarchy-mirror checks) and the matching
  `doc-freshness` mechanical-scan checklist.
- bot **#3861**: 2 consumer-surfaced API-doc nits (false Android/insights claim;
  missing `/strategies/{name}/review` table row).
- dashboard **#105**: README dead-IPs, roadmap-pointer refresh, 7→3 stage comment,
  nullability block slimmed to a pointer.
- android **#57**: **new deferring `CLAUDE.md`** (closed the governance gap) +
  README dead-IPs + net-config comment.

**Part 2 — liveness/zombie sweep + fixes (all merged):**
- bot **#3863** (Tier-1): deleted dead code (`default_signal_builder`,
  `is_close_verdict`), fixed the inter-tick heartbeat status bug
  (`main.py` wrote hardcoded `"ok"` over a failed tick), removed the spent
  `stop-micro-zombie.yml`, wired `ict-web-api-watchdog` into `_CANONICAL_UNITS`,
  fixed 5 doc-drift spots (incl. the now-false "`_DRY_RUN_OVERRIDES` cleanup
  pending" claims — that code was already deleted).
- dashboard **#106** (Tier-1): dead `_format_pill`/`_TV_EMA50`, News added to Tabs.
- android **#58** (Tier-1): removed dead `PlaceholderScreen` scaffolding
  (live `EventKind.inFlight` preserved).
- bot **#3865** (Tier-3): `total_account_usd` wired — `_fetch_linear_total_equity`
  restores the documented S-052 total-cross-margin-equity basis for Bybit UNIFIED
  linear sizing; best-effort (`None` → prior free-balance behaviour, no regression).
- bot **#3866** (Tier-2/3): removed two superseded integrations — the native
  IB-gateway unit (+ install/start scripts + IBC template; Docker path untouched)
  and the unrouted binance connector (19 files; every other exchange path
  byte-for-byte unchanged).
- android **#59** (CI): `testDebugUnitTest` blocking gate + seed test +
  report-only `lintDebug`.

## Validation Performed
- **CI green on every merged PR.** bot #3865/#3866 each passed the full required
  set (incl. `pytest-run`, `ruff-lint`, all guards, `canonical-doc-coherence`);
  android #58/#59 passed `build-debug-apk` + `testDebugUnitTest`.
- `scripts/ci/check_canonical_doc_coherence.py` run locally at each step and on
  final `main` — **all 4 checks pass.**
- All changed Python `py_compile`-clean; AST scan confirmed no live reference to
  the removed binance symbols.
- Live runtime confirmed via diag relay: core services active; bot ticking
  (`git_sha 3b9b353` healthy at probe time).
- **Gaps not yet verified:**
  - `_fetch_linear_total_equity` was not exercised against the LIVE Bybit wallet
    (no diag endpoint exposes the free-vs-total split). Safe by construction —
    any fetch failure returns `None` → the sizer keeps prior behaviour.
  - The `/api/diag/snapshot` bundle was malformed (embedded newlines), so
    `bybit_2`'s individual recent order-packages could not be fully enumerated;
    however **zero** min-balance/insufficient/refusal signatures were present in
    the whole bundle, so no evidence the `total_account_usd` defect was biting.
  - The binance test surgery (10 test files) was not run locally (no pytest in
    sandbox); verified green by the #3866 `pytest-run` CI instead.

## Documentation Updated
- `CLAUDE.md` (×3), `docs/CLAUDE-RULES-CANONICAL.md`, `docs/ARCHITECTURE-CANONICAL.md`
  (single-source topology, hierarchy mirror, mode-mutation contract corrected,
  stage-ladder, broken links), `.claude/skills/{doc-freshness,model-training}`,
  `.claude/commands/ml-review.md`, multiple `docs/runbooks/*`,
  `docs/exit-coverage-architecture.md`, `docs/news_layer.md`, dashboard `CLAUDE.md`
  + roadmap pointer, android `CLAUDE.md` (new). This sprint log + the
  health-review-backlog entries below.

## Contradictions or Drift Found
- The unifying root cause of all three operator-reported symptoms: the canonical
  rule was correct but a **stale neighbour** (runbook/skill/doc) said the
  opposite — and `doc-freshness` had no mechanical teeth. Fixed both the stale
  neighbours and the lack of enforcement.
- Canonical docs claimed the `_DRY_RUN_OVERRIDES`/`set_account_dry_run()`
  cleanup was "pending" — it was **already deleted** (a regression test asserts
  absence). Corrected.
- The `apply_advisory_influence` orphan is a **documented-keep**
  (`ROADMAP.md:205`, parked Tier-3 WS7 deliverable), NOT a corpse — verification
  before deletion caught this.

## Risks and Follow-Ups
- **`total_account_usd` is now live-bound** — on `main`, auto-deploys via
  `ict-git-sync`, so it starts sourcing total equity for `bybit_2` on next sync.
  No-regression by construction; watch the live box.
- Tier-3 product decisions deferred: see Deferred Items.
- Dashboard chart timezone bug (candle/EMA tz-naive vs markers UTC) — flagged for
  the **preview-app** verification path, not fixed (production runs UTC, masked).

## Deferred Items
1. **`prop_risk.py` `account_id` fix** (Tier-3) — split out of #3865 because CI
   proved it changes `daily_pnl` sourcing (turns on the journal-based daily-risk
   rebuild) and breaks 4 prop-breach tests that set `daily_pnl` in-memory. Needs
   its own PR with those tests reworked. Logged: `BL-20260617-PROP-RISK-ACCOUNT-ID`.
2. **Dashboard chart-timezone bug** — `BL-20260617-DASH-CHART-TZ`.
3. **Android lint promotion** — `lintDebug` is report-only pending a
   warning-baseline cleanup; promote to blocking later. `BL-20260617-ANDROID-LINT-PROMOTE`.

## Next Recommended Sprint
- **The `prop_risk` `account_id` Tier-3 fix** — a focused single-PR sprint: add
  `account_id=account_name` to `PropRiskManager.__init__`, then rework the 4
  prop-breach tests to seed `daily_pnl` via the journal (or a fixture) rather
  than the now-overridden in-memory field. Required verification: full
  `pytest-run` green + confirm prop-breach detection still fires under the
  journal-sourced `daily_pnl`. Prop is not live, so no urgency.

## Wrap-Up Check
- [x] Code inspected directly (not just PR summaries) — paths listed above.
- [x] Canonical docs reviewed and updated (governance layer is the deliverable).
- [x] TRADE-PIPELINE: no pipeline *stage* changed (sizing basis fix is within an
  existing stage); no `TRADE-PIPELINE.md` edit required.
- [x] Roadmap checked — status row added for this sprint.
- [x] Contradictions recorded (above + fixed in-PR).
- [x] Unknowns stated (Gaps not yet verified).
- [x] `canonical-doc-coherence` guard green on final `main`.
