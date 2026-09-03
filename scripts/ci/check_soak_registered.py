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

WHAT "REGISTERED" MEANS, AND WHY IT IS NOT "MENTIONED"
------------------------------------------------------
A row in `docs/claude/OPEN-ITEMS.json` carrying a `soak` block whose `log`
names this soak. A probe command mentioning the name does NOT count: a probe
READS a soak, an alarm says what READY means and can tell a dead soak from a
patient one. Four of the sixteen logs were "mentioned" and none of them could
answer either question — which is precisely why counting mentions would make
this guard pass while changing nothing.

Exit codes: 0 clean · 1 an unregistered, unbaselined soak (or a stale baseline
entry) · 2 we could not look.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_REGISTER = Path("docs/claude/OPEN-ITEMS.json")
_SCAN_ROOTS = ("src",)

#: A soak log name as it appears in a writer: `"<name>_soak.jsonl"`.
_LOG_RE = re.compile(r'["\']([a-z0-9_]+_soak)\.jsonl["\']')

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


def registered_soak_logs(root: Path) -> tuple[set[str], str | None]:
    """Log names carrying a `soak` block. Returns (names, error-or-None).

    An error is propagated rather than swallowed: an unreadable register means
    we could not establish what is registered, which must exit `could not look`
    and never be reported as "nothing is registered" — that would fail every
    soak in the tree on a JSON typo.
    """
    p = root / _REGISTER
    if not p.is_file():
        return set(), f"{_REGISTER} is missing"
    try:
        items = json.loads(p.read_text(encoding="utf-8"))["items"]
    except Exception as exc:  # noqa: BLE001
        return set(), f"{_REGISTER} did not parse: {type(exc).__name__}: {exc}"

    names: set[str] = set()
    for row in items:
        soak = row.get("soak") if isinstance(row, dict) else None
        if isinstance(soak, dict):
            log = str(soak.get("log") or "").strip()
            if log:
                names.add(log[:-6] if log.endswith(".jsonl") else log)
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
            f"SOAK NOT REGISTERED: `{name}` is declared in {where} and no row in "
            f"{_REGISTER} carries a `soak` block naming it.\n"
            f"    A soak nobody registered accrues to NOBODY: the review backlogs "
            f"are not due-list sources, so nothing will surface it when it is "
            f"ready and nothing will notice if it stops writing.\n"
            f"    FIX: add a `soak` block to the row that owns this work — "
            f"{{log, declared_at, ready_when, min_matching}}. `ready_when` states "
            f"what READY means in DATA (a `probe_lib` condition such as "
            f"`verdicts_differ=true`), never in elapsed days; `check_every_days` "
            f"already carries the timer.")

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

    def plant(logs, register_soaks, *, baseline=None):
        """Build a fake tree. Returns (rc, problems) under a patched BASELINE."""
        td = Path(tempfile.mkdtemp())
        (td / "src/runtime").mkdir(parents=True)
        for i, name in enumerate(logs):
            (td / f"src/runtime/w{i}.py").write_text(
                f'SOAK_LOG_NAME = "{name}.jsonl"\n', encoding="utf-8")
        (td / "docs/claude").mkdir(parents=True)
        (td / _REGISTER).write_text(json.dumps({"items": [
            {"id": f"OI-{n}", "soak": {"log": n, "declared_at": "2026-09-02",
                                       "ready_when": "x=1", "min_matching": 1}}
            for n in register_soaks]}), encoding="utf-8")
        global BASELINE
        saved = BASELINE
        BASELINE = dict.fromkeys(baseline or [], "pre-2026-09-02")
        try:
            return check(td)
        finally:
            BASELINE = saved

    # ⚠️ THE CONTROL THAT MATTERS: the guard must find a POSITIVE. A guard that
    # only ever reports clean is indistinguishable from one that scans nothing —
    # RULE ONE: show the probe can find a positive before trusting it is quiet.
    rc, probs = plant(["new_soak"], [])
    ok(rc == 1 and any("SOAK NOT REGISTERED" in p for p in probs),
       "a NEW soak writer with no register row FAILS the guard — this is the "
       "planted positive, and without it a permanently-green guard would be "
       "indistinguishable from one that scans nothing")
    ok(any("`new_soak`" in p for p in probs),
       "and the failure NAMES the soak, so the fix is actionable without a hunt")
    ok(any("ready_when" in p for p in probs),
       "and it names the field that carries the threshold, not just 'add a row'")

    rc, _ = plant(["new_soak"], ["new_soak"])
    ok(rc == 0, "the same soak WITH a register block passes — the fix works")

    rc, _ = plant(["old_soak"], [], baseline=["old_soak"])
    ok(rc == 0, "a pre-existing soak on the dated debt list passes")

    rc, probs = plant(["old_soak", "new_soak"], [], baseline=["old_soak"])
    ok(rc == 1 and len([p for p in probs if "NOT REGISTERED" in p]) == 1
       and "new_soak" in probs[0],
       "⚠️ a baselined soak does NOT excuse a new one beside it — the debt list "
       "grandfathers exactly the names on it and nothing else. This is the "
       "control against the baseline quietly becoming a blanket exemption")

    rc, probs = plant([], [], baseline=["ghost_soak"])
    ok(rc == 1 and any("STALE BASELINE" in p for p in probs),
       "a BASELINE entry whose writer is gone FAILS — the list may only shrink, "
       "and a name outliving its writer is a slot a future soak could reuse")

    # `.jsonl` on the registered name must not break the match, and a probe
    # command MENTIONING a soak must not count as registering it.
    td = Path(tempfile.mkdtemp())
    (td / "src/runtime").mkdir(parents=True)
    (td / "src/runtime/w.py").write_text('P = "x_soak.jsonl"\n', encoding="utf-8")
    (td / "docs/claude").mkdir(parents=True)
    (td / _REGISTER).write_text(json.dumps({"items": [
        {"id": "A", "soak": {"log": "x_soak.jsonl"}}]}), encoding="utf-8")
    ok("x_soak" in registered_soak_logs(td)[0],
       "a `log` written with the .jsonl suffix still matches — the register and "
       "the writer spell it differently and neither is wrong")

    (td / _REGISTER).write_text(json.dumps({"items": [
        {"id": "A", "probe": {"cmd": ["probe_soak.py", "--path", "name=x_soak"]}}]}),
        encoding="utf-8")
    ok(registered_soak_logs(td)[0] == set(),
       "⚠️ a probe command MENTIONING a soak does NOT register it. A probe is a "
       "READER; an alarm says what READY means and can tell a dead soak from a "
       "patient one. Four of the sixteen live soaks were 'mentioned' this way "
       "and could answer neither question — counting mentions would make this "
       "guard pass while changing nothing")

    # An unreadable register is COULD NOT LOOK, never "nothing is registered".
    (td / _REGISTER).write_text("{not json", encoding="utf-8")
    rc, probs = check(td)
    ok(rc == 2 and any("could not look" in p for p in probs),
       "⚠️ an unreadable register exits COULD NOT LOOK (2), never 1 — reading it "
       "as 'nothing is registered' would fail every soak in the tree on a JSON "
       "typo, which is the collapse this whole family of code exists to refuse")

    # The live tree's own baseline must be accurate, or the guard ships lying
    # about the debt it carries.
    live_declared = set(declared_soak_logs(Path(".")))
    if live_declared:
        ok(not (set(BASELINE) - live_declared),
           "the shipped BASELINE names no soak that is absent from this tree — "
           "measured against the real repo, not a fixture")

    print(f"soak-registered: self-test OK — {fired} planted controls all fire")
    return 0


if __name__ == "__main__":
    sys.exit(main())
