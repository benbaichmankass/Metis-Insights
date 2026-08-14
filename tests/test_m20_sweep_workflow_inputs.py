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
