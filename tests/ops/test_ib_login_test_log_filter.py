"""FIX-CA-18 (CA-A14-ib-login-test-password-grep).

``vm-ib-gateway-live-login-test.yml`` tails the throwaway LIVE ib-gateway
container's docker log and posts the matched lines into a PUBLIC issue
comment / step summary. The old filter was a broad keyword grep including
bare ``password`` / ``incorrect`` / ``denied`` — so any log line carrying a
raw credential next to one of those words would be published.

The filter is now a fixed allowlist of IBC status phrases plus a hard
deny of credential-shaped lines, defined as ``auth_log_filter`` between
markers in the workflow's remote script. This test extracts that function
and runs it with bash against synthetic log lines.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github" / "workflows" / "vm-ib-gateway-live-login-test.yml"
)
_BEGIN = "# --- auth_log_filter BEGIN"
_END = "# --- auth_log_filter END"


def _filter_source() -> str:
    text = _WORKFLOW.read_text()
    m = re.search(re.escape(_BEGIN) + r".*?\n(.*?)\n\s*" + re.escape(_END),
                  text, re.S)
    assert m, (
        "auth_log_filter block not found in the workflow — the log tail must "
        "go through a fixed, tested allowlist filter"
    )
    return textwrap.dedent(m.group(1))


def _run_filter(lines: list[str]) -> list[str]:
    src = _filter_source()
    proc = subprocess.run(
        ["bash", "-c", src + "\nauth_log_filter"],
        input="\n".join(lines) + "\n", capture_output=True, text=True,
        timeout=30,
    )
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


pytestmark = pytest.mark.skipif(shutil.which("bash") is None,
                                reason="bash required")


def test_workflow_log_tail_uses_the_filter():
    text = _WORKFLOW.read_text()
    # the tail that feeds out.txt must go through the tested function,
    # not an inline keyword grep
    assert re.search(r'docker logs --since 8m "\$NAME" 2>&1 \\\s*\n\s*\| auth_log_filter',
                     text), "log tail does not pipe through auth_log_filter"
    assert "|password|" not in text


@pytest.mark.parametrize("line", [
    "2026-09-27 10:00:01:123 IBC: Login dialog: password=Hunter2! user=jdoe",
    "IBC: Invalid password 'Hunter2!' supplied",
    "TWS_PASSWORD=Hunter2!",
    "IbPassword=Hunter2!",
    "Login has completed for userid jdoe password Hunter2!",
    "IBC: Access denied: Hunter2!",
    "IBC: incorrect credentials jdoe/Hunter2!",
])
def test_credential_shaped_lines_are_not_forwarded(line):
    assert _run_filter([line]) == []


@pytest.mark.parametrize("line", [
    "2026-09-27 10:00:05:000 IBC: Login has completed",
    "IBC: Second Factor Authentication initiated",
    "IBC: could not find second factor device 'IB Key' in the list",
    "IBC: Existing session detected - will wait",
    "IBC: Starting application",
    "Market data farm connection is OK:usfarm",
])
def test_status_lines_still_forwarded(line):
    assert _run_filter([line]) == [line]
