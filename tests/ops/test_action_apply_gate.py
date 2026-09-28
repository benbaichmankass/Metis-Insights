"""FIX-CA-14 (CA-A10-200): money-DB-writing system-action wrappers must
respect their python helper's dry-run default.

``rebuild_pnl_from_bybit_action.sh`` and
``backfill_monitor_closed_pnl_action.sh`` used to pass ``--apply``
unconditionally, so every dispatch rewrote ``trades`` rows with no dry-run
preview — unlike every sibling Tier-2 wrapper (e.g.
``reconcile_netting_rows_action.sh``), which gates the flag on
``ACTION_APPLY``. These tests run each wrapper in a sandbox (stub
``_lib.sh`` + a stub helper that records its argv) and assert ``--apply``
reaches the helper only when ``ACTION_APPLY`` is ``true``/``True``.

They also assert the system-actions workflow forwards ``ACTION_APPLY`` to
the VM for these actions; without that, the gate would make ``apply: true``
unreachable from an issue body.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OPS = _REPO_ROOT / "scripts" / "ops"
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "system-actions.yml"

# action name -> (wrapper, helper basename, extra env the wrapper requires)
_GATED = {
    "rebuild-pnl-from-bybit": (
        "rebuild_pnl_from_bybit_action.sh",
        "rebuild_pnl_from_bybit.py",
        {"ACCOUNT_ID": "bybit_2"},
    ),
    "backfill-monitor-closed-pnl": (
        "backfill_monitor_closed_pnl_action.sh",
        "backfill_monitor_closed_pnl.py",
        {},
    ),
}

_STUB_LIB = """\
REPO_DIR="${STUB_REPO_DIR}"
log() { echo "[stub] $*"; }
record_audit() { :; }
load_runtime_secrets() { :; }
runtime_db_path() { echo "${STUB_DB}"; }
"""

_STUB_HELPER = """\
import json, os, sys
with open(os.environ["STUB_ARGV_OUT"], "w") as fh:
    json.dump(sys.argv[1:], fh)
"""


def _run_wrapper(tmp_path: Path, action: str, apply_value):
    wrapper, helper, extra_env = _GATED[action]
    ops = tmp_path / "scripts" / "ops"
    ops.mkdir(parents=True)
    shutil.copy(_OPS / wrapper, ops / wrapper)
    (ops / "_lib.sh").write_text(_STUB_LIB)
    (ops / helper).write_text(_STUB_HELPER)
    db = tmp_path / "trade_journal.db"
    db.write_bytes(b"")
    argv_out = tmp_path / "argv.json"

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "STUB_REPO_DIR": str(tmp_path),
        "STUB_DB": str(db),
        "STUB_ARGV_OUT": str(argv_out),
        **extra_env,
    }
    if apply_value is not None:
        env["ACTION_APPLY"] = apply_value
    proc = subprocess.run(
        ["bash", str(ops / wrapper)],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert argv_out.exists(), (
        "helper was never invoked:\n" + proc.stdout + proc.stderr
    )
    return json.loads(argv_out.read_text())


@pytest.mark.parametrize("action", sorted(_GATED))
@pytest.mark.parametrize("apply_value", [None, "", "false", "no", "yes"])
def test_wrapper_is_dry_run_unless_apply_true(tmp_path, action, apply_value):
    argv = _run_wrapper(tmp_path, action, apply_value)
    assert "--apply" not in argv, (
        f"{action}: ACTION_APPLY={apply_value!r} still passed --apply "
        f"(argv={argv})"
    )


@pytest.mark.parametrize("action", sorted(_GATED))
@pytest.mark.parametrize("apply_value", ["true", "True"])
def test_wrapper_applies_when_apply_true(tmp_path, action, apply_value):
    argv = _run_wrapper(tmp_path, action, apply_value)
    assert "--apply" in argv


@pytest.mark.parametrize("action", sorted(_GATED))
def test_workflow_forwards_action_apply(action):
    """The gate is only reachable if the workflow puts ACTION_APPLY on the
    remote command line for this action."""
    text = _WORKFLOW.read_text()
    block = re.search(
        r'if \[ "\$\{ACTION\}" = "' + re.escape(action) + r'" \]; then\n'
        r"(.*?)\n\s*fi\n",
        text, re.S,
    )
    assert block is not None, f"no REMOTE_CMD block for {action}"
    assert "ACTION_APPLY=$(printf '%q' \"${ACTION_APPLY:-}\")" in block.group(1)
