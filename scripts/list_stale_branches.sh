#!/usr/bin/env bash
# ============================================
# List stale `claude/*` and `feat/*` branches on origin.
#
# Read-only. Prints one branch name per line, sorted by last-commit
# date (oldest first). The operator can then prune with:
#
#     git push origin --delete <branch>
#
# Or for a bulk one-shot:
#
#     scripts/list_stale_branches.sh | head -50 | while read b; do
#         git push origin --delete "$b"
#     done
#
# CAUTION: `git branch -r --merged main` does NOT recognise squash-
# merged branches as "merged" because their commits aren't ancestors
# of main. So we use a tip-age heuristic instead: branches whose last
# commit is older than $STALE_DAYS are candidates. The operator should
# spot-check before bulk deletion (e.g. PR #218 might still want its
# branch around briefly post-merge).
#
# Usage:
#     scripts/list_stale_branches.sh                # default 30 days
#     STALE_DAYS=14 scripts/list_stale_branches.sh
# ============================================

set -euo pipefail

STALE_DAYS=${STALE_DAYS:-30}
NOW_EPOCH=$(date +%s)
CUTOFF=$((NOW_EPOCH - STALE_DAYS * 86400))

git fetch --prune origin >/dev/null 2>&1

# Format: epoch_seconds <tab> branch_name
git for-each-ref \
    --format='%(committerdate:unix)%09%(refname:short)' \
    refs/remotes/origin/claude/ \
    refs/remotes/origin/feat/ \
    refs/remotes/origin/feature/ \
    refs/remotes/origin/fix/ \
    refs/remotes/origin/chore/ \
    refs/remotes/origin/docs/ \
| awk -F'\t' -v cutoff="$CUTOFF" '$1 < cutoff { print $0 }' \
| sort -n \
| while IFS=$'\t' read -r ts ref; do
    # Strip the "origin/" prefix so the operator can paste straight
    # into `git push origin --delete <branch>`.
    branch_name="${ref#origin/}"
    age_days=$(( (NOW_EPOCH - ts) / 86400 ))
    printf '%s\t(age %dd)\n' "$branch_name" "$age_days"
done
