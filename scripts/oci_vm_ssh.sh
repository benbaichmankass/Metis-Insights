#!/usr/bin/env bash
# scripts/oci_vm_ssh.sh — wrapper around ssh to the live VM with sane defaults.
#
# Usage:
#   ./scripts/oci_vm_ssh.sh 'uptime'
#   ./scripts/oci_vm_ssh.sh --dry-run 'sudo systemctl status ict-trader-live'
#   echo 'set -e; ls /data' | ./scripts/oci_vm_ssh.sh 'bash -s'
#
# Env:
#   VM_HOST          — defaults to 158.178.210.252.
#   VM_USER          — defaults to ubuntu.
#   VM_SSH_KEY_PATH  — path to private key, defaults to ~/.ssh/ict-bot-ovm-private.key.
set -euo pipefail

VM_HOST="${VM_HOST:-158.178.210.252}"
VM_USER="${VM_USER:-ubuntu}"
SSH_KEY="${VM_SSH_KEY_PATH:-${HOME}/.ssh/ict-bot-ovm-private.key}"
DRYRUN=false

POSITIONAL=()
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRYRUN=true ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) POSITIONAL+=("$arg") ;;
    esac
done

if [[ ${#POSITIONAL[@]} -eq 0 ]]; then
    echo "ERROR: no remote command given." >&2
    exit 2
fi
if [[ ! -r "$SSH_KEY" ]]; then
    echo "ERROR: SSH key not readable at $SSH_KEY" >&2
    exit 3
fi

ssh_cmd=(ssh
    -o StrictHostKeyChecking=accept-new
    -o BatchMode=yes
    -o ConnectTimeout=15
    -o ServerAliveInterval=30
    -i "$SSH_KEY"
    "${VM_USER}@${VM_HOST}"
    "${POSITIONAL[@]}")

if $DRYRUN; then
    printf 'DRY-RUN: %s\n' "${ssh_cmd[*]}"
    exit 0
fi

exec "${ssh_cmd[@]}"
