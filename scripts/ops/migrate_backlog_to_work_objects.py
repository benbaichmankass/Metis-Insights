#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::wip-ceiling-guard (--self-test) + manual --write
"""Migrate the CARRIED review-backlog rows into ``docs/claude/work/`` as work objects.

Phase C of the operating-layer build. This is the half that fills the store;
``scripts/ci/check_wip_ceiling.py`` is the half that keeps it from reading as
hundreds of things in flight. **They ship together, deliberately** — see the
build plan's Phase C: rows arriving unbounded would render as ~575 things in
flight, which is precisely the condition the redesign exists to end.

⚠️ **CARRYING EVERYTHING IS NOT THE SAME AS EVERYTHING BEING OPEN.** The
registry may hold hundreds of objects while at most 8 are being worked. Every
row this script writes arrives ``lifecycle: dormant``. A row becomes
``in_flight`` only when a human or a session gives it three things it does not
get here: an OWNER, a real dependency EDGE, and a place under an INTENT.

WHAT THIS DOES NOT DO
---------------------
**It does not modify the backlogs.** It reads them. ``health-review-backlog.json``
is 1,062 rows / ~5 MB, and a naive read-append-write buries a one-row change in a
30,000-line diff that re-attributes every pre-existing row to the author
(``BL-20260820-BACKLOG-APPEND-REFORMATS-AND-REATTRIBUTES``). That is the hazard
``scripts/ops/backlog_append.py`` exists for — it reproduces the file's exact
serialisation or refuses to write — and the reason nothing here opens a backlog
for writing.

⚠️ **Measured 2026-09-01, because the stronger version of this claim is wrong and
is repeated in several places:** ``health-review-backlog.json`` DOES round-trip,
at ``indent=2, ensure_ascii=False``. It is ``docs/claude/OPEN-ITEMS.json`` that
round-trips at NO ``indent``/``ensure_ascii`` combination — it mixes backslash-u
escapes with literal non-ASCII in the same file. Both must be edited carefully;
only the second is unreproducible. Saying "the backlog cannot be serialised" when
it can is the kind of inherited almost-true claim that stops a future session
using the safe tool that already works.

**It does not cap the register.** ``scripts/ci/check_open_items.py`` keeps
``MAX_ITEMS = None`` and that stays. The REGISTER is uncapped; the IN-FLIGHT SET
is capped. Different populations — conflating them re-introduces the eviction
rule the operator reversed on 2026-08-26, which told sessions to delete
knowledge in order to satisfy a rule nothing enforced.

**It does not migrate ``OPEN-ITEMS.json``.** That register is what a session must
KNOW before it plans, which ``docs/claude/work/README.md`` names as a different
thing from what is being WORKED. Migrating it would give one fact two homes.

THE MAPPINGS, AND THEIR BASIS
-----------------------------
Every mapping below is **DECIDED**, uniform, and derived from a field the source
row already carries — never inferred per-row from prose, which would be
fabrication at 575x.

``lifecycle``  → always ``dormant``. Two reasons, and the second is mechanical:
    1. Nothing is started. ``dormant`` is the honest state.
    2. ``scripts/ops/work_phase_ping.py`` treats ``accepted`` as PING-WORTHY and
       ``dormant``/``ready`` as not. Mapping the 238 ``kept_open`` rows to
       ``accepted`` would queue 238 operator notifications for a bulk migration.
       The notification contract is *events on STATE CHANGES, never on activity*
       — and a migration is activity. So the design's "dormant or accepted"
       resolves to dormant for everything here, and ``accepted`` is left for
       rows a human actually accepts.

``type``       → ``commitment`` when the source row carries a non-empty
    ``next_action`` (we have said what we will do), else ``question`` (we have
    not). Read off the row's own field, not guessed from prose.

``stage``      → fixed per SOURCE BACKLOG, not per row:
    health → INTEGRITY · performance/ml/research → EVIDENCE.
    A per-row stage would require reading 575 prose bodies and deciding; this is
    uniform and auditable, and a wrong-but-consistent stage is correctable in one
    pass whereas 575 individual guesses are not.

``blocked_on`` → ``[]`` **with ``blocked_on_basis: NOT_ASSESSED`` stating so.**
    ⚠️ THIS IS THE POINT OF THE FIELD. ``docs/claude/work/README.md``: *"An empty
    ``blocked_on`` is a claim that nothing blocks this, not an absence of
    information; if it is unknown, say so in the row rather than leaving it
    empty."* No edge is derived here, so none is claimed. The alternative —
    inventing an edge per row — is the exact defect this phase was dispatched
    with a correction for: on 2026-09-01 three edges were written as
    ``object: WO-PHASE-A`` when the real dependency was "the store exists", and
    the graph reported two phases blocked that were actually available. **A FALSE
    BLOCKER IS WORSE THAN A MISSING ONE** — it is what Phase D's constraint
    computation reads, and it would say "we are waiting" while work was free.

``id``         → the SOURCE ROW ID, verbatim. Measured 2026-09-01: 1,291 of 1,291
    ids across the four backlogs are unique and filesystem-safe. Minting a second
    identifier for one thing is how a round-trip stops being mechanical.

ABSENCES ARE RECORDED, NOT FILLED
---------------------------------
Measured on the carried population (575 rows): 39 have no ``title``, 154 have no
``opened_at``, and 51 have no ``resolution_criteria``. A missing done-condition
is written as an explicit ⚠️ rather than an empty string, because an object that
cannot say what would end it must not be allowed to leave ``dormant`` quietly.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
OBJECTS_DIR = REPO_ROOT / "docs" / "claude" / "work" / "objects"

#: A row is CARRIED when its status is not a closed one. Stated as the open set
#: rather than the closed set: a new status nobody told us about should show up
#: as "not carried" and be noticed, not be silently swept in as carried.
CARRIED_STATUSES = {"open", "kept_open"}

#: source file -> (stage, short name used in provenance)
SOURCES: List[tuple[str, str]] = [
    ("docs/claude/health-review-backlog.json", "INTEGRITY"),
    ("docs/claude/ml-review-backlog.json", "EVIDENCE"),
    ("docs/claude/performance-review-backlog.json", "EVIDENCE"),
    ("docs/claude/research-review-backlog.json", "EVIDENCE"),
]

_NO_DONE_CONDITION = (
    "⚠️ UNKNOWN — the source backlog row states no resolution_criteria. This "
    "object cannot leave `dormant` until someone writes what would end it. An "
    "object with no done-condition is not a small gap: it is a thing that can "
    "never be finished, only abandoned."
)

_REVIEW_TRIGGER = (
    "Re-read when this is pulled under an intent, or when a session working a "
    "related area finds it. A dormant object is NOT a queued one — nothing is "
    "scheduled to pick this up, and that is the honest state rather than a "
    "backlog promise nobody is keeping."
)

_BLOCKED_ON_BASIS = (
    "NOT_ASSESSED — migrated in bulk 2026-09-01; no dependency edge was derived "
    "for this row and none is claimed. The empty list above is therefore NOT the "
    "claim 'nothing blocks this'. ⚠️ Write a real edge before moving this out of "
    "`dormant`: an invented edge would be read by the constraint computation as "
    "a true blocker, and a false blocker is worse than a missing one."
)


def _text(v: Any) -> str:
    """A field's value as clean text, or '' — never the string 'None'."""
    if v is None:
        return ""
    return str(v).strip()


def carried_rows(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The not-closed rows of one backlog document."""
    items = doc.get("items") if isinstance(doc, dict) else doc
    return [r for r in (items or []) if _text(r.get("status")) in CARRIED_STATUSES]


def build_object(row: Dict[str, Any], *, source_path: str, stage: str,
                 migrated_on: str) -> Dict[str, Any]:
    """One work object from one backlog row. Pure — no I/O, so it is testable."""
    next_action = _text(row.get("next_action"))
    criteria = _text(row.get("resolution_criteria"))
    title = _text(row.get("title"))
    opened_at = _text(row.get("opened_at"))

    obj: Dict[str, Any] = {
        "id": _text(row.get("id")),
        "type": "commitment" if next_action else "question",
        # ⚠️ Deliberately null. "A row becomes in_flight only when given an owner,
        # a dependency edge AND A PLACE UNDER AN INTENT" — inventing a parent here
        # would satisfy a third of that test on paper and none of it in fact.
        "parent_intent": None,
        "title": title or f"⚠️ UNTITLED in the source backlog — see row {_text(row.get('id'))}",
        "stage": stage,
        "lifecycle": "dormant",
        # Nobody is working this. `owner: claude` on 575 rows would be a lie that
        # the WIP ceiling would then have to be argued around.
        "owner": None,
        "opened_at": opened_at or None,
        "closed_at": None,
        "review_trigger": _REVIEW_TRIGGER,
        "done_condition": criteria or _NO_DONE_CONDITION,
        "blocked_on": [],
        "blocked_on_basis": _BLOCKED_ON_BASIS,
        "source": {
            "backlog": source_path,
            "row_id": _text(row.get("id")),
            "status_at_migration": _text(row.get("status")),
            "severity": _text(row.get("severity")) or None,
            "tier": _text(row.get("tier")) or None,
            "opened_by": _text(row.get("opened_by")) or None,
            "migrated_on": migrated_on,
            "note": (
                "The backlog row remains the state of record for the FINDING and "
                "its updates; this object is the state of record for the WORK of "
                "dealing with it. New findings are still filed to the backlog "
                "through scripts/ops/backlog_append.py::append_row, never here."
            ),
        },
        "evidence": [f"{source_path}#{_text(row.get('id'))}"],
        "verdict": None,
    }
    if next_action:
        obj["next_action_at_migration"] = next_action
    return obj


# --- YAML emission -----------------------------------------------------------
# Long/multiline prose is emitted as a LITERAL block (`|-`), which keeps every
# continuation line INDENTED. That matters beyond looks: work_phase_ping.py reads
# fields with a column-0 line scan (`line.startswith("lifecycle:")`), so a folded
# scalar that happened to wrap a line beginning "lifecycle:" would be misread as
# a top-level field. Indented block scalars cannot collide with it.
def _str_representer(dumper: yaml.SafeDumper, data: str):
    style = "|" if ("\n" in data or len(data) > 80) else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(str, _str_representer)


def to_yaml(obj: Dict[str, Any]) -> str:
    header = (
        "# MIGRATED from a review backlog by scripts/ops/migrate_backlog_to_work_objects.py\n"
        "# (Phase C, 2026-09-01). Arrives `dormant`: carried, not started, not queued.\n"
        "# ⚠️ `blocked_on: []` here is NOT a claim that nothing blocks this — see\n"
        "# `blocked_on_basis` below. Write a TRUE edge before moving this out of dormant.\n"
    )
    body = yaml.dump(obj, Dumper=_Dumper, sort_keys=False,
                     allow_unicode=True, width=88, default_flow_style=False)
    return header + body


def migrate(*, write: bool, migrated_on: str) -> Dict[str, Any]:
    """Returns a measured summary. Idempotent: an existing file is never clobbered."""
    written = skipped = 0
    per_source: Dict[str, int] = {}
    absences = {"no_title": 0, "no_opened_at": 0, "no_done_condition": 0}
    types = {"question": 0, "commitment": 0}
    # Counted from the objects actually built, never asserted from the
    # constructor: the migration's whole safety argument is that nothing
    # arrives in flight, and an argument you cannot check is not one.
    lifecycles: dict = {}

    if write:
        OBJECTS_DIR.mkdir(parents=True, exist_ok=True)

    for rel, stage in SOURCES:
        path = REPO_ROOT / rel
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows = carried_rows(doc)
        per_source[rel] = len(rows)
        for row in rows:
            obj = build_object(row, source_path=rel, stage=stage,
                               migrated_on=migrated_on)
            types[obj["type"]] += 1
            if not _text(row.get("title")):
                absences["no_title"] += 1
            if not _text(row.get("opened_at")):
                absences["no_opened_at"] += 1
            if not _text(row.get("resolution_criteria")):
                absences["no_done_condition"] += 1

            dest = OBJECTS_DIR / f"{obj['id']}.yaml"
            if dest.exists():
                # Never clobber: a later hand-edit (a real edge, an owner) is
                # exactly what we want to survive a re-run.
                skipped += 1
                continue
            if write:
                dest.write_text(to_yaml(obj), encoding="utf-8")
            written += 1
            lifecycles[obj["lifecycle"]] = lifecycles.get(obj["lifecycle"], 0) + 1

    return {"written": written, "skipped_existing": skipped,
            "per_source": per_source, "absences": absences, "types": types,
            "lifecycles": lifecycles,
            "carried_total": sum(per_source.values())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="create the object files (default: measure and report only)")
    ap.add_argument("--date", default=str(date.today()), help="migration date stamp")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    s = migrate(write=a.write, migrated_on=a.date)
    verb = "WROTE" if a.write else "would write"
    print(f"migrate-backlog: {verb} {s['written']} object(s); "
          f"{s['skipped_existing']} already existed and were left untouched.")
    print(f"  CARRIED population (status in {sorted(CARRIED_STATUSES)}): {s['carried_total']}")
    for rel, n in s["per_source"].items():
        print(f"    {rel}: {n}")
    print(f"  type split (from the row's own next_action field): {s['types']}")
    print(f"  absences carried through rather than filled: {s['absences']}")
    # Quantified deliberately. This line read "every object written is
    # lifecycle=dormant" with no denominator, and diagnostic-provenance-guard
    # failed it (sub-class C, unquantified universal claim). It was right: a
    # run that wrote NOTHING printed the same reassuring sentence as one that
    # wrote 584 correct objects. This is the migration's core safety property
    # -- that it cannot blow the WIP ceiling -- so it is precisely the claim
    # that has to carry the population it ranges over.
    lc = s["lifecycles"]
    dormant = lc.get("dormant", 0)
    if s["written"] and dormant == s["written"]:
        print(f"  lifecycle: {dormant} of {s['written']} object(s) "
              f"{'written' if a.write else 'to write'} are dormant "
              f"— carried, NOT in flight.")
    else:
        print(f"  lifecycle: {dormant} dormant of {s['written']}; full split {lc}")
    return 0


def _self_test() -> int:
    """A migration whose mapping is never exercised is a mapping nobody checked."""
    ok = True

    def check(label: str, got: Any, want: Any) -> None:
        nonlocal ok
        good = got == want
        ok &= good
        print(f"  self-test ({label}): {'PASS' if good else f'FAIL got={got!r} want={want!r}'}")

    base = {"id": "BL-X", "status": "open", "title": "T",
            "resolution_criteria": "C", "opened_at": "2026-01-01"}
    o = build_object(base, source_path="docs/claude/health-review-backlog.json",
                     stage="INTEGRITY", migrated_on="2026-09-01")

    check("a migrated row is dormant, never in_flight", o["lifecycle"], "dormant")
    check("a migrated row has no owner", o["owner"], None)
    check("a migrated row has no parent intent", o["parent_intent"], None)
    check("no edge is invented", o["blocked_on"], [])
    check("the empty edge list says it was NOT assessed",
          o["blocked_on_basis"].startswith("NOT_ASSESSED"), True)
    check("the source row stays traceable", o["source"]["row_id"], "BL-X")
    check("resolution_criteria becomes the done-condition", o["done_condition"], "C")

    # The two type mappings, read off the row's own field.
    check("no next_action -> question", o["type"], "question")
    o2 = build_object(dict(base, next_action="do the thing"),
                      source_path="x", stage="EVIDENCE", migrated_on="2026-09-01")
    check("a next_action -> commitment", o2["type"], "commitment")
    check("the next_action is carried, not dropped",
          o2["next_action_at_migration"], "do the thing")

    # Absences must be LOUD, never quietly empty.
    o3 = build_object({"id": "BL-Y", "status": "open"}, source_path="x",
                      stage="EVIDENCE", migrated_on="2026-09-01")
    check("a row with no resolution_criteria gets a LOUD unknown, not ''",
          o3["done_condition"].startswith("⚠️ UNKNOWN"), True)
    check("a row with no title is marked untitled, not left blank",
          "UNTITLED" in o3["title"], True)
    check("a missing opened_at is None, never the string 'None'",
          o3["opened_at"], None)

    # Status selection.
    doc = {"items": [{"id": "1", "status": "open"}, {"id": "2", "status": "kept_open"},
                     {"id": "3", "status": "resolved"}, {"id": "4", "status": "wont_fix"},
                     {"id": "5", "status": "superseded"}, {"id": "6", "status": "invalid"}]}
    check("only open + kept_open are carried",
          [r["id"] for r in carried_rows(doc)], ["1", "2"])
    check("an UNKNOWN status is not swept in as carried",
          carried_rows({"items": [{"id": "9", "status": "brand_new_status"}]}), [])

    # The emitted YAML must survive the column-0 line scan work_phase_ping uses.
    text = to_yaml(build_object(
        dict(base, title="x" * 200,
             resolution_criteria="prose\nlifecycle: in_flight\nmore prose"),
        source_path="x", stage="EVIDENCE", migrated_on="2026-09-01"))
    scanned = [ln.split(":", 1)[1].strip() for ln in text.split("\n")
               if ln.startswith("lifecycle:")]
    check("prose containing 'lifecycle:' cannot forge a top-level field",
          scanned, ["dormant"])
    check("the emitted YAML parses", yaml.safe_load(text)["id"], "BL-X")

    # The population guard: dormant must not be ping-worthy, or a bulk migration
    # pages the operator once per row.
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_wpp", REPO_ROOT / "scripts" / "ops" / "work_phase_ping.py")
        wpp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wpp)
        check("dormant is NOT ping-worthy, so a bulk migration is silent",
              "dormant" in wpp.PING_WORTHY, False)
        check("accepted IS ping-worthy — which is WHY nothing here uses it",
              "accepted" in wpp.PING_WORTHY, True)
    except Exception as e:  # pragma: no cover - the check is the point, not the import
        ok = False
        print(f"  self-test (work_phase_ping cross-check): FAIL could not load: {e}")

    print("migrate-backlog self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
