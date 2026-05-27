#!/usr/bin/env bash
# Tier-2 system-action: strip lines from the VM's `.env` that systemd's
# EnvironmentFile parser would reject, and restart the trader so the
# warnings stop bleeding into the journal.
#
# Background — 2026-05-27 incident
# --------------------------------
# A pre-`set-mobile-push-secrets`-era setup pasted a multi-line FCM
# service-account JSON blob directly into `.env`. systemd's
# EnvironmentFile parser only accepts single-line `KEY=VALUE` entries,
# so every continuation line (including the entire base64 PEM private
# key) lands as an `Ignoring invalid environment assignment '<line>':`
# warning in `journalctl -u ict-trader-live.service`. The new
# file-based credential path (`scripts/ops/set_mobile_push_secrets.sh`,
# 2026-05-23) writes the JSON to `${DATA_DIR}/fcm_service_account.json`
# and only stores `FCM_SERVICE_ACCOUNT_JSON_PATH=` in `.env`, but the
# old inline blob was never removed — so every service restart still
# echoes the key into the journal.
#
# Behaviour
# ---------
# A line is KEPT iff it satisfies systemd's EnvironmentFile contract:
#   1. blank (only whitespace), OR
#   2. a comment (first non-space char is `#`), OR
#   3. of the form `KEY=...` (optionally `export KEY=...`) where KEY
#      matches `^[A-Za-z_][A-Za-z0-9_]*$`.
# Every other line is one systemd is already ignoring, so removing it
# changes runtime behaviour zero ways and only stops the journal
# bleed. The original file is backed up to `${REPO_DIR}/.env.bak.<ts>`
# (mode 600) before the rewrite — recovery is `cp .env.bak.<ts> .env`.
#
# What lands in the audit log:
#   - counts only (kept / stripped / total), never the line content,
#   - the backup file path,
#   - the post-restart service state.
#
# What this does NOT touch:
#   - any conforming env entry, including ones with secrets,
#   - comments,
#   - any other file or service.
#
# Idempotent: a second invocation with nothing to strip exits 0 with
# `stripped=0` and does not bounce the service.
#
# Exit codes: 0 success (including a zero-strip no-op), 1 validation /
# write / restart failure.

set -euo pipefail

SCRIPT_NAME="scrub_env_noncompliant"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

ENV_FILE="${REPO_DIR}/.env"
SERVICE="${ENV_SERVICE:-ict-trader-live.service}"

# Services this action is allowed to restart. Same allowlist shape as
# set_env.sh — bounded blast radius, never the order path beyond the
# trader unit the operator already restarts via restart-bot-service.
ALLOWED_SERVICES="ict-claude-bridge.service ict-telegram-bot.service ict-web-api.service ict-hourly-snapshot.service ict-trader-live.service none"

case "${SERVICE}" in
    *.service|none) ;;
    *) SERVICE="${SERVICE}.service" ;;
esac
if ! printf '%s' " ${ALLOWED_SERVICES} " | grep -q " ${SERVICE} "; then
    log "ERROR: service '${SERVICE}' not in allowlist: ${ALLOWED_SERVICES}"
    record_audit "scrub-env-noncompliant" "error" \
        "{\"reason\": \"service not allowlisted\", \"service\": \"${SERVICE}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${ENV_FILE}" ]; then
    log "ERROR: ${ENV_FILE} does not exist; nothing to scrub."
    record_audit "scrub-env-noncompliant" "error" \
        "{\"reason\": \"env file missing\", \"path\": \"${ENV_FILE}\"}" >/dev/null || true
    exit 1
fi

BACKUP_TS="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_FILE="${ENV_FILE}.bak.${BACKUP_TS}"

# Filter in Python so the line-classification logic is easy to read and
# test. Writes the cleaned file to a sibling .tmp under a strict umask
# (mode 600 from creation) and only renames on top of `.env` after the
# backup is in place. Counts go to stdout so the shell can log + audit.
counts="$(
ENV_FILE="${ENV_FILE}" BACKUP_FILE="${BACKUP_FILE}" \
    /usr/bin/python3 - <<'PY'
import os
import re
import pathlib
import shutil
import stat

src = pathlib.Path(os.environ["ENV_FILE"])
backup = pathlib.Path(os.environ["BACKUP_FILE"])

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

def is_compliant(line: str) -> bool:
    stripped = line.lstrip()
    if not stripped.strip():
        return True
    if stripped.startswith("#"):
        return True
    if "=" not in stripped:
        return False
    head = stripped.split("=", 1)[0]
    if head.startswith("export "):
        head = head[len("export "):]
    head = head.strip()
    return bool(KEY_RE.match(head))

lines = src.read_text().splitlines()
total = len(lines)
kept = [ln for ln in lines if is_compliant(ln)]
stripped = total - len(kept)

if stripped == 0:
    # No-op: leave the file untouched, no backup, no rewrite.
    print(f"kept={len(kept)}\nstripped=0\ntotal={total}\nbackup=none")
else:
    # Atomic write via mode-600 tmp file, with the backup landing first.
    shutil.copy2(src, backup)
    backup.chmod(0o600)
    tmp = src.with_suffix(src.suffix + ".tmp")
    old_umask = os.umask(0o177)
    try:
        tmp.write_text("\n".join(kept) + "\n")
    finally:
        os.umask(old_umask)
    tmp.chmod(0o600)
    os.replace(tmp, src)
    print(f"kept={len(kept)}\nstripped={stripped}\ntotal={total}\nbackup={backup}")
PY
)"

# shellcheck disable=SC2034
eval "${counts}"

log "scrub complete: total=${total}, kept=${kept}, stripped=${stripped}, backup=${backup}"

if [ "${stripped}" = "0" ]; then
    log "Nothing to strip; service restart skipped."
    record_audit "scrub-env-noncompliant" "ok" \
        "{\"stripped\": 0, \"kept\": ${kept}, \"total\": ${total}, \"restart\": \"skipped\"}" >/dev/null || true
    exit 0
fi

if [ "${SERVICE}" = "none" ]; then
    log "service=none — env scrubbed, no restart requested."
    record_audit "scrub-env-noncompliant" "ok" \
        "{\"stripped\": ${stripped}, \"kept\": ${kept}, \"total\": ${total}, \"backup\": \"${backup}\", \"restart\": \"none\"}" >/dev/null || true
    exit 0
fi

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required."
    record_audit "scrub-env-noncompliant" "failed" \
        "{\"reason\": \"sudo unavailable\", \"backup\": \"${backup}\"}" >/dev/null || true
    exit 1
fi

log "Restarting ${SERVICE} to pick up the cleaned env..."
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
    record_audit "scrub-env-noncompliant" "ok" \
        "{\"stripped\": ${stripped}, \"kept\": ${kept}, \"total\": ${total}, \"backup\": \"${backup}\", \"service\": \"${SERVICE}\", \"unit\": \"active\"}" >/dev/null || true
    exit 0
else
    record_audit "scrub-env-noncompliant" "failed" \
        "{\"stripped\": ${stripped}, \"kept\": ${kept}, \"total\": ${total}, \"backup\": \"${backup}\", \"service\": \"${SERVICE}\", \"unit\": \"${post_state}\"}" >/dev/null || true
    log "ERROR: ${SERVICE} did not return to 'active' within 30 s."
    exit 1
fi
