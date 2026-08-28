#!/usr/bin/env bash
# ============================================================================
# Offload-drain wrapper (R3) — see deploy/trainer/ict-offload-drain.service.
#
# ⚠️ UNARMED BY DEFAULT. `OFFLOAD_DRAIN_APPLY` must be exactly `1` to write the
# registry. Anything else — unset, empty, "true", "yes", a typo — grades the
# pending drops and writes NOTHING. The permissive direction here would register
# a model, so the strict reading is the safe one and the check is exact-match.
#
# ⚠️ THIS DELIBERATELY DOES **NOT** `exit 0` ON ERROR, unlike its sibling
# trainer_git_sync.sh. That script is code-only, so a swallowed failure costs a
# retry; this one writes the model registry, so a swallowed failure is a model
# that silently never arrived — the exact class R2 exists to make loud. A failed
# unit alarming is the POINT here.
# ============================================================================
set -uo pipefail

REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"
cd "$REPO_ROOT" || { echo "offload-drain: repo root $REPO_ROOT missing"; exit 1; }

APPLY_ARG=""
if [ "${OFFLOAD_DRAIN_APPLY:-0}" = "1" ]; then
  APPLY_ARG="--apply"
  echo "offload-drain: ARMED (OFFLOAD_DRAIN_APPLY=1) — the registry WILL be written."
else
  echo "offload-drain: unarmed (OFFLOAD_DRAIN_APPLY=${OFFLOAD_DRAIN_APPLY:-unset}) — grading only, nothing will be written."
fi

PY="${PYTHON_BIN:-python3}"
[ -x "venv/bin/python" ] && PY="venv/bin/python"

exec "$PY" scripts/ml/offload/drain_inbox.py \
  --inbox "${OFFLOAD_INBOX:-ml/offload-inbox}" \
  --registry-root "${REGISTRY_ROOT:-ml/registry-store}" \
  --experiments-root "${EXPERIMENTS_ROOT:-ml/experiments-runs}" \
  --code-revision "$(git rev-parse --short HEAD 2>/dev/null || echo unknown)" \
  $APPLY_ARG
