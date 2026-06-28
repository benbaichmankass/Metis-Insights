#!/usr/bin/env bash
# Train the ETH 5m + 15m regime heads and RG3-gate them in one shot
# (MB-20260627-003). Trainer-side; run under nohup (the RG3 candle-replay
# outlives a single SSH session).
#
#   nohup bash scripts/ml/train_and_rg3_eth_finetf.sh > /tmp/eth_train_rg3.log 2>&1 &
#
# Prereq: datasets-out/market_features/ETHUSDT/{5m,15m}/v002 already built
# (scripts/ml/build_eth_finetf_datasets.sh). `ml train` registers each head at
# its manifest target_deployment_stage (shadow) so the live RG4 soak can begin
# once the registry propagates; RG3 (replay_pregate_fleet) then scores clean-
# candle discrimination — the in-session gate. RG4 (live-row skew) is future-
# dated until the heads accrue live shadow rows.
set -uo pipefail
cd "$(dirname "$0")/../.." 2>/dev/null || true
PY=python3; for c in .venv/bin/python venv/bin/python; do [ -x "$c" ] && PY="$c" && break; done
export PYTHONPATH=.

HEADS="eth-regime-5m-lgbm-v1 eth-regime-15m-lgbm-v1"

for mid in $HEADS; do
  echo "== [$(date -u +%H:%M:%S)] TRAIN ${mid} =="
  "$PY" -m ml train "ml/configs/${mid}.yaml" 2>&1 | tail -25 \
    || { echo "TRAIN ${mid} FAILED"; }
done

echo "== [$(date -u +%H:%M:%S)] registry (eth regime heads) =="
"$PY" -m ml list-models 2>/dev/null | grep -iE "eth-regime-(5m|15m)" | head

echo "== [$(date -u +%H:%M:%S)] RG3 (clean-candle discrimination) =="
"$PY" scripts/ml/replay_pregate_fleet.py \
  --models eth-regime-5m-lgbm-v1,eth-regime-15m-lgbm-v1 \
  --max-bars 8000 --json /tmp/rg3_eth.json 2>/tmp/rg3_eth.err >/dev/null \
  || { echo "RG3 run failed"; tail -8 /tmp/rg3_eth.err; }
"$PY" scripts/ml/_rg3_print.py /tmp/rg3_eth.json 2>/dev/null \
  || { echo "RG3 parse failed"; tail -5 /tmp/rg3_eth.err; }

echo "ETH_TRAIN_RG3_DONE"
