#!/usr/bin/env python3
"""Compute the fold-dispersion MOVER RATE from the consolidated arms record.

A "mover" is a leg whose E1 verdict changes across the `--fold-offset` arms of a
screen — the statistic the whole 2026-08-15 dispersion study reports.

WHY THIS SCRIPT EXISTS. The rate was hand-computed in a shell during one
session and quoted into prose. That is a weaker version of the defect the
consolidated record was built to fix: re-derivable in principle, not in
practice. It is also the reasoning that removed the hardcoded re-sweep base rate
from `m20_coverage_rollup.py` — *a rate baked into printed text is a claim
nothing re-derives*, and this one had already gone subtly wrong once (the doc's
`per_leg` denominator said 2 where the data says 3, because a leg screened in
`unanimity2` was never counted).

⚠️ THE DEDUP RULE IS A REAL CHOICE, NOT A DETAIL, so both are always printed.
22 legs were measured by more than one screen, and on 2 of them the *mover
verdict itself* disagrees (`gdx_pullback_1d`, `trend_donchian_sol_4h`). So:

  * **any-screen**   — a leg moved if it moved in ANY screen. One observed flip
    demonstrates instability; a later hold does not un-demonstrate it.
  * **every-screen** — a leg moved only if it moved in EVERY screen that
    measured it.

On the committed record these differ by **7.4 points** for `family_pooled`
(33.3% vs 25.9%). Printing one silently picks a side of that, so this prints
both and names them. Neither is "the" rate.

Tier-1 research tooling. Reads the record; writes nothing.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ARMS = REPO / "docs" / "research" / "m20-fold-dispersion-arms-consolidated.jsonl"


_ARM_COMPONENT = re.compile(r"off\d+$")
ROOT_SCREEN = "(root)"


def screen_of(row: dict) -> str:
    """The screen a row belongs to — the run dir, not the per-arm subdir.

    `screen` is `relpath(arm_dir, root)`, so what it contains depends on where
    the consolidator's ``--root`` was pointed, and the arms must end up in ONE
    group or no leg can ever be seen at two offsets.

    Strips trailing ARM components rather than taking ``split("/")[0]``, because
    that index assumed one specific layout::

        pull2h_20260815T095550Z/pull2h_off0   -> pull2h_20260815T095550Z   (run)
        off0/out                              -> off0                      (ARM!)

    The second is what the 2026-08-15 worktree-isolated screen produces when
    ``--root`` is the run dir itself (the driver writes ``<arm>/out``). Under the
    index rule each arm became its OWN screen, every screen-leg pair then had a
    single arm, and `rates()` excludes those as "cannot move" — so a 4-arm run
    would have reported a mover rate over ZERO comparable pairs and read as a
    clean "nothing moved". Verified a no-op on all 234 committed rows.

    An empty result means every path component was an arm marker, i.e. ``--root``
    WAS the run dir — one screen, which is exactly right. It returns
    ``ROOT_SCREEN`` rather than ``""`` so the grouping is legible in the output
    instead of appearing as a blank label. Sibling runs under a shared root keep
    their own names and never merge.
    """
    parts = [p for p in str(row.get("screen") or "").split("/") if p and p != "."]
    while parts and (parts[-1] == "out" or _ARM_COMPONENT.search(parts[-1])):
        parts.pop()
    return "/".join(parts) or ROOT_SCREEN


def load(path: Path) -> list[dict]:
    if not path.is_file():
        raise SystemExit(f"consolidated arms record not found: {path}")
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    if not rows:
        raise SystemExit(f"{path} is empty — refusing to report a rate over "
                         "no rows, which would print 0/0 as though measured")
    return rows


def group(rows: list[dict]) -> tuple[dict, dict]:
    """(screen, leg) -> {fold_offset: verdict}, plus (screen, leg) -> block_unit."""
    arms: dict = collections.defaultdict(dict)
    unit: dict = {}
    for r in rows:
        key = (screen_of(r), r.get("leg"))
        arms[key][r.get("fold_offset")] = r.get("verdict")
        unit[key] = r.get("block_unit")
    return arms, unit


def rates(rows: list[dict]) -> dict:
    arms, unit = group(rows)

    # A leg measured at ONE offset cannot move. Counting it would dilute the
    # rate with legs that were never given the chance — the denominator has to
    # be "legs that could have moved", and the excluded count is reported so a
    # reader can see it rather than infer it.
    single = {k for k, a in arms.items() if len(a) < 2}
    multi = {k: a for k, a in arms.items() if len(a) >= 2}

    per_leg: dict = collections.defaultdict(list)
    for (scr, leg), a in multi.items():
        per_leg[leg].append((scr, len(a), len(set(a.values())) > 1,
                             unit[(scr, leg)]))

    disagree = sorted(leg for leg, v in per_leg.items()
                      if len({m for _, _, m, _ in v}) > 1)

    out = {"rows": len(rows),
           "screen_leg_pairs_with_multi_arms": len(multi),
           "screen_leg_pairs_excluded_single_arm": len(single),
           "distinct_legs": len(per_leg),
           "legs_in_multiple_screens": sum(1 for v in per_leg.values() if len(v) > 1),
           "legs_whose_mover_verdict_disagrees": disagree,
           "by_rule": {}}

    for rule, fn in (("any_screen", any), ("every_screen", all)):
        tot: dict = collections.Counter()
        mov: dict = collections.Counter()
        for leg, v in per_leg.items():
            u = v[0][3] or "unknown"
            tot[u] += 1
            if fn(m for _, _, m, _ in v):
                mov[u] += 1
        out["by_rule"][rule] = {
            "by_block_unit": {u: {"legs": tot[u], "movers": mov[u]} for u in tot},
            "total": {"legs": sum(tot.values()), "movers": sum(mov.values())},
        }
    return out


def render(r: dict) -> str:
    L = ["fold-dispersion mover rate", "=" * 60,
         f"record            : {ARMS.relative_to(REPO)}",
         f"rows              : {r['rows']}",
         f"distinct legs     : {r['distinct_legs']} (legs that could move: "
         f"measured at >= 2 offsets)",
         f"EXCLUDED          : {r['screen_leg_pairs_excluded_single_arm']} "
         f"screen-leg pair(s) measured at ONE offset — a leg that cannot move "
         f"must not dilute the rate",
         f"legs in >1 screen : {r['legs_in_multiple_screens']}", ""]

    dis = r["legs_whose_mover_verdict_disagrees"]
    L.append(f"⚠️  legs whose MOVER VERDICT ITSELF disagrees across screens: "
             f"{len(dis)}")
    if dis:
        for leg in dis:
            L.append(f"      {leg}")
        L.append("    So the rate below is one draw of a statistic that has its "
                 "own dispersion.")
    L.append("")

    for rule, label in (("any_screen",
                         "ANY-SCREEN — a leg moved if it moved in ANY screen "
                         "(one flip demonstrates instability)"),
                        ("every_screen",
                         "EVERY-SCREEN — a leg moved only if it moved in EVERY "
                         "screen that measured it")):
        b = r["by_rule"][rule]
        L.append(f"  {label}")
        for u, c in sorted(b["by_block_unit"].items()):
            pct = c["movers"] / c["legs"] if c["legs"] else 0.0
            L.append(f"    {u:<16}{c['movers']:>3}/{c['legs']:<4} = {pct:>6.1%}")
        t = b["total"]
        pct = t["movers"] / t["legs"] if t["legs"] else 0.0
        L.append(f"    {'TOTAL':<16}{t['movers']:>3}/{t['legs']:<4} = {pct:>6.1%}")
        L.append("")

    L.append("BOTH RULES ARE PRINTED DELIBERATELY. They differ, and reporting "
             "one would")
    L.append("silently pick a side of that difference. Neither is 'the' rate.")
    return "\n".join(L)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit the raw dict")
    ap.add_argument("--arms", default=str(ARMS))
    a = ap.parse_args(argv[1:])
    r = rates(load(Path(a.arms)))
    print(json.dumps(r, indent=2, sort_keys=True) if a.json else render(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
