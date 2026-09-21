#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::pipeline-guard (--self-test + --check).
# Read by A3 (the daily brief) for section 0 and the section-5 unrouted count.
"""THE FOLLOW-THROUGH PIPELINE — the thing that pulls work to a finish.

WHY THIS EXISTS, in the operator's words (2026-09-21), which are the
acceptance criterion and not a preamble:

    "there's too many things here that get just dropped halfway through, and
    that was definitely one of the problems we were trying to solve and that
    still doesn't seem to have been resolved... not just more CI guards or
    whatever, not just building up the CLAUDE.md -- actually creating infra
    that has a pipeline that pulls things through to the finish."

⚠️ **ARCHIVING THE REGISTERS DID NOT FIX THIS. IT MOVED IT.** The reset removed
the place work rotted and did not build the thing that pulls it through, so
between the reset and this module follow-through was WORSE than before: 1,065
unresolved backlog rows and 91 monitoring rows sat in git history with nothing
reading them at all. Scope of record: docs/plans/OPERATING-PLAN-2026-09-21.md
section 3b.

THE FIVE REASONS THE OLD SYSTEM DROPPED THINGS
-----------------------------------------------
Written here so this module can be CHECKED against them rather than hoped at.
Each is answered by a specific mechanism below, and the self-test asserts the
mechanism rather than the intention.

  (1) FILING WAS FREE; PICKING UP WAS VOLUNTARY.
      -> `due()` is computed, not declared. Nothing has to choose to look.
  (2) MOST ROWS CARRIED NO DUE CONDITION.
      -> `due_when` is REQUIRED and validated. OPEN-ITEMS.json got this right
         (91 of 91 carried `clears_when`) and that part is kept deliberately.
  (3) NO LINK BACK TO THE GENERATOR.
      -> `origin.rerun` is REQUIRED: the command that REGENERATES the finding,
         so a successor can ask whether it still applies instead of trusting a
         months-old sentence.
  (4) NO FORCED TERMINAL STATE.
      -> `terminal_reason` is REQUIRED to enter `done` or `killed`. An item may
         not simply stop being mentioned.
  (5) THE SURFACE NOBODY READ. `DUE.md` was rendered, read by the `duty` skill,
      which a session had to CHOOSE to run, and the operator never saw it.
      -> The pull is the OPERATOR'S OWN PAGE. `due()` feeds brief section 0 and
         `unrouted_count()` is reported as a NUMBER in section 5. A reminder is
         not a mechanism; a rising count on a page they open daily is.

⚠️ **THE TEST OF "THIS IS NOT THE EIGHT REGISTERS AGAIN" IS SPECIFIC**, and it
is the property to defend in review: in the old model an item could sit in a
register forever without anyone noticing, and 951 rows prove it could. Here,
coming due puts an item on the operator's page and it STAYS there until it is
routed. If that property is ever removed, this is the eight registers again.

APPEND-ONLY JSONL, AND WHY IT IS NOT A JSON ARRAY
--------------------------------------------------
The archived registers were JSON arrays, and a shared array is what produced
their merge conflicts: two sessions editing different rows collide on the same
bytes. This store is **JSONL, append-only**. A state change is a NEW LINE, not
an edit, so two sessions appending concurrently produce a file that merges
cleanly by construction.

**The current state of an item is its LAST record.** `load()` folds the log;
`read_log()` returns it raw. That also gives the audit trail for free -- how an
item reached `killed` is in the file, not lost to an overwrite.

⚠️ **A PARSE FAILURE IS NOT A MISSING ROW.** A malformed line is reported as
`unreadable`, never skipped silently, because "we could not read it" and "it is
not there" must stay distinguishable (scripts/ci/check_collapsed_states.py
enforces this class of distinction repo-wide). A loader that drops junk lines
would make a corrupted pipeline look like an empty one -- an EMPTY pipeline
reads as "nothing is due", which is precisely the false all-clear this module
exists to prevent.

Self-test:  python3 scripts/ops/pipeline.py --self-test
Check:      python3 scripts/ops/pipeline.py --check
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

STORE = Path("docs/claude/work/PIPELINE.jsonl")

ORIGIN_KINDS = ("audit", "review", "session", "deploy", "research", "operator")
DUE_KINDS = ("observation", "date", "event")
NEXT_ACTIONS = ("dispatch_lane", "check_observation", "apply_mandate", "ask_operator")
STATES = ("queued", "due", "routed", "done", "killed")

#: States an item may not enter without a stated reason. This is reason (4).
TERMINAL_STATES = ("done", "killed")
#: States that still owe someone work. `routed` is NOT terminal -- it means a
#: lane/mandate/row now owns it, and that owner's completion is what closes it.
OPEN_STATES = ("queued", "due", "routed")

_ISO_DATE = "%Y-%m-%d"


class PipelineError(ValueError):
    """A refusal to accept an item. Carries WHICH field and WHY."""


@dataclass
class LoadResult:
    """⚠️ Three counts, never collapsed into one 'items' list.

    `unreadable` existing and being non-empty is a LOUD condition: it means the
    store is partially unreadable, which must never render as an empty or
    healthy pipeline.
    """

    items: dict[str, dict] = field(default_factory=dict)
    unreadable: list[tuple[int, str]] = field(default_factory=list)
    records: int = 0

    @property
    def healthy(self) -> bool:
        return not self.unreadable


def _today(today: date | None = None) -> date:
    return today or datetime.now(timezone.utc).date()


def _parse_date(s: str, field_name: str) -> date:
    try:
        return datetime.strptime(s, _ISO_DATE).date()
    except (TypeError, ValueError):
        raise PipelineError(
            f"{field_name}: expected an ISO date YYYY-MM-DD, got {s!r}"
        ) from None


def validate(item: dict) -> dict:
    """Refuse an item that cannot be followed through, and say which reason.

    ⚠️ Every refusal below maps to one of the five documented failure reasons.
    A validator that accepted an item with no `due_when` would rebuild reason
    (2); one that accepted `killed` with no `terminal_reason` would rebuild
    reason (4). Do not relax these to make an import pass -- fix the import.
    """
    if not isinstance(item, dict):
        raise PipelineError(f"item must be an object, got {type(item).__name__}")

    for key in ("id", "what", "origin", "due_when", "next_action", "state"):
        if key not in item:
            raise PipelineError(f"missing required field {key!r}")

    if not isinstance(item["id"], str) or not item["id"].strip():
        raise PipelineError("id: must be a non-empty string")
    if not isinstance(item["what"], str) or not item["what"].strip():
        raise PipelineError("what: must be a non-empty string")

    # ── reason (3): it must know how to regenerate itself ────────────────────
    origin = item["origin"]
    if not isinstance(origin, dict):
        raise PipelineError("origin: must be an object")
    if origin.get("kind") not in ORIGIN_KINDS:
        raise PipelineError(
            f"origin.kind: must be one of {ORIGIN_KINDS}, got {origin.get('kind')!r}"
        )
    for k in ("ref", "rerun"):
        if not isinstance(origin.get(k), str) or not origin[k].strip():
            raise PipelineError(
                f"origin.{k}: required and non-empty — reason (3), a row that "
                f"cannot point at what produced it cannot be re-checked later"
            )

    # ── reason (2): it must know when it needs attention ─────────────────────
    due_when = item["due_when"]
    if not isinstance(due_when, dict):
        raise PipelineError("due_when: must be an object")
    kind = due_when.get("kind")
    if kind not in DUE_KINDS:
        raise PipelineError(
            f"due_when.kind: must be one of {DUE_KINDS}, got {kind!r}"
        )
    if kind == "date":
        _parse_date(due_when.get("due_date"), "due_when.due_date")
    else:
        if not isinstance(due_when.get("clears_when"), str) or not due_when["clears_when"].strip():
            raise PipelineError(
                "due_when.clears_when: required for observation/event items — "
                "reason (2). What would have to be TRUE for this to be over?"
            )
    every = due_when.get("check_every_days")
    if every is not None and (not isinstance(every, int) or every < 1):
        raise PipelineError(
            f"due_when.check_every_days: must be a positive int, got {every!r}"
        )

    if item["next_action"] not in NEXT_ACTIONS:
        raise PipelineError(
            f"next_action: must be one of {NEXT_ACTIONS}, got {item['next_action']!r}"
        )
    if item["state"] not in STATES:
        raise PipelineError(
            f"state: must be one of {STATES}, got {item['state']!r}"
        )

    # ── reason (4): it may not simply stop being mentioned ───────────────────
    reason = item.get("terminal_reason")
    if item["state"] in TERMINAL_STATES:
        if not isinstance(reason, str) or not reason.strip():
            raise PipelineError(
                f"terminal_reason: REQUIRED to enter {item['state']!r} — reason "
                f"(4). An item leaves the pipeline done or killed WITH A STATED "
                f"REASON, never by going quiet."
            )
    elif reason not in (None, ""):
        raise PipelineError(
            f"terminal_reason: set to {reason!r} on non-terminal state "
            f"{item['state']!r} — that reads as closed while still being open"
        )

    if item["state"] == "routed" and not str(item.get("routed_to") or "").strip():
        raise PipelineError(
            "routed_to: required in state 'routed' — routed to WHAT? A routing "
            "with no destination is how an item stops being anyone's."
        )
    return item


def read_log(store: Path = STORE) -> LoadResult:
    """Read every record, folding to the LAST per id. Never drops a bad line."""
    res = LoadResult()
    if not store.exists():
        return res
    for lineno, raw in enumerate(store.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        try:
            rec = json.loads(line)
            if not isinstance(rec, dict) or not isinstance(rec.get("id"), str):
                raise ValueError("record is not an object carrying a string id")
        except Exception as exc:  # noqa: BLE001 — the message is the payload
            res.unreadable.append((lineno, f"{type(exc).__name__}: {exc}"))
            continue
        res.records += 1
        res.items[rec["id"]] = rec
    return res


def load(store: Path = STORE) -> LoadResult:
    return read_log(store)


def append(item: dict, store: Path = STORE) -> dict:
    """Validate, then append one record. Atomic and never rewrites history."""
    validate(item)
    store.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
    with open(store, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    return item


def is_due(item: dict, today: date | None = None) -> bool:
    """Is this item asking for attention now?

    ⚠️ COMPUTED, NOT DECLARED — this is reason (1). An item does not become due
    because someone remembered to set a flag; it becomes due because the clock
    or the condition says so, whether or not anyone looked.
    """
    if item.get("state") in TERMINAL_STATES:
        return False
    dw = item.get("due_when") or {}
    today = _today(today)

    if dw.get("kind") == "date":
        try:
            return _parse_date(dw.get("due_date"), "due_when.due_date") <= today
        except PipelineError:
            # ⚠️ An unparseable due date is treated as DUE, not as not-due. The
            # safe direction for a broken clock is to surface the item.
            return True

    every = dw.get("check_every_days")
    if not isinstance(every, int) or every < 1:
        # No cadence declared on an observation item -> it is always asking.
        return True
    last = dw.get("last_checked")
    if not isinstance(last, str) or not last.strip():
        return True
    try:
        return _parse_date(last, "due_when.last_checked") + timedelta(days=every) <= today
    except PipelineError:
        return True


def due(items: Iterable[dict], today: date | None = None) -> list[dict]:
    return [i for i in items if is_due(i, today)]


def unrouted(items: Iterable[dict], today: date | None = None) -> list[dict]:
    """Due, and nobody has taken it. THIS is the number section 5 reports.

    ⚠️ `routed` is excluded and `queued`/`due` are not. An item that a lane owns
    is being worked; an item that is due and unowned is the thing that used to
    rot invisibly.
    """
    return [i for i in due(items, today) if i.get("state") != "routed"]


def unrouted_count(items: Iterable[dict], today: date | None = None) -> int:
    return len(unrouted(items, today))


def stats(res: LoadResult, today: date | None = None) -> dict:
    items = list(res.items.values())
    by_state = {s: sum(1 for i in items if i.get("state") == s) for s in STATES}
    return {
        "records": res.records,
        "items": len(items),
        "by_state": by_state,
        "open": sum(by_state[s] for s in OPEN_STATES),
        "due": len(due(items, today)),
        "unrouted": unrouted_count(items, today),
        # ⚠️ Reported ALWAYS, including as 0, so "we could not read the store"
        # is never rendered as a clean bill of health.
        "unreadable": len(res.unreadable),
        "readable": res.healthy,
    }


def render_section_0(res: LoadResult, today: date | None = None) -> list[str]:
    """Brief section 0 — WHAT CAME DUE. Consumed by A3.

    ⚠️ Renders the unreadable count FIRST when non-zero. A brief that opened
    with a tidy due-list while half the store failed to parse would be the
    frozen-board failure this repo already paid for: a valid-looking read of a
    broken source is indistinguishable from a healthy one unless it says so.
    """
    L = ["## §0 — WHAT CAME DUE", ""]
    if not res.healthy:
        L += [
            f"> ⚠️ **{len(res.unreadable)} RECORD(S) COULD NOT BE PARSED** — this "
            f"section is INCOMPLETE and the counts below are a floor, not a total.",
            "",
        ]
        for lineno, why in res.unreadable[:10]:
            L.append(f"> - line {lineno}: {why}")
        L.append("")
    rows = due(res.items.values(), today)
    if not rows:
        L += ["Nothing came due." if res.healthy else
              "Nothing came due **among the records that parsed**.", ""]
        return L
    L += [f"**{len(rows)} item(s) due. Each needs a disposition today.**", ""]
    for i in sorted(rows, key=lambda r: r.get("id", "")):
        owner = f" → `{i['routed_to']}`" if i.get("routed_to") else ""
        L.append(
            f"- **{i.get('id')}** [{i.get('state')}{owner}] {i.get('what')} "
            f"· next: `{i.get('next_action')}` · rerun: `{(i.get('origin') or {}).get('rerun')}`"
        )
    L.append("")
    return L


# ─────────────────────────────────────────────────────────────── self-test ──
def _selftest() -> int:
    failures: list[str] = []

    def check(label: str, cond: bool) -> None:
        print(f"  {'PASS' if cond else 'FAIL'}: {label}")
        if not cond:
            failures.append(label)

    def base(**over: Any) -> dict:
        item = {
            "id": "PI-20260921-0001",
            "what": "one line",
            "origin": {"kind": "audit", "ref": "#12674",
                       "rerun": "python3 scripts/ci/run_guards.py --all"},
            "due_when": {"kind": "observation", "clears_when": "the roster reads 3",
                         "check_every_days": 7, "last_checked": "2026-09-01"},
            "next_action": "check_observation",
            "state": "queued",
            "routed_to": None,
            "terminal_reason": None,
        }
        item.update(over)
        return item

    def refuses(label: str, item: dict, needle: str) -> None:
        try:
            validate(item)
        except PipelineError as exc:
            check(f"{label} (says why: {needle!r})", needle in str(exc))
        else:
            check(f"{label} — REFUSED", False)

    print("— the five failure reasons, each asserted —")
    check("a well-formed item validates (positive control)", validate(base()) is not None)

    refuses("reason (2): no due condition is refused",
            base(due_when={"kind": "observation", "check_every_days": 7}), "clears_when")
    refuses("reason (3): no rerun command is refused",
            base(origin={"kind": "audit", "ref": "#1", "rerun": "  "}), "origin.rerun")
    refuses("reason (4): `killed` with no reason is refused",
            base(state="killed"), "terminal_reason")
    refuses("reason (4): `done` with no reason is refused",
            base(state="done"), "terminal_reason")
    refuses("a terminal_reason on an OPEN item is refused",
            base(state="queued", terminal_reason="looks fine"), "non-terminal")
    refuses("`routed` with no destination is refused",
            base(state="routed"), "routed_to")
    check("…but `killed` WITH a reason is accepted",
          validate(base(state="killed", terminal_reason="unworked since 2026-06")) is not None)
    check("…and `routed` WITH a destination is accepted",
          validate(base(state="routed", routed_to="A7")) is not None)

    print("— reason (1): due is COMPUTED, not declared —")
    t = date(2026, 9, 21)
    check("an observation past its cadence is due",
          is_due(base(), t))
    check("…and the SAME item inside its cadence is NOT due (negative control)",
          not is_due(base(due_when={"kind": "observation", "clears_when": "x",
                                    "check_every_days": 7,
                                    "last_checked": "2026-09-20"}), t))
    check("a never-checked observation is due",
          is_due(base(due_when={"kind": "observation", "clears_when": "x",
                                "check_every_days": 30}), t))
    check("a future dated item is not due",
          not is_due(base(due_when={"kind": "date", "due_date": "2026-12-01"}), t))
    check("a past dated item is due",
          is_due(base(due_when={"kind": "date", "due_date": "2026-09-20"}), t))
    check("⚠️ an UNPARSEABLE due date is treated as DUE, not as quiet",
          is_due({"state": "queued", "due_when": {"kind": "date", "due_date": "soon"}}, t))
    check("a killed item is never due, whatever its clock says",
          not is_due(base(state="killed", terminal_reason="r",
                          due_when={"kind": "date", "due_date": "2020-01-01"}), t))

    print("— reason (5): the number the operator's page reports —")
    pool = [base(id="A", state="queued"),
            base(id="B", state="routed", routed_to="A7"),
            base(id="C", state="killed", terminal_reason="dead")]
    check("unrouted counts the due-and-unowned only",
          [i["id"] for i in unrouted(pool, t)] == ["A"])
    check("…and a routed item is still DUE (its owner is being tracked)",
          {i["id"] for i in due(pool, t)} == {"A", "B"})

    print("— append-only round trip, and the unreadable line —")
    with tempfile.TemporaryDirectory() as td:
        store = Path(td) / "P.jsonl"
        append(base(id="X"), store)
        append(base(id="X", state="routed", routed_to="A7"), store)
        append(base(id="Y"), store)
        res = read_log(store)
        check("two records for one id fold to the LAST (append-only state)",
              res.items["X"]["state"] == "routed")
        check("…and the raw log kept BOTH (the audit trail survives)",
              res.records == 3 and len(res.items) == 2)

        store.write_text(store.read_text() + "{not json\n" + "[1,2]\n")
        res2 = read_log(store)
        check("⚠️ a malformed line is REPORTED, never skipped silently",
              len(res2.unreadable) == 2)
        check("…a JSON array line is also unreadable (it carries no id)",
              any("id" in why or "object" in why for _, why in res2.unreadable))
        check("…the good items still load (partial read is not total failure)",
              len(res2.items) == 2)
        check("…and `healthy` is False so nothing can call this clean",
              not res2.healthy and stats(res2, t)["readable"] is False)
        check("⚠️ section 0 SAYS the store is partly unreadable",
              "COULD NOT BE PARSED" in "\n".join(render_section_0(res2, t)))
        check("…and a HEALTHY store's section 0 does NOT say that "
              "(negative control)",
              "COULD NOT BE PARSED" not in "\n".join(render_section_0(res, t)))

        empty = read_log(Path(td) / "missing.jsonl")
        check("a missing store is readable-and-empty, not an error",
              empty.healthy and not empty.items)

    print("— validation is enforced on the WRITE path, not just on read —")
    with tempfile.TemporaryDirectory() as td:
        store = Path(td) / "P.jsonl"
        try:
            append(base(state="killed"), store)
        except PipelineError:
            check("append() refuses an invalid item", True)
        else:
            check("append() refuses an invalid item", False)
        check("…and wrote NOTHING when it refused",
              not store.exists() or store.read_text() == "")

    print()
    if failures:
        print(f"SELF-TEST FAILED — {len(failures)} check(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SELF-TEST PASS")
    return 0


def _check(store: Path) -> int:
    """Validate every item in the real store. Used by the guard."""
    res = read_log(store)
    bad: list[str] = []
    for item_id, item in sorted(res.items.items()):
        try:
            validate(item)
        except PipelineError as exc:
            bad.append(f"{item_id}: {exc}")

    s = stats(res)
    print(json.dumps(s, indent=2, sort_keys=True))

    if res.unreadable:
        print(f"\n::error::pipeline: {len(res.unreadable)} UNREADABLE record(s) — "
              f"a store that cannot be fully read must never render as an empty "
              f"or healthy pipeline.")
        for lineno, why in res.unreadable:
            print(f"  line {lineno}: {why}")
    if bad:
        print(f"\n::error::pipeline: {len(bad)} INVALID item(s) — each names the "
              f"field and the failure reason it maps to.")
        for b in bad:
            print(f"  {b}")
    if res.unreadable or bad:
        return 1
    print("\npipeline: clean")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="validate every item in the store")
    ap.add_argument("--due", action="store_true", help="render brief section 0")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--store", default=str(STORE))
    args = ap.parse_args(argv)

    if args.self_test:
        return _selftest()
    store = Path(args.store)
    if args.check:
        return _check(store)
    res = read_log(store)
    if args.stats:
        print(json.dumps(stats(res), indent=2, sort_keys=True))
        return 0 if res.healthy else 1
    print("\n".join(render_section_0(res)))
    return 0 if res.healthy else 1


if __name__ == "__main__":
    sys.exit(main())
