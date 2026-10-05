"""Tests for the venv-bootstrap logic in ``breakout_login_check_action.sh``.

Issue #13210: the wrapper's `python3 -m venv` died with "ensurepip is not
available ... install python3.10-venv", and a second run would have then
found a partial venv (`bin/python` present, no working pip) and skipped
creation entirely, both actionable without operator intervention.

These extract ``venv_pip_ok`` / ``ensure_venv`` straight out of the wrapper
(the same `sed` pattern ``test_rearm_alpaca_protective.py`` uses for
``rearm_banner``) and drive them with stub ``python3``/``sudo``/``apt-get``
scripts, so no real venv or apt state is touched.
"""
import pathlib
import stat
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
WRAPPER = REPO / "scripts" / "ops" / "breakout_login_check_action.sh"


def _extract_functions() -> str:
    out = []
    for name in ("venv_pip_ok", "ensure_venv"):
        r = subprocess.run(
            ["sed", "-n", f"/^{name}()/,/^}}/p", str(WRAPPER)],
            capture_output=True, text=True, check=True,
        )
        assert r.stdout.strip(), f"could not extract {name}() from {WRAPPER}"
        out.append(r.stdout)
    return "\n".join(out)


FUNCTIONS = _extract_functions()

STUB_PREAMBLE = """
log() { printf '[log] %s\\n' "$*" >&2; }
record_audit() { printf '[record_audit] %s %s %s\\n' "$1" "$2" "$3" >> "${AUDIT_LOG}"; }
ACCOUNT="breakout_1"
"""


def _write_stub(path: pathlib.Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run(script_body: str, tmp_path, bin_dir=None, extra_env=None):
    audit_log = tmp_path / "audit.log"
    audit_log.write_text("")
    script = tmp_path / "run.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + STUB_PREAMBLE
        + FUNCTIONS
        + "\n"
        + script_body
        + "\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    env = {"AUDIT_LOG": str(audit_log), "PATH": "/usr/bin:/bin"}
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
    if extra_env:
        env.update(extra_env)
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                        env=env, cwd=tmp_path)
    return r, audit_log.read_text()


def _fake_py3(tmp_path, *, ensurepip_ok: bool, create_pip_ok: bool = True) -> pathlib.Path:
    """A stand-in for `${PY3}` covering the three call shapes ensure_venv makes."""
    ensurepip_branch = "exit 0" if ensurepip_ok else "exit 1"
    pip_touch = (
        'mkdir -p "$VENV_ARG/bin"; '
        'printf "#!/bin/sh\\nexit 0\\n" > "$VENV_ARG/bin/python"; chmod +x "$VENV_ARG/bin/python"; '
        + ('printf "#!/bin/sh\\nexit 0\\n" > "$VENV_ARG/bin/pip"; chmod +x "$VENV_ARG/bin/pip"; '
           if create_pip_ok else '')
    )
    py3 = tmp_path / "fake_python3.sh"
    _write_stub(py3, f'''
case "$*" in
  *"import ensurepip"*) {ensurepip_branch} ;;
  *"sys.version_info"*) echo "3.11" ;;
  *"-m venv"*)
    VENV_ARG="${{@: -1}}"
    {pip_touch}
    exit 0
    ;;
  *) exit 0 ;;
esac
''')
    return py3


def test_venv_pip_ok_rejects_partial_venv_missing_pip(tmp_path):
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    _write_stub(venv / "bin" / "python", "exit 0")
    r, _ = _run(f'venv_pip_ok "{venv}" && echo OK || echo BROKEN', tmp_path)
    assert "BROKEN" in r.stdout, r.stdout + r.stderr


def test_venv_pip_ok_accepts_working_venv(tmp_path):
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    _write_stub(venv / "bin" / "python", 'if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then exit 0; fi; exit 0')
    _write_stub(venv / "bin" / "pip", "exit 0")
    r, _ = _run(f'venv_pip_ok "{venv}" && echo OK || echo BROKEN', tmp_path)
    assert "OK" in r.stdout, r.stdout + r.stderr


def test_ensure_venv_recreates_a_broken_partial_venv(tmp_path):
    """bin/python present, no pip — must be wiped and rebuilt, not skipped."""
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    _write_stub(venv / "bin" / "python", "exit 0")  # no pip alongside it

    py3 = _fake_py3(tmp_path, ensurepip_ok=True)
    r, audit = _run(f'PY3="{py3}"; ensure_venv "{venv}"; venv_pip_ok "{venv}" && echo REBUILT_OK',
                     tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "REBUILT_OK" in r.stdout
    assert "Removing broken venv" in r.stderr
    assert audit == ""  # no bootstrap failure recorded


def test_ensure_venv_installs_python3_venv_when_ensurepip_missing(tmp_path):
    """The exact issue #13210 case, with a working passwordless sudo."""
    venv = tmp_path / "venv"
    py3 = _fake_py3(tmp_path, ensurepip_ok=False)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir / "sudo", 'if [ "$1" = "-n" ] && [ "$2" = "true" ]; then exit 0; fi; '
                                   'shift; "$@"')
    apt_log = tmp_path / "apt.log"
    _write_stub(bin_dir / "apt-get", f'echo "$*" >> "{apt_log}"; exit 0')

    r, audit = _run(f'PY3="{py3}"; ensure_venv "{venv}"; venv_pip_ok "{venv}" && echo OK',
                     tmp_path, bin_dir=bin_dir)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "OK" in r.stdout
    assert "install -y python3.11-venv" in apt_log.read_text()
    assert audit == ""


def test_ensure_venv_exits_5_with_clear_message_when_sudo_unavailable(tmp_path):
    """No passwordless sudo, ensurepip missing — a clear line, not a traceback."""
    venv = tmp_path / "venv"
    py3 = _fake_py3(tmp_path, ensurepip_ok=False)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir / "sudo", 'exit 1')  # `sudo -n true` fails: no passwordless sudo

    r, audit = _run(f'PY3="{py3}"; ensure_venv "{venv}"', tmp_path, bin_dir=bin_dir)
    assert r.returncode == 5, r.stdout + r.stderr
    assert "environment: python3-venv missing and cannot install" in r.stderr
    assert "Traceback" not in r.stderr
    assert "environment" in audit and "venv_bootstrap" in audit


def test_ensure_venv_exits_5_when_every_apt_package_name_fails(tmp_path):
    venv = tmp_path / "venv"
    py3 = _fake_py3(tmp_path, ensurepip_ok=False)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir / "sudo", 'if [ "$1" = "-n" ] && [ "$2" = "true" ]; then exit 0; fi; '
                                   'shift; "$@"')
    _write_stub(bin_dir / "apt-get", 'exit 100')  # every candidate package fails

    r, audit = _run(f'PY3="{py3}"; ensure_venv "{venv}"', tmp_path, bin_dir=bin_dir)
    assert r.returncode == 5, r.stdout + r.stderr
    assert "environment: python3-venv missing and cannot install" in r.stderr
    assert "environment" in audit and "venv_bootstrap" in audit


def test_ensure_venv_skips_bootstrap_when_venv_already_works(tmp_path):
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    _write_stub(venv / "bin" / "python", "exit 0")
    _write_stub(venv / "bin" / "pip", "exit 0")

    py3 = tmp_path / "should_not_run.sh"
    _write_stub(py3, 'echo "PY3 SHOULD NOT HAVE RUN" >&2; exit 1')

    r, audit = _run(f'PY3="{py3}"; ensure_venv "{venv}"', tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SHOULD NOT HAVE RUN" not in r.stderr
    assert audit == ""


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))



# ── `limit` is dry-only (review of #14737, 2026-09-30) ─────────────────────

def _mode_block():
    src = (REPO / "scripts" / "ops" / "breakout_login_check_action.sh").read_text()
    start = src.index('EXEC_MODE=""')
    end = src.index("esac", src.index('case ",${APPLY}," in *",limit,"*)')) + len("esac")
    return src[start:end]


def _run_mode_block(apply):
    script = "log() { echo \"$*\"; }\nAPPLY=%s\n%s\necho OK:${EXEC_MODE}\n" % (apply, _mode_block())
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


@pytest.mark.parametrize("apply", ["round-trip-live,sol,limit", "watched-click,limit",
                                   "executor-dry-run,limit", "limit", "close-position-live,sol,limit"])
def test_limit_with_any_mode_but_round_trip_dry_is_refused(apply):
    got = _run_mode_block(apply)
    assert got.returncode != 0 and "limit: refused" in got.stdout and "OK:" not in got.stdout


def test_limit_with_round_trip_dry_is_accepted():
    got = _run_mode_block("round-trip-dry,sol,limit")
    assert got.returncode == 0 and "OK:round-trip-dry" in got.stdout


def test_the_limit_refusal_runs_before_the_executor_tick():
    src = (REPO / "scripts" / "ops" / "breakout_login_check_action.sh").read_text()
    assert src.index("limit: refused") < src.index('log "Running prop executor')


# ── `lim-below` / `lim-above`: DRY-only limit offset (TRADEIFY-SOL-SIZE) ─────

def _offset_block():
    src = (REPO / "scripts" / "ops" / "breakout_login_check_action.sh").read_text()
    start = src.index('EXEC_MODE=""')
    marker = 'LIMIT_OFFSET=""'
    end = src.index("\nfi\n", src.index(marker)) + len("\nfi\n")
    return src[start:end]


def _run_offset_block(apply):
    script = "log() { echo \"$*\"; }\nAPPLY=%s\n%s\necho OK:${EXEC_MODE}:${LIMIT_OFFSET}\n" % (apply, _offset_block())
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


@pytest.mark.parametrize("apply,off", [("round-trip-dry,sol,limit,lim-below", "-0.5"),
                                       ("round-trip-dry,sol,limit,lim-above", "0.5")])
def test_limit_offset_with_dry_limit_round_trip_is_accepted(apply, off):
    got = _run_offset_block(apply)
    assert got.returncode == 0 and f"OK:round-trip-dry:{off}" in got.stdout


@pytest.mark.parametrize("apply", ["round-trip-live,sol,limit,lim-below", "round-trip-dry,sol,lim-above",
                                   "watched-click,lim-below", "round-trip-dry,sol,limit,lim-below,lim-above",
                                   "lim-above"])
def test_limit_offset_anywhere_else_is_refused(apply):
    got = _run_offset_block(apply)
    # refused by the offset check, or earlier by the existing `limit` check
    assert got.returncode != 0 and "refused" in got.stdout and "OK:" not in got.stdout


def test_the_offset_refusal_runs_before_the_executor_tick():
    src = (REPO / "scripts" / "ops" / "breakout_login_check_action.sh").read_text()
    assert src.index("lim-below/lim-above: refused") < src.index('log "Running prop executor')
