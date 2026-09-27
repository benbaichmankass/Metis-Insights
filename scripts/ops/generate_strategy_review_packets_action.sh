#!/usr/bin/env bash
# Tier-1 generation: M7 strategy review packets.
#
# TWO GENERATORS, ONE WRAPPER (checklist row B4, 2026-09-27):
#   `strategy:` / `all_btc: true`  -> scripts/ml/strategy_review_packet.py,
#     the legacy per-strategy expectancy matrix. Still what
#     GET /api/bot/strategies/{name}/review reads.
#   `all_strategies: true`         -> scripts/ops/strategy_review_stage_aware.py
#     (the CRON/COMMITTED path). Grades each leg with the instrument its STAGE
#     calls for -- Stage 2 (bybit_2/alpaca_live) on money via R4, Stage 1
#     (bybit_1, alpaca_paper) on mechanics+cost via R5 -- instead of running
#     the expectancy matrix uniformly over a population where half of it can
#     never establish edge in the first place (root CLAUDE.md's promotion
#     ladder). See that script's module docstring for the full "what was
#     broken" and why it lives in scripts/ops/ rather than scripts/ml/.
#
# Both write under runtime_logs/strategy_reviews/<UTC-date>/, read-only with
# respect to the trade journal (sqlite mode=ro) and never touch the order
# path.
#
# Operator invokes via system-actions issue with body:
#   action: generate-strategy-review-packets
#   reason: <text>
#   strategy: <name>              (optional; repeatable via comma-separated list)
#   window_days: <int>            (optional, default 7 -- legacy matrix only)
#   all_btc: <true|1>             (optional; iterate every BTCUSDT strategy -- legacy matrix)
#   all_strategies: <true|1>      (optional; the stage-aware cron path)
#   shadow_soak_days: <int>       (optional, default 0 -- legacy matrix only, promote gate)
#   stage2_window: <str>          (optional, default 30d -- all_strategies only, R4's window)
#   stage1_window_hours: <int>    (optional, default 168 -- all_strategies only, R5's window)
#   skip_stage1_cost_pull: <true|1> (optional -- all_strategies only, mechanics-only dry run)
#
# Exactly one of `strategy:`, `all_btc: true` or `all_strategies: true` must
# be supplied. MES is intentionally excluded by the --all-btc-strategies path
# while delayed-CME-data effects are investigated separately.
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
ALL_STRATEGIES="${ACTION_ALL_STRATEGIES:-}"
SHADOW_SOAK_DAYS="${ACTION_SHADOW_SOAK_DAYS:-0}"
PRINT_PACKETS="${ACTION_PRINT_PACKETS:-}"
STAGE2_WINDOW="${ACTION_STAGE2_WINDOW:-30d}"
STAGE1_WINDOW_HOURS="${ACTION_STAGE1_WINDOW_HOURS:-168}"
SKIP_STAGE1_COST_PULL="${ACTION_SKIP_STAGE1_COST_PULL:-}"

# Tolerate the truthy values the rest of the codebase accepts.
case "${ALL_BTC,,}" in
    1|true|yes|on) ALL_BTC=1 ;;
    *) ALL_BTC=0 ;;
esac

case "${ALL_STRATEGIES,,}" in
    1|true|yes|on) ALL_STRATEGIES=1 ;;
    *) ALL_STRATEGIES=0 ;;
esac

case "${PRINT_PACKETS,,}" in
    1|true|yes|on) PRINT_PACKETS=1 ;;
    *) PRINT_PACKETS=0 ;;
esac

case "${SKIP_STAGE1_COST_PULL,,}" in
    1|true|yes|on) SKIP_STAGE1_COST_PULL=1 ;;
    *) SKIP_STAGE1_COST_PULL=0 ;;
esac

if [ "${ALL_BTC}" -ne 1 ] && [ "${ALL_STRATEGIES}" -ne 1 ] && [ -z "${STRATEGY}" ]; then
    log "ERROR: provide 'strategy: <name>', 'all_btc: true' or 'all_strategies: true'"
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

# The CRON path (.github/workflows/strategy-review-packets.yml). Kept in THIS
# wrapper rather than invoked directly by the workflow so there is exactly one
# place that knows how to run either generator on the VM — the venv, the
# runtime secrets, and above all the DB path and the output root, which
# resolve to ${DATA_DIR}/runtime_logs and NOT ${REPO_DIR}/runtime_logs. A
# second invocation path would have got that wrong (it did, in review).
if [ "${ALL_STRATEGIES}" -eq 1 ]; then
    CMD=(python3 -m scripts.ops.strategy_review_stage_aware
         --db-path "${DB_PATH}"
         --stage2-window "${STAGE2_WINDOW}"
         --stage1-window-hours "${STAGE1_WINDOW_HOURS}")
    if [ "${SKIP_STAGE1_COST_PULL}" -eq 1 ]; then
        CMD+=(--skip-stage1-cost-pull)
    fi
else
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

    if [ "${ALL_STRATEGIES}" -eq 1 ] && [ -f "${REVIEW_DIR}/INDEX.json" ]; then
        # B4 (checklist row, 2026-09-27): --all-strategies now writes ONE
        # stage-aware INDEX.json (Stage 2 via R4, Stage 1 via R5) and no
        # per-leg .json/.md files, so the OLD per-file loop below would only
        # ever see INDEX.json itself. Print the index's own summary instead.
        python3 -c "
import json, sys
idx = json.load(open(sys.argv[1]))
print(f\"schema={idx.get('schema', 'legacy_matrix')} graded={idx.get('graded')} actionable={idx.get('actionable')}\")
s2, s1 = idx.get('stage2'), idx.get('stage1')
if s2:
    print(f\"  stage2 (R4, window={s2.get('window')}): n_legs={s2.get('n_legs')} \"
          f\"verdict_reached={s2.get('verdict_reached')} actionable={s2.get('actionable')}\")
if s1:
    print(f\"  stage1 (R5, window_hours={s1.get('window_hours')}): n_legs={s1.get('n_legs')} \"
          f\"verdict_reached={s1.get('verdict_reached')} actionable={s1.get('actionable')} \"
          f\"mechanics={s1.get('mechanics_read_state')} cost_fidelity={s1.get('cost_fidelity_read_state')}\")
for r in idx.get('rows', []):
    if r.get('actionable'):
        print(f\"  ACTIONABLE {r.get('stage')} {r.get('account')}/{r.get('strategy')}: \"
              f\"{r.get('proposed_action')} -- {r.get('reason')}\")
" "${REVIEW_DIR}/INDEX.json" 2>/dev/null || echo "  (could not parse INDEX.json)"
    else
        # Legacy per-strategy path: echo the proposed_action from each packet
        # so the issue-comment gives the operator a one-line verdict per
        # strategy without needing to curl /api/bot/strategies/{name}/review.
        echo
        echo "===== proposed actions ====="
        for f in "${REVIEW_DIR}"/*.json; do
            [ -f "${f}" ] || continue
            name="$(basename "${f}" .json)"
            [ "${name}" = "INDEX" ] && continue
            action="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('proposed_action','?'))" "${f}" 2>/dev/null || echo "?")"
            printf '  %-30s %s\n' "${name}" "${action}"
        done
    fi

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
    "{\"strategy\": \"${STRATEGY}\", \"window_days\": \"${WINDOW_DAYS}\", \"all_btc\": ${ALL_BTC}, \"all_strategies\": ${ALL_STRATEGIES}, \"exit_code\": ${exit_code}}" >/dev/null || true

exit "${exit_code}"
