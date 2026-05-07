# CI status checks

Quick reference for every GitHub Actions workflow on this repo.
Read this first when a PR check goes red — most failures map to a
single command you can re-run locally.

> **Authority:** `docs/claude/workplan.md` is the decider for what
> the project ships. This runbook documents how the CI suite shipped
> in **S-044** gates work that lands on `main`.
>
> **Scope:** workflows under `.github/workflows/` only. Manual
> verification commands (`scripts/secret_scan.py`,
> `scripts/repo_inventory.py`, etc.) keep working unchanged for
> local dev — see `CLAUDE.md` § "Default verification".

---

## Workflows at a glance

| Workflow | File | Trigger | Gate | Local equivalent |
|---|---|---|---|---|
| `pytest-collect` | `.github/workflows/pytest-collect.yml` | `pull_request` to `main`, `push` to `main` | **blocking** (since S-045) | `PYTHONPATH=. pytest --collect-only -q tests/ --ignore=tests/test_main_loop.py` |
| `secret-scan` | `.github/workflows/secret-scan.yml` | `pull_request` to `main`, `push` to `main` | **blocking** | `python scripts/secret_scan.py` |
| `ruff-lint` | `.github/workflows/ruff-lint.yml` | `pull_request` to `main`, `push` to `main` | **blocking** | `ruff check . --select E9,F63,F7` |
| `repo-inventory` | `.github/workflows/repo-inventory.yml` | `pull_request` to `main`, `push` to `main` | advisory | `python scripts/repo_inventory.py` |
| `dry-run-guard` | `.github/workflows/dry-run-guard.yml` | `pull_request` to `main` | **blocking** | `python scripts/check_dry_run_in_diff.py /tmp/pr.diff` |
| `hf-cron` | `.github/workflows/hf-cron.yml` | `schedule` (HF dataset publish) | n/a | not PR-gating |
| `training-run` | `.github/workflows/training-run.yml` | `workflow_dispatch` | n/a | not PR-gating |

**Required status checks on `main`** (post-S-045): `pytest-collect`,
`secret-scan`, `ruff-lint`, `dry-run-guard`. `repo-inventory` stays
advisory until ≥ 5 PRs have observed the artifact and the operator
confirms the signal is useful. Branch protection wiring is the T4
deliverable of S-045 — see § "Branch protection wiring" below.

---

## Per-workflow details

### `pytest-collect` (blocking — since S-045)

- **What it does.** Installs `requirements.txt` + `requirements-test.txt`
  on Python 3.11, then runs `pytest --collect-only -q tests/
  --ignore=tests/test_main_loop.py`. Collection-only — tests do **not**
  execute. Surfaces: import errors, fixture name collisions, broken
  `conftest.py` setup, mis-spelled `pytest.mark.*`, missing test deps.
- **Why collect-only.** Full pytest needs the live data layer +
  market connectors and is not yet sandbox-safe. Promotion to a full
  test run is a separate sprint after the test suite is stabilised
  end-to-end.
- **Why ignore `tests/test_main_loop.py`.** That module imports
  `src.main`, which imports the live trading entrypoint. CLAUDE.md's
  "Default verification" section excludes it for the same reason.
- **History.** S-044 shipped this workflow advisory because the test
  suite carried 52 collection errors at the time (45 telegram-stub +
  7 fastapi-stub failures, see BUG-062). S-045 T1 fixed both stubs;
  S-045 T2 dropped the `|| true` shim and `--continue-on-collection-errors`
  flag. From PR #438 onward the workflow fails any PR that introduces
  a collection regression.
- **Debug.** Reproduce with the local equivalent above. If the failure
  is a missing dep, add it to `requirements-test.txt` (not the runtime
  `requirements.txt`). If it's a `sys.modules` test-isolation failure
  ("X is not a package"), follow the BUG-062 pattern: convert any
  `if "X" not in sys.modules:` guard into `try: import X; except
  ImportError: stub`. Do **not** add `--continue-on-collection-errors`
  back to the workflow — fix the import.

### `secret-scan`

- **What it does.** Runs `python scripts/secret_scan.py` against every
  tracked file. Patterns target Telegram bot tokens
  (`\d{8,12}:[A-Za-z0-9_-]{30,}`) and literal API key/secret
  assignments. ALLOW_WORDS in the script silence example/placeholder
  hits.
- **Debug.** Reproduce locally — the script prints `<path>:<line> —
  <kind>` for every hit. The fix is one of:
  1. **Real leak.** Rotate the credential immediately, remove the
     value from the file, force-push (operator-approved) or reset.
     Telegram tokens, exchange API secrets — treat as a security
     incident.
  2. **Test fixture / docs example.** Add `# example` or `not_displayed`
     to the line, or use a placeholder like `your_token_here`. ALLOW_WORDS
     handles common forms.
  3. **Script false-positive.** Update `ALLOW_WORDS` or the regex in
     `scripts/secret_scan.py`. Do **not** suppress in the workflow.

### `ruff-lint`

- **What it does.** Installs `requirements-dev.txt` (currently
  `ruff>=0.15.0` only), then runs `ruff check . --select E9,F63,F7`.
  The narrow rule set covers runtime errors (E9), assertion / comparison
  bugs (F63), and semantic errors (F7).
- **Why narrow.** Current `main` carries 286 ruff hits across rules
  the narrow set excludes (E402 imports-not-at-top, F401 unused-imports,
  F541 unnecessary f-strings, F811 redefinitions, F821 undefined names,
  F841 unused vars). The S-044 prompt explicitly forbids mass-formatting
  in this sprint. A follow-up Janitor sprint expands the rule set after
  cleaning each category in isolation.
- **Debug.** Reproduce with the local equivalent above. If your PR
  introduced an E9/F63/F7 hit, fix it. If you're trying to fix a
  pre-existing hit outside the narrow set, that's its own PR — open it
  separately and reference S-045 (or whichever Janitor sprint is open)
  in the description.

### `repo-inventory` (advisory)

- **What it does.** Runs `python scripts/repo_inventory.py`, writes the
  output to `artifacts/repo-inventory.txt`, and uploads it as a
  build artifact (14-day retention). Reports: total file count, top
  extensions, large-file warnings (> 500 KB), junk-file candidates
  (`*.bak`, `*.save`, `*.tmp`, `*~`).
- **Why advisory.** The inventory is a drift-detection tool, not a
  gate. Promotion to a blocking check is a follow-up sprint after
  the artifact has been observed across ≥ 5 PRs and the operator has
  confirmed the signal is useful.
- **Debug.** Download the artifact from the GitHub Actions run page.
  If a junk file appeared, remove it. If a large file appeared,
  decide whether it belongs in `data/` or should move to Hugging
  Face / Drive. None of these block the PR today.

### `dry-run-guard` (pre-S-044, included for completeness)

- **What it does.** Diffs the PR against `main` and runs
  `scripts/check_dry_run_in_diff.py`. Fails the PR if any added line
  flips an account out of live mode (e.g. a new `mode: dry_run` line
  in `config/accounts.yaml`). Pings the operator via Telegram if
  configured. See `docs/claude/trading-mode-flags.md` for the full
  rule.
- **Debug.** If the guard fires correctly, follow the ping-PR pattern
  in `CLAUDE.md` § "Telegram Reporting" — open a separate ping-PR for
  operator approval before merging the work-PR.

---

## Branch protection wiring

After **S-045** lands, the operator (or Claude with admin token)
should configure required checks on `main` to include:

- `pytest-collect`
- `secret-scan`
- `ruff-lint`
- `dry-run-guard`

`repo-inventory` stays unticked (advisory) until ≥ 5 PRs have observed
the artifact and the operator confirms the drift signal is useful. The
other workflows (`hf-cron`, `training-run`) are not PR-triggered and
do not appear in the branch-protection list.

The S-045 T4 deliverable provides a one-click Colab notebook under
`notebooks/operator/update_branch_protection.ipynb` that sets these
contexts via `gh api` from an operator-supplied admin token (per
CLAUDE.md "Always do" → "For ANY manual VM operator step, deliver a
one-click Colab notebook").

Verify with:

```bash
gh api repos/the-lizardking/ict-trading-bot/branches/main/protection \
  | jq '.required_status_checks.contexts'
```

---

## Adding a new workflow

When adding a new GitHub Actions workflow:

1. Place it under `.github/workflows/` with a kebab-case filename
   matching the `name:` field.
2. Default to `on: pull_request: branches: [main]` + `on: push:
   branches: [main]` for PR gates; use `schedule` / `workflow_dispatch`
   for cron / manual jobs.
3. Use `actions/checkout@v4` and `actions/setup-python@v5` with
   `python-version: "3.11"` to match the rest of the suite.
4. Add a row to the **Workflows at a glance** table above and a
   per-workflow section. State whether it's blocking or advisory.
5. If it's blocking, update branch protection and the **Required
   status checks on `main`** list.
6. If it consumes new dev deps, add them to `requirements-dev.txt`
   (not `requirements.txt` / `requirements-test.txt`).
7. Cross-link from any related runbook (e.g.
   `docs/claude/trading-mode-flags.md` for `dry-run-guard`).
