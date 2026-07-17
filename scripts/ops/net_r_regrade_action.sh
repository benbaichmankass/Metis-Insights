#!/usr/bin/env bash
# Tier-1 read-only analysis: M24 P2 net-R re-grade scorecard.
#
# Wraps scripts/research/net_r_regrade.py. Opens trade_journal.db STRICTLY
# READ-ONLY (mode=ro URI inside the script), recomputes the per-strategy /
# per-(strategy,symbol) aggregates on TRUE net-of-cost R (gross_pnl minus the
# Slice-B broker/estimate fee + funding columns, over the same SL-distance risk
# denominator the /performance R-metrics use), and prints the markdown
# scorecard — coverage buckets (broker / estimate / uncosted / r_uncomputable),
# per-strategy + per-cell Σgross_R vs Σnet_R + cost-drag, and the sign-flip flag
# (a cell that is gross-positive but net-negative after real costs → a Tier-3
# review candidate).
#
# Observability-only: NO DB write, NO CREATE, NO order path, NO service. The
# script never changes config; sign-flips are flagged for the operator, not
# enacted. Design of record: docs/research/M24-net-r-cost-aware-DESIGN.md (P2).
#
# Operator/Claude invokes via a system-actions issue:
#   action: net-r-regrade
#   reason: <text>
set -euo pipefail

SCRIPT_NAME="net_r_regrade"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/research/net_r_regrade.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: helper missing at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "net-r-regrade" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db missing at ${DB_PATH}"
    record_audit "net-r-regrade" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

echo
echo "===== net_r_regrade.py --db ${DB_PATH} (read-only) ====="
set +e
python3 "${PY_SCRIPT}" --db "${DB_PATH}"
exit_code=$?
set -e

if [ "${exit_code}" -ne 0 ]; then
    record_audit "net-r-regrade" "failed" \
        "{\"exit_code\": ${exit_code}}" >/dev/null || true
    log "Helper exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "net-r-regrade" "ok" "{\"db\": \"${DB_PATH}\"}" >/dev/null || true
log "net-r-regrade complete."
exit 0
