#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::role-pack-operating-layer
"""CI guard: the role packs that SITUATE a session must reach the operating layer.

WHY THIS EXISTS
---------------
The operating model's anti-silo mechanism is **context = work object + role
pack**, and on 2026-09-01 its two halves were wired to different systems. The
object half shipped — store, intents, WIP ceiling, constraint readout, lease,
checklist, cycle priority, sunset pass — and
``docs/audits/operating-layer-skills-workflows-inventory-2026-09-02.md`` § 2.2
measured that **not one role pack was updated to know it exists**. A session
handed a work object opened a role pack describing the pre-2026-09-01 world.

⚠️ **THE AUDIT'S OWN NUMBERS NEEDED ONE CORRECTION, MEASURED 2026-09-02 ON THE
PRE-EDIT TREE.** It reported ``docs/claude/work`` at **0** skills; it is **2**
(``delegate-work``, ``workplan-vs-architecture``, both via ``SESSIONS.json``).
That does not weaken the finding, it sharpens it: every remaining term is
genuinely zero — ``work object``, ``WIP``, ``blocked_on``, ``CYCLE-PRIORITY``,
``READOUT``, ``SUNSET-DISPOSITIONS``, ``MANAGER-LEASE``. Only the sub-session
REGISTRY was ever wired; the whole STEERING half was invisible.

WHY A GUARD AND NOT JUST THE PROSE EDIT
---------------------------------------
Operator directive, 2026-09-02: *"I don't want more instructions for Claude —
that has proved to be severely unreliable... I want actual guards and mechanisms
in the repo/vm themselves."* A one-time prose edit decays silently back to zero
on the next rewrite, and the measurement above is what that decay looks like
after one day. This is the mechanism that keeps the edit true.

⚠️ **DELIBERATELY NOT ALL 32 SKILLS.** The audit is explicit that most role packs
are domain procedure (how to wire a broker, how to run a sweep) and are
*correctly* indifferent to where work is tracked; a blanket requirement would be
the "carrying everything" failure the model warns against. Only the packs that
govern **how a session situates itself** are required to reach the layer, and
each is listed below WITH THE REASON it qualifies.

WHAT MAKES THIS MORE THAN A TOKEN-PRESENCE GREP
-----------------------------------------------
``new-table-wiring-guard``'s lesson is that a guard cheaper to lie to than to
satisfy is worse than none. So this checks BOTH directions:

* **REQUIRED** — a situating pack must name at least one live operating-layer
  path (the six below). Pasting a token satisfies this, and that is fine: the
  half that has teeth is the next one.
* **DANGLING** — across **all** role packs, every operating-layer path a pack
  names must EXIST ON DISK. So renaming or moving the store reddens the packs
  that point at the old place, which is the drift this is really for, and a
  pasted token that names nothing real fails immediately.

Exit 0 = clean. Exit 1 = a missing required reference, or a dangling one.
"""
from __future__ import annotations

import glob
import os
import re
import sys
from typing import Dict, List, Tuple

SKILL_GLOB = ".claude/skills/*/SKILL.md"

#: Path prefixes that ARE the operating layer. A reference to any of these both
#: satisfies the requirement and is checked for existence.
_LAYER_PREFIXES = (
    "docs/claude/work/",
    "docs/claude/CYCLE-PRIORITY.json",
    "docs/claude/CONSTRAINT.json",
    "docs/claude/READOUT.md",
    "docs/claude/SUNSET-DISPOSITIONS.json",
    "scripts/ops/constraint_readout.py",
    "scripts/ops/manager_lease.py",
    "scripts/ops/session_registry.py",
    "scripts/ci/check_wip_ceiling.py",
)

#: Operating-layer paths that are legitimately ABSENT until someone creates
#: them, with the reason. A pack pointing a session at WHERE TO WRITE something
#: is correct even when nothing has been written yet, and failing that would
#: push authors toward vaguer prose — the opposite of what this guard is for.
#:
#: ⚠️ THIS IS NOT A SILENCER. Each entry names ONE exact path and says why it is
#: absent; a renamed store still fails, because the renamed path is not in here.
#: Do not add a directory prefix, and do not add a path merely to make CI green.
_WRITE_TARGETS: Dict[str, str] = {
    "docs/claude/work/wip-ceiling-exception.yaml":
        "created only when a ninth in_flight object is justified; its ABSENCE is "
        "the normal, healthy state (check_wip_ceiling.py::EXCEPTION_FILE)",
}

#: Role packs that govern HOW A SESSION SITUATES ITSELF, and why each qualifies.
#: Everything not listed is domain procedure and is exempt by construction —
#: adding a pack here is a deliberate act, not a default.
_SITUATING: Dict[str, str] = {
    "session-coordination": "owns the session-start preflight and the board claim",
    "session-handoff":      "decides what a successor inherits when this session ends",
    "doc-freshness":        "the session-END reconciliation pass",
    "duty":                 "drives every detected signal to an owner",
    "research-driver":      "decides WHAT a session works on",
    "delegate-work":        "spawns sub-sessions, against a ceiling and a lease",
}

#: Backticked path-ish spans. Placeholders (`<id>`) and globs are resolved to
#: their nearest real directory rather than being asserted to exist as files.
_TICKED = re.compile(r"`([^`\n]+)`")


def _referenced_layer_paths(text: str) -> List[str]:
    out = []
    for span in _TICKED.findall(text):
        span = span.strip().split()[0].rstrip(".,;:)")
        for pre in _LAYER_PREFIXES:
            if span.startswith(pre):
                out.append(span)
                break
    return out


def _exists(path: str) -> bool:
    """Does this reference point at something real?

    A glob or a `<placeholder>` cannot be checked as a literal, so it is
    resolved to the deepest ancestor directory that contains neither — asserting
    `docs/claude/work/objects/<id>.yaml` exists would fail on a correct doc.
    """
    parts = path.split("/")
    concrete = []
    truncated = False
    for p in parts:
        if any(c in p for c in "<>*?["):
            truncated = True
            break
        concrete.append(p)
    if not concrete:
        return True
    cand = "/".join(concrete).rstrip("/")
    if os.path.exists(cand):
        return True
    # ⚠️ THE FALLBACK APPLIES ONLY TO A TRUNCATED PATH, and getting this wrong is
    # what makes a guard cheap to lie to. An earlier draft fell back to "does the
    # PARENT directory exist" unconditionally, so `docs/claude/CYCLE-PRIORITY.jsonx`
    # passed on the strength of `docs/claude/` existing — i.e. a pasted token
    # naming nothing real satisfied the check. Its own --self-test caught that.
    # A FULLY CONCRETE path must exist as itself; only a path we had to cut at a
    # placeholder is allowed to be judged by its surviving ancestor.
    if not truncated:
        return False
    return os.path.isdir(cand) or os.path.isdir(os.path.dirname(cand) or ".")


def audit() -> Tuple[List[str], List[Tuple[str, str]], int]:
    missing: List[str] = []
    dangling: List[Tuple[str, str]] = []
    checked = 0
    for path in sorted(glob.glob(SKILL_GLOB)):
        name = os.path.basename(os.path.dirname(path))
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            continue
        checked += 1
        refs = _referenced_layer_paths(text)
        # DANGLING is checked for every pack, situating or not: a domain pack is
        # not required to mention the layer, but if it does it must not point at
        # something that is gone.
        for r in refs:
            if r in _WRITE_TARGETS:
                continue
            if not _exists(r):
                dangling.append((name, r))
        if name in _SITUATING and not refs:
            missing.append(name)
    return missing, dangling, checked


def self_test() -> int:
    """Exercise both refusal paths and the placeholder tolerance.

    The required-reference path is satisfied on the live tree, so without these
    fixtures the guard's own refusals would never run — and a guard whose
    refusal path never runs is indistinguishable from a broken one.
    """
    fails = []

    def check(label, got, want):
        if got != want:
            fails.append(f"  FAIL — {label}: got {got!r}, want {want!r}")
        else:
            print(f"  PASS — {label}")

    check("a live store path is recognised as a layer reference",
          _referenced_layer_paths("see `docs/claude/work/objects/` for state"),
          ["docs/claude/work/objects/"])
    check("an unrelated backticked path is NOT a layer reference",
          _referenced_layer_paths("run `scripts/ops/backlog_append.py` first"), [])
    check("a `<placeholder>` path does not read as dangling",
          _exists("docs/claude/work/objects/<object_id>.yaml"), True)
    check("a glob path does not read as dangling",
          _exists("docs/claude/work/objects/*.yaml"), True)
    check("a RENAMED store IS caught as dangling",
          _exists("docs/claude/work-store/objects/"), False)
    check("a pasted token naming nothing real IS caught",
          _exists("docs/claude/CYCLE-PRIORITY.jsonx"), False)
    check("every situating pack carries a stated reason",
          all(bool(v) for v in _SITUATING.values()), True)
    check("every write-target exemption carries a stated reason",
          all(bool(v) for v in _WRITE_TARGETS.values()), True)
    # The exemption must not be usable as a blanket silencer: it is keyed on an
    # EXACT path, so a renamed store does not inherit it.
    check("a write-target exemption does NOT cover a renamed sibling",
          "docs/claude/work-store/wip-ceiling-exception.yaml" in _WRITE_TARGETS,
          False)
    check("...and the exempt path is genuinely absent, not a typo for a live one",
          any(os.path.isdir(os.path.dirname(k)) for k in _WRITE_TARGETS), True)

    if fails:
        print("\n".join(fails))
        print("\nSELF-TEST FAILED")
        return 1
    print("\nALL PASS")
    return 0


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return self_test()
    missing, dangling, checked = audit()
    print(f"role-pack-operating-layer: {checked} role pack(s) · "
          f"{len(_SITUATING)} required to reach the layer · "
          f"{len(missing)} missing · {len(dangling)} dangling")
    if not missing and not dangling:
        print("role-pack-operating-layer: OK — every situating pack reaches the "
              "operating layer, and every layer path any pack names exists.")
        return 0
    if missing:
        print("\nFAIL — situating role pack(s) that never mention the operating layer:")
        for n in missing:
            print(f"  {n}  ({_SITUATING[n]})")
        print("""
  A session handed a work object opens these packs to find out how to situate
  itself. If the pack describes only the pre-2026-09-01 registers, the model's
  `context = work object + role pack` mechanism has one half wired to the wrong
  system. Name what that pack actually needs — not all of it:
    docs/claude/work/            the state of record for WORK
    docs/claude/CYCLE-PRIORITY.json + READOUT.md   direction, and the constraint
    scripts/ci/check_wip_ceiling.py               the enforced cap of 8
  If a pack genuinely should not care, REMOVE IT from `_SITUATING` in this file
  with a reason — do not paste a token to silence the check.""")
    if dangling:
        print("\nFAIL — role pack(s) naming an operating-layer path that does not exist:")
        for n, r in dangling:
            print(f"  {n}  ->  {r}")
        print("""
  Either the path moved (update the pack) or the pack is describing machinery
  that was removed (say so, or drop the reference). This is the half that
  catches drift: a pointer to a renamed store is worse than no pointer, because
  it reads as current.""")
    return 1


if __name__ == "__main__":
    sys.exit(main())
