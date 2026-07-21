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
#   shadow_predictions.jsonl (+ _backfill) — the live shadow-prediction log
#                          (real-time + retroactive backfill). Needed so
#                          `python -m ml gate-check` / `model-attribution` can
#                          compute the live_agreement (AUC of scored win/loss)
#                          and drift gates LOCALLY on the trainer — those
#                          gates report `insufficient` today because the log
#                          isn't on the trainer and /api/bot/trades/scores is
#                          unreachable from a web session. Lands under
#                          runtime_logs/ so the gate-check CLI defaults
#                          (--shadow-log runtime_logs/shadow_predictions.jsonl)
#                          find it. Non-fatal if absent. (S-MLOPT-S8 follow-up,
#                          MB-20260527-004 / MB-20260529-001 unblock.)
#
# Every invocation appends a JSONL row to PULL_LOG_PATH so downstream
# scripts can trace when data was last synced.
#
# Environment knobs:
#   REPO_ROOT              — defaults to /home/ubuntu/ict-trading-bot
#   LIVE_VM_IP             — defaults to 141.145.193.91 (Ampere live trader; was 158.178.210.252 pre-2026-06-14)
#   LIVE_VM_DB_PATH        — defaults to /data/bot-data/trade_journal.db
#                            (the canonical post-2026-05-12 data-dir mount;
#                            see deploy/dropins/data-dir.conf and PR #1311.
#                            The legacy /home/ubuntu/ict-trading-bot/trade_journal.db
#                            on the live VM is a stale standalone file frozen
#                            around 2026-05-14 — pulling from it gives the
#                            trainer a 2-day-old snapshot of label feedstock.)
#   LIVE_VM_AUDIT_PATH     — defaults to /data/bot-data/runtime_logs/signal_audit.jsonl
#                            (canonical post-2026-05-12 path; the live VM's
#                            DATA_DIR drop-in moved runtime_logs/ under
#                            /data/bot-data/. Pre-2026-05-19 default
#                            pointed at the legacy /home/ubuntu/... path,
#                            which is stale because the trader stopped
#                            writing there on 2026-05-12 — symptom: the
#                            setup_labels_audit dataset froze for 8 days
#                            until this default was updated.)
#   VM_SSH_KEY             — defaults to ~/.ssh/ict-bot-ovm-private.key
#   VM_SSH_USER            — defaults to ubuntu
#   DATA_DIR               — defaults to $REPO_ROOT/data
#   PULL_LOG_PATH          — defaults to $REPO_ROOT/runtime_logs/trainer/db_pulls.jsonl
#   LIVE_VM_SHADOW_PRED_PATH        — live shadow log; defaults to
#                            /data/bot-data/runtime_logs/shadow_predictions.jsonl
#   LIVE_VM_SHADOW_PRED_BACKFILL_PATH — live backfill log; defaults to
#                            /data/bot-data/runtime_logs/shadow_predictions_backfill.jsonl
#   RUNTIME_LOGS_DIR       — where the shadow logs land on the trainer;
#                            defaults to $REPO_ROOT/runtime_logs (so the
#                            gate-check CLI's relative defaults resolve to it)
#
# Exit codes:
#   0   trade_journal.db synced (signal_audit.jsonl absence is non-fatal)
#   1   trade_journal.db rsync failed
#   2   environment misconfigured (missing SSH key)
set -uo pipefail

REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"
LIVE_VM_IP="${LIVE_VM_IP:-141.145.193.91}"
LIVE_VM_DB_PATH="${LIVE_VM_DB_PATH:-/data/bot-data/trade_journal.db}"
LIVE_VM_AUDIT_PATH="${LIVE_VM_AUDIT_PATH:-/data/bot-data/runtime_logs/signal_audit.jsonl}"
VM_SSH_USER="${VM_SSH_USER:-ubuntu}"
VM_SSH_KEY="${VM_SSH_KEY:-$HOME/.ssh/ict-bot-ovm-private.key}"
DATA_DIR="${DATA_DIR:-$REPO_ROOT/data}"
PULL_LOG_PATH="${PULL_LOG_PATH:-$REPO_ROOT/runtime_logs/trainer/db_pulls.jsonl}"
LIVE_VM_SHADOW_PRED_PATH="${LIVE_VM_SHADOW_PRED_PATH:-/data/bot-data/runtime_logs/shadow_predictions.jsonl}"
LIVE_VM_SHADOW_PRED_BACKFILL_PATH="${LIVE_VM_SHADOW_PRED_BACKFILL_PATH:-/data/bot-data/runtime_logs/shadow_predictions_backfill.jsonl}"
RUNTIME_LOGS_DIR="${RUNTIME_LOGS_DIR:-$REPO_ROOT/runtime_logs}"

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

# --- shadow_predictions.jsonl (+ backfill) (optional) ---------------------
# The live shadow-prediction log + its retroactive backfill. Pulled so the
# trainer can compute the live_agreement + drift promotion gates locally
# (`python -m ml gate-check`) instead of reporting them `insufficient` because
# the log lives only on the live VM. Lands under RUNTIME_LOGS_DIR so the
# gate-check CLI's relative defaults (--shadow-log runtime_logs/...) find it.
# Both are non-fatal: real-time absence means no shadow predictions yet; the
# backfill log only exists after a `python -m ml backfill-shadow-predictions`.
# The two exit shadow-soak logs (M20): fc_geometry_soak.jsonl is what
# scripts/ml/fc_geometry_resolve.py's contract expects mirrored here (its
# docstring predated this block — the mirror was missing until 2026-07-12),
# and exit_ladder_soak.jsonl feeds the same exit-refinement analyses.
mkdir -p "$RUNTIME_LOGS_DIR"
for pair in \
  "shadow_predictions.jsonl|${LIVE_VM_SHADOW_PRED_PATH}" \
  "shadow_predictions_backfill.jsonl|${LIVE_VM_SHADOW_PRED_BACKFILL_PATH}" \
  "exit_ladder_soak.jsonl|/data/bot-data/runtime_logs/exit_ladder_soak.jsonl" \
  "fc_geometry_soak.jsonl|/data/bot-data/runtime_logs/fc_geometry_soak.jsonl"; do
  artifact="${pair%%|*}"
  src_path="${pair##*|}"
  emit "$(printf '{"ts":"%s","status":"pulling","artifact":"%s","src":"%s@%s:%s"}' \
    "$(iso_now)" "$artifact" "$VM_SSH_USER" "$LIVE_VM_IP" "$src_path")"
  set +e
  rsync -az --checksum -e "ssh ${SSH_OPTS}" \
    "${VM_SSH_USER}@${LIVE_VM_IP}:${src_path}" \
    "${RUNTIME_LOGS_DIR}/${artifact}"
  rc=$?
  set -e
  if [ "$rc" -eq 0 ] && [ -f "${RUNTIME_LOGS_DIR}/${artifact}" ]; then
    lines="$(wc -l < "${RUNTIME_LOGS_DIR}/${artifact}" 2>/dev/null | tr -d ' ')"
    emit "$(python3 -c "
import json, sys
print(json.dumps({'ts': sys.argv[1], 'status': 'ok', 'artifact': sys.argv[2],
  'lines': int(sys.argv[3])}))" \
      "$(iso_now)" "$artifact" "${lines:-0}")"
  else
    # Non-fatal: absent until shadow predictions (or a backfill run) exist.
    emit "$(printf '{"ts":"%s","status":"skipped","artifact":"%s","detail":"not found on live VM","exit_code":%d}' \
      "$(iso_now)" "$artifact" "$rc")"
  fi
done

# --- IBKR MES market_raw shards (optional, deep history) ------------------
# When the operator has run scripts/ops/pull_mes_ibkr_history.sh on the LIVE
# VM, native MES intraday history lands under LIVE_VM_IBKR_PATH. Pull the whole
# tree so build_mes_market can prefer it over the rolling ~60d ES=F yfinance
# window. Absence is expected (and non-fatal) until that pull has been run —
# the MES regime models fall back to yfinance. See MB-20260528-002.
# 2026-07-21 (M27 Batch-2): the pull side went symbol-parameterized on
# 2026-07-07 (`pull-ibkr-history` — MGC/MHG land beside MES) but this sync
# stayed MES-only, so non-MES shards never reached the trainer. Sync the
# WHOLE market_raw tree; the artifact name is kept for log continuity.
LIVE_VM_IBKR_PATH="${LIVE_VM_IBKR_PATH:-/data/bot-data/ibkr_datasets/market_raw/}"
emit "$(printf '{"ts":"%s","status":"pulling","artifact":"ibkr_market_raw","src":"%s@%s:%s"}' \
  "$(iso_now)" "$VM_SSH_USER" "$LIVE_VM_IP" "$LIVE_VM_IBKR_PATH")"
mkdir -p "${DATA_DIR}/ibkr_datasets/market_raw"
set +e
rsync -az --checksum -e "ssh ${SSH_OPTS}" \
  "${VM_SSH_USER}@${LIVE_VM_IP}:${LIVE_VM_IBKR_PATH}" \
  "${DATA_DIR}/ibkr_datasets/market_raw/"
rc=$?
set -e
if [ "$rc" -eq 0 ]; then
  emit "$(printf '{"ts":"%s","status":"ok","artifact":"ibkr_market_raw"}' "$(iso_now)")"
else
  # Non-fatal: no live-VM IBKR pull has been run yet (yfinance fallback).
  emit "$(printf '{"ts":"%s","status":"skipped","artifact":"ibkr_market_raw","detail":"not present on live VM (run pull-ibkr-history) — yfinance fallback","exit_code":%d}' \
    "$(iso_now)" "$rc")"
fi

emit "$(printf '{"ts":"%s","status":"sync_done","overall_rc":%d,"data_dir":"%s"}' \
  "$(iso_now)" "$overall_rc" "$DATA_DIR")"
exit "$overall_rc"
