#!/usr/bin/env python3
# wiring: imported by scripts/ops/probe_soak.py, probe_file.py, probe_api.py
"""Shared core for the `docs/claude/OPEN-ITEMS.json` probe family — work-plan item 3.

⚠️ NOT "W3". The 2026-08-31 operations plan's W-sequence already uses W3 for
the MERGE SERIALIZER, which was refuted by measurement and deliberately not
built — `.github/workflows/scope-overlap-audit.yml` carries that record so no
session re-proposes it. This work is item 3 of the artifact's five-item work
plan (probe coverage), a continuation of W2. The two enumerations are
different sequences and a third (`full-system-audit W2`) exists in ROADMAP.md,
so a bare W-number is ambiguous here — say which plan.

WHY THIS EXISTS AS A MODULE AND NOT AS A COPY
---------------------------------------------
`probe_soak.py` shipped first and grew the predicate engine + the three-state
exit contract. Adding a second and third probe SOURCE (a repo-local corpus, the
unauthenticated `/api/bot/*` surface) by copying that engine would give the repo
two definitions of what `legs[].position_idx~1,2` means, free to drift — the
same argument `CLAUDE.md` makes for `src/runtime/provenance.py` ("import it; do
not re-derive the vocabulary") and for `_regime_score_semantics.py` ("two probes
re-derived it independently and both got it wrong on the same day").

So: ONE predicate engine, ONE exit-code contract, N sources.

THE EXIT-CODE CONTRACT — THE WHOLE POINT OF THE FAMILY
------------------------------------------------------
    0  the predicate matched at least one row      → pass
    1  the source was READ and NOTHING matched     → fail (a real negative)
    2  we could not look                           → could_not_run

Code 2 is emphatically NOT code 1. Every probe here reports on a row that is
open *because the thing has not been observed yet*, so "nothing matched" is the
state these rows are ALREADY in — which makes an unread source rendering as a
negative indistinguishable from the expected answer. That is the
`curl … || echo '{}'` defect (`CLAUDE.md` § "Diagnostic provenance", sub-class
**C**: an empty result reading as a clean negative), and here it is worse than
usual because the wrong answer looks exactly like the right one.

THE DENOMINATOR IS ALWAYS PRINTED
---------------------------------
A `fail` prints how many rows were scanned. Nothing-over-0-rows and
nothing-over-8,520-rows are different findings, and the first is usually a read
that quietly returned an empty page.
"""

from __future__ import annotations

EXIT_PASS, EXIT_FAIL, EXIT_COULD_NOT_LOOK = 0, 1, 2


def die_unlooked(msg: str) -> int:
    """Print an unread as an UNREAD and return code 2.

    The wording is load-bearing: never "no rows matched". This path never
    establishes that, and a reader who cannot tell the two apart has the
    defect the whole family exists to prevent.
    """
    print(f"probe: COULD NOT LOOK — {msg}")
    return EXIT_COULD_NOT_LOOK


# ── predicate ──────────────────────────────────────────────────────────────

def walk(row, path: str):
    """Yield every value at a dotted path. `[]` fans out over a list.

    `legs[].position_idx` yields one value per leg, so "any leg" and "this
    specific field" are both expressible without a query language.
    """
    cur = [row]
    for part in path.split("."):
        nxt = []
        fan = part.endswith("[]")
        key = part[:-2] if fan else part
        for c in cur:
            if not isinstance(c, dict) or key not in c:
                continue
            v = c[key]
            if fan:
                if isinstance(v, list):
                    nxt.extend(v)
            else:
                nxt.append(v)
        cur = nxt
    return cur


def coerce(s: str):
    low = s.lower()
    if low in {"true", "false"}:
        return low == "true"
    if low == "null":
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


def _gt(values, want) -> bool:
    """`a > b` where a mismatched pair is FALSE, never an exception.

    A probe compares whatever the source happened to serve. Letting a
    str-vs-int comparison raise would turn a genuine negative into a crash the
    runner grades `could_not_run` — reporting "we did not look" about a source
    we demonstrably read. Numbers compare numerically; everything else compares
    as text, which is why ISO-8601 UTC timestamps work here (they sort
    lexicographically) and why a naive-vs-aware mix does not silently pass.
    """
    for v in values:
        try:
            if isinstance(v, bool) or isinstance(want, bool):
                continue
            if isinstance(v, (int, float)) and isinstance(want, (int, float)):
                if v > want:
                    return True
            elif isinstance(v, str) and isinstance(want, str):
                if v > want:
                    return True
        except TypeError:
            continue
    return False


def parse_condition(spec: str):
    """`path=value` (equals), `path~a,b,c` (membership), `path>value` (greater).

    Returns a callable taking one row. `>` was added for the E35 row, whose
    criterion turns on a trade being OPENED AFTER a named deploy — a comparison
    an equality engine cannot express, and the exact word the row calls "the
    whole criterion".
    """
    for op in ("~", ">", "="):
        if op not in spec:
            continue
        # Pick the EARLIEST operator so a value containing a later operator
        # character cannot silently re-split the spec.
        idx = min(spec.index(o) for o in ("~", ">", "=") if o in spec)
        op = spec[idx]
        path, raw = spec[:idx], spec[idx + 1:]
        path = path.strip()
        if not path:
            raise ValueError(f"condition {spec!r} has an empty path")
        if op == "~":
            wanted = {coerce(x.strip()) for x in raw.split(",") if x.strip() != ""}
            if not wanted:
                raise ValueError(f"empty membership set in {spec!r}")
            return lambda row: any(v in wanted for v in walk(row, path))
        if op == ">":
            want = coerce(raw.strip())
            return lambda row: _gt(walk(row, path), want)
        want = coerce(raw.strip())
        return lambda row: any(v == want for v in walk(row, path))
    raise ValueError(
        f"condition {spec!r} must be `path=value`, `path~a,b` or `path>value`")


# ── row normalisation ──────────────────────────────────────────────────────

def normalise_rows(raw) -> list[dict]:
    """Coerce a list of dicts-or-JSON-strings into dicts, dropping neither
    silently nor loudly — a non-dict entry simply cannot satisfy a predicate.

    Diag log tails serve `lines` as raw JSONL STRINGS; the `/api/bot/*` routes
    serve real objects. One normaliser so a predicate reads the same either way.
    """
    import json
    rows: list[dict] = []
    for r in raw or []:
        if isinstance(r, dict):
            rows.append(r)
        elif isinstance(r, str):
            try:
                obj = json.loads(r)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def report(rows: list[dict], conds, require_labels, note: str,
           control=None, control_label: str = "") -> int:
    """The one place a verdict is turned into an exit code + a printed line.

    THE POSITIVE CONTROL IS WHY A `fail` HERE IS WORTH BELIEVING
    ------------------------------------------------------------
    `docs/CLAUDE-RULES-CANONICAL.md` § RULE ONE: *"A search returning nothing is
    not proof of absence. Show the probe can find a positive before trusting
    that it is quiet."* Every probe in this family reports on a row that is open
    because the thing has NOT been seen, so `fail` is the expected answer — and
    an expected answer is exactly the one nobody re-checks. A predicate typo, a
    renamed field, or a source whose schema moved all produce that same quiet
    `fail`, indefinitely, and it reads as diligence.

    So a declaration may name a `--positive-control`: a condition that DOES hold
    in the source today. If the control does not fire, we have not established
    that this reader can see anything at all, so the verdict is
    **could_not_look**, never `fail`. That converts a silently-broken reader
    from a confident negative into a declared unread — the whole polarity of
    this family.
    """
    if control is not None:
        seen = [r for r in rows if control(r)]
        if not seen:
            return die_unlooked(
                f"the POSITIVE CONTROL {control_label!r} matched 0 of {len(rows)} "
                f"row(s) ({note}). The reader is unproven on this source, so a "
                f"quiet result here is NOT a negative — it is an unread. Either "
                f"the source moved, the schema changed, or the control is stale.")
        note = f"{note}; positive control {control_label!r} matched {len(seen)}"

    hits = [r for r in rows if all(c(r) for c in conds)]
    if hits:
        print(f"probe: PASS — {len(hits)} of {len(rows)} row(s) match "
              f"{require_labels} ({note})")
        return EXIT_PASS
    # A zero denominator is the finding when it happens, so it is said out loud
    # rather than left for the reader to notice in the count.
    print(f"probe: FAIL — 0 of {len(rows)} row(s) match {require_labels} ({note}). "
          f"{'A ZERO DENOMINATOR IS NOT A NEGATIVE — the source read empty.' if not rows else ''}")
    return EXIT_FAIL


def self_test() -> int:
    """Planted controls for the shared core. Every probe binary runs this."""
    fired = 0

    def ok(cond, label):
        nonlocal fired
        assert cond, f"control FAILED: {label}"
        fired += 1

    row = {"a": 1, "state": "exceeds_cushion", "applied": True,
           "openedAt": "2026-08-30T12:00:00+00:00",
           "legs": [{"position_idx": 0}, {"position_idx": 2}]}
    ok(parse_condition("state=exceeds_cushion")(row), "equals matches")
    ok(not parse_condition("state=within_cushion")(row), "equals rejects")
    ok(parse_condition("applied=true")(row), "`true` coerces to bool, not the string 'true'")
    ok(not parse_condition("applied=false")(row), "bool coercion is not truthiness of a string")
    ok(parse_condition("legs[].position_idx~1,2")(row),
       "`[]` fans out — ANY leg satisfying the set is a match")
    ok(not parse_condition("legs[].position_idx~1")(row),
       "the fan-out does not match a value no leg carries")
    ok(not parse_condition("missing.key=1")(row), "an absent path never matches")

    ok(parse_condition("openedAt>2026-08-29T00:00:00+00:00")(row),
       "`>` on ISO-8601 UTC compares chronologically because it sorts lexically")
    ok(not parse_condition("openedAt>2026-08-31T00:00:00+00:00")(row),
       "`>` rejects a row on the wrong side of the boundary")
    # ⚠️ THE CAVEAT, ASSERTED RATHER THAN PROMISED. `>` on two strings is
    # LEXICOGRAPHIC, so comparing a non-timestamp field to a timestamp yields a
    # defined but meaningless answer ("exceeds_cushion" > "2026-01-01" is True
    # because 'e' > '2'). This control exists so nobody reads `>` as
    # type-aware. A declaration must therefore point `>` at a field that really
    # is ordered — a timestamp or a number — and `probe.checks` must say which.
    ok(parse_condition("state>2026-01-01")(row),
       "`>` between two strings is LEXICOGRAPHIC, not semantic — pointing it at "
       "a non-ordered field gives a defined answer about nothing")
    ok(not parse_condition("missing>1")(row),
       "`>` on an absent path is FALSE — never a crash the runner would grade "
       "could_not_run, which would report 'we did not look' about a source we read")
    ok(not parse_condition("state>1")(row),
       "`>` across a str/int mismatch is FALSE, not a TypeError")
    ok(not parse_condition("applied>0")(row),
       "a bool is never ordered against a number — True > 0 would be a spurious match")

    try:
        parse_condition("garbage")
        ok(False, "an unparseable condition raises")
    except ValueError:
        ok(True, "an unparseable condition raises")
    try:
        parse_condition("=1")
        ok(False, "an empty path raises")
    except ValueError:
        ok(True, "an empty path raises")

    ok(normalise_rows([{"a": 1}, '{"b": 2}', "not json", 7]) == [{"a": 1}, {"b": 2}],
       "JSONL strings and real objects normalise to the same shape")
    ok(normalise_rows(None) == [], "a None payload normalises to no rows, not a crash")

    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = die_unlooked("planted")
    said = buf.getvalue()
    ok(rc == EXIT_COULD_NOT_LOOK, "the could-not-look path returns its own code")
    ok("COULD NOT LOOK" in said and "match" not in said.lower(),
       "and it words itself as an unread, never as a negative result — a reader "
       "must not be able to mistake it for 'nothing matched'")
    ok(EXIT_COULD_NOT_LOOK != EXIT_FAIL,
       "could_not_look and fail are different exit codes — the whole point")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = report([], [parse_condition("a=1")], ["a=1"], "read 0 row(s)")
    ok(rc == EXIT_FAIL and "ZERO DENOMINATOR" in buf.getvalue(),
       "a fail over an EMPTY source says so — the denominator is the finding")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = report([{"a": 2}], [parse_condition("a=1")], ["a=1"], "read 1 row(s)")
    ok(rc == EXIT_FAIL and "ZERO DENOMINATOR" not in buf.getvalue(),
       "a fail over a NON-empty source is a real negative and is not mislabelled")

    # The positive control, as controls rather than as prose.
    rows3 = [{"kind": "x", "grade": "accruing"}, {"kind": "x", "grade": "ok"}]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = report(rows3, [parse_condition("grade=infeasible")], ["grade=infeasible"],
                    "read 2 row(s)", parse_condition("grade=accruing"), "grade=accruing")
    ok(rc == EXIT_FAIL, "a LIVE control lets a genuine negative stay a negative")
    ok("positive control" in buf.getvalue(),
       "and the passing control is stated in the output, so a fail carries its warrant")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = report(rows3, [parse_condition("grade=infeasible")], ["grade=infeasible"],
                    "read 2 row(s)", parse_condition("gradeX=accruing"), "gradeX=accruing")
    ok(rc == EXIT_COULD_NOT_LOOK,
       "a control that does NOT fire turns the verdict into an unread — a broken "
       "reader must never emit the confident negative that looks like diligence")
    ok("NOT a negative" in buf.getvalue(),
       "and it says so in words, not just in the exit code")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = report(rows3, [parse_condition("grade=accruing")], ["grade=accruing"],
                    "read 2 row(s)", parse_condition("grade=accruing"), "grade=accruing")
    ok(rc == EXIT_PASS, "a control never blocks a genuine PASS")

    print(f"probe-lib: self-test OK — {fired} planted controls all fire")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
