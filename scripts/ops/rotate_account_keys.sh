#!/usr/bin/env bash
# rotate_account_keys.sh — patch .env API credentials for one account and restart.
#
# Called by .github/workflows/rotate-account-keys.yml via SSH. Replaces the
# canonical Colab/SOPS/age-key path for key rotation.
#
# Required env vars (injected by the workflow, never logged):
#   ACCOUNT_ID   — e.g. bybit_1, bybit_2
#   NEW_API_KEY  — new API key value
#   NEW_API_SECRET — new API secret value
#
# The script never echoes key or secret values; it exits 2 on any validation
# failure so the workflow's audit bundle captures the reason without leaking
# credentials. On success it restarts ict-trader-live and ict-web-api.

set -euo pipefail

REPO_DIR="${BOT_REPO_DIR:-/home/ubuntu/ict-trading-bot}"
ENV_FILE="${REPO_DIR}/.env"

# ── Input validation ──────────────────────────────────────────────────────────

if [ -z "${ACCOUNT_ID:-}" ]; then
  echo "ERROR: ACCOUNT_ID is required." >&2
  exit 2
fi

if [ -z "${NEW_API_KEY:-}" ]; then
  echo "ERROR: NEW_API_KEY is required." >&2
  exit 2
fi

if [ -z "${NEW_API_SECRET:-}" ]; then
  echo "ERROR: NEW_API_SECRET is required." >&2
  exit 2
fi

# Resolve the env var names from the account id.
# Must match config/accounts.yaml::api_key_env for each account.
case "${ACCOUNT_ID}" in
  bybit_1)
    KEY_VAR="BYBIT_API_KEY_1"
    SECRET_VAR="BYBIT_API_SECRET_1"
    ;;
  bybit_2)
    KEY_VAR="BYBIT_API_KEY_2"
    SECRET_VAR="BYBIT_API_SECRET_2"
    ;;
  *)
    echo "ERROR: Unknown ACCOUNT_ID '${ACCOUNT_ID}'. Allowed: bybit_1, bybit_2." >&2
    exit 2
    ;;
esac

if [ ! -f "${ENV_FILE}" ]; then
  echo "ERROR: .env file not found at ${ENV_FILE}." >&2
  exit 2
fi

echo ">>> Rotating credentials for account '${ACCOUNT_ID}' (${KEY_VAR} / ${SECRET_VAR})"
echo ">>> .env path: ${ENV_FILE}"

# ── Backup ────────────────────────────────────────────────────────────────────

BACKUP="${ENV_FILE}.bak.$(date +%Y%m%dT%H%M%S)"
cp "${ENV_FILE}" "${BACKUP}"
echo ">>> Backup written to ${BACKUP}"

# ── Patch .env ───────────────────────────────────────────────────────────────
# Uses printf %q to produce a shell-safe quoted value for sed, so keys
# containing special characters don't corrupt the substitution. The value is
# written unquoted (bare) into .env — dotenv-style parsers expect bare values;
# systemd EnvironmentFile strips surrounding quotes if present.

_patch_env_var() {
  local var_name="$1"
  local var_value="$2"
  local env_file="$3"
  # Escape for use in sed replacement (handles /, &, \)
  local escaped_value
  escaped_value=$(printf '%s' "${var_value}" | sed 's/[\/&]/\\&/g')
  if grep -q "^${var_name}=" "${env_file}"; then
    sed -i "s|^${var_name}=.*|${var_name}=${escaped_value}|" "${env_file}"
    echo ">>> Updated ${var_name} in ${env_file}"
  else
    printf '%s=%s\n' "${var_name}" "${var_value}" >> "${env_file}"
    echo ">>> Appended ${var_name} to ${env_file}"
  fi
}

_patch_env_var "${KEY_VAR}"    "${NEW_API_KEY}"    "${ENV_FILE}"
_patch_env_var "${SECRET_VAR}" "${NEW_API_SECRET}" "${ENV_FILE}"

echo ">>> Credential patch complete (values redacted from log)"

# ── Verify the vars appear in .env (key name only, never the value) ───────────

if ! grep -q "^${KEY_VAR}=" "${ENV_FILE}"; then
  echo "ERROR: ${KEY_VAR} not found in .env after patch." >&2
  exit 1
fi
if ! grep -q "^${SECRET_VAR}=" "${ENV_FILE}"; then
  echo "ERROR: ${SECRET_VAR} not found in .env after patch." >&2
  exit 1
fi

# ── Restart services ─────────────────────────────────────────────────────────

echo ">>> Restarting ict-trader-live ..."
sudo systemctl restart ict-trader-live
sleep 3
if ! systemctl is-active --quiet ict-trader-live; then
  echo "ERROR: ict-trader-live failed to start after key rotation." >&2
  systemctl status ict-trader-live --no-pager -l >&2 || true
  exit 1
fi
echo ">>> ict-trader-live: $(systemctl is-active ict-trader-live)"

echo ">>> Restarting ict-web-api ..."
sudo systemctl restart ict-web-api
sleep 2
echo ">>> ict-web-api: $(systemctl is-active ict-web-api 2>/dev/null || echo unknown)"

echo ">>> rotate_account_keys: done for ${ACCOUNT_ID}"
