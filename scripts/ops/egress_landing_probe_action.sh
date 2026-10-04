#!/usr/bin/env bash
# Tier-2 system-action: READ-ONLY landing-page reachability probe from the
# live VM's egress (lane PROP-DXTRADE-FIRMS, 2026-09-30). Answers ONE question
# the Breakout ASN ban (PI-20260929-FRJ7NMPU-0001) made expensive: does a
# prop-terminal host answer, or block, this VM's Oracle ASN?
#
# Scope, deliberately narrow:
#   - A FIXED host allowlist below. There is NO url parameter: an arbitrary-URL
#     fetch from the trader VM is not something this action offers.
#   - Plain HTTPS GET, no credentials, no cookies sent, no redirect following
#     beyond 3 hops, no clicks, no browser, no POST.
#   - Prints per URL: HTTP status, server, cf-mitigated, cf-ray presence,
#     content-type, body size, a Cloudflare error code if the body carries one
#     ("Error 1005" etc.), login-form markers, and (for /specs only) whether the
#     body looks like API docs. Never prints set-cookie or any header value
#     beyond that list, and never dumps a body: for a small JSON body it prints
#     the key NAMES only (values can echo the client IP).
#   - No anti-detection: default curl User-Agent-less request with an honest UA.
#
# Dispatched by the system-actions workflow (issue body):
#   action: egress-landing-probe
#   reason: <audit note>                       (required, Tier-2)
#
# Exit codes: 0 done (any HTTP outcome is a MEASUREMENT, not a failure),
# 1 environment (no curl).

set -euo pipefail

SCRIPT_NAME="egress_landing_probe_action"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

command -v curl >/dev/null 2>&1 || { log "environment: curl missing"; exit 1; }

# The allowlist. Add a host here in a reviewed PR; never accept it as input.
URLS=(
    "https://dx.tradeify247.co/"
    "https://dx.tradeify247.co/specs"
    "https://tradeify247.co/"
    # Velotrade (lane PROP-FIRM-DEEP, 2026-10-04; pipeline PI-20261004-OZ9AAZSV-0001):
    # the same three shapes -- DXtrade terminal landing, a DXtrade front-door path
    # (the developer portal, which served the REST/Push/FIX docs from a sandbox
    # egress), and the firm's site. Measured 200 / 200 / 200 from a Google-cloud
    # egress on 2026-10-04 with no Cloudflare header; this run asks the same
    # question from the VM's Oracle egress.
    "https://dx.velotrade.com/"
    "https://dx.velotrade.com/developers/"
    "https://velotrade.com/"
)

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
UA="metis-insights-landing-probe/1 (read-only reachability check; contact: repo owner)"

log "landing-only probe from this VM's egress (no credentials, GET only)"
for url in "${URLS[@]}"; do
    hdr="${TMP}/h.txt"; body="${TMP}/b.bin"; : >"${hdr}"; : >"${body}"
    set +e
    code="$(curl -q -sS --proto =https --proto-redir =https -m 25 --max-redirs 3 -L -A "${UA}" -D "${hdr}" -o "${body}" \
        -w '%{http_code}' "${url}" 2>"${TMP}/err.txt")"
    rc=$?
    set -e
    echo "--- ${url}"
    if [ "${rc}" -ne 0 ]; then
        echo "  curl_exit=${rc} error=$(tr -d '\r\n' <"${TMP}/err.txt" | cut -c1-160)"
        continue
    fi
    # Last response block only (after any redirect).
    last="$(awk 'BEGIN{RS="\r?\n\r?\n"} {b=$0} END{print b}' "${hdr}")"
    # `|| true`: a header that is absent is a MEASUREMENT (empty), never an abort
    # under `set -e -o pipefail` (grep exits 1 on no match).
    getv() { { printf '%s\n' "${last}" | tr -d '\r' | grep -i "^$1:" | head -n1 | cut -d: -f2- | sed 's/^ *//' | cut -c1-80; } || true; }
    size="$(wc -c <"${body}" | tr -d ' ')"
    echo "  http_status=${code}"
    echo "  server=$(getv server)"
    echo "  cf-mitigated=$(getv cf-mitigated)"
    echo "  cf-ray_present=$([ -n "$(getv cf-ray)" ] && echo yes || echo no)"
    echo "  content-type=$(getv content-type)"
    echo "  body_bytes=${size}"
    # Cloudflare block / challenge markers in the body (text only).
    cferr="$(grep -aoiE 'error (code )?:? ?1[0-9]{3}|error 10[0-9]{2}' "${body}" | head -n1 || true)"
    chal="$(grep -aoiE 'just a moment|challenge-platform|cf-chl|turnstile|attention required' "${body}" | head -n1 || true)"
    echo "  cloudflare_error_marker=${cferr:-none}"
    echo "  challenge_marker=${chal:-none}"
    echo "  login_form_markers=$(grep -aoE 'loginForm-main|id="username"|id="password"' "${body}" | sort -u | tr '\n' ' ')"
    case "${url}" in
        */specs)
            ct="$(getv content-type)"
            if [ "${size}" -lt 600 ] && printf '%s' "${ct}" | grep -qi json; then
                # Keys only, never values: an error body can echo the client IP.
                echo "  small_json_keys=$( { grep -aoE '"[A-Za-z_][A-Za-z0-9_]*"[[:space:]]*:' "${body}" | tr -d ' :"' | sort -u | tr '\n' ' '; } || true )"
            fi
            echo "  looks_like_api_docs=$(grep -aciE 'openapi|swagger|dxsca-web|<title>[^<]*(spec|api)' "${body}" || true) matching lines"
            ;;
    esac
done

record_audit "egress-landing-probe" "ok" "{\"urls\": ${#URLS[@]}}" >/dev/null || true
log "egress-landing-probe: done"
exit 0
