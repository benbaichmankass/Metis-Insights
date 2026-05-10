"""S-067 follow-up #4 — unit tests for ``scripts/check_env_gate_in_diff.py``.

Mirrors ``tests/test_check_silent_empty_in_diff.py`` shape: synthetic
unified-diff fixtures cover (a) every offending pattern, (b) every
counter-pattern that should NOT fire, (c) the override / scope-
exclusion paths.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import scripts.check_env_gate_in_diff as guard


def _diff(path: str, hunk: str, *, start_line: int = 10) -> str:
    body = "\n".join(hunk.splitlines())
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -{start_line},2 +{start_line},6 @@\n"
        f"{body}\n"
    )


# ---------------------------------------------------------------------------
# Positive cases — the guard MUST flag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_name",
    [
        "MULTI_ACCOUNT_DISPATCH",
        "MULTI_ACCOUNT_FALLBACK",
        "MONITOR_RECONCILE_ENABLED",
        "MONITOR_APPLY_TO_EXCHANGE",  # the BUG-039-removed pattern
        "DISPATCH_FALLBACK",
        "FOO_APPLY_TO_BAR",
        "FOO_DRY_BAR",
        "RUNTIME_DRY_RUN_ENABLED",
        "FEATURE_ENABLED",
        "FEATURE_DISABLED",
    ],
)
def test_flags_suspect_env_name_in_protected_path(env_name: str) -> None:
    diff = _diff(
        "src/runtime/pipeline.py",
        f'+    raw = os.environ.get("{env_name}", "false")\n',
    )
    findings = guard.scan_diff(diff)
    assert len(findings) == 1
    assert env_name in findings[0]


@pytest.mark.parametrize(
    "path",
    [
        "src/runtime/orders.py",
        "src/runtime/order_monitor.py",
        "src/units/accounts/__init__.py",
        "src/web/api/routers/dashboard.py",
    ],
)
def test_flags_in_each_protected_prefix(path: str) -> None:
    diff = _diff(
        path,
        '+    if os.environ.get("MONITOR_FOO") == "1":\n'
        "+        do_thing()\n",
    )
    assert len(guard.scan_diff(diff)) == 1


def test_flags_subscript_form() -> None:
    diff = _diff(
        "src/runtime/pipeline.py",
        '+    raw = os.environ["MULTI_ACCOUNT_DISPATCH"]\n',
    )
    assert len(guard.scan_diff(diff)) == 1


def test_flags_getenv_form() -> None:
    diff = _diff(
        "src/runtime/pipeline.py",
        '+    raw = os.getenv("MONITOR_FOO_ENABLED")\n',
    )
    assert len(guard.scan_diff(diff)) == 1


# ---------------------------------------------------------------------------
# Negative cases — the guard MUST NOT flag
# ---------------------------------------------------------------------------


def test_does_not_flag_existing_lines() -> None:
    """Pre-existing reads (context lines) are grandfathered."""
    diff = _diff(
        "src/runtime/pipeline.py",
        ' def fn():\n'
        '     raw = os.environ.get("MONITOR_FOO_ENABLED")\n'
        "+    pass\n",
    )
    assert guard.scan_diff(diff) == []


def test_does_not_flag_unrelated_env_names() -> None:
    """Env vars whose names DON'T match the suspect patterns are
    business-as-usual feature flags."""
    diff = _diff(
        "src/runtime/pipeline.py",
        '+    api_key = os.environ.get("BYBIT_API_KEY")\n'
        '+    db_path = os.environ.get("TRADE_JOURNAL_DB")\n'
        '+    timeout = os.environ.get("HTTP_TIMEOUT")\n',
    )
    assert guard.scan_diff(diff) == []


def test_does_not_flag_outside_protected_paths() -> None:
    """A new env-gate in scripts/ or src/bot/ is out of scope."""
    diff = _diff(
        "src/bot/comms_handler.py",
        '+    if os.environ.get("MONITOR_FOO_ENABLED"):\n',
    )
    assert guard.scan_diff(diff) == []


def test_does_not_flag_test_files() -> None:
    diff = _diff(
        "tests/test_runtime_status.py",
        '+    monkeypatch.setenv("MONITOR_FOO_ENABLED", "true")\n',
    )
    assert guard.scan_diff(diff) == []


def test_does_not_flag_lines_with_allow_silent_comment() -> None:
    """Inline override silences the guard."""
    diff = _diff(
        "src/runtime/pipeline.py",
        '+    raw = os.environ.get("MULTI_ACCOUNT_DISPATCH")  # allow-silent: documented in env-gate-purge-2026-05-10.md\n',
    )
    assert guard.scan_diff(diff) == []


def test_does_not_flag_self_referencing_lint_script() -> None:
    """The lint script's own source contains the patterns it scans
    for. The ignore regex must filter it out."""
    diff = _diff(
        "scripts/check_env_gate_in_diff.py",
        '+    raw = os.environ.get("MONITOR_FOO_ENABLED")\n',
    )
    assert guard.scan_diff(diff) == []


def test_does_not_flag_silent_empty_companion_script() -> None:
    """Sibling lint scripts under scripts/lint/ are also exempt."""
    diff = _diff(
        "scripts/check_silent_empty_in_diff.py",
        '+    raw = os.environ.get("MONITOR_FOO_ENABLED")\n',
    )
    assert guard.scan_diff(diff) == []


def test_does_not_flag_docs() -> None:
    diff = _diff(
        "docs/audits/env-gate-purge-2026-05-10.md",
        "+    `os.environ.get(\"MONITOR_FOO_ENABLED\")` is documented as a survivor.\n",
    )
    assert guard.scan_diff(diff) == []


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class _StringIOLike:
    def __init__(self, s):
        self._s = s

    def read(self):
        return self._s


def test_main_returns_0_when_clean(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    diff = _diff("src/runtime/pipeline.py", " pass\n+# benign\n")
    monkeypatch.setattr("sys.stdin", _StringIOLike(diff))
    rc = guard.main(["check_env_gate_in_diff.py"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "clean" in out


def test_main_returns_1_on_hit(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    diff = _diff(
        "src/runtime/pipeline.py",
        '+    raw = os.environ.get("MONITOR_FOO_ENABLED")\n',
    )
    monkeypatch.setattr("sys.stdin", _StringIOLike(diff))
    rc = guard.main(["check_env_gate_in_diff.py"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "ENV_GATE_GUARD\t" in captured.out
    assert "ENV-GATE GUARD" in captured.err


def test_main_reads_diff_from_argv_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    diff = _diff(
        "src/runtime/pipeline.py",
        '+    raw = os.environ.get("MONITOR_FOO_ENABLED")\n',
    )
    path = tmp_path / "pr.diff"
    path.write_text(diff, encoding="utf-8")
    rc = guard.main(["check_env_gate_in_diff.py", str(path)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "ENV_GATE_GUARD\t" in out
