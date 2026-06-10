#!/usr/bin/env bash
# Tier-2 operator action: flip a per-account live/dry_run mode.
#
# Edits config/accounts.yaml (the canonical source of truth per
# src/units/accounts/__init__.py docstring) to set `mode:` for the
# named account, then restarts ict-trader-live.service so the change
# takes effect on the next process start.
#
# Created 2026-05-12 in response to the silent-flip incident where
# bybit_2 ended up live=false at runtime despite YAML declaring
# mode: live. Per operator directive: live is the default; any
# transition off live must be operator-driven and audited via this
# action's transparency-notify wire (system-actions.yml § 5.5).
#
# Required env vars (passed by the workflow's exec step):
#   ACCOUNT_ID  - name as keyed in config/accounts.yaml (e.g. bybit_2)
#   MODE        - "live" | "dry_run"
#
# Pre/post checks:
#   - capture pre-edit `mode:` value for the account
#   - perform in-place YAML edit (targeted single-line replacement;
#     preserves comments + formatting byte-for-byte outside the line)
#   - verify post-edit `mode:` matches MODE
#   - restart ict-trader-live.service; poll is-active up to 30s
#   - probe runtime_status.json's `live[ACCOUNT_ID]` if present
#     (informational; the file may not exist immediately after
#     restart on a cold boot)
#   - record audit JSON under runtime_logs/operator_actions/
#
# Exit codes:
#   0 - success (yaml edited, unit active)
#   1 - validation / yaml / restart failure
#   3 - deferred (claude-vm-runner@*.service active)

set -euo pipefail

SCRIPT_NAME="set_account_mode"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

REPO_DIR="${REPO_DIR:-/home/ubuntu/ict-trading-bot}"
YAML="${REPO_DIR}/config/accounts.yaml"
UNIT="ict-trader-live.service"

ACCOUNT_ID="${ACCOUNT_ID:-}"
MODE="${MODE:-}"

if [ -z "${ACCOUNT_ID}" ] || [ -z "${MODE}" ]; then
    log "ERROR: set-account-mode requires ACCOUNT_ID and MODE env vars."
    record_audit "set-account-mode" "error" '{"reason": "missing ACCOUNT_ID or MODE"}' >/dev/null || true
    exit 1
fi

case "${MODE}" in
    live|dry_run) ;;
    *)
        log "ERROR: MODE must be 'live' or 'dry_run' (got '${MODE}')."
        record_audit "set-account-mode" "error" "{\"reason\": \"invalid MODE '${MODE}'\"}" >/dev/null || true
        exit 1
        ;;
esac

# Charset guard for ACCOUNT_ID. Account names in accounts.yaml are
# bare YAML keys composed of [A-Za-z0-9_-]; this both defends the
# regex-based YAML edit below from injection and matches the actual
# naming convention used by load_accounts().
if ! [[ "${ACCOUNT_ID}" =~ ^[A-Za-z0-9_-]+$ ]]; then
    log "ERROR: ACCOUNT_ID '${ACCOUNT_ID}' contains invalid characters (allowed: A-Z a-z 0-9 _ -)."
    record_audit "set-account-mode" "error" "{\"reason\": \"invalid ACCOUNT_ID charset\"}" >/dev/null || true
    exit 1
fi

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required (see deploy_pull_restart.sh)."
    record_audit "set-account-mode" "error" '{"reason": "sudo unavailable"}' >/dev/null || true
    exit 1
fi

# Defer if any /vm runner is mid-flight (mirror restart_bot.sh).
if "${SYSTEMCTL[@]}" list-units 'claude-vm-runner@*.service' --state=active --no-legend 2>/dev/null | grep -q .; then
    log "ABORT: a claude-vm-runner@*.service unit is active. Refusing to mutate mid-runner."
    record_audit "set-account-mode" "deferred" '{"reason": "vm-runner active"}' >/dev/null || true
    exit 3
fi

if [ ! -f "${YAML}" ]; then
    log "ERROR: accounts.yaml not found at ${YAML}"
    record_audit "set-account-mode" "error" "{\"reason\": \"yaml missing\"}" >/dev/null || true
    exit 1
fi

# Read pre-edit mode value (informational + audit).
pre_mode="$(
/usr/bin/python3 - "${YAML}" "${ACCOUNT_ID}" <<'PY' || echo "error"
import re, sys, pathlib
path, acct = sys.argv[1], sys.argv[2]
content = pathlib.Path(path).read_text()
m = re.search(rf"^  {re.escape(acct)}:\s*$", content, re.MULTILINE)
if not m:
    print("account_missing")
    sys.exit(0)
start = m.end()
nxt = re.search(r"^  \w[\w-]*:\s*$", content[start:], re.MULTILINE)
end = start + nxt.start() if nxt else len(content)
mm = re.search(r"^\s{4}mode:\s*(\S+)", content[start:end], re.MULTILINE)
print(mm.group(1) if mm else "missing")
PY
)"
log "Pre-edit ${ACCOUNT_ID}.mode = ${pre_mode}"

if [ "${pre_mode}" = "account_missing" ]; then
    log "ERROR: account '${ACCOUNT_ID}' not found in ${YAML}."
    record_audit "set-account-mode" "error" "{\"reason\": \"account missing\", \"account\": \"${ACCOUNT_ID}\"}" >/dev/null || true
    exit 1
fi

# Perform the edit. Targeted single-line replacement preserves all
# surrounding comments + ordering byte-for-byte.
/usr/bin/python3 - "${YAML}" "${ACCOUNT_ID}" "${MODE}" <<'PY'
import re, sys, pathlib
path, acct, mode = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(path)
content = p.read_text()
m = re.search(rf"^  {re.escape(acct)}:\s*$", content, re.MULTILINE)
if not m:
    raise SystemExit(f"account '{acct}' not found in {path}")
start = m.end()
nxt = re.search(r"^  \w[\w-]*:\s*$", content[start:], re.MULTILINE)
end = start + nxt.start() if nxt else len(content)
block = content[start:end]
new_block, n = re.subn(
    r"^(\s{4}mode:\s*)\S+(.*)$",
    lambda mm: f"{mm.group(1)}{mode}{mm.group(2)}",
    block,
    count=1,
    flags=re.MULTILINE,
)
if n != 1:
    raise SystemExit(f"could not locate 'mode:' line in '{acct}' block")
p.write_text(content[:start] + new_block + content[end:])
print(f"YAML edited: {acct}.mode -> {mode}")
PY

# Verify the edit landed.
post_mode_yaml="$(
/usr/bin/python3 - "${YAML}" "${ACCOUNT_ID}" <<'PY' || echo "read_error"
import re, sys, pathlib
content = pathlib.Path(sys.argv[1]).read_text()
m = re.search(rf"^  {re.escape(sys.argv[2])}:\s*$", content, re.MULTILINE)
if not m:
    print("account_missing"); sys.exit(0)
start = m.end()
nxt = re.search(r"^  \w[\w-]*:\s*$", content[start:], re.MULTILINE)
end = start + nxt.start() if nxt else len(content)
mm = re.search(r"^\s{4}mode:\s*(\S+)", content[start:end], re.MULTILINE)
print(mm.group(1) if mm else "missing")
PY
)"
log "Post-edit ${ACCOUNT_ID}.mode = ${post_mode_yaml}"

if [ "${post_mode_yaml}" != "${MODE}" ]; then
    log "ERROR: YAML edit did not stick (read back '${post_mode_yaml}')."
    record_audit "set-account-mode" "failed" "{\"account\": \"${ACCOUNT_ID}\", \"yaml_pre\": \"${pre_mode}\", \"yaml_post\": \"${post_mode_yaml}\"}" >/dev/null || true
    exit 1
fi

# Restart so the trader picks up the updated YAML on the next start.
echo "===== pre-restart status ====="
"${SYSTEMCTL[@]}" status "${UNIT}" --no-pager -n 5 || true
log "Restarting ${UNIT}..."
"${SYSTEMCTL[@]}" restart "${UNIT}"

# Allow up to 30s for systemd to settle.
deadline=$(( $(date +%s) + 30 ))
post_unit="unknown"
while [ "$(date +%s)" -lt "${deadline}" ]; do
    post_unit="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>/dev/null || echo "unknown")"
    if [ "${post_unit}" = "active" ]; then
        break
    fi
    sleep 2
done
log "Post-restart unit state: ${post_unit}"

echo
echo "===== post-restart journalctl (last 30 lines) ====="
journalctl -u "${UNIT}" -n 30 --no-pager 2>/dev/null || true

# Probe runtime_status.json's `live` projection so the audit bundle
# records whether the dashboard agrees with the YAML edit. May not
# exist yet on a cold boot - that's informational, not fatal.
status_file="${REPO_DIR}/runtime_logs/runtime_status.json"
status_runtime_live="unknown"
if [ -f "${status_file}" ]; then
    status_runtime_live="$(
/usr/bin/python3 - "${status_file}" "${ACCOUNT_ID}" <<'PY' || echo "unknown"
import json, sys, pathlib
try:
    data = json.loads(pathlib.Path(sys.argv[1]).read_text())
    val = data.get("live", {}).get(sys.argv[2])
    print("missing" if val is None else str(val).lower())
except Exception as e:
    print(f"error:{type(e).__name__}")
PY
)"
fi
log "runtime_status.live[${ACCOUNT_ID}] after restart: ${status_runtime_live}"

if [ "${post_unit}" = "active" ]; then
    record_audit "set-account-mode" "ok" \
        "{\"account\": \"${ACCOUNT_ID}\", \"yaml_pre\": \"${pre_mode}\", \"yaml_post\": \"${post_mode_yaml}\", \"runtime_live_post\": \"${status_runtime_live}\", \"unit\": \"${post_unit}\"}" >/dev/null || true
    log "set-account-mode succeeded."
    exit 0
else
    record_audit "set-account-mode" "failed" \
        "{\"account\": \"${ACCOUNT_ID}\", \"yaml_post\": \"${post_mode_yaml}\", \"unit\": \"${post_unit}\"}" >/dev/null || true
    log "ERROR: ${UNIT} did not return to 'active' within 30 s."
    exit 1
fi
