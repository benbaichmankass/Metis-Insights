"""`require('@actions/github')` crashed the arming relay repo-wide.

MEASURED 2026-09-12. #11889 merged at 09:56:31Z and every branch that merged
`main` afterwards failed `open-and-automerge` with
``Unhandled error: Error: Cannot find module '@actions/github'``. **POPULATION:
the last 15 `claude-pr-automerge` runs — 9 failed, across four lanes
(mi278/mi279/mi280/mi285).** The survivors were branches that had not yet
merged `main` and so still ran their own older copy of the workflow, since a
`push`-triggered workflow runs the branch's own file — **so it spread as people
synced**, and syncing was exactly what everyone was being told to do to pick up
the new per-branch merge slot.

⚠️ **THE CHANGE THAT INTRODUCED IT WAS NEVER RUN BY THE WORKFLOW IT CHANGED.**
#11889's merge commit carries `guards`, `pytest-run`, `pytest-collect`,
`repo-inventory`, `gate`, `sync` and `reconcile` — and **no `open-and-automerge`
run at all**.

⚠️ **THESE TESTS RUN THE REAL JAVASCRIPT**, extracted from the workflow and
executed under `node`, because the defect is a require that resolves in one
runtime and not another — something no YAML assertion can see. A test that
grepped for `__original_require__` would pass on a helper that still threw.

⚠️ **AND THE SECOND HALF IS THE ONE THAT MATTERS MORE THAN THE CRASH.**
`openerKind` must key on the CLIENT WE BUILT, not on the TOKEN WE HOLD. Keying
it on the token would report `pat` after a fallback and tell step 3 the checks
attached when they did not — arming a head nothing is measuring, which is the
precise defect #11889 was written to prevent.

Run: ``python3 -m pytest tests/test_automerge_pat_client_fallback.py``
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WF = REPO / ".github" / "workflows" / "claude-pr-automerge.yml"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is required to execute the workflow's own JavaScript")


def _script() -> str:
    """The `open-and-automerge` step's script, straight from the workflow."""
    import yaml
    doc = yaml.safe_load(WF.read_text(encoding="utf-8"))
    for job in doc["jobs"].values():
        for st in job.get("steps") or []:
            body = (st.get("with") or {}).get("script")
            if body and "_patClient" in body:
                return body
    raise AssertionError("the _patClient helper was not found in the workflow — "
                         "extraction is stale and these tests would prove nothing")


def _helper() -> str:
    src = _script()
    start = src.index("function _patClient(")
    end = src.index("const patClient =", start)
    return textwrap.dedent(src[start:end])


def test_the_helper_is_actually_found():
    """Guards the extraction itself: a rename would otherwise leave every test
    below exercising nothing and reporting green."""
    h = _helper()
    assert "__original_require__" in h
    assert "core.warning" in h


def _run_node(prelude: str) -> dict:
    """Execute the REAL helper under node with a stubbed environment."""
    js = f"""
    const warnings = [];
    const core = {{ warning: (m) => warnings.push(String(m)) }};
    const github = {{ __kind: 'github_token_client' }};
    {prelude}
    {_helper()}
    const client = _patClient('tok');
    console.log(JSON.stringify({{
      kind: client === null ? null : (client.__kind || 'built'),
      warnings,
    }}));
    """
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, f"the helper THREW — it must never crash:\n{r.stderr}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_it_uses_the_documented_escape_hatch_FIRST():
    out = _run_node("""
    globalThis.__original_require__ = (m) =>
      m === '@actions/github' ? { getOctokit: () => ({ __kind: 'pat_via_hatch' }) }
                              : (() => { throw new Error('nope'); })();
    const require = () => { throw new Error('bare require must not be reached'); };
    """)
    assert out["kind"] == "pat_via_hatch"
    assert out["warnings"] == [], "a working hatch must warn about nothing"


def test_it_falls_through_to_a_bare_require():
    out = _run_node("""
    const require = (m) =>
      m === '@actions/github' ? { getOctokit: () => ({ __kind: 'pat_via_require' }) }
                              : (() => { throw new Error('nope'); })();
    """)
    assert out["kind"] == "pat_via_require"
    assert out["warnings"] == []


def test_BOTH_failing_returns_null_and_NEVER_throws():
    """THE DEFECT. Before this, the unhandled error took the whole job down."""
    out = _run_node("""
    globalThis.__original_require__ = () => { throw new Error("Cannot find module '@actions/github'"); };
    const require = () => { throw new Error("Cannot find module '@actions/github'"); };
    """)
    assert out["kind"] is None, "it must return null, not a half-built client"


def test_and_that_fallback_is_LOUD():
    """A silent swallow would leave four lanes unable to land and unable to see
    why — which is how the crash went unexplained for half an hour."""
    out = _run_node("""
    globalThis.__original_require__ = () => { throw new Error('x'); };
    const require = () => { throw new Error('x'); };
    """)
    assert len(out["warnings"]) == 1, out["warnings"]
    w = out["warnings"][0]
    assert "REFUSE to arm" in w, "it must say what happens next, not just what failed"
    assert "GITHUB_TOKEN" in w
    assert "fix it" in w.lower(), "it must not read as an acceptable steady state"


def test_a_missing_original_require_is_not_itself_fatal():
    """Older runtimes have no hatch at all. That is a fall-through, not a crash."""
    out = _run_node("""
    const require = (m) => ({ getOctokit: () => ({ __kind: 'pat_via_require' }) });
    """)
    assert out["kind"] == "pat_via_require"


# ── the half that matters more than the crash ───────────────────────────────

def test_openerKind_keys_on_the_CLIENT_not_the_TOKEN():
    """Keying on the token would report `pat` after a fallback and tell step 3
    the checks attached when they did not — arming a head nothing is measuring,
    the exact defect #11889 exists to prevent."""
    src = _script()
    m = re.search(r"openerKind = (\w+) \? 'pat' : 'github_token';", src)
    assert m, "the openerKind assignment was not found"
    assert m.group(1) == "patClient", (
        f"openerKind keys on `{m.group(1)}`; it must key on `patClient`, because "
        "holding a token and having built a client are different facts")


def test_the_opener_is_the_client_or_github_never_a_throw():
    src = _script()
    assert "const opener = patClient || github;" in src
