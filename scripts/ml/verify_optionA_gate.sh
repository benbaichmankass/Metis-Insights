#!/usr/bin/env bash
# One-off: verify the option-A regime gate (live_regime_discrimination) on the
# two BTC 15m lgbm promotion candidates, then restore the trainer's ml/ tree.
#
# The CALLER (trainer-vm-diag relay) checks out the branch's ml/ + scripts/ml
# FIRST (so `python -m ml gate-check` runs the new gate code), then runs this as
# a nohup background job. This script restores HEAD's ml/ + scripts/ml at the end
# so the trainer is left on its normal code.
#
#   relay:  git checkout origin/<BR> -- ml scripts/ml
#           nohup bash scripts/ml/verify_optionA_gate.sh > /tmp/gcv2.out 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/../.." 2>/dev/null || true
PY=python3; for c in .venv/bin/python venv/bin/python; do [ -x "$c" ] && PY="$c" && break; done
RR=$(PYTHONPATH=. "$PY" -c "from ml.shadow import factory;print(factory._resolve_default_registry_root())" 2>/dev/null)
echo "registry=$RR"
for h in btc-regime-15m-lgbm-yz-v1 btc-regime-15m-lgbm-v2; do
  echo "#### $h ####"
  # gate-check has NO --json flag — it prints a human preamble + the JSON report
  # to stdout. Capture stdout, parse from the first brace.
  PYTHONPATH=. "$PY" -m ml gate-check "$h" --registry-root "$RR" \
    --db data/trade_journal.db --shadow-log runtime_logs/shadow_predictions.jsonl \
    --datasets-root datasets-out >"/tmp/gc_$h.out" 2>"/tmp/gc_$h.err" \
    || echo "  gate-check rc=$?"
  H="$h" "$PY" -c "import json,os
h=os.environ['H']
try:
    t=open('/tmp/gc_'+h+'.out').read()
    d=json.loads(t[t.index('{'):])
    print(' ready=',d['ready'],' blocking=',d['blocking'])
    keep={'live_agreement','live_regime_discrimination','oos_edge','drift_clean','shadow_soak'}
    for g in d['gates']:
        if g['name'] in keep:
            print('   ',g['name'],g['status'],'|',g.get('detail'))
except Exception as e:
    print('  parse-fail',repr(e))
    try: print(open('/tmp/gc_'+h+'.err').read()[-500:])
    except Exception: pass
"
done
echo "== restore trainer ml/ + scripts/ml =="
git checkout HEAD -- ml scripts/ml 2>&1 | tail -1 || true
git reset -q HEAD ml scripts/ml 2>/dev/null || true   # unstage branch-only files
echo DONE_RESTORED
