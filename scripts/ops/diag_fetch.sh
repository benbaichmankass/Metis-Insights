#!/usr/bin/env bash
# Fetch a /api/diag/<path> document, preferring DIRECT access when the
# session is configured for it, and signalling a relay fallback when not.
#
# WHY: a Claude Code on the web session normally reaches the live VM's
# read-only diag surface through the GitHub-issue relay (vm-diag-snapshot.yml),
# which adds 30-60 s per call. When the session's cloud environment is
# configured with direct egress + the bearer, this script hits the API
# directly in one shot. The /health-review skill calls this first and only
# falls back to the issue relay on a non-zero "use-relay" exit.
#
# Direct access requires these env vars (set in the cloud environment's
# Environment variables field) AND a Network access level that permits
# egress to the host:
#   DIAG_BASE_URL    e.g. http://141.145.193.91:8001  (or an https host)
#   DIAG_READ_TOKEN  the bearer (see the get-diag-token workflow)
#
# Usage:   scripts/ops/diag_fetch.sh '<diag-path>'
#   e.g.   scripts/ops/diag_fetch.sh 'audit?limit=600'
#          scripts/ops/diag_fetch.sh 'journal?table=trades&limit=100'
#          scripts/ops/diag_fetch.sh 'status'
#
# Exit codes:
#   0   success — the diag JSON is on stdout
#   3   direct path unavailable (env unset, egress blocked, or web-api
#       down) — the caller should use the GitHub-issue relay instead
#   2   usage error
#
# The token is sent only as an Authorization header (via a 0600 curl
# config), never as an argv, and is never printed.
set -euo pipefail

path="${1:-}"
if [ -z "$path" ]; then
  echo "usage: $0 '<diag-path>'   e.g. 'audit?limit=600'" >&2
  exit 2
fi

if [ -z "${DIAG_BASE_URL:-}" ] || [ -z "${DIAG_READ_TOKEN:-}" ]; then
  echo "diag_fetch: DIAG_BASE_URL / DIAG_READ_TOKEN not set in this session — use the issue relay." >&2
  exit 3
fi

# Stale-env self-heal (BL-20260705-ENV-DIAG-BASE-URL-STALE): several cloud
# environments still carry a DIAG_BASE_URL pointing at the RETIRED x86 micro
# (158.178.210.252, terminated 2026-06-16) instead of the Ampere live trader
# (141.145.193.91, the 2026-06-14 cutover target). A dead host makes every
# direct fetch time out and silently fall back to the slow issue relay. Rewrite
# the known-retired host to the canonical live IP here so a Full-network session
# is not broken by the stale setting — the PERMANENT fix is still the operator
# updating DIAG_BASE_URL in the cloud-environment settings.
_RETIRED_LIVE_HOST="158.178.210.252"
_CANONICAL_LIVE_HOST="141.145.193.91"
if printf '%s' "${DIAG_BASE_URL}" | grep -q "${_RETIRED_LIVE_HOST}"; then
  echo "diag_fetch: DIAG_BASE_URL points at the retired micro ${_RETIRED_LIVE_HOST}; rewriting to the live Ampere host ${_CANONICAL_LIVE_HOST} (BL-20260705-ENV-DIAG-BASE-URL-STALE — update the cloud-env setting to make this permanent)." >&2
  DIAG_BASE_URL="${DIAG_BASE_URL//${_RETIRED_LIVE_HOST}/${_CANONICAL_LIVE_HOST}}"
fi

cfg="$(mktemp)"
chmod 600 "$cfg"
trap 'rm -f "$cfg"' EXIT
printf 'header = "Authorization: Bearer %s"\n' "$DIAG_READ_TOKEN" > "$cfg"

base="${DIAG_BASE_URL%/}"
if curl -sS --fail --max-time 10 -K "$cfg" "$base/api/diag/$path"; then
  exit 0
fi

echo "diag_fetch: direct fetch of '$path' failed (egress blocked / web-api down) — use the issue relay." >&2
exit 3
