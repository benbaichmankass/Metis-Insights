# CI status checks

Quick reference for every GitHub Actions workflow on this repo.
Read this first when a PR check goes red — most failures map to a
single command you can re-run locally.

> **Authority:** the S-CANON-1 canonical doc set
> (`docs/CLAUDE-RULES-CANONICAL.md`,
> `docs/ARCHITECTURE-CANONICAL.md`, `ROADMAP.md`, current sprint
> log) is authoritative as of 2026-05-10. This runbook documents
> how the CI suite shipped in **S-044** gates work that lands on
> `main`. The legacy `docs/claude/workplan.md` is preserved for
> historical context only.
>
> **Scope:** workflows under `.github/workflows/` only. Manual
> verification commands (`scripts/secret_scan.py`,
> `scripts/repo_inventory.py`, etc.) keep working unchanged for
> local dev — see `CLAUDE.md` § "Default verification".

---

## Workflows at a glance

> **CONSOLIDATED 2026-08-06** (`BL-20260806-CI-FANOUT-AMPLIFIES-ACTIONS-OUTAGES`).
> The 30 fast static guards that each used to be their own workflow now run in
> **one** job. See § "The consolidated `guards` job" below before adding a guard.

| Workflow | File | Trigger | Gate | Local equivalent |
|---|---|---|---|---|
| `guards` | `.github/workflows/guards.yml` | `pull_request` / `push` to `main`, `merge_group`, `workflow_dispatch` | **blocking** | `python3 scripts/ci/run_guards.py --all` |
| `pytest-collect` | `.github/workflows/pytest-collect.yml` | `pull_request` to `main`, `push` to `main` | **blocking** (since S-045) | `PYTHONPATH=. pytest --collect-only -q tests/ --ignore=tests/test_main_loop.py` |
| `pytest-run` | `.github/workflows/pytest-run.yml` | `pull_request` to `main`, `push` to `main` | **blocking** (since 2026-05-22) | `PYTHONPATH=. pytest -q tests/ --ignore=tests/test_main_loop.py` |
| `repo-inventory` | `.github/workflows/repo-inventory.yml` | `pull_request` to `main`, `push` to `main` | advisory | `python scripts/repo_inventory.py` |
| `hf-cron` | `.github/workflows/hf-cron.yml` | `schedule` (HF dataset publish) | n/a | not PR-gating |
| `training-run` | `.github/workflows/training-run.yml` | `workflow_dispatch` | n/a | not PR-gating |

**Required status checks on `main`** (2026-08-06): `pytest-collect`,
`pytest-run`, `guards`. The 13 individual guard contexts that were required
before that date (`secret-scan`, `ruff-lint`, `dry-run-guard`,
`env-gate-guard`, `silent-empty-guard`, `canonical-config-loaders`,
`canonical-db-resolver`, `provenance-consumer-guard`,
`diagnostic-provenance-guard`, `layer-guard`, `json-extract-guard`,
`soak-doctrine-guard`, `artifact-validity-guard`) all still run — inside
`guards`. **`enforce_admins` is `true`** — admin / admin-scoped-API merges do
not bypass these checks (without it the required list would be cosmetic, since
every merge in this repo is an admin merge). `repo-inventory` stays advisory by
design. Branch protection wiring is in § "Branch protection wiring" below.

### The consolidated `guards` job

**Why.** Each guard being its own workflow meant one PR asked GitHub for ~29
separate hosted runners. That is free when the runner pool is healthy and
catastrophic when it is not: during the 2026-08-06 Actions incident 28 of 29
jobs sat `queued` with no runner for over an hour, and because almost none of
those workflows declared a `concurrency:` group, each re-run **stacked** rather
than superseding — 75 queued runs from ~3 attempts at one PR. The PR read red
for reasons unrelated to its diff, and "CI failed" was indistinguishable from
"CI never ran".

**What it is.** A packaging change, and only that. Every guard runs the same
command, against the same relevance condition, with the same assertions.

- Registry: **`scripts/ci/run_guards.py`** — one entry per guard, with its
  relevance predicate (`when`) and its steps.
- Failure-path self-tests: **`scripts/ci/guard_selftests.py`** — the five that
  used to be inline heredocs in workflow YAML, lifted verbatim in behaviour and
  now runnable on their own.
- `guards.yml` declares `concurrency: … cancel-in-progress: true`, so a re-run
  supersedes the previous attempt instead of queueing behind it. **This is the
  half that actually fixes the stacking** — keep it on any future CI job.

**Running it locally:**

```bash
python3 scripts/ci/run_guards.py --list          # the registry
python3 scripts/ci/run_guards.py --all           # every guard, ignore relevance
python3 scripts/ci/run_guards.py --only ruff-lint --only secret-scan
python3 scripts/ci/guard_selftests.py --all      # just the failure paths
```

Diff-scoped guards read `/tmp/pr.diff`; produce it with
`git diff origin/main...HEAD > /tmp/pr.diff` (override with `--pr-diff`).

**Adding a guard:** append an entry to `GUARDS` in `run_guards.py` — do **not**
create a new workflow file. `tests/ci/test_run_guards.py` asserts that no guard
is ever silently dropped from the registry and that branch protection still
requires `guards`. A guard that needs to ping the operator on a hit sets
`notify: True`; `guards.yml` sends one Telegram message naming everything that
tripped (previously one message per guard).

> **Status-context naming (post 2026-05-10 audit):** the GitHub
> status-context name comes from the workflow's **job ID**, not the
> file name. Each guard workflow now uses a unique job ID that
> matches its workflow name (`pytest-collect.yml` → job
> `pytest-collect`, `secret-scan.yml` → `secret-scan`,
> `ruff-lint.yml` → `ruff-lint`, `dry-run-guard.yml` → `dry-run-guard`,
> `env-gate-guard.yml` → `env-gate-guard`,
> `silent-empty-guard.yml` → `silent-empty-guard`,
> `repo-inventory.yml` → `repo-inventory`). Before this change the
> guard workflows shared the job ID `scan`, so a 4-way collision
> would have made `REQUIRED_CONTEXTS` match wrongly once
> `BRANCH_PROTECTION_TOKEN` got configured.

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

### `pytest-run` (blocking — since 2026-05-22)

- **What it does.** Installs `requirements.txt` + `requirements-test.txt`
  on Python 3.11, then runs `pytest -q tests/
  --ignore=tests/test_main_loop.py` — the **actual test suite**, not
  just collection. This is the companion to `pytest-collect`: collection
  only verifies imports, so a broken assertion / drifted guard / logic
  regression passes `pytest-collect` as long as the module imports. Three
  such regressions shipped green in this repo (the 14/30 system-action
  allowlist drift, the dead `_RUNTIME_LOGS` fixture, the shadow-wiring
  gap) precisely because no job ran the test bodies.
- **Why advisory at first, now blocking.** The suite had never executed
  in CI, so its true pass/fail state on `main` was unknown. Making it
  required on day one would have blocked every merge behind a wall of
  pre-existing failures, so it shipped advisory. The baseline was then
  driven green — #1658-1667 cleared the failure backlog, #1681 the
  order-dependent + post-IB-merge regressions, #1717 the non-root
  sudo-stub fix that failed only on the CI runner — and on 2026-05-22 it
  was promoted by adding `"pytest-run"` to `REQUIRED_CONTEXTS` in
  `branch-protection-sync.yml` (#1721), the same advisory→blocking path
  `pytest-collect` followed in S-045.
- **Why ignore `tests/test_main_loop.py`.** Same as `pytest-collect`: it
  imports the live trading entrypoint (`src.main`) and is not CI-safe.
- **Debug.** Reproduce with the local equivalent above. Fix the failing
  test or the code it guards — do **not** add `|| true` or
  `--continue-on-collection-errors`.

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
  `ruff>=0.15.0` only), then runs `ruff check .` against ruff's
  default rule set. Repo-level config in `ruff.toml` excludes
  `*.ipynb` (notebook re-serialization is not a behaviour-preserving
  fix) and lists a small `lint.per-file-ignores` table for the
  operator-hold paths (`src/runtime/pipeline.py`,
  `src/units/accounts/*`) where mechanical lint fixes are blocked
  on operator review.
- **History.** S-044 shipped this workflow with the narrow rule set
  `--select E9,F63,F7` because the broader default flagged 286
  pre-existing hits. S-045 walked the rules in scoped per-rule
  commits (T3a F541 → T3b E401 → T3c F811 → T3d F841 → T3e F401 →
  T3f E402 → T3g E741 → T3h F821 → T3 cleanup E731+E701), brought
  the count to 0 on every non-operator-hold path, and dropped
  `--select`.
- **Debug.** Reproduce with `ruff check .` locally. If your PR
  introduced a new hit, either fix it or — if the hit is in an
  operator-hold path — file a ping-PR per CLAUDE.md § "Telegram
  Reporting". Do **not** add the path to `ruff.toml`'s ignore
  list to silence a hit; the ignore list is reserved for the
  S-045 ping-PR backlog and gets emptied as the operator approves
  each fix.

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

The required-status-checks contexts on `main` are kept in sync by
**`.github/workflows/branch-protection-sync.yml`**. The workflow runs
on every push to `main` (and on `workflow_dispatch`); the source of
truth for which checks gate `main` lives in the workflow file's
`REQUIRED_CONTEXTS` shell variable. To add or remove a required
check, edit that variable, commit, push to `main`. The workflow
GETs the current protection (preserving any existing PR-review /
restrictions / admin-enforcement values), then PUTs the new spec.
Idempotent — re-running with no change is a no-op.

Current required contexts on `main` (2026-05-22):

- `pytest-collect`
- `pytest-run`
- `secret-scan`
- `ruff-lint`
- `dry-run-guard`
- `env-gate-guard`
- `silent-empty-guard`
- `canonical-config-loaders`
- `canonical-db-resolver`

`enforce_admins` is set to `true` so the checks apply to admins and
admin-scoped API merges (otherwise they're bypassed and the list is
cosmetic). `pytest-run` was promoted into this list on 2026-05-22
(#1721) once its baseline went green on `main`. `repo-inventory` and
`arch-doc-guard` stay advisory by design.
The other workflows (`hf-cron`, `training-run`) are not PR-triggered and
do not appear in the branch-protection list.

### One-time operator setup

The `branch-protection-sync.yml` workflow needs admin scope to call
the protection API. The default `GITHUB_TOKEN` cannot grant that, so
the workflow reads from a **fine-grained PAT** stored as a repo secret:

1. Create a fine-grained PAT scoped to **only this repo** with
   permission `Administration: Read and write` (Settings → Developer
   settings → Personal access tokens → Fine-grained tokens →
   Generate new token). Set a 1-year expiry and put a calendar
   reminder to rotate.
2. Add it as a repo secret named **`BRANCH_PROTECTION_TOKEN`**
   (Settings → Secrets and variables → Actions → New repository
   secret).
3. Push any commit to `main` (or click *Run workflow* on the
   `branch-protection-sync` workflow page) to trigger the first
   sync.

That's it. From this point on, every change to the required-checks
list is just a git commit on `.github/workflows/branch-protection-sync.yml`.

### Manual fallback

If the workflow ever breaks (PAT expired, permission revoked,
GitHub outage), `notebooks/operator/update_branch_protection.ipynb`
is the manual one-shot — same API call, run from Colab using a token
in Colab Secrets. Keep it as the recovery path; not part of the
regular flow.

### Verify

```bash
gh api repos/benbaichmankass/ict-trading-bot/branches/main/protection \
  | jq '.required_status_checks.contexts'
```

Or check the most recent **branch-protection-sync** run on the Actions
tab — its summary line lists the contexts it just applied.

### Status (2026-05-10, post-canon-followups)

The four required contexts (`pytest-collect`, `secret-scan`,
`ruff-lint`, `dry-run-guard`) match the actual job IDs in the
corresponding workflow files (verified against
`.github/workflows/{pytest-collect,secret-scan,ruff-lint,dry-run-guard}.yml`
after the unique-job-id rename in PR #671). The
`branch-protection-sync.yml` workflow is correct and ready to fire
the moment `BRANCH_PROTECTION_TOKEN` is configured. While the
secret is unset, the workflow no-ops (`token_check` step prints a
notice and skips the PUT) and the protection on `main` is whatever
GitHub's last manual / Colab apply put there.

When the operator sets `BRANCH_PROTECTION_TOKEN` and dispatches a
run, the final step's notice should print:

```
::notice::Branch protection updated. Required contexts now: ["pytest-collect","secret-scan","ruff-lint","dry-run-guard"]
```

The advisory guards (`repo-inventory`, `silent-empty-guard`,
`env-gate-guard`) still run on every PR but do not block the
merge button.

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
