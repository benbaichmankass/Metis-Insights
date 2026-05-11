#!/usr/bin/env bash
# scripts/migrate_to_data_dir.sh — copy repo-resident runtime data to DATA_DIR.
#
# Dry-run by default. Use --execute to actually copy. Idempotent: rsync
# with -a preserves perms/times; re-runs only copy what changed.
#
# Usage:
#   ./scripts/migrate_to_data_dir.sh                  # dry-run, uses /data/bot-data
#   ./scripts/migrate_to_data_dir.sh --execute        # actually copy
#   DATA_DIR=/mnt/x ./scripts/migrate_to_data_dir.sh  # custom target
#
# Order of operations (per subdir):
#   1. Source <repo>/<sub> exists? If not, skip silently.
#   2. Target <DATA_DIR>/<sub> exists? If not, mkdir -p.
#   3. rsync -a --info=stats2 <repo>/<sub>/ <DATA_DIR>/<sub>/
#
# What this does NOT do:
#   - Delete source files (rollback stays trivial; you can revert
#     DATA_DIR and the trader picks up the repo copies again).
#   - Stop or start services. The operator decides when to flip
#     DATA_DIR in the systemd EnvironmentFile and `systemctl restart`.
#   - Touch trade_journal.db (lives at <repo>/trade_journal.db, not
#     under any of the four subdirs). Use TRADE_JOURNAL_DB env to
#     redirect it independently.

set -euo pipefail

DRYRUN=1
for arg in "$@"; do
    case "$arg" in
        --execute|-x) DRYRUN=0 ;;
        --help|-h)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *)
            printf 'unknown arg: %s\n' "$arg" >&2
            exit 64
            ;;
    esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${DATA_DIR:-/data/bot-data}"
SUBDIRS=(data runtime_logs runtime_state artifacts)

if [ $DRYRUN -eq 1 ]; then
    printf '*** DRY RUN — pass --execute to actually copy. ***\n\n'
fi

printf 'source: %s\ntarget: %s\n\n' "$REPO_ROOT" "$TARGET"

if [ ! -d "$TARGET" ]; then
    printf 'ERROR: target %s does not exist. Run check_data_dir.sh first.\n' "$TARGET" >&2
    exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
    printf 'ERROR: rsync not found. Install with: sudo apt-get install -y rsync\n' >&2
    exit 1
fi

for sub in "${SUBDIRS[@]}"; do
    src="$REPO_ROOT/$sub"
    dst="$TARGET/$sub"

    if [ ! -d "$src" ]; then
        printf '%-15s skip — source missing (%s)\n' "$sub" "$src"
        continue
    fi

    if [ ! -d "$dst" ]; then
        if [ $DRYRUN -eq 1 ]; then
            printf '%-15s would mkdir %s\n' "$sub" "$dst"
        else
            mkdir -p "$dst"
            printf '%-15s mkdir %s\n' "$sub" "$dst"
        fi
    fi

    if [ $DRYRUN -eq 1 ]; then
        # 'sub' is an awk builtin — pass the name as 'name' instead.
        rsync -an --itemize-changes "$src/" "$dst/" \
            | awk -v name="$sub" '{print name": "$0}'
    else
        rsync -a --info=stats2 "$src/" "$dst/"
        printf '%-15s rsync complete\n' "$sub"
    fi
done

printf '\n'
if [ $DRYRUN -eq 1 ]; then
    printf 'Dry run done. Re-run with --execute to copy.\n'
else
    printf 'Migration done.\n'
    printf 'Next steps:\n'
    printf '  1. Set DATA_DIR=%s in the systemd EnvironmentFile.\n' "$TARGET"
    printf '  2. sudo systemctl daemon-reload\n'
    printf '  3. sudo systemctl restart ict-trader-live ict-web-api\n'
    printf '  4. Verify with: ./scripts/print_runtime_profile.py\n'
fi
