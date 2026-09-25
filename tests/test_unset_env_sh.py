"""Tests for `scripts/ops/unset_env.sh`.

WHY THIS EXISTS (2026-09-25). `set-env` can create or update a key but, by
design, can never delete one — its empty-value guard (added after the
2026-08-01 `TELEGRAM_BOT_TOKEN` crashloop) refuses an empty resolved value
outright, which is correct but means there was NO allowlisted path to
remove a retired key's line from the VM `.env` (issue #12938 hit exactly
this refusal trying to retire `ARBITRATION_FANOUT_MODE` /
`ARBITRATION_FANOUT_ACCOUNTS`, both retired by E35 / #12924).

`unset-env` fills that gap with a narrower safety property: it can only
delete a key on a fixed, reviewed `RETIRABLE_KEYS` allowlist inside the
script — never a caller-supplied freeform key, however well-formed its
charset. These tests cover the positive control (an allowlisted key is
removed, everything else survives byte-for-byte, a backup lands first) and
the negative control (a charset-valid but non-allowlisted key is refused
and the file is left completely untouched).

These run with `REPO_DIR` pointed at a tmp dir and `systemctl` shimmed, so
nothing touches a real VM.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "ops" / "unset_env.sh"


def _run(tmp_path: Path, *, key: str = "ARBITRATION_FANOUT_MODE",
         service: str = "none", env_file: str | None = None,
         systemctl_log: Path | None = None):
    """Invoke the script against a tmp REPO_DIR with systemctl shimmed."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    shim = bin_dir / "systemctl"
    if systemctl_log is not None:
        # Records every invocation (one line per call) so a test can assert
        # whether a restart was actually attempted. Reports "active" for
        # `is-active` so the script's post-restart poll succeeds immediately.
        shim.write_text(
            "#!/usr/bin/env bash\n"
            f"echo \"$*\" >> {systemctl_log}\n"
            "if [ \"$1\" = \"is-active\" ]; then echo active; fi\n"
            "exit 0\n"
        )
    else:
        shim.write_text("#!/usr/bin/env bash\nexit 0\n")
    shim.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["REPO_DIR"] = str(tmp_path)
    env["ENV_KEY"] = key
    env["ENV_SERVICE"] = service
    if env_file is not None:
        env["ENV_FILE_TARGET"] = env_file
    else:
        env.pop("ENV_FILE_TARGET", None)
    return subprocess.run(["bash", str(_SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=60)


def test_removes_only_the_target_key_and_backs_up_first(tmp_path: Path) -> None:
    shared = tmp_path / ".env"
    shared.write_text("ARBITRATION_FANOUT_MODE=apply\nOTHER=1\n# a comment\n")
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    body = shared.read_text()
    assert "ARBITRATION_FANOUT_MODE" not in body
    assert "OTHER=1" in body and "# a comment" in body

    backups = list(tmp_path.glob(".env.bak.*"))
    assert len(backups) == 1
    assert "ARBITRATION_FANOUT_MODE=apply" in backups[0].read_text()
    assert oct(backups[0].stat().st_mode)[-3:] == "600"


def test_export_prefixed_line_is_also_removed(tmp_path: Path) -> None:
    shared = tmp_path / ".env"
    shared.write_text("export ARBITRATION_FANOUT_MODE=apply\nOTHER=1\n")
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    body = shared.read_text()
    assert "ARBITRATION_FANOUT_MODE" not in body
    assert "OTHER=1" in body


def test_similarly_prefixed_key_is_left_untouched(tmp_path: Path) -> None:
    """THE 'other lines untouched' control. A substring match on the key
    would also delete `ARBITRATION_FANOUT_MODE_X`, which is a DIFFERENT
    key. Byte-for-byte preservation of everything else is the whole point.
    """
    shared = tmp_path / ".env"
    original = (
        "ARBITRATION_FANOUT_MODE=apply\n"
        "ARBITRATION_FANOUT_MODE_X=keep-me\n"
        "# ARBITRATION_FANOUT_MODE=commented-out-keep-me\n"
        "OTHER=1\n"
    )
    shared.write_text(original)
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    body = shared.read_text()
    assert "ARBITRATION_FANOUT_MODE_X=keep-me" in body
    assert "# ARBITRATION_FANOUT_MODE=commented-out-keep-me" in body
    assert "OTHER=1" in body
    assert "\nARBITRATION_FANOUT_MODE=apply\n" not in body


def test_absent_key_is_a_clean_noop(tmp_path: Path) -> None:
    shared = tmp_path / ".env"
    shared.write_text("OTHER=1\n")
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert shared.read_text() == "OTHER=1\n"
    assert list(tmp_path.glob(".env.bak.*")) == []


def test_non_allowlisted_key_is_refused_and_writes_NOTHING(tmp_path: Path) -> None:
    """THE negative control. A charset-valid key that is not on
    `RETIRABLE_KEYS` must be refused, not silently accepted — a freeform
    delete could remove a credential and crashloop the trader, exactly the
    failure class `set-env`'s empty-value guard prevents on the write side.
    """
    shared = tmp_path / ".env"
    original = "TELEGRAM_BOT_TOKEN=super-secret-value\nOTHER=1\n"
    shared.write_text(original)
    proc = _run(tmp_path, key="TELEGRAM_BOT_TOKEN")
    assert proc.returncode != 0, "non-allowlisted key was accepted"
    assert shared.read_text() == original, "the file was written anyway"
    assert list(tmp_path.glob(".env.bak.*")) == [], "a backup was made for a refused run"


def test_invalid_charset_key_is_refused(tmp_path: Path) -> None:
    shared = tmp_path / ".env"
    shared.write_text("OTHER=1\n")
    proc = _run(tmp_path, key="lowercase_bad")
    assert proc.returncode != 0
    assert shared.read_text() == "OTHER=1\n"


@pytest.mark.parametrize("bogus", [
    "sharedd", "SHARED", "web_api", "..", "/etc/passwd",
    "/home/ubuntu/ict-trading-bot/.env",
])
def test_an_unknown_env_file_target_errors_and_writes_NOTHING(tmp_path: Path, bogus: str) -> None:
    shared = tmp_path / ".env"
    shared.write_text("ARBITRATION_FANOUT_MODE=apply\n")
    proc = _run(tmp_path, env_file=bogus)
    assert proc.returncode != 0, f"{bogus!r} was accepted"
    assert shared.read_text() == "ARBITRATION_FANOUT_MODE=apply\n"


def test_web_api_target_resolves_to_the_scoped_path(tmp_path: Path) -> None:
    """Mirrors set_env.sh's own test: the `web-api` target must NOT resolve
    to the shared file, and a missing root-owned path fails closed.
    """
    shared = tmp_path / ".env"
    shared.write_text("ARBITRATION_FANOUT_MODE=apply\n")
    proc = _run(tmp_path, env_file="web-api")
    assert proc.returncode != 0
    assert shared.read_text() == "ARBITRATION_FANOUT_MODE=apply\n"


def test_restart_is_skipped_when_the_key_was_never_present(tmp_path: Path) -> None:
    shared = tmp_path / ".env"
    shared.write_text("OTHER=1\n")
    log = tmp_path / "systemctl.log"
    proc = _run(tmp_path, service="ict-trader-live", systemctl_log=log)
    assert proc.returncode == 0, proc.stderr
    assert not log.exists() or log.read_text() == ""


def test_restart_happens_when_the_key_is_removed(tmp_path: Path) -> None:
    shared = tmp_path / ".env"
    shared.write_text("ARBITRATION_FANOUT_MODE=apply\n")
    log = tmp_path / "systemctl.log"
    proc = _run(tmp_path, service="ict-trader-live", systemctl_log=log)
    assert proc.returncode == 0, proc.stderr
    assert log.exists()
    assert "restart ict-trader-live.service" in log.read_text()


def test_idempotent_second_run_is_also_a_clean_noop(tmp_path: Path) -> None:
    shared = tmp_path / ".env"
    shared.write_text("ARBITRATION_FANOUT_MODE=apply\nOTHER=1\n")
    assert _run(tmp_path).returncode == 0
    first_backups = list(tmp_path.glob(".env.bak.*"))
    assert len(first_backups) == 1

    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert shared.read_text() == "OTHER=1\n"
    # No second backup — the second run found nothing to remove.
    assert list(tmp_path.glob(".env.bak.*")) == first_backups
