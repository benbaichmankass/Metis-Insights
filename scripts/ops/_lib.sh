#!/usr/bin/env bash
# Shared helpers for scripts/ops/*.sh.
#
# Sourced — never executed directly.
#
# Conventions every wrapper follows:
#   - set -euo pipefail at top
#   - log() prints to stderr with a timestamp + script name
#   - record_audit() appends a one-line JSON record under
#     runtime_logs/operator_actions/ so the audit trail lands inside
#     the repo's runtime_logs/ tree (which the diag relay tails).
#   - All scripts read $REPO_DIR (default /home/ubuntu/ict-trading-bot).

REPO_DIR="${REPO_DIR:-/home/ubuntu/ict-trading-bot}"
AUDIT_DIR="${REPO_DIR}/runtime_logs/operator_actions"

log() {
    printf '[%s] [%s] %s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        "${SCRIPT_NAME:-ops}" \
        "$*" >&2
}

record_audit() {
    # Args: action_name status [extra_json_object]
    local action="$1"
    local status="$2"
    local extra="${3:-{\}}"
    mkdir -p "${AUDIT_DIR}"
    local ts
    ts="$(date -u +%Y%m%dT%H%M%SZ)"
    local out_file="${AUDIT_DIR}/${ts}-${action}.json"
    python3 - "${out_file}" "${action}" "${status}" "${extra}" <<'PY'
import json, os, sys, datetime, socket
out_file, action, status, extra = sys.argv[1:5]
try:
    extra_obj = json.loads(extra) if extra else {}
except Exception:
    extra_obj = {"_extra_parse_error": extra}
record = {
    "ts": datetime.datetime.utcnow().isoformat() + "Z",
    "host": socket.gethostname(),
    "user": os.environ.get("USER", ""),
    "action": action,
    "status": status,
    **extra_obj,
}
with open(out_file, "w") as f:
    json.dump(record, f, indent=2, default=str)
print(out_file)
PY
}

require_systemctl() {
    if ! command -v systemctl >/dev/null 2>&1; then
        log "ERROR: systemctl not found on PATH; this VM is not systemd-managed."
        return 1
    fi
}
