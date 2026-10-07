#!/usr/bin/env python3
"""research-queue-id-bands -- a unit a PR ADDS must have a free id, in the right band.

WHY (PI-20261005-RQ-ID-RACE-0001). ``queue_replenish.py`` (generated units) and hand-authored units
minted ids from one ``RQ-YYYYMMDD-NNN`` counter and neither could see the other's open PR, so a
long-lived PR collided with main once the generator caught up (2026-10-05, 653..661, renumbered by
hand). The two now mint in DISJOINT bands: generated 001-899 (``queue_replenish.HAND_FLOOR``), hand
900-999 (``scripts/research/next_rq_id.py``). This guard enforces it on every unit file the PR adds:

  C1  the id must not already exist on the base under that path (a real collision),
  C2  a unit carrying a ``generated:`` block must be < 900,
  C3  a unit WITHOUT one (hand-authored) must be >= 900 -- only for ids dated on or after
      ``BAND_FROM_DAY``, so units already in flight on open PRs are not retro-failed.

Only files the PR ADDS are graded (git diff --diff-filter=A against the merge-base). Exit 0 clean,
1 findings, 2 could not look (no base) -- never reported as clean.

    python3 scripts/ci/check_research_queue_id_bands.py --base origin/main
    python3 scripts/ci/check_research_queue_id_bands.py --self-test
"""
# wiring: scripts/ci/run_guards.py::research-queue-id-bands (--self-test + --base origin/{base_ref})
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
HAND_FLOOR = 900            # keep equal to scripts/research/queue_replenish.py::HAND_FLOOR
BAND_FROM_DAY = "20261008"
_ID = re.compile(r"^RQ-(\d{8})-(\d{3})$")
_GEN = re.compile(r"^generated:", re.M)


def grade(added: Dict[str, str], on_base: set) -> List[str]:
    """{path: text} for the unit files a PR adds; ``on_base`` = those paths already on the base."""
    out: List[str] = []
    for path, text in sorted(added.items()):
        stem = Path(path).stem
        m = _ID.match(stem)
        if not m:
            continue
        day, n = m.group(1), int(m.group(2))
        if path in on_base:
            out.append(f"C1 {path}: id {stem} already exists on the base -- two units would share it. "
                       f"Renumber with `python3 scripts/research/next_rq_id.py --fetch`.")
            continue
        if day < BAND_FROM_DAY:
            continue
        generated = bool(_GEN.search(text))
        if generated and n >= HAND_FLOOR:
            out.append(f"C2 {path}: a GENERATED unit must be numbered < {HAND_FLOOR} (900-999 is the hand band).")
        if not generated and n < HAND_FLOOR:
            out.append(f"C3 {path}: a hand-authored unit must be numbered >= {HAND_FLOOR} (1-899 is the "
                       f"generator's band, and collides once it catches up). Use "
                       f"`python3 scripts/research/next_rq_id.py --fetch`.")
    return out


def _git(*a: str) -> Optional[str]:
    p = subprocess.run(["git", *a], cwd=str(REPO), capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else None


def collect(base: str):
    mb = _git("merge-base", base, "HEAD")
    if mb is None:
        return None
    names = _git("diff", "--name-only", "--diff-filter=A", mb.strip(), "HEAD", "--", "research/queue/")
    if names is None:
        return None
    added, on_base = {}, set()
    for path in names.split():
        if not path.endswith(".yaml"):
            continue
        txt = _git("show", f"HEAD:{path}")
        if txt is None:
            return None
        added[path] = txt
        if subprocess.run(["git", "cat-file", "-e", f"{base}:{path}"], cwd=str(REPO),
                          capture_output=True).returncode == 0:
            on_base.add(path)
    return added, on_base


def _self_test() -> int:
    ok = True

    def check(label, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  {label}" + ("" if good else f" (got {got!r}, want {want!r})"))

    gen = "id: x\ngenerated:\n  key: k\n"
    hand = "id: x\nstatus: queued\n"
    q = "research/queue/"
    check("a hand unit in the hand band is clean", grade({q + "RQ-20261009-901.yaml": hand}, set()), [])
    check("a generated unit in the generated band is clean", grade({q + "RQ-20261009-005.yaml": gen}, set()), [])
    r = grade({q + "RQ-20261009-653.yaml": hand}, set())
    check("THE 2026-10-05 SHAPE: a hand unit at 653 is refused (C3)", len(r) == 1 and r[0].startswith("C3"), True)
    r = grade({q + "RQ-20261009-950.yaml": gen}, set())
    check("a generated unit in the hand band is refused (C2)", len(r) == 1 and r[0].startswith("C2"), True)
    r = grade({q + "RQ-20261009-901.yaml": hand}, {q + "RQ-20261009-901.yaml"})
    check("an id already on the base is a collision (C1) in any band", len(r) == 1 and r[0].startswith("C1"), True)
    check("units dated before BAND_FROM_DAY keep their ids (no retro-fail)",
          grade({q + "RQ-20261007-700.yaml": hand}, set()), [])
    check("a non-unit file is ignored", grade({q + "README.yaml": hand}, set()), [])
    check("HAND_FLOOR matches the generator's",
          HAND_FLOOR, __import__("importlib").import_module("scripts.research.queue_replenish").HAND_FLOOR)
    print("research-queue-id-bands self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    sys.path.insert(0, str(REPO))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=None)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return _self_test()
    if not a.base:
        print("research-queue-id-bands: no --base, nothing was checked (COULD NOT LOOK, not clean)")
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _dirty_tree  # noqa: E402,PLC0415 — path shim above
    _dirty_tree.warn()
    got = collect(a.base)
    if got is None:
        print(f"research-queue-id-bands: could not diff against {a.base} (COULD NOT LOOK, not clean)")
        return 2
    added, on_base = got
    findings = grade(added, on_base)
    for f in findings:
        print(f"::error::research-queue-id-bands: {f}")
    print(f"research-queue-id-bands: {len(added)} added unit file(s) graded, {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
