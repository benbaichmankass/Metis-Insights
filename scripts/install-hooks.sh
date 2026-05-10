#!/usr/bin/env bash
# scripts/install-hooks.sh — installs opt-in git hooks for this repo.
#
# Currently installs:
#   - pre-commit → arch-doc-guard local mirror (S-AI-WS10 FU)
#
# Idempotent: re-running replaces the symlinks (no-op if they're
# already correct).
#
# Bypass any installed hook for a single commit with:
#   git commit --no-verify
#
# Uninstall:
#   rm .git/hooks/pre-commit
#
# Why opt-in?  CI carries the authoritative arch-doc check
# (`.github/workflows/arch-doc-guard.yml`) as an advisory warning.
# The local hook is more aggressive (blocks the commit on warning)
# precisely because the operator chose to install it. Forcing it
# globally would create install friction without operator buy-in.

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
hooks_src="${repo_root}/scripts/git-hooks"
hooks_dst="${repo_root}/.git/hooks"

if [ ! -d "${hooks_dst}" ]; then
  echo "no .git/hooks directory at ${hooks_dst}; is this a worktree?" >&2
  exit 1
fi

installed=()
for hook in "${hooks_src}"/*; do
  name="$(basename "${hook}")"
  case "${name}" in
    README*|*.md) continue ;;
  esac
  target="${hooks_dst}/${name}"
  # Use a relative symlink so it works inside worktrees.
  rel_src="$(realpath --relative-to="${hooks_dst}" "${hook}")"
  ln -sfn "${rel_src}" "${target}"
  chmod +x "${hook}"  # source script needs +x
  installed+=("${name}")
done

if [ "${#installed[@]}" -eq 0 ]; then
  echo "no hook sources found in ${hooks_src}"
  exit 0
fi

echo "Installed git hooks (symlinked into .git/hooks/):"
for h in "${installed[@]}"; do
  echo "  - ${h}"
done
echo ""
echo "Bypass for a single commit: git commit --no-verify"
echo "Uninstall: rm .git/hooks/<hook-name>"
