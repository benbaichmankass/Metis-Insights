#!/usr/bin/env bash
set -uo pipefail
cd /home/ubuntu/ict-trading-bot || exit 1
echo "===== A. did the publish service run since the artifacts were written (04:14Z)? ====="
systemctl status ict-trainer-publish.service --no-pager 2>&1 | head -12
echo
echo "--- journal, last 60 lines ---"
journalctl -u ict-trainer-publish.service -n 60 --no-pager 2>&1 | tail -45
echo
echo "===== B. mirror still holds the 4 artifacts? ====="
ls -la runtime_logs/trainer_mirror/exit_head/
echo
echo "===== C. publisher status JSON (what it reports about its own last run) ====="
for F in runtime_logs/trainer_mirror/trainer_status.json runtime_logs/trainer/publish_status.json; do
  [ -f "$F" ] && { echo "--- $F ---"; python3 -m json.tool "$F" 2>/dev/null | head -30; }
done
