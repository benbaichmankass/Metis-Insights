#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::checklist-routing-age-guard (--self-test) +
#         .github/workflows/constraint-readout.yml (--write)
"""How long has a FILED checklist row sat with nobody to do it?

WHY THIS EXISTS
---------------
Operator, 2026-09-10, stating the manager's core mandate: *"your job as a
manager session … is to ensure that things don't get dropped in the middle and
that we actually finish things and execute them … the main impetus for moving
to a new system with the manager session was to prevent these kinds of things
from happening."*

`MI-246` is the third member of a family. `MI-235` covers a session BLOCKED on a
manager action never being told when it clears. `MI-236` covers an `in_flight`
row going stale because nothing decays it. **Neither covers a row that was
FILED, correctly, with a real owner-shaped gap — and then simply never routed.**
Such a row sits `ready` / `owner: unassigned` indefinitely, and the register made
*filed* and *routed* indistinguishable: the row had **no age**. Nothing computed
how long it had been unrouted and no surface reported it.

Four measured instances, every one found only because the operator happened to
ask a question that touched it — `MI-201` (two days, while being the answer to
*"why am I still not seeing any live trades"*), an operator answer written into
an object's TITLE while `chosen` stayed null, `MI-140` reading `queued` for three
days after its PR merged, and the checklist's own `cycle` field naming a
priority superseded four days earlier.

THE AGE ALREADY EXISTS — IT IS JUST NEVER COMPUTED
--------------------------------------------------
`items[]` carries no `state_since`, and adding one would be a schema migration
(`MI-237`) maintained by hand — a habit, not a mechanism. But
`docs/claude/work/MANAGER-CHECKLIST.json` is committed several times an hour, so
**git already records every state transition**. This module replays those
revisions and derives the age. That is why `MI-246` is deliberately NOT blocked
on `MI-237`: the missing thing was an elapsed-time term, and git has it.

⚠️ **A SHALLOW CLONE MUST REFUSE, NOT ANSWER.** `git log` on a truncated clone
returns a plausible wrong answer with no error — `BL-20260730-SHALLOW-CLONE-
DEFEATS-HISTORY-RULE`, the instance where the mandated history check was itself
silently answering out of a truncated scope. So the derivation grades
`could_not_read` rather than reporting a comfortable zero.

THE THRESHOLD IS MEASURED, AND SO IS THE REASON IT IS NOT THE WHOLE DESIGN
--------------------------------------------------------------------------
MEASURED 2026-09-11 by ``--measure`` over **all 195 revisions of
docs/claude/work/MANAGER-CHECKLIST.json on main**, 2026-09-02T00:36:53+03:00 →
2026-09-11T09:50:01Z, 0 unparseable (re-derive it, do not trust this paragraph):

* time to first real owner, for rows that were ever open-and-unrouted and later
  got one — **n=37**: median **3.85 h**, p75 14.08, p90 22.37, **p95 24.24**,
  max 43.60. 94.6% routed within 24 h, 100% within 48 h.
* currently open-and-unrouted: **75 of 270 rows**, of which **70 are older than
  24 h**, 55 older than 48 h, oldest **228.2 h**.

So ``THRESHOLD_HOURS = 24`` is the p95 of observed routing latency — a measured
quantile that happens to land on a round number, not a round number chosen by
feel.

⚠️ **AND THE SECOND FIGURE KILLS THE OBVIOUS DESIGN.** A surface that reports
every row past the threshold would report **70 rows today**. That is not a
signal, it is a second backlog, and a list of 70 in the session brief is the
desensitised alarm this repo calls its own worst failure mode — the same shape
as the 62/86-manifest dataset audit that hid a real bug for weeks.

So the surface reports the **CROSSING**, not the **STOCK**. MEASURED over the
same population, crossings run **~7/day** (68 over 10 days; 22 on 2026-09-07,
one day after 21 rows were added in bulk). A row is LOUD once, when it crosses;
after that it is a COUNT. That is the pattern
``src/runtime/close_wedge_standing.py`` already uses and `CLAUDE.md` documents:
*"the first sighting still PAGES … only `still_standing` is quiet"*.

WHAT THIS IS NOT
----------------
**It is not a licence to auto-route.** Spawning against an unread row is worse
than leaving it, and the operator's cap of three concurrent lanes is deliberate.
This reports; a human or a manager decides. It is equally **not another
reminder** — it renders into `CLAUDE.md` and `docs/claude/DUE.md` without anyone
choosing to look, which is the whole of `MI-246`'s `what_this_is_not`.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CHECKLIST = "docs/claude/work/MANAGER-CHECKLIST.json"
OUT = Path("docs/claude/work/CHECKLIST-ROUTING-AGE.json")

#: The p95 of measured routing latency — see the module docstring for the
#: population. Re-derive with ``--measure``; do not adjust it by feel.
THRESHOLD_HOURS = 24
THRESHOLD_BASIS = (
    "p95 of time-to-first-real-owner (24.24h) over n=37 rows that were ever "
    "open-and-unrouted and later got one, replayed from all 195 revisions of "
    f"{CHECKLIST} on main, 2026-09-02T00:36:53+03:00 -> 2026-09-11T09:50:01Z. "
    "Re-derive with: python3 scripts/ops/checklist_routing_age.py --measure"
)

# ── routing state ──────────────────────────────────────────────────────────
# Is there a real person-shaped owner on this row? Four states, and the fourth
# is the one that matters: an owner field we cannot read is NOT an owner.
ROUTE_ROUTED = "routed"          # a named session / manager / person
ROUTE_UNROUTED = "unrouted"      # explicitly nobody
ROUTE_ABSENT = "absent"          # no owner field at all — WE DID NOT LOOK
ROUTING_STATES = (ROUTE_ROUTED, ROUTE_UNROUTED, ROUTE_ABSENT)

_NOBODY = re.compile(r"^\s*(unassigned|unowned|none|null|nobody|tbd|n/?a|-+)\b", re.I)

# ── the status vocabulary, closed on purpose ───────────────────────────────
# MI-237: the checklist carries TWO competing status fields (`state` and
# `status`) that disagree on 13 rows. This module reads BOTH and does not try
# to adjudicate them — a row is OPEN only if no reading of it is terminal.
#
# ⚠️ A VALUE IN NEITHER SET IS `ungradeable`, NEVER a default. Folding an
# unknown status into OPEN manufactures a stall; folding it into NOT-OPEN hides
# one. Both are confident wrong answers, which is what this whole family of
# guards exists to refuse.
OPEN_STATUSES = frozenset({"queued", "ready", "triage", "unassigned", "open"})
NOT_OPEN_STATUSES = frozenset({
    "done", "dropped", "closed", "superseded", "landed_unproven", "review_ready",
    "in_flight", "blocked", "waiting", "deferred", "filed", "filed_not_taken",
    "merged", "accepted", "failed", "operator_approved",
    "needs_redispatch_premise_is_wrong", "snoozed", "promoted_to_roadmap",
})
#: `duplicate_of_MI-180` etc. — a family, not a literal.
_NOT_OPEN_PREFIXES = ("duplicate_of", "superseded_by", "closed_")

OPEN_OPEN = "open"
OPEN_NOT_OPEN = "not_open"
OPEN_UNGRADEABLE = "ungradeable"
OPENNESS_STATES = (OPEN_OPEN, OPEN_NOT_OPEN, OPEN_UNGRADEABLE)

# ── stall state — THE CONTRACT CONSUMERS BRANCH ON ─────────────────────────
STALL_NEWLY = "newly_stalled"   # crossed the threshold and has NEVER been reported — LOUD
STALL_STANDING = "standing"     # already reported once — a COUNT, never a list
STALL_WITHIN = "within"         # unrouted, but not past the threshold yet
STALL_UNKNOWN = "unknown"       # WE COULD NOT LOOK — never "nothing is stale"
STALL_STATES = (STALL_NEWLY, STALL_STANDING, STALL_WITHIN, STALL_UNKNOWN)

# ── history state ──────────────────────────────────────────────────────────
HIST_DERIVED = "derived"
HIST_COULD_NOT_READ = "could_not_read"
HISTORY_STATES = (HIST_DERIVED, HIST_COULD_NOT_READ)


# ── pure classifiers ───────────────────────────────────────────────────────

def routing_state(row: dict) -> str:
    """Does this row name a real owner? Pure, so the policy is arguable in tests."""
    owner = row.get("owner")
    if owner is None or not str(owner).strip():
        return ROUTE_ABSENT
    return ROUTE_UNROUTED if _NOBODY.match(str(owner)) else ROUTE_ROUTED


def _status_values(row: dict) -> list:
    return [str(v).strip() for v in (row.get("state"), row.get("status"))
            if v is not None and str(v).strip()]


def openness(row: dict) -> str:
    """Is this row still awaiting work? `ungradeable` is a real answer."""
    vals = _status_values(row)
    if not vals:
        return OPEN_UNGRADEABLE
    if any(v in NOT_OPEN_STATUSES or v.startswith(_NOT_OPEN_PREFIXES) for v in vals):
        return OPEN_NOT_OPEN
    if any(v in OPEN_STATUSES for v in vals):
        return OPEN_OPEN
    return OPEN_UNGRADEABLE


def is_unrouted_open(row: dict) -> bool | None:
    """True / False / None — and `None` is *we could not grade it*.

    Collapsing `None` into False is the exact defect this module exists to
    catch, one level up: a row nobody can grade is a row nobody is watching.
    """
    if routing_state(row) == ROUTE_ROUTED:
        return False
    o = openness(row)
    if o == OPEN_UNGRADEABLE:
        return None
    return o == OPEN_OPEN


def grade_stall(hours: float | None, *, reported_before: bool,
                threshold_hours: float = THRESHOLD_HOURS) -> str:
    """One row's stall state.

    ``hours is None`` means the age could not be derived, which is
    ``STALL_UNKNOWN`` — never ``STALL_WITHIN``. A row whose age we failed to
    compute is the *most* likely to be the dropped one, not the least.
    """
    if hours is None:
        return STALL_UNKNOWN
    if hours < threshold_hours:
        return STALL_WITHIN
    return STALL_STANDING if reported_before else STALL_NEWLY


# ── git-derived history ────────────────────────────────────────────────────

def _run(args: list) -> tuple:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if p.returncode != 0:
        return None, (p.stderr or "").strip()[:200]
    return p.stdout, ""


def history_is_usable(repo: Path) -> tuple:
    """(usable, note). The test is a POSITIVE CONTROL, not the absence of an error.

    A truncated history does not error — it silently reports every row as young,
    which reads as *nothing is stale*. That is the reassuring direction, and the
    reassuring direction is the one that costs weeks
    (`BL-20260730-SHALLOW-CLONE-DEFEATS-HISTORY-RULE`, where the mandated
    Tier-2/3 history check was itself answering out of a truncated scope).

    ⚠️ **`.git/shallow` is the WRONG test on its own, in BOTH directions.** This
    session's clone carries a shallow boundary at 2026-06-28 while the checklist
    was created 2026-09-02 — so the history is complete *for this path* and a
    bare shallow check would refuse a measurement it could perfectly well make.
    The converse is worse: a deepened clone can still stop short of the add.

    So the test is whether the commit that **ADDED** the file is reachable. That
    is the positive control RULE ONE asks for: prove the probe can see the
    beginning before trusting the ages it derives from the middle.

    ⚠️ Stated rather than hidden: if the checklist is ever RENAMED, git reports
    the rename as an add at the new path and this would read `derived` over a
    history that begins at the rename. The ages would then be under-estimates,
    which is the reassuring direction — so re-point this check at the same time
    as any such rename.
    """
    out, err = _run(["git", "-C", str(repo), "log", "--diff-filter=A",
                     "--format=%H", "--", CHECKLIST])
    if out is None:
        return False, f"git log failed: {err}"
    if not out.strip():
        shallow = (repo / ".git" / "shallow").exists()
        return False, (
            f"the commit that ADDED {CHECKLIST} is NOT reachable"
            + (" and the clone is SHALLOW" if shallow else "")
            + " — every derived age would be an under-estimate. Run "
              "`git fetch --unshallow` (or checkout with fetch-depth: 0).")
    out2, err2 = _run(["git", "-C", str(repo), "log", "--format=%H", "--", CHECKLIST])
    if out2 is None:
        return False, f"git log failed: {err2}"
    n = len([ln for ln in out2.splitlines() if ln.strip()])
    if n < 2:
        return False, (f"only {n} revision(s) of {CHECKLIST} are reachable — an age "
                       f"needs at least one prior revision to be measured against.")
    return True, f"{n} revisions reachable, back to the commit that added the file"


def _revisions(repo: Path) -> list:
    out, err = _run(["git", "-C", str(repo), "log", "--format=%H|%aI", "--reverse",
                     "--", CHECKLIST])
    if out is None:
        return []
    revs = []
    for ln in out.splitlines():
        if "|" not in ln:
            continue
        sha, ts = ln.split("|", 1)
        try:
            revs.append((sha, datetime.fromisoformat(ts).astimezone(timezone.utc)))
        except ValueError:
            continue
    return revs


def unrouted_since(repo: Path, revs: list) -> dict:
    """id -> the UTC instant it most recently BECAME open-and-unrouted.

    "Most recently" matters: a row that was routed, finished, and re-opened has
    a new clock, not its original one. A row that is currently ungradeable is
    absent from the map, which the caller grades `STALL_UNKNOWN`.
    """
    since: dict = {}
    for sha, when in revs:
        blob, _ = _run(["git", "-C", str(repo), "show", f"{sha}:{CHECKLIST}"])
        if blob is None:
            continue
        try:
            items = (json.loads(blob) or {}).get("items") or []
        except (ValueError, TypeError):
            continue
        seen = set()
        for row in items:
            ident = row.get("id")
            if not ident:
                continue
            seen.add(ident)
            flag = is_unrouted_open(row)
            if flag is True:
                since.setdefault(ident, when)
            elif flag is False:
                since.pop(ident, None)   # routed or finished: the clock resets
            # flag is None -> ungradeable: HOLD the existing clock rather than
            # clearing it. A row that becomes unreadable has not been routed.
        for gone in [k for k in since if k not in seen]:
            since.pop(gone)              # dropped from the file entirely
    return since


# ── the register ───────────────────────────────────────────────────────────

def build(repo: Path, *, now: datetime | None = None,
          previous: dict | None = None, seed: bool | None = None,
          threshold_hours: float = THRESHOLD_HOURS) -> dict:
    """Grade every row. On the FIRST run the standing backlog is SEEDED, not paged.

    ⚠️ **THE COLD START IS THE WHOLE RISK AND IT IS HANDLED DELIBERATELY.**
    MEASURED 2026-09-11: 70 of 270 rows are already past the threshold, several
    by more than 200 h. Reporting them all on the first run would produce one
    70-row block — unreadable, skimmed, and then silent forever, so the
    mechanism's first and only loud act would be the one nobody reads. That is
    the desensitised alarm, arrived at by being thorough.

    So a first run ARMS ITSELF: the standing backlog goes into `seeded_ids` and
    into the ledger, and the surface says in one line how many it armed over.
    Same shape as `check_pr_queue_watch.py` (`never_ran` -> arms on its first
    successful run) and the `soak-registered-guard` BASELINE — an escape hatch
    that is **visible, counted, and named in the artifact**, never silent.

    ⚠️ **Seeding is NOT a disposition.** Those 70 rows remain unrouted and
    remain somebody's problem; this says only that they are not NEWS. They are
    `MI-243`/backlog territory, and `seeded_ids` names every one of them so the
    set is workable rather than lost.
    """
    now = now or datetime.now(timezone.utc)
    seed = (previous is None or not previous) if seed is None else seed
    previous = previous or {}
    already = set(previous.get("reported_ids") or [])

    try:
        checklist = json.loads((repo / CHECKLIST).read_text(encoding="utf-8"))
        items = checklist.get("items") or []
    except (OSError, ValueError, TypeError) as exc:
        return _envelope(now, HIST_COULD_NOT_READ,
                         f"{CHECKLIST}: {type(exc).__name__}: {exc}",
                         [], 0, 0, 0, sorted(already), threshold_hours)

    usable, note = history_is_usable(repo)
    since = unrouted_since(repo, _revisions(repo)) if usable else {}

    newly, seeded, standing_n, within_n, unknown_n = [], [], 0, 0, 0
    for row in items:
        flag = is_unrouted_open(row)
        if flag is False:
            continue
        ident = row.get("id") or "(no id)"
        if flag is None:
            # Ungradeable openness: we cannot say it is fine.
            unknown_n += 1
            continue
        start = since.get(ident) if usable else None
        hours = (now - start).total_seconds() / 3600 if start else None
        state = grade_stall(hours, reported_before=ident in already,
                            threshold_hours=threshold_hours)
        if state == STALL_NEWLY:
            (seeded if seed else newly).append({
                "id": ident,
                # The state travels WITH the row, so a reader of the JSON sees
                # why it is here and a consumer branches on the vocabulary
                # rather than on the shape of the list it arrived in.
                "stall_state": state,
                "title": str(row.get("title") or "")[:180],
                "owner": str(row.get("owner") or "(no owner field)")[:80],
                "status": "/".join(_status_values(row)) or "(none)",
                "lane": row.get("lane"),
                "tier": row.get("tier"),
                "unrouted_hours": round(hours or 0.0, 1),
                "unrouted_since": start.replace(microsecond=0).isoformat() if start else None,
            })
        elif state == STALL_STANDING:
            standing_n += 1
        elif state == STALL_WITHIN:
            within_n += 1
        else:
            unknown_n += 1

    newly.sort(key=lambda r: -r["unrouted_hours"])
    seeded.sort(key=lambda r: -r["unrouted_hours"])
    reported = sorted(already | {r["id"] for r in newly} | {r["id"] for r in seeded})
    return _envelope(now, HIST_DERIVED if usable else HIST_COULD_NOT_READ, note,
                     newly, standing_n, within_n, unknown_n, reported,
                     threshold_hours, seeded=seeded)


def _envelope(now, hist_state, note, newly, standing, within, unknown,
              reported, threshold_hours, *, seeded=None) -> dict:
    seeded = seeded or []
    return {
        "schema_version": 1,
        "what_this_is": (
            "How long each FILED manager-checklist row has sat with nobody to do "
            "it, derived from the git history of " + CHECKLIST + " by "
            "scripts/ops/checklist_routing_age.py. Read `history_state` BEFORE "
            "`newly_stalled`: on `could_not_read` an empty list means WE DID NOT "
            "LOOK, never that nothing is stalled."
        ),
        "generated_at": now.replace(microsecond=0).isoformat(),
        "history_state": hist_state,
        "history_note": note,
        "threshold_hours": threshold_hours,
        "threshold_basis": THRESHOLD_BASIS,
        "newly_stalled": newly,
        # Keyed by the STATE NAMES, not by ad-hoc field names, so a consumer
        # cannot read three of the four and silently drop the fourth.
        "stall_counts": {
            STALL_NEWLY: len(newly),
            STALL_STANDING: standing,
            STALL_WITHIN: within,
            STALL_UNKNOWN: unknown,
        },
        # The first-run arming set. NOT a disposition and NOT a report — see
        # build()'s docstring. Named in full so the backlog stays workable.
        "seeded_count": len(seeded),
        "seeded_ids": [r["id"] for r in seeded],
        "standing_count": standing,
        "within_count": within,
        "ungradeable_count": unknown,
        # The ledger is what makes a crossing LOUD ONCE. Without it the surface
        # reports the standing stock every run — 70 rows today — which is the
        # desensitised alarm this module's docstring refuses.
        "reported_ids": reported,
    }


# ── the brief block (the ONE author of these words) ────────────────────────

def render_brief_lines(register: dict | None) -> list:
    """Markdown for the CLAUDE.md session brief.

    Lives HERE, not in the renderer, for the reason `constraint_readout` and
    `sunset_pass` already give: the brief and this register must not drift into
    two answers about the same rows.
    """
    if not isinstance(register, dict) or not register.get("schema_version"):
        return ["**🧭 UNROUTED CHECKLIST ROWS — no reading is present.** (Generated from "
                "`docs/claude/work/CHECKLIST-ROUTING-AGE.json` — this line means the file "
                "is absent or unreadable, **NOT that nothing has been left unrouted**. "
                "Regenerate: `python3 scripts/ops/checklist_routing_age.py --write`.)", ""]

    hist = register.get("history_state")
    thr = register.get("threshold_hours", THRESHOLD_HOURS)
    if hist != HIST_DERIVED:
        return [f"**🧭 UNROUTED CHECKLIST ROWS — ⚠️ COULD NOT BE MEASURED** "
                f"(`{register.get('history_note', 'no reason recorded')}`). "
                f"**This is *we did not look*, not *nothing is stalled*** — the ages come "
                f"from the git history of the checklist, and a truncated history reports "
                f"every row as young. Do not read this block as an all-clear.", ""]

    newly = register.get("newly_stalled") or []
    standing = register.get("standing_count", 0)
    within = register.get("within_count", 0)
    ung = register.get("ungradeable_count", 0)
    L: list = []
    if newly:
        L.append(f"**🧭 {len(newly)} FILED ROW(S) CROSSED {thr}h UNROUTED — nobody has been "
                 f"given them, and this is the first time each is being said.** "
                 f"`MI-246`: filing is not routing. Route it, disposition it, or say why "
                 f"it stays unrouted — it will not be reported again.")
        L.append("")
        for r in newly:
            L.append(f"- **`{r['id']}`** — {r.get('title') or '(no title)'}")
            L.append(f"  - unrouted **{r['unrouted_hours']}h** (since `{r.get('unrouted_since')}`) "
                     f"· status `{r.get('status')}` · owner `{r.get('owner')}` "
                     f"· lane `{r.get('lane')}` · tier `{r.get('tier')}`")
        L.append("")
    else:
        L.append(f"**🧭 No filed checklist row crossed {thr}h unrouted since the last "
                 f"reading.** (Generated — an empty list here means no NEW crossing, "
                 f"not that nothing is waiting; see the standing count below.)")
        L.append("")
    if register.get("seeded_count"):
        L.append(f"- ⚠️ **FIRST READING — armed over a pre-existing backlog of "
                 f"**{register['seeded_count']}** row(s) already past {thr}h**, which are "
                 f"listed in `seeded_ids` and are deliberately NOT reported one by one "
                 f"(a {register['seeded_count']}-row block is the desensitised alarm, not a "
                 f"signal). **Seeding is not a disposition** — they are still unrouted.")
        L.append("")
    L.append(f"- Context, deliberately NOT a page: **{standing}** row(s) already reported and "
             f"still unrouted · **{within}** unrouted but inside {thr}h · **{ung}** whose "
             f"status could not be graded (*we did not look* — `MI-237`'s two competing "
             f"status fields). A standing row is a COUNT because reporting all of them "
             f"every run is the desensitised alarm, not a signal.")
    L.append("")
    return L


# ── --measure: re-derive the threshold rather than inheriting it ───────────

def measure(repo: Path) -> dict:
    """The routing-latency distribution, so the threshold stays falsifiable.

    A constant nobody can re-derive becomes folklore. This is how a later
    session checks whether 24h is still the p95, over its own population.
    """
    usable, note = history_is_usable(repo)
    if not usable:
        return {"state": HIST_COULD_NOT_READ, "note": note}
    revs = _revisions(repo)
    first_ou: dict = {}
    routed_at: dict = {}
    for sha, when in revs:
        blob, _ = _run(["git", "-C", str(repo), "show", f"{sha}:{CHECKLIST}"])
        if blob is None:
            continue
        try:
            items = (json.loads(blob) or {}).get("items") or []
        except (ValueError, TypeError):
            continue
        for row in items:
            ident = row.get("id")
            if not ident:
                continue
            if is_unrouted_open(row) is True:
                first_ou.setdefault(ident, when)
            elif routing_state(row) == ROUTE_ROUTED and ident in first_ou:
                routed_at.setdefault(ident, when)
    lat = sorted((routed_at[i] - first_ou[i]).total_seconds() / 3600
                 for i in routed_at if routed_at[i] >= first_ou[i])

    def q(p: float) -> float:
        if not lat:
            return 0.0
        k = (len(lat) - 1) * p
        lo = int(k)
        hi = min(lo + 1, len(lat) - 1)
        return round(lat[lo] + (lat[hi] - lat[lo]) * (k - lo), 2)

    return {
        "state": HIST_DERIVED,
        "note": note,
        "population": f"all {len(revs)} revisions of {CHECKLIST} reachable from HEAD",
        "window": [revs[0][1].isoformat(), revs[-1][1].isoformat()] if revs else None,
        "n_routed_after_being_unrouted": len(lat),
        "hours": {"median": q(.5), "p75": q(.75), "p90": q(.90), "p95": q(.95),
                  "max": round(lat[-1], 2) if lat else 0.0},
        "still_unrouted_now": len(unrouted_since(repo, revs)),
    }


# ── self-test ──────────────────────────────────────────────────────────────

def _self_test() -> int:
    f = []

    def ck(cond, msg):
        if not cond:
            f.append(msg)

    # routing_state
    ck(routing_state({"owner": "session_01ABC"}) == ROUTE_ROUTED, "named session is routed")
    ck(routing_state({"owner": "manager"}) == ROUTE_ROUTED, "manager is routed")
    ck(routing_state({"owner": "unassigned"}) == ROUTE_UNROUTED, "unassigned is unrouted")
    ck(routing_state({"owner": "unassigned — needs a session"}) == ROUTE_UNROUTED,
       "annotated unassigned is unrouted")
    ck(routing_state({"owner": "UNASSIGNED"}) == ROUTE_UNROUTED, "case-insensitive")
    ck(routing_state({}) == ROUTE_ABSENT, "absent owner is its own state")
    ck(routing_state({"owner": "  "}) == ROUTE_ABSENT, "blank owner is absent")
    # ⚠️ absent != unrouted, and neither is routed. Three states, and the guard
    # below asserts nothing collapsed two of them.
    ck(len(set(ROUTING_STATES)) == 3, "three routing states")

    # openness
    ck(openness({"state": "ready"}) == OPEN_OPEN, "ready is open")
    ck(openness({"status": "queued"}) == OPEN_OPEN, "status field is read too")
    ck(openness({"state": "done"}) == OPEN_NOT_OPEN, "done is not open")
    ck(openness({"state": "in_flight"}) == OPEN_NOT_OPEN, "in_flight is MI-236's class")
    ck(openness({"state": "blocked"}) == OPEN_NOT_OPEN, "blocked is MI-235's class")
    ck(openness({"status": "duplicate_of_MI-180"}) == OPEN_NOT_OPEN, "duplicate prefix")
    ck(openness({}) == OPEN_UNGRADEABLE, "no status field at all is ungradeable")
    ck(openness({"state": "wibble"}) == OPEN_UNGRADEABLE,
       "an UNKNOWN status is ungradeable, never a silent default")
    # the two-field disagreement (MI-237): terminal on EITHER reading wins
    ck(openness({"state": "queued", "status": "done"}) == OPEN_NOT_OPEN,
       "a terminal reading on either field is not-open")

    # is_unrouted_open — the tri-state
    ck(is_unrouted_open({"owner": "session_x", "state": "ready"}) is False, "routed -> False")
    ck(is_unrouted_open({"owner": "unassigned", "state": "ready"}) is True, "the class")
    ck(is_unrouted_open({"owner": "unassigned", "state": "done"}) is False, "finished")
    ck(is_unrouted_open({"owner": "unassigned", "state": "wibble"}) is None,
       "ungradeable is None, NOT False — collapsing it hides the drop")

    # grade_stall — every declared state is reachable
    ck(grade_stall(1.0, reported_before=False) == STALL_WITHIN, "young")
    ck(grade_stall(25.0, reported_before=False) == STALL_NEWLY, "crossed, never said")
    ck(grade_stall(25.0, reported_before=True) == STALL_STANDING, "said once already")
    ck(grade_stall(None, reported_before=False) == STALL_UNKNOWN,
       "no derivable age is UNKNOWN, never within")
    ck(grade_stall(24.0, reported_before=False) == STALL_NEWLY, "boundary is inclusive")
    ck({grade_stall(1.0, reported_before=False), grade_stall(25.0, reported_before=False),
        grade_stall(25.0, reported_before=True), grade_stall(None, reported_before=False)}
       == set(STALL_STATES), "all four stall states are producible")

    # LOUD ONCE: a row already in the ledger must not re-page
    ck(grade_stall(500.0, reported_before=True) == STALL_STANDING,
       "a 500h row that was reported stays quiet — the stock is a count, not a page")

    # render_brief_lines branches on every history/stall state
    absent = render_brief_lines(None)
    ck(any("NOT that nothing" in ln for ln in absent),
       "an absent register says so rather than rendering an all-clear")
    unread = render_brief_lines({"schema_version": 1, "history_state": HIST_COULD_NOT_READ,
                                 "history_note": "shallow"})
    ck(any("COULD NOT BE MEASURED" in ln for ln in unread), "could_not_read renders distinctly")
    ck(any("not *nothing is stalled*" in ln for ln in unread),
       "could_not_read must refuse the all-clear reading")
    empty = render_brief_lines({"schema_version": 1, "history_state": HIST_DERIVED,
                                "newly_stalled": [], "standing_count": 7,
                                "within_count": 2, "ungradeable_count": 1,
                                "threshold_hours": 24})
    ck(any("No filed checklist row crossed" in ln for ln in empty), "empty renders")
    ck(any("**7**" in ln for ln in empty), "the standing stock is rendered as a COUNT")
    hit = render_brief_lines({"schema_version": 1, "history_state": HIST_DERIVED,
                              "threshold_hours": 24, "standing_count": 0,
                              "within_count": 0, "ungradeable_count": 0,
                              "newly_stalled": [{"id": "MI-999", "title": "t",
                                                 "owner": "unassigned", "status": "ready",
                                                 "lane": "engineering", "tier": 1,
                                                 "unrouted_hours": 30.2,
                                                 "unrouted_since": "2026-09-10T00:00:00+00:00"}]})
    ck(any("MI-999" in ln for ln in hit), "a crossing names the row")
    ck(any("30.2h" in ln for ln in hit), "and states its age")

    # seeding: a first run arms, it does not page
    seeded_reg = {"schema_version": 1, "history_state": HIST_DERIVED, "threshold_hours": 24,
                  "newly_stalled": [], "seeded_count": 70, "seeded_ids": ["MI-1"],
                  "standing_count": 0, "within_count": 0, "ungradeable_count": 0}
    sl = render_brief_lines(seeded_reg)
    ck(any("FIRST READING" in ln for ln in sl), "a seed run says it armed")
    ck(any("not a disposition" in ln for ln in sl),
       "and says seeding does not dispose of the backlog")
    ck(not any("FIRST READING" in ln for ln in empty), "a normal run does not claim to seed")

    # build() on an unreadable checklist must NOT report zero stalls
    env = build(Path("/nonexistent-repo-for-self-test"), now=datetime.now(timezone.utc))
    ck(env["history_state"] == HIST_COULD_NOT_READ, "missing checklist -> could_not_read")
    ck(env["newly_stalled"] == [], "and an empty list...")
    ck("NOT" in env["what_this_is"] or "never" in env["what_this_is"],
       "...that the envelope explicitly says is not an all-clear")

    # the envelope round-trips through the repo's serialisation
    blob = json.dumps(env, indent=2, ensure_ascii=False)
    ck(json.loads(blob) == env, "envelope round-trips (indent=2, ensure_ascii=False)")

    if f:
        for m in f:
            print(f"::error::checklist-routing-age self-test: {m}")
        print(f"checklist-routing-age: SELF-TEST FAILED ({len(f)} case(s))")
        return 1
    print("checklist-routing-age: self-test OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Age of unrouted manager-checklist rows")
    ap.add_argument("--write", action="store_true", help=f"regenerate {OUT}")
    ap.add_argument("--print", action="store_true", help="print the register to stdout")
    ap.add_argument("--measure", action="store_true",
                    help="re-derive the routing-latency distribution behind THRESHOLD_HOURS")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--repo", default=".", help="repository root")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    repo = Path(a.repo).resolve()
    if a.measure:
        print(json.dumps(measure(repo), indent=2, ensure_ascii=False))
        return 0

    prev: dict = {}
    out_path = repo / OUT
    if out_path.exists():
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # ⚠️ An unreadable ledger must not be treated as an EMPTY one: that
            # would re-page every standing row at once, which is the flood.
            print("::warning::checklist-routing-age: the previous register is unreadable; "
                  "every standing row would re-page. Refusing to rewrite it.", file=sys.stderr)
            return 2

    env = build(repo, previous=prev)
    blob = json.dumps(env, indent=2, ensure_ascii=False) + "\n"
    if a.print or not a.write:
        print(blob, end="")
    if a.write:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(blob, encoding="utf-8")
        print(f"checklist-routing-age: wrote {OUT} — history_state={env['history_state']} "
              f"newly_stalled={len(env['newly_stalled'])} standing={env['standing_count']} "
              f"within={env['within_count']} ungradeable={env['ungradeable_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
