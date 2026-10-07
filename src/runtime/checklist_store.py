"""The per-row checklist store: loader, generator, seed, check (stdlib only).

Lives under ``src/runtime`` so BOTH the web API (``GET /api/bot/work/checklist``)
and the ``scripts/`` readers import one implementation; the CLI and self-test are
``scripts/ops/checklist.py``. Layout, cutover and rationale (PI-20261004-APBY4NTV-0003)
are documented there.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STORE = Path("docs/claude/work/checklist")
MONOLITH = Path("docs/claude/work/MANAGER-CHECKLIST.json")
HEADER = "_header.json"
SEQ = "_seq"
_ID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


__all__ = ["ChecklistError", "HEADER", "MONOLITH", "MONOLITH_NAME", "REPO", "SEQ", "STORE",
           "HISTORY_PATHS", "check", "exists", "is_checklist_path", "load", "load_at", "load_path", "render", "seeded", "seed", "write_row"]


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
    return _load_dir(repo / store)


MONOLITH_NAME = MONOLITH.name


def is_checklist_path(path) -> bool:
    """Is ``path`` the (possibly absent) monolith file? Readers that were handed a
    PATH (``--checklist``, a test fixture root, ``sr.CHECKLIST_PATH``) ask this to
    decide whether to read it as a file or through the store."""
    return Path(path).name == MONOLITH_NAME


def _store_for(path) -> Path:
    return Path(path).parent / STORE.name


def seeded(path) -> bool:
    """Has the per-row store been cut over for the monolith at ``path``?"""
    return (_store_for(path) / HEADER).is_file()


def exists(path) -> bool:
    """Is there a checklist at ``path`` -- the monolith file OR a seeded store?
    Distinct from *readable*: callers keep their absent/unreadable split."""
    return Path(path).is_file() or seeded(path)


def load_path(path):
    """Drop-in for ``json.loads(Path(path).read_text())`` on the checklist: reads the
    per-row store when it is seeded next to ``path`` (rows are the truth after the
    cutover), else the monolith file. Raises OSError / ValueError exactly as the
    inline form did (ChecklistError is a ValueError), so existing ``except`` blocks
    and their absent/unreadable split are unchanged."""
    path = Path(path)
    if seeded(path):
        return _load_dir(_store_for(path))
    return json.loads(path.read_text(encoding="utf-8"))


def _load_dir(base: Path) -> dict:
    header = json.loads((base / HEADER).read_text(encoding="utf-8"))
    rows = [{k: v for k, v in r.items() if k != SEQ} for r in _read_rows(base)]
    return {k: (rows if k == "items" else v) for k, v in header.items()}


HISTORY_PATHS = (str(STORE), str(MONOLITH))
"""Pass both to ``git log -- <paths>``: a row's history is its own file after the
cutover, the monolith's before it."""


def load_at(repo, ref: str):
    """The checklist as of git ``ref``, in the served shape: ``None`` when neither the
    store nor the monolith exists there ("absent", not "empty"); raises ValueError
    when one exists but cannot be read. Uses ONE ``git cat-file --batch`` per call, so
    walking hundreds of revisions stays cheap."""
    repo = str(repo)

    def git(*args):
        return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)

    head = git("show", f"{ref}:{STORE}/{HEADER}")
    if head.returncode != 0:
        mono = git("show", f"{ref}:{MONOLITH}")
        return None if mono.returncode != 0 else json.loads(mono.stdout)
    names = [n for n in git("ls-tree", "-r", "--name-only", ref, "--", str(STORE)).stdout.splitlines()
             if n.endswith(".json") and not n.endswith(f"/{HEADER}")]
    rows = []
    if names:
        out = subprocess.run(["git", "-C", repo, "cat-file", "--batch"],
                             input="".join(f"{ref}:{n}\n" for n in names).encode(),
                             capture_output=True)
        buf, pos = out.stdout, 0
        for n in names:
            nl = buf.index(b"\n", pos)
            meta = buf[pos:nl].split()
            if len(meta) != 3 or meta[1] != b"blob":
                raise ChecklistError(f"{ref}:{n}: unreadable ({buf[pos:nl][:80]!r})")
            size = int(meta[2])
            rows.append(json.loads(buf[nl + 1:nl + 1 + size]))
            pos = nl + 1 + size + 1
    rows.sort(key=lambda r: (r.get(SEQ, 1 << 60), r["id"]))
    rows = [{k: v for k, v in r.items() if k != SEQ} for r in rows]
    return {k: (rows if k == "items" else v) for k, v in json.loads(head.stdout).items()}


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
