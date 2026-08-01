#!/usr/bin/env bash
# Promotion gate-check for a set of shadow regime heads — runs the FULL
# computed shadow->advisory gate (ml.promotion.gates via `python -m ml
# gate-check`) for each candidate, WITH --datasets-root so the offline
# purged-WF-CV oos_edge gate populates, and WITH the live --db so the
# live-attribution gates (sample_sufficiency / live_agreement / drift) run.
#
# Reports only — `gate-check` never mutates the registry and never touches
# the order path (the shadow->advisory flip stays operator-gated). This is
# the evidence packet the operator reads before approving a promotion.
#
# Designed to run on the trainer VM (where the registry + datasets live),
# ideally as a nohup background job since each model's oos_edge runs a
# purged WF-CV that outlives a single SSH session.
#
# Usage:  bash scripts/ml/gate_check_candidates.sh [model_id ...]
#   (defaults to the BTC 5m/15m lgbm promotion candidates from the
#    2026-06-26 fleet scorecard)
set -uo pipefail
cd "$(dirname "$0")/../.." 2>/dev/null || true
PY=python3; for c in .venv/bin/python venv/bin/python; do [ -x "$c" ] && PY="$c" && break; done

# Resolve the registry root the SAME way the shadow factory / fleet scorecard
# does (the resolver that found all 17 heads), rather than guessing a path.
RR=$(PYTHONPATH=. "$PY" -c "from ml.shadow import factory; print(factory._resolve_default_registry_root())" 2>/dev/null)
[ -z "$RR" ] && RR=ml/registry-store
DB=data/trade_journal.db; [ -f "$DB" ] || DB=trade_journal.db
SL=runtime_logs/shadow_predictions.jsonl
DS=datasets-out
# Refresh the label feedstock + shadow log from the live VM BEFORE gating.
# The live_parity gate counts rows scored since the CURRENT artifact's
# training run, and the nightly retrain (~01:00Z) resets that window — so a
# gate-check against a stale trainer-side shadow log (synced ~05:00Z daily)
# can never accumulate the 20-row parity bar and reads a permanent
# insufficient_data (observed 2026-08-01: counts frozen at 13-14 across a 7h
# gap until a manual sync unblocked them, MB-20260721-FCPCV-V2-SOAK).
# Best-effort: a sync failure falls through to whatever log is present.
if [ -x scripts/ops/sync_trainer_data.sh ] || [ -f scripts/ops/sync_trainer_data.sh ]; then
  bash scripts/ops/sync_trainer_data.sh >/dev/null 2>&1 \
    || echo "WARN: sync_trainer_data.sh failed — gating against the existing (possibly stale) shadow log"
fi

echo "== gate-check fleet :: registry=$RR db=$DB shadow_log=$SL datasets=$DS =="

DEFAULT_MODELS="btc-regime-5m-lgbm-yz-v1 btc-regime-5m-lgbm-v2 btc-regime-15m-lgbm-yz-v1 btc-regime-15m-lgbm-v2 btc-regime-5m-baseline-v1 btc-regime-15m-baseline-v1"
MODELS="${*:-$DEFAULT_MODELS}"

for mid in $MODELS; do
  echo ""
  echo "######## GATE-CHECK $mid ########"
  PYTHONPATH=. "$PY" -m ml gate-check "$mid" \
    --registry-root "$RR" --db "$DB" --shadow-log "$SL" \
    --datasets-root "$DS" --gate-profile auto 2>&1 || echo "  gate-check FAILED for $mid"
done
echo ""
echo "== GATE-CHECK FLEET DONE =="
