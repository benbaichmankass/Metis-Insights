"""Undefined names in python-in-shell heredocs must fail CI, not the trainer.

WHY THIS EXISTS
---------------
2026-08-20: PR #10059 added a disk-usage block to the python heredoc inside
``scripts/ops/publish_trainer_mirror.sh``. The heredoc receives its paths
positionally and binds them **lowercase** (``repo_root``); the new block used
the **shell** variable name ``REPO_ROOT``. That is a plain ``NameError``, and
the first statement using it sits OUTSIDE the surrounding ``try``, so the guard
below it never ran::

    NameError: name 'REPO_ROOT' is not defined
    {"ts":"2026-08-20T15:55:16+00:00","status":"status_build_failed"}

Every 2-minute publish failed from ~14:50Z onward. The live VM kept serving the
last good ``trainer_status.json``, so ``/api/bot/ml/status`` degraded to a stale
mirror and the ``trainer_down`` banner fired — for a trainer that was **up**
(36 days uptime, load 0.00, git HEAD current). The publisher was the casualty,
not the VM.

⚠️ **THE STALE MIRROR MISDIAGNOSES ITSELF.** Two consecutive check-ins read
``status.trainer_vm.head_sha`` (``33ccda09``, one commit behind the disk PR) and
concluded "the trainer has not pulled the writer yet". That field lives INSIDE
the payload that stopped being written, so it necessarily reports the SHA as of
the last SUCCESSFUL publish — reading it to explain why publishing stopped is
circular. The trainer was in fact on ``4ff5f5ce``, i.e. it HAD the writer and the
writer was crashing. Never diagnose a stale feed from a field the feed carries.

WHY THE #10059 TESTS DID NOT CATCH IT
-------------------------------------
``tests/test_trainer_disk_visibility.py`` covers the *consumer* — the banner in
``src/web/api/routers/notifications.py`` — against synthetic payloads, and the
shell script's *shape* by grepping its text. **Nothing executed the heredoc.**
Python embedded in a shell string is invisible to pytest, ruff, and every guard
in ``scripts/ci/``, because none of them see it as Python. That is the actual
gap, and it is wider than one script.

WHAT THIS ASSERTS
-----------------
Extract every quoted python heredoc from ``scripts/ops/*.sh`` and run ruff's
undefined-name rules (F821/F822) over each one. A quoted delimiter (``<<'PY'``)
means the shell performs NO expansion, so the captured text is exactly the
program python receives — it can be analysed verbatim.

Deliberately scoped to **undefined names**, not general lint: these blocks are
written in a different style from the repo's python and a broad ruleset would
produce noise nobody acts on, which is the desensitized-alarm failure mode.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
OPS = REPO / "scripts" / "ops"

# `<<'DELIM'` / `<<"DELIM"` — a QUOTED delimiter, which is what makes the body
# analysable: the shell expands nothing, so what we read is what python runs.
# An UNQUOTED `<<DELIM` interpolates shell variables and its text is not valid
# python on its own; those are skipped rather than mis-reported.
_HEREDOC = re.compile(
    r"<<'(?P<d>[A-Za-z_][A-Za-z0-9_]*)'\n(?P<body>.*?)\n(?P=d)\n",
    re.S,
)
# Only heredocs actually fed to a python interpreter.
_PY_INVOCATION = re.compile(r"\bpython3?\b[^\n|]*<<'(?P<d>[A-Za-z_][A-Za-z0-9_]*)'")


def _python_heredocs(text: str) -> list[tuple[str, str]]:
    """Return (delimiter, body) for each heredoc piped into python."""
    py_delims = {m.group("d") for m in _PY_INVOCATION.finditer(text)}
    return [
        (m.group("d"), m.group("body"))
        for m in _HEREDOC.finditer(text)
        if m.group("d") in py_delims
    ]


def _shell_scripts() -> list[Path]:
    return sorted(OPS.glob("*.sh"))


def test_the_probe_can_find_a_positive():
    """A denominator assertion: prove extraction works before trusting a clean run.

    A regex that silently matches nothing would make this whole module a
    guaranteed pass over zero programs — the unasserted-denominator defect
    (CLAUDE.md § "Diagnostic provenance", sub-class C). So: the publisher MUST
    yield a heredoc, and that heredoc MUST contain the argv binding line the
    2026-08-20 incident turned on.
    """
    pub = OPS / "publish_trainer_mirror.sh"
    assert pub.exists(), f"{pub} is missing — this test's subject is gone"
    blocks = _python_heredocs(pub.read_text(encoding="utf-8"))
    assert blocks, "extracted 0 python heredocs from the publisher — the regex is broken"
    body = "\n".join(b for _, b in blocks)
    assert "repo_root, training_log" in body, (
        "the publisher's argv binding line is not in the extracted text; "
        "extraction is capturing the wrong region"
    )


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not installed")
def test_no_undefined_names_in_shell_embedded_python(tmp_path):
    """Every python heredoc under scripts/ops/ resolves all its names.

    This is the permanent detector for the #10059 class. Before the fix it
    reported exactly the four real sites and nothing else.
    """
    scanned = 0
    failures: list[str] = []

    for script in _shell_scripts():
        for idx, (delim, body) in enumerate(
            _python_heredocs(script.read_text(encoding="utf-8"))
        ):
            scanned += 1
            target = tmp_path / f"{script.stem}__{delim}__{idx}.py"
            target.write_text(body, encoding="utf-8")
            proc = subprocess.run(
                [
                    "ruff", "check",
                    "--select", "F821,F822",
                    "--no-cache",
                    "--output-format", "concise",
                    str(target),
                ],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                failures.append(
                    f"\n{script.relative_to(REPO)} (heredoc <<'{delim}'):\n"
                    + (proc.stdout or proc.stderr).strip()
                )

    # Never report clean over an empty population.
    assert scanned > 0, (
        "scanned 0 python heredocs under scripts/ops/ — extraction is broken, "
        "which would make this test a permanent false green"
    )

    assert not failures, (
        "python embedded in a shell heredoc references undefined name(s). "
        "This runs on a VM with no test coverage of its own — a NameError here "
        "is a silent production outage, not a lint nit "
        "(2026-08-20: it stopped the trainer mirror for ~95 min).\n"
        + "".join(failures)
    )


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not installed")
def test_the_detector_flags_a_planted_undefined_name(tmp_path):
    """Discriminating control: the check must FAIL on a known-bad block.

    Without this, a ruff invocation that silently no-ops (wrong flag, missing
    binary, changed output contract) would let the test above pass forever while
    checking nothing.
    """
    bad = tmp_path / "planted.py"
    bad.write_text("import json\nx = str(REPO_ROOT)\n", encoding="utf-8")
    proc = subprocess.run(
        ["ruff", "check", "--select", "F821,F822", "--no-cache",
         "--output-format", "concise", str(bad)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0, "ruff did not flag a planted undefined name"
    assert "REPO_ROOT" in (proc.stdout + proc.stderr)


def test_publisher_disk_block_uses_the_bound_name():
    """The specific 2026-08-20 regression, pinned by name.

    The generic detector above would catch it, but this asserts the MECHANISM:
    the heredoc binds `repo_root` from argv, so the disk block must use that and
    never the shell's `REPO_ROOT`.
    """
    body = "\n".join(
        b for _, b in _python_heredocs(
            (OPS / "publish_trainer_mirror.sh").read_text(encoding="utf-8")
        )
    )
    assert "disk = {" in body, "the disk block is gone from the publisher heredoc"
    assert "REPO_ROOT" not in body, (
        "the publisher's python heredoc references the SHELL variable REPO_ROOT; "
        "inside the heredoc the path is bound as `repo_root` from sys.argv"
    )
