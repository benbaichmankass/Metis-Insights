"""Tests for the new-table wiring guard (scripts/check_new_table_wiring.py).

The guard is the mechanical backstop for § Generation Discipline Rule 3: a PR
that adds a persistent table must declare its source-of-truth relationship with
a `# data-wiring:` annotation, or the check fails.
"""
from __future__ import annotations

from scripts.check_new_table_wiring import scan_diff


def _diff(path: str, *added: str) -> str:
    body = "".join(f"+{line}\n" for line in added)
    return f"+++ b/{path}\n@@ -0,0 +1,{len(added)} @@\n{body}"


def test_new_table_without_annotation_is_flagged() -> None:
    diff = _diff("src/prop/x.py", "    CREATE TABLE IF NOT EXISTS widgets (id INTEGER)")
    findings = scan_diff(diff)
    assert len(findings) == 1
    assert "widgets" in findings[0]


def test_new_table_with_annotation_passes() -> None:
    diff = _diff(
        "src/prop/x.py",
        "    # data-wiring: canonical store for widgets — nothing else holds them",
        "    CREATE TABLE IF NOT EXISTS widgets (id INTEGER)",
    )
    assert scan_diff(diff) == []


def test_table_in_tests_is_ignored() -> None:
    diff = _diff("tests/test_x.py", "    CREATE TABLE scratch (id INTEGER)")
    assert scan_diff(diff) == []


def test_non_code_file_ignored() -> None:
    diff = _diff("docs/notes.md", "CREATE TABLE example (id INTEGER)")
    assert scan_diff(diff) == []


def test_no_create_table_is_clean() -> None:
    diff = _diff("src/prop/x.py", "    x = 1")
    assert scan_diff(diff) == []
