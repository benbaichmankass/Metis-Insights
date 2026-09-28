#!/usr/bin/env bash
# Tier-1, READ-ONLY: does the live VM actually run <ACCOUNT_ID> in <MODE>?
#
# The post-state half of the durable set-account-mode (JC-CA-01, operator
# decision 2026-09-28). The flip itself is now a commit to `main`
# (scripts/ops/account_mode_commit.py). This script answers the only question
# that matters afterwards: has the VM converged on it? It does NOT edit or
# restart anything.
#
# It checks three facts, each one read rather than inferred:
#   1. HEAD   `git show HEAD:config/accounts.yaml` on the VM reads mode: MODE
#             (ict-git-sync has converged the worktree on main).
#   2. DISK   the working-tree file reads the same. `load_accounts()` re-reads it
#             on every dispatch, so this is what the order path consults.
#   3. RUNTIME <runtime_logs_dir()>/runtime_status.json `live[ACCOUNT_ID]` equals
#             (MODE == live), and the file was written AFTER the HEAD
#             convergence was first seen. The trader writes it per tick from
#             accounts.yaml, so this proves a tick ran on the new mode.
#             Unit state is reported alongside.
#             ⚠️ THE PATH IS RESOLVED THE WAY THE TRADER RESOLVES IT: the
#             repo's `load_runtime_env` (drop-in, then .env, then the running
#             unit's systemctl Environment), then `src.utils.paths.
#             runtime_logs_dir()`. On the live VM that is
#             /data/bot-data/runtime_logs (DATA_DIR drop-in,
#             deploy/dropins/data-dir.conf). ${REPO_DIR}/runtime_logs is the
#             LEGACY pre-cutover copy (status_check.sh labels it so). Reading it
#             would never see a fresh tick, and every real verify would page a
#             false NOT CONVERGED. (Review finding on #13508, 2026-09-28.)
#
# Required env:
#   ACCOUNT_ID, MODE (live | dry_run)
# Optional env:
#   VERIFY_WAIT_SECONDS  how long to poll for convergence (default 600)
#   VERIFY_PENDING_OK=1  report "pending" and exit 0 instead of 4 when not yet
#                        converged. set-account-mode uses this right after it
#                        opens the PR, when non-convergence is expected.
#
# Exit codes: 0 converged (or pending with VERIFY_PENDING_OK) · 1 bad input /
# unreadable · 4 not converged within VERIFY_WAIT_SECONDS

set -euo pipefail

SCRIPT_NAME="verify_account_mode"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

REPO_DIR="${REPO_DIR:-/home/ubuntu/ict-trading-bot}"
UNIT="ict-trader-live.service"
ACCOUNT_ID="${ACCOUNT_ID:-}"
MODE="${MODE:-}"
VERIFY_WAIT_SECONDS="${VERIFY_WAIT_SECONDS:-600}"
VERIFY_PENDING_OK="${VERIFY_PENDING_OK:-0}"

# Give this shell the trader's DATA_DIR / RUNTIME_LOGS_DIR (a system-action
# wrapper does not inherit the unit's drop-in Environment=).
load_runtime_env

if [ -z "${ACCOUNT_ID}" ] || [ -z "${MODE}" ]; then
    log "ERROR: verify-account-mode requires ACCOUNT_ID and MODE."
    exit 1
fi
case "${MODE}" in live|dry_run) ;; *) log "ERROR: MODE must be live|dry_run (got '${MODE}')."; exit 1 ;; esac
if ! [[ "${ACCOUNT_ID}" =~ ^[A-Za-z0-9_-]+$ ]]; then
    log "ERROR: ACCOUNT_ID '${ACCOUNT_ID}' has characters outside [A-Za-z0-9_-]."
    exit 1
fi
if ! [[ "${VERIFY_WAIT_SECONDS}" =~ ^[0-9]+$ ]]; then
    log "ERROR: VERIFY_WAIT_SECONDS must be an integer."
    exit 1
fi

# One reader for all three facts, printing "head|disk|runtime|runtime_mtime".
# `runtime` is true/false/missing/absent/error:<type>. "absent" (no file) and
# "missing" (file, no entry) stay distinct from each other and from false.
probe() {
    /usr/bin/python3 - "${REPO_DIR}" "${ACCOUNT_ID}" <<'PY'
import json, pathlib, subprocess, sys
repo, acct = sys.argv[1], sys.argv[2]
sys.path.insert(0, f"{repo}/scripts/ops")
sys.path.insert(0, repo)
from account_mode_commit import read_mode, runtime_status_path
def mode(text):
    try:
        return read_mode(text, acct) or "missing"
    except LookupError:
        return "account_missing"
r = subprocess.run(["git", "-C", repo, "show", "HEAD:config/accounts.yaml"],
                   capture_output=True, text=True)
head = mode(r.stdout) if r.returncode == 0 else "unreadable"
p = pathlib.Path(repo, "config/accounts.yaml")
disk = mode(p.read_text()) if p.exists() else "unreadable"
s = runtime_status_path()
if not s.exists():
    rt, mt = "absent", 0
else:
    mt = int(s.stat().st_mtime)
    try:
        v = json.loads(s.read_text()).get("live", {}).get(acct)
        rt = "missing" if v is None else str(bool(v)).lower()
    except Exception as e:  # noqa: BLE001 - reported, never folded into a verdict
        rt = f"error:{type(e).__name__}"
print(f"{head}|{disk}|{rt}|{mt}|{s}")
PY
}

want_live="false"; [ "${MODE}" = "live" ] && want_live="true"
deadline=$(( $(date +%s) + VERIFY_WAIT_SECONDS ))
converged_at=""
state="pending"
while :; do
    IFS='|' read -r head disk runtime rt_mtime status_path < <(probe || echo "unreadable|unreadable|error:probe|0|unresolved")
    rt_mtime="${rt_mtime:-0}"
    if [ "${head}" = "account_missing" ] && [ "${disk}" = "account_missing" ]; then
        log "ERROR: account '${ACCOUNT_ID}' is not in config/accounts.yaml at HEAD or on disk."
        exit 1
    fi
    now=$(date +%s)
    if [ "${head}" = "${MODE}" ] && [ "${disk}" = "${MODE}" ] && [ -z "${converged_at}" ]; then
        converged_at="${now}"
        log "HEAD + worktree read ${ACCOUNT_ID}.mode=${MODE}; waiting for a trader tick written after this moment"
    fi
    if [ -n "${converged_at}" ] && [ "${runtime}" = "${want_live}" ] && [ "${rt_mtime}" -ge "${converged_at}" ]; then
        state="converged"; break
    fi
    [ "${now}" -ge "${deadline}" ] && break
    sleep 15
done

unit="$(systemctl is-active "${UNIT}" 2>/dev/null || true)"
sha="$(git -C "${REPO_DIR}" rev-parse --short HEAD 2>/dev/null || echo unreadable)"
log "verify-account-mode ${ACCOUNT_ID}: want=${MODE} head=${head} disk=${disk} runtime_live=${runtime} (want ${want_live}) unit=${unit:-unknown} sha=${sha} status_file=${status_path} -> ${state}"
detail="{\"account\": \"${ACCOUNT_ID}\", \"want\": \"${MODE}\", \"head\": \"${head}\", \"disk\": \"${disk}\", \"runtime_live\": \"${runtime}\", \"unit\": \"${unit:-unknown}\", \"sha\": \"${sha}\", \"status_file\": \"${status_path}\"}"

if [ "${state}" = "converged" ]; then
    record_audit "verify-account-mode" "ok" "${detail}" >/dev/null || true
    exit 0
fi
if [ "${VERIFY_PENDING_OK}" = "1" ]; then
    log "NOT YET CONVERGED — expected while the mode PR is unmerged. account-mode-verify.yml re-checks after it merges."
    record_audit "verify-account-mode" "pending" "${detail}" >/dev/null || true
    exit 0
fi
log "ERROR: not converged within ${VERIFY_WAIT_SECONDS}s."
record_audit "verify-account-mode" "failed" "${detail}" >/dev/null || true
exit 4
