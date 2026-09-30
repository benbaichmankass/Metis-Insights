"""breakout-login-check for a second prop account (TRADEIFY-WIRE, login only).

A non-breakout_1 account gets the READ-ONLY login check with ITS OWN credential
names, and nothing else: every executor mode and reset-feed are refused (the
executor kill switch, state and feed are breakout_1's until PR A #14663).
breakout_1's key list is unchanged.
"""
import pathlib
import re
import subprocess
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
WRAPPER = (REPO / "scripts" / "ops" / "breakout_login_check_action.sh").read_text()
CODE = "\n".join(ln for ln in WRAPPER.splitlines() if not ln.lstrip().startswith("#"))


def test_breakout_key_list_is_unchanged():
    assert 'CHECK_KEYS="BREAKOUT_DX_USERNAME BREAKOUT_DX_PASSWORD DASHBOARD_API_TOKEN PROP_EXECUTOR_MODE"' in CODE


def test_other_account_keys_come_from_its_platform_entry():
    m = re.search(r"python3 -c '([^']+)' \"\$\{ACCOUNT\}\"", CODE)
    assert m, "key derivation one-liner not found"
    one_liner = m.group(1)
    ok = subprocess.run([sys.executable, "-c", one_liner, "tradeify_1"],
                        capture_output=True, text=True, cwd=str(REPO))
    assert ok.returncode == 0 and ok.stdout.split() == ["TRADEIFY_DX_USERNAME", "TRADEIFY_DX_PASSWORD"]
    bad = subprocess.run([sys.executable, "-c", one_liner, "no_such_account"],
                         capture_output=True, text=True, cwd=str(REPO))
    assert bad.returncode != 0 and bad.stdout == ""
    # the other-account branch exports no executor kill switch
    other = CODE.split('CHECK_KEYS="${ACCT_KEYS} DASHBOARD_API_TOKEN"')[0].rsplit("else", 1)[1]
    assert "PROP_EXECUTOR_MODE" not in other


def test_executor_modes_and_reset_feed_refused_for_other_accounts_before_anything_runs():
    refuse_exec = CODE.index('if [ -n "${EXEC_MODE}" ] && [ "${ACCOUNT}" != "breakout_1" ]; then')
    refuse_reset = CODE.index('if [ "${WANT_RESET}" = "1" ] && [ "${ACCOUNT}" != "breakout_1" ]; then')
    first_action = min(CODE.index('if [ "${EXEC_MODE}" = "executor-clear-halt" ]'),
                       CODE.index("TIMER_SRC="),
                       CODE.index("scripts/prop/prop_executor_tick.py"),
                       CODE.index("scripts/prop/breakout_login_check.py"))
    assert refuse_reset < first_action and refuse_exec < first_action


def test_tradeify_platform_entry_is_explicit_and_disarmed():
    from src.prop.platform import load_platform_config
    t = load_platform_config("tradeify_1")
    assert t["platform"] == "dxtrade" and t["login_url"] == "https://dx.tradeify247.co/"
    assert (t["username_env"], t["password_env"]) == ("TRADEIFY_DX_USERNAME", "TRADEIFY_DX_PASSWORD")
    assert t["executor"]["enabled_venue_symbols"] == [] and t["executor"]["lots"] == {}
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
