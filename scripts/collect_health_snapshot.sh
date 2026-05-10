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
tail_recent 'tick*.log' 50 "no tick logs in last ${LOOKBACK_MIN}m"

echo "=== SIGNALS ==="
tail_recent 'signal*.log' 100 "no signal logs in last ${LOOKBACK_MIN}m"

echo "=== ORDERS ==="
tail_recent 'order*.log' 100 "no order logs in last ${LOOKBACK_MIN}m"

echo "=== TRADES ==="
tail_recent 'trade*.log' 100 "no trade logs in last ${LOOKBACK_MIN}m"

echo "=== POSITIONS ==="
tail_recent 'position*' 30 "no position logs in last ${LOOKBACK_MIN}m"

echo "=== MONITORING ==="
tail_recent 'monitor*' 30 "no monitor logs"
tail_recent 'watchdog*' 30 "no watchdog logs"

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
