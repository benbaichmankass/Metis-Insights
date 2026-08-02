#!/usr/bin/env bash
# Pre-flight guard for a DESTRUCTIVE "sync to a remote ref" — the idiom
# `git reset --hard <ref>` / `git checkout -B <branch> <ref>` used to line a
# working branch up with e.g. origin/main.
#
# WHY (BL-20260730-DESTRUCTIVE-GIT-SYNC-NO-GUARD): those commands silently
# DISCARD any commit on the current HEAD that is not already in <ref>. Three
# near-miss data losses happened in a single session because the question "does
# my branch carry work not in the target?" was never asked — recovery depended
# on git's non-fast-forward rejection on the NEXT push, which only fires after
# the local work is already gone and is easy to misread. This makes that one
# cheap question a REQUIRED step: it refuses (exit 2) when HEAD carries commits
# absent from the target, unless you explicitly opt into discarding them.
#
# This is NOT a CI check (CI can't intercept a local reset) — it is a wrapper to
# run BEFORE the destructive command, in a session or by hand. The legitimate
# read-only-mirror syncs (scripts/deploy_pull_restart.sh on the live VM,
# scripts/ops/trainer_git_sync.sh) discard by design and pass `--allow-discard`.
#
# Usage:
#   scripts/ops/git_sync_preflight.sh origin/main            # refuse if HEAD has unmerged commits
#   scripts/ops/git_sync_preflight.sh origin/main --fetch    # fetch the ref first
#   scripts/ops/git_sync_preflight.sh origin/main --allow-discard   # mirror case: OK to discard
# Then, only on exit 0, run the destructive command yourself.
set -euo pipefail

TARGET="${1:-}"
if [ -z "$TARGET" ]; then
  echo "usage: $0 <target-ref> [--fetch] [--allow-discard]" >&2
  exit 64
fi
shift || true

FETCH=0
ALLOW_DISCARD=0
for arg in "$@"; do
  case "$arg" in
    --fetch) FETCH=1 ;;
    --allow-discard) ALLOW_DISCARD=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 64 ;;
  esac
done

if [ "$FETCH" = "1" ]; then
  # Split "origin/main" into remote + branch for a targeted fetch; tolerate a
  # bare local ref (no slash) by skipping the fetch.
  if [[ "$TARGET" == */* ]]; then
    git fetch --prune "${TARGET%%/*}" "${TARGET#*/}" >/dev/null 2>&1 || \
      git fetch --prune "${TARGET%%/*}" >/dev/null 2>&1 || true
  fi
fi

if ! git rev-parse --verify --quiet "$TARGET" >/dev/null; then
  echo "PREFLIGHT: target ref '$TARGET' does not resolve — fetch it first (--fetch) or fix the name." >&2
  exit 65
fi

# Commits on HEAD that are NOT in the target — exactly what a hard reset discards.
UNMERGED="$(git log --oneline "$TARGET..HEAD" 2>/dev/null || true)"

if [ -z "$UNMERGED" ]; then
  echo "PREFLIGHT OK: HEAD has no commits absent from $TARGET — a sync to it discards nothing."
  exit 0
fi

COUNT="$(printf '%s\n' "$UNMERGED" | grep -c . || true)"
if [ "$ALLOW_DISCARD" = "1" ]; then
  echo "PREFLIGHT (--allow-discard): $COUNT commit(s) on HEAD are absent from $TARGET and WILL be discarded (intentional mirror sync):"
  printf '%s\n' "$UNMERGED" | sed 's/^/    /'
  exit 0
fi

echo "PREFLIGHT REFUSED: HEAD carries $COUNT commit(s) NOT in $TARGET — a 'git reset --hard $TARGET' or 'git checkout -B <branch> $TARGET' would DISCARD them:" >&2
printf '%s\n' "$UNMERGED" | sed 's/^/    /' >&2
echo "" >&2
echo "If that work should survive: push the branch / cherry-pick / rebase onto $TARGET first." >&2
echo "If discarding is genuinely intended (read-only mirror): re-run with --allow-discard." >&2
exit 2
