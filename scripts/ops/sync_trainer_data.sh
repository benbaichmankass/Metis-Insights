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
#
# BL-20260807-TRAINER-JOURNAL-PULL-TORN-RSYNC. This used to be a bare
# `rsync` of the LIVE, WAL-mode trade_journal.db straight over the mirror,
# while the trader was actively writing to it. rsync is not atomic and SQLite
# does not tolerate a torn page image, so the copy was a race: on 2026-08-07
# 05:00 a 28s transfer over 750MB lost it and the mirror landed malformed
# (`PRAGMA quick_check` fails; COUNT(*) still worked on every table while
# `MAX(indexed_col)` did not — a corrupt index, localized, which is why it went
# unnoticed). The mechanism was unchanged since the script was written, so every
# earlier pull was luck rather than correctness, and the success line reported a
# SIZE match — which is not integrity ("Green is not evidence", obligation 1).
#
# Three changes, in order of importance:
#   1. PROMOTE-ON-VERIFY. The download lands on a TEMP path and is only moved
#      over the live mirror after `PRAGMA quick_check` passes. A bad pull can no
#      longer destroy the last good mirror — previously it overwrote it.
#   2. CONSISTENT SNAPSHOT. Prefer `VACUUM INTO` on the live VM, which takes a
#      read transaction and writes a coherent copy, so there is nothing to tear.
#      Falls back to the direct rsync when the remote has no usable sqlite3.
#   3. BOUNDED RETRY + LOUD FAILURE. A verify miss retries; exhausting the
#      retries is a hard failure with a distinct status, never a silent proceed.
#
# The live DB is only ever READ here. The snapshot is written to the live VM's
# /tmp and removed in the same ssh invocation.
JOURNAL_DEST="${DATA_DIR}/trade_journal.db"
JOURNAL_TMP="${DATA_DIR}/.trade_journal.db.incoming"
JOURNAL_PULL_ATTEMPTS="${JOURNAL_PULL_ATTEMPTS:-3}"

# Opening a WAL-mode DB creates `-wal`/`-shm` beside it, so VERIFYING the temp
# leaves `.trade_journal.db.incoming-wal`/`-shm` behind after the body is moved
# away. Observed on the trainer 2026-08-07, first run after the fix landed.
#
# Not cosmetic: a stale `.incoming-shm` sitting next to a FUTURE `.incoming`
# body is the mismatched-sidecar hazard this whole function exists to prevent,
# just moved to the temp path. Always clear all three together.
journal_tmp_clear() { rm -f "$JOURNAL_TMP" "${JOURNAL_TMP}-wal" "${JOURNAL_TMP}-shm"; }

# Verify a candidate SQLite file. Fails on a malformed image OR on a
# structurally-plausible file that cannot answer a real query — asserting the
# input rather than the transfer's exit code.
journal_verify() {
  python3 - "$1" <<'PYVERIFY'
import sqlite3, sys
path = sys.argv[1]
try:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    res = con.execute("PRAGMA quick_check(20)").fetchall()
    ok = bool(res) and str(res[0][0]).lower() == "ok"
    if not ok:
        print("quick_check: %s" % (res[:3],), file=sys.stderr)
        sys.exit(1)
    # quick_check can pass while an index the consumers actually use is bad, so
    # exercise one: MAX() over an indexed column is the exact query that
    # surfaced the 2026-08-07 corruption after COUNT(*) had reported fine.
    con.execute("SELECT COUNT(*) FROM trades").fetchone()
    con.execute("SELECT MAX(logged_at_utc) FROM signals").fetchone()
except Exception as exc:
    print("%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
    sys.exit(1)
PYVERIFY
}

rc=1
journal_method="none"
for attempt in $(seq 1 "$JOURNAL_PULL_ATTEMPTS"); do
  emit "$(printf '{"ts":"%s","status":"pulling","artifact":"trade_journal.db","src":"%s@%s:%s","attempt":%d}' \
    "$(iso_now)" "$VM_SSH_USER" "$LIVE_VM_IP" "$LIVE_VM_DB_PATH" "$attempt")"
  journal_tmp_clear
  remote_snap="/tmp/tj_snap_$$_${attempt}.db"
  set +e
  # 1. consistent snapshot on the live VM (read-only wrt the money DB).
  #
  # Uses PYTHON's sqlite3 online-backup API, not the `sqlite3` CLI: measured
  # 2026-08-07, the live VM has NO sqlite3 command-line tool, so the original
  # CLI-based `VACUUM INTO` probe failed every time and the pull silently
  # degraded to the direct-rsync fallback. The protection was shipped inert —
  # the pull log said `method:direct_rsync_fallback` and nothing read it. python3
  # is guaranteed present (the trader runs on it).
  #
  # `Connection.backup()` is the purpose-built online-backup API: it is designed
  # to copy a database that a concurrent writer is using, and `pages=-1` copies
  # in ONE step holding a read lock, so there is no restart loop under a busy
  # writer. The source is opened `mode=ro` — the money DB is never opened
  # writable by this script.
  ssh ${SSH_OPTS} "${VM_SSH_USER}@${LIVE_VM_IP}" \
    "rm -f '${remote_snap}' && python3 -c \"import sqlite3; src=sqlite3.connect('file:${LIVE_VM_DB_PATH}?mode=ro',uri=True); dst=sqlite3.connect('${remote_snap}'); src.backup(dst, pages=-1); dst.close(); src.close()\"" \
    2>/dev/null
  snap_rc=$?
  if [ "$snap_rc" -eq 0 ]; then
    journal_method="python_backup_snapshot"
    rsync -az --checksum -e "ssh ${SSH_OPTS}" \
      "${VM_SSH_USER}@${LIVE_VM_IP}:${remote_snap}" "$JOURNAL_TMP"
    rc=$?
  else
    # Snapshot unavailable — degrade to the direct copy, but keep
    # promote-on-verify so a torn read still cannot land.
    journal_method="direct_rsync_fallback"
    rsync -az --checksum -e "ssh ${SSH_OPTS}" \
      "${VM_SSH_USER}@${LIVE_VM_IP}:${LIVE_VM_DB_PATH}" "$JOURNAL_TMP"
    rc=$?
  fi
  # Always attempt remote cleanup, including on a FAILED snapshot — a partial
  # snapshot file left in the live VM's /tmp is the failure mode that would
  # accumulate on the money box across retries.
  ssh ${SSH_OPTS} "${VM_SSH_USER}@${LIVE_VM_IP}" "rm -f '${remote_snap}'" 2>/dev/null
  set -e
  if [ "$rc" -ne 0 ] || [ ! -f "$JOURNAL_TMP" ]; then
    emit "$(printf '{"ts":"%s","status":"retrying","artifact":"trade_journal.db","reason":"transfer_failed","method":"%s","exit_code":%d,"attempt":%d}' \
      "$(iso_now)" "$journal_method" "$rc" "$attempt")"
    continue
  fi
  set +e
  verify_err="$(journal_verify "$JOURNAL_TMP" 2>&1 >/dev/null)"
  verify_rc=$?
  set -e
  if [ "$verify_rc" -eq 0 ]; then
    # Promote. The snapshot is self-contained, so any sidecars belonging to the
    # PREVIOUS mirror must go with it or SQLite will read them against a body
    # they do not describe.
    rm -f "${JOURNAL_DEST}-wal" "${JOURNAL_DEST}-shm"
    mv -f "$JOURNAL_TMP" "$JOURNAL_DEST"
    # the body moved; its OWN sidecars must not outlive it
    rm -f "${JOURNAL_TMP}-wal" "${JOURNAL_TMP}-shm"
    break
  fi
  rc=1
  emit "$(python3 -c "
import json, sys
print(json.dumps({'ts': sys.argv[1], 'status': 'retrying', 'artifact': 'trade_journal.db',
  'reason': 'integrity_check_failed', 'method': sys.argv[2],
  'attempt': int(sys.argv[3]), 'detail': sys.argv[4][:300]}))" \
    "$(iso_now)" "$journal_method" "$attempt" "$verify_err")"
  journal_tmp_clear
done

if [ "$rc" -eq 0 ] && [ -f "$JOURNAL_DEST" ]; then
  db_size="$(stat -c%s "$JOURNAL_DEST" 2>/dev/null || echo 0)"
  emit "$(python3 -c "
import json, sys
print(json.dumps({'ts': sys.argv[1], 'status': 'ok', 'artifact': 'trade_journal.db',
  'size_bytes': int(sys.argv[2]), 'method': sys.argv[3], 'integrity_verified': True}))" \
    "$(iso_now)" "$db_size" "$journal_method")"
else
  # Hard failure. The PREVIOUS mirror (if any) is untouched and still usable —
  # stale beats malformed — but the cycle must not pretend the pull succeeded.
  emit "$(printf '{"ts":"%s","status":"failed","artifact":"trade_journal.db","reason":"no_verified_copy_after_%d_attempts","method":"%s","mirror_left_unmodified":true}' \
    "$(iso_now)" "$JOURNAL_PULL_ATTEMPTS" "$journal_method")"
  journal_tmp_clear
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

# --- Rotated shadow-prediction ARCHIVES (MB-20260712-SHADOW-LOG-HISTORY) --
# The four pulls above name LITERAL paths, so they fetch only the CURRENT
# shadow_predictions.jsonl. But ict-shadow-log-rotate.timer rotates that file
# on the live VM at 100 MiB / 7 days into
# `shadow_predictions.<YYYY-MM-DD>.jsonl[.gz]` (scripts/ops/rotate_shadow_log.py
# ::_next_rotated_path, plus a `.N` suffix when rotation runs twice in one UTC
# day) — and NOTHING pulled those. So every trainer-side research join over
# shadow history silently began at the last rotation: a window that LOOKS like
# the full record and is bounded by a log-hygiene timer nobody was thinking
# about while analysing. That is the unasserted-denominator shape — the join
# returns rows, so the truncation never announces itself.
#
# Non-fatal by design: no archives yet (a fresh VM, or under the rotation
# thresholds) is the expected early state, and `rsync` exits 23 when a glob
# matches nothing. We log `status:"none"` for that case rather than "ok" with
# a zero count, so "no archive exists" stays distinguishable from "we pulled
# and got nothing" — the same rule the artifact pulls above follow.
emit "$(printf '{"ts":"%s","status":"pulling","artifact":"shadow_predictions_archives","src":"%s@%s:%s"}' \
  "$(iso_now)" "$VM_SSH_USER" "$LIVE_VM_IP" "$(dirname "${LIVE_VM_SHADOW_PRED_PATH}")/shadow_predictions.*")"
set +e
rsync -az --checksum -e "ssh ${SSH_OPTS}" \
  --include='shadow_predictions.????-??-??.jsonl' \
  --include='shadow_predictions.????-??-??.jsonl.gz' \
  --include='shadow_predictions.????-??-??.*.jsonl' \
  --include='shadow_predictions.????-??-??.*.jsonl.gz' \
  --exclude='*' \
  "${VM_SSH_USER}@${LIVE_VM_IP}:$(dirname "${LIVE_VM_SHADOW_PRED_PATH}")/" \
  "${RUNTIME_LOGS_DIR}/"
rc=$?
set -e
archive_count="$(find "${RUNTIME_LOGS_DIR}" -maxdepth 1 -name 'shadow_predictions.????-??-??*' 2>/dev/null | wc -l | tr -d ' ')"
if [ "$rc" -eq 0 ]; then
  if [ "${archive_count:-0}" -gt 0 ]; then
    emit "$(printf '{"ts":"%s","status":"ok","artifact":"shadow_predictions_archives","archives":%d}' \
      "$(iso_now)" "${archive_count:-0}")"
  else
    emit "$(printf '{"ts":"%s","status":"none","artifact":"shadow_predictions_archives","detail":"no rotated archives on the live VM yet"}' \
      "$(iso_now)")"
  fi
else
  emit "$(printf '{"ts":"%s","status":"skipped","artifact":"shadow_predictions_archives","detail":"rsync failed (23 = glob matched nothing)","exit_code":%d,"archives_local":%d}' \
    "$(iso_now)" "$rc" "${archive_count:-0}")"
fi

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
