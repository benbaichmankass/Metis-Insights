#!/usr/bin/env bash
# MI-154b: re-validate the STAGED scalp artifacts using the venv interpreter
# (the previous run validated with system python3, which has no lightgbm --
# a bug in the CHECK, not in the artifacts), then publish at stage=shadow.
set -uo pipefail
cd /home/ubuntu/ict-trading-bot || exit 1
STAGE=/tmp/mi154b_stage
MIRROR=runtime_logs/trainer_mirror/exit_head

echo "===== 0. staged artifacts still present? ====="
ls -la "$STAGE" 2>&1 || { echo "FATAL: staging gone, re-run the build"; exit 1; }

echo
echo "===== 1. VALIDATE with the venv interpreter (positive control included) ====="
.venv/bin/python3 - <<'PYEOF'
import json, sys, pathlib
import lightgbm as lgb
print("  positive control: lightgbm", lgb.__version__, "importable in this interpreter")
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
        "train_rows>0":      (d.get("train_rows") or 0) > 0,
    }
    try:
        b = lgb.Booster(model_str=d["booster_txt"])
        checks["booster loads"] = True
        checks["feature count matches"] = b.num_feature() == len(d.get("features") or [])
        print(f"    booster: {b.num_trees()} trees, {b.num_feature()} features; "
              f"artifact declares {len(d.get('features') or [])}")
    except Exception as e:
        checks["booster loads"] = False; print("    booster load error:", e)
    print(f"  {p.name}: " + " | ".join(f"{k}={'OK' if v else 'FAIL'}" for k, v in checks.items()))
    if not all(checks.values()): ok = False
print("  VALIDATION:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
PYEOF
if [ $? -ne 0 ]; then echo "VALIDATION FAILED -- publishing nothing"; exit 1; fi

echo
echo "===== 2. PUBLISH into the mirror (stage=shadow only, no promotion) ====="
echo "--- mirror BEFORE (POPULATION: *.json in $MIRROR) ---"
ls -1 "$MIRROR"/*.json | wc -l; ls -1 "$MIRROR"
cp "$STAGE"/exit-head-ict_scalp-5m-v1.json "$MIRROR"/
cp "$STAGE"/exit-head-ict_scalp-15m-v1.json "$MIRROR"/
echo "--- mirror AFTER ---"; ls -la "$MIRROR"

echo
echo "===== 3. READ BACK from the mirror -- verify, never assert ====="
.venv/bin/python3 - <<'PYEOF'
import json, pathlib
d = pathlib.Path("runtime_logs/trainer_mirror/exit_head")
files = sorted(d.glob("*.json"))
print(f"  POPULATION: {len(files)} artifact(s) in the mirror")
assert files, "DEAD READ: mirror glob returned nothing"
for p in files:
    a = json.loads(p.read_text())
    print(f"  {p.name}: family={a.get('family')!r} tf={a.get('tf')} stage={a.get('stage')} "
          f"symbols={a.get('symbols')} rows={a.get('train_rows')}")
scalp = [p.name for p in files if json.loads(p.read_text()).get("family") == "ict_scalp"]
print(f"  ict_scalp-family artifacts now published: {len(scalp)} -> {scalp}")
PYEOF

echo
echo "===== 4. publish channel: is the mirror rsync timer live? ====="
systemctl is-active ict-trainer-publish.timer 2>&1
systemctl list-timers ict-trainer-publish.timer --no-pager 2>&1 | head -4
echo
echo "===== 5. disk ====="
df -h / | tail -1
