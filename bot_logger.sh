#!/usr/bin/env bash
while true; do
  echo "=== $(date) ==="
  tmux capture-pane -pt trader 2>/dev/null | tail -n 15 || echo "No tmux trader output"
  echo "======================"
  sleep 30
done
