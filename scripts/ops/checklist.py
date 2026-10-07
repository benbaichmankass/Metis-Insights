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
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STORE = Path("docs/claude/work/checklist")
MONOLITH = Path("docs/claude/work/MANAGER-CHECKLIST.json")
HEADER = "_header.json"
SEQ = "_seq"
_ID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ChecklistError(ValueError):
    pass


def _dump(obj) -> str:
    return json.dumps(obj, indent=2) + "\n"


def _row_path(store: Path, row_id: str) -> Path:
    if not isinstance(row_id, str) or not _ID_OK.match(row_id) or row_id == "_header":
        raise ChecklistError(f"row id {row_id!r} is not filename-safe ([A-Za-z0-9._-], not _header)")
    return store / f"{row_id}.json"


def _read_rows(store: Path) -> list[dict]:
    rows = []
    for p in sorted(store.glob("*.json")):
        if p.name == HEADER:
            continue
        row = json.loads(p.read_text(encoding="utf-8"))
        if row.get("id") != p.stem:
            raise ChecklistError(f"{p.name}: id {row.get('id')!r} does not match the filename")
        rows.append(row)
    # stable order: _seq, then id. A row without _seq sorts last, not first.
    rows.sort(key=lambda r: (r.get(SEQ, 1 << 60), r["id"]))
    return rows


def load(repo: Path = REPO, store: Path = STORE) -> dict:
    """The checklist in exactly today's served shape. Raises ChecklistError /
    OSError / ValueError when unreadable -- callers must not read that as empty."""
    base = repo / store
    header = json.loads((base / HEADER).read_text(encoding="utf-8"))
    rows = [{k: v for k, v in r.items() if k != SEQ} for r in _read_rows(base)]
    return {k: (rows if k == "items" else v) for k, v in header.items()}


def render(repo: Path = REPO, store: Path = STORE) -> str:
    return _dump(load(repo, store))


def write_row(row: dict, repo: Path = REPO, store: Path = STORE) -> Path:
    """Create or replace ONE row file. A new row takes `_seq` max+1; an existing
    row keeps its position."""
    base = repo / store
    path = _row_path(base, row.get("id"))
    row = dict(row)
    if path.exists():
        row[SEQ] = json.loads(path.read_text(encoding="utf-8")).get(SEQ, row.get(SEQ, 0))
    elif SEQ not in row:
        row[SEQ] = max((r.get(SEQ, 0) for r in _read_rows(base)), default=-1) + 1
    path.write_text(_dump(row), encoding="utf-8")
    return path


def seed(src: Path | None = None, repo: Path = REPO, store: Path = STORE) -> int:
    """Split a monolith into the store. Refuses duplicate ids; idempotent."""
    src = src or (repo / MONOLITH)
    data = json.loads(src.read_text(encoding="utf-8"))
    ids = [i["id"] for i in data["items"]]
    if len(ids) != len(set(ids)):
        raise ChecklistError("duplicate row ids in the monolith")
    base = repo / store
    base.mkdir(parents=True, exist_ok=True)
    for n, row in enumerate(data["items"]):
        path = _row_path(base, row["id"])
        path.write_text(_dump({**row, SEQ: n}), encoding="utf-8")
    (base / HEADER).write_text(
        _dump({k: (None if k == "items" else v) for k, v in data.items()}), encoding="utf-8")
    for p in base.glob("*.json"):  # a row deleted from the monolith leaves the store
        if p.name != HEADER and p.stem not in ids:
            p.unlink()
    return len(ids)


def check(repo: Path = REPO, store: Path = STORE) -> list[str]:
    """Why the monolith is not render(), or [] if it is."""
    try:
        want = render(repo, store)
    except (OSError, ValueError) as exc:
        return [f"store unreadable: {type(exc).__name__}: {exc}"]
    have = (repo / MONOLITH).read_text(encoding="utf-8")
    if have == want:
        return []
    h, w = json.loads(have), json.loads(want)
    hi, wi = {r["id"]: r for r in h["items"]}, {r["id"]: r for r in w["items"]}
    out = [f"row {i}: {'only in the monolith' if i not in wi else 'differs'}"
           for i in hi if i not in wi or hi[i] != wi[i]]
    out += [f"row {i}: only in the store" for i in wi if i not in hi]
    out += [f"header key {k!r} differs" for k in h if k != "items" and h.get(k) != w.get(k)]
    return out or ["bytes differ (key order / formatting) though the data is equal"]


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
