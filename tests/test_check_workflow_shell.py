"""The workflow-shell guard — catches a malformed `run:` block before it burns a runner.

Motivating incident (2026-07-30): the `econ-event-study` re-run against real survey consensus
computed EVERY scorecard (553 natgas / 575 crude / 136 cpi releases, price_bars ~2911) and then
discarded all of it, because the summary heredoc's terminator sat at column 2. The commit step
runs after the summary step, so a reporting-side syntax error threw away real results. The file
was valid YAML throughout — nothing in CI looked at the shell inside the block.
"""
from __future__ import annotations

import os
import sys
import textwrap

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "ops"))

mod = pytest.importorskip("check_workflow_shell")
pytest.importorskip("yaml")


def _wf(tmp_path, body: str, name: str = "wf.yml"):
    d = tmp_path / "workflows"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
    return p


class TestCatchesTheRealIncident:
    def test_a_heredoc_terminator_below_column_zero_is_caught(self, tmp_path):
        """The exact 2026-07-30 shape: the heredoc is nested in a `{ }` group, so after YAML
        strips the block's common indent the terminator lands at column 2, not column 0."""
        p = _wf(tmp_path, """
        jobs:
          study:
            steps:
              - name: Summarize
                run: |
                  {
                    echo "summary<<EOF"
                    python3 - <<'SUMEOF'
                    import json
                    print(json.dumps({}))
                    SUMEOF
                    echo "EOF"
                  } >> "$GITHUB_OUTPUT"
        """)
        fails = mod.check_run_blocks(p)
        assert fails, "a heredoc that never terminates must be caught"
        assert "Summarize" in fails[0]

    def test_a_correctly_flush_left_heredoc_passes(self, tmp_path):
        """Same content, terminator at the block's BASE indent -> column 0 after YAML strips."""
        p = _wf(tmp_path, """
        jobs:
          study:
            steps:
              - name: Summarize
                run: |
                  python3 - <<'SUMEOF' > /tmp/out.json
                  import json
                  print(json.dumps({}))
                  SUMEOF
                  { echo "summary<<EOF"; cat /tmp/out.json; echo "EOF"; } >> "$GITHUB_OUTPUT"
        """)
        assert mod.check_run_blocks(p) == []


class TestScoping:
    def test_a_non_bash_step_is_skipped_not_misparsed(self, tmp_path):
        """`bash -n` on a python step would be the wrong parser, not a real finding."""
        p = _wf(tmp_path, """
        jobs:
          j:
            steps:
              - name: python step
                shell: python
                run: |
                  x = {"a": 1}
                  print(x)
        """)
        assert mod.check_run_blocks(p) == []

    def test_a_job_level_default_shell_is_honoured(self, tmp_path):
        p = _wf(tmp_path, """
        jobs:
          j:
            defaults:
              run:
                shell: python
            steps:
              - name: inherits python
                run: |
                  d = {}
                  print(d)
        """)
        assert mod.check_run_blocks(p) == []

    def test_steps_without_run_are_ignored(self, tmp_path):
        p = _wf(tmp_path, """
        jobs:
          j:
            steps:
              - uses: actions/checkout@v4
              - name: no run here
                uses: actions/setup-python@v5
        """)
        assert mod.check_run_blocks(p) == []

    def test_unbalanced_quote_is_caught(self, tmp_path):
        p = _wf(tmp_path, """
        jobs:
          j:
            steps:
              - name: broken quote
                run: |
                  echo "never closed
        """)
        assert mod.check_run_blocks(p)


class TestTheRepoItself:
    def test_every_committed_workflow_parses(self):
        """The guard must be green on the real repo — a guard that ships red is ignored."""
        assert mod.main([]) == 0


class TestCouldNotCheckIsItsOwnOutcome:
    """A missing dependency must be neither a pass nor a pile of fake findings.

    The guard's FIRST CI run exited with 117 "PyYAML not available" findings because the
    per-file helper returned the missing import as a per-file result. That is the mirror
    image of the bug this repo keeps hitting: red while measuring nothing, instead of green
    while measuring nothing — same root, the result did not reflect what was checked.
    """

    @staticmethod
    def _hide_yaml(monkeypatch):
        import builtins
        real_import = builtins.__import__

        def no_yaml(name, *a, **k):
            if name == "yaml":
                raise ImportError("No module named 'yaml'")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", no_yaml)

    def test_missing_pyyaml_exits_2_not_1_and_says_nothing_was_checked(self, monkeypatch,
                                                                      capsys):
        self._hide_yaml(monkeypatch)
        rc = mod.main([])
        out = capsys.readouterr().out
        # 2 == could not check; 1 == real findings. They must not be confusable.
        assert rc == 2, "a missing dependency must not share an exit code with real findings"
        assert "NOT a finding" in out
        assert "nothing was checked" in out
        # and it must not enumerate the workflows as if each were a defect
        assert out.count(".yml") <= 1

    def test_missing_dependency_never_silently_passes(self, monkeypatch, capsys):
        self._hide_yaml(monkeypatch)
        assert mod.main([]) != 0, "could-not-check must never be reported as OK"
