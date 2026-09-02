#!/usr/bin/env bash
# Register the register merge driver in THIS clone's git config.
#
# ⚠️ WHY THIS STEP CANNOT BE SKIPPED OR COMMITTED AWAY. `.gitattributes` names a
# driver; it cannot DEFINE one. Git deliberately refuses to take an executable
# path from a tracked file, because cloning a repo would then run its code. So
# every clone that wants the driver must opt in once, here. A clone that has not
# run this still merges these files the old way — it degrades to today's
# behaviour, it does not break.
#
# ⚠️ THIS DOES NOT AFFECT GITHUB. Custom merge drivers are client-side only.
# GitHub's auto-merge will still report `dirty` on a conflicted register PR.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
git config merge.jsonregister.name "row-aware 3-way merge for the shared JSON registers"
git config merge.jsonregister.driver "python3 '$root/scripts/ops/merge_json_register.py' %O %A %B %P"
git config merge.jsonregister.recursive binary
echo "installed: merge.jsonregister -> $root/scripts/ops/merge_json_register.py"
echo "verify with: git check-attr merge -- docs/claude/work/MANAGER-CHECKLIST.json"
