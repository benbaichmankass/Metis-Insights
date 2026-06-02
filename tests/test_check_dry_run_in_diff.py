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


def test_yaml_dry_run_true_is_not_caught():
    # The guard was tightened to avoid false positives on Python kwargs
    # (dry_run=dry_run patterns). Only the canonical account-mode toggle
    # `mode: dry_run` (in accounts.yaml) and the uppercase env-var forms
    # DRY_RUN=true / ALLOW_LIVE_TRADING=false are flagged. A lowercase
    # `dry_run: true` in an arbitrary YAML file is intentionally ignored.
    findings = scan_diff(_diff("config/services.yaml", ["  dry_run: true"]))
    assert len(findings) == 0


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


# ---------------------------------------------------------------------------
# Strategy-level demotion: execution: shadow (operator directive 2026-06-02 —
# Claude must never set a thing to shadow/dry_run without explicit permission).
# This is the gap that silently stranded the MES sleeve on the IBKR paper
# account (shipped execution: shadow, generated signals but never traded).
# ---------------------------------------------------------------------------
def test_execution_shadow_is_caught():
    findings = scan_diff(_diff("config/strategies.yaml", ["    execution: shadow"]))
    assert len(findings) == 1
    assert "shadow" in findings[0]


def test_execution_shadow_with_shadow_guard_marker_exempt():
    """A deliberate, operator-approved shadow line carrying the allow marker
    is exempt — the marker records the explicit permission in the diff."""
    findings = scan_diff(_diff(
        "config/strategies.yaml",
        ["    execution: shadow   # shadow-guard: allow — operator-approved shadow A/B"],
    ))
    assert findings == []


def test_execution_shadow_with_dry_run_guard_marker_also_exempt():
    """Either marker name satisfies the override (broadened allow regex)."""
    findings = scan_diff(_diff(
        "config/strategies.yaml",
        ["    execution: shadow   # dry-run-guard: allow — held data-only, approved"],
    ))
    assert findings == []


def test_execution_live_is_not_caught():
    """Promoting TO live (the permissive direction) never fires the guard."""
    assert scan_diff(_diff("config/strategies.yaml", ["    execution: live"])) == []


def test_execution_shadow_in_tests_is_ignored():
    """Tests may set execution: shadow to exercise the data-only path."""
    assert scan_diff(_diff("tests/test_foo.py", ["    execution: shadow"])) == []


def test_execution_shadow_substring_in_prose_not_caught():
    """A comment/description mentioning 'execution: shadow' mid-line (not at the
    start of the line) must NOT trip the guard — only an actual YAML field does."""
    findings = scan_diff(_diff(
        "config/strategy_changelog.json",
        ['   "summary": "Wired execution: shadow then promoted to live."'],
    ))
    assert findings == []
