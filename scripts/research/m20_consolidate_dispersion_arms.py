#!/usr/bin/env python3
"""Consolidate every fold-dispersion arm on the trainer into one record.

Produces `docs/research/m20-fold-dispersion-arms-consolidated.jsonl` by walking
the trainer's `runtime_logs/m20_exit_head/` tree, reading each arm's
`rounds.jsonl`, and stamping the `fold_offset` each row cannot state for itself.

WHY THIS EXISTS. Before it, the fold-dispersion study's headline — *"10 of 30
screened legs change verdict under re-partitioning"* — was **22 % machine-
readable**: the committed arms file held 6 legs against a screened denominator of
27, and the other 21 lived only as prose tables in
`docs/research/m20-fold-dispersion-2026-08-15.md`. Neither the rate nor its
denominator could be re-derived. That is the same defect `rounds.jsonl` was added
to prevent, one level up (see `tests/test_exit_head_round_emits_evidence.py`).

Committed rather than left as a one-off shell paste because **an artifact whose
producer is a throwaway is unreproducible**, which is a weaker version of the
problem it was written to solve.

⚠️ THE ROWS DO NOT CARRY THEIR OWN OFFSET
(`BL-20260815-EVIDENCE-ROWS-DO-NOT-RECORD-FOLD-OFFSET`). `m20_exit_head_round.py`
stamps `total_sort` and `block_unit` on every evidence row and **not**
`fold_offset`, while `_round_meta` in the same function does record it. So the
offset is read from each arm's `round_report.json` and the provenance of that
read is kept explicit:

  * ``offset_source`` — ``round_meta`` (read from the report) or ``unavailable``
    (we could not establish it). An unreadable report yields ``fold_offset:
    null``, **never a defaulted 0** — "we did not record it" and "it was the
    control arm" are different facts, and defaulting collapses them.
  * ``dir_offset`` — the offset parsed from the directory name, kept as a
    SEPARATE field so the two can be cross-checked rather than one silently
    standing in for the other. On the 2026-08-15 corpus: 61 of 61 recovered from
    the report, **0 mismatches** against the directory.

    ⚠️ READ ``dir_offset_mismatch`` BESIDE ``dir_offset_comparable``. A bare 0
    is the SAME reading whether every arm agreed or no arm could be compared,
    and only one of those is reassuring. The layout decides which: the flat
    ``…/off8/rounds.jsonl`` states the offset in the arm dir, but the
    worktree-isolated screen writes ``…/off8/out/rounds.jsonl`` (the driver
    passes ``--out <arm>/out``), whose basename is ``out``. ``dir_offset_for``
    checks the parent for exactly that reason; before it did, every arm of that
    run resolved None and the cross-check would have been vacuous while
    reporting a clean 0. The run summary now says so in words.

RUN IT ON THE TRAINER (the arm tree lives there, not in the repo), e.g. via the
`trainer-vm-diag` relay, then transfer the output. **Transfer it compressed and
check the hash**: a first plain-text emit was silently truncated by GitHub's
comment limit at 137 of 234 rows, and was caught only because the run printed its
own row count first.

Tier-1 research tooling. Reads arm outputs; writes one JSONL. Touches nothing the
trader executes.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import sys

DEFAULT_ROOT = "/home/ubuntu/ict-trading-bot/runtime_logs/m20_exit_head"

# Fields carried through from each evidence row. Deliberately explicit rather
# than `**row`: a consolidated record whose schema drifts with whatever the
# driver last emitted cannot be compared across screens.
CARRY = ("leg", "block_unit", "mean_auc", "usable_folds", "beats_actual",
         "beats_hard", "n_oos", "verdict", "tp_geometry", "tp_cap_pct",
         "total_sort", "family")

_DIR_OFFSET = re.compile(r"off(\d+)$")


def dir_offset_for(arm_dir: str) -> object:
    """The offset parsed from the directory NAME, or None if it does not state one.

    Deliberately checks the arm dir AND its parent, because the two layouts this
    tree has used put the marker in different places::

        runtime_logs/m20_exit_head/<screen>/off8/rounds.jsonl   -> basename `off8`
        /tmp/m20_5m_wt_<ts>/off8/out/rounds.jsonl               -> basename `out`

    The second is what the 2026-08-15 worktree-isolated screen writes (the driver
    passes `--out <arm>/out`), and against a basename-only match EVERY arm in that
    run resolves None. That is not a cosmetic miss: `dir_offset` exists solely as
    an INDEPENDENT check on the `_round_meta` offset, so silently resolving it to
    None everywhere leaves `dir_offset_mismatch` at 0 over a population of ZERO
    comparisons -- a clean-looking agreement that compared nothing, which is the
    unasserted-denominator class this repo's diagnostic-provenance rule names.

    Still returns None when neither level states an offset. None means "the path
    does not say", never 0 -- the same distinction `offset_for` keeps.
    """
    base = os.path.basename(os.path.normpath(arm_dir))
    m = _DIR_OFFSET.search(base)
    if m is None:
        parent = os.path.basename(os.path.dirname(os.path.normpath(arm_dir)))
        m = _DIR_OFFSET.search(parent)
    return int(m.group(1)) if m else None


def offset_for(arm_dir: str) -> tuple[object, str]:
    """(fold_offset, offset_source) for one arm directory.

    Returns ``(None, "unavailable")`` when the round report is missing,
    unparseable, or simply does not carry the key — three ways of not knowing,
    all of which must stay distinct from a recorded 0.
    """
    rp = os.path.join(arm_dir, "round_report.json")
    if not os.path.exists(rp):
        return None, "unavailable"
    try:
        meta = (json.load(open(rp)) or {}).get("_round_meta") or {}
    except (OSError, ValueError):
        return None, "unavailable"
    if "fold_offset" not in meta:
        return None, "unavailable"
    return meta["fold_offset"], "round_meta"


def consolidate(root: str) -> tuple[list[dict], dict]:
    out: list[dict] = []
    # `dir_offset_comparable` is the DENOMINATOR for `dir_offset_mismatch`, and
    # it ships beside it because the mismatch count alone cannot distinguish
    # "every arm agreed" from "no arm was comparable". Both read 0.
    stats = {"arm_dirs": 0, "rows": 0, "meta_ok": 0, "meta_missing": 0,
             "dir_offset_comparable": 0, "dir_offset_mismatch": 0,
             "unparseable_rows": 0}
    for dirpath, _dirs, files in os.walk(root):
        if "rounds.jsonl" not in files:
            continue
        stats["arm_dirs"] += 1
        off, src = offset_for(dirpath)
        stats["meta_ok" if src == "round_meta" else "meta_missing"] += 1

        dir_off = dir_offset_for(dirpath)
        if off is not None and dir_off is not None:
            stats["dir_offset_comparable"] += 1
            if int(off) != dir_off:
                stats["dir_offset_mismatch"] += 1

        for line in open(os.path.join(dirpath, "rounds.jsonl")):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                # COUNTED, not skipped silently — a corpus that quietly drops
                # rows reports a denominator it did not measure.
                stats["unparseable_rows"] += 1
                continue
            stats["rows"] += 1
            rec = {k: row.get(k) for k in CARRY}
            rec.update(fold_offset=off, offset_source=src, dir_offset=dir_off,
                       screen=os.path.relpath(dirpath, root))
            out.append(rec)
    out.sort(key=lambda r: (r["screen"], str(r["leg"]), str(r["fold_offset"])))
    return out, stats


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--out", default="/tmp/m20_dispersion_consolidated.jsonl")
    a = ap.parse_args(argv[1:])

    if not os.path.isdir(a.root):
        print(f"arm tree not found: {a.root}\n"
              f"This runs on the TRAINER, where the arms live — not in the "
              f"repo checkout.", file=sys.stderr)
        return 2

    rows, stats = consolidate(a.root)
    if not rows:
        # Refuse to write an empty record. An empty file that EXISTS reads as a
        # measured negative to every consumer, which is precisely the zero-row
        # `rounds.jsonl` failure this study already suffered.
        print(f"NO ROWS found under {a.root} — writing nothing rather than an "
              f"empty record that would read as a measured result.",
              file=sys.stderr)
        return 3

    body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(body)

    print("STATS " + json.dumps(stats, sort_keys=True))
    # Say it in words, because `dir_offset_mismatch: 0` is the SAME reading
    # whether every arm agreed or none could be compared, and only one of those
    # is reassuring.
    _cmp, _arms = stats["dir_offset_comparable"], stats["arm_dirs"]
    if _cmp == 0:
        print(f"dir cross-check: VACUOUS — 0 of {_arms} arm dir(s) stated an "
              f"offset in their path, so `dir_offset_mismatch: 0` compared "
              f"NOTHING and is not evidence the offsets are right.")
    else:
        print(f"dir cross-check: {_cmp} of {_arms} arm dir(s) comparable, "
              f"{stats['dir_offset_mismatch']} mismatch(es).")
    for label, key in (("offsets", "fold_offset"), ("offset_source", "offset_source"),
                       ("block_unit", "block_unit")):
        print(f"{label:<14}: "
              + json.dumps(dict(collections.Counter(str(r[key]) for r in rows))))
    print(f"screens       : {len({r['screen'] for r in rows})}")
    print(f"distinct legs : {len({r['leg'] for r in rows})}")
    print(f"wrote {a.out} ({len(body)} bytes, {len(rows)} rows)")
    # The hash is the transfer check — compare it after decoding on the far side.
    print(f"sha256        : {hashlib.sha256(body.encode()).hexdigest()}")
    if stats["dir_offset_mismatch"]:
        print(f"!! {stats['dir_offset_mismatch']} arm(s) whose round_report "
              f"offset disagrees with the directory name — investigate before "
              f"trusting the record", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
