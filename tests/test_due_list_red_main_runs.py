"""A red on the DEFAULT BRANCH must reach somebody.

WHY THIS EXISTS. `render_due_list.py::src_red_crons` queries
`/actions/runs?event=schedule` and nothing else — its own docstring calls its
class *"a nightly nobody is waiting on"*. A failed run on `main` is a different
class and was read by nothing.

MEASURED 2026-09-13, every link:
  * `main` at `2a6c1b24d` carried a duplicated register id (1575 rows, one id
    twice);
  * `check_register_ids.py` exits 1 at that commit — R1 needs no `--base`;
  * `guards.yml` carries `push: branches: [main]` (1370 runs) and
    `register-id-guard` is `when: None`;
  * so `main` was red-capable, and the duplicate was still found by an
    unrelated PR author tripping over it.

⚠️ THE ROW THAT PROMPTED THIS ASKS FOR A CADENCE, AND THE CADENCE ALREADY
EXISTED. Building a cron would have put a second mechanism beside a working one
and left the real gap — that its red reached nobody — wide open.
"""
from __future__ import annotations

import datetime
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ops"))

R = pytest.importorskip("render_due_list")
TODAY = datetime.date.today()


def _runs(monkeypatch, *runs):
    """Stub the Actions API with a newest-first run list."""
    monkeypatch.setattr(R, "_gh", lambda *a, **k: {"workflow_runs": list(runs)})


def _run(name="Guards", conclusion="failure", status="completed"):
    return {"name": name, "conclusion": conclusion, "status": status,
            "html_url": f"https://example/{name}/{conclusion}"}


class TestSilenceIsNeverGreen:
    def test_no_token_is_could_not_read(self):
        """It must never report 'main is green' because it could not look."""
        r = R.src_red_main_runs(pathlib.Path("."), TODAY, token="")
        assert r.state == "could_not_read"
        assert r.rows == []

    def test_an_api_failure_is_could_not_read(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("network")
        monkeypatch.setattr(R, "_gh", boom)
        r = R.src_red_main_runs(pathlib.Path("."), TODAY, token="t")
        assert r.state == "could_not_read"


class TestItFiresOnARealFailure:
    def test_a_failed_main_run_is_a_loud_row(self, monkeypatch):
        _runs(monkeypatch, _run(conclusion="failure"))
        r = R.src_red_main_runs(pathlib.Path("."), TODAY, token="t")
        assert r.state == "read" and len(r.rows) == 1
        assert r.rows[0]["loud"] is True
        assert "DEFAULT BRANCH is red" in r.rows[0]["why_due"]

    def test_timed_out_also_counts(self, monkeypatch):
        _runs(monkeypatch, _run(conclusion="timed_out"))
        assert len(R.src_red_main_runs(pathlib.Path("."), TODAY, token="t").rows) == 1


class TestOrdinaryMergeTrafficIsNotAFinding:
    """THE CONTROL THAT MATTERS. `guards.yml`'s push concurrency group is
    `guards-push-refs/heads/main` for EVERY commit with `cancel-in-progress`,
    so each merge cancels the previous run — MEASURED at 3 of the 15 most
    recent. Counting those would put a row on the due list for ordinary merge
    traffic, which is the desensitised-alarm failure this repo calls its worst.

    It is safe to ignore because `main` is LINEAR: a later commit's run grades
    the whole tree, so a cancelled run is a delay (median inter-merge gap
    2.7 min), not lost coverage."""

    @pytest.mark.parametrize("conclusion", ["success", "cancelled", "skipped", None])
    def test_these_do_not_fire(self, monkeypatch, conclusion):
        _runs(monkeypatch, _run(conclusion=conclusion))
        r = R.src_red_main_runs(pathlib.Path("."), TODAY, token="t")
        assert r.state == "read" and r.rows == [], conclusion

    def test_cancelled_is_in_the_declared_not_a_finding_set(self):
        """Asserted against the module's own constant, not restated here, so a
        later edit to that tuple has to argue with this test."""
        assert "cancelled" in R._PUSH_RUN_NOT_A_FINDING
        assert "failure" not in R._PUSH_RUN_NOT_A_FINDING

    def test_a_run_still_in_progress_is_not_an_answer(self, monkeypatch):
        _runs(monkeypatch, _run(conclusion=None, status="in_progress"))
        assert R.src_red_main_runs(pathlib.Path("."), TODAY, token="t").rows == []

    def test_an_in_progress_run_does_not_MASK_an_older_failure(self, monkeypatch):
        """WHY THE STATUS FILTER IS LOAD-BEARING, and it is not the obvious
        reason. An in-progress run always carries `conclusion: null`, which the
        not-a-finding set already absorbs — so dropping the filter does NOT make
        it fire spuriously, and a control asserting only that passes against the
        broken version. I planted exactly that and my first battery stayed green.

        What the filter actually prevents: `main` is busy, so at any moment the
        newest run is usually still running. Without the filter it takes the
        latest-per-workflow slot, its null conclusion reads as 'not a finding',
        and a COMPLETED failure underneath it is masked — the source goes quiet
        precisely when the branch is red.
        """
        _runs(monkeypatch,
              _run(conclusion=None, status="in_progress"),   # newest, still running
              _run(conclusion="failure"))                    # the red underneath
        rows = R.src_red_main_runs(pathlib.Path("."), TODAY, token="t").rows
        assert len(rows) == 1, "an in-progress run masked a completed failure"


class TestLatestPerWorkflowNotEveryFailure:
    def test_an_older_failure_under_a_newer_success_is_not_reported(self, monkeypatch):
        """`main` takes hundreds of commits a day; every historical red would be
        a backlog, not a signal. What is actionable is whether it is red NOW."""
        _runs(monkeypatch, _run(conclusion="success"), _run(conclusion="failure"))
        assert R.src_red_main_runs(pathlib.Path("."), TODAY, token="t").rows == []

    def test_a_newer_failure_under_an_older_success_IS_reported(self, monkeypatch):
        _runs(monkeypatch, _run(conclusion="failure"), _run(conclusion="success"))
        assert len(R.src_red_main_runs(pathlib.Path("."), TODAY, token="t").rows) == 1

    def test_two_workflows_are_graded_independently(self, monkeypatch):
        _runs(monkeypatch, _run("Guards", "failure"), _run("Tests", "success"))
        rows = R.src_red_main_runs(pathlib.Path("."), TODAY, token="t").rows
        assert [r["id"] for r in rows] == ["Guards"]


class TestItIsActuallyWired:
    def test_registered_in_sources(self):
        """A source nothing calls is the 'registered but never executed' shape
        this module's own self-test already pins for its siblings."""
        assert R.src_red_main_runs in R.SOURCES

    def test_collect_passes_it_the_token(self):
        """It takes a token, so `collect` must dispatch it in the token branch —
        otherwise it would be called without one and report could_not_read
        forever, which looks exactly like a quiet source."""
        src = (REPO / "scripts" / "ops" / "render_due_list.py").read_text(encoding="utf-8")
        disp = src.index("if fn in (src_red_crons,")
        line_end = src.index("\n", disp)
        assert "src_red_main_runs" in src[disp:line_end], src[disp:line_end]

    def test_it_queries_the_default_branch_not_the_schedule(self):
        """The whole defect was a source scoped to `event=schedule`. If this one
        drifted to the same scope it would silently re-open the gap.

        ⚠️ ASSERTED ON THE QUERY, NOT ON THE FUNCTION BODY. The first version of
        this test scanned the whole body and failed on its own docstring, which
        QUOTES `event=schedule` while explaining what the sibling source does.
        A test that cannot tell a query from prose about a query is measuring
        the wrong thing.
        """
        src = (REPO / "scripts" / "ops" / "render_due_list.py").read_text(encoding="utf-8")
        body = src[src.index("def src_red_main_runs("):]
        body = body[:body.index("\ndef ")]
        call = body[body.index("_gh(f\""):]
        call = call[:call.index(", token)")]
        assert "event=push" in call and "branch=main" in call, call
        assert "event=schedule" not in call, call
