# Sprint Log: S-CANON-1

> First sprint to use the canonical sprint-log template. Older
> sprint records under `docs/sprint-summaries/` and `docs/sprint-plans/`
> remain historical record.

## Date Range
- Start: 2026-05-10
- End: 2026-05-10

## Objective
- Primary goal: rebase the project on a fresh canonical-doc set,
  audit the repo against it, fix mismatches.
- Secondary goals:
  - separate Claude rules from architecture,
  - publish a canonical GitHub Actions reference,
  - correct stale `the-lizardking` owner references in active files,
  - clean up 9 spurious zero-byte files committed in PR #658,
  - establish the new sprint-log format.

## Tier
- **Tier 1** for everything in this sprint:
  doc updates, removing accidentally-tracked junk files, correcting
  hardcoded URL strings to the current GitHub owner, fixing test
  fixtures to match the corrected URLs. No live trading code,
  no risk-policy edits, no strategy edits, no service or timer changes.
- The owner-ref change in
  `.github/workflows/branch-protection-sync.yml` is technically a
  workflow change but the workflow had been silently broken for the
  current repo (it pointed `OWNER="the-lizardking"`); restoring the
  intended behaviour for the current repo is a Tier-1 fix.

## Starting Context
- Active roadmap items: S-066 just closed; S-047 T6 + T7 paused;
  M5 next.
- Prior sprint reference: S-066 (M1 P2 janitor pass).
- Known risks at start:
  - older `docs/claude/workplan.md` was being treated as the canonical
    decider; multiple overlapping rule docs (`operating-protocol.md`,
    `external-delegation.md`, `decomposition-rules.md`,
    `cleanup-policy.md`, …);
  - sprint summaries and sprint prompts split across two folders;
  - hardcoded `the-lizardking/...` URLs in active scripts and
    workflows;
  - 9 zero-byte files at repo root with names like
    `<sqlite3.Connection object at 0x...>`.

## Repo State Checked
- Branch: `claude/update-canonical-docs-chffN`, off `main`.
- Deployment state reviewed via systemd unit files in `deploy/`; no
  changes proposed.
- Canonical docs reviewed:
  - root `CLAUDE.md`,
  - `ROADMAP.md`,
  - `docs/architecture.md`,
  - `docs/claude/INDEX.md`,
  - `docs/api-tier-policy.md`,
  - `docs/claude/operating-protocol.md`,
  - `docs/claude/cleanup-policy.md`,
  - `docs/claude/comms-architecture.md`,
  - `comms/README.md`.

## Files and Systems Inspected
- Code files inspected:
  - `src/main.py` (entrypoint, heartbeat write loop).
  - `src/runtime/pipeline.py` (kill-switch flag,
    risk-counter injection, signal-audit logging, outcome reporting,
    1209 lines total).
  - `src/runtime/orders.py` (presence of `safe_place_order`).
  - `src/runtime/closed_flat_invariant.py` (root cause for the
    spurious sqlite filenames — `_fetch_recently_closed` falls
    through to `sqlite3.connect(str(db))` when an unsupported
    object is passed; the `repr()` of a Connection becomes a
    file).
  - `src/strategy_registry.py` (single source of truth for strategy
    names and signal prefixes).
  - `src/units/accounts/risk.py` (per-account `RiskManager`,
    risk-cap fields).
  - `src/bot/telegram_query_bot.py` (the operator-facing bot;
    Colab notebook URL constants were stale).
  - `src/comms/{models,state,store,templates,log}.py` (presence
    confirmed).
- Config files inspected: `config/accounts.yaml`,
  `config/strategies.yaml`, `.env.example`.
- Deployment files inspected: every `deploy/ict-*.{service,timer}`.
  `ict-git-sync.timer` confirmed at 5-min cadence.
- Docs inspected: every file under `docs/` plus the sprint-summary,
  sprint-plan, and sprint-prompt directories (counts only, full reads
  on the canonical-set candidates).
- Services or timers inspected: `ict-trader-live`, `ict-web-api`,
  `ict-telegram-bot`, `ict-git-sync`, `ict-heartbeat`,
  `ict-hourly-snapshot`, `ict-smoke-once`, `ict-claude-bridge`,
  `ict-env-check`.
- GitHub Actions workflows inspected: every file under
  `.github/workflows/` (19 files).

## Work Completed
1. Created `docs/CLAUDE-RULES-CANONICAL.md` — Claude operating rules
   with explicit Tier 1 / 2 / 3 permission matrix, code-first
   verification rule, GitHub-Actions rule, and a workflow map.
2. Created `docs/ARCHITECTURE-CANONICAL.md` — system architecture
   covering runtime trading, research/validation, comms, deployment,
   GitHub Actions automation; verified file paths against the repo.
3. Created `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md` — uniform
   sprint-log template (this file is the first instance).
4. Created `docs/github-actions-workflows.md` — canonical reference
   for every workflow under `.github/workflows/` (CI guards, repo
   admin, VM operations, training, sprint continuity).
5. Updated root `CLAUDE.md` and `ROADMAP.md` to defer to the
   canonical docs and record the repo-identity rule.
6. Removed 9 zero-byte tracked files at repo root with names like
   `<sqlite3.Connection object at 0x...>` (introduced by PR #658).
7. Corrected stale `the-lizardking` → `benbaichmankass` references
   in active files (full list under "Stale owner references" below).
8. Filed S-CANON-1 row in the sprint ledger inside `ROADMAP.md`.

## Validation Performed
- Ran `git ls-files | grep '<sqlite3.Connection'` before and after
  removal — empty after.
- Ran `grep -rn "the-lizardking" --include='*.md' --include='*.py'
  --include='*.yml' --include='*.yaml' --include='*.sh' --include='*.toml'`
  before and after; remaining hits are confined to historical sprint
  summaries (`docs/sprint-summaries/sprint-0XX-summary.md`) and the
  Hugging Face Space identifier in `docs/hf_claude_patch.md` (HF
  username, not GitHub).
- Verified `tests/test_set_keys_command.py` was updated alongside
  the source URL change in `src/bot/telegram_query_bot.py` so the
  assertion stays in sync.
- Verified `tests/test_notify_on_pull.py` summary-URL fixture was
  updated alongside `scripts/notify_on_pull.py`.
- Did **not** run the test suite from this sandbox (network
  restrictions; pytest-collect runs as a required check on the PR).

## Stale owner references (before → after)
| File | Before | After |
|---|---|---|
| `README.md` | `git clone .../the-lizardking/...` | `benbaichmankass` |
| `CLAUDE.md` | (rules-block referenced lizardking implicitly) | now points to canonical docs |
| `ROADMAP.md` | M6 row mentioned `the-lizardking/ict-trader-dashboard` | `benbaichmankass/ict-trader-dashboard` (with note) |
| `docs/ICT_BOT_MASTER_INSTRUCTIONS.md` | repo + git-username rows | updated, historical noted |
| `docs/audit/repo_audit.md` | repo header | updated, historical noted |
| `docs/operator/setup-api-keys.md` | raw GitHub URLs | updated |
| `docs/operator/colab-key-rotation.md` | colab URL | updated |
| `docs/claude/colab-workflows.md` | colab URLs (×3) | updated |
| `docs/sprints/sprint-044-prompt.md` | `gh api repos/the-lizardking/...` | updated |
| `docs/sprints/sprint-045-prompt.md` | `gh api repos/the-lizardking/...` | updated |
| `src/bot/telegram_query_bot.py` | `_COLAB_NOTEBOOK_URL`, `_COLAB_DOC_URL` | updated |
| `tests/test_set_keys_command.py` | URL assertion | updated |
| `tests/test_notify_on_pull.py` | `summary_url` fixture | updated |
| `scripts/notify_on_pull.py` | `GITHUB_COMMIT_URL` | updated |
| `scripts/notify_session.py` | docstring example link | updated |
| `scripts/bootstrap_diag_relay.sh` | `REPO=` default | updated |
| `scripts/sprint015/data_sources.py` | `USER_AGENT` | updated |
| `.github/workflows/branch-protection-sync.yml` | `OWNER="the-lizardking"` (broken automation on the current repo) | `OWNER="benbaichmankass"` |

Preserved unchanged (historical record only — sprint summaries with
PR links from the previous owner namespace):
- `docs/sprint-summaries/sprint-012-summary.md`
- `docs/sprint-summaries/sprint-013-summary.md`
- `docs/sprint-summaries/sprint-014-summary.md`
- `docs/sprint-summaries/sprint-021-summary.md`
- `docs/sprint-summaries/sprint-022-summary.md`
- `docs/sprint-summaries/sprint-023-summary.md`
- `docs/sprint-summaries/operator-onboarding-summary.md`

Out of scope (not a GitHub owner):
- `docs/hf_claude_patch.md` — `the-lizardking/ict-training-job` is
  a Hugging Face Space identifier, separate namespace, left alone.

## Documentation Updated
- Rules doc updates: `docs/CLAUDE-RULES-CANONICAL.md` (new).
- Architecture doc updates: `docs/ARCHITECTURE-CANONICAL.md` (new).
- Roadmap updates: `ROADMAP.md` masthead + S-CANON-1 ledger row +
  M6 owner correction.
- GitHub Actions doc updates: `docs/github-actions-workflows.md`
  (new).
- Subsystem doc updates: root `CLAUDE.md` masthead, several
  operator docs and the master-instructions doc.
- Historical docs marked superseded: the rule-and-architecture
  sections of `CLAUDE.md` defer to the new canonical docs;
  `docs/architecture.md` and `docs/claude/operating-protocol.md`
  remain useful but non-authoritative.

## Contradictions or Drift Found
1. **`branch-protection-sync.yml` had OWNER hardcoded to the
   previous repo namespace.** Any branch-protection sync run was
   targeting a repo we no longer own. Fixed in this sprint.
2. **9 spurious tracked zero-byte files at repo root** with names
   like `<sqlite3.Connection object at 0x7f5...>`. Introduced by
   PR #658 (`feat(monitor): closed → exchange-flat invariant
   reconciler [DRAFT]`). Root cause is the `else: conn =
   sqlite3.connect(str(db))` branch at
   `src/runtime/closed_flat_invariant.py:128` — when something
   that isn't a `sqlite3.Connection`, `Database` wrapper, or path
   is passed, its `repr()` becomes a filename and sqlite creates
   the empty file. This sprint removes the files; the underlying
   defensive check should be tightened in a follow-up Tier-2 PR
   (the module is on the live-order path even though
   `CLOSED_FLAT_INVARIANT_ENABLED` defaults to false).
3. **Multiple overlapping rule docs.** The new
   `CLAUDE-RULES-CANONICAL.md` is the single decider going forward;
   `docs/claude/workplan.md`, `docs/claude/operating-protocol.md`,
   `docs/claude/external-delegation.md`,
   `docs/claude/decomposition-rules.md`, etc. are useful context
   but not authoritative on policy.
4. **Sprint summaries vs. sprint prompts vs. sprint logs.** Three
   separate folders (`docs/sprint-summaries/`, `docs/sprint-plans/`,
   `docs/sprints/`) with overlapping purposes. New sprints land in
   `docs/sprint-logs/` using the canonical template; the older
   folders stay as historical record.
5. **No code/doc mismatches found in the trade pipeline.** The
   pipeline (`src/runtime/pipeline.py`), risk gating
   (`src/units/accounts/risk.py`), comms isolation (`comms/` not
   imported by `src/runtime/` or `src/units/`), and deploy/timer
   layout match what the new architecture doc describes.

## Risks and Follow-Ups
- **Remaining technical risks:**
  - `closed_flat_invariant._fetch_recently_closed` still has the
    "stringify whatever was passed" fallback. Tighten to raise
    `TypeError` and add a test that the fallback is never hit by
    real callers. Tier 2.
  - `tests/test_install_systemd_units.sh` — present, but not run
    in this sandbox. Worth a CI-side smoke check after the deploy
    list grows again.
  - 19 GitHub Actions workflows — confirm `bootstrap-labels.yml`
    has rows for every label any workflow filters on
    (`vm-cloud-fix-request`, `vm-net-diag-request`,
    `vm-net-fix-request` newer than the original list).
- **Remaining product decisions (Tier 3):**
  - S-065 still blocked on operator Google Cloud Console OAuth
    setup.
  - M5 backtest workflow still pending.
- **Blockers:** none for this sprint.

## Deferred Items
- Hugging Face Space `the-lizardking/ict-training-job` rename or
  re-publish under `bentzbk/...` — this is an HF-side decision,
  not a repo edit.
- Closing out `docs/claude/workplan.md` formally as "superseded by
  canonical docs" rather than just leaving it un-cited. Done in a
  separate small PR if the operator agrees.
- Migrating older sprint summaries into the new template format —
  not worthwhile; they are historical record and the new template
  applies forward.
- Cleaning up duplicate notes in `docs/claude/` (50+ working
  notes). Targeted hygiene sprint candidate.

## Next Recommended Sprint
- **Suggested next sprint:** small Tier-2 follow-up
  ("S-CFI-FIX") — tighten
  `src/runtime/closed_flat_invariant._fetch_recently_closed` to
  reject unsupported `db` types instead of silently
  `sqlite3.connect(str(db))`-ing them; add a regression test;
  verify nothing else in the repo writes spurious 0-byte files at
  the repo root.
- **Why next:** removes a known surface area for accidental
  on-disk file creation in a module that will eventually be wired
  to the tick loop (PR #658 is currently a DRAFT pending operator
  ack on the design memo). Better to close the silent-write hole
  before the wiring PR lands.
- **Required verification before starting:**
  - Confirm operator ack on the
    `closed_flat_invariant` design memo (DRAFT PR #658).
  - Confirm `CLOSED_FLAT_INVARIANT_ENABLED` default stays `false`
    until Phase 2.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] Roadmap status was checked (S-CANON-1 row added).
- [x] Contradictions were recorded (5 listed).
- [x] Remaining unknowns were stated clearly.
