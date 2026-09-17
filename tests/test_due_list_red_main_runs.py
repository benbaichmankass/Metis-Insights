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


# ---------------------------------------------------------------------------
# A verdict about "the DEFAULT BRANCH" that graded some OTHER commit
#
# The source above is correct about WHICH conclusion is a finding and says
# nothing about WHICH COMMIT produced it. It reports the latest COMPLETED
# push-to-`main` run per workflow, and an auto-merged PR's merge commit is
# created by `GITHUB_TOKEN`, which produces no push run at all — so the run it
# reads can be many commits behind the tip, and because a row is emitted only
# on failure, a green run from an old commit renders as ZERO ROWS.
#
# MEASURED 2026-09-17 (the fixture at the bottom of this file):
#   * `main`'s tip was `00045e5d3` (#12417);
#   * the newest COMPLETED push-to-main `guards` run had graded `88de5e156`
#     (#12414, `success`) — FOUR commits back;
#   * all four ungraded commits touched a register, the class that turned
#     `main` red twice that same day.
# `main` was in fact clean (1610 rows, 0 duplicates, checked independently).
# That is the point: this source could not tell the two apart.
#
# ⚠️ THE FIX IS NOT A NEW ALARM. `main` is ungraded at its tip for most of any
# given day, so a row for it would fire on ordinary merge traffic — the
# desensitised-alarm failure this repo calls its own worst. What the verdict
# gains is its POPULATION, in the same idiom this module already uses for
# sources whose zero is not a clean negative.
# ---------------------------------------------------------------------------

def _api(monkeypatch, runs, shas=(), commits_raise=False):
    """Stub BOTH endpoints this source reads, keyed on the path.

    The pre-existing `_runs` helper answers every call with the run list, so
    under it the commits read yields nothing and every placement degrades to
    `distance_unknown`. That is the intended degradation and the tests above
    still pass unchanged because of it.
    """
    def gh(path, token):
        if "/commits" in path:
            if commits_raise:
                raise OSError("commits unavailable")
            return [{"sha": s} for s in shas]
        return {"workflow_runs": list(runs)}
    monkeypatch.setattr(R, "_gh", gh)


def _run_at(sha, name="Guards", conclusion="failure", status="completed"):
    r = _run(name, conclusion, status)
    r["head_sha"] = sha
    return r


class TestGradeDistanceIsThreeStatesNeverTwo:
    def test_index_zero_is_the_tip(self):
        assert R.grade_distance(["a", "b", "c"], "a") == (R.GRADE_AT_TIP, 0)

    def test_index_n_is_n_commits_behind(self):
        assert R.grade_distance(["a", "b", "c"], "c") == (R.GRADE_BEHIND_TIP, 2)

    def test_a_sha_off_the_page_is_unknown_and_NOT_a_made_up_number(self):
        """The commit list is one page. A sha that is not on it is either older
        than the page or not on `main` at all — two different facts. Returning
        `len(main_shas)` would put a fabricated denominator on the very verdict
        this change exists to give a real one."""
        state, dist = R.grade_distance(["a", "b", "c"], "zzz")
        assert state == R.GRADE_DISTANCE_UNKNOWN
        assert dist is None, "a distance was invented for a commit we never found"
        assert dist != 3

    def test_an_unreadable_commit_list_is_unknown_not_the_tip(self):
        """*We could not look* must never render as *it is current*."""
        assert R.grade_distance([], "a") == (R.GRADE_DISTANCE_UNKNOWN, None)

    def test_a_run_with_no_head_sha_is_unknown(self):
        assert R.grade_distance(["a"], "") == (R.GRADE_DISTANCE_UNKNOWN, None)
        assert R.grade_distance(["a"], None) == (R.GRADE_DISTANCE_UNKNOWN, None)

    def test_the_three_states_are_distinct(self):
        """Anti-collapse: two of these sharing a value would make the guard
        above vacuous while every assertion still passed."""
        assert len({R.GRADE_AT_TIP, R.GRADE_BEHIND_TIP,
                    R.GRADE_DISTANCE_UNKNOWN}) == 3


class TestTheVerdictCarriesTheCommitItGraded:
    def test_a_red_row_names_the_sha_and_the_distance(self, monkeypatch):
        _api(monkeypatch, [_run_at("bbbbbbbbbb")], shas=["aaaaaaaaaa", "bbbbbbbbbb"])
        rows = R.src_red_main_runs(pathlib.Path("."), TODAY, token="t").rows
        assert len(rows) == 1
        why = rows[0]["why_due"]
        assert "bbbbbbbbb" in why, why
        assert "1 commit(s) behind" in why, why

    def test_a_row_graded_at_the_tip_says_so(self, monkeypatch):
        _api(monkeypatch, [_run_at("aaaaaaaaaa")], shas=["aaaaaaaaaa", "bbbbbbbbbb"])
        why = R.src_red_main_runs(pathlib.Path("."), TODAY, token="t").rows[0]["why_due"]
        assert "tip" in why and "behind" not in why, why

    def test_the_note_counts_the_population(self, monkeypatch):
        _api(monkeypatch,
             [_run_at("aaaaaaaaaa", "Guards", "failure"),
              _run_at("cccccccccc", "Tests", "success")],
             shas=["aaaaaaaaaa", "bbbbbbbbbb", "cccccccccc"])
        note = R.src_red_main_runs(pathlib.Path("."), TODAY, token="t").note
        assert "2 workflow(s) graded, 1 red" in note, note
        assert "1 graded at `main`'s tip" in note, note
        assert "worst distance 2 commit(s) behind" in note, note


class TestZeroRowsIsNotACleanNegative:
    """The case the whole change exists for: no rows, and the reader must not
    be able to read that as a statement about the tip."""

    def test_an_all_green_result_still_carries_a_note(self, monkeypatch):
        _api(monkeypatch, [_run_at("cccccccccc", conclusion="success")],
             shas=["aaaaaaaaaa", "bbbbbbbbbb", "cccccccccc"])
        r = R.src_red_main_runs(pathlib.Path("."), TODAY, token="t")
        assert r.rows == []
        assert r.note, "a zero-row result shipped with no denominator at all"
        assert "1 workflow(s) graded, 0 red" in r.note, r.note
        assert "worst distance 2 commit(s) behind" in r.note, r.note

    def test_the_note_says_a_zero_is_not_about_the_tip(self, monkeypatch):
        _api(monkeypatch, [_run_at("aaaaaaaaaa", conclusion="success")],
             shas=["aaaaaaaaaa"])
        note = R.src_red_main_runs(pathlib.Path("."), TODAY, token="t").note
        assert "NOT A CLEAN NEGATIVE ABOUT THE TIP" in note, note
        assert "auto-merged" in note, note


class TestTheDenominatorNeverEatsTheFinding:
    """A red is the product; the denominator is context. Losing the red because
    the context could not be read would be strictly worse than the
    unprovenanced verdict this change repairs."""

    def test_a_commits_failure_still_reports_the_red(self, monkeypatch):
        _api(monkeypatch, [_run_at("bbbbbbbbbb")], commits_raise=True)
        r = R.src_red_main_runs(pathlib.Path("."), TODAY, token="t")
        assert r.state == "read"
        assert len(r.rows) == 1, "the red was suppressed by a missing denominator"

    def test_and_the_note_says_the_distance_could_not_be_read(self, monkeypatch):
        _api(monkeypatch, [_run_at("bbbbbbbbbb")], commits_raise=True)
        note = R.src_red_main_runs(pathlib.Path("."), TODAY, token="t").note
        assert "could not read `main`'s commit list" in note, note
        assert "1 with distance_unknown" in note, note


class TestTheRealIncident:
    """2026-09-17, reproduced from the measurement rather than invented.

    `main` tip `00045e5d3` (#12417); newest COMPLETED push-to-main `guards` run
    graded `88de5e156` (#12414) with conclusion `success`, four commits back.
    Before this change the source emitted zero rows and no note — a clean
    negative about a tip carrying four ungraded register-touching squashes.
    """

    TIP_TO_GRADED = [
        "00045e5d3f0000000000000000000000000000000",  # #12417
        "918c9b1490000000000000000000000000000000",   # #12411
        "dc084242460000000000000000000000000000000",  # #12415
        "287fe7ca70000000000000000000000000000000",   # #12410
        "88de5e156effd048ff72721b6a0e5276aed4cda7",   # #12414 — the graded one
    ]

    def test_the_verdict_now_states_how_far_back_it_graded(self, monkeypatch):
        _api(monkeypatch,
             [_run_at(self.TIP_TO_GRADED[-1], conclusion="success")],
             shas=self.TIP_TO_GRADED)
        r = R.src_red_main_runs(pathlib.Path("."), TODAY, token="t")
        assert r.rows == [], "nothing was red — that half was always correct"
        assert "worst distance 4 commit(s) behind" in r.note, r.note
        assert "0 graded at `main`'s tip" in r.note, r.note
