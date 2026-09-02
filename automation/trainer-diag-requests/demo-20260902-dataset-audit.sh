cd /home/ubuntu/ict-trading-bot 2>/dev/null || cd ~
echo "=== dataset_audit.jsonl — the file the mirror does NOT carry ==="
wc -l runtime_logs/trainer/dataset_audit.jsonl
echo
echo "=== newest 3 rows, one summary line each (aggregate ON the box) ==="
python3 - <<'PY'
import json, collections
rows = []
with open("runtime_logs/trainer/dataset_audit.jsonl") as fh:
    for line in fh:
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
print("parsed rows:", len(rows))
for r in rows[-3:]:
    feats = r.get("features") or []
    flagged = [f["name"] for f in feats if f.get("flagged")]
    print(f"  manifest={r.get('manifest')} ok={r.get('ok')} n_rows={r.get('n_rows')} "
          f"features={len(feats)} flagged={flagged}")
print()
c = collections.Counter(r.get("manifest") for r in rows)
print("distinct manifests:", len(c), "| top 5:", c.most_common(5))
bad = [r for r in rows if r.get("ok") is False]
print("rows with ok=false:", len(bad))
PY
