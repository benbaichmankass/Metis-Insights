"""A whole-file JSON corpus must be READ, not reported as an empty population.

`probe_file.py` read every named corpus line by line. `docs/claude/DUE.json` is
one pretty-printed JSON object, so line-by-line yields **zero** rows — and the
probe then reports *"read and nothing matched"*, a clean negative worn over a
population nobody read. That is the sub-class **C** defect in `CLAUDE.md`
§ "Diagnostic provenance", and it is invisible: the verdict is byte-identical
to a genuine no-match.
"""
from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import sys
from pathlib import Path

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


class TestTheEnvelopeShapeThisWasBuiltFor:
    """A `DUE.json`-shaped envelope — as a FIXTURE, deliberately.

    ⚠️ THIS CLASS USED TO READ THE COMMITTED `docs/claude/DUE.json`, AND THAT
    WAS TWO DEFECTS AT ONCE. `tests/test_pytest_run_filter.py` caught the first
    and it was right: that path short-circuits `pytest-run`, so a PR touching
    only it merges with a green tick having executed nothing — instance 5 of
    the class that produced `BL-20260814-COVERAGE-MATRIX-SHORTCIRCUITS-THE-SUITE-THAT-READS-IT`.

    ⚠️ ITS PRESCRIBED REMEDY IS *COVER THE PATH*, AND I DID NOT TAKE IT, FOR A
    MEASURED REASON. That instruction was written for a rarely-touched research
    artifact. MEASURED 2026-09-17 on `main`: `docs/claude/DUE.json` changed
    **4-8 times a day** over the preceding week and `docs/claude/OPEN-ITEMS.json`
    **77 times in 7 days**. Covering them would put a ~16-minute full suite on
    roughly fifteen pushes a day, on two of the hottest files in the repo —
    against MB-20260706-CI-MINUTES, which is why the short-circuit exists.

    ⚠️ AND THE SECOND DEFECT IS THE ONE NO GUARD CAUGHT: three of the four tests
    here asserted TODAY'S OPERATIONAL STATE of a file a cron regenerates. One
    required `sources.red_crons.state == "read"`, which goes false the moment a
    render happens to run without a token — reddening `main` for CORRECT
    behaviour. Another asserted `red_main_runs` was ABSENT, i.e. it was built to
    FAIL the moment the thing it watches starts working. A live operational fact
    belongs in the PROBE, which can say `could_not_look`; a test can only say
    pass or fail, and over a moving file that is a time bomb either way.

    ⚠️ NOTHING IS LOST BY MOVING THEM HERE, and that is checked rather than
    asserted: the REGISTRY half — that the row carries a probe at all — is
    already enforced by `probe-guard` (`scripts/ops/run_probes.py --check`),
    which is in the guard set and was OBSERVED failing with *"monitoring row
    declares neither `probe` nor `probe_absent_reason`"* when the field was
    missing. The SHAPE half is genuinely low-value now: the reader handles a
    whole-file object AND JSONL, so a `DUE.json` that changed shape would keep
    being read correctly rather than going blind.
    """

    ENVELOPE = {
        "schema_version": 1,
        "generated_at": "2026-09-13T05:20:13+00:00",
        "verdict": "partial",
        "unreadable_sources": ["spent_decision_edges"],
        "sources": {
            "red_crons": {"state": "read", "rows": 6, "note": ""},
            "open_items": {"state": "read", "rows": 75, "note": ""},
        },
        "counts": {"due": 119, "loud": 94},
    }

    def _envelope(self, tmp_path, **override):
        env = dict(self.ENVELOPE)
        env.update(override)
        (tmp_path / "DUE.json").write_text(json.dumps(env, indent=2))
        return tmp_path

    def test_it_is_one_row_not_zero(self, tmp_path):
        """The shape claim: pretty-printed, so the line reader sees NOTHING."""
        root = self._envelope(tmp_path)
        rows, note = probe_file.read_files(["DUE.json"], root)
        assert rows is not None and len(rows) == 1, note

    def test_a_condition_that_holds_passes(self, tmp_path):
        root = self._envelope(tmp_path)
        out = _run("--root", str(root), "--file", "DUE.json",
                   "--require", "sources.red_crons.state=read",
                   "--positive-control", "verdict~partial,all_sources_read")
        assert out.returncode == PASS, out.stdout + out.stderr

    def test_a_source_absent_from_the_envelope_is_read_and_nothing_matched(self, tmp_path):
        """The state the real row sits in until its source first runs: the file
        WAS read, and the condition is not met. Never `could_not_look`."""
        root = self._envelope(tmp_path)
        out = _run("--root", str(root), "--file", "DUE.json",
                   "--require", "sources.red_main_runs.state=read",
                   "--positive-control", "sources.red_crons.state=read")
        assert out.returncode == NOTHING_MATCHED, out.stdout + out.stderr
        assert "positive control" in out.stdout

    def test_a_blind_reader_is_reported_as_could_not_look(self, tmp_path):
        """The control on the control: a positive control that does not fire
        makes the verdict an unread, so a quiet probe over a moved schema can
        never be banked as a negative."""
        root = self._envelope(tmp_path)
        out = _run("--root", str(root), "--file", "DUE.json",
                   "--require", "sources.red_main_runs.state=read",
                   "--positive-control", "sources.no_such_source.state=read")
        assert out.returncode == COULD_NOT_LOOK, out.stdout + out.stderr

    def test_this_file_reads_no_committed_register(self):
        """The control that keeps the fix from silently regressing.

        If a future edit re-introduces a committed read of a short-circuiting
        register, `test_pytest_run_filter` would catch it only as a coverage
        gap — and the fragility above would be back either way.

        ⚠️ IT SCANS THE CODE, NOT THE PROSE, AND THAT DISTINCTION IS THE WHOLE
        CONTROL. A plain substring scan fails on this very file, because the
        docstrings above NAME the two paths in order to explain why they are
        not read — and this repo's convention is to preserve a corrected claim
        beside its correction rather than delete it. So the scan walks the AST
        and looks only at string literals that are NOT docstrings: a mention
        cannot trip it, and a read cannot hide behind one.
        """
        SELF = "test_this_file_reads_no_committed_register"
        tree = ast.parse(pathlib.Path(__file__).read_text())
        # ⚠️ IT EXCISES ITS OWN BODY FROM THE TREE, because the tuple naming the
        # paths is itself a non-docstring literal — the first run of this
        # control tripped on exactly that, and the second attempt (filtering by
        # name while still walking the parent class) failed the same way,
        # because the class body carries the function back in. A scanner that
        # cannot see past its own declaration reports a finding that is always
        # present, which is indistinguishable from a guard that never passes.
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(body, list):
                node.body = [
                    n for n in body
                    if not (isinstance(n, ast.FunctionDef) and n.name == SELF)
                ]
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        literals = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ]
        for hot in ("docs/claude/DUE.json", "docs/claude/OPEN-ITEMS.json"):
            offenders = [lit for lit in literals if hot in lit]
            assert not offenders, (
                f"{hot} is regenerated many times a day; assert over a fixture "
                f"and let the PROBE read the live file, which can report "
                f"`could_not_look` where a test can only pass or fail. "
                f"Offending literal(s): {offenders}")


# ⚠️ REMOVED 2026-09-21 by the operating reset: `TestTheRowsProbeIsEnforcedByTheGuardNotByThisSuite`.
# It asserted a property of the LIVE retired governance registers, which is archived under
# docs/archive/2026-09-21-operating-reset/. Its subject is gone, so the
# test cannot pass and cannot be made to pass — it is removed WITH its
# subject rather than skipped, because a permanently-skipped test is a
# control in name only. Its fixture-based siblings in this file are
# UNTOUCHED and still green: they test the CODE, which still exists.
# Restore it from git history if the register ever returns.


