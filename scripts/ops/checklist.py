#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::checklist-store-guard (--self-test).
"""THE CHECKLIST STORE: one file per row, one loader, one generator.

WHY (PI-20261004-APBY4NTV-0003, manager-approved 2026-10-07): MANAGER-CHECKLIST.json
is ONE JSON array, so any two PRs that append or edit rows collide on GitHub --
MEASURED over 41 commits / 5 days: 20 appended rows (the end-of-array comma/brace
lines), 11 touched the shared header fields. GitHub's server-side merge ignores
`.gitattributes` merge drivers, so only the data layout can fix it. This is the
same cure `scripts/ops/pipeline.py` applied to its store: distinct files for
distinct rows, so two branches never touch the same bytes.

LAYOUT (docs/claude/work/checklist/):
  _header.json   every top-level key EXCEPT the rows, in the monolith's key order;
                 `"items": null` marks where the rows go.
  <ROW-ID>.json  one row, verbatim, plus a private `_seq` int that fixes its
                 position (rows keep today's order; a new row takes max+1 and
                 two concurrent appends may share a _seq -- ties break on id,
                 and being different FILES they never conflict).

`load()` returns EXACTLY the shape the monolith has today (`_seq` stripped, key
order preserved), so every reader can swap `json.load(MONOLITH)` for `load()`
and the served shape is identical. `render()` is byte-identical to the monolith
(`json.dumps(indent=2)` + newline), which is how the monolith can stay the file
the SPA route and the old readers read until they are migrated.

CUTOVER: `--seed` splits the CURRENT monolith into rows + header (once, at the
cutover; the manager keeps editing the monolith until then). `--check` fails if
the monolith differs from `render()` -- the rows become the truth.

Usage: checklist.py --self-test | --seed | --render | --check | --write-row FILE
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.runtime.checklist_store import *  # noqa: E402,F401,F403
from src.runtime.checklist_store import (  # noqa: E402
    MONOLITH, SEQ, STORE, ChecklistError, _dump, check, load,
    render, seed, write_row,
)


def _self_test() -> int:
    ok = True

    def ck(label: str, cond: bool) -> None:
        nonlocal ok
        ok &= bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")

    mono = {"_comment": ["a"], "schema_version": 2, "updated_at": "t", "items": [
        {"id": "B", "title": "second-in-file", "state": "queued", "prs": []},
        {"id": "A1", "title": "é unicode", "state": "done", "blocked_on": [{"kind": "x"}]},
        {"id": "C", "title": "c", "state": "queued"}], "tail_key": {"k": 1}}
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        (repo / MONOLITH.parent).mkdir(parents=True)
        (repo / MONOLITH).write_text(_dump(mono), encoding="utf-8")
        ck("seed splits every row", seed(repo=repo) == 3)
        ck("load() returns EXACTLY the monolith's data, row order and key order included",
           load(repo) == mono and list(load(repo)) == list(mono)
           and [r["id"] for r in load(repo)["items"]] == ["B", "A1", "C"])
        ck("render() is byte-identical to the monolith", render(repo) == (repo / MONOLITH).read_text(encoding="utf-8"))
        ck("check() is clean on a faithful monolith", check(repo) == [])
        ck("_seq never leaks into the served shape", all(SEQ not in r for r in load(repo)["items"]))
        write_row({"id": "D", "title": "new", "state": "queued"}, repo)
        ck("a new row appends at the END", [r["id"] for r in load(repo)["items"]][-1] == "D")
        write_row({"id": "A1", "title": "edited", "state": "done"}, repo)
        ck("editing a row keeps its position",
           [r["id"] for r in load(repo)["items"]] == ["B", "A1", "C", "D"]
           and load(repo)["items"][1]["title"] == "edited")
        ck("check() names a hand-edited monolith", any("row D" in m for m in check(repo)))
        # two concurrent appenders pick the same _seq: distinct files, deterministic order
        for rid in ("Z9", "Y8"):
            (repo / STORE / f"{rid}.json").write_text(_dump({"id": rid, SEQ: 50}), encoding="utf-8")
        ck("tied _seq breaks on id, deterministically",
           [r["id"] for r in load(repo)["items"]][-2:] == ["Y8", "Z9"])
        try:
            write_row({"id": "../evil"}, repo)
        except ChecklistError:
            ck("a path-shaped id is refused", True)
        else:
            ck("a path-shaped id is refused", False)
        (repo / STORE / "Q.json").write_text(_dump({"id": "other"}), encoding="utf-8")
        try:
            load(repo)
        except ChecklistError:
            ck("a row whose id disagrees with its filename is refused, not skipped", True)
        else:
            ck("a row whose id disagrees with its filename is refused, not skipped", False)
    print("checklist store self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--seed", action="store_true", help="split the current monolith into the store")
    ap.add_argument("--render", action="store_true", help="print the generated monolith")
    ap.add_argument("--check", action="store_true", help="fail if the monolith != render()")
    ap.add_argument("--write-row", metavar="FILE", help="create/replace one row from a JSON file")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if a.seed:
        print(f"checklist: seeded {seed()} row(s) into {STORE}")
        return 0
    if a.write_row:
        print(write_row(json.loads(Path(a.write_row).read_text(encoding="utf-8"))))
        return 0
    if a.render:
        sys.stdout.write(render())
        return 0
    if a.check:
        problems = check()
        for p in problems:
            print(f"  ✗ {p}")
        print("checklist: monolith == render()" if not problems else "checklist: monolith DIFFERS from the rows")
        return 1 if problems else 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
