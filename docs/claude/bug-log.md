# Bug log

A running ledger of bugs found and fixed in this repo, plus the
architectural concern each one surfaces. Reviewed at the start of every
planning sprint to spot recurring trouble spots.

## How to append

Every time a bug is identified and fixed:

1. Add a row to the **Active bugs (chronological)** table below.
2. Fill every column. If you don't have a value, write `unknown`.
3. Tag with the architectural-concern category in column "concern" so
   `grep -i "<category>" docs/claude/bug-log.md` surfaces clusters.
4. Commit alongside (or immediately after) the fix PR. The bug log
   landing later is fine — what matters is it lands.

If a bug recurs, add a *new* row referencing the original (`see #BUG-NNN`).
Don't edit history.

### Architectural-concern categories

Pick the closest match. New categories are fine; just be consistent.

- `auth` — authentication, token handling, JWT, allowlist, session
- `config` — YAML / env var / settings precedence, defaults, drift
- `data` — OHLCV fetch, parsing, schema, DB, journals, caches
- `deploy` — systemd, sudoers, polkit, restart logic, install scripts
- `git` — gitignore, branch / merge / rebase, conflicts, force-push
- `markdown` — Telegram parse_mode, HTML escaping, template rendering
- `risk` — risk caps, position sizing, drawdown enforcement
- `telegram` — bot wiring, MagicMock stubs, command handlers, pings
- `tests` — fixtures, stubs, conftest, dependency leakage between tests
- `tls` — egress allowlist, cert chains, proxy, network reachability
- `ui` — web dashboard, HTMX fragments, static files, templates

## Active bugs (chronological, newest first)

| ID | Date | Sprint | Area | Symptom | Root cause | Fix (PR) | Concern | Notes |
|---|---|---|---|---|---|---|---|---|
| BUG-018 | 2026-04-30 | S-015 | Telegram ping | Operator not receiving sprint progress pings | TELEGRAM_BOT_TOKEN absent in sandbox env; `notify_session.py` fails on missing pandas; no VM-side trigger on checkpoint commits | open — flagged for housekeeping session | telegram | drives the pings spec at `docs/claude/telegram-pings.md` |
| BUG-017 | 2026-04-30 | S-015 | data_sources / coinmetrics | github-raw adapter returned 0 rows for 2025 window despite the CSV having 5,765 valid bars | Used `ReferenceRateUSD` column which only populated for the last 7 rows; correct column is `PriceUSD` | #207 | data | column-name assumption — verify schema before parsing |
| BUG-016 | 2026-04-30 | S-015 | CHECKPOINT_LOG conflicts | Final checkpoint PR (#205) hit a CHECKPOINT_LOG merge conflict because the mid-session checkpoint (#203) had landed at the same insertion point | Two append-only-at-top sections opened from different bases → both want line 11; rebase fails | #206 (re-issue with rebase done locally) | git | recurring — see also BUG-006; could be solved by an "append at bottom" log convention or a non-positional ID-keyed log |
| BUG-015 | 2026-04-30 | S-015 | data egress | Sandbox returns 403 for every keyless market-data API; egress allowlisted to pypi + github only | Egress proxy policy — out of repo's control | #207 (worked around with github-raw + coinmetrics) | tls | drives the testing-policy.md note we still need to add |
| BUG-014 | 2026-04-30 | S-015 | VWAP timeframe precedence | Setting `vwap.timeframe: "5m"` in `config/strategies.yaml` would silently no-op if any account's `.env` had `TIMEFRAME=15m` | `vwap_signal_builder` consulted env first, YAML last | #209 | config | recurring shape: env-vs-yaml precedence — see also BUG-009 |
| BUG-013 | 2026-04-30 | S-015 | yfinance noise | Smoke-test report had `Failed to get ticker 'BTC-USD'…HTTP 403…` lines leaking into the markdown | yfinance writes to stderr from inside the Python interpreter; the script captured stdout but not stderr | fixed in #208 by `2>/dev/null` on the runner invocation | data | better fix is logger redirection inside the adapter |
| BUG-012 | 2026-04-30 | S-014 | gitignore — fragments/ subdir | M3 PR #1 lost `web/templates/fragments/*.html` because the repo-wide `*.html` exclusion swallowed them | `!web/templates/*.html` whitelist is non-recursive | #195 (added `!web/templates/**/*.html`) | git | drives the "document the recursive whitelist pattern" item still pending in carry-over |
| BUG-011 | 2026-04-30 | S-014 | gitignore — top-level templates | M1 PR #1 lost `web/templates/*.html` for the same reason | repo-wide `*.html` exclusion (added for coverage reports) hit web templates | #192 (added `!web/templates/*.html`) | git | also see BUG-012 |
| BUG-010 | 2026-04-30 | S-014 | tests / telegram stub | Module-level `_VM_WRITE_BUTTONS = InlineKeyboardMarkup([[…]])` in PR #184 broke the `_tg.InlineKeyboardMarkup = MagicMock` stub used by ~10 existing test files | `MagicMock([[…]])` constructor crashes `_mock_set_magics` because lists are unhashable | one-off worked around in `tests/test_telegram_signals.py` with `lambda *a, **kw: MagicMock()`; ~10 other test files still broken | tests | drives the "centralise telegram stubs in conftest.py" item still pending in carry-over |
| BUG-009 | 2026-04-30 | S-014 | /signals Markdown parse | Telegram `/signals` returned no message even though `runtime_logs/signal_audit.jsonl` was growing | Pipeline statuses / reasons (`no_signal`, `failed_validation`) contain underscores; the formatter wrapped them in legacy-Markdown italic / bold delimiters → unbalanced italic → Telegram rejected with `Bad Request: Can't parse entities` → BadRequest swallowed in reply_text | #190 (switched to plain text + emojis) | markdown | recurring shape: legacy Markdown parse — avoid `parse_mode="Markdown"` on dynamic content |
| BUG-008 | 2026-04-30 | S-014.5 | git-sync over-restart | `ict-git-sync.timer` restarted both services every 5 min unconditionally, killing in-flight `/vm` runner cgroup children | `scripts/deploy_pull_restart.sh` had explicit "no-op restart is cheap" logic | #188 (conditional restart on HEAD advance + defer if claude-vm-runner@*.service is active) | deploy | timer-driven side-effects need idempotency |
| BUG-007 | 2026-04-30 | S-014.5 | systemd ProtectHome | `/vm` runner ran (exit 0) but Claude's Bash tool was disabled because `~/.claude/session-env` was unwritable | `ProtectHome=read-only` on `claude-vm-runner@.service`; needed selective `ReadWritePaths` for `~/.claude`, `~/.cache`, `~/.config/claude` | #187 | deploy | systemd hardening interacts with toolchain expectations |
| BUG-006 | 2026-04-30 | S-014.5 | systemd-run polkit hang | Bot's wrapper subprocess hung silently when invoking system-mode units as non-root | `systemd-run` non-root needs polkit auth, which has no tty in the bot's cgroup | #186 (privileged dispatcher + sudoers drop-in) | deploy | privilege-boundary needs an explicit wrapper, not a `sudo` chain |
| BUG-005 | 2026-04-30 | S-014.5 | apscheduler / tzlocal | Bot crash-looped 121 times before VM session restarted it cleanly | `apscheduler 3.6.3` ↔ `tzlocal 5.x` timezone format mismatch — fixed by `apscheduler>=3.10.4` | not yet pinned in requirements.txt — flagged in S-014.5 cleanup | deploy | dependency drift between sandbox CI and prod VM |
| BUG-004 | 2026-04-30 | S-014.5 | empty Anthropic API credit | Bot couldn't dispatch `/vm` because pay-as-you-go API key had $0 balance | external billing | switched to long-lived OAuth subscription token via `claude setup-token`; `CLAUDE_CODE_OAUTH_TOKEN` in `/etc/ict-trader/claude.env` | auth | also: leaked OAuth token from chat needs revoke (still pending) |
| BUG-003 | 2026-04-30 | S-014 | bybit /balance silently empty | `/balance` for accounts.yaml entries said "balance unavailable" | `account_balance` and `account_open_positions` inlined `_bybit_client(env)` (legacy env_path-only) instead of routing through the api-key-env-aware `bybit_client_for(account)` | S-012 hotfix #3 (predates this log) | config | recurring shape: legacy code path bypasses the post-refactor API |
| BUG-002 | 2026-04-30 | S-014 | strategy/account wiring | Both Bybit accounts had `strategies: [turtle_soup, vwap]`, but bybit_2 wallet is USDT-only and turtle_soup needs BTC base | config drift between intent and YAML | #198 (turtle_soup → bybit_1 only; vwap → bybit_2 only; prop disabled) | config | drives the no-PM-review-needed-for-config rule clarification |
| BUG-001 | 2026-04-30 | S-014 | PR rebase loop | PR #183 (M0 backend) was carried over from previous session and had a CHECKPOINT_LOG conflict with main | append-only-at-top doc + cross-session rebase | resolved at start of S-014 by taking main's superset | git | see BUG-016 — same shape, recurring |

## Standing patterns flagged for architecture planning

If a category accumulates ≥ 3 entries, surface it as a discussion topic in
the next planning sprint. Current candidates:

- **`config` (3+ entries — BUG-002, BUG-003, BUG-014)** — config / env / YAML
  precedence is the most common bug source. Worth designing a single
  "settings resolver" module that documents the precedence and is unit-
  tested independently of every consumer.
- **`git` (3+ entries — BUG-001, BUG-011, BUG-012, BUG-016)** — append-only
  CHECKPOINT_LOG conflicts and gitignore patterns recur. Worth considering
  a non-positional log format (one file per checkpoint, indexed) and a
  documented gitignore-whitelist convention.
- **`deploy` (3+ entries — BUG-005, BUG-006, BUG-007, BUG-008)** — systemd /
  polkit / dependency drift between sandbox and VM is a recurring failure
  mode. Worth treating the VM bootstrap as a tested contract, not a
  sequence of hand-fixed hotfixes.

## Cross-references

- `docs/claude/sprint-planning.md` — sprint planning template that
  references this log.
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` — sprint-level history.
