#!/usr/bin/env bash
# Tier-2 system-action (VM half): ROTATE DIAG_READ_TOKEN on the live VM.
#
# ⚠️ THIS SCRIPT NEVER PRINTS THE TOKEN. Not to stdout, not to stderr, not as a
# fingerprint. Its output is posted verbatim to a GitHub issue comment and the
# repo is PUBLIC. Before 2026-09-27 this script (a) REUSED any existing token —
# so a "rotation" re-installed the leaked value — and (b) printed
# `DIAG_READ_TOKEN=<value>` to stdout, which the system-actions workflow posts
# to the request issue. Both are gone; tests/ops/test_init_diag_token_rotation.py
# pins that neither comes back.
#
# The VALUE is originated on the GitHub runner (scripts/ops/rotate_diag_token_runner.sh,
# `openssl rand -hex 32`, `::add-mask::`ed at once) and arrives here on STDIN —
# never argv, never an env var in the ssh command line, so it is not visible in
# /proc/<pid>/cmdline on either side. The runner verifies (new → 200, old → 401),
# updates the Actions secret, and calls `rollback` here if anything fails.
#
# Modes (first argument):
#   rotate    (default) read the new token from stdin, back up every file that
#             holds DIAG_READ_TOKEN, write the new value to ALL of them, restart
#             ict-web-api.service, wait for active.
#   rollback  restore the backups taken by `rotate`, restart, wait for active.
#   commit    delete the backups (they hold the retired token).
#
# WHERE THE WEB API READS THE TOKEN (deploy/ict-web-api.service):
#   EnvironmentFile=/etc/ict-trader/web-api.env
#   EnvironmentFile=-/home/ubuntu/ict-trading-bot/.env
# systemd applies EnvironmentFile= in order and a later file OVERRIDES an
# earlier one, so `.env` WINS whenever it carries the key. Writing only
# web-api.env (what set-diag-token does) is shadowed by a stale `.env` line.
# This script writes web-api.env, AND .env when .env already carries the key
# (never adding a new shadowing line), plus the legacy
# /etc/ict-trading-bot/diag_token that scripts/deploy_pull_restart.sh falls
# back to — so every VM-side reader agrees.
#
# Exit codes: 0 ok · 2 bad input / refused · 3 no backups to roll back ·
#             4 unit not active after restart · 1 other failure.

# ROTATION-PROTOCOL: stdin-v1
# ^ rotate_diag_token_runner.sh refuses to call this script unless the VM's copy
#   carries that exact line: the pre-2026-09-27 script ignores stdin and PRINTS
#   the token, so calling a stale copy would publish the value on the issue.

set -euo pipefail

SCRIPT_NAME="init_diag_token"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

MODE="${1:-rotate}"

# Paths are overridable ONLY so the test can point them at a temp dir.
ENV_FILE="${DIAG_ENV_FILE:-/home/ubuntu/ict-trading-bot/.env}"
SYSTEM_ENV="${DIAG_SYSTEM_ENV:-/etc/ict-trader/web-api.env}"
LEGACY_TOKEN_FILE="${DIAG_LEGACY_TOKEN_FILE:-/etc/ict-trading-bot/diag_token}"
UNIT="ict-web-api.service"
BAK_SUFFIX=".diag-rotate.bak"
ABSENT_SUFFIX=".diag-rotate.absent"

require_systemctl

# ── privilege helper ───────────────────────────────────────────────────────
# Files under /etc are root-owned; .env is ubuntu-owned and must STAY
# ubuntu-owned (the trader reads it as ubuntu). So sudo is chosen PER PATH:
# only when the file (or, if absent, its directory) is not writable by us.
if [ "$(id -u)" -eq 0 ]; then
    SUDO=()
else
    SUDO=(sudo -n)
fi
SYSTEMCTL=("${SUDO[@]}" systemctl)

# as_for <path> <cmd...> — run <cmd> directly if <path> is ours, else via sudo.
as_for() {
    local p="$1"; shift
    local probe="${p}"
    [ -e "${p}" ] || probe="$(dirname "${p}")"
    if [ -w "${probe}" ] && { [ ! -e "${p}" ] || [ -r "${p}" ]; }; then
        "$@"
    else
        "${SUDO[@]}" "$@"
    fi
}

file_exists() { as_for "$1" test -f "$1"; }

# Replace-or-append DIAG_READ_TOKEN in a KEY=VALUE env file. The value travels
# through the printf BUILTIN into a 600 temp file, then `cp` onto the target —
# cp onto an existing file keeps the target's owner and mode.
write_env_key() {
    local target="$1" tmp
    tmp="$(mktemp)"
    chmod 600 "${tmp}"
    as_for "${target}" cat "${target}" 2>/dev/null | grep -v '^DIAG_READ_TOKEN=' > "${tmp}" || true
    printf 'DIAG_READ_TOKEN=%s\n' "${NEW_TOKEN}" >> "${tmp}"
    as_for "${target}" cp "${tmp}" "${target}"
    rm -f "${tmp}"
}

write_plain_token() {
    local target="$1" tmp
    tmp="$(mktemp)"
    chmod 600 "${tmp}"
    printf '%s\n' "${NEW_TOKEN}" > "${tmp}"
    as_for "${target}" cp "${tmp}" "${target}"
    rm -f "${tmp}"
}

backup() {
    local f="$1"
    if file_exists "${f}"; then
        as_for "${f}" cp -a "${f}" "${f}${BAK_SUFFIX}"
        as_for "${f}" rm -f "${f}${ABSENT_SUFFIX}"
    elif [ "${f}" = "${ENV_FILE}" ]; then
        # Only .env is CREATED when absent, so only it needs an absent-marker
        # for rollback to undo the creation. The /etc files are written only
        # when they already exist.
        as_for "${f}" touch "${f}${ABSENT_SUFFIX}"
    fi
}

restart_and_wait() {
    local pre post deadline
    pre="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>/dev/null || echo unknown)"
    echo "unit_pre_state=${pre}"
    "${SYSTEMCTL[@]}" restart "${UNIT}"
    deadline=$(( $(date +%s) + ${DIAG_RESTART_TIMEOUT_S:-30} ))
    post="unknown"
    while :; do
        post="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>/dev/null || echo unknown)"
        [ "${post}" = "active" ] && break
        [ "$(date +%s)" -ge "${deadline}" ] && break
        sleep 2
    done
    echo "unit_post_state=${post}"
    [ "${post}" = "active" ]
}

current_token_equals_new() {
    # True when any existing file already holds exactly the incoming value —
    # a "rotation" to the same value is a no-op and must be refused, not
    # reported as done (the 2026-08-18 set-diag-token failure).
    local f v
    for f in "${ENV_FILE}" "${SYSTEM_ENV}"; do
        v="$(as_for "${f}" grep -m1 '^DIAG_READ_TOKEN=' "${f}" 2>/dev/null | cut -d= -f2- || true)"
        if [ -n "${v}" ] && [ "${v}" = "${NEW_TOKEN}" ]; then return 0; fi
    done
    v="$(as_for "${LEGACY_TOKEN_FILE}" cat "${LEGACY_TOKEN_FILE}" 2>/dev/null | head -n1 || true)"
    [ -n "${v}" ] && [ "${v}" = "${NEW_TOKEN}" ]
}

case "${MODE}" in
  rotate)
    NEW_TOKEN=""
    IFS= read -r NEW_TOKEN || true
    NEW_TOKEN="${NEW_TOKEN//[[:space:]]/}"
    if ! [[ "${NEW_TOKEN}" =~ ^[0-9a-f]{64}$ ]]; then
        echo "refused=bad_input (expected a 64-hex token on stdin; got none or a malformed one)"
        exit 2
    fi
    if current_token_equals_new; then
        echo "refused=no_op (the incoming token equals the one already installed; nothing would rotate)"
        exit 2
    fi

    # Back up EVERYTHING before writing ANYTHING, so rollback is total.
    backup "${ENV_FILE}"
    backup "${SYSTEM_ENV}"
    backup "${LEGACY_TOKEN_FILE}"
    echo "backups=taken"

    # Write every file that the web API reads the key FROM — and never ADD a
    # line to .env that was not there: .env is loaded last, so a new line
    # there would silently shadow web-api.env for every later writer
    # (set-diag-token and deploy_diag.sh write only web-api.env).
    wrote_env=0
    if file_exists "${SYSTEM_ENV}${BAK_SUFFIX}"; then
        write_env_key "${SYSTEM_ENV}"
        echo "written=${SYSTEM_ENV}"
        wrote_env=1
    else
        echo "skipped=${SYSTEM_ENV} (absent)"
    fi
    if as_for "${ENV_FILE}" grep -q '^DIAG_READ_TOKEN=' "${ENV_FILE}" 2>/dev/null \
       || [ "${wrote_env}" -eq 0 ]; then
        write_env_key "${ENV_FILE}"
        echo "written=${ENV_FILE}"
    else
        echo "skipped=${ENV_FILE} (does not carry the key; ${SYSTEM_ENV} is authoritative)"
    fi
    if file_exists "${LEGACY_TOKEN_FILE}${BAK_SUFFIX}"; then
        write_plain_token "${LEGACY_TOKEN_FILE}"
        echo "written=${LEGACY_TOKEN_FILE}"
    else
        echo "skipped=${LEGACY_TOKEN_FILE} (absent)"
    fi
    unset NEW_TOKEN

    if ! restart_and_wait; then
        echo "error=${UNIT} not active after restart"
        exit 4
    fi
    record_audit "init-diag-token" "rotated_pending_verify" "{\"unit\": \"${UNIT}\"}" >/dev/null 2>&1 || true
    echo "vm_rotate=ok"
    ;;

  rollback)
    restored=0
    for f in "${ENV_FILE}" "${SYSTEM_ENV}" "${LEGACY_TOKEN_FILE}"; do
        if file_exists "${f}${BAK_SUFFIX}"; then
            as_for "${f}" cp -a "${f}${BAK_SUFFIX}" "${f}"
            as_for "${f}" rm -f "${f}${BAK_SUFFIX}"
            echo "restored=${f}"
            restored=$((restored + 1))
        elif file_exists "${f}${ABSENT_SUFFIX}"; then
            as_for "${f}" rm -f "${f}" "${f}${ABSENT_SUFFIX}"
            echo "removed=${f} (did not exist before rotate)"
            restored=$((restored + 1))
        fi
    done
    if [ "${restored}" -eq 0 ]; then
        echo "error=no backups found — nothing to roll back"
        exit 3
    fi
    if ! restart_and_wait; then
        echo "error=${UNIT} not active after rollback restart"
        exit 4
    fi
    record_audit "init-diag-token" "rolled_back" "{\"unit\": \"${UNIT}\"}" >/dev/null 2>&1 || true
    echo "vm_rollback=ok"
    ;;

  commit)
    for f in "${ENV_FILE}" "${SYSTEM_ENV}" "${LEGACY_TOKEN_FILE}"; do
        as_for "${f}" rm -f "${f}${BAK_SUFFIX}" "${f}${ABSENT_SUFFIX}"
    done
    record_audit "init-diag-token" "rotated" "{\"unit\": \"${UNIT}\"}" >/dev/null 2>&1 || true
    echo "vm_commit=ok (backups holding the retired token deleted)"
    ;;

  *)
    echo "refused=unknown_mode '${MODE}' (rotate|rollback|commit)"
    exit 2
    ;;
esac
