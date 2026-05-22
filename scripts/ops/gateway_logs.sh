#!/usr/bin/env bash
# Read-only: show the IB Gateway container status + recent logs. Does NOT
# restart or recreate the container (so it never triggers a fresh login / 2FA
# attempt). Used to diagnose why a login didn't reach the 2FA step.
#
# IBC does not log the password, so the output is safe to surface.
set -euo pipefail

echo "===== docker ps -a (ib-gateway) ====="
sudo docker ps -a --filter name=ib-gateway \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>&1 || true

echo
echo "===== docker logs --tail 200 ib-gateway ====="
sudo docker logs --tail 200 ib-gateway 2>&1 | tail -200 || true
