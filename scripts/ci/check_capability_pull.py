#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::capability-pull-guard (--self-test, then the scan)
"""E2 — capability build is PULLED by a held-up stage, never self-started.

Phase G of the operating-layer build. The operating model states this as the
single highest-leverage rule it has:

    *"E2 is pulled by E1, never self-started. This single rule is what redirects
    the measured 45-governance-sprints-to-2-deployments ratio."*

August ran **45 governance / hardening / observability sprints against 2
deployments**. Capability gets built because it is legible, not because a stage
is held up. The derivation grades E2 *"missing as a GOVERNED function"* — the
building happens constantly; what is absent is the **pull rule**.

So: a change that ADDS capability must carry the stage it claims to unblock,
declared as ``unblocks_stage`` on a work object in the same change.

⚠️⚠️ THE HARD PART, AND THE WHOLE REASON THIS FILE READS THE WAY IT DOES
========================================================================
**The rule is not enforceable today, and pretending otherwise would be the
worse failure.** Phase D shipped E1 and the diagnosis **REFUSES**: measured over
all 584 work objects, **6 carry an ASSESSED `blocked_on` basis — 1.0% against a
declared 50% floor** — so ``constraint.verdict`` is ``insufficient_basis`` and
``named_stage`` is ``null``. That is the correct output, not a shortfall: 578
objects carry an empty ``blocked_on`` that is *nobody having looked*, and a
stage named over that graph would describe the migration rather than the system.

Two ways to get this wrong, and this guard refuses both:

**(a) Reading the refusal as "nothing is held up, so any capability is fine."**
That inverts it. A refusal is *we do not know where the chain is stuck*, which
is strictly weaker than *nothing is stuck* — and it is the reading that would
let the guard bless every self-started capability in the repo while looking
like enforcement. Nothing this file prints may be read that way.

**(b) Enforcing a DECLARATION whose claim nothing can check.** A required field
whose truth no consumer verifies is `exit_price_source` again — written in 12
files, branched on in one. Worse here: it would fail every concurrent capability
PR today to produce a field that means nothing until the edges are written.

So enforcement is **STATE-DEPENDENT, and the state is published on every run**:

``enforcing``
    ``named_stage`` is set. A change adding capability must declare
    ``unblocks_stage``, and it must MATCH the named stage. **Fails otherwise.**
``advisory``
    ``verdict`` is ``insufficient_basis``. The declaration is REPORTED and
    graded ``unverified`` — never ``verified``, never ``ok``. Exits 0, and says
    in terms why: the diagnosis refused, so this PR's pull claim is unchecked.
``unknown``
    ``CONSTRAINT.json`` is absent or unparseable — *we could not look*. Reports;
    does not fail. Emphatically not ``advisory``, which is a MEASURED refusal.

The ``enforcing`` branch is exercised by ``--self-test`` on every invocation, so
the teeth are known to work on the day the edges make them reachable, rather
than discovered broken then. **The way to switch this guard on is to write true
``blocked_on`` edges** — not to edit this file.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
CONSTRAINT = REPO / "docs" / "claude" / "CONSTRAINT.json"
OBJECTS = "docs/claude/work/objects/"

#: Trees where a NEW file is capability. Deliberately narrow: docs, tests,
#: config and the work store itself are not capability, and sweeping them in
#: would make the rule fire on the very rows that answer it.
CAPABILITY_PREFIXES = ("scripts/", "src/", ".github/workflows/")
CAPABILITY_SUFFIXES = (".py", ".sh", ".yml", ".yaml")

#: The chain stages a pull claim may name. Mirrors `constraint_readout`'s own
#: vocabulary; a claim naming something else is not checkable.
STAGES = ("QUESTION", "EVIDENCE", "DECISION", "DEPLOYMENT", "OBSERVATION",
          "CAPABILITY", "INTEGRITY")


def _load(p: Path) -> Optional[Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 -- we could not look
        return None


def enforcement_state(constraint: Optional[dict]) -> Tuple[str, Optional[str], str]:
    """(state, named_stage, why). Three states, never collapsed."""
    if not isinstance(constraint, dict) or not isinstance(constraint.get("constraint"), dict):
        return ("unknown", None,
                "docs/claude/CONSTRAINT.json is absent or unparseable — WE COULD NOT LOOK. "
                "This is not the same as the diagnosis refusing, and must not be read as "
                "'nothing is held up'.")
    c = constraint["constraint"]
    stage = c.get("named_stage")
    if c.get("verdict") == "ok" and stage in STAGES:
        return ("enforcing", str(stage),
                f"the constraint readout names `{stage}` as the held-up stage.")
    cov, floor = c.get("assessed_coverage"), c.get("min_assessed_coverage")
    pct = f"{cov * 100:.1f}%" if isinstance(cov, (int, float)) else "?"
    fl = f"{floor * 100:.0f}%" if isinstance(floor, (int, float)) else "?"
    return ("advisory", None,
            f"the constraint diagnosis REFUSES (verdict `{c.get('verdict')}`): "
            f"{c.get('assessed')} of {c.get('population')} objects carry an assessed "
            f"`blocked_on` basis ({pct}) against a floor of {fl}, so NO stage is named. "
            f"⚠️ That is 'we do not know where the chain is stuck' — strictly weaker than "
            f"'nothing is stuck'. A pull claim cannot be verified against it.")


def changed_files(base: str) -> Tuple[List[str], List[str], str]:
    """(added, modified, state). `state` is `read` or `unavailable`."""
    try:
        out = subprocess.run(
            ["git", "diff", "--name-status", f"{base}...HEAD"],
            cwd=REPO, capture_output=True, text=True, timeout=60, check=True).stdout
    except Exception:  # noqa: BLE001
        return [], [], "unavailable"
    added, modified = [], []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        (added if status.startswith("A") else modified).append(path)
    return added, modified, "read"


def is_capability(path: str) -> bool:
    return (path.startswith(CAPABILITY_PREFIXES)
            and path.endswith(CAPABILITY_SUFFIXES)
            and not path.startswith("scripts/ci/guard_selftests"))


def declared_stages(paths: List[str], repo: Path = REPO) -> Dict[str, str]:
    """{work-object path: declared stage} over work objects the change touches."""
    out: Dict[str, str] = {}
    for p in paths:
        if not p.startswith(OBJECTS) or not p.endswith((".yaml", ".yml")):
            continue
        f = repo / p
        if not f.is_file():
            continue
        try:
            import yaml
            doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(doc, dict) and doc.get("unblocks_stage"):
            out[p] = str(doc["unblocks_stage"]).strip().upper()
    return out


def audit(*, added: List[str], modified: List[str], diff_state: str,
          constraint: Optional[dict], repo: Path = REPO) -> Tuple[int, List[str]]:
    """(exit_code, lines)."""
    state, stage, why = enforcement_state(constraint)
    L = [f"capability-pull guard: enforcement `{state}` — {why}"]

    if diff_state != "read":
        L.append("  the diff could not be read, so NOTHING was judged. That is a probe "
                 "failure, not a clean result.")
        return 0, L

    caps = sorted(p for p in added if is_capability(p))
    decls = declared_stages(added + modified, repo)

    # STATE THE POPULATION on every run — a verdict with no denominator is the
    # error this repo has a top-level rule against.
    L.append(f"  population: {len(added)} file(s) added, of which {len(caps)} are "
             f"capability under {', '.join(CAPABILITY_PREFIXES)}; "
             f"{len(decls)} work object(s) in this change declare `unblocks_stage`.")

    if not caps:
        L.append("  this change adds no new capability, so the pull rule does not apply.")
        return 0, L

    for p in caps[:12]:
        L.append(f"    + {p}")
    if len(caps) > 12:
        L.append(f"    … and {len(caps) - 12} more")

    if not decls:
        msg = ("no work object in this change declares `unblocks_stage`, so this "
               "capability names no held-up stage.")
        if state == "enforcing":
            L.append(f"  ✗ {msg} The readout names `{stage}` — declare it, or record "
                     f"why this build is pulled by something else.")
            return 1, L
        L.append(f"  ⚠️ {msg} UNVERIFIED, not approved: enforcement is `{state}`, so the "
                 f"guard cannot check a claim that was not made either. Do not read this "
                 f"pass as the capability being justified.")
        return 0, L

    for p, s in sorted(decls.items()):
        if s not in STAGES:
            L.append(f"  ✗ `{p}` declares `unblocks_stage: {s}`, which is not a chain "
                     f"stage ({', '.join(STAGES)}).")
            return 1, L

    claimed = sorted(set(decls.values()))
    if state == "enforcing":
        if stage not in claimed:
            L.append(f"  ✗ this change declares {claimed} but the constraint readout names "
                     f"`{stage}`. Capability is pulled by the HELD-UP stage, not a chosen "
                     f"one — re-argue it or fix the declaration.")
            return 1, L
        L.append(f"  ✓ VERIFIED: declares `{stage}`, which the readout names as held up.")
        return 0, L

    L.append(f"  ⚠️ declares {claimed} — recorded and UNVERIFIED. Enforcement is "
             f"`{state}`, so nothing checked whether that stage is actually held up. "
             f"The way to make this verifiable is to write true `blocked_on` edges, "
             f"not to change this guard.")
    return 0, L


# ---------------------------------------------------------------------------
def _self_test() -> int:
    ok, checks = 0, []
    REFUSING = {"constraint": {"verdict": "insufficient_basis", "named_stage": None,
                               "assessed": 6, "population": 584,
                               "assessed_coverage": 0.0103, "min_assessed_coverage": 0.5}}
    NAMING = {"constraint": {"verdict": "ok", "named_stage": "DECISION"}}

    s, stage, why = enforcement_state(REFUSING)
    checks.append(("a refusing readout is `advisory`, never `enforcing`",
                   s == "advisory" and stage is None))
    checks.append(("...and the message says the refusal is WEAKER than 'nothing is stuck'",
                   "strictly weaker" in why))
    checks.append(("an ABSENT readout is `unknown`, NOT `advisory`",
                   enforcement_state(None)[0] == "unknown"))
    checks.append(("a naming readout is `enforcing`",
                   enforcement_state(NAMING)[0] == "enforcing"))

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / OBJECTS).mkdir(parents=True)
        (root / OBJECTS / "good.yaml").write_text("id: X\nunblocks_stage: DECISION\n")
        (root / OBJECTS / "wrong.yaml").write_text("id: Y\nunblocks_stage: EVIDENCE\n")
        (root / OBJECTS / "bogus.yaml").write_text("id: Z\nunblocks_stage: BANANAS\n")
        def obj(n: str) -> str:
            return f"{OBJECTS}{n}"

        # THE TEETH. This is the branch that does not fire in production today,
        # which is exactly why it must fire here on every invocation.
        rc, _ = audit(added=["scripts/ops/new_tool.py"], modified=[], diff_state="read",
                      constraint=NAMING, repo=root)
        checks.append(("ENFORCING: capability with NO declaration FAILS", rc == 1))
        rc, _ = audit(added=["scripts/ops/new_tool.py", obj("wrong.yaml")], modified=[],
                      diff_state="read", constraint=NAMING, repo=root)
        checks.append(("ENFORCING: declaring the WRONG stage FAILS", rc == 1))
        rc, _ = audit(added=["scripts/ops/new_tool.py", obj("good.yaml")], modified=[],
                      diff_state="read", constraint=NAMING, repo=root)
        checks.append(("ENFORCING: declaring the NAMED stage passes", rc == 0))
        rc, _ = audit(added=["scripts/ops/new_tool.py", obj("bogus.yaml")], modified=[],
                      diff_state="read", constraint=NAMING, repo=root)
        checks.append(("a stage outside the chain vocabulary FAILS", rc == 1))

        rc, lines = audit(added=["scripts/ops/new_tool.py"], modified=[],
                          diff_state="read", constraint=REFUSING, repo=root)
        checks.append(("ADVISORY: an undeclared capability does NOT fail the PR", rc == 0))
        checks.append(("...and it is graded UNVERIFIED, never approved",
                       any("UNVERIFIED, not approved" in x for x in lines)))
        rc, lines = audit(added=["scripts/ops/new_tool.py", obj("good.yaml")], modified=[],
                          diff_state="read", constraint=REFUSING, repo=root)
        checks.append(("ADVISORY: even a CORRECT declaration is only `UNVERIFIED`",
                       rc == 0 and any("UNVERIFIED" in x for x in lines)))

        rc, lines = audit(added=["docs/x.md", "tests/test_x.py"], modified=[],
                          diff_state="read", constraint=NAMING, repo=root)
        checks.append(("docs and tests are not capability and never trip the rule",
                       rc == 0 and any("adds no new capability" in x for x in lines)))
        rc, lines = audit(added=[], modified=[], diff_state="unavailable",
                          constraint=NAMING, repo=root)
        checks.append(("an unreadable diff reports a PROBE FAILURE, not a clean pass",
                       rc == 0 and any("not a clean result" in x for x in lines)))
        rc, lines = audit(added=["scripts/ops/a.py"], modified=[], diff_state="read",
                          constraint=NAMING, repo=root)
        checks.append(("every run states its population",
                       any("population:" in x for x in lines)))

    for name, good in checks:
        print(f"  {'ok  ' if good else 'FAIL'}  {name}")
        ok += bool(good)
    print(f"self-test: {ok}/{len(checks)}")
    return 0 if ok == len(checks) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--base", default=None,
                    help="git ref to diff against (the PR base). Without it the guard "
                         "judges nothing and says so.")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    if not a.base:
        state, stage, why = enforcement_state(_load(CONSTRAINT))
        print(f"capability-pull guard: enforcement `{state}` — {why}")
        print("  no --base given, so no diff was judged. Nothing was checked.")
        return 0
    added, modified, dstate = changed_files(a.base)
    rc, lines = audit(added=added, modified=modified, diff_state=dstate,
                      constraint=_load(CONSTRAINT))
    for x in lines:
        print(x)
    if rc:
        print("\n::error::capability-pull guard: capability build is PULLED by a held-up "
              "stage, never self-started. Declare `unblocks_stage` on a work object in "
              "this change.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
