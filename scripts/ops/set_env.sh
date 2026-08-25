#!/usr/bin/env bash
# Tier-2 system-action: set / update one env var in the VM's .env and
# restart the service that consumes it.
#
# This is the autonomous "Claude configures the VM environment" path:
# Claude owns the env, edits it, and applies it — no operator hand-off.
# Idempotent single-key upsert (preserves every other line + comments
# byte-for-byte); the targeted service is restarted so systemd re-reads
# its EnvironmentFile.
#
# Dispatched by the system-actions workflow (issue body:
#   action: set-env
#   env_key: TELEGRAM_CLAUDE_THREAD_ID
#   env_value: 42                 (omit for secret-backed keys; see below)
#   service: ict-claude-bridge
#   env_file: shared              (OPTIONAL; shared | web-api, default shared)
#   reason: <why>
# ). The workflow threads ENV_KEY / ENV_VALUE / ENV_SERVICE / ENV_FILE_TARGET.
#
# `env_file:` (2026-08-25) picks WHICH env file to write. It defaults to
# `shared` — the repo `.env` this action has always written — so every existing
# caller is byte-identical. Use `web-api` for a key that must be scoped to the
# web-api process alone, because `ict-web-api.service` loads the shared `.env`
# too and a key written there reaches the TRADER as well. See the allowlist
# below for the worked example (IB_MD_CLIENT_ID, where a shared-file write
# would have collided two IB market-data sockets on the same clientId). For keys whose
# value is a secret (e.g. TELEGRAM_CLAUDE_BOT_TOKEN), the workflow supplies
# ENV_VALUE from a GitHub Actions secret of the same name, so the value
# never appears in the (public) issue body or run log.
#
# Values are NEVER logged or recorded in the audit JSON — only the key,
# service, and whether the key was created vs updated.
#
# Exit codes: 0 success, 1 validation / write / restart failure.

set -euo pipefail

SCRIPT_NAME="set_env"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

KEY="${ENV_KEY:-}"
VALUE="${ENV_VALUE:-}"
SERVICE="${ENV_SERVICE:-}"
TARGET="${ENV_FILE_TARGET:-}"

# ── WHICH FILE (2026-08-25, BL-20260825-SET-ENV-CANNOT-TARGET-A-SERVICE-SCOPED-ENV-FILE)
#
# Until now this action chose which SERVICE to restart but not which FILE to
# write, and always wrote the shared repo `.env`. That is exactly the wrong
# shape for a key that must DIFFER between two services which share that file
# — and `ict-web-api.service` shares it with `ict-trader-live.service` by
# design (it loads `/etc/ict-trader/web-api.env` first, then the repo `.env`
# so operator overrides stay aligned between writer and reader).
#
# The motivating key is `IB_MD_CLIENT_ID`. Nothing puts it in a settings dict
# except `routers/candles.py` (web-api only), so the TRADER reads it from the
# environment and today falls through to `exec_client_id + 1` = 498. Writing
# `600` into the SHARED file moves the trader's market-data socket 498 -> 600,
# where it collides with the web-api's own 600 across two processes: IB error
# 326, starving the MES/MGC/MHG candles the reservation exists to protect.
# A shared-file write would have been WORSE than doing nothing.
#
# TARGETS ARE SYMBOLIC NAMES, NEVER PATHS. The issue body is untrusted input;
# accepting a path here would make this action an arbitrary-file writer on the
# live VM. Adding a target is a reviewed one-line edit, the same doctrine
# `get_env.py::ALLOWED_KEYS` uses for the read half.
#
# ⚠️ AN UNKNOWN TARGET IS A HARD ERROR, NEVER A FALLBACK TO `shared`. A typo
# that silently wrote the shared file would reintroduce the exact collision
# this parameter exists to prevent, and would report success doing it.
case "${TARGET}" in
    ""|shared)  ENV_FILE="${REPO_DIR}/.env"; TARGET="shared" ;;
    web-api)    ENV_FILE="/etc/ict-trader/web-api.env" ;;
    *)
        log "ERROR: env_file '${TARGET}' not in allowlist: shared web-api"
        record_audit "set-env" "error" \
            "{\"reason\": \"env_file not allowlisted\", \"env_file\": \"${TARGET}\"}" >/dev/null || true
        exit 1
        ;;
esac

# Services this action is allowed to restart. Keeps the blast radius
# bounded to the bot units; never the order path beyond the trader unit
# the operator already restarts via restart-bot-service.
ALLOWED_SERVICES="ict-claude-bridge.service ict-telegram-bot.service ict-web-api.service ict-hourly-snapshot.service ict-trader-live.service none"

if [ -z "${KEY// }" ]; then
    log "ERROR: set-env requires 'env_key'."
    record_audit "set-env" "error" '{"reason": "missing env_key"}' >/dev/null || true
    exit 1
fi

# Env-var name charset guard (also defends the sed/grep edit below).
if ! [[ "${KEY}" =~ ^[A-Z][A-Z0-9_]*$ ]]; then
    log "ERROR: env_key '${KEY}' invalid (allowed: ^[A-Z][A-Z0-9_]*$)."
    record_audit "set-env" "error" '{"reason": "invalid env_key charset"}' >/dev/null || true
    exit 1
fi

# Value must be single-line (no newlines / control chars that would
# corrupt the KEY=VALUE line). Empty value is allowed (clears a setting).
if printf '%s' "${VALUE}" | grep -q '[[:cntrl:]]'; then
    log "ERROR: env_value contains control characters / newlines."
    record_audit "set-env" "error" "{\"reason\": \"invalid env_value\", \"key\": \"${KEY}\"}" >/dev/null || true
    exit 1
fi

# Normalise + validate the service (default: restart the claude bridge,
# the most common target; 'none' skips the restart for env-only changes).
SERVICE="${SERVICE:-ict-claude-bridge.service}"
case "${SERVICE}" in
    *.service|none) ;;
    *) SERVICE="${SERVICE}.service" ;;
esac
if ! printf '%s' " ${ALLOWED_SERVICES} " | grep -q " ${SERVICE} "; then
    log "ERROR: service '${SERVICE}' not in allowlist: ${ALLOWED_SERVICES}"
    record_audit "set-env" "error" "{\"reason\": \"service not allowlisted\", \"service\": \"${SERVICE}\"}" >/dev/null || true
    exit 1
fi

# A non-shared target is root-owned (/etc/ict-trader/web-api.env is root:root
# — the sudo-read precedent is flatten_alpaca_position_action.sh), so the
# read/write hop through sudo. PRIV is empty for the shared repo `.env`, which
# keeps that path byte-identical to its pre-2026-08-25 behaviour.
PRIV=()
if [ "${TARGET}" = "shared" ]; then
    touch "${ENV_FILE}"
else
    if [ "$(id -u)" -eq 0 ]; then
        PRIV=()
    elif sudo -n true 2>/dev/null; then
        PRIV=(sudo)
    else
        log "ERROR: env_file '${TARGET}' is root-owned and passwordless sudo is unavailable."
        record_audit "set-env" "failed" \
            "{\"reason\": \"sudo unavailable for env_file\", \"env_file\": \"${TARGET}\"}" >/dev/null || true
        exit 1
    fi
    # DELIBERATELY does not create a missing root-owned env file. Guessing the
    # owner/mode of a file that may hold credentials is not this action's job,
    # and a silently-created file would be loaded by systemd with whatever
    # permissions we happened to pick.
    if ! "${PRIV[@]}" test -f "${ENV_FILE}"; then
        log "ERROR: ${ENV_FILE} does not exist; refusing to create it."
        record_audit "set-env" "failed" \
            "{\"reason\": \"env_file absent\", \"env_file\": \"${TARGET}\"}" >/dev/null || true
        exit 1
    fi
fi

# Idempotent single-key upsert via Python (handles quoting, preserves the
# rest of the file). Returns "created" or "updated" on stdout.
#
# Staged through two 600-mode temp files so the heredoc keeps stdin AND the
# privileged read/write stay one-liners. The write lands via `tee`, which
# writes THROUGH the existing inode and so preserves the target's owner and
# mode — `mv` or a redirect would replace them with the invoking user's.
TMP_CUR="$(mktemp)"; TMP_NEW="$(mktemp)"
chmod 600 "${TMP_CUR}" "${TMP_NEW}"
trap 'rm -f "${TMP_CUR}" "${TMP_NEW}"' EXIT

"${PRIV[@]}" cat "${ENV_FILE}" > "${TMP_CUR}" 2>/dev/null || : > "${TMP_CUR}"

op="$(
KEY="${KEY}" VALUE="${VALUE}" ENV_FILE="${TMP_CUR}" OUT_FILE="${TMP_NEW}" /usr/bin/python3 - <<'PY'
import os, pathlib
key, value = os.environ["KEY"], os.environ["VALUE"]
path = pathlib.Path(os.environ["ENV_FILE"])
lines = path.read_text().splitlines() if path.exists() else []
new_line = f"{key}={value}"
found = False
out = []
for ln in lines:
    stripped = ln.lstrip()
    # Match KEY= possibly preceded by 'export ' (ignore commented lines).
    bare = stripped[len("export "):] if stripped.startswith("export ") else stripped
    if bare.split("=", 1)[0].strip() == key and not stripped.startswith("#"):
        out.append(new_line)
        found = True
    else:
        out.append(ln)
if not found:
    out.append(new_line)
pathlib.Path(os.environ["OUT_FILE"]).write_text("\n".join(out) + "\n")
print("updated" if found else "created")
PY
)"

"${PRIV[@]}" tee "${ENV_FILE}" < "${TMP_NEW}" >/dev/null

log "env_key ${KEY} ${op} in ${ENV_FILE} (env_file=${TARGET})."

# Confirm the key reads back (value not echoed).
if ! "${PRIV[@]}" grep -qE "^(export )?${KEY}=" "${ENV_FILE}"; then
    log "ERROR: post-write read-back for ${KEY} failed."
    record_audit "set-env" "failed" "{\"reason\": \"readback failed\", \"key\": \"${KEY}\"}" >/dev/null || true
    exit 1
fi

if [ "${SERVICE}" = "none" ]; then
    log "service=none — env written, no restart requested."
    record_audit "set-env" "ok" "{\"key\": \"${KEY}\", \"op\": \"${op}\", \"env_file\": \"${TARGET}\", \"service\": \"none\"}" >/dev/null || true
    exit 0
fi

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required."
    record_audit "set-env" "failed" "{\"reason\": \"sudo unavailable\", \"key\": \"${KEY}\"}" >/dev/null || true
    exit 1
fi

log "Restarting ${SERVICE} to pick up the new env..."
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
    record_audit "set-env" "ok" \
        "{\"key\": \"${KEY}\", \"op\": \"${op}\", \"env_file\": \"${TARGET}\", \"service\": \"${SERVICE}\", \"unit\": \"active\"}" >/dev/null || true
    exit 0
else
    record_audit "set-env" "failed" \
        "{\"key\": \"${KEY}\", \"op\": \"${op}\", \"env_file\": \"${TARGET}\", \"service\": \"${SERVICE}\", \"unit\": \"${post_state}\"}" >/dev/null || true
    log "ERROR: ${SERVICE} did not return to 'active' within 30 s."
    exit 1
fi
