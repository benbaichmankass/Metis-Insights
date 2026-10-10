#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::blocker-watch-guard (--self-test); read by
#   scripts/ops/render_daily_brief.py, scripts/ops/attention_watch.py and
#   scripts/ops/manager_preflight.py.
"""BLOCKER-WATCH — does a row that SAYS it is blocked, or running, actually hold?

WHY (operator, 2026-10-10, intent): the manager needs a mechanism that checks
blocked items the way soaks alert when done or needing attention — "if most of
the things here aren't actually blocked, then that's a problem that they were
still marked as blocked and weren't put into a work queue." MEASURED the same
day: 4 of 7 blocked rows had resolved or retired blockers, and one row claimed
three research units 'running' that had ``last_dispatched_at`` null for two
days. ``manager_preflight.check_blocked_claims`` only checks that a basis is
STATED; nothing checked whether the blocker still holds.

FOUR FINDINGS (every one names the row/unit, the edge and the evidence):

  STALE_BLOCK       state=blocked, and every edge resolves to a finished thing
                    (work_item row done/dropped; pipeline item done/killed;
                    research unit done/retired/graded/has results) — or there is
                    NO resolvable edge at all (empty, malformed, unknown ref).
  NOT_RUNNING       a research unit with status ``queued`` whose last_dispatched_at
                    is null or older than 48 h, plus any row blocked on such a
                    unit (its blocker claims work that is not happening). A unit
                    whose run.workflow cannot be auto-dispatched is tagged
                    NEEDS_LANE: nothing will ever fire it, a lane must.
  OPERATOR_WAIT     blocked on an ``operator_decision`` edge for > 7 days
                    (by the row's updated_at): re-ask or re-scope.
  UNPROVEN_OVERDUE  landed_unproven whose observation.due_by has passed. Rows
                    with no observation at all are COUNTED separately, never
                    listed as findings.

READ PATHS (guarded): the checklist ONLY through ``src.runtime.checklist_store``
(checklist-readers-guard), the pipeline ONLY through ``scripts/ops/pipeline.py``,
research units from ``research/queue/*.yaml`` and ``research/results/``.

A QUIET REPORT MUST SHOW IT COULD HAVE FOUND SOMETHING, so the report carries its
denominators (rows scanned per state, units scanned, pipeline items, units with
a result store) and a source state per input — ``read`` / ``unreadable``. An
unreadable input is "we did not look", never "nothing found".

Usage::

    python3 scripts/ops/blocker_watch.py            # human report
    python3 scripts/ops/blocker_watch.py --json     # machine report
    python3 scripts/ops/blocker_watch.py --self-test
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (REPO_ROOT, REPO_ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.ops import pipeline  # noqa: E402
from src.runtime import checklist_store  # noqa: E402

QUEUE_DIR = REPO_ROOT / "research" / "queue"
RESULTS_DIR = REPO_ROOT / "research" / "results"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# Bounds are constants, so moving one is a reviewed diff, not a tuning knob.
DISPATCH_MAX_HOURS = 48
OPERATOR_WAIT_DAYS = 7

STALE_BLOCK = "STALE_BLOCK"
NOT_RUNNING = "NOT_RUNNING"
OPERATOR_WAIT = "OPERATOR_WAIT"
UNPROVEN_OVERDUE = "UNPROVEN_OVERDUE"
KINDS = (STALE_BLOCK, NOT_RUNNING, OPERATOR_WAIT, UNPROVEN_OVERDUE)

FINISHED, LIVE, UNVERIFIABLE, MALFORMED = "finished", "live", "unverifiable", "malformed"

_WORK_KINDS = {"work_item", "object", "item", "row", "checklist", "step", "intent"}
_PIPE_KINDS = {"pipeline", "pipeline_item", "pipeline-item", "backlog_row"}
_RESEARCH_KINDS = {"research", "research_unit", "research-unit", "queue", "rq"}
_OPERATOR_KINDS = {"operator_decision", "operator"}
_ROW_DONE = ("done", "dropped")
_UNIT_DONE = ("done", "retired")


# ── small helpers ────────────────────────────────────────────────────────────
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(v: Any) -> datetime | None:
    """ISO-ish timestamp -> aware UTC, or None. Accepts ``...Z``, ``...00Z``
    without seconds (``2026-10-23T00:00Z``) and a bare date."""
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.strip().replace("Z", "+00:00")
    for cand in (s, s + ":00" if "T" in s and s.count(":") == 1 else s):
        try:
            d = datetime.fromisoformat(cand)
        except ValueError:
            continue
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return None


def _short(s: Any, n: int = 140) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _ago(d: timedelta) -> str:
    h = d.total_seconds() / 3600
    return f"{h:.0f}h" if h < 48 else f"{h / 24:.0f}d"


def _norm_edges(value: Any) -> list[dict]:
    """``blocked_on`` appears as list-of-dict, bare dict, bare string, list of
    strings, and ints (measured 2026-10-10). Normalise rather than assume."""
    if isinstance(value, dict):
        return [value]
    if isinstance(value, bool):
        return []
    if isinstance(value, (str, int)):
        s = str(value).strip()
        return [{"kind": None, "ref": s, "_shape": "bare"}] if s else []
    if isinstance(value, list):
        out: list[dict] = []
        for e in value:
            out.extend(_norm_edges(e))
        return out
    return []


# ── inputs ───────────────────────────────────────────────────────────────────
def load_units(queue_dir: Path = QUEUE_DIR, results_dir: Path = RESULTS_DIR) -> tuple[str, dict[str, dict], int]:
    """-> (read_state, {id: unit}, unparseable_count). ``results_dir`` is probed
    here so ``_has_results`` is a dict lookup."""
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return "unreadable", {}, 0
    if not queue_dir.is_dir():
        return "unreadable", {}, 0
    units: dict[str, dict] = {}
    bad = 0
    for f in sorted(queue_dir.glob("RQ-*.yaml")):
        try:
            d = yaml.safe_load(f.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            bad += 1
            continue
        if not isinstance(d, dict):
            bad += 1
            continue
        uid = str(d.get("id") or f.stem)
        d["_has_results"] = _dir_nonempty(results_dir / uid)
        units[uid] = d
    return "read", units, bad


def _dir_nonempty(p: Path) -> bool:
    try:
        return p.is_dir() and any(p.iterdir())
    except OSError:
        return False


def _dispatchable(workflow: Any, workflows_dir: Path = WORKFLOWS_DIR) -> bool:
    """A unit fires on its own only if run.workflow names a real workflow file."""
    w = str(workflow or "").strip()
    return bool(w.endswith(".yml") and " " not in w and (workflows_dir / w).is_file())


def unit_finished(u: dict) -> tuple[bool, str]:
    st = str(u.get("status") or "")
    if st in _UNIT_DONE:
        return True, f"unit status={st}"
    if u.get("_has_results"):
        return True, "results present under research/results/"
    for k in ("grading", "verdict", "result"):
        if u.get(k):
            return True, f"unit carries {k}"
    return False, f"unit status={st or '?'}, no grading, no results"


def _cadence_not_elapsed(u: dict, now: datetime) -> bool:
    """True when the DISPATCHER itself says nothing is owed yet: a ``cadence: once``
    unit that already ran, or a recurring unit whose cadence has not elapsed.

    The 48 h bound alone read every healthy monthly unit as stalled the moment its
    stamp aged past two days (RQ-DISPATCH-STALL, 2026-10-10: 9 of the 13 units a
    scan flagged were exactly that or a once-unit awaiting grading). The cadence
    verdict is the dispatcher's own ``_is_due`` -- one definition of "owed", not
    a second copy here. ONLY the cadence/idempotence reasons exempt a unit; a unit
    that is not due because it is MISCONFIGURED or has an unmet precondition still
    surfaces. If the dispatcher cannot be imported the answer is False (we did not
    look; the old bound applies)."""
    wf = str((u.get("run") or {}).get("workflow") or "").strip()
    if not wf.endswith((".yml", ".yaml")) or any(ch.isspace() for ch in wf):
        return False   # session-bound: a stamp there is a hand note, not a dispatch the cadence can vouch for
    try:
        from scripts.research import dispatch_queue as dq  # noqa: PLC0415
        due, why = dq._is_due(u, now)
    except Exception:  # noqa: BLE001 -- "could not look" must fall back to the bound, not hide the unit
        return False
    return (not due) and (why.startswith("cadence=once") or "not yet elapsed" in why)


def unit_not_running(u: dict, now: datetime) -> str | None:
    """Evidence string when the unit is queued but not being dispatched."""
    if str(u.get("status") or "") != "queued":
        return None
    if unit_finished(u)[0]:
        return None
    if _cadence_not_elapsed(u, now):
        return None
    ld = u.get("last_dispatched_at")
    t = _parse_ts(ld)
    if t is None:
        return "queued, last_dispatched_at " + ("null" if not ld else f"unparseable ({ld!r})")
    hrs = (now - t).total_seconds() / 3600
    if hrs > DISPATCH_MAX_HOURS:
        return f"queued, last dispatched {hrs:.0f}h ago (bound {DISPATCH_MAX_HOURS}h)"
    return None


# ── edge resolution ──────────────────────────────────────────────────────────
def classify_edge(edge: dict, rows: dict[str, dict], pipe_items: dict[str, dict],
                  units: dict[str, dict], now: datetime) -> dict:
    """-> {state, what, evidence}. state ∈ finished|live|unverifiable|malformed.

    ``live`` = the blocker is real and still open; ``unverifiable`` = an edge
    this tool cannot machine-check (external_event, capability, …) and so never
    calls stale."""
    kind = str(edge.get("kind") or "").strip().lower() or None
    ref = str(edge.get("ref") or "").strip()
    out = {"kind": kind, "ref": ref, "state": MALFORMED, "evidence": ""}
    if not ref:
        out["evidence"] = "edge has no ref"
        return out
    if kind in _OPERATOR_KINDS:
        out.update(state=LIVE, evidence="waiting on an operator decision", operator=True)
        return out
    # Resolve by declared kind first, then (bare/unknown kind) by the ref's shape.
    target = None
    if kind in _WORK_KINDS:
        target = "row"
    elif kind in _PIPE_KINDS:
        target = "pipeline"
    elif kind in _RESEARCH_KINDS:
        target = "unit"
    elif kind is None or kind not in {"external_event", "data_accrual", "capability", "pr", "artifact", "observation", "review"}:
        if ref in rows:
            target = "row"
        elif ref in pipe_items or ref.startswith("PI-"):
            target = "pipeline"
        elif ref in units or ref.startswith("RQ-"):
            target = "unit"
    if target is None:
        if kind is None or kind not in {"external_event", "data_accrual", "capability", "pr", "artifact", "observation", "review"}:
            out["evidence"] = f"ref {_short(ref, 60)!r} matches no checklist row, pipeline item or research unit"
            return out
        out.update(state=UNVERIFIABLE, evidence=f"kind {kind!r} is not machine-checkable")
        return out
    if target == "row":
        r = rows.get(ref)
        if r is None:
            out["evidence"] = f"no checklist row {ref!r}"
            return out
        st = r.get("state")
        out["state"] = FINISHED if st in _ROW_DONE else LIVE
        out["evidence"] = f"row {ref} state={st}"
        if st == "blocked":
            out["evidence"] += " (itself blocked)"
        return out
    if target == "pipeline":
        it = pipe_items.get(ref)
        if it is None:
            out["evidence"] = f"no pipeline item {ref!r}"
            return out
        st = it.get("state")
        out["state"] = FINISHED if st in pipeline.TERMINAL_STATES else LIVE
        out["evidence"] = f"pipeline {ref} state={st}"
        return out
    u = units.get(ref)
    if u is None:
        out["evidence"] = f"no research unit {ref!r}"
        return out
    fin, why = unit_finished(u)
    nr = unit_not_running(u, now)
    out["state"] = FINISHED if fin else LIVE
    out["evidence"] = f"unit {ref}: {nr or why}"
    if nr:
        out["not_running"] = True
    return out


def _finding(kind: str, ident: str, edge: str, evidence: str, **extra) -> dict:
    return {"kind": kind, "id": ident, "edge": edge, "evidence": evidence,
            "key": f"{kind}:{ident}" + (f":{edge}" if kind in (STALE_BLOCK, OPERATOR_WAIT) or extra.get("blocked_row") else ""),
            **extra}


# ── the scan (pure) ──────────────────────────────────────────────────────────
def scan(rows: list[dict], pipe_items: dict[str, dict], units: dict[str, dict],
         now: datetime, workflows_dir: Path = WORKFLOWS_DIR) -> dict:
    by_id = {r["id"]: r for r in rows if isinstance(r, dict) and r.get("id")}
    findings: list[dict] = []
    by_state: dict[str, int] = {}
    for r in by_id.values():
        by_state[str(r.get("state"))] = by_state.get(str(r.get("state")), 0) + 1

    # (b) units queued but not being dispatched.
    not_running: dict[str, str] = {}
    for uid in sorted(units):
        ev = unit_not_running(units[uid], now)
        if ev is None:
            continue
        not_running[uid] = ev
        wf = (units[uid].get("run") or {}).get("workflow")
        needs_lane = not _dispatchable(wf, workflows_dir)
        findings.append(_finding(NOT_RUNNING, uid, "run.workflow",
                                 ev + (f"; run.workflow {_short(wf, 50)!r} cannot be auto-dispatched -> NEEDS_LANE"
                                       if needs_lane else ""),
                                 needs_lane=needs_lane, title=_short(units[uid].get("title"), 90)))

    # (a)(c) blocked rows.
    for r in sorted((x for x in by_id.values() if x.get("state") == "blocked"), key=lambda x: x["id"]):
        edges = _norm_edges(r.get("blocked_on"))
        cls = [classify_edge(e, by_id, pipe_items, units, now) for e in edges]
        live = [c for c in cls if c["state"] == LIVE]
        unver = [c for c in cls if c["state"] == UNVERIFIABLE]
        fin = [c for c in cls if c["state"] == FINISHED]
        # a row blocked on a unit that is not running: the blocker claims work
        # that is not happening (the ML-RESEARCH-W41 shape).
        for c in live:
            if c.get("not_running"):
                findings.append(_finding(NOT_RUNNING, r["id"], c["ref"],
                                         f"blocked on {c['evidence']} — the blocker claims work that is not happening",
                                         blocked_row=True))
        if not live and not unver:
            why = ("finished: " + "; ".join(c["evidence"] for c in fin)) if fin else \
                  ("no resolvable edge: " + ("; ".join(c["evidence"] for c in cls) if cls else "blocked_on is empty"))
            bad = [c["evidence"] for c in cls if c["state"] == MALFORMED]
            if fin and bad:
                why += " | unresolvable: " + "; ".join(bad)
            findings.append(_finding(STALE_BLOCK, r["id"],
                                     ",".join(c["ref"] for c in cls) or "(none)", why,
                                     title=_short(r.get("title"), 90)))
        op = [c for c in live if c.get("operator")]
        t = _parse_ts(r.get("updated_at"))
        if op and t is not None and now - t > timedelta(days=OPERATOR_WAIT_DAYS):
            findings.append(_finding(OPERATOR_WAIT, r["id"], _short(op[0]["ref"], 60),
                                     f"waiting on the operator {(now - t).days}d (updated_at {r.get('updated_at')}; "
                                     f"bound {OPERATOR_WAIT_DAYS}d) — re-ask or re-scope",
                                     title=_short(r.get("title"), 90)))

    # (d) landed_unproven past due.
    no_obs = 0
    lu = [r for r in by_id.values() if r.get("state") == "landed_unproven"]
    for r in sorted(lu, key=lambda x: x["id"]):
        obs = r.get("observation")
        if not isinstance(obs, dict) or not obs:
            no_obs += 1
            continue
        due = _parse_ts(obs.get("due_by"))
        if due is None:
            no_obs += 1  # an observation with no readable due date can never come due
            continue
        if due < now:
            findings.append(_finding(UNPROVEN_OVERDUE, r["id"], "observation.due_by",
                                     f"due_by {obs.get('due_by')} passed {_ago(now - due)} ago: "
                                     f"{_short(obs.get('what'), 100)}",
                                     title=_short(r.get("title"), 90)))

    findings.sort(key=lambda f: (KINDS.index(f["kind"]), f["id"]))
    counts = {k: sum(1 for f in findings if f["kind"] == k) for k in KINDS}
    return {
        "now": now.isoformat(),
        "findings": findings,
        "counts": counts,
        "denominators": {
            "rows_scanned": len(by_id),
            "rows_by_state": dict(sorted(by_state.items())),
            "blocked_rows": by_state.get("blocked", 0),
            "landed_unproven_rows": len(lu),
            "landed_unproven_no_observation": no_obs,
            "research_units_scanned": len(units),
            "research_units_queued": sum(1 for u in units.values() if u.get("status") == "queued"),
            "pipeline_items": len(pipe_items),
        },
    }


def build(now: datetime | None = None, *, repo: Path = REPO_ROOT) -> dict:
    """Read the three inputs and scan. Any unreadable input is reported as such
    in ``sources`` and ``readable`` is False — never silently an empty list."""
    now = now or _now()
    sources: dict[str, str] = {}
    rows: list[dict] = []
    try:
        rows = list(checklist_store.load(repo)["items"])
        sources["checklist"] = "read"
    except Exception as e:  # noqa: BLE001 — unreadable is a state, not a crash
        sources["checklist"] = f"unreadable: {_short(e, 80)}"
    pipe_items: dict[str, dict] = {}
    try:
        res = pipeline.load(repo / pipeline.STORE)
        pipe_items = dict(res.items)
        sources["pipeline"] = "read" if res.healthy else f"read ({len(res.unreadable)} record(s) unreadable)"
    except Exception as e:  # noqa: BLE001
        sources["pipeline"] = f"unreadable: {_short(e, 80)}"
    q_state, units, bad = load_units(repo / "research" / "queue", repo / "research" / "results")
    sources["research_queue"] = q_state if not bad else f"{q_state} ({bad} unit file(s) unparseable)"
    report = scan(rows, pipe_items, units, now, repo / ".github" / "workflows")
    report["sources"] = sources
    report["readable"] = all(v.startswith("read") for v in sources.values())
    return report


# ── rendering ────────────────────────────────────────────────────────────────
def render_lines(rep: dict, cap: int = 12) -> list[str]:
    """Markdown lines for the brief. Capped, with counts for what is left out."""
    d, c = rep["denominators"], rep["counts"]
    L = ["### 🧱 BLOCKER-WATCH — do the blocked / 'running' claims still hold?", ""]
    if not rep["readable"]:
        L += ["⚠️ COULD NOT READ: " + "; ".join(f"{k} {v}" for k, v in rep["sources"].items()
                                              if not v.startswith("read")) +
              " — a quiet list below is NOT 'nothing found'.", ""]
    L.append("Scanned: %d rows (%d blocked, %d landed_unproven of which %d carry no observation) · %d research units "
             "(%d queued) · %d pipeline items." % (
                 d["rows_scanned"], d["blocked_rows"], d["landed_unproven_rows"],
                 d["landed_unproven_no_observation"], d["research_units_scanned"],
                 d["research_units_queued"], d["pipeline_items"]))
    L.append("Findings: " + " · ".join(f"{k} {c[k]}" for k in KINDS) + f" (total {len(rep['findings'])}).")
    L.append("")
    shown = rep["findings"][:cap]
    for f in shown:
        lane = " [NEEDS_LANE]" if f.get("needs_lane") else ""
        L.append(f"- **{f['kind']}** `{f['id']}`{lane} — {f['evidence']}")
    if len(rep["findings"]) > cap:
        L.append(f"- …and {len(rep['findings']) - cap} more (`python3 scripts/ops/blocker_watch.py`).")
    if not rep["findings"]:
        L.append("- none (a clean result over the denominators above).")
    L += ["", "Each finding is unblocked, re-queued, dispatched to a lane, or dropped with a stated reason in the "
              "same review turn — a stale block is never left standing.", ""]
    return L


def render_text(rep: dict) -> str:
    return "\n".join(render_lines(rep, cap=10_000))


# ── self-test ────────────────────────────────────────────────────────────────
def _self_test() -> int:
    import tempfile  # noqa: PLC0415
    fails: list[str] = []

    def check(name: str, got: Any, want: Any) -> None:
        if got != want:
            fails.append(f"{name}: got {got!r}, want {want!r}")

    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    old = "2026-10-01T00:00:00Z"
    with tempfile.TemporaryDirectory() as td:
        wf = Path(td)
        (wf / "real.yml").write_text("name: x\n")
        rows = [
            {"id": "A", "state": "done"},
            {"id": "DROPPED", "state": "dropped"},
            {"id": "OPEN", "state": "in_flight"},
            {"id": "S1", "state": "blocked", "updated_at": old, "blocked_on": [{"kind": "work_item", "ref": "A"}]},
            {"id": "S2", "state": "blocked", "updated_at": old, "blocked_on": ["A", "DROPPED"]},
            {"id": "L1", "state": "blocked", "updated_at": old, "blocked_on": [{"kind": "work_item", "ref": "OPEN"}]},
            {"id": "M1", "state": "blocked", "updated_at": old, "blocked_on": []},
            {"id": "M2", "state": "blocked", "updated_at": old, "blocked_on": [7]},
            {"id": "M3", "state": "blocked", "updated_at": old, "blocked_on": [{"kind": "work_item", "ref": "NOPE"}]},
            {"id": "MIX", "state": "blocked", "updated_at": old,
             "blocked_on": [{"kind": "work_item", "ref": "A"}, {"kind": "work_item", "ref": "OPEN"}]},
            {"id": "EXT", "state": "blocked", "updated_at": old,
             "blocked_on": [{"kind": "external_event", "ref": "x"}, {"kind": "work_item", "ref": "A"}]},
            {"id": "OP_OLD", "state": "blocked", "updated_at": old, "blocked_on": [{"kind": "operator_decision", "ref": "go?"}]},
            {"id": "OP_NEW", "state": "blocked", "updated_at": "2026-10-09T00:00:00Z",
             "blocked_on": [{"kind": "operator_decision", "ref": "go?"}]},
            {"id": "PIPE_DONE", "state": "blocked", "updated_at": old, "blocked_on": ["PI-1"]},
            {"id": "PIPE_OPEN", "state": "blocked", "updated_at": old, "blocked_on": ["PI-2"]},
            {"id": "ON_UNIT", "state": "blocked", "updated_at": old, "blocked_on": [{"kind": "research_unit", "ref": "RQ-Q"}]},
            {"id": "ON_GRADED", "state": "blocked", "updated_at": old, "blocked_on": ["RQ-G"]},
            {"id": "LU_OVER", "state": "landed_unproven", "observation": {"what": "w", "due_by": "2026-10-09T00:00Z"}},
            {"id": "LU_FUT", "state": "landed_unproven", "observation": {"what": "w", "due_by": "2026-10-23T00:00Z"}},
            {"id": "LU_NONE", "state": "landed_unproven"},
        ]
        pipe = {"PI-1": {"id": "PI-1", "state": "killed"}, "PI-2": {"id": "PI-2", "state": "queued"}}
        units = {
            "RQ-Q": {"id": "RQ-Q", "status": "queued", "last_dispatched_at": None, "run": {"workflow": "real.yml"}},
            "RQ-OLD": {"id": "RQ-OLD", "status": "queued", "last_dispatched_at": "2026-10-01T00:00:00+00:00",
                       "run": {"workflow": "trainer-vm-diag (issue label x)"}},
            "RQ-FRESH": {"id": "RQ-FRESH", "status": "queued", "last_dispatched_at": "2026-10-10T06:00:00+00:00",
                         "run": {"workflow": "real.yml"}},
            "RQ-G": {"id": "RQ-G", "status": "queued", "last_dispatched_at": None, "grading": {"v": 1}},
            "RQ-RES": {"id": "RQ-RES", "status": "queued", "last_dispatched_at": None, "_has_results": True},
            "RQ-DONE": {"id": "RQ-DONE", "status": "done"},
            "RQ-MONTHLY": {"id": "RQ-MONTHLY", "status": "queued", "cadence": "monthly",
                           "last_dispatched_at": "2026-10-01T00:00:00+00:00", "run": {"workflow": "real.yml"}},
            "RQ-ONCE-RAN": {"id": "RQ-ONCE-RAN", "status": "queued", "cadence": "once",
                            "last_dispatched_at": "2026-10-01T00:00:00+00:00", "run": {"workflow": "real.yml"}},
            "RQ-MONTHLY-ELAPSED": {"id": "RQ-MONTHLY-ELAPSED", "status": "queued", "cadence": "monthly",
                                   "last_dispatched_at": "2026-08-01T00:00:00+00:00", "run": {"workflow": "real.yml"}},
        }
        rep = scan(rows, pipe, units, now, wf)
        keys = {f["key"] for f in rep["findings"]}
        has = lambda k: any(f["key"].startswith(k) for f in rep["findings"])  # noqa: E731

        check("S1 stale (work_item done)", has("STALE_BLOCK:S1:"), True)
        check("S2 stale (bare strings, done+dropped)", has("STALE_BLOCK:S2:"), True)
        check("L1 NOT stale (blocker in_flight)", has("STALE_BLOCK:L1:"), False)
        check("empty blocked_on is stale", has("STALE_BLOCK:M1:"), True)
        check("int edge is stale (unresolvable)", has("STALE_BLOCK:M2:"), True)
        check("unknown work_item ref is stale", has("STALE_BLOCK:M3:"), True)
        check("MIX (one live edge) NOT stale", has("STALE_BLOCK:MIX:"), False)
        check("EXT (external edge + finished) NOT stale", has("STALE_BLOCK:EXT:"), False)
        check("closed pipeline ref is stale", has("STALE_BLOCK:PIPE_DONE:"), True)
        check("open pipeline ref is NOT stale", has("STALE_BLOCK:PIPE_OPEN:"), False)
        check("row blocked on a graded unit is stale", has("STALE_BLOCK:ON_GRADED:"), True)
        check("OP_OLD operator wait", has("OPERATOR_WAIT:OP_OLD:"), True)
        check("OP_NEW not yet", has("OPERATOR_WAIT:OP_NEW:"), False)
        check("operator-waiting row is not stale", has("STALE_BLOCK:OP_OLD:"), False)
        check("row blocked on never-dispatched unit -> NOT_RUNNING", "NOT_RUNNING:ON_UNIT:RQ-Q" in keys, True)
        check("unit never dispatched", "NOT_RUNNING:RQ-Q" in keys, True)
        check("unit dispatched 9d ago", "NOT_RUNNING:RQ-OLD" in keys, True)
        check("fresh unit is fine", "NOT_RUNNING:RQ-FRESH" in keys, False)
        check("monthly unit inside its cadence is NOT 'not running'", "NOT_RUNNING:RQ-MONTHLY" in keys, False)
        check("cadence=once unit that ran is NOT 'not running'", "NOT_RUNNING:RQ-ONCE-RAN" in keys, False)
        check("monthly unit past its cadence IS 'not running'", "NOT_RUNNING:RQ-MONTHLY-ELAPSED" in keys, True)
        check("graded unit is not 'not running'", "NOT_RUNNING:RQ-G" in keys, False)
        check("unit with results is not 'not running'", "NOT_RUNNING:RQ-RES" in keys, False)
        check("done unit is not 'not running'", "NOT_RUNNING:RQ-DONE" in keys, False)
        nl = {f["id"]: f.get("needs_lane") for f in rep["findings"] if f["kind"] == NOT_RUNNING and not f.get("blocked_row")}
        check("manual workflow -> NEEDS_LANE", nl.get("RQ-OLD"), True)
        check("real workflow -> not NEEDS_LANE", nl.get("RQ-Q"), False)
        check("overdue observation", "UNPROVEN_OVERDUE:LU_OVER" in keys, True)
        check("future observation not overdue", "UNPROVEN_OVERDUE:LU_FUT" in keys, False)
        check("no-observation row counted, not a finding",
              ("UNPROVEN_OVERDUE:LU_NONE" in keys, rep["denominators"]["landed_unproven_no_observation"]), (False, 1))
        check("denominators: rows", rep["denominators"]["rows_scanned"], len(rows))
        check("denominators: units", rep["denominators"]["research_units_scanned"], len(units))
        # stable keys: a second scan yields the identical key set (dedupe depends on it)
        check("keys are stable across scans", {f["key"] for f in scan(rows, pipe, units, now, wf)["findings"]}, keys)
        # positive control: a clean world yields no findings AND still reports denominators
        clean = scan([{"id": "A", "state": "done"}], {}, {}, now, wf)
        check("clean world has no findings", clean["findings"], [])
        check("...but shows what it scanned", clean["denominators"]["rows_scanned"], 1)
        # unreadable input is not 'nothing found'
        lines = render_lines({**clean, "readable": False, "sources": {"checklist": "unreadable: x"}})
        check("unreadable source is called out", any("COULD NOT READ" in x for x in lines), True)
        check("cap states what it left out",
              any("more" in x for x in render_lines({**rep, "readable": True, "sources": {}}, cap=2)), True)
        check("_parse_ts accepts 'T00:00Z'", _parse_ts("2026-10-23T00:00Z") is not None, True)
    # live repo smoke: build() must run against the real stores and read all three.
    live = build(now)
    check("live build reads every source", live["readable"], True)
    check("live build scanned rows", live["denominators"]["rows_scanned"] > 0, True)
    if fails:
        print("blocker_watch self-test FAILED:\n  " + "\n  ".join(fails))
        return 1
    print("blocker_watch self-test OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    rep = build()
    print(json.dumps(rep, indent=2, default=str) if a.json else render_text(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
