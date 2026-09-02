#!/usr/bin/env bash
# First end-to-end exercise of the trainer-diag-relay (MI-31, merged 7b95451c).
# Reads dataset_audit.jsonl — the file two backlog drains were blocked on and
# which no /api/bot/ml/* route can serve, because publish_trainer_mirror.sh
# pushes dataset_builds.jsonl and db_pulls.jsonl BY NAME and omits it.
set -u
echo "=== whoami / host ==="
whoami; hostname
echo
echo "=== does the file exist? ==="
AUDIT=/home/ubuntu/ict-trading-bot/runtime_logs/trainer/dataset_audit.jsonl
ls -la "$AUDIT" 2>&1 || true
if [ ! -f "$AUDIT" ]; then
  echo "NOT AT THAT PATH — searching:"
  find /home/ubuntu -maxdepth 6 -name 'dataset_audit.jsonl' 2>/dev/null | head -5
fi
echo
echo "=== line count + date span (the denominator) ==="
if [ -f "$AUDIT" ]; then
  wc -l "$AUDIT"
  echo "--- oldest:"; head -1 "$AUDIT" | cut -c1-400
  echo "--- newest:"; tail -1 "$AUDIT" | cut -c1-400
else
  echo "absent — reporting that rather than inferring"
fi
echo
echo "=== negative control: a path that should NOT exist ==="
ls -la /home/ubuntu/definitely-not-a-real-path-xyz 2>&1 | head -2
