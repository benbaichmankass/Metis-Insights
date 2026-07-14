#!/usr/bin/env bash
# ============================================
# DEPLOY PULL RESTART SCRIPT
# Run on Oracle VM to sync to origin/main and restart services.
#
# IMPORTANT: The VM is a read-only mirror of origin/main. This script uses
# `git fetch && git reset --hard origin/main` rather than `git pull` so any
# accidental local commits or dirty working tree on the VM are wiped out
# on the next sync. Never commit on the VM — always commit through GitHub.
#
# Usage: bash scripts/deploy_pull_restart.sh
#
# Verify locally before merging:
#   bash -n scripts/deploy_pull_restart.sh
#   shellcheck scripts/deploy_pull_restart.sh
# ============================================

set -euo pipefail

REPO_DIR="/home/ubuntu/ict-trading-bot"

# ---------------------------------------------------------------------------
# Self-heal a non-writable /dev/null BEFORE anything redirects to it.
#
# An OS-level host agent on this OCI VM (suspected oracle-cloud-agent
# oci-wlp / workload-protection FIM) intermittently chmods /dev/null to
# 0444. That makes every `>/dev/null` EACCES for this NON-root deploy user,
# so the very next line — the `sudo -n systemctl ... >/dev/null 2>&1` probe —
# fails and `set -e` aborts the whole deploy. On 2026-06-15 that wedged
# auto-deploy for ~16h (a merged monitor fix never reached the trader).
# `[ -w ]` is reliable here because this runs as `ubuntu` (non-root), so it
# correctly sees the stripped write bit. Best-effort: if sudo can't chmod,
# the standalone ict-devnull-guard.timer heals it within <=60 s anyway.
if [ ! -w /dev/null ]; then
    echo ">>> /dev/null not writable (perms stripped to 0444 by a host agent) — restoring 0666"
    sudo -n chmod 0666 /dev/null || echo ">>> WARNING: could not chmod /dev/null (ict-devnull-guard.timer will heal it)"
fi

# ---------------------------------------------------------------------------
# Detect sudo capability once at startup and build a reusable helper array.
# Running as root: no sudo needed. Otherwise require NOPASSWD sudo for systemctl.
# ---------------------------------------------------------------------------
if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    echo "ERROR: Cannot invoke systemctl. Grant passwordless sudo for systemctl:" >&2
    echo "       ubuntu ALL=(ALL) NOPASSWD: /bin/systemctl" >&2
    exit 1
fi

echo "===== DEPLOY STARTED: $(date) ====="

cd "$REPO_DIR"

# ---------------------------------------------------------------------------
# Capture current HEAD before we sync. Together with the deploy marker
# (runtime_logs/deployed_sha.txt, read at the restart-decision gate below) this
# decides whether to re-install dependencies and restart: the restart is driven
# off the SHA the running processes were last deployed onto, NOT merely whether
# THIS fetch moved HEAD — so a manual `git reset --hard` (or any sync that
# advances HEAD out of band) cannot leave the running Python processes pinned to
# stale code (BL-20260714-DEPLOY-STALE-ON-OOB-SYNC).
# ---------------------------------------------------------------------------
PRE_SYNC_HEAD=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
echo ">>> Pre-sync HEAD: ${PRE_SYNC_HEAD}"

echo ">>> Fetching latest from origin..."
# BL-20260706-GITSYNC-AUTH-BROKEN: the repo went private 2026-07-06, so the
# plain anonymous HTTPS fetch this script relied on since inception no longer
# authenticates ("could not read Username for 'https://github.com'"). The
# credential lives in a GLOBAL git config value
# (http.https://github.com/.extraheader, a Basic-auth header built from a
# fine-grained Contents:Read-only PAT) set once by the one-shot
# vm-git-credential-bootstrap.yml workflow — NOT per-invocation here.
#
# An earlier version of this fix attached the SAME header again per-call via
# `git -c http...extraheader=X fetch`. http.extraheader is a documented
# MULTI-VALUED git config key, so that -c value ADDED to (never replaced)
# the already-global one — git sent BOTH as separate Authorization headers,
# and GitHub's HTTP layer flat-out rejects that: "remote: Duplicate header:
# Authorization" / 400 (confirmed live, BL-20260706-GITSYNC-AUTH-BROKEN
# follow-up). A `-c ...extraheader=` (empty) does NOT clear a multi-valued
# key's other sources either — verified experimentally, not merely assumed.
# So: exactly ONE source of this credential, ever — the global config —
# and this script trusts whatever git already knows how to authenticate
# with, same as before the repo ever went private.
#
# Re-provisioning a fresh VM needs vm-git-credential-bootstrap.yml re-run
# once (its own header explains why) — the global config doesn't survive a
# fresh clone/home-dir.
git fetch --prune origin

echo ">>> Hard-resetting to origin/main (VM is a read-only mirror)..."
git reset --hard origin/main

POST_SYNC_HEAD=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
echo ">>> Post-sync HEAD: ${POST_SYNC_HEAD}"

# ---------------------------------------------------------------------------
# GATEWAY VM short-circuit (BL-20260622-GATEWAY-GIT-SYNC).
#
# The dedicated IB-Gateway VM is a MINIMAL box: it runs only the Docker
# IB-Gateway container + a couple of stdlib-only systemd units
# (ict-ib-gateway-{watchdog,reset}). It deliberately has NO bot venv and NO
# .env. The rest of this script is the TRADER deploy: it `pip install -r
# requirements.txt` (the full bot dependency tree) and restarts every
# enumerated ict-*.service. Neither belongs on the gateway VM — a pip install
# would bloat the minimal box, and the service enumeration could START the
# trader/web-api there (they're copied as unit files but never meant to run).
# So on the gateway, do the gateway-appropriate deploy only: refresh the unit
# FILES (install_systemd_units.sh is idempotent + role-gates its own enables)
# and bounce the gateway timers so a changed cadence/ExecStart takes effect —
# then exit BEFORE the pip + trader-service-restart section. Keyed on the
# /etc/ict-vm-role marker (written by provision_ib_gateway.sh). The live trader
# VM has no marker (correctly — it must NOT enable the gateway watchdog), so
# guard the read with a readability test FIRST: an input redirection `< file`
# that fails is reported by the shell BEFORE the command's `2>/dev/null` takes
# effect, leaking a "No such file or directory" warning every deploy
# (BL-20260623-VMROLE). The `[ -r ]` guard makes a missing marker a clean
# empty-role default (= non-gateway), no noise.
VM_ROLE=""
[ -r /etc/ict-vm-role ] && VM_ROLE="$(tr -d '[:space:]' < /etc/ict-vm-role 2>/dev/null || true)"
if [ "${VM_ROLE}" = "gateway" ]; then
    if [ "${PRE_SYNC_HEAD}" = "${POST_SYNC_HEAD}" ]; then
        echo ">>> [gateway] HEAD unchanged (${POST_SYNC_HEAD:0:7}); nothing to deploy."
        echo "===== DEPLOY COMPLETE (gateway no-op): $(date) ====="
        exit 0
    fi
    echo ">>> [gateway] ${PRE_SYNC_HEAD:0:7} -> ${POST_SYNC_HEAD:0:7}: refreshing units (NO pip, NO trader restart)."
    if bash "${REPO_DIR}/scripts/install_systemd_units.sh"; then
        echo ">>> [gateway] systemd units in sync."
    else
        echo ">>> [gateway] WARNING: install_systemd_units.sh exited nonzero — see journal."
    fi
    # Bounce the gateway timers so a changed OnUnitActiveSec/ExecStart lands now
    # (daemon-reload alone may not reschedule an already-active timer). Tolerant:
    # a unit that isn't installed on this host is simply skipped.
    "${SYSTEMCTL[@]}" restart ict-ib-gateway-watchdog.timer ict-ib-gateway-reset.timer 2>/dev/null \
        || echo ">>> [gateway] note: could not restart one/both gateway timers (may not be installed)."
    echo "===== DEPLOY COMPLETE (gateway): $(date) ====="
    exit 0
fi

# ---------------------------------------------------------------------------
# S-020: Telegram ping fanout, state-file driven.
#
# We compare against the LAST-NOTIFIED head (persisted in
# runtime_logs/notify_state.txt), NOT this run's PRE_SYNC_HEAD. Why:
# during S-019 debugging the operator manually `git reset --hard`d to
# advance HEAD outside the timer's window. PRE_SYNC_HEAD only remembers
# this run, so the next tick sees pre==post and skipped the ping —
# CP-2026-04-30-15 (#226) was permanently lost. The state file fixes
# that: as long as POST_SYNC_HEAD differs from what we last pinged for,
# we ping, regardless of how HEAD got there.
#
# Auto-ping test flag: if runtime_flags/auto_ping_test.flag exists, we
# force a notify_on_pull run with --force-checkpoint, which emits a
# checkpoint ping even if the diff doesn't naturally include
# CHECKPOINT_LOG.md. The flag file is consumed (deleted) on success.
#
# Failures here are logged but do NOT abort the deploy: a broken ping
# channel must not break the deploy channel. The state file is updated
# only on success, so the next tick retries.
# ---------------------------------------------------------------------------
NOTIFY_STATE_DIR="${REPO_DIR}/runtime_logs"
NOTIFY_STATE_FILE="${NOTIFY_STATE_DIR}/notify_state.txt"
AUTO_PING_TEST_FLAG="${REPO_DIR}/runtime_flags/auto_ping_test.flag"
mkdir -p "${NOTIFY_STATE_DIR}"
LAST_NOTIFIED_HEAD=$(cat "${NOTIFY_STATE_FILE}" 2>/dev/null || true)
# Bootstrap: on first run after this fix lands the state file is absent.
# notify_on_pull.py treats "unknown" as a hard short-circuit (no diff,
# no blocker scan), so we'd silently miss the very first checkpoint
# ping. Default to HEAD~1 so the merge commit's diff (which includes
# CHECKPOINT_LOG.md when this PR lands) actually fires a ping.
if [ -z "${LAST_NOTIFIED_HEAD}" ]; then
    LAST_NOTIFIED_HEAD=$(git rev-parse HEAD~1 2>/dev/null || echo "unknown")
    echo ">>> No notify_state.txt — bootstrapping with HEAD~1=${LAST_NOTIFIED_HEAD:0:7}"
fi

NOTIFY_ARGS=(--pre "${LAST_NOTIFIED_HEAD}" --post "${POST_SYNC_HEAD}")
if [ -f "${AUTO_PING_TEST_FLAG}" ]; then
    echo ">>> auto_ping_test.flag detected — adding --force-checkpoint"
    NOTIFY_ARGS+=(--force-checkpoint)
fi

if [ "${LAST_NOTIFIED_HEAD}" != "${POST_SYNC_HEAD}" ] || [ -f "${AUTO_PING_TEST_FLAG}" ]; then
    echo ">>> Sending Telegram pings (last_notified=${LAST_NOTIFIED_HEAD:0:7} -> head=${POST_SYNC_HEAD:0:7})..."
    if /usr/bin/python3 scripts/notify_on_pull.py "${NOTIFY_ARGS[@]}"; then
        echo ">>> Pings dispatched."
        echo "${POST_SYNC_HEAD}" > "${NOTIFY_STATE_FILE}"
        if [ -f "${AUTO_PING_TEST_FLAG}" ]; then
            rm -f "${AUTO_PING_TEST_FLAG}"
            echo ">>> Consumed auto_ping_test.flag."
        fi
    else
        echo ">>> WARNING: notify_on_pull exited nonzero — leaving state file untouched so next tick retries."
    fi
else
    echo ">>> notify state already at HEAD (${POST_SYNC_HEAD:0:7}); no pings to send."
fi

# ---------------------------------------------------------------------------
# Restart only when HEAD actually moved during THIS run.
#
# Originally we restarted unconditionally on every 5-minute git-sync tick,
# reasoning that a no-op restart is cheap. That broke the S-014.5
# Telegram-dispatched VM runner: a /vm invocation that lands within ~30 s
# of the next git-sync tick gets killed by the bot restart (the wrapper
# subprocess is in the bot's cgroup and dies with it).
#
# We now restart ONLY when the new HEAD differs from the pre-sync HEAD.
# Trade-off: if an operator does a manual `git reset --hard` to a different
# revision and the timer happens not to advance HEAD on its next tick,
# the running Python processes will hold the previous in-memory copy
# until the next deploy. That is a rare path and is handled by a manual
# `sudo systemctl restart ict-trader-live ict-telegram-bot ict-web-api`.
#
# Defense in depth: even when HEAD advances, skip the restart if any
# claude-vm-runner@*.service unit is currently active — the next
# git-sync tick (5 min) will pick up the change with no /vm in flight.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# BL-20260714-DEPLOY-STALE-ON-OOB-SYNC: restart when the RUNNING code is stale,
# not merely when THIS fetch moved HEAD.
#
# The old gate skipped the restart whenever PRE_SYNC_HEAD == POST_SYNC_HEAD —
# "this fetch pulled nothing new". But HEAD can advance on disk WITHOUT these
# long-running Python processes being bounced: a manual `git reset --hard`, or
# any sync path that moved the worktree without a restart. The processes then
# hold the OLD in-memory code while HEAD (and this run's PRE==POST) look clean,
# so the deploy skipped and the change never went live — the exact gap the
# PRE_SYNC_HEAD comment above flagged as "handled by a manual restart".
#
# We record the SHA the services were last (re)started onto in a marker file
# (runtime_logs/deployed_sha.txt — alongside notify_state.txt; both survive the
# `git reset --hard` above because runtime_logs/ is untracked) and drive the
# restart decision off THAT, so drift is caught however HEAD got where it is.
# Fail-safe: an absent/unresolvable marker falls back to PRE_SYNC_HEAD (the
# historical behaviour), so a fresh VM never restart-storms on an unknown —
# only a POSITIVELY-recorded older SHA forces a restart when HEAD didn't move.
# ---------------------------------------------------------------------------
DEPLOYED_SHA_FILE="${REPO_DIR}/runtime_logs/deployed_sha.txt"
LAST_DEPLOYED_SHA="$(tr -d '[:space:]' < "${DEPLOYED_SHA_FILE}" 2>/dev/null || true)"
if [ -n "${LAST_DEPLOYED_SHA}" ] && git rev-parse -q --verify "${LAST_DEPLOYED_SHA}^{commit}" >/dev/null 2>&1; then
    RUNTIME_BASE="${LAST_DEPLOYED_SHA}"
else
    RUNTIME_BASE="${PRE_SYNC_HEAD}"
fi

if [ "${RUNTIME_BASE}" = "${POST_SYNC_HEAD}" ]; then
    echo ">>> Running processes already deployed at ${POST_SYNC_HEAD:0:7}; nothing to deploy."
    echo "===== DEPLOY COMPLETE: $(date) ====="
    exit 0
fi
if [ "${PRE_SYNC_HEAD}" = "${POST_SYNC_HEAD}" ]; then
    echo ">>> This fetch moved nothing (HEAD ${POST_SYNC_HEAD:0:7}), but running code is at ${RUNTIME_BASE:0:7} — deploying to clear drift."
fi

# ---------------------------------------------------------------------------
# BL-20260529-002: skip the disruptive restart for NON-RUNTIME commits.
#
# A docs/comms-only commit (a session-end handoff, a health-review backlog
# touch, a sprint log) used to run the full dependency-install + systemd
# unit-refresh + restart-every-ict-*-service path — bouncing the live
# money-path trader (and blinding the web-api read surface for ~5-7 min)
# for a change that touches no code the running processes load. Observed
# three /health-reviews running (2026-05-29 x2, 2026-06-01) where a pure
# docs commit reset the trader uptime.
#
# We diff PRE..POST and, if EVERY changed path is in a known-safe,
# non-runtime set (docs/, tests/, .claude/, .github/, and top-level *.md),
# skip the restart entirely: the working tree was already synced by the
# hard-reset above and the Telegram pings already fired. .github/ is safe
# because Actions workflows run on GitHub-hosted runners, never on this VM,
# so the long-running Python processes never load them — a workflow-only
# merge (the bulk of the diag/ops PRs; 2026-06-15 had a full day of them,
# each triggering a needless restart attempt) must not bounce the live
# trader. ANYTHING else — src/, ml/,
# config/, deploy/, scripts/, comms/ (read at runtime by the insights /
# order-package / comms-handler paths), requirements*, pyproject, etc. —
# falls through to the normal restart below.
#
# FAIL-SAFE: if the diff cannot be computed, or comes back empty while HEAD
# moved, we DO restart. An unnecessary restart is the prior status quo; a
# MISSED restart would pin the running processes to stale code. Only a
# non-empty, fully-safe diff is allowed to skip. Override: set
# DEPLOY_FORCE_RESTART=1 to force the restart path regardless of the diff.
# ---------------------------------------------------------------------------
if [ "${DEPLOY_FORCE_RESTART:-0}" != "1" ]; then
    # Diff from RUNTIME_BASE (the SHA the running code is actually on) rather
    # than PRE_SYNC_HEAD, so a drift deploy (HEAD didn't move this fetch, but
    # the marker is behind) evaluates the files that changed since the running
    # processes started — not an empty PRE..POST diff.
    CHANGED_FILES="$(git diff --name-only "${RUNTIME_BASE}" "${POST_SYNC_HEAD}" 2>/dev/null || true)"
    if [ -n "${CHANGED_FILES}" ]; then
        # Strip the known-safe non-runtime paths; anything left needs a restart.
        RUNTIME_CHANGES="$(printf '%s\n' "${CHANGED_FILES}" \
            | grep -vE '^(docs/|tests/|\.claude/|\.github/|[^/]+\.md$)' || true)"
        if [ -z "${RUNTIME_CHANGES}" ]; then
            echo ">>> Non-runtime commit (${RUNTIME_BASE:0:7} -> ${POST_SYNC_HEAD:0:7}): only docs/tests/.claude/top-level-markdown changed."
            echo ">>> Code synced + pings sent; skipping dependency install, unit refresh, and service restart (BL-20260529-002)."
            printf '%s\n' "${CHANGED_FILES}" | sed 's/^/>>>   changed: /'
            # The running processes' RUNTIME code already matches POST (only
            # non-loaded files differ), so record it as deployed — otherwise
            # this skip would re-fire every 5-min tick while the marker lags HEAD.
            printf '%s\n' "${POST_SYNC_HEAD}" > "${DEPLOYED_SHA_FILE}" 2>/dev/null || true
            echo "===== DEPLOY COMPLETE (no runtime change; restart skipped): $(date) ====="
            exit 0
        fi
    fi
fi

echo ">>> Code changed (${PRE_SYNC_HEAD:0:7} -> ${POST_SYNC_HEAD:0:7}). Installing/updating dependencies..."
/usr/bin/python3 -m pip install -r requirements.txt --quiet

# ---------------------------------------------------------------------------
# S-018 fix: auto-refresh systemd units from deploy/.
#
# Closes the gap that caused operator frustration: new .service / .timer
# files used to require manual `sudo cp ... && systemctl daemon-reload`.
# The installer is idempotent (compares each unit against /etc/systemd
# /system; only copies + reloads on diff) and never restarts anything —
# the existing flow below handles restarts for long-running units.
# ---------------------------------------------------------------------------
echo ">>> Refreshing systemd units from deploy/..."
if bash "${REPO_DIR}/scripts/install_systemd_units.sh"; then
    echo ">>> Systemd units in sync."
else
    echo ">>> WARNING: install_systemd_units.sh exited nonzero — see journal."
fi

if "${SYSTEMCTL[@]}" list-units 'claude-vm-runner@*.service' --state=active --no-legend 2>/dev/null | grep -q .; then
    echo ">>> A claude-vm-runner unit is active — deferring service restart to the next sync tick to avoid killing an in-flight /vm invocation."
    echo "===== DEPLOY COMPLETE (restart deferred): $(date) ====="
    exit 0
fi

# ---------------------------------------------------------------------------
# S-067 follow-up #5: enumerate ict-* services from systemd rather than
# carrying a fixed list. The 2026-05-09 24+h-stale-code incident
# happened because ict-web-api.service was added to the deploy unit
# inventory after the script was last touched, and the script's
# explicit list silently missed it. Enumeration closes that class of
# bug — any new ict-*.service file dropped under /etc/systemd/system/
# is automatically restarted on the next deploy.
#
# Skip-list (DEPLOY_RESTART_SKIP, space-separated unit names) is the
# escape hatch for one-shot units (ict-smoke-once.service has a
# dedicated trigger below) and any unit the operator wants to manage
# out-of-band. The default skip-list covers the units that should not
# be restarted on every deploy.
#
# ict-smoke-once.service is a oneshot — restarting it would re-run the
# smoke test on every deploy; gated below by the run_smoke_once.flag
# instead.
# ict-env-check.service is a oneshot run on bootup; re-running on
# every deploy is wasteful and may produce confusing duplicate alerts.
# ict-hourly-snapshot.service is gated by its timer.
# ict-heartbeat.service is gated by its timer.
# ict-git-sync.service is the sync timer's payload — restarting it
# from inside its own run causes systemd to refuse the request.
# ict-mes-ibkr-pull.service is a oneshot owned by ict-mes-ibkr-pull.timer
# (daily) — restarting it on every deploy would fire an unscheduled ~20-30 min
# IBKR gateway pull each time (flock/heartbeat-guarded, but wasteful). Let the
# timer own it (BL-20260626-MES-BASE-STALE).
# ict-exchange-fills-pull.service is a oneshot owned by
# ict-exchange-fills-pull.timer (daily) — restarting it on every deploy would
# fire an unscheduled Bybit fills pull each time (cheap + idempotent, but
# needless). Let the timer own it (BL-20260713-EXCHANGE-FILLS-STORE-EMPTY).
# ---------------------------------------------------------------------------
DEFAULT_SKIP="ict-smoke-once.service ict-env-check.service ict-hourly-snapshot.service ict-heartbeat.service ict-git-sync.service ict-mes-ibkr-pull.service ict-exchange-fills-pull.service"
SKIP_LIST="${DEPLOY_RESTART_SKIP:-${DEFAULT_SKIP}}"

# list-units --all surfaces inactive units too; --type=service excludes
# .timer/.socket/etc. so we don't try to "restart" a timer (systemctl
# refuses). Some platforms emit padding spaces — awk handles both. Anchor
# the awk output to real ict-*.service names so a stray header/blank line
# can't sneak in.
mapfile -t ICT_UNITS < <(
    "${SYSTEMCTL[@]}" list-units --all --type=service --plain --no-legend 'ict-*.service' \
        2>/dev/null | awk '{print $1}' | grep -E '^ict-.*\.service$' | sort -u
)

# BL-20260615-DEPLOY-NOOP: on the Ampere live VM, `list-units 'ict-*.service'`
# was observed returning ZERO matches even though the units are active — so
# every code deploy printed "enumeration: 0 ict-* unit(s)" and restarted
# NOTHING, silently pinning the live trader to stale code (and forcing manual
# restart-bot-service / pull-and-deploy actions all day, which looked like a
# restart loop). A deploy must NEVER silently restart nothing. Fall back to
# the unit-FILE view (independent of runtime load state), then to an explicit
# canonical long-running-unit list as a last resort.
if [ "${#ICT_UNITS[@]}" -eq 0 ]; then
    echo ">>> list-units matched no ict-*.service; falling back to list-unit-files."
    mapfile -t ICT_UNITS < <(
        "${SYSTEMCTL[@]}" list-unit-files --type=service --plain --no-legend 'ict-*.service' \
            2>/dev/null | awk '{print $1}' | grep -E '^ict-.*\.service$' | sort -u
    )
fi
if [ "${#ICT_UNITS[@]}" -eq 0 ]; then
    echo ">>> WARNING: neither list-units nor list-unit-files matched ict-*.service."
    echo ">>>   Using the explicit canonical long-running-unit list so the deploy"
    echo ">>>   never silently restarts nothing (BL-20260615-DEPLOY-NOOP)."
    ICT_UNITS=(ict-trader-live.service ict-web-api.service ict-telegram-bot.service)
fi

echo ">>> Restarting services (enumeration: ${#ICT_UNITS[@]} ict-* unit(s))..."
RESTARTED_UNITS=()
for unit in "${ICT_UNITS[@]}"; do
    skip=0
    for skip_unit in ${SKIP_LIST}; do
        if [ "${unit}" = "${skip_unit}" ]; then
            skip=1
            break
        fi
    done
    if [ "${skip}" -eq 1 ]; then
        echo ">>>   skip ${unit} (in DEPLOY_RESTART_SKIP)"
        continue
    fi
    if "${SYSTEMCTL[@]}" restart "${unit}"; then
        echo ">>>   restarted ${unit}"
        RESTARTED_UNITS+=("${unit}")
    else
        echo ">>>   WARNING: restart ${unit} failed (continuing)"
    fi
done

# ---------------------------------------------------------------------------
# S-017 T7: one-shot smoke trigger. If a sandbox/operator session committed
# `runtime_flags/run_smoke_once.flag`, fire the smoke now via the
# ict-smoke-once.service oneshot unit. The unit's wrapper deletes the
# flag after running so a no-op re-pull does not refire.
#
# Per CLAUDE.md "Autonomous live-trading rule": this fires without
# per-trade operator confirmation. Safety is enforced by the hard qty
# cap in scripts/smoke_test_trade.py (MAX_SAFE_QTY=0.001 BTC) and the
# per-account mode in config/accounts.yaml (operator directive 2026-05-03;
# ALLOW_LIVE_TRADING env var removed per BUG-055).
# ---------------------------------------------------------------------------
if [ -f "${REPO_DIR}/runtime_flags/run_smoke_once.flag" ]; then
    if [ -f /etc/systemd/system/ict-smoke-once.service ]; then
        echo ">>> Smoke trigger flag detected — starting ict-smoke-once.service"
        "${SYSTEMCTL[@]}" start ict-smoke-once.service || true
    else
        echo ">>> Smoke trigger flag detected but ict-smoke-once.service is not installed."
        echo ">>> Operator: copy deploy/ict-smoke-once.service to /etc/systemd/system/ and 'systemctl daemon-reload'."
    fi
fi

# Compact one-line-per-unit summary (is-active only). The previous
# `systemctl status <unit> --no-pager` for every restarted unit read the
# journal per unit and emitted ~10 lines each; on a CPU-constrained VM the
# 14-unit dump ran long enough to push the whole pull-and-deploy past the
# 15-min Actions job timeout → the job was cancelled and reported a
# false-failure even though the deploy + restarts had succeeded. is-active
# is instant and bounded. (oneshot/timer units legitimately read "inactive"
# after a clean run — that's not a failure.)
echo ">>> Service status (is-active; oneshot/timer units show inactive after a clean run):"
for unit in "${RESTARTED_UNITS[@]}"; do
    printf '>>>   %-48s %s\n' "${unit}" "$("${SYSTEMCTL[@]}" is-active "${unit}" 2>/dev/null || true)"
done

# ---------------------------------------------------------------------------
# S-067 follow-up #5: post-deploy version round-trip assertion.
#
# Hits /api/diag/version on the local web-api and checks that its
# reported git_sha matches POST_SYNC_HEAD's short SHA. Catches the
# 2026-05-09 incident class — web-api advertised "running" via systemd
# but its in-process git SHA was 24+h stale because nothing in the
# deploy chain restarted it.
#
# Soft failure: if web-api isn't installed, DIAG_READ_TOKEN isn't
# set, or curl is missing, we log and move on. Hard failure: the
# endpoint is reachable AND advertises a different SHA than HEAD.
# ---------------------------------------------------------------------------
WEB_API_HOST="${WEB_API_HOST:-127.0.0.1}"
WEB_API_PORT="${WEB_API_PORT:-8001}"
DIAG_TOKEN_FILE="${DIAG_TOKEN_FILE:-/etc/ict-trading-bot/diag_token}"
DIAG_TOKEN="${DIAG_READ_TOKEN:-}"
if [ -z "${DIAG_TOKEN}" ] && [ -r "${DIAG_TOKEN_FILE}" ]; then
    DIAG_TOKEN="$(cat "${DIAG_TOKEN_FILE}")"
fi

if ! command -v curl >/dev/null 2>&1; then
    echo ">>> Skipping post-deploy version assertion (curl not installed)."
elif [ ! -f /etc/systemd/system/ict-web-api.service ]; then
    echo ">>> Skipping post-deploy version assertion (ict-web-api.service not installed)."
elif [ -z "${DIAG_TOKEN}" ]; then
    echo ">>> Skipping post-deploy version assertion (DIAG_READ_TOKEN unset and ${DIAG_TOKEN_FILE} not readable)."
else
    echo ">>> Asserting web-api git_sha matches HEAD..."
    EXPECTED_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
    # Allow up to 30 s for the freshly-restarted web-api to come up.
    ASSERT_OK=0
    for attempt in 1 2 3 4 5 6; do
        sleep 5
        VERSION_JSON="$(curl -fsS --max-time 5 \
            -H "Authorization: Bearer ${DIAG_TOKEN}" \
            "http://${WEB_API_HOST}:${WEB_API_PORT}/api/diag/version" 2>/dev/null || true)"
        if [ -z "${VERSION_JSON}" ]; then
            echo ">>>   attempt ${attempt}: /api/diag/version not yet reachable"
            continue
        fi
        REPORTED_SHA="$(printf '%s' "${VERSION_JSON}" \
            | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin).get("git_sha","unknown"))' 2>/dev/null || echo parse_error)"
        if [ "${REPORTED_SHA}" = "${EXPECTED_SHA}" ]; then
            echo ">>>   web-api git_sha=${REPORTED_SHA} matches HEAD — OK"
            ASSERT_OK=1
            break
        fi
        echo ">>>   attempt ${attempt}: web-api reports git_sha=${REPORTED_SHA}, expected ${EXPECTED_SHA}"
    done
    if [ "${ASSERT_OK}" -ne 1 ]; then
        echo ">>> ERROR: post-deploy version round-trip failed."
        echo ">>>   expected SHA: ${EXPECTED_SHA}"
        echo ">>>   This usually means ict-web-api.service didn't actually restart."
        exit 4
    fi
fi

# Record the SHA the services were just (re)started onto, so the NEXT run can
# tell whether the running code is current even if HEAD later advances out of
# band (BL-20260714-DEPLOY-STALE-ON-OOB-SYNC — see the restart-decision gate
# above). Written only after a successful restart (and version assertion when
# it ran) — a hard assertion failure exit 4's above and leaves the marker stale
# so the next tick retries the restart.
if printf '%s\n' "${POST_SYNC_HEAD}" > "${DEPLOYED_SHA_FILE}" 2>/dev/null; then
    echo ">>> Recorded deploy marker: ${POST_SYNC_HEAD:0:7}"
else
    echo ">>> WARNING: could not write deploy marker ${DEPLOYED_SHA_FILE}"
fi

echo "===== DEPLOY COMPLETE: $(date) ====="
