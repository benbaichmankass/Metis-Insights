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
cutover; the monolith was deleted 2026-10-08). `--check` (while a monolith exists) fails if
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
from src.runtime import checklist_store as _store  # noqa: E402
from src.runtime.checklist_store import (  # noqa: E402
    MONOLITH, SEQ, STORE, ChecklistError, _dump, check, load,
    render, seed, write_row,
)


# ── the write CLI: every register edit goes through here, never ad-hoc python ──
def _now_utc() -> str:
    from datetime import datetime, timezone  # noqa: PLC0415
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_value(text: str):
    """``null`` / numbers / lists / objects parse as JSON; anything else is a string."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def _vocab_problems(row: dict, header: dict) -> list[str]:
    """Grade ONE row against the file's own declared vocabulary, with the same
    functions the vocabulary guard uses (`manager_status` owns them), so a bad row
    is refused at the keyboard instead of at CI."""
    from src.runtime.manager_status import _declared_vocabulary, effective_state  # noqa: PLC0415
    vocab = _declared_vocabulary({"states": header.get("states"), "items": [row]})
    eff = effective_state(row, vocabulary=vocab)
    out: list[str] = []
    if eff.disagrees:
        out.append("`state` and the legacy `status` disagree; the file has ONE status field: `state`")
    if eff.value is not None and not eff.in_declared_vocabulary:
        out.append(f"state {eff.value!r} is not in the file's declared `states` "
                   f"({', '.join(sorted(header.get('states') or {}))})")
    if eff.value is None:
        out.append("a row needs a `state`")
    return out


def _require_seeded(repo: Path) -> dict:
    hp = repo / STORE / "_header.json"
    if not hp.is_file():
        raise ChecklistError(
            "the per-row store is not seeded (no docs/claude/work/checklist/_header.json); "
            "until the cutover the register is still edited in MANAGER-CHECKLIST.json")
    return json.loads(hp.read_text(encoding="utf-8"))


def _commit_row(row: dict, repo: Path, header: dict, by: str | None) -> Path:
    problems = _vocab_problems(row, header)
    if problems:
        raise ChecklistError(f"row {row.get('id')!r} refused: " + "; ".join(problems))
    row = dict(row, updated_at=_now_utc())
    if by:
        row["updated_by"] = by
    return _store.write_row(row, repo)


def op_add(repo: Path, row_id: str, fields: dict, by: str | None) -> Path:
    header = _require_seeded(repo)
    if _store.read_row(row_id, repo) is not None:
        raise ChecklistError(f"row {row_id!r} already exists; use `set` or `note`")
    row = {"id": row_id, "title": "", "phase": None, "state": "queued", "owner": None, "lane": None,
           "model": None, "ceiling_usd": None, "spend_usd": None, "tier": None, "prs": [],
           "blocked_on": [], "note": ""}
    row.update(fields)
    if not str(row["title"]).strip():
        raise ChecklistError("`add` needs --title")
    return _commit_row(row, repo, header, by)


def op_set(repo: Path, row_id: str, fields: dict, by: str | None) -> Path:
    header = _require_seeded(repo)
    row = _store.read_row(row_id, repo)
    if row is None:
        raise ChecklistError(f"no row {row_id!r}")
    if "id" in fields and fields["id"] != row_id:
        raise ChecklistError("a row's id cannot change")
    row.update(fields)
    return _commit_row(row, repo, header, by)


def op_note(repo: Path, row_id: str, text: str, append: bool, by: str | None) -> Path:
    row = _store.read_row(row_id, repo)
    if row is None:
        raise ChecklistError(f"no row {row_id!r}")
    return op_set(repo, row_id, {"note": (f"{row.get('note') or ''} {text}".strip() if append else text)}, by)


def op_archive_lane(repo: Path, row_id: str, by: str | None) -> Path:
    row = _store.read_row(row_id, repo)
    if row is None:
        raise ChecklistError(f"no row {row_id!r}")
    if not row.get("lane"):
        raise ChecklistError(f"row {row_id!r} has no lane to archive")
    hist = list(row.get("lane_history") or [])
    hist.append({"lane": row["lane"], "archived_at": _now_utc()})
    return op_set(repo, row_id, {"lane": None, "lane_history": hist}, by)


def _cli_write(a, repo: Path) -> int:
    by = a.by or None
    try:
        if a.cmd == "add":
            fields = {k: v for k, v in (("title", a.title), ("phase", a.phase), ("state", a.state),
                                        ("owner", a.owner), ("tier", None if a.tier is None else _parse_value(a.tier)), ("note", a.note),
                                        ("lane", a.lane)) if v is not None}
            if a.prs:
                fields["prs"] = [int(x) for x in a.prs.split(",") if x.strip()]
            path = op_add(repo, a.id, fields, by)
        elif a.cmd == "set":
            fields = {}
            for kv in a.assignments:
                if "=" not in kv:
                    raise ChecklistError(f"{kv!r}: expected key=value")
                k, v = kv.split("=", 1)
                fields[k] = _parse_value(v)
            path = op_set(repo, a.id, fields, by)
        elif a.cmd == "note":
            path = op_note(repo, a.id, a.text, a.append, by)
        elif a.cmd == "archive-lane":
            path = op_archive_lane(repo, a.id, by)
        else:  # header
            _require_seeded(repo)
            path = _store.write_header(a.key, _parse_value(a.value), repo)
    except ChecklistError as exc:
        print(f"checklist: refused -- {exc}")
        return 1
    print(f"checklist: wrote {path.relative_to(repo)}")
    return 0


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
    ap.add_argument("--repo", default=str(REPO), help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd")
    pa = sub.add_parser("add", help="add a new row")
    pa.add_argument("id")
    for flag in ("title", "phase", "state", "owner", "note", "lane", "tier", "prs"):
        pa.add_argument(f"--{flag}")
    ps = sub.add_parser("set", help="set fields on a row: key=value ... (JSON values parsed)")
    ps.add_argument("id")
    ps.add_argument("assignments", nargs="+")
    pn = sub.add_parser("note", help="set (or --append to) a row's note")
    pn.add_argument("id")
    pn.add_argument("text")
    pn.add_argument("--append", action="store_true")
    pl = sub.add_parser("archive-lane", help="move a row's lane into lane_history and null it")
    pl.add_argument("id")
    ph = sub.add_parser("header", help="set one top-level key (rare; the header is shared)")
    ph.add_argument("key")
    ph.add_argument("value")
    for sp in (pa, ps, pn, pl):
        sp.add_argument("--by", help="session id stamped on the row as updated_by")
    ph.add_argument("--by", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.cmd:
        return _cli_write(a, Path(a.repo))
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
        if not (REPO / MONOLITH).exists():
            # Post-cutover (2026-10-08): the monolith was deleted, the rows ARE the register,
            # so there is nothing to compare -- but "store unreadable" must still fail loudly.
            try:
                n = len(load()["items"])
            except (OSError, ValueError) as exc:
                print(f"checklist: store unreadable: {type(exc).__name__}: {exc}")
                return 1
            print(f"checklist: monolith retired; the per-row store is the register ({n} rows, readable)")
            return 0
        problems = check()
        for p in problems:
            print(f"  ✗ {p}")
        print("checklist: monolith == render()" if not problems else "checklist: monolith DIFFERS from the rows")
        return 1 if problems else 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
