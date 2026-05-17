#!/usr/bin/env bash
# scripts/ops/sync_trainer_data.sh — pull label feedstock from the live VM.
#
# Pulls read-only data artifacts from the live trader VM to the trainer VM
# for use by the training pipeline.  Permitted and logged per
# docs/claude/trainer-vm-mode.md § 3.b (cross-VM read with audit trail).
#
# Artifacts pulled:
#   trade_journal.db     — primary label feedstock for all journal-backed
#                          dataset families (trade_outcomes, setup_labels,
#                          execution_quality, account_context,
#                          backtest_results, setup_labels_audit).
#   signal_audit.jsonl   — pipeline audit log for setup_labels_audit.
#                          Non-fatal if absent (means no signals yet;
#                          setup_labels_audit will produce an empty dataset).
#
# Every invocation appends a JSONL row to PULL_LOG_PATH so downstream
# scripts can trace when data was last synced.
#
# Environment knobs:
#   REPO_ROOT              — defaults to /home/ubuntu/ict-trading-bot
#   LIVE_VM_IP             — defaults to 158.178.210.252
#   LIVE_VM_DB_PATH        — defaults to /data/bot-data/trade_journal.db
#                            (the canonical post-2026-05-12 data-dir mount;
#                            see deploy/dropins/data-dir.conf and PR #1311.
#                            The legacy /home/ubuntu/ict-trading-bot/trade_journal.db
#                            on the live VM is a stale standalone file frozen
#                            around 2026-05-14 — pulling from it gives the
#                            trainer a 2-day-old snapshot of label feedstock.)
#   LIVE_VM_AUDIT_PATH     — defaults to /home/ubuntu/ict-trading-bot/runtime_logs/signal_audit.jsonl
#   VM_SSH_KEY             — defaults to ~/.ssh/ict-bot-ovm-private.key
#   VM_SSH_USER            — defaults to ubuntu
#   DATA_DIR               — defaults to $REPO_ROOT/data
#   PULL_LOG_PATH          — defaults to $REPO_ROOT/runtime_logs/trainer/db_pulls.jsonl
#
# Exit codes:
#   0   trade_journal.db synced (signal_audit.jsonl absence is non-fatal)
#   1   trade_journal.db rsync failed
#   2   environment misconfigured (missing SSH key)
set -uo pipefail

REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"
LIVE_VM_IP="${LIVE_VM_IP:-158.178.210.252}"
LIVE_VM_DB_PATH="${LIVE_VM_DB_PATH:-/data/bot-data/trade_journal.db}"
LIVE_VM_AUDIT_PATH="${LIVE_VM_AUDIT_PATH:-/home/ubuntu/ict-trading-bot/runtime_logs/signal_audit.jsonl}"
VM_SSH_USER="${VM_SSH_USER:-ubuntu}"
VM_SSH_KEY="${VM_SSH_KEY:-$HOME/.ssh/ict-bot-ovm-private.key}"
DATA_DIR="${DATA_DIR:-$REPO_ROOT/data}"
PULL_LOG_PATH="${PULL_LOG_PATH:-$REPO_ROOT/runtime_logs/trainer/db_pulls.jsonl}"

iso_now() { date -u +'%Y-%m-%dT%H:%M:%S+00:00'; }

emit() {
  local payload="$1"
  mkdir -p "$(dirname "$PULL_LOG_PATH")"
  printf '%s\n' "$payload" >> "$PULL_LOG_PATH"
  printf '%s\n' "$payload"
}

# --- Env checks -----------------------------------------------------------
if [ ! -f "$VM_SSH_KEY" ]; then
  emit "$(printf '{"ts":"%s","status":"env_error","detail":"VM_SSH_KEY not found: %s"}' \
    "$(iso_now)" "$VM_SSH_KEY")"
  exit 2
fi

mkdir -p "$DATA_DIR"

SSH_OPTS="-i ${VM_SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o BatchMode=yes"
overall_rc=0

# --- trade_journal.db (required) -----------------------------------------
emit "$(printf '{"ts":"%s","status":"pulling","artifact":"trade_journal.db","src":"%s@%s:%s"}' \
  "$(iso_now)" "$VM_SSH_USER" "$LIVE_VM_IP" "$LIVE_VM_DB_PATH")"
set +e
rsync -az --checksum -e "ssh ${SSH_OPTS}" \
  "${VM_SSH_USER}@${LIVE_VM_IP}:${LIVE_VM_DB_PATH}" \
  "${DATA_DIR}/trade_journal.db"
rc=$?
set -e
if [ "$rc" -eq 0 ] && [ -f "${DATA_DIR}/trade_journal.db" ]; then
  db_size="$(stat -c%s "${DATA_DIR}/trade_journal.db" 2>/dev/null || echo 0)"
  emit "$(python3 -c "
import json, sys
print(json.dumps({'ts': sys.argv[1], 'status': 'ok', 'artifact': 'trade_journal.db',
  'size_bytes': int(sys.argv[2])}))" \
    "$(iso_now)" "$db_size")"
else
  emit "$(printf '{"ts":"%s","status":"failed","artifact":"trade_journal.db","exit_code":%d}' \
    "$(iso_now)" "$rc")"
  overall_rc=1
fi

# --- signal_audit.jsonl (optional) ----------------------------------------
emit "$(printf '{"ts":"%s","status":"pulling","artifact":"signal_audit.jsonl","src":"%s@%s:%s"}' \
  "$(iso_now)" "$VM_SSH_USER" "$LIVE_VM_IP" "$LIVE_VM_AUDIT_PATH")"
set +e
rsync -az --checksum -e "ssh ${SSH_OPTS}" \
  "${VM_SSH_USER}@${LIVE_VM_IP}:${LIVE_VM_AUDIT_PATH}" \
  "${DATA_DIR}/signal_audit.jsonl"
rc=$?
set -e
if [ "$rc" -eq 0 ] && [ -f "${DATA_DIR}/signal_audit.jsonl" ]; then
  audit_lines="$(wc -l < "${DATA_DIR}/signal_audit.jsonl" 2>/dev/null | tr -d ' ')"
  emit "$(python3 -c "
import json, sys
print(json.dumps({'ts': sys.argv[1], 'status': 'ok', 'artifact': 'signal_audit.jsonl',
  'lines': int(sys.argv[2])}))" \
    "$(iso_now)" "${audit_lines:-0}")"
else
  # Non-fatal: no signals fired on the live VM yet is expected early on.
  emit "$(printf '{"ts":"%s","status":"skipped","artifact":"signal_audit.jsonl","detail":"not found on live VM (no signals fired yet)","exit_code":%d}' \
    "$(iso_now)" "$rc")"
fi

emit "$(printf '{"ts":"%s","status":"sync_done","overall_rc":%d,"data_dir":"%s"}' \
  "$(iso_now)" "$overall_rc" "$DATA_DIR")"
exit "$overall_rc"
