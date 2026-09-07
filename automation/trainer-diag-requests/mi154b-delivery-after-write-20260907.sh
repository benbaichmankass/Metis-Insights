#!/usr/bin/env bash
set -uo pipefail
cd /home/ubuntu/ict-trading-bot || exit 1
echo "===== artifact mtimes (epoch) ====="
for F in runtime_logs/trainer_mirror/exit_head/exit-head-ict_scalp-*.json; do
  echo "  $(basename $F): $(stat -c %Y "$F") ($(stat -c %y "$F"))"
done
NEWEST=$(stat -c %Y runtime_logs/trainer_mirror/exit_head/exit-head-ict_scalp-5m-v1.json)
echo "newest scalp artifact epoch: $NEWEST"
echo
echo "===== publish runs that COMPLETED AFTER the artifacts were written ====="
journalctl -u ict-trainer-publish.service --no-pager -o short-iso 2>/dev/null \
  | grep '"status":"published"' | tail -10
echo
echo "--- decision: was there a published-run strictly after the artifact mtime? ---"
journalctl -u ict-trainer-publish.service --no-pager -o short-iso 2>/dev/null \
  | grep '"status":"published"' \
  | python3 -c "
import sys,re,datetime
newest=int('$NEWEST')
after=[]
for line in sys.stdin:
    m=re.search(r'\"ts\":\"([0-9T:+\-]+)\"', line)
    if not m: continue
    ts=datetime.datetime.fromisoformat(m.group(1)).timestamp()
    if ts > newest: after.append(m.group(1))
print('POPULATION: publish runs parsed from the journal tail')
print('runs completed AFTER the newest scalp artifact was written:', len(after))
for a in after: print('   ', a)
print('VERDICT:', 'DELIVERED (a full mirror rsync ran after the write)' if after else 'NOT YET CONFIRMED -- next timer tick will ship it')
"
