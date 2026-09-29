"""FIX-SA-07 — redaction of relay output: each secret shape + negative controls."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.ops.redact_diag_output import redact  # noqa: E402

SCRIPT = ROOT / "scripts" / "ops" / "redact_diag_output.py"

# Synthetic, shape-only fixtures — none is a real credential.
FAKE_TG = "123456789:" + "A" * 35
# Built at runtime, never written as a literal: a literal AKIA-shaped string or PEM
# header in the tree is (rightly) flagged by the weekly gitleaks history scan.
AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"
PEM_BEGIN = "-----" + "BEGIN "
PEM_END = "-----" + "END "
SECRETS = {
    "telegram_url": f"https://api.telegram.org/bot{FAKE_TG}/getMe",
    "telegram_token": f"token={FAKE_TG} end",
    "aws_access_key": f"key {AWS_KEY} end",
    "github_token": "t ghp_" + "a1B2" * 9 + " end",
    "github_token_pat": "t github_pat_" + "A1b2_" * 6 + " end",
    "pem_block": f"k {PEM_BEGIN}OPENSSH PRIVATE KEY-----\\nb3BlbnNzaC1rZXk\\n{PEM_END}OPENSSH PRIVATE KEY----- end",
    "pem_truncated": f"k {PEM_BEGIN}RSA PRIVATE KEY-----\nMIIEow",
    "bearer": "Authorization: Bearer abcdef0123456789ABCDEF.xyz-_~+/=",
    "long_hex": "tok " + "0123456789abcdef" * 4 + " end",
    "long_hex_upper": "tok " + "ABCDEF0123456789" * 5 + " end",
}


@pytest.mark.parametrize("name,text", SECRETS.items())
def test_each_secret_shape_redacted(name, text):
    out, counts = redact(text)
    assert counts, name
    assert "[REDACTED:" in out
    # the secret body must be gone, the surrounding context preserved
    for frag in (AWS_KEY, "ghp_a1B2", "github_pat_A1b2", "b3BlbnNzaC1rZXk",
                 "MIIEow", "abcdef0123456789ABCDEF", "0123456789abcdef0123456789abcdef",
                 "ABCDEF0123456789ABCDEF", FAKE_TG):
        assert frag not in out, (name, frag)


def test_pem_redaction_does_not_swallow_following_content():
    out, _ = redact(f"{PEM_BEGIN}PRIVATE KEY-----\nAAAA\n{PEM_END}PRIVATE KEY-----\nafter=1")
    assert out.endswith("after=1")


NORMAL = {
    "version": {"git_sha": "25f9eeb0" + "c" * 32, "branch": "main", "restart_pending": False},
    "trades": {"rows": [{"id": 1, "symbol": "BTCUSDT", "pnl": -12.5, "ts": "2026-09-29T10:00:00Z",
                         "order_package_id": "op-20260929-abc123", "strategy": "trend_long_1d"}],
               "total": 1, "limit": 5},
    "journalctl": "Sep 29 10:00:01 ict-bot-arm python[123]: tick ok in 0.42s 12345678901234",
    "numbers": {"a": 1234567890123456789012345678901234567890, "b": "3.14159265358979"},
}


@pytest.mark.parametrize("name,obj", NORMAL.items())
def test_negative_control_normal_diag_unchanged(name, obj):
    text = obj if isinstance(obj, str) else json.dumps(obj, indent=2)
    out, counts = redact(text)
    assert out == text and not counts, (name, counts)


def test_redacted_json_stays_valid():
    doc = {"x": SECRETS["long_hex"], "y": SECRETS["aws_access_key"], "z": SECRETS["github_token"]}
    out, _ = redact(json.dumps(doc))
    json.loads(out)


def test_cli_prints_counts_never_values(tmp_path):
    f = tmp_path / "o.txt"
    f.write_text(SECRETS["aws_access_key"] + "\n" + SECRETS["long_hex"])
    r = subprocess.run([sys.executable, str(SCRIPT), str(f)], capture_output=True, text=True)
    assert r.returncode == 0
    assert "aws_access_key=1" in r.stdout and "long_hex=1" in r.stdout
    assert "AKIA" not in r.stdout + r.stderr and "0123456789abcdef" not in r.stdout + r.stderr
    assert AWS_KEY not in f.read_text()


def test_cli_missing_file_fails_closed(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "nope")],
                       capture_output=True, text=True)
    assert r.returncode == 1
