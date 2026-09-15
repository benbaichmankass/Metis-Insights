"""A whole-file JSON corpus must be READ, not reported as an empty population.

`probe_file.py` read every named corpus line by line. `docs/claude/DUE.json` is
one pretty-printed JSON object, so line-by-line yields **zero** rows — and the
probe then reports *"read and nothing matched"*, a clean negative worn over a
population nobody read. That is the sub-class **C** defect in `CLAUDE.md`
§ "Diagnostic provenance", and it is invisible: the verdict is byte-identical
to a genuine no-match.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ops"))

import probe_file  # noqa: E402

PASS, NOTHING_MATCHED, COULD_NOT_LOOK = 0, 1, 2


def _run(*args):
    return subprocess.run([sys.executable, "scripts/ops/probe_file.py", *args],
                          cwd=REPO, capture_output=True, text=True, timeout=120)


class TestAWholeFileJsonObjectIsOneRow:
    def test_it_is_read(self, tmp_path):
        (tmp_path / "c.json").write_text(json.dumps(
            {"verdict": "partial", "sources": {"a": {"state": "read"}}}, indent=2))
        rows, note = probe_file.read_files(["c.json"], tmp_path)
        assert rows is not None and len(rows) == 1
        assert "c.json:1" in note

    def test_a_nested_path_reaches_into_it(self, tmp_path):
        (tmp_path / "c.json").write_text(json.dumps(
            {"sources": {"red_crons": {"state": "read"}}}, indent=2))
        rows, _ = probe_file.read_files(["c.json"], tmp_path)
        import probe_lib
        # `walk` yields EVERY value at the path, so the answer is a list.
        assert probe_lib.walk(rows[0], "sources.red_crons.state") == ["read"]

    def test_the_old_reader_saw_nothing_in_the_same_bytes(self, tmp_path):
        """The measurement that motivates the change, asserted rather than
        described: line-by-line, this file is an EMPTY population."""
        text = json.dumps({"sources": {"x": {"state": "read"}}}, indent=2)
        (tmp_path / "c.json").write_text(text)
        line_rows = []
        for line in text.splitlines():
            try:
                o = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            if isinstance(o, dict):
                line_rows.append(o)
        assert line_rows == []
        rows, _ = probe_file.read_files(["c.json"], tmp_path)
        assert len(rows) == 1


class TestAJsonArrayIsItsRows:
    def test_each_object_becomes_a_row(self, tmp_path):
        (tmp_path / "c.json").write_text(json.dumps(
            [{"state": "a"}, {"state": "b"}, {"state": "c"}], indent=2))
        rows, _ = probe_file.read_files(["c.json"], tmp_path)
        assert [r["state"] for r in rows] == ["a", "b", "c"]

    def test_non_objects_inside_the_array_are_skipped(self, tmp_path):
        (tmp_path / "c.json").write_text(json.dumps([{"state": "a"}, 7, "x", None]))
        rows, _ = probe_file.read_files(["c.json"], tmp_path)
        assert rows == [{"state": "a"}]


class TestJsonlIsUnaffected:
    def test_a_multi_line_jsonl_still_reads_line_by_line(self, tmp_path):
        (tmp_path / "c.jsonl").write_text(
            '{"state": "a"}\n{"state": "b"}\n{"state": "c"}\n')
        rows, note = probe_file.read_files(["c.jsonl"], tmp_path)
        assert [r["state"] for r in rows] == ["a", "b", "c"]
        assert "c.jsonl:3" in note

    def test_a_one_line_jsonl_reads_the_same_either_way(self, tmp_path):
        """The only file shape both paths can claim. They must agree, or the
        change would silently alter an existing corpus."""
        (tmp_path / "c.jsonl").write_text('{"state": "a"}\n')
        rows, _ = probe_file.read_files(["c.jsonl"], tmp_path)
        assert rows == [{"state": "a"}]

    def test_a_malformed_line_among_good_ones_is_still_skipped(self, tmp_path):
        (tmp_path / "c.jsonl").write_text(
            '{"state": "a"}\nnot json at all\n{"state": "b"}\n')
        rows, _ = probe_file.read_files(["c.jsonl"], tmp_path)
        assert [r["state"] for r in rows] == ["a", "b"]

    def test_a_scalar_whole_file_falls_through_to_the_line_reader(self, tmp_path):
        """`7` parses as whole-file JSON and is not a corpus. It must not
        swallow the file: the line reader is the fallback, and it finds
        nothing here either — but for the right reason."""
        (tmp_path / "c.json").write_text("7\n")
        rows, _ = probe_file.read_files(["c.json"], tmp_path)
        assert rows == []


class TestWeCouldNotLookIsStillItsOwnVerdict:
    def test_an_absent_file_is_not_an_empty_population(self, tmp_path):
        rows, note = probe_file.read_files(["nope.json"], tmp_path)
        assert rows is None
        assert "ABSENT" in note

    def test_an_absent_file_exits_could_not_look(self, tmp_path):
        out = _run("--file", "docs/claude/NO-SUCH-FILE.json",
                   "--require", "x=1", "--positive-control", "x=1")
        assert out.returncode == COULD_NOT_LOOK


class TestTheRealCorpusThisWasBuiltFor:
    """`docs/claude/DUE.json` — the committed due-list envelope."""

    def _due(self):
        p = REPO / "docs/claude/DUE.json"
        if not p.is_file():
            pytest.skip("DUE.json is not committed in this clone; SKIPPED IS "
                        "NOT PASSED — the real-corpus control did not run.")
        return p

    def test_it_is_one_row_not_zero(self):
        self._due()
        rows, note = probe_file.read_files(["docs/claude/DUE.json"], REPO)
        assert rows is not None and len(rows) == 1, note

    def test_a_condition_that_holds_today_passes(self):
        self._due()
        out = _run("--file", "docs/claude/DUE.json",
                   "--require", "sources.red_crons.state=read",
                   "--positive-control", "verdict~partial,all_sources_read")
        assert out.returncode == PASS, out.stdout + out.stderr

    def test_the_row_this_probe_watches_does_not_hold_yet(self):
        """`red_main_runs` shipped after the last render, so the honest answer
        today is 'read, and nothing matched' — never 'could not look'."""
        self._due()
        out = _run("--file", "docs/claude/DUE.json",
                   "--require", "sources.red_main_runs.state=read",
                   "--positive-control", "sources.red_crons.state=read")
        assert out.returncode == NOTHING_MATCHED, out.stdout + out.stderr
        assert "positive control" in out.stdout

    def test_a_blind_reader_is_reported_as_could_not_look(self):
        """The control on the control: a positive control that does not fire
        makes the verdict an unread, so a quiet probe over a moved schema can
        never be banked as a negative."""
        self._due()
        out = _run("--file", "docs/claude/DUE.json",
                   "--require", "sources.red_main_runs.state=read",
                   "--positive-control", "sources.no_such_source.state=read")
        assert out.returncode == COULD_NOT_LOOK, out.stdout + out.stderr


class TestTheProbeIsDeclaredOnTheRowItWatches:
    ROW = "OI-20260913-THE-DEFAULT-BRANCH-RED-READER-IS-DEPLOYED-AND-HAS-NEVER-QUERIED-THE-ACTIONS-API"

    def _row(self):
        items = json.loads(
            (REPO / "docs/claude/OPEN-ITEMS.json").read_text())["items"]
        hit = [r for r in items if r.get("id") == self.ROW]
        assert hit, f"{self.ROW} is not in the register"
        return hit[0]

    def test_it_carries_a_probe_rather_than_an_excuse(self):
        row = self._row()
        assert "probe" in row, (
            "a monitoring row whose observable IS probeable must carry the "
            "probe; probe_absent_reason is for the cases that are not")
        assert "probe_absent_reason" not in row

    def test_the_probe_names_a_positive_control(self):
        cmd = self._row()["probe"]["cmd"]
        assert "--positive-control" in cmd, (
            "without one, a quiet probe cannot be told from a blind one")
