#!/usr/bin/env python3
"""E1 constraint diagnosis + A1 readout — operating-layer Phase D.

**What this is.** E1 walks the typed ``blocked_on`` edges in
``docs/claude/work/objects/*.yaml`` and names the stage of the value chain the
system is held up on. A1 is the four-item readout built on it: where the chain
is held up with its evidence · the book and the money with population and
coverage · what is in flight against the ceiling and what has stopped moving ·
decisions waiting on the operator.

Design: ``docs/design/operating-layer-build-plan-DESIGN.md`` § "Phase D" ·
``docs/design/operating-model-DESIGN.md`` § the value chain.

⚠️ **THE EDGES ARE MOSTLY UNASSESSED, AND THAT IS THE WHOLE DIFFICULTY.**
Every row Phase C migrated carries ``blocked_on: []`` with
``blocked_on_basis: NOT_ASSESSED``, and the store's own README is explicit that
the empty list is **not** the claim that nothing blocks it. So a naive
computation over these edges reports "nothing is blocked" across ~583 objects
with total confidence — a fabricated answer wearing a computed label, which is
the exact class this repo keeps paying for. Three defences, all load-bearing:

1. ``declared_none`` and ``unstated`` are **different states and never
   collapsed.** An empty list with a basis that ASSERTS the assessment is a
   claim; an empty list with ``NOT_ASSESSED`` (or no basis key at all) is
   nobody having looked.
2. **The denominator ships with every finding.** ``assessed_coverage`` says how
   much of the store has an assessed basis at all, and no consumer gets a
   held-up stage without it.
3. **The verdict REFUSES below a declared coverage floor.** Under
   ``MIN_ASSESSED_COVERAGE`` the verdict is ``insufficient_basis`` — not a
   stage. The assessed subgraph is still reported, under its own denominator,
   because *"we looked at 6 objects and here is what they say"* is useful and
   *"the constraint is INTEGRITY"* over a 1% sample is not.

⚠️ **A STAGE HISTOGRAM OVER THIS STORE IS NOT A CONSTRAINT.** ``stage``
describes what each object IS, not where the chain is stuck. Measured
2026-09-01 the store holds INTEGRITY 498 · EVIDENCE 78 · CAPABILITY 8 and
**zero** objects on QUESTION, DECISION, DEPLOYMENT or OBSERVATION — because the
migration's source was three review backlogs, which are registers of defects.
Reading "the constraint is INTEGRITY" off that would be reporting the shape of
the *migration*. ``chain_coverage`` publishes the empty stages explicitly so a
consumer cannot miss them.

**Money is MEASURED or it says it could not look — never zero.** Item 2 reads
``/api/bot/performance`` live and carries ``pnlCoverage`` and ``journalTrust``
verbatim. ``read_state`` is three-way (``read`` · ``unreachable`` ·
``not_attempted``) and the figures are ``None`` when we did not get them; a
zeroed money block would assert a measurement nobody made.

Usage::

    python3 scripts/ops/constraint_readout.py            # print the readout
    python3 scripts/ops/constraint_readout.py --write     # write JSON + MD
    python3 scripts/ops/constraint_readout.py --no-fetch  # skip the live read
    python3 scripts/ops/constraint_readout.py --self-test
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a repo dependency
    yaml = None

_OBJECTS = Path("docs/claude/work/objects")
_INTENTS = Path("docs/claude/work/intents")
_CYCLE_PRIORITY = Path("docs/claude/CYCLE-PRIORITY.json")
_OPERATOR_OWED = Path("docs/claude/operator-owed-register.json")
_OUT_JSON = Path("docs/claude/CONSTRAINT.json")
_OUT_MD = Path("docs/claude/READOUT.md")

#: The value chain, in order. CAPABILITY and INTEGRITY are not chain stages —
#: they exist to unblock and to keep trustworthy — so they are graded apart.
CHAIN_STAGES = ("QUESTION", "EVIDENCE", "DECISION", "DEPLOYMENT", "OBSERVATION")
SUPPORT_STAGES = ("CAPABILITY", "INTEGRITY")
DECLARED_STAGES = CHAIN_STAGES + SUPPORT_STAGES

#: The WIP ceiling. Imported from the guard that ENFORCES it rather than
#: restated, so the readout and CI can never disagree about the number — a
#: second copy of a constant is how two surfaces drift into two answers.
try:  # pragma: no cover - exercised by the self-test
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ci"))
    from check_wip_ceiling import CEILING as WIP_CEILING  # type: ignore
except Exception:  # noqa: BLE001
    WIP_CEILING = None

#: The operator-owed status vocabulary, IMPORTED from the module that grades it
#: rather than restated here, for the same reason as the ceiling above.
#:
#: ⚠️ **THIS IS NOT DEFENSIVE TIDINESS — RE-DERIVING IT PRODUCED A FALSE FINDING
#: IN THIS FILE'S FIRST RUN.** The first draft filtered on ``item["state"]`` and
#: defaulted a missing key to open. The register's field is ``status``, so every
#: item read as open and the readout reported **5 decisions waiting on the
#: operator when all 5 were terminal** (4 ``resolved``, 1 ``withdrawn``) — among
#: them the diag-token rotation the operator CLOSED on 2026-08-30 and which
#: CLAUDE.md says in terms must not be raised again. A readout that invents work
#: for a human is worse than no readout, and it was confident.
try:  # pragma: no cover - exercised by the self-test
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.runtime.operator_owed import (  # type: ignore
        OPEN_STATUSES, TERMINAL_STATUSES,
    )
except Exception:  # noqa: BLE001
    OPEN_STATUSES = TERMINAL_STATUSES = None

#: Below this share of ASSESSED objects the verdict refuses to name a stage.
#:
#: ⚠️ **A CHOSEN VALUE WITH A STATED ARGUMENT, NOT A TUNED ONE.** There is no
#: distribution behind it and there cannot be one yet — the store has been
#: assessed once, at ~1%. The argument for a half: a constraint is a claim about
#: WHERE THE SYSTEM is held up, and a graph in which most objects have never
#: been asked the question cannot support that claim about the whole, however
#: clean the minority is. Set it lower and the readout starts naming stages off
#: a handful of rows; set it at 1.0 and the diagnosis can never turn on, which
#: would make the refusal permanent and therefore ignorable. Raising or lowering
#: it is a real decision and should be argued in the PR that moves it.
MIN_ASSESSED_COVERAGE = 0.50

#: How long an ``in_flight`` / ``waiting`` object may go without a DECLARED
#: movement date before it is reported as stopped. Chosen, not measured.
STALLED_DAYS = 14

_MONEY_URL = "https://ict-bot.duckdns.org/api/bot/performance?window=30d"
_MONEY_TIMEOUT_S = 25


# ---------------------------------------------------------------- loading


def _as_date(v: Any) -> date | None:
    """Parse a date from the several shapes the store actually holds."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        return datetime.fromisoformat(v.strip().replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(v.strip()[:10])
        except ValueError:
            return None


def load_objects(root: Path = _OBJECTS) -> tuple[list[dict], list[dict]]:
    """Return ``(objects, read_errors)``.

    ⚠️ **A FILE THAT FAILS TO PARSE IS REPORTED, NEVER DROPPED.** Otherwise
    *"we could not read the store"* and *"the store is clean"* render
    identically — the consumer half of the ``silent-empty-guard`` defect, and
    the same rule ``/api/bot/work`` already follows with ``readErrors``.
    """
    objects: list[dict] = []
    errors: list[dict] = []
    if yaml is None:
        return objects, [{"path": str(root), "error": "PyYAML is not importable"}]
    for path in sorted(root.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            errors.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
            continue
        if not isinstance(data, dict):
            errors.append({"path": str(path), "error": "not a mapping"})
            continue
        data.setdefault("id", path.stem)
        data["_path"] = str(path)
        objects.append(data)
    return objects, errors


# ------------------------------------------------------------------- E1


def grade_blocked_on(obj: dict) -> tuple[str, list[dict]]:
    """Grade one object's edge basis. Four states, never collapsed.

    - ``blocked``      — at least one typed edge is declared.
    - ``declared_none``— the list is empty AND the basis asserts an assessment
                         was made. This is a CLAIM that nothing blocks it.
    - ``unstated``     — the list is empty and the basis says ``NOT_ASSESSED``,
                         or there is no basis key at all. **Nobody has looked.**
    - ``malformed``    — ``blocked_on`` is present but is not a list.

    ⚠️ ``declared_none`` and ``unstated`` are the whole point. Reading the
    second as the first is how a false *ready* appears, and across this store it
    would turn 578 unexamined rows into 578 confident all-clears.
    """
    raw = obj.get("blocked_on", None)
    if raw is not None and not isinstance(raw, list):
        return "malformed", []
    edges = [e for e in (raw or []) if isinstance(e, dict)]
    if edges:
        return "blocked", edges
    basis = obj.get("blocked_on_basis")
    if not isinstance(basis, str) or not basis.strip():
        # No basis key at all. Nobody said, so we must not say either.
        return "unstated", []
    if basis.strip().upper().startswith("NOT_ASSESSED"):
        return "unstated", []
    return "declared_none", []


def grade_edge(edge: dict, by_id: dict[str, dict]) -> dict:
    """Grade one typed edge, including whether it is still a live hold.

    ``ref_state`` is graded **only** for a ``kind: object`` edge. An
    ``external_event`` / ``operator_decision`` / ``data_accrual`` / ``capability``
    edge names something outside the store and is ``not_in_store_by_design`` —
    it is not a dangling reference, and grading it as one would manufacture a
    defect out of the schema working correctly (the rule ``/api/bot/work``
    already applies to ``refResolvedInStore``).

    ``hold_state`` is the fact a constraint computation actually needs: an edge
    pointing at an object that is ``done`` or ``accepted`` is a **stale**
    blocker and holds nothing.
    """
    kind = edge.get("kind")
    ref = edge.get("ref")
    out = {
        "kind": kind,
        "ref": ref,
        "since": str(_as_date(edge.get("since")) or edge.get("since") or ""),
        "ref_state": None,
        "target_lifecycle": None,
        "hold_state": "unknown",
    }
    if kind != "object":
        out["ref_state"] = "not_in_store_by_design"
        # We cannot see outside the store, so we cannot say the event happened.
        # "unresolvable" is honest; "holding" would assert what we did not check.
        out["hold_state"] = "unverifiable_outside_store"
        return out
    target = by_id.get(str(ref))
    if target is None:
        out["ref_state"] = "dangling"
        out["hold_state"] = "unknown"
        return out
    out["ref_state"] = "resolved"
    life = target.get("lifecycle")
    out["target_lifecycle"] = life
    out["hold_state"] = "stale" if life in ("done", "accepted") else "holding"
    return out


def grade_stage_basis(obj: dict) -> str:
    """Grade HOW an object's ``stage`` was arrived at. Three states, never collapsed.

    - ``bulk_by_source_file`` — the row was migrated, and the migration assigned
      ``stage`` from a **fixed per-SOURCE-FILE table**, not by reading the row.
      The stage therefore says which backlog the row came from and **nothing
      about the work**.
    - ``per_object``          — a stage is stated on a row with no bulk-migration
      provenance, i.e. somebody chose it. ⚠️ This is NOT a claim the stage is
      RIGHT, only that it was assigned rather than derived from a filename.
    - ``unstated``            — no stage at all. We could not look.

    ⚠️ **WHY THIS EXISTS.** ``stage_counts`` reads like a description of where the
    system's work sits. Measured 2026-09-02 over all 584 objects it is not: 576 of
    them carry ``source.backlog``, and their stage is a **deterministic function of
    that one field** — ``health-review-backlog.json`` → INTEGRITY (498),
    ``{ml,performance,research}-review-backlog.json`` → EVIDENCE (78), with **zero
    exceptions**. So ``INTEGRITY 498`` is a census of one filename. Publishing it
    unqualified is UNPROVENANCED DIAGNOSTIC OUTPUT sub-class **B** (an implicit
    input selection wearing the label of a measurement), and the remedy this repo
    declares for sub-class B is to branch on the actual condition rather than
    reword the label — which is what this function does, off the row's own
    ``source`` block rather than off prose about it.

    ⚠️ **AND IT IS WHY THE FOUR EMPTY CHAIN STAGES ARE NOT A READING EITHER.**
    QUESTION / DECISION / DEPLOYMENT / OBSERVATION hold zero objects because **no
    producer has ever emitted one** — the migration's table names two stages and
    the build's own phases name a third. That is a statement about the migration,
    not about the system, and a reader must not conclude the system makes no
    decisions and takes no observations.
    """
    if not isinstance(obj.get("stage"), str) or not obj["stage"].strip():
        return "unstated"
    src = obj.get("source")
    if isinstance(src, dict) and src.get("backlog") and src.get("migrated_on"):
        return "bulk_by_source_file"
    return "per_object"


def diagnose(objects: list[dict], read_errors: list[dict]) -> dict:
    """E1 — the constraint diagnosis.

    Returns a verdict that is never a bare stage name. ``verdict`` is one of
    ``computed`` · ``insufficient_basis`` · ``no_objects`` · ``unreadable``, and
    ``assessed_coverage`` travels with it in every case.
    """
    by_id = {str(o.get("id")): o for o in objects}
    basis_counts = {"blocked": 0, "declared_none": 0, "unstated": 0, "malformed": 0}
    stage_counts = {s: 0 for s in DECLARED_STAGES}
    stage_counts["(unstated)"] = 0
    # Every declared state ships with an explicit zero, so the buckets sum to the
    # population checkably rather than by trust.
    stage_basis_counts: dict[str, int] = {
        "per_object": 0, "bulk_by_source_file": 0, "unstated": 0,
    }
    stage_counts_by_basis: dict[str, dict[str, int]] = {}
    lifecycle_counts: dict[str, int] = {}
    blocked_objects: list[dict] = []
    edge_kinds: dict[str, int] = {}
    hold_states: dict[str, int] = {}

    for obj in objects:
        state, edges = grade_blocked_on(obj)
        basis_counts[state] += 1
        stage = obj.get("stage")
        stage_key = stage if stage in stage_counts else "(unstated)"
        stage_counts[stage_key] += 1
        sbasis = grade_stage_basis(obj)
        stage_basis_counts[sbasis] = stage_basis_counts.get(sbasis, 0) + 1
        stage_counts_by_basis.setdefault(sbasis, {})
        stage_counts_by_basis[sbasis][stage_key] = (
            stage_counts_by_basis[sbasis].get(stage_key, 0) + 1
        )
        life = str(obj.get("lifecycle") or "(unstated)")
        lifecycle_counts[life] = lifecycle_counts.get(life, 0) + 1
        if state != "blocked":
            continue
        graded = [grade_edge(e, by_id) for e in edges]
        for g in graded:
            edge_kinds[str(g["kind"])] = edge_kinds.get(str(g["kind"]), 0) + 1
            hold_states[g["hold_state"]] = hold_states.get(g["hold_state"], 0) + 1
        blocked_objects.append({
            "id": obj.get("id"),
            "stage": stage,
            "lifecycle": obj.get("lifecycle"),
            "title": _one_line(obj.get("title")),
            "edges": graded,
        })

    population = len(objects)
    assessed = basis_counts["blocked"] + basis_counts["declared_none"]
    coverage = (assessed / population) if population else None

    # Which stages does the ASSESSED subgraph say are held up? Reported under
    # its own denominator, and never promoted to "the constraint" on its own.
    held: dict[str, int] = {}
    for b in blocked_objects:
        if any(g["hold_state"] in ("holding", "unverifiable_outside_store") for g in b["edges"]):
            key = str(b["stage"] or "(unstated)")
            held[key] = held.get(key, 0) + 1

    # ⚠️ A `holding` edge whose TARGET is `waiting` is the weakest hold the graph
    # can express, and it is counted separately because `waiting` covers two
    # opposite facts: *not delivered yet* and *delivered, awaiting an
    # observation*. A dependent needs the capability, not the observation — so
    # an object waiting on "a cold session reads this" holds nothing downstream,
    # while one waiting on delivery holds everything. The store cannot tell them
    # apart today, so the count is published as a caveat rather than resolved
    # into a state we did not measure. This is not hypothetical: it is why
    # WO-20260901-PHASE-D reads blocked by WO-20260901-PHASE-C, whose migration
    # landed and whose only open edge is an unobserved cold-session read.
    holds_on_waiting = [
        {"object_id": b["id"], "ref": g["ref"]}
        for b in blocked_objects for g in b["edges"]
        if g["hold_state"] == "holding" and g["target_lifecycle"] == "waiting"
    ]

    if read_errors and not objects:
        verdict, why = "unreadable", "The store could not be read at all."
    elif population == 0:
        verdict, why = "no_objects", "The store holds no work objects."
    elif coverage is not None and coverage < MIN_ASSESSED_COVERAGE:
        verdict = "insufficient_basis"
        why = (
            f"{assessed} of {population} objects ({coverage:.1%}) have an ASSESSED "
            f"`blocked_on` basis, below the declared floor of "
            f"{MIN_ASSESSED_COVERAGE:.0%}. **No stage is named.** "
            f"{basis_counts['unstated']} objects carry an empty `blocked_on` that is "
            f"NOT a claim that nothing blocks them — it is nobody having looked. A "
            f"stage computed over this graph would describe the {assessed} rows "
            f"somebody assessed, not the system."
        )
    else:
        ranked = sorted(held.items(), key=lambda kv: -kv[1])
        if not ranked:
            verdict = "computed"
            why = (
                f"Coverage is {coverage:.1%} ({assessed}/{population}) and **no assessed "
                f"object carries a live hold**. On this graph nothing is held up."
            )
        else:
            verdict = "computed"
            why = (
                f"Coverage is {coverage:.1%} ({assessed}/{population}). The most held-up "
                f"stage is **{ranked[0][0]}** ({ranked[0][1]} object(s) with a live hold)."
            )

    chain_empty = [s for s in CHAIN_STAGES if stage_counts.get(s, 0) == 0]

    return {
        "verdict": verdict,
        "verdict_note": why,
        "named_stage": (
            max(held.items(), key=lambda kv: kv[1])[0]
            if verdict == "computed" and held else None
        ),
        "population": population,
        "assessed": assessed,
        "assessed_coverage": coverage,
        "min_assessed_coverage": MIN_ASSESSED_COVERAGE,
        "basis_counts": basis_counts,
        "stage_counts": stage_counts,
        "stage_basis_counts": stage_basis_counts,
        "stage_counts_by_basis": stage_counts_by_basis,
        "lifecycle_counts": lifecycle_counts,
        "chain_stages_with_no_objects": chain_empty,
        "held_up_candidates": held,
        "holds_on_waiting_targets": holds_on_waiting,
        "edge_kinds": edge_kinds,
        "hold_states": hold_states,
        "blocked_objects": blocked_objects,
        "read_errors": read_errors,
    }


# ------------------------------------------------------------------- A1


def _one_line(v: Any, limit: int = 160) -> str:
    s = " ".join(str(v or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def money_block(fetch: bool = True) -> dict:
    """Item 2 — the book and the money, WITH POPULATION AND COVERAGE.

    ⚠️ **THREE READ STATES, AND THE FIGURES ARE ``None`` WHEN WE DID NOT GET
    THEM.** A zeroed money block asserts a measurement nobody made, and "the
    book is flat" and "we could not look" are opposite statements.

    Everything here is copied from ``/api/bot/performance`` rather than
    re-derived: ``pnlCoverage``/``pnlMeasuredCount`` are MEASURED-only while
    ``totalPnlMeasured`` sums MEASURED+ESTIMATED, deliberately, and the two must
    not be harmonised (CLAUDE.md § the /performance row). ``journalTrust`` is
    carried because a row can be ``measured`` on an account that does not
    reconcile with the venue's wallet at all.
    """
    empty = {
        "read_state": "not_attempted",
        "source": _MONEY_URL,
        "window": None, "totalTrades": None, "winRate": None,
        "totalPnl": None, "totalPnlMeasured": None, "pnlCoverage": None,
        "pnlMeasuredCount": None, "pnlEstimatedCount": None,
        "pnlFabricatedCount": None, "pnlUnverifiedCount": None,
        "journalTrust": None, "error": None,
        "population_note": (
            "Real-money only, closed non-backtest rows inside the window; paper "
            "rides in a separate sub-block on the same route and is never blended."
        ),
    }
    if not fetch:
        return empty
    try:
        with urllib.request.urlopen(_MONEY_URL, timeout=_MONEY_TIMEOUT_S) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        out = dict(empty)
        out["read_state"] = "unreachable"
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    out = dict(empty)
    out["read_state"] = "read"
    for k in ("window", "totalTrades", "winRate", "totalPnl", "totalPnlMeasured",
              "pnlCoverage", "pnlMeasuredCount", "pnlEstimatedCount",
              "pnlFabricatedCount", "pnlUnverifiedCount", "journalTrust"):
        out[k] = payload.get(k)
    # The route returns HTTP 200 with `error` set on a bad window / DB error.
    if payload.get("error"):
        out["read_state"] = "unreachable"
        out["error"] = f"route returned error={payload.get('error')!r}"
    return out


def flight_block(objects: list[dict], today: date) -> dict:
    """Item 3 — what is in flight against the ceiling, and what has stopped.

    ⚠️ **"STOPPED MOVING" IS COMPUTED FROM DECLARED DATES ONLY**, and the block
    says so in ``stalled_basis``. The store records no ``last_moved`` field, and
    a shallow clone cannot supply a per-file git date, so the newest of
    (``opened_at``, every edge's ``since``) is the best available proxy. For a
    migrated row ``opened_at`` is the date the BACKLOG row was filed, not the
    date the work last moved — so an object with no other date is graded
    ``unstated`` rather than counted as stalled. Calling that "stalled" would
    report 500-odd rows as abandoned work when the honest answer is that nobody
    has recorded movement for them.
    """
    in_flight = [o for o in objects if o.get("lifecycle") == "in_flight"]
    waiting = [o for o in objects if o.get("lifecycle") == "waiting"]
    stalled, unstated = [], 0
    for obj in in_flight + waiting:
        dates = [_as_date(obj.get("opened_at"))]
        for e in (obj.get("blocked_on") or []):
            if isinstance(e, dict):
                dates.append(_as_date(e.get("since")))
        known = [d for d in dates if d]
        if not known:
            unstated += 1
            continue
        age = (today - max(known)).days
        if age >= STALLED_DAYS:
            stalled.append({
                "id": obj.get("id"),
                "lifecycle": obj.get("lifecycle"),
                "days_since_declared_movement": age,
                "title": _one_line(obj.get("title")),
            })
    return {
        "ceiling": WIP_CEILING,
        "ceiling_source": (
            "scripts/ci/check_wip_ceiling.py::CEILING (imported, not restated)"
            if WIP_CEILING is not None else "UNREADABLE — the guard could not be imported"
        ),
        "in_flight": len(in_flight),
        "in_flight_ids": [o.get("id") for o in in_flight],
        "waiting": len(waiting),
        "waiting_ids": [o.get("id") for o in waiting],
        "headroom": (WIP_CEILING - len(in_flight)) if WIP_CEILING is not None else None,
        "stalled_days_threshold": STALLED_DAYS,
        "stalled_basis": "declared_dates_only",
        "stalled_basis_note": (
            "Computed from `opened_at` and each edge's `since`. NOT a filesystem "
            "or git observation of when the object last changed."
        ),
        "stalled": sorted(stalled, key=lambda s: -s["days_since_declared_movement"]),
        "movement_date_unstated": unstated,
    }


_EDGE_NOTE = (
    "Zero here does NOT mean no decision is pending — it means no object DECLARES "
    "one, and {unstated} of {pop} objects have never been assessed for edges at all."
)


def operator_block(diag: dict, today: date) -> dict:
    """Item 4 — decisions waiting on the operator.

    Two sources, kept **separate rather than merged**, because they answer
    different questions and neither is a superset of the other: the work store's
    ``operator_decision`` edges say *this work is held by a pending decision*,
    while ``operator-owed-register.json`` is the durable record of anything
    whose next action belongs to a person. An object can be held by a decision
    that was never filed to the register, and the register carries items no
    object points at.
    """
    from_edges = []
    for b in diag["blocked_objects"]:
        for e in b["edges"]:
            if e["kind"] == "operator_decision":
                from_edges.append({
                    "object_id": b["id"], "stage": b["stage"],
                    "ref": e["ref"], "since": e["since"],
                })
    if OPEN_STATUSES is None:
        register: dict[str, Any] = {
            "read_state": "unreadable", "open_items": [], "count": None,
            "terminal_count": None, "unrecognised": [],
            "error": ("src.runtime.operator_owed could not be imported, so the status "
                      "vocabulary is unknown. Grading items without it is what produced "
                      "a false 5-item finding on this file's first run."),
        }
        return {"from_work_store_edges": from_edges,
                "from_work_store_note": _EDGE_NOTE.format(
                    unstated=diag["basis_counts"]["unstated"], pop=diag["population"]),
                "operator_owed_register": register}
    try:
        raw = json.loads(_OPERATOR_OWED.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        register = {"read_state": "unreadable", "open_items": [], "count": None,
                    "terminal_count": None, "unrecognised": [],
                    "error": f"{type(exc).__name__}: {exc}"}
    else:
        items = [i for i in (raw.get("items") or []) if isinstance(i, dict)]
        open_items, terminal, unrecognised = [], [], []
        for i in items:
            status = str(i.get("status") or "").strip().casefold()
            if status in TERMINAL_STATUSES:
                terminal.append(i)
            elif status in OPEN_STATUSES:
                open_items.append(i)
            else:
                # Present but saying nothing recognisable. Reported in its own
                # bucket — bucketing an unknown status as either open or closed
                # is a guess, and this readout is consumed as a work list.
                unrecognised.append({"id": i.get("id"), "status": i.get("status")})
        register = {
            "read_state": "read",
            "count": len(open_items),
            "terminal_count": len(terminal),
            "unrecognised": unrecognised,
            "carry_limit": raw.get("carry_limit"),
            "status_vocabulary_source": "src.runtime.operator_owed (imported)",
            "open_items": [{
                "id": i.get("id"),
                "status": i.get("status"),
                "opened_at": i.get("opened_at"),
                "severity": i.get("severity"),
                "owner_class": i.get("owner_class"),
                "age_days": ((today - d).days if (d := _as_date(i.get("opened_at"))) else None),
                "title": _one_line(i.get("title")),
            } for i in open_items],
        }
    return {
        "from_work_store_edges": from_edges,
        "from_work_store_note": _EDGE_NOTE.format(
            unstated=diag["basis_counts"]["unstated"], pop=diag["population"]),
        "operator_owed_register": register,
    }


def build(fetch_money: bool = True, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    objects, errors = load_objects()
    diag = diagnose(objects, errors)
    try:
        priority = json.loads(_CYCLE_PRIORITY.read_text(encoding="utf-8")).get("current")
    except (OSError, json.JSONDecodeError):
        priority = None
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": "scripts/ops/constraint_readout.py",
        "cycle_priority": {
            "cycle_id": (priority or {}).get("cycle_id"),
            "basis": (priority or {}).get("basis"),
            "set_by": (priority or {}).get("set_by"),
            "present": bool(priority),
        },
        "constraint": diag,
        "money": money_block(fetch=fetch_money),
        "flight": flight_block(objects, today),
        "operator": operator_block(diag, today),
    }


# ---------------------------------------------------------------- render


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.1%}"


def render_md(d: dict) -> str:
    c, m, f, o = d["constraint"], d["money"], d["flight"], d["operator"]
    L = ["# The readout — where the chain is held up, and what that costs", ""]
    L.append(f"_Generated {d['generated_at']} by `{d['generator']}` · "
             f"cycle `{d['cycle_priority']['cycle_id'] or '(none set)'}` "
             f"(basis {d['cycle_priority']['basis'] or 'unstated'})_")
    L.append("")
    L.append("> **This is A1, and it is computed rather than judged.** It reports its "
             "denominator before its conclusion, because a constraint named over "
             "unassessed edges is a fabricated answer wearing a computed label.")
    L.append("")

    # ---- 1
    L.append("## 1 · Where the chain is held up")
    L.append("")
    L.append(f"**Verdict: `{c['verdict']}`**"
             + (f" — held-up stage **{c['named_stage']}**" if c["named_stage"] else ""))
    L.append("")
    L.append(c["verdict_note"])
    L.append("")
    L.append("| population | assessed | coverage | floor |")
    L.append("|---|---|---|---|")
    L.append(f"| {c['population']} objects | {c['assessed']} | "
             f"**{_pct(c['assessed_coverage'])}** | {_pct(c['min_assessed_coverage'])} |")
    L.append("")
    b = c["basis_counts"]
    L.append(f"**Edge basis, never collapsed** — `blocked` {b['blocked']} · "
             f"`declared_none` {b['declared_none']} · **`unstated` {b['unstated']}** · "
             f"`malformed` {b['malformed']}.")
    L.append("")
    L.append("⚠️ `unstated` is an empty `blocked_on` whose basis says `NOT_ASSESSED` "
             "(or which carries no basis at all). It is **nobody having looked**, not a "
             "claim that nothing blocks the object. Reading the second as the first is "
             "how a false *ready* appears.")
    L.append("")
    if c["chain_stages_with_no_objects"]:
        L.append("⚠️ **Chain coverage is PARTIAL: "
                 + ", ".join(f"`{s}`" for s in c["chain_stages_with_no_objects"])
                 + " hold ZERO objects.** The store cannot locate a hold-up on a stage "
                 "it has no objects for, so a stage histogram over this store describes "
                 "what got migrated (review-backlog defect rows), not where the chain is "
                 "stuck.")
        L.append("")
    L.append("Objects by stage: "
             + " · ".join(f"`{k}` {v}" for k, v in c["stage_counts"].items() if v))
    L.append("")
    sb = c.get("stage_basis_counts") or {}
    bulk, per_obj = sb.get("bulk_by_source_file", 0), sb.get("per_object", 0)
    if bulk:
        L.append(f"⚠️ **{bulk} of {c['population']} of those stages were assigned in BULK "
                 f"FROM THE SOURCE FILENAME, not by reading the row.** The Phase C "
                 f"migration maps `health-review-backlog.json` → `INTEGRITY` and "
                 f"`{{ml,performance,research}}-review-backlog.json` → `EVIDENCE`, with no "
                 f"per-row judgement, so `INTEGRITY {c['stage_counts'].get('INTEGRITY', 0)}` "
                 f"is a census of ONE filename. Only **{per_obj}** stage(s) in the whole "
                 f"store were chosen per object — and choosing one is not a claim it is "
                 f"RIGHT, only that a filename did not decide it.")
        L.append("")
        L.append("Stage by how the stage was arrived at: "
                 + " · ".join(
                     f"`{basis}` → " + ", ".join(f"{st} {n}" for st, n in sorted(counts.items()))
                     for basis, counts in sorted((c.get("stage_counts_by_basis") or {}).items())))
        L.append("")
    if c["blocked_objects"]:
        L.append(f"**The assessed subgraph — every object that declares an edge "
                 f"({len(c['blocked_objects'])} of {c['population']}):**")
        L.append("")
        for bo in c["blocked_objects"]:
            L.append(f"- **`{bo['id']}`** ({bo['stage']} · {bo['lifecycle']}) — {bo['title']}")
            for e in bo["edges"]:
                extra = f" → `{e['target_lifecycle']}`" if e["target_lifecycle"] else ""
                L.append(f"  - `{e['kind']}` → `{e['ref']}`{extra} · ref `{e['ref_state']}` "
                         f"· hold **`{e['hold_state']}`** · since {e['since'] or '—'}")
        L.append("")
    if c["holds_on_waiting_targets"]:
        L.append(f"⚠️ **{len(c['holds_on_waiting_targets'])} of the live `object` holds point "
                 "at a target whose lifecycle is `waiting`, and that is the weakest hold "
                 "the graph can express.** `waiting` covers two opposite facts — *not "
                 "delivered yet* and *delivered, awaiting an observation* — and a dependent "
                 "needs the capability, not the observation. The store cannot tell them "
                 "apart, so this is published as a caveat rather than resolved into a state "
                 "nobody measured. Check the target before treating one of these as a real "
                 "blocker:")
        for h in c["holds_on_waiting_targets"]:
            L.append(f"  - `{h['object_id']}` → `{h['ref']}`")
        L.append("")
    if c["read_errors"]:
        L.append(f"⚠️ **{len(c['read_errors'])} file(s) could not be read** — reported, "
                 "never dropped, because *we could not read the store* and *the store is "
                 "clean* must not render identically:")
        for e in c["read_errors"]:
            L.append(f"  - `{e['path']}` — {e['error']}")
        L.append("")

    # ---- 2
    L.append("## 2 · The book and the money")
    L.append("")
    L.append(f"**Read state: `{m['read_state']}`** · source `{m['source']}`")
    L.append("")
    if m["read_state"] != "read":
        L.append("⚠️ **The figures are `null`, not `0`.** We did not obtain them; the book "
                 "is not being reported as flat."
                 + (f" ({m['error']})" if m.get("error") else ""))
    else:
        L.append(f"Population: **{m['population_note']}** Window `{m['window']}`.")
        L.append("")
        L.append("| trades | win rate | totalPnl | totalPnlMeasured | pnlCoverage |")
        L.append("|---|---|---|---|---|")
        L.append(f"| {m['totalTrades']} | {m['winRate']}% | {m['totalPnl']} | "
                 f"{m['totalPnlMeasured']} | **{_pct(m['pnlCoverage'])}** |")
        L.append("")
        L.append(f"Provenance split — measured {m['pnlMeasuredCount']} · estimated "
                 f"{m['pnlEstimatedCount']} · fabricated {m['pnlFabricatedCount']} · "
                 f"unverified {m['pnlUnverifiedCount']}.")
        L.append("")
        L.append("⚠️ **The count and the sum are over DIFFERENT populations, deliberately** "
                 "— `pnlCoverage`/`pnlMeasuredCount` are MEASURED-only, `totalPnlMeasured` "
                 "sums MEASURED+ESTIMATED. Neither may be harmonised to the other.")
        jt = m.get("journalTrust") or {}
        if jt:
            L.append("")
            div = jt.get("accountsKnownDivergent") or []
            L.append(f"`journalTrust` — readState `{jt.get('readState')}` · "
                     f"known-divergent {div or '[]'} · "
                     f"unrecorded {jt.get('accountsUnrecorded')} · "
                     f"unreadable {jt.get('accountsUnreadable')}.")
            if div:
                L.append(f"  - ⚠️ **{', '.join(div)} does not reconcile with the venue's "
                         "wallet.** A row can be `measured` on an account that does not "
                         "reconcile at all — coverage and trust are different questions.")
            L.append("  - ⚠️ `accountsUnrecorded` is **not** `accountsTrusted`: the ledger "
                     "is populated by hand, so an absent record means nobody reconciled "
                     "that account.")
    L.append("")

    # ---- 3
    L.append("## 3 · In flight against the ceiling, and what has stopped moving")
    L.append("")
    L.append(f"**{f['in_flight']} in flight against a ceiling of "
             f"{f['ceiling'] if f['ceiling'] is not None else '(unreadable)'}** "
             f"(headroom {f['headroom'] if f['headroom'] is not None else '—'}) · "
             f"{f['waiting']} waiting.")
    L.append("")
    L.append(f"Ceiling source: {f['ceiling_source']}. `waiting` is deliberately free of the "
             "ceiling — a thing blocked on an operator decision is not consuming the "
             "attention the ceiling rations.")
    L.append("")
    if f["in_flight_ids"]:
        L.append("In flight: " + " · ".join(f"`{i}`" for i in f["in_flight_ids"]))
        L.append("")
    if f["waiting_ids"]:
        L.append("Waiting: " + " · ".join(f"`{i}`" for i in f["waiting_ids"]))
        L.append("")
    if f["stalled"]:
        L.append(f"**Stopped moving** (no declared movement in ≥{f['stalled_days_threshold']}d):")
        for s in f["stalled"]:
            L.append(f"- `{s['id']}` ({s['lifecycle']}) — {s['days_since_declared_movement']}d "
                     f"— {s['title']}")
    else:
        L.append(f"**Nothing in flight or waiting has been still for "
                 f"≥{f['stalled_days_threshold']}d** on declared dates.")
    L.append("")
    L.append(f"⚠️ **Basis `{f['stalled_basis']}`.** {f['stalled_basis_note']} "
             + (f"**{f['movement_date_unstated']} of these objects carry no usable movement "
                "date at all** and are counted as *unstated*, never as stalled — silence "
                "about movement is not evidence of stillness."
                if f["movement_date_unstated"]
                else "Every object counted here carries at least one usable date."))
    L.append("")

    # ---- 4
    L.append("## 4 · Decisions waiting on the operator")
    L.append("")
    fe = o["from_work_store_edges"]
    L.append(f"**From the work store: {len(fe)} `operator_decision` edge(s).**")
    if fe:
        for e in fe:
            L.append(f"- `{e['object_id']}` ({e['stage']}) → {e['ref']} · since {e['since']}")
    L.append("")
    L.append(f"⚠️ {o['from_work_store_note']}")
    L.append("")
    reg = o["operator_owed_register"]
    L.append(f"**From `docs/claude/operator-owed-register.json`: read state "
             f"`{reg['read_state']}`, {reg['count'] if reg['count'] is not None else '—'} "
             f"OPEN item(s)** (carry limit {reg.get('carry_limit', '—')}; "
             f"{reg.get('terminal_count')} terminal, not listed).")
    if reg.get("error"):
        L.append("")
        L.append(f"⚠️ {reg['error']}")
    L.append("")
    for i in reg["open_items"]:
        L.append(f"- `{i['id']}` (`{i['status']}` · {i['severity']} · {i['owner_class']} · "
                 f"{i['age_days'] if i['age_days'] is not None else '?'}d) — {i['title']}")
    if not reg["open_items"] and reg["read_state"] == "read":
        L.append("- _(none open — every item carries a terminal `status`)_")
    if reg.get("unrecognised"):
        L.append("")
        L.append("⚠️ **Unrecognised `status` values — bucketed as NEITHER open nor "
                 "terminal**, because guessing which is what a work list must not do:")
        for u in reg["unrecognised"]:
            L.append(f"  - `{u['id']}` — status `{u['status']}`")
    L.append("")
    L.append(f"Status vocabulary: {reg.get('status_vocabulary_source', '(unavailable)')}. "
             "⚠️ Re-deriving it is not a hypothetical risk — this file's first run keyed on "
             "a field the register does not have (`state`, not `status`) and reported all "
             "5 terminal items as open, one of them a question the operator had closed.")
    L.append("")
    L.append("The two sources are kept **separate rather than merged** — one says *this "
             "work is held by a pending decision*, the other is the durable record of "
             "anything whose next action belongs to a person. Neither is a superset.")
    L.append("")
    return "\n".join(L)


def render_brief_lines(d: dict) -> list[str]:
    """The compact form the CLAUDE.md session brief carries under the priority.

    Kept to a handful of lines on purpose: the brief must SHRINK as work lands,
    and a readout that grew into a wall would be one more thing to skim past —
    the failure mode of the due-list it sits beside.
    """
    c, f = d["constraint"], d["flight"]
    L = [f"**📉 THE COMPUTED READOUT BEHIND THAT PRIORITY** "
         f"(`docs/claude/READOUT.md`, from `scripts/ops/constraint_readout.py`, "
         f"generated `{str(d.get('generated_at') or 'unknown')[:10]}` — **it is a dated "
         f"snapshot, not a live read**; re-run the script rather than trusting its age)", ""]
    if c["verdict"] == "computed" and c["named_stage"]:
        L.append(f"- **Held-up stage: `{c['named_stage']}`** — computed over "
                 f"{c['assessed']}/{c['population']} assessed objects "
                 f"({_pct(c['assessed_coverage'])} coverage).")
    else:
        L.append(f"- **No stage is named — verdict `{c['verdict']}`.** Only "
                 f"{c['assessed']} of {c['population']} objects "
                 f"({_pct(c['assessed_coverage'])}) have an ASSESSED `blocked_on` basis, "
                 f"below the {_pct(c['min_assessed_coverage'])} floor. "
                 f"**{c['basis_counts']['unstated']} objects carry an empty `blocked_on` "
                 f"that is NOT a claim that nothing blocks them** — it is nobody having "
                 f"looked. Do not read this as *nothing is blocked*.")
    if c["chain_stages_with_no_objects"]:
        L.append("- ⚠️ **Chain coverage is partial:** "
                 + ", ".join(f"`{s}`" for s in c["chain_stages_with_no_objects"])
                 + " hold **zero** objects, so the store cannot locate a hold-up there. "
                   "A stage histogram over it describes what got migrated, not the chain.")
    sb = c.get("stage_basis_counts") or {}
    if sb.get("bulk_by_source_file"):
        L.append(f"- ⚠️ **And the stages that ARE populated were assigned from the source "
                 f"FILENAME in bulk** ({sb['bulk_by_source_file']} of {c['population']}; only "
                 f"{sb.get('per_object', 0)} were chosen per object), so the histogram is a "
                 f"census of which backlog a row came from — **not a reading of the work.**")
    L.append(f"- **{f['in_flight']} in flight** against a ceiling of "
             f"{f['ceiling'] if f['ceiling'] is not None else '(unreadable)'} · "
             f"{f['waiting']} waiting · {len(f['stalled'])} stopped moving "
             f"(≥{f['stalled_days_threshold']}d, declared dates only).")
    L.append("- **If you are about to write a real `blocked_on` edge, that is the single "
             "highest-value thing you can do to this store** — the diagnosis is refusing "
             "for want of assessed edges, not for want of machinery.")
    L.append("")
    return L


# ------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true",
                    help=f"write {_OUT_JSON} and {_OUT_MD}")
    ap.add_argument("--no-fetch", action="store_true",
                    help="skip the live /api/bot/performance read (money reports "
                         "`not_attempted`, never zeros)")
    ap.add_argument("--json", action="store_true", help="print the JSON envelope")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    d = build(fetch_money=not args.no_fetch)
    md = render_md(d)
    if args.write:
        _OUT_JSON.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
        _OUT_MD.write_text(md + "\n", encoding="utf-8")
        print(f"wrote {_OUT_JSON} and {_OUT_MD}")
    if args.json:
        print(json.dumps(d, indent=2, ensure_ascii=False))
    elif not args.write:
        print(md)
    return 0


def _self_test() -> int:
    """Assert the distinctions this module exists to keep apart."""
    failures: list[str] = []

    def check(label: str, got: Any, want: Any) -> None:
        ok = got == want
        print(f"  {'PASS' if ok else 'FAIL'} — {label}"
              + ("" if ok else f" (got {got!r}, want {want!r})"))
        if not ok:
            failures.append(label)

    print("constraint_readout self-test")

    # The four edge-basis states, which are the whole point.
    check("NOT_ASSESSED empty list grades `unstated`, NEVER `declared_none`",
          grade_blocked_on({"blocked_on": [], "blocked_on_basis":
                            "NOT_ASSESSED — migrated in bulk"})[0], "unstated")
    check("an absent basis key ALSO grades `unstated` (nobody said)",
          grade_blocked_on({"blocked_on": []})[0], "unstated")
    check("an empty list with a real assessment grades `declared_none`",
          grade_blocked_on({"blocked_on": [], "blocked_on_basis":
                            "ASSESSED 2026-09-01 — nothing blocks this."})[0],
          "declared_none")
    check("an object with an edge grades `blocked`",
          grade_blocked_on({"blocked_on": [{"kind": "object", "ref": "X"}]})[0], "blocked")
    check("a non-list blocked_on grades `malformed`",
          grade_blocked_on({"blocked_on": "soon"})[0], "malformed")

    # Edge grading: a done target holds nothing; a non-object ref is not dangling.
    by_id = {"A": {"id": "A", "lifecycle": "done"}, "B": {"id": "B", "lifecycle": "waiting"}}
    check("an edge to a `done` object is a STALE blocker, not a live hold",
          grade_edge({"kind": "object", "ref": "A"}, by_id)["hold_state"], "stale")
    check("an edge to a `waiting` object IS holding",
          grade_edge({"kind": "object", "ref": "B"}, by_id)["hold_state"], "holding")
    check("an unresolvable object ref is `dangling`",
          grade_edge({"kind": "object", "ref": "ZZZ"}, by_id)["ref_state"], "dangling")
    check("an external_event ref is NOT graded dangling",
          grade_edge({"kind": "external_event", "ref": "a cold session"}, by_id)["ref_state"],
          "not_in_store_by_design")

    # ---- stage_basis: the histogram must declare how its stages were arrived at.
    check("a migrated row's stage grades `bulk_by_source_file`",
          grade_stage_basis({"stage": "INTEGRITY", "source": {
              "backlog": "docs/claude/health-review-backlog.json",
              "migrated_on": "2026-09-01"}}),
          "bulk_by_source_file")
    check("a hand-authored row's stage grades `per_object`",
          grade_stage_basis({"stage": "DECISION"}), "per_object")
    check("...and a row with a source block but NO migrated_on is NOT bulk",
          grade_stage_basis({"stage": "DECISION", "source": {"backlog": "x"}}),
          "per_object")
    check("no stage at all grades `unstated`, never a stage",
          grade_stage_basis({"source": {"backlog": "x", "migrated_on": "y"}}),
          "unstated")
    _sb = diagnose([
        {"id": "M1", "stage": "INTEGRITY", "lifecycle": "dormant", "blocked_on": [],
         "blocked_on_basis": "NOT_ASSESSED",
         "source": {"backlog": "b", "migrated_on": "2026-09-01"}},
        {"id": "H1", "stage": "DECISION", "lifecycle": "waiting", "blocked_on": [],
         "blocked_on_basis": "NOT_ASSESSED"},
    ], [])["stage_basis_counts"]
    check("the basis buckets sum to the population, checkably",
          sum(_sb.values()), 2)
    check("...with every declared state present as an explicit zero",
          sorted(_sb), ["bulk_by_source_file", "per_object", "unstated"])

    # The refusal. A store of unassessed rows must NOT yield a stage.
    unassessed = [{"id": f"O{i}", "stage": "INTEGRITY", "lifecycle": "dormant",
                   "blocked_on": [], "blocked_on_basis": "NOT_ASSESSED — bulk"}
                  for i in range(100)]
    d = diagnose(unassessed, [])
    check("100 unassessed objects yield verdict `insufficient_basis`",
          d["verdict"], "insufficient_basis")
    check("...and NAME NO STAGE", d["named_stage"], None)
    check("...with coverage 0.0 reported, not hidden", d["assessed_coverage"], 0.0)
    check("...and the empty chain stages are published",
          set(d["chain_stages_with_no_objects"]),
          {"QUESTION", "EVIDENCE", "DECISION", "DEPLOYMENT", "OBSERVATION"})

    # ...and the positive control: assessed rows DO produce a stage.
    assessed = [{"id": f"P{i}", "stage": "DECISION", "lifecycle": "ready",
                 "blocked_on": [{"kind": "operator_decision", "ref": "pick one"}],
                 "blocked_on_basis": "ASSESSED"} for i in range(6)] + \
               [{"id": f"Q{i}", "stage": "EVIDENCE", "lifecycle": "ready",
                 "blocked_on": [], "blocked_on_basis": "ASSESSED — nothing blocks"}
                for i in range(4)]
    d2 = diagnose(assessed, [])
    check("POSITIVE CONTROL: an assessed store DOES compute", d2["verdict"], "computed")
    check("...and names the stage carrying the live holds", d2["named_stage"], "DECISION")
    check("...at 100% coverage", d2["assessed_coverage"], 1.0)

    # Money never fabricates a zero.
    mb = money_block(fetch=False)
    check("an unfetched money block reports `not_attempted`", mb["read_state"], "not_attempted")
    check("...with pnlCoverage None, NEVER 0.0", mb["pnlCoverage"], None)
    check("...and totalPnl None, NEVER 0.0", mb["totalPnl"], None)

    # The operator-owed status vocabulary — the defect this file already made.
    check("the operator-owed vocabulary is IMPORTED, not re-derived",
          (OPEN_STATUSES, TERMINAL_STATUSES),
          (("open", "dispatched", "snoozed"), ("resolved", "withdrawn")))
    ob = operator_block({"blocked_objects": [], "basis_counts": {"unstated": 0},
                         "population": 0}, date(2026, 9, 1))
    reg = ob["operator_owed_register"]
    check("REGRESSION: a register of terminal items reports ZERO open, not all of them",
          reg["count"], 0)
    check("...and the terminal ones are counted, not silently dropped",
          reg["terminal_count"] is not None and reg["terminal_count"] > 0, True)
    check("...and nothing is bucketed as unrecognised on the real register",
          reg["unrecognised"], [])

    # A parse failure is reported, never dropped.
    d3 = diagnose([], [{"path": "x.yaml", "error": "boom"}])
    check("an unreadable store verdicts `unreadable`, not `no_objects`",
          d3["verdict"], "unreadable")

    # The ceiling is imported, not restated.
    check("the WIP ceiling is imported from the enforcing guard", WIP_CEILING, 8)

    print(f"\n{'ALL PASS' if not failures else str(len(failures)) + ' FAILURE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
