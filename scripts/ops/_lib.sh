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

heal_devnull() {
    # A clobbered /dev/null breaks every `>/dev/null` redirect for the non-root
    # SSH user, which previously masqueraded as "systemctl not found" and sent
    # operators chasing a non-existent systemd problem (BL-20260616-DEVNULL).
    # The recurring OCI host-agent regression (docs/runbooks/devnull-guard.md)
    # has TWO variants: (a) the char device's mode stripped to 0444, and (b) the
    # node replaced by a root-owned *regular* file. Either way the redirect
    # EACCESes.
    #
    # SELF-HEAL, never punt. The git-sync deploy path
    # (scripts/deploy_pull_restart.sh) already repairs /dev/null in place before
    # redirecting; this wrapper used to merely DETECT the breakage and abort with
    # an error telling the operator to SSH in and run `mknod` by hand. That
    # institutionalised an autonomy-contract violation (BL-20260629): a runner
    # holds VM_SSH_KEY and can repair this itself, so the wrapper must too.
    # Repair in place (best-effort sudo) and continue — chmod fixes (a),
    # rm+mknod fixes (b); the ict-devnull-guard.timer is the 60s periodic belt.
    # Only abort if /dev/null is STILL unwritable after the self-heal (e.g.
    # `sudo -n` unavailable to this user) — a genuine, escalation-worthy failure.
    # The probe's stderr goes to a temp file, NOT /dev/null (the thing under test).
    #
    # Callable MID-RUN too (not just at wrapper start): the strip has been
    # observed to land WHILE a wrapper runs — the run's first redirect succeeds,
    # every later one EACCESes (BL-20260713-DEVNULL-RESTART-MISREPORT, seen on
    # both 2026-07-13 and 2026-07-14 restart-bot-service runs) — so long-running
    # wrappers re-invoke this right before load-bearing state reads.
    local _probe="${TMPDIR:-/tmp}/.devnull_probe.$$"
    if ! ( : >/dev/null ) 2>"${_probe}"; then
        log "WARNING: /dev/null is not writable (clobbered by a host agent) — self-healing before continuing (mirrors deploy_pull_restart.sh; ict-devnull-guard.timer is the periodic belt)."
        # Variant (a): mode stripped — a chmod restores write.
        sudo -n chmod 0666 /dev/null 2>"${_probe}" || true
        if ! ( : >/dev/null ) 2>"${_probe}"; then
            # Variant (b): replaced by a regular file — recreate the 1:3 char
            # device. Single sudo so we never leave /dev/null removed-but-not-
            # recreated if the second half fails.
            sudo -n sh -c 'rm -f /dev/null && mknod -m 666 /dev/null c 1 3' 2>"${_probe}" || true
        fi
        if ! ( : >/dev/null ) 2>"${_probe}"; then
            rm -f "${_probe}" 2>&- || true
            log "ERROR: /dev/null is STILL not writable after self-heal (sudo -n chmod and rm+mknod both failed — sudo may be unavailable to this user). Dispatch the vm-fix-devnull workflow (label: vm-fix-devnull) to repair it with elevated rights."
            return 1
        fi
        log ">>> /dev/null self-healed; continuing."
    fi
    rm -f "${_probe}" 2>&- || true
}

require_systemctl() {
    heal_devnull || return 1
    if ! command -v systemctl >/dev/null 2>&1; then
        log "ERROR: systemctl not found on PATH; this VM is not systemd-managed."
        return 1
    fi
}


# load_runtime_env — populate DATA_DIR / TRADE_JOURNAL_DB (and friends) so
# system-action wrappers run with the same path resolution the live
# trader services see at runtime.
#
# WHY: system-action wrappers run from a fresh shell, not as a child of
# ict-trader-live.service, so they do NOT inherit the systemd drop-in's
# Environment= directives. Before this helper existed, every wrapper that
# touched the SQLite journal defaulted to ${REPO_DIR}/trade_journal.db —
# the pre-2026-05-12 path. The live trader has been writing to
# /data/bot-data/trade_journal.db since the data-dir externalisation, so
# the wrappers were silently reading a stale file. The 2026-05-16
# orphan-backfill failure (issue #1308 — 14 candidates recovered against
# the wrong DB, 0 useful writes) was the proximate trigger.
#
# Resolution order, matching systemd's load order (drop-in → EnvironmentFile):
#
#   1. Drop-in defaults from deploy/dropins/data-dir.conf, parsed as
#      `Environment=KEY=VAL` lines.
#   2. ${REPO_DIR}/.env, sourced as a shell file (KEY=VAL pairs). Per the
#      drop-in's own docstring, .env wins over the drop-in because
#      systemd loads EnvironmentFile after Environment=.
#   3. systemctl show ict-trader-live.service --property=Environment,
#      which captures whatever the running unit is actually using
#      (including any other drop-ins the operator may have layered on).
#      Authoritative if systemctl is available and the unit is loaded.
#
# Idempotent — vars already set in the caller's environment win over
# every layer above (export -p semantics; we never overwrite a pre-set var).
#
# Variables exported (whitelist; expand here as new ones become canonical):
#   DATA_DIR, TRADE_JOURNAL_DB, MODEL_DIR, LOG_DIR, RUNTIME_LOGS_DIR,
#   RUNTIME_STATE_DIR, ARTIFACTS_DIR
#
# Returns 0 always. Diagnostic-quiet by design — if no source is
# available (running on a dev box, deploy/ stripped, no systemd), the
# wrapper falls back to its own defaults.

_RUNTIME_ENV_WHITELIST="DATA_DIR TRADE_JOURNAL_DB MODEL_DIR LOG_DIR RUNTIME_LOGS_DIR RUNTIME_STATE_DIR ARTIFACTS_DIR"

load_runtime_env() {
    # Resolution order (most authoritative first):
    #
    #   0. Caller's pre-existing shell env — wins everything. Snapshotted
    #      up front and restored at the end so explicit operator overrides
    #      (e.g. `TRADE_JOURNAL_DB=/tmp/sandbox.db ./wrapper.sh`) take
    #      precedence over both the drop-in and the .env layer.
    #   1. Live systemctl unit — what the trader is actually using.
    #   2. .env file at ${REPO_DIR}/.env — operator's override of the
    #      drop-in's defaults, per the drop-in's documented semantics.
    #   3. Drop-in defaults at deploy/dropins/data-dir.conf — base layer.
    #
    # Each layer overwrites the previous, then the caller's snapshot is
    # re-applied at the end. The result: caller env > systemctl > .env >
    # drop-in > "unset".
    local key

    # Snapshot caller's pre-set values.
    local _preset=""
    for key in ${_RUNTIME_ENV_WHITELIST}; do
        if [ -n "${!key+x}" ]; then
            _preset+="${key}=${!key}"$'\n'
        fi
    done

    # Layer 3 (lowest priority): drop-in defaults
    local dropin="${REPO_DIR}/deploy/dropins/data-dir.conf"
    if [ -f "${dropin}" ]; then
        while IFS= read -r line; do
            if [[ "${line}" =~ ^Environment=([A-Z_][A-Z0-9_]*)=(.*)$ ]]; then
                key="${BASH_REMATCH[1]}"
                case " ${_RUNTIME_ENV_WHITELIST} " in
                    *" ${key} "*) export "${key}=${BASH_REMATCH[2]}" ;;
                esac
            fi
        done < "${dropin}"
    fi

    # Layer 2: .env overrides (systemd EnvironmentFile semantics)
    local env_file="${REPO_DIR}/.env"
    if [ -f "${env_file}" ]; then
        while IFS= read -r line; do
            if [[ "${line}" =~ ^([A-Z_][A-Z0-9_]*)=(.*)$ ]]; then
                key="${BASH_REMATCH[1]}"
                local val="${BASH_REMATCH[2]}"
                # Strip surrounding quotes (`KEY="val"` and `KEY='val'`).
                val="${val#\"}"; val="${val%\"}"
                val="${val#\'}"; val="${val%\'}"
                case " ${_RUNTIME_ENV_WHITELIST} " in
                    *" ${key} "*) export "${key}=${val}" ;;
                esac
            fi
        done < "${env_file}"
    fi

    # Layer 1: live systemd unit
    if command -v systemctl >/dev/null 2>&1; then
        local env_line
        env_line=$(systemctl show ict-trader-live.service --property=Environment --value 2>/dev/null || true)
        if [ -n "${env_line}" ]; then
            for assign in ${env_line}; do
                if [[ "${assign}" =~ ^([A-Z_][A-Z0-9_]*)=(.*)$ ]]; then
                    key="${BASH_REMATCH[1]}"
                    case " ${_RUNTIME_ENV_WHITELIST} " in
                        *" ${key} "*) export "${assign?}" ;;
                    esac
                fi
            done
        fi
    fi

    # Layer 0 (highest priority): restore caller's pre-set values
    if [ -n "${_preset}" ]; then
        while IFS= read -r assign; do
            if [ -n "${assign}" ]; then
                export "${assign?}"
            fi
        done <<<"${_preset}"
    fi
}


# runtime_db_path — print the canonical trade_journal.db path the live
# trader services use. Operator-action wrappers must call this instead
# of constructing `${TRADE_JOURNAL_DB:-${REPO_DIR}/trade_journal.db}`
# inline — the inline form misses the systemd drop-in's pinning of the
# DB to /data/bot-data/, which is the bug load_runtime_env exists to fix.
#
# Always returns a non-empty path. If load_runtime_env couldn't find a
# canonical source (dev box / stripped deploy/), falls back to the
# pre-2026-05-12 repo-local path — preserving the old single-machine
# layout for tests + developer environments.

runtime_db_path() {
    load_runtime_env
    printf '%s\n' "${TRADE_JOURNAL_DB:-${REPO_DIR}/trade_journal.db}"
}


# load_runtime_secrets — source ${REPO_DIR}/.env in full so the wrapper's
# Python subprocesses can authenticate to exchange APIs (Bybit, Binance, …).
#
# WHY (the 2026-05-16 silent-credential failure, issue #1314):
# system-action wrappers run via SSH from a fresh shell — they are NOT
# children of ict-trader-live.service and so do NOT inherit the systemd
# unit's EnvironmentFile=/home/ubuntu/ict-trading-bot/.env. The backfill-
# orphan-pnl wrapper then invoked python3, which called
# resolve_credentials(), which read os.environ for BYBIT_API_KEY_2, got
# None, returned None, and propagated four silent-fallback layers up to
# the script's "account_closed_pnl_for_trade returned None" skip — same
# skip reason as "Bybit had no record." The script reported "0 recovered"
# while never having reached Bybit at all.
#
# WHY this is a distinct helper from load_runtime_env:
# load_runtime_env is whitelist-gated (only DATA_DIR / TRADE_JOURNAL_DB /
# MODEL_DIR / LOG_DIR / RUNTIME_LOGS_DIR / RUNTIME_STATE_DIR /
# ARTIFACTS_DIR get exported from .env or the drop-in). That tightness is
# load-bearing — it prevents random .env keys from leaking into wrapper
# shells where they might shadow caller env or break expectations. But
# credentials are precisely the keys NOT in that whitelist. Folding
# secrets into load_runtime_env would either (a) widen the whitelist to
# permit BYBIT_*, BINANCE_*, etc. — ad-hoc and hard to maintain — or
# (b) drop the whitelist entirely, losing the gate. A separate helper
# lets each wrapper explicitly opt in: "I need exchange auth."
#
# Behavior:
#   - Sources ${REPO_DIR}/.env via `set -a; source; set +a` so every
#     KEY=VAL line becomes an exported env var. Comments and blanks are
#     handled by bash's source semantics. Quoted values are stripped by
#     bash, matching systemd's EnvironmentFile parsing.
#   - Returns 0 always. If .env is missing (dev box, fresh checkout),
#     the wrapper proceeds with whatever the caller's shell already had;
#     the eventual resolve_credentials() will return None and the
#     wrapper's existing error-handling kicks in. We do NOT exit non-zero
#     here because the wrapper may not actually need creds for every run.
#   - Idempotent in the sense that re-sourcing .env is a no-op against
#     the same file. Calling load_runtime_secrets twice is safe.

load_runtime_secrets() {
    local env_file="${REPO_DIR}/.env"
    if [ ! -f "${env_file}" ]; then
        return 0
    fi
    set -a
    # shellcheck disable=SC1090
    source "${env_file}"
    set +a
}

