"""Two pushes to `main` must not cancel each other.

`guards.yml`'s push run is the repo's only post-merge check: a PR's checks ran on
the merge REF, not on the commit the squash produced, so this run is the only
thing that ever looks at what actually landed. Cancelling it discards that one
look — and `main` went red on a duplicate register id TWICE on 2026-09-17, found
both times by a human tripping over a red PR.

⚠️ The block's own comment already said *"cancelling a main-branch run would lose
the post-merge signal"* while the expression beneath it did exactly that: it
included the EVENT (so a push could not cancel a PR run) and keyed the rest on
`github.ref`, which is the same value for every push to `main`. The fix reached
the instance and not the class — measured, 6 cancelled runs in 54 pushes.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "guards.yml"

_BLOCK = re.compile(r"^concurrency:\n  group: (?P<group>.+)\n  cancel-in-progress: (?P<cancel>.+)$",
                    re.M)


def _block():
    m = _BLOCK.search(WF.read_text(encoding="utf-8"))
    assert m, "guards.yml has no top-level concurrency block in the expected shape"
    return m.group("group").strip(), m.group("cancel").strip()


def _render(group: str, *, event: str, pr_number: str | None, ref: str, sha: str) -> str:
    """Evaluate the group expression the way Actions would, for one event.

    Only the three contexts this expression uses are modelled, and `||` is
    GitHub's falsy-coalesce: an absent `pull_request.number` is the empty string.
    """
    out = group
    out = out.replace("${{ github.event_name }}", event)
    out = out.replace("${{ github.event.pull_request.number || github.ref }}",
                      pr_number or ref)
    out = out.replace("${{ github.event.pull_request.number || github.sha }}",
                      pr_number or sha)
    assert "${{" not in out, f"an unmodelled context survived: {out}"
    return out


class TestTwoPushesToMainAreDistinct:
    def test_two_pushes_to_main_get_DIFFERENT_groups(self):
        group, cancel = _block()
        a = _render(group, event="push", pr_number=None,
                    ref="refs/heads/main", sha="aaaaaaa")
        b = _render(group, event="push", pr_number=None,
                    ref="refs/heads/main", sha="bbbbbbb")
        assert a != b, (
            "both pushes share one concurrency group, so with "
            f"cancel-in-progress={cancel} the second cancels the first — and on "
            "main that discards the only check of the first commit")

    def test_cancel_in_progress_is_still_on(self):
        """The fix is the KEY, not switching superseding off.

        Turning `cancel-in-progress` off would undo the 2026-08-06 stacking fix
        this block exists for, which is a worse trade than the bug.
        """
        _, cancel = _block()
        assert cancel == "true", cancel


class TestThePullRequestArmIsUntouched:
    def test_two_pushes_to_ONE_pr_still_share_a_group(self):
        group, _ = _block()
        a = _render(group, event="pull_request", pr_number="123",
                    ref="refs/pull/123/merge", sha="aaaaaaa")
        b = _render(group, event="pull_request", pr_number="123",
                    ref="refs/pull/123/merge", sha="bbbbbbb")
        assert a == b, (
            "superseding on a PR is correct and load-bearing — only the newest "
            "commit's verdict is meaningful there")

    def test_two_different_prs_do_not_share_a_group(self):
        group, _ = _block()
        a = _render(group, event="pull_request", pr_number="123",
                    ref="refs/pull/123/merge", sha="a")
        b = _render(group, event="pull_request", pr_number="124",
                    ref="refs/pull/124/merge", sha="b")
        assert a != b

    def test_a_push_can_never_cancel_a_pull_request_run(self):
        """The property the original comment was reaching for, kept."""
        group, _ = _block()
        push = _render(group, event="push", pr_number=None,
                       ref="refs/heads/main", sha="aaaaaaa")
        pr = _render(group, event="pull_request", pr_number="123",
                     ref="refs/pull/123/merge", sha="aaaaaaa")
        assert push != pr


class TestTheRequiredContractIsUnchanged:
    def test_the_job_id_is_still_the_required_context(self):
        """⚠️ `guards` is the REQUIRED status check.

        The file's own header records that a required context which no longer
        exists leaves every PR hanging forever, so a rename here is not a
        refactor — it is an outage.
        """
        body = WF.read_text(encoding="utf-8")
        assert re.search(r"^jobs:\n  guards:\n", body, re.M), \
            "the required check's job id is no longer `guards`"

    def test_the_push_trigger_still_exists(self):
        """Keying per-sha is pointless if the trigger is gone."""
        body = WF.read_text(encoding="utf-8")
        assert re.search(r"^  push:\n    branches: \[main\]$", body, re.M)

    @pytest.mark.parametrize("ctx", ["github.event_name", "github.sha",
                                     "github.event.pull_request.number"])
    def test_the_group_uses_exactly_the_contexts_the_renderer_models(self, ctx):
        """Vacuity control on `_render`.

        If the expression gains a context the renderer does not model, `_render`
        raises rather than silently returning a half-substituted string — but
        only if the tests above are still exercising it. This pins the three it
        uses, so a fourth cannot be added without this file noticing.
        """
        group, _ = _block()
        assert ctx in group
