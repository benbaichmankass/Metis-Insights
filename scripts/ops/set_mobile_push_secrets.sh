#!/usr/bin/env bash
# Tier-2 system-action: rotate FCM_SERVICE_ACCOUNT_JSON on the live VM.
#
# Thin wrapper around set_env.sh that pins the env key + target service
# so the operator can't accidentally write the FCM credential to the
# wrong key or restart the wrong unit. The actual value comes from the
# FCM_SERVICE_ACCOUNT_JSON GitHub Actions secret via the
# SECRET_FCM_SERVICE_ACCOUNT_JSON env var the workflow exports — see
# .github/workflows/system-actions.yml § "Secret-backed env values".
#
# The system-actions workflow prepends
# ``ENV_VALUE=$(printf %q "${SECRET_FCM_SERVICE_ACCOUNT_JSON}")`` to the
# remote command for this action, so by the time set_env.sh runs:
#   - ENV_KEY   = FCM_SERVICE_ACCOUNT_JSON   (set below)
#   - ENV_VALUE = <the secret's value>        (injected by the workflow)
#   - ENV_SERVICE = ict-trader-live.service  (set below)
# and set_env.sh handles the idempotent upsert + restart + post-restart
# health probe + audit logging.
#
# What this does NOT touch:
#   - MOBILE_PUSH_ENABLED (use enable-mobile-push / disable-mobile-push)
#   - Anything outside the trader's .env
#   - Any service other than ict-trader-live.service
#
# Exit codes match set_env.sh: 0 success, 1 validation / write /
# restart failure.

set -euo pipefail

SCRIPT_NAME="set_mobile_push_secrets"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

# Fix the env key + service. ENV_VALUE comes from the workflow (which
# pulls it from secrets.FCM_SERVICE_ACCOUNT_JSON, never from the issue
# body, so the credential never appears in logs).
if [ -z "${ENV_VALUE:-}" ]; then
    log "ERROR: ENV_VALUE is empty."
    log "ERROR: the system-actions workflow should inject it from secrets.FCM_SERVICE_ACCOUNT_JSON."
    log "ERROR: if the secret is unset, add it under Settings → Secrets and variables → Actions."
    record_audit "set-mobile-push-secrets" "error" '{"reason": "ENV_VALUE empty"}' >/dev/null || true
    exit 1
fi

export ENV_KEY="FCM_SERVICE_ACCOUNT_JSON"
export ENV_SERVICE="ict-trader-live.service"

exec "${SCRIPT_DIR}/set_env.sh"
