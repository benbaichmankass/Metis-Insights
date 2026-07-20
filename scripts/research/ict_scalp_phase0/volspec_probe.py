"""Probe the trainer registry for regime-head vol specs (Phase-0 helper).

Run on the trainer VM (via the trainer-vm-diag relay) from the repo root;
walks registry entry JSONs matching *regime*5m*, resolves each entry's
model_state_path, and prints the frozen vol-bucket fields.
"""
import glob
import json
import os
import pickle

cands = sorted(set(
    glob.glob('ml/registry-store/*regime*5m*')
    + glob.glob('ml/registry-store/**/*regime*5m*', recursive=True)
))
print('entries:', cands)
for c in cands[:4]:
    try:
        d = json.load(open(c))
    except Exception as e:
        print('ERR', c, repr(e))
        continue
    paths = set()
    if d.get('model_state_path'):
        paths.add(d['model_state_path'])
    for r in (d.get('records') or []):
        if isinstance(r, dict) and r.get('model_state_path'):
            paths.add(r['model_state_path'])
    print(c, '-> states:', sorted(paths)[-3:])
    for p in sorted(paths)[-2:]:
        print('STATE', p, os.path.exists(p))
        if not os.path.exists(p):
            continue
        s = None
        try:
            s = json.load(open(p))
        except Exception:
            try:
                s = pickle.load(open(p, 'rb'))
            except Exception as e:
                print('  unreadable:', repr(e))
                continue
        if isinstance(s, dict):
            fields = ('symbol', 'timeframe', 'vol_bucket_edges',
                      'vol_bucket_labels', 'vol_window_n', 'vol_feature_column')
            keys = {k: s.get(k) for k in fields if k in s}
            print('KEYS', json.dumps(keys, default=str))
            for kk, vv in s.items():
                if isinstance(vv, dict) and 'vol_bucket_edges' in vv:
                    print('NESTED', kk,
                          json.dumps({k: vv.get(k) for k in fields}, default=str))
