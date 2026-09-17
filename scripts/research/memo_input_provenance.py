#!/usr/bin/env python3
# wiring: manual-only - this is an INSTRUMENT a memo author runs once, plus a
# census of a DATED population (docs/research/ as of 2026-09-12). A scheduled
# runner would re-answer the census against a moving population and turn a
# recorded exposure count into a drifting one; and the fingerprint half is
# called at the moment a memo is written, which no cron can know.
"""Can a journal-derived memo table be reproduced — and if not, WHY not? (MI-278 U26)

THE ROW
--------
`BL-20260912-U15S-OWN-CODE-NO-LONGER-REPRODUCES-U15S-OWN-PUBLISHED-TABLE-ONE-CONTROL-PACKAGE-FLIPPED-AND-NOBODY-WOULD-HAVE-KNOWN`
names two causes for a published control_pre cell reading 30 today against a
published 31, and says they are indistinguishable:

  (a) a self-contradicting fan-out package adjudicated differently because the
      two pulls ORDERED its rows differently;
  (b) a trade whose `exit_price` or bracket was RE-STAMPED between the pulls,
      which the protection-repair and netting-attribution paths both do.

Its `resolution_criteria` offers two exits, and only the second is reachable:
*"Either the 30-vs-31 difference is ATTRIBUTED to one of the two causes with
evidence, or **memos gain a retained/hashed input so the next such difference is
attributable when it happens**."* The first is closed — U15's original pull was
not retained and cannot be recovered. This file is the second.

⚠️ **THIS DOES NOT ATTRIBUTE THE 30-vs-31, AND CANNOT.** It makes the NEXT one
attributable. Reporting it as closing the historical question would be exactly
the unprovenanced claim the row was filed about.

WHY A TWO-PART DIGEST — IT IS THE WHOLE POINT
-----------------------------------------------
A single digest over a pull answers *did anything change*, which is the question
already answerable by re-running and seeing a different number. It cannot separate
(a) from (b). Two digests can:

  `rowset_digest`  — order-INDEPENDENT, over the multiset of per-row digests
  `order_digest`   — over the SEQUENCE of row ids as the pull delivered them

  same rowset, same order  -> `reproduces`
  same rowset, diff order  -> `reordered`         <- hypothesis (a), and ONLY (a)
  diff rowset              -> `content_changed`   <- hypothesis (b), and the
                                                     changed rows are NAMED **only
                                                     if both sides carry per-row
                                                     digests** (see below)

⚠️ **A MULTISET, NOT A SET.** Deduplicating per-row digests would make a pull that
lost a duplicate row hash identically to one that did not — the failure mode is
silent and in the reassuring direction. `n_duplicate_ids` ships beside it.

⚠️ **`fields_covered` IS PART OF THE FINGERPRINT AND IS COMPARED FIRST.** A digest
over a different field set is not a smaller answer to the same question, it is an
answer to a different one — so two fingerprints hashing different fields grade
`undecidable_field_sets_differ`, never `content_changed`. And
`fields_present_not_covered` ships on every fingerprint, because *we did not hash
it* is not *it did not change*.

⚠️ **A ROW WITH NO ID IS COUNTED, NEVER DROPPED.** It contributes to the rowset
digest (its content is still content) but cannot be localised or ordered, so
`n_rows_without_id` is reported and a comparison naming changed rows says so.

STATES, NEVER COLLAPSED
-------------------------
⚠️ **NAMING THE ROWS IS A SEPARATE CAPABILITY FROM DETECTING THE CHANGE, AND THE
STANZA DID NOT CARRY IT.** `render_stanza` emitted no per-row digests, so a
`content_changed` graded against a MEMO -- the only comparison a later session can
make, and the one this module exists for -- derived its per-row answer from a map
that was not there. MEASURED on two real declared memos (U32 and U38, a genuine
re-stamp: same ids, same `order_digest`, different `rowset_digest`) it reported
`rows_changed/added/removed` all `[]` beside `rows_not_localisable: 0`; against a
live pull it reported the ENTIRE population as `rows_added`. Both are confident
falsehoods and they point at OPPOSITE causes. `compare()` now returns `None` with a
`localisation` state, and `render_stanza(include_row_digests=True)` is what buys
the naming back (MI-278 U51).

Localisation: `localised` · `declared_carries_no_row_digests` ·
`observed_carries_no_row_digests` · `neither_side_carries_row_digests`. On
`reproduces` and `reordered` the empty row lists are ENTAILED by `rowset_digest`
equality and need no per-row map, so `rows_basis` says which produced them.

Comparison: `reproduces` · `reordered` · `content_changed` ·
`undecidable_field_sets_differ` · **`no_declared_input`** (*we could not look* —
the memo declares nothing, which is the CURRENT state of every memo in the repo).

Census: `declares_input` · `journal_cited_no_declaration` (the exposure) ·
**`not_detected`** — deliberately NOT *"not journal-derived"*: the probe is a token
match over prose and a memo that computed a table from a pull without saying so is
indistinguishable from one that never touched the journal. Calling that a clean
negative is the unasserted-denominator defect (sub-class C) this repo files.

WHAT THE REPO ALREADY DECIDED, AND WHY THIS IS A RECURRENCE
-------------------------------------------------------------
`docs/research/RESEARCH-WORKFLOW-ARCHITECTURE-2026-08-27.md` § R1 already requires
every result row to carry **"on what data — dataset id + version + fingerprint"**,
and names two open rows as the reason. Both are still `kept_open`:
`BL-20260810-SWEEP-VERDICTS-DO-NOT-RECORD-THEIR-DATASET` and
`BL-20260812-SWEEP-CORPUS-RECORDS-NO-FRAME-FINGERPRINT`. The U15 row is the THIRD
instance of one class, filed 16 days after the contract that forbids it. That the
earlier fix did not hold IS the finding.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from typing import Any

COMPARE_STATES = (
    "reproduces",
    "reordered",
    "content_changed",
    "undecidable_field_sets_differ",
    "no_declared_input",
)

CENSUS_STATES = (
    "declares_input",
    "journal_cited_no_declaration",
    "not_detected",
)

# The tokens that make a memo's journal derivation VISIBLE in its own prose.
# Every one is reported per memo in `signals`, so a census number can be re-read
# under a narrower or wider rule without re-running the probe.
JOURNAL_SIGNALS = (
    "/api/diag/journal",
    "diag_fetch",
    "trade_journal.db",
    "journal pull",
    "journal tail",
)

STANZA_OPEN = "<!-- input-provenance"
STANZA_CLOSE = "-->"

# Keys a stanza must carry to be a fingerprint rather than a note. `pulled_at`
# and `source` are deliberately NOT required for the COMPARISON to work -- they
# are for a human -- but a stanza missing a digest cannot be compared at all.
REQUIRED_STANZA_KEYS = ("rowset_digest", "order_digest", "fields_covered", "n_rows")


def _canon(value: Any) -> str:
    """One serialisation for a field value, so two pulls cannot differ on typing.

    A journal pull crosses JSON, so 1 and 1.0 and "1" are all reachable for the
    same underlying column depending on the writer. Hashing `repr` would make a
    type change look like a value change; hashing `str` would make 1 and "1"
    collide. This canonicalises NUMBERS to a fixed repr and leaves everything
    else distinguishable by its JSON type tag.
    """
    if value is None:
        return "\x00null"
    if isinstance(value, bool):
        return "\x00bool:" + ("1" if value else "0")
    if isinstance(value, (int, float)):
        # repr of a float is round-trip exact in CPython; int is exact by nature.
        return "\x00num:" + repr(float(value))
    if isinstance(value, str):
        return "\x00str:" + value
    return "\x00json:" + json.dumps(value, sort_keys=True, separators=(",", ":"))


def _row_digest(row: dict, fields: tuple[str, ...]) -> str:
    h = hashlib.sha256()
    for f in fields:
        h.update(f.encode())
        h.update(_canon(row.get(f)).encode())
    return h.hexdigest()


def fingerprint(
    rows: list[dict],
    *,
    id_field: str = "id",
    fields: list[str] | tuple[str, ...] | None = None,
    group_field: str | None = None,
    source: str | None = None,
    pulled_at: str | None = None,
) -> dict:
    """Two-part fingerprint of a pull, plus everything needed to read it honestly.

    `fields=None` covers the UNION of every key present across the rows, sorted.
    That is the widest honest default: a narrower set is a choice and must be
    made explicitly, because it decides what a later comparison can see.
    """
    present: set[str] = set()
    for r in rows:
        present.update(r.keys())

    if fields is None:
        covered = tuple(sorted(present))
    else:
        covered = tuple(sorted(fields))

    row_digests: dict[str, str] = {}
    multiset: list[str] = []
    order: list[str] = []
    without_id = 0
    dup_ids = 0

    for r in rows:
        d = _row_digest(r, covered)
        multiset.append(d)
        rid = r.get(id_field)
        if rid is None:
            without_id += 1
            continue
        key = str(rid)
        order.append(key)
        if key in row_digests:
            dup_ids += 1
            # Keep BOTH: a duplicate id whose two rows differ is a real finding,
            # and last-wins would hide it. The suffix is stable per occurrence.
            row_digests[f"{key}#dup{dup_ids}"] = d
        else:
            row_digests[key] = d

    # Optional GROUP membership. Off by default and NEVER defaulted to a value:
    # a fingerprint that did not look at grouping must say so, because a memo
    # whose unit is the PACKAGE has a third failure mode a row-level digest
    # cannot see (see `groups_changed` in `compare`).
    group_members: dict[str, list[str]] | None = None
    group_digest: str | None = None
    if group_field:
        gm: dict[str, list[str]] = {}
        for r in rows:
            g = r.get(group_field)
            if g is None:
                continue
            gm.setdefault(str(g), []).append(str(r.get(id_field)))
        group_members = {k: sorted(v) for k, v in sorted(gm.items())}
        gh = hashlib.sha256()
        for k, v in group_members.items():
            gh.update(k.encode())
            gh.update(b"\x00")
            gh.update(",".join(v).encode())
            gh.update(b"\x01")
        group_digest = "sha256:" + gh.hexdigest()

    rowset = hashlib.sha256()
    for d in sorted(multiset):
        rowset.update(d.encode())

    order_h = hashlib.sha256()
    for key in order:
        order_h.update(key.encode())
        order_h.update(b"\x00")

    return {
        "rowset_digest": "sha256:" + rowset.hexdigest(),
        "order_digest": "sha256:" + order_h.hexdigest(),
        "row_digests": row_digests,
        "id_order": order,
        "id_first": order[0] if order else None,
        "id_last": order[-1] if order else None,
        "fields_covered": list(covered),
        "fields_present_not_covered": sorted(present - set(covered)),
        "n_rows": len(rows),
        "n_rows_without_id": without_id,
        "n_duplicate_ids": dup_ids,
        "id_field": id_field,
        "group_field": group_field,
        "group_digest": group_digest,
        "group_members": group_members,
        "n_groups": None if group_members is None else len(group_members),
        "n_multi_row_groups": None if group_members is None else sum(1 for v in group_members.values() if len(v) > 1),
        "source": source,
        "pulled_at": pulled_at,
    }


def _shared_order_preserved(declared: dict, observed: dict) -> bool | None:
    """Did the rows present in BOTH pulls keep their RELATIVE order?

    ⚠️ THIS EXISTS BECAUSE `also_reordered` IS CONFOUNDED WITH POPULATION CHANGE,
    AND THE CONFOUND WAS FOUND BY RUNNING ON LIVE DATA, NOT BY READING THE CODE.
    `order_digest` covers the whole sequence, so a tail pull that gained two rows
    and lost two rows moves it even when every surviving row kept its place — and
    the first live run of this module reported `also_reordered: True` on exactly
    that, which reads as evidence for the U15 row's hypothesis (a) and is not.

    Returns None -- *not comparable* -- when the two pulls share no ids, never
    True: 'nothing to compare' is not 'order preserved'.
    """
    d = list(declared.get("id_order") or [])
    o = list(observed.get("id_order") or [])
    if not d or not o:
        return None
    shared = set(d) & set(o)
    if not shared:
        return None
    return [i for i in d if i in shared] == [i for i in o if i in shared]


GROUP_STATES = ("compared", "not_declared", "undecidable_group_fields_differ")

# Can a `content_changed` verdict NAME the rows, or only assert that some row
# changed? Four states, never collapsed -- because the two sides fail
# independently and the remedy differs (re-render the memo's stanza vs re-take
# the pull).
LOCALISATION_STATES = (
    "localised",
    "declared_carries_no_row_digests",
    "observed_carries_no_row_digests",
    "neither_side_carries_row_digests",
)

# How many hex characters of each row digest a stanza carries. Full digests are
# 64 hex; a memo declaring 1000 rows at full length adds ~77 KB of HTML comment,
# which is why the length is a CHOICE and is DECLARED rather than assumed.
DEFAULT_ROW_DIGEST_PREFIX = 16


def _prefix_of(digest: str, n: int | None) -> str:
    """Trim a `sha256:<hex>` digest to `n` hex characters. `None` means full."""
    if n is None:
        return digest
    body = digest.split(":", 1)[1] if ":" in digest else digest
    return body[:n]


def _row_digest_maps(declared: dict, observed: dict) -> tuple[dict, dict, str, int | None]:
    """The two per-row digest maps, brought onto ONE basis, plus the verdict.

    ⚠️ **THE DECLARED SIDE SETS THE PRECISION AND THE OBSERVED SIDE IS TRIMMED TO
    IT.** A stanza carrying 16-hex prefixes compared against full observed digests
    would differ on EVERY row -- which is not a milder version of the right answer,
    it is the fabricated total-turnover this function exists to stop.
    """
    dd = declared.get("row_digests") or {}
    od = observed.get("row_digests") or {}
    if not dd and not od:
        return {}, {}, "neither_side_carries_row_digests", None
    if not dd:
        return {}, {}, "declared_carries_no_row_digests", None
    if not od:
        return {}, {}, "observed_carries_no_row_digests", None

    n = declared.get("row_digest_prefix")
    n = None if n in ("full", None) else int(n)
    return ({k: _prefix_of(v, n) for k, v in dd.items()},
            {k: _prefix_of(v, n) for k, v in od.items()},
            "localised", n)


def _group_verdict(declared: dict | None, observed: dict) -> dict:
    """Did any GROUP's membership change — the third mechanism, observed live.

    ⚠️ **THE U15 ROW NAMES TWO CAUSES AND THIS IS A THIRD.** Measured on two real
    `limit=1000` tail pulls 7.4h apart: **zero** rows re-stamped, the surviving
    rows' relative order **identical**, and yet `pkg-2f8c3a901ae84be1` went from a
    3-row fan-out package to a 2-row one because row 4729 fell off the tail. A
    package-level adjudication — or any `rows[0]` selection — can flip on that
    alone. It is neither hypothesis (a) (nothing was reordered) nor hypothesis (b)
    (nothing was re-stamped): the WINDOW moved.

    Three states, never collapsed. `not_declared` is *we did not look* — a
    fingerprint taken without `group_field` carries no grouping, and reporting
    that as 'no group changed' would be the reassuring answer.
    """
    dg = (declared or {}).get("group_field")
    og = observed.get("group_field")
    if dg is None or og is None:
        return {"group_comparison": "not_declared", "groups_changed": None,
                "groups_added": None, "groups_removed": None}
    if dg != og:
        return {"group_comparison": "undecidable_group_fields_differ", "groups_changed": None,
                "groups_added": None, "groups_removed": None}
    dm = (declared or {}).get("group_members") or {}
    om = observed.get("group_members") or {}
    return {
        "group_comparison": "compared",
        "groups_changed": sorted(k for k in (set(dm) & set(om)) if dm[k] != om[k]),
        "groups_added": sorted(set(om) - set(dm)),
        "groups_removed": sorted(set(dm) - set(om)),
    }


def compare(declared: dict | None, observed: dict) -> dict:
    """Grade a fresh fingerprint against a declared one. Five states, never collapsed."""
    if not declared:
        return {
            "state": "no_declared_input",
            "why": "the memo declares no input fingerprint, so a difference is unattributable "
                   "-- this is the state EVERY memo in docs/research/ is in today",
            "rows_changed": None,
            "rows_added": None,
            "rows_removed": None,
            "also_reordered": None,
            "shared_order_preserved": None,
            "localisation": "neither_side_carries_row_digests",
            "rows_basis": "not_compared",
            **_group_verdict(declared, observed),
        }

    dfields = list(declared.get("fields_covered") or [])
    ofields = list(observed.get("fields_covered") or [])
    if dfields != ofields:
        return {
            "state": "undecidable_field_sets_differ",
            "why": "the two fingerprints hashed different field sets, so a difference between "
                   "them is not evidence about the data -- we did not look at the same thing",
            "declared_fields_only": sorted(set(dfields) - set(ofields)),
            "observed_fields_only": sorted(set(ofields) - set(dfields)),
            "rows_changed": None,
            "rows_added": None,
            "rows_removed": None,
            "also_reordered": None,
            "shared_order_preserved": None,
            "localisation": "neither_side_carries_row_digests",
            "rows_basis": "not_compared",
            **_group_verdict(declared, observed),
        }

    same_set = declared.get("rowset_digest") == observed.get("rowset_digest")
    same_order = declared.get("order_digest") == observed.get("order_digest")

    if same_set and same_order:
        return {
            "state": "reproduces",
            "why": "same rows, same order",
            "rows_changed": [],
            "rows_added": [],
            "rows_removed": [],
            "also_reordered": False,
            "rows_basis": "rowset_digest_equality",
            "localisation": _row_digest_maps(declared, observed)[2],
            "shared_order_preserved": _shared_order_preserved(declared, observed),
            **_group_verdict(declared, observed),
        }

    if same_set:
        return {
            "state": "reordered",
            "why": "the same rows arrived in a different order -- hypothesis (a): a "
                   "self-contradicting fan-out package can adjudicate differently on this "
                   "alone, with NO data change",
            "rows_changed": [],
            "rows_added": [],
            "rows_removed": [],
            "also_reordered": True,
            "rows_basis": "rowset_digest_equality",
            "localisation": _row_digest_maps(declared, observed)[2],
            "shared_order_preserved": _shared_order_preserved(declared, observed),
            **_group_verdict(declared, observed),
        }

    dd, od, localisation, prefix = _row_digest_maps(declared, observed)

    base = {
        "state": "content_changed",
        "why": "the row multiset differs -- hypothesis (b): a field was re-stamped, or the "
               "population moved. Content dominates order deliberately: a reordering "
               "reported instead of a re-stamp would be the reassuring answer.",
        "also_reordered": not same_order,
        "shared_order_preserved": _shared_order_preserved(declared, observed),
        "localisation": localisation,
        "row_digest_prefix": prefix,
        "rows_basis": "row_digests" if localisation == "localised" else "not_localisable",
        **_group_verdict(declared, observed),
    }

    if localisation != "localised":
        # ⚠️ **`None`, NEVER `[]`, AND NEVER A DERIVED LIST.** Deriving the lists
        # from a side that carries no row digests produces a confident falsehood
        # in whichever direction the missing side happens to be: an absent
        # DECLARED map makes `set(od) - set(dd)` the ENTIRE observed population,
        # so a one-field re-stamp renders as total turnover; an absent OBSERVED
        # map makes both intersections empty, so a genuine re-stamp renders as
        # `nothing changed` beside a `content_changed` state. Both were MEASURED
        # on real declared memos before this branch existed (MI-278 U51).
        base["rows_changed"] = None
        base["rows_added"] = None
        base["rows_removed"] = None
        base["rows_not_localisable"] = None
        base["why"] += (
            " ⚠️ THE CHANGED ROWS CANNOT BE NAMED: " + localisation + ". Some row differs "
            "and WHICH is unknown -- re-render the memo's stanza with "
            "`include_row_digests=True`, or compare against a retained pull."
        )
        return base

    base["rows_changed"] = sorted(k for k in (set(dd) & set(od)) if dd[k] != od[k])
    base["rows_added"] = sorted(set(od) - set(dd))
    base["rows_removed"] = sorted(set(dd) - set(od))
    base["rows_not_localisable"] = int(declared.get("n_rows_without_id") or 0) + int(
        observed.get("n_rows_without_id") or 0
    )
    return base


def render_stanza(
    fp: dict,
    *,
    include_order: bool = False,
    include_row_digests: bool | int = False,
) -> str:
    """The declarable block. An HTML comment: invisible when rendered, greppable on disk.

    ⚠️ **`include_order=False` IS A REAL LOSS AND IS NAMED RATHER THAN HIDDEN.**
    Without the id sequence a later comparison cannot compute
    `shared_order_preserved`, so it returns None — *not comparable* — and a
    difference between the pulls cannot be split into 'the population moved' and
    'the surviving rows were reordered'. `id_first`/`id_last` still ship, which
    for an id-ordered tail bounds the window cheaply, but they are a summary and
    not the sequence. Pass `include_order=True` when full attributability matters
    more than a few KB in the memo.

    ⚠️ **`include_row_digests=False` IS THE LOSS THAT MADE THIS MODULE'S OWN HEADLINE
    PROMISE FALSE, AND IT IS NOW NAMED RATHER THAN DISCOVERED.** The module docstring
    says a `content_changed` verdict arrives "with the changed rows NAMED". Without
    per-row digests it can name none -- `compare()` now returns `None` and a
    `localisation` state instead of a derived list, which is honest and is still not
    an answer. Pass `include_row_digests=True` on any memo whose table a later
    session may need to ATTRIBUTE rather than merely re-run.

    The prefix length is a real trade and is therefore DECLARED in the stanza as
    `row_digest_prefix`, never assumed. At the default 16 hex characters 1000 rows
    cost ~23 KB against ~77 KB at full length, and the collision exposure over 1000
    rows is a birthday bound of about 1000**2 / 2 / 2**64 = 2.7e-14 -- so a MISSED
    change is possible in principle and vanishing in practice. `rowset_digest` and
    `order_digest` are always full length and are unaffected. `True` means the
    default prefix; an int means that many hex characters; >= 64 means full.
    """
    lines = [STANZA_OPEN]
    for k in ("source", "pulled_at"):
        if fp.get(k):
            lines.append(f"{k}: {fp[k]}")
    lines.append(f"n_rows: {fp['n_rows']}")
    lines.append(f"id_field: {fp['id_field']}")
    lines.append(f"rowset_digest: {fp['rowset_digest']}")
    lines.append(f"order_digest: {fp['order_digest']}")
    lines.append("fields_covered: " + ",".join(fp["fields_covered"]))
    if fp.get("fields_present_not_covered"):
        lines.append(
            "fields_present_not_covered: " + ",".join(fp["fields_present_not_covered"])
        )
    for k in ("id_first", "id_last"):
        if fp.get(k) is not None:
            lines.append(f"{k}: {fp[k]}")
    if include_order and fp.get("id_order"):
        lines.append("id_order: " + ",".join(fp["id_order"]))
    if include_row_digests and fp.get("row_digests"):
        n = DEFAULT_ROW_DIGEST_PREFIX if include_row_digests is True else int(include_row_digests)
        n = None if n >= 64 else n
        lines.append(f"row_digest_prefix: {'full' if n is None else n}")
        lines.append(
            "row_digests: "
            + ",".join(f"{k}={_prefix_of(v, n)}" for k, v in fp["row_digests"].items())
        )
    lines.append(f"n_rows_without_id: {fp['n_rows_without_id']}")
    lines.append(f"n_duplicate_ids: {fp['n_duplicate_ids']}")
    lines.append(STANZA_CLOSE)
    return "\n".join(lines)


def parse_stanza(text: str) -> dict | None:
    """Read a declared stanza back. Returns None when there is none to read.

    ⚠️ A stanza present but MISSING a required key returns a dict carrying
    `incomplete`, never None -- 'declared badly' and 'declared nothing' are
    different facts and the census grades them apart.
    """
    i = text.find(STANZA_OPEN)
    if i < 0:
        return None
    j = text.find(STANZA_CLOSE, i)
    if j < 0:
        return {"incomplete": True, "why": "stanza opened and never closed"}
    body = text[i + len(STANZA_OPEN): j]
    out: dict[str, Any] = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip()
        if k == "row_digests":
            # `id=digest` pairs. A malformed entry is DROPPED and COUNTED rather
            # than guessed: a silently shortened map would make `rows_added` name
            # rows that were never missing.
            m: dict[str, str] = {}
            bad = 0
            for part in v.split(","):
                if not part:
                    continue
                rid, sep, dig = part.partition("=")
                if not sep or not rid.strip() or not dig.strip():
                    bad += 1
                    continue
                m[rid.strip()] = dig.strip()
            out["row_digests"] = m
            if bad:
                out["row_digests_malformed_entries"] = bad
        elif k in ("fields_covered", "fields_present_not_covered", "id_order"):
            out[k] = [x for x in v.split(",") if x]
        elif k in ("n_rows", "n_rows_without_id", "n_duplicate_ids"):
            try:
                out[k] = int(v)
            except ValueError:
                out[k] = None
        else:
            out[k] = v
    missing = [k for k in REQUIRED_STANZA_KEYS if out.get(k) in (None, [], "")]
    if missing:
        out["incomplete"] = True
        out["missing_keys"] = missing
    return out


def census_memo(path: pathlib.Path) -> dict:
    text = path.read_text(errors="replace")
    signals = [s for s in JOURNAL_SIGNALS if s in text]
    stanza = parse_stanza(text)
    if stanza is not None and not stanza.get("incomplete"):
        state = "declares_input"
    elif signals:
        state = "journal_cited_no_declaration"
    else:
        state = "not_detected"
    return {
        "path": str(path),
        "state": state,
        "signals": signals,
        "stanza_present": stanza is not None,
        "stanza_incomplete": bool(stanza.get("incomplete")) if stanza else None,
    }


def census(root: str = "docs/research") -> dict:
    base = pathlib.Path(root)
    rows = [census_memo(p) for p in sorted(base.glob("*.md"))]
    counts = {s: 0 for s in CENSUS_STATES}
    for r in rows:
        counts[r["state"]] += 1
    return {
        "root": root,
        "n_memos": len(rows),
        "counts": counts,
        "signal_tokens": list(JOURNAL_SIGNALS),
        "rows": rows,
        "not_detected_is_not_a_clean_negative": True,
    }


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------
def _rows_a() -> list[dict]:
    return [
        {"id": 1, "pnl": 10.0, "exit_price": 100.0, "status": "closed"},
        {"id": 2, "pnl": -5.0, "exit_price": 90.0, "status": "closed"},
        {"id": 3, "pnl": 2.0, "exit_price": 95.0, "status": "closed"},
    ]


def _selftest() -> int:
    fails = 0
    n = 0

    def check(label: str, cond: bool) -> None:
        nonlocal fails, n
        n += 1
        if not cond:
            fails += 1
            print(f"  FAIL {n:>3} {label}")
        else:
            print(f"  ok   {n:>3} {label}")

    a = _rows_a()
    fa = fingerprint(a)
    narrow_for_key = fingerprint(_rows_a(), fields=["id", "pnl"])

    # 1-3 the identity case
    check("identical pull reproduces", compare(fa, fingerprint(_rows_a()))["state"] == "reproduces")
    check("fingerprint is deterministic", fingerprint(_rows_a()) == fa)
    check("digests are prefixed and hex", fa["rowset_digest"].startswith("sha256:") and len(fa["rowset_digest"]) == 71)

    # 4-6 REORDERED -- hypothesis (a), and it must NOT read as a content change
    rev = list(reversed(_rows_a()))
    c_rev = compare(fa, fingerprint(rev))
    check("reordered pull grades `reordered`", c_rev["state"] == "reordered")
    check("reordered names no changed rows", c_rev["rows_changed"] == [])
    check("reordered sets also_reordered", c_rev["also_reordered"] is True)

    # 7-11 CONTENT CHANGED -- hypothesis (b), localised
    restamped = _rows_a()
    restamped[1]["exit_price"] = 91.0
    c_re = compare(fa, fingerprint(restamped))
    check("a re-stamped field grades `content_changed`", c_re["state"] == "content_changed")
    check("the changed row is NAMED", c_re["rows_changed"] == ["2"])
    check("nothing added", c_re["rows_added"] == [])
    check("nothing removed", c_re["rows_removed"] == [])
    check("a re-stamp is not reported as a reorder", c_re["also_reordered"] is False)

    # 11b BOTH at once -- the case a real pull produces, and the one an
    # order-first branch would answer reassuringly: a re-stamp that also moved
    # the row order must still grade `content_changed`.
    both = list(reversed(_rows_a()))
    both[1]["exit_price"] = 91.0
    c_both = compare(fa, fingerprint(both))
    check("a re-stamp that ALSO reordered still grades `content_changed`", c_both["state"] == "content_changed")
    check("...and reports the reorder beside it rather than instead of it", c_both["also_reordered"] is True)
    check("...and still names the changed row", c_both["rows_changed"] == ["2"])

    # THE CONFOUND THE LIVE RUN EXPOSED, and the reason `shared_order_preserved`
    # exists at all: a TAIL pull that gained and lost rows moves `order_digest`
    # even when every surviving row kept its place. Reporting that as
    # `also_reordered: True` alone reads as support for the U15 row's hypothesis
    # (a) and is not. Measured on two real pulls 7.4h apart: 998 shared rows,
    # relative order IDENTICAL, `also_reordered` True purely from 2 added / 2
    # removed. These controls pin the distinction.
    drifted = _rows_a()[1:] + [{"id": 4, "pnl": 1.0, "exit_price": 99.0, "status": "closed"}]
    c_drift = compare(fa, fingerprint(drifted))
    check("tail drift alone still reports also_reordered", c_drift["also_reordered"] is True)
    check("...but shared_order_preserved separates it from a real reorder", c_drift["shared_order_preserved"] is True)
    check("a genuine reorder of the shared rows reads False", compare(fa, fingerprint(rev))["shared_order_preserved"] is False)
    check("no shared rows is None -- not comparable, never True",
          compare(fa, fingerprint([{"id": 99, "pnl": 0.0, "exit_price": 0.0, "status": "closed"}]))["shared_order_preserved"] is None)
    check("an ungraded verdict carries the key as None too",
          compare(None, fa)["shared_order_preserved"] is None
          and compare(narrow_for_key, fa)["shared_order_preserved"] is None)

    # a stanza without the order line CANNOT answer it, and says None rather than True
    st_no_order = parse_stanza(render_stanza(fa))
    check("a stanza without id_order grades shared_order_preserved None, never True",
          compare(st_no_order, fingerprint(_rows_a()))["shared_order_preserved"] is None)
    st_order = parse_stanza(render_stanza(fa, include_order=True))
    check("include_order=True restores it", compare(st_order, fingerprint(rev))["shared_order_preserved"] is False)
    check("...and id_first/id_last ship either way", st_no_order["id_first"] == "1" and st_no_order["id_last"] == "3")

    # THE THIRD MECHANISM -- observed live, and named by neither hypothesis in
    # the U15 row. A group whose membership changes because the WINDOW moved.
    ga = [{"id": 1, "p": "A"}, {"id": 2, "p": "A"}, {"id": 3, "p": "B"}]
    gb = [{"id": 2, "p": "A"}, {"id": 3, "p": "B"}]
    fga, fgb = fingerprint(ga, group_field="p"), fingerprint(gb, group_field="p")
    vg = compare(fga, fgb)
    check("a group that LOST a member is named", vg["groups_changed"] == ["A"])
    check("...and the surviving rows were neither re-stamped nor reordered",
          vg["rows_changed"] == [] and vg["shared_order_preserved"] is True)
    check("a fingerprint taken without group_field grades `not_declared`, never 'no change'",
          compare(fingerprint(ga), fingerprint(gb))["group_comparison"] == "not_declared"
          and compare(fingerprint(ga), fingerprint(gb))["groups_changed"] is None)
    check("one side declaring a group and the other not is also `not_declared`",
          compare(fga, fingerprint(gb))["group_comparison"] == "not_declared")
    check("two DIFFERENT group fields are undecidable, never compared",
          compare(fga, fingerprint(gb, group_field="id"))["group_comparison"] == "undecidable_group_fields_differ")
    check("an identical pull reports no group change", compare(fga, fingerprint(ga, group_field="p"))["groups_changed"] == [])
    check("group counts ship, and are None when not looked at",
          fga["n_multi_row_groups"] == 1 and fingerprint(ga)["n_multi_row_groups"] is None)
    check("all three group states are reachable",
          {compare(fga, fingerprint(ga, group_field="p"))["group_comparison"],
           compare(fingerprint(ga), fingerprint(gb))["group_comparison"],
           compare(fga, fingerprint(gb, group_field="id"))["group_comparison"]} == set(GROUP_STATES))
    # ⚠️ MEMBERSHIP IS A SET, NOT A SEQUENCE. Planting an unsorted member list
    # tripped NOTHING because every fixture happened to arrive in sorted order --
    # so this control delivers the SAME group in a different row order. Without
    # it, a package whose siblings merely swapped places would be reported as a
    # membership change: a false positive on the third mechanism, in the ALARMING
    # direction, which is the one that gets acted on.
    ga_shuf = [{"id": 2, "p": "A"}, {"id": 1, "p": "A"}, {"id": 3, "p": "B"}]
    check("the same group delivered in a different ROW order is not a membership change",
          compare(fga, fingerprint(ga_shuf, group_field="p"))["groups_changed"] == [])
    check("...and its group digest is identical",
          fga["group_digest"] == fingerprint(ga_shuf, group_field="p")["group_digest"])
    check("a row with a null group is excluded from grouping, not given a synthetic id",
          fingerprint([{"id": 1, "p": None}, {"id": 2, "p": "A"}], group_field="p")["n_groups"] == 1)

    # 12-13 population moved
    grown = _rows_a() + [{"id": 4, "pnl": 1.0, "exit_price": 99.0, "status": "closed"}]
    c_gr = compare(fa, fingerprint(grown))
    check("an added row is named", c_gr["rows_added"] == ["4"])
    shrunk = _rows_a()[:2]
    check("a removed row is named", compare(fa, fingerprint(shrunk))["rows_removed"] == ["3"])

    # 14-16 the field-set guard: a different field set is UNDECIDABLE, never a diff
    narrow = fingerprint(_rows_a(), fields=["id", "pnl"])
    c_nf = compare(fa, narrow)
    check("differing field sets grade `undecidable_field_sets_differ`", c_nf["state"] == "undecidable_field_sets_differ")
    check("undecidable names which fields only one side had", c_nf["declared_fields_only"] == ["exit_price", "status"])
    check("undecidable localises nothing", c_nf["rows_changed"] is None)

    # 17-18 a narrow field set is BLIND, and says so
    c_blind = compare(narrow, fingerprint(restamped, fields=["id", "pnl"]))
    check("a field outside the covered set does not move the digest", c_blind["state"] == "reproduces")
    check("and the fingerprint declares what it did not hash", narrow["fields_present_not_covered"] == ["exit_price", "status"])

    # 19-21 no declaration is its own state
    c_nd = compare(None, fa)
    check("absent declaration grades `no_declared_input`", c_nd["state"] == "no_declared_input")
    check("absent declaration localises nothing (None, not [])", c_nd["rows_changed"] is None)
    check("empty-dict declaration is also `no_declared_input`", compare({}, fa)["state"] == "no_declared_input")

    # 22-24 duplicates and missing ids are counted, never silently dropped
    dup = _rows_a() + [{"id": 1, "pnl": 999.0, "exit_price": 1.0, "status": "closed"}]
    fdup = fingerprint(dup)
    check("a duplicate id is counted", fdup["n_duplicate_ids"] == 1)
    check("the duplicate's own digest is kept, not overwritten", len(fdup["row_digests"]) == 4)
    noid = _rows_a() + [{"pnl": 7.0, "exit_price": 7.0, "status": "closed"}]
    fnoid = fingerprint(noid)
    check("a row with no id is counted, not dropped", fnoid["n_rows_without_id"] == 1 and fnoid["n_rows"] == 4)

    # 25 THE MULTISET CONTROL: dropping a duplicate row must NOT hash the same
    dup_dropped = _rows_a() + [{"id": 1, "pnl": 999.0, "exit_price": 1.0, "status": "closed"}]
    dup_twice = dup_dropped + [{"id": 1, "pnl": 999.0, "exit_price": 1.0, "status": "closed"}]
    check("a lost duplicate row changes the rowset digest (multiset, not set)",
          fingerprint(dup_dropped)["rowset_digest"] != fingerprint(dup_twice)["rowset_digest"])

    # 26-28 canonicalisation: type churn must not read as a value change,
    # but a genuine type change must not be invisible either
    as_int = [{"id": 1, "pnl": 10, "exit_price": 100.0, "status": "closed"}]
    as_float = [{"id": 1, "pnl": 10.0, "exit_price": 100.0, "status": "closed"}]
    # ⚠️ The string here is "10.0", NOT "10". An earlier draft used "10" and the
    # control was INEFFECTIVE: planting a `str()`-for-everything canonicalisation
    # tripped nothing, because "10" and repr(float(10)) == "10.0" differ anyway.
    # "10.0" is the string that actually collides with the float under a naive
    # canonicalisation, so it is the one the control has to use.
    as_str = [{"id": 1, "pnl": "10.0", "exit_price": 100.0, "status": "closed"}]
    check("int 10 and float 10.0 hash the same", fingerprint(as_int)["rowset_digest"] == fingerprint(as_float)["rowset_digest"])
    check("string \"10.0\" does NOT collide with numeric 10.0", fingerprint(as_str)["rowset_digest"] != fingerprint(as_float)["rowset_digest"])
    check("None does not collide with the string 'None'",
          fingerprint([{"id": 1, "pnl": None}])["rowset_digest"] != fingerprint([{"id": 1, "pnl": "None"}])["rowset_digest"])

    # 29-30 a field renamed between pulls is a field-set difference, not a content one
    renamed = [{"id": 1, "pnl": 10.0, "exitPrice": 100.0, "status": "closed"}]
    check("a renamed column is caught as a field-set difference",
          compare(fingerprint(as_float), fingerprint(renamed))["state"] == "undecidable_field_sets_differ")
    check("...and is therefore never reported as a data change",
          compare(fingerprint(as_float), fingerprint(renamed))["rows_changed"] is None)

    # 31-35 the stanza round-trips, and an incomplete one is not a declaration
    stanza = render_stanza(fa)
    parsed = parse_stanza("prose above\n" + stanza + "\nprose below")
    check("stanza round-trips its digests", parsed["rowset_digest"] == fa["rowset_digest"] and parsed["order_digest"] == fa["order_digest"])
    check("stanza round-trips its field list", parsed["fields_covered"] == fa["fields_covered"])
    check("a round-tripped stanza compares as `reproduces`", compare(parsed, fingerprint(_rows_a()))["state"] == "reproduces")
    check("no stanza parses to None (not to an empty declaration)", parse_stanza("no stanza here") is None)
    trunc = stanza.replace(f"rowset_digest: {fa['rowset_digest']}\n", "")
    check("a stanza missing a required key is `incomplete`, not a declaration",
          parse_stanza(trunc).get("incomplete") is True and "rowset_digest" in parse_stanza(trunc)["missing_keys"])

    # 36-38 the census grades the three states apart, with a positive control
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        (d / "a-declares.md").write_text("# a\n" + stanza + "\nread /api/diag/journal\n")
        (d / "b-cites-only.md").write_text("# b\ncomputed from a /api/diag/journal pull\n")
        (d / "c-silent.md").write_text("# c\nno data here\n")
        (d / "d-incomplete.md").write_text("# d\n" + trunc + "\nfrom a journal pull\n")
        cen = census(str(d))
        check("a declaring memo grades `declares_input`", cen["counts"]["declares_input"] == 1)
        check("a citing memo with no stanza is the EXPOSURE state", cen["counts"]["journal_cited_no_declaration"] == 2)
        check("a silent memo is `not_detected`, never 'not journal-derived'", cen["counts"]["not_detected"] == 1)
        inc = [r for r in cen["rows"] if r["path"].endswith("d-incomplete.md")][0]
        check("an INCOMPLETE stanza does not count as a declaration", inc["state"] == "journal_cited_no_declaration" and inc["stanza_incomplete"] is True)
        # MEASURED, not assumed: this fixture's prose carries TWO tokens
        # ("/api/diag/journal" and "journal pull"), which is the point of
        # reporting the matched set rather than a boolean -- a census number can
        # then be re-read under a narrower rule without re-running the probe.
        check("the census reports WHICH tokens matched, so the rule is re-readable",
              [r for r in cen["rows"] if r["path"].endswith("b-cites-only.md")][0]["signals"]
              == ["/api/diag/journal", "journal pull"])

    # 41 every declared state is reachable -- a state nothing can produce is decoration
    seen = {
        compare(fa, fingerprint(_rows_a()))["state"],
        compare(fa, fingerprint(rev))["state"],
        compare(fa, fingerprint(restamped))["state"],
        compare(fa, narrow)["state"],
        compare(None, fa)["state"],
    }
    check("all five compare states are reachable", seen == set(COMPARE_STATES))

    # ------------------------------------------------------------------
    # LOCALISATION -- the defect these exist for (MI-278 U51).
    # Every control above compares two LIVE fingerprint dicts, which always
    # carry `row_digests`; the one that touched the stanza path checked the
    # IDENTITY case, where the lists are empty either way. So a suite of 64
    # controls was green while the module's headline promise was false on the
    # only path a later session can use. These compare through a STANZA on a
    # case where the answer differs.
    # ------------------------------------------------------------------
    restamped_one = [dict(r) for r in _rows_a()]
    restamped_one[1]["pnl"] = -999.0
    f_re = fingerprint(restamped_one)
    st_plain = parse_stanza(render_stanza(fa))

    v_sl = compare(st_plain, f_re)
    check("stanza-vs-live on a re-stamp still grades `content_changed`",
          v_sl["state"] == "content_changed")
    check("stanza-vs-live names the MISSING SIDE, not a row list",
          v_sl["localisation"] == "declared_carries_no_row_digests")
    check("stanza-vs-live returns rows_added None -- NOT the whole population",
          v_sl["rows_added"] is None)
    check("stanza-vs-live returns rows_changed None -- NOT []",
          v_sl["rows_changed"] is None and v_sl["rows_removed"] is None)
    check("rows_not_localisable is None when nothing could be localised, never 0",
          v_sl["rows_not_localisable"] is None)
    check("the `why` carries the localisation state, so a printed verdict cannot hide it",
          "CANNOT BE NAMED" in v_sl["why"] and "declared_carries_no_row_digests" in v_sl["why"])

    v_ss = compare(st_plain, parse_stanza(render_stanza(f_re)))
    check("stanza-vs-stanza on a re-stamp reports NEITHER side, not agreement",
          v_ss["localisation"] == "neither_side_carries_row_digests"
          and v_ss["rows_changed"] is None)

    # the capability that buys the naming back
    st_full = parse_stanza(render_stanza(fa, include_row_digests=True))
    v_ok = compare(st_full, f_re)
    check("a stanza WITH row digests localises against a live pull",
          v_ok.get("localisation") == "localised" and v_ok.get("rows_basis") == "row_digests")
    check("...and NAMES exactly the re-stamped row",
          v_ok.get("rows_changed") == [str(restamped_one[1]["id"])]
          and v_ok.get("rows_added") == [] and v_ok.get("rows_removed") == [])
    check("the declared prefix is carried in the stanza and parsed back as an int",
          st_full.get("row_digest_prefix") == str(DEFAULT_ROW_DIGEST_PREFIX)
          and v_ok.get("row_digest_prefix") == DEFAULT_ROW_DIGEST_PREFIX)
    check("the OBSERVED side is trimmed to the DECLARED prefix, so a 16-hex stanza "
          "against full observed digests does not report every row changed",
          len(v_ok.get("rows_changed") or []) == 1)
    check("a full-length stanza (>=64) declares `full` and still localises",
          parse_stanza(render_stanza(fa, include_row_digests=64)).get("row_digest_prefix") == "full"
          and compare(parse_stanza(render_stanza(fa, include_row_digests=64)), f_re).get("rows_changed")
              == [str(restamped_one[1]["id"])])
    check("a stanza WITHOUT row digests on the OBSERVED side is its own state",
          compare(st_full, st_plain)["localisation"] == "observed_carries_no_row_digests")
    check("a malformed row_digests entry is COUNTED, never guessed",
          parse_stanza(render_stanza(fa, include_row_digests=True)
                       .replace("row_digests: ", "row_digests: junkwithoutequals,"))
          .get("row_digests_malformed_entries") == 1)
    check("`reproduces` still reports [] with no row digests -- entailed by digest equality",
          compare(st_plain, fingerprint(_rows_a()))["rows_changed"] == []
          and compare(st_plain, fingerprint(_rows_a()))["rows_basis"] == "rowset_digest_equality")
    check("every localisation state is reachable -- a state nothing produces is decoration",
          {v_ok["localisation"], v_sl["localisation"], v_ss["localisation"],
           compare(st_full, st_plain)["localisation"]} == set(LOCALISATION_STATES))

    # 42 the historical question stays open, in the module's own output
    check("the module does not claim to attribute the 30-vs-31",
          "DOES NOT ATTRIBUTE THE 30-vs-31" in (__doc__ or ""))

    print(f"\nself-test: {n} controls, {fails} failure(s)")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--census", action="store_true", help="grade docs/research/*.md for a declared input")
    ap.add_argument("--census-root", default="docs/research")
    ap.add_argument("--fingerprint", help="JSON array from /api/diag/journal?table=trades")
    ap.add_argument("--fields", help="comma-separated field allow-list (default: every key present)")
    ap.add_argument("--source", help="the pull URL, recorded verbatim in the stanza")
    ap.add_argument("--pulled-at", help="ISO timestamp of the pull")
    ap.add_argument("--against", help="a memo (or stanza file) to COMPARE the pull against")
    ap.add_argument("--include-row-digests", action="store_true",
                    help="emit per-row digests in the stanza so a later comparison can NAME "
                         "the changed rows (without them it can only say that some row changed)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return _selftest()

    if args.census:
        out = census(args.census_root)
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            print(f"memo-input-provenance census over {out['root']} — {out['n_memos']} memo(s)")
            for s in CENSUS_STATES:
                print(f"  {s:<32} {out['counts'][s]}")
            # The caveat carries its own denominator, on the same line as the
            # claim: "not a clean negative" is only readable beside the count it
            # qualifies and the token list that produced it.
            print(f"  ⚠️ `not_detected` — {out['counts']['not_detected']} of "
                  f"{out['n_memos']} memo(s) — is NOT a clean negative. The probe is a "
                  f"token match over prose ({len(out['signal_tokens'])} tokens: "
                  f"{', '.join(out['signal_tokens'])}), so a memo that computed a table "
                  f"from a pull without saying so is counted in that number.")
        return 0

    if args.fingerprint:
        rows = json.loads(pathlib.Path(args.fingerprint).read_text())
        fields = args.fields.split(",") if args.fields else None
        fp = fingerprint(rows, fields=fields, source=args.source, pulled_at=args.pulled_at)
        if args.against:
            declared = parse_stanza(pathlib.Path(args.against).read_text())
            if declared and declared.get("incomplete"):
                declared = None
            verdict = compare(declared, fp)
            if args.json:
                print(json.dumps({"verdict": verdict, "observed": {k: v for k, v in fp.items() if k != "row_digests"}}, indent=2))
            else:
                print(f"state: {verdict['state']}")
                print(f"why:   {verdict['why']}")
                print(f"localisation: {verdict.get('localisation')} "
                      f"(rows_basis={verdict.get('rows_basis')})")
                for k in ("rows_changed", "rows_added", "rows_removed"):
                    v = verdict.get(k)
                    # `None` is printed, never skipped. A falsy-only printer is how
                    # "we could not look" and "we looked and found none" became one
                    # line of output (MI-278 U51).
                    if v is None:
                        print(f"{k}: NOT LOCALISABLE -- {verdict.get('localisation')}")
                    elif v:
                        print(f"{k}: {v[:20]}{' …' if len(v) > 20 else ''}")
            return 0
        if args.json:
            print(json.dumps({k: v for k, v in fp.items() if k != "row_digests"}, indent=2))
        else:
            print(render_stanza(fp, include_row_digests=args.include_row_digests))
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
