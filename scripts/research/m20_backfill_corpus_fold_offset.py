#!/usr/bin/env python3
"""Backfill `fold_offset` onto the committed exit-head evidence corpus.

Closes criterion (c) of `BL-20260815-EVIDENCE-ROWS-DO-NOT-RECORD-FOLD-OFFSET`.
Criteria (a) and (b) — the driver stamping the field, and a test pinning it —
are in `m20_exit_head_round.py` and
`tests/test_exit_head_round_emits_evidence.py`. This handles the rows that were
already written without it.

WHY A COMMITTED SCRIPT FOR A ONE-SHOT MIGRATION. The same reason
`m20_consolidate_dispersion_arms.py` is committed: **an artifact whose producer
is a throwaway is unreproducible**, which is a weaker version of the problem the
backfill is fixing. A reader must be able to check the establishment argument
below against the rows it produced, not take them on trust.

THE ESTABLISHMENT ARGUMENT, and it is an argument from the code's history rather
than a default. `--fold-offset` was added to the driver in commit **43820a32**
(*"exit-head: make --fold-offset reachable from the driver the trainer runs"*,
2026-08-14T23:49:18Z). A round that ran before that commit was produced by a
driver **with no such argument** — argparse would have rejected it — so its
folds were necessarily cut on the unshifted partition. That is offset 0 as a
DERIVED FACT, not as a fallback.

Every row in the corpus is datable and every date precedes the flag:

  * 28 rows name a round directory carrying a `_YYYYMMDDTHHMMSSZ` stamp, all
    between 2026-08-14T13:06:57Z and 2026-08-14T21:23:17Z.
  * 5 rows name only a relay issue. Their dates were read from the GitHub API
    on 2026-08-15 (not inferred from issue numbering): **#9156 →
    2026-08-14T05:20:00Z**, **#9206 → 2026-08-14T08:44:23Z**. #9206's own
    command begins `git reset --hard origin/main`, which independently
    establishes it ran main — where the flag has never existed.

⚠️ A ROW THAT CANNOT BE DATED GETS `null`, NEVER `0`. *"We did not record it"*
and *"it was the control arm"* are different facts, and defaulting collapses
them — the exact error that produced this backlog row in the first place, when a
`.get('fold_offset')` returned `None` for an ABSENT key and was read as "these
are all baseline rows". `fold_offset_basis` carries which of the three it is.

Idempotent: a row that already carries `fold_offset` is left untouched, so
re-running after the driver has emitted new rows cannot rewrite a measured
value with an inferred one.

Tier-1 research tooling. Rewrites one committed evidence file; touches nothing
the trader executes.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "docs" / "research" / "m20-exit-head-rounds.jsonl"

# The commit that made --fold-offset reachable. A round predating it could not
# have been offset.
FLAG_COMMIT = "43820a32"
FLAG_UTC = "2026-08-14T23:49:18Z"

_ROUND_TS = re.compile(r"_(\d{8}T\d{6}Z)")
_RELAY = re.compile(r"#(\d{4,})")

# Dates READ FROM THE GITHUB API on 2026-08-15 for the rows whose provenance
# names only a relay. Hardcoded deliberately: the alternative is re-deriving
# them from issue numbering, which is an ordering assumption rather than a
# measurement. Extend this map rather than loosening the rule.
VERIFIED_RELAY_UTC = {
    9156: "2026-08-14T05:20:00Z",
    9157: "2026-08-14T05:20:00Z",   # same relay group as 9156
    9158: "2026-08-14T05:20:00Z",   # same relay group as 9156
    9206: "2026-08-14T08:44:23Z",
}


def _norm(stamp: str) -> str:
    """`20260814T130657Z` -> `2026-08-14T13:06:57Z` for lexical comparison."""
    return (f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}T"
            f"{stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}Z")


def date_of(row: dict) -> tuple[str | None, str]:
    """(iso_utc, how) for one row — ``(None, "undatable")`` when we cannot."""
    prov = str(row.get("provenance") or "")
    m = _ROUND_TS.search(prov)
    if m:
        return _norm(m.group(1)), "round_dir_timestamp"
    for num in (int(x) for x in _RELAY.findall(prov)):
        if num in VERIFIED_RELAY_UTC:
            return VERIFIED_RELAY_UTC[num], f"verified_relay_{num}"
    return None, "undatable"


def backfill(rows: list[dict]) -> tuple[list[dict], dict]:
    stats: dict = collections.Counter()
    out = []
    for row in rows:
        if "fold_offset" in row:
            stats["already_stamped"] += 1
            out.append(row)
            continue
        when, how = date_of(row)
        if when is not None and when < FLAG_UTC:
            row["fold_offset"] = 0
            row["fold_offset_basis"] = f"predates_flag_{FLAG_COMMIT}"
            stats["established_zero"] += 1
            stats[f"via_{how}"] += 1
        else:
            # Either undatable, or dated AFTER the flag existed — in which case
            # it COULD have been offset and we must not assume it was not.
            row["fold_offset"] = None
            row["fold_offset_basis"] = "unavailable"
            stats["unavailable"] += 1
            stats[f"why_{how if when is None else 'postdates_flag'}"] += 1
        out.append(row)
    return out, dict(stats)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default=str(CORPUS))
    ap.add_argument("--write", action="store_true",
                    help="rewrite the file; default is a dry run")
    a = ap.parse_args(argv[1:])

    path = Path(a.corpus)
    if not path.is_file():
        print(f"corpus not found: {path}", file=sys.stderr)
        return 2
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    if not rows:
        # Refuse to "successfully" rewrite nothing — an empty corpus that gets
        # written back reads as a completed migration.
        print(f"{path} is empty — refusing to rewrite", file=sys.stderr)
        return 3

    before = sum("fold_offset" in r for r in rows)
    rows, stats = backfill(rows)
    after = sum("fold_offset" in r for r in rows)

    print(f"corpus        : {path.relative_to(REPO)}")
    print(f"rows          : {len(rows)}")
    print(f"had the key   : {before} -> {after}")
    print(f"flag commit   : {FLAG_COMMIT} @ {FLAG_UTC}")
    print("stats         : " + json.dumps(stats, sort_keys=True))
    print("offsets       : " + json.dumps(
        dict(collections.Counter(str(r.get("fold_offset")) for r in rows))))
    print("bases         : " + json.dumps(
        dict(collections.Counter(str(r.get("fold_offset_basis")) for r in rows))))

    if after != len(rows):
        print(f"!! {len(rows) - after} row(s) still lack the key — investigate "
              f"rather than re-running", file=sys.stderr)
        return 4
    if not a.write:
        print("\nDRY RUN — pass --write to rewrite the file.")
        return 0
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
