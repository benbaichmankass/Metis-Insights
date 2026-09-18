"""A workflow that runs a Python script must install what that script IMPORTS.

WHY THIS IS GENERIC AND NOT THREE ONE-LINE FIXES
(`BL-20260917-A-WORKFLOW-THAT-RUNS-A-PYTHON-SCRIPT-IS-NOT-CHECKED-AGAINST-WHAT-THAT-SCRIPT-IMPORTS`).

`actions/setup-python` installs a bare interpreter. A job that then runs a repo
script gets whatever the stdlib provides and nothing else, and the failure is a
`ModuleNotFoundError` at the top of the run — cheap to fix, and invisible to
every local run and every CI job, because a developer's machine and the `guards`
job both have the dependency installed.

THIS EXACT CLASS HAS NOW BITTEN TWICE, AND THE SECOND TIME THE FIRST FIX'S OWN
COMMENT IS WHAT STOPPED IT SPREADING.

  * 2026-09-08 — `constraint-readout.yml`: 6 of 6 scheduled runs dead in ~14s at
    its self-test gate, missing PyYAML. Fixed at that ONE call site. Its comment
    then said, verbatim, *"render_due_list.py and render_session_brief.py are
    stdlib-only, which is why due-list.yml needs no install step and succeeds"*.

  * 2026-09-12 — `render_due_list.py`'s `src_spent_decision_edges` landed. It
    execs `scripts/ci/check_decision_answers.py`, which imports PyYAML at module
    scope. `render_due_list.py` stopped being stdlib-only and the sentence above
    became false, four days after it was written.

  * 2026-09-13 .. 2026-09-16 — `due-list.yml` failed **4 of 4** scheduled runs
    (#104-#107), each at its self-test gate, on
    `ModuleNotFoundError: No module named 'yaml'`. `docs/claude/DUE.json` — "the
    ONE surface that answers what is due right now" — sat four days stale.

So the invariant belongs HERE, where one edit to any script is checked against
EVERY workflow that runs it, rather than in a prose claim about which file is
stdlib-only that a reader has to trust and that goes stale silently.

WHAT "REACHES" MEANS, AND WHY A PLAIN IMPORT WALK IS NOT ENOUGH
The edge that caused the outage is NOT an `import`. `render_due_list.py` builds
the guard's path from a constant and runs it through
`importlib.util.spec_from_file_location` + `exec_module`. An AST import walk
sees nothing. So the graph follows two kinds of edge:

  * **import edges** — a first-party module named in any `import` / `from`, and
  * **exec edges** — a quoted `scripts|src|ml/....py` path literal, but ONLY in a
    file that also contains an execution idiom (`spec_from_file_location`,
    `exec_module`, `runpy.run_path`).

The exec-edge restriction is load-bearing for precision: without it a guard that
merely NAMES a path in a message or a docstring gains an edge. `check_scope_overlap.py`
was flagged through `src/runtime/orders.py` exactly that way, and it contains ZERO
execution idioms, so the restriction removes it. It is pinned as a control below.

⚠️ THE TWO FALSE POSITIVES THE LOOSE DRAFT PRODUCED CAME FROM DIFFERENT RULES,
and saying so matters because each rule is separately load-bearing:
`check_scope_overlap.py` was removed by the exec-edge restriction, while
`branch-protection-sync.yml` was removed by COMMENT STRIPPING — it names
`run_guards.py` only inside a `#` comment and runs it nowhere.

⚠️ AND `run_guards.py` IS A TRUE POSITIVE, NOT A FALSE ONE. An earlier draft of
this docstring called it a false positive; that was wrong and is corrected here.
It really does exec its guards (`spec_from_file_location` + `exec_module`) and one
of them, `check_manifest_scope_constants.py`, imports PyYAML at module scope.
`guards.yml::guards` installs requirements, so it is correctly covered — which is
the case that shows the graph finding a real multi-hop reach rather than nothing.

WHAT "NEEDS IT" MEANS: AN UNGUARDED MODULE-SCOPE IMPORT, NOT ANY IMPORT
A `try: import yaml / except: ...` inside a function is deliberate graceful
degradation and must NOT be treated as a hard dependency — `blocked_lane_watch.py`
and `broker_bracket_reconcile.py` both do exactly that, on purpose, and both are
pinned as controls. Only a module-scope import outside a `try` crashes the run.

⚠️ THIS IS A NECESSARY CONDITION, NOT A SUFFICIENT ONE. It grades PyYAML, which
is the dependency that has actually caused two outages, and it grades a job by
whether SOME install step covers it — not by whether that step runs before the
use, and not by version adequacy. A job importing some OTHER third-party package
passes this test and still dies. Read a pass as "this class of failure is not
present", never as "the dependencies are right".
"""
from __future__ import annotations

import ast
import glob
import os
import re

import yaml as _yaml  # the test harness's own; unrelated to what it grades

WORKFLOW_GLOB = ".github/workflows/*.yml"

#: A script invocation is matched GENEROUSLY — any mention of a repo script path
#: inside a comment-stripped `run:` block counts. Over-matching costs a job an
#: install step it may not need (seconds); under-matching costs a dead cron.
SCRIPT_REF = re.compile(r"(scripts/(?:ops|ci)/[A-Za-z0-9_]+\.py)")

#: A quoted first-party path literal — the shape `spec_from_file_location` takes.
PATH_LITERAL = re.compile(r"""["']((?:scripts|src|ml)/[A-Za-z0-9_/.\-]+\.py)["']""")

#: A file gets exec edges only if it demonstrably executes a path.
EXEC_IDIOMS = ("spec_from_file_location", "exec_module", "runpy.run_path", "run_path(")

#: What counts as "this job installed PyYAML". A requirements file counts because
#: every requirements file in this repo pins it; that is asserted below rather
#: than assumed.
INSTALLS_YAML = re.compile(r"pip install[^\n]*(?:pyyaml|requirements)", re.I)

REQUIREMENTS_FILES = ("requirements.txt", "requirements-dev.txt", "requirements-test.txt")


# ── the graph ────────────────────────────────────────────────────────────────

def module_scope_yaml_import(src: str) -> bool:
    """Does this source import PyYAML in a way a missing module would CRASH?

    Depth-0 nodes only. A `try:`/`if TYPE_CHECKING:` wrapper is a `Try`/`If` node,
    so its nested import is not in `tree.body` and correctly does not count.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for node in tree.body:
        if isinstance(node, ast.Import):
            if any(a.name == "yaml" or a.name.startswith("yaml.") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "yaml" or node.module.startswith("yaml."):
                return True
    return False


def _module_candidates(dotted: str) -> list[str]:
    p = dotted.replace(".", "/")
    return [p + ".py", p + "/__init__.py"]


def edges(src: str, *, exists=os.path.exists) -> set[str]:
    """Files this source can pull in: import edges always, exec edges if it execs."""
    out: set[str] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                out.update(c for c in _module_candidates(n) if exists(c))
    if any(i in src for i in EXEC_IDIOMS):
        out.update(m for m in PATH_LITERAL.findall(src) if exists(m))
    return out


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def reaches_hard_yaml(root: str, _cache: dict | None = None) -> str | None:
    """The file in `root`'s closure that hard-imports PyYAML, or None."""
    cache = {} if _cache is None else _cache
    seen, stack = {root}, [root]
    while stack:
        cur = stack.pop()
        if cur not in cache:
            src = _read(cur)
            cache[cur] = (edges(src), module_scope_yaml_import(src))
        nxt, hard = cache[cur]
        if hard:
            return cur
        for n in nxt:
            if n not in seen:
                seen.add(n)
                stack.append(n)
    return None


# ── the workflows ────────────────────────────────────────────────────────────

def strip_shell_comments(text: str) -> str:
    """Drop `#` comments so a script NAMED in a comment is not read as run.

    `branch-protection-sync.yml` mentions `scripts/ci/run_guards.py` only inside
    a comment; without this it is graded as an invocation. Pinned as a control.
    """
    return "\n".join(re.sub(r"(^|\s)#.*$", "", line) for line in text.splitlines())


def jobs_with_script_invocations(pattern: str = WORKFLOW_GLOB):
    """(workflow basename, job name, scripts invoked, installs_yaml) per job."""
    for path in sorted(glob.glob(pattern)):
        doc = _yaml.safe_load(_read(path))
        if not isinstance(doc, dict):
            continue
        for job_name, job in (doc.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            steps = [s for s in (job.get("steps") or []) if isinstance(s, dict)]
            run_text = strip_shell_comments("\n".join(str(s.get("run", "")) for s in steps))
            scripts = sorted(set(SCRIPT_REF.findall(run_text)))
            if not scripts:
                continue
            yield os.path.basename(path), job_name, scripts, bool(INSTALLS_YAML.search(run_text))


# ── THE INVARIANT ────────────────────────────────────────────────────────────

def test_every_workflow_installs_the_yaml_its_scripts_hard_import():
    cache: dict = {}
    offenders = []
    for wf, job, scripts, installs in jobs_with_script_invocations():
        if installs:
            continue
        for s in scripts:
            via = reaches_hard_yaml(s, cache)
            if via:
                offenders.append(f"{wf}::{job} runs {s}, which reaches a module-scope "
                                 f"`import yaml` in {via}, and installs nothing")
    assert not offenders, (
        "a workflow job runs a script whose import closure hard-imports PyYAML "
        "without installing it — that job dies with ModuleNotFoundError:\n  "
        + "\n  ".join(offenders)
    )


def test_the_probe_can_find_a_positive_when_an_install_is_removed():
    """POSITIVE CONTROL — a clean run above must not be a probe that cannot fail.

    Re-grades every job as if its install step were absent. At least one must be
    flagged, or the invariant is vacuous.
    """
    cache: dict = {}
    would_flag = [
        f"{wf}::{job} ({s} -> {via})"
        for wf, job, scripts, _ in jobs_with_script_invocations()
        for s in scripts
        if (via := reaches_hard_yaml(s, cache))
    ]
    assert would_flag, (
        "no job in the tree runs a yaml-reaching script at all, so the invariant "
        "above passed without examining anything — it has gone vacuous"
    )


def test_the_three_repaired_workflows_still_carry_their_install():
    """The measured outage, pinned by name so a revert cannot be silent."""
    have = {f"{wf}::{job}": installs for wf, job, _, installs in jobs_with_script_invocations()}
    for key in ("due-list.yml::render", "error-feed-digest.yml::digest", "probes.yml::run"):
        assert have.get(key) is True, (
            f"{key} runs render_due_list.py, whose closure hard-imports PyYAML. "
            "Its cron failed 4 of 4 scheduled runs without this step."
        )


def test_requirements_files_really_do_pin_pyyaml():
    """INSTALLS_YAML accepts a requirements install; that acceptance is measured."""
    found = [f for f in REQUIREMENTS_FILES
             if os.path.exists(f) and re.search(r"^\s*pyyaml", _read(f), re.I | re.M)]
    assert found, (
        "no requirements file pins PyYAML, so treating `pip install -r requirements*.txt` "
        "as satisfying the dependency is an unchecked assumption"
    )


# ── PLANTED DEFECTS — each must make the classifier answer differently ────────

def test_a_guarded_import_is_not_a_hard_dependency():
    assert module_scope_yaml_import("import yaml\n")
    assert module_scope_yaml_import("from yaml import safe_load\n")
    assert not module_scope_yaml_import("try:\n    import yaml\nexcept Exception:\n    yaml = None\n")
    assert not module_scope_yaml_import("def f():\n    import yaml\n    return yaml\n")
    assert not module_scope_yaml_import(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import yaml\n")


def test_real_files_pin_both_sides_of_that_distinction():
    """Synthetic strings prove the rule; these prove it on the files it grades."""
    assert module_scope_yaml_import(_read("scripts/ci/check_decision_answers.py")), (
        "check_decision_answers.py imports yaml at module scope — it is the file "
        "whose ModuleNotFoundError killed the due-list cron")
    for graceful in ("scripts/ops/blocked_lane_watch.py", "scripts/ops/broker_bracket_reconcile.py"):
        assert not module_scope_yaml_import(_read(graceful)), (
            f"{graceful} imports yaml inside a try/except on purpose; grading that "
            "as a hard dependency would demand installs for deliberate degradation")


def test_an_exec_edge_needs_an_execution_idiom():
    exists = lambda p: p == "scripts/ci/check_decision_answers.py"  # noqa: E731
    execs = ('G = "scripts/ci/check_decision_answers.py"\n'
             'spec = importlib.util.spec_from_file_location("g", G)\n')
    mentions = 'MSG = "see scripts/ci/check_decision_answers.py for the rule"\n'
    assert edges(execs, exists=exists) == {"scripts/ci/check_decision_answers.py"}
    assert edges(mentions, exists=exists) == set(), (
        "a path merely NAMED in a message must not create an edge — that over-"
        "approximation flagged check_scope_overlap.py and run_guards.py through "
        "src/runtime/orders.py, two live false positives")


def test_a_mention_only_script_does_not_reach_yaml():
    """The live false positive the exec-edge rule removed, pinned on the real file."""
    assert "spec_from_file_location" not in _read("scripts/ci/check_scope_overlap.py"), (
        "this control is only meaningful while the file execs nothing")
    assert reaches_hard_yaml("scripts/ci/check_scope_overlap.py") is None, (
        "check_scope_overlap.py only NAMES first-party paths (src/runtime/orders.py "
        "among them); if it now reaches a hard yaml import, establish whether that "
        "is real before adding an install — a false positive here demands installs "
        "everywhere and is how a guard gets disabled instead of fixed")


def test_a_real_multi_hop_exec_reach_is_found_and_is_already_covered():
    """`run_guards.py` is a TRUE positive, and the job that runs it installs.

    ⚠️ An earlier draft of this file asserted the opposite — that `run_guards.py`
    was a false positive. It is not: it execs its guards, and
    `check_manifest_scope_constants.py` imports PyYAML at module scope. Getting
    this backwards would have been the dangerous direction, since the fix for a
    "false positive" is to weaken the graph until the real reach disappears.
    """
    via = reaches_hard_yaml("scripts/ci/run_guards.py")
    assert via is not None, (
        "run_guards.py execs its guards and at least one imports yaml; finding "
        "nothing means the exec-edge rule has been narrowed past the real case")
    assert module_scope_yaml_import(_read(via)), f"{via} was reported but does not hard-import yaml"
    covered = {f"{wf}::{job}": inst for wf, job, s, inst in jobs_with_script_invocations()
               if any("run_guards.py" in x for x in s)}
    assert covered, "no workflow job runs run_guards.py — the control has no subject"
    assert all(covered.values()), f"a job runs run_guards.py without installing PyYAML: {covered}"


def test_branch_protection_sync_names_run_guards_only_in_a_comment():
    """The OTHER false positive, removed by comment stripping rather than by the graph."""
    path = ".github/workflows/branch-protection-sync.yml"
    if not os.path.exists(path):
        return
    raw = _read(path)
    if "run_guards.py" not in raw:
        return
    jobs = {f"{wf}::{job}" for wf, job, s, _ in jobs_with_script_invocations()
            if wf == os.path.basename(path) and any("run_guards.py" in x for x in s)}
    assert not jobs, (
        "branch-protection-sync.yml mentions run_guards.py only in a comment; "
        f"grading it as an invocation would demand an install it never needs: {jobs}")


def test_the_exec_edge_that_caused_the_outage_is_actually_found():
    """Without this edge the whole check is inert on the case it was built for."""
    assert reaches_hard_yaml("scripts/ops/render_due_list.py") == \
        "scripts/ci/check_decision_answers.py"


def test_a_script_named_only_in_a_shell_comment_is_not_an_invocation():
    raw = "  # python3 scripts/ops/render_due_list.py --write\n  echo hi\n"
    assert SCRIPT_REF.findall(strip_shell_comments(raw)) == []
    assert SCRIPT_REF.findall(raw) == ["scripts/ops/render_due_list.py"], (
        "the comment-stripping must be what removes it, not the pattern")


def test_install_detection_accepts_the_spellings_the_repo_uses():
    assert INSTALLS_YAML.search('python3 -m pip install --quiet --upgrade pip "pyyaml>=6.0"')
    assert INSTALLS_YAML.search("pip install -r requirements.txt")
    assert INSTALLS_YAML.search("pip install PyYAML")
    assert not INSTALLS_YAML.search("pip install ruff")
    assert not INSTALLS_YAML.search("echo pyyaml"), "a mention is not an install"
