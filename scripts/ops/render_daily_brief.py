#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::daily-brief-guard (--self-test + --check);
# served live by GET /api/bot/work/brief (src/web/api/routers/work.py).
"""THE DAILY BRIEF — six fixed sections, per `.claude/skills/manager/SKILL.md`
§ "The daily brief" and `docs/plans/OPERATING-PLAN-2026-09-21.md` § 5.4:

    0 what came due · 1 taken under mandate · 2 decisions for you ·
    3 what moved · 4 what is running · 5 spend

REPLACES THE PRE-RESET FOUR-SECTION MODULE OF THE SAME NAME (kept in git
history; read it before touching this one). That module read six inputs —
`MANAGER-CHECKLIST.json`, `SESSIONS.json`, `OPEN-PRS.json`,
`MANAGER-LEASE.json`, `OPEN-ITEMS.json`, `CYCLE-PRIORITY.json` — of which the
2026-09-21 operating reset archived five, leaving only the checklist. It
rendered green in its own unit tests while being structurally unable to
answer the question it was built for (`ARCHITECTURE-CANONICAL.md`'s own
words: "a brief that renders but does not answer that sentence FAILS"). This
module is A3 — the acceptance criterion, verbatim, is whether it does.

KEPT from the old module, deliberately: the DELTA/STATE split (§3 is what
just concluded, §4 is what is still open) and the discipline of declaring
what could not be read rather than rendering silence or a fabricated zero.

DECIDED 2026-09-21 (A3a) — THE SCHEDULE QUESTION
--------------------------------------------------
The old module's docstring argued the brief could not be a cron: one of its
four inputs — what a night session CONCLUDED — lived only in `get_session`'s
`post_turn_summary`, reachable from a live manager session and from nothing
else. That constraint drove the whole design (a close-out deliverable, never
a cron, `--session-notes` as a declared hole when omitted).

**That constraint does not apply to this module, and this is a decision, not
an oversight.** All six sections here are built from inputs already on disk
or already reachable by the process serving them:

  * §0 / §5's unrouted count — `scripts/ops/pipeline.py` reading
    `docs/claude/work/PIPELINE.jsonl`.
  * §1 — `config/mandates.yaml` (today: absent — B5 is not built).
  * §2, §3, §4 — `docs/claude/work/MANAGER-CHECKLIST.json`.

None of them needs a live `mcp__*` observation. So instead of a generated
file a cron would have to write and the operator would have to trust was
recent, **this module is served LIVE**: `GET /api/bot/work/brief` calls
`build()` + `render()` on every request (20s cache), exactly the way
`GET /api/bot/work/checklist` already serves `MANAGER-CHECKLIST.json` — see
`src/web/api/routers/work.py::get_work_checklist`. The route reads the VM's
working tree, which `ict-git-sync` pulls roughly every 5 minutes, so the
brief is never stale beyond that interval and **needs no writer step at
all** — which is a stronger answer to "produced without anyone choosing to
run it" than a cron writing a file would be: there is no file to go stale
between runs.

The CLI below (`python3 scripts/ops/render_daily_brief.py`) is kept for a
human who wants a terminal/markdown copy, for `--write`ing a dated snapshot
on request, and for the `--self-test` / `--check` the CI guard runs. It is
NOT the authoritative generation path any more — the route is.

⚠️ **This closes the missing half of A7, and only this half.** A7 built the
pipeline's store, validator and pull logic
(`scripts/ops/pipeline.py::render_section_0` / `unrouted_count`); until
something rendered them on the operator's own page, asking was still
voluntary — reason (5) in the operating plan, the exact failure that killed
`DUE.md`. This route is that render. A7 is not "done" by this alone — A8's
import of the archived backlog rows is separate — but the pull described in
plan § 3b is now connected end to end: PIPELINE.jsonl → `pipeline.py` →
this module → `GET /api/bot/work/brief` → the Workflow page.

Usage::

    python3 scripts/ops/render_daily_brief.py              # print to stdout
    python3 scripts/ops/render_daily_brief.py --write       # comms/briefs/<date>.md
    python3 scripts/ops/render_daily_brief.py --self-test
    python3 scripts/ops/render_daily_brief.py --check        # CI guard mode
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

# REUSED, NOT RE-DERIVED — the whole point of A3 is to call A7's functions
# rather than fork the definition of "due". `render_section_0` already emits
# its own "## §0 —" header, so it is inserted into this brief verbatim.
from scripts.ops import pipeline  # noqa: E402

BRIEF_DIR = REPO_ROOT / "comms" / "briefs"

_CHECKLIST = Path("docs/claude/work/MANAGER-CHECKLIST.json")
_MANDATES = Path("config/mandates.yaml")
_PIPELINE_STORE = pipeline.STORE

#: Terminal-ish checklist states that read as "moved" rather than "running".
#: `landed_unproven` is terminal-ish (a lane stopped being worked) but is NOT
#: `done` — the one invariant carried over verbatim from the old module.
_MOVED_STATES = ("done", "dropped", "landed_unproven")
_DONE_STATES = frozenset({"done"})
_NOT_DONE_BUT_MERGED = "landed_unproven"
#: States that still owe someone eyes, enumerated in §4 in full — never a
#: count-only line, because silently dropping a live row is what this brief
#: exists to prevent.
_RUNNING_STATES = ("in_flight", "blocked")

READ_STATES = ("read", "absent", "unreadable")
_HOLE = {
    "absent": "⛔ ABSENT (we looked; it is not there)",
    "unreadable": "⛔ UNREADABLE (**we could not look** — this is not 'empty')",
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


def read_yaml(path: Path, root: Path | None = None) -> tuple[Any, str]:
    p = (root or REPO_ROOT) / path
    if not p.exists():
        return None, "absent"
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")), "read"
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return None, "unreadable"


# ── §2/§3/§4 — checklist views ─────────────────────────────────────────────

def checklist_view(doc: Any) -> dict[str, Any]:
    """Split the checklist by state. `landed_unproven` stays OUT of `done`."""
    items = [i for i in ((doc or {}).get("items") or []) if isinstance(i, dict)]
    by_state: dict[str, list[dict]] = {}
    for it in items:
        by_state.setdefault(str(it.get("state") or "unstated"), []).append(it)
    return {
        "total": len(items),
        "asOf": (doc or {}).get("as_of") or (doc or {}).get("updated_at"),
        "byState": by_state,
        "counts": {k: len(v) for k, v in sorted(by_state.items())},
        "doneCount": sum(len(v) for k, v in by_state.items() if k in _DONE_STATES),
        "mergedEffectUnobservedCount": len(by_state.get(_NOT_DONE_BUT_MERGED, [])),
    }


def _is_operator_owned(item: dict) -> bool:
    return "operator" in str(item.get("owner") or "").lower()


def decisions_for_you(ck: dict[str, Any]) -> list[dict]:
    """§2 population: operator-owned rows not yet resolved. NO CAP — a queue
    outrunning one person is an argument for a mandate, not for truncation."""
    out = []
    for state, rows in ck["byState"].items():
        if state in ("done", "dropped"):
            continue
        out.extend(r for r in rows if _is_operator_owned(r))
    return sorted(out, key=lambda r: str(r.get("id") or ""))


def _blocked_on_text(it: dict) -> str:
    edges = it.get("blocked_on")
    if not edges:
        return ""
    parts = []
    for e in edges if isinstance(edges, list) else [edges]:
        if isinstance(e, dict):
            parts.append(f"{e.get('kind', '?')}:{e.get('ref', '?')}"
                         + (f" — {e['what']}" if e.get("what") else ""))
        else:
            parts.append(str(e))
    return "; ".join(parts)


# ── §1 — mandates ────────────────────────────────────────────────────────

def mandates_view(doc: Any, state: str) -> dict[str, Any]:
    if state == "absent":
        return {"state": "absent", "mandates": []}
    if state != "read":
        return {"state": state, "mandates": []}
    rows = (doc or {}).get("mandates") or []
    return {"state": "read", "mandates": rows if isinstance(rows, list) else []}


# ── assembly ─────────────────────────────────────────────────────────────

def build(*, today: date | None = None, root: Path | None = None) -> dict[str, Any]:
    """Assemble the brief. Reads files; writes nothing."""
    root = root or REPO_ROOT
    today = today or datetime.now(timezone.utc).date()

    pipe_res = pipeline.read_log(root / _PIPELINE_STORE)
    pipe_stats = pipeline.stats(pipe_res, today)
    section0_lines = pipeline.render_section_0(pipe_res, today)

    checklist_doc, checklist_state = read_json(_CHECKLIST, root)
    ck = checklist_view(checklist_doc) if checklist_state == "read" else checklist_view({})

    mandates_doc, mandates_state = read_yaml(_MANDATES, root)

    return {
        "schemaVersion": 1,
        "forDate": today.isoformat(),
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "pipeline": {"stats": pipe_stats, "section0Lines": section0_lines,
                     "healthy": pipe_res.healthy},
        "checklistState": checklist_state,
        "checklist": ck,
        "mandatesState": mandates_state,
        "mandates": mandates_view(mandates_doc, mandates_state),
        # ⚠️ NEVER True. This reads the pipeline store, the checklist and
        # `config/mandates.yaml`. It does not read `src/`, the VM, or the
        # three review backlogs. A consumer treating it as the whole of
        # system state has misread it — the same discipline the old module
        # carried as `coverageComplete`.
        "coverageComplete": False,
    }


# ── rendering — six sections, fixed order ──────────────────────────────────

def _section0(b: dict) -> list[str]:
    # `render_section_0` already emits its own "## §0 —" header — inserted
    # verbatim per the binding constraint: call pipeline.py, never re-derive
    # due-ness here.
    return list(b["pipeline"]["section0Lines"]) + [""]


def _section1(b: dict) -> list[str]:
    L = ["---", "", "## §1 — TAKEN UNDER MANDATE", ""]
    mv = b["mandates"]
    if mv["state"] == "absent":
        L += ["**No mandate mechanism exists yet.** `config/mandates.yaml` does "
              "not exist — B5 (the mandate mechanism) is not built. This is "
              "*there is no mechanism*, distinct from *a mechanism exists and "
              "nothing has fired*: nothing in this system can act on real "
              "money without an explicit operator decision today.", ""]
    elif mv["state"] == "unreadable":
        L += [f"{_HOLE['unreadable']} — `config/mandates.yaml` exists and "
              "could not be parsed. This is **we could not look**, never "
              "*nothing fired*.", ""]
    elif not mv["mandates"]:
        L += ["`config/mandates.yaml` exists and declares **no mandates**. "
              "That is a reading, not a hole.", ""]
    else:
        L += [f"{len(mv['mandates'])} mandate(s) declared. This module does "
              "not yet read a firing/evidence log — none exists on disk — so "
              "it reports the declared mandates only; **a report of what "
              "fired is a separate, not-yet-built surface, not zero "
              "firings.**", ""]
        for m in mv["mandates"]:
            if isinstance(m, dict):
                L.append(f"- `{m.get('id', '?')}` — {m.get('grants', m)}")
        L.append("")
    return L


def _section2(b: dict) -> list[str]:
    if b["checklistState"] != "read":
        return ["---", "", "## §2 — DECISIONS FOR YOU", "",
                f"{_HOLE[b['checklistState']]} — `docs/claude/work/MANAGER-CHECKLIST.json`. "
                "The standing picture below is missing its centre.", ""]
    rows = decisions_for_you(b["checklist"])
    L = ["---", "", f"## §2 — DECISIONS FOR YOU ({len(rows)})", "",
         "_Checklist rows owned (in full or in part) by the operator and not "
         "yet `done`/`dropped`. **No cap** — a queue outrunning one person is "
         "an argument for a mandate, not for shortening this list._", ""]
    if not rows:
        L += ["_None._", ""]
        return L
    for it in rows:
        L.append(f"- **{it.get('id')}** [{it.get('state')}] {it.get('title') or '(untitled)'}"
                 f" · owner `{it.get('owner')}`")
        if it.get("note"):
            L.append(f"  - {it['note']}")
        bo = _blocked_on_text(it)
        if bo:
            L.append(f"  - blocked on: {bo}")
    L.append("")
    return L


def _section3(b: dict) -> list[str]:
    L = ["---", "", "## §3 — WHAT MOVED", ""]
    if b["checklistState"] != "read":
        L += [f"{_HOLE[b['checklistState']]} — `docs/claude/work/MANAGER-CHECKLIST.json`.", ""]
        return L
    ck = b["checklist"]
    L += [f"> ⚠️ **`{_NOT_DONE_BUT_MERGED}` ({ck['mergedEffectUnobservedCount']}) is "
          f"NOT `done` ({ck['doneCount']}).** A merge is a deploy, not an "
          "observation; the two are never added together.", ""]
    for state in ("done", "dropped", _NOT_DONE_BUT_MERGED):
        rows = ck["byState"].get(state, [])
        head = {"done": "✅ DONE — merged and observed",
                "dropped": "🗑️ DROPPED — closed without landing",
                _NOT_DONE_BUT_MERGED: ("⏳ LANDED, EFFECT UNOBSERVED — merged; "
                                       "**NOT done**")}[state]
        L += [f"### {head} ({len(rows)})", ""]
        if not rows:
            L += ["_None._", ""]
            continue
        for it in rows:
            line = f"- **{it.get('id')}** — {it.get('title') or '(untitled)'}"
            if it.get("owner"):
                line += f" · owner `{it['owner']}`"
            L.append(line)
            if it.get("note"):
                L.append(f"  - {it['note']}")
        L.append("")
    return L


def _section4(b: dict) -> list[str]:
    if b["checklistState"] != "read":
        return ["---", "", "## §4 — WHAT IS RUNNING", "",
                f"{_HOLE[b['checklistState']]} — `docs/claude/work/MANAGER-CHECKLIST.json`.", ""]
    ck = b["checklist"]
    running_total = sum(len(ck["byState"].get(s, [])) for s in _RUNNING_STATES)
    L = ["---", "", f"## §4 — WHAT IS RUNNING ({running_total})", ""]
    for state in _RUNNING_STATES:
        rows = ck["byState"].get(state, [])
        head = {"in_flight": "🔧 IN FLIGHT — a session is actively working it",
                "blocked": "⛔ BLOCKED — waiting on a named thing"}[state]
        L += [f"### {head} ({len(rows)})", ""]
        if not rows:
            L += ["_None._", ""]
            continue
        for it in rows:
            line = f"- **{it.get('id')}** — {it.get('title') or '(untitled)'}"
            if it.get("lane"):
                line += f" · lane `{it['lane']}`"
            if it.get("model"):
                line += f" · model `{it['model']}`"
            if it.get("spend_usd") is not None or it.get("ceiling_usd") is not None:
                line += (f" · spend ${it.get('spend_usd')} / "
                         f"ceiling ${it.get('ceiling_usd')}")
            L.append(line)
            bo = _blocked_on_text(it)
            if bo:
                L.append(f"  - blocked on: {bo}")
        L.append("")
    other = {k: v for k, v in ck["counts"].items() if k not in _RUNNING_STATES}
    if other:
        L += ["_Everything else, by count only (nothing here needs your eyes "
              "this morning): " + ", ".join(f"`{k}` {v}" for k, v in other.items())
              + "._", ""]
    return L


def _section5(b: dict) -> list[str]:
    unrouted = b["pipeline"]["stats"]["unrouted"]
    L = ["---", "", "## §5 — SPEND", "",
         "**Cost/budget figures: not measured — A1 (cost meter) is not "
         "built.** This is a declared gap, never a fabricated `$0`.", "",
         f"**Unrouted pipeline items: {unrouted}.** From "
         "`scripts/ops/pipeline.py::unrouted_count()` — due, and nobody has "
         "taken it. This is the number that must never quietly grow: a rising "
         "count on this page is the structural difference between this "
         "pipeline and `DUE.md`, whose due-list nobody read.", ""]
    if not b["pipeline"]["healthy"]:
        L += ["> ⚠️ The pipeline store has unreadable records (see §0) — "
              "the unrouted count above is a **floor**, not a total.", ""]
    return L


def _hdr(b: dict) -> list[str]:
    return [f"# DAILY BRIEF — {b['forDate']}", "",
            f"_Generated `{b['generatedAt']}` by `scripts/ops/render_daily_brief.py`, "
            "served live by `GET /api/bot/work/brief`._", "",
            "_**GENERATED — do not hand-edit.** Six fixed sections: what came "
            "due · taken under mandate · decisions for you · what moved · "
            "what is running · spend._", ""]


def _footer(b: dict) -> list[str]:
    return ["---", "", "## INPUTS", "",
            "| input | state | path |", "|---|---|---|",
            f"| `pipeline` | `{'read' if b['pipeline']['healthy'] else 'partial'}` "
            f"| `{_PIPELINE_STORE}` |",
            f"| `checklist` | `{b['checklistState']}` | `{_CHECKLIST}` |",
            f"| `mandates` | `{b['mandatesState']}` | `{_MANDATES}` |", "",
            "⚠️ **`coverageComplete` is `false`.** This reads the pipeline "
            "store, the checklist and `config/mandates.yaml`. It does not "
            "read `src/`, either VM, or the three review backlogs. A reader "
            "treating it as the whole of system state has misread it.", ""]


def render(b: dict) -> str:
    parts = (_hdr(b) + _section0(b) + _section1(b) + _section2(b)
             + _section3(b) + _section4(b) + _section5(b) + _footer(b))
    return "\n".join(parts).rstrip() + "\n"


# ── self-test: planted controls in both directions ─────────────────────────

def _self_test() -> int:
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(f"  self-test ({name}): {'PASS' if cond else 'FAIL'}")
        ok = ok and cond

    today = date(2026, 9, 21)

    # ── §0 calls through to pipeline.py, never re-derives due-ness ────────
    res_due = pipeline.LoadResult(items={
        "X": {"id": "X", "what": "due thing", "state": "queued",
              "origin": {"kind": "audit", "ref": "#1", "rerun": "r"},
              "due_when": {"kind": "observation", "clears_when": "c"},
              "next_action": "dispatch_lane", "routed_to": None,
              "terminal_reason": None}}, records=1)
    b_due = {"pipeline": {"stats": pipeline.stats(res_due, today),
                          "section0Lines": pipeline.render_section_0(res_due, today),
                          "healthy": True},
             "checklistState": "read", "checklist": checklist_view({}),
             "mandatesState": "absent", "mandates": mandates_view(None, "absent"),
             "forDate": "2026-09-21", "generatedAt": "x", "coverageComplete": False}
    md_due = render(b_due)
    check("§0 is pipeline.render_section_0's OWN text, not re-derived",
          "**1 item(s) due. Each needs a disposition today.**" in md_due
          and "- **X**" in md_due)

    # ── an unreadable pipeline store is a declared floor, not a clean 0 ────
    res_bad = pipeline.LoadResult(unreadable=[(3, "JSONDecodeError: x")])
    b_bad = dict(b_due, pipeline={"stats": pipeline.stats(res_bad, today),
                                  "section0Lines": pipeline.render_section_0(res_bad, today),
                                  "healthy": False})
    md_bad = render(b_bad)
    check("an unreadable pipeline store is declared, not a clean 0",
          "COULD NOT BE PARSED" in md_bad and "floor" in md_bad.lower())

    # ── §1 — the three mandate states never collapse ───────────────────────
    check("no mandates.yaml -> 'no mandate mechanism exists yet'",
          "no mandate mechanism exists yet" in render(b_due).lower())
    b_unread_mandates = dict(b_due, mandatesState="unreadable",
                             mandates=mandates_view(None, "unreadable"))
    check("an unreadable mandates.yaml says 'could not look', not 'nothing fired'",
          "we could not look" in render(b_unread_mandates).lower()
          and "no mandate mechanism" not in render(b_unread_mandates).lower())
    b_empty_mandates = dict(b_due, mandatesState="read",
                            mandates=mandates_view({"mandates": []}, "read"))
    check("mandates.yaml present but empty is a reading, not a hole",
          "declares **no mandates**" in render(b_empty_mandates))
    b_fired = dict(b_due, mandatesState="read",
                   mandates=mandates_view(
                       {"mandates": [{"id": "MD-DEMOTE-S1-OFF", "grants": "derisk_only"}]},
                       "read"))
    check("a declared mandate is listed by id",
          "MD-DEMOTE-S1-OFF" in render(b_fired))

    # ── §2 — operator-owned, unresolved rows; NO CAP ───────────────────────
    ck2 = checklist_view({"items": [
        {"id": "A6", "state": "landed_unproven", "owner": "operator",
         "title": "pull two legs"},
        {"id": "A3", "state": "in_flight", "owner": "build lane",
         "title": "this module"},
        {"id": "R6", "state": "queued", "owner": "operator",
         "title": "trade at all?"},
        {"id": "Z", "state": "done", "owner": "operator",
         "title": "already settled"},
    ]})
    rows2 = decisions_for_you(ck2)
    check("§2 includes unresolved operator-owned rows, excludes done and non-operator",
          {r["id"] for r in rows2} == {"A6", "R6"})

    # ── §3/§4 — landed_unproven is NOT done, and stays out of §4 ───────────
    b3 = dict(b_due, checklist=ck2)
    md3 = render(b3)
    s3 = md3.split("## §3")[1].split("## §4")[0]
    check("landed_unproven renders under a NOT-done heading in §3",
          "**NOT done**" in s3 and s3.index("- **A6**") > s3.index("LANDED, EFFECT UNOBSERVED"))
    s4 = md3.split("## §4")[1].split("## §5")[0]
    check("§4 lists only in_flight/blocked — landed_unproven is not 'running'",
          "- **A3**" in s4 and "- **A6**" not in s4)

    # ── §5 — the unrouted count is pipeline's, and spend is a declared gap ─
    res5 = pipeline.LoadResult(items={
        "A": {"id": "A", "state": "queued", "what": "w",
              "origin": {"kind": "audit", "ref": "r", "rerun": "r"},
              "due_when": {"kind": "observation", "clears_when": "c"},
              "next_action": "dispatch_lane", "routed_to": None,
              "terminal_reason": None},
        "B": {"id": "B", "state": "routed", "routed_to": "A7", "what": "w",
              "origin": {"kind": "audit", "ref": "r", "rerun": "r"},
              "due_when": {"kind": "observation", "clears_when": "c"},
              "next_action": "dispatch_lane", "terminal_reason": None},
    })
    b5 = dict(b_due, pipeline={"stats": pipeline.stats(res5, today),
                               "section0Lines": pipeline.render_section_0(res5, today),
                               "healthy": True})
    md5 = render(b5)
    check("§5's unrouted count is pipeline.unrouted_count(), not re-derived",
          f"Unrouted pipeline items: {pipeline.unrouted_count(res5.items.values(), today)}."
          in md5)
    check("§5 declares spend as unmeasured, never a fabricated $0",
          "not measured — A1 (cost meter) is not built" in md5)

    # ── absent/unreadable checklist is a declared hole in every section that
    #    depends on it, never silence and never an empty-but-clean render ──
    b_ck_absent = dict(b_due, checklistState="absent")
    md_absent = render(b_ck_absent)
    check("an absent checklist is declared in §2/§3/§4, not silently empty",
          md_absent.count("we looked; it is not there") >= 3)
    b_ck_bad = dict(b_due, checklistState="unreadable")
    md_ckbad = render(b_ck_bad)
    check("an unreadable checklist says 'could not look' in §2/§3/§4",
          md_ckbad.count("we could not look") >= 3)

    # ── coverage is always declared incomplete ──────────────────────────────
    check("coverage is declared incomplete", "`coverageComplete` is `false`" in md_due)

    # ── the read_json/read_yaml tri-state, on real files ────────────────────
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        r = Path(td)
        (r / "d").mkdir()
        (r / "d" / "bad.json").write_text("{oops", encoding="utf-8")
        (r / "d" / "bad.yaml").write_text("a: [oops", encoding="utf-8")
        (r / "d" / "ok.yaml").write_text("mandates: []\n", encoding="utf-8")
        check("read_json distinguishes absent from unreadable from read",
              read_json(Path("d/missing.json"), r) == (None, "absent")
              and read_json(Path("d/bad.json"), r)[1] == "unreadable")
        check("read_yaml distinguishes absent from unreadable from read",
              read_yaml(Path("d/missing.yaml"), r) == (None, "absent")
              and read_yaml(Path("d/bad.yaml"), r)[1] == "unreadable"
              and read_yaml(Path("d/ok.yaml"), r) == ({"mandates": []}, "read"))

    print(f"daily-brief self-test: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def _check() -> int:
    """Render over the LIVE files and assert the invariant sentences. Grades
    the CODE, not the data: an unreadable/absent input is reported loudly and
    PASSES — failing here on live data would red every unrelated PR, the
    lesson `daily-brief-guard`'s predecessor already learned."""
    try:
        b = build()
        md = render(b)
    except Exception as exc:  # noqa: BLE001 — the failure IS the finding
        print(f"daily-brief: FAIL — the renderer raised on the live inputs: "
              f"{type(exc).__name__}: {exc}")
        return 1

    required = [
        ("all six sections are present", all(
            f"## §{n} —" in md for n in range(6))),
        ("landed_unproven is not flattened into done", "is\nNOT `done`" in md
         or "is\nNOT" in md or "NOT `done`" in md),
        ("§0 is pipeline.py's own text", "WHAT CAME DUE" in md),
        ("§5 carries the unrouted count", "Unrouted pipeline items:" in md),
        ("§5 declares spend unmeasured", "A1 (cost meter) is not built" in md),
        ("inputs are enumerated with a state", "## INPUTS" in md),
        ("coverage is declared incomplete", "`coverageComplete` is `false`" in md),
        ("the artifact says it is generated", "do not hand-edit" in md),
    ]
    missing = [why for why, ok in required if not ok]
    if missing:
        for why in missing:
            print(f"  ::FINDING:: the brief no longer states: {why}")
        print(f"daily-brief: FAIL — {len(missing)} invariant(s) missing")
        return 1

    for name, state in (("pipeline", "read" if b["pipeline"]["healthy"] else "partial"),
                        ("checklist", b["checklistState"]),
                        ("mandates", b["mandatesState"])):
        if state not in ("read",):
            print(f"  ::NOTICE:: input `{name}` reads `{state}` — the brief "
                  f"declares it, and this check PASSES rather than reding "
                  f"unrelated PRs")
    print(f"daily-brief: OK — renders all six sections; unrouted="
          f"{b['pipeline']['stats']['unrouted']}, checklist="
          f"{b['checklistState']}, mandates={b['mandatesState']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="Emit the envelope.")
    ap.add_argument("--write", action="store_true",
                    help=f"Write to {BRIEF_DIR.relative_to(REPO_ROOT)}/<date>.md")
    ap.add_argument("--check", action="store_true",
                    help="Guard mode: render over the live inputs and assert "
                         "the invariant sentences. Grades the CODE, not the "
                         "data — an unreadable/absent input is reported "
                         "loudly and PASSES.")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()
    if a.check:
        return _check()

    b = build()
    text = json.dumps(b, indent=2, default=str) if a.json else render(b)

    if a.write:
        BRIEF_DIR.mkdir(parents=True, exist_ok=True)
        out = BRIEF_DIR / f"{b['forDate']}.md"
        out.write_text(render(b), encoding="utf-8")
        print(f"daily-brief: wrote {out.relative_to(REPO_ROOT)} "
              f"(unrouted={b['pipeline']['stats']['unrouted']}, "
              f"checklist={b['checklistState']}, mandates={b['mandatesState']})")
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
