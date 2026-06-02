#!/usr/bin/env bash
# sync_vm_secrets.sh — propagate Actions-secret values to the live VM's .env
# and restart the trader/web-api so the new values are in process env.
#
# Called by .github/workflows/sync-vm-secrets.yml via SSH. Replaces the
# per-account rotate_account_keys.sh.
#
# Inputs (injected by the workflow, never logged):
#   SYNC_REQUIRED — space-separated list of REQUIRED env-var names
#                    (must all be set in this process's env on entry)
#   SYNC_OPTIONAL — space-separated list of OPTIONAL env-var names
#                    (synced only when set; absent → skipped + warning)
#   Plus the values themselves, carried in this script's env via SSH
#   SendEnv. The script reads them via indirect expansion (${!name}).
#
# The script never echoes a value; verification compares "is the var
# present" rather than "is the var equal to <X>". Failures surface env-var
# NAMES (never values) so the workflow's audit bundle captures the cause
# without leaking credentials.
#
# Exits 2 on input-validation failure, 1 on patch / restart failure, 0
# on success (including the no-change idempotent case).

set -euo pipefail

REPO_DIR="${BOT_REPO_DIR:-/home/ubuntu/ict-trading-bot}"
# shellcheck source=scripts/ops/_lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lib.sh"
ENV_FILE="${REPO_DIR}/.env"

# ── Input validation ──────────────────────────────────────────────────────────

if [ -z "${SYNC_REQUIRED:-}" ]; then
  echo "ERROR: SYNC_REQUIRED is empty — the workflow must declare at least one required secret." >&2
  exit 2
fi
SYNC_OPTIONAL="${SYNC_OPTIONAL:-}"

# Verify every REQUIRED var has a value on entry.
for name in ${SYNC_REQUIRED}; do
  if [ -z "${!name:-}" ]; then
    echo "ERROR: required env var '${name}' is not set in this script's env (SSH SendEnv missing?)." >&2
    exit 2
  fi
done

if [ ! -f "${ENV_FILE}" ]; then
  echo "ERROR: .env file not found at ${ENV_FILE}." >&2
  exit 2
fi

echo ">>> Syncing secrets into ${ENV_FILE}"
echo ">>> REQUIRED: ${SYNC_REQUIRED}"
echo ">>> OPTIONAL: ${SYNC_OPTIONAL:-(none)}"

# ── Backup .env once before any change ───────────────────────────────────────

BACKUP="${ENV_FILE}.bak.$(date +%Y%m%dT%H%M%S)"
cp "${ENV_FILE}" "${BACKUP}"
echo ">>> Backup written to ${BACKUP}"

# ── Patch helper ─────────────────────────────────────────────────────────────
# Same shape as rotate_account_keys.sh::_patch_env_var. Returns 0 on any
# write or no-op; never echoes the value.

_patch_env_var() {
  local var_name="$1"
  local var_value="$2"
  local env_file="$3"
  # Escape for use in sed replacement (handles /, &, \).
  local escaped_value
  escaped_value=$(printf '%s' "${var_value}" | sed 's/[\/&]/\\&/g')
  if grep -q "^${var_name}=" "${env_file}"; then
    # Read the current line and compare its right-hand side to the
    # incoming value WITHOUT echoing either side. If identical, skip
    # the sed write — keeps the run idempotent.
    local current
    current=$(grep -m1 "^${var_name}=" "${env_file}" | cut -d= -f2-)
    if [ "${current}" = "${var_value}" ]; then
      echo ">>> ${var_name}: unchanged (skip write)"
      return 0
    fi
    sed -i "s|^${var_name}=.*|${var_name}=${escaped_value}|" "${env_file}"
    echo ">>> ${var_name}: updated"
  else
    printf '%s=%s\n' "${var_name}" "${var_value}" >> "${env_file}"
    echo ">>> ${var_name}: appended"
  fi
}

# ── Patch REQUIRED + present OPTIONAL ────────────────────────────────────────

changed=0
total=0

for name in ${SYNC_REQUIRED}; do
  total=$(( total + 1 ))
  # _patch_env_var prints "updated"/"appended"/"unchanged"; track changes by
  # diffing against the backup at the end. Cheaper than tracking inline.
  _patch_env_var "${name}" "${!name}" "${ENV_FILE}"
done

skipped_optional=""
for name in ${SYNC_OPTIONAL}; do
  total=$(( total + 1 ))
  if [ -z "${!name:-}" ]; then
    skipped_optional="${skipped_optional} ${name}"
    echo ">>> ${name}: not set in Actions, skipped"
    continue
  fi
  _patch_env_var "${name}" "${!name}" "${ENV_FILE}"
done

if [ -n "${skipped_optional}" ]; then
  echo ">>> Skipped (OPTIONAL absent):${skipped_optional}"
fi

# Compare against backup to know whether ANY value actually changed —
# the restart below can be skipped on a no-op run.
if cmp -s "${BACKUP}" "${ENV_FILE}"; then
  changed=0
  echo ">>> No .env changes — nothing to restart."
else
  changed=1
  echo ">>> .env updated — trader restart required."
fi

# ── Verify every REQUIRED + present OPTIONAL appears in .env ─────────────────

for name in ${SYNC_REQUIRED}; do
  if ! grep -q "^${name}=" "${ENV_FILE}"; then
    echo "ERROR: ${name} not found in .env after patch." >&2
    exit 1
  fi
done
for name in ${SYNC_OPTIONAL}; do
  if [ -n "${!name:-}" ] && ! grep -q "^${name}=" "${ENV_FILE}"; then
    echo "ERROR: ${name} (present in Actions) not found in .env after patch." >&2
    exit 1
  fi
done

echo ">>> Sync verified — ${total} secret(s) processed, .env changed=${changed}"

# ── Restart services (only when .env changed) ────────────────────────────────

if [ "${changed}" -eq 0 ]; then
  echo ">>> Skipping restart — .env unchanged."
  echo ">>> sync_vm_secrets: done (no-op)"
  exit 0
fi

echo ">>> Restarting ict-trader-live ..."
sudo systemctl restart ict-trader-live
sleep 3
if ! systemctl is-active --quiet ict-trader-live; then
  echo "ERROR: ict-trader-live failed to start after secret sync." >&2
  systemctl status ict-trader-live --no-pager -l >&2 || true
  exit 1
fi
echo ">>> ict-trader-live: $(systemctl is-active ict-trader-live)"

echo ">>> Restarting ict-web-api ..."
sudo systemctl restart ict-web-api
sleep 2
echo ">>> ict-web-api: $(systemctl is-active ict-web-api 2>/dev/null || echo unknown)"

echo ">>> sync_vm_secrets: done"
