"""The PAT-authenticated PR-open path must never crash the relay, and must
never report `pat` unless a PAT client was actually built.

⚠️ **SUPERSEDES THE `getOctokit(patToken)` / `require('@actions/github')`
MECHANISM THIS FILE ORIGINALLY TESTED.** That construction was itself the
subject of a MEASURED, LIVE defect (2026-09-12, #11889): `require('@actions/
github')` is not resolvable from a `github-script@v7` user script, so it threw
`Cannot find module '@actions/github'`. The code added a two-attempt fallback
(`__original_require__` then a bare `require`) plus a `core.warning` + `null`
return on total failure — precisely so a broken import degrades to "PR opened
under GITHUB_TOKEN, checks absent" rather than crashing the whole job.

⚠️ **THAT DEGRADED PATH THEN BECAME THE ONLY PATH, SILENTLY.** ROOT-CAUSED
2026-09-27 (`PI-20260926-DP2BHGT3-0001`): `BRANCH_PROTECTION_TOKEN` was set on
this repo the entire time, yet EVERY PR this job opened (#13000, #13003,
#13018, #13019, #13021, #13027) carried zero attached checks at its first
head. MEASURED directly in the job log for PR #13027's opening push (run
36279095114 / job 108498515118): `patToken` was truthy — no "BRANCH_
PROTECTION_TOKEN is NOT set" warning — and BOTH require spellings threw
`Cannot find module '@actions/github'` anyway, so the fallback this file
tested as a SAFETY NET was actually running on every single invocation.

**THE FIX REMOVES THE REQUIRE CHAIN ENTIRELY.** Opening a PR is one REST call,
which needs no Octokit client — the runtime's own global `fetch` (this job's
own log reads "forced to run on Node.js 24", which ships `fetch` built in)
does it directly, with no module to fail to resolve. So the multi-step
require/fallback tests this file used to run (`test_it_uses_the_documented_
escape_hatch_FIRST`, `..._falls_through_to_a_bare_require`, `..._BOTH_failing_
returns_null...`) tested a mechanism that no longer exists and are REMOVED,
not left in place skipped — a mechanism that isn't there can't regress.

⚠️ **THE HALF THAT MATTERS MORE THAN THE CRASH IS UNCHANGED AND STILL TESTED
HERE.** `openerKind` must key on the CLIENT WE BUILT (`patClient`), not the
TOKEN WE HOLD (`patToken`) — keying it on the token would report `pat` after a
failed request and tell step 3 the checks attached when they did not, which is
the precise defect #11889 was written to prevent. That property does not
depend on how the client is built, so `test_openerKind_keys_on_the_CLIENT_
not_the_TOKEN` and `test_the_opener_is_the_client_or_github_never_a_throw`
below are carried over VERBATIM from the previous version of this file.

⚠️ **THESE TESTS RUN THE REAL JAVASCRIPT**, extracted from the workflow and
executed under `node`, for the same reason the original tests did: the defect
class here is exactly "something no YAML assertion can see" — a fetch call
whose URL, method or auth header silently drifts from what GitHub's API
expects would still `grep` clean.

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
            if body and "_patCreatePR" in body:
                return body
    raise AssertionError("the _patCreatePR helper was not found in the workflow — "
                         "extraction is stale and these tests would prove nothing")


def _helper() -> str:
    src = _script()
    start = src.index("async function _patCreatePR(")
    end = src.index("const patClient =", start)
    return textwrap.dedent(src[start:end])


def test_the_helper_is_actually_found():
    """Guards the extraction itself: a rename would otherwise leave every test
    below exercising nothing and reporting green."""
    h = _helper()
    assert "fetch(" in h
    # `automerge-trigger-guard` C6 tests for the literal `Bearer ${patToken}`
    # and its planted-defect test mutates that exact string, so a rename makes
    # both the check and its plant silently stop matching.
    assert "Authorization: `Bearer ${patToken}`," in h, (
        "the Authorization header changed shape — C6 and its plant key on "
        "the literal `Bearer ${patToken}`; see check_automerge_trigger.py")


def _run_node(prelude: str, fetch_impl: str) -> dict:
    """Execute the REAL helper under node with a stubbed `fetch` and env."""
    js = f"""
    const patToken = 'tok';
    let fetchCalls = [];
    globalThis.fetch = async (url, opts) => {{
      fetchCalls.push({{ url, opts }});
      {fetch_impl}
    }};
    {prelude}
    {_helper()}
    const patClient = patToken ? {{ rest: {{ pulls: {{ create: _patCreatePR }} }} }} : null;
    (async () => {{
      let result = null, error = null;
      try {{
        result = await patClient.rest.pulls.create({{
          owner: 'o', repo: 'r', head: 'claude/x', base: 'main',
          draft: false, title: 't', body: 'b',
        }});
      }} catch (e) {{
        error = {{ message: e.message, status: e.status }};
      }}
      console.log(JSON.stringify({{ result, error, fetchCalls }}));
    }})();
    """
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, f"the helper THREW synchronously — it must never crash the job:\n{r.stderr}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_it_posts_to_the_right_endpoint_with_the_pat_bearer_token():
    out = _run_node("", "return { ok: true, json: async () => ({ number: 42, node_id: 'PR_x' }) };")
    assert len(out["fetchCalls"]) == 1
    call = out["fetchCalls"][0]
    assert call["url"] == "https://api.github.com/repos/o/r/pulls"
    assert call["opts"]["method"] == "POST"
    assert call["opts"]["headers"]["Authorization"] == "Bearer tok"
    assert call["opts"]["headers"]["Accept"] == "application/vnd.github+json"
    body = json.loads(call["opts"]["body"])
    assert body == {"title": "t", "head": "claude/x", "base": "main",
                     "draft": False, "body": "b"}


def test_it_returns_data_shaped_like_an_octokit_response_on_success():
    """Downstream code reads `pr.number`, `pr.node_id`, `pr.head.sha`, `pr.draft`
    off `(await opener.rest.pulls.create(...)).data` — the shape must match."""
    out = _run_node(
        "",
        "return { ok: true, json: async () => "
        "({ number: 42, node_id: 'PR_x', draft: false, head: { sha: 'abc' } }) };")
    assert out["error"] is None
    assert out["result"]["data"] == {"number": 42, "node_id": "PR_x",
                                     "draft": False, "head": {"sha": "abc"}}


def test_a_non_ok_response_THROWS_with_status_rather_than_returning_junk():
    """The caller's existing `catch (e) { ...e.status...e.message... }` block
    (unchanged by this fix) depends on both fields being present."""
    out = _run_node(
        "",
        "return { ok: false, status: 403, "
        "json: async () => ({ message: 'Resource not accessible' }) };")
    assert out["result"] is None
    assert out["error"] == {"message": "Resource not accessible", "status": 403}


def test_an_unparseable_error_body_still_throws_rather_than_crashing():
    out = _run_node(
        "", "return { ok: false, status: 500, json: async () => {{ throw new Error('bad json'); }} };"
        .replace("{{", "{").replace("}}", "}"))
    assert out["result"] is None
    assert out["error"]["status"] == 500
    assert "HTTP 500" in out["error"]["message"]


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


def test_patClient_is_null_exactly_when_the_token_is_absent():
    """No require chain remains to fail differently from an absent token — the
    construction is now unconditional, so this is the only branch left."""
    src = _script()
    assert "const patClient = patToken ? { rest: { pulls: { create: _patCreatePR } } } : null;" in src
