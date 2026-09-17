"""A guard that cannot read its own input must REFUSE, never report a clean bill.

⚠️ WHAT WENT WRONG, MEASURED RATHER THAN IMAGINED. `check_backlog_refs.py::_git`
ran `git` and returned `.stdout`, discarding `returncode`. A base ref that does
not resolve therefore produced an empty diff, zero introduced references, and:

    OK — every tracking id this change introduces resolves to a filed backlog row.

The same commit, graded twice against **byte-identical base trees** on
2026-09-17 while investigating the four-day due-list outage:

    --base origin/histbase   -> exit 1, names the dangling fragment
    --base hb12245           -> exit 0, "OK — every tracking id ... resolves"

`hb12245` was a remote-tracking ref spelled without its `origin/` prefix, so
`git diff hb12245...HEAD` died with `fatal: bad revision` — and this guard
called it clean, for all four PRs in the sample. A false all-clear was avoided
only because that run carried a known-positive control, which also came back
clean and gave the typo away.

⚠️ THE FILE ALREADY KNEW. Two of its three reads are careful about exactly this
distinction, and both are FALLBACK reads: `_refs_in_file_at` returns `None` for
*the file did not exist* rather than `set()`, citing the Collapsed-states rule
by name; `_refs_anywhere_at` raises above rc 1, noting that reading a git
failure as "nothing was cited at base" *"would silently restore the blindness
this function removes"*. The read that computes **the diff itself** did the
thing they both refuse.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "ops" / "check_backlog_refs.py"


def _load():
    spec = importlib.util.spec_from_file_location("_backlog_refs_cannot_look", GUARD)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load()


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GUARD), *args],
                          capture_output=True, text=True, cwd=REPO)


# ── the unit ───────────────────────────────────────────────────────────────

def test_git_raises_rather_than_returning_empty_output():
    with pytest.raises(M.GitRead) as exc:
        M._git(["rev-parse", "--verify", "definitely-not-a-ref-42"], REPO)
    # The message must carry the CAUSE, not just the fact of failure —
    # UNPROVENANCED DIAGNOSTIC OUTPUT sub-class A is what cost four days here.
    assert "exited" in str(exc.value)


def test_git_still_returns_stdout_on_success():
    out = M._git(["rev-parse", "--abbrev-ref", "HEAD"], REPO)
    assert out.strip()


def test_refs_in_added_lines_propagates_rather_than_reporting_nothing():
    """The empty dict this used to return is what main() read as 'all clear'."""
    with pytest.raises(M.GitRead):
        M.refs_in_added_lines("no-such-base-ref-42", REPO)


# ── the behaviour a caller actually sees ───────────────────────────────────

def test_an_unresolvable_base_exits_2_and_never_claims_OK():
    p = _run("--base", "no-such-base-ref-42")
    assert p.returncode == 2, (p.returncode, p.stdout[-400:])
    assert "OK —" not in p.stdout
    assert "WE DID NOT LOOK" in p.stdout
    # …and it says how to fix the commonest cause, which is the one that bit.
    assert "origin/" in p.stdout


def test_a_prefixless_remote_ref_is_the_measured_case():
    """The exact typo from 2026-09-17: a remote-tracking ref without `origin/`.

    Built deterministically rather than borrowed from whatever branches this
    checkout happens to have. My first draft named a real branch and PASSED
    VACUOUSLY in the wrong direction — a LOCAL branch of that name existed, so
    the prefixless spelling resolved after all and the guard correctly exited
    0. The premise has to be constructed, and asserted, not assumed.
    """
    name = f"pytest-tmp-cannot-look-{__import__('os').getpid()}"
    ref = f"refs/remotes/origin/{name}"
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "-C", str(REPO), "update-ref", ref, head], check=True)
    try:
        # Premise, asserted rather than assumed: prefixed resolves, bare does not.
        assert subprocess.run(["git", "-C", str(REPO), "rev-parse", "--verify",
                               f"origin/{name}"], capture_output=True).returncode == 0
        assert subprocess.run(["git", "-C", str(REPO), "rev-parse", "--verify",
                               name], capture_output=True).returncode != 0

        bare = _run("--base", name)
        assert bare.returncode == 2, (bare.returncode, bare.stdout[-300:])
        assert "OK —" not in bare.stdout

        prefixed = _run("--base", f"origin/{name}")
        assert prefixed.returncode in (0, 1), prefixed.stdout[-300:]
    finally:
        subprocess.run(["git", "-C", str(REPO), "update-ref", "-d", ref],
                       capture_output=True)


def test_a_resolvable_base_still_reaches_a_verdict():
    p = _run("--base", "origin/main")
    assert p.returncode in (0, 1), (p.returncode, p.stderr[-300:])
    assert ("OK —" in p.stdout) or ("resolve to NOTHING" in p.stdout)


def test_could_not_look_and_found_dangling_are_different_exit_codes():
    """2 and 1 must stay apart, or the fix collapses a different pair.

    Reporting "I could not look" with the same code as "I looked and found
    problems" would leave a caller unable to tell a broken invocation from a
    real finding — which is the same collapse one level up.
    """
    could_not_look = _run("--base", "no-such-base-ref-42").returncode
    assert could_not_look == 2
    assert could_not_look != 1


def test_the_all_path_is_untouched():
    p = _run("--all")
    assert p.returncode in (0, 2), (p.returncode, p.stderr[-300:])


# ── the class, not just this one function ──────────────────────────────────

def test_every_subprocess_read_in_this_module_grades_its_own_failure():
    """The defect was ONE unguarded read among three. Forbid the shape.

    Two of the three already refused a git failure; the third did not, and it
    was the one whose result decides whether the guard looks at anything. A
    fix that only repairs `_git` leaves the next `subprocess.run` free to
    repeat it, so this asserts the invariant over the whole module: any
    function that shells out must mention `returncode` somewhere in its body.

    It is deliberately a SHAPE check and not a semantic one — it cannot tell a
    correct rc check from a wrong one. It stops the read that ignores failure
    ENTIRELY, which is the failure that actually occurred.
    """
    tree = ast.parse(GUARD.read_text(encoding="utf-8"))
    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        shells_out = any(
            isinstance(n, ast.Attribute) and n.attr == "run"
            and isinstance(n.value, ast.Name) and n.value.id == "subprocess"
            for n in ast.walk(fn)
        )
        if not shells_out:
            continue
        grades_it = any(
            (isinstance(n, ast.Attribute) and n.attr == "returncode")
            for n in ast.walk(fn)
        )
        if not grades_it:
            offenders.append(f"{fn.name} (line {fn.lineno})")
    assert not offenders, (
        "function(s) shelling out without inspecting `returncode` — a failed "
        "read must never be returned as empty output:\n  " + "\n  ".join(offenders)
    )
