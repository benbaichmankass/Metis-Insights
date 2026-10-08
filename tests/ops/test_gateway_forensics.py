"""gateway-forensics must stay read-only, masked, and routed like gateway-logs."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/ops/gateway_forensics.sh"
WORKFLOW = (ROOT / ".github/workflows/system-actions.yml").read_text()

MUTATING = re.compile(
    r"docker\s+(restart|stop|start|kill|rm|run|exec|update)\b|"
    r"systemctl\s+(restart|stop|start|enable|disable|kill|mask|reload)\b|"
    r"\brm\s|\btee\b|>>|\bkill\b"
)


def test_script_is_syntax_valid():
    assert subprocess.run(["bash", "-n", str(SCRIPT)], check=False).returncode == 0


def test_script_never_mutates():
    body = "\n".join(
        ln for ln in SCRIPT.read_text().splitlines() if not ln.lstrip().startswith("#")
    )
    assert not MUTATING.search(body), MUTATING.search(body)


def test_output_is_masked():
    mask = re.search(r"mask\(\) \{ sed -E '([^']+)'", SCRIPT.read_text()).group(1)
    sample = "10.0.0.251 acct DUQ325724 1.2.3.4 DU123456"
    out = subprocess.run(
        ["sed", "-E", mask], input=sample, capture_output=True, text=True, check=False
    ).stdout
    assert (
        "10.0.0.251" not in out
        and "DUQ325724" not in out
        and "1.2.3.4" not in out
        and "DU123456" not in out
    )


def test_routed_to_gateway_vm_and_tier1():
    assert WORKFLOW.count('"${ACTION}" = "gateway-forensics"') == 2
    assert (
        "gateway-forensics"
        in WORKFLOW.split("status-check|verify-account-mode")[1][:200]
    )
