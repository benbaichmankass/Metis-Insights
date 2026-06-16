#!/usr/bin/env bash
# Tier-2 system-action: rotate the FCM service-account credential on the
# live VM.
#
# **File-based, not .env-inline.** systemd ``EnvironmentFile`` only
# supports single-line ``KEY=VALUE``, and the service-account JSON's
# ``private_key`` field is multi-line. An earlier .env-inline version of
# this wrapper silently broke (systemd dropped every continuation line of
# the JSON value, leaving FCM_SERVICE_ACCOUNT_JSON set to a truncated
# invalid prefix and the notifier inert) — see the deploy log on issue
# #2079 for the rejected `Ignoring invalid environment assignment` lines.
#
# New contract:
#   1. The JSON itself is written to ``${DATA_DIR}/fcm_service_account.json``
#      (mode 600, owned by the trader user). systemd never parses it.
#   2. ``.env`` gets a single-line ``FCM_SERVICE_ACCOUNT_JSON_PATH``
#      pointing at that file. systemd parses this fine.
#   3. ``FcmNotifier.from_env`` reads the file via the _PATH var first,
#      falls back to the legacy ``FCM_SERVICE_ACCOUNT_JSON`` env for
#      tests / sandboxed deploys where single-line JSON is sufficient.
#
# The system-actions workflow prepends
# ``ENV_VALUE=$(printf %q "${SECRET_FCM_SERVICE_ACCOUNT_JSON}")`` to the
# remote command, so the JSON arrives in this script's ENV_VALUE. The
# workflow + this wrapper are the only places the credential transits
# outside the GitHub Actions secret store and the on-disk file.
#
# What this does NOT touch:
#   - Mobile push is unconditional (no enable flag); setting these FCM
#     credentials is the only thing needed to make push actually send.
#   - Anything outside the trader's .env + the fcm_service_account.json file
#   - Any service other than ict-trader-live.service
#
# Exit codes: 0 success, 1 validation / write / restart failure.

set -euo pipefail

SCRIPT_NAME="set_mobile_push_secrets"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

if [ -z "${ENV_VALUE:-}" ]; then
    log "ERROR: ENV_VALUE is empty."
    log "ERROR: the system-actions workflow should inject it from secrets.FCM_SERVICE_ACCOUNT_JSON."
    log "ERROR: if the secret is unset, add it under Settings → Secrets and variables → Actions."
    record_audit "set-mobile-push-secrets" "error" '{"reason": "ENV_VALUE empty"}' >/dev/null || true
    exit 1
fi

# Validate the value parses as JSON before we write it anywhere. A
# corrupted secret here is much easier to diagnose at write-time than
# 30 seconds later when the trader restarts and the notifier silently
# goes inert.
if ! printf '%s' "${ENV_VALUE}" | /usr/bin/python3 -c 'import sys, json; json.load(sys.stdin)' 2>/dev/null; then
    log "ERROR: ENV_VALUE is not valid JSON."
    record_audit "set-mobile-push-secrets" "error" '{"reason": "value not valid JSON"}' >/dev/null || true
    exit 1
fi

# Write the JSON to a file. DATA_DIR is the canonical block-volume mount
# from scripts/ops/_lib.sh; fall back to /data/bot-data for parity with
# the systemd drop-in's RuntimeDirectory.
DATA_DIR_RESOLVED="${DATA_DIR:-/data/bot-data}"
FCM_FILE="${DATA_DIR_RESOLVED}/fcm_service_account.json"
FCM_TMP="${FCM_FILE}.tmp.$$"

if [ ! -d "${DATA_DIR_RESOLVED}" ]; then
    log "ERROR: ${DATA_DIR_RESOLVED} does not exist."
    record_audit "set-mobile-push-secrets" "error" \
        "{\"reason\": \"data dir missing\", \"path\": \"${DATA_DIR_RESOLVED}\"}" >/dev/null || true
    exit 1
fi

# Restrictive umask so the temp file is created mode 600 from the start;
# the JSON never sits on disk with a wider mode.
( umask 077 && printf '%s' "${ENV_VALUE}" > "${FCM_TMP}" )
chmod 600 "${FCM_TMP}"
mv "${FCM_TMP}" "${FCM_FILE}"
log "Wrote FCM service-account JSON to ${FCM_FILE} (mode 600)."

# Now hand off to set_env.sh to set FCM_SERVICE_ACCOUNT_JSON_PATH in .env
# (single-line value — safe for systemd) and restart the trader.
export ENV_KEY="FCM_SERVICE_ACCOUNT_JSON_PATH"
export ENV_VALUE="${FCM_FILE}"
export ENV_SERVICE="ict-trader-live.service"

exec "${SCRIPT_DIR}/set_env.sh"
