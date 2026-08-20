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
# Direct access requires the bearer, and a base URL the sandbox can actually
# reach:
#   DIAG_READ_TOKEN  the bearer (see the get-diag-token workflow)
#   DIAG_BASE_URL    optional — the canonical HTTPS base is tried FIRST
#                    regardless, so a stale or plain-http value no longer
#                    strands the direct path (see the candidate list below)
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

# Only the BEARER is genuinely required. DIAG_BASE_URL is now optional: the
# candidate list below falls back to the canonical HTTPS base, so an env that
# never set it is no longer stranded on the relay. Previously this gate
# demanded both, which meant a session holding a perfectly good token but no
# base URL took the slow path for no reason.
if [ -z "${DIAG_READ_TOKEN:-}" ]; then
  echo "diag_fetch: DIAG_READ_TOKEN not set in this session — use the issue relay." >&2
  exit 3
fi

# ⚠️ THE OLD SELF-HEAL REWROTE A DEAD HOST INTO AN UNREACHABLE ONE, so it
# looked fixed and was not (corrected 2026-08-20, BL-20260705-ENV-DIAG-BASE-URL-STALE
# / BL-20260818-DIAG-BASE-URL-POINTS-AT-TERMINATED-VM). It rewrote the retired
# micro 158.178.210.252 -> the raw live IP 141.145.193.91 — but the sandbox
# proxy allowlists by SCHEME+HOSTNAME, and a plain-http call to a raw IP is
# DROPPED at the default `Trusted` network level. Measured this session, same
# process, seconds apart:
#
#   http://141.145.193.91:8001/api/health   -> curl 28, timeout   (rc 000)
#   https://ict-bot.duckdns.org/api/health  -> 200 {"ok":true}
#   https://ict-bot.duckdns.org/api/diag/version + bearer -> 200
#
# So the "self-heal" fired, produced a firewalled host, timed out, and exited 3
# — sending every session down the 30-60s issue relay while reporting that it
# had healed the setting. That is why the relay hop persisted for weeks after
# this block was added.
#
# The fix is to stop trusting a single configured base: try an ORDERED list of
# candidates, canonical HTTPS first. This works at `Trusted` with no
# cloud-environment change at all, which is what makes it a repo fix rather
# than an operator one.
_RETIRED_LIVE_HOST="158.178.210.252"
_LIVE_VM_IP="141.145.193.91"
_CANONICAL_DIAG_BASE="https://ict-bot.duckdns.org"   # Caddy -> localhost:8001

# Candidate order. The canonical HTTPS base goes first whenever the configured
# value is plain-http or names a known VM IP — i.e. exactly the cases the proxy
# drops. A caller who has deliberately set some OTHER https base keeps it first.
_configured="${DIAG_BASE_URL:-}"
_configured="${_configured%/}"
_candidates=()
case "${_configured}" in
  "") _candidates+=("${_CANONICAL_DIAG_BASE}") ;;
  http://*|*"${_RETIRED_LIVE_HOST}"*|*"${_LIVE_VM_IP}"*)
      _candidates+=("${_CANONICAL_DIAG_BASE}" "${_configured}") ;;
  *)  _candidates+=("${_configured}" "${_CANONICAL_DIAG_BASE}") ;;
esac

cfg="$(mktemp)"
chmod 600 "$cfg"
trap 'rm -f "$cfg"' EXIT
printf 'header = "Authorization: Bearer %s"\n' "$DIAG_READ_TOKEN" > "$cfg"

_seen=""
for base in "${_candidates[@]}"; do
  [ -n "$base" ] || continue
  case " ${_seen} " in *" ${base} "*) continue ;; esac   # de-dup
  _seen="${_seen} ${base}"
  if curl -sS --fail --max-time 10 -K "$cfg" "${base}/api/diag/${path}"; then
    # Say which base served, on stderr so it never pollutes the JSON. A reader
    # who cannot tell WHICH host answered cannot tell a healthy direct path
    # from a lucky one.
    echo "diag_fetch: served by ${base}" >&2
    exit 0
  fi
  echo "diag_fetch: candidate ${base} did not answer; trying the next." >&2
done

echo "diag_fetch: no candidate answered for '${path}' (tried:${_seen}) — web-api down, bearer wrong, or egress blocked. Use the issue relay." >&2
exit 3
