"""Every `workflow_dispatch` input the M20 sweep declares must actually be READ.

THE FAILURE THIS EXISTS TO CATCH is not a typo, it is a *silent narrowing*: a
declared-but-unread input reports a FULL result under a NARROWED label. The
caller asks for X, the workflow ignores X, and the artifact says X. That is the
unprovenanced-diagnostic class (`docs/CLAUDE-RULES-CANONICAL.md`), and this repo
has already been bitten by both halves of it on THIS workflow:

  * `split_mode` was declared and never passed to the script (fixed 2026-08-13).
    A caller asking for the fixed 2025-07-01 boundary silently got a DERIVED
    one, and the corpus row recorded `split: null` so it could not even say
    which. Measured cost: on `trend_donchian_sol_prop` the derived window was
    OOS 24 against the fixed window's 65 — across the 25-trade floor, turning
    every gradeable cell `insufficient_base`.

  * `split_target_oos` was the *other* half — not declared at all, so the
    autonomous workflow path could not reach a CLI flag that had existed since
    #8965. Only the relay could. Measured 2026-08-14: the nine-pair lever-OFF
    arm returned base OOS 23/24/24/24/23 on five legs (every one ungradeable)
    and 25 on the single leg that returned a PASS. Six measured cells, one
    verdict, and the difference was one or two trades — decided by a knob the
    dispatching path had no way to set.

The two are the same defect wearing different clothes: the set of knobs the
workflow EXPOSES and the set it USES must be the same set. This test asserts
that, in both directions.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "m20-exit-lever-sweep.yml"


def _doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _declared_inputs(doc: dict) -> set[str]:
    # PyYAML parses the bare `on:` key as the boolean True, not the string.
    on = doc.get("on", doc.get(True))
    return set((on["workflow_dispatch"]["inputs"] or {}).keys())


def _referenced_inputs(text: str) -> set[str]:
    return set(re.findall(r"inputs\.([A-Za-z_][A-Za-z0-9_]*)", text))


def test_every_declared_input_is_actually_read() -> None:
    """A declared input nothing reads is a promise the workflow does not keep."""
    text = WORKFLOW.read_text()
    declared = _declared_inputs(_doc())
    referenced = _referenced_inputs(text)

    unread = sorted(declared - referenced)
    assert not unread, (
        f"{WORKFLOW.name} declares workflow_dispatch input(s) that no step "
        f"reads: {unread}. A caller can set them and the run will silently "
        f"ignore the request while labelling the output as though it honoured "
        f"it. Either wire the input into the job's env + the script "
        f"invocation, or remove it."
    )


def test_every_referenced_input_is_declared() -> None:
    """The reverse: `inputs.foo` that no one can set is always empty."""
    text = WORKFLOW.read_text()
    declared = _declared_inputs(_doc())
    referenced = _referenced_inputs(text)

    undeclared = sorted(referenced - declared)
    assert not undeclared, (
        f"{WORKFLOW.name} references inputs that are not declared: "
        f"{undeclared}. These evaluate to empty on every run, so whatever "
        f"they gate is permanently off — and looks configurable."
    )


def test_the_two_split_knobs_reach_the_script() -> None:
    """Both halves of the boundary decision must reach the CLI, not just env.

    Binding one into `env:` and forgetting the command line is exactly how
    `split_mode` shipped broken: the value was present in the job and absent
    from the process that used it.

    VERIFIED, NOT ASSUMED: run against `origin/main`'s pre-fix copy of this
    workflow, THIS test is the one that fails (`--split-target-oos` absent).
    `test_every_declared_input_is_actually_read` PASSES there, because the
    pre-fix defect was *never declaring the input at all* — a hole is invisible
    to a consistency check between two sets that both omit it. Worth stating
    because the natural assumption is that the declared/unread test covers this
    class, and it does not: it catches the `split_mode` half only.
    """
    text = WORKFLOW.read_text()
    for flag in ("--split-mode", "--split-target-oos"):
        assert flag in text, (
            f"{WORKFLOW.name} never passes {flag} to "
            f"m20_fleet_exit_sweep.py. The boundary is then decided by the "
            f"script's default rather than by the dispatch, and the run "
            f"reports under the dispatch's label."
        )


def test_split_target_oos_defaults_to_reproducing_history() -> None:
    """An omitted value must reproduce prior runs, never silently change them.

    The knob exists because the script's own default (MIN_OOS_TRADES) aims the
    derived boundary at exactly the floor the verdict requires. Fixing that by
    changing the WORKFLOW's default would retroactively make every re-run
    non-comparable to the corpus it is being compared against — so the default
    stays empty and the caller opts in.
    """
    on = _doc().get("on", _doc().get(True))
    spec = on["workflow_dispatch"]["inputs"]["split_target_oos"]
    assert spec.get("default", "") == "", (
        "split_target_oos must default to empty so an omitted value falls "
        "through to the script's own default. A non-empty default here would "
        "silently re-target every existing dispatch."
    )
    assert spec.get("required") is False


def test_the_scan_would_catch_a_planted_unread_input() -> None:
    """Negative control: prove the check can fail, not just that it passes.

    A guard that has never been shown to fire is a guard whose green is
    unproven — the same reason `docs/CLAUDE-RULES-CANONICAL.md` treats a search
    returning nothing as needing a denominator.
    """
    declared = {"legs", "planted_never_read"}
    referenced = _referenced_inputs("uses ${{ inputs.legs }} only")
    assert sorted(declared - referenced) == ["planted_never_read"]


def _commit_corpus_step(doc: dict) -> str:
    steps = doc["jobs"]["corpus"]["steps"]
    return next(s["run"] for s in steps if s.get("name") == "Commit the corpus")


def test_conflict_rederive_uses_the_dispatched_extractor_not_the_branch_copy() -> None:
    """The re-derive after `reset --hard` must not run the TARGET branch's extractor.

    `rebase_onto_target`'s conflict path hard-resets the worktree to the corpus
    branch and then re-runs the extractor. The reset reverts the extractor
    ITSELF, so re-deriving via the worktree path silently runs whatever copy
    that long-diverged branch happens to carry — dropping every field added
    since, while the job stays green and the summary reports the full row count.

    MEASURED (2026-08-16, run 31976325152): the conflict fired, the reset landed
    123 commits behind main, and all 52 rows came back missing the eight
    `live_tp_reach_r_*` keys #9037 added. This is not a rare race — the corpus
    branch never merges main, so a main-dispatched run takes add/add conflicts
    across dozens of unrelated files every time, making the conflict path the
    NORMAL path.
    """
    run = _commit_corpus_step(_doc())

    assert 'cp scripts/research/m20_corpus_extract.py "$RUNNER_TEMP/' in run, (
        "the 'Commit the corpus' step must preserve the dispatched extractor "
        "BEFORE any git operation can revert it; without the copy the conflict "
        "path has nothing correct left to re-derive with."
    )

    rederive = run.split('git reset --hard "origin/$TARGET"', 1)
    assert len(rederive) == 2, (
        "expected the hard-reset re-derive path to still exist in the step; if "
        "it was removed, this guard is measuring nothing and must be updated."
    )
    after_reset = rederive[1]
    assert "python3 scripts/research/m20_corpus_extract.py" not in after_reset, (
        "the re-derive after `git reset --hard` invokes the WORKTREE extractor, "
        "which the reset just replaced with the corpus branch's stale copy. Row "
        "fields added since that branch last moved will be silently absent and "
        "the rows will still look complete. Use the preserved dispatched copy."
    )
    assert '"$RUNNER_TEMP/extract_dispatched.py"' in after_reset, (
        "the re-derive must invoke the preserved dispatched extractor."
    )


def test_the_rederive_guard_would_catch_the_regression_it_exists_for() -> None:
    """Negative control: the guard must fail on the exact pre-fix shape."""
    pre_fix = (
        'git reset --hard "origin/$TARGET"\n'
        "python3 scripts/research/m20_corpus_extract.py --in sweep_out\n"
    )
    after_reset = pre_fix.split('git reset --hard "origin/$TARGET"', 1)[1]
    assert "python3 scripts/research/m20_corpus_extract.py" in after_reset


def test_the_rederive_rebuilds_the_commit_message_from_its_own_extraction() -> None:
    """The commit message must describe the extraction actually being committed.

    It used to be built ONCE, before the rebase, and REUSED by the conflict
    path — which re-runs the extractor after `reset --hard`. So a conflict-path
    commit carried the counts of a computation whose result had been discarded.

    MEASURED (2026-08-17, corpus commit 54e9b63e): the message read
    `superseded: 0 ... corpus now: 1316 rows` while the file it committed held
    998 rows with 52 superseded. Both numbers were real; only one described the
    commit. Verifying that repair therefore had to read the FILE, because the
    message could not be trusted — the unprovenanced-diagnostic class.
    """
    run = _commit_corpus_step(_doc())

    assert "write_commitmsg() {" in run, (
        "expected ONE writer for the commit message; two inline constructions "
        "would be free to drift, which is the defect this guards.")

    after_reset = run.split('git reset --hard "origin/$TARGET"', 1)[1]
    before_commit = after_reset.split("git commit -F .commitmsg", 1)[0]
    assert "write_commitmsg" in before_commit, (
        "the conflict path commits without rebuilding the message, so it will "
        "report the DISCARDED extraction's counts over the rows it actually "
        "lands. Call write_commitmsg after the re-derive.")


def test_the_commitmsg_guard_would_catch_the_regression() -> None:
    """Negative control: the pre-fix shape must fail the predicate above."""
    pre_fix = (
        'git reset --hard "origin/$TARGET"\n'
        "python3 extract.py | tee extract.out\n"
        "git add docs/research/m20-sweep-corpus.jsonl\n"
        "git commit -F .commitmsg\n"
    )
    after_reset = pre_fix.split('git reset --hard "origin/$TARGET"', 1)[1]
    before_commit = after_reset.split("git commit -F .commitmsg", 1)[0]
    assert "write_commitmsg" not in before_commit
