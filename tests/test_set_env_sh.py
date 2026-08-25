"""Target-scoping tests for `scripts/ops/set_env.sh`.

WHY THIS EXISTS (2026-08-25,
`BL-20260825-SET-ENV-CANNOT-TARGET-A-SERVICE-SCOPED-ENV-FILE`). The action
could choose which SERVICE to restart but not which FILE to write, and always
wrote the shared repo `.env`. `ict-web-api.service` loads that file too, so a
key written there also reaches `ict-trader-live`.

For `IB_MD_CLIENT_ID` that is not cosmetic: nothing puts the key in a settings
dict except `routers/candles.py` (web-api only), so the TRADER reads it from
the environment and falls through to `exec_client_id + 1` = 498. Writing 600
into the SHARED file moves the trader's market-data socket onto 600, where it
collides with the web-api's own 600 across two processes — IB error 326,
starving the MES/MGC/MHG candles the reservation exists to protect. The
shared-file write would have been WORSE than doing nothing.

These run with `REPO_DIR` pointed at a tmp dir and `systemctl` shimmed, so
nothing touches a real VM.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "ops" / "set_env.sh"


def _run(tmp_path: Path, *, key: str = "IB_MD_CLIENT_ID", value: str = "600",
         service: str = "none", env_file: str | None = None):
    """Invoke the script against a tmp REPO_DIR with systemctl shimmed."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    # `require_systemctl` only needs the binary to exist; service is "none"
    # in these tests so nothing is actually restarted.
    shim = bin_dir / "systemctl"
    shim.write_text("#!/usr/bin/env bash\nexit 0\n")
    shim.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["REPO_DIR"] = str(tmp_path)
    env["ENV_KEY"] = key
    env["ENV_VALUE"] = value
    env["ENV_SERVICE"] = service
    if env_file is not None:
        env["ENV_FILE_TARGET"] = env_file
    else:
        env.pop("ENV_FILE_TARGET", None)
    return subprocess.run(["bash", str(_SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=60)


def test_default_target_writes_the_shared_env(tmp_path: Path) -> None:
    """No `env_file` at all == the pre-2026-08-25 behaviour, unchanged."""
    shared = tmp_path / ".env"
    shared.write_text("EXISTING=1\n# a comment\n")
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    body = shared.read_text()
    assert "IB_MD_CLIENT_ID=600" in body
    # every other line preserved byte-for-byte
    assert "EXISTING=1" in body and "# a comment" in body


def test_explicit_shared_is_identical_to_the_default(tmp_path: Path) -> None:
    shared = tmp_path / ".env"
    shared.write_text("EXISTING=1\n")
    assert _run(tmp_path, env_file="shared").returncode == 0
    first = shared.read_text()

    shared.write_text("EXISTING=1\n")
    assert _run(tmp_path).returncode == 0
    assert shared.read_text() == first


def test_upsert_is_idempotent_and_does_not_duplicate(tmp_path: Path) -> None:
    shared = tmp_path / ".env"
    shared.write_text("IB_MD_CLIENT_ID=499\nOTHER=x\n")
    assert _run(tmp_path).returncode == 0
    body = shared.read_text()
    assert body.count("IB_MD_CLIENT_ID=") == 1
    assert "IB_MD_CLIENT_ID=600" in body
    assert "499" not in body


@pytest.mark.parametrize("bogus", [
    "sharedd", "SHARED", "web_api", "trader", "..", "/etc/passwd",
    "/home/ubuntu/ict-trading-bot/.env",
])
def test_an_unknown_target_errors_and_writes_NOTHING(tmp_path: Path, bogus: str) -> None:
    """THE test. A silent fallback to `shared` reintroduces the exact
    collision this parameter exists to prevent — and would report success
    while doing it. An unrecognised target must fail closed.

    A raw PATH is included in the cases deliberately: targets are symbolic
    names, and the issue body is untrusted input, so accepting a path would
    make this action an arbitrary-file writer on the live VM.
    """
    shared = tmp_path / ".env"
    shared.write_text("EXISTING=1\n")
    proc = _run(tmp_path, env_file=bogus)
    assert proc.returncode != 0, f"{bogus!r} was accepted"
    assert shared.read_text() == "EXISTING=1\n", "the shared file was written anyway"


def test_the_value_is_never_echoed(tmp_path: Path) -> None:
    """`set-env`'s standing rule: values never reach the log or the audit."""
    (tmp_path / ".env").write_text("")
    secret = "s3cr3t-must-not-appear"
    proc = _run(tmp_path, key="TELEGRAM_CLAUDE_BOT_TOKEN", value=secret)
    assert proc.returncode == 0, proc.stderr
    assert secret not in proc.stdout
    assert secret not in proc.stderr


def test_web_api_target_resolves_to_the_scoped_path(tmp_path: Path) -> None:
    """The `web-api` target must NOT resolve to the shared file.

    It points at a root-owned `/etc` path that does not exist in the sandbox,
    so the run fails — which is itself the assertion worth making: it failed
    reaching for the OTHER file, and left the shared one untouched. A target
    that quietly fell back would show up here as a written shared `.env`.
    """
    shared = tmp_path / ".env"
    shared.write_text("EXISTING=1\n")
    proc = _run(tmp_path, env_file="web-api")
    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    assert "/etc/ict-trader/web-api.env" in combined or "web-api" in combined
    assert shared.read_text() == "EXISTING=1\n", "shared file must be untouched"
