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
(`scripts/ops/pipeline.py` — `due` / `unrouted_count`; §0 itself is now
`section0_lines` here, ranked and capped, still built on `pipeline.due`); until
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
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# REUSED, NOT RE-DERIVED — the whole point of A3 is to call A7's functions
# rather than fork the definition of "due". §0 is rendered HERE by
# `section0_lines` (ranked, capped) from `pipeline.due` / `due_bucket` /
# `unrouted_alarm`; `pipeline.render_section_0` remains for `pipeline.py --due`.
from scripts.ops import pipeline  # noqa: E402

BRIEF_DIR = REPO_ROOT / "comms" / "briefs"

_CHECKLIST = Path("docs/claude/work/MANAGER-CHECKLIST.json")
_MANDATES = Path("config/mandates.yaml")
_PIPELINE_STORE = pipeline.STORE
_SOAK_SRC = Path("scripts/ops/soak_state.py")

# ── size discipline (BRIEF-FIX, 2026-10-04) ────────────────────────────────
# MEASURED 2026-10-04 on the live tree: 738,748 B, of which §3 WHAT MOVED was
# 673,254 B (every done/landed row with its full note) and §2 was 38,299 B for
# 6 rows (full notes). A brief nobody can read is a brief nobody acts on, so
# every section is ranked-and-capped and SAYS what it left out. The cap below
# is pinned by tests/test_render_daily_brief.py on a synthetic worst case.
BRIEF_SIZE_CAP_BYTES = 16 * 1024
TOP_DUE = 15            # §0 rows shown
MOVED_PER_STATE = 5     # §3 rows shown per state
STALE_AFTER_DAYS = 3    # §4: no row/git activity this long => STALE
_LINE = 100             # one-line truncation for list rows
_NOTE = 90             # truncation for a note excerpt in §2

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



# ── small shared helpers ────────────────────────────────────────────────────

def _clip(text: Any, n: int) -> str:
    t = " ".join(str(text if text is not None else "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


_TS = re.compile(r"\[(20\d\d-\d\d-\d\d)")


def _latest_note_date(it: dict, today: date | None = None) -> date | None:
    """Newest `[YYYY-MM-DD…` update stamp in a row's `note` (sessions stamp
    their updates `[2026-10-01T16:45Z …]`). Only the bracketed form counts —
    bare dates in prose are deadlines, and a future one must not read as
    activity (MEASURED: PACE-W40's note named 2026-10-07 while today is 10-04).
    Stamps after today are ignored. None when the row carries no stamp."""
    best: date | None = None
    for m in _TS.finditer(str(it.get("note") or "")):
        try:
            d = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if today is not None and d > today:
            continue
        if best is None or d > best:
            best = d
    return best


# ── §0 — ranked, bucketed, denominator kept ─────────────────────────────────

def _money_path(it: dict) -> bool:
    """STRUCTURED fields only — `loud`, severity high/critical, or tier >= 2
    (Tier-2/3 = runtime/order-path/strategy by path). Deliberately NOT a
    keyword match over the prose: MEASURED 253 of 366 due rows mention
    live/order/risk/position, so a keyword rule would rank everything first."""
    sev = str(it.get("severity") or "").lower()
    try:
        tier = int(it.get("tier"))
    except (TypeError, ValueError):
        tier = 0
    return bool(it.get("loud")) or sev in ("high", "critical") or tier >= 2


def _rank_key(it: dict) -> tuple:
    far = date.max
    return (not _money_path(it), it.get("next_action") != "ask_operator",
            it.get("state") == "routed", pipeline.filed_date(it) or far,
            str(it.get("id", "")))


def ranked_items(res: Any, today: date) -> list[dict]:
    """Every due pipeline item in brief order (money-path → ask_operator →
    unrouted → oldest). Public so other renderers (the Telegram digest) rank
    identically to §0 instead of forking the order."""
    return sorted(pipeline.due(list(res.items.values()), today), key=_rank_key)


_BUCKETS = ("never-checked", "lapsed", "date-passed", "no-cadence")


def section0_lines(res: Any, today: date) -> list[str]:
    """§0 — top TOP_DUE by priority + counts by bucket and routed/unrouted.
    Reuses `pipeline.due` / `due_bucket` / `unrouted_alarm`: due-ness is never
    re-derived here. Full list: `python3 scripts/ops/pipeline.py --due --all`."""
    items = list(res.items.values())
    rows = pipeline.due(items, today)
    L = ["## §0 — WHAT CAME DUE", ""]
    if not res.healthy:
        L += [f"> ⚠️ **{len(res.unreadable)} RECORD(S) COULD NOT BE PARSED** — counts "
              "below are a floor, not a total.", ""]
    alarm = pipeline.unrouted_alarm(items, today)
    if alarm["breached"]:
        L += ["> 🚨 **ROUTING ALARM** — " + "; ".join(alarm["breached"]) + ".", ""]
    if alarm["undated"]:
        L += [f"> {alarm['undated']} unrouted item(s) carry no date in their id — age unread.", ""]
    if not rows:
        L += ["Nothing came due." if res.healthy else
              "Nothing came due **among the records that parsed**.", ""]
        return L
    n_un = sum(1 for r in rows if r.get("state") != "routed")
    bk = {b: 0 for b in _BUCKETS}
    for r in rows:
        b = pipeline.due_bucket(r, today)
        bk[b] = bk.get(b, 0) + 1
    L += [f"**{len(rows)} item(s) due. Each needs a disposition today.** "
          f"Unrouted {n_un} · routed {len(rows) - n_un} · of {len(items)} items in the store.",
          "By why due: " + " · ".join(f"{k} {v}" for k, v in bk.items()) + ".", ""]
    ordered = ranked_items(res, today)
    shown = ordered[:TOP_DUE]
    L.append(f"_Top {len(shown)} of {len(rows)}: money-path (`loud`/severity high/"
             "tier≥2) → `ask_operator` → unrouted → oldest._")
    for i in shown:
        owner = f" → `{_clip(i['routed_to'], 22)}`" if i.get("routed_to") else ""
        L.append(f"- **{i.get('id')}** [{i.get('state')}{owner}] "
                 f"{_clip(i.get('what'), _LINE)} · `{i.get('next_action')}`")
    rest = len(ordered) - len(shown)
    if rest > 0:
        L += ["", f"**{rest} more due, not shown.** Full list: "
              "`python3 scripts/ops/pipeline.py --due --all`."]
    L.append("")
    return L

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



# ── §4 evidence: git, because the VM cannot see sessions ────────────────────

_ACTIVITY_CACHE: dict[tuple, dict[str, Any]] = {}


def _head_sha(root: Path) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=5,
                              check=True).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def git_activity(root: Path, today: date, days: int = 45) -> dict[str, Any]:
    """Cached on (root, HEAD sha, today): the route calls this per request and
    the log is only as new as the last pull. An unreadable HEAD is not cached."""
    sha = _head_sha(root)
    key = (str(root), sha, today)
    if sha is not None and key in _ACTIVITY_CACHE:
        return _ACTIVITY_CACHE[key]
    out = _git_activity_uncached(root, today, days)
    if sha is not None and out["state"] != "unreadable":
        _ACTIVITY_CACHE.clear()
        _ACTIVITY_CACHE[key] = out
    return out


def _git_activity_uncached(root: Path, today: date, days: int = 45) -> dict[str, Any]:
    """Commit dates + text over the last `days`, from ONE `git log`. The VM can
    not see sessions; commits (and the PR numbers in their subjects) are what
    it can see. `coversDays` is how far back the clone actually reaches — a
    shallow clone reaching back 2 days cannot prove a row quiet for 3, and says
    so (`state: short`) instead of calling everything stale."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", f"--since={days} days ago", "-n", "6000",
             "--format=%cs%x1f%s%x1f%b%x1e"],
            capture_output=True, text=True, timeout=20, check=True).stdout
        shallow = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-shallow-repository"],
            capture_output=True, text=True, timeout=5).stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return {"state": "unreadable", "commits": [], "coversDays": None}
    commits = []
    for rec in out.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) < 2:
            continue
        try:
            commits.append((date.fromisoformat(parts[0].strip()), parts[1],
                            parts[2] if len(parts) > 2 else ""))
        except ValueError:
            continue
    if not commits:
        return {"state": "unreadable", "commits": [], "coversDays": None}
    covers = (today - min(c[0] for c in commits)).days
    short = shallow and covers < STALE_AFTER_DAYS
    return {"state": "short" if short else "read", "commits": commits,
            "coversDays": covers}


def _token(tok: str) -> "re.Pattern[str]":
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(tok) + r"(?![A-Za-z0-9])")


def row_last_evidence(it: dict, act: dict, today: date) -> tuple[date | None, str]:
    """(latest date, what it came from) for one in_flight row: the newest of
    its own note stamp and any commit naming its id/lane/PRs."""
    best: date | None = _latest_note_date(it, today)
    src = "row note" if best else ""
    pats = []
    if it.get("id"):
        pats.append((_token(str(it["id"])), "subject"))
    if it.get("lane"):
        pats.append((_token(str(it["lane"])), "any"))
    for pr in it.get("prs") or []:
        m = re.search(r"\d+", str(pr.get("number") if isinstance(pr, dict) else pr))
        if m:
            pats.append((re.compile(r"#" + m.group(0) + r"(?!\d)"), "any"))
    for d, subj, body in act.get("commits", []):
        if best is not None and d <= best:
            continue
        if any(rx.search(subj if where == "subject" else subj + "\n" + body)
               for rx, where in pats):
            best, src = d, "git commit"
    return best, src


# ── SOAKS — computed LIVE by scripts/ops/soak_state.py ──────────────────────
# INTERFACE (agreed WORK-SYSTEM <-> SOAK-WATCH): `soak_state.soak_states() ->
# list[dict]` with keys {leg, account, state, end_date, reason}; state is one of
# accruing/ready/overdue/dead/unknown. Imported live, like `pipeline` — NOT read
# from a committed file: the VM only PULLS git, so a VM-computed soak state can
# never land in one, and a session-written file is the stale-state failure this
# brief exists to end. `days_in` is computed here when the row carries `started`.
# Module absent => "soak state source absent (we looked)", never "no soaks";
# import/call failure => "could not look". Missing fields render "—", never 0.

def read_soaks(root: Path, today: date) -> dict[str, Any]:
    import importlib
    try:
        mod = importlib.import_module("scripts.ops.soak_state")
    except ModuleNotFoundError as exc:
        if exc.name == "scripts.ops.soak_state":
            return {"state": "absent", "rows": [], "asOf": None}
        return {"state": "unreadable", "rows": [], "asOf": None}
    except Exception:  # noqa: BLE001 -- declared as "could not look", not a crash of the brief
        return {"state": "unreadable", "rows": [], "asOf": None}
    try:
        rows_in = mod.soak_states()
    except Exception:  # noqa: BLE001
        return {"state": "unreadable", "rows": [], "asOf": None}
    if not isinstance(rows_in, list):
        return {"state": "unreadable", "rows": [], "asOf": None}
    rows = []
    for r in rows_in:
        if not isinstance(r, dict):
            continue
        days_in = None
        try:
            days_in = (today - date.fromisoformat(str(r.get("started"))[:10])).days
        except ValueError:
            pass
        rows.append({**r, "days_in": days_in})
    return {"state": "read", "rows": rows, "asOf": today.isoformat()}


def _soak_lines(b: dict) -> list[str]:
    sk = b.get("soaks") or {"state": "absent", "rows": []}
    L = ["### 🌱 SOAKS", ""]
    if sk["state"] == "absent":
        return L + ["_Soak state source absent (we looked: `scripts/ops/soak_state.py` "
                    "does not exist) — this is not 'no soaks'._", ""]
    if sk["state"] != "read":
        return L + [f"{_HOLE['unreadable']} — `{_SOAK_SRC}`.", ""]
    n = len(sk["rows"])
    by: dict[str, int] = {}
    for r in sk["rows"]:
        by[str(r.get("state") or "—")] = by.get(str(r.get("state") or "—"), 0) + 1
    L.append(f"_{n} soak(s): " + (", ".join(f"{k} {v}" for k, v in sorted(by.items())) or "none")
             + ". Computed live by `soak_state.py`._")
    if not n:
        return L + ["_None declared._", ""]
    dash = lambda v: "—" if v in (None, "") else v  # noqa: E731
    for r in sk["rows"]:
        L.append(f"- **{dash(r.get('id'))}** {dash(r.get('leg'))} @{dash(r.get('account'))} · "
                 f"{dash(r.get('state'))} · started {dash(r.get('started'))} · "
                 f"day {dash(r.get('days_in'))} · {_clip(dash(r.get('progress')), 40)} · "
                 f"ends {dash(r.get('end_date'))} · {_clip(dash(r.get('reason')), 50)}")
    L.append("")
    return L


# ── assembly ─────────────────────────────────────────────────────────────

def build(*, today: date | None = None, root: Path | None = None) -> dict[str, Any]:
    """Assemble the brief. Reads files; writes nothing."""
    root = root or REPO_ROOT
    today = today or datetime.now(timezone.utc).date()

    pipe_res = pipeline.read_log(root / _PIPELINE_STORE)
    pipe_stats = pipeline.stats(pipe_res, today)
    section0 = section0_lines(pipe_res, today)
    pipe_items = list(pipe_res.items.values())
    ask_operator = sorted(
        (i for i in pipeline.due(pipe_items, today) if i.get("next_action") == "ask_operator"),
        key=_rank_key)

    checklist_doc, checklist_state = read_json(_CHECKLIST, root)
    ck = checklist_view(checklist_doc) if checklist_state == "read" else checklist_view({})

    mandates_doc, mandates_state = read_yaml(_MANDATES, root)

    return {
        "schemaVersion": 1,
        "forDate": today.isoformat(),
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "researchThroughput": _research_throughput(root),
        "liveParity": _live_parity_line(root),
        "pipeline": {"stats": pipe_stats, "section0Lines": section0,
                     "healthy": pipe_res.healthy, "askOperator": ask_operator},
        "activity": git_activity(root, today),
        "soaks": read_soaks(root, today),
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
    # `section0_lines` emits its own "## §0 —" header. Due-ness comes from
    # pipeline.py (`due`, `due_bucket`), never re-derived here.
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
        L += [f"{len(mv['mandates'])} mandate(s) declared. No firing/evidence log "
              "is read — **what fired is not-yet-built, not zero firings.**", ""]
        ids = [f"`{m.get('id', '?')}`" for m in mv["mandates"] if isinstance(m, dict)]
        L += [", ".join(ids) + " (grants: `config/mandates.yaml`).", ""]
    return L


def _section2(b: dict) -> list[str]:
    ask = b["pipeline"].get("askOperator") or []
    if b["checklistState"] != "read":
        return ["---", "", "## §2 — DECISIONS FOR YOU", "",
                f"{_HOLE[b['checklistState']]} — `docs/claude/work/MANAGER-CHECKLIST.json`. "
                f"(Pipeline `ask_operator` items below are still shown: {len(ask)}.)", ""] + _ask_lines(ask)
    rows = decisions_for_you(b["checklist"])
    L = ["---", "", f"## §2 — DECISIONS FOR YOU ({len(rows)} checklist + {len(ask)} pipeline)", "",
         "_Operator-owned checklist rows, then due pipeline items with "
         "`ask_operator`. **No cap on rows** (a queue outrunning one person argues "
         "for a mandate, not a shorter list); only prose is clipped — full text "
         "`GET /api/bot/work/checklist`._", ""]
    if not rows and not ask:
        return L + ["_None._", ""]
    for it in rows:
        L.append(f"- **{it.get('id')}** [{it.get('state')}] {_clip(it.get('title') or '(untitled)', 90)}")
        if it.get("note"):
            L.append(f"  - {_clip(it['note'], 80)}")
        bo = _blocked_on_text(it)
        if bo:
            L.append(f"  - blocked on: {_clip(bo, 120)}")
    L += _ask_lines(ask)
    L.append("")
    return L


def _ask_lines(ask: list) -> list[str]:
    if not ask:
        return []
    L = ["", f"**Pipeline items awaiting you ({len(ask)}):**"]
    for i in ask:
        L.append(f"- **{i.get('id')}** {_clip(i.get('what'), 100)}")
    return L


def _recency_key(item: tuple[int, dict]) -> tuple:
    pos, it = item
    d = _latest_note_date(it)
    return (d is not None, d or date.min, pos)  # dated newest first; undated by file order


def _section3(b: dict) -> list[str]:
    L = ["---", "", "## §3 — WHAT MOVED", ""]
    if b["checklistState"] != "read":
        L += [f"{_HOLE[b['checklistState']]} — `docs/claude/work/MANAGER-CHECKLIST.json`.", ""]
        return L
    ck = b["checklist"]
    L += [f"> ⚠️ **`{_NOT_DONE_BUT_MERGED}` ({ck['mergedEffectUnobservedCount']}) is "
          f"NOT `done` ({ck['doneCount']}).** A merge is a deploy, not an "
          "observation; the two are never added together.", "",
          f"_Newest {MOVED_PER_STATE} per state; counts are full populations. "
          "All: `GET /api/bot/work/checklist`._", ""]
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
        top = sorted(enumerate(rows), key=_recency_key, reverse=True)[:MOVED_PER_STATE]
        for _, it in top:
            L.append(f"- **{it.get('id')}** — {_clip(it.get('title') or '(untitled)', 100)}")
        if len(rows) > len(top):
            L.append(f"- _…and {len(rows) - len(top)} more._")
        L.append("")
    return L


def _research_throughput(root: Path | None) -> dict | None:
    """The research queue's 24 h throughput, or None when it could not be read
    (rendered as a declared hole, never as zeros)."""
    try:
        from scripts.research.queue_throughput import throughput
        return throughput(root or REPO_ROOT)
    except Exception:  # noqa: BLE001 -- a hole, declared in §4, not a crash of the whole brief
        return None


_LIVE_PARITY = Path("comms/live_parity/latest.json")


def _live_parity_line(root: Path | None) -> str:
    """ONE line: legs checked, bars checked, divergences = N — or COULD NOT
    CHECK. Rendered from the committed artifact of the daily
    live-replay-parity workflow; an absent/unreadable artifact is a declared
    hole, never "0 divergences"."""
    doc, state = read_json(_LIVE_PARITY, root)
    try:
        from scripts.ops.live_replay_parity import summary_line
    except Exception:  # noqa: BLE001 -- a hole, declared below, not a crash of the brief
        return f"Live↔replay parity — COULD NOT BE READ (summary module failed to import; artifact {state})"
    if state != "read":
        return f"Live↔replay parity — COULD NOT CHECK ({_HOLE[state]} — `{_LIVE_PARITY}`)"
    try:
        return summary_line(doc)
    except Exception:  # noqa: BLE001
        return f"Live↔replay parity — COULD NOT BE READ (`{_LIVE_PARITY}` has an unexpected shape)"


def _research_lines(b: dict) -> list[str]:
    t = b.get("researchThroughput")
    if t is None:
        return ["### 🔬 Research queue — COULD NOT BE READ (we did not look; this is not zero)", ""]
    from scripts.research.queue_throughput import summary_line
    return [f"### 🔬 {summary_line(t)}", ""]


def _parity_lines(b: dict) -> list[str]:
    line = b.get("liveParity") or "Live↔replay parity — COULD NOT CHECK (not computed)"
    return [f"### 🔁 {line}", ""]


def _section4(b: dict) -> list[str]:
    if b["checklistState"] != "read":
        return ["---", "", "## §4 — WHAT IS RUNNING", "",
                f"{_HOLE[b['checklistState']]} — `docs/claude/work/MANAGER-CHECKLIST.json`.", ""] + _soak_lines(b) + _research_lines(b) + _parity_lines(b)
    ck = b["checklist"]
    today = date.fromisoformat(b["forDate"])
    act = b.get("activity") or {"state": "unreadable", "commits": [], "coversDays": None}
    running_total = sum(len(ck["byState"].get(s, [])) for s in _RUNNING_STATES)
    L = ["---", "", f"## §4 — WHAT IS RUNNING ({running_total})", ""]

    live, stale = [], []
    for it in ck["byState"].get("in_flight", []):
        last, src = row_last_evidence(it, act, today)
        if last is None or (today - last).days > STALE_AFTER_DAYS:
            stale.append((it, last, src))
        else:
            live.append((it, last, src))
    basis = {"read": f"git log over the last {act.get('coversDays')} d",
             "short": f"git log, but this clone reaches back only {act.get('coversDays')} d "
                      f"(< {STALE_AFTER_DAYS} d) — STALE below is **not provable** here",
             "unreadable": "git log UNREADABLE — only the row's own note stamp was available"}[act["state"]]
    L += [f"_Basis (VM cannot see sessions): running = a `[date` stamp in the row note "
          f"or a commit naming its id/lane/PR within {STALE_AFTER_DAYS} d. {basis}. "
          "PR comments and CI are not visible._", ""]

    L += [f"### 🔧 IN FLIGHT — evidence of activity ({len(live)})", ""]
    L += ([f"- **{it.get('id')}** — {_clip(it.get('title') or '(untitled)', 60)} · last "
           f"{last.isoformat()} ({src})" for it, last, src in live] or ["_None._"]) + [""]
    L += [f"### 🕸️ STALE — no live evidence ({len(stale)})", ""]
    for it, last, src in stale:
        seen = f"last {last.isoformat()} ({src}, {(today - last).days} d)" if last else "no stamp or commit found"
        L.append(f"- **{it.get('id')}** — {_clip(it.get('title') or '(untitled)', 60)} · {seen}")
    if not stale:
        L.append("_None._")
    L.append("")

    blocked = ck["byState"].get("blocked", [])
    L += [f"### ⛔ BLOCKED — waiting on a named thing ({len(blocked)})", ""]
    for it in blocked:
        L.append(f"- **{it.get('id')}** — {_clip(it.get('title') or '(untitled)', 70)}"
                 f" · on: {_clip(_blocked_on_text(it) or '— (none named)', 80)}")
    if not blocked:
        L.append("_None._")
    L.append("")
    other = {k: v for k, v in ck["counts"].items() if k not in _RUNNING_STATES}
    if other:
        L += ["_Everything else, by count only: "
              + ", ".join(f"`{k}` {v}" for k, v in other.items()) + "._", ""]
    return L + _soak_lines(b) + _research_lines(b) + _parity_lines(b)


def _section5(b: dict) -> list[str]:
    unrouted = b["pipeline"]["stats"]["unrouted"]
    L = ["---", "", "## §5 — SPEND", "",
         "**Cost/budget figures: not measured — A1 (cost meter) is not "
         "built.** This is a declared gap, never a fabricated `$0`.", "",
         f"**Unrouted pipeline items: {unrouted}.** (`pipeline.unrouted_count()` — "
         "due, nobody has taken it; must never quietly grow; alarm text in §0).", ""]
    if not b["pipeline"]["healthy"]:
        L += ["> ⚠️ The pipeline store has unreadable records (see §0) — "
              "the unrouted count above is a **floor**, not a total.", ""]
    return L


def _hdr(b: dict) -> list[str]:
    return [f"# DAILY BRIEF — {b['forDate']}", "",
            f"_Generated `{b['generatedAt']}` · `GET /api/bot/work/brief`._", "",
            "_**GENERATED — do not hand-edit.** Six sections: due · mandate · "
            "decisions · moved · running · spend. Ranked and capped to stay "
            "readable; every cap names its full list._", ""]


def _footer(b: dict) -> list[str]:
    return ["---", "", "## INPUTS", "",
            "| input | state | path |", "|---|---|---|",
            f"| `pipeline` | `{'read' if b['pipeline']['healthy'] else 'partial'}` "
            f"| `{_PIPELINE_STORE}` |",
            f"| `checklist` | `{b['checklistState']}` | `{_CHECKLIST}` |",
            f"| `mandates` | `{b['mandatesState']}` | `{_MANDATES}` |",
            f"| `soaks` | `{(b.get('soaks') or {}).get('state', 'absent')}` | `{_SOAK_SRC}` |",
            f"| `git activity` | `{(b.get('activity') or {}).get('state', 'unreadable')}` | `git log` |", "",
            "⚠️ **`coverageComplete` is `false`.** Reads the pipeline store, "
            "checklist, mandates, soak_state.py and git log only — not `src/`, "
            "either VM or the review backlogs.", ""]


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
                          "section0Lines": section0_lines(res_due, today),
                          "healthy": True},
             "checklistState": "read", "checklist": checklist_view({}),
             "mandatesState": "absent", "mandates": mandates_view(None, "absent"),
             "forDate": "2026-09-21", "generatedAt": "x", "coverageComplete": False}
    md_due = render(b_due)
    check("§0 is built from pipeline.due via section0_lines, not re-derived",
          "**1 item(s) due. Each needs a disposition today.**" in md_due
          and "- **X**" in md_due)

    # ── an unreadable pipeline store is a declared floor, not a clean 0 ────
    res_bad = pipeline.LoadResult(unreadable=[(3, "JSONDecodeError: x")])
    b_bad = dict(b_due, pipeline={"stats": pipeline.stats(res_bad, today),
                                  "section0Lines": section0_lines(res_bad, today),
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
                               "section0Lines": section0_lines(res5, today),
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
