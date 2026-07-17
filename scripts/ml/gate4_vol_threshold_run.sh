#!/usr/bin/env bash
# Gate-4 driver for MB-20260701-001 — vol-gate operating-THRESHOLD money A/B.
#
# Runs the full chain on the trainer VM, writing progress to $RESULT:
#   1. register-train the matched-sibling 0.005 head (vt005-pin) -> candidate.
#   2. register-train the matched-sibling 0.004 head (vt004-pin) -> candidate.
#   3. run scripts/ml/walkforward_vol_threshold.sh (the per-fold money A/B).
#   4. print a compact per-fold net= summary so the verdict is legible at a glance.
#
# Both pins are built identically and differ ONLY in the vol_threshold label, so the
# per-fold money delta ISOLATES the threshold change (the clean causal answer to
# "does moving the live gate 0.005->0.004 make money"). Tier-1 research tooling —
# never touches the live order path or config; the live 0.005 threshold is UNCHANGED
# regardless of this run. Run detached (nohup) — it invokes ~8 backtests.
#
# Usage (on the trainer):
#   nohup bash scripts/ml/gate4_vol_threshold_run.sh >/tmp/gate4_run.log 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/../.." 2>/dev/null || true
PY=python3; for c in .venv/bin/python venv/bin/python; do [ -x "$c" ] && PY="$c" && break; done
RESULT="${RESULT:-/tmp/gate4_result.txt}"
export PYTHONPATH=.
export ML_REGISTRY_ROOT="${ML_REGISTRY_ROOT:-ml/registry-store}"
: > "$RESULT"
log() { echo "[$(date -u +%H:%M:%S 2>/dev/null || echo g4)] $*" | tee -a "$RESULT"; }
source .venv/bin/activate 2>/dev/null || log "WARN: no .venv"

for pin in vt005 vt004; do
  mid="btc-regime-15m-lgbm-${pin}-pin-v1"
  log "STEP: register-train ${mid} (candidate)"
  "$PY" -m ml train "ml/configs/${mid}.yaml" \
    --datasets-root datasets-out --registry-root ml/registry-store >>"$RESULT" 2>&1 \
    || log "  WARN: ${pin} register-train returned nonzero (see above)"
  # Promote candidate -> shadow: backtest_system --ml-stage accepts only
  # {advisory, shadow} (candidate is refused by the shadow factory), so the pin
  # must be at shadow for the A/B to load it. candidate->shadow is autonomous
  # (below the operator-gated shadow->advisory line); shadow never influences a
  # live order — this is offline research on a NON-live registry entry.
  log "STEP: promote ${mid} candidate -> shadow"
  "$PY" -m ml promote-stage "${mid}" --new-stage shadow \
    --registry-root ml/registry-store --by "gate4-vol-threshold-AB" \
    --reason "MB-20260701-001 gate-4: candidate->shadow so backtest_system --ml-stage shadow can load the pin for the offline vol-gate money A/B (never live-order-influencing)" \
    >>"$RESULT" 2>&1 || log "  WARN: ${pin} promote-stage returned nonzero (see above)"
done
log "registry heads (pins): $(ls ml/registry-store/ 2>/dev/null | grep -E 'vt00[45]-pin' | tr '\n' ' ')"

log "STEP: walk-forward vol-gate THRESHOLD money A/B (0.005 pin vs 0.004 pin)"
bash scripts/ml/walkforward_vol_threshold.sh >>"$RESULT" 2>&1

log "=== COMPACT SUMMARY (net= lines) ==="
grep -E "== fold|0.005\(pin\)|0.004\(pin\)|WF_THRESHOLD_DONE" "$RESULT" | tee -a "$RESULT"
echo '{"gate4_done":true}' >> "$RESULT"
log "DONE — full A/B in $RESULT"
