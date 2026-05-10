# Sprint S-067 — Silent-empty error path audit & hardening

> **Date filed:** 2026-05-10
> **Trigger:** 24h trade-performance review on 2026-05-10
> ([branch `claude/analyze-trading-performance-Xbjbg`](https://github.com/benbaichmankass/ict-trading-bot/tree/claude/analyze-trading-performance-Xbjbg)).
> Same root-cause class as PRs #627 and #629.

## 1. Goal

Audit every `except Exception` / `except sqlite3.Error` / bare-except site
under `src/web_api/`, `src/runtime/`, `src/units/db/` (and the
`_journal_*` helpers), classify each as legitimate or trust-corroding,
and convert the trust-corroding ones to `logger.exception(...) + raise`
(or `HTTPException 503` at the endpoint boundary). Add a CI guard so the
pattern can't come back. After this sprint ships, a future schema or
SQL bug surfaces as a loud failing test or 5xx response — never as a
silent `[]` that takes weeks to spot from production behaviour
(`/positions` was wrong since first commit; `/signals` dropped `price`
in production for an unknown duration).

## 2. Dependencies

- **Sprint dependency:** PR #627 (`/positions` schema fix) and PR #629
  (`/signals` price aliasing fix) on `main` — they define the regression-test
  pattern this sprint generalises. Both merged 2026-05-09.
- **Infra dependency:** none. The real-schema test fixture pattern from PR
  #627 is duplicated locally where needed; an extraction sprint is filed as
  follow-up (§ 8 Hand-off).
- **External dependency:** none.

## 3. Deliverables

- `docs/audits/silent-empty-2026-05-10.md` — classification table (file,
  line, current pattern, classification, proposed action, follow-up PR if
  any).
- One PR per ~3-5 trust-corroding sites converting `except …: return
  <empty>` to `logger.exception(...) + raise` (or `HTTPException 503`),
  each with a regression test asserting the endpoint raises (not returns
  empty) on a known-bad query.
- `scripts/lint/check_silent_empty.py` — AST-based lint check flagging new
  `except (Exception | sqlite3.Error | BaseException): return ([] | {} |
  None)` sites inside `src/web_api/` and `src/units/db/` unless the
  exception block carries a `# allow-silent: <reason>` comment.
- `.github/workflows/lint.yml` (or existing equivalent) wired to run the
  new check on every push and PR.
- `docs/claude/bug-log.md` — silent-empty class entry citing #627, #629,
  and this sprint's audit + fix PRs.
- `docs/claude/testing-policy.md` — new "writing a new endpoint" checklist
  item requiring an explicit error-path assertion (not just a happy-path
  test).
- `docs/sprint-summaries/sprint-067-summary.md` — closing summary.
- Updated `docs/claude/milestone-state.md` and
  `docs/claude/checkpoints/CHECKPOINT_LOG.md`.

## 4. Checkpoints

| #  | Checkpoint title                                | What completes by then                                                                                                                       | Risk class | Wall-clock | Gates next  |
|----|-------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------|------------|------------|-------------|
| T0 | Audit grep + classification                     | `docs/audits/silent-empty-2026-05-10.md` lists every offending site under the in-scope dirs with classification and proposed action.         | infra      | 1.5h       | T1, T3      |
| T1 | First fix batch                                 | First ~3-5 trust-corroding sites converted, regression tests added, PR opened + self-merged (Tier 1 / infra).                                | infra      | 2h         | T2          |
| T2 | Remaining fix batch(es)                         | All remaining trust-corroding sites converted across one or more PRs. Each PR has its own regression test.                                   | infra      | 2-3h       | T4          |
| T3 | CI guard                                        | `scripts/lint/check_silent_empty.py` lands, wired to CI, demonstrated to block a synthetic offending PR via a unit test on the lint script. | infra      | 1.5h       | T4          |
| T4 | Docs + sprint close                             | `bug-log.md`, `testing-policy.md`, `sprint-067-summary.md`, `milestone-state.md`, `CHECKPOINT_LOG.md` all updated. Sprint close PR self-merged. | docs-only  | 0.5h       | (sprint end)|

**Notes**
- T1 and T3 are independent of each other but both depend on T0's audit
  output. Run them in parallel where practical.
- If T0 surfaces > ~25 trust-corroding sites, stop and revise the prompt
  per `sprint-planning.md` § "When to revise the prompt mid-sprint" — the
  sprint is too big and should split.

## 4b. Unit boundary declaration

| Unit                          | Role in this sprint |
|-------------------------------|---------------------|
| `src/units/strategies/`       | untouched           |
| `src/units/accounts/`         | untouched           |
| `src/data_layer/` (DB unit)   | untouched           |
| `src/units/db/`               | reads + owns (error-handling pattern fixes only; no schema or query changes) |
| `src/web_api/`                | owns (error-handling pattern fixes + tests)                                  |
| `src/ui/`                     | untouched           |
| `src/runtime/`                | reads + owns (error-handling pattern fixes only; no orchestration changes)   |
| `src/bot/`                    | untouched           |
| `src/core/coordinator.py`     | untouched           |

No new cross-unit imports. The fixes preserve existing call shapes — only
the failure-mode contract changes (silent empty → loud error).

## 5. Risk class & merge model

All PRs in this sprint are **infra** class per `sprint-planning.md` § 5:
read-path error-handling, tests, CI, documentation. Self-merge.

**Explicitly out of scope (would change the class):**
- Live order path (`src/runtime/orders.py`, `src/main.py`,
  `src/runtime/risk_counters.py`).
- Any change to monitor / dispatch decision logic.
- Any env-gate addition or removal.
- Any change to query SQL semantics (only the catch handler changes).

If T0 surfaces a site whose fix would require any of the above, stop and
file the work into the relevant follow-up sprint (§ 8 Hand-off).

## 6. Success criteria

- ✅ `docs/audits/silent-empty-2026-05-10.md` exists, lists every
  in-scope offending site, and every site has a classification.
- ✅ Every site classified "trust-corroding" in the audit either has a
  merged fix PR by sprint close, or has an explicit deferred-with-reason
  note in the audit doc.
- ✅ `pytest` green at sprint close, including the new regression tests
  (one per converted site).
- ✅ `python scripts/lint/check_silent_empty.py src/web_api src/units/db`
  exits 0 on `main` after the sprint.
- ✅ A synthetic test PR introducing a new offending pattern fails CI
  with a non-zero exit from the lint script (proven via a unit test on
  the lint script itself).
- ✅ `docs/claude/bug-log.md` includes the silent-empty class entry.
- ✅ `docs/claude/testing-policy.md` includes the new endpoint
  error-path checklist item.
- ❌ No live-order-path file modified in any PR in this sprint.
- ❌ No env-gate added or removed in any PR in this sprint.

## 7. Hard guardrails

- No edits to `src/runtime/orders.py`, `src/main.py`,
  `src/runtime/risk_counters.py`, `config/strategies.yaml`,
  `deploy/*.service`, or any `*.env*` file.
- No new env-gates introduced. If a fix conceptually wants one, file it
  into the follow-up sprint instead.
- No bare `raise` without first calling `logger.exception(...)` — the
  whole point is loud failure, which means a logged stack trace at the
  boundary.
- No dropping of legitimate `except` blocks (e.g. fan-out across optional
  data sources where empty really is the right answer). Classify each
  before touching it.

## 8. Hand-off

After this sprint ships, the natural follow-ups in priority order:

1. **Test fixture extraction** — refactor PR #627's real-schema fixture into
   `tests/fixtures/real_schema_db.py`, apply to every read endpoint.
   Tier 1 / infra. Independent.
2. **`/api/bot/trades/closed`** (closes ict-trading-bot#557) — replaces the
   dashboard's `deriveClosedTradesFromLogs` regex fallback with a real
   endpoint. Tier 1 / infra. Builds on the fixture extraction.
3. **`closed → exchange-flat` invariant reconciler** — periodic check that
   any DB row marked `status='closed'` in the last N seconds has zero
   residual size on the exchange; alert + optional auto-flatten. Tier 2
   (touches monitor orchestration) — needs operator ack pre-merge.
   Filed against the trade #1049 incident.
4. **Process-wide env-gate purge** — grep `MULTI_ACCOUNT_*`, `*_ENABLED`,
   `*_APPLY_TO_*`, `*_DRY_*`, `MONITOR_*`, `DISPATCH_*` and confirm only
   per-account `RiskManager.dry_run` survives. Tier 2 — needs operator
   ack. Filed against PR #630 (`MONITOR_APPLY_TO_EXCHANGE` survivor).
5. **Deploy restart contract universalisation** — replace the fixed unit
   list in `deploy_pull_restart.sh` with `systemctl list-units 'ict-*'`
   enumeration; add post-deploy version round-trip assertion. Tier 1.
   Filed against PR #635 (28h `ict-web-api` stale-code drift).
6. **Exchange-fills P&L attribution job** — daily job pulling Bybit fills
   and reconciling against the local DB so performance reads are immune
   to local schema/state bugs. Tier 1.
7. **Daily one-trade audit (auto-task category)** — pseudo-random pick
   from yesterday's closed trades, full lifecycle walkthrough committed
   under `docs/claude/audits/`. Tier 1.

If S-067 ships clean, the operator should pick the next one off this
list at session start.
