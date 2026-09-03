#!/usr/bin/env python3
"""A row-aware 3-way merge driver for the shared JSON registers.

WHY THIS EXISTS, MEASURED. The registers re-conflict every sibling PR, and the
mechanism is NOT what it looks like. Measured on `main` @1b82ab7 over adjacent
register-touching commit pairs since 2026-08-26, the share of pairs where both
sides bump the SAME header scalar (`updated_at` / `as_of`):

    MANAGER-CHECKLIST.json  29/39 (74%)      SESSIONS.json   14/23 (61%)
    OPEN-PRS.json            8/12 (67%)      OPEN-ITEMS.json  2/65  (3%)
    health-review-backlog.json 1/91 (1%)

PR #10815's ENTIRE conflict in MANAGER-CHECKLIST.json was one line: `as_of`.
⚠️ Sharding one-file-per-row does NOT fix that — the container keeps the scalar.

WHAT IT REFUSES TO DO, AND WHY THAT IS THE POINT. A union-by-id merge is NOT
automatically safe: the manager's own resolver once reported "no id lost, none
resurrected" while silently dropping an edit, because both sides had ADDED the
same id with different content and last-write-wins took one. So:

  * divergent same-id ADD   -> REFUSE (this is the case that bit)
  * divergent same-id EDIT  -> REFUSE
  * delete on one side, EDIT on the other -> REFUSE (deletion is intent, and so
    is the edit; a machine cannot rank them)
  * delete on one side, UNTOUCHED on the other -> STAYS DELETED, never
    resurrected by seeding from base
  * disjoint rows / append+append -> merged
  * a header scalar in TIMESTAMP_KEYS bumped on both sides -> take the max

IT NEVER REFORMATS. Rows are spliced as their ORIGINAL BYTES; equality is judged
semantically (parsed + canonicalised) but emission is byte-for-byte. This is what
keeps `backlog_append.py::append_row`'s exact-serialisation round-trip intact and
what makes OPEN-ITEMS.json — which is NOT byte-reproducible, mixing a literal
em-dash with an escaped one — safe to merge at all. A naive read-append-write
re-attributes ~21k lines to whoever touched it last.

Usage as a git merge driver (see .gitattributes + scripts/ops/install_merge_driver.sh):
    merge_json_register.py %O %A %B          # ancestor, ours(result), theirs
Exit 0 = merged clean. Exit 1 = REFUSED, conflict markers left in %A for a human.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from collections import Counter

ID_FIELDS = ("id", "session_id", "pr", "item_id", "key")
# Header scalars that are pure "when did I last write this" bookkeeping. Both
# sides bumping one is not a disagreement, it is two clocks; take the later.
TIMESTAMP_KEYS = ("updated_at", "as_of", "generated_at", "last_reconciled_at")
# Keys that are only meaningful WITH a timestamp key: resolved by taking the
# whole side whose timestamp is later, never independently.
COUPLED_WITH = {"last_reconciled_at": ("last_reconciled_sha",)}


class ParseError(Exception):
    pass


class Refuse(Exception):
    """A conflict a machine must not resolve."""


# ---------------------------------------------------------------- byte scanner

def _ws(t, i):
    while i < len(t) and t[i] in " \t\r\n":
        i += 1
    return i


def _scan_string(t, i):
    if t[i] != '"':
        raise ParseError("expected string at %d" % i)
    i += 1
    while i < len(t):
        c = t[i]
        if c == "\\":
            i += 2
            continue
        if c == '"':
            return i + 1
        i += 1
    raise ParseError("unterminated string")


def _scan_value(t, i):
    i = _ws(t, i)
    if i >= len(t):
        raise ParseError("truncated")
    c = t[i]
    if c == '"':
        return _scan_string(t, i)
    if c in "{[":
        depth = 0
        while i < len(t):
            ch = t[i]
            if ch == '"':
                i = _scan_string(t, i)
                continue
            if ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        raise ParseError("unterminated container")
    j = i
    while j < len(t) and t[j] not in ",}] \t\r\n":
        j += 1
    return j


def _members(t):
    """Top-level object members as (key, value_start, value_end)."""
    i = _ws(t, 0)
    if i >= len(t) or t[i] != "{":
        raise ParseError("top level is not an object")
    i += 1
    out = []
    while True:
        i = _ws(t, i)
        if i >= len(t):
            raise ParseError("unterminated object")
        if t[i] == "}":
            return out
        if t[i] == ",":
            i += 1
            continue
        ke = _scan_string(t, i)
        key = json.loads(t[i:ke])
        i = _ws(t, ke)
        if t[i] != ":":
            raise ParseError("expected ':' after %r" % key)
        i = _ws(t, i + 1)
        vs = i
        ve = _scan_value(t, i)
        out.append((key, vs, ve))
        i = ve


def _elements(t, s, e):
    """Element spans of the array occupying t[s:e]."""
    i = s + 1
    out = []
    while True:
        i = _ws(t, i)
        if i >= e or t[i] == "]":
            return out
        if t[i] == ",":
            i += 1
            continue
        es = i
        ee = _scan_value(t, i)
        out.append((es, ee))
        i = ee


def _row_id(text):
    try:
        obj = json.loads(text)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    for f in ID_FIELDS:
        if f in obj:
            return "%s=%s" % (f, obj[f])
    return None


def _canon(text):
    """Semantic identity of a row, independent of byte formatting."""
    return json.dumps(json.loads(text), sort_keys=True, ensure_ascii=False)


class RowArray:
    """An array of id-bearing objects, held as ORIGINAL BYTE SPANS."""

    def __init__(self, key, raw, opening, rows, seps, closing):
        self.key, self.raw, self.opening = key, raw, opening
        self.rows = rows            # list[(id, original_text)]
        self.seps, self.closing = seps, closing

    @property
    def sep(self):
        return Counter(self.seps).most_common(1)[0][0] if self.seps else ",\n    "

    def render(self, rows=None):
        rows = self.rows if rows is None else rows
        if not rows:
            return self.opening.rstrip() + self.closing.lstrip() if False else self.raw
        return self.opening + self.sep.join(r[1] for r in rows) + self.closing

    def render_original(self):
        """Reassemble with the ORIGINAL per-element separators — the round-trip."""
        if not self.rows:
            return self.raw
        out = [self.opening]
        for n, (_, txt) in enumerate(self.rows):
            out.append(txt)
            if n < len(self.seps):
                out.append(self.seps[n])
        out.append(self.closing)
        return "".join(out)


def parse(text):
    """-> (segments, arrays_by_key). Segments alternate text / RowArray."""
    segs, arrays = [], {}
    pos = 0
    for key, vs, ve in _members(text):
        if text[vs] != "[":
            continue
        els = _elements(text, vs, ve)
        if not els:
            continue
        ids = [_row_id(text[a:b]) for a, b in els]
        if any(i is None for i in ids):
            continue                      # e.g. "_comment": [...] — header prose
        if len(set(ids)) != len(ids):
            raise ParseError("duplicate row id in %r" % key)
        opening = text[vs:els[0][0]]
        rows = [(ids[n], text[a:b]) for n, (a, b) in enumerate(els)]
        seps = [text[els[n][1]:els[n + 1][0]] for n in range(len(els) - 1)]
        closing = text[els[-1][1]:ve]
        arr = RowArray(key, text[vs:ve], opening, rows, seps, closing)
        segs.append(("text", text[pos:vs]))
        segs.append(("array", arr))
        arrays[key] = arr
        pos = ve
    segs.append(("text", text[pos:]))
    return segs, arrays


def round_trip(text):
    segs, _ = parse(text)
    return "".join(s[1] if s[0] == "text" else s[1].render_original() for s in segs)


# ------------------------------------------------------------------ row merge

def merge_rows(base, ours, theirs, key):
    """Row-level 3-way. Raises Refuse on anything a machine must not decide."""
    b = {i: t for i, t in base.rows}
    o = {i: t for i, t in ours.rows}
    t_ = {i: t for i, t in theirs.rows}

    def same(x, y):
        if x is None or y is None:
            return x is y
        try:
            return _canon(x) == _canon(y)
        except Exception:
            return x == y

    chosen, refusals = {}, []
    for rid in set(b) | set(o) | set(t_):
        bv, ov, tv = b.get(rid), o.get(rid), t_.get(rid)
        if same(ov, tv):
            if ov is not None:
                chosen[rid] = ov
            continue
        if bv is None:
            if ov is not None and tv is not None:
                refusals.append((rid, "both sides ADDED %s with different content" % rid))
            else:
                chosen[rid] = ov if ov is not None else tv
            continue
        if ov is None and tv is None:
            continue                                   # deleted both sides
        if ov is None:
            if same(tv, bv):
                continue                               # deleted by ours, untouched
            refusals.append((rid, "%s DELETED by one side, EDITED by the other" % rid))
            continue
        if tv is None:
            if same(ov, bv):
                continue                               # deleted by theirs, untouched
            refusals.append((rid, "%s DELETED by one side, EDITED by the other" % rid))
            continue
        if same(ov, bv):
            chosen[rid] = tv
        elif same(tv, bv):
            chosen[rid] = ov
        else:
            refusals.append((rid, "both sides EDITED %s differently" % rid))

    if refusals:
        raise Refuse("in %r:\n" % key + "\n".join("    - " + m for _, m in refusals))

    out = []
    for rid, _ in base.rows:
        if rid in chosen:
            out.append((rid, chosen.pop(rid)))
    for rid, _ in ours.rows:
        if rid in chosen:
            out.append((rid, chosen.pop(rid)))
    for rid, _ in theirs.rows:
        if rid in chosen:
            out.append((rid, chosen.pop(rid)))
    return out


# --------------------------------------------------------------- header merge

_KV = re.compile(r'^\s*"([^"]+)"\s*:\s*(.*?),?\s*$')


def _kv(lines):
    out = []
    for ln in lines:
        m = _KV.match(ln)
        if not m:
            return None
        out.append((m.group(1), m.group(2), ln))
    return out


def _resolve_ts(ours_lines, theirs_lines):
    """Resolve a header conflict that is only bookkeeping timestamps."""
    ko, kt = _kv(ours_lines), _kv(theirs_lines)
    if ko is None or kt is None:
        return None
    if [k for k, _, _ in ko] != [k for k, _, _ in kt]:
        return None
    keys = [k for k, _, _ in ko]
    driving = [k for k in keys if k in COUPLED_WITH]
    if driving:
        d = driving[0]
        followers = set(COUPLED_WITH[d])
        if not set(keys) <= ({d} | followers):
            return None
        ov = dict((k, v) for k, v, _ in ko)[d]
        tv = dict((k, v) for k, v, _ in kt)[d]
        return list(ours_lines) if ov >= tv else list(theirs_lines)
    if not all(k in TIMESTAMP_KEYS for k in keys):
        return None
    out = []
    for (k, vo, lo), (_, vt, lt) in zip(ko, kt):
        out.append(lo if vo >= vt else lt)
    return out


def merge_header(base, ours, theirs):
    with tempfile.TemporaryDirectory() as td:
        p = {}
        for n, c in (("b", base), ("o", ours), ("t", theirs)):
            p[n] = "%s/%s" % (td, n)
            open(p[n], "w", encoding="utf-8").write(c)
        r = subprocess.run(["git", "merge-file", "-p", "--diff3", p["o"], p["b"], p["t"]],
                           capture_output=True)
        merged = r.stdout.decode("utf-8")
        if r.returncode == 0:
            return merged
    lines, out, i, unresolved = merged.split("\n"), [], 0, []
    while i < len(lines):
        if not lines[i].startswith("<<<<<<<"):
            out.append(lines[i])
            i += 1
            continue
        i += 1
        a = []
        while i < len(lines) and not lines[i].startswith("|||||||"):
            a.append(lines[i])
            i += 1
        while i < len(lines) and not lines[i].startswith("======="):
            i += 1
        i += 1
        bl = []
        while i < len(lines) and not lines[i].startswith(">>>>>>>"):
            bl.append(lines[i])
            i += 1
        i += 1
        res = _resolve_ts([x for x in a if x.strip()], [x for x in bl if x.strip()])
        if res is None:
            unresolved.append("\n".join(a) + "  <-> " + "\n".join(bl))
            out += ["<<<<<<< ours"] + a + ["======="] + bl + [">>>>>>> theirs"]
        else:
            out += res
    if unresolved:
        raise Refuse("header scalars disagree and are not bookkeeping timestamps:\n"
                     + "\n".join("    - " + u.strip() for u in unresolved))
    return "\n".join(out)


# ------------------------------------------------------------------ top level

def merge(base_t, ours_t, theirs_t):
    bs, ba = parse(base_t)
    os_, oa = parse(ours_t)
    ts, ta = parse(theirs_t)
    if not (set(ba) == set(oa) == set(ta)):
        raise Refuse("the set of row arrays differs between sides: "
                     "base=%s ours=%s theirs=%s" % (sorted(ba), sorted(oa), sorted(ta)))

    problems, merged_arrays = [], {}
    for k in oa:
        try:
            merged_arrays[k] = merge_rows(ba[k], oa[k], ta[k], k)
        except Refuse as e:
            problems.append(str(e))

    b_txt = [s[1] for s in bs if s[0] == "text"]
    o_txt = [s[1] for s in os_ if s[0] == "text"]
    t_txt = [s[1] for s in ts if s[0] == "text"]
    headers = []
    if not (len(b_txt) == len(o_txt) == len(t_txt)):
        problems.append("header segment count differs between sides")
    else:
        for bb, oo, tt in zip(b_txt, o_txt, t_txt):
            try:
                headers.append(merge_header(bb, oo, tt))
            except Refuse as e:
                problems.append(str(e))
                headers.append(oo)
    if problems:
        raise Refuse("\n".join(problems))

    out, hi = [], 0
    for kind, val in os_:
        if kind == "text":
            out.append(headers[hi])
            hi += 1
        else:
            out.append(val.render(merged_arrays[val.key]))
    return "".join(out)


def main(argv):
    if len(argv) >= 4 and argv[1] == "--check-round-trip":
        bad = 0
        for path in argv[2:]:
            txt = open(path, encoding="utf-8").read()
            ok = round_trip(txt) == txt
            print("%-6s %s" % ("OK" if ok else "DIFFER", path))
            bad += 0 if ok else 1
        return 1 if bad else 0
    if len(argv) < 4:
        sys.stderr.write(__doc__)
        return 2
    anc, cur, other = argv[1], argv[2], argv[3]
    b = open(anc, encoding="utf-8").read()
    o = open(cur, encoding="utf-8").read()
    t = open(other, encoding="utf-8").read()
    try:
        res = merge(b, o, t)
    except (Refuse, ParseError) as e:
        sys.stderr.write("merge_json_register: REFUSING to auto-resolve %s\n%s\n"
                         % (argv[4] if len(argv) > 4 else cur, e))
        return 1
    open(cur, "w", encoding="utf-8").write(res)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
