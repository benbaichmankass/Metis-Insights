#!/usr/bin/env python3
"""Union one sweep corpus into another, newest measurement wins.

SERVES BOTH SWEEP FAMILIES — `--corpus m20` (default) and `--corpus e35`.
The filename says m20 because that is the failure it was built for and the
m20 workflow references it by path; it is NOT m20-only. If you arrived here
looking for an e35 corpus union, this is it — do not write a second one
(`RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED`). The two families differ in
exactly one thing, which extractor owns `measurement_key`, and that is the
only thing `--corpus` selects; the merge rule below is identical for both.

WHY THIS EXISTS
---------------
`.github/workflows/m20-exit-lever-sweep.yml` cannot push to `main` (protected),
so a default-branch dispatch retargets the corpus to `claude/m20-sweep-corpus`.
That branch never merges `main`, and the conflict path does
`git reset --hard "origin/$TARGET"` before re-deriving — which replaces the
worktree corpus with the branch's stale copy. Every row `main` has and the
branch does not is dropped from the branch copy, silently, on every run.

Measured 2026-08-17 before this script existed: `main` 1264 rows, branch 1004,
**360 rows on main alone**. A hand union closed it once (#9823); the next
dispatch would have re-opened it. This script is the durable half.

THE MERGE RULE IS `sweep_generated_at`, NOT "the incoming side is fresher"
-------------------------------------------------------------------------
The extractor's own merge (`m20_corpus_extract.main`) lets `fresh` supersede
unconditionally, which is correct for artefacts→corpus: a re-sweep genuinely
re-measures the cell. It is WRONG corpus→corpus. Measured on the same pair: of
904 shared measurement keys, 19 differed, and on **all 19** it was `main` that
was newer (2026-08-15 vs 2026-08-13) and complete (8 `live_tp_reach_r_*` keys
vs 0). A side-wins union would have re-dropped precisely the schema
`BL-20260816-CORPUS-CONFLICT-REDERIVE-RUNS-THE-STALE-BRANCH-EXTRACTOR`
was filed to stop losing — the same defect, reintroduced by its own follow-up.

A MISSING TIMESTAMP IS NOT GUESSED
----------------------------------
Every row in both corpora carried `sweep_generated_at` when this was written,
but a row predating the field must not be resolved by assumption. Where the
timestamp cannot decide (either side missing it, or an exact tie), the tie is
broken ONLY by a strict superset of keys — objectively more information, no
judgement — and otherwise the union REFUSES (exit 2) naming the key. "We could
not compare" is its own outcome, not a licence to pick a side.

Identity is the selected family's own `measurement_key` — imported, never
re-derived, so this can never drift from what that extractor considers the same
measurement. Both sides of a union are keyed with the SAME function: two
corpora from different families have no comparable identity and must never be
unioned, which is why `--corpus` is chosen once rather than sniffed per row.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

TIMESTAMP_FIELD = "sweep_generated_at"

# Which extractor owns "the same measurement" for each corpus family. The value
# is the MODULE NAME, resolved lazily in `key_fn` — never imported at module
# scope. That is load-bearing, not style: the m20 sweep workflow's conflict
# re-derive copies ONLY `m20_corpus_extract.py` onto the runner's path, so an
# eager `import e35_corpus_extract` here would break the recovery path at
# exactly the moment it is needed. Pinned by
# `test_module_import_is_lazy_so_the_m20_runner_path_still_works`.
CORPUS_EXTRACTORS = {
    "m20": "m20_corpus_extract",
    "e35": "e35_corpus_extract",
}
DEFAULT_CORPUS = "m20"


def key_fn(corpus: str):
    """Return the `measurement_key` callable that owns identity for `corpus`.

    Imported from the family's own extractor, never re-derived here, so this
    can never drift from what that extractor considers the same measurement.
    The two families key DIFFERENTLY — m20 returns a tuple, e35 a string — and
    that is fine: the union only ever needs them hashable and comparable within
    one corpus. It must never compare keys ACROSS families, which is why the
    corpus is chosen once, at the top, and applied to both sides.
    """
    try:
        module = CORPUS_EXTRACTORS[corpus]
    except KeyError:
        raise ValueError(
            f"unknown corpus {corpus!r}; known: "
            f"{', '.join(sorted(CORPUS_EXTRACTORS))}") from None
    return __import__(module).measurement_key


class AmbiguousUnion(RuntimeError):
    """Two rows share a measurement key and nothing can order them."""


def _load(path: Path) -> list[dict]:
    rows: list[dict] = []
    malformed = 0
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # COUNTED, never skipped silently — a corpus quietly shedding rows
            # to a parse error shrinks its own denominator invisibly. Same
            # disposition as the extractor's own merge.
            malformed += 1
    if malformed:
        print(f"warning: {path}: {malformed} malformed line(s) dropped",
              file=sys.stderr)
    return rows


def _pick(a: dict, b: dict, key: tuple) -> dict:
    """Return whichever of `a`/`b` is the row to KEEP for this key.

    `a` is the incumbent (from `--into`), `b` the challenger (from `--from`).
    Ordering is by `sweep_generated_at`; a tie or a missing stamp falls through
    to a strict-superset test, and then refuses.
    """
    if a == b:
        return a
    ta, tb = a.get(TIMESTAMP_FIELD), b.get(TIMESTAMP_FIELD)
    if isinstance(ta, str) and isinstance(tb, str) and ta != tb:
        return a if ta > tb else b

    # The timestamp could not decide. Only an objective superset breaks the
    # tie — MORE recorded fields, not a preference for a side.
    ka, kb = set(a), set(b)
    if ka > kb:
        return a
    if kb > ka:
        return b
    raise AmbiguousUnion(
        f"cannot order two rows for measurement key {key!r}: "
        f"{TIMESTAMP_FIELD} is {ta!r} vs {tb!r} and neither row's field set is "
        "a strict superset of the other. Refusing rather than picking a side.")


def union_rows(into: list[dict], incoming: list[dict],
               measurement_key=None) -> tuple[list[dict], dict]:
    """Union `incoming` into `into`. Returns (rows, stats).

    `into`'s ORDER is preserved and its rows come first, so when nothing on the
    incoming side is newer the result is `into` plus appended rows — a purely
    additive diff, which is what makes the change reviewable.

    `measurement_key` defaults to the m20 family's, so every pre-existing
    caller keeps its exact behaviour; pass `key_fn("e35")` for that corpus.
    """
    if measurement_key is None:
        measurement_key = key_fn(DEFAULT_CORPUS)
    by_key_incoming: dict = {}
    for row in incoming:
        by_key_incoming[measurement_key(row)] = row

    out: list[dict] = []
    replaced = 0
    for row in into:
        key = measurement_key(row)
        challenger = by_key_incoming.get(key)
        if challenger is None:
            out.append(row)
            continue
        kept = _pick(row, challenger, key)
        if kept is not row:
            replaced += 1
        out.append(kept)

    into_keys = {measurement_key(r) for r in into}
    appended = [r for r in incoming if measurement_key(r) not in into_keys]
    out.extend(appended)

    keys = [measurement_key(r) for r in out]
    if len(keys) != len(set(keys)):
        raise AmbiguousUnion(
            f"union produced {len(keys) - len(set(keys))} duplicate measurement "
            "key(s); the corpus would over-count its own population.")

    return out, {
        "into_rows": len(into),
        "incoming_rows": len(incoming),
        "shared_keys": len(into_keys & set(by_key_incoming)),
        "replaced_by_incoming": replaced,
        "appended_from_incoming": len(appended),
        "total_rows": len(out),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--into", required=True,
                    help="corpus to update in place (the incumbent side)")
    ap.add_argument("--from", dest="src", required=True,
                    help="corpus to union in (the challenger side)")
    ap.add_argument("--corpus", default=DEFAULT_CORPUS,
                    choices=sorted(CORPUS_EXTRACTORS),
                    help="which sweep family owns measurement identity "
                         f"(default: {DEFAULT_CORPUS}). BOTH sides of the "
                         "union are keyed with this one function — a corpus "
                         "may never be unioned against another family.")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the union without writing")
    a = ap.parse_args(argv)

    into_path, src_path = Path(a.into), Path(a.src)
    if not into_path.exists():
        print(f"error: --into does not exist: {into_path}", file=sys.stderr)
        return 1
    if not src_path.exists():
        # NOT a silent no-op: the caller asked to union a file that is not
        # there, and reporting success would read as "nothing to merge".
        print(f"error: --from does not exist: {src_path}", file=sys.stderr)
        return 1

    try:
        rows, stats = union_rows(_load(into_path), _load(src_path),
                                 measurement_key=key_fn(a.corpus))
    except AmbiguousUnion as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not a.dry_run:
        into_path.write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))

    # Labels name what was COMPUTED: `replaced` counts keys where the incoming
    # row won on sweep_generated_at, `appended` counts keys absent from --into.
    print(
        f"corpus union{' (dry-run)' if a.dry_run else ''} "
        f"[corpus={a.corpus}]: "
        f"into {stats['into_rows']} + from {stats['incoming_rows']} "
        f"-> {stats['total_rows']} rows; "
        f"shared keys: {stats['shared_keys']}, "
        f"replaced by newer incoming: {stats['replaced_by_incoming']}, "
        f"appended: {stats['appended_from_incoming']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
