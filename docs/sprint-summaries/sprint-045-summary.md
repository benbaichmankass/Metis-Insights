# Sprint S-045 Summary — M4 step 2: conftest cleanup, pytest-collect → blocking, ruff rule expansion

**Sprint:** S-045 | **Milestone:** M4 — Repo hygiene + CI
**Type:** auto-claude (roadmap) | **Date:** 2026-05-07
**Status:** CLOSED ✅ (M4 step 2 done — Janitor audits remain → S-046)

---

## Outcome

S-044 shipped the GitHub Actions CI suite but had to ride two
compromises: `pytest-collect` was advisory because 52 pre-existing
collection errors blocked promotion, and `ruff-lint` ran with
`--select E9,F63,F7` because the broader default flagged 286 hits
on `main`. S-045 cleared both compromises:

- **`pytest-collect` is now blocking.** A two-bug fix in
  `tests/conftest.py` + `tests/test_bot_web_sweep.py` (BUG-062 in
  the bug log) took the suite from 52 collection errors to 0. The
  workflow no longer carries `--continue-on-collection-errors` or
  the `|| true` shim.

- **`ruff-lint` runs the default rule set.** Eight per-rule
  passes (T3a..T3h) plus a small E731+E701 cleanup brought every
  non-operator-hold path to zero hits. The 15 residual hits in
  operator-hold paths (`src/runtime/pipeline.py`,
  `src/units/accounts/*`) are explicitly suppressed via
  `[lint.per-file-ignores]` in `ruff.toml` with a backlog comment;
  fixes for those lines wait on a separate operator-approved
  ping-PR.

- **Branch protection wiring is one click.** A new
  `notebooks/operator/update_branch_protection.ipynb` PUTs the
  required-status-checks contexts via the GitHub API:
  `pytest-collect`, `secret-scan`, `ruff-lint`, `dry-run-guard`
  (with `repo-inventory` deliberately not in the list).

M4 advances from "CI suite shipped" to "CI suite + conftest + ruff
cleanup done; Janitor audits remaining". S-046 is queued to close
M4 with the dead-file / duplicate-module / missing-test audits.

---

## What was done

### T0 — Kickoff
- `docs/sprints/sprint-045-prompt.md` filed (T0..T5 plan,
  unit-boundary declaration, hard guardrails, success criteria).
- `CP-2026-05-07-04-s045-kickoff` prepended to CHECKPOINT_LOG.
- T0 PR #438 opened as draft.

### T1 — `tests/conftest.py` fix (option B + secondary fix)
- Extended the conftest stub to expose `telegram.error.TelegramError`
  (real `Exception` subclass so `except TelegramError` works) and
  `telegram.constants.ChatAction`. Added `MessageHandler` and
  `filters` to the `telegram.ext` stub. Cross-linked
  `telegram.{error,constants}` onto the parent namespace.
- Discovered a second bug after the conftest fix unmasked it:
  `tests/test_bot_web_sweep.py` guarded its fastapi MagicMock stub
  with `if "fastapi" not in sys.modules:` — the wrong shape for
  "stub only if the real package is missing". Replaced with
  `try: import fastapi; import fastapi.testclient; except ImportError: stub`.
  Same change applied to the `src.web.api.auth` stub.
- Added `email-validator>=2.0.0` to `requirements-test.txt`
  (pydantic[email] needs it as a separate package; `EmailStr` in
  the auth router was the last collection blocker after the stub
  fixes).
- Refreshed the "Known collection errors" comment block in
  `requirements-test.txt` — the telegram + fastapi failure modes
  are now resolved; only the jwt/cryptography pyo3 sandbox issue
  remains documented.
- `BUG-062` row appended to `docs/claude/bug-log.md`.
- Verification: `PYTHONPATH=. pytest --collect-only -q tests/
  --ignore=tests/test_main_loop.py` → `2502 tests collected in 1.60s`
  (was `1767 collected, 45 errors` pre-T1).

### T2 — `pytest-collect` → blocking
- Dropped `--continue-on-collection-errors` and the `|| true` shim
  from `.github/workflows/pytest-collect.yml`. The workflow now
  fails on the first collection error.
- Header comment updated to reflect the BUG-062 fix + blocking gate.
- `docs/claude/ci-status-checks.md` updated: workflows-at-a-glance
  table flips `pytest-collect` to **blocking**; per-workflow
  section rewrites the "Why advisory today" block as a "History"
  + BUG-062 cross-reference; required-status-checks list adds
  `pytest-collect`.

### T3 — Ruff rule expansion (one rule per commit)
- **T3a F541** (f-strings without placeholders, 23→2 hits): autofix
  applied to 9 files / 21 hits. Notebooks excluded in a new repo-
  level `ruff.toml` (`extend-exclude = ["*.ipynb"]`) — ruff's
  notebook re-serialization rewrites the JSON in non-behaviour-
  preserving ways (escaped Unicode → literal, `"id"` field
  injection). 2 residual hits in `src/units/accounts/execute.py`
  (operator-hold) suppressed at T3i.
- **T3b E401** (multiple imports on one line, 1 hit after .ipynb
  exclusion): `import os, sys, requests` in
  `scripts/startup_env_check.py` split onto three lines.
- **T3c F811** (redefined-while-unused, 6 hits): function-local
  re-imports of names already imported at module top, removed.
  Fixed in `src/backtest/backtester.py`, `src/units/db/database.py`
  (3×), `tests/test_s007_bot_commands.py`,
  `tests/test_s007_pipeline_rewire.py`.
- **T3d F841** (unused local variables, 11 hits): each reviewed
  before applying. `--unsafe-fixes` autofix landed for cases with
  no rhs side effect; cases where ruff left a bare side-effect-free
  expression (`datetime.now(...).isoformat()`,
  `argparse.ArgumentParser()`) were edited manually to delete the
  whole line. `accounts = self._stub_accounts(monkeypatch)` →
  `self._stub_accounts(monkeypatch)` to preserve the monkeypatch
  side effect.
- **T3e F401** (unused imports, 161 hits) — split into two commits
  for review hygiene:
  - **tests/**: 121 hits across 65 files autofixed.
  - **src + scripts + utils + visualize_*.py**: 35 hits autofixed +
    1 manual fix in `src/units/strategies/__init__.py` (ruff treats
    `__init__.py` F401 as a re-export by default and skipped a
    stale `Optional` import).
  - 4 residual hits in `src/units/accounts/*` (operator-hold)
    suppressed at T3i.
- **T3f E402** (module-level import not at top, 42 hits): correct
  Python pattern is `# noqa: E402` for legitimate deferred imports
  (sys.modules stubs before pipeline import, etc.). 33 hits across
  21 files annotated with the noqa comment via a small helper
  script. 9 residual hits in `src/runtime/pipeline.py` (operator-
  hold) suppressed at T3i.
- **T3g E741** (ambiguous variable name `l`, 13 hits): manually
  renamed `l → ln` (line) in comprehensions, `l → lo` (low) in
  the bar-builder helper. 6 files touched, all in tests + scripts
  + visualize_swings.py.
- **T3h F821** (undefined name, 4 hits):
  `scripts/sprint015/data_sources.py` was missing `Dict` from
  `from typing import …`; added. `src/core/coordinator.py` had a
  forward ref to `"TradingAccount"` with no import; added a
  `TYPE_CHECKING` import block (avoids a `accounts → coordinator`
  circular dep at runtime).
- **T3 cleanup E731 + E701** (2 hits not in the prompt's audit
  but discovered during the sweep): `lambda` assignment in
  `src/units/ui/processor.py` rewritten as a `def`; one-line `if
  __name__ == "__main__": fn()` in `utils/hf_push.py` split.
- **T3i drop --select**: `.github/workflows/ruff-lint.yml` now
  runs `ruff check .` (default rule set). 15 residual hits in
  operator-hold paths suppressed via `[lint.per-file-ignores]` in
  `ruff.toml` with a backlog comment naming the ping-PR. Runbook
  updated.

### T4 — Branch protection wiring (Colab notebook)
- New `notebooks/operator/update_branch_protection.ipynb`. Cell
  sequence: load `GH_ADMIN_TOKEN` from Colab Secrets → GET current
  protection → PUT new spec preserving review/restrictions/admin
  fields → verify by reading back. Idempotent.
- Required contexts pinned by the notebook: `pytest-collect`,
  `secret-scan`, `ruff-lint`, `dry-run-guard`. `repo-inventory`
  deliberately not in the list (still advisory).
- Per CLAUDE.md "Always do" rule — operator manual step delivered
  as a Colab notebook, not a CLI checklist.

### T5 — Sprint close
- This summary doc.
- `docs/claude/milestone-state.md` refreshed.
- `CP-2026-05-07-NN-s045-complete` prepended to CHECKPOINT_LOG.

---

## Files changed

### Test / collection
- `tests/conftest.py` — telegram stub extended (T1).
- `tests/test_bot_web_sweep.py` — fastapi guard fix + 1 F401 (T1 + T3e).
- `requirements-test.txt` — `email-validator` added; comment refresh (T1).

### CI workflows + config
- `.github/workflows/pytest-collect.yml` — drop `|| true` shim, blocking (T2).
- `.github/workflows/ruff-lint.yml` — drop `--select`, default rule set (T3i).
- `ruff.toml` — new file (`extend-exclude = ["*.ipynb"]` + per-file-ignores) (T3a + T3i).

### Source (non-operator-hold ruff fixes)
- `src/backtest/backtester.py`, `src/units/db/database.py`,
  `src/runtime/health.py`, `src/runtime/order_monitor.py`,
  `src/bot/telegram_query_bot.py`, `src/units/ui/processor.py`,
  `src/ict_detection/swing_points.py`,
  `src/runtime/{hourly_report,signal_notifications,validation}.py`,
  `src/units/db/data_loader.py`,
  `src/units/ui/{data_loaders,telegram_format}.py`,
  `src/units/strategies/__init__.py`, `src/exchange/{binance,bybit}_connector.py`,
  `src/ict_detection/{fvg_detector,key_levels,liquidity,order_blocks}.py`,
  `src/news/news_pipeline.py`, `src/backtest/run_backtest.py`,
  `src/bot/`-suffixed files, `src/core/coordinator.py` — F541 / F811 /
  F841 / F401 / E402 / E741 / E731 cleanups.

### Tests (non-operator-hold ruff fixes)
- 65 test files touched by F401 autofix; ~21 by E402 noqa pass; 6
  by E741 rename; 5 by F841 fix.

### Scripts / utils / top-level
- `scripts/{startup_env_check,notify_on_pull,s006_ict_synthetic_validate,
  sprint015/run_smoke_test,sprint015/data_sources}.py`,
  `utils/hf_push.py`, `bin/analyze_ict_results.py`, `download_data.py`,
  `test_order_safe.py`, `visualize_all.py`, `visualize_swings.py` —
  ruff fixes.

### Docs
- `docs/sprints/sprint-045-prompt.md` (new — T0).
- `docs/claude/ci-status-checks.md` — pytest-collect + ruff-lint
  sections rewritten + branch-protection list refresh (T2 + T3i).
- `docs/claude/bug-log.md` — BUG-062 row (T1).
- `docs/sprint-summaries/sprint-045-summary.md` (this file — T5).
- `docs/claude/milestone-state.md` — M4 row refresh (T5).
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` — T0 + T5 entries.

### Notebooks
- `notebooks/operator/update_branch_protection.ipynb` (new — T4).

No `src/runtime/orders.py`, `src/runtime/pipeline.py`,
`src/runtime/trading_mode.py`, `src/units/accounts/*`,
`src/main.py`, `config/accounts.yaml`, or `deploy/` edits.

---

## PR list

| PR | Subject |
|---|---|
| #438 | S-045 (full sprint, multi-commit) — conftest + ruff cleanup + branch-protection notebook |

S-045 was executed as a single multi-commit PR per the historical
S-042 / S-043 / S-044 pattern.

---

## Checkpoint IDs

- `CP-2026-05-07-04-s045-kickoff` — sprint open + prompt filed.
- `CP-2026-05-07-05-s045-complete` — sprint close + milestone-state refresh.

---

## Tests run

- `python scripts/secret_scan.py` — clean.
- `ruff check .` (no `--select`) — `All checks passed!` (residuals
  in operator-hold paths suppressed via `ruff.toml`).
- `PYTHONPATH=. pytest --collect-only -q tests/ --ignore=tests/test_main_loop.py`
  — `2502 tests collected, 0 errors` (was `1767 collected, 45 errors`
  on `main`).

---

## Live-mode check

✅ No live-trading code touched in any commit. Diff vs `main` is
entirely `tests/`, `src/` (non-operator-hold), `scripts/`, `utils/`,
top-level entry-point .py files, `notebooks/operator/`,
`requirements-test.txt`, `ruff.toml` (new), `.github/workflows/`,
and `docs/`. `scripts/check_dry_run_in_diff.py` clean.

---

## Deferred items / follow-ups

1. **Operator-hold lint residuals → ping-PR.** 15 mechanical lint
   hits in operator-hold paths are suppressed via
   `[lint.per-file-ignores]` in `ruff.toml`:
   - `src/runtime/pipeline.py`: 9 × E402 (deferred imports below
     the matplotlib stub block — same pattern that already carries
     `# noqa: E402` in many test files).
   - `src/units/accounts/dxtrade_client.py:40`: 1 × F401.
   - `src/units/accounts/integrator.py:15+:16`: 2 × F401 (`os`,
     `typing.Any`).
   - `src/units/accounts/prop_risk.py:29`: 1 × F401
     (`datetime.time`).
   - `src/units/accounts/execute.py:269+:271`: 2 × F541.

   A follow-up ping-PR (per CLAUDE.md § "Telegram Reporting") will
   propose the mechanical fixes for operator review. When the
   operator approves, the corresponding `ruff.toml` entries get
   removed in the same PR.

2. **`repo-inventory` promotion to blocking.** Stays advisory until
   ≥ 5 PRs have observed the artifact and the operator confirms
   the drift signal is useful. Same plan as S-044's deferred item.

3. **Janitor audits → S-046.** Dead-file audit (using the
   `repo-inventory.yml` artifact across PRs), duplicate-module audit
   (`src/ui/` vs `src/units/ui/`, post-S-035 back-compat shims may
   now be removable), missing-test audit (modules under
   `src/units/` without a corresponding `tests/test_<unit>_*.py`).
   The S-046 prompt should also check `tests/test_main_loop.py`
   (currently ignored by `pytest-collect`) — that ignore is the
   one remaining excludes path.

4. **Full pytest run in CI.** Today's workflow is collect-only —
   full execution needs the live data layer + market connectors
   stabilised end-to-end. Separate sprint after the test suite is
   sandbox-safe.

5. **`tests/test_backtester.py:test_run_capital_updated` missing
   assertion.** T3d removed the unused `initial = bt.capital` line
   because the test never compared `bt.capital` to `initial`. The
   test asserts `isinstance(bt.capital, float)` and
   `bt.capital > 0` only — the comment "Capital should have changed
   if any trades executed" suggests an intended `assert bt.capital
   != initial` that was never written. Fixing the test logic is
   out of scope for a janitor sprint; tracked here as a follow-up.

---

## Lessons learned

1. **`if "X" not in sys.modules:` is the wrong guard.** It tests
   "has anything in this run touched the name", not "is the real
   package available". The first test file to run wins, poisoning
   `sys.modules` for everything else. The correct pattern is
   `try: import X; except ImportError: stub`. BUG-062 surfaced
   this in two places (`tests/conftest.py` for `telegram` and
   `tests/test_bot_web_sweep.py` for `fastapi`); a CI helper that
   greps test files for the buggy guard would catch the next
   recurrence early.

2. **Per-rule per-commit pacing kept the diff reviewable.** With
   161 F401 hits across 91 files, a single mass-format would have
   been impossible to review and impossible to revert cleanly.
   Splitting tests/ from src/ inside F401 (T3e) was a small
   discipline cost that paid off — each commit fits a 30-second
   review window.

3. **Ruff's `*.ipynb` autofix is not behaviour-preserving.** First
   F541 pass rewrote `notebooks/operator/rotate_api_keys.ipynb`
   with escaped-Unicode → literal-Unicode and auto-injected `"id"`
   fields on markdown cells. Reverted, then added the global
   exclude in `ruff.toml`. Notebook hygiene is its own follow-up,
   handled with notebook-aware tooling — not ruff.

4. **Operator-hold paths need a structured suppression mechanism,
   not silent skips.** `[lint.per-file-ignores]` with a backlog
   comment is the right shape: the suppression is explicit, every
   suppressed rule is named, the backlog comment names the ping-PR
   that will remove the entry, and CI gates against any *new* hits
   in the operator-hold paths (only the named rules are silenced,
   not the whole file).

5. **Verify-before-trusting-done caught a second bug.** The
   prompt-listed F541 / E401 / F811 / etc. counts came from the
   S-044 audit. After T3a's `*.ipynb` exclusion landed, the actual
   counts on disk dropped (e.g. E401 went from 9 → 1 because 8
   were inside notebooks). Trusting the prompt's audit blindly
   would have led to commits that fixed nothing. Same principle
   the workplan applies to "done" labels — re-verify against the
   on-disk state.
