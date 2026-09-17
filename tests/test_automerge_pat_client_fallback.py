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
    """Both helpers, because `_patClient` calls `_patFetchClient`.

    ⚠️ THIS EXTRACTION STARTED AT `function _patClient(` UNTIL 2026-09-13, AND
    THE DAY A THIRD CONSTRUCTION PATH WAS ADDED ABOVE IT, ALL EIGHT TESTS IN
    THIS FILE PASSED WHILE EXERCISING THE OLD BEHAVIOUR. The new try calls a
    helper defined just above `_patClient`; excluded from the extraction, that
    call threw a `ReferenceError` inside the try, was caught like any other
    failure, and the helper fell through to `null` — i.e. the tests reported
    green on the exact code path they exist to police. The docstring at the top
    of this file warns that a rename would do this; the same hazard arrives
    through an ADDITION, which `test_the_helper_is_actually_found` now checks
    for as well.
    """
    src = _script()
    start = src.index("function _patFetchClient(")
    end = src.index("const patClient =", start)
    return textwrap.dedent(src[start:end])


def test_the_helper_is_actually_found():
    """Guards the extraction itself: a rename would otherwise leave every test
    below exercising nothing and reporting green."""
    h = _helper()
    assert "__original_require__" in h
    assert "core.warning" in h
    # The helper must close over `patToken` rather than take a parameter:
    # `automerge-trigger-guard` C6 tests for the literal `getOctokit(patToken)`
    # and its planted-defect test mutates that exact string, so a rename makes
    # both the check and its plant silently stop matching.
    assert "function _patClient()" in h, (
        "the helper grew a parameter — C6 and its plant key on the literal "
        "`getOctokit(patToken)`; see check_automerge_trigger.py")
    assert h.count("getOctokit(patToken)") == 2, (
        "expected both require spellings to build the client from the "
        "closed-over `patToken`; got " + str(h.count("getOctokit(patToken)")))
    # The THIRD try, and the guard against the extraction silently missing it:
    # if `_patFetchClient` is not inside what we extract, the call below throws
    # a ReferenceError that the helper's own try/catch swallows, and every test
    # in this file goes green on the OLD behaviour. That is not hypothetical —
    # it is what happened when the try was first added.
    assert "function _patFetchClient()" in h, (
        "the fetch-based construction is not inside the extracted source, so "
        "the node tests below would exercise a two-try helper that no longer "
        "exists in the workflow")
    assert "_patFetchClient()" in h.split("function _patClient()")[1], (
        "_patClient no longer calls the fetch client — the require-free path "
        "is the ONLY one measured to work in the github-script runtime")


def _run_node(prelude: str) -> dict:
    """Execute the REAL helper under node with a stubbed environment."""
    js = f"""
    const warnings = [];
    const core = {{ warning: (m) => warnings.push(String(m)) }};
    const github = {{ __kind: 'github_token_client' }};
    // ⚠️ `patToken` is a CLOSED-OVER const, not a parameter. The helper takes
    //    no argument on purpose — `automerge-trigger-guard` C6 asserts the
    //    literal `getOctokit(patToken)` in the source, so the name is part of
    //    the contract. Passing a token here instead would exercise a helper
    //    the workflow does not have.
    const patToken = 'tok';
    {prelude}
    {_helper()}
    const client = _patClient();
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


# ── both requires failing IS the production case, not an edge one ───────────
# MEASURED on two live runs 10.5h apart — 34718195692 (#12087) and 34744864126
# (#12211) — every `claude-pr-automerge` run since 2026-09-12T09:56:31Z logs
# `@actions/github is not resolvable from this script`. So the two tests below
# are about the path production takes EVERY TIME, not a fallback nobody meets.

_BOTH_REQUIRES_FAIL = """
    globalThis.__original_require__ = () => { throw new Error("Cannot find module '@actions/github'"); };
    const require = () => { throw new Error("Cannot find module '@actions/github'"); };
"""


def test_both_requires_failing_now_yields_the_FETCH_client():
    """THE LIVE CASE. `fetch` is a global on Node 20 and 24 and needs no module,
    so the third try builds a client where the first two cannot."""
    out = _run_node(_BOTH_REQUIRES_FAIL)
    assert out["kind"] == "built", (
        "with both requires dead the helper must still build a PAT client — "
        "returning null here is what left #12087 opened under GITHUB_TOKEN "
        "with no CI on its head")


def test_and_the_working_fetch_path_is_SILENT():
    """It is not a failure, so it must not warn: a warning on the path
    production always takes is how a real one stops being read."""
    out = _run_node(_BOTH_REQUIRES_FAIL)
    assert out["warnings"] == [], out["warnings"]


def test_NO_construction_path_at_all_returns_null_and_NEVER_THROWS():
    """THE ORIGINAL DEFECT, preserved. Before the try/catch, the unhandled
    error took the whole job down. A runtime without `fetch` must still reach
    the degraded path rather than crash — and must not hand back a half-built
    client, which would tell step 3 the checks attached when they did not."""
    out = _run_node(_BOTH_REQUIRES_FAIL + "\n    delete globalThis.fetch;\n")
    assert out["kind"] is None, "it must return null, not a half-built client"


def test_and_that_fallback_is_LOUD():
    """A silent swallow would leave four lanes unable to land and unable to see
    why — which is how the crash went unexplained for half an hour.

    ⚠️ The trigger NARROWED with the fetch try: the warning now fires only when
    NO construction path worked at all. That is deliberate — it is the state
    where the PR really is opened under GITHUB_TOKEN and really will not arm."""
    out = _run_node(_BOTH_REQUIRES_FAIL + "\n    delete globalThis.fetch;\n")
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


# ── the shim's own contract, because it sits on the PR-CREATE path ─────────
# It implements ONE method, `rest.pulls.create`, which is the only call made
# through `opener` (grep: one site). A partial client is honest; a fake
# general-purpose one would be a trap for the next caller.

def _run_create(fetch_stub: str) -> dict:
    """Build the client with both requires dead, then CALL create() through it."""
    js = f"""
    const warnings = [];
    const core = {{ warning: (m) => warnings.push(String(m)) }};
    const github = {{ __kind: 'github_token_client' }};
    const patToken = 'tok';
    globalThis.__original_require__ = () => {{ throw new Error('no module'); }};
    const require = () => {{ throw new Error('no module'); }};
    {fetch_stub}
    {_helper()}
    (async () => {{
      const c = _patClient();
      let out = {{ ok: false, status: null, number: null, error: null, sent: null }};
      try {{
        const r = await c.rest.pulls.create({{
          owner: 'o', repo: 'r', head: 'h', base: 'main',
          title: 't', body: 'b', draft: false }});
        out.ok = true; out.number = r.data.number; out.sent = globalThis.__sent || null;
      }} catch (e) {{ out.error = String(e.message); out.status = e.status ?? null; }}
      console.log(JSON.stringify(out));
    }})();
    """
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, f"the shim THREW out of band:\n{r.stderr}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_create_returns_an_OCTOKIT_SHAPED_result():
    """The call site reads `(await opener.rest.pulls.create(args)).data`, so a
    bare body would be a TypeError on the one line that opens the PR."""
    out = _run_create("""
    globalThis.fetch = async (url, init) => {
      globalThis.__sent = { url, method: init.method,
                            auth: (init.headers||{}).Authorization,
                            body: JSON.parse(init.body) };
      return { ok: true, json: async () => ({ number: 99 }) };
    };
    """)
    assert out["ok"] and out["number"] == 99, out
    sent = out["sent"]
    assert sent["method"] == "POST"
    assert sent["url"].endswith("/repos/o/r/pulls"), sent["url"]
    assert sent["auth"] == "Bearer tok", (
        "the request must carry the PAT — that is the entire point; a request "
        "without it would open the PR as the bot again and attach no checks")
    assert sent["body"]["head"] == "h" and sent["body"]["base"] == "main"


def test_a_REFUSED_create_throws_carrying_status():
    """⚠️ LOAD-BEARING FOR A BRANCH THIS FILE DOES NOT OTHERWISE TOUCH. Whether
    `BRANCH_PROTECTION_TOKEN` carries pull-request write scope is unestablished
    and unreadable from a session, so the workflow keeps a 403 fallback that
    retries under GITHUB_TOKEN — and that fallback reads `e.status`. A shim
    that returned undefined, or threw a plain Error, would silently disable it
    and no PR would be opened at all."""
    out = _run_create("""
    globalThis.fetch = async () => ({ ok: false, status: 403,
                                      text: async () => 'Resource not accessible' });
    """)
    assert out["ok"] is False
    assert out["status"] == 403, (
        "the error must carry `.status`; the workflow branches on it")
    assert "403" in out["error"]


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
