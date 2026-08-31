#!/usr/bin/env python3
# wiring: docs/claude/OPEN-ITEMS.json `probe.cmd`; run by scripts/ops/run_probes.py
"""Probe GitHub Actions JOB LOGS for a literal a `monitoring` row clears on — W3.

WHY A FOURTH SOURCE
-------------------
Two rows clear on something that is only ever printed into a CI job log:

  * OI-20260831-SESSION-BRIEF-DIFF-SCOPING — `session-brief: verdict=inherited`
    on a PR that did not touch the registers, and `stale-branch refresh -> …`
    on a commit-to-main run. Its reason said "buildable against the Actions API
    later", which is a merely-unwritten probe by its own admission.
  * OI-20260831-RESEARCH-QUEUE-GPU-ROUTE — the ledger preflight OBSERVED making
    a decision, read from that run's own output.

⚠️ THE GPU ROW'S REASON CONTAINED A CATEGORY ERROR, and correcting it is why
this file covers two rows rather than one. It read: *"A scheduled probe cannot
produce that without FIRING a GPU job, which spends real budget — an action, not
an observation."* True about FIRING and irrelevant to PROBING: **no probe in
this family produces the event it watches.** `pairs_soak` does not open a pair;
`arbitration_fanout_soak` does not route an order. Conflating *producing* an
observation with *observing* it turned a buildable probe into a declared
impossibility. What is true is narrower and is kept: the event will not occur
spontaneously, so this probe reports `fail` until an operator fires one — which
is the same honest shape the two shipped armed-but-unexercised probes have, and
is strictly better than a human remembering to look.

SHIPPED WITHOUT A LIVE END-TO-END RUN, DELIBERATELY, AND HERE IS WHY THAT IS SAFE
---------------------------------------------------------------------------------
`api.github.com` is INTERCEPTED from a Claude Code on the web sandbox — measured
2026-08-31, HTTP **403** with a Claude-specific body, exactly as CLAUDE.md
§ "PM-side session capabilities" documents. So this reader could not be
exercised against the real API by the session that wrote it; its controls run
against a fake server.

That is stated rather than hidden, because the runner's own docstring says a
decorative probe is *strictly worse than no probe*. What makes shipping it
honest is the family's polarity: a reader that is broken, unauthorised, or
pointed at a moved endpoint reports **could_not_run**, never `fail`. It cannot
manufacture the quiet negative that looks like diligence. Its FIRST run in the
`probes` workflow is therefore its real verification, and the result of that run
is visible in PROBES.json either way. A declaration should also name a
`--positive-control` literal, so a log that is fetched but no longer contains
what we think it does is an unread too.

Exit codes: 0 pass · 1 read-and-nothing-matched · 2 we could not look.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import probe_lib  # noqa: E402

API_BASE = "https://api.github.com"
TIMEOUT_S = 30
# A probe runs unattended. Bound BOTH the runs walked and the bytes read, or a
# noisy workflow turns a scheduled read into a runner wedge — the shape of both
# June 2026 trader wedges one level up.
MAX_RUNS = 20
MAX_LOG_BYTES = 4_000_000


def _api(path: str, token: str) -> tuple[object | None, str]:
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "metis-probe-actions-log"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310
            return json.loads(r.read().decode("utf-8", "replace")), ""
    except urllib.error.HTTPError as exc:
        # The host ANSWERED and refused. Never an empty result.
        return None, f"GET {path} -> answered HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return None, f"GET {path} -> {exc}"


def _job_log(repo: str, job_id: int, token: str) -> tuple[str | None, str]:
    req = urllib.request.Request(
        f"{API_BASE}/repos/{repo}/actions/jobs/{job_id}/logs",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "metis-probe-actions-log"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310
            return r.read(MAX_LOG_BYTES).decode("utf-8", "replace"), ""
    except urllib.error.HTTPError as exc:
        # 410 = the log has EXPIRED (GitHub retains ~90 days). That is an
        # unread, and a materially different one from "the line is not there":
        # the evidence existed and we arrived late.
        return None, f"job {job_id} -> answered HTTP {exc.code}" + (
            " (log EXPIRED — the evidence existed and we arrived late, which is "
            "not the same as the line being absent)" if exc.code == 410 else "")
    except (urllib.error.URLError, OSError) as exc:
        return None, f"job {job_id} -> {exc}"


def scan(repo: str, workflow: str, token: str, runs_wanted: int,
         branch: str | None) -> tuple[list[dict] | None, str]:
    """Return (one row per RUN carrying its combined log text, note).

    `None` means we could not look. A run whose logs could not be fetched is
    RECORDED as unread rather than skipped silently — a partially-read
    population must not be reported as a whole one.
    """
    q = f"?per_page={min(runs_wanted, MAX_RUNS)}"
    if branch:
        q += f"&branch={branch}"
    data, err = _api(f"/repos/{repo}/actions/workflows/{workflow}/runs{q}", token)
    if data is None:
        return None, err
    if not isinstance(data, dict) or not isinstance(data.get("workflow_runs"), list):
        return None, f"runs listing for {workflow} was not the expected envelope"

    rows: list[dict] = []
    unread = 0
    for run in data["workflow_runs"][:min(runs_wanted, MAX_RUNS)]:
        rid = run.get("id")
        jobs, err = _api(f"/repos/{repo}/actions/runs/{rid}/jobs?per_page=50", token)
        text_parts: list[str] = []
        run_unread = False
        if jobs is None or not isinstance(jobs, dict):
            run_unread = True
        else:
            for job in jobs.get("jobs") or []:
                txt, jerr = _job_log(repo, job.get("id"), token)
                if txt is None:
                    run_unread = True
                    continue
                text_parts.append(txt)
        if run_unread and not text_parts:
            unread += 1
            continue
        rows.append({
            "run_id": rid,
            "run_number": run.get("run_number"),
            "conclusion": run.get("conclusion"),
            "head_branch": run.get("head_branch"),
            "event": run.get("event"),
            "created_at": run.get("created_at"),
            "log": "\n".join(text_parts),
            "log_partial": run_unread,
        })

    if not rows:
        return None, (f"{workflow}: {len(data['workflow_runs'])} run(s) listed but "
                      f"NONE had readable logs ({unread} unread). That is an unread "
                      f"population, not an absent line.")
    return rows, (f"read logs for {len(rows)} run(s) of {workflow}"
                  + (f" ({unread} run(s) UNREAD and excluded — the population is "
                     f"partial)" if unread else ""))


def _contains(literal: str):
    return lambda row: literal in (row.get("log") or "")


class _Ordered(argparse.Action):
    """Collect --workflow/--contains in the ORDER given, so literals bind to the
    workflow that precedes them.

    Several rows clear on BOTH halves of a criterion that live in DIFFERENT
    workflows (the session-brief row: `verdict=inherited` in guards.yml AND
    `stale-branch refresh ->` in a commit-to-main run). A single-workflow probe
    could only cover one half, and a PASS on half a criterion is precisely the
    over-read this family exists to prevent — so the grouping is not a
    convenience, it is what lets the declaration say what clears_when says.
    """

    def __call__(self, parser, ns, value, option_string=None):
        ns.ordered = getattr(ns, "ordered", None) or []
        ns.ordered.append((option_string.lstrip("-"), value))


def build_groups(ordered) -> list[tuple[str, list[str]]]:
    """[(workflow, [literals])]. A literal before any --workflow is an error."""
    groups: list[tuple[str, list[str]]] = []
    for kind, value in ordered or []:
        if kind == "workflow":
            groups.append((value, []))
        else:
            if not groups:
                raise ValueError("--contains given before any --workflow")
            groups[-1][1].append(value)
    if not groups:
        raise ValueError("at least one --workflow is required")
    for wf, lits in groups:
        if not lits:
            raise ValueError(f"--workflow {wf} has no --contains after it")
    return groups


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--workflow", action=_Ordered,
                    help="workflow FILE name, e.g. guards.yml. Repeatable; every "
                         "--contains after it binds to it, and EVERY group must hit.")
    ap.add_argument("--contains", action=_Ordered,
                    help="literal that must appear in ONE run's logs of the "
                         "preceding --workflow. Repeatable; ALL in a group must "
                         "appear in the SAME run.")
    ap.add_argument("--positive-control",
                    help="a literal that DOES appear in these logs today, checked "
                         "against EVERY group. If it does not appear, the verdict is "
                         "could_not_look — a reader proven blind must not emit a "
                         "confident negative.")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--branch")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    try:
        groups = build_groups(getattr(args, "ordered", None))
    except ValueError as exc:
        return probe_lib.die_unlooked(str(exc))

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token:
        return probe_lib.die_unlooked(
            "neither GITHUB_TOKEN nor GH_TOKEN is set — no credential, so nothing "
            "was read. This is NOT 'the line is absent'.")
    if not args.repo:
        return probe_lib.die_unlooked(
            "no repository: pass --repo or set GITHUB_REPOSITORY")

    # EVERY group is scanned before ANY verdict. A group that could not be read
    # makes the WHOLE verdict an unread — we cannot say a criterion went
    # unsatisfied over a population we did not finish reading.
    scanned = []
    for wf, lits in groups:
        rows, note = scan(args.repo, wf, token, args.runs, args.branch)
        if rows is None:
            return probe_lib.die_unlooked(f"[{wf}] {note}")
        scanned.append((wf, lits, rows, note))

    control = _contains(args.positive_control) if args.positive_control else None
    for wf, lits, rows, note in scanned:
        rc = probe_lib.report(rows, [_contains(c) for c in lits],
                              [f"{wf}:{c}" for c in lits], note,
                              control, args.positive_control or "")
        if rc != probe_lib.EXIT_PASS:
            return rc
    print(f"probe: PASS — all {len(scanned)} workflow group(s) matched")
    return probe_lib.EXIT_PASS


def _self_test() -> int:
    import http.server
    import threading
    probe_lib.self_test()
    fired = 0

    def ok(cond, label):
        nonlocal fired
        assert cond, f"control FAILED: {label}"
        fired += 1

    state = {"job_status": 200, "job_body": "hello\nsession-brief: verdict=inherited\nbye"}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            p = self.path.split("?")[0]
            # Order matters: a missing workflow must 404 BEFORE the /runs match,
            # or the fake answers for a workflow that does not exist and the
            # 404 control cannot fire. (The first version of this fake did
            # exactly that, and the control caught it.)
            if "missing.yml" in p:
                code, body = 404, '{"message": "Not Found"}'
            elif p.endswith("/runs"):
                code, body = 200, json.dumps({"workflow_runs": [
                    {"id": 1, "run_number": 7, "conclusion": "success",
                     "head_branch": "main", "event": "push",
                     "created_at": "2026-08-31T00:00:00Z"}]})
            elif p.endswith("/jobs"):
                code, body = 200, json.dumps({"jobs": [{"id": 11, "name": "guards"}]})
            elif p.endswith("/logs"):
                code, body = state["job_status"], state["job_body"]
            elif p.endswith("/empty/runs"):
                code, body = 200, json.dumps({"workflow_runs": []})
            else:
                code, body = 404, "{}"
            self.send_response(code)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    saved_api = globals()["API_BASE"]
    globals()["API_BASE"] = f"http://127.0.0.1:{srv.server_port}"
    saved_tok = os.environ.get("GITHUB_TOKEN")
    try:
        rows, note = scan("o/r", "ci.yml", "tok", 5, None)
        ok(rows is not None and len(rows) == 1, "a run's job logs are fetched and combined")
        ok("session-brief" in rows[0]["log"], "the log text reaches the predicate")

        ok(main(["--repo", "o/r", "--workflow", "ci.yml",
                 "--contains", "verdict=inherited"]) == 0, "end-to-end pass")
        ok(main(["--repo", "o/r", "--workflow", "ci.yml", "--contains", "verdict=inherited",
                 "--workflow", "ci.yml", "--contains", "session-brief:"]) == 0,
           "TWO workflow groups both hitting is a pass")
        ok(main(["--repo", "o/r", "--workflow", "ci.yml", "--contains", "verdict=inherited",
                 "--workflow", "ci.yml", "--contains", "no-such-line"]) == 1,
           "a SECOND group that misses fails the whole probe — a criterion with two "
           "halves must not pass on one")
        ok(main(["--repo", "o/r", "--workflow", "ci.yml", "--contains", "verdict=inherited",
                 "--workflow", "missing.yml", "--contains", "x"]) == 2,
           "a group that could not be READ makes the whole verdict an unread, never "
           "a negative — even when another group hit")
        try:
            build_groups([("contains", "x")])
            ok(False, "a --contains before any --workflow is refused")
        except ValueError:
            ok(True, "a --contains before any --workflow is refused")
        try:
            build_groups([("workflow", "a.yml")])
            ok(False, "a --workflow with no literal is refused")
        except ValueError:
            ok(True, "a --workflow with no literal is refused")
        ok(main(["--repo", "o/r", "--workflow", "ci.yml",
                 "--contains", "verdict=base_unreadable"]) == 1,
           "a literal genuinely absent from a READ log is a real negative")

        state["job_status"], state["job_body"] = 410, "gone"
        ok(main(["--repo", "o/r", "--workflow", "ci.yml",
                 "--contains", "verdict=inherited"]) == 2,
           "an EXPIRED (410) log is could_not_look — the evidence existed and we "
           "arrived late, which is NOT the line being absent")
        state["job_status"], state["job_body"] = 200, "hello\nsession-brief: verdict=inherited\nbye"

        ok(main(["--repo", "o/r", "--workflow", "missing.yml",
                 "--contains", "x"]) == 2, "a 404 workflow is an unread, not a negative")
        ok(main(["--repo", "o/r", "--workflow", "ci.yml", "--contains", "verdict=x",
                 "--positive-control", "session-brief:"]) == 1,
           "a firing control leaves a genuine negative as a negative")
        ok(main(["--repo", "o/r", "--workflow", "ci.yml", "--contains", "verdict=x",
                 "--positive-control", "no-such-marker-anywhere"]) == 2,
           "a control that cannot fire turns the negative into a declared unread")

        os.environ.pop("GITHUB_TOKEN", None)
        os.environ.pop("GH_TOKEN", None)
        ok(main(["--repo", "o/r", "--workflow", "ci.yml", "--contains", "x"]) == 2,
           "NO TOKEN is could_not_look — the single most likely failure of this "
           "probe must never render as 'the line is absent'")
        ok(main(["--repo", "", "--workflow", "ci.yml", "--contains", "x"]) == 2,
           "no repository is likewise an unread")
    finally:
        globals()["API_BASE"] = saved_api
        if saved_tok is not None:
            os.environ["GITHUB_TOKEN"] = saved_tok
        srv.shutdown()

    ok(MAX_RUNS <= 20 and MAX_LOG_BYTES <= 8_000_000,
       "the walk is bounded in BOTH runs and bytes — an unattended unbounded read "
       "is the shape of both June 2026 wedges")

    print(f"probe-actions-log: self-test OK — {fired} planted controls all fire")
    return 0


if __name__ == "__main__":
    sys.exit(main())
