#!/usr/bin/env bash
# Smoke test for scripts/install_systemd_units.sh.
#
# Runs the installer with REPO_DIR + SYSTEMD_DIR pointing at a temp
# directory so we can assert behaviour without touching /etc.

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
PASS=0
FAIL=0

assert_eq() {
    local name="$1" got="$2" want="$3"
    if [ "$got" = "$want" ]; then
        PASS=$((PASS + 1))
        echo "ok  $name"
    else
        FAIL=$((FAIL + 1))
        echo "FAIL $name  got=$got want=$want"
    fi
}

# ---------------------------------------------------------------------------
# 1. Fresh systemd dir: every deploy/*.service must land.
# ---------------------------------------------------------------------------

setup_temp_systemd() {
    local tmp
    tmp=$(mktemp -d)
    # Stage a minimal repo layout the installer expects.
    mkdir -p "$tmp/deploy" "$tmp/systemd"
    # Copy real units so we exercise the actual production filenames.
    cp "$REPO_ROOT/deploy/"*.service "$tmp/deploy/" 2>/dev/null || true
    cp "$REPO_ROOT/deploy/"*.timer "$tmp/deploy/" 2>/dev/null || true
    cp "$REPO_ROOT/scripts/install_systemd_units.sh" "$tmp/install.sh"
    chmod +x "$tmp/install.sh"
    echo "$tmp"
}

count_non_template_units() {
    local dir="$1"
    find "$dir" -maxdepth 1 -name '*.service' -o -name '*.timer' \
        | grep -v '@' | wc -l
}

run_test_fresh() {
    local tmp; tmp=$(setup_temp_systemd)
    REPO_DIR="$tmp" SYSTEMD_DIR="$tmp/systemd" bash "$tmp/install.sh" >/dev/null 2>&1 \
        || { FAIL=$((FAIL + 1)); echo "FAIL fresh-install  installer exited nonzero"; return; }
    local installed; installed=$(count_non_template_units "$tmp/systemd")
    local source;    source=$(count_non_template_units "$tmp/deploy")
    assert_eq "fresh-install copies every non-template unit" "$installed" "$source"
    rm -rf "$tmp"
}

# ---------------------------------------------------------------------------
# 2. Re-run is a no-op (idempotency).
# ---------------------------------------------------------------------------

run_test_idempotent() {
    local tmp; tmp=$(setup_temp_systemd)
    # First run installs everything.
    REPO_DIR="$tmp" SYSTEMD_DIR="$tmp/systemd" bash "$tmp/install.sh" >/dev/null 2>&1
    # Second run should print "nothing to refresh".
    local out; out=$(REPO_DIR="$tmp" SYSTEMD_DIR="$tmp/systemd" bash "$tmp/install.sh" 2>&1)
    if echo "$out" | grep -q "nothing to refresh"; then
        PASS=$((PASS + 1)); echo "ok  re-run is no-op"
    else
        FAIL=$((FAIL + 1)); echo "FAIL re-run is no-op  output=$out"
    fi
    rm -rf "$tmp"
}

# ---------------------------------------------------------------------------
# 3. Diff'd unit gets refreshed.
# ---------------------------------------------------------------------------

run_test_refresh_on_change() {
    local tmp; tmp=$(setup_temp_systemd)
    REPO_DIR="$tmp" SYSTEMD_DIR="$tmp/systemd" bash "$tmp/install.sh" >/dev/null 2>&1

    # Pick one unit, mutate the deploy/ copy, ensure it lands.
    local unit; unit=$(ls "$tmp/deploy/"*.service | grep -v '@' | head -1)
    local unit_name; unit_name=$(basename "$unit")
    echo "# mutated comment" >> "$unit"

    local out; out=$(REPO_DIR="$tmp" SYSTEMD_DIR="$tmp/systemd" bash "$tmp/install.sh" 2>&1)
    if cmp -s "$unit" "$tmp/systemd/$unit_name"; then
        PASS=$((PASS + 1)); echo "ok  refresh on diff  $unit_name"
    else
        FAIL=$((FAIL + 1)); echo "FAIL refresh on diff  $unit_name"
        echo "$out"
    fi
    rm -rf "$tmp"
}

# ---------------------------------------------------------------------------
# 4. Template units (with @ in the name) are skipped.
# ---------------------------------------------------------------------------

run_test_template_skipped() {
    local tmp; tmp=$(setup_temp_systemd)
    REPO_DIR="$tmp" SYSTEMD_DIR="$tmp/systemd" bash "$tmp/install.sh" >/dev/null 2>&1
    if ls "$tmp/systemd/"*@*.service 2>/dev/null | grep -q .; then
        FAIL=$((FAIL + 1)); echo "FAIL template-skipped  template unit was copied"
    else
        PASS=$((PASS + 1)); echo "ok  template units (claude-vm-runner@) are not auto-installed"
    fi
    rm -rf "$tmp"
}

# ---------------------------------------------------------------------------
# 5. Pinned: ict-git-sync.service has EnvironmentFile pointing at .env.
# ---------------------------------------------------------------------------

run_test_git_sync_loads_env() {
    if grep -q "^EnvironmentFile=-/home/ubuntu/ict-trading-bot/\.env\b" \
        "$REPO_ROOT/deploy/ict-git-sync.service"; then
        PASS=$((PASS + 1)); echo "ok  ict-git-sync.service loads .env (S-018 ping fix)"
    else
        FAIL=$((FAIL + 1))
        echo "FAIL ict-git-sync.service must load .env so notify_on_pull.py sees TELEGRAM_BOT_TOKEN"
    fi
}

# ---------------------------------------------------------------------------
# 6. FIX-SA-08: every oneshot behind a timer gets OnFailure=, the template is
#    installed, the trader (long-running) does not, and a re-run is a no-op.
# ---------------------------------------------------------------------------
run_test_onfailure_dropins() {
    local tmp; tmp=$(setup_temp_systemd)
    mkdir -p "$tmp/deploy/dropins"
    cp "$REPO_ROOT/deploy/dropins/"*.conf "$tmp/deploy/dropins/"
    cp "$REPO_ROOT/deploy/ict-notify-failure@.service" "$tmp/deploy/"
    REPO_DIR="$tmp" SYSTEMD_DIR="$tmp/systemd" bash "$tmp/install.sh" >/dev/null 2>&1
    assert_eq "notify template installed" \
        "$([ -f "$tmp/systemd/ict-notify-failure@.service" ] && echo yes || echo no)" yes
    local want=0 got=0 base
    for t in "$tmp"/deploy/*.timer; do
        base=$(basename "$t" .timer)
        case " ict-trainer-git-sync ict-heartbeat ict-ib-gateway-reset ict-ib-gateway-watchdog " in *" $base "*) continue;; esac
        [ -f "$tmp/deploy/$base.service" ] && grep -q '^Type=oneshot' "$tmp/deploy/$base.service" || continue
        want=$((want + 1))
        grep -q '^OnFailure=ict-notify-failure@%n.service' \
            "$tmp/systemd/$base.service.d/onfailure.conf" 2>/dev/null && got=$((got + 1))
    done
    assert_eq "every timer-driven oneshot has OnFailure= ($want expected, >0)" \
        "$([ "$want" -gt 0 ] && echo "$got" || echo none)" "$want"
    assert_eq "trader unit gets no OnFailure drop-in" \
        "$([ -e "$tmp/systemd/ict-trader-live.service.d/onfailure.conf" ] && echo yes || echo no)" no
    local out; out=$(REPO_DIR="$tmp" SYSTEMD_DIR="$tmp/systemd" bash "$tmp/install.sh" 2>&1)
    if echo "$out" | grep -q "nothing to refresh"; then
        PASS=$((PASS + 1)); echo "ok  onfailure re-run is no-op"
    else
        FAIL=$((FAIL + 1)); echo "FAIL onfailure re-run is no-op  output=$out"
    fi
    rm -rf "$tmp"
}

run_test_fresh
run_test_idempotent
run_test_refresh_on_change
run_test_template_skipped
run_test_git_sync_loads_env
run_test_onfailure_dropins

echo
echo "=== install_systemd_units summary: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -ne 0 ]; then
    exit 1
fi
