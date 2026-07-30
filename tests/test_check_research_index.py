"""The research capability-index completeness check.

This guard exists because 47 of 51 `scripts/research/` tools appeared in no skill, and a
session consequently reported six live regime gates as permanently un-auditable when
`analyze_exit_head.py` had been doing exactly that job the whole time.

The load-bearing property is that an unindexed script **fails**. A checker that warned and
exited 0 would recreate the original condition — a toolbox nobody can route to — with the
added harm of looking like it was being watched.

The second load-bearing property is that the exemption list cannot become a silence list:
an entry needs a reason, and a dead entry is reported. An earlier draft of the checker
special-cased the one exemption its author had just added so it could never be flagged
stale; `test_no_entry_is_exempt_from_the_staleness_check` exists so that cannot come back.
"""
from __future__ import annotations

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "ops"))

cri = pytest.importorskip("check_research_index")


def _mk(root, scripts, index_body):
    d = root / "scripts" / "research"
    d.mkdir(parents=True, exist_ok=True)
    for s in scripts:
        (d / s).write_text("# x\n", encoding="utf-8")
    idx = root / "docs" / "research"
    idx.mkdir(parents=True, exist_ok=True)
    (idx / "RESEARCH-CAPABILITY-INDEX.md").write_text(index_body, encoding="utf-8")
    return root


class TestDetection:
    def test_unindexed_script_is_found(self, tmp_path):
        _mk(tmp_path, ["a.py", "b.py"], "routes a.py only")
        assert cri.unindexed(tmp_path) == ["b.py"]

    def test_fully_indexed_is_clean(self, tmp_path):
        _mk(tmp_path, ["a.py", "b.py"], "a.py and b.py")
        assert cri.unindexed(tmp_path) == []

    def test_exempt_script_is_not_reported(self, tmp_path, monkeypatch):
        _mk(tmp_path, ["a.py", "skipme.py"], "a.py")
        monkeypatch.setattr(cri, "EXEMPT", {"skipme.py": "a reason"})
        assert cri.unindexed(tmp_path) == []

    def test_missing_index_is_an_error_not_a_pass(self, tmp_path):
        (tmp_path / "scripts" / "research").mkdir(parents=True)
        with pytest.raises(FileNotFoundError):
            cri.unindexed(tmp_path)


class TestFailsLoudly:
    """THE property: unindexed must exit NON-ZERO."""

    def test_unindexed_exits_nonzero(self, tmp_path):
        _mk(tmp_path, ["a.py", "b.py"], "a.py")
        assert cri.main(["--repo-root", str(tmp_path)]) == 1

    def test_clean_exits_zero(self, tmp_path):
        _mk(tmp_path, ["a.py"], "a.py")
        assert cri.main(["--repo-root", str(tmp_path)]) == 0

    def test_missing_index_exits_nonzero(self, tmp_path):
        (tmp_path / "scripts" / "research").mkdir(parents=True)
        assert cri.main(["--repo-root", str(tmp_path)]) == 1


class TestExemptionDiscipline:
    def test_unexplained_exemption_is_a_failure(self, tmp_path, monkeypatch):
        _mk(tmp_path, ["a.py"], "a.py")
        monkeypatch.setattr(cri, "EXEMPT", {"a.py": ""})
        assert cri.exemption_problems()
        assert cri.main(["--repo-root", str(tmp_path)]) == 1

    def test_dead_exemption_is_reported(self, tmp_path, monkeypatch):
        _mk(tmp_path, ["a.py"], "a.py")
        monkeypatch.setattr(cri, "EXEMPT", {"gone.py": "used to exist"})
        assert cri.stale_exemptions(tmp_path) == ["gone.py"]
        assert cri.main(["--repo-root", str(tmp_path)]) == 1

    def test_no_entry_is_exempt_from_the_staleness_check(self, tmp_path, monkeypatch):
        """No special cases. An earlier draft carved out `__init__.py` so the author's own
        exemption could never be flagged stale — the 'exempt myself from my own guard'
        move that makes a guard decorative."""
        _mk(tmp_path, ["a.py"], "a.py")
        monkeypatch.setattr(cri, "EXEMPT", {"__init__.py": "package marker"})
        assert cri.stale_exemptions(tmp_path) == ["__init__.py"]


class TestThisRepo:
    def test_the_live_repo_is_fully_indexed(self):
        """Anchored on reality: adding a research script without indexing it fails here."""
        assert cri.unindexed() == []

    def test_the_incident_tools_are_routed(self):
        """The specific tools whose absence caused the 2026-07-30 error."""
        text = cri.index_text()
        for name in ("analyze_exit_head.py", "build_intrabar_exit_panel.py",
                     "regime_debt_matrix.py", "regime_cell_walkforward.py",
                     "m20_ml_exit_probe.py"):
            assert name in text, f"{name} must stay routed"

    def test_the_index_answers_the_question_that_was_gotten_wrong(self):
        """The index must state explicitly that an ML exit head IS replayable — the
        single fact whose absence cost six live gates a wrong verdict."""
        text = cri.index_text()
        assert "Replay an ML exit head offline" in text

    def test_shipped_exemptions_are_all_valid(self):
        assert cri.exemption_problems() == []
        assert cri.stale_exemptions() == []
