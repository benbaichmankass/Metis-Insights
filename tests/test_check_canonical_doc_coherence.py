"""FIX-CA-32 (CA-B09-third-execution-gate-undocumented): unit test for the
new `check_third_gate_documented` rule in
`scripts/ci/check_canonical_doc_coherence.py`.

The finding: `src/core/coordinator.py` folds `config/account_state.yaml`
into `effective_dry` via `account_state_dry_run()`, while CLAUDE.md and
CLAUDE-RULES-CANONICAL.md both asserted "exactly two ... and no third" gate
with no mention of it. This test proves the guard catches a doc that omits
the third-gate mention, and stays clean once both docs name it.
"""
from __future__ import annotations

import scripts.ci.check_canonical_doc_coherence as guard


def test_flags_when_doc_omits_the_third_gate(tmp_path, monkeypatch):
    src = tmp_path / "coordinator.py"
    src.write_text("state_dry = account_state_dry_run(account.name)\n", encoding="utf-8")
    silent_doc = tmp_path / "CLAUDE.md"
    silent_doc.write_text("There are exactly two execution gates.\n", encoding="utf-8")
    named_doc = tmp_path / "CLAUDE-RULES-CANONICAL.md"
    named_doc.write_text(
        "config/account_state.yaml is a third, dry-only gate input.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "_THIRD_GATE_CODE_SOURCE", "coordinator.py")
    monkeypatch.setattr(guard, "_THIRD_GATE_DOCS", ("CLAUDE.md", "CLAUDE-RULES-CANONICAL.md"))

    fails = guard.check_third_gate_documented()
    assert len(fails) == 1
    assert "CLAUDE.md" in fails[0]
    assert "CLAUDE-RULES-CANONICAL.md" not in fails[0]


def test_clean_when_both_docs_name_the_third_gate(tmp_path, monkeypatch):
    src = tmp_path / "coordinator.py"
    src.write_text("state_dry = account_state_dry_run(account.name)\n", encoding="utf-8")
    for name in ("CLAUDE.md", "CLAUDE-RULES-CANONICAL.md"):
        (tmp_path / name).write_text(
            "config/account_state.yaml folds into effective_dry.\n", encoding="utf-8",
        )

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "_THIRD_GATE_CODE_SOURCE", "coordinator.py")
    monkeypatch.setattr(guard, "_THIRD_GATE_DOCS", ("CLAUDE.md", "CLAUDE-RULES-CANONICAL.md"))

    assert guard.check_third_gate_documented() == []


def test_clean_when_code_no_longer_folds_the_third_gate(tmp_path, monkeypatch):
    """JC-CA-06 resolved as 'retire': the fold is gone, nothing to document."""
    src = tmp_path / "coordinator.py"
    src.write_text("effective_dry = account_dry\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("no mention here\n", encoding="utf-8")
    (tmp_path / "CLAUDE-RULES-CANONICAL.md").write_text("no mention here\n", encoding="utf-8")

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "_THIRD_GATE_CODE_SOURCE", "coordinator.py")
    monkeypatch.setattr(guard, "_THIRD_GATE_DOCS", ("CLAUDE.md", "CLAUDE-RULES-CANONICAL.md"))

    assert guard.check_third_gate_documented() == []


def test_repo_state_passes_today() -> None:
    """The real repo, after FIX-CA-32's doc edits, must be clean. Must fail
    on current main (reproduced: coordinator.py folds it and neither doc
    named it before this fix)."""
    assert guard.check_third_gate_documented() == []
