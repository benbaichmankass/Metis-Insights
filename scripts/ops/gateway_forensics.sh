#!/usr/bin/env bash
# Read-only restart forensics for the IB Gateway container. Runs ON the gateway
# VM (the workflow ProxyJumps through the trader, same as gateway-logs).
#
# Answers one question gateway-logs cannot: WHO restarts ib-gateway — the
# watchdog timer, Docker's own restart policy after a java exit, or IBC.
# Prints docker inspect restart facts and the state + last journal lines of the
# watchdog and reset units. Restarts nothing, stops nothing, writes nothing.
#
# The repo is public and this output is posted to an issue, so IPv4 addresses
# and IBKR account ids (DU + digits) are masked before anything is printed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

mask() { sed -E 's/[0-9]{1,3}(\.[0-9]{1,3}){3}/<ip>/g; s/\b(DU|DF)[A-Z0-9]{5,}\b/<acct>/g'; }

{
echo "===== now (UTC) ====="
date -u +%Y-%m-%dT%H:%M:%SZ

echo
echo "===== docker inspect ib-gateway ====="
sudo -n docker inspect ib-gateway --format \
  'RestartCount={{.RestartCount}} ExitCode={{.State.ExitCode}} OOMKilled={{.State.OOMKilled}} Status={{.State.Status}} StartedAt={{.State.StartedAt}} FinishedAt={{.State.FinishedAt}} RestartPolicy={{.HostConfig.RestartPolicy.Name}}/{{.HostConfig.RestartPolicy.MaximumRetryCount}}' 2>&1 || true

for u in ict-ib-gateway-watchdog.timer ict-ib-gateway-watchdog.service \
         ict-ib-gateway-reset.timer ict-ib-gateway-reset.service; do
  echo
  echo "===== ${u} ====="
  echo "is-active: $(systemctl is-active "${u}" 2>&1 || true)"
  echo "is-enabled: $(systemctl is-enabled "${u}" 2>&1 || true)"
  echo "--- last 50 journal lines ---"
  journalctl -u "${u}" -n 50 --no-pager 2>&1 \
    || sudo -n journalctl -u "${u}" -n 50 --no-pager 2>&1 || true
done

echo
echo "===== next/last timer fires ====="
systemctl list-timers --all --no-pager 'ict-ib-gateway-*' 2>&1 || true
} 2>&1 | mask
