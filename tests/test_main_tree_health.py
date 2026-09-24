"""`main` is graded on a cadence, and a cadence that lies is worse than none.

WHY THIS EXISTS. The repo's only post-merge check is `guards.yml`'s
`push: branches: [main]` arm, and it skips auto-merged PRs entirely — measured
2026-09-17 on two disjoint populations: by MERGE CREDENTIAL over all 29
PR-merge commits in one window (user credential 5 of 5 produce a push run,
GITHUB_TOKEN 0 of 24), and by COMMIT KIND over a later 5.1h window (PR merges
0 of 31, chore(ops) pushes 9 of 9).

⚠️ THE BLIND LANDINGS ARE LATE, NOT UNCOVERED — all 24 were eventually covered,
median lag 34.4 min. What the watcher bounds is the gap between consecutive
GRADED runs, measured at up to 442.6 min, and the argument is CORRELATION: that
coverage rides on chore(ops) pushes (95 of the last 100 push runs), which stop
exactly when the repo is unhealthy.

The assertions below are mostly about NOT COLLAPSING STATES, because every way
this watcher could fail silently is a collapse: a probe that could not run
reported as clean, an empty probe list reported as clean, or a non-clean
verdict exiting 0 so nothing ever pages.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ci"))

M = pytest.importorskip("check_main_tree_health")


class TestWeCouldNotLookIsNeverClean:
    """The whole family of defects this repo keeps paying for, in one place."""

    def test_a_timeout_is_could_not_run(self):
        assert M.grade(None) == M.COULD_NOT_RUN

    def test_a_timeout_is_not_clean(self):
        """`returncode is None` must not be truthy-tested — 0 is falsy too, so a
        naive `if not returncode` would fold a timeout into a pass."""
        assert M.grade(None) != M.CLEAN

    def test_a_spawn_failure_is_could_not_run_even_with_returncode_zero(self):
        assert M.grade(0, spawn_failed=True) == M.COULD_NOT_RUN

    def test_exit_zero_is_clean(self):
        assert M.grade(0) == M.CLEAN

    def test_any_non_zero_is_a_finding(self):
        assert M.grade(1) == M.FINDING
        assert M.grade(2) == M.FINDING


class TestTheFoldNeverDowngradesARed:
    def test_a_finding_outranks_a_could_not_run(self):
        """A real red must not be downgraded to 'we could not look' because an
        unrelated probe failed to start."""
        assert M.overall([M.COULD_NOT_RUN, M.FINDING]) == M.FINDING
        assert M.overall([M.FINDING, M.COULD_NOT_RUN]) == M.FINDING

    def test_a_could_not_run_outranks_a_clean(self):
        assert M.overall([M.CLEAN, M.COULD_NOT_RUN]) == M.COULD_NOT_RUN

    def test_all_clean_is_clean(self):
        assert M.overall([M.CLEAN, M.CLEAN, M.CLEAN]) == M.CLEAN

    def test_grading_NOTHING_is_could_not_run_not_clean(self):
        """An empty probe list means nothing was examined. Reporting that as a
        clean default branch is the unasserted-denominator defect."""
        assert M.overall([]) == M.COULD_NOT_RUN


class TestItCanActuallyPage:
    def test_clean_exits_zero(self):
        assert M.EXIT[M.CLEAN] == 0

    def test_both_non_clean_verdicts_exit_non_zero(self):
        """The alert keys on the run's conclusion, so a verdict that exits 0
        pages nobody however loudly it prints."""
        assert M.EXIT[M.FINDING] != 0
        assert M.EXIT[M.COULD_NOT_RUN] != 0

    def test_the_two_failure_modes_are_distinguishable(self):
        """`a real red on main` and `we could not look` need different responses,
        so they must not share an exit code."""
        assert M.EXIT[M.FINDING] != M.EXIT[M.COULD_NOT_RUN]

    def test_the_three_states_are_distinct(self):
        assert len({M.CLEAN, M.FINDING, M.COULD_NOT_RUN}) == 3


class TestTheProbesAreRealAndDeclareTheirLimits:
    def test_the_probe_set_is_not_empty(self):
        """An empty set would grade nothing and — correctly — page forever, but
        it would also mean the watcher watches nothing."""
        assert M.PROBES

    def test_every_probe_points_at_a_file_that_exists(self):
        for p in M.PROBES:
            target = next((a for a in p.argv if a.endswith(".py")), None)
            assert target, f"{p.name} runs no script"
            assert (REPO / target).exists(), f"{p.name} -> missing {target}"

    def test_every_probe_declares_what_it_covers(self):
        for p in M.PROBES:
            assert p.covers.strip(), f"{p.name} declares no coverage statement"

    def test_no_probe_passes_a_base(self):
        """A --base on a scheduled run of `main` diffs main against itself, so
        the rule would pass over an empty diff — a green that checked nothing."""
        for p in M.PROBES:
            assert not any(a.startswith("--base") for a in p.argv), p.name

    def test_the_calendar_class_is_NOT_covered_and_that_is_asserted(self):
        """⚠️ INVERTED 2026-09-22 (E45), and the inversion is the point.

        This test used to assert the opposite: that some probe runs
        `check_open_items.py`, whose 21-day affirmation window crosses on the
        CALENDAR with nobody's diff, which no PR-time check can reach. That is
        still the reason a cadence is not optional — and the probe is GONE,
        because its register (`docs/claude/OPEN-ITEMS.json`) was archived by the
        2026-09-21 reset, so it had been reporting `register is MISSING` on
        every hourly run.

        Three ways to respond and only one is honest:

          - delete the test          -> the loss becomes invisible, which is how
                                        a coverage gap turns into folklore
          - keep it as written       -> a true statement about the repo (no
                                        calendar probe exists) fails CI forever
          - STATE THE GAP            -> assert it, on purpose, so re-adding a
                                        calendar probe FAILS here until someone
                                        updates this test deliberately

        The third. `we looked and found nothing` is recorded as itself.
        """
        calendar_probes = [p for p in M.PROBES
                           if "check_open_items.py" in " ".join(p.argv)]
        assert calendar_probes == [], (
            "a calendar-class probe is back in main-tree-watch: "
            f"{[p.name for p in calendar_probes]}. That is good news, not a "
            "failure — confirm it grades a register that exists and then update "
            "this test to assert coverage again.")

        # Positive control on the side that still holds data: this file's
        # subject must be readable, or the assertion above proves nothing.
        assert M.PROBES, "positive control: main-tree-watch still runs probes"


class TestItRefusesToClaimMainIsHealthy:
    def test_the_module_says_what_it_did_not_evaluate(self, capsys):
        M.main([])
        out = capsys.readouterr().out
        assert "NOT A STATEMENT THAT `main` IS HEALTHY" in out, out[-400:]

    def test_the_self_test_passes(self):
        assert M._self_test() == 0


class TestTheWorkflowIsWiredAndWatched:
    WF = REPO / ".github" / "workflows" / "main-tree-watch.yml"
    ALERT = REPO / ".github" / "workflows" / "claude-run-failure-alert.yml"

    def _load(self, path):
        yaml = pytest.importorskip("yaml")
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_the_workflow_exists_and_is_scheduled(self):
        d = self._load(self.WF)
        on = d.get(True) or d.get("on")
        assert "schedule" in on, "a watcher with no cadence watches nothing"

    def test_the_workflow_runs_the_checker(self):
        assert "check_main_tree_health.py" in self.WF.read_text(encoding="utf-8")

    def test_it_checks_out_main_explicitly_not_github_ref(self):
        """A workflow_dispatch from a topic branch would otherwise grade THAT
        branch's tree and report the verdict as `main`'s."""
        d = self._load(self.WF)
        steps = d["jobs"]["watch"]["steps"]
        co = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))
        assert co.get("with", {}).get("ref") == "main", co

    def test_it_is_in_the_failure_alert_watch_list(self):
        """Its own silence is the failure it watches for. The repo's
        cron-failure-watch guard enforces this too — verified red before the
        entry was added and green after — and this pins it from the test side."""
        d = self._load(self.ALERT)
        on = d.get(True) or d.get("on")
        watched = on["workflow_run"]["workflows"]
        assert "main-tree-watch" in watched
        assert "pr-queue-watch" in watched, "positive control: the parse works"

    def test_the_declared_name_matches_the_watch_list_entry(self):
        """The alert keys on the workflow's `name:`, not its filename, so a
        rename that touches only one of the two silently unwatches it."""
        assert self._load(self.WF)["name"] == "main-tree-watch"
