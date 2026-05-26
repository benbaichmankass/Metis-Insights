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
#
# Why cloudflared needs its own drop-in:
#   setup_named_cloudflare_tunnel.sh used Python's base64.b64decode to decode
#   the CF API token. The CF API returns URL-safe base64 (chars - and _);
#   standard b64decode raises binascii.Error, silently swallowed by
#   `2>/dev/null || true`. Credentials file written empty → cloudflared
#   crash-loops → Vercel 502 on /api/bot/* (2026-05-12 incident).
#   The drop-in switches to --token mode: raw token stored in tunnel.env,
#   passed directly to cloudflared with no decode.
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

_CF_DROPIN_SRC="${REPO_DIR}/deploy/dropins/cloudflared-token.conf"
_CF_DROPIN_DST="${SYSTEMD_DIR}/ict-cloudflared-tunnel.service.d/token.conf"
if [ -f "${_CF_DROPIN_SRC}" ]; then
    if [ ! -e "${_CF_DROPIN_DST}" ] || ! cmp -s "${_CF_DROPIN_SRC}" "${_CF_DROPIN_DST}"; then
        echo ">>> install_systemd_units: dropin cloudflared-token.conf → ${_CF_DROPIN_DST}"
        "${SUDO[@]}" mkdir -p "$(dirname "${_CF_DROPIN_DST}")"
        "${SUDO[@]}" cp "${_CF_DROPIN_SRC}" "${_CF_DROPIN_DST}"
        "${SUDO[@]}" chmod 0644 "${_CF_DROPIN_DST}"
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
_TGBOT_DROPIN_SRC="${REPO_DIR}/deploy/dropins/data-dir.conf"
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
_BRIDGE_DROPIN_SRC="${REPO_DIR}/deploy/dropins/data-dir.conf"
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
_INSIGHTS_DROPIN_SRC="${REPO_DIR}/deploy/dropins/data-dir.conf"
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
shopt -s nullglob
for timer_path in deploy/*.timer; do
    timer_name=$(basename "$timer_path")
    if [[ "$timer_name" == *@* ]]; then
        continue
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

exit 0
