#!/usr/bin/env bash
# Tier-1 system-action: write ONE always-dry PHONE test ticket for a phone-driven
# prop account (PI-20261006-APBY4NTV-0009).
#
# Operator, 2026-10-06: "is there no way to get around doing the full test suite
# each time? It's a lot of time to check a one line change" — bumping
# config/prop_platforms.yaml::phone_accounts.<acct>.dry_test_request was the
# only operator-free way to send the phone a dry test ticket, at ~32 min of CI
# plus ~5 min of git-sync per ticket. This does it in one issue-dispatched run.
#
# It calls scripts/prop/phone_dry_test.py, which uses the SAME writer the
# in-app Dry test button and dry_test_request use
# (src.prop.phone_executor.make_test_ticket): meta.test=True, so the server
# forces submit=dry whatever the account mode and the phone never submits it.
# The account must be a phone_accounts entry and the symbol one of its
# instruments — anything else is REFUSED (exit 1, nothing written). No flag
# writes a non-test ticket. No exchange socket; one prop_tickets row.
#
# Dispatched by the system-actions workflow (issue body:
#   action: phone-dry-test
#   account: breakout_2          (required)
#   symbol: ETHUSDT              (optional, default ETHUSDT)
# ). The workflow threads these as ACCOUNT_ID / ACTION_SYMBOL env vars and the
# dispatching issue number as ACTION_ISSUE (recorded in meta.source).
#
# Exit codes: 0 = ticket written (status=emitted), 1 = refused or failed.

set -euo pipefail

SCRIPT_NAME="phone_dry_test_action"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

# The ticket must land in the canonical trade_journal.db the web API serves and
# the phone claims from — resolve DATA_DIR / TRADE_JOURNAL_DB the way the trader does.
load_runtime_env

ACCOUNT_ID="${ACCOUNT_ID:?ACCOUNT_ID required (issue body: account: <phone_accounts key>)}"
SYMBOL="${ACTION_SYMBOL:-ETHUSDT}"
SOURCE="phone-dry-test${ACTION_ISSUE:+#${ACTION_ISSUE}}"
WRITER="${REPO_DIR}/scripts/prop/phone_dry_test.py"

if [ ! -f "${WRITER}" ]; then
    log "ERROR: ${WRITER} not found."
    record_audit "phone-dry-test" "error" '{"reason": "phone_dry_test.py missing"}' >/dev/null || true
    exit 1
fi

log "Writing ONE dry phone test ticket (account=${ACCOUNT_ID} symbol=${SYMBOL} source=${SOURCE})."
if OUT="$(cd "${REPO_DIR}" && /usr/bin/python3 "${WRITER}" --account "${ACCOUNT_ID}" --symbol "${SYMBOL}" --source "${SOURCE}")"; then
    printf '%s\n' "${OUT}"
    record_audit "phone-dry-test" "ok" \
        "{\"account\": \"${ACCOUNT_ID}\", \"symbol\": \"${SYMBOL}\", \"result\": ${OUT:-null}}" >/dev/null || true
    log "phone-dry-test: ticket written as status=emitted; the phone claims it on its next poll (then dry_filled)."
    exit 0
else
    printf '%s\n' "${OUT:-}"
    record_audit "phone-dry-test" "failed" \
        "{\"account\": \"${ACCOUNT_ID}\", \"symbol\": \"${SYMBOL}\", \"result\": ${OUT:-null}}" >/dev/null || true
    log "ERROR: phone_dry_test.py refused or failed (nothing written)."
    exit 1
fi
