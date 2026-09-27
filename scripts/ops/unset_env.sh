#!/usr/bin/env bash
# Tier-2 system-action: REMOVE one key's line(s) from an env file and
# optionally restart the service that consumes it.
#
# WHY THIS EXISTS (2026-09-25). `set-env` can create or update a key but
# cannot delete one — and, by design, it never can: its empty-value guard
# (added after the 2026-08-01 TELEGRAM_BOT_TOKEN incident, where an
# unmapped secret resolved to "" and the write blanked the key, restarting
# the trader straight into a startup-validation crashloop) makes an
# unintentional blank impossible on the write side by REFUSING an empty
# resolved value outright. That guard is correct and is not being loosened
# here — issue #12938 hit exactly that refusal trying to use `set-env` to
# retire `ARBITRATION_FANOUT_MODE` / `ARBITRATION_FANOUT_ACCOUNTS` (both
# retired by E35, #12924; the trader already ignores them and only logs a
# one-time warning — see `src/runtime/arbitration_fanout_soak.py`,
# `_warn_retired_env_once`). No allowlisted action could remove a key at
# all, so this is a distinct action with its own, narrower safety property:
# not "never write empty", but "never delete a key nobody has reviewed".
#
# Dispatched by the system-actions workflow (issue body:
#   action: unset-env
#   env_key: ARBITRATION_FANOUT_MODE
#   service: ict-trader-live      (OPTIONAL; default ict-trader-live; 'none' skips restart)
#   env_file: shared              (OPTIONAL; shared | web-api, default shared)
#   reason: <why>
# ). The workflow threads ENV_KEY / ENV_SERVICE / ENV_FILE_TARGET — the same
# variables `set-env` / `get-env` already carry; no new issue-body fields.
#
# SAFETY: env_key must be BOTH charset-valid (`^[A-Z][A-Z0-9_]*$`, same guard
# `set-env` uses) AND present on the fixed `RETIRABLE_KEYS` allowlist below.
# `set-env` can write ANY charset-valid key because a write only ever adds
# or replaces a value the caller supplied; a DELETE has no such bound — a
# freeform key here would let an issue body remove a credential and
# crashloop the trader, which is the exact failure class the 2026-08-01
# empty-value guard exists to prevent on the write side. Adding a key to
# `RETIRABLE_KEYS` is a reviewed one-line edit, the same doctrine
# `get_env.py::ALLOWED_KEYS` uses for the read half — never a caller-chosen
# freeform key.
#
# The env file is backed up first (mode 600, timestamped
# `<file>.bak.<UTC-ts>`, same convention as `scrub_env_noncompliant.sh`)
# before any mutation. Only exact `KEY=` (or `export KEY=`) lines are
# removed; every other line — including comments, blanks, and a
# similarly-prefixed key (`KEY_OTHER=`) — is preserved byte-for-byte.
# Idempotent: a key that is not present is a clean no-op — no backup, no
# rewrite, no restart.
#
# Values are NEVER logged or recorded in the audit JSON — only the key,
# whether it was present, and the restart outcome.
#
# Exit codes: 0 success (including the not-present no-op), 1 validation /
# write / restart failure.

set -euo pipefail

SCRIPT_NAME="unset_env"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

KEY="${ENV_KEY:-}"
SERVICE="${ENV_SERVICE:-}"
TARGET="${ENV_FILE_TARGET:-}"

# ── FIXED ALLOWLIST OF RETIRABLE KEYS ──────────────────────────────────────
# Deliberately narrower than get_env.py::ALLOWED_KEYS (which many keys are
# safe to READ) — this is the set of keys someone has reviewed as safe to
# DELETE. Start with exactly the two E35 (#12924) retired; extending this
# list is a reviewed code change, never a caller-supplied value.
RETIRABLE_KEYS="ARBITRATION_FANOUT_MODE ARBITRATION_FANOUT_ACCOUNTS"

# Services this action is allowed to restart. Same bounded blast radius as
# set_env.sh / scrub_env_noncompliant.sh — never the order path beyond the
# trader unit the operator already restarts via restart-bot-service.
ALLOWED_SERVICES="ict-claude-bridge.service ict-telegram-bot.service ict-web-api.service ict-hourly-snapshot.service ict-trader-live.service none"

if [ -z "${KEY// }" ]; then
    log "ERROR: unset-env requires 'env_key'."
    record_audit "unset-env" "error" '{"reason": "missing env_key"}' >/dev/null || true
    exit 1
fi

# Env-var name charset guard (also defends the removal match below).
if ! [[ "${KEY}" =~ ^[A-Z][A-Z0-9_]*$ ]]; then
    log "ERROR: env_key '${KEY}' invalid (allowed: ^[A-Z][A-Z0-9_]*$)."
    record_audit "unset-env" "error" '{"reason": "invalid env_key charset"}' >/dev/null || true
    exit 1
fi

# THE control: a charset-valid key is not enough — it must also be on the
# fixed retirable-keys allowlist. A silent fallback to "anything charset-
# valid" would make this an arbitrary-key deleter on the live VM.
if ! printf '%s' " ${RETIRABLE_KEYS} " | grep -q " ${KEY} "; then
    log "ERROR: env_key '${KEY}' is not on the retirable-keys allowlist: ${RETIRABLE_KEYS}"
    record_audit "unset-env" "error" \
        "{\"reason\": \"env_key not allowlisted\", \"env_key\": \"${KEY}\"}" >/dev/null || true
    exit 1
fi

# ── WHICH FILE (mirrors set_env.sh exactly — targets are symbolic names,
# never paths; an unknown target is a hard error, never a fallback). ───────
case "${TARGET}" in
    ""|shared)  ENV_FILE="${REPO_DIR}/.env"; TARGET="shared" ;;
    web-api)    ENV_FILE="/etc/ict-trader/web-api.env" ;;
    *)
        log "ERROR: env_file '${TARGET}' not in allowlist: shared web-api"
        record_audit "unset-env" "error" \
            "{\"reason\": \"env_file not allowlisted\", \"env_file\": \"${TARGET}\"}" >/dev/null || true
        exit 1
        ;;
esac

# Normalise + validate the service (default: the trader, since the
# initial retirable keys are trader-consumed; 'none' skips the restart).
SERVICE="${SERVICE:-ict-trader-live.service}"
case "${SERVICE}" in
    *.service|none) ;;
    *) SERVICE="${SERVICE}.service" ;;
esac
if ! printf '%s' " ${ALLOWED_SERVICES} " | grep -q " ${SERVICE} "; then
    log "ERROR: service '${SERVICE}' not in allowlist: ${ALLOWED_SERVICES}"
    record_audit "unset-env" "error" "{\"reason\": \"service not allowlisted\", \"service\": \"${SERVICE}\"}" >/dev/null || true
    exit 1
fi

# A non-shared target is root-owned (same precedent as set_env.sh), so the
# read/write hop through sudo.
PRIV=()
if [ "${TARGET}" != "shared" ]; then
    if [ "$(id -u)" -eq 0 ]; then
        PRIV=()
    elif sudo -n true 2>/dev/null; then
        PRIV=(sudo)
    else
        log "ERROR: env_file '${TARGET}' is root-owned and passwordless sudo is unavailable."
        record_audit "unset-env" "failed" \
            "{\"reason\": \"sudo unavailable for env_file\", \"env_file\": \"${TARGET}\"}" >/dev/null || true
        exit 1
    fi
fi
if ! "${PRIV[@]}" test -f "${ENV_FILE}"; then
    log "ERROR: ${ENV_FILE} does not exist; nothing to unset."
    record_audit "unset-env" "error" \
        "{\"reason\": \"env file missing\", \"env_file\": \"${TARGET}\"}" >/dev/null || true
    exit 1
fi

# ── REMOVE, backup-first, atomic write ─────────────────────────────────────
TMP_CUR="$(mktemp)"; TMP_NEW="$(mktemp)"
chmod 600 "${TMP_CUR}" "${TMP_NEW}"
trap 'rm -f "${TMP_CUR}" "${TMP_NEW}"' EXIT

"${PRIV[@]}" cat "${ENV_FILE}" > "${TMP_CUR}"

# Filter in Python so the removal match is easy to read and test — same
# split point set_env.sh uses for its upsert. Only an exact `KEY=` (or
# `export KEY=`) line is dropped; a longer key sharing the same prefix
# (`KEY_OTHER=`) or a commented-out line is left untouched.
result="$(
KEY="${KEY}" ENV_FILE="${TMP_CUR}" OUT_FILE="${TMP_NEW}" /usr/bin/python3 - <<'PY'
import os, pathlib
key = os.environ["KEY"]
path = pathlib.Path(os.environ["ENV_FILE"])
lines = path.read_text().splitlines()
out = []
removed = 0
for ln in lines:
    stripped = ln.lstrip()
    bare = stripped[len("export "):] if stripped.startswith("export ") else stripped
    if (
        not stripped.startswith("#")
        and "=" in bare
        and bare.split("=", 1)[0].strip() == key
    ):
        removed += 1
        continue
    out.append(ln)
if removed:
    text = "\n".join(out)
    pathlib.Path(os.environ["OUT_FILE"]).write_text(text + "\n" if text else "")
print(f"removed={removed}")
PY
)"
eval "${result}"

if [ "${removed}" = "0" ]; then
    log "env_key ${KEY} not present in ${ENV_FILE} (env_file=${TARGET}) — clean no-op, no backup, no restart."
    record_audit "unset-env" "ok" \
        "{\"key\": \"${KEY}\", \"env_file\": \"${TARGET}\", \"outcome\": \"absent\", \"restart\": \"skipped\"}" >/dev/null || true
    exit 0
fi

# Backup BEFORE the write — same convention as scrub_env_noncompliant.sh
# (mode 600, timestamped, alongside the target so recovery is a plain cp).
BACKUP_TS="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_FILE="${ENV_FILE}.bak.${BACKUP_TS}"
"${PRIV[@]}" tee "${BACKUP_FILE}" < "${TMP_CUR}" >/dev/null
"${PRIV[@]}" chmod 600 "${BACKUP_FILE}"

# `tee` writes THROUGH the existing inode, preserving the target's owner
# and mode — same reasoning set_env.sh documents for its own write.
"${PRIV[@]}" tee "${ENV_FILE}" < "${TMP_NEW}" >/dev/null

log "env_key ${KEY} removed (${removed} line(s)) from ${ENV_FILE} (env_file=${TARGET}); backup at ${BACKUP_FILE}."

# Confirm the key no longer reads back.
if "${PRIV[@]}" grep -qE "^[[:space:]]*(export )?${KEY}=" "${ENV_FILE}"; then
    log "ERROR: post-write read-back still finds ${KEY} in ${ENV_FILE}."
    record_audit "unset-env" "failed" \
        "{\"reason\": \"readback found key after removal\", \"key\": \"${KEY}\", \"backup\": \"${BACKUP_FILE}\"}" >/dev/null || true
    exit 1
fi

if [ "${SERVICE}" = "none" ]; then
    log "service=none — key removed, no restart requested."
    record_audit "unset-env" "ok" \
        "{\"key\": \"${KEY}\", \"env_file\": \"${TARGET}\", \"outcome\": \"removed\", \"removed_lines\": ${removed}, \"backup\": \"${BACKUP_FILE}\", \"service\": \"none\"}" >/dev/null || true
    exit 0
fi

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required."
    record_audit "unset-env" "failed" \
        "{\"reason\": \"sudo unavailable\", \"key\": \"${KEY}\", \"backup\": \"${BACKUP_FILE}\"}" >/dev/null || true
    exit 1
fi

log "Restarting ${SERVICE} to pick up the removal..."
"${SYSTEMCTL[@]}" restart "${SERVICE}"

deadline=$(( $(date +%s) + 30 ))
post_state="unknown"
while [ "$(date +%s)" -lt "${deadline}" ]; do
    post_state="$("${SYSTEMCTL[@]}" is-active "${SERVICE}" 2>/dev/null || echo "unknown")"
    [ "${post_state}" = "active" ] && break
    sleep 2
done
log "Post-restart ${SERVICE} state: ${post_state}"

if [ "${post_state}" = "active" ]; then
    record_audit "unset-env" "ok" \
        "{\"key\": \"${KEY}\", \"env_file\": \"${TARGET}\", \"outcome\": \"removed\", \"removed_lines\": ${removed}, \"backup\": \"${BACKUP_FILE}\", \"service\": \"${SERVICE}\", \"unit\": \"active\"}" >/dev/null || true
    exit 0
else
    record_audit "unset-env" "failed" \
        "{\"key\": \"${KEY}\", \"env_file\": \"${TARGET}\", \"outcome\": \"removed\", \"removed_lines\": ${removed}, \"backup\": \"${BACKUP_FILE}\", \"service\": \"${SERVICE}\", \"unit\": \"${post_state}\"}" >/dev/null || true
    log "ERROR: ${SERVICE} did not return to 'active' within 30 s."
    exit 1
fi
