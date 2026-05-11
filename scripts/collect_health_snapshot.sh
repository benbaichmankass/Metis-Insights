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
#   REPO_DIR        repo root on VM (default: parent of this script's dir)
#   LOOKBACK_MIN    minutes of log history to surface (default: 1440 = 24h)

set -u

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)}"
LOOKBACK_MIN="${LOOKBACK_MIN:-1440}"

# Search roots — only scan dirs that exist, otherwise find errors out.
roots=()
for d in "$REPO_DIR/logs" "$REPO_DIR/runtime_logs"; do
  [ -d "$d" ] && roots+=("$d")
done

audit_jsonl="$REPO_DIR/runtime_logs/signal_audit.jsonl"
journal_db="$REPO_DIR/trade_journal.db"

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
echo "git_head: $(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "lookback_min: $LOOKBACK_MIN"

echo "=== PROCESSES ==="
ps -eo pid,etime,pcpu,pmem,cmd --sort=-pcpu 2>/dev/null \
  | grep -E "(python|ict|trader|strategy|telegram_bot|claude_bridge)" \
  | grep -v grep \
  | head -30 \
  || echo "no matching processes"

echo "=== HEARTBEAT ==="
hb="$REPO_DIR/runtime_logs/heartbeat.txt"
if [ -f "$hb" ]; then
  echo "path: $hb"
  echo "mtime: $(stat -c %y "$hb" 2>/dev/null || stat -f %Sm "$hb" 2>/dev/null || echo unknown)"
  echo "age_seconds: $(( $(date +%s) - $(stat -c %Y "$hb" 2>/dev/null || stat -f %m "$hb" 2>/dev/null || echo 0) ))"
  echo "tail:"
  tail -n 5 "$hb" 2>/dev/null || true
else
  echo "no heartbeat.txt at $hb"
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
