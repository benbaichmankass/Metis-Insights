#!/usr/bin/env bash
set -uo pipefail
cd /home/ubuntu/ict-trading-bot || exit 1
echo "===== A. pooled-dir precedent: how is eh_1h_pooled laid out? ====="
ls -la runtime_logs/m20_exit_head/eh_1h_pooled/ 2>&1 | head -20
echo "--- does it hold a single rows.jsonl? ---"
[ -f runtime_logs/m20_exit_head/eh_1h_pooled/rows.jsonl ] && echo "YES rows=$(wc -l < runtime_logs/m20_exit_head/eh_1h_pooled/rows.jsonl)" || echo "NO"
echo
echo "===== B. is there an existing POOLING script? (do not rebuild what exists) ====="
grep -rln "pooled" scripts/ml/ scripts/research/ 2>/dev/null | head -20
echo "--- any script that concatenates rows.jsonl ---"
grep -rln "rows.jsonl" scripts/ 2>/dev/null | head -20
echo
echo "===== C. row schema of one scalp leg (first row keys + symbol values) ====="
head -1 runtime_logs/m20_exit_head/scalp_5m_20260814T151003Z/ict_scalp_sol_5m/rows.jsonl | python3 -c "
import json,sys
r=json.loads(sys.stdin.read())
print('keys:', sorted(r.keys()))
print('symbol:', r.get('symbol'), '| source:', r.get('source'), '| trade_key:', r.get('trade_key'))
"
echo "--- distinct symbols per scalp leg ---"
for R in scalp_5m_20260814T151003Z scalp_15m_20260814T135244Z; do
  for LEG in runtime_logs/m20_exit_head/$R/*/; do
    L=$(basename "$LEG"); F="$LEG/rows.jsonl"
    [ -f "$F" ] || continue
    echo -n "  $L: "
    python3 -c "
import json,sys
syms=set(); tks=set()
for line in open('$F'):
    try: d=json.loads(line)
    except: continue
    syms.add(d.get('symbol')); tks.add(d.get('trade_key'))
print('symbols=',sorted(x for x in syms if x),'trades=',len(tks))
"
  done
done
echo
echo "===== D. how the donchian pooled head was actually produced (shell history / notes) ====="
grep -rn "export_exit_head" runtime_logs/m20_exit_head/*.log docs/research/M20-exit-head-PROGRAM.md 2>/dev/null | head -10
