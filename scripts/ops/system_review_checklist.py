#!/usr/bin/env python3
"""The /system-review CHECKLIST — one cohesive, always-renderable statement of
what the review mandate contains, what has actually been done, and what has not.

WHY (operator directive, 2026-08-31): *"every time that I ask for a status
update, the session knows to give me the chart with the items that are in the
review mandate, a checklist of what was actually done versus not done or is
still in work. And another row for notes ... we need clear log keeping so that
I can also understand what the state is."*

And the binding half: **the system review is DONE only when every item is
ticked.** Before this, "done" was a judgement a session made in prose at the end
of a long context, which is how a review reported completion while a third of
its mandate had never been touched.

THE ITEM LIST IS DERIVED, NOT TYPED. It comes from
``render_system_report.py::_REQUIRED_COVERAGE_KEYS`` — the tuple CI actually
enforces — plus the three sub-reviews and the report itself. A hand-typed list
would drift from the enforced one, and it already has: SKILL.md's prose says
"the TEN required keys" while that tuple holds **13**. Field beats comment, so
this file reads the field.

STATUSES ARE NOT COLLAPSED. ``not_started`` (nobody looked), ``in_progress``
(started, incomplete), ``blocked`` (cannot proceed, and on what), ``done``
(finished, WITH evidence) and ``n_a`` (does not apply this run, WITH a reason)
are five different facts. ``done`` without evidence is refused — an unevidenced
tick is the thing this checklist exists to stop.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
STATE = ROOT / "docs/claude/system-review-checklist.json"
RENDERER = ROOT / "scripts/reports/render_system_report.py"

#: Items that are not coverage keys but ARE part of the mandate.
_FIXED_ITEMS: tuple[tuple[str, str], ...] = (
    ("health_review", "Run /health-review (technical/pipeline/data health)"),
    ("performance_review", "Run /performance-review (per-trade grading, strategy aggregates)"),
    ("ml_review", "Run /ml-review (trainer, registry, promotion ladder)"),
    ("consolidated_report", "Render the consolidated system report with --strict"),
    ("operator_ping", "Send the single consolidated Telegram ping"),
)

STATUSES = ("not_started", "in_progress", "blocked", "done", "n_a")
_GLYPH = {
    "done": "[x]", "in_progress": "[~]", "blocked": "[!]",
    "not_started": "[ ]", "n_a": "[-]",
}


def coverage_keys() -> list[str]:
    """The keys CI ENFORCES, parsed from the renderer. Never hand-typed."""
    src = RENDERER.read_text(encoding="utf-8")
    i = src.index("_REQUIRED_COVERAGE_KEYS = (")
    blk = src[i : src.index("\n)", i)]
    keys = re.findall(r'"([a-z_]+)"', blk)
    if not keys:
        raise SystemExit(
            "could not parse _REQUIRED_COVERAGE_KEYS — the probe is broken, not "
            "the source (a negative needs a denominator)"
        )
    return keys


def canonical_items() -> list[dict[str, str]]:
    items = [{"id": k, "label": f"review_coverage.{k}", "kind": "coverage"}
             for k in coverage_keys()]
    items += [{"id": i, "label": lbl, "kind": "mandate"} for i, lbl in _FIXED_ITEMS]
    return items


def load_state(path: pathlib.Path = STATE) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": 1, "run": None, "items": {}, "notes": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        # A garbled state file must not read as an empty (all-not_started) run:
        # that would silently discard evidence of work already done.
        raise SystemExit(f"{path} is unreadable — refusing to render a false checklist")


def verdict(state: dict[str, Any]) -> tuple[bool, list[str]]:
    """(is_done, outstanding_ids). Done ONLY when every item is done/n_a."""
    got = state.get("items") or {}
    outstanding = []
    for item in canonical_items():
        rec = got.get(item["id"]) or {}
        st = rec.get("status", "not_started")
        if st == "done" and not (rec.get("evidence") or "").strip():
            outstanding.append(item["id"] + " (done without evidence)")
        elif st == "n_a" and not (rec.get("evidence") or "").strip():
            outstanding.append(item["id"] + " (n_a without a reason)")
        elif st not in ("done", "n_a"):
            outstanding.append(item["id"])
    return (not outstanding), outstanding


def render(state: dict[str, Any]) -> str:
    items = canonical_items()
    got = state.get("items") or {}
    done, outstanding = verdict(state)
    counts: dict[str, int] = {s: 0 for s in STATUSES}
    for it in items:
        counts[(got.get(it["id"]) or {}).get("status", "not_started")] += 1

    run = state.get("run") or "(unnamed run)"
    out = [f"# SYSTEM REVIEW CHECKLIST — {run}", ""]
    out.append(
        f"**{counts['done']}/{len(items)} done** · {counts['in_progress']} in work · "
        f"{counts['blocked']} blocked · {counts['not_started']} not started · "
        f"{counts['n_a']} n/a"
    )
    out.append("")
    out.append(f"**REVIEW IS {'COMPLETE' if done else 'NOT COMPLETE'}** — "
               + ("every mandate item is ticked."
                  if done else f"{len(outstanding)} item(s) outstanding."))
    out.append("")
    out.append("| | Mandate item | Status | What was actually done |")
    out.append("|---|---|---|---|")
    for it in items:
        rec = got.get(it["id"]) or {}
        st = rec.get("status", "not_started")
        ev = (rec.get("evidence") or "").replace("|", "\\|").strip() or "—"
        out.append(f"| {_GLYPH[st]} | `{it['label']}` | {st} | {ev} |")

    notes = state.get("notes") or []
    out.append("")
    out.append("## Notes — things the operator needs to know")
    if not notes:
        out.append("")
        out.append("_(none recorded this run)_")
    else:
        for n in notes:
            out.append(f"- {n}")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", default=str(STATE))
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero unless every mandate item is ticked")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    st = load_state(pathlib.Path(a.state))
    if a.json:
        done, outstanding = verdict(st)
        print(json.dumps({"complete": done, "outstanding": outstanding}, indent=2))
        return 0 if done or not a.check else 1
    print(render(st))
    if a.check:
        done, outstanding = verdict(st)
        if not done:
            print(f"\n::error::system review INCOMPLETE — outstanding: {', '.join(outstanding)}",
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
