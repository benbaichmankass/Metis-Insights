#!/usr/bin/env bash
# Collects a read-only health snapshot of the ICT trading bot VM.
#
# Runs on the VM via SSH from the GitHub Actions health-check workflow
# (see .github/workflows/health-check.yml). Output goes to stdout; the
# workflow captures it into health_snapshot.txt.
#
# Touches no runtime state. Touches no strategy logic. Safe to invoke
# at any time, including during an active trading session.
#
# Section markers (=== NAME ===) are parsed by scripts/run_health_check.py
# when feeding the snapshot to Claude — keep them stable.
#
# Env overrides:
#   REPO_DIR             repo root on VM (default: parent of this script's dir)
#   DATA_DIR             runtime data root (auto-detected: /data/bot-data
#                        when mounted, else REPO_DIR — matches the trader's
#                        path-helper logic post-OCI migration)
#   LOOKBACK_MIN         minutes of log history to surface (default: 1440 = 24h)
#   SIGNAL_AUDIT_JSONL   absolute path to signal_audit.jsonl (override)
#   TRADE_JOURNAL_DB     absolute path to trade_journal.db (override)

set -u

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)}"
LOOKBACK_MIN="${LOOKBACK_MIN:-1440}"

# Auto-detect runtime data root. The trader's path helpers honour DATA_DIR
# (set by the systemd drop-in post-OCI migration) and otherwise fall back
# to repo-relative paths. Match that contract here — reading the wrong
# runtime_logs is exactly the regression S-OCI introduced for snapshots.
if [ -n "${DATA_DIR:-}" ]; then
  runtime_root="$DATA_DIR"
elif [ -d "/data/bot-data" ] && mountpoint -q /data 2>/dev/null; then
  runtime_root="/data/bot-data"
else
  runtime_root="$REPO_DIR"
fi

# Search roots — only scan dirs that exist, otherwise find errors out.
# Include both the runtime root (live data) and the repo root (legacy /
# pre-migration data) so a half-migrated VM still surfaces every file.
roots=()
for d in "$REPO_DIR/logs" "$runtime_root/runtime_logs" "$REPO_DIR/runtime_logs"; do
  if [ -d "$d" ]; then
    # Dedupe — runtime_root may equal REPO_DIR in the unmigrated case.
    skip=0
    for existing in "${roots[@]}"; do
      [ "$existing" = "$d" ] && { skip=1; break; }
    done
    [ $skip -eq 0 ] && roots+=("$d")
  fi
done

audit_jsonl="${SIGNAL_AUDIT_JSONL:-$runtime_root/runtime_logs/signal_audit.jsonl}"
journal_db="${TRADE_JOURNAL_DB:-$REPO_DIR/trade_journal.db}"

# Helpers ---------------------------------------------------------------

# tail_recent <name-glob> <n-lines> <fallback-msg>
# Tails the last N lines of every file matching the glob modified within
# LOOKBACK_MIN minutes. Quiet on missing roots.
tail_recent() {
  local glob="$1" n="$2" fallback="$3"
  if [ "${#roots[@]}" -eq 0 ]; then
    echo "$fallback"
    return
  fi
  local found=0
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    found=1
    echo "--- $f ---"
    tail -n "$n" "$f" 2>/dev/null || true
  done < <(find "${roots[@]}" -type f -name "$glob" -mmin "-${LOOKBACK_MIN}" 2>/dev/null)
  [ "$found" -eq 0 ] && echo "$fallback"
}

# grep_recent <ERE-pattern> <max-lines> <fallback-msg>
# Always case-insensitive — both call sites benefit and a positional
# flag arg is too easy to misuse.
grep_recent() {
  local pat="$1" max="$2" fallback="$3"
  if [ "${#roots[@]}" -eq 0 ]; then
    echo "$fallback"
    return
  fi
  local out
  out="$(grep -rEhIi --include='*.log' "$pat" "${roots[@]}" 2>/dev/null | tail -n "$max")"
  if [ -z "$out" ]; then
    echo "$fallback"
  else
    printf '%s\n' "$out"
  fi
}

# audit_recent <event-name-regex> <n-lines> <fallback-msg>
# Reads runtime_logs/signal_audit.jsonl (the NDJSON pipeline audit log) and
# emits the last N events whose `event` field matches the regex AND whose
# `logged_at_utc` is within LOOKBACK_MIN minutes. The pre-FU-004 behaviour
# was to grep *.log for ticks / signals / orders / trades, but the live
# pipeline writes NDJSON not *.log — so every section was under-reporting
# even while the bot was actively trading. Reads the last 20000 lines via
# tail to bound runtime on multi-MB jsonl files.
audit_recent() {
  local pat="$1" n="$2" fallback="$3"
  if [ ! -f "$audit_jsonl" ]; then
    echo "$fallback (audit jsonl missing at $audit_jsonl)"
    return
  fi
  tail -n 20000 "$audit_jsonl" 2>/dev/null \
    | python3 - "$pat" "$n" "$LOOKBACK_MIN" "$fallback" <<'PY'
import json, re, sys, time
from datetime import datetime
pat, n, lookback, fallback = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
cutoff = time.time() - lookback * 60
pat_re = re.compile(pat)
matched = []
for line in sys.stdin:
    line = line.rstrip("\n")
    if not line:
        continue
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        continue
    ts_str = ev.get("logged_at_utc") or ev.get("ts") or ""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.timestamp() < cutoff:
            continue
    except (ValueError, AttributeError):
        continue
    if pat_re.search(str(ev.get("event", ""))):
        matched.append(line)
if not matched:
    print(fallback)
    sys.exit(0)
print(f"({len(matched)} events matched /{pat}/ in last {lookback}m, showing last {min(n, len(matched))})")
for line in matched[-n:]:
    print(line[:320])
PY
}

# journal_recent <table> <n-rows> <fallback-msg>
# Reads trade_journal.db (SQLite) for the named table, filtered to rows
# created within LOOKBACK_MIN minutes. Used for ORDERS / TRADES — the
# audit jsonl doesn't track order_packages lifecycle or trade row state,
# so the DB is the source of truth. Pipe-delimited so multi-field rows
# read clearly in the snapshot.
journal_recent() {
  local table="$1" n="$2" fallback="$3"
  if [ ! -f "$journal_db" ]; then
    echo "$fallback (journal db missing at $journal_db)"
    return
  fi
  if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "$fallback (sqlite3 not available)"
    return
  fi
  local since_iso
  since_iso=$(python3 -c "import time, datetime as d; print(d.datetime.fromtimestamp(time.time() - $LOOKBACK_MIN*60, tz=d.timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z'))")
  case "$table" in
    order_packages)
      sqlite3 -header -separator ' | ' "$journal_db" \
        "SELECT order_package_id, strategy_name, symbol, direction, status, datetime(created_at) AS created, datetime(updated_at) AS updated FROM order_packages WHERE datetime(created_at) >= '$since_iso' ORDER BY datetime(created_at) DESC LIMIT $n;" \
        2>/dev/null \
        || echo "$fallback (query failed)"
      ;;
    trades)
      sqlite3 -header -separator ' | ' "$journal_db" \
        "SELECT id, datetime(timestamp) AS ts, symbol, direction, status, setup_type, account_id, COALESCE(printf('%.4f', pnl), 'open') AS pnl FROM trades WHERE datetime(timestamp) >= '$since_iso' ORDER BY datetime(timestamp) DESC LIMIT $n;" \
        2>/dev/null \
        || echo "$fallback (query failed)"
      ;;
    open_positions)
      sqlite3 -header -separator ' | ' "$journal_db" \
        "SELECT id, datetime(timestamp) AS opened, symbol, direction, account_id, setup_type, position_size FROM trades WHERE status='open' ORDER BY datetime(timestamp) DESC LIMIT $n;" \
        2>/dev/null \
        || echo "$fallback (query failed)"
      ;;
  esac
}

# ----------------------------------------------------------------------

echo "=== META ==="
echo "host: $(hostname 2>/dev/null || echo unknown)"
echo "now_utc: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "repo_dir: $REPO_DIR"
echo "runtime_root: $runtime_root"
echo "audit_jsonl: $audit_jsonl"
echo "journal_db: $journal_db"
echo "git_head: $(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "lookback_min: $LOOKBACK_MIN"

echo "=== PROCESSES ==="
ps -eo pid,etime,pcpu,pmem,cmd --sort=-pcpu 2>/dev/null \
  | grep -E "(python|ict|trader|strategy|telegram_bot|claude_bridge)" \
  | grep -v grep \
  | head -30 \
  || echo "no matching processes"

echo "=== HEARTBEAT ==="
# Heartbeat lives under runtime_logs, same migration story as the audit
# jsonl — prefer the runtime_root (live), fall back to the repo path.
hb="$runtime_root/runtime_logs/heartbeat.txt"
[ ! -f "$hb" ] && hb="$REPO_DIR/runtime_logs/heartbeat.txt"
if [ -f "$hb" ]; then
  echo "path: $hb"
  echo "mtime: $(stat -c %y "$hb" 2>/dev/null || stat -f %Sm "$hb" 2>/dev/null || echo unknown)"
  echo "age_seconds: $(( $(date +%s) - $(stat -c %Y "$hb" 2>/dev/null || stat -f %m "$hb" 2>/dev/null || echo 0) ))"
  echo "tail:"
  tail -n 5 "$hb" 2>/dev/null || true
else
  echo "no heartbeat.txt at $runtime_root/runtime_logs/ or $REPO_DIR/runtime_logs/"
fi
tail_recent 'heartbeat*.log' 20 "no heartbeat .log files in last ${LOOKBACK_MIN}m"

echo "=== TICKS ==="
# pipeline_result fires per tick whether or not a signal fires; treat as
# the canonical "the loop is running" trace. Legacy *.log glob kept as
# belt-and-braces in case a future writer drops a tick*.log.
audit_recent '^pipeline_result$' 20 "no tick events in last ${LOOKBACK_MIN}m"
tail_recent 'tick*.log' 50 ""

echo "=== SIGNALS ==="
# *_eval events (turtle_soup_eval, vwap_eval, …) cover every strategy
# evaluation, actionable or not. side=buy/sell on a non-eval event
# (Generated signal lines) would also be useful but the audit emitter
# names vary — the eval count is the reliable signal-of-life metric.
audit_recent '_eval$' 30 "no signal-eval events in last ${LOOKBACK_MIN}m"

echo "=== ORDERS ==="
# Source of truth is trade_journal.db::order_packages, not *.log.
journal_recent order_packages 20 "no order_packages rows in last ${LOOKBACK_MIN}m"

echo "=== TRADES ==="
# Source of truth is trade_journal.db::trades.
journal_recent trades 20 "no trades rows in last ${LOOKBACK_MIN}m"

echo "=== POSITIONS ==="
# Open positions = trades WHERE status='open'. No lookback filter — an
# old open position is exactly what the snapshot needs to surface.
journal_recent open_positions 30 "no open positions"

echo "=== MONITORING ==="
audit_recent 'monitor' 20 "no monitor events in last ${LOOKBACK_MIN}m"
tail_recent 'watchdog*' 30 ""

echo "=== API ==="
grep_recent '(api|HTTP|http)|\b429\b|\b5[0-9]{2}\b' 50 "no api/http lines"

echo "=== ERRORS ==="
grep_recent 'error|exception|failed|crash|traceback' 50 "no error lines"

echo "=== STORAGE ==="
# OCI block-storage health (S-OCI). Surfaces /data mount,
# /data/bot-data presence, fstab persistence, and DATA_DIR env on the
# trader services so the layer-2 review notices if the mount or
# drop-ins drift. Non-fatal: the trailing `|| true` keeps a missing or
# unmounted /data from aborting the snapshot.
storage_script="$REPO_DIR/scripts/verify_storage_setup.sh"
if [ -x "$storage_script" ] || [ -f "$storage_script" ]; then
  bash "$storage_script" 2>&1 || true
else
  echo "verify_storage_setup.sh missing at $storage_script"
  echo "-- df -h /data --"
  df -h /data 2>&1 || echo "(df failed)"
  echo "-- mountpoint /data --"
  mountpoint /data 2>&1 || true
  echo "-- ls /data/bot-data --"
  ls -ld /data/bot-data 2>&1 || true
fi

echo "=== DB ==="
# trade_journal.db health: existence, size, mtime, integrity, row counts.
# A stale or corrupt DB is the silent killer of every downstream metric
# (ORDERS / TRADES / POSITIONS sections read tails from here, but a tail
# of an empty / locked / corrupt DB shows the same "no rows" signature
# as a quiet trading window). The layer-2 review uses `db_integrity` to
# disambiguate.
if [ ! -f "$journal_db" ]; then
  echo "journal db missing at $journal_db"
else
  echo "path: $journal_db"
  echo "size_bytes: $(stat -c %s "$journal_db" 2>/dev/null || stat -f %z "$journal_db" 2>/dev/null || echo unknown)"
  echo "mtime: $(stat -c %y "$journal_db" 2>/dev/null || stat -f %Sm "$journal_db" 2>/dev/null || echo unknown)"
  echo "age_seconds: $(( $(date +%s) - $(stat -c %Y "$journal_db" 2>/dev/null || stat -f %m "$journal_db" 2>/dev/null || echo 0) ))"
  for ext in -wal -shm; do
    f="${journal_db}${ext}"
    if [ -f "$f" ]; then
      echo "$(basename "$f")_size_bytes: $(stat -c %s "$f" 2>/dev/null || stat -f %z "$f" 2>/dev/null || echo unknown)"
    fi
  done
  if command -v sqlite3 >/dev/null 2>&1; then
    # PRAGMA integrity_check returns "ok" on a clean DB; otherwise lists
    # the first ~100 corruption findings. Truncate at 200 chars so a
    # noisy result doesn't bloat the snapshot.
    integ=$(sqlite3 "$journal_db" 'PRAGMA integrity_check;' 2>&1 | tr '\n' ' ' | head -c 200)
    echo "integrity_check: ${integ:-(no output)}"
    for table in trades order_packages; do
      count=$(sqlite3 "$journal_db" "SELECT COUNT(*) FROM $table;" 2>/dev/null || echo '?')
      echo "${table}_total: $count"
    done
    open_count=$(sqlite3 "$journal_db" "SELECT COUNT(*) FROM trades WHERE status='open';" 2>/dev/null || echo '?')
    echo "open_positions_total: $open_count"
  else
    echo "integrity_check: skipped (sqlite3 not available)"
  fi
fi

echo "=== AUDIT_LOG ==="
# signal_audit.jsonl is the NDJSON source of truth for ticks, signal
# evaluations, and pipeline outcomes. The layer-2 review's mandatory
# 6h diag pulls read from it; if it's stale or empty the review can't
# distinguish "bot idle" from "writer crashed." Surface freshness and
# a per-hour event count up front.
if [ ! -f "$audit_jsonl" ]; then
  echo "audit jsonl missing at $audit_jsonl"
else
  echo "path: $audit_jsonl"
  echo "size_bytes: $(stat -c %s "$audit_jsonl" 2>/dev/null || stat -f %z "$audit_jsonl" 2>/dev/null || echo unknown)"
  echo "mtime: $(stat -c %y "$audit_jsonl" 2>/dev/null || stat -f %Sm "$audit_jsonl" 2>/dev/null || echo unknown)"
  echo "age_seconds: $(( $(date +%s) - $(stat -c %Y "$audit_jsonl" 2>/dev/null || stat -f %m "$audit_jsonl" 2>/dev/null || echo 0) ))"
  # Last event — helps the reviewer correlate "file fresh" with "actual
  # writes," not just a touch.
  last_line=$(tail -n 1 "$audit_jsonl" 2>/dev/null)
  if [ -n "$last_line" ]; then
    echo "last_event:"
    printf '%s\n' "$last_line" | python3 -c '
import json, sys
try:
    ev = json.loads(sys.stdin.read())
    ts = ev.get("logged_at_utc") or ev.get("ts") or "?"
    name = ev.get("event", "?")
    print(f"  ts={ts}  event={name}")
except Exception as e:
    print(f"  (parse error: {e})")
' 2>/dev/null || echo "  (parse error)"
  else
    echo "last_event: (empty file)"
  fi
  # Event count over the last hour — layer-2 escalates on long silences.
  tail -n 20000 "$audit_jsonl" 2>/dev/null \
    | python3 - <<'PY' || echo "events_last_hour: (count failed)"
import json, sys, time
from datetime import datetime
cutoff = time.time() - 3600
count = 0
for line in sys.stdin:
    line = line.rstrip("\n")
    if not line:
        continue
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        continue
    ts_str = ev.get("logged_at_utc") or ev.get("ts") or ""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        continue
    if dt.timestamp() >= cutoff:
        count += 1
print(f"events_last_hour: {count}")
PY
fi

echo "=== VM ==="
echo "-- disk --"
df -h "$REPO_DIR" 2>/dev/null || df -h
echo "-- memory --"
free -h 2>/dev/null || vm_stat 2>/dev/null || true
echo "-- uptime --"
uptime
echo "-- load --"
cat /proc/loadavg 2>/dev/null || true

echo "=== END ==="
