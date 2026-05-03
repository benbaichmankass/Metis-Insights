# Next-session prompt — overnight Sonnet, low-risk pickup

Use this as the prompt when starting the next Claude Code session on
the ICT trading bot repo. Copy-paste the block below verbatim.

---

You are picking up an autonomous overnight session on
`the-lizardking/ict-trading-bot`. The previous session closed at
**CP-2026-05-03-22** — read it first via
`docs/claude/checkpoints/CHECKPOINT_LOG.md` (most recent entry on
top) so you know what landed and what's deferred.

## Hard constraints

- **Sonnet-only run.** Pace yourself. This is an overnight session
  but you must NOT time out. Take short tasks, finish each
  one cleanly, commit + push + open PR + self-merge (Tier 1) or
  leave draft (Tier 2), then move to the next.
- **Tier 1 only.** Anything Tier 2 (touches `src/runtime/orders.py`,
  `src/runtime/pipeline.py` dispatch logic, `src/runtime/trading_mode.py`,
  `src/units/accounts/execute.py`, or controls live/dry routing) is
  **out of scope for this run** — leave it for an Opus session and
  document the hand-off.
- **No new sprints.** Pick from the queue below. If something looks
  ambiguous, file a brief question via the BLOCKED-PM commit + draft
  PR pattern (`docs/claude/telegram-pings.md` § "Ping-PR vs work-PR")
  and skip to the next item.
- **One PR at a time.** Don't stack drafts. Self-merge the small
  Tier-1 PRs as soon as their `scan` job passes.

## Pickup queue (priority order)

Pick from the top; finish each before moving down. Each item is
sized for 30–90 min including tests + commit + PR + merge.

### 1. BUG-042 PR 3/3 — runbook + flag flip + bug-log entry

**Tier 1, docs-only.** The reconciler (`_reconcile_open_trades` in
`src/runtime/order_monitor.py`) shipped in PR #385 gated by
`MONITOR_RECONCILE_ENABLED=false`. PR 3 of the sprint:

1. Add `docs/runbooks/monitor-reconciler.md` documenting:
   - What the reconciler does (compare DB-open vs exchange-open per
     account; mark mismatches `status='orphaned'`; cascade
     `order_packages` to `closed`; ping operator).
   - What it doesn't do (no exchange writes, no auto-close — only
     status updates).
   - Skip rules (dry-run accounts, missing creds, accounts not in
     `accounts.yaml`).
   - The `MONITOR_RECONCILE_ENABLED` flag (current default
     `false` → flip to `true`).
   - How to interpret an orphan ping ("the DB and exchange
     disagreed about position X; we marked it orphaned, no action
     required — but if this fires repeatedly for the same symbol,
     investigate `execute_pkg` or the exchange's open-position
     read path").
   - Manual override SQL: `UPDATE trades SET status='open' WHERE
     status='orphaned' AND id=?` to flip back if the reconciler
     made the wrong call (rare; only with race conditions).
2. Flip `MONITOR_RECONCILE_ENABLED` from `false` to `true` in any
   `.env.master` or `.env.live` template the repo carries (search:
   `grep -rn "MONITOR_RECONCILE_ENABLED" --include="*.env*"`).
   Skip if no template exists in-repo (the operator may set it
   directly on the VM).
3. Append a BUG-042 row to `docs/claude/bug-log.md` (newest at the
   top of the table; the existing rows for BUG-041 / BUG-044 / BUG-045
   / BUG-046 give you the format and column count). Cross-reference:
   PR #357 (canonical `_log_trade_to_journal`), PR #367 (one-shot
   cleanup notebook), PR #384 (PR 1 — accounts unit lift), PR #385
   (PR 2 — reconciler), this PR (PR 3 — runbook + flag flip),
   CP-2026-05-03-22 (sprint kickoff + PRs 1+2 merged).

PR title: `docs(runbook): BUG-042 monitor-loop reconciler runbook + flag flip [PR 3/3]`.
Self-merge after CI green. Then update CLAUDE.md if the reconciler
changes any operator-facing contract (it shouldn't, but check).

### 2. paths.py helper — replace ad-hoc REPO_ROOT calcs

**Tier 1, autonomous refactor.** ~10 modules each compute REPO_ROOT
via `os.path.abspath(os.path.join(_BASE_DIR, "..", "..", ...))` with
the `..` count varying by module depth. BUG-037 was caused by exactly
this drift (S-032 module move bumped depth, the REPO_ROOT calc didn't
update).

1. Create `src/utils/paths.py::repo_root()` that walks up from the
   caller's `__file__` until it finds a marker (`.git/`,
   `pyproject.toml`, or `requirements.txt` — first match wins).
   Cache the result.
2. Migrate the call sites one at a time. Run
   `grep -rn "REPO_ROOT\s*=\s*os\.path\.abspath" src/` to enumerate
   them (~10 sites: `src/strategy_registry.py`, `src/backtest/run_backtest.py`,
   `src/web/config_ui.py`, `src/units/__init__.py`, `src/web/backtest_ui.py`,
   `src/units/ui/data_loaders.py`, `src/units/strategies/__init__.py`,
   `src/units/accounts/clients.py`, `src/units/accounts/__init__.py`,
   `src/bot/telegram_query_bot.py`, `src/core/coordinator.py`).
3. Test: `tests/test_repo_root_helper.py` — at least 3 contracts
   (returns the same path from any depth, finds via `.git`,
   pre-existing tests for affected modules don't regress).
4. Open ONE PR for the helper + a few migrations; if the diff gets
   over ~200 lines, split into a helper-only PR + a migration PR.

PR title: `refactor(utils): repo_root() helper to replace ad-hoc REPO_ROOT calcs`.
Self-merge if Tier 1.

### 3. Renderer cosmetic — `Accounts dispatched — 3` shows `?: ?`

**Tier 1, cosmetic.** The per-tick "Pipeline result" Telegram message
renders accounts dispatched as:

```
• Accounts dispatched — 3
  ?: ?
  ?: ?
  ?: ?
```

instead of `bybit_1: skipped_not_assigned`, `bybit_2: ok`,
`prop_velotrade_1: below_min_balance` etc. The dispatcher's
`multi_account_execute` returns `[{name, exchange, account_type,
trade_id, sized_qty, error}, ...]`; the renderer in
`src/units/ui/telegram_format.py` (or wherever the pipeline-result
Telegram body is built) isn't extracting `name` + `error` correctly.

Steps:

1. `grep -rn "Accounts dispatched" src/` to find the renderer.
2. Inspect what it expects vs what the result dicts carry.
3. Fix the field extraction so each row shows `<name>: <error or 'ok'>`.
4. Add a test in `tests/test_telegram_format.py` (or wherever the
   adjacent renderer tests live) that pins the expected output for a
   3-account dispatch with mixed outcomes.

PR title: `fix(telegram): render Accounts dispatched rows with name + outcome`.
Self-merge after CI.

### 4. Strategy-monocle PR 2/3 — partial-close verdict shape (DB-side only)

**Tier 1, DB-only.** Sprint plan summary in
`docs/sprint-plans/bug-042-monitor-loop-reconciler.md`'s sibling
(no formal plan filed yet — the operator approved the shape inline:
"a - one trade per strategy, no matter how many accounts following
it" and "b - lets configure partial close as well, it's worth the
effort").

PR 2's scope is **DB-side only** (no exchange call). Extend the
monitor-verdict shape:

1. `_apply_update` in `src/runtime/order_monitor.py` already handles
   `{"action": "close", "reason": str}`. Extend to:
   `{"action": "close", "close_qty_pct": float (default 1.0), "reason": str, "exit_price": float?}`.
2. Behaviour:
   - `close_qty_pct == 1.0` (or unset): existing full-close path.
     `order_packages.status='closed'`, `trades.status='closed'`,
     `exit_reason='reconciler'` / `<verdict.reason>`.
   - `close_qty_pct < 1.0`: partial close. The
     `order_packages` row stays `status='open'`. The `trades` row's
     `position_size` is reduced by `close_qty_pct * original_qty`.
     Append a fragment marker to the `notes` JSON:
     `{"partial_closes": [{"qty": fraction, "reason": ..., "ts": ...}, ...]}`.
3. **Do NOT touch the exchange-side close.** That's PR 3 of this
   sprint, Tier 2, parked for an Opus session.
4. Tests in `tests/test_strategy_monocle_partial_close_verdict.py`
   covering: full close (existing path); 50% partial close;
   sequential partials adding to 100% (last one closes the package);
   invalid pct (>1.0 / negative) is rejected; the open-gate from PR 1
   continues to refuse new packages while a partial-closed trade is
   still open.

PR title: `feat(monitor): partial-close verdict shape + DB cascade [strategy-monocle PR 2/3]`.
Self-merge if Tier 1 (it should be — no exchange call, no
live/dry routing).

### 5. Test environment cleanup — categorise the 282 pre-existing failures

**Tier 1, no production code changes.** The full-suite `pytest` run
on `main` reports `1746 passed / 282 failed`. The 282 are all
pre-existing env-level issues (missing `pandas` / `pyo3-asyncio` /
`pytest-asyncio` for unrelated suites). Verify and document.

1. `pip install pandas pyyaml pyo3-asyncio pytest-asyncio` in the
   sandbox; re-run the full suite. Report the new count.
2. Whatever still fails after deps install: investigate. If it's a
   real bug, file a draft `BLOCKED-PM` PR + ping-PR (don't fix
   blindly). If it's another missing dep, add to a
   `requirements-test.txt` if not already there.
3. Open ONE PR with the dep additions to `requirements-test.txt`
   only (no production code changes). Self-merge.

PR title: `chore(tests): pin missing test deps so the suite collects cleanly`.

### 6. Documentation drift sweep — cross-check `docs/claude/*.md` against actual code

**Tier 1, docs-only.** Read each `docs/claude/*.md` and look for
stale references — e.g. function paths that have moved, env vars
that have been removed (`ALLOW_LIVE_TRADING`, `MODE`, `DRY_RUN`),
deprecated PR numbers, etc. Don't rewrite anything wholesale; just
flag the drift.

1. Open `docs/claude/INDEX.md` first to see the full catalogue.
2. For each file, do a 2-minute scan: are the file paths it
   references still valid? Are the env vars it references still
   in use?
3. Open ONE PR with mechanical fixes (broken-path corrections,
   removed env-var mentions). Anything that needs interpretation
   goes into a follow-up sprint plan, not this PR.
4. Skip `CHECKPOINT_LOG.md`, `bug-log.md`, and any sprint-plans/
   files (those are append-only history; drift is expected).

PR title: `docs(claude): mechanical doc-drift fixes (paths + removed env vars)`.

## Stop conditions

- If you've spent more than 1 hour on a single task without a
  shippable PR, stop, commit a `[BLOCKED-PM]` summary, and skip to
  the next item.
- If pre-existing tests fail in a way that suggests a real bug
  (not env-deps), DO NOT fix it autonomously — file a `BLOCKED-PM`
  draft PR with the failure trace and a one-paragraph diagnosis,
  then move to the next item.
- If you reach the end of the queue, append a checkpoint to
  `docs/claude/checkpoints/CHECKPOINT_LOG.md` summarising what
  shipped and stop.

## What's already deployed (don't redo)

- BUG-042 PRs 1+2 (foundation lift + reconciler) — merged today.
- BUG-044 (early-out refusal-row contract) — merged earlier today.
- BUG-045 (silent dry-run on `multi_account_execute`) — merged today.
- BUG-046 (strategy-monocle open-gate) — PR 1/3 merged today.
- Checkpoint-ping dedup — merged earlier today.

## What you must NOT touch this run

- `src/units/accounts/execute.py` — Tier 2 (live order routing).
  PR 3 of the strategy-monocle sprint will need this; leave it for
  Opus.
- `src/runtime/orders.py` — Tier 2 (live order placement).
- `src/core/coordinator.py` — Tier 2 (dry/live routing). Can read
  for context but no edits.
- Any `accounts.yaml` change.
- Any new env var that controls dry/live behaviour.
