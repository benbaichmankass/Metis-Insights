#!/usr/bin/env bash
# Tier-1 operator action: read-only listening-port + host-firewall inventory.
#
# Answers "what is exposed on this VM?" — the live counterpart to the
# 2026-06-28 intrusion-surface audit, which could document INTENDED exposure
# from the repo but not the live runtime state (the live VM has no arbitrary-
# bash relay by design; this allowlisted read action is the sanctioned way to
# pull it). Surfaces:
#   - every listening TCP socket, with the binding address (PUBLIC vs loopback)
#   - which ports are bound to a public interface (0.0.0.0 / ::) — the exposure
#   - host-level firewall rules (iptables / nft), best-effort
#
# READS ONLY. Mutates nothing, opens no socket outward, restarts nothing.
# Every command is timeout-bounded and failure-tolerant so a missing tool or a
# denied sudo never breaks the inventory. Always exits 0 (it is an observation,
# not a health gate).
#
# NOTE: the OCI security list (cloud-side ingress firewall) is NOT visible from
# inside the host — confirm it in the OCI console / via the OCI CLI separately.
# This reports the HOST's view (what is listening + the host firewall).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

log "Collecting listening-port + firewall inventory (read-only)…"

echo "===== listening TCP sockets (ss -tlnp) ====="
# -p (process) needs root; try sudo -n, fall back to no-process listing.
if sudo -n true 2>/dev/null; then
    timeout 8 sudo -n ss -tlnp 2>/dev/null || timeout 8 ss -tln 2>/dev/null || echo "(ss unavailable)"
else
    timeout 8 ss -tlnp 2>/dev/null || timeout 8 ss -tln 2>/dev/null || echo "(ss unavailable)"
fi

echo
echo "===== PUBLIC-facing binds (0.0.0.0 / :: / non-loopback) — the exposure ====="
# Flag any listener NOT bound to loopback. These are the ports reachable from
# off-box (subject to the OCI security list). Expected: 22 (SSH) + 8001 (API).
# Anything else here is worth a second look (e.g. a DB/metrics/gateway port).
timeout 8 ss -tln 2>/dev/null \
    | awk 'NR>1 {print $4}' \
    | grep -vE '^(127\.|\[::1\]|::1)' \
    | sort -u \
    | sed 's/^/  PUBLIC  /' \
    || echo "(could not enumerate)"

echo
echo "===== host firewall (best-effort; OCI security list is separate, cloud-side) ====="
echo "--- nft ruleset ---"
timeout 8 sudo -n nft list ruleset 2>/dev/null | head -80 \
    || echo "(nft unavailable / not permitted — check OCI security list in the console)"
echo "--- iptables -S ---"
timeout 8 sudo -n iptables -S 2>/dev/null | head -60 \
    || echo "(iptables unavailable / not permitted)"

echo
echo "===== summary ====="
PUB_COUNT="$(timeout 8 ss -tln 2>/dev/null | awk 'NR>1 {print $4}' \
    | grep -vcE '^(127\.|\[::1\]|::1)' || echo '?')"
echo "public-facing listeners: ${PUB_COUNT}"
echo "expected public set: SSH (22) + bot API (8001). Investigate anything else."
echo "reminder: IB Gateway API (4002) MUST be loopback/private only — never public."

exit 0
