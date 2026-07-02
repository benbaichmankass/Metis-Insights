#!/usr/bin/env bash
# M19 Tier-1 GPU-burst runner — dispatch to the chosen provider adapter.
#
# The provider-specific half of the burst tier. The adapter it calls is the ONLY
# place a spot pod is launched (so the ONLY place money is spent) and it is built
# around one hard rule: **teardown is guaranteed** — the pod is terminated in a
# `finally` (RunPod adapter) / EXIT trap, so a crash or timeout can never leak a
# running (billing) pod.
#
# Env in: GPU_PROVIDER (runpod|vast|oci), the provider's API key (its own secret),
# EXPERIMENT, EST_COST, MAX_MINUTES, plus VERIFY=1 for the launch+teardown smoke test
# or PROBE=1 for the launch+SSH-probe+teardown connectivity check.
# On success it writes cost facts (gpu_type, rate, gpu_hours, cost) to $GITHUB_OUTPUT.
set -uo pipefail

: "${GPU_PROVIDER:=}"
: "${EXPERIMENT:=(unnamed)}"
: "${VERIFY:=}"
: "${PROBE:=}"

if [ -z "$GPU_PROVIDER" ]; then
  echo "::error::GPU_PROVIDER unset — no provider adapter configured. Aborting (no pod, no spend)."
  exit 3
fi

# One mode flag: --verify (launch+teardown) or --ssh-probe (launch+ssh+teardown).
MODE_FLAG=""
[ "$VERIFY" = "1" ] && MODE_FLAG="--verify"
[ "$PROBE" = "1" ] && MODE_FLAG="--ssh-probe"

case "$GPU_PROVIDER" in
  runpod)
    echo "== provider: runpod (community spot) · experiment: $EXPERIMENT · verify=${VERIFY:-0} probe=${PROBE:-0} =="
    pip install --quiet "runpod>=1.6" || { echo "::error::failed to install runpod SDK"; exit 3; }
    exec python -m scripts.ml.gpu_burst.runpod_burst \
      --experiment "$EXPERIMENT" $MODE_FLAG
    ;;
  vast|oci)
    echo "::error::GPU_PROVIDER=$GPU_PROVIDER has no verified adapter yet. Aborting safely (no spend)."
    echo "Implement + verify launch/teardown for $GPU_PROVIDER before arming."
    exit 3
    ;;
  *)
    echo "::error::unknown GPU_PROVIDER=$GPU_PROVIDER. Aborting (no spend)."
    exit 3
    ;;
esac
