# Sprint Log: S-API-TIER-POLICY-GUARD-2026-08-09

## Date Range
- Start: 2026-08-09
- End: 2026-08-09

## Objective
- Primary goal: ship a CI guard that keeps `docs/api-tier-policy.md` complete, **then** backfill the missing rows — in that order, because the guard stops the bleeding and backfilling first just means more drift by the time it lands.
- Secondary goals: re-derive the completeness figure rather than restating it; record the tier of each route from the **runtime gate**, not the route name.

## Tier
- **Tier 1** — docs + CI only.
- Justification: no runtime, config, or order-path file was modified. The guard *reads* `src/web/api/routers/` to enumerate routes; it changes none of them. `git diff --stat` confirms no file under `src/` or `config/` in the diff.

## Starting Context
- Active roadmap items: none directly — this drains `BL-20260809-API-TIER-INVENTORY-77PCT-STALE` from the health-review backlog.
- Prior sprint reference: [`S-COLLAPSED-STATES-2026-08-09.md`](S-COLLAPSED-STATES-2026-08-09.md), the session that found the gap. Its recurring defect — *a claim that is asserted rather than verified* — is the one this file embodied.
- Known risks at start: the backlog item warned that a naive matcher over-counts the gap by crediting none of the Tier-2.5 family-row convention, and that bulk-generating tier rows would be worse than the gap.

## Repo State Checked
- Branch or commit reviewed: `claude/api-tier-policy-guard-npihz7` off `main` @ `55c3455`.
- Deployment state reviewed: none — nothing here deploys.
- Canonical docs reviewed: `CLAUDE.md` § "Dashboard REST API", `docs/api-tier-policy.md`, `docs/claude/health-review-backlog.json`.

## Files and Systems Inspected
- Code files inspected: all 40 modules under `src/web/api/routers/`, `src/web/api/main.py`, `src/web/api/auth.py`; `scripts/check_diagnostic_provenance.py` (the shape to copy), `scripts/ci/run_guards.py`, `scripts/ci/guard_selftests.py`, `scripts/ops/check_backlog_refs.py`.
- Config files inspected: none.
- Deployment files inspected: `.github/workflows/guards.yml` (read-only — the registry lives in `run_guards.py`, so the YAML needed no edit).
- Docs inspected: as above, plus `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`.
- Services or timers inspected: none.
- GitHub Actions workflows inspected: `guards.yml`.

## Work Completed
- **Item 1 — the guard.** `scripts/check_api_tier_policy.py`: a route defined under `src/web/api/routers/` must carry a row. Routes are enumerated **by AST, joining each `@router.<verb>` to its `APIRouter(prefix=...)`** — matching the decorator string alone would compare `/snapshot` against `/api/diag/snapshot` and report a gap for every route in the repo. Registered as `api-tier-policy-guard` in `scripts/ci/run_guards.py`, with a failure-path self-test in `scripts/ci/guard_selftests.py`.
- **Item 2 — the family-row convention is credited, not overridden.** The Tier-2.5 section documents diag leaves (`audit`, `journal`, …) after a sibling full path. The parser resolves a bare leaf against the last full path in the same row, so the existing shorthand counts as documentation **as written**. A guard that silently forces a correct document into a reformat picks a fight with the thing it protects; a test (`TestCreditsTheFamilyRowConvention`) pins this.
- **Item 3 — the backfill, row by row.** All missing rows written by reading the actual gate in each router. Coverage went **36/92 → 92/92**.
- **Item 4 — the banner replaced by the guard's name**, and its number computed rather than counted (`--list`), with a test asserting the stated figure equals a freshly computed one.
- **Item 5 — a third bearer token surfaced and given a home** (see Contradictions).

## Validation Performed
- **Tests run:** `tests/test_check_api_tier_policy.py` — 24 passed. Also re-ran `tests/test_check_artifact_validity.py tests/test_check_research_index.py tests/test_check_backlog_refs.py` — 57 passed.
- **The enumerator was verified against the live FastAPI route table**, not just asserted. Built a venv, imported `src.web.api.main:app`, and diffed `app.routes` against the AST walk: it finds **exactly** the 92 router-defined routes with **zero false positives**, and the only live routes it does not cover are the five defined outside `routers/` — `GET /api/health` plus FastAPI's four built-in docs routes. Kept as a test (`test_matches_the_live_fastapi_route_table`, skipped where FastAPI is not importable — the guard itself is stdlib-only by design).
- **The failure path was exercised, in both modes.** `--all` with a row deleted → exit 1. A diff adding a new undocumented route → exit 1 naming that route. A diff touching only the `APIRouter(prefix=…)` line → all routes in that file pulled into scope.
- **The coverage assertion was checked to bite:** editing the doc's stated number to 91/92 fails the test; restored, it passes.
- **Gate inventory measured with a denominator, not sampled:** an AST pass over all 92 route handlers found **22 gated** (16 `_require_diag_token`, 2 `require_session`, 3 `_check_admin_token`, 1 `_require_write_token`) and **70 ungated**. Every tier row below rests on that scan rather than on the route's name.
- **Lint:** `ruff check` clean on all new/changed files under the repo's pin (0.15.22). `ruff format --check` would reformat them — **and would equally reformat `scripts/check_diagnostic_provenance.py` and `scripts/ci/run_guards.py`**, and no workflow runs it, so this is repo convention rather than a miss. Checked, not assumed; the board flagged the unpinned-ruff trap the same day.
- **Gaps not yet verified:** I did not enumerate every sibling guard affected by the `--all` step-skip below — only the mechanism and the one instance I was next to. Said so in the backlog item rather than implying a full sweep.

## Documentation Updated
- Rules doc updates: none.
- Architecture doc updates: none.
- Trade pipeline doc updates: none — no pipeline stage touched.
- Roadmap updates: none.
- GitHub Actions doc updates: none — the registry entry is self-describing.
- Subsystem doc updates: `docs/api-tier-policy.md` (backfill + banner replaced by the guard's name); `CLAUDE.md` (one corrected gate description, below).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- **Contradiction 1 — a third bearer token with no home in the taxonomy.** Four routes are gated by `DASHBOARD_API_TOKEN`, which is neither the Tier-2 JWT nor the Tier-2.5 diag token. Worse, **the two gates fail in opposite directions when the var is unset**: `POST /api/bot/prop/report` is fail-**closed** (503, deliberately — BL-20260705), while `GET`/`DELETE`/`PATCH` on `/api/bot/devices` are fail-**open** (`_check_admin_token` returns silently). They are now a labelled Tier-2 sub-table stating that per row. I did **not** change any gate: tightening one is a runtime change (Tier 2), not a docs backfill.
- **Contradiction 2 — `CLAUDE.md` labels the `devices` rows Tier 1**, on the strength of the permissive default. Recorded in place as a known divergence rather than resolved by rewriting either doc: that table documents the contract a consumer sees, this one documents the gate mechanism. Whether fail-open is right for the DELETE/PATCH is a live question, not a settled one.
- **Code/doc mismatch — fixed.** `CLAUDE.md`'s `POST /api/bot/prop/report` row read "token-gated via `DASHBOARD_API_TOKEN` **when set**", which describes the permissive `_check_admin_token` shape the prop write path deliberately does *not* use. Field beats comment: the row now states the fail-closed 503 behaviour.
- **Tier-1's own rule was violated by two of its routes.** The section said "Read-only — never mutates state"; `POST /devices/register` and `POST /learning/progress` are unauthenticated writes. Rather than mis-tier them (their runtime gate is *nothing*, so they are not Tier 2), the rule now names the carve-out explicitly and bounds it: a write touching money, an order, config, or a notification does not qualify.
- **The completeness figure moved again.** The backlog recorded 90 routes / 36 documented; this session measured **92 / 36** — same documented count, two apart on the denominator, hours later. Not reconciled, and deliberately so: it is the argument for the guard, and the enumerator was validated against the live app instead.

## Risks and Follow-Ups
- Remaining technical risks: the guard's population is `routers/` only. A route added directly to `main.py` would not be caught. That is stated in the doc rather than silently assumed, and `/api/health` is rowed anyway.
- Remaining product decisions (Tier 3): none.
- Blockers: none.
- **Filed:** `BL-20260809-GUARD-STEP-WHEN-SKIPS-ON-PUSH` — under `run_guards.py --all` (push / workflow_dispatch), `changed` is empty, so a per-**step** `when` clause can never match and the step is silently skipped. `guards.yml` comments that push runs everything "stricter than the retired behaviour, never weaker"; for step-gated guards it is the reverse. Verified by observation, and `diagnostic-provenance-guard`'s scan step has the same shape. Not fixed here — the one-line fix flips behaviour for every step-gated guard at once and wants its own PR. `api-tier-policy-guard` sidesteps it by carrying its `--all` step **ungated**, which is also what makes the row-deletion case catchable.

## Addendum — two follow-ons taken in the same session

### A. My own test committed the defect class the guard catches

`pytest-run` went red on `test_matches_the_live_fastapi_route_table`. The repo pins only `fastapi>=0.110.0`, so **CI resolved 0.141 while my venv had 0.115** — and on the newer version `include_router` leaves an `_IncludedRouter` wrapper exposing neither `.path` nor `.routes` (sub-routes hang off `.original_router.routes`). My one-level walk of `app.routes` therefore found **5** routes, and then reported all 92 enumerated routes as "not live".

The bug was not the traversal. It was that **the probe never asserted its own denominator** — it compared a near-empty extraction against a real set and produced a confident, entirely wrong 92-line diff. That is sub-class **C** (unasserted denominator) from `CLAUDE.md` § "Diagnostic provenance", occurring inside the test written to validate a guard against that very class.

Fixed by traversing **by structure, not version sniffing** (a version check would fail silently on the next rename, exactly as the original did), and by making an implausible extraction fail as *a broken probe* naming the real cause. Verified green under **both** 0.115.6 and 0.141.1, and `TestLiveRouteProbe` pins both shapes with stubs so the next regression is caught as a one-line assertion rather than an unreadable route dump.

### B. `BL-20260809-GUARD-STEP-WHEN-SKIPS-ON-PUSH` — resolved, but not by the fix I proposed

The item offered option (a): *"under `force_all`, treat a per-step `when` as satisfied — one line, and it makes the comment true."* **Measuring first killed it.**

Both step-gated commands in the registry — the only two, `api-tier-policy-guard` and `diagnostic-provenance-guard`, not the "several" the item guessed at — consume `{pr_diff}`, and on push that file is **empty**. Forcing them to run makes `check_diagnostic_provenance.py` print *"OK — every scanned diagnostic states what it computed"* and exit 0 having scanned nothing. **Option (a) would have made the comment true and the check false.** Substituting the whole-tree equivalent is no better: `--all` exits 1 on **52** pre-existing grandfathered sites (which is *why* it is diff-scoped) and would redden `main` on every merge.

So the skip is correct and stays. The real defect was that it was **indistinguishable from an ordinary not-relevant skip** — a reader could not tell *"we asked about this diff and it did not apply"* from *"there was no diff to ask about"*. Shipped: the skip names its reason, the summary prints a `NOT SCANNED on this event (N)` block with the remedy, `guards.yml`'s "stricter … never weaker" claim (the thing that made the gap invisible) is corrected, and `tests/test_run_guards_step_scoping.py` pins the distinction **and the premise** — it fails if a future step-gated command stops consuming `{pr_diff}`, so the reasoning is re-derived rather than inherited.

**The transferable bit:** both A and B are the same shape as the sprint that opened this file — *a claim asserted rather than verified*. In B the claim was mine, in my own backlog item, written the same day.

## Deferred Items
- Deferred item 1: hardening `DELETE`/`PATCH /api/bot/devices` to the prop route's fail-closed shape (Tier 2 — a runtime change).
- Deferred item 2: the 52 pre-existing `diagnostic-provenance --all` findings remain grandfathered. Not touched here; they are why that guard cannot run whole-tree on push.

## Next Recommended Sprint
- Suggested next sprint: none required by this work. The guard is self-maintaining.
- Why next: the inventory now fails CI when it drifts, so it no longer needs a periodic manual audit — which was the point.
- Required verification before starting: none.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries — every tier row rests on an AST gate scan over all 92 handlers, plus reading the four token-gate helpers.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage was touched, so `docs/TRADE-PIPELINE.md` needed no update.
- [x] Roadmap status was checked — no milestone claims this work.
- [x] Contradictions were recorded (four, above) rather than quietly reconciled.
- [x] Remaining unknowns were stated clearly — notably that the affected-guard list for the step-skip is unenumerated.
