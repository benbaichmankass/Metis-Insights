#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py (soak-registered-guard)
"""Every soak log must be REGISTERED with an alarm, or named in a dated debt list.

STANDING OPERATOR DIRECTIVE, 2026-09-02
---------------------------------------
    "Anything soaking needs to be logged with an alarm that has either a timer
     or a soak threshold, so that we know to get back to it when the soak is
     ready."

The rule lives in `docs/CLAUDE-RULES-CANONICAL.md` § "A soak must carry its own
alarm". This is the executable half — the thing that makes a future session
MEET the rule at the moment it applies, rather than at the moment it happens to
read a checklist.

WHY A GUARD AT ALL, MEASURED
----------------------------
On 2026-09-02, **16 soak logs were declared in `src/` and ZERO carried a
register alarm.** Four were named somewhere in `OPEN-ITEMS.json` (in a probe
command), which is a READER, not an alarm — nothing said what READY meant or
would notice if the writer died. The immediate case that prompted this was
`bybit_coverage_soak`: shipped in #10746 as the only declared evidence for
widening a real-money gate, with its follow-up proposed for the health-review
backlog — and the backlog is **not** a due-list source, so it would have
accrued, or failed to accrue, and surfaced to nobody.

⚠️ WHY THIS IS A BASELINE LIST AND NOT A DIFF-SCOPED CHECK
-----------------------------------------------------------
The obvious design — "only check files this PR touched" — was considered and
rejected. A diff-scoped guard passes VACUOUSLY on every PR that touches no soak
writer, which is nearly all of them, so it would spend most of its life
reporting a green that checked nothing. `CLAUDE.md` names that exact shape:
*"a green that checked nothing"*, and the `diagnostic-provenance-guard` row
records a diff-scoped check whose residue sat at exactly 52 findings for 26
days because it could not see a site regress.

So this runs on EVERY PR, over the WHOLE tree, and the pre-existing debt is
carried in `BASELINE` below: explicit, dated, and countable.

⚠️ AND HERE IS THE HONEST LIMIT, STATED RATHER THAN HIDDEN. `BASELINE` is an
escape hatch, and adding a name to it is cheaper than writing a register row.
What makes that acceptable is that it is **not silent**: the name lands as a
visible line in a file called `check_soak_registered.py`, in the PR diff, under
a comment saying the list may only shrink. A reviewer sees a deliberate act.
That is the whole difference from `new-table-wiring-guard`, whose presence-only
`# data-wiring:` marker made the cheapest way to silence a real finding a
comment naming a table that does not exist — a guard cheaper to LIE to than to
satisfy, and worse than none.

Two further properties keep the hatch from rotting:

  * **A BASELINE entry naming a log that no longer exists is a FAILURE.** The
    list cannot accumulate stale names that quietly widen it.
  * **The debt count is PRINTED on every run**, passing or failing, so it
    appears in every CI log and a growing number is visible without anyone
    auditing the file.

⚠️ RE-POINTED 2026-09-22 (E45) — THE OLD REGISTER WAS ARCHIVED AND NOBODY SWEPT
------------------------------------------------------------------------------
Until this change the register was `docs/claude/OPEN-ITEMS.json`, which the
2026-09-21 operating reset ARCHIVED. `registered_soak_logs` returned
`"OPEN-ITEMS.json is missing"` and the guard exited 2 on every run — honest
about not looking, and grading nothing at all. MEASURED on `main` 2026-09-22
before the re-point: `19 soak log(s) declared · 0 registered · 16 carried as
pre-2026-09-02 debt`, exit 2. A rule whose executable half cannot see its
subject is prose, and § "A soak must carry its own alarm" was still naming this
file as what enforces it.

The post-reset taxonomy has exactly three intakes — `research/queue/<id>.yaml`
for questions, `docs/claude/work/MANAGER-CHECKLIST.json` for builds, and
`docs/claude/work/PIPELINE.jsonl` for anything that needs picking up later. A
soak is the third by definition: it is a thing that must be COME BACK TO. So the
register is now `PIPELINE.jsonl`, and no fourth register was invented for it.

WHAT "REGISTERED" MEANS, AND WHY IT IS NOT "MENTIONED"
------------------------------------------------------
An OPEN row in `docs/claude/work/PIPELINE.jsonl` (state `queued`/`due`/`routed`)
whose `due_when.kind` is `observation` or `event`, carrying a non-empty
`due_when.clears_when`, with the soak's name in the row's `what` or in that
`clears_when`.

Each clause is one half of the original rule, mapped onto the schema that
survived the reset:

  * **`clears_when` IS `ready_when`** — it states what READY means in DATA. A
    row whose `due_when.kind` is `date` carries only a TIMER, and the old rule
    refused exactly that: *"a block with no `ready_when` is refused … because it
    is a second timer wearing a threshold's name."* `check_every_days` still
    carries the timer, alongside.
  * **A TERMINAL row is not an alarm.** `done`/`killed` rows are excluded: a
    closed row will never come back for anything.
  * **`origin.rerun` DOES NOT COUNT, and that is the same refusal as before.**
    `origin.rerun` is the command that REGENERATES the finding — a READER,
    which is precisely what "mentioned in a probe command" was. Four of the
    sixteen 2026-09-02 logs were "mentioned" that way and could answer neither
    *is it ready* nor *is it dead*. Counting mentions would make this guard pass
    while changing nothing, so only `what` and `clears_when` are scanned.

⚠️ `declared_at` HAS NO POST-RESET FIELD AND IS NOT FAKED. The old block carried
it so `soak_alarm.py` could tell `not_writing` (no rows SINCE IT WAS DECLARED)
from `accruing`. A pipeline row has no equivalent, so this guard does not claim
to grade deadness — it grades REGISTRATION, which is all it ever graded. The
four-state grading in `scripts/ops/soak_alarm.py` still reads the archived
register and is a separate, still-dead surface; it is filed rather than silently
implied to work here.

Exit codes: 0 clean · 1 an unregistered, unbaselined soak (or a stale baseline
entry) · 2 we could not look.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: ⚠️ RE-POINTED 2026-09-24 (E64): the register moved from a single
#: append-only JSONL file to a directory, one immutable file per record —
#: see `scripts/ops/pipeline.py`'s module docstring for why. This constant
#: keeps its old NAME (read by messages below) but now names the directory;
#: `registered_soak_logs()` reads it via `pipeline.read_log()` rather than
#: re-parsing it here, so this guard never has to track the storage format.
_REGISTER = Path("docs/claude/work/pipeline")
_SCAN_ROOTS = ("src",)

#: An OPEN pipeline row can still come back for something. A terminal one cannot.
_OPEN_STATES = ("queued", "due", "routed")

#: `due_when` kinds that carry a CONDITION. `date` is a bare timer and is the
#: shape the original rule refused as "a second timer wearing a threshold's name".
_CONDITION_KINDS = ("observation", "event")

#: A soak log name as it appears in a writer: `"<name>_soak.jsonl"`.
_LOG_RE = re.compile(r'["\']([a-z0-9_]+_soak)\.jsonl["\']')

#: The same name as it appears in a REGISTER row, where it is prose rather than
#: a quoted filename — with or without the `.jsonl` suffix.
_REGISTER_LOG_RE = re.compile(r"\b([a-z0-9_]+_soak)(?:\.jsonl)?\b")

#: ── THE DEBT LIST — MEASURED 2026-09-02, AND IT MAY ONLY SHRINK ───────────
#:
#: Every soak log that existed BEFORE the 2026-09-02 directive and carries no
#: register alarm. All sixteen, because on that date zero soaks carried one.
#:
#: ⚠️ DO NOT ADD A NAME HERE TO MAKE A NEW SOAK PASS. That is the one use this
#: list is not for, and it is visible in the diff when someone tries. A new
#: soak gets a `soak` block in `docs/claude/OPEN-ITEMS.json` — see
#: `scripts/ops/soak_alarm.py::declaration_problems` for the four fields and
#: why each is refused when empty.
#:
#: REMOVING a name is the good direction and needs no ceremony: write the
#: register row, delete the line. The guard then holds that soak to the rule
#: permanently.
BASELINE: dict[str, str] = {
    "allocator_soak": "pre-2026-09-02",
    "arbitration_fanout_soak": "pre-2026-09-02",
    "cash_settlement_soak": "pre-2026-09-02",
    "conflict_taxonomy_soak": "pre-2026-09-02",
    "exit_interval_soak": "pre-2026-09-02",
    "exit_ladder_soak": "pre-2026-09-02",
    "exit_lever_soak": "pre-2026-09-02",
    "exposure_soak": "pre-2026-09-02",
    "fc_geometry_soak": "pre-2026-09-02",
    "macro_thesis_soak": "pre-2026-09-02",
    "netting_attribution_soak": "pre-2026-09-02",
    "pairs_soak": "pre-2026-09-02",
    "prop_ticket_risk_soak": "pre-2026-09-02",
    "protection_reassert_soak": "pre-2026-09-02",
    "stray_oca_soak": "pre-2026-09-02",
    "target_extension_soak": "pre-2026-09-02",
}


def declared_soak_logs(root: Path) -> dict[str, set[str]]:
    """Every `*_soak.jsonl` name mentioned under the scanned roots → the files.

    Deliberately a MENTION scan rather than a writer analysis. A name reaching
    `src/` at all means the log is real; asking *which* call actually appends
    would need to model `pathlib` composition and would fail open on the first
    writer that builds its filename slightly differently — failing open is the
    direction that loses the finding.
    """
    out: dict[str, set[str]] = {}
    for r in _SCAN_ROOTS:
        base = root / r
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*.py")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in _LOG_RE.finditer(text):
                out.setdefault(m.group(1), set()).add(str(f.relative_to(root)))
    return out


def _pipeline_module():
    """`scripts/ops/pipeline.py`, or None when it cannot be loaded.

    ⚠️ REUSED, NOT RE-DERIVED — this guard used to hand-parse the register's
    bytes itself, which is exactly the drift `scripts/ops/pipeline.py`'s own
    module docstring warns about (one schema, one home). Importing it means
    a future storage change (like this one) requires touching this file not
    at all rather than in two places that can silently disagree.
    """
    ops = str(Path(__file__).resolve().parents[1] / "ops")
    if ops not in sys.path:
        sys.path.insert(0, ops)
    try:
        import pipeline  # noqa: PLC0415 — deliberately late and optional
    except Exception:  # noqa: BLE001
        return None
    return pipeline


def registered_soak_logs(root: Path) -> tuple[set[str], str | None]:
    """Log names carrying a `soak` block. Returns (names, error-or-None).

    An error is propagated rather than swallowed: an unreadable register means
    we could not establish what is registered, which must exit `could not look`
    and never be reported as "nothing is registered" — that would fail every
    soak in the tree on a JSON typo.
    """
    pipeline = _pipeline_module()
    if pipeline is None:
        return set(), "scripts/ops/pipeline.py would not import"

    p = root / _REGISTER
    if not p.exists():
        return set(), f"{_REGISTER} is missing"

    res = pipeline.read_log(p)
    if not res.healthy:
        locs = ", ".join(f"{loc} ({why})" for loc, why in res.unreadable[:5])
        return set(), f"{_REGISTER} has {len(res.unreadable)} unreadable record(s): {locs}"

    names: set[str] = set()
    # ⚠️ SCANS `res.raw` (every RECORD, unfolded), matching the pre-2026-09-24
    # behaviour byte-for-byte -- NOT `res.items` (the current, folded state).
    # RULE ONE: verified against the real store before choosing this (a
    # fold-based rewrite silently DROPPED registrations: `bybit_coverage_soak`
    # and `ict_scalp_exit_head_soak` are each named in an OLD `queued` record
    # whose id later moved to `killed`; folding to current state alone would
    # newly report both as unregistered, a behaviour change this storage
    # migration has no mandate to make). Whether "ever registered, even by a
    # now-closed row" is the RIGHT rule is a separate, un-decided question --
    # filed rather than silently resolved either way; see the pipeline row
    # this guard cites in its own module docstring update.
    for row in res.raw:
        if row.get("state") not in _OPEN_STATES:
            continue
        due = row.get("due_when")
        if not isinstance(due, dict) or due.get("kind") not in _CONDITION_KINDS:
            continue
        clears = str(due.get("clears_when") or "").strip()
        if not clears:
            continue
        # `what` + `clears_when` ONLY. `origin.rerun` is the regeneration
        # command — a reader — and counting it would re-admit the "mentioned in
        # a probe" case this guard exists to refuse.
        hay = f"{row.get('what') or ''} {clears}"
        for m in _REGISTER_LOG_RE.finditer(hay):
            names.add(m.group(1))
    return names, None


def check(root: Path) -> tuple[int, list[str]]:
    declared = declared_soak_logs(root)
    registered, err = registered_soak_logs(root)
    if err:
        return 2, [f"could not look: {err}"]

    problems: list[str] = []

    unregistered = sorted(set(declared) - registered - set(BASELINE))
    for name in unregistered:
        where = ", ".join(sorted(declared[name])[:3])
        problems.append(
            f"SOAK NOT REGISTERED: `{name}` is declared in {where} and no OPEN "
            f"row in {_REGISTER} names it behind a condition.\n"
            f"    A soak nobody registered accrues to NOBODY: nothing will "
            f"surface it when it is ready, and the waiting never ends because "
            f"nothing was ever asked to come back for it.\n"
            f"    FIX: file it with `scripts/ops/pipeline.py` — an OPEN row "
            f"whose `due_when.kind` is `observation` or `event`, whose "
            f"`clears_when` states what READY means IN DATA (never in elapsed "
            f"days — `check_every_days` already carries the timer), and whose "
            f"`what` or `clears_when` names `{name}`. Naming it only in "
            f"`origin.rerun` does NOT register it: that is the command that "
            f"re-reads the soak, which is a probe, not an alarm.")

    stale = sorted(set(BASELINE) - set(declared))
    for name in stale:
        problems.append(
            f"STALE BASELINE ENTRY: `{name}` is in BASELINE but no longer exists "
            f"in the tree. Delete the line.\n"
            f"    The debt list may only SHRINK, and a name that outlives its "
            f"writer is a slot a future soak could quietly reuse.")

    return (1 if problems else 0), problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    root = Path(args.root)
    rc, problems = check(root)
    declared = declared_soak_logs(root)
    registered, _ = registered_soak_logs(root)
    debt = sorted(set(BASELINE) & set(declared))

    # Printed on EVERY run, pass or fail. A debt number that only appears when
    # something breaks is a number nobody watches.
    print(f"soak-registered-guard: {len(declared)} soak log(s) declared · "
          f"{len(set(declared) & registered)} registered · {len(debt)} carried "
          f"as pre-2026-09-02 debt")

    if rc == 0:
        print("soak-registered-guard: OK — every soak is registered or baselined.")
        if debt:
            print(f"  Carried debt (each accrues to nobody until registered): "
                  f"{', '.join(debt)}")
        return 0
    for p in problems:
        print(f"::error::{p}" if rc == 1 else f"::warning::{p}")
    return rc


# ── self-test: planted controls, so a vacuous pass is impossible ───────────

def _self_test() -> int:
    import tempfile
    fired = 0

    def ok(cond, label):
        nonlocal fired
        assert cond, f"control FAILED: {label}"
        fired += 1

    def row(name, *, state="queued", kind="observation", clears="rows>=1",
            where="what"):
        """One pipeline row, shaped so each clause of the rule can be planted."""
        r = {
            "id": f"PI-{name}-{state}-{kind}-{where}",
            "what": "a soak is accruing",
            "origin": {"kind": "session", "ref": "s1",
                       "rerun": f"tail runtime_logs/{name}.jsonl"},
            "due_when": {"kind": kind, "clears_when": clears},
            "next_action": "check_observation",
            "state": state,
        }
        if kind == "date":
            r["due_when"] = {"kind": "date", "due_date": "2026-10-01"}
        if where == "what":
            r["what"] = f"the {name} soak is accruing"
        elif where == "clears_when" and r["due_when"].get("clears_when") is not None:
            r["due_when"]["clears_when"] = f"{name}.jsonl has >= 30 rows"
        elif where == "rerun_only":
            r["what"] = "a soak is accruing"
        return r

    def plant(logs, register_rows, *, baseline=None, raw=None):
        """Build a fake tree. Returns (rc, problems) under a patched BASELINE.

        The register is `_REGISTER` (a DIRECTORY since the 2026-09-24
        re-point, lane E64) -- one file per row, written directly rather
        than via `pipeline.append()` since a self-test fixture is allowed to
        plant a row shape `append()` itself would refuse (a closed/killed row
        with no `terminal_reason`, tested elsewhere). `raw`, when given, is
        written as ONE malformed file, to plant the "could not look" case.
        """
        td = Path(tempfile.mkdtemp())
        (td / "src/runtime").mkdir(parents=True)
        for i, name in enumerate(logs):
            (td / f"src/runtime/w{i}.py").write_text(
                f'SOAK_LOG_NAME = "{name}.jsonl"\n', encoding="utf-8")
        (td / _REGISTER).mkdir(parents=True, exist_ok=True)
        if raw is not None:
            (td / _REGISTER / "raw.json").write_text(raw, encoding="utf-8")
        else:
            for i, r in enumerate(register_rows):
                (td / _REGISTER / f"{i:04d}.json").write_text(
                    json.dumps(r), encoding="utf-8")
        global BASELINE
        saved = BASELINE
        BASELINE = dict.fromkeys(baseline or [], "pre-2026-09-02")
        try:
            return check(td), td
        finally:
            BASELINE = saved

    # ── THE PLANTED VIOLATION AGAINST THE NEW SUBJECT ─────────────────────
    # ⚠️ RE-POINTED 2026-09-22 (E45). A GREEN RE-POINT PROVES NOTHING: this
    # guard spent the post-reset period exiting 2 against an archived register
    # and grading nothing, so the only evidence that it grades now is that it
    # FAILS on a violation planted in `docs/claude/work/PIPELINE.jsonl`.
    # RULE ONE: show the probe can find a positive before trusting it is quiet.
    (rc, probs), _ = plant(["new_soak"], [])
    ok(rc == 1 and any("SOAK NOT REGISTERED" in p for p in probs),
       "P1 a soak writer with NO pipeline row FAILS — the planted positive "
       "against the re-pointed subject")
    ok(any("`new_soak`" in p for p in probs),
       "P1b and the failure NAMES the soak, so the fix needs no hunt")
    ok(any("clears_when" in p for p in probs),
       "P1c and it names the field that carries the threshold")

    # ⚠️ THE CLAUSE THAT CARRIES THE ORIGINAL RULE: a row that only READS the
    # soak is a probe, not an alarm. `origin.rerun` is the regeneration
    # command, so a soak named ONLY there is still unregistered.
    (rc, probs), _ = plant(["probe_soak"], [row("probe_soak", where="rerun_only")])
    ok(rc == 1 and any("probe_soak" in p for p in probs),
       "P2 a soak named ONLY in `origin.rerun` is NOT registered — that is the "
       "'mentioned in a probe command' case, which could answer neither *is it "
       "ready* nor *is it dead*")

    # A `date` due_when is a bare TIMER. The original rule refused exactly this
    # shape: a second timer wearing a threshold's name.
    (rc, probs), _ = plant(["timer_soak"], [row("timer_soak", kind="date")])
    ok(rc == 1 and any("timer_soak" in p for p in probs),
       "P3 a row due on a DATE only does not register a soak — `clears_when` "
       "states what READY means in data; a date says only when to look")

    (rc, probs), _ = plant(["empty_soak"], [row("empty_soak", clears="  ")])
    ok(rc == 1 and any("empty_soak" in p for p in probs),
       "P4 an empty `clears_when` does not register it either — presence of the "
       "key is not a threshold")

    (rc, probs), _ = plant(["closed_soak"], [row("closed_soak", state="done")])
    ok(rc == 1 and any("closed_soak" in p for p in probs),
       "P5 a TERMINAL row is not an alarm — a `done` row will never come back "
       "for anything, so it cannot be what makes the waiting end")

    # ── the negatives: the fix actually works, both ways of naming it ──────
    (rc, _), _ = plant(["new_soak"], [row("new_soak", where="what")])
    ok(rc == 0, "N1 an OPEN observation row naming the soak in `what` passes")

    (rc, _), _ = plant(["new_soak"],
                       [row("new_soak", kind="event", where="clears_when")])
    ok(rc == 0, "N2 ...and naming it in `clears_when` passes too, .jsonl suffix "
                "and all — the register and the writer spell it differently and "
                "neither is wrong")

    (rc, _), _ = plant(["old_soak"], [], baseline=["old_soak"])
    ok(rc == 0, "N3 a pre-existing soak on the dated debt list passes")

    (rc, probs), _ = plant(["old_soak", "new_soak"], [], baseline=["old_soak"])
    ok(rc == 1 and len([p for p in probs if "NOT REGISTERED" in p]) == 1
       and "new_soak" in probs[0],
       "P6 ⚠️ a baselined soak does NOT excuse a new one beside it — the debt "
       "list grandfathers exactly the names on it and nothing else")

    (rc, probs), _ = plant([], [], baseline=["ghost_soak"])
    ok(rc == 1 and any("STALE BASELINE" in p for p in probs),
       "P7 a BASELINE entry whose writer is gone FAILS — the list may only "
       "shrink, and a name outliving its writer is a slot a future soak reuses")

    # ── could not look, never 'nothing is registered' ──────────────────────
    (rc, probs), _ = plant(["x_soak"], [], raw="{not json\n")
    ok(rc == 2 and any("could not look" in p for p in probs),
       "P8 ⚠️ an unparseable register line exits COULD NOT LOOK (2), never 1 — "
       "reading it as 'nothing is registered' would fail every soak in the tree "
       "on one bad line, the collapse this family of code exists to refuse")

    td = Path(tempfile.mkdtemp())
    (td / "src").mkdir(parents=True)
    rc, probs = check(td)
    ok(rc == 2 and any("missing" in p for p in probs),
       "P9 ...and a MISSING register is the same state — which is exactly what "
       "this guard read on every run between 2026-09-21 and this re-point")

    # A well-formed single-row register reads clean.
    (rc, _), _ = plant(["new_soak"], [row("new_soak")])
    ok(rc == 0, "N4 a single well-formed registered row reads clean")

    # The live tree's own baseline must be accurate, or the guard ships lying
    # about the debt it carries.
    live_declared = set(declared_soak_logs(Path(".")))
    if live_declared:
        ok(not (set(BASELINE) - live_declared),
           "N5 the shipped BASELINE names no soak absent from this tree — "
           "measured against the real repo, not a fixture")

    print(f"soak-registered: self-test OK — {fired} planted controls all fire")
    return 0


if __name__ == "__main__":
    sys.exit(main())
