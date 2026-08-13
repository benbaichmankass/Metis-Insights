#!/usr/bin/env bash
# scripts/vm_bootstrap.sh — one-time bootstrap for VM-resident Claude Code.
#
# Paste-into-Oracle-Cloud-Shell installer. Idempotent: re-running is safe.
#
# What it does:
#   1. Adds 2 GB swap (if not already present) — protects the live trader
#      from OOM when Claude runs.
#   2. Installs Node.js 20 LTS via NodeSource and Claude Code via npm.
#   3. Creates /etc/claude/{permissions.read.json,permissions.write.json,
#      vm-marker} from the in-repo deploy/ files. Mode 0644 root:root —
#      writable only by root, so the runner (ubuntu) cannot mutate the
#      tier policy. (This is enforced by the Tier 3 deny list as well,
#      but defense in depth.)
#   4. Creates /etc/ict-trader/claude.env, mode 0640 root:ubuntu, and
#      prompts the operator for ANTHROPIC_API_KEY (read silently).
#   5. Creates /var/log/claude-vm/ and /run/claude/prompts/ with the
#      right ownership.
#   6. Installs deploy/ict-ufw.sudoers (the ufw grant). The claude-vm-runner
#      unit + dispatch wrapper were removed 2026-08-13 (dead since PR #1933).
#   7. Installs the new vm-runner Telegram command (no restart needed —
#      the bot picks up the new handler when systemd restarts the unit
#      after the next deploy / git pull).
#
# What it does NOT do:
#   - Start a Claude Code session. Each invocation spawns a oneshot.
#   - Open any inbound port. The bridge is the existing Telegram bot.
#   - Touch the live-trader unit, web-api unit, or any trading config.
#
# Run as: `bash scripts/vm_bootstrap.sh` (the script will sudo when
# needed; do not run as root directly — we want the ubuntu user's
# environment).
#
# See docs/claude/vm-operator-mode.md for the authority model and
# docs/claude/deployment-ops.md § "VM-resident Claude" for the
# operational runbook.

set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run as ubuntu, not root. The script will sudo for privileged steps." >&2
  exit 1
fi

if [[ "$(id -un)" != "ubuntu" ]]; then
  echo "Expected user 'ubuntu'; got '$(id -un)'." >&2
  exit 1
fi

REPO_DIR="${REPO_DIR:-/home/ubuntu/ict-trading-bot}"
if [[ ! -d "${REPO_DIR}/deploy" ]]; then
  echo "Repo not found at ${REPO_DIR}. Set REPO_DIR if it lives elsewhere." >&2
  exit 1
fi

echo "==> 1/7  Ensuring swap is configured (target: 2 GB)"
if [[ ! -f /swapfile ]]; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  if ! grep -q "^/swapfile " /etc/fstab; then
    echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab
  fi
  echo "    swap added"
else
  echo "    swap already present, skipping"
fi

echo "==> 2/7  Installing Node.js 20 LTS + Claude Code (idempotent)"
if ! command -v node >/dev/null || [[ "$(node --version | cut -d. -f1)" != "v20" ]]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi
if ! command -v claude >/dev/null; then
  sudo npm install -g @anthropic-ai/claude-code
fi
claude --version

echo "==> 3/7  Installing tier permission profiles to /etc/claude"
sudo install -d -m 0755 -o root -g root /etc/claude
sudo install -m 0644 -o root -g root \
  "${REPO_DIR}/deploy/claude-permissions.read.json" /etc/claude/permissions.read.json
sudo install -m 0644 -o root -g root \
  "${REPO_DIR}/deploy/claude-permissions.write.json" /etc/claude/permissions.write.json
{
  echo "host: $(hostnamectl --static 2>/dev/null || hostname)"
  echo "ocid_prefix: ocid1.instance.oc1.eu-paris-1.anrwiljrnpsi"
  echo "bootstrap_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} | sudo install -m 0644 -o root -g root /dev/stdin /etc/claude/vm-marker

echo "==> 4/7  Configuring /etc/ict-trader/claude.env"
sudo install -d -m 0750 -o root -g ubuntu /etc/ict-trader
if [[ ! -f /etc/ict-trader/claude.env ]]; then
  read -rsp "    Paste ANTHROPIC_API_KEY (input hidden): " ANTHROPIC_API_KEY
  echo
  if [[ -z "${ANTHROPIC_API_KEY}" ]]; then
    echo "    Empty key — aborting." >&2
    exit 1
  fi
  sudo install -m 0640 -o root -g ubuntu /dev/null /etc/ict-trader/claude.env
  printf 'ANTHROPIC_API_KEY=%s\n' "${ANTHROPIC_API_KEY}" | \
    sudo tee /etc/ict-trader/claude.env >/dev/null
  unset ANTHROPIC_API_KEY
  echo "    written /etc/ict-trader/claude.env (mode 0640 root:ubuntu)"
else
  echo "    /etc/ict-trader/claude.env already present, leaving as-is"
fi

echo "==> 5/7  Preparing transcript + prompt directories"
sudo install -d -m 0750 -o ubuntu -g ubuntu /var/log/claude-vm
sudo install -d -m 0750 -o ubuntu -g ubuntu /run/claude
sudo install -d -m 0750 -o ubuntu -g ubuntu /run/claude/prompts
# Claude Code state dirs — runner writes session-env, cache, and
# config under HOME; ReadWritePaths in the unit needs these to exist.
sudo install -d -m 0700 -o ubuntu -g ubuntu /home/ubuntu/.claude
sudo install -d -m 0700 -o ubuntu -g ubuntu /home/ubuntu/.cache
sudo install -d -m 0700 -o ubuntu -g ubuntu /home/ubuntu/.config/claude
# /run is tmpfs — ensure the dir is recreated on every boot via tmpfiles.d.
echo "d /run/claude         0750 ubuntu ubuntu - -" | \
  sudo tee /etc/tmpfiles.d/claude-vm.conf >/dev/null
echo "d /run/claude/prompts 0750 ubuntu ubuntu - -" | \
  sudo tee -a /etc/tmpfiles.d/claude-vm.conf >/dev/null

# 6a/6b (the claude-vm-runner@.service unit + claude-vm-dispatch wrapper) were
# REMOVED 2026-08-13. Their only caller — the Telegram /vm + /vm_write surface —
# was deleted in PR #1933 (2026-05-25), and the audit found the wrapper's
# passwordless-root sudoers grant still installed on the live money VM three
# months later with zero call sites in src/
# (BL-20260813-VM-RUNNER-ZOMBIE-SUDOERS-ROOT-GRANT). A fresh VM must not be
# bootstrapped back into that state.
#
# The `ufw` grant that SHARED that file is live and load-bearing (the
# system-actions / vm-net-fix reopen of TCP/8001 after a reboot drops it —
# #537/#542/#545), so it survives on its own in deploy/ict-ufw.sudoers. Splitting
# it is the whole point: deleting the combined file would have taken the recovery
# path with it, silently.
#
# An ALREADY-bootstrapped VM is cleaned by the `purge-vm-runner` operator action
# (scripts/ops/purge_vm_runner.sh) — this file is install-only and never removes.
echo "==> 6/7 Installing ufw sudoers grant"
# Stage the sudoers file in /etc/sudoers.d after a visudo -c check.
TMP_SUDOERS="$(mktemp)"
cp "${REPO_DIR}/deploy/ict-ufw.sudoers" "${TMP_SUDOERS}"
if ! sudo visudo -cf "${TMP_SUDOERS}"; then
  echo "    sudoers file failed visudo -c check; refusing to install" >&2
  rm -f "${TMP_SUDOERS}"
  exit 1
fi
sudo install -m 0440 -o root -g root \
  "${TMP_SUDOERS}" /etc/sudoers.d/ict-ufw
rm -f "${TMP_SUDOERS}"

sudo systemctl daemon-reload

echo "==> 7/7  Verifying"
echo "    claude:            $(command -v claude)"
echo "    vm-marker:         $(sudo test -f /etc/claude/vm-marker && echo present || echo MISSING)"
echo "    permissions.read:  $(sudo test -r /etc/claude/permissions.read.json && echo readable || echo MISSING)"
echo "    permissions.write: $(sudo test -r /etc/claude/permissions.write.json && echo readable || echo MISSING)"
echo "    ufw sudoers:       $(test -f /etc/sudoers.d/ict-ufw && echo installed || echo MISSING)"
echo "    sudoers:           $(sudo test -r /etc/sudoers.d/claude-vm-runner && echo installed || echo MISSING)"
echo "    sudo (passwordless): $(sudo -n -l /usr/local/bin/claude-vm-dispatch >/dev/null 2>&1 && echo ok || echo MISSING)"
echo "    swap:              $(free -m | awk '/Swap:/ {print $2}') MB"
echo
echo "Bootstrap complete. Restart the Telegram bot to enable /vm and /vm_write:"
echo "    sudo systemctl restart ict-telegram-bot"
echo
echo "Smoke-test from Telegram:"
echo "    /vm  what services are active and what is the trader uptime"
