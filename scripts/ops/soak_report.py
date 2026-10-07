#!/usr/bin/env python3
# wiring: .github/workflows/soak-book-grade-weekly.yml (after the grade) -> docs/claude/work/SOAK-REPORT.md
#         + docs/claude/work/soak-state.json, read by scripts/ops/pipeline.py::is_due / render_section_0.
"""ONE SOAK REPORT — everything soaking, how long, against what exit, and its state.

WHY (operator, 2026-10-04): "I want a report on what's soaking, how long it's
been soaking, and what needs a decision. Verify we actually have a working
mechanism for reviewing soaks when they're due."

What was measured before this existed (SOAK-WATCH, 2026-10-04):
  * ``scripts/ops/soak_alarm.py`` — the four-state grader the canonical rule
    § "A soak must carry its own alarm" describes — reads the ``soak`` block of
    ``docs/claude/OPEN-ITEMS.json``, which was ARCHIVED 2026-09-21. It graded
    nothing for 13 days.
  * the R5 weekly grader never landed its per-leg record from the workflow
    (only a pointer), and the 2026-10-04 run crashed — see
    ``scripts/ops/soak_grade_landed.py``.

THE FOUR POPULATIONS (each listed in full — none sampled)
  1. every leg on a Stage-1 roster (``bybit_1``, ``alpaca_paper``);
  2. every ``execution: shadow`` strategy in ``config/strategies.yaml`` (the
     FIELD, not a grep — comment lines that mention "execution: shadow" are
     not strategies);
  3. every checklist row in state ``landed_unproven``;
  4. every open pipeline item with ``next_action: check_observation``.

THE FOUR STATES — ``soak_alarm.py``'s, ported, never collapsed
  ``ready``           the exit condition is met in data — come back now (LOUD)
  ``dead``            the soak cannot reach its exit as wired (LOUD) — was
                      ``not_writing``
  ``could-not-look``  nothing read it, or the read is stale (quiet, but a due row)
                      — was ``unknown``. NOT ``dead``: an unread log and an empty
                      log are opposite findings.
  ``accruing``        evidence arriving, exit not met — NO row, context only.
                      A daily "not ready yet" page is the desensitised alarm.

Populations 1-2 get a real grade from the latest R5 record. Populations 3-4
carry free-text exit conditions no predicate here can evaluate, so they are
``could-not-look`` by construction and SAY so — that is the honest state, and
the count of such rows is itself the finding.

START DATES are MEASURED from git: the commit that put the leg on the roster
(or set ``execution: shadow``) and kept it there continuously to HEAD. When the
run reaches the oldest commit available the start reads ``<=date`` — a floor,
never a guess. Requires full history (the workflow checks out fetch-depth 0).

``--sync-pipeline`` files one pipeline item per population-1/2 soak (id
``PI-SOAK-<start>-<subject>``) carrying ``due_when.soak``, so
``pipeline.is_due`` computes due-ness from the graded state (ready / dead /
could-not-look → due; accruing → not due), and closes the item with a stated
reason when the leg leaves its roster/shadow. Idempotent: an unchanged roster
writes nothing.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline  # noqa: E402
import checklist  # noqa: E402

STAGE1_ACCOUNTS = ("bybit_1", "alpaca_paper")
GRADE_DIR = "comms/research/soak_book_grade"
REPORT = Path("docs/claude/work/SOAK-REPORT.md")
#: population 4 is ~600 rows of free text: listed in full here, summarised in REPORT.
OBS_REPORT = Path("docs/claude/work/SOAK-REPORT-observations.md")
STATE = Path("docs/claude/work/soak-state.json")
CHECKLIST = Path("docs/claude/work/MANAGER-CHECKLIST.json")
#: A grade older than this is a stale read -> could-not-look. Weekly cadence + 1d.
GRADE_STALE_DAYS = 8
COST_FLOOR = 10  # r3_cost_fidelity.N_FLOOR, per side (rule RULE-R5 / R3)
_YAML = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

READY, DEAD, CNL, ACCRUING = "ready", "dead", "could-not-look", "accruing"
LOUD = (READY, DEAD)


# ── git ─────────────────────────────────────────────────────────────────────
def _git(*args: str) -> Tuple[int, str]:
    p = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)
    return p.returncode, p.stdout


def _commits(path: str, ref: str) -> List[Tuple[str, str]]:
    """(sha, YYYY-MM-DD) newest first for every commit touching ``path``."""
    rc, out = _git("log", ref, "--format=%H %cs", "--", path)
    return [tuple(ln.split()) for ln in out.splitlines() if ln.strip()] if rc == 0 else []


def _yaml_at(sha: str, path: str) -> Optional[dict]:
    rc, out = _git("show", f"{sha}:{path}")
    if rc != 0:
        return None
    try:
        return yaml.load(out, Loader=_YAML)
    except yaml.YAMLError:
        return None


def roster_members(doc: Optional[dict]) -> Optional[set]:
    """{(account, strategy)} on the Stage-1 rosters, or None if unparseable."""
    if not isinstance(doc, dict) or not isinstance(doc.get("accounts"), dict):
        return None
    out = set()
    for acc in STAGE1_ACCOUNTS:
        for s in (doc["accounts"].get(acc) or {}).get("strategies") or []:
            out.add(f"{acc}/{s}")
    return out


def shadow_members(doc: Optional[dict]) -> Optional[set]:
    if not isinstance(doc, dict) or not isinstance(doc.get("strategies"), dict):
        return None
    return {n for n, v in doc["strategies"].items()
            if isinstance(v, dict) and v.get("execution") == "shadow"}


def start_dates(subjects: set, path: str, member_fn, ref: str) -> Dict[str, str]:
    """Earliest date of the CONTINUOUS run of commits (to HEAD) in which each
    subject is a member. ``<=date`` when the run reaches the history boundary.
    Stops parsing as soon as every subject's run has ended."""
    commits = _commits(path, ref)
    open_ = set(subjects)
    last_seen: Dict[str, str] = {}
    for sha, day in commits:
        members = member_fn(_yaml_at(sha, path))
        if members is None:  # unparseable/older schema: treat as the boundary
            break
        for s in list(open_):
            if s in members:
                last_seen[s] = day
            else:
                open_.discard(s)
        if not open_:
            break
    out = {}
    for s in subjects:
        if s not in last_seen:
            out[s] = "unknown"  # member at HEAD per config but no commit carries it (uncommitted tree)
        elif s in open_:
            out[s] = f"<={last_seen[s]}"
        else:
            out[s] = last_seen[s]
    return out


def _day(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(s.lstrip("<="))
    except (ValueError, AttributeError):
        return None


def next_grade_day(today: date) -> date:
    """Next Sunday strictly after today (the R5 cron is Sunday 03:00 UTC)."""
    return today + timedelta(days=(6 - today.weekday()) % 7 or 7)


# ── grade ───────────────────────────────────────────────────────────────────
def latest_grade(ref: str) -> Tuple[Optional[str], Optional[dict]]:
    rc, out = _git("ls-tree", "--name-only", ref, f"{GRADE_DIR}/")
    names = sorted(n for n in out.split() if re.search(r"/\d{4}-\d{2}-\d{2}\.json$", n)) if rc == 0 else []
    if not names:
        return None, None
    rc, body = _git("show", f"{ref}:{names[-1]}")
    try:
        return names[-1], json.loads(body) if rc == 0 else None
    except ValueError:
        return names[-1], None


def grade_shadow(pkgs: Optional[dict], window_days: Optional[int]) -> Tuple[str, str]:
    """Verdict for an ``execution: shadow`` soak from the order packages R5
    counted for it (``grade["shadow_packages"]["by_strategy"][name]``).

    PI-20261004-JC8KDKLF-0002. >0 packages in the window -> accruing (the leg is
    alive and logging). 0 is NOT graded dead: a daily/4h leg can be quiet for a
    week, so a quiet window stays could-not-look (due), with the count stated.
    ``None`` = nobody counted -> could-not-look."""
    if pkgs is None or pkgs.get("n") is None:
        return CNL, "no shadow order-package count in the R5 record — nobody is looking"
    n, win = pkgs["n"], f"{window_days}d " if window_days else ""
    if n > 0:
        return ACCRUING, f"{n} shadow order packages logged in the {win}R5 window (last {pkgs.get('last_created_at')})"
    return CNL, (f"0 shadow order packages in the {win}R5 window — a quiet leg or a silent one; "
                 "not graded dead without a longer look")


@dataclass
class Soak:
    population: str
    subject: str
    what: str
    start: str
    exit_condition: str
    progress: str
    eta: str
    next_review: str
    verdict: str
    why: str
    refs: List[str] = field(default_factory=list)

    def days(self, today: date) -> Optional[int]:
        d = _day(self.start)
        return (today - d).days if d else None


def _cost_n(cell: Any) -> Tuple[Optional[int], Optional[int]]:
    if isinstance(cell, dict):
        return cell.get("n_entry"), cell.get("n_exit")
    return None, None


def grade_stage1(leg: Optional[dict], grade_ok: bool, grade_day: str) -> Tuple[str, str, str, str]:
    """(verdict, progress, eta, why) for a Stage-1 leg from its R5 row."""
    if not grade_ok:
        return CNL, "—", "—", f"no usable R5 grade newer than {GRADE_STALE_DAYS}d"
    if leg is None:
        return CNL, "—", "—", f"not in the {grade_day} grade (joined the roster after it, or the grade missed it)"
    mech = leg.get("mechanics") or {}
    ne, nx = _cost_n(leg.get("cost_fidelity"))
    prog = (f"mechanics {mech.get('state', '—')} ({mech.get('intents', '—')} intents → "
            f"{mech.get('received', '—')} received); cost fills entry {ne if ne is not None else '—'}"
            f"/{COST_FLOOR}, exit {nx if nx is not None else '—'}/{COST_FLOOR}")
    disp = leg.get("disposition")
    v = {"healthy": READY, "tweak": READY, "kill": DEAD, "insufficient-data": ACCRUING}.get(disp, CNL)
    return v, prog, str(leg.get("eta") or "—"), f"R5 {disp}: {str(leg.get('reason') or '')[:220]}"


# ── populations ─────────────────────────────────────────────────────────────
_CLOSE_RE = re.compile(
    r"((?:closes?|clears?|proven|done|ready) (?:when|on|once)|observation that (?:would )?close[sd]? it"
    r"|landed_unproven until|to close)[:\s\-–—]+(.{10,260}?)(?:\.\s|$)", re.I)


def exit_from_note(note: str) -> str:
    m = _CLOSE_RE.search(note or "")
    return m.group(2).strip() if m else "(NOT STATED in the row — close-out requires it)"


def pr_merge_dates(ref: str) -> Dict[int, str]:
    rc, out = _git("log", ref, "--format=%cs %s")
    dates: Dict[int, str] = {}
    for line in out.splitlines():
        day, _, subj = line.partition(" ")
        for n in re.findall(r"\(#(\d+)\)", subj):
            dates.setdefault(int(n), day)  # newest-first log: first hit = the merge
    return dates


def build(today: date, ref: str = "HEAD", grade_file: Optional[Path] = None) -> Dict[str, Any]:
    acc = yaml.load(open(REPO / "config/accounts.yaml"), Loader=_YAML)
    strat = yaml.load(open(REPO / "config/strategies.yaml"), Loader=_YAML)
    stage1 = sorted(roster_members(acc) or set())
    shadow = sorted(shadow_members(strat) or set())
    sdefs = strat.get("strategies") or {}

    if grade_file is not None:
        # The weekly workflow passes the record it JUST wrote (not yet on any ref).
        gfile = str(grade_file)
        try:
            grade = json.loads(Path(grade_file).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            grade = None
    else:
        gfile, grade = latest_grade(ref)
    gday = _day(Path(gfile).stem) if gfile else None
    gage = (today - gday).days if gday else None
    grade_ok = bool(grade) and gage is not None and gage <= GRADE_STALE_DAYS and \
        grade.get("mechanics_read_state") == "measured"
    legs = {f"{lg['account_id']}/{lg['strategy']}": lg for lg in (grade or {}).get("legs") or []}

    s1_start = start_dates(set(stage1), "config/accounts.yaml", roster_members, ref)
    sh_start = start_dates(set(shadow), "config/strategies.yaml", shadow_members, ref)
    nxt = next_grade_day(today).isoformat()

    res = pipeline.read_log(REPO / pipeline.STORE)
    open_items = [i for i in res.items.values() if i.get("state") in pipeline.OPEN_STATES]

    def refs_for(subject: str) -> List[str]:
        return sorted(i["id"] for i in open_items
                      if subject in str(i.get("what", "")) and not str(i["id"]).startswith("PI-SOAK-"))

    soaks: List[Soak] = []
    shadow_set = set(shadow)
    for s in stage1:
        if s.split("/", 1)[1] in shadow_set:
            soaks.append(Soak("stage1", s, "Stage-1 roster leg in execution: shadow", s1_start[s],
                              f"graded under the shadow population (`{s.split('/', 1)[1]}`) — a shadow "
                              "leg sends no order, so Gate 2 cannot apply", "—", "—", nxt, "see-shadow",
                              "shadow: no orders, no fills — R5 cannot grade it", refs_for(s)))
            continue
        v, prog, eta, why = grade_stage1(legs.get(s), grade_ok, gfile or "—")
        soaks.append(Soak("stage1", s, "Stage-1 soak leg", s1_start[s],
                          "Gate 2: mechanics flowing AND R3 cost fidelity decisive at >=10 fills/side "
                          "(RULE-R5-SOAK-BOOK-GRADE-v1)", prog, eta, nxt, v, why, refs_for(s)))

    rostered = {k: [a for a, x in (acc.get("accounts") or {}).items() if k in (x.get("strategies") or [])]
                for k in shadow}
    for s in shadow:
        d = sdefs.get(s) or {}
        accts = rostered[s]
        s1 = [f"{a}/{s}" for a in accts if a in STAGE1_ACCOUNTS]
        if d.get("enabled") is False:
            v, why, prog = DEAD, "enabled: false — the shadow soak runs nothing and logs nothing", "—"
        elif not accts:
            v, why, prog = DEAD, "on NO account roster — nothing runs it, so nothing accrues", "—"
        elif s1 and grade_ok and (legs.get(s1[0]) or {}).get("mechanics") is not None \
                and legs[s1[0]].get("execution") == "shadow":
            # only a row graded WHILE the leg was shadow describes its shadow soak;
            # a row from before the flip measured a live leg.
            mech = legs[s1[0]]["mechanics"]
            n = mech.get("intents")
            if n is None:
                v, why = CNL, f"R5 mechanics row for {s1[0]} carries no intent count"
            else:
                v = ACCRUING if n > 0 else DEAD
                why = (f"logging order packages on {s1[0]} ({n} intents in the R5 window)" if n > 0
                       else f"0 intents on {s1[0]} in the R5 window — writing nothing")
            prog = f"{n} intents / R5 window; shadow sends no order, so cost fidelity cannot accrue"
        else:
            # MEASURED 2026-09-26 grade: R5 writes `mechanics: null` for every
            # shadow leg. Since JC8KDKLF-0002 R5 also records shadow_packages
            # (order packages per shadow strategy over the window); use it when
            # the record is fresh and that read measured.
            sp = (grade or {}).get("shadow_packages") or {}
            pk = (sp.get("by_strategy") or {}).get(s) if (
                gage is not None and gage <= GRADE_STALE_DAYS and sp.get("read_state") == "measured") else None
            v, why = grade_shadow(pk, (sp.get("window_hours") or 0) // 24 or None)
            prog = f"{pk['n']} shadow packages / R5 window" if pk else "—"
        ev = (REPO / "comms/strategy_evidence" / f"{s}.json").exists()
        soaks.append(Soak("shadow", s, "execution: shadow strategy", sh_start[s],
                          "Gate 1: a committed Stage-0 evidence record clearing its registered rule "
                          f"→ flip to live, else kill (evidence record {'present' if ev else 'ABSENT'})",
                          prog, "no data-driven ETA — shadow never accrues fills", nxt, v, why,
                          refs_for(s)))

    prs = pr_merge_dates(ref)
    ck = checklist.load_path(REPO / CHECKLIST)
    for row in ck.get("items") or []:
        if row.get("state") != "landed_unproven":
            continue
        ds = [prs[p] for p in row.get("prs") or [] if isinstance(p, int) and p in prs]
        soaks.append(Soak("landed_unproven", row["id"], str(row.get("title", ""))[:140],
                          max(ds) if ds else "unknown (no PR merge found in git)",
                          exit_from_note(row.get("note", "")), "—", "—", "none declared",
                          CNL, "checklist rows carry free-text exit conditions; nothing evaluates them"))

    for it in sorted(open_items, key=lambda i: i["id"]):
        if it.get("next_action") != "check_observation" or str(it["id"]).startswith("PI-SOAK-"):
            continue
        dw = it.get("due_when") or {}
        fd = pipeline.filed_date(it)
        every = dw.get("check_every_days")
        last = dw.get("last_checked")
        if pipeline.is_due(it, today):
            nr = "due now"
        elif last and every:
            nr = (_day(last) + timedelta(days=every)).isoformat() if _day(last) else "—"
        else:
            nr = "—"
        soaks.append(Soak("check_observation", it["id"], str(it.get("what", ""))[:140],
                          fd.isoformat() if fd else "unknown (no date in id)",
                          str(dw.get("clears_when") or dw.get("due_date") or "—")[:200], "—", "—", nr, CNL,
                          f"state {it.get('state')}; nothing evaluates clears_when"))

    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "today": today.isoformat(), "grade_file": gfile, "grade_age_days": gage,
            "grade_usable": grade_ok, "soaks": soaks}


# ── outputs ─────────────────────────────────────────────────────────────────
POP_TITLES = {
    "stage1": "Stage-1 soak legs (bybit_1, alpaca_paper)",
    "shadow": "`execution: shadow` strategies (config/strategies.yaml field)",
    "landed_unproven": "Checklist rows in `landed_unproven`",
    "check_observation": "Open pipeline items with `next_action: check_observation`",
}


def state_doc(b: Dict[str, Any]) -> Dict[str, Any]:
    today = date.fromisoformat(b["today"])
    return {
        "generated_at": b["generated_at"], "grade_file": b["grade_file"],
        "grade_age_days": b["grade_age_days"], "grade_usable": b["grade_usable"],
        "subjects": {s.subject: {"population": s.population, "verdict": s.verdict, "start": s.start,
                                 "days": s.days(today), "why": s.why}
                     for s in b["soaks"] if s.population in ("stage1", "shadow")
                     and s.verdict != "see-shadow"},
    }


_STAMP = ("> **Doc status:** `unknown` · category `unknown` · last verified `never` · registered in "
          "[`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · *generated weekly by "
          "`scripts/ops/soak_report.py` — the generation time below is its freshness*")


def _cell(x: Any) -> str:
    return str(x).replace("|", "\\|").replace("\n", " ")


def render(b: Dict[str, Any]) -> str:
    today = date.fromisoformat(b["today"])
    soaks: List[Soak] = b["soaks"]
    L = ["# SOAK REPORT", "", _STAMP, "",
         f"> **Generated** {b['generated_at']} by `scripts/ops/soak_report.py` (weekly, after the R5 grade). "
         "Do not hand-edit — regenerate.", "",
         f"> **R5 grade read:** `{b['grade_file']}` — {b['grade_age_days']}d old — "
         f"{'usable' if b['grade_usable'] else '**NOT USABLE (missing, stale or unmeasured) → Stage-1 legs read could-not-look**'}.",
         "", "## Summary", "",
         "| population | n | ready | dead | could-not-look | accruing | graded under shadow |",
         "|---|---|---|---|---|---|---|"]
    for pop, title in POP_TITLES.items():
        rows = [s for s in soaks if s.population == pop]
        c = {v: sum(1 for s in rows if s.verdict == v) for v in (READY, DEAD, CNL, ACCRUING, "see-shadow")}
        L.append(f"| {title} | {len(rows)} | {c[READY]} | {c[DEAD]} | {c[CNL]} | {c[ACCRUING]} "
                 f"| {c['see-shadow']} |")
    L += ["", "States are `soak_alarm.py`'s four, ported: **ready** and **dead** are loud (a decision is "
          "owed); **could-not-look** means nothing read it — *not* that it is empty; **accruing** is quiet "
          "by design.", "", "## Needs a decision (ready or dead)", ""]
    loud = [s for s in soaks if s.verdict in LOUD]
    if not loud:
        L.append("Nothing is ready or dead." if b["grade_usable"] else
                 "Nothing graded loud — but the grade is not usable, so this is *could not look*, not *clear*.")
    for s in loud:
        L.append(f"- **{s.verdict.upper()}** `{s.subject}` ({s.population}, soaking since {s.start}, "
                 f"{s.days(today)}d) — {s.why}" + (f" · open items: {', '.join(s.refs[:4])}"
                    + (f" (+{len(s.refs) - 4})" if len(s.refs) > 4 else "") if s.refs else ""))
    L.append("")
    for pop, title in POP_TITLES.items():
        rows = [s for s in soaks if s.population == pop]
        L += [f"## {title} — {len(rows)}", ""]
        if pop == "check_observation":
            due_now = sum(1 for r in rows if r.next_review == "due now")
            L += [f"{len(rows)} open items; {due_now} are due now by their own timer. None carries an "
                  "evaluator for its `clears_when`, so every one is **could-not-look** until a session "
                  f"reads it. Full list: [`{OBS_REPORT.name}`]({OBS_REPORT.name}).", ""]
            continue
        L += _table(rows, today)
    return "\n".join(L)


def _table(rows: List[Soak], today: date) -> List[str]:
    L = ["| subject | what | start | days | exit condition | progress | ETA | next review | verdict |",
         "|---|---|---|---|---|---|---|---|---|"]
    for s in sorted(rows, key=lambda r: (r.verdict not in LOUD, r.verdict, r.subject)):
        d = s.days(today)
        L.append("| " + " | ".join(_cell(x) for x in (
            f"`{s.subject}`", s.what, s.start, "—" if d is None else d, s.exit_condition,
            s.progress, s.eta, s.next_review, f"**{s.verdict}**")) + " |")
    L.append("")
    return L


def render_observations(b: Dict[str, Any]) -> str:
    today = date.fromisoformat(b["today"])
    rows = [s for s in b["soaks"] if s.population == "check_observation"]
    L = ["# SOAK REPORT — open `check_observation` pipeline items", "", _STAMP, "",
         f"> Generated {b['generated_at']} by `scripts/ops/soak_report.py`; companion to "
         f"[`{REPORT.name}`]({REPORT.name}). {len(rows)} rows, every one **could-not-look** (no evaluator "
         "reads `clears_when`).", "",
         "| id | start | days | clears_when | next review | state |", "|---|---|---|---|---|---|"]
    for s in sorted(rows, key=lambda r: (r.next_review != "due now", r.start, r.subject)):
        d = s.days(today)
        L.append("| " + " | ".join(_cell(x) for x in (
            f"`{s.subject}`", s.start, "—" if d is None else d, s.exit_condition[:140], s.next_review,
            s.why.split(";")[0])) + " |")
    return "\n".join(L)


# ── pipeline sync ───────────────────────────────────────────────────────────
def _soak_items(res) -> Dict[str, dict]:
    return {(i.get("due_when") or {}).get("soak", {}).get("subject"): i
            for i in res.items.values()
            if str(i.get("id", "")).startswith("PI-SOAK-") and i.get("state") in pipeline.OPEN_STATES}


def sync_pipeline(b: Dict[str, Any], store: Path) -> List[str]:
    res = pipeline.read_log(store)
    have = _soak_items(res)
    want = {s.subject: s for s in b["soaks"]
            if s.population in ("stage1", "shadow") and s.verdict != "see-shadow"}
    wrote: List[str] = []
    for subj, s in sorted(want.items()):
        if subj in have:
            continue
        # The id carries the FILING date: pipeline.filed_date() reads it as the
        # item's age for the routing alarm. The soak's own start date lives in
        # due_when.soak.declared_at (a soak begun in May filed today is 0d unrouted).
        item = {
            "id": f"PI-SOAK-{date.today().strftime('%Y%m%d')}-{subj.replace('/', '-')}",
            "what": f"SOAK ({s.population}): {subj} — {s.what}. Exit: {s.exit_condition}. "
                    f"Graded weekly into ready/dead/could-not-look/accruing; due only when not accruing.",
            "origin": {"kind": "review", "ref": "SOAK-WATCH 2026-10-04 (scripts/ops/soak_report.py)",
                       "rerun": "python3 scripts/ops/soak_report.py --print-state"},
            "due_when": {"kind": "observation", "clears_when": s.exit_condition, "check_every_days": 7,
                         "soak": {"subject": subj, "declared_at": s.start.lstrip("<="),
                                  "state_source": str(STATE)}},
            "next_action": "check_observation", "state": "queued",
        }
        pipeline.append(item, store, intent="new")
        wrote.append(item["id"])
    for subj, it in sorted(have.items()):
        if subj in want:
            continue
        closed = dict(it, state="killed",
                      terminal_reason=f"soak ended {date.today().isoformat()}: {subj} is no longer on a "
                                      f"Stage-1 roster / no longer execution: shadow (soak_report.py sync)")
        closed.pop("routed_to", None)
        pipeline.append(closed, store, intent="update")
        wrote.append(f"{it['id']} (closed)")
    return wrote


def _self_test() -> int:
    ok = True

    def ck(label: str, cond: bool) -> None:
        nonlocal ok
        ok &= cond
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")

    ck("next grade day from Sat 10-03 is Sun 10-04", next_grade_day(date(2026, 10, 3)) == date(2026, 10, 4))
    ck("next grade day from Sun 10-04 is the FOLLOWING Sunday",
       next_grade_day(date(2026, 10, 4)) == date(2026, 10, 11))
    leg = {"disposition": "kill", "reason": "starved", "mechanics": {"state": "starved", "intents": 5,
           "received": 0}, "cost_fidelity": {"n_entry": 0, "n_exit": 0}}
    ck("kill grades dead", grade_stage1(leg, True, "g")[0] == DEAD)
    ck("insufficient-data grades accruing (quiet)",
       grade_stage1(dict(leg, disposition="insufficient-data"), True, "g")[0] == ACCRUING)
    ck("healthy grades ready", grade_stage1(dict(leg, disposition="healthy"), True, "g")[0] == READY)
    ck("stale grade is could-not-look, never dead", grade_stage1(leg, False, "g")[0] == CNL)
    ck("leg absent from grade is could-not-look", grade_stage1(None, True, "g")[0] == CNL)
    ck("roster_members reads only Stage-1 rosters",
       roster_members({"accounts": {"bybit_1": {"strategies": ["x"]}, "bybit_2": {"strategies": ["y"]}}})
       == {"bybit_1/x"})
    ck("shadow_members reads the field, not comments",
       shadow_members({"strategies": {"a": {"execution": "shadow"}, "b": {"execution": "live"}}}) == {"a"})
    ck("shadow with logged packages grades accruing", grade_shadow({"n": 3, "last_created_at": "x"}, 7)[0] == ACCRUING)
    ck("shadow with 0 packages is could-not-look, never dead", grade_shadow({"n": 0}, 7)[0] == CNL)
    ck("shadow with no count is could-not-look", grade_shadow(None, 7)[0] == CNL)
    ck("exit_from_note finds a stated close condition",
       exit_from_note("blah. Closes when: one live fill shows exchange_fill. more").startswith("one live fill"))
    ck("exit_from_note says NOT STATED when absent", "NOT STATED" in exit_from_note("nothing here"))
    print("soak_report self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help=f"write {REPORT} and {STATE}")
    ap.add_argument("--sync-pipeline", action="store_true",
                    help="file/close PI-SOAK-* pipeline items to match populations 1-2")
    ap.add_argument("--print-state", action="store_true")
    ap.add_argument("--ref", default="HEAD", help="git ref for history + the grade record")
    ap.add_argument("--grade-file", type=Path, default=None,
                    help="read this R5 record instead of the newest one on --ref")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    b = build(datetime.now(timezone.utc).date(), a.ref, a.grade_file)
    if a.write:
        (REPO / REPORT).write_text(render(b) + "\n", encoding="utf-8")
        (REPO / OBS_REPORT).write_text(render_observations(b) + "\n", encoding="utf-8")
        (REPO / STATE).write_text(json.dumps(state_doc(b), indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {REPORT}, {OBS_REPORT} and {STATE}")
    if a.sync_pipeline:
        for w in sync_pipeline(b, REPO / pipeline.STORE):
            print(f"pipeline: {w}")
    if a.print_state or not (a.write or a.sync_pipeline):
        c: Dict[str, int] = {}
        for s in b["soaks"]:
            c[f"{s.population}:{s.verdict}"] = c.get(f"{s.population}:{s.verdict}", 0) + 1
        print(json.dumps({"grade_file": b["grade_file"], "grade_age_days": b["grade_age_days"],
                          "grade_usable": b["grade_usable"], "counts": c}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
