#!/usr/bin/env bash
# scripts/ops/pull_mes_ibkr_history.sh — paced IBKR historical pull for MES,
# run ON THE LIVE VM (where the IB Gateway lives), writing market_raw shards
# that the trainer syncs for the MES regime models.
#
# WHY THIS RUNS ON THE LIVE VM (not the trainer):
#   The IB Gateway lives on its OWN dedicated VM at 10.0.0.251:4002 (the
#   2026-06-10 gateway-isolation; previously a 127.0.0.1:4002 loopback on the
#   live micro). Only the live VM is on the private subnet that can reach it;
#   the trainer VM cannot, and IBKR permits exactly ONE logged-in session per
#   username — so we cannot stand up a second gateway on the trainer. Historical
#   pulls therefore SHARE the live gateway on a DISTINCT clientId (default 450,
#   vs the execution clients 497/496) and must be paced gently. This is the
#   ibkr_offvm adapter's designed deployment (ml/datasets/adapters/ibkr_offvm.py).
#
#   This replaces the rolling ~60d ES=F yfinance window the trainer uses as a
#   fallback (build_trainer_datasets.sh::build_mes_market) with deep, native
#   MES intraday history. See docs/claude/ml-review-backlog.json MB-20260528-002
#   and docs/runbooks/ib-integration.md § Historical backfill.
#
# SAFETY — this shares the live trading gateway:
#   * Distinct clientId (default 450) so it never collides with the trader's
#     execution socket (497) or the read probes (9000+).
#   * Paced: pause_s=20 (well above the adapter's 12s default) to stay well
#     under IBKR's ~60 historical requests / 10 min / contract pacing ceiling
#     that, if tripped, returns Error 162 and could disturb the live trader's
#     own reqHistoricalData (MES candle) calls. At ~80 chunked requests this is
#     ~3/min over ~27 min — the live trader's few req/tick always have headroom.
#   * Idle priority: the whole run re-execs under `nice -n 19 ionice -c3` so it
#     never competes with the live trader for CPU/IO. Secondary by construction.
#   * Live-first guard: aborts if the trader heartbeat is stale (>10 min) — it
#     will not add gateway load during a live-trading incident.
#   * Best run during the CME maintenance break / weekend when the live trader
#     is idle. It is a one-shot, not a timer, by design — re-run only when you
#     want to extend/refresh the window.
#   * NEVER run this on the trainer VM (it is not on the gateway's private
#     subnet — it would simply fail to reach 10.0.0.251:4002, or hit Error 162
#     "connected from a different IP" — the 2026-05-22 failure mode).
#
# Output (under DATA_DIR so the trainer's sync_trainer_data.sh picks it up):
#   $IBKR_OUT/market_raw/MES/{5m,15m}/$DATASET_VERSION/data.jsonl
#
# Detached, survivable launch (recommended):
#   nohup bash scripts/ops/pull_mes_ibkr_history.sh \
#     > runtime_logs/ibkr_mes_pull.out 2>&1 &
# Progress: tail $PULL_LOG_PATH (default runtime_logs/ibkr_mes_pull.jsonl).
#
# Environment knobs:
#   REPO_ROOT          /opt/ict-trading-bot (live VM symlink) or the working tree
#   VENV_DIR           $REPO_ROOT/.venv (used if present; the live VM has none
#                      and falls back to system python, where deps are installed)
#   DATA_DIR           /data/bot-data            (canonical live-VM data dir)
#   IBKR_OUT           $DATA_DIR/ibkr_datasets   (synced to the trainer)
#   DATASET_VERSION    v002
#   MES_SYMBOL         MES
#   IB_HOST            10.0.0.251                 (dedicated IB-Gateway VM)
#   IB_PORT            4002                       (gateway VM bind; gnzsnz socat 4002->4004)
#   IB_HIST_CLIENT_ID  450                        (distinct from 497/496)
#   IB_HIST_PAUSE_S    20
#   HEARTBEAT_FILE     /data/bot-data/runtime_logs/heartbeat.txt
#   HEARTBEAT_MAX_AGE_S 600  (abort if the live trader heartbeat is older)
#   MES_TIMEFRAMES     "5m 15m"
#   MES_HIST_START     (default: 365 days ago)
#   PULL_LOG_PATH      $DATA_DIR/runtime_logs/ibkr_mes_pull.jsonl (canonical;
#                      readable via diag log_file?name=ibkr_mes_pull)
#   LOCK_FILE          $DATA_DIR/runtime_logs/ibkr_mes_pull.lock (single-instance)
#
# Exit codes: 0 = at least one timeframe pulled with bars; 1 = all pulls
#             failed/empty; 2 = environment misconfigured / trader unhealthy.
#
# This script DETACHES itself on first invocation (nohup + setsid) so a caller
# over SSH / the system-actions workflow returns immediately and the ~20-30 min
# paced pull survives the caller exiting. Monitor progress via PULL_LOG_PATH
# (also exposed on the diag surface as log_file?name=ibkr_mes_pull).
set -euo pipefail

SCRIPT_NAME="pull_mes_ibkr_history.sh"
# Source the shared ops helpers (log/record_audit/REPO_DIR) — every
# system-action wrapper sources _lib.sh. We keep our own emit() below for the
# structured JSONL progress log this script publishes.
# shellcheck source=scripts/ops/_lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lib.sh"

REPO_ROOT="${REPO_ROOT:-/opt/ict-trading-bot}"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv}"
DATA_DIR="${DATA_DIR:-/data/bot-data}"
IBKR_OUT="${IBKR_OUT:-$DATA_DIR/ibkr_datasets}"
DATASET_VERSION="${DATASET_VERSION:-v002}"
MES_SYMBOL="${MES_SYMBOL:-MES}"
# 2026-06-14: default to the dedicated IB-Gateway VM (10.0.0.251:4002), NOT the
# old same-box loopback. The 2026-06-10 gateway-isolation moved the gateway off
# the live micro onto its own Ampere VM; the trader uses ib_host: 10.0.0.251
# (config/accounts.yaml), so this pull must too. The retired 127.0.0.1:4002
# loopback has no listener now → ConnectionRefused (the silent-break this fixes).
IB_HOST="${IB_HOST:-10.0.0.251}"
IB_PORT="${IB_PORT:-4002}"
IB_HIST_CLIENT_ID="${IB_HIST_CLIENT_ID:-450}"
IB_HIST_PAUSE_S="${IB_HIST_PAUSE_S:-20}"
HEARTBEAT_FILE="${HEARTBEAT_FILE:-/data/bot-data/runtime_logs/heartbeat.txt}"
HEARTBEAT_MAX_AGE_S="${HEARTBEAT_MAX_AGE_S:-600}"
MES_TIMEFRAMES="${MES_TIMEFRAMES:-5m 15m}"
MES_HIST_START="${MES_HIST_START:-$(date -u -d '365 days ago' +%Y-%m-%d 2>/dev/null || echo 2025-05-28)}"
MES_HIST_END="${MES_HIST_END:-$(date -u +%Y-%m-%d)}"
# How many dated contracts to page back through. The ibkr_offvm adapter pages
# contracts newest-first and defaults to 4 — which for a 1y window only covers
# the most-recent ~2 quarters (the older expiries holding the rest of the year
# fall past index 4). Bump to 8 so a full year's quarterly contracts are paged.
# Depth is ultimately capped by IBKR's per-contract intraday retention.
MES_MAX_CONTRACTS="${MES_MAX_CONTRACTS:-8}"
# PER_CONTRACT (roll-adjustment increment 2): when truthy, write the PER-CONTRACT
# stream (no cross-contract dedup, each bar tagged with its contract month) via
# `python -m ml.datasets.percontract_pull` to market_raw_percontract/<sym>/<tf>/<ver>/,
# instead of the deduped canonical market_raw shard. That stream is the input to
# scripts/research/build_continuous_contract.py (roll-adjusted continuous series).
PER_CONTRACT="${PER_CONTRACT:-}"
PULL_LOG_PATH="${PULL_LOG_PATH:-$DATA_DIR/runtime_logs/ibkr_mes_pull.jsonl}"
LOCK_FILE="${LOCK_FILE:-$DATA_DIR/runtime_logs/ibkr_mes_pull.lock}"

# 1) DETACH: on the first (foreground) invocation, relaunch fully detached and
#    return immediately so the SSH/system-actions caller doesn't block (and the
#    pull isn't SIGHUP-killed when that caller exits).
if [ -z "${_IBKR_DETACHED:-}" ]; then
  export _IBKR_DETACHED=1
  mkdir -p "$(dirname "$PULL_LOG_PATH")" 2>/dev/null || true
  setsid nohup bash "$0" "$@" >/dev/null 2>&1 < /dev/null &
  echo "pull_mes_ibkr_history: launched detached (pid $!). Monitor ${PULL_LOG_PATH} or diag log_file?name=ibkr_mes_pull"
  exit 0
fi

# 2) SECONDARY PRIORITY: run the detached body at idle CPU + IO priority so it
#    never competes with the live trader for the box. (IBKR API pacing is
#    handled separately by IB_HIST_PAUSE_S.)
if [ -z "${_IBKR_NICED:-}" ]; then
  export _IBKR_NICED=1
  exec nice -n 19 ionice -c3 bash "$0" "$@"
fi

iso_now() { date -u +'%Y-%m-%dT%H:%M:%S+00:00'; }
emit() {
  mkdir -p "$(dirname "$PULL_LOG_PATH")"
  printf '%s\n' "$1" >> "$PULL_LOG_PATH"
  printf '%s\n' "$1"
}

# 3) SINGLE-INSTANCE LOCK: the system-action fires on issues.opened AND
#    issues.labeled, so the wrapper can be invoked twice. Hold an exclusive
#    lock for the whole run; a second concurrent invocation exits cleanly
#    instead of opening a duplicate IB connection (which would collide on the
#    clientId anyway). Lock is released when this process exits (fd closes).
mkdir -p "$(dirname "$LOCK_FILE")" 2>/dev/null || true
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  emit "$(printf '{"ts":"%s","status":"skipped","detail":"another pull_mes_ibkr_history run holds the lock — not starting a duplicate"}' "$(iso_now)")"
  exit 0
fi

if [ ! -d "$REPO_ROOT/.git" ] && [ ! -e "$REPO_ROOT/ml" ]; then
  emit "$(printf '{"ts":"%s","status":"env_error","detail":"REPO_ROOT looks wrong: %s"}' "$(iso_now)" "$REPO_ROOT")"
  exit 2
fi
cd "$REPO_ROOT"
# The trainer VM runs from a .venv; the LIVE VM runs from system python (the
# trader is `python3 -m src.main`, deps installed system-wide by
# pull_and_deploy). Use the venv when present, else fall back to system python.
if [ -d "$VENV_DIR" ]; then
  # shellcheck source=/dev/null
  source "$VENV_DIR/bin/activate"
  PY=python
else
  PY="$(command -v python3 || command -v python || true)"
  if [ -z "$PY" ]; then
    emit "$(printf '{"ts":"%s","status":"env_error","detail":"no venv at %s and no system python found"}' "$(iso_now)" "$VENV_DIR")"
    exit 2
  fi
  emit "$(printf '{"ts":"%s","status":"info","detail":"no venv at %s — using system python %s"}' "$(iso_now)" "$VENV_DIR" "$PY")"
fi

# SECONDARY-PRIORITY guard: only touch the shared IB gateway when the live
# trader is healthy. If the heartbeat is stale, something is wrong with live
# trading — do NOT add gateway load during a trader incident. Live needs first.
if [ -f "$HEARTBEAT_FILE" ]; then
  hb_age=$(( $(date +%s) - $(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null || echo 0) ))
  if [ "$hb_age" -gt "$HEARTBEAT_MAX_AGE_S" ]; then
    emit "$(printf '{"ts":"%s","status":"abort","detail":"live trader heartbeat stale (%ss > %ss) — not adding IB gateway load during a trader incident"}' \
      "$(iso_now)" "$hb_age" "$HEARTBEAT_MAX_AGE_S")"
    exit 2
  fi
  emit "$(printf '{"ts":"%s","status":"preflight_ok","detail":"trader heartbeat fresh (%ss)"}' "$(iso_now)" "$hb_age")"
else
  emit "$(printf '{"ts":"%s","status":"preflight_warn","detail":"no heartbeat file at %s — proceeding (cannot confirm trader health)"}' "$(iso_now)" "$HEARTBEAT_FILE")"
fi

# The adapter's deliberate opt-in: it shares the live gateway, so it refuses
# to open a socket unless this is set. We set it here because a paced pull is
# exactly what this script is for.
export ICT_IB_HISTORICAL_OK=1

emit "$(printf '{"ts":"%s","status":"pull_start","symbol":"%s","timeframes":"%s","start":"%s","end":"%s","client_id":%s,"pause_s":%s,"max_contracts":%s}' \
  "$(iso_now)" "$MES_SYMBOL" "$MES_TIMEFRAMES" "$MES_HIST_START" "$MES_HIST_END" "$IB_HIST_CLIENT_ID" "$IB_HIST_PAUSE_S" "$MES_MAX_CONTRACTS")"

any_ok=0
family="market_raw"; [ -n "${PER_CONTRACT// }" ] && family="market_raw_percontract"
for tf in $MES_TIMEFRAMES; do
  out_path="${IBKR_OUT}/${family}/${MES_SYMBOL}/${tf}/${DATASET_VERSION}/data.jsonl"
  emit "$(printf '{"ts":"%s","status":"building","family":"%s","symbol":"%s","timeframe":"%s"}' "$(iso_now)" "$family" "$MES_SYMBOL" "$tf")"
  set +e
  if [ -n "${PER_CONTRACT// }" ]; then
    # Roll-adjustment increment 2: per-contract stream (tagged, no cross-contract
    # dedup) -> market_raw_percontract/. Same gateway + pacing + guard as below.
    "$PY" -m ml.datasets.percontract_pull \
      --symbol "${MES_SYMBOL}" --timeframe "${tf}" \
      --start "${MES_HIST_START}" --end "${MES_HIST_END}" \
      --out-dir "${IBKR_OUT}" --version "${DATASET_VERSION}" \
      --host "${IB_HOST}" --port "${IB_PORT}" --client-id "${IB_HIST_CLIENT_ID}" \
      --pause-s "${IB_HIST_PAUSE_S}" --max-contracts "${MES_MAX_CONTRACTS}" \
      >"/tmp/ibkr_mes_${tf}_$$.out" 2>"/tmp/ibkr_mes_${tf}_$$.err"
  else
    "$PY" -m ml build-dataset market_raw \
      --output-dir "$IBKR_OUT" --version "$DATASET_VERSION" \
      --source ibkr_offvm --symbol-scope "$MES_SYMBOL" --timeframe "$tf" --overwrite \
      "adapter=ibkr_offvm" "symbol=${MES_SYMBOL}" "timeframe=${tf}" \
      "start=${MES_HIST_START}" "end=${MES_HIST_END}" \
      "host=${IB_HOST}" "port=${IB_PORT}" "client_id=${IB_HIST_CLIENT_ID}" \
      "use_rth=false" "pause_s=${IB_HIST_PAUSE_S}" "max_contracts=${MES_MAX_CONTRACTS}" \
      >"/tmp/ibkr_mes_${tf}_$$.out" 2>"/tmp/ibkr_mes_${tf}_$$.err"
  fi
  rc=$?
  set -e
  rows=0
  if [ -f "$out_path" ]; then
    rows="$(wc -l < "$out_path" 2>/dev/null | tr -d ' ' || echo 0)"
  fi
  if [ "$rc" -eq 0 ] && [ "${rows:-0}" -gt 0 ]; then
    emit "$(printf '{"ts":"%s","status":"ok","family":"%s","symbol":"%s","timeframe":"%s","rows":%s}' "$(iso_now)" "$family" "$MES_SYMBOL" "$tf" "${rows:-0}")"
    any_ok=1
  else
    err="$(tail -n 3 "/tmp/ibkr_mes_${tf}_$$.err" 2>/dev/null | tr '\n' ' ' | head -c 400 || true)"
    emit "$(python3 -c "import json,sys; print(json.dumps({'ts':sys.argv[1],'status':'failed','family':sys.argv[6],'symbol':sys.argv[2],'timeframe':sys.argv[3],'exit_code':int(sys.argv[4]),'stderr_tail':sys.argv[5]}))" \
      "$(iso_now)" "$MES_SYMBOL" "$tf" "$rc" "$err" "$family")"
  fi
  rm -f "/tmp/ibkr_mes_${tf}_$$.out" "/tmp/ibkr_mes_${tf}_$$.err"
done

emit "$(printf '{"ts":"%s","status":"pull_end","any_ok":%d,"out":"%s"}' "$(iso_now)" "$any_ok" "$IBKR_OUT")"
[ "$any_ok" -eq 1 ] && exit 0 || exit 1
