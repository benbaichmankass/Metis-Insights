#!/usr/bin/env bash
# M19 Tier-1 GPU-burst runner — launch → train → CPU/ONNX-export → TEARDOWN.
#
# The provider-specific half of the burst tier. This is the ONLY place a spot pod
# is launched, so it is also the ONLY place money is spent — and it is built around
# a single hard rule: **teardown is in a bash trap, so a crash or timeout can never
# leak a running (billing) pod.**
#
# STATUS: provider adapter PENDING. Until GPU_PROVIDER is set to a supported,
# verified backend AND the launch/teardown functions below are filled in for it,
# this script ABORTS before launching anything (exit 3) — so it cannot spend even
# if invoked. The workflow additionally gates it behind GPU_BURST_ARMED=1.
#
# Env in: GPU_PROVIDER (runpod|vast|oci), the provider's API key (as its own
# secret), EXPERIMENT, TRAIN_CMD, EST_COST, MAX_MINUTES.
# On success it writes cost facts to $GITHUB_OUTPUT (gpu_hours, rate, cost, gpu_type)
# for the workflow's record-run step.
set -uo pipefail

: "${GPU_PROVIDER:=}"
: "${MAX_MINUTES:=90}"
POD_ID=""

teardown() {
  # ALWAYS runs (EXIT trap). Provider-specific pod-kill goes here; must be
  # idempotent + best-effort so it can't itself hang the run.
  if [ -n "$POD_ID" ]; then
    echo "== teardown: terminating pod $POD_ID (provider=$GPU_PROVIDER) =="
    provider_teardown "$POD_ID" || echo "::warning::teardown reported non-zero for pod $POD_ID — the scheduled reaper is the backstop"
  fi
}
trap teardown EXIT

provider_launch()   { echo "PROVIDER_ADAPTER_NOT_IMPLEMENTED"; return 3; }
provider_teardown() { : ; }   # no-op until an adapter is wired

if [ -z "$GPU_PROVIDER" ]; then
  echo "::error::GPU_PROVIDER unset — no provider adapter configured. Aborting (no pod, no spend)."
  exit 3
fi

# ---- Provider adapter dispatch (fill in per chosen backend) --------------------
# When RunPod/Vast/OCI is chosen: implement provider_launch (→ sets POD_ID, echoes
# the rate) + provider_teardown for it, gated on the arm flag. Until then:
echo "::error::GPU_PROVIDER=$GPU_PROVIDER has no verified launch/teardown adapter yet. Aborting safely (no spend)."
echo "Next: implement provider_launch/provider_teardown for $GPU_PROVIDER, verify a manual launch+terminate, then set GPU_BURST_ARMED=1."
exit 3
# --------------------------------------------------------------------------------
#
# Verified-adapter flow (for reference, executed once the adapter above exists):
#   POD_ID="$(provider_launch)"                 # launch spot pod, capture id + rate
#   rsync_corpus_to_pod "$POD_ID"               # read-only training data only
#   timeout "${MAX_MINUTES}m" run_train_on_pod "$POD_ID" "$TRAIN_CMD"
#   export_cpu_artifact_from_pod "$POD_ID"      # torch→ONNX + numeric parity gate
#   publish_to_model_mirror                     # the same channel models arrive on
#   # teardown fires via the EXIT trap; cost facts -> $GITHUB_OUTPUT
