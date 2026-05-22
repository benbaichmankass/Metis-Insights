#!/usr/bin/env bash
# Autonomous self-heal action: restart the IB Gateway container.
#
# Why this lives outside operator-actions.yml's allowlist:
#   IB Gateway (gnzsnz/IBC docker container `ib-gateway`) is the live
#   trader's only path to IBKR for MES market data + execution. When the
#   account's market-data line gets bound to another session (IBKR error
#   162 "Trading TWS session is connected from a different IP address"),
#   closing the other login does not always hand the line back — the
#   gateway has to re-login. Recovering used to require an operator. This
#   wrapper closes that loop with an issue-driven workflow Claude drives
#   autonomously, mirroring restart_web_api.sh.
#
# Blast radius:
#   - `docker restart ib-gateway` bounces ONLY the IB Gateway container.
#     The live trader (ict-trader-live.service) tolerates a few ticks of
#     "no candle data for MES" and reconnects on the next tick; BTCUSDT
#     (Bybit) is unaffected. The gateway re-logs into the SAME paper
#     account on the SAME config — no account/mode/strategy/risk change.
#   - This wrapper does NOT touch accounts.yaml, strategies.yaml, risk
#     caps, orders, or any trade-decision code. Any attempt to do so
#     should be rejected at code review.
#
# Pre/post checks:
#   - Capture `docker ps` state of ib-gateway before restart.
#   - `docker restart ib-gateway`.
#   - Poll up to 150 s for the IBC log to show "Login has completed"
#     (the gateway takes ~50 s to re-login) AND the API port 4002 to
#     accept a TCP connection.
#   - Dump the last 40 lines of `docker logs ib-gateway` so the caller
#     can confirm the login + spot any 2FA / auth prompt.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh" 2>/dev/null || true

NAME="${IB_GATEWAY_CONTAINER:-ib-gateway}"
API_PORT="${IB_GATEWAY_API_PORT:-4002}"
LOGIN_TIMEOUT="${IB_GATEWAY_LOGIN_TIMEOUT:-150}"

_log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

if ! command -v docker >/dev/null 2>&1; then
    _log "ERROR: docker not found on PATH."
    exit 1
fi

_log "Pre-restart state:"
docker ps -a --filter "name=^/${NAME}$" --format '{{.Names}} | {{.Status}} | {{.Ports}}' || true

_log "Restarting container '${NAME}'…"
if ! docker restart "${NAME}" >/dev/null 2>&1; then
    _log "ERROR: 'docker restart ${NAME}' failed."
    docker ps -a --filter "name=^/${NAME}$" --format '{{.Names}} | {{.Status}}' || true
    exit 1
fi

_log "Waiting up to ${LOGIN_TIMEOUT}s for IBC login + API port ${API_PORT}…"
login_ok=no
port_ok=no
deadline=$(( $(date +%s) + LOGIN_TIMEOUT ))
while [ "$(date +%s)" -lt "${deadline}" ]; do
    if [ "${login_ok}" != yes ] && \
       docker logs --since 4m "${NAME}" 2>&1 | grep -q "Login has completed"; then
        login_ok=yes
    fi
    if [ "${port_ok}" != yes ] && \
       (exec 3<>"/dev/tcp/127.0.0.1/${API_PORT}") 2>/dev/null; then
        exec 3>&- 3<&- 2>/dev/null || true
        port_ok=yes
    fi
    [ "${login_ok}" = yes ] && [ "${port_ok}" = yes ] && break
    sleep 5
done
_log "login_completed=${login_ok} api_port_${API_PORT}_reachable=${port_ok}"

_log "Post-restart state:"
docker ps -a --filter "name=^/${NAME}$" --format '{{.Names}} | {{.Status}} | {{.Ports}}' || true

echo
echo "===== docker logs --tail 40 ${NAME} ====="
docker logs --tail 40 "${NAME}" 2>&1 | tail -40 || true

if command -v record_audit >/dev/null 2>&1; then
    status=$([ "${login_ok}" = yes ] && echo ok || echo failed)
    record_audit "restart-ib-gateway" "${status}" \
        "{\"login\": \"${login_ok}\", \"port\": \"${port_ok}\"}" >/dev/null 2>&1 || true
fi

# Success = the gateway re-logged in. Port reachability alone isn't
# enough (socat may answer before login finishes), so gate on login.
if [ "${login_ok}" = yes ]; then
    _log "Restart succeeded — IBC login completed."
    exit 0
fi
_log "ERROR: gateway did not confirm login within ${LOGIN_TIMEOUT}s (check logs above for a 2FA / auth prompt)."
exit 1
