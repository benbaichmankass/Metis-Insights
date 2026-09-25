#!/usr/bin/env python3
"""Migration (lane E64): `docs/claude/work/PIPELINE.jsonl` (a single
append-only JSONL file) -> `docs/claude/work/pipeline/` (one immutable JSON
file per record).

WHY: every writer appended at end-of-file, so two concurrent PRs' diffs
always touched the same tail and every merge re-conflicted every other open
PR (measured 2026-09-24: 9 green lane PRs, each squash re-conflicting the
rest, ONLY on this file). See `scripts/ops/pipeline.py`'s module docstring,
"ONE FILE PER RECORD", for the full reasoning; this script only performs and
PROVES the conversion.

Usage:
    python3 scripts/ops/migrate_pipeline_to_dir.py            # dry run
    python3 scripts/ops/migrate_pipeline_to_dir.py --apply    # writes files
    python3 scripts/ops/migrate_pipeline_to_dir.py --self-test

⚠️ IDEMPOTENT AND INCREMENTAL (added the day the guard first landed, in
review). At least 6 lane PRs were already appending to the flat file when
this migration merged. Each hits a modify/delete conflict on the flat file
(deleted here, modified there), and a hand-resolved conflict can easily
choose to KEEP it -- resurrecting a flat `PIPELINE.jsonl` beside the new
directory, where nothing reads it (`pipeline.py --check` now REFUSES that
state outright -- see `_resurrected_legacy_file()`). The remedy is this
script, and it must work whether the directory is empty (first run) or
already holds records (a lane's flat file needs folding in): it moves ONLY
the flat file's records that are not ALREADY in the directory, matched on
FULL CONTENT (not id -- an id can legitimately gain a new record; a
byte-for-byte-equal record is the one that is already migrated), then
deletes the flat file.

Proof of losslessness:
  - First/full migration (directory starts empty): `pipeline.stats()` for
    the old flat file and the new directory, over the SAME `today`, must be
    IDENTICAL, and the folded item sets must be byte-for-byte identical.
  - Incremental catch-up (directory already holds records): every record in
    the flat file must be representable, by content, somewhere in the
    directory afterward -- the directory may have grown independently
    since the last migration, so exact equality is the wrong invariant here.
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


def _canon(rec: dict) -> str:
    """A canonical string for a record's FULL content -- so a legacy line
    (compact JSON) and a migrated file (pretty-printed) compare equal
    whenever their PARSED content is identical, regardless of formatting."""
    return json.dumps(rec, ensure_ascii=False, sort_keys=True)


def _existing_directory_records(new_store: Path) -> list[dict]:
    """Every record currently in the directory, raw and unfolded."""
    if not new_store.exists():
        return []
    return pipeline.read_log(new_store).raw


def migrate(apply: bool, *, old_store: Path = OLD_STORE,
            new_store: Path = NEW_STORE) -> int:
    if not old_store.exists():
        print(f"{old_store} does not exist -- nothing to migrate.")
        return 1

    today = pipeline._today()
    before = pipeline._read_legacy_jsonl(old_store)
    before_stats = pipeline.stats(before, today)
    print(f"BEFORE ({old_store}, as of {today.isoformat()}):")
    print(json.dumps(before_stats, indent=2, sort_keys=True))

    records = _raw_records(old_store)
    print(f"\n{len(records)} record(s) parsed from the flat file "
          f"({before.records} counted by the legacy reader -- "
          f"must match).")
    if len(records) != before.records:
        print("::error:: raw record count does not match the legacy reader's "
              "own count -- refusing to migrate on a disagreement with "
              "myself.")
        return 1

    existing_raw = _existing_directory_records(new_store)
    existing_keys = {_canon(r) for r in existing_raw}
    new_records = [(i, rec) for i, rec in enumerate(records)
                   if _canon(rec) not in existing_keys]
    incremental = bool(existing_raw)

    if incremental:
        print(f"\n{new_store} already holds {len(existing_raw)} record(s) — "
              f"INCREMENTAL mode: only flat-file records not already present "
              f"(matched on full content, not just id) will be migrated.")
    print(f"{len(new_records)} of {len(records)} flat-file record(s) are NOT "
          f"already in the directory" + ("." if apply else " (dry run)."))

    if not apply:
        print(f"\nDRY RUN — would write {len(new_records)} file(s) under "
              f"{new_store}. Re-run with --apply to perform the migration.")
        return 0

    new_store.mkdir(parents=True, exist_ok=True)
    base_ts = datetime.now(timezone.utc)
    for seq, (orig_i, rec) in enumerate(new_records):
        # A shared base timestamp, ordered by an explicit zero-padded index --
        # NOT wall-clock spacing -- so the original line order (what "last
        # record wins" must reproduce for a fold) survives regardless of how
        # many records there are or how fast this loop runs. Every record
        # written by `append()` from this point forward gets a REAL current
        # timestamp, strictly later than this migration's `base_ts`, so new
        # records always sort after every migrated one.
        ts = base_ts + timedelta(microseconds=seq)
        name = f"{ts.strftime('%Y%m%dT%H%M%S%f')}Z-MIGR-{orig_i:05d}{pipeline.RECORD_SUFFIX}"
        (new_store / name).write_text(
            json.dumps(rec, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")

    if not incremental:
        # First/full migration: the strict invariant -- the directory
        # started empty, so stats and folded items must match EXACTLY.
        after = pipeline.read_log(new_store)
        after_stats = pipeline.stats(after, today)
        print(f"\nAFTER ({new_store}, as of {today.isoformat()}):")
        print(json.dumps(after_stats, indent=2, sort_keys=True))

        mismatches = {k: (before_stats[k], after_stats.get(k))
                      for k in before_stats if before_stats[k] != after_stats.get(k)}
        if mismatches:
            print(f"\n::error:: stats mismatch after migration: {mismatches}")
            return 1

        if before.items != after.items:
            only_before = set(before.items) - set(after.items)
            only_after = set(after.items) - set(before.items)
            diff_content = {k for k in set(before.items) & set(after.items)
                             if before.items[k] != after.items[k]}
            print(f"\n::error:: folded item sets differ -- only_before="
                  f"{sorted(only_before)} only_after={sorted(only_after)} "
                  f"content_diff={sorted(diff_content)}")
            return 1

        print(f"\nMIGRATION OK — {len(records)} record(s) → {len(records)} "
              f"file(s), stats identical, folded items byte-for-byte "
              f"identical.")
    else:
        # Incremental catch-up: the directory may have grown independently
        # since the last migration (other lanes' PRs merging their own
        # pipeline.append() calls), so exact stats/item equality is the WRONG
        # invariant. The one that must hold is losslessness: every flat-file
        # record is now represented, by content, somewhere in the directory.
        after_raw = _existing_directory_records(new_store)
        after_keys = {_canon(r) for r in after_raw}
        missing = [rec for rec in records if _canon(rec) not in after_keys]
        if missing:
            print(f"\n::error:: {len(missing)} flat-file record(s) still "
                  f"missing from the directory after migration -- this "
                  f"should be impossible.")
            return 1
        print(f"\nMIGRATION OK — {len(new_records)} new record(s) written "
              f"(of {len(records)} in the flat file); every flat-file "
              f"record is now represented in the directory by content.")

    old_store.unlink()
    print(f"\nDeleted {old_store} — its content is now fully represented "
          f"under {new_store}.")
    return 0


def _self_test() -> int:
    import shutil
    import tempfile

    fired = 0

    def ok(cond: bool, label: str) -> None:
        nonlocal fired
        assert cond, f"control FAILED: {label}"
        fired += 1

    def item(rid: str, what: str = "x") -> dict:
        return {
            "id": rid, "what": what,
            "origin": {"kind": "audit", "ref": "#1", "rerun": "true"},
            "due_when": {"kind": "observation", "clears_when": "c"},
            "next_action": "check_observation", "state": "queued",
        }

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        old = root / "PIPELINE.jsonl"
        new = root / "pipeline"

        old_rows = [item(f"OLD-{i}") for i in range(5)]
        old.write_text("// header\n" + "\n".join(
            json.dumps(r) for r in old_rows) + "\n", encoding="utf-8")

        rc = migrate(True, old_store=old, new_store=new)
        ok(rc == 0, "first/full migration succeeds")
        ok(not old.exists(), "…and deletes the flat file")
        ok(len(list(new.glob(f"*{pipeline.RECORD_SUFFIX}"))) == 5,
           "…writing exactly one file per flat-file record")

        # ── THE PLANTED SCENARIO: a lane's flat file is resurrected by a
        # hand-resolved conflict, carrying ALL the old rows PLUS 2 new ones.
        resurrected_rows = [*old_rows, item("NEW-A"), item("NEW-B")]
        old.write_text("// header\n" + "\n".join(
            json.dumps(r) for r in resurrected_rows) + "\n", encoding="utf-8")

        before_files = set(new.glob(f"*{pipeline.RECORD_SUFFIX}"))
        rc2 = migrate(True, old_store=old, new_store=new)
        after_files = set(new.glob(f"*{pipeline.RECORD_SUFFIX}"))
        ok(rc2 == 0, "PLANTED: incremental re-migration succeeds")
        ok(not old.exists(), "…and deletes the resurrected flat file again")
        ok(len(after_files - before_files) == 2,
           "PLANTED: exactly 2 NEW record files result — the 5 old rows, "
           "already present by content, are not re-written")

        res = pipeline.read_log(new)
        ok({"OLD-0", "OLD-1", "OLD-2", "OLD-3", "OLD-4", "NEW-A", "NEW-B"}
           == set(res.items),
           "…and every row (old and new) is now in the directory")

        # ── re-running with NOTHING new is a no-op that still cleans up ────
        old.write_text("// header\n" + "\n".join(
            json.dumps(r) for r in resurrected_rows) + "\n", encoding="utf-8")
        before_files2 = set(new.glob(f"*{pipeline.RECORD_SUFFIX}"))
        rc3 = migrate(True, old_store=old, new_store=new)
        after_files2 = set(new.glob(f"*{pipeline.RECORD_SUFFIX}"))
        ok(rc3 == 0 and after_files2 == before_files2,
           "re-migrating a flat file with NOTHING new writes NOTHING new "
           "(idempotent)")
        ok(not old.exists(), "…and still deletes the now-fully-redundant "
                             "flat file")

        # ── dry run never writes and never deletes ─────────────────────────
        old.write_text("// header\n" + json.dumps(item("DRYRUN")) + "\n",
                       encoding="utf-8")
        before_files3 = set(new.glob(f"*{pipeline.RECORD_SUFFIX}"))
        rc4 = migrate(False, old_store=old, new_store=new)
        after_files3 = set(new.glob(f"*{pipeline.RECORD_SUFFIX}"))
        ok(rc4 == 0 and after_files3 == before_files3,
           "…negative control: a DRY RUN writes nothing, even with a new row "
           "present")
        ok(old.exists(), "…and never deletes the flat file")
        shutil.rmtree(new)

    print(f"migrate-pipeline-to-dir: self-test OK — {fired} planted "
          f"controls all fire")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return _self_test()
    return migrate("--apply" in argv)


if __name__ == "__main__":
    sys.exit(main())
