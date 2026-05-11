#!/usr/bin/env bash
# scripts/check_data_dir.sh — OCI block-storage preflight.
#
# Non-destructive check that the configured DATA_DIR is mounted,
# writable, and that the four logical subdirs (data, runtime_logs,
# runtime_state, artifacts) exist or can be created. Exits non-zero
# on any failure so it can gate a systemd ExecStartPre or be run
# manually before flipping the env var.
#
# Usage:
#   ./scripts/check_data_dir.sh                   # uses $DATA_DIR or /data/bot-data
#   ./scripts/check_data_dir.sh /mnt/some/other   # explicit path
#   DATA_DIR=/data/bot-data ./scripts/check_data_dir.sh
#
# Exit codes:
#   0  — all checks passed (or path is a non-mount writable dir, with warning)
#   1  — path missing or unwritable
#   2  — subdir creation failed
#
# Read-only checks: this never writes outside the target tree, and
# only creates the four subdirs (idempotent mkdir -p). Safe to run
# repeatedly.

set -euo pipefail

TARGET="${1:-${DATA_DIR:-/data/bot-data}}"
SUBDIRS=(data runtime_logs runtime_state artifacts)

ok()    { printf '  [\033[32mOK\033[0m] %s\n' "$1"; }
warn()  { printf '  [\033[33m??\033[0m] %s\n' "$1"; }
fail()  { printf '  [\033[31mFAIL\033[0m] %s\n' "$1"; }

printf 'check_data_dir: target=%s\n' "$TARGET"

# 1. Path exists.
if [ ! -d "$TARGET" ]; then
    fail "$TARGET does not exist (or is not a directory)"
    exit 1
fi
ok "exists: $TARGET"

# 2. Is the path a mountpoint? Warn but don't fail — a bind-mount or
#    a regular dir is acceptable for dev, only suspicious in prod.
if command -v mountpoint >/dev/null 2>&1; then
    if mountpoint -q "$TARGET"; then
        ok "is a mountpoint"
    else
        warn "not a mountpoint — fine for dev, suspicious in prod"
    fi
else
    warn "mountpoint(1) not installed; skipping mount check"
fi

# 3. Writable by current user. Try an atomic create/remove rather
#    than [-w], which lies on some overlay filesystems.
PROBE="$TARGET/.check_data_dir.$$"
if ( : > "$PROBE" ) 2>/dev/null; then
    rm -f "$PROBE"
    ok "writable by $(id -un)"
else
    fail "not writable by $(id -un)"
    exit 1
fi

# 4. Subdirectories exist or can be created. mkdir -p is idempotent.
for sub in "${SUBDIRS[@]}"; do
    path="$TARGET/$sub"
    if [ -d "$path" ]; then
        ok "subdir present: $sub/"
    elif mkdir -p "$path" 2>/dev/null; then
        ok "subdir created: $sub/"
    else
        fail "could not create $path"
        exit 2
    fi
done

# 5. Free-space sanity. Soft warn at <2 GiB, fail at <128 MiB.
if command -v df >/dev/null 2>&1; then
    avail_kb="$(df -Pk "$TARGET" | awk 'NR==2 {print $4}')"
    avail_mib=$(( avail_kb / 1024 ))
    if [ "$avail_mib" -lt 128 ]; then
        fail "only ${avail_mib} MiB free on $TARGET (<128 MiB)"
        exit 1
    elif [ "$avail_mib" -lt 2048 ]; then
        warn "${avail_mib} MiB free on $TARGET (<2 GiB)"
    else
        ok "$(( avail_mib / 1024 )) GiB free"
    fi
fi

printf 'check_data_dir: all checks passed.\n'
