# S-AI-WS10-FU — Opt-in pre-commit hook for arch-doc-guard

**Date:** 2026-05-10
**Authority:** [`docs/sprint-logs/S-AI-WS10.md`](S-AI-WS10.md)
**Status:** ✅ COMPLETE — opt-in by design.

## Goal

WS10 ships the CI-side `arch-doc-guard` as an advisory `::warning`
annotation. PART-FU adds an **opt-in** pre-commit hook that runs
the same guard locally — and blocks the commit on warning. The
opt-in nature is the trade: operators who install it accept
stronger enforcement than CI provides; operators who skip it get
exactly the existing CI behavior.

## Decisions

- **Local hook is stronger than CI.** The CI `arch-doc-guard.yml`
  always exits 0 — its job is *visibility*, not *blocking*.
  The local hook exits 1 on warning, blocking the commit. The
  asymmetry is intentional: operators install the local hook
  because they want the friction. If they don't want it, they
  don't run `scripts/install-hooks.sh`.
- **Bypass via `git commit --no-verify`.** Standard git escape
  hatch. The hook's stderr message explicitly tells the operator
  this. No custom env var needed.
- **Symlink, not copy.** `install-hooks.sh` symlinks
  `scripts/git-hooks/pre-commit` into `.git/hooks/pre-commit`.
  Future repo updates to the hook source flow through
  automatically — no re-install needed.
- **No third-party `pre-commit` framework.** Adding the `pre-commit`
  python package as a dependency would balloon install footprint
  for a 30-line bash hook. KISS: pure bash + Python, install via
  a 40-line shell script.
- **Hook works from worktrees.** Symlink target is computed
  relative to `.git/hooks/`. Each git worktree has its own
  `.git/hooks` (or `git/worktrees/<name>/hooks`), and
  `scripts/install-hooks.sh` resolves correctly in both.
- **Discovers all hooks in `scripts/git-hooks/`.** Future hook
  additions (e.g. `pre-push`, `commit-msg`) drop into the same
  directory and get auto-installed by the existing script. No
  install-hooks.sh modification needed.

## Deliverables

- `scripts/git-hooks/pre-commit` (new) — bash hook. Reads
  staged files via `git diff --cached --name-only -z`, calls
  `scripts/arch_doc_guard.py`, blocks on `::warning`.
- `scripts/install-hooks.sh` (new) — opt-in installer. Idempotent
  symlink creation; auto-discovers hooks under `scripts/git-hooks/`.
- `tests/test_install_hooks_sh.py` (new) — 6 integration tests
  against a synthetic git repo:
  - No staged files → exit 0, no stderr.
  - Only test / README files → exit 0, no warning.
  - High-impact path without arch doc → exit 1 with helpful message.
  - High-impact path + arch doc → exit 0.
  - `install-hooks.sh` creates symlink correctly.
  - `install-hooks.sh` is idempotent.

## Acceptance

- [x] `pytest tests/test_install_hooks_sh.py` — 6 / 6 pass.
- [x] `bash -n` clean on both scripts.
- [x] Hook end-to-end test against synthetic repo (build a tmp
      repo, stage files, run hook, assert behavior).
- [x] Installer is idempotent — re-running replaces symlinks
      without error.
- [x] Hook respects `git commit --no-verify` (standard git
      behavior; documented in the stderr message).

## Out of scope (filed for follow-ups)

- **Pre-push hook** that runs the same guard against the full
  push range (catches commits the operator made before installing
  the hook).
- **`commit-msg` hook** that enforces the canonical sprint-tag
  pattern (`S-AI-WS<n>-…`) — useful for hard-failing PR titles
  that diverge from the sprint-log naming.
- **Auto-install via `make setup` or equivalent.** Today the
  operator runs `bash scripts/install-hooks.sh` once. A make
  target would consolidate, but adds a Makefile dependency we
  don't currently have.

## Live runtime impact

None. Hook is opt-in; CI workflow is unchanged. No new repo
dependencies. Operator runs `bash scripts/install-hooks.sh`
once on their dev clone; uninstall is `rm .git/hooks/pre-commit`.

## Operator usage

```
# One-time install (opt-in):
bash scripts/install-hooks.sh

# Now any commit that touches high-impact paths without arch
# docs will be blocked locally. Bypass for emergencies:
git commit --no-verify

# Uninstall:
rm .git/hooks/pre-commit
```
