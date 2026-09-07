#!/usr/bin/env bash
# MI-154b: build pooled scalp exit-head datasets, train, export to STAGING,
# validate, and only then publish into the trainer mirror at stage=shadow.
set -uo pipefail
cd /home/ubuntu/ict-trading-bot || exit 1

R5=runtime_logs/m20_exit_head/scalp_5m_20260814T151003Z
R15=runtime_logs/m20_exit_head/scalp_15m_20260814T135244Z
STAGE=/tmp/mi154b_stage
MIRROR=runtime_logs/trainer_mirror/exit_head
mkdir -p "$STAGE"

echo "===== 1. assemble pooled datasets (layout: datasets-out/exit_head/<tf>/<family>/rows.jsonl) ====="
for SPEC in "5m:$R5:ict_scalp_avax_5m ict_scalp_sol_5m ict_scalp_xrp_5m" \
            "15m:$R15:ict_scalp_eth_15m ict_scalp_sol_15m ict_scalp_xrp_15m"; do
  TF="${SPEC%%:*}"; REST="${SPEC#*:}"; ROUND="${REST%%:*}"; LEGS="${REST#*:}"
  OUT="datasets-out/exit_head/${TF}/ict_scalp"
  mkdir -p "$OUT"
  : > "$OUT/rows.jsonl"
  for L in $LEGS; do
    F="$ROUND/$L/rows.jsonl"
    if [ ! -f "$F" ]; then echo "  FATAL: missing $F"; exit 1; fi
    cat "$F" >> "$OUT/rows.jsonl"
    echo "  + $L ($(wc -l < "$F") rows)"
  done
  echo "  == ${TF} pooled total: $(wc -l < "$OUT/rows.jsonl") rows"
  # POSITIVE CONTROL: assert every pooled row is a harness row before training.
  python3 -c "
import json
n=h=0; syms=set(); tks=set()
for line in open('$OUT/rows.jsonl'):
    d=json.loads(line); n+=1
    if d.get('source')=='harness': h+=1
    syms.add(d.get('symbol')); tks.add(d.get('trade_key'))
assert n>0, 'DEAD: pooled file empty'
print(f'  == ${TF} POPULATION n={n} harness={h} ({h/n:.1%}) symbols={sorted(x for x in syms if x)} trades={len(tks)}')
assert h==n, 'non-harness rows present -- exporter would silently drop them'
"
  [ $? -ne 0 ] && exit 1
done

echo
echo "===== 2. train + export to STAGING (never straight into the mirror) ====="
for TF in 5m 15m; do
  MID="exit-head-ict_scalp-${TF}-v1"
  echo "--- $MID ---"
  .venv/bin/python3 scripts/ml/export_exit_head.py \
      --family-dir "datasets-out/exit_head/${TF}/ict_scalp" \
      --tf "$TF" \
      --family ict_scalp \
      --model-id "$MID" \
      --stage shadow \
      --evidence "docs/research/MI-154b-scalp-exit-head-artifact-2026-09-07.md" \
      --out "$STAGE/${MID}.json"
  RC=$?
  echo "  exporter rc=$RC"
  [ $RC -ne 0 ] && { echo "  FATAL: export failed"; exit 1; }
done

echo
echo "===== 3. VALIDATE the staged artifacts before publishing ====="
python3 - <<'PYEOF'
import json, sys, pathlib
ok = True
for tf in ("5m", "15m"):
    p = pathlib.Path(f"/tmp/mi154b_stage/exit-head-ict_scalp-{tf}-v1.json")
    if not p.exists():
        print(f"  FAIL {tf}: staged artifact absent"); ok = False; continue
    d = json.loads(p.read_text())
    checks = {
        "family==ict_scalp": d.get("family") == "ict_scalp",
        f"tf=={tf}":         d.get("tf") == tf,
        "stage==shadow":     d.get("stage") == "shadow",
        "symbols non-empty": bool(d.get("symbols")),
        "booster present":   bool(d.get("booster_txt")),
        "train_rows>0":      (d.get("train_rows") or 0) > 0,
    }
    # a booster that will not LOAD is the failure the live guard would hit
    try:
        import lightgbm as lgb
        lgb.Booster(model_str=d["booster_txt"]); checks["booster loads"] = True
    except Exception as e:
        checks["booster loads"] = False; print("   booster load error:", e)
    print(f"  {p.name}: " + " | ".join(f"{k}={'OK' if v else 'FAIL'}" for k, v in checks.items()))
    print(f"    family={d.get('family')!r} tf={d.get('tf')} stage={d.get('stage')} "
          f"symbols={d.get('symbols')} train_rows={d.get('train_rows')} "
          f"train_trades={d.get('train_trades')} window={d.get('train_start')}..{d.get('train_end')}")
    if not all(checks.values()): ok = False
sys.exit(0 if ok else 1)
PYEOF
[ $? -ne 0 ] && { echo "VALIDATION FAILED -- publishing nothing"; exit 1; }

echo
echo "===== 4. PUBLISH into the mirror (stage=shadow only) ====="
echo "--- mirror BEFORE ---"; ls -1 "$MIRROR"
cp "$STAGE"/exit-head-ict_scalp-5m-v1.json "$MIRROR"/
cp "$STAGE"/exit-head-ict_scalp-15m-v1.json "$MIRROR"/
echo "--- mirror AFTER ---"; ls -la "$MIRROR"

echo
echo "===== 5. READ BACK from the mirror (verify, never assert) ====="
for J in "$MIRROR"/*.json; do
  python3 -c "
import json
d=json.load(open('$J'))
print('  $(basename $J): family=',repr(d.get('family')),'tf=',d.get('tf'),'stage=',d.get('stage'),'symbols=',d.get('symbols'),'rows=',d.get('train_rows'))
"
done
echo
echo "===== 6. disk after ====="
df -h / | tail -1
