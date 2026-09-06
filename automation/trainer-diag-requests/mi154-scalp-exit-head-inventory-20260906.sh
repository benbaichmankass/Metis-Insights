#!/usr/bin/env bash
# MI-154 — does an E0 exit-head dataset for the ict_scalp 5m/15m legs still exist?
# Read-only inventory. No training, no writes.
set -u
cd /home/ubuntu/ict-trading-bot 2>/dev/null || cd /home/ubuntu/metis-insights 2>/dev/null || { echo "NO REPO DIR"; exit 2; }
echo "=== PWD / HEAD ==="; pwd; git log -1 --format='%h %ci %s' 2>&1 | head -1
echo
echo "=== A. exit_head dataset dirs ==="
find . -type d -name "exit_head" -not -path "./.git/*" 2>/dev/null | head -20
echo "--- families under each ---"
for d in $(find . -type d -name "exit_head" -not -path "./.git/*" 2>/dev/null | head -8); do
  echo "### $d"; find "$d" -maxdepth 2 -mindepth 1 -type d 2>/dev/null | head -30
done
echo
echo "=== B. every rows.jsonl under an exit_head tree (size, mtime, lines) ==="
find . -path "*exit_head*" -name "rows.jsonl" -not -path "./.git/*" -printf '%10s  %TY-%Tm-%Td  %p\n' 2>/dev/null | sort -k3 | head -40
echo
echo "=== C. POSITIVE CONTROL — the donchian family dir that produced the LIVE 1h head ==="
echo "(if this is also absent, section B's silence is a broken probe, not an answer)"
find . -path "*exit_head*" -ipath "*donchian*" -not -path "./.git/*" 2>/dev/null | head -20
echo
echo "=== D. anything named for the two scalp rounds in the coverage ref ==="
find . -type d \( -name "*scalp_5m_2026*" -o -name "*scalp_15m_2026*" \) -not -path "./.git/*" 2>/dev/null | head -20
ls -dt runtime_logs/m20_* 2>/dev/null | head -15
echo
echo "=== E. published exit-head artifacts in the trainer mirror (what the live VM receives) ==="
ls -la runtime_logs/trainer_mirror/exit_head/ 2>&1 | head -20
echo "--- their declared family/tf/symbols/stage ---"
for f in runtime_logs/trainer_mirror/exit_head/*.json; do
  [ -e "$f" ] || continue
  python3 -c "
import json,sys
a=json.load(open('$f'))
print('  %-46s family=%-12s tf=%-4s stage=%-9s symbols=%s rows=%s' % (
  a.get('model_id'), a.get('family'), a.get('tf'), a.get('stage'),
  a.get('symbols'), a.get('train_rows')))
" 2>&1 | head -3
done
echo
echo "=== F. dataset GC — has it pruned exit_head? ==="
tail -n 20 runtime_logs/trainer/dataset_gc.jsonl 2>/dev/null || echo "(no dataset_gc.jsonl)"
echo
echo "=== G. canonical 5m/15m candle CSVs (what a rebuild would need) ==="
ls -la data/*_5m.csv data/*_15m.csv 2>/dev/null | head -20 || echo "(none matching data/*_5m.csv)"
echo "--- any SOL/XRP/AVAX/ETH 5m or 15m csv anywhere ---"
find . -name "*_5m.csv" -o -name "*_15m.csv" 2>/dev/null | grep -v "\.git" | head -20
echo
echo "=== H. disk + service ==="
df -h / /data 2>/dev/null | head -5
systemctl is-active ict-trainer.service 2>&1
echo "=== END ==="
