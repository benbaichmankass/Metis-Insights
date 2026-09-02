#!/usr/bin/env python3
"""Wait for a pull request's CI to SETTLE on a runner, and emit one compact payload.

WHY THIS EXISTS (measured, not assumed)
---------------------------------------
``api.github.com`` returns **HTTP 403** from inside a Claude Code sandbox — the
platform proxy intercepts it (re-confirmed 2026-09-02 from a live session:
``curl -o /dev/null -w '%{http_code}' https://api.github.com/repos/...`` → 403).
So a session **cannot** write a bash poller for its own PR's CI. Its only channel
is the GitHub MCP, and the only way to learn "has CI finished yet" is to call
``pull_request_read`` again, and again, while a suite runs. Every one of those
calls spends context to learn *still running*.

A GitHub-hosted runner has no such restriction. This script runs THERE: it does
the polling, the waiting and the aggregating, and writes ONE small JSON the
session reads back over git in a single command.

⚠️ **BUT THE WAITING IS USUALLY NOT THE PART THAT IS MISSING — READ THIS BEFORE
REACHING FOR THE WAIT MODE.** ``mcp__github__subscribe_pr_activity`` already
wakes a session on ``check_suite.completed``, at zero polling cost, and for a
subscribed PR that is strictly better than any poll loop. Do not use ``wait``
mode where a wake will do.

**What the wake does NOT give you is a trustworthy VERDICT, and that is this
module's actual job.** ``BL-20260821-CHECK-SUITE-EVENT-IS-PER-SUITE-NOT-PER-PR``
is OPEN at severity HIGH: this repo's four required checks (``guards``,
``pytest-run``, ``pytest-collect``, ``repo-inventory``) come from FOUR SEPARATE
check suites, so a ``check_suite.completed`` success says one suite finished --
never that the PR is green. It was observed twice on two PRs within one hour,
and this relay's own run 3 on PR #10757 is a third instance: ``guards``,
``pytest-collect`` and ``repo-inventory`` all passing while ``pytest-run`` was
still in flight. A session acting on a success event in that window merges on a
partial required set. The event's own footer says to verify overall state first
-- this is the thing that verifies it.

So the intended pairing is: **the wake is the TRIGGER, this is the READER.**
Set ``timeout_minutes: 0`` (``mode: "once"``) to grade the PR's whole head in a
single observation with no waiting at all. Reach for ``wait`` mode only when
there is no subscription -- a PR nobody subscribed to, or a session that cannot
end its turn and be woken.

It is the same trade the ``vm-diag-snapshot`` relay makes for the VM, and the
same dispatch shape as ``pr-opener.yml`` / ``board-post.yml`` (push a request
file, read a result file) — chosen because ``issue_write`` 403s for exactly the
sessions that need this most.

STATE VOCABULARY — SEVEN STATES, NEVER COLLAPSED
------------------------------------------------
The whole point of the payload is that a session can act on it without a second
look, so a state that quietly means two things would be worse than no payload.

* ``green``      — at least one check run exists on the head sha, every one of
                   them has CONCLUDED, and none failed or was cancelled.
* ``red``        — at least one check concluded ``failure`` /
                   ``timed_out`` / ``action_required``. Decisive: fix it.
* ``cancelled``  — no failures, but at least one check ended ``cancelled``.
                   A cancelled check produced **no verdict**; it is emphatically
                   NOT a pass. (This repo runs ``cancel-in-progress: true`` on
                   its required checks, so a superseded push leaves exactly this
                   state behind and grading it green would be a false pass.)
* ``pending``    — at least one check is ``queued`` / ``in_progress``. Only ever
                   reported when the watcher hit its own deadline; it means
                   *we stopped waiting*, never *CI stopped*.
* ``conflict``   — ``mergeable_state == "dirty"``. GitHub builds
                   ``pull_request`` runs against the MERGE ref, so when that ref
                   cannot be built the workflows are **skipped by construction**
                   and zero checks appear. Reported ahead of ``no_checks``
                   because it is the explanation for it.
* ``no_checks``  — the read SUCCEEDED and the head sha carries zero check runs.
                   ⚠️ This is the trap ``CLAUDE.md`` documents: zero check runs
                   renders identically to *queued* and to *all green*, and the
                   usual cause is a merge conflict or a head pushed by
                   ``github-actions[bot]`` (GitHub suppresses workflow triggers
                   for ``GITHUB_TOKEN`` pushes). It is NEVER graded green.
* ``unreadable`` — an API read failed. **We did not look.** Never folded into
                   ``no_checks``, which is a real observation of emptiness.

``settled`` is a separate boolean, deliberately: ``green``/``red``/``cancelled``/
``conflict`` are settled, ``pending``/``no_checks``/``unreadable`` are not.

The failing-log excerpt carries its own ``log_state`` for the same reason — an
empty ``log_tail`` must not read as "the job failed quietly".

Self-test:  python3 scripts/ops/ci_settle.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Tuple

API = "https://api.github.com"

# Conclusions that mean "this check gave a verdict and the verdict was fine".
_PASSING = frozenset({"success", "neutral", "skipped"})
# Conclusions that mean "this check gave a verdict and the verdict was bad".
_FAILING = frozenset({"failure", "timed_out", "action_required", "startup_failure"})
# A cancelled check gave NO verdict. Its own bucket on purpose — see the module
# docstring. `stale` is GitHub's term for a check superseded before it ran.
_CANCELLED = frozenset({"cancelled", "stale"})

# Bounds on the failing-log excerpt. A relay that pastes whole job logs back
# would re-create the cost it exists to remove.
MAX_FAILING_LOGS = 3
MAX_LOG_LINES = 60

_ERROR_LINE = re.compile(
    r"(?:^|\s)(?:E\s+\w|FAILED |ERROR |error:|::error|AssertionError|Traceback"
    r"|\bfailed\b|SyntaxError|ImportError|ModuleNotFoundError)",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# Pure logic — no network. Everything below `summarise` is unit-testable with
# plain dicts, which is the point: the policy is arguable in tests rather than
# against a live PR.
# --------------------------------------------------------------------------


def bucket_conclusion(check: Dict[str, Any]) -> str:
    """One check run -> ``passing`` | ``failing`` | ``cancelled`` | ``running``.

    ``running`` covers anything not yet ``completed`` AND a ``completed`` run
    whose conclusion GitHub has not populated (which does happen, briefly) --
    an unknown conclusion is treated as *not yet decided*, never as passing.
    """
    if (check.get("status") or "") != "completed":
        return "running"
    conclusion = (check.get("conclusion") or "").lower()
    if conclusion in _PASSING:
        return "passing"
    if conclusion in _FAILING:
        return "failing"
    if conclusion in _CANCELLED:
        return "cancelled"
    return "running"


def _started(check: Dict[str, Any]) -> str:
    return str(check.get("started_at") or check.get("completed_at") or "")


def dedupe_checks(checks: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep the LATEST run per check NAME.

    A re-run leaves both attempts on the same head sha. Counting the stale
    attempt would report a red that has already been re-run green, or a
    cancelled that has already been superseded -- both wrong in a direction that
    costs a session a wasted cycle.
    """
    latest: Dict[str, Dict[str, Any]] = {}
    for check in checks:
        name = str(check.get("name") or "")
        prior = latest.get(name)
        if prior is None or _started(check) >= _started(prior):
            latest[name] = check
    return sorted(latest.values(), key=lambda c: str(c.get("name") or ""))


def summarise(
    *,
    pr: Optional[Dict[str, Any]],
    pr_read_ok: bool,
    checks: Optional[List[Dict[str, Any]]],
    checks_read_ok: bool,
    threads: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Grade one observation of a PR into the seven-state payload.

    ``*_read_ok`` is passed separately from the payload rather than inferred
    from ``is None``: a successful read that legitimately returns nothing and a
    read that never happened are different facts, and inferring one from the
    other is the collapse this whole module is built to avoid.
    """
    if not pr_read_ok or not checks_read_ok:
        which = [
            name
            for name, ok in (("pull_request", pr_read_ok), ("check_runs", checks_read_ok))
            if not ok
        ]
        return {
            "state": "unreadable",
            "settled": False,
            "reason": "could not read " + ", ".join(which) + " — we did not look",
            "mergeable_state": (pr or {}).get("mergeable_state"),
            "checks": [],
            "counts": {"passing": None, "failing": None, "cancelled": None, "running": None},
        }

    pr = pr or {}
    mergeable_state = pr.get("mergeable_state")
    runs = dedupe_checks(checks or [])
    buckets = [bucket_conclusion(c) for c in runs]
    counts = {
        "passing": buckets.count("passing"),
        "failing": buckets.count("failing"),
        "cancelled": buckets.count("cancelled"),
        "running": buckets.count("running"),
    }

    rows = [
        {
            "name": c.get("name"),
            "bucket": b,
            "conclusion": c.get("conclusion"),
            "status": c.get("status"),
            "id": c.get("id"),
            "url": c.get("html_url"),
        }
        for c, b in zip(runs, buckets)
    ]

    if not runs:
        # Order matters. `dirty` EXPLAINS the emptiness; reporting `no_checks`
        # first would hand the reader the symptom and hide the cause.
        if mergeable_state == "dirty":
            state, settled, reason = (
                "conflict",
                True,
                "merge conflict with the base branch — GitHub builds pull_request "
                "runs against the merge ref, so no checks can start until it is "
                "resolved. Merge the base branch in; CI fires seconds after the "
                "push that resolves it.",
            )
        else:
            state, settled, reason = (
                "no_checks",
                False,
                "zero check runs on the head sha. This is NOT green. Common "
                "causes: the head was pushed by github-actions[bot] (GitHub "
                "suppresses workflow triggers for GITHUB_TOKEN pushes), the PR "
                "was opened by a bot token, or the checks have not been "
                f"attached yet. mergeable_state={mergeable_state!r}.",
            )
    elif counts["failing"]:
        state, settled, reason = ("red", True, f"{counts['failing']} check(s) failed")
    elif counts["running"]:
        state, settled, reason = (
            "pending",
            False,
            f"{counts['running']} check(s) still running — the watcher stopped "
            "waiting, CI did not stop",
        )
    elif counts["cancelled"]:
        state, settled, reason = (
            "cancelled",
            True,
            f"{counts['cancelled']} check(s) were cancelled and produced NO "
            "verdict. Not a pass. Usually a superseded push (this repo runs "
            "cancel-in-progress on its required checks) — push again or re-run.",
        )
    else:
        state, settled, reason = ("green", True, "all checks concluded, none failing")

    if mergeable_state == "dirty" and state in {"green", "cancelled"}:
        # A green head can still be unmergeable. Say so rather than letting a
        # reader infer mergeability from the check state.
        reason += " — but mergeable_state is 'dirty': resolve the conflict."

    out: Dict[str, Any] = {
        "state": state,
        "settled": settled,
        "reason": reason,
        "mergeable_state": mergeable_state,
        "merge_state_note": _merge_state_note(mergeable_state),
        "head_sha": (pr.get("head") or {}).get("sha"),
        "draft": pr.get("draft"),
        "pr_state": pr.get("state"),
        "counts": counts,
        "checks": rows,
    }
    if threads is not None:
        out["review_threads"] = threads
    return out


_MERGE_STATE_NOTES = {
    "dirty": "merge conflict with the base branch",
    "blocked": "a required check is missing or failing, or a review is required",
    "behind": "head is behind the base (not blocking here — 'require up to date' is off)",
    "unstable": "a NON-required check is failing; the PR is still mergeable",
    "clean": "mergeable",
    "has_hooks": "mergeable, with pre-receive hooks",
    "unknown": "GitHub has not computed mergeability yet — ask again shortly",
}


def _merge_state_note(mergeable_state: Optional[str]) -> Optional[str]:
    if mergeable_state is None:
        return None
    return _MERGE_STATE_NOTES.get(mergeable_state, f"unrecognised value {mergeable_state!r}")


def extract_log_tail(log_text: str, max_lines: int = MAX_LOG_LINES) -> List[str]:
    """Pick the lines a human would actually read out of a failing job log.

    Error-shaped lines first (in order), then the tail, deduped and capped.
    Returning the whole log would re-create the cost this relay exists to
    remove; returning nothing would make a failure look quiet.
    """
    lines = [ln.rstrip() for ln in log_text.splitlines() if ln.strip()]
    if not lines:
        return []
    hits = [ln for ln in lines if _ERROR_LINE.search(ln)]
    tail = lines[-max_lines:]
    picked: List[str] = []
    seen = set()
    for ln in hits[-max_lines:] + tail:
        if ln not in seen:
            seen.add(ln)
            picked.append(ln)
    return picked[-max_lines:]


# --------------------------------------------------------------------------
# Network. Runs on a GitHub-hosted runner, where api.github.com is reachable.
# --------------------------------------------------------------------------


class _DropAuthOnCrossHostRedirect(urllib.request.HTTPRedirectHandler):
    """Strip ``Authorization`` when a redirect leaves the api.github.com host.

    MEASURED 2026-09-02 on the relay's own first live red verdict: the job-log
    endpoint answers ``302`` to a blob-storage host, urllib's default handler
    re-sent the GitHub bearer to it, and the blob host replied ``401 Server
    failed to authenticate the request``. The excerpt came back empty.

    That it came back as ``log_state: "unreadable"`` WITH the 401 attached --
    rather than as an empty ``log_tail`` -- is the state vocabulary doing its
    job: an empty excerpt would have read as *the job failed quietly*, which is
    a different and wrong claim. The state was honest; the read still needs to
    work, which is what this handler fixes.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        from urllib.parse import urlparse

        if urlparse(newurl).netloc != urlparse(req.full_url).netloc:
            # `Request` normalises header names to title case on add_header.
            new_req.headers.pop("Authorization", None)
            new_req.unredirected_hdrs.pop("Authorization", None)
        return new_req


_LOG_OPENER = urllib.request.build_opener(_DropAuthOnCrossHostRedirect)


class GitHub:
    def __init__(self, token: str, repo: str) -> None:
        self.token = token
        self.repo = repo

    def _request(self, path: str, accept: str, *, opener=None) -> Tuple[bool, Any]:
        url = path if path.startswith("http") else f"{API}{path}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", accept)
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "metis-ci-settle")
        open_fn = opener.open if opener is not None else urllib.request.urlopen
        try:
            with open_fn(req, timeout=45) as resp:
                raw = resp.read()
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            return False, str(exc)
        if accept.endswith("json"):
            try:
                return True, json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                return False, f"undecodable JSON: {exc}"
        return True, raw.decode("utf-8", "replace")

    def json(self, path: str) -> Tuple[bool, Any]:
        return self._request(path, "application/vnd.github+json")

    def text(self, path: str) -> Tuple[bool, Any]:
        # The log endpoint redirects to blob storage, which rejects the GitHub
        # bearer -- see _DropAuthOnCrossHostRedirect.
        return self._request(
            path, "application/vnd.github.raw+json", opener=_LOG_OPENER
        )

    def pull(self, number: int) -> Tuple[bool, Any]:
        return self.json(f"/repos/{self.repo}/pulls/{number}")

    def check_runs(self, sha: str) -> Tuple[bool, Any]:
        ok, payload = self.json(
            f"/repos/{self.repo}/commits/{sha}/check-runs?per_page=100&filter=all"
        )
        if not ok:
            return False, payload
        if not isinstance(payload, dict):
            return False, "unexpected check-runs shape"
        return True, payload.get("check_runs") or []

    def job_log(self, job_id: int) -> Tuple[bool, Any]:
        return self.text(f"/repos/{self.repo}/actions/jobs/{job_id}/logs")

    def unresolved_threads(self, number: int) -> Dict[str, Any]:
        """Unresolved review-thread count via GraphQL, degrading honestly."""
        owner, _, name = self.repo.partition("/")
        query = (
            "query($o:String!,$n:String!,$p:Int!){repository(owner:$o,name:$n)"
            "{pullRequest(number:$p){reviewDecision "
            "reviewThreads(first:100){nodes{isResolved isOutdated}}}}}"
        )
        body = json.dumps(
            {"query": query, "variables": {"o": owner, "n": name, "p": number}}
        ).encode()
        req = urllib.request.Request(f"{API}/graphql", data=body)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "metis-ci-settle")
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - degrade, never fail the watch
            return {"read_state": "unreadable", "unresolved": None, "error": str(exc)}
        node = (
            ((payload.get("data") or {}).get("repository") or {}).get("pullRequest")
            or {}
        )
        threads = (node.get("reviewThreads") or {}).get("nodes")
        if threads is None:
            return {
                "read_state": "unreadable",
                "unresolved": None,
                "error": str(payload.get("errors") or "no reviewThreads in response"),
            }
        return {
            "read_state": "read",
            "unresolved": sum(1 for t in threads if not t.get("isResolved")),
            "total": len(threads),
            "review_decision": node.get("reviewDecision"),
        }


def observe(gh: GitHub, number: int, *, with_threads: bool) -> Dict[str, Any]:
    pr_ok, pr = gh.pull(number)
    if not pr_ok:
        return summarise(pr=None, pr_read_ok=False, checks=None, checks_read_ok=False)
    sha = ((pr or {}).get("head") or {}).get("sha")
    if not sha:
        return summarise(pr=pr, pr_read_ok=True, checks=None, checks_read_ok=False)
    checks_ok, checks = gh.check_runs(sha)
    threads = gh.unresolved_threads(number) if with_threads else None
    return summarise(
        pr=pr,
        pr_read_ok=True,
        checks=checks if checks_ok else None,
        checks_read_ok=checks_ok,
        threads=threads,
    )


def attach_failure_logs(gh: GitHub, summary: Dict[str, Any]) -> None:
    """Fold a bounded excerpt of each failing job's log into the payload.

    Without this a red verdict still costs the session a ``get_job_logs`` call
    returning an entire job log -- the single fattest read in the loop this
    relay exists to shorten.
    """
    failing = [c for c in summary.get("checks", []) if c.get("bucket") == "failing"]
    for check in failing[:MAX_FAILING_LOGS]:
        job_id = check.get("id")
        if not isinstance(job_id, int):
            check["log_state"] = "no_job_id"
            continue
        ok, text = gh.job_log(job_id)
        if not ok:
            # A non-Actions check (a GitHub App status) has no job log. That is
            # not a failure to look -- there is nothing there to look at.
            check["log_state"] = "unreadable"
            check["log_error"] = str(text)[:200]
            continue
        check["log_state"] = "read"
        check["log_tail"] = extract_log_tail(str(text))
    if len(failing) > MAX_FAILING_LOGS:
        summary["log_note"] = (
            f"{len(failing)} checks failed; logs attached for the first "
            f"{MAX_FAILING_LOGS}."
        )


def watch(
    gh: GitHub,
    number: int,
    *,
    timeout_s: int,
    poll_s: int,
    with_threads: bool,
    sleeper=time.sleep,
    clock=time.monotonic,
) -> Dict[str, Any]:
    """Poll until settled or the deadline. Returns the LAST observation."""
    deadline = clock() + timeout_s
    polls = 0
    summary: Dict[str, Any] = {}
    while True:
        polls += 1
        summary = observe(gh, number, with_threads=with_threads)
        if summary.get("settled"):
            break
        # timeout_s == 0 is OBSERVE-ONCE, and it is the mode meant to pair with
        # a check_suite.completed wake -- see the module docstring. It is NOT a
        # zero-length wait that timed out: nothing was waited for, so saying
        # `timed_out_waiting` would claim an attempt nobody made.
        if timeout_s <= 0:
            summary["observed_once"] = True
            # The `pending` reason is written for the WAIT path and says the
            # watcher stopped waiting. In `once` mode nothing waited, so that
            # sentence names an action no code path took -- the semantic
            # substitution CLAUDE.md files as UNPROVENANCED DIAGNOSTIC OUTPUT
            # sub-class A. Branch on the actual condition rather than reword it
            # generically: the count is the same, the claim about how we got it
            # is not.
            if summary.get("state") == "pending":
                summary["reason"] = (
                    f"{summary['counts']['running']} check(s) still running as of "
                    "this SINGLE observation — nothing was waited for. Re-observe, "
                    "or use the wait mode if this PR has no subscribe_pr_activity "
                    "wake behind it."
                )
            break
        if clock() >= deadline:
            summary["timed_out_waiting"] = True
            break
        sleeper(poll_s)
    summary["polls"] = polls
    summary["mode"] = "once" if timeout_s <= 0 else "wait"
    summary["pr"] = number
    return summary


# --------------------------------------------------------------------------
# Self-test. `pytest` is not installed in every container this runs in, so the
# same assertions live here and in tests/test_ci_settle.py.
# --------------------------------------------------------------------------


def _check(name: str, condition: bool, failures: List[str]) -> None:
    if not condition:
        failures.append(name)


def self_test() -> int:
    f: List[str] = []
    ok = {"name": "a", "status": "completed", "conclusion": "success"}
    bad = {"name": "b", "status": "completed", "conclusion": "failure"}
    can = {"name": "c", "status": "completed", "conclusion": "cancelled"}
    run = {"name": "d", "status": "in_progress", "conclusion": None}
    clean = {"mergeable_state": "clean", "head": {"sha": "s"}}
    dirty = {"mergeable_state": "dirty", "head": {"sha": "s"}}

    def s(checks, pr=clean, pr_ok=True, checks_ok=True):
        return summarise(pr=pr, pr_read_ok=pr_ok, checks=checks, checks_read_ok=checks_ok)

    _check("green", s([ok])["state"] == "green", f)
    _check("red", s([ok, bad])["state"] == "red", f)
    _check("red-beats-pending", s([bad, run])["state"] == "red", f)
    _check("cancelled-is-not-green", s([ok, can])["state"] == "cancelled", f)
    _check("cancelled-not-settled-green", s([ok, can])["settled"] is True, f)
    _check("pending", s([ok, run])["state"] == "pending", f)
    _check("pending-not-settled", s([ok, run])["settled"] is False, f)
    _check("no-checks-is-not-green", s([])["state"] == "no_checks", f)
    _check("no-checks-not-settled", s([])["settled"] is False, f)
    _check("dirty-explains-empty", s([], pr=dirty)["state"] == "conflict", f)
    _check("unreadable", s(None, checks_ok=False)["state"] == "unreadable", f)
    _check("unreadable-not-no-checks", s(None, checks_ok=False)["settled"] is False, f)
    _check(
        "unreadable-counts-are-null",
        s(None, checks_ok=False)["counts"]["passing"] is None,
        f,
    )
    _check("no-checks-counts-are-zero", s([])["counts"]["passing"] == 0, f)
    _check("green-but-dirty-says-so", "dirty" in s([ok], pr=dirty)["reason"], f)

    # completed-with-no-conclusion is "not yet decided", never passing
    _check(
        "empty-conclusion-is-running",
        bucket_conclusion({"status": "completed", "conclusion": None}) == "running",
        f,
    )

    # dedupe keeps the newest attempt per name
    old = {"name": "x", "status": "completed", "conclusion": "failure", "started_at": "1"}
    new = {"name": "x", "status": "completed", "conclusion": "success", "started_at": "2"}
    _check("dedupe-keeps-newest", s([old, new])["state"] == "green", f)
    _check("dedupe-order-independent", s([new, old])["state"] == "green", f)
    _check("dedupe-length", len(dedupe_checks([old, new])) == 1, f)

    # log extraction is bounded and prefers error-shaped lines
    log = "\n".join(["noise"] * 200 + ["FAILED tests/test_x.py::test_y"] + ["tail"] * 5)
    tail = extract_log_tail(log)
    _check("log-bounded", len(tail) <= MAX_LOG_LINES, f)
    _check("log-keeps-error", any("FAILED" in ln for ln in tail), f)
    _check("log-empty-stays-empty", extract_log_tail("") == [], f)

    # the watcher must STOP on a settled state and must not loop forever
    class _Stub:
        def __init__(self, seq):
            self.seq = list(seq)
            self.calls = 0

        def pull(self, _n):
            self.calls += 1
            return True, clean

        def check_runs(self, _s):
            return True, self.seq.pop(0) if self.seq else []

        def unresolved_threads(self, _n):
            return {"read_state": "read", "unresolved": 0}

    stub = _Stub([[run], [run], [ok]])
    res = watch(stub, 1, timeout_s=999, poll_s=0, with_threads=False, sleeper=lambda _: None)
    _check("watch-settles", res["state"] == "green", f)
    _check("watch-polls-3", res["polls"] == 3, f)

    # observe-once must NOT claim a timeout it never attempted
    stub_once = _Stub([[run]] * 3)
    once = watch(stub_once, 1, timeout_s=0, poll_s=0, with_threads=False,
                 sleeper=lambda _: None)
    _check("once-polls-exactly-1", once["polls"] == 1, f)
    _check("once-mode-label", once["mode"] == "once", f)
    _check("once-sets-observed-once", once.get("observed_once") is True, f)
    _check("once-never-claims-timeout", "timed_out_waiting" not in once, f)
    _check("once-still-pending", once["state"] == "pending", f)
    _check("once-reason-says-single-observation",
           "SINGLE observation" in once["reason"], f)
    _check("once-reason-does-not-claim-waiting",
           "stopped waiting" not in once["reason"], f)
    stub_once_green = _Stub([[ok]])
    og = watch(stub_once_green, 1, timeout_s=0, poll_s=0, with_threads=False,
               sleeper=lambda _: None)
    _check("once-can-settle-green", og["state"] == "green", f)
    _check("once-green-has-no-observed-once", "observed_once" not in og, f)

    ticks = iter([0, 1, 2, 3, 4, 5, 6])
    stub2 = _Stub([[run]] * 10)
    res2 = watch(
        stub2, 1, timeout_s=1, poll_s=0, with_threads=False,
        sleeper=lambda _: None, clock=lambda: next(ticks),
    )
    _check("watch-times-out", res2.get("timed_out_waiting") is True, f)
    _check("wait-mode-label", res2["mode"] == "wait", f)
    _check("timeout-is-pending-not-green", res2["state"] == "pending", f)

    if f:
        print("ci-settle self-test: FAILED — " + ", ".join(f))
        return 1
    print("ci-settle self-test: OK")
    return 0


def _write_result(path: str, payload: Dict[str, Any]) -> None:
    payload.setdefault("watched_by", "ci-settled relay")
    payload.setdefault(
        "generated_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--request", help="path to the request JSON")
    ap.add_argument("--out", help="path to write the result JSON")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    if not args.request or not args.out:
        ap.error("--request and --out are required unless --self-test")

    # A MALFORMED REQUEST MUST PRODUCE A READABLE REFUSAL, NEVER A MISSING FILE.
    # The caller blocks on the result file appearing; if a bad request simply
    # wrote nothing, "you sent nonsense" and "the relay is dead" would look
    # identical from the other end -- and the caller would wait out its whole
    # timeout to learn neither.
    try:
        with open(args.request, "r", encoding="utf-8") as fh:
            req = json.load(fh)
        number = int(req["pr"])
        # 0 is a MEANINGFUL value (observe once), so the clamp floors at 0 not 1.
        timeout_s = int(min(max(int(req.get("timeout_minutes", 20)), 0), 45) * 60)
        poll_s = int(min(max(int(req.get("poll_seconds", 20)), 5), 120))
        with_threads = bool(req.get("review_threads", True))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        _write_result(
            args.out,
            {
                "state": "unreadable",
                "settled": False,
                "reason": f"the request could not be read: {type(exc).__name__}: {exc}. "
                "This is 'we did not look', not a CI verdict.",
            },
        )
        print(f"ci-settle: refused a malformed request ({type(exc).__name__})")
        return 0

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        result = {
            "state": "unreadable",
            "settled": False,
            "reason": "no GH_TOKEN in the environment — we did not look",
            "pr": number,
        }
    else:
        gh = GitHub(token, args.repo)
        result = watch(
            gh, number, timeout_s=timeout_s, poll_s=poll_s, with_threads=with_threads
        )
        if result.get("state") == "red":
            attach_failure_logs(gh, result)

    result["watched_by"] = "ci-settled relay"
    result["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    result["request"] = {
        "pr": number,
        "timeout_minutes": timeout_s // 60,
        "poll_seconds": poll_s,
    }
    _write_result(args.out, result)
    print(f"ci-settle: pr #{number} -> {result.get('state')} after {result.get('polls')} poll(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
