#!/usr/bin/env bash
# =============================================================================
# Auto-install / refresh systemd units from deploy/ (S-018).
#
# The autonomous-deploy contract says: an operator pushes a commit
# from anywhere with `git push`, the VM picks it up via
# ict-git-sync.timer, services come up running the new code. New
# systemd units (e.g. deploy/ict-smoke-once.service shipped in S-017)
# used to require a manual `sudo cp ... && sudo systemctl daemon-reload`
# on the VM — defeating the promise. This script closes that gap.
#
# Behaviour:
#   - For every `deploy/*.service` and `deploy/*.timer` that's NOT a
#     systemd template (i.e. doesn't contain `@`), compare against
#     `/etc/systemd/system/<name>`. If different (or missing), copy
#     the new version in.
#   - Installs unit drop-ins from deploy/dropins/ for units that need
#     them (see drop-in section below for the explicit mapping).
#   - `daemon-reload` ONCE at the end if any change happened.
#   - DOES NOT enable / start / restart anything. The regular
#     `deploy_pull_restart.sh` flow handles restarts of the long-
#     running units.
#   - Idempotent — second run with no changes is a no-op.
#
# Wiring: called from scripts/deploy_pull_restart.sh after a HEAD-
# advancing pull, before the service-restart step.
#
# Required: passwordless sudo for `cp`, `systemctl daemon-reload` and
# `chmod` (already granted in the existing deploy environment).
# =============================================================================

set -uo pipefail

REPO_DIR=${REPO_DIR:-/home/ubuntu/ict-trading-bot}
SYSTEMD_DIR=${SYSTEMD_DIR:-/etc/systemd/system}

if [ "$(id -u)" -eq 0 ]; then
    SUDO=()
else
    SUDO=(sudo)
fi

cd "$REPO_DIR"

changed=0

# ---------------------------------------------------------------------------
# Select the data-dir drop-in flavor by mount topology.
#
# data-dir.conf binds units to the OCI block-storage mount
# (RequiresMountsFor=/data/bot-data + ExecStartPre preflight) — correct on
# the block-volume live VMs, where refusing to start on a broken mount is a
# feature. But on a host where /data/bot-data is a plain directory on the
# boot/root volume (the Ampere live candidate ict-bot-arm, post-2026-06-14
# cutover), that binding holds every unit in "activating" forever waiting
# for a mount unit that never appears — wedging the whole stack.
#
# So: pick the mount-binding data-dir.conf only when /data/bot-data is
# actually a mount; otherwise use the env-only data-dir-nomount.conf
# sibling (same DATA_DIR/TRADE_JOURNAL_DB env, no mount requirement). The
# same deploy script is then safe on both topologies.
# ---------------------------------------------------------------------------
_DATA_MOUNT="${DATA_DIR:-/data/bot-data}"
if mountpoint -q "$_DATA_MOUNT" 2>/dev/null; then
    _DATADIR_DROPIN_SRC="${REPO_DIR}/deploy/dropins/data-dir.conf"
    echo ">>> install_systemd_units: $_DATA_MOUNT is a mount — using data-dir.conf (mount-binding)"
else
    _DATADIR_DROPIN_SRC="${REPO_DIR}/deploy/dropins/data-dir-nomount.conf"
    echo ">>> install_systemd_units: $_DATA_MOUNT is NOT a mount — using data-dir-nomount.conf (env-only)"
fi

shopt -s nullglob
for unit_path in deploy/*.service deploy/*.timer; do
    unit_name=$(basename "$unit_path")
    # Skip systemd template units (e.g. claude-vm-runner@.service) —
    # those are installed once by the bootstrap script and don't get
    # mass-refreshed.
    if [[ "$unit_name" == *@* ]]; then
        continue
    fi

    target="$SYSTEMD_DIR/$unit_name"

    if [ ! -e "$target" ] || ! cmp -s "$unit_path" "$target"; then
        echo ">>> install_systemd_units: $unit_name → $target"
        "${SUDO[@]}" cp "$unit_path" "$target"
        "${SUDO[@]}" chmod 0644 "$target"
        changed=1
    fi
done
shopt -u nullglob

# ---------------------------------------------------------------------------
# Install unit drop-ins from deploy/dropins/.
#
# Listed explicitly (one variable per drop-in) so installs are transparent
# and auditable. Each drop-in is idempotently compared before copying.
#
# Why the watchdog needs its own drop-in:
#   check_heartbeat.py resolves DEFAULT_HEARTBEAT at module load time using
#   DATA_DIR. Without a drop-in, the watchdog inherits only .env (which has
#   no DATA_DIR after the fix-data-dir strip), falls back to
#   <repo>/runtime_logs/heartbeat.txt, and perpetually reads a stale
#   heartbeat even when the trader is healthy (2026-05-12 incident).
#   No service restart needed after installing the drop-in — the watchdog
#   is a oneshot fired by its timer; the next tick picks up the new env.
# ---------------------------------------------------------------------------
_WATCHDOG_DROPIN_SRC="${REPO_DIR}/deploy/dropins/watchdog-data-dir.conf"
_WATCHDOG_DROPIN_DST="${SYSTEMD_DIR}/ict-liveness-watchdog.service.d/data-dir.conf"
if [ -f "${_WATCHDOG_DROPIN_SRC}" ]; then
    if [ ! -e "${_WATCHDOG_DROPIN_DST}" ] || ! cmp -s "${_WATCHDOG_DROPIN_SRC}" "${_WATCHDOG_DROPIN_DST}"; then
        echo ">>> install_systemd_units: dropin watchdog-data-dir.conf → ${_WATCHDOG_DROPIN_DST}"
        "${SUDO[@]}" mkdir -p "$(dirname "${_WATCHDOG_DROPIN_DST}")"
        "${SUDO[@]}" cp "${_WATCHDOG_DROPIN_SRC}" "${_WATCHDOG_DROPIN_DST}"
        "${SUDO[@]}" chmod 0644 "${_WATCHDOG_DROPIN_DST}"
        changed=1
    fi
fi

# Why ict-telegram-bot needs the data-dir drop-in:
#   The bot's pending-pings drainer (src/bot/cloud_notifier) + journal
#   reads resolve through DATA_DIR-aware paths. Without a drop-in it
#   inherits no DATA_DIR (stripped from .env), falls back to
#   <repo>/runtime_logs, and drains a DIFFERENT directory than the
#   canonical writers: execution_diagnostics trade pings + a DATA_DIR-aware
#   send-ping write $DATA_DIR/runtime_logs/pending_pings, and the claude
#   bridge reads $DATA_DIR/runtime_logs/pending_claude_pings. That split
#   silently dropped trade-lifecycle + claude-channel pings (2026-05-25).
#   This is the same generic data-dir.conf the trader/bridge/web-api units
#   already carry, so the bot reads the canonical store too.
_TGBOT_DROPIN_SRC="${_DATADIR_DROPIN_SRC}"
_TGBOT_DROPIN_DST="${SYSTEMD_DIR}/ict-telegram-bot.service.d/data-dir.conf"
if [ -f "${_TGBOT_DROPIN_SRC}" ]; then
    if [ ! -e "${_TGBOT_DROPIN_DST}" ] || ! cmp -s "${_TGBOT_DROPIN_SRC}" "${_TGBOT_DROPIN_DST}"; then
        echo ">>> install_systemd_units: dropin data-dir.conf → ${_TGBOT_DROPIN_DST}"
        "${SUDO[@]}" mkdir -p "$(dirname "${_TGBOT_DROPIN_DST}")"
        "${SUDO[@]}" cp "${_TGBOT_DROPIN_SRC}" "${_TGBOT_DROPIN_DST}"
        "${SUDO[@]}" chmod 0644 "${_TGBOT_DROPIN_DST}"
        changed=1
    fi
fi

# Why ict-health-snapshot needs the data-dir drop-in:
#   write_health_snapshot.py resolves artifacts_dir() via src.utils.paths,
#   which is DATA_DIR-aware. ict-web-api (the READER of latest.json /
#   health_check_*.json) runs with DATA_DIR=/data/bot-data via its own
#   drop-in. Without this drop-in the writer would inherit no DATA_DIR
#   (stripped from .env), fall back to <repo>/artifacts, and write to a
#   DIFFERENT directory than the API reads — the writer/reader path-split
#   that froze the dashboard's health card at the 2026-05-11 snapshot
#   (BL-20260529-005). Same generic data-dir.conf the trader/web-api/
#   bridge/tgbot units carry, so writer and reader agree.
_HEALTHSNAP_DROPIN_SRC="${_DATADIR_DROPIN_SRC}"
_HEALTHSNAP_DROPIN_DST="${SYSTEMD_DIR}/ict-health-snapshot.service.d/data-dir.conf"
if [ -f "${_HEALTHSNAP_DROPIN_SRC}" ]; then
    if [ ! -e "${_HEALTHSNAP_DROPIN_DST}" ] || ! cmp -s "${_HEALTHSNAP_DROPIN_SRC}" "${_HEALTHSNAP_DROPIN_DST}"; then
        echo ">>> install_systemd_units: dropin data-dir.conf → ${_HEALTHSNAP_DROPIN_DST}"
        "${SUDO[@]}" mkdir -p "$(dirname "${_HEALTHSNAP_DROPIN_DST}")"
        "${SUDO[@]}" cp "${_HEALTHSNAP_DROPIN_SRC}" "${_HEALTHSNAP_DROPIN_DST}"
        "${SUDO[@]}" chmod 0644 "${_HEALTHSNAP_DROPIN_DST}"
        changed=1
    fi
fi

# Why ict-hourly-snapshot needs the data-dir drop-in:
#   scripts/send_hourly_now.py (fired by ict-hourly-snapshot.timer) calls
#   account_snapshots() in src/runtime/hourly_report.py, which WRITES
#   runtime_logs/balance_snapshots.json via runtime_logs_dir() (DATA_DIR-aware).
#   ict-web-api (the READER, /api/bot/accounts/balances) runs with
#   DATA_DIR=/data/bot-data via its own drop-in. Without this drop-in the
#   writer inherits no DATA_DIR (stripped from .env by fix_data_dir.sh),
#   falls back to <repo>/runtime_logs, and writes to a DIFFERENT directory
#   than the API reads — so the dashboard + risk-gate balance view froze at
#   the data-dir migration (~2026-05-25) while the repo-path copy kept
#   updating (BL-20260611-M15-2). Identical writer/reader path-split as the
#   ict-health-snapshot case above (BL-20260529-005). oneshot+timer: the next
#   timer tick (<=1h) picks up the new env after daemon-reload; no restart.
_HOURLYSNAP_DROPIN_SRC="${_DATADIR_DROPIN_SRC}"
_HOURLYSNAP_DROPIN_DST="${SYSTEMD_DIR}/ict-hourly-snapshot.service.d/data-dir.conf"
if [ -f "${_HOURLYSNAP_DROPIN_SRC}" ]; then
    if [ ! -e "${_HOURLYSNAP_DROPIN_DST}" ] || ! cmp -s "${_HOURLYSNAP_DROPIN_SRC}" "${_HOURLYSNAP_DROPIN_DST}"; then
        echo ">>> install_systemd_units: dropin data-dir.conf → ${_HOURLYSNAP_DROPIN_DST}"
        "${SUDO[@]}" mkdir -p "$(dirname "${_HOURLYSNAP_DROPIN_DST}")"
        "${SUDO[@]}" cp "${_HOURLYSNAP_DROPIN_SRC}" "${_HOURLYSNAP_DROPIN_DST}"
        "${SUDO[@]}" chmod 0644 "${_HOURLYSNAP_DROPIN_DST}"
        changed=1
    fi
fi

# Why ict-db-integrity needs the data-dir drop-in:
#   scripts/db_integrity_alert.py -> scripts/check_db_integrity.py resolves
#   trade_journal_db_path() via src.utils.paths (DATA_DIR-aware) and opens the
#   canonical DB read-only (mode=ro). ict-web-api / the trader run with
#   DATA_DIR=/data/bot-data via their own drop-ins. Without this drop-in the
#   checker inherits no DATA_DIR (stripped from .env by fix_data_dir.sh), falls
#   back to <repo>/trade_journal.db, and reads a DIFFERENT (empty/stale) DB than
#   the live trader writes — so the INV-1..5 guardrail would silently grade an
#   empty DB clean. Same writer/reader path-split rationale as the
#   ict-health-snapshot + ict-hourly-snapshot cases above.
_DBINTEGRITY_DROPIN_SRC="${_DATADIR_DROPIN_SRC}"
_DBINTEGRITY_DROPIN_DST="${SYSTEMD_DIR}/ict-db-integrity.service.d/data-dir.conf"
if [ -f "${_DBINTEGRITY_DROPIN_SRC}" ]; then
    if [ ! -e "${_DBINTEGRITY_DROPIN_DST}" ] || ! cmp -s "${_DBINTEGRITY_DROPIN_SRC}" "${_DBINTEGRITY_DROPIN_DST}"; then
        echo ">>> install_systemd_units: dropin data-dir.conf → ${_DBINTEGRITY_DROPIN_DST}"
        "${SUDO[@]}" mkdir -p "$(dirname "${_DBINTEGRITY_DROPIN_DST}")"
        "${SUDO[@]}" cp "${_DBINTEGRITY_DROPIN_SRC}" "${_DBINTEGRITY_DROPIN_DST}"
        "${SUDO[@]}" chmod 0644 "${_DBINTEGRITY_DROPIN_DST}"
        changed=1
    fi
fi

# Why ict-claude-bridge needs the data-dir drop-in:
#   The bridge is the SOLE drainer of the Claude update channel — it reads
#   $DATA_DIR/runtime_logs/pending_claude_pings (via runtime_logs_dir()).
#   Producers (send-ping system-action, notify_on_pull) write to that same
#   canonical inbox now that they load DATA_DIR. If the bridge inherits no
#   DATA_DIR (stripped from .env) it falls back to <repo>/runtime_logs and
#   drains a DIFFERENT directory than the writers — every Claude-channel
#   ping is silently dropped. This drop-in was previously only installed by
#   hand (deploy/dropins/README.md), so a reprovisioned VM (or one that
#   never ran the manual step) leaves the channel dark. Auto-installing it
#   here — same generic data-dir.conf the trader/web-api/telegram-bot units
#   carry — closes that gap. The bridge is a long-running unit, so
#   deploy_pull_restart.sh's restart enumeration picks up the new env on
#   the next deploy (it is not in DEPLOY_RESTART_SKIP). Idempotent: a no-op
#   when the drop-in is already present and identical.
_BRIDGE_DROPIN_SRC="${_DATADIR_DROPIN_SRC}"
_BRIDGE_DROPIN_DST="${SYSTEMD_DIR}/ict-claude-bridge.service.d/data-dir.conf"
if [ -f "${_BRIDGE_DROPIN_SRC}" ]; then
    if [ ! -e "${_BRIDGE_DROPIN_DST}" ] || ! cmp -s "${_BRIDGE_DROPIN_SRC}" "${_BRIDGE_DROPIN_DST}"; then
        echo ">>> install_systemd_units: dropin data-dir.conf → ${_BRIDGE_DROPIN_DST}"
        "${SUDO[@]}" mkdir -p "$(dirname "${_BRIDGE_DROPIN_DST}")"
        "${SUDO[@]}" cp "${_BRIDGE_DROPIN_SRC}" "${_BRIDGE_DROPIN_DST}"
        "${SUDO[@]}" chmod 0644 "${_BRIDGE_DROPIN_DST}"
        changed=1
    fi
fi

# ict-insights-generator.service drop-in (M13 S1):
#   Without DATA_DIR + TRADE_JOURNAL_DB, the cycle's Python subprocess
#   resolves trade_journal_db_path() to the repo-relative fallback
#   <repo>/trade_journal.db — a fresh empty file. Every data-source
#   query then logs "no such table: trades" and the LLM gets a zero-
#   row data window. The .env file alone is insufficient: it does not
#   declare these vars (the canonical paths come from this drop-in on
#   every other unit), AND a stray invalid line elsewhere in .env
#   (e.g. PR #2082's incomplete FCM-credential strip) aborts the
#   wrapper's `source .env` mid-file. The drop-in path bypasses both
#   problems because systemd parses Environment= directives directly,
#   so DATA_DIR + TRADE_JOURNAL_DB are in the inherited env when the
#   wrapper starts. Surfaced by the live-VM inspect-insights audit
#   on 2026-05-26 (issue #2096).
_INSIGHTS_DROPIN_SRC="${_DATADIR_DROPIN_SRC}"
_INSIGHTS_DROPIN_DST="${SYSTEMD_DIR}/ict-insights-generator.service.d/data-dir.conf"
if [ -f "${_INSIGHTS_DROPIN_SRC}" ]; then
    if [ ! -e "${_INSIGHTS_DROPIN_DST}" ] || ! cmp -s "${_INSIGHTS_DROPIN_SRC}" "${_INSIGHTS_DROPIN_DST}"; then
        echo ">>> install_systemd_units: dropin data-dir.conf → ${_INSIGHTS_DROPIN_DST}"
        "${SUDO[@]}" mkdir -p "$(dirname "${_INSIGHTS_DROPIN_DST}")"
        "${SUDO[@]}" cp "${_INSIGHTS_DROPIN_SRC}" "${_INSIGHTS_DROPIN_DST}"
        "${SUDO[@]}" chmod 0644 "${_INSIGHTS_DROPIN_DST}"
        changed=1
    fi
fi

# M13 S2 slow tier — per-strategy unit needs the same drop-in so its
# Python subprocess reads the canonical /data/bot-data/trade_journal.db.
_INSIGHTS_STRATEGIES_DROPIN_DST="${SYSTEMD_DIR}/ict-insights-generator-strategies.service.d/data-dir.conf"
if [ -f "${_INSIGHTS_DROPIN_SRC}" ]; then
    if [ ! -e "${_INSIGHTS_STRATEGIES_DROPIN_DST}" ] || ! cmp -s "${_INSIGHTS_DROPIN_SRC}" "${_INSIGHTS_STRATEGIES_DROPIN_DST}"; then
        echo ">>> install_systemd_units: dropin data-dir.conf → ${_INSIGHTS_STRATEGIES_DROPIN_DST}"
        "${SUDO[@]}" mkdir -p "$(dirname "${_INSIGHTS_STRATEGIES_DROPIN_DST}")"
        "${SUDO[@]}" cp "${_INSIGHTS_DROPIN_SRC}" "${_INSIGHTS_STRATEGIES_DROPIN_DST}"
        "${SUDO[@]}" chmod 0644 "${_INSIGHTS_STRATEGIES_DROPIN_DST}"
        changed=1
    fi
fi

# Why ict-shadow-log-rotate needs the data-dir drop-in:
#   rotate_shadow_log.py resolves the active log under runtime_logs_dir()
#   (DATA_DIR-aware) so it rotates the SAME /data/bot-data/runtime_logs/
#   shadow_predictions.jsonl the live trader appends to. Without DATA_DIR in
#   the unit's environment the resolver falls back to the repo-relative path
#   — a stale leftover under the DATA_DIR regime that never grows — so the
#   real log is never rotated and grows unbounded until it threatens the
#   boot volume (BL-20260614-SHADOWROT-NODATADIR). Same generic data-dir
#   drop-in (mount-aware flavor) the other writers carry.
_SHADOWROT_DROPIN_DST="${SYSTEMD_DIR}/ict-shadow-log-rotate.service.d/data-dir.conf"
if [ -f "${_DATADIR_DROPIN_SRC}" ]; then
    if [ ! -e "${_SHADOWROT_DROPIN_DST}" ] || ! cmp -s "${_DATADIR_DROPIN_SRC}" "${_SHADOWROT_DROPIN_DST}"; then
        echo ">>> install_systemd_units: dropin data-dir.conf → ${_SHADOWROT_DROPIN_DST}"
        "${SUDO[@]}" mkdir -p "$(dirname "${_SHADOWROT_DROPIN_DST}")"
        "${SUDO[@]}" cp "${_DATADIR_DROPIN_SRC}" "${_SHADOWROT_DROPIN_DST}"
        "${SUDO[@]}" chmod 0644 "${_SHADOWROT_DROPIN_DST}"
        changed=1
    fi
fi

if [ "$changed" -eq 1 ]; then
    echo ">>> install_systemd_units: daemon-reload"
    if ! "${SUDO[@]}" systemctl daemon-reload 2>&1; then
        echo ">>> install_systemd_units: WARN daemon-reload failed (no systemd in this env?)"
    fi
else
    echo ">>> install_systemd_units: nothing to refresh."
fi

# Auto-enable + start any timers shipped under deploy/. Service units
# are left alone — they're either oneshots fired by their timer, or
# long-running units managed by deploy_pull_restart.sh's restart step.
# Idempotent: enable --now on an already-enabled-and-active timer is
# a no-op.
#
# Why this exists: ict-liveness-watchdog.timer (2026-05-11 silent-
# failure incident) needs to start the moment the file lands on the VM.
# Before this step the operator had to SSH and run `systemctl enable
# --now ict-liveness-watchdog.timer` by hand, defeating the autonomous-
# deploy contract this script was added for.
#
# Topology guard (BL-20260614-INSTALLER-GATEWAY-TIMERS): the IB-Gateway
# timers belong ONLY on the dedicated gateway VM since the 2026-06-10
# gateway-isolation split. Blanket `enable --now` on the trader box (where
# they were installed by the deploy/*.timer glob) starts them probing a
# non-existent local container / unreachable IB and spamming alerts — so the
# Ampere cutover had to `systemctl mask` them by hand. Enable them only when
# this host is the gateway VM, signalled by the role marker /etc/ict-vm-role
# containing "gateway"; absent/any-other marker => trader box => skip. The
# unit files are still copied above (inert, harmless) — only the auto-enable
# is gated.
_GATEWAY_ONLY_TIMERS=" ict-ib-gateway-watchdog.timer ict-ib-gateway-reset.timer "
# Units allowed to RUN on the dedicated gateway VM (BL-20260622-GATEWAY-ISOLATE-UNITS).
# The gateway is a MINIMAL box: Docker IB-Gateway + stdlib units only, no bot
# venv / no .env. The trader-oriented timers (insights, db-integrity,
# hourly/health snapshot, liveness-watchdog, shadow-log-rotate, …) and the
# long-running trader services (web-api, trader-live, telegram-bot) must NOT run
# here — their services just fail / crash-loop. Earlier this installer enabled
# every non-gateway timer here unconditionally, and a stray git-sync fire started
# ict-web-api (crash-looping) + several timer services (failed) on the gateway.
_GATEWAY_ALLOWED_TIMERS=" ict-ib-gateway-watchdog.timer ict-ib-gateway-reset.timer ict-git-sync.timer ict-devnull-guard.timer "
_GATEWAY_ALLOWED_SERVICES=" ict-ib-gateway-watchdog.service ict-ib-gateway-reset.service ict-git-sync.service ict-devnull-guard.service "
_VM_ROLE="$(tr -d '[:space:]' < /etc/ict-vm-role 2>/dev/null || true)"

# Gateway isolation pruning: on the gateway VM, actively disable/stop any
# non-allowlisted ict-* unit a prior run or a stray deploy left
# enabled/active/failed, so the box converges to "gateway units only".
# Idempotent + gateway-scoped (the trader box never enters this block).
if [ "$_VM_ROLE" = "gateway" ]; then
    echo ">>> install_systemd_units: gateway isolation — pruning non-gateway ict-* units"
    while read -r _t; do
        [ -n "$_t" ] || continue
        case "$_GATEWAY_ALLOWED_TIMERS" in *" $_t "*) continue;; esac
        echo ">>> [gateway] disabling stray timer: $_t"
        "${SUDO[@]}" systemctl disable --now "$_t" 2>/dev/null || true
    done < <("${SUDO[@]}" systemctl list-unit-files 'ict-*.timer' --no-legend 2>/dev/null | awk '{print $1}')
    while read -r _s; do
        [ -n "$_s" ] || continue
        case "$_GATEWAY_ALLOWED_SERVICES" in *" $_s "*) continue;; esac
        echo ">>> [gateway] stopping+disabling stray service: $_s"
        "${SUDO[@]}" systemctl disable --now "$_s" 2>/dev/null || true
        "${SUDO[@]}" systemctl reset-failed "$_s" 2>/dev/null || true
    done < <("${SUDO[@]}" systemctl list-units 'ict-*.service' --all --state=active,activating,failed --plain --no-legend 2>/dev/null | awk '{print $1}')
fi

# Retired timers (notification-streamlining 2026-07-08): the daily 13:00 UTC
# operator digest (ict-heartbeat.timer → daily_heartbeat.py) is superseded by
# the hourly snapshot (ict-hourly-snapshot.timer), which now folds in the
# training/ML section, and by the once-an-hour consolidated prop pulse. The
# unit files are kept (inert) so re-enabling is trivial and nothing that
# references them breaks; this block ACTIVELY disables an already-enabled timer
# on the VM, and the enable loop below SKIPS it so a deploy never re-enables it.
_RETIRED_TIMERS=" ict-heartbeat.timer "
for _rt in $_RETIRED_TIMERS; do
    # Unconditional idempotent disable — `disable --now` on an already-disabled
    # timer is a harmless no-op. Deliberately NOT guarded on `is-enabled
    # >/dev/null` because this box periodically loses /dev/null write perms
    # (the reason ict-devnull-guard exists); a guard whose redirect fails would
    # silently skip the disable. `|| true` keeps a genuinely-absent unit from
    # failing the deploy.
    echo ">>> install_systemd_units: retiring $_rt (superseded by the hourly snapshot)"
    "${SUDO[@]}" systemctl disable --now "$_rt" >/dev/null 2>&1 || true
done

shopt -s nullglob
for timer_path in deploy/*.timer; do
    timer_name=$(basename "$timer_path")
    if [[ "$timer_name" == *@* ]]; then
        continue
    fi
    # Retired timers are never (re-)enabled — disabled once above, kept inert.
    if [[ "$_RETIRED_TIMERS" == *" $timer_name "* ]]; then
        echo ">>> install_systemd_units: skip enable $timer_name (retired; superseded by hourly snapshot)"
        continue
    fi
    if [ "$_VM_ROLE" = "gateway" ]; then
        # Gateway VM: enable ONLY the gateway allowlist.
        if [[ "$_GATEWAY_ALLOWED_TIMERS" != *" $timer_name "* ]]; then
            echo ">>> install_systemd_units: skip enable $timer_name (not in gateway allowlist; host role=gateway)"
            continue
        fi
    else
        # Trader box: skip the gateway-only timers (they'd probe a missing container).
        if [[ "$_GATEWAY_ONLY_TIMERS" == *" $timer_name "* ]]; then
            echo ">>> install_systemd_units: skip enable $timer_name (gateway-only; host role='${_VM_ROLE:-unset}')"
            continue
        fi
    fi
    if "${SUDO[@]}" systemctl is-enabled "$timer_name" >/dev/null 2>&1 \
        && "${SUDO[@]}" systemctl is-active "$timer_name" >/dev/null 2>&1; then
        continue
    fi
    echo ">>> install_systemd_units: enable --now $timer_name"
    if ! "${SUDO[@]}" systemctl enable --now "$timer_name" 2>&1; then
        echo ">>> install_systemd_units: WARN could not enable $timer_name (no systemd? not yet installed?)"
    fi
done
shopt -u nullglob

# ict-claude-bridge is a CORE always-on service (the prop / Claude-comms bot),
# NOT a oneshot — but unlike the trader / web-api / telegram services (enabled
# at provisioning), it was left disabled after the 2026-06-14 Ampere cutover and
# silently stayed dark (its TELEGRAM_CLAUDE_BOT_TOKEN also didn't carry over), so
# prop tickets fell back to the trader bot. Enable + start it on the trader box
# so it survives a reboot like the other core services; the gateway-prune block
# above keeps it off the gateway VM. Idempotent (skip when already enabled +
# active) and tolerant of a failed start (e.g. token not yet synced) so a deploy
# never hard-fails on it.
if [ "$_VM_ROLE" != "gateway" ] && [ -f deploy/ict-claude-bridge.service ]; then
    if "${SUDO[@]}" systemctl is-enabled ict-claude-bridge.service >/dev/null 2>&1 \
        && "${SUDO[@]}" systemctl is-active ict-claude-bridge.service >/dev/null 2>&1; then
        :  # already enabled + running — nothing to do
    else
        echo ">>> install_systemd_units: enable --now ict-claude-bridge.service (core always-on)"
        if ! "${SUDO[@]}" systemctl enable --now ict-claude-bridge.service 2>&1; then
            echo ">>> install_systemd_units: WARN could not enable ict-claude-bridge.service (no systemd? token unset?)"
        fi
        changed=1
    fi
fi

exit 0
