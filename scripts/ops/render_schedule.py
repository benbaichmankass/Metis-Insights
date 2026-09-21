#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::schedule-guard (--self-test + --check).
# Read by A9 (docs/claude/work/MANAGER-CHECKLIST.json); served live by
# GET /api/bot/work/schedule (src/web/api/routers/work.py).
"""THE WORK SCHEDULE — renders what already exists, on the operator's page.

OPERATOR-REQUESTED 2026-09-21, verbatim: "A work schedule, visible on the
gitpages UI site -- when different sessions need to happen, when decisions
are due, monitoring items, etc."

THE HARD CONSTRAINT, AND IT IS THE WHOLE DESIGN: it renders what already
exists — this is not a sixth register. Four sources, three computed live and
never re-derived here:

  * cadenced sessions — `docs/claude/work/SCHEDULE.json`. The ONE new
    declaration, and deliberately narrow: the daily sync and the three
    project-level reviews (`/health-review`, `/performance-review`,
    `/ml-review`) have NO machine-readable cadence record anywhere else in
    this repo — they are named in `CLAUDE.md` / skill prose, not in a data
    file. Per the A9 checklist row: "if it needs a fact nobody records, that
    is a finding about the pipeline, not a licence for a second register" —
    this fact is not a pipeline finding (no due date, no observation to
    recheck), it is a standing declaration, the same shape as
    `config/mandates.yaml`. It is NOT where decisions/monitoring/crons live.
  * decisions owed — `scripts/ops/pipeline.py`, items DUE with
    `next_action == "ask_operator"`. Reused via `pipeline.read_log()` /
    `pipeline.due()`, never re-parsed — this module does not touch
    `PIPELINE.jsonl`'s bytes.
  * monitoring coming due — the rest of `pipeline.due()`
    (`next_action != "ask_operator"`).
  * cron cadences — read out of `.github/workflows/*.yml`'s real `on:`
    trigger via `yaml.safe_load`, never a text grep for the word
    "schedule:". Verified 2026-09-21: 29 workflow files contain that word
    somewhere (many as a comment left by the 2026-09-21 operating-reset
    disarm — see e.g. `due-list.yml`'s `# schedule: / # - cron: ...`), and
    parsing the actual YAML `on.schedule` key finds exactly 18 with an ARMED
    cron. A grep-based reader would have reported 29.

Usage::

    python3 scripts/ops/render_schedule.py              # print markdown
    python3 scripts/ops/render_schedule.py --json        # print the envelope
    python3 scripts/ops/render_schedule.py --self-test
    python3 scripts/ops/render_schedule.py --check        # CI guard mode
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# REUSED, NOT RE-DERIVED — never re-implement PIPELINE.jsonl's parser or its
# due-ness rule here; call pipeline.py the way render_daily_brief.py does.
from scripts.ops import pipeline  # noqa: E402

SCHEDULE_DECL = Path("docs/claude/work/SCHEDULE.json")
_PIPELINE_STORE = pipeline.STORE
_WORKFLOWS_DIR = Path(".github/workflows")

READ_STATES = ("read", "absent", "unreadable")
_HOLE = {
    "absent": "⛔ ABSENT (we looked; it is not there)",
    "unreadable": "⛔ UNREADABLE (**we could not look** — this is not 'empty')",
    "partial": "⚠️ PARTIAL (some records/files could not be read)",
    "read": "✅ read",
}


# ── reading, with the state kept (never collapsed: absent vs unreadable) ──

def read_json(path: Path, root: Path | None = None) -> tuple[Any, str]:
    p = (root or REPO_ROOT) / path
    if not p.exists():
        return None, "absent"
    try:
        return json.loads(p.read_text(encoding="utf-8")), "read"
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None, "unreadable"


def cadenced_sessions(doc: Any, state: str) -> dict[str, Any]:
    if state != "read":
        return {"state": state, "sessions": []}
    rows = (doc or {}).get("sessions") or []
    return {"state": "read", "sessions": rows if isinstance(rows, list) else []}


# ── cron cadences — the real YAML trigger, never a text grep ──────────────

def read_workflow_crons(root: Path | None = None) -> dict[str, Any]:
    """Live cron cadences, read out of `.github/workflows/*.yml::on.schedule`.

    ⚠️ Parses the actual YAML `on:` trigger. A workflow whose cron is
    commented out (the 2026-09-21 operating-reset disarm, e.g.
    `due-list.yml`) is correctly ABSENT from the result — `#`-prefixed YAML
    is invisible to `yaml.safe_load`, so there is no separate "is it
    commented out" check to get wrong.
    """
    root = root or REPO_ROOT
    wf_dir = root / _WORKFLOWS_DIR
    rows: list[dict[str, Any]] = []
    unreadable: list[str] = []
    if not wf_dir.is_dir():
        return {"state": "absent", "workflows": [], "unreadable": []}
    for path in sorted(wf_dir.glob("*.yml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
            unreadable.append(f"{path.name}: {type(exc).__name__}: {exc}")
            continue
        if not isinstance(doc, dict):
            continue
        # PyYAML 1.1 parses a bare `on:` key as the boolean True.
        on = doc.get("on")
        if on is None:
            on = doc.get(True)
        if not isinstance(on, dict):
            continue
        schedule = on.get("schedule")
        if not isinstance(schedule, list):
            continue
        crons = [s["cron"] for s in schedule if isinstance(s, dict) and s.get("cron")]
        if not crons:
            continue
        rows.append({
            "workflow": path.name,
            "name": doc.get("name") or path.stem,
            "cron": crons,
        })
    return {
        "state": "read" if not unreadable else "partial",
        "workflows": rows,
        "unreadable": unreadable,
    }


# ── pipeline split — decisions owed vs monitoring due, both DUE items ─────

def _pipeline_rows(res: pipeline.LoadResult, today: date) -> tuple[list[dict], list[dict]]:
    """Split `pipeline.due()` into decisions owed vs monitoring due.

    ⚠️ Calls `pipeline.due()` wholesale — never re-derives due-ness. The
    split is purely on `next_action`, a field pipeline.py already validates.
    """
    rows = pipeline.due(res.items.values(), today)
    decisions = [r for r in rows if r.get("next_action") == "ask_operator"]
    monitoring = [r for r in rows if r.get("next_action") != "ask_operator"]

    def _key(r: dict) -> str:
        return str(r.get("id") or "")

    return sorted(decisions, key=_key), sorted(monitoring, key=_key)


# ── assembly ─────────────────────────────────────────────────────────────

def build(*, today: date | None = None, root: Path | None = None) -> dict[str, Any]:
    """Assemble the schedule envelope. Reads files; writes nothing."""
    root = root or REPO_ROOT
    today = today or datetime.now(timezone.utc).date()

    pipe_res = pipeline.read_log(root / _PIPELINE_STORE)
    decisions, monitoring = _pipeline_rows(pipe_res, today)

    decl_doc, decl_state = read_json(SCHEDULE_DECL, root)
    sessions = cadenced_sessions(decl_doc, decl_state)

    crons = read_workflow_crons(root)

    return {
        "schemaVersion": 1,
        "forDate": today.isoformat(),
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "cadencedSessions": sessions,
        "decisionsOwed": decisions,
        "monitoringDue": monitoring,
        "pipeline": {"healthy": pipe_res.healthy, "unreadable": len(pipe_res.unreadable)},
        "crons": crons,
        "inputs": {
            "pipeline": "read" if pipe_res.healthy else "partial",
            "cadencedSessions": sessions["state"],
            "workflows": crons["state"],
        },
        # ⚠️ NEVER True — this reads PIPELINE.jsonl, SCHEDULE.json and the
        # workflow files. It does not read the checklist, mandates, or any
        # live VM/session state. A consumer treating it as the whole of
        # "what is scheduled" has misread it — same discipline as
        # render_daily_brief.py's `coverageComplete`.
        "coverageComplete": False,
    }


# ── rendering ────────────────────────────────────────────────────────────

def _hdr(b: dict) -> list[str]:
    return [f"# WORK SCHEDULE — {b['forDate']}", "",
            f"_Generated `{b['generatedAt']}` by `scripts/ops/render_schedule.py`, "
            "served live by `GET /api/bot/work/schedule`._", "",
            "_**GENERATED — do not hand-edit.** Renders what already exists: "
            "cadenced sessions · decisions owed · monitoring coming due · "
            "cron cadences._", ""]


def _section_sessions(b: dict) -> list[str]:
    sv = b["cadencedSessions"]
    L = ["## CADENCED SESSIONS", ""]
    if sv["state"] != "read":
        L += [f"{_HOLE[sv['state']]} — `{SCHEDULE_DECL}`.", ""]
        return L
    if not sv["sessions"]:
        L += ["_None declared._", ""]
        return L
    for s in sv["sessions"]:
        L.append(f"- **{s.get('title', s.get('id', '?'))}** — {s.get('cadence', '?')}"
                  f" _(source: {s.get('source', '?')})_")
        if s.get("note"):
            L.append(f"  - {s['note']}")
    L.append("")
    return L


def _section_decisions(b: dict) -> list[str]:
    rows = b["decisionsOwed"]
    L = [f"## DECISIONS OWED ({len(rows)})", "",
         "_Pipeline items DUE with `next_action: ask_operator`. From "
         "`scripts/ops/pipeline.py::due()`, never re-derived._", ""]
    if not rows:
        L += ["_None due._", ""]
        return L
    for i in rows:
        L.append(f"- **{i.get('id')}** [{i.get('state')}] {i.get('what')} "
                  f"· rerun: `{(i.get('origin') or {}).get('rerun')}`")
    L.append("")
    return L


def _section_monitoring(b: dict) -> list[str]:
    rows = b["monitoringDue"]
    L = [f"## MONITORING COMING DUE ({len(rows)})", "",
         "_The rest of `pipeline.py::due()` — items due whose `next_action` "
         "is not `ask_operator`._", ""]
    if not rows:
        L += ["_None due._", ""]
        return L
    for i in rows:
        owner = f" → `{i['routed_to']}`" if i.get("routed_to") else ""
        L.append(f"- **{i.get('id')}** [{i.get('state')}{owner}] {i.get('what')} "
                  f"· next: `{i.get('next_action')}`")
    L.append("")
    if not b["pipeline"]["healthy"]:
        L += [f"> ⚠️ {b['pipeline']['unreadable']} pipeline record(s) could not "
              "be parsed — the lists above are a floor, not a total.", ""]
    return L


def _section_crons(b: dict) -> list[str]:
    c = b["crons"]
    rows = c["workflows"]
    L = [f"## CRON CADENCES ({len(rows)})", "",
         "_Read out of `.github/workflows/*.yml`'s real `on.schedule` "
         "trigger — a commented-out cron is correctly absent._", ""]
    if c["state"] == "absent":
        L += [f"{_HOLE['absent']} — `.github/workflows/`.", ""]
        return L
    if not rows:
        L += ["_None armed._", ""]
    for w in rows:
        L.append(f"- **{w['name']}** (`{w['workflow']}`) — `{', '.join(w['cron'])}`")
    if rows:
        L.append("")
    if c["unreadable"]:
        L += [f"> ⚠️ {len(c['unreadable'])} workflow file(s) could not be parsed "
              "— this list is a floor, not a total:", ""]
        for why in c["unreadable"][:10]:
            L.append(f"> - {why}")
        L.append("")
    return L


def _footer(b: dict) -> list[str]:
    return ["---", "", "## INPUTS", "",
            "| input | state | path |", "|---|---|---|",
            f"| `pipeline` | `{'read' if b['pipeline']['healthy'] else 'partial'}` "
            f"| `{_PIPELINE_STORE}` |",
            f"| `cadencedSessions` | `{b['cadencedSessions']['state']}` | `{SCHEDULE_DECL}` |",
            f"| `workflows` | `{b['crons']['state']}` | `{_WORKFLOWS_DIR}/*.yml` |", "",
            "⚠️ **`coverageComplete` is `false`.** This reads the pipeline "
            "store, `SCHEDULE.json` and the workflow files. It does not read "
            "the checklist, `config/mandates.yaml`, or any live VM/session "
            "state. A reader treating it as the whole of \"what is "
            "scheduled\" has misread it.", ""]


def render(b: dict) -> str:
    parts = (_hdr(b) + _section_sessions(b) + _section_decisions(b)
             + _section_monitoring(b) + _section_crons(b) + _footer(b))
    return "\n".join(parts).rstrip() + "\n"


# ── self-test: planted controls in both directions ─────────────────────────

def _self_test() -> int:
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(f"  self-test ({name}): {'PASS' if cond else 'FAIL'}")
        ok = ok and cond

    today = date(2026, 9, 21)

    def base_item(**over: Any) -> dict:
        item = {
            "id": "PI-X", "what": "one line", "state": "queued",
            "origin": {"kind": "audit", "ref": "r", "rerun": "r"},
            "due_when": {"kind": "observation", "clears_when": "c"},
            "next_action": "check_observation", "routed_to": None,
            "terminal_reason": None,
        }
        item.update(over)
        return item

    # ── decisions vs monitoring split, both from pipeline.due() ───────────
    res = pipeline.LoadResult(items={
        "D1": base_item(id="D1", next_action="ask_operator"),
        "M1": base_item(id="M1", next_action="check_observation"),
        "NOTDUE": base_item(id="NOTDUE", next_action="ask_operator",
                             due_when={"kind": "observation", "clears_when": "c",
                                       "check_every_days": 30,
                                       "last_checked": "2026-09-20"}),
    })
    decisions, monitoring = _pipeline_rows(res, today)
    check("decisions owed is ask_operator DUE items only",
          [r["id"] for r in decisions] == ["D1"])
    check("monitoring due excludes ask_operator and excludes not-yet-due",
          [r["id"] for r in monitoring] == ["M1"])
    check("…and pipeline.due() is what decides due-ness (negative control: "
          "NOTDUE inside its cadence is in neither list)",
          "NOTDUE" not in [r["id"] for r in decisions + monitoring])

    # ── cadenced sessions: three states, never collapsed ───────────────────
    check("absent SCHEDULE.json is a declared hole",
          cadenced_sessions(None, "absent") == {"state": "absent", "sessions": []})
    check("unreadable SCHEDULE.json says 'could not look', not empty",
          cadenced_sessions(None, "unreadable")["state"] == "unreadable")
    check("a read SCHEDULE.json with rows is passed through",
          cadenced_sessions({"sessions": [{"id": "x"}]}, "read")["sessions"] == [{"id": "x"}])

    # ── cron reader: armed vs commented-out, on REAL workflow YAML shapes ──
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        r = Path(td)
        (r / ".github" / "workflows").mkdir(parents=True)
        (r / ".github" / "workflows" / "armed.yml").write_text(
            "name: armed-job\non:\n  schedule:\n    - cron: '0 6 * * *'\n  "
            "workflow_dispatch:\n", encoding="utf-8")
        (r / ".github" / "workflows" / "disarmed.yml").write_text(
            "name: disarmed-job\non:\n  # schedule:\n    # - cron: '50 5 * * *'\n  "
            "workflow_dispatch:\n", encoding="utf-8")
        (r / ".github" / "workflows" / "no_trigger.yml").write_text(
            "name: no-trigger\n", encoding="utf-8")
        (r / ".github" / "workflows" / "broken.yml").write_text(
            "name: broken\non: [oops\n", encoding="utf-8")
        c = read_workflow_crons(r)
        names = {w["name"] for w in c["workflows"]}
        check("an ARMED cron is found", "armed-job" in names)
        check("⚠️ a cron commented out (the operating-reset disarm shape) is "
              "correctly ABSENT — text grep would have found the word "
              "'schedule:' in both files; YAML parsing does not "
              "(negative control)",
              "disarmed-job" not in names)
        check("a workflow with no schedule trigger contributes nothing",
              "no-trigger" not in names)
        check("a malformed workflow file is reported unreadable, not dropped "
              "silently", len(c["unreadable"]) == 1 and "broken.yml" in c["unreadable"][0])
        check("…and the state reflects the partial read",
              c["state"] == "partial")
        empty_dir = Path(tempfile.mkdtemp())
        check("no .github/workflows dir at all is 'absent', not an error",
              read_workflow_crons(empty_dir)["state"] == "absent")

    # ── build() + render() over a real (relative) root ─────────────────────
    with tempfile.TemporaryDirectory() as td:
        r = Path(td)
        (r / "docs" / "claude" / "work").mkdir(parents=True)
        (r / "docs" / "claude" / "work" / "SCHEDULE.json").write_text(
            json.dumps({"sessions": [{"id": "daily-sync", "title": "Daily sync",
                                       "cadence": "once per day", "source": "CLAUDE.md"}]}),
            encoding="utf-8")
        (r / "docs" / "claude" / "work" / "PIPELINE.jsonl").write_text(
            json.dumps(base_item(id="D2", next_action="ask_operator")) + "\n",
            encoding="utf-8")
        (r / ".github" / "workflows").mkdir(parents=True)
        (r / ".github" / "workflows" / "x.yml").write_text(
            "name: x\non:\n  schedule:\n    - cron: '0 0 * * *'\n", encoding="utf-8")
        b = build(today=today, root=r)
        md = render(b)
        check("render() surfaces the declared session",
              "Daily sync" in md and "once per day" in md)
        check("render() surfaces the due decision",
              "**D2**" in md and "DECISIONS OWED (1)" in md)
        check("render() surfaces the armed cron",
              "0 0 * * *" in md)
        check("coverage is declared incomplete",
              "`coverageComplete` is `false`" in md)
        check("the artifact says it is generated",
              "do not hand-edit" in md)

        # missing SCHEDULE.json / PIPELINE.jsonl / workflows dir entirely
        r2 = Path(tempfile.mkdtemp())
        b2 = build(today=today, root=r2)
        md2 = render(b2)
        check("a wholly-missing tree declares every hole, never a silent "
              "empty-but-clean render",
              "we looked; it is not there" in md2
              and b2["cadencedSessions"]["state"] == "absent"
              and b2["crons"]["state"] == "absent")

    print(f"schedule self-test: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def _check() -> int:
    """Render over the LIVE files and assert the invariant sentences. Grades
    the CODE, not the data — an unreadable/absent input is reported loudly
    and PASSES (same discipline as `render_daily_brief.py::_check`)."""
    try:
        b = build()
        md = render(b)
    except Exception as exc:  # noqa: BLE001 — the failure IS the finding
        print(f"schedule: FAIL — the renderer raised on the live inputs: "
              f"{type(exc).__name__}: {exc}")
        return 1

    required = [
        ("cadenced sessions section present", "## CADENCED SESSIONS" in md),
        ("decisions-owed section present", "## DECISIONS OWED" in md),
        ("monitoring-due section present", "## MONITORING COMING DUE" in md),
        ("cron-cadences section present", "## CRON CADENCES" in md),
        ("inputs are enumerated with a state", "## INPUTS" in md),
        ("coverage is declared incomplete", "`coverageComplete` is `false`" in md),
        ("the artifact says it is generated", "do not hand-edit" in md),
    ]
    missing = [why for why, okv in required if not okv]
    if missing:
        for why in missing:
            print(f"  ::FINDING:: the schedule no longer states: {why}")
        print(f"schedule: FAIL — {len(missing)} invariant(s) missing")
        return 1

    for name, state in (("pipeline", "read" if b["pipeline"]["healthy"] else "partial"),
                        ("cadencedSessions", b["cadencedSessions"]["state"]),
                        ("workflows", b["crons"]["state"])):
        if state not in ("read",):
            print(f"  ::NOTICE:: input `{name}` reads `{state}` — the schedule "
                  f"declares it, and this check PASSES rather than redding "
                  f"unrelated PRs")
    print(f"schedule: OK — decisionsOwed={len(b['decisionsOwed'])}, "
          f"monitoringDue={len(b['monitoringDue'])}, "
          f"crons={len(b['crons']['workflows'])}, "
          f"cadencedSessions={b['cadencedSessions']['state']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="Emit the envelope.")
    ap.add_argument("--check", action="store_true",
                    help="Guard mode: render over the live inputs and assert "
                         "the invariant sentences.")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()
    if a.check:
        return _check()

    b = build()
    print(json.dumps(b, indent=2, default=str) if a.json else render(b))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
