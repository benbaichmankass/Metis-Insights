#!/usr/bin/env bash
# Tier-1 generation: M7 strategy review packets.
#
# Runs scripts/ml/strategy_review_packet.py against the live trade
# journal and writes per-strategy review packets (JSON + Markdown)
# under runtime_logs/strategy_reviews/<UTC-date>/. The packets are
# read-only with respect to the trade journal (sqlite mode=ro) and
# never touch the order path; the only writes land under
# runtime_logs/strategy_reviews/ which the API route
# GET /api/bot/strategies/{name}/review serves.
#
# Operator invokes via system-actions issue with body:
#   action: generate-strategy-review-packets
#   reason: <text>
#   strategy: <name>           (optional; repeatable via comma-separated list)
#   window_days: <int>         (optional, default 7)
#   all_btc: <true|1>          (optional; iterate every BTCUSDT strategy)
#   shadow_soak_days: <int>    (optional, default 0 — only matters for promote)
#
# Either `strategy:` or `all_btc: true` must be supplied (the python
# script's CLI enforces this too). MES is intentionally excluded by
# the --all-btc-strategies path while delayed-CME-data effects are
# investigated separately.
set -euo pipefail

SCRIPT_NAME="generate_strategy_review_packets"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

load_runtime_secrets
DB_PATH="$(runtime_db_path)"

STRATEGY="${ACTION_STRATEGY:-}"
WINDOW_DAYS="${ACTION_WINDOW_DAYS:-7}"
ALL_BTC="${ACTION_ALL_BTC:-}"
SHADOW_SOAK_DAYS="${ACTION_SHADOW_SOAK_DAYS:-0}"
PRINT_PACKETS="${ACTION_PRINT_PACKETS:-}"

# Tolerate the truthy values the rest of the codebase accepts.
case "${ALL_BTC,,}" in
    1|true|yes|on) ALL_BTC=1 ;;
    *) ALL_BTC=0 ;;
esac

case "${PRINT_PACKETS,,}" in
    1|true|yes|on) PRINT_PACKETS=1 ;;
    *) PRINT_PACKETS=0 ;;
esac

if [ "${ALL_BTC}" -ne 1 ] && [ -z "${STRATEGY}" ]; then
    log "ERROR: provide 'strategy: <name>' or 'all_btc: true' in the issue body"
    record_audit "generate-strategy-review-packets" "error" \
        "{\"reason\": \"no strategy or all_btc flag\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db missing at ${DB_PATH}"
    record_audit "generate-strategy-review-packets" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

cd "${REPO_DIR}"

CMD=(python3 -m scripts.ml.strategy_review_packet
     --window-days "${WINDOW_DAYS}"
     --shadow-soak-days "${SHADOW_SOAK_DAYS}"
     --db-path "${DB_PATH}")

if [ "${ALL_BTC}" -eq 1 ]; then
    CMD+=(--all-btc-strategies)
fi

# Comma-split STRATEGY → repeated --strategy NAME flags.
if [ -n "${STRATEGY}" ]; then
    IFS=',' read -r -a strategies_arr <<< "${STRATEGY}"
    for s in "${strategies_arr[@]}"; do
        s_trimmed="$(echo "${s}" | xargs)"
        if [ -n "${s_trimmed}" ]; then
            CMD+=(--strategy "${s_trimmed}")
        fi
    done
fi

echo
echo "===== ${CMD[*]} ====="
set +e
TRADE_JOURNAL_DB="${DB_PATH}" "${CMD[@]}"
exit_code=$?
set -e

# Surface the per-day output dir so the operator can see what landed
# without a second relay round-trip.
#
# scripts/ml/strategy_review_packet.py defaults --out-dir to
# src.utils.paths.runtime_logs_dir()/strategy_reviews, which resolves
# to ${DATA_DIR}/runtime_logs (/data/bot-data/runtime_logs on the live
# VM) — NOT ${REPO_DIR}/runtime_logs. Reuse load_runtime_env's
# RUNTIME_LOGS_DIR (falling back to DATA_DIR, then REPO_DIR, matching
# the Python resolver's own fallback chain) so this wrapper looks in
# the same place the packets actually landed (BL-20260630-PRINTPACKETS).
load_runtime_env
TODAY="$(date -u +%Y-%m-%d)"
REVIEW_ROOT="${RUNTIME_LOGS_DIR:-${DATA_DIR:+${DATA_DIR}/runtime_logs}}"
REVIEW_ROOT="${REVIEW_ROOT:-${REPO_DIR}/runtime_logs}"
REVIEW_DIR="${REVIEW_ROOT}/strategy_reviews/${TODAY}"
echo
echo "===== ${REVIEW_ROOT}/strategy_reviews/${TODAY}/ ====="
if [ -d "${REVIEW_DIR}" ]; then
    ls -la "${REVIEW_DIR}" || true
    # Echo the proposed_action from each packet so the issue-comment
    # gives the operator a one-line verdict per strategy without needing
    # to curl /api/bot/strategies/{name}/review afterwards.
    echo
    echo "===== proposed actions ====="
    for f in "${REVIEW_DIR}"/*.json; do
        [ -f "${f}" ] || continue
        name="$(basename "${f}" .json)"
        action="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('proposed_action','?'))" "${f}" 2>/dev/null || echo "?")"
        printf '  %-30s %s\n' "${name}" "${action}"
    done

    # When ACTION_PRINT_PACKETS=true, also cat the full Markdown summary
    # of each packet written this run. Useful for sandbox sessions
    # (which can't reach the live VM directly) that need the matrix's
    # reasons + headline / regime-cell table inline in the issue
    # comment, not just the one-line verdict above. Kept opt-in so the
    # default routine run stays terse.
    if [ "${PRINT_PACKETS}" -eq 1 ]; then
        for f in "${REVIEW_DIR}"/*.md; do
            [ -f "${f}" ] || continue
            name="$(basename "${f}" .md)"
            echo
            echo "===== packet: ${name}.md ====="
            cat "${f}" || true
        done
    fi
else
    echo "  (no packets written — review_dir absent)"
fi

record_audit "generate-strategy-review-packets" "ok" \
    "{\"strategy\": \"${STRATEGY}\", \"window_days\": \"${WINDOW_DAYS}\", \"all_btc\": ${ALL_BTC}, \"exit_code\": ${exit_code}}" >/dev/null || true

exit "${exit_code}"
