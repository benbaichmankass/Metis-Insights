"""Tests for scripts/check_dry_run_in_diff.py (BUG-031 PR guard)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "check_dry_run_in_diff.py"
_SPEC = importlib.util.spec_from_file_location("dry_run_guard", _SCRIPT)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["dry_run_guard"] = _MOD
_SPEC.loader.exec_module(_MOD)  # type: ignore[union-attr]
scan_diff = _MOD.scan_diff


def _diff(file: str, added: list[str]) -> str:
    """Build a tiny unified diff that adds *added* lines to *file*."""
    body = "\n".join(f"+{line}" for line in added)
    return (
        f"diff --git a/{file} b/{file}\n"
        f"--- a/{file}\n"
        f"+++ b/{file}\n"
        f"@@ -1,1 +1,{len(added) + 1} @@\n"
        f" header_context_line\n"
        f"{body}\n"
    )


def test_clean_diff_passes():
    assert scan_diff(_diff("src/runtime/orders.py", ["x = 1"])) == []


def test_dry_run_true_in_env_is_caught():
    findings = scan_diff(_diff(".env.live", ["DRY_RUN=true"]))
    assert len(findings) == 1
    assert "DRY_RUN" in findings[0]


def test_allow_live_false_is_caught():
    findings = scan_diff(_diff("config/foo.yaml", ["ALLOW_LIVE_TRADING=false"]))
    assert len(findings) == 1
    assert "ALLOW_LIVE_TRADING" in findings[0]


def test_yaml_dry_run_true_is_caught():
    findings = scan_diff(_diff("config/services.yaml", ["  dry_run: true"]))
    assert len(findings) == 1


def test_test_files_are_ignored():
    """Tests are allowed to set DRY_RUN=true to exercise the dry path."""
    assert scan_diff(_diff("tests/test_orders.py", ["DRY_RUN=true"])) == []


def test_allow_marker_exempts_dry_line():
    """A deliberate, marked dry line (new intentionally-dry account) is exempt."""
    findings = scan_diff(_diff(
        "config/accounts.yaml",
        ["    mode: dry_run   # dry-run-guard: allow — new IB acct held dry"],
    ))
    assert findings == []


def test_unmarked_dry_line_still_caught():
    """Without the marker the guard still fires (protection intact)."""
    findings = scan_diff(_diff("config/accounts.yaml", ["    mode: dry_run"]))
    assert len(findings) == 1


def test_docs_are_ignored():
    assert scan_diff(_diff("docs/foo.md", ["DRY_RUN=true"])) == []


def test_account_mode_dry_run_is_caught():
    """Per the operator directive of 2026-05-03, the canonical toggle is
    config/accounts.yaml ``mode: live | dry_run``. Adding mode: dry_run
    on an account should fire the guard."""
    findings = scan_diff(
        _diff("config/accounts.yaml", ["    mode: dry_run"])
    )
    assert len(findings) == 1


def test_paper_trading_alias_is_caught():
    findings = scan_diff(_diff(".env", ["paper_trading=true"]))
    assert len(findings) == 1
