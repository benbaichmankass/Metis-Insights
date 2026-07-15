#!/usr/bin/env bash
# Install + configure Caddy as the HTTPS front for the bot FastAPI (:8001).
# Phase-0 of the Svelte SPA (ict-trader-dashboard/webapp). Idempotent: safe to
# re-run — it installs Caddy only if missing, then (re)deploys the committed
# Caddyfile and reloads.
#
# Run on the LIVE trader VM (via the vm-caddy-deploy workflow). Reverse-proxies
# localhost:8001 with an auto Let's Encrypt cert for ict-bot.duckdns.org.
#
# Requires: inbound TCP 80+443 open (vm-cloud-fix + vm-net-fix) and the DuckDNS
# A record already pointing at this VM. If the ports aren't open yet, install
# still succeeds; the cert is issued on the first inbound ACME challenge once
# they are — this script reports (does not fail) if the HTTPS probe can't
# connect yet.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC_CADDYFILE="${REPO_ROOT}/deploy/caddy/Caddyfile"
DST_CADDYFILE="/etc/caddy/Caddyfile"
HOSTNAME_FQDN="ict-bot.duckdns.org"

SUDO=()
if [ "$(id -u)" -ne 0 ]; then SUDO=(sudo); fi

echo ">>> install_caddy: host=$(hostname) repo=${REPO_ROOT}"

if [ ! -f "${SRC_CADDYFILE}" ]; then
	echo "ERROR: ${SRC_CADDYFILE} not found — is the checkout current?"
	exit 1
fi

# --- 1. Install Caddy from the official apt repo if not present ---------------
if ! command -v caddy >/dev/null 2>&1; then
	echo ">>> Caddy not installed — adding official apt repo + installing"
	export DEBIAN_FRONTEND=noninteractive
	"${SUDO[@]}" apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg 2>&1 | tail -3
	curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
		| "${SUDO[@]}" gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
	curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
		| "${SUDO[@]}" tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
	"${SUDO[@]}" apt-get update 2>&1 | tail -2
	"${SUDO[@]}" apt-get install -y caddy 2>&1 | tail -3
else
	echo ">>> Caddy already installed: $(caddy version 2>/dev/null | head -1)"
fi

# --- 2. Deploy the committed Caddyfile ---------------------------------------
echo ">>> deploying ${SRC_CADDYFILE} -> ${DST_CADDYFILE}"
"${SUDO[@]}" mkdir -p /etc/caddy
"${SUDO[@]}" cp "${SRC_CADDYFILE}" "${DST_CADDYFILE}"

echo ">>> validating Caddyfile"
if ! "${SUDO[@]}" caddy validate --config "${DST_CADDYFILE}" --adapter caddyfile 2>&1 | tail -5; then
	echo "ERROR: caddy validate failed — not reloading."
	exit 1
fi

# --- 3. Enable + (re)load -----------------------------------------------------
echo ">>> enabling + reloading caddy.service"
"${SUDO[@]}" systemctl enable caddy 2>&1 | tail -1 || true
if "${SUDO[@]}" systemctl is-active --quiet caddy; then
	"${SUDO[@]}" systemctl reload caddy 2>&1 | tail -2 || "${SUDO[@]}" systemctl restart caddy 2>&1 | tail -2
else
	"${SUDO[@]}" systemctl restart caddy 2>&1 | tail -2
fi

echo ">>> caddy service state:"
"${SUDO[@]}" systemctl is-active caddy || true
"${SUDO[@]}" systemctl --no-pager status caddy 2>/dev/null | head -6 || true

# --- 4. Best-effort verification (never fails the run) ------------------------
echo
echo ">>> local upstream check (127.0.0.1:8001 must be up for the proxy to have a target):"
curl -sS -m 8 -o /dev/null -w "  api /health -> HTTP %{http_code}\n" http://127.0.0.1:8001/api/health || echo "  (upstream :8001 not reachable — is ict-web-api up?)"

echo ">>> public HTTPS check (expected to work once 80+443 are open + cert issued):"
curl -sS -m 20 -o /dev/null -w "  https://${HOSTNAME_FQDN}/api/health -> HTTP %{http_code}\n" "https://${HOSTNAME_FQDN}/api/health" \
	|| echo "  (HTTPS not reachable yet — open 80+443 via vm-cloud-fix + vm-net-fix, then Caddy issues the cert on the first challenge)"

echo ">>> install_caddy complete."
