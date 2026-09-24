#!/usr/bin/env python3
"""ONE-TIME migration (lane E64): `docs/claude/work/PIPELINE.jsonl` (a single
append-only JSONL file) -> `docs/claude/work/pipeline/` (one immutable JSON
file per record).

WHY: every writer appended at end-of-file, so two concurrent PRs' diffs
always touched the same tail and every merge re-conflicted every other open
PR (measured 2026-09-24: 9 green lane PRs, each squash re-conflicting the
rest, ONLY on this file). See `scripts/ops/pipeline.py`'s module docstring,
"ONE FILE PER RECORD", for the full reasoning; this script only performs and
PROVES the one-time conversion.

Usage:
    python3 scripts/ops/migrate_pipeline_to_dir.py            # dry run
    python3 scripts/ops/migrate_pipeline_to_dir.py --apply    # writes files

Proof of losslessness: prints `pipeline.stats()` for the old flat file and
the new directory, over the SAME `today`, and refuses (exit 1) if any field
differs. This is the artifact the lane's PR quotes as evidence, not a
re-assertion of it.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ops"))
import pipeline  # noqa: E402

OLD_STORE = REPO / "docs" / "claude" / "work" / "PIPELINE.jsonl"
NEW_STORE = REPO / pipeline.STORE


def _raw_records(old_store: Path) -> list[dict]:
    """Every JSON record in the old file, in original line order. Skips the
    `//` header/comment lines, exactly as `pipeline._read_legacy_jsonl` does."""
    out: list[dict] = []
    for line in old_store.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        out.append(json.loads(s))
    return out


def migrate(apply: bool) -> int:
    if not OLD_STORE.exists():
        print(f"{OLD_STORE} does not exist -- nothing to migrate.")
        return 1
    if NEW_STORE.exists() and any(NEW_STORE.iterdir()):
        print(f"::error:: {NEW_STORE} already exists and is non-empty -- "
              f"refusing to migrate into it twice.")
        return 1

    today = pipeline._today()
    before = pipeline._read_legacy_jsonl(OLD_STORE)
    before_stats = pipeline.stats(before, today)
    print(f"BEFORE ({OLD_STORE}, as of {today.isoformat()}):")
    print(json.dumps(before_stats, indent=2, sort_keys=True))

    records = _raw_records(OLD_STORE)
    print(f"\n{len(records)} record(s) parsed from the flat file "
          f"({before.records} counted by the legacy reader -- "
          f"must match).")
    if len(records) != before.records:
        print("::error:: raw record count does not match the legacy reader's "
              "own count -- refusing to migrate on a disagreement with "
              "myself.")
        return 1

    if not apply:
        print(f"\nDRY RUN — would write {len(records)} file(s) under "
              f"{NEW_STORE}. Re-run with --apply to perform the migration.")
        return 0

    NEW_STORE.mkdir(parents=True, exist_ok=True)
    base_ts = datetime.now(timezone.utc)
    for i, rec in enumerate(records):
        # A shared base timestamp, ordered by an explicit zero-padded index --
        # NOT wall-clock spacing -- so the original line order (which is what
        # "last record wins" must reproduce) survives regardless of how many
        # records there are or how fast this loop runs. Every record written
        # by `append()` from this point forward gets a REAL current timestamp,
        # which is later than this migration's `base_ts` by construction, so
        # new records always sort after every migrated one.
        ts = base_ts + timedelta(microseconds=i)
        name = f"{ts.strftime('%Y%m%dT%H%M%S%f')}Z-MIGR-{i:05d}{pipeline.RECORD_SUFFIX}"
        (NEW_STORE / name).write_text(
            json.dumps(rec, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")

    after = pipeline.read_log(NEW_STORE)
    after_stats = pipeline.stats(after, today)
    print(f"\nAFTER ({NEW_STORE}, as of {today.isoformat()}):")
    print(json.dumps(after_stats, indent=2, sort_keys=True))

    mismatches = {k: (before_stats[k], after_stats.get(k))
                  for k in before_stats if before_stats[k] != after_stats.get(k)}
    if mismatches:
        print(f"\n::error:: stats mismatch after migration: {mismatches}")
        return 1

    # Item-for-item equality, not just the summary counts.
    if before.items != after.items:
        only_before = set(before.items) - set(after.items)
        only_after = set(after.items) - set(before.items)
        diff_content = {k for k in set(before.items) & set(after.items)
                         if before.items[k] != after.items[k]}
        print(f"\n::error:: folded item sets differ -- only_before="
              f"{sorted(only_before)} only_after={sorted(only_after)} "
              f"content_diff={sorted(diff_content)}")
        return 1

    print(f"\nMIGRATION OK — {len(records)} record(s) → {len(records)} file(s), "
          f"stats identical, folded items byte-for-byte identical.")
    print(f"\nNext steps (not done by this script): "
          f"`git rm {OLD_STORE.relative_to(REPO)}` and "
          f"`git add {NEW_STORE.relative_to(REPO)}`.")
    return 0


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    return migrate(apply)


if __name__ == "__main__":
    sys.exit(main())
