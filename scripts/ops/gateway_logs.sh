#!/usr/bin/env bash
# Read-only: show the IB Gateway container status + recent logs. Does NOT
# restart or recreate the container (so it never triggers a fresh login / 2FA
# attempt). Used to diagnose why a login didn't reach the 2FA step.
#
# IBC does not log the password, so the output is safe to surface.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

echo "===== docker ps -a (ib-gateway) ====="
sudo docker ps -a --filter name=ib-gateway \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>&1 || true

echo
# Filtered view first: each IBC restart reprints ~120 lines of JVM /
# system-properties boilerplate, which buries the login-relevant lines in a
# plain --tail. Grep the FULL container log for the events that actually
# explain a login outcome (login completed / failed, second-factor prompts,
# existing-session conflicts, credential errors, IBC exit codes) so a restart
# loop's root cause is visible without paging past the boilerplate. Still
# password-safe: IBC never logs the password.
echo "===== login-relevant lines (grep over full log, last 80) ====="
sudo docker logs ib-gateway 2>&1 \
  | grep -iE 'login|second[ -]?factor|2fa|authenticat|existing session|competing|already logged|failed|invalid|incorrect|password|exit code|cannot|disconnect|connected to|market data' \
  | tail -80 || true

echo
echo "===== docker logs --tail 200 ib-gateway ====="
sudo docker logs --tail 200 ib-gateway 2>&1 | tail -200 || true
