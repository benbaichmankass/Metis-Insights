#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::scope-overlap-guard (--self-test) + scope-overlap-audit.yml (live)
"""Does this PR touch a file another LIVE session has already declared? — W3.

WHY THIS, AND NOT A MERGE SERIALIZER
------------------------------------
W3 was planned as a merge serializer. **The measurement refuted the premise.**
Over 2026-08-30T19:13Z -> 2026-08-31T12:53Z, 39 merges landed on `main` — one
every 27.3 min, with a MEDIAN gap of 15.9 min between merges from different
sources. Nothing was racing for the merge button, and `require-up-to-date` has
been off since 2026-08-10, so one merge does not invalidate another PR's checks.

The one real collision in that window is instructive. PR #10582 went `dirty`
because #10579 and #10580 landed under it — and those two merged **23 minutes
apart**, already serial. Serialising merges would not have prevented it. What
made the PR dirty was its BRANCH BEING OLD, which no merge ordering fixes.

Nor was it under-declaration: the other session's 11:41Z START named
`docs/claude/OPEN-ITEMS.json` explicitly and even carried a collision heads-up.
The declaration existed and was correct. What failed is that **a declared scope
never reaches a session that is already running** — the `PreToolUse` guard that
would have caught it is never invoked on Claude Code on the web
(`BL-20260820-PROJECT-HOOKS-INERT-ON-WEB`).

So this does not gate, order, or enforce anything. It carries information that
already exists to the one surface a running session cannot miss: its own PR.

WHAT IT DOES NOT DO, DELIBERATELY
---------------------------------
It is **non-blocking**. A required check verifying the protocol was considered
and REJECTED with the operator on 2026-08-20: it would be presence-only, so it
would be cheaper to satisfy by posting a formulaic comment than by doing the
work of reading the board — enforcing the ARTIFACT of the protocol, not the
protocol. Same reasoning holds here, so this reports and stops.

THREE STATES, NEVER COLLAPSED
-----------------------------
    overlap         — this PR touches a path another session declared
    no_overlap      — the board was read, and nothing this PR touches was claimed
    could_not_check — we did not look (board unreadable, no comments on a board
                      that is never silent, changed-file list unavailable)

`could_not_check` is emphatically not `no_overlap`.

THE EXTRACTOR'S OWN COVERAGE IS PART OF THE OUTPUT
--------------------------------------------------
STARTs are prose. This parses backticked path-ish tokens, expands `{a,b}` brace
groups, and treats a trailing `/` as a prefix. It WILL under-extract — a START
saying "several `tests/`" yields a prefix, but one saying "the usual files"
yields nothing. Under-extraction reports a false clean, which is the dangerous
direction, so every verdict ships `parsed` (what it resolved) and
`unparsed_hints` (path-ish prose it saw and could NOT resolve). A `no_overlap`
over zero parsed paths is not evidence of anything, and says so.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

STATES = ("overlap", "no_overlap", "could_not_check")

#: A START declares scope. Only these are read as declarations — a QUESTION, a
#: DONE, a merge-slot claim or an audit comment declares nothing.
_START_RE = re.compile(r"(?:▶️|:arrow_forward:)?\s*\*{0,2}START\*{0,2}\b", re.I)

#: A backticked token that looks like a repo path: has a `/` or a known
#: extension, and no spaces. Deliberately conservative — a false path costs a
#: spurious overlap report, which is noise on a mechanism whose whole value is
#: that people read it.
_PATH_RE = re.compile(r"`([^`\s]+)`")
_EXTS = (".py", ".yml", ".yaml", ".json", ".md", ".sh", ".toml", ".cfg", ".txt")

#: Path-ish prose the extractor could not resolve to a concrete path. Recorded
#: so a `no_overlap` verdict carries its own coverage rather than implying the
#: START declared nothing.
_HINT_RE = re.compile(r"`([^`\s]*(?:several|various|the usual|etc)[^`]*)`", re.I)

def _looks_like_path(tok: str) -> bool:
    if tok.startswith(("http://", "https://", "#")):
        return False
    if " " in tok:
        return False
    return "/" in tok or tok.endswith(_EXTS)


def expand_braces(tok: str) -> list[str]:
    """`a/{b,c}.py` -> ['a/b.py', 'a/c.py']. The STARTs really use this form."""
    m = re.search(r"\{([^{}]*)\}", tok)
    if not m:
        return [tok]
    out: list[str] = []
    for part in m.group(1).split(","):
        out.extend(expand_braces(tok[: m.start()] + part.strip() + tok[m.end():]))
    return out


#: ⚠️ SECTION AWARENESS IS NOT POLISH — WITHOUT IT THIS MECHANISM IS INVERTED.
#:
#: The first version read every backticked path in the body. Run against the
#: real 2026-08-31 START it reported an overlap on `docs/claude/INDEX.md` — a
#: path that comment named in its **"Not touching:"** line. The extractor fired
#: on the one file the other session went out of its way to say it would NOT
#: touch. That is a label describing the opposite of what was computed, and it
#: is the desensitized-alarm direction: a mechanism that fires on explicit
#: non-collisions trains people to stop reading it.
#:
#: So a path counts as DECLARED only under a declaration marker, and a path
#: under a negation marker is recorded as EXPLICITLY EXCLUDED rather than
#: silently dropped, so the output can show the negation was honoured.
#:
#: NEGATION IS TESTED FIRST, because "not touching" contains "touching".
_NEGATION_MARKERS = ("not touching", "not editing", "not going to touch",
                     "won't touch", "will not touch", "not claiming",
                     "hands off", "leaving alone", "untouched")
_DECLARATION_MARKERS = ("touching", "scope", "files:", "editing", "claiming")


def _classify(line: str):
    """`declare` / `exclude` / None (not a marker — inherits the open section)."""
    low = line.lower()
    if any(m in low for m in _NEGATION_MARKERS):
        return "exclude"
    if any(m in low for m in _DECLARATION_MARKERS):
        return "declare"
    return None


def parse_declared_paths(body: str):
    """Return (declared, EXPLICITLY EXCLUDED, unresolved hints).

    A path counts only while inside a section. Anything before the first marker
    is attributed to NEITHER: a path mentioned in prose is a mention, not a
    claim, and treating it as one is what made the first version fire on a
    "Not touching:" list.
    """
    declared, excluded, hints = set(), set(), []
    section = None

    for line in (body or "").splitlines():
        marker = _classify(line)
        if marker is not None:
            section = marker
        elif not line.strip():
            # A blank line closes the section, so a later paragraph saying
            # "the fix is in `x.py`" is prose rather than a claim.
            section = None
        if section is None:
            continue
        bucket = declared if section == "declare" else excluded
        for tok in _PATH_RE.findall(line):
            for cand in expand_braces(tok):
                cand = cand.strip().rstrip(",;")
                if _looks_like_path(cand):
                    bucket.add(cand)
        if section == "declare":
            hints.extend(h.strip() for h in _HINT_RE.findall(line))

    # Named in BOTH -> excluded. The explicit negative wins, because the cost of
    # a false alarm here is the alarm itself being ignored.
    declared -= excluded
    return declared, excluded, hints


def matches(changed: str, declared: str) -> bool:
    """A declaration matches a changed file exactly, or as a directory prefix.

    The prefix rule is what rescues most of the extractor's imprecision: a START
    naming `scripts/ci/` covers every file under it without listing them.
    """
    if changed == declared:
        return True
    if declared.endswith("/"):
        return changed.startswith(declared)
    # A bare directory (no extension, no trailing slash) still reads as a prefix.
    if not declared.endswith(_EXTS):
        return changed.startswith(declared.rstrip("/") + "/")
    return False


def attribution(st: dict, *, my_branch: str, my_pr: int | None) -> str:
    """Whose START is this? THREE states, and the third is the whole point.

    `mine` / `other` / `unattributable`. The first live fires of this detector
    (PRs #10590 and #10592, 2026-08-31) were BOTH false positives that reported
    this session's own START back at it, because self-exclusion had exactly one
    key — a backticked `claude/...` branch in the body — and that session's START
    named its PR number instead. An unparseable branch fell through to the
    `other` branch of an implicit two-way test, so "we could not tell whose this
    is" was silently rendered as "another session declared this".

    A detector whose first two findings are noise is the desensitized-alarm P1,
    and this one was built expressly to avoid that.

    `unattributable` is NOT resolved toward `mine`. Suppressing a real overlap is
    the dangerous direction; the caller reports these separately and says so.
    """
    branch = st.get("branch") or ""
    if branch:
        return "mine" if my_branch and branch == my_branch else "other"
    # No branch to compare. A START that names THIS PR identifies itself — but
    # only consult that when the branch is absent, so a START that legitimately
    # MENTIONS another PR can never suppress a real overlap.
    if my_pr and re.search(rf"#{my_pr}\b", st.get("body") or ""):
        return "mine"
    return "unattributable"


def assess(changed_files, starts, *, my_branch: str, my_pr: int | None = None) -> dict:
    """`starts` is [{author_hint, branch, body, url, created_at}, ...]."""
    if not changed_files:
        return {"state": "could_not_check",
                "reason": "no changed-file list — nothing was compared",
                "hits": [], "parsed": 0, "explicitly_excluded": 0, "unparsed_hints": []}
    if not starts:
        # A board with no STARTs in the window is possible, but on a board that
        # is never silent it is far more likely a failed read. Refused, not
        # reported as clean — the merge-claim-audit denominator lesson.
        return {"state": "could_not_check",
                "reason": "no START comments found in the window on a board that "
                          "is never silent — treated as a failed read, not as "
                          "'nobody declared anything'",
                "hits": [], "parsed": 0, "explicitly_excluded": 0, "unparsed_hints": []}

    hits, unattributed, parsed_total, excluded_total, hints_total = [], [], 0, 0, []
    for st in starts:
        # A session never collides with itself.
        who = attribution(st, my_branch=my_branch, my_pr=my_pr)
        if who == "mine":
            continue
        declared, excluded, hints = parse_declared_paths(st.get("body", ""))
        parsed_total += len(declared)
        excluded_total += len(excluded)
        hints_total.extend(hints)
        for f in changed_files:
            for d in sorted(declared):
                if matches(f, d):
                    row = {"file": f, "declared": d,
                           "branch": st.get("branch"), "url": st.get("url"),
                           "at": st.get("created_at"), "attribution": who}
                    (hits if who == "other" else unattributed).append(row)
                    break
    return {
        # Still exactly three states. An unattributable hit IS worth surfacing —
        # it may be another session — so it counts toward `overlap`; what changes
        # is that render() never CLAIMS it belongs to another session.
        "state": "overlap" if (hits or unattributed) else "no_overlap",
        "reason": "",
        "hits": hits,
        "unattributed_hits": unattributed,
        # Always shipped: a `no_overlap` over 0 parsed paths establishes nothing.
        "parsed": parsed_total,
        # Shipped so a reader can see negations were honoured rather
        # than assume it. The first version had no such concept.
        "explicitly_excluded": excluded_total,
        "unparsed_hints": sorted(set(hints_total)),
    }


def render(v: dict, *, pr: int, changed_n: int) -> str:
    if v["state"] == "could_not_check":
        return (f"### 🔍 scope-overlap audit — COULD NOT CHECK\n\n"
                f"**This is not a clean result.** {v['reason']}\n\n"
                f"Read the coordination board yourself before assuming no other "
                f"session has declared these files.\n")
    if v["state"] == "no_overlap":
        return (f"### 🔍 scope-overlap audit — no overlap\n\n"
                f"{changed_n} changed file(s) compared against {v['parsed']} path(s) "
                f"declared by other sessions' recent STARTs.\n")
    lines = [f"### ⚠️ scope-overlap audit — PR #{pr} touches files another session declared",
             "",
             "**This is an observation, not a block, and nothing is gated.** Another live "
             "session posted a `START` on the coordination board naming paths this PR also "
             "changes. That is normal when the edits are additive, and a real problem when "
             "they are not — you are the one who can tell.", ""]
    for h in v["hits"]:
        lines.append(f"- `{h['file']}` — declared as `{h['declared']}` by "
                     f"[`{h['branch']}`]({h['url']}) at {h['at']}")
    # Reported, never merged into the list above: the heading there asserts
    # "another session", and for these we do not know that. Saying it anyway is
    # what made this detector's first two live fires noise.
    un = v.get("unattributed_hits") or []
    if un:
        lines += ["", "**Declared by a START we could not attribute** — its comment "
                  "names no `claude/...` branch and does not name this PR, so we "
                  "cannot tell whether it is another session or your own. Resolved "
                  "toward reporting, not toward silence: suppressing a real overlap "
                  "is the worse error.", ""]
        for h in un:
            lines.append(f"- `{h['file']}` — declared as `{h['declared']}` by "
                         f"[an unattributed START]({h['url']}) at {h['at']}")
    lines += ["",
              f"_Compared {changed_n} changed file(s) against {v['parsed']} declared "
              f"path(s) — {len(v['hits'])} attributed to another session, "
              f"{len(un)} unattributable._"]
    if v["unparsed_hints"]:
        lines += ["",
                  "⚠️ **This list is a LOWER BOUND.** These declarations were prose the "
                  "extractor could not resolve to concrete paths, so files they cover are "
                  "not checked: " + ", ".join(f"`{h}`" for h in v["unparsed_hints"])]
    return "\n".join(lines)


def _self_test() -> int:
    fired = 0

    def ok(cond, label):
        nonlocal fired
        assert cond, f"control FAILED: {label}"
        fired += 1

    ok(expand_braces("a/{b,c}.py") == ["a/b.py", "a/c.py"],
       "brace groups expand — the STARTs really use this form")
    ok(expand_braces("a/b.py") == ["a/b.py"], "a plain path is unchanged")

    body = ("**Touching:** `scripts/ci/check_collapsed_states.py`, "
            "`scripts/research/{research_queue,research_disposition}.py`, "
            "`docs/claude/OPEN-ITEMS.json`, several `tests/`. See `#10575` and "
            "`https://example.com/x`.")
    paths, excl, hints = parse_declared_paths(body)
    ok("scripts/ci/check_collapsed_states.py" in paths, "a plain backticked path is read")
    ok("scripts/research/research_queue.py" in paths
       and "scripts/research/research_disposition.py" in paths,
       "both halves of a brace group are read")
    ok("docs/claude/OPEN-ITEMS.json" in paths, "the file that caused the real collision is read")
    ok("tests/" in paths, "a trailing-slash directory is read as a prefix")
    ok("#10575" not in paths, "a PR reference is not a path")
    ok(not any(p.startswith("http") for p in paths), "a URL is not a path")

    # ── the negation regression, planted from the REAL comment that caused it ──
    real = ("**Touching:** `scripts/ci/run_guards.py`, `docs/claude/OPEN-ITEMS.json`.\n"
            "\n"
            "Some prose that merely mentions `src/runtime/orders.py` in passing.\n"
            "\n"
            "Not touching: `docs/claude/INDEX.md`, `claude-run-failure-alert.yml`.\n")
    dec, exc, _ = parse_declared_paths(real)
    ok("docs/claude/INDEX.md" not in dec,
       "a path under 'Not touching:' is NOT declared — the inversion that made the "
       "first version fire on the one file the other session promised to avoid")
    ok("docs/claude/INDEX.md" in exc, "and it is recorded as EXPLICITLY excluded, not dropped")
    ok("scripts/ci/run_guards.py" in dec and "docs/claude/OPEN-ITEMS.json" in dec,
       "the real declarations still parse")
    ok("src/runtime/orders.py" not in dec and "src/runtime/orders.py" not in exc,
       "a path in loose prose is a MENTION, not a claim — attributed to neither")

    both = "Touching: `a/x.py`\nNot touching: `a/x.py`\n"
    dbo, ebo, _ = parse_declared_paths(both)
    ok("a/x.py" not in dbo and "a/x.py" in ebo,
       "named in both -> excluded; the explicit negative wins because a false alarm "
       "costs the alarm being read at all")

    v_neg = assess(["docs/claude/INDEX.md"],
                   [{"branch": "claude/other", "body": real, "url": "u", "created_at": "t"}],
                   my_branch="claude/mine")
    ok(v_neg["state"] == "no_overlap" and v_neg["explicitly_excluded"] >= 1,
       "end to end: the excluded file reports no_overlap AND surfaces the exclusion count")

    # ── ATTRIBUTION: the regression planted from the REAL false positives ──
    # PRs #10590 and #10592 (2026-08-31) were this detector's first two live
    # fires and BOTH reported this session's own START back at it. The START
    # named its PR number, not a `claude/...` branch, so `branchOf` yielded ""
    # and the implicit two-way test filed it under "another session".
    mine_no_branch = {
        "branch": "", "url": "u", "created_at": "t",
        "body": ("▶️ START · session_012LgMzB\n"
                 "**Touching:** `.github/workflows/pytest-run.yml`, "
                 "`.github/workflows/pytest-collect.yml`, "
                 "`.github/workflows/guards.yml`. PR **#10590** (draft)."),
    }
    changed = [".github/workflows/pytest-run.yml",
               ".github/workflows/pytest-collect.yml",
               ".github/workflows/guards.yml"]

    ok(attribution(mine_no_branch, my_branch="claude/x", my_pr=10590) == "mine",
       "a START naming THIS PR is mine even with no parseable branch — the exact "
       "miss that made #10590's own START read as a foreign declaration")
    v_self = assess(changed, [mine_no_branch], my_branch="claude/x", my_pr=10590)
    ok(v_self["state"] == "no_overlap" and not v_self["hits"],
       "end to end: the live #10590 false positive does not reproduce")

    ok(attribution(mine_no_branch, my_branch="claude/x", my_pr=99999) == "unattributable",
       "the same START against a DIFFERENT PR is unattributable — not silently "
       "promoted to another session, and not silently claimed as mine either")
    v_un = assess(changed, [mine_no_branch], my_branch="claude/x", my_pr=99999)
    ok(v_un["state"] == "overlap" and not v_un["hits"] and len(v_un["unattributed_hits"]) == 3,
       "an unattributable hit is REPORTED (suppressing a real overlap is the worse "
       "error) but is kept out of `hits`, which asserts 'another session'")
    md_un = render(v_un, pr=99999, changed_n=3)
    ok("could not attribute" in md_un and "unattributable" in md_un,
       "and the rendered comment says so rather than asserting another session")

    ok(attribution({"branch": "claude/other", "body": "#10590"},
                   my_branch="claude/mine", my_pr=10590) == "other",
       "a PARSED branch always wins: a START that merely MENTIONS this PR can "
       "never suppress a real overlap")
    ok(attribution({"branch": "claude/mine", "body": ""},
                   my_branch="claude/mine", my_pr=None) == "mine",
       "the original branch-equality path still excludes")

    ok(matches("tests/test_x.py", "tests/"), "a trailing-slash prefix matches beneath it")
    ok(matches("scripts/ci/a.py", "scripts/ci"), "a bare directory matches as a prefix")
    ok(not matches("tests_other/x.py", "tests/"), "the prefix does not leak across a sibling dir")
    ok(matches("a/b.py", "a/b.py"), "an exact path matches")
    ok(not matches("a/bc.py", "a/b.py"), "a longer filename is not a match")

    starts = [{"branch": "claude/other", "body": body, "url": "u", "created_at": "t"}]
    v = assess(["docs/claude/OPEN-ITEMS.json"], starts, my_branch="claude/mine")
    ok(v["state"] == "overlap" and v["hits"][0]["declared"] == "docs/claude/OPEN-ITEMS.json",
       "the real 2026-08-31 collision is detected")
    ok(v["unparsed_hints"] == [], "a resolvable body reports no unresolved hints")

    v = assess(["src/unrelated.py"], starts, my_branch="claude/mine")
    ok(v["state"] == "no_overlap" and v["parsed"] > 0,
       "a clean PR reports no_overlap WITH its denominator")

    v = assess(["docs/claude/OPEN-ITEMS.json"], starts, my_branch="claude/other")
    ok(v["state"] == "no_overlap", "a session never collides with its OWN declaration")

    v = assess([], starts, my_branch="claude/mine")
    ok(v["state"] == "could_not_check", "no changed-file list is could_not_check, NOT no_overlap")
    v = assess(["a.py"], [], my_branch="claude/mine")
    ok(v["state"] == "could_not_check" and "never silent" in v["reason"],
       "an empty board is a failed read, not 'nobody declared anything'")

    vague = [{"branch": "claude/other", "url": "u", "created_at": "t",
              "body": "**Touching:** `docs/claude/OPEN-ITEMS.json` and `several other files`."}]
    v = assess(["docs/claude/OPEN-ITEMS.json"], vague, my_branch="claude/mine")
    ok(v["unparsed_hints"] and "LOWER BOUND" in render(v, pr=1, changed_n=1),
       "unresolvable prose makes the report declare itself a lower bound")

    ok("not a clean result" in render(
        {"state": "could_not_check", "reason": "r", "hits": [], "parsed": 0,
         "explicitly_excluded": 0, "unparsed_hints": []}, pr=1, changed_n=0),
       "the could-not-check render never reads as a clean pass")

    ok(set(STATES) == {"overlap", "no_overlap", "could_not_check"},
       "the three states are exactly these")

    print(f"scope-overlap: self-test OK — {fired} planted controls all fire")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--input", help="JSON: {changed_files, starts, my_branch, pr}")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    if not args.input:
        ap.error("pass --self-test or --input")
    data = json.loads(open(args.input, encoding="utf-8").read())
    v = assess(data.get("changed_files") or [], data.get("starts") or [],
               my_branch=data.get("my_branch") or "",
               my_pr=data.get("pr"))
    print(json.dumps({**v, "markdown": render(v, pr=data.get("pr", 0),
                                              changed_n=len(data.get("changed_files") or []))},
                     indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
