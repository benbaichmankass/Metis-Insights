#!/usr/bin/env bash
# Gate-4 for MB-20260701-001 — vol-gate operating-THRESHOLD money A/B.
#
# Compares the SHIPPED 0.005 advisory head (btc-regime-15m-lgbm-v2) vs a
# CANDIDATE denser-label 0.004 head (btc-regime-15m-lgbm-vt004-pin-v1) as the
# vol-gate's volatile verdict, across the same consecutive BTC folds as the
# 2026-06-27 go-live A/B (walkforward_vol_gating.sh). This is the DECISIVE money
# evidence behind any live threshold change: gate-2 pinned the classifier curve
# (f1_volatile 0.249->0.341->0.468) but f1 is prevalence-sensitive and the gate
# is a money-routing switch — the denser head justifies a live change ONLY if its
# gated book >= the 0.005 book on NET PnL AND not worse on maxDD%, out-of-sample
# per fold (the FLIP_POLICY-style acceptance bar).
#
# Tier-1 research tooling — never touches the live order path or config. Reads the
# registry (ML_REGISTRY_ROOT) + the candidate policy; runs the existing harness
# per fold. The live 0.005 threshold is UNCHANGED regardless of this run.
#
# PREREQ (run first, on the trainer): register-train BOTH matched-sibling heads as
# candidates (no --no-register -> lands candidate). scripts/ml/gate4_vol_threshold_run.sh
# does this then calls this harness:
#   python -m ml train ml/configs/btc-regime-15m-lgbm-vt005-pin-v1.yaml --datasets-root datasets-out
#   python -m ml train ml/configs/btc-regime-15m-lgbm-vt004-pin-v1.yaml --datasets-root datasets-out
#
# Usage:
#   bash scripts/ml/walkforward_vol_threshold.sh [DATA_CSV] [CANDIDATE_POLICY_YAML]
set -uo pipefail
cd "$(dirname "$0")/../.." 2>/dev/null || true
PY=python3; for c in .venv/bin/python venv/bin/python; do [ -x "$c" ] && PY="$c" && break; done
DATA="${1:-data/backtest_BTCUSDT_5m.csv}"
CAND="${2:-docs/research/regime_policy_trend_vol_candidate-2026-06-27.yaml}"
ROSTER="trend_donchian,squeeze_breakout_4h,htf_pullback_trend_2h"
H005="btc-regime-15m-lgbm-vt005-pin-v1"  # matched-sibling 0.005 head (shadow stage)
H004="btc-regime-15m-lgbm-vt004-pin-v1"  # matched-sibling 0.004 head (shadow stage)
# NB: backtest_system --ml-stage accepts only {advisory, shadow} (candidate is
# refused by the shadow factory), so the gate4 driver promotes both pins
# candidate->shadow before this A/B. shadow never influences a live order.
# Matched siblings: vt005-pin and vt004-pin are built identically (same 7 features,
# same 60d recency half-life, same purged 5-fold CV, matched inverse-base-rate
# class weight) and differ ONLY in the vol_threshold label (0.005 vs 0.004). So the
# per-fold money delta ISOLATES the threshold change — the clean causal answer to
# "does moving the live gate 0.005->0.004 make money", free of the confounds a
# production-v2 baseline (different data window/build) would carry. (A production-v2
# cross-check is a documented follow-up, not this controlled comparison.)
export PYTHONPATH=.
export ML_REGISTRY_ROOT="${ML_REGISTRY_ROOT:-ml/registry-store}"
# Consecutive yearly folds (non-overlap = an implicit purge between folds) —
# identical to the go-live vol-gate walk-forward so the two are comparable.
FOLDS="2022-07-01:2023-07-01 2023-07-01:2024-07-01 2024-07-01:2025-07-01 2025-07-01:2026-06-01"

run_arm() {  # $1=label $2..=extra flags ; reads $S/$E
  local label="$1"; shift
  local line
  line=$("$PY" scripts/backtest_system.py --data "$DATA" --roster "$ROSTER" \
           --regime-router on --regime-policy "$CAND" --vol-verdict ml \
           --start "$S" --end "$E" "$@" 2>/dev/null | grep -m1 "net=")
  echo "  ${label}: ${line:-<no-output>}"
}

echo "== vol-gate THRESHOLD money A/B (0.005 head vs 0.004 head), BTC, candidate trend_vol cells =="
echo "data=$DATA  policy=$CAND  h005=$H005  h004=$H004"
for f in $FOLDS; do
  S="${f%%:*}"; E="${f##*:}"
  echo "== fold ${S} .. ${E} =="
  run_arm "0.005(pin)" --ml-model-id "$H005" --ml-stage shadow
  run_arm "0.004(pin)" --ml-model-id "$H004" --ml-stage shadow
done
echo WF_THRESHOLD_DONE
