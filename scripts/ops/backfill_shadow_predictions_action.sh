#!/usr/bin/env bash
# Tier-2 operator action: retroactive shadow-prediction backfill.
#
# Replays every historical trade in trade_journal.db against every model
# currently at target_deployment_stage=shadow and writes one JSONL record
# per (trade, model) pair to runtime_logs/shadow_predictions_backfill.jsonl
# (the writer truncates on each run). Read by /api/bot/trades/scores via
# the backfill_kind fields so the dashboard shows shadow decisions for the
# full live history, not only from real-time logging onward.
#
# Observational only: the sole write is the backfill JSONL. No trade
# journal mutation, no service restart, no exchange calls. Safe to re-run.
#
# The registry root and output path are resolved through the SAME Python
# the live shadow factory / trades-scores reader use (factory
# DEFAULT_REGISTRY_ROOT and src.utils.paths.runtime_logs_dir) so the
# backfill scores against exactly the models the real-time path auto-wires
# and writes exactly where the API reads — no path drift.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

load_runtime_env  # exports DATA_DIR / TRADE_JOURNAL_DB / RUNTIME_LOGS_DIR
DB_PATH="$(runtime_db_path)"

# Pick the interpreter the way every other live-VM wrapper does: the live
# VM runs the system /usr/bin/python3 (deps installed at deploy time, no
# venv), while the trainer VM uses a .venv. Prefer the venv when it
# exists, else fall back to python3 on PATH.
if [ -x "${REPO_DIR}/.venv/bin/python" ]; then
    PY="${REPO_DIR}/.venv/bin/python"
else
    PY="python3"
fi

if ! command -v "${PY}" >/dev/null 2>&1; then
    log "ERROR: no python interpreter found (tried ${REPO_DIR}/.venv/bin/python and python3)."
    record_audit "backfill-shadow-predictions" "error" \
        "{\"reason\": \"python missing\"}" >/dev/null || true
    exit 1
fi
if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "backfill-shadow-predictions" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

cd "${REPO_DIR}"

# Resolve registry root + output dir exactly as the live runtime does.
REGISTRY_ROOT="$("${PY}" -c 'from ml.shadow.factory import DEFAULT_REGISTRY_ROOT; print(DEFAULT_REGISTRY_ROOT)')"
RUNTIME_LOGS="$("${PY}" -c 'from src.utils.paths import runtime_logs_dir; print(runtime_logs_dir())')"
OUTPUT="${RUNTIME_LOGS}/shadow_predictions_backfill.jsonl"

if [ ! -d "${REGISTRY_ROOT}" ]; then
    log "ERROR: registry root ${REGISTRY_ROOT} missing — no shadow models to score against."
    record_audit "backfill-shadow-predictions" "error" \
        "{\"reason\": \"registry missing\", \"path\": \"${REGISTRY_ROOT}\"}" >/dev/null || true
    exit 1
fi

# Pre-check: how many shadow-stage models will be scored? Zero means the
# backfill would write an empty file — surface it loudly rather than
# silently producing nothing.
model_count="$("${PY}" -c 'from ml.registry.model_registry import ModelRegistry; from ml.shadow.factory import discover_shadow_stage_model_ids, DEFAULT_REGISTRY_ROOT; print(len(discover_shadow_stage_model_ids(ModelRegistry(DEFAULT_REGISTRY_ROOT))))' 2>/dev/null || echo "?")"
log "backfill-shadow-predictions: db=${DB_PATH} registry=${REGISTRY_ROOT} shadow_models=${model_count} output=${OUTPUT}"
if [ "${model_count}" = "0" ]; then
    log "WARNING: zero shadow-stage models discovered; backfill will be empty."
fi

summary="$("${PY}" -m ml backfill-shadow-predictions \
    --db "${DB_PATH}" \
    --registry-root "${REGISTRY_ROOT}" \
    --output "${OUTPUT}")"

log "backfill summary:"
printf '%s\n' "${summary}"

record_audit "backfill-shadow-predictions" "ok" \
    "{\"output\": \"${OUTPUT}\", \"shadow_models\": \"${model_count}\"}" >/dev/null || true
