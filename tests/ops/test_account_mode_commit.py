"""scripts/ops/account_mode_commit.py — the durable set-account-mode (JC-CA-01).

The edit must touch exactly one `mode:` line, carry the operator marker that
scripts/check_dry_run_in_diff.py requires on a demotion, produce a Tier-3 HOLD
declaration that check_pr_landing.py accepts, and `mode_changes` must report
only real parsed-mode changes (a comment edit dispatches no verification).
"""
from __future__ import annotations

import difflib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ops"))
import account_mode_commit as amc  # noqa: E402

REAL = (REPO / "config" / "accounts.yaml").read_text(encoding="utf-8")

TOY = """accounts:
  acct_a:
    exchange: bybit
    mode: live                         # original comment
                                       # continuation comment
    risk_pct: 0.5
  acct_b:
    exchange: bybit
    mode: dry_run
"""


def _diff(before: str, after: str) -> list[str]:
    return [ln for ln in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="")
            if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))]


def test_edit_touches_exactly_one_line_of_the_named_account():
    new, pre = amc.edit_mode(TOY, "acct_a", "dry_run", amc.marker_for("dry_run", "1", "why"))
    assert pre == "live"
    changed = _diff(TOY, new)
    assert len(changed) == 2, changed
    assert changed[0].strip() == "-    mode: live                         # original comment"
    assert changed[1].startswith("+    mode: dry_run  # mode-guard: allow")
    assert amc.read_mode(new, "acct_b") == "dry_run"  # the neighbour is untouched
    assert "continuation comment" in new


def test_edit_on_the_real_accounts_yaml_changes_one_line():
    new, pre = amc.edit_mode(REAL, "bybit_2", "dry_run", amc.marker_for("dry_run", "1", "x"))
    assert pre == "live"
    assert len(_diff(REAL, new)) == 2
    assert amc.read_mode(new, "bybit_2") == "dry_run"
    assert amc.read_mode(new, "bybit_1") == amc.read_mode(REAL, "bybit_1")


@pytest.mark.parametrize("marker_mode, expect_rc", [("dry_run", 0), ("unmarked", 1)])
def test_demotion_marker_satisfies_the_dry_run_guard_and_its_absence_does_not(
        tmp_path, marker_mode, expect_rc):
    marker = (amc.marker_for("dry_run", "1", "operator asked")
              if marker_mode == "dry_run" else "set-account-mode run 1: no marker")
    new, _ = amc.edit_mode(REAL, "bybit_2", "dry_run", marker)
    diff = "".join(difflib.unified_diff(REAL.splitlines(True), new.splitlines(True),
                                        "a/config/accounts.yaml", "b/config/accounts.yaml"))
    p = tmp_path / "d.diff"
    p.write_text(diff)
    rc = subprocess.run([sys.executable, str(REPO / "scripts" / "check_dry_run_in_diff.py"), str(p)],
                        capture_output=True, text=True).returncode
    assert rc == expect_rc


def test_promotion_carries_no_allow_marker():
    assert "allow" not in amc.marker_for("live", "1", "x")
    assert amc.marker_for("dry_run", "1", "x").startswith("mode-guard: allow")


@pytest.mark.parametrize("account, mode, exc", [
    ("nope", "live", LookupError),
    ("acct_a", "paper", ValueError),
    ("acct a", "live", ValueError),
])
def test_bad_input_raises(account, mode, exc):
    with pytest.raises(exc):
        amc.edit_mode(TOY, account, mode, "m")


def test_marker_cannot_inject_a_line():
    with pytest.raises(ValueError):
        amc.edit_mode(TOY, "acct_a", "live", "x\n    mode: dry_run")
    # reason text is collapsed to one line before it reaches the marker
    assert "\n" not in amc.marker_for("dry_run", "1", "a\nb\r\nc")


def test_declaration_is_a_tier3_hold():
    d = amc.declaration("bybit_2", "dry_run", "live", "9", "why", "12")
    assert d["tier"] == 3 and d["landing"] == "hold"
    assert d["hold_reason"] == "tier_2_3_needs_approval"
    assert "issue #12" in d["hold_text"]


def test_mode_changes_reports_real_changes_only():
    flipped, _ = amc.edit_mode(TOY, "acct_a", "dry_run", "m")
    assert amc.mode_changes(TOY, flipped) == [
        {"account": "acct_a", "before": "live", "after": "dry_run"}]
    comment_only = TOY.replace("# original comment", "# edited comment")
    assert amc.mode_changes(TOY, comment_only) == []
    assert amc.mode_changes(TOY, TOY) == []


def test_prepare_cli_writes_edit_and_declaration_and_is_idempotent(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "accounts.yaml").write_text(TOY)
    args = ["--root", str(tmp_path), "prepare", "--account", "acct_a", "--mode", "dry_run",
            "--run-id", "77", "--branch", "automation/set-account-mode-77", "--reason", "r"]
    assert amc.main(args) == 0
    assert amc.read_mode((tmp_path / "config" / "accounts.yaml").read_text(), "acct_a") == "dry_run"
    decl = json.loads((tmp_path / ".github" / "pr-landing"
                       / "automation-set-account-mode-77.json").read_text())
    assert decl["landing"] == "hold"
    assert amc.main(args) == 3  # already at that mode: no-op, nothing to commit
