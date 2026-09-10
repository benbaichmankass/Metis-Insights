#!/usr/bin/env bash
# Tier-2 operator action: re-attach the audit F-39 stranded package legs.
#
# Wraps scripts/ops/reattach_stranded_package_legs.py. Three `order_packages`
# rows were flipped to `closed` while their legs were still open, so
# order_monitor's `status="open"` selector can never pick them up again and
# those legs are managed by nobody. The helper flips `status` back to `open`
# and clears the contradictory `close_reason`, keeping both prior values under
# `meta.reattach_repair`. That is the whole change.
#
# APPROVAL (both halves, and they are separate records):
#   * WO-20260909-DECISION-PACKAGES-CLOSED-WHILE-HOLDING-OPEN-LEGS
#     (`chosen: detector_and_reattach`, 2026-09-09) — the two alpaca_paper
#     packages, approved "paper first".
#   * DEC-20260910-F39-REATTACH-XRPUSDT-APPROVED (2026-09-10T07:52Z,
#     `chosen: reattach_xrpusdt_too`) — pkg-293021e2e84a48db, which carries
#     leg 5474 on REAL-MONEY bybit_2. This SUPERSEDES the manager's earlier
#     standing hold on that package; the hold is lifted.
#
# ⚠️ IT PLACES, MODIFIES AND CANCELS NOTHING. The approval is scoped in terms
# to a JOURNAL/STATE repair. The standing prohibition on remediating by
# cancelling resting legs
# (BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG)
# applies here without exception.
#
# ⚠️ THE INTENDED CONSEQUENCE IS THAT THE EXIT PATH CAN ACT ON THESE LEGS
# AGAIN, INCLUDING REAL-MONEY TRADE 5474 — once the package is `open` the
# strategy's monitor() may trail, tighten or CLOSE it on the next exit pass.
# That is the point (they are unmanaged today) and the operator was shown it
# in the option text they chose. It is stated here rather than buried.
#
# WHY THIS WRAPPER CAPTURES THE VENUE PAYLOAD ITSELF
# --------------------------------------------------
# The helper REQUIRES `--exchange-positions` and refuses without it: a leg that
# has since closed at the venue must be reconciled as CLOSED, never
# re-attached, and re-opening a package over a position that no longer exists
# would hand the exit path a phantom. The approval's condition is that the read
# be FRESH — "re-read all four legs live immediately before any --apply" — so
# the capture happens HERE, in the same run, seconds before the write. Passing
# a payload in through the issue body would be stale by construction.
#
# The capture is a GET against the VM's own /api/diag/exchange_positions over
# loopback. The helper itself still opens no socket; this wrapper is the one
# place a read happens, and it is read-only.
#
# ⚠️ THE FETCH MAY NOT SWALLOW ITS OWN FAILURE. No `|| echo '{}'`, no
# `|| true` — that idiom turns an unreachable web-api into an empty payload,
# which the helper would read as "every account blind" and refuse on, hiding a
# plumbing fault behind a plausible-looking refusal. A failed capture aborts
# the run with a distinct message instead (RULE: "could not measure" is its own
# outcome, and is neither a pass nor a finding).
#
# ⚠️ THIS WRAPPER DOES NOT SECOND-GUESS THE PER-LEG DECISION. It asserts only
# that the payload is structurally usable (parses, carries `captured_at`, has a
# non-empty `accounts` list). Whether a given leg is venue-backed, flat, or
# unreadable is the helper's single-owner policy — an unreadable account is a
# REFUSAL there, never a flat. A second copy of that rule in bash is exactly
# how the two would drift.
#
# DRY-RUN by default; pass `apply: true` (issue body) to write. The helper
# pins every target's expected CURRENT signature, so a row that has moved is
# REFUSED and named — the action is idempotent and safe to re-run.
#
# What this does NOT touch: any package outside the three hard-coded ids; the
# venue positions; the running ict-trader-live.service (no restart required).
set -euo pipefail

SCRIPT_NAME="reattach_stranded_package_legs"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

ACTION_SLUG="reattach-stranded-package-legs"
DIAG_URL="http://127.0.0.1:8001/api/diag/exchange_positions"
SYSTEM_ENV="/etc/ict-trader/web-api.env"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/reattach_stranded_package_legs.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "${ACTION_SLUG}" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "${ACTION_SLUG}" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

# --- resolve the diag bearer -------------------------------------------------
# System env first (root-written by deploy_diag.sh), then the repo .env, which
# is where init_diag_token.sh also writes it. Never echoed.
DIAG_TOKEN=""
for candidate in "${SYSTEM_ENV}" "${REPO_DIR}/.env"; do
    if [ -z "${DIAG_TOKEN}" ] && [ -f "${candidate}" ]; then
        DIAG_TOKEN="$(grep -m1 '^DIAG_READ_TOKEN=' "${candidate}" 2>/dev/null | cut -d= -f2- || true)"
        if [ -n "${DIAG_TOKEN}" ]; then
            log "Resolved DIAG_READ_TOKEN from ${candidate}"
        fi
    fi
done
if [ -z "${DIAG_TOKEN}" ]; then
    log "ERROR: DIAG_READ_TOKEN not found in ${SYSTEM_ENV} or ${REPO_DIR}/.env."
    log "       Without it the venue evidence cannot be captured, and this"
    log "       action REFUSES rather than writing on an unverified book."
    record_audit "${ACTION_SLUG}" "error" \
        "{\"reason\": \"diag token unresolved\"}" >/dev/null || true
    exit 1
fi

# --- capture the venue evidence, FRESH, in this run --------------------------
VENUE_JSON="$(mktemp -t reattach_expos.XXXXXX.json)"
cleanup() { rm -f "${VENUE_JSON}"; }
trap cleanup EXIT

log "Capturing venue evidence from ${DIAG_URL} …"
http_code=""
set +e
http_code="$(curl -sS --max-time 45 -o "${VENUE_JSON}" -w '%{http_code}' \
    -H "Authorization: Bearer ${DIAG_TOKEN}" "${DIAG_URL}")"
curl_rc=$?
set -e

if [ "${curl_rc}" -ne 0 ] || [ "${http_code}" != "200" ]; then
    log "ERROR: venue capture FAILED (curl_rc=${curl_rc} http=${http_code:-none})."
    log "       503 = DIAG_READ_TOKEN unset on the web-api; 401 = wrong bearer;"
    log "       7/28 = web-api down or wedged. These are different faults."
    log "       Nothing was written. This is 'could not look', NOT 'nothing to do'."
    record_audit "${ACTION_SLUG}" "error" \
        "{\"reason\": \"venue capture failed\", \"curl_rc\": ${curl_rc}, \"http\": \"${http_code:-none}\"}" >/dev/null || true
    exit 1
fi

# Assert the payload is structurally usable BEFORE reading a verdict out of the
# helper. An empty/'{}' payload would make every leg read as blind and produce
# a refusal that looks like a considered decision.
CAPTURED_AT="$(python3 - "${VENUE_JSON}" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception as exc:                      # noqa: BLE001 - reported, not swallowed
    print(f"__BAD__ unparseable: {type(exc).__name__}")
    raise SystemExit(0)
if not isinstance(d, dict):
    print("__BAD__ payload is not an object"); raise SystemExit(0)
at = d.get("captured_at")
accounts = d.get("accounts")
if not at:
    print("__BAD__ no captured_at"); raise SystemExit(0)
if not isinstance(accounts, list) or not accounts:
    print("__BAD__ accounts is empty or not a list"); raise SystemExit(0)
print(at)
PY
)"
case "${CAPTURED_AT}" in
    __BAD__*)
        log "ERROR: venue payload unusable — ${CAPTURED_AT#__BAD__ }"
        log "       Nothing was written."
        record_audit "${ACTION_SLUG}" "error" \
            "{\"reason\": \"venue payload unusable\"}" >/dev/null || true
        exit 1
        ;;
esac
log "Venue evidence captured_at=${CAPTURED_AT}"

# --- ACTION_APPLY gates dry-run (default) vs the real write ------------------
APPLY_FLAG=""
case "${ACTION_APPLY:-}" in
    true|True) APPLY_FLAG="--apply" ;;
    *)         APPLY_FLAG="" ;;
esac

if [ -n "${APPLY_FLAG}" ]; then
    log "Running ${SCRIPT_NAME}.py --apply (Tier-2 money-DB write) on ${DB_PATH} …"
else
    log "Running ${SCRIPT_NAME}.py DRY RUN (pass apply: true to write) on ${DB_PATH} …"
fi
echo
echo "===== reattach_stranded_package_legs.py ${APPLY_FLAG:-(dry run)} ====="

set +e
python3 "${PY_SCRIPT}" \
    --db "${DB_PATH}" \
    --exchange-positions "${VENUE_JSON}" \
    ${APPLY_FLAG}
exit_code=$?
set -e

if [ "${exit_code}" -ne 0 ]; then
    record_audit "${ACTION_SLUG}" "failed" \
        "{\"apply\": \"${ACTION_APPLY:-}\", \"exit_code\": ${exit_code}, \"captured_at\": \"${CAPTURED_AT}\"}" >/dev/null || true
    log "ERROR: helper exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "${ACTION_SLUG}" "ok" \
    "{\"apply\": \"${ACTION_APPLY:-}\", \"captured_at\": \"${CAPTURED_AT}\"}" >/dev/null || true

echo
log "${ACTION_SLUG} complete (apply=${ACTION_APPLY:-false})."
log "⚠️ VERIFY THE POST-STATE INDEPENDENTLY — read order_packages back off"
log "   /api/diag/journal?table=order_packages and the legs off"
log "   /api/bot/positions. NEVER from this run's own output."
exit 0
