#!/usr/bin/env bash
# MI-154b probe: establish the ground truth needed before training a scalp exit head.
set -uo pipefail
cd /home/ubuntu/ict-trading-bot || { echo "FATAL: repo dir missing"; exit 1; }

echo "===== 1. repo state (does it carry #11169's --family flag?) ====="
git log -1 --oneline
echo "--family present in exporter: $(grep -c '\-\-family' scripts/ml/export_exit_head.py)"
echo

echo "===== 2. E0 scalp round dirs (POPULATION: everything under runtime_logs/m20_exit_head) ====="
ls -1 runtime_logs/m20_exit_head/ 2>&1 | head -40
echo "--- total round dirs: $(ls -1 runtime_logs/m20_exit_head/ 2>/dev/null | wc -l) ---"
echo

echo "===== 3. scalp round contents + harness row counts ====="
for R in runtime_logs/m20_exit_head/scalp_5m_20260814T151003Z runtime_logs/m20_exit_head/scalp_15m_20260814T135244Z; do
  echo "### $R"
  if [ ! -d "$R" ]; then echo "  ABSENT"; continue; fi
  for LEG in "$R"/*/; do
    L=$(basename "$LEG")
    F="$LEG/rows.jsonl"
    if [ -f "$F" ]; then
      TOTAL=$(wc -l < "$F")
      HARNESS=$(grep -c '"source": *"harness"' "$F" 2>/dev/null || echo 0)
      echo "  $L: total=$TOTAL harness=$HARNESS"
    else
      echo "  $L: no rows.jsonl ($(ls "$LEG" 2>/dev/null | tr '\n' ' '))"
    fi
  done
done
echo

echo "===== 4. current published mirror artifacts (POPULATION: the exit_head mirror dir) ====="
ls -la runtime_logs/trainer_mirror/exit_head/ 2>&1
echo "--- declared family/tf/stage per artifact ---"
for J in runtime_logs/trainer_mirror/exit_head/*.json; do
  [ -f "$J" ] || continue
  python3 -c "
import json,sys
d=json.load(open('$J'))
print('  $(basename $J):','family=',d.get('family'),'tf=',d.get('tf'),'stage=',d.get('stage'),'symbols=',d.get('symbols'),'train_rows=',d.get('train_rows'))
" 2>&1
done
echo

echo "===== 5. toolchain ====="
ls .venv/bin/python3 2>&1
.venv/bin/python3 -c "import lightgbm,sys;print('lightgbm',lightgbm.__version__,'python',sys.version.split()[0])" 2>&1
echo
echo "===== 6. disk ====="
df -h / | tail -1
