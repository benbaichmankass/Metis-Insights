#!/usr/bin/env bash
# Tier-1 operator action: collect a one-shot health snapshot.
#
# Reads only. Does not restart anything. Safe to run autonomously.
#
# Output to stdout:
#   - systemctl is-active for the canonical trading services
#   - heartbeat.txt mtime + age in seconds
#   - last 20 lines of journalctl for ict-trader-live
#   - last 5 lines of signal_audit.jsonl
#
# Exit codes:
#   0 — all canonical services active (infra healthy)
#   1 — at least one canonical service is not active OR heartbeat missing
#
# Note: trading-level errors visible in journalctl (e.g. insufficient balance,
# order rejections) do NOT affect the exit code. Exit code reflects infra
# health only, not trading P&L or strategy state.
#
# Systemctl calls are wrapped in `timeout 8` to guard against D-Bus hangs
# that can occur on VMs that need a kernel update (shows as
# "*** System restart required ***" at SSH login).

set -euo pipefail

SCRIPT_NAME="status_check"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

CANONICAL_UNITS=(
    ict-trader-live.service
    ict-web-api.service
    ict-telegram-bot.service
)

log "Collecting service status…"
echo "===== systemctl is-active ====="
overall_ok=0
for unit in "${CANONICAL_UNITS[@]}"; do
    state="$(timeout 8 systemctl is-active "${unit}" 2>/dev/null || echo "unknown")"
    printf '%-32s %s\n' "${unit}" "${state}"
    if [ "${state}" != "active" ]; then
        overall_ok=1
    fi
done

# claude bridge is optional — report but don't fail on it.
if [ -f /etc/systemd/system/ict-claude-bridge.service ]; then
    state="$(timeout 8 systemctl is-active ict-claude-bridge.service 2>/dev/null || echo "unknown")"
    printf '%-32s %s (optional)\n' "ict-claude-bridge.service" "${state}"
fi

# Full enumeration of every ict-* unit systemd knows about, with its
# active + enabled state. This closes the diag-coverage chicken-and-egg
# (full-system audit Workstream B, 2026-06-28): the `is-active` block above
# and `/api/diag/services` only report the hardcoded canonical set, so a
# Claude session could NOT confirm a non-canonical unit (e.g.
# ict-devnull-guard) is enabled+active before allowlisting it in
# diag.py::_CANONICAL_UNITS — the relay can't query a unit until it's already
# allowlisted. Dumping the full ict-* unit list here gives a session the live
# observation it needs to verify-then-add. Read-only, bounded (head), never
# affects the exit code.
echo
echo "===== all ict-* units (full enumeration — verify before allowlisting in diag) ====="
echo "--- list-units (loaded; active/sub state) ---"
timeout 8 systemctl list-units 'ict-*.service' 'ict-*.timer' --all \
    --no-legend --no-pager 2>/dev/null | head -60 \
    || echo "(systemctl list-units unavailable)"
echo "--- list-unit-files (install state: enabled/disabled/masked — catches enabled-but-inactive) ---"
timeout 8 systemctl list-unit-files 'ict-*.service' 'ict-*.timer' \
    --no-legend --no-pager 2>/dev/null | head -60 \
    || echo "(systemctl list-unit-files unavailable)"

echo
echo "===== heartbeat ====="
# Resolve the heartbeat the same way the TRADER writes it. The trader runs with
# DATA_DIR=/data/bot-data (the live-VM data-dir drop-in), so heartbeat.txt lives
# under $DATA_DIR/runtime_logs — NOT the repo-relative runtime_logs. Reading only
# the repo path reported a ~30-day-stale heartbeat on 2026-06-10 while the trader
# was ticking fine (a false-alarm; exit 1 / "heartbeat missing"). Prefer the
# DATA_DIR path, then the canonical /data/bot-data mount, then the repo path.
HEARTBEAT=""
for _hb in \
    ${DATA_DIR:+"${DATA_DIR}/runtime_logs/heartbeat.txt"} \
    "/data/bot-data/runtime_logs/heartbeat.txt" \
    "${REPO_DIR}/runtime_logs/heartbeat.txt"; do
    if [ -f "${_hb}" ]; then HEARTBEAT="${_hb}"; break; fi
done
[ -z "${HEARTBEAT}" ] && HEARTBEAT="${REPO_DIR}/runtime_logs/heartbeat.txt"
if [ -f "${HEARTBEAT}" ]; then
    mtime="$(stat -c %Y "${HEARTBEAT}")"
    now="$(date +%s)"
    age=$(( now - mtime ))
    printf 'path: %s\n' "${HEARTBEAT}"
    printf 'mtime: %s\n' "$(date -u -d "@${mtime}" +%Y-%m-%dT%H:%M:%SZ)"
    printf 'age_sec: %d\n' "${age}"
else
    echo "MISSING: ${HEARTBEAT}"
    overall_ok=1
fi

echo
echo "===== journalctl -u ict-trader-live -n 20 ====="
# timeout 10 guards against D-Bus hangs; errors in journal output (e.g.
# RuntimeError, insufficient balance) are informational only and do NOT
# affect the exit code below.
timeout 10 journalctl -u ict-trader-live.service -n 20 --no-pager 2>/dev/null || \
    echo "(journalctl unavailable or timed out)"

echo
echo "===== tail -n 5 runtime_logs/signal_audit.jsonl ====="
AUDIT="${REPO_DIR}/runtime_logs/signal_audit.jsonl"
if [ -f "${AUDIT}" ]; then
    tail -n 5 "${AUDIT}"
else
    echo "(no audit log yet)"
fi

# ---------------------------------------------------------------------------
# CPU snapshot (added 2026-06-09, BL-20260609-001).
#
# The 2026-06-09 incident review could not identify WHAT was saturating the
# live VM (CPU 95-100%, which wedged the trader) because there is no
# arbitrary-bash relay for the live VM and this script captured no
# per-process CPU. This block closes that gap: a /health-review (or the
# status-check system-action) now surfaces the load average + the top CPU
# consumers + per-service cgroup CPU, so the hog can be named without SSH.
#
# Read-only, best-effort, bounded (head + cut). Never affects the exit code.
# ---------------------------------------------------------------------------
echo
echo "===== CPU snapshot (load + top consumers) ====="
echo "loadavg: $(cat /proc/loadavg 2>/dev/null || echo '(unreadable)')   nproc: $(nproc 2>/dev/null || echo '?')"
echo "--- ps: top 15 by %CPU (pid %cpu %mem etimes args, truncated) ---"
ps -eo pid,%cpu,%mem,etimes,args --sort=-%cpu 2>/dev/null | head -16 | cut -c1-140 \
    || echo "(ps unavailable)"
echo "--- systemd-cgtop: one-shot, ordered by CPU (per-service) ---"
timeout 8 systemd-cgtop --order=cpu --iterations=1 --batch 2>/dev/null | head -15 \
    || echo "(systemd-cgtop unavailable)"

# ---------------------------------------------------------------------------
# Memory snapshot (added 2026-06-14). The CPU block above sorts ps by %CPU,
# so a high-RSS but IDLE process (e.g. a leftover container, a heavy agent)
# never appears — which is exactly the gap that left the 2026-06-14 90.4%
# psutil reading un-attributed. This block sorts by RSS, shows free/swap,
# per-service cgroup MEMORY, swappiness, and any docker/containerd container
# (the IB gateway moved to its own VM on 2026-06-10, so the live micro should
# normally have NONE). Read-only, best-effort, never affects the exit code.
# ---------------------------------------------------------------------------
echo
echo "===== Memory snapshot (RSS-sorted; attribute used memory, not just CPU) ====="
echo "--- free -m (used / available / swap) ---"
free -m 2>/dev/null | head -3 || echo "(free unavailable)"
echo "swappiness: $(cat /proc/sys/vm/swappiness 2>/dev/null || echo '?')"
echo "--- ps: top 15 by RSS (pid %mem rss_kB etimes args, truncated) ---"
ps -eo pid,%mem,rss,etimes,args --sort=-rss 2>/dev/null | head -16 | cut -c1-140 \
    || echo "(ps unavailable)"
echo "--- systemd-cgtop: one-shot, ordered by MEMORY (per-service) ---"
timeout 8 systemd-cgtop --order=memory --iterations=1 --batch 2>/dev/null | head -15 \
    || echo "(systemd-cgtop unavailable)"
echo "--- docker/containerd containers (gateway isolated 2026-06-10 — expect none here) ---"
(docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' 2>/dev/null \
    || echo "(docker CLI unavailable / no perms / no daemon)") | head -10

# ---------------------------------------------------------------------------
# Runtime-data path diagnostic (added 2026-05-11 after the heartbeat
# writer froze silently and we couldn't tell whether it was failing or
# writing to a different path).
#
# What we want to know on each status check:
#   1. Is DATA_DIR / RUNTIME_LOGS_DIR set on the trader process? If yes
#      and the readers (diag.py / dashboard.py) still hardcode the repo
#      path, that explains a writer-vs-reader divergence.
#   2. What's actually in runtime_logs/ on disk? (file vs symlink vs
#      missing, mtimes for the three canonical signals.)
#   3. If DATA_DIR resolves to an alternative root, dump that root too.
#
# Read-only; no behaviour changes. Failures here never affect exit code.
# ---------------------------------------------------------------------------
echo
echo "===== runtime-data path diagnostic ====="
TRADER_PID="$(pgrep -f 'python3 -u -B -m src.main' | head -n 1 2>/dev/null || true)"
if [ -n "${TRADER_PID}" ] && [ -r "/proc/${TRADER_PID}/environ" ]; then
    echo "trader pid: ${TRADER_PID}"
    env_match="$(tr '\0' '\n' < "/proc/${TRADER_PID}/environ" 2>/dev/null \
        | grep -E '^(DATA_DIR|RUNTIME_LOGS_DIR|RUNTIME_STATE_DIR|ARTIFACTS_DIR)=' \
        || true)"
    if [ -n "${env_match}" ]; then
        echo "${env_match}"
    else
        echo "(no DATA_DIR / RUNTIME_LOGS_DIR / RUNTIME_STATE_DIR / ARTIFACTS_DIR in trader env)"
    fi
else
    echo "(trader pid not found or /proc unreadable — env dump skipped)"
fi

echo
echo "--- runtime_logs canonical files (repo path) ---"
for f in \
    "${REPO_DIR}/runtime_logs/heartbeat.txt" \
    "${REPO_DIR}/runtime_logs/runtime_status.json" \
    "${REPO_DIR}/runtime_logs/signal_audit.jsonl"; do
    if [ -e "${f}" ]; then
        stat -c '%n  type=%F  size=%s  mtime=%y' "${f}" 2>/dev/null || \
            ls -la "${f}"
    else
        printf '%s  (missing)\n' "${f}"
    fi
done

# If a non-repo root resolves from env, dump that side too so we can
# diff the two views side-by-side without a second roundtrip.
ALT_ROOT=""
data_dir=""
if [ -n "${TRADER_PID}" ] && [ -r "/proc/${TRADER_PID}/environ" ]; then
    runtime_logs_override="$(tr '\0' '\n' < "/proc/${TRADER_PID}/environ" \
        | awk -F= '$1=="RUNTIME_LOGS_DIR"{print $2; exit}')"
    data_dir="$(tr '\0' '\n' < "/proc/${TRADER_PID}/environ" \
        | awk -F= '$1=="DATA_DIR"{print $2; exit}')"
    if [ -n "${runtime_logs_override}" ]; then
        ALT_ROOT="${runtime_logs_override}"
    elif [ -n "${data_dir}" ]; then
        ALT_ROOT="${data_dir}/runtime_logs"
    fi
fi
if [ -n "${ALT_ROOT}" ] && [ "${ALT_ROOT}" != "${REPO_DIR}/runtime_logs" ]; then
    echo
    echo "--- runtime_logs alternative root (env-resolved) ${ALT_ROOT} ---"
    for f in \
        "${ALT_ROOT}/heartbeat.txt" \
        "${ALT_ROOT}/runtime_status.json" \
        "${ALT_ROOT}/signal_audit.jsonl"; do
        if [ -e "${f}" ]; then
            stat -c '%n  type=%F  size=%s  mtime=%y' "${f}" 2>/dev/null || \
                ls -la "${f}"
        else
            printf '%s  (missing)\n' "${f}"
        fi
    done
fi

# 2026-05-11 heartbeat-freeze round 2: the python resolver
# (src/utils/paths.py::runtime_logs_dir) anchors relative DATA_DIR
# under repo_root, NOT the calling process's CWD. The shell ALT_ROOT
# above does naive concatenation, so its missing/present judgement
# disagrees with python's view when DATA_DIR is relative. Show the
# absolute candidate explicitly + /proc/<pid>/cwd + drop-in contents
# so the next round of diagnosis doesn't need three roundtrips.
echo
echo "--- python-resolver-shaped candidate (repo_root + DATA_DIR) ---"
if [ -n "${data_dir}" ]; then
    data_dir_trim="${data_dir%/}"
    case "${data_dir_trim}" in
        /*) python_alt_root="${data_dir_trim}/runtime_logs" ;;
        *)  python_alt_root="${REPO_DIR}/${data_dir_trim}/runtime_logs" ;;
    esac
    echo "absolute candidate: ${python_alt_root}"
    for f in \
        "${python_alt_root}/heartbeat.txt" \
        "${python_alt_root}/runtime_status.json" \
        "${python_alt_root}/signal_audit.jsonl"; do
        if [ -e "${f}" ]; then
            stat -c '%n  type=%F  size=%s  mtime=%y' "${f}" 2>/dev/null || \
                ls -la "${f}"
        else
            printf '%s  (missing)\n' "${f}"
        fi
    done
    echo
    echo "--- ls ${python_alt_root}/ (parent must exist for writes) ---"
    ls -la "${python_alt_root}/" 2>&1 | head -30
else
    echo "(DATA_DIR unset; python resolver would return repo-relative paths)"
fi

echo
echo "--- trader CWD (from /proc/<pid>/cwd) ---"
if [ -n "${TRADER_PID}" ] && [ -r "/proc/${TRADER_PID}/cwd" ]; then
    cwd="$(readlink "/proc/${TRADER_PID}/cwd" 2>/dev/null || true)"
    if [ -n "${cwd}" ]; then
        echo "${cwd}"
    else
        echo "(cwd readlink returned empty)"
    fi
else
    echo "(trader pid not found or /proc/<pid>/cwd unreadable)"
fi

echo
echo "--- systemd drop-ins for ict-trader-live ---"
DROPIN_DIR="/etc/systemd/system/ict-trader-live.service.d"
if [ -d "${DROPIN_DIR}" ]; then
    for f in "${DROPIN_DIR}"/*; do
        [ -e "$f" ] || continue
        echo "===== ${f} ====="
        cat "${f}" 2>&1 || echo "(unreadable)"
    done
else
    echo "(no drop-in dir at ${DROPIN_DIR})"
fi

# Claude-ping drainer diagnosis (BL-20260718): the ict-claude-bridge +
# ict-telegram-bot drainers read $DATA_DIR/runtime_logs/pending_claude_pings
# via runtime_logs_dir(). They load ONLY .env (which has DATA_DIR stripped by
# fix_data_dir.sh), so WITHOUT a data-dir.conf drop-in they inherit no DATA_DIR
# and drain the REPO-path inbox — a different dir than the send-ping writer's
# canonical /data/bot-data inbox — silently dropping every Claude Telegram ping.
# Surface the drop-in presence + both inbox counts + delivery journal so the
# split is diagnosable from the reliable status-check (the /api/diag relay is
# not always reachable). Read-only; every step best-effort under set -e.
echo
echo "--- Claude-ping drainer drop-ins + inbox split (undelivered-ping diagnosis) ---"
for _u in ict-claude-bridge ict-telegram-bot; do
    _d="/etc/systemd/system/${_u}.service.d"
    if [ -f "${_d}/data-dir.conf" ]; then
        echo "== ${_u}: data-dir.conf PRESENT =="
        cat "${_d}/data-dir.conf" 2>/dev/null || true
    else
        echo "!! ${_u}: NO data-dir.conf drop-in at ${_d} — inherits no DATA_DIR → drains REPO-path inbox (PATH SPLIT)"
        ls -1 "${_d}" 2>/dev/null || true
    fi
done
echo "-- pending_claude_pings inbox dirs (writer uses /data/bot-data; drainer must match) --"
for _pd in /data/bot-data/runtime_logs/pending_claude_pings /home/ubuntu/ict-trading-bot/runtime_logs/pending_claude_pings; do
    _n="$(ls -1 "${_pd}"/*.json 2>/dev/null | wc -l | tr -d ' ' || true)"
    echo "  ${_pd}: ${_n:-0} queued .json"
done
echo "-- drainer journal (ping-delivery lines) --"
for _u in ict-claude-bridge ict-telegram-bot; do
    echo "== ${_u} =="
    { journalctl -u "${_u}.service" -n 250 --no-pager 2>/dev/null \
        | grep -iE "claude ping inbox|send skipped|trader-bot send|creds missing" \
        | tail -6; } || echo "  (no ping-delivery lines / journal unreadable)"
done

if [ "${overall_ok}" -eq 0 ]; then
    record_audit "status-check" "ok" "{\"all_active\": true}" >/dev/null || true
    log "All canonical services active."
    exit 0
else
    record_audit "status-check" "degraded" "{\"all_active\": false}" >/dev/null || true
    log "One or more canonical services are NOT active."
    exit 1
fi
