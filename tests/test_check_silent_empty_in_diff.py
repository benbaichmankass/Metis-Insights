"""S-067 CP-4 — unit tests for ``scripts/check_silent_empty_in_diff.py``.

The guard is a regex-over-added-lines scan, so the test surface is
(a) every offending pattern, (b) every legitimate counter-pattern that
should NOT fire, (c) every override / scope-exclusion path. The fixture
is a synthetic unified-diff string per case; no on-disk repo state is
required.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import scripts.check_silent_empty_in_diff as guard


def _diff(path: str, hunk: str, *, start_line: int = 10) -> str:
    """Compose a minimal valid unified diff with one hunk.

    *hunk* is the body lines (one per line; prefix `+` for added,
    `-` for removed, ` ` for context).
    """
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
    "path",
    [
        "src/web/api/routers/dashboard.py",
        "src/web/api/routers/diag.py",
        "src/units/db/database.py",
        "src/web/runtime_status.py",
        # S-067 follow-up #8: hourly_report + boot_audit added to
        # _PROTECTED_FILES; pin the extension here.
        "src/runtime/hourly_report.py",
        "src/runtime/boot_audit.py",
    ],
)
def test_flags_broad_exception_in_protected_path(path: str) -> None:
    diff = _diff(
        path,
        " def handler():\n"
        "     try:\n"
        "         do_thing()\n"
        "+    except Exception:\n"
        "+        return []\n",
    )
    findings = guard.scan_diff(diff)
    assert len(findings) == 1
    assert path in findings[0]
    assert "broad except" in findings[0]


def test_flags_sqlite3_error_in_protected_path() -> None:
    diff = _diff(
        "src/web/api/routers/dashboard.py",
        " def fn():\n"
        "     try:\n"
        "         conn.execute('select 1')\n"
        "+    except sqlite3.Error:\n"
        "+        return []\n",
    )
    findings = guard.scan_diff(diff)
    assert len(findings) == 1
    assert "sqlite3.Error" in findings[0] or "broad except" in findings[0]


def test_flags_bare_except_in_protected_path() -> None:
    diff = _diff(
        "src/web/api/routers/diag.py",
        "+    except:\n"
        "+        return None\n",
    )
    findings = guard.scan_diff(diff)
    assert len(findings) == 1
    assert "bare except" in findings[0]


def test_flags_tuple_except_containing_exception() -> None:
    diff = _diff(
        "src/web/api/routers/diag.py",
        "+    except (OSError, Exception):\n"
        "+        return {}\n",
    )
    findings = guard.scan_diff(diff)
    assert len(findings) == 1
    assert "tuple form" in findings[0]


# ---------------------------------------------------------------------------
# Negative cases — the guard MUST NOT flag
# ---------------------------------------------------------------------------


def test_does_not_flag_existing_lines() -> None:
    """Pre-existing handlers (context lines, not added) are
    grandfathered — the diff scanner only inspects + lines."""
    diff = _diff(
        "src/web/api/routers/dashboard.py",
        " def fn():\n"
        "     try:\n"
        "         do_thing()\n"
        "     except Exception:\n"
        "         return []\n"
        "+    pass\n",
    )
    assert guard.scan_diff(diff) == []


def test_does_not_flag_narrow_exception_types() -> None:
    """Narrow types (sqlite3.OperationalError, OSError on its own,
    json.JSONDecodeError) catch a specific failure mode and are fine."""
    diff = _diff(
        "src/web/api/routers/diag.py",
        "+    except sqlite3.OperationalError:\n"
        "+        return None\n"
        "+    except OSError:\n"
        "+        return []\n"
        "+    except json.JSONDecodeError:\n"
        "+        return None\n",
    )
    assert guard.scan_diff(diff) == []


def test_does_not_flag_outside_protected_paths() -> None:
    """Live-order-path / strategy / runtime files outside the
    audit's scope are not protected here. (The TODO follow-up is
    a Tier-2 sprint with operator ack per fix.)"""
    diff = _diff(
        "src/runtime/orders.py",
        "+    except Exception:\n"
        "+        return []\n",
    )
    assert guard.scan_diff(diff) == []


def test_does_not_flag_test_files() -> None:
    """Tests legitimately exercise the swallow path and are excluded."""
    diff = _diff(
        "tests/test_dashboard_data_contract.py",
        "+    except Exception:\n"
        "+        return []\n",
    )
    assert guard.scan_diff(diff) == []


def test_does_not_flag_lines_with_allow_silent_comment() -> None:
    """The explicit override path: an inline `# allow-silent: <reason>`
    comment on the except line silences the guard."""
    diff = _diff(
        "src/web/api/routers/dashboard.py",
        "+    except Exception:  # allow-silent: psutil sample is best-effort\n"
        "+        return None\n",
    )
    assert guard.scan_diff(diff) == []


def test_does_not_flag_self_referencing_lint_script() -> None:
    """The scripts/check_silent_empty_in_diff.py source contains the
    very patterns it scans for. The ignore regex must filter it out."""
    diff = _diff(
        "scripts/check_silent_empty_in_diff.py",
        "+    except Exception:\n"
        "+        return []\n",
    )
    assert guard.scan_diff(diff) == []


def test_does_not_flag_docs() -> None:
    diff = _diff(
        "docs/audits/silent-empty-2026-05-10.md",
        "+    except Exception: return []   # bad pattern, see audit\n",
    )
    assert guard.scan_diff(diff) == []


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_main_returns_0_when_clean(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    diff = _diff(
        "src/web/api/routers/dashboard.py",
        " pass\n+# benign added line\n",
    )
    monkeypatch.setattr("sys.stdin", _StringIOLike(diff))
    rc = guard.main(["check_silent_empty_in_diff.py"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "clean" in out


def test_main_returns_1_on_hit(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    diff = _diff(
        "src/web/api/routers/dashboard.py",
        "+    except Exception:\n"
        "+        return []\n",
    )
    monkeypatch.setattr("sys.stdin", _StringIOLike(diff))
    rc = guard.main(["check_silent_empty_in_diff.py"])
    assert rc == 1
    captured = capsys.readouterr()
    # Stdout carries the machine-parseable line; stderr carries the
    # human-readable warning block.
    assert "SILENT_EMPTY_GUARD\t" in captured.out
    assert "SILENT-EMPTY GUARD" in captured.err


def test_main_reads_diff_from_argv_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    diff = _diff(
        "src/web/api/routers/dashboard.py",
        "+    except Exception:\n"
        "+        return []\n",
    )
    path = tmp_path / "pr.diff"
    path.write_text(diff, encoding="utf-8")
    rc = guard.main(["check_silent_empty_in_diff.py", str(path)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "SILENT_EMPTY_GUARD\t" in out


class _StringIOLike:
    """Tiny stand-in for ``sys.stdin`` because monkeypatching the real
    one breaks pytest's capture machinery on some platforms."""

    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text
