"""probe-ticket takes ONE ``symbols:`` value (TRADEIFY-GOLIVE, manager
2026-10-02 21:35Z: probe-ticket ETHUSD, then SOLUSD, then XRPUSD). Nothing
forwarded PROBE_SYMBOL from an issue, so every probe-ticket measured the
SOLUSD default. No ``symbols:`` line keeps that default."""
import re
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ACTION = REPO / "scripts" / "ops" / "breakout_login_check_action.sh"


def test_probe_ticket_refuses_more_than_one_symbol(tmp_path):
    from tests.test_prop_executor_clear_halt import STUB_LIB
    d = tmp_path / "ops"
    d.mkdir()
    shutil.copy(ACTION, d / "action.sh")
    (d / "_lib.sh").write_text(STUB_LIB)
    home = tmp_path / "home"
    (home / ".cache" / "metis-prop-browser").mkdir(parents=True)
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "ACTION_APPLY": "probe-ticket",
           "ACCOUNT_ID": "tradeify_1", "ACTION_SYMBOLS": "ETHUSD,SOLUSD"}
    p = subprocess.run(["bash", str(d / "action.sh")], env=env, capture_output=True, text=True, timeout=30)
    assert p.returncode == 1 and "probe-ticket: refused — exactly one symbol" in p.stdout + p.stderr


def test_one_symbol_sets_probe_symbol_before_the_executor_args():
    s = ACTION.read_text()
    i = s.index('PROBE_SYMBOL="${ACTION_SYMBOLS// /}"')
    j = s.index('probe-ticket)        EARGS+=(--probe-ticket "${PROBE_SYMBOL:-SOLUSD}")')
    assert i < j                                         # resolved before it is used
    assert re.search(r'"\$\{EXEC_MODE\}" = "probe-ticket" \] && \[ -n "\$\{ACTION_SYMBOLS// /\}" \]', s)
