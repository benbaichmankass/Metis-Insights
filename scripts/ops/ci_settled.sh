#!/usr/bin/env bash
# Wait for a PR's CI to settle WITHOUT burning a session's context on polling.
#
#   scripts/ops/ci_settled.sh <PR_NUMBER> [TIMEOUT_MINUTES]
#
# ONE invocation, ONE compact JSON on stdout. The wall clock is spent on a
# GitHub runner (see .github/workflows/ci-settled.yml), not in a poll loop of
# `mcp__github__pull_request_read` calls -- which is the only alternative a
# sandbox session has, because `api.github.com` answers HTTP 403 in here.
#
# The watch branch is left behind on purpose: this sandbox can CREATE a remote
# ref but deleting one is HTTP 403 at the git proxy, so the relay sweeps spent
# `automation/ciwatch-*` branches itself on its next run.
#
# ⚠️ IT NEVER TOUCHES THE PR BEING WATCHED. The request is built as a commit on
# top of `origin/main` with git plumbing (no checkout, no index change, the
# working tree is not read or written) and pushed to a THROWAWAY branch
# `automation/ciwatch-<name>`. Committing the request onto the watched branch
# would add a commit to the PR head, re-trigger the very run being waited on,
# and move the head out from under the answer.
#
# Exit codes:
#   0  green AND not conflicted -- safe to act on
#   1  settled but not actionable (red / cancelled / conflict / green-but-dirty)
#   2  we could not look (unreadable, or no result landed)
#
# ⚠️ 2 IS DELIBERATELY DISTINCT FROM 1. "CI failed" and "we never got an answer"
# are different facts and a caller must be able to branch on which it got.
#
# ⚠️ GREEN CHECKS DO NOT MEAN MERGEABLE, so exit 0 requires BOTH. Observed live
# 2026-09-02 on this relay's own PR: all four required checks passing while
# `mergeable_state` read `dirty` because the base branch had moved. The payload
# said so in its `reason` and the exit code said 0 -- a caller branching on the
# code would have read "mergeable" while the prose said the opposite. Fixed
# here rather than left to the reader, because the exit code is the half a
# script acts on.
set -uo pipefail

PR="${1:-}"
TIMEOUT_MIN="${2:-20}"
if [ -z "$PR" ]; then echo "usage: $0 <PR_NUMBER> [TIMEOUT_MINUTES]" >&2; exit 2; fi
case "$PR" in ''|*[!0-9]*) echo "PR must be a number, got: $PR" >&2; exit 2;; esac
case "$TIMEOUT_MIN" in ''|*[!0-9]*) echo "timeout must be a number" >&2; exit 2;; esac

NAME="pr${PR}-$(date -u +%Y%m%dT%H%M%SZ)-$$"
BRANCH="automation/ciwatch-${NAME}"
# Give up locally a little after the runner does, so a runner that dies is
# reported as `unreadable` here rather than hanging this command forever.
LOCAL_DEADLINE=$(( $(date +%s) + (TIMEOUT_MIN * 60) + 300 ))

cleanup() { [ -n "${TMPIDX:-}" ] && rm -f "$TMPIDX"; }
trap cleanup EXIT

# The request commit is based on a ref that CONTAINS .github/workflows/ci-settled.yml.
# Normally that is origin/main. The override exists so the relay can be proven
# end to end on the branch that introduces it -- a workflow that has never
# actually run is exactly the "looks armed and is not" state this repo has been
# bitten by (probes.yml fired ~4h50m late, once rather than daily).
BASE_REF="${CI_SETTLED_BASE:-origin/main}"
git fetch -q origin "${BASE_REF#origin/}" || { echo "{\"state\":\"unreadable\",\"reason\":\"could not fetch ${BASE_REF}\"}"; exit 2; }

REQ="$(printf '{"pr": %s, "timeout_minutes": %s, "poll_seconds": 20, "review_threads": true}\n' "$PR" "$TIMEOUT_MIN")"
BLOB="$(printf '%s' "$REQ" | git hash-object -w --stdin)" || exit 2

TMPIDX="$(mktemp)"
GIT_INDEX_FILE="$TMPIDX" git read-tree "$BASE_REF" || exit 2
GIT_INDEX_FILE="$TMPIDX" git update-index --add \
  --cacheinfo "100644,${BLOB},automation/ci-watch/${NAME}.json" || exit 2
TREE="$(GIT_INDEX_FILE="$TMPIDX" git write-tree)" || exit 2
COMMIT="$(git commit-tree "$TREE" -p "$(git rev-parse "$BASE_REF")" \
  -m "ci-watch: PR #${PR}")" || exit 2

git push -q origin "${COMMIT}:refs/heads/${BRANCH}" || {
  echo '{"state":"unreadable","reason":"could not push the ci-watch request"}'; exit 2; }
echo "ci-settled: watching PR #${PR} on ${BRANCH} (up to ${TIMEOUT_MIN}m)" >&2

RESULT_PATH="automation/ci-results/${NAME}.json"
while :; do
  sleep 20
  if git fetch -q origin "$BRANCH" 2>/dev/null && \
     git cat-file -e "FETCH_HEAD:${RESULT_PATH}" 2>/dev/null; then
    git cat-file -p "FETCH_HEAD:${RESULT_PATH}"
    read -r STATE MERGE <<EOF
$(git cat-file -p "FETCH_HEAD:${RESULT_PATH}" | python3 -c 'import json,sys
d = json.load(sys.stdin)
print(d.get("state") or "?", d.get("mergeable_state") or "?")')
EOF
    # NO CLEANUP HERE, DELIBERATELY. Deleting a remote branch is HTTP 403 at
    # this sandbox's git proxy (measured 2026-09-02) even though CREATING one
    # succeeds, so a caller cannot remove its own watch branch. The relay
    # sweeps `automation/ciwatch-*` branches older than 6h on its next run.
    case "$STATE" in
      green)      [ "$MERGE" = "dirty" ] && exit 1; exit 0 ;;
      unreadable) exit 2 ;;
      *)          exit 1 ;;
    esac
  fi
  if [ "$(date +%s)" -ge "$LOCAL_DEADLINE" ]; then
    # WE STOPPED WAITING. This says nothing about the PR's CI, and must not be
    # read as a verdict on it.
    echo "{\"state\":\"unreadable\",\"settled\":false,\"pr\":${PR},\"reason\":\"no result file on ${BRANCH} within the local deadline — the relay run may have failed. Check the ci-settled workflow run; this is 'we did not look', not a CI verdict.\"}"
    exit 2
  fi
done
