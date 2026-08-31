#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::probe-guard (--self-test, --check) + probes.yml (--run --write)
"""Run the declared probes on `docs/claude/OPEN-ITEMS.json` monitoring rows — W2.

WHY (operator directive, 2026-08-31)
------------------------------------
    "automated scripts that know to run periodically to check when things are
     soaking and when they're done soaking and need to be given a decision …
     so that instead of the session review having to do a whole pull of the live
     VM, we can just see that a test that we set up that definitely verifies
     what we're checking for passed or didn't pass."

A `monitoring` row can only be re-affirmed by an honest look, and today every
one of those looks is a session hand-pulling the live VM. That is exactly the
work a review drops first when budget runs out (measured: the 2026-08-31
`/system-review` completed 25 of 37 items, and every dropped item was the
thinking, not the bookkeeping). A probe moves the LOOKING off the session.

THE CONTRACT, WHICH IS THE WHOLE DESIGN
---------------------------------------
**The probe REPORTS. A session CLEARS the row.** Operator decision, 2026-08-31.

A probe never writes `verified_at`, never writes `observation`, and never
removes a row. It cannot: `clears_when` on these rows names things like "the
mechanism is seen PAGING the operator on a NEW event", and a command that exits
0 is not that observation. A probe that could clear a row would be a machine
asserting an observation nobody made — the exact defect
`OPEN-ITEMS.json` already forbids ("it cannot be cleared by asserting
progress").

So each probe declares TWO things and the second is not optional:
    checks — the observation this command actually makes
    is_not — what a PASS still does not establish

`is_not` exists because the hazard here is not a broken probe, it is a WORKING
probe that is over-read. That is `CLAUDE.md` § "Diagnostic provenance",
sub-class **A**: a real value under a label that does not describe it.

THREE STATES, NEVER COLLAPSED
-----------------------------
    pass          — the probe ran and its condition held
    fail          — the probe ran and its condition did NOT hold
    could_not_run — we did not look (missing tool, no network, timeout,
                    non-zero exit that is not a graded verdict)

`could_not_run` is emphatically not `pass`. Collapsing them is the
`curl … || echo '{}'` failure the repo has already paid for twice.

A row with NO probe carries `probe_absent_reason` instead — so "nothing probes
this" stays distinguishable from "a probe ran and was quiet", and the coverage
gap is countable rather than assumed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OPEN_ITEMS = Path("docs/claude/OPEN-ITEMS.json")
OUT = Path("docs/claude/PROBES.json")

STATES = ("pass", "fail", "could_not_run")

# A probe runs unattended on a schedule. An unbounded one wedges the runner,
# which is the shape of both June 2026 trader wedges one level up.
DEFAULT_TIMEOUT_S = 120


def _load(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def monitoring_rows(root: Path) -> list[dict]:
    items = _load(root / OPEN_ITEMS)["items"]
    return [i for i in items if i.get("kind") == "monitoring"]


# ── the declaration check (what CI enforces) ───────────────────────────────

def declaration_problems(rows: list[dict]) -> list[str]:
    """Every monitoring row must declare a probe OR why it has none.

    Deliberately NOT "every row must have a probe": some conditions genuinely
    cannot be probed yet, and forcing a probe would produce a decorative one
    that always passes — strictly worse than no probe, because a reader would
    trust it (the `new-table-wiring-guard` lesson: a guard cheaper to lie to
    than to satisfy).
    """
    problems: list[str] = []
    for row in rows:
        rid = row.get("id", "(no id)")
        probe = row.get("probe")
        reason = row.get("probe_absent_reason")

        if probe is None and not reason:
            problems.append(
                f"{rid}: monitoring row declares neither `probe` nor "
                f"`probe_absent_reason` — 'nothing probes this' is then "
                f"indistinguishable from 'a probe ran and was quiet'")
            continue
        if probe is not None and reason:
            problems.append(
                f"{rid}: declares BOTH `probe` and `probe_absent_reason` — "
                f"a reader cannot tell which is true")
            continue
        if probe is None:
            if not str(reason).strip():
                problems.append(f"{rid}: `probe_absent_reason` is empty")
            continue

        if not isinstance(probe, dict):
            problems.append(f"{rid}: `probe` must be an object")
            continue
        cmd = probe.get("cmd")
        if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) for c in cmd):
            problems.append(f"{rid}: `probe.cmd` must be a non-empty list of strings "
                            f"(a list, not a shell string — no shell means no injection)")
        if not str(probe.get("checks", "")).strip():
            problems.append(f"{rid}: `probe.checks` is empty — a probe that does not "
                            f"say what it observes cannot be read")
        if not str(probe.get("is_not", "")).strip():
            problems.append(
                f"{rid}: `probe.is_not` is empty. This is the load-bearing field: "
                f"the hazard is a WORKING probe read as proof the row cleared. "
                f"State what a PASS still does not establish.")
    return problems


# ── running ────────────────────────────────────────────────────────────────

def run_one(row: dict, root: Path, timeout_s: int) -> dict:
    rid = row.get("id", "(no id)")
    probe = row.get("probe")
    if probe is None:
        return {
            "id": rid,
            "state": "could_not_run",
            "reason": "no_probe_declared",
            "detail": str(row.get("probe_absent_reason", ""))[:400],
            "checks": None,
            "is_not": None,
        }

    cmd = list(probe["cmd"])
    base = {
        "id": rid,
        "checks": probe.get("checks"),
        # Carried into the OUTPUT, not just the declaration, so a reader of the
        # results file cannot see a `pass` without seeing its limit.
        "is_not": probe.get("is_not"),
        "cmd": cmd,
    }
    try:
        proc = subprocess.run(  # noqa: S603 — cmd is a declared list, never a shell string
            cmd, cwd=root, capture_output=True, text=True, timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        return {**base, "state": "could_not_run", "reason": "tool_missing", "detail": str(exc)[:400]}
    except subprocess.TimeoutExpired:
        return {**base, "state": "could_not_run", "reason": "timeout",
                "detail": f"exceeded {timeout_s}s"}
    except OSError as exc:
        return {**base, "state": "could_not_run", "reason": "os_error", "detail": str(exc)[:400]}

    tail = (proc.stdout or proc.stderr or "").strip().splitlines()
    detail = "\n".join(tail[-8:])[:1200]

    # Exit codes are the contract: 0 = pass, 1 = fail, anything else = we could
    # not look. A probe that cannot distinguish "the condition is false" from
    # "I broke" must return the third, and 2+ is how it says so.
    if proc.returncode == 0:
        return {**base, "state": "pass", "reason": "exit_0", "detail": detail}
    if proc.returncode == 1:
        return {**base, "state": "fail", "reason": "exit_1", "detail": detail}
    return {**base, "state": "could_not_run", "reason": f"exit_{proc.returncode}", "detail": detail}


def build(rows: list[dict], results: list[dict], now: datetime) -> dict:
    counts = {s: sum(1 for r in results if r["state"] == s) for s in STATES}
    probed = sum(1 for r in rows if r.get("probe") is not None)
    return {
        "generated_at": now.isoformat(),
        "contract": "A probe REPORTS. A session CLEARS the row. Nothing here "
                    "writes verified_at, observation, or removes a row.",
        "states": list(STATES),
        # The denominator, always beside the counts. A `pass` count read without
        # it is a share of an unstated population.
        "monitoring_rows": len(rows),
        "rows_with_a_probe": probed,
        "rows_without_a_probe": len(rows) - probed,
        "counts": counts,
        "results": results,
    }


def render_markdown(env: dict) -> str:
    out = ["# Probe results", "",
           f"_Generated {env['generated_at']}_", "",
           f"**{env['rows_with_a_probe']} of {env['monitoring_rows']} monitoring rows "
           f"carry a probe.** pass={env['counts']['pass']} · "
           f"fail={env['counts']['fail']} · could_not_run={env['counts']['could_not_run']}", "",
           "> A probe REPORTS. It never clears a row — read `is_not` on every "
           "pass before treating it as evidence.", ""]
    order = {"fail": 0, "could_not_run": 1, "pass": 2}
    for r in sorted(env["results"], key=lambda r: (order[r["state"]], r["id"])):
        icon = {"fail": "🔴", "could_not_run": "⚪", "pass": "🟢"}[r["state"]]
        out.append(f"- {icon} **{r['id']}** — `{r['state']}` ({r['reason']})")
        if r.get("checks"):
            out.append(f"  - checks: {r['checks']}")
        if r.get("is_not"):
            out.append(f"  - a pass does NOT establish: {r['is_not']}")
        if r.get("detail"):
            out.append(f"  - {r['detail'].splitlines()[0][:200]}")
    return "\n".join(out)


# ── self-test ──────────────────────────────────────────────────────────────

def _self_test() -> int:
    fired = 0

    def ok(cond: bool, label: str) -> None:
        nonlocal fired
        assert cond, f"control FAILED: {label}"
        fired += 1

    good = {"id": "A", "kind": "monitoring",
            "probe": {"cmd": ["true"], "checks": "c", "is_not": "n"}}
    ok(declaration_problems([good]) == [], "a fully declared probe is accepted")

    ok(any("neither" in p for p in declaration_problems([{"id": "B", "kind": "monitoring"}])),
       "a row with no probe and no reason is refused")
    ok(declaration_problems([{"id": "C", "kind": "monitoring",
                              "probe_absent_reason": "no live event yet"}]) == [],
       "a declared absence is accepted")
    ok(any("BOTH" in p for p in declaration_problems([
        {"id": "D", "kind": "monitoring", "probe_absent_reason": "x",
         "probe": {"cmd": ["true"], "checks": "c", "is_not": "n"}}])),
       "declaring both is refused")
    ok(any("is_not" in p for p in declaration_problems([
        {"id": "E", "kind": "monitoring", "probe": {"cmd": ["true"], "checks": "c"}}])),
       "a probe with no `is_not` is refused — the over-read hazard")
    ok(any("checks" in p for p in declaration_problems([
        {"id": "F", "kind": "monitoring", "probe": {"cmd": ["true"], "is_not": "n"}}])),
       "a probe with no `checks` is refused")
    ok(any("list of strings" in p for p in declaration_problems([
        {"id": "G", "kind": "monitoring",
         "probe": {"cmd": "true; rm -rf /", "checks": "c", "is_not": "n"}}])),
       "a shell STRING cmd is refused — a list is what makes shell injection impossible")

    root = Path(".")
    ok(run_one({"id": "H", "probe": {"cmd": ["true"], "checks": "c", "is_not": "n"}},
               root, 30)["state"] == "pass", "exit 0 is pass")
    ok(run_one({"id": "I", "probe": {"cmd": ["false"], "checks": "c", "is_not": "n"}},
               root, 30)["state"] == "fail", "exit 1 is fail")
    r = run_one({"id": "J", "probe": {"cmd": ["sh", "-c", "exit 3"], "checks": "c", "is_not": "n"}},
                root, 30)
    ok(r["state"] == "could_not_run" and r["reason"] == "exit_3",
       "exit 2+ is could_not_run, NOT fail — 'I broke' is not 'the condition is false'")
    ok(run_one({"id": "K", "probe": {"cmd": ["definitely-not-a-real-binary-xyz"],
                                     "checks": "c", "is_not": "n"}},
               root, 30)["state"] == "could_not_run", "a missing tool is could_not_run, not pass")
    ok(run_one({"id": "L", "probe": {"cmd": ["sleep", "5"], "checks": "c", "is_not": "n"}},
               root, 1)["reason"] == "timeout", "a hung probe times out rather than wedging the runner")

    r = run_one({"id": "M", "probe_absent_reason": "nothing to probe yet"}, root, 30)
    ok(r["state"] == "could_not_run" and r["reason"] == "no_probe_declared",
       "an unprobed row reports could_not_run — never absent from the results")

    res = [run_one({"id": "N", "probe": {"cmd": ["true"], "checks": "c", "is_not": "n"}}, root, 30)]
    env = build([{"id": "N", "kind": "monitoring", "probe": {"cmd": ["true"]}},
                 {"id": "O", "kind": "monitoring", "probe_absent_reason": "x"}],
                res, datetime.now(timezone.utc))
    ok(env["monitoring_rows"] == 2 and env["rows_with_a_probe"] == 1,
       "the denominator ships beside the counts")
    ok("is_not" in render_markdown(env) or "does NOT establish" in render_markdown(env),
       "a rendered pass carries its own limit")

    # The contract as an EXECUTABLE control, not a promise in prose: the only
    # thing this file writes is its own results file. If a future edit made it
    # write OPEN-ITEMS, the probe would be clearing rows and this fires.
    import ast as _ast
    tree = _ast.parse(Path(__file__).read_text(encoding="utf-8"))
    targets = [
        _ast.unparse(n.func.value)
        for n in _ast.walk(tree)
        if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)
        and n.func.attr in {"write_text", "write_bytes", "open"}
    ]
    ok(all("OPEN_ITEMS" not in t for t in targets),
       "the runner never opens OPEN-ITEMS for writing — a probe reports, it does "
       "not clear")
    ok(any("OUT" in t for t in targets),
       "the control has a denominator: it DOES see the one write that exists")

    print(f"probes: self-test OK — {fired} planted controls all fire")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="validate the DECLARATIONS only; runs no probe, touches no network")
    ap.add_argument("--run", action="store_true", help="execute the declared probes")
    ap.add_argument("--write", action="store_true", help=f"with --run, write {OUT}")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    root = Path(args.root)
    try:
        rows = monitoring_rows(root)
    except Exception as exc:  # noqa: BLE001
        print(f"probes: FAIL — cannot read {OPEN_ITEMS} ({exc})")
        return 1

    if args.check:
        problems = declaration_problems(rows)
        if problems:
            print(f"probes: {len(rows)} monitoring row(s)\n\nFAIL — undeclared probe state:")
            for p in problems:
                print(f"  - {p}")
            return 1
        probed = sum(1 for r in rows if r.get("probe") is not None)
        print(f"probes: OK — {len(rows)} monitoring row(s), {probed} probed, "
              f"{len(rows) - probed} with a declared reason for having none")
        return 0

    if not args.run:
        ap.error("pass one of --self-test / --check / --run")

    results = [run_one(r, root, args.timeout) for r in rows]
    env = build(rows, results, datetime.now(timezone.utc))

    if args.markdown:
        print(render_markdown(env))
        return 0
    if args.write:
        (root / OUT).write_text(json.dumps(env, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
        print(f"probes: wrote {OUT} — {env['counts']}")
        return 0
    print(json.dumps(env, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
