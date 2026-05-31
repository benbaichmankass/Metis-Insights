import json, sys
from collections import defaultdict
path = sys.argv[1] if len(sys.argv) > 1 else '/tmp/research/results.jsonl'
rows = [json.loads(l) for l in open(path) if l.strip()]
ok = [r for r in rows if r.get('ok')]
print('rows', len(rows), 'ok', len(ok), 'failed', len(rows) - len(ok))
cfg = defaultdict(dict)
for r in ok:
    cfg[(r['family'], r['market'], r['tf'], r['params'])][r['window']] = r
rank = []
for k, w in cfg.items():
    if all(x in w for x in ('full', 'IS', 'OOS')):
        rank.append((w['OOS']['net_r'], w['IS']['net_r'], w['full']['net_r'], k,
                     w['full']['trades'], w['full'].get('net_long'), w['full'].get('maxdd'), w['full'].get('win')))
rank.sort(reverse=True)
print('=== TOP 20 by OOS net_r (walk-forward) ===')
print('%-36s %8s %8s %8s %6s %8s %7s %6s' % ('family/mkt/tf/params', 'OOS', 'IS', 'full', 'trades', 'long', 'maxdd', 'win'))
for oos, isr, fu, k, tr, lng, dd, win in rank[:20]:
    print('%-36s %8.1f %8.1f %8.1f %6d %8s %7s %6s' % ('/'.join(map(str, k)), oos, isr, fu, tr, lng, dd, win))
pos = [x for x in rank if x[0] > 0 and x[1] > 0]
print('=== %d of %d configs net-positive in BOTH IS and OOS ===' % (len(pos), len(rank)))
for oos, isr, fu, k, tr, lng, dd, win in pos:
    print('  BOTH+ %-32s OOS=%.1f IS=%.1f full=%.1f tr=%d dd=%s' % ('/'.join(map(str, k)), oos, isr, fu, tr, dd))
