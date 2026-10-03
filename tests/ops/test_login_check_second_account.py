"""breakout-login-check for a second prop account (TRADEIFY-WIRE, login only).

A non-breakout_1 account gets the READ-ONLY login check with ITS OWN credential
names, and nothing else: every executor mode and reset-feed are refused (the
executor kill switch, state and feed are breakout_1's until PR A #14663).
breakout_1's key list is unchanged.
"""
import pathlib
import subprocess
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
WRAPPER = (REPO / "scripts" / "ops" / "breakout_login_check_action.sh").read_text()
CODE = "\n".join(ln for ln in WRAPPER.splitlines() if not ln.lstrip().startswith("#"))


def test_breakout_key_list_is_unchanged():
    assert 'CHECK_KEYS="BREAKOUT_DX_USERNAME BREAKOUT_DX_PASSWORD DASHBOARD_API_TOKEN PROP_EXECUTOR_MODE"' in CODE


def test_other_account_keys_come_from_its_platform_entry():
    # PR A (#14663): the names come from scripts/prop/prop_env_keys.py, which
    # reads the account's prop_platforms.yaml entry (no entry = exit 1, refused).
    assert 'python3 scripts/prop/prop_env_keys.py "${ACCOUNT}"' in CODE
    ok = subprocess.run([sys.executable, "scripts/prop/prop_env_keys.py", "tradeify_1"],
                        capture_output=True, text=True, cwd=str(REPO))
    assert ok.returncode == 0
    assert ok.stdout.split() == ["TRADEIFY_DX_USERNAME", "TRADEIFY_DX_PASSWORD", "PROP_EXECUTOR_MODE_TRADEIFY_1"]
    bad = subprocess.run([sys.executable, "scripts/prop/prop_env_keys.py", "no_such_account"],
                         capture_output=True, text=True, cwd=str(REPO))
    assert bad.returncode != 0 and bad.stdout == ""
    # the other-account branch never exports breakout_1's GLOBAL kill switch
    other = CODE.split('CHECK_KEYS="${ACCT_KEYS} DASHBOARD_API_TOKEN"')[0].rsplit("else", 1)[1]
    assert "PROP_EXECUTOR_MODE " not in other and '"PROP_EXECUTOR_MODE"' not in other


def test_other_accounts_never_touch_breakout_1_state():
    """PR A replaces #14682's blanket refusal with PER-ACCOUNT state: another
    account's feed, executor ledger/latch and kill switch are its own, and the
    executor TIMER (breakout_1's) still refuses any other account."""
    assert 'FEED_DIR="${BASE}/feed"' in CODE and 'X_STATE_DIR="${BASE}/executor"' in CODE
    assert 'FEED_DIR="${BASE}/accounts/${ACCOUNT}/feed"' in CODE
    assert 'X_STATE_DIR="${BASE}/accounts/${ACCOUNT}/executor"' in CODE
    assert 'MODE_KEY="PROP_EXECUTOR_MODE_' in CODE
    # reset-feed clears the ACCOUNT's own feed dir, never a hardcoded ${BASE}/feed
    assert 'rm -f "${FEED_DIR}/tripped"' in CODE and 'rm -f "${BASE}/feed/tripped"' not in CODE
    # executor modes reuse the account's own session + state dir
    assert '--storage-state "${FEED_DIR}/session_state.json"' in CODE
    assert '--state-dir "${X_STATE_DIR}"' in CODE
    # the breakout_1 executor timer refuses every other account
    assert "the executor timer is breakout_1's" in CODE


def test_tradeify_platform_entry_is_explicit_and_disarmed():
    from src.prop.platform import load_platform_config
    t = load_platform_config("tradeify_1")
    assert t["platform"] == "dxtrade" and t["login_url"] == "https://dx.tradeify247.co/"
    assert (t["username_env"], t["password_env"]) == ("TRADEIFY_DX_USERNAME", "TRADEIFY_DX_PASSWORD")
    # Nothing ENABLED is the arming gate. The only lot entry is the dry-only
    # ETHUSD one (TRADEIFY-DRY, #15846) that lets round-trip-dry reach the form.
    assert t["executor"]["enabled_venue_symbols"] == []
    assert t["executor"]["lots"] == {
        "ETHUSD": {"lot_units": 1, "lot_step": 0.01, "min_lots": 0.01, "price_step": 0.001},
        "SOLUSD": {"lot_units": 1, "lot_step": 0.01, "min_lots": 0.01, "price_step": 0.001},
        "XRPUSD": {"lot_units": 1, "lot_step": 0.01, "min_lots": 0.01, "price_step": 0.00001}}
    assert t["executor"]["watched_click_max_lots"] == {"ETHUSD": 0.01}
    assert load_platform_config("breakout_1")["login_url"] == "https://wss.breakoutprop.com/"


def test_tradeify_credentials_are_optional_secrets():
    wf = yaml.safe_load((REPO / ".github/workflows/sync-vm-secrets.yml").read_text())
    assert "TRADEIFY_DX_USERNAME TRADEIFY_DX_PASSWORD" in str(wf)
    found = 0
    for job in (wf.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            env = step.get("env") or {}
            if "BREAKOUT_DX_USERNAME" in env:
                found += 1
                assert env.get("TRADEIFY_DX_USERNAME") == "${{ secrets.TRADEIFY_DX_USERNAME }}"
                assert env.get("TRADEIFY_DX_PASSWORD") == "${{ secrets.TRADEIFY_DX_PASSWORD }}"
    assert found >= 2


def test_other_account_run_switches_to_its_own_lock_after_the_shared_bootstrap():
    shared = CODE.index('exec 9>"${BASE}/login.lock"')
    install = CODE.index("-m playwright install chromium")
    own = CODE.index('exec 9>"${ACCT_LOCK_DIR}/login.lock"')
    run = CODE.index("scripts/prop/breakout_login_check.py")
    execu = CODE.index("scripts/prop/prop_executor_tick.py")
    assert shared < install < own < min(run, execu)
    assert 'ACCT_LOCK_DIR="${BASE}/accounts/${ACCOUNT}"' in CODE


def test_dump_dir_is_per_account_and_breakout_keeps_its_own():
    assert 'DUMP_DIR="${BASE}/last-run"' in CODE
    assert 'DUMP_DIR="${BASE}/accounts/${ACCOUNT}/last-run"' in CODE
    assert 'ARGS=(--account "${ACCOUNT}" --dump-dir "${DUMP_DIR}")' in CODE
    assert '--dump-dir "${BASE}/last-run"' not in CODE
