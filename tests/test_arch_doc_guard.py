"""Tests for `scripts/arch_doc_guard.py` (S-AI-WS10).

The guard script ALWAYS exits 0; the assertion surface is the
``::warning`` annotation printed to stdout. These tests verify the
classification logic, the warning is emitted when (and only when)
the heuristic fires, and that the script is robust to edge
inputs.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

# Make `scripts` importable as a top-level package for the test
# session; the repo doesn't ship a package __init__ there.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from scripts import arch_doc_guard  # noqa: E402


class TestClassify:
    def test_high_impact_path_only(self):
        hi, doc = arch_doc_guard.classify([
            "src/runtime/pipeline.py",
        ])
        assert hi == ["src/runtime/pipeline.py"]
        assert doc == []

    def test_arch_doc_path_only(self):
        hi, doc = arch_doc_guard.classify([
            "docs/ARCHITECTURE-CANONICAL.md",
        ])
        assert hi == []
        assert doc == ["docs/ARCHITECTURE-CANONICAL.md"]

    def test_both(self):
        hi, doc = arch_doc_guard.classify([
            "src/core/coordinator.py",
            "docs/ARCHITECTURE-CANONICAL.md",
        ])
        assert "src/core/coordinator.py" in hi
        assert "docs/ARCHITECTURE-CANONICAL.md" in doc

    def test_neither(self):
        hi, doc = arch_doc_guard.classify([
            "tests/test_something.py",
            "README.md",
        ])
        assert hi == []
        assert doc == []

    def test_blank_lines_skipped(self):
        hi, doc = arch_doc_guard.classify(["", "  ", "src/runtime/pipeline.py"])
        assert hi == ["src/runtime/pipeline.py"]

    def test_strategy_module(self):
        hi, _ = arch_doc_guard.classify(["src/units/strategies/vwap.py"])
        assert "src/units/strategies/vwap.py" in hi

    def test_ml_predictor_module(self):
        hi, _ = arch_doc_guard.classify(["ml/predictors/shadow.py"])
        assert "ml/predictors/shadow.py" in hi

    def test_config_strategies_yaml(self):
        hi, _ = arch_doc_guard.classify(["config/strategies.yaml"])
        assert "config/strategies.yaml" in hi

    def test_ai_model_platform_doc(self):
        _, doc = arch_doc_guard.classify([
            "docs/architecture/ai-model-platform.md",
        ])
        assert "docs/architecture/ai-model-platform.md" in doc

    def test_claude_rules_doc(self):
        _, doc = arch_doc_guard.classify([
            "docs/CLAUDE-RULES-CANONICAL.md",
        ])
        assert "docs/CLAUDE-RULES-CANONICAL.md" in doc


class TestFormatWarning:
    def test_short_list_inline(self):
        msg = arch_doc_guard.format_warning(["a.py", "b.py"])
        assert "::warning" in msg
        assert "a.py, b.py" in msg
        assert "more)" not in msg

    def test_long_list_truncated(self):
        files = [f"f{i}.py" for i in range(8)]
        msg = arch_doc_guard.format_warning(files)
        assert "+3 more" in msg
        # Only the first 5 names should appear inline.
        assert "f0.py" in msg
        assert "f4.py" in msg
        assert "f5.py" not in msg

    def test_references_checklist_doc(self):
        msg = arch_doc_guard.format_warning(["a.py"])
        assert "ARCHITECTURE-CHANGE-CHECKLIST.md" in msg


def _capture(args: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        rc = arch_doc_guard.main(args)
    finally:
        sys.stdout = saved
    return rc, buf.getvalue()


class TestMain:
    def test_always_exits_zero(self):
        rc, _ = _capture(["--changed-files", "src/runtime/pipeline.py"])
        assert rc == 0

    def test_emits_warning_when_high_impact_without_doc(self):
        rc, out = _capture(["--changed-files", "src/runtime/pipeline.py"])
        assert rc == 0
        assert "::warning" in out
        assert "src/runtime/pipeline.py" in out

    def test_silent_when_arch_doc_also_changed(self):
        rc, out = _capture([
            "--changed-files",
            "src/runtime/pipeline.py docs/ARCHITECTURE-CANONICAL.md",
        ])
        assert rc == 0
        assert "::warning" not in out

    def test_silent_when_no_high_impact_paths(self):
        rc, out = _capture(["--changed-files", "tests/test_x.py README.md"])
        assert rc == 0
        assert "::warning" not in out

    def test_empty_input(self):
        rc, out = _capture(["--changed-files", ""])
        assert rc == 0
        assert "::warning" not in out

    def test_stdin_input(self, monkeypatch):
        monkeypatch.setattr(
            sys, "stdin",
            io.StringIO("src/units/strategies/vwap.py\n"),
        )
        rc, out = _capture([])
        assert rc == 0
        assert "::warning" in out
        assert "vwap.py" in out
