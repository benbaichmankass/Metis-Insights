#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::scope-overlap-guard (--self-test) + scope-overlap-audit.yml (live)
r"""Does this PR touch a file another LIVE session has already declared? — W3.

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

THREE VERDICT STATES, NEVER COLLAPSED
-------------------------------------
    overlap         — this PR touches a path some START declared
    no_overlap      — the board was read, and nothing this PR touches was claimed
    could_not_check — we did not look (board unreadable, no comments on a board
                      that is never silent, changed-file list unavailable)

`could_not_check` is emphatically not `no_overlap`.

⚠️ A MENTION IS NOT A CLAIM — AND THAT RULE WAS ONLY EVER APPLIED TO PATHS
--------------------------------------------------------------------------
`parse_declared_paths` already knows that a path in loose prose is a MENTION,
not a declaration; that is the fix recorded below for the `Not touching:` list.
**The same rule was never applied to the declarer's own IDENTITY**, and that is
the defect this module carried for its first nine live fires.

The workflow used to resolve "whose START is this?" with
`(String(body).match(/`(claude\/[^`\s]+)`/) || [])[1]` — **the first backticked
`claude/...` token anywhere in the body**. Measured against the real board:

  * `issuecomment-5503070932` (2026-09-02T01:42:42Z) is the manager's own,
    deliberately precise START. It contains **exactly one** backticked
    `claude/...` token, and that token is **another session's branch, quoted in
    prose complaining that that session's 14-hour-old declaration keeps matching
    it**. The extractor stamped that innocent branch onto the manager's own
    START and reported it back to the manager as a foreign declaration.

That is not a self-match. It is a **fabricated attribution to a named third
party** — strictly worse, because a reader can act on the name. It is the same
inversion as the `Not touching:` bug, one level up: a mechanism firing on the
exact comment written to prevent it firing.

So identity is now parsed like paths are — **only from a self-identification
context** (the START line, or a `Session:` / `Branch:` label line), never from
prose. See `parse_identity`.

FOUR ATTRIBUTION STATES, NEVER COLLAPSED
----------------------------------------
The previous output collapsed four different facts into "another session
declared". They have four different responses and are now kept apart:

    mine           — YOU declared this. Suppressed from the hit list, but
                     COUNTED in the footer, so over-suppression stays visible.
    other_active   — a sibling declared it and we have NO evidence it finished.
                     This is the headline. This is the real collision.
    other_landed   — a sibling declared it and its declaring branch has since
                     MERGED/CLOSED, or it posted a `DONE`. Reported in its own
                     section: worth knowing (those files moved under you), but
                     it is not a live collision and must not read as one.
    unattributable — we could not tell whose it is. PRESERVED, and still
                     reported: suppressing a real overlap is the worse error.

⚠️ `other_landed` SAYS THE BRANCH LANDED, NOT THAT THE SESSION ENDED.
A session merges many PRs and keeps working — the manager merged 36 in one
night. The evidence is named per hit (`landed_because`) rather than implied,
and an UNKNOWN branch state resolves to `other_active`, never to `landed`:
staleness must fail toward reporting.

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


ATTRIBUTIONS = ("mine", "other_active", "other_landed", "unattributable")

#: ── IDENTITY: parsed ONLY from a self-identification context ──────────────
#: The declarer names itself on the START line, or on a `Session:` / `Branch:`
#: label line. Anywhere else is PROSE, and a branch named in prose belongs to
#: whoever the prose is ABOUT — which on 2026-09-02 was a different session.
_SESSION_RE = re.compile(r"\bsession_[A-Za-z0-9]{6,}")
#: "branch `x`", "Branch: `x`", "**Branch:** `x`", "(branch `x`", "on branch `x`".
_BRANCH_AFTER_RE = re.compile(r"\bbranch\b\W{0,6}`([^`\s]+)`", re.I)
_BRANCH_LABEL_RE = re.compile(r"^[\s>#*\-•·]*\**\s*branch\**\s*:", re.I)
_IDENTITY_LABEL_RE = re.compile(r"^[\s>#*\-•·]*\**\s*(?:session|branch)\**\s*:", re.I)

#: A PR states its own session identity in the MANDATED attribution footer, as a
#: `claude.ai/code/session_...` URL. That structured form is the whole point: a
#: PR body also NAMES other sessions in prose (PR #10729 names two sub-sessions
#: it spawned), so harvesting bare `session_...` tokens from a PR body would let
#: a sibling's START be mistaken for our own — a suppressed REAL overlap, the
#: worse error. Only the URL form counts.
_PR_SESSION_URL_RE = re.compile(r"claude\.ai/code/(session_[A-Za-z0-9]{6,})", re.I)

#: A `DONE` retires the START that shares its identity. This is the protocol's
#: OWN liveness signal, which is why it is preferred over the branch-state
#: heuristic below.
#:
#: ⚠️ ANCHORED TO THE FIRST NON-EMPTY LINE, unlike `_START_RE`, and the asymmetry
#: is deliberate. A false START only adds a declaration, which over-reports — the
#: safe direction. A false DONE RETIRES a live declaration, which suppresses a
#: real overlap — the one error this module must never make. An unanchored
#: `DONE\b` would fire on any board comment saying "done" in its opening
#: paragraph, and the board is full of prose. The protocol's actual form is a
#: header: `✅ DONE · session_… · branch …`.
_DONE_RE = re.compile(r"^[\s>#*_]*(?:✅|:white_check_mark:)?\s*\*{0,2}DONE\*{0,2}\b", re.I)


def _first_line(body: str) -> str:
    for line in (body or "").splitlines():
        if line.strip():
            return line
    return ""


def is_start(body: str) -> bool:
    return bool(_START_RE.search((body or "")[:400]))


def is_done(body: str) -> bool:
    """A DONE header, not the word 'done' in prose. See `_DONE_RE`."""
    return not is_start(body) and bool(_DONE_RE.match(_first_line(body)))


def _identity_lines(body: str) -> list[str]:
    """The lines on which a comment may speak about ITSELF.

    Line 0 (STARTs open with their own identity), a START-marked line in the
    first few, and any `Session:` / `Branch:` label line. Nothing else — that
    exclusion is the entire fix.
    """
    lines = (body or "").splitlines()
    out: list[str] = []
    for i, line in enumerate(lines):
        if i == 0 or (i < 4 and _START_RE.search(line)) or _IDENTITY_LABEL_RE.match(line):
            out.append(line)
    return out


def parse_identity(body: str) -> dict:
    """Who is this comment's author? -> {'sessions': [...], 'branches': [...]}.

    At most ONE session id and ONE branch per identity line: the FIRST of each.
    A declarer identifies itself once, and the real board carries the line
    `- Session: `session_A` (child of manager `session_B`)` — taking both would
    let the MANAGER's PR suppress a sub-session's genuine declaration.
    """
    sessions, branches = [], []
    for line in _identity_lines(body):
        m = _SESSION_RE.search(line)
        if m and m.group(0) not in sessions:
            sessions.append(m.group(0))
        b = _BRANCH_AFTER_RE.search(line)
        if not b and _BRANCH_LABEL_RE.match(line):
            # A `Branch:` label line's first backticked token IS the branch,
            # even when the word "branch" is not repeated beside it.
            b = _PATH_RE.search(line)
        if b and b.group(1) not in branches:
            branches.append(b.group(1))
    return {"sessions": sessions, "branches": branches}


def pr_session_ids(pr_body: str) -> list[str]:
    """This PR's OWN session identity, from the attribution footer URL only.

    ⚠️ DECLARED RESIDUAL, not a solved problem. This trusts that a
    `claude.ai/code/session_...` URL in a PR body is the PR's own footer. A body
    that QUOTES another session's footer — a pasted handoff prompt is the
    realistic case — would let that session's START read as ours and be
    suppressed. The tighter alternatives are worse: taking only the LAST URL
    breaks on any note appended after the footer, and taking none at all
    reinstates the branch-only identity this fix exists to remove. Harvesting
    bare `session_...` tokens instead is far worse still — PR #10729's body
    names two sub-sessions it spawned. Left as-is and stated, with the exposure
    bounded: a suppressed row still requires that session to have declared an
    overlapping path in the same 24h window.
    """
    out: list[str] = []
    for s in _PR_SESSION_URL_RE.findall(pr_body or ""):
        if s not in out:
            out.append(s)
    return out


def attribution(st: dict, *, my_branch: str, my_pr: int | None = None,
                my_sessions=None) -> str:
    """Whose START is this? `mine` / `other` / `unattributable`.

    IDENTITY IS A CLAIM THE DECLARER MAKES ABOUT ITSELF, so it is read only from
    `parse_identity`'s self-identification context. Reading it from anywhere in
    the body is what made the manager's own START report as
    `claude/trading-system-workflow-design-1ln10f` — a branch it named only to
    complain about.

    A SESSION IS NOT A BRANCH, and that is the second half of the defect. The
    manager posts one START per session and opens PRs from short-lived branches
    (`claude/manager-state-0316`), so branch equality could NEVER match its own
    declaration however precisely it declared. Session id is the durable key;
    the branch stays a key too, so a session that takes over a branch is still
    recognised.

    `unattributable` is NOT resolved toward `mine`. Suppressing a real overlap is
    the dangerous direction; the caller reports these separately and says so.
    """
    my_sessions = list(my_sessions or [])
    ident = parse_identity(st.get("body") or "")

    # ── positive identity matches first, either key ──
    if my_sessions and set(ident["sessions"]) & set(my_sessions):
        return "mine"
    if my_branch and my_branch in ident["branches"]:
        return "mine"

    # ── then the negatives, but only where BOTH sides actually identified ──
    if ident["sessions"] and my_sessions:
        return "other"          # both named themselves, and they differ
    if ident["branches"]:
        return "other"          # it named a branch, and it is not ours

    # No comparable identity. A START that names THIS PR identifies itself — but
    # only consult that last, so a START that legitimately MENTIONS another PR
    # can never suppress a real overlap.
    if my_pr and re.search(rf"#{my_pr}\b", st.get("body") or ""):
        return "mine"
    return "unattributable"


def liveness(st: dict, *, done_posts=None, branch_states=None) -> tuple[str, str]:
    """Has this declaration's work landed? -> (`active` | `landed`, evidence).

    ⚠️ FAILS TOWARD `active`. An unknown branch, an unparsed identity, a board we
    could not fully read — all resolve to `active`, because declaring a live
    session finished is exactly the suppression this module must never do.

    ⚠️ `landed` IS A STATEMENT ABOUT THE BRANCH, NOT THE SESSION. A session
    merges many PRs and keeps working. The evidence string is returned so the
    report says which signal fired instead of implying a stronger one.
    """
    ident = parse_identity(st.get("body") or "")
    mine_s, mine_b = set(ident["sessions"]), set(ident["branches"])

    # (a) The protocol's own signal, and the only one that speaks to the SESSION.
    for d in (done_posts or []):
        if d.get("created_at", "") <= st.get("created_at", ""):
            continue                     # a DONE cannot retire a LATER START
        d_ident = parse_identity(d.get("body") or "")
        if (mine_s & set(d_ident["sessions"])) or (mine_b & set(d_ident["branches"])):
            return "landed", "the session posted a DONE after this START"

    # (b) The weaker branch signal. Only `merged`/`closed` count; `open`,
    #     `unknown` and an absent entry all leave it ACTIVE.
    states = branch_states or {}
    for b in ident["branches"]:
        s = (states.get(b) or "unknown").lower()
        if s in ("merged", "closed"):
            return "landed", f"its declaring branch `{b}` is {s}"
    return "active", ""


def assess(changed_files, starts, *, my_branch: str, my_pr: int | None = None,
           my_body: str = "", done_posts=None, branch_states=None) -> dict:
    """`starts` is [{body, url, created_at}, ...].

    ⚠️ NO `branch` KEY. The collector used to supply one and it is now
    deliberately absent: the branch it supplied was grepped out of prose, which
    is the defect this module was repaired for. Identity comes from `body` via
    `parse_identity`, which reads only self-identifying positions. A stray
    `branch` key on an input row is IGNORED rather than honoured, so a caller
    that has not been updated cannot quietly reinstate the old behaviour.
    """
    if not changed_files:
        return {"state": "could_not_check",
                "reason": "no changed-file list — nothing was compared",
                "hits": [], "landed_hits": [], "unattributed_hits": [],
                "self_declared": 0, "parsed": 0, "explicitly_excluded": 0,
                "unparsed_hints": []}
    if not starts:
        # A board with no STARTs in the window is possible, but on a board that
        # is never silent it is far more likely a failed read. Refused, not
        # reported as clean — the merge-claim-audit denominator lesson.
        return {"state": "could_not_check",
                "reason": "no START comments found in the window on a board that "
                          "is never silent — treated as a failed read, not as "
                          "'nobody declared anything'",
                "hits": [], "landed_hits": [], "unattributed_hits": [],
                "self_declared": 0, "parsed": 0, "explicitly_excluded": 0,
                "unparsed_hints": []}

    my_sessions = pr_session_ids(my_body)
    hits, landed, unattributed = [], [], []
    self_declared, parsed_total, excluded_total, hints_total = 0, 0, 0, []

    for st in starts:
        who = attribution(st, my_branch=my_branch, my_pr=my_pr,
                          my_sessions=my_sessions)
        declared, excluded, hints = parse_declared_paths(st.get("body", ""))
        parsed_total += len(declared)
        excluded_total += len(excluded)
        hints_total.extend(hints)

        live, why = ("active", "")
        if who == "other":
            live, why = liveness(st, done_posts=done_posts,
                                 branch_states=branch_states)

        for f in changed_files:
            for d in sorted(declared):
                if not matches(f, d):
                    continue
                if who == "mine":
                    # A session never collides with itself. COUNTED, not hidden:
                    # a silent suppressor cannot be audited for over-suppression.
                    self_declared += 1
                    break
                ident = parse_identity(st.get("body") or "")
                row = {"file": f, "declared": d,
                       "branch": (ident["branches"] or [None])[0],
                       "session": (ident["sessions"] or [None])[0],
                       "url": st.get("url"), "at": st.get("created_at"),
                       "attribution": ("unattributable" if who == "unattributable"
                                       else f"other_{live}"),
                       "landed_because": why}
                if who == "unattributable":
                    unattributed.append(row)
                elif live == "landed":
                    landed.append(row)
                else:
                    hits.append(row)
                break

    return {
        # Still exactly three VERDICT states. Every non-`mine` hit counts toward
        # `overlap`; what changed is that render() no longer CLAIMS all of them
        # belong to a live sibling.
        "state": "overlap" if (hits or landed or unattributed) else "no_overlap",
        "reason": "",
        "hits": hits,
        "landed_hits": landed,
        "unattributed_hits": unattributed,
        # Shipped so suppression is visible. A jump here with no matching drop in
        # `hits` is how you would catch this fix over-reaching.
        "self_declared": self_declared,
        # Always shipped: a `no_overlap` over 0 parsed paths establishes nothing.
        "parsed": parsed_total,
        # Shipped so a reader can see negations were honoured rather
        # than assume it. The first version had no such concept.
        "explicitly_excluded": excluded_total,
        "unparsed_hints": sorted(set(hints_total)),
    }


def _who(h: dict) -> str:
    b, s = h.get("branch"), h.get("session")
    if b:
        return f"[`{b}`]({h['url']})"
    if s:
        return f"[`{s}`]({h['url']})"
    return f"[an unattributed START]({h['url']})"


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

    hits = v["hits"]
    landed = v.get("landed_hits") or []
    un = v.get("unattributed_hits") or []

    # THREE HEADLINES FOR THREE FACTS. The old single headline asserted "another
    # session" over every hit, including hits that were the reader's own and
    # hits nobody could attribute.
    if hits:
        lines = [f"### ⚠️ scope-overlap audit — PR #{pr} touches files a LIVE session declared",
                 "",
                 "**This is an observation, not a block, and nothing is gated.** Another "
                 "session posted a `START` naming paths this PR also changes, and nothing "
                 "says that session has finished. That is normal when the edits are "
                 "additive, and a real problem when they are not — you are the one who "
                 "can tell.", ""]
    elif un:
        lines = [f"### 🟡 scope-overlap audit — PR #{pr} touches files declared by a START "
                 f"we could not attribute",
                 "",
                 "**No LIVE session's declaration matched this PR.** What follows could "
                 "not be attributed to anyone, so it is reported rather than dropped.", ""]
    else:
        lines = [f"### ℹ️ scope-overlap audit — PR #{pr} touches files whose declaring "
                 f"branch has since landed",
                 "",
                 "**No live collision.** Every declaration matching this PR came from a "
                 "session whose declaring branch has already merged or closed. Worth "
                 "knowing — those files moved under you — but nobody is editing them "
                 "against you right now.", ""]

    for h in hits:
        lines.append(f"- `{h['file']}` — declared as `{h['declared']}` by "
                     f"{_who(h)} at {h['at']}")

    if landed:
        lines += ["", "**Declared by a session whose branch has since landed** — reported, "
                  "not headlined, because it is not a live collision. ⚠️ This says the "
                  "BRANCH landed, not that the session ended: a session merges many PRs "
                  "and keeps working. If one of these is yours and you are finished, post "
                  "a `✅ DONE` so it stops matching.", ""]
        for h in landed:
            lines.append(f"- `{h['file']}` — declared as `{h['declared']}` by "
                         f"{_who(h)} at {h['at']} — {h['landed_because']}")

    if un:
        lines += ["", "**Declared by a START we could not attribute** — its comment names "
                  "no branch and no session id in a self-identifying position, and does "
                  "not name this PR, so we cannot tell whether it is another session or "
                  "your own. Resolved toward reporting, not toward silence: suppressing a "
                  "real overlap is the worse error.", ""]
        for h in un:
            lines.append(f"- `{h['file']}` — declared as `{h['declared']}` by "
                         f"{_who(h)} at {h['at']}")

    lines += ["",
              f"_Compared {changed_n} changed file(s) against {v['parsed']} declared "
              f"path(s) — {len(hits)} from a live session, {len(landed)} from a landed "
              f"branch, {len(un)} unattributable, {v.get('self_declared', 0)} matched "
              f"your OWN declaration and were not reported._"]
    if v["unparsed_hints"]:
        lines += ["",
                  "⚠️ **This list is a LOWER BOUND.** These declarations were prose the "
                  "extractor could not resolve to concrete paths, so files they cover are "
                  "not checked: " + ", ".join(f"`{h}`" for h in v["unparsed_hints"])]
    return "\n".join(lines)


# ── REAL BOARD FIXTURES ────────────────────────────────────────────────────
#: issuecomment-5503070932, 2026-09-02T01:42:42Z — VERBATIM excerpt of the
#: manager's own, deliberately precise START. This is the definitive regression
#: case: its ONLY backticked `claude/...` token is ANOTHER session's branch,
#: quoted in prose complaining that that session's declaration keeps matching it.
_MANAGER_START = """## ▶️ START — manager session (overnight), narrow scope

**Session:** `session_011JWFxuYAaEQKCFCmG6gnHJ` — holds the manager lease (heartbeat 01:39:03Z).

**Files I write, and only these three:**

- `docs/claude/work/MANAGER-LEASE.json`
- `docs/claude/work/MANAGER-CHECKLIST.json`
- `docs/claude/work/SESSIONS.json`

I am **managing, not building**. I do not edit backlogs, `docs/claude/OPEN-ITEMS.json`, `config/`, `src/`, or any other path under `docs/claude/work/`.

### Why I'm posting this narrowly

The scope-overlap audit has now fired on **five consecutive manager PRs** (#10712, #10713, #10714, #10715, #10716), every time attributing my files to the broad `docs/claude/work/` declaration made by `claude/trading-system-workflow-design-1ln10f` at 2026-09-01T11:59:53Z. That declaration is ~14 hours old and covers a whole directory.
"""

#: PR #10729's own identity, as GitHub reports it: a short-lived branch that is
#: NOT the session's board branch, and the mandated attribution footer.
_PR10729_BRANCH = "claude/manager-state-0316"
_PR10729_BODY = """Manager-state only — `MANAGER-LEASE.json`, `MANAGER-CHECKLIST.json`, `SESSIONS.json`.

Wave 2 spawned 03:15Z: **ml** (`session_01Au13tQ9BaLKsEU7youUomr`) and
**performance** (`session_011Gqsv3NxLmm5yp9gfNh6ar`).

https://claude.ai/code/session_011JWFxuYAaEQKCFCmG6gnHJ

---
_Generated by [Claude Code](https://claude.ai/code/session_011JWFxuYAaEQKCFCmG6gnHJ)_"""
_PR10729_FILES = ["docs/claude/work/MANAGER-CHECKLIST.json",
                  "docs/claude/work/MANAGER-LEASE.json",
                  "docs/claude/work/SESSIONS.json"]

#: issuecomment-5502915452, 2026-09-02T01:22:16Z — VERBATIM identity block of a
#: sub-session's START. It names TWO session ids on one line, the second being
#: its MANAGER's. Taking both would let the manager's PRs suppress this genuine
#: declaration.
_DRAIN3_START = """▶️ **START** — Backlog drain #3

**Scope (exclusive): `docs/claude/performance-review-backlog.json`.** `docs/claude/OPEN-ITEMS.json` is READ-ONLY for this session.

- Session: `session_01JXBmVC65hkkoSQ2LcV1ETY` (child of manager `session_011JWFxuYAaEQKCFCmG6gnHJ`)
- Branch: `claude/drain-perf-backlog-20260902`
"""

#: issuecomment-5503056365, 2026-09-02T01:40:46Z — VERBATIM first line. Its
#: branch carries no `claude/` prefix, so the old extractor could not see it at
#: all and filed the comment `unattributable`, which the live #10731 audit shows.
_PING_START = ("▶️ **START** · ping delivery ledger read surface · session "
               "`session_01TASYv35o6XciFnMC9wmnHi` · branch "
               "`diag-pending-pings-delivered-read-surface`\n\n"
               "**Scope — one file:** `src/web/api/routers/diag.py`.\n")

#: RECONSTRUCTED, not verbatim: the 2026-09-01T11:59:53Z START from
#: `claude/trading-system-workflow-design-1ln10f`. Its declared paths and branch
#: are taken from the live audit comments on PRs #10729/#10731; the prose around
#: them is not the original. Its PR (#10649) merged at 11:58:24Z — 90 seconds
#: BEFORE this START was posted — and it went on matching for 15 more hours.
_STALE_START = ("▶️ START · branch `claude/trading-system-workflow-design-1ln10f`\n\n"
                "Touching: `docs/claude/work/`, `CLAUDE.md`, `docs/claude/OPEN-ITEMS.json`.\n")


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
                   [{"body": "▶️ START · branch `claude/other`\n" + real,
                     "url": "u", "created_at": "t"}],
                   my_branch="claude/mine")
    ok(v_neg["state"] == "no_overlap" and v_neg["explicitly_excluded"] >= 1,
       "end to end: the excluded file reports no_overlap AND surfaces the exclusion count")

    # ══ IDENTITY: a MENTION IS NOT A CLAIM, applied to the declarer itself ══
    ident = parse_identity(_MANAGER_START)
    ok(ident["sessions"] == ["session_011JWFxuYAaEQKCFCmG6gnHJ"],
       "the manager's START yields its OWN session id from its `Session:` line")
    ok(ident["branches"] == [],
       "and yields NO branch — its only backticked `claude/...` token sits in prose "
       "ABOUT another session. This is the whole defect: the old extractor took "
       "that token and reported the manager's own START as that branch's")
    ok("claude/trading-system-workflow-design-1ln10f" in _MANAGER_START,
       "positive control: the mis-attributed branch really IS present in the body, "
       "so the empty result above is discrimination and not a failed parse")

    # ── THE DEFINITIVE REGRESSION CASE (live PR #10729, 2026-09-02T03:18:04Z) ──
    ok(attribution({"body": _MANAGER_START}, my_branch=_PR10729_BRANCH,
                   my_sessions=pr_session_ids(_PR10729_BODY)) == "mine",
       "a session recognises its OWN START across a DIFFERENT branch — the manager "
       "declares on `session_011JW...` and opens PRs from `claude/manager-state-*`, "
       "so branch equality could never have matched however precisely it declared")
    v_mgr = assess(_PR10729_FILES,
                   [{"body": _STALE_START, "url": "u1", "created_at": "2026-09-01T11:59:53Z"},
                    {"body": _MANAGER_START, "url": "u2", "created_at": "2026-09-02T01:42:42Z"}],
                   my_branch=_PR10729_BRANCH, my_pr=10729, my_body=_PR10729_BODY,
                   branch_states={"claude/trading-system-workflow-design-1ln10f": "merged"})
    ok(not v_mgr["hits"],
       "live #10729 does not reproduce: ZERO hits headlined as a live session, where "
       "the deployed audit headlined SIX")
    ok(v_mgr["self_declared"] == 3,
       "all three manager files are attributed to the manager ITSELF, and COUNTED — "
       "a suppressor that hides its own suppressions cannot be audited")
    ok(len(v_mgr["landed_hits"]) == 3 and not v_mgr["unattributed_hits"],
       "the OTHER session's declaration still matches — it is not silenced — but is "
       "graded `landed`, its branch having merged 90s before it even posted")
    md_mgr = render(v_mgr, pr=10729, changed_n=3)
    ok("LIVE session declared" not in md_mgr and "since landed" in md_mgr,
       "and the headline no longer asserts a live collision")
    ok("your OWN declaration" in md_mgr, "the footer states how many were self-matches")

    # ── CONTROL, THE OTHER DIRECTION: a GENUINE cross-session overlap still fires ──
    v_real = assess(["docs/claude/performance-review-backlog.json"],
                    [{"body": _DRAIN3_START, "url": "u", "created_at": "2026-09-02T01:22:16Z"}],
                    my_branch=_PR10729_BRANCH, my_pr=10729, my_body=_PR10729_BODY)
    ok(v_real["state"] == "overlap" and len(v_real["hits"]) == 1
       and v_real["self_declared"] == 0,
       "a REAL cross-session overlap is still headlined: the manager's PR against a "
       "sub-session's exclusive backlog claim")
    ok("LIVE session declared" in render(v_real, pr=10729, changed_n=1),
       "and it renders under the live-collision headline")
    ok(parse_identity(_DRAIN3_START)["sessions"] == ["session_01JXBmVC65hkkoSQ2LcV1ETY"],
       "the sub-session's identity is its OWN, not the manager's — that line reads "
       "'(child of manager `session_011JW...`)', and taking the second id would let "
       "the manager suppress this very declaration")

    # ── the branch key still works, and still cannot be MENTIONED into existence ──
    ok(attribution({"body": "▶️ START · branch `claude/mine`\nTouching: `a.py`"},
                   my_branch="claude/mine") == "mine",
       "the original branch-equality path still excludes")
    ok(attribution({"body": "▶️ START · branch `claude/other`\nSee PR #10590."},
                   my_branch="claude/mine", my_pr=10590) == "other",
       "a self-declared branch still wins: a START that merely MENTIONS this PR can "
       "never suppress a real overlap")
    ok(parse_identity(_PING_START)["branches"] == ["diag-pending-pings-delivered-read-surface"],
       "a branch with no `claude/` prefix is now read — the old regex required that "
       "prefix, so this real START was filed `unattributable` on live PR #10731")

    # ── a PR body NAMES other sessions; only the footer URL is its own identity ──
    ok(pr_session_ids(_PR10729_BODY) == ["session_011JWFxuYAaEQKCFCmG6gnHJ"],
       "the PR's identity comes from its attribution-footer URL only — its body also "
       "names two sub-sessions it spawned, and harvesting those would let a sibling's "
       "START read as our own and SUPPRESS a real overlap")
    ok("session_01Au13tQ9BaLKsEU7youUomr" in _PR10729_BODY,
       "positive control: those sibling ids really are in the body")
    spawned = ("▶️ START\n- Session: `session_01Au13tQ9BaLKsEU7youUomr`\n"
               "Touching: `docs/claude/ml-review-backlog.json`\n")
    v_spawn = assess(["docs/claude/ml-review-backlog.json"],
                     [{"body": spawned, "url": "u", "created_at": "t"}],
                     my_branch=_PR10729_BRANCH, my_pr=10729, my_body=_PR10729_BODY)
    ok(len(v_spawn["hits"]) == 1 and v_spawn["self_declared"] == 0,
       "end to end: a session merely NAMED in our PR body is still a foreign declarer")

    # ── the four attribution states are four, and none collapses into another ──
    st_live = {"body": _DRAIN3_START, "url": "u", "created_at": "2026-09-02T01:22:16Z"}
    ok(liveness(st_live, branch_states={}) == ("active", ""),
       "an UNKNOWN branch state is `active`, never `landed` — staleness fails toward "
       "reporting, because declaring a live session finished is the suppression this "
       "module must never do")
    ok(liveness(st_live, branch_states={"claude/drain-perf-backlog-20260902": "open"})[0]
       == "active", "an OPEN branch is active")
    lv, why = liveness(st_live, branch_states={"claude/drain-perf-backlog-20260902": "merged"})
    ok(lv == "landed" and "merged" in why,
       "a MERGED declaring branch is `landed`, and the EVIDENCE is named rather than "
       "implied — it says the branch merged, not that the session ended")
    done = [{"body": "✅ DONE · branch `claude/drain-perf-backlog-20260902`",
             "created_at": "2026-09-02T02:30:00Z"}]
    lv2, why2 = liveness(st_live, done_posts=done)
    ok(lv2 == "landed" and "DONE" in why2,
       "a DONE posted AFTER the START retires it — the protocol's own signal")
    ok(liveness(st_live, done_posts=[{**done[0], "created_at": "2026-09-02T00:00:00Z"}])[0]
       == "active",
       "a DONE posted BEFORE the START does not retire it — that DONE closed an "
       "earlier claim, and reading it as this one's would silence a live declaration")
    ok(is_done("✅ DONE · branch `claude/x`"), "a real DONE header is recognised")
    ok(not is_done("▶️ START · branch `claude/x`\n\nThe merge train is DONE."),
       "a START is never a DONE, however its prose reads")
    ok(not is_done("Status update · branch `claude/drain-perf-backlog-20260902`\n\n"
                   "Wave 1 is DONE and wave 2 is spawned."),
       "'DONE' in an opening paragraph is NOT a DONE header — an unanchored match "
       "would RETIRE a live START, which is the one error this module must not make")
    prose_done = [{"body": "Status update · branch `claude/drain-perf-backlog-20260902`\n\n"
                           "Wave 1 is DONE.", "created_at": "2026-09-02T02:30:00Z"}]
    ok(liveness(st_live, done_posts=[d for d in prose_done if is_done(d["body"])])[0]
       == "active",
       "end to end: the collector's DONE filter keeps that prose out, so the live "
       "declaration stays ACTIVE")
    ok(set(ATTRIBUTIONS) == {"mine", "other_active", "other_landed", "unattributable"},
       "the four attribution states are exactly these")

    # ── unattributable is PRESERVED, and still reported ──
    anon = "▶️ START — backlog-drain session #2\n\nScope: `docs/claude/health-review-backlog.json`.\n"
    ok(attribution({"body": anon}, my_branch="claude/x", my_pr=1) == "unattributable",
       "a START that identifies itself nowhere stays unattributable — NOT resolved "
       "toward `mine`, which would suppress a possibly-real overlap")
    v_un = assess(["docs/claude/health-review-backlog.json"],
                  [{"body": anon, "url": "u", "created_at": "t"}],
                  my_branch="claude/x", my_pr=1)
    ok(v_un["state"] == "overlap" and len(v_un["unattributed_hits"]) == 1
       and not v_un["hits"],
       "an unattributable hit is REPORTED but kept out of `hits`, which asserts a "
       "live sibling")
    md_un = render(v_un, pr=1, changed_n=1)
    ok("could not attribute" in md_un and "LIVE session declared" not in md_un,
       "and the rendered comment says so rather than asserting another session")

    ok(matches("tests/test_x.py", "tests/"), "a trailing-slash prefix matches beneath it")
    ok(matches("scripts/ci/a.py", "scripts/ci"), "a bare directory matches as a prefix")
    ok(not matches("tests_other/x.py", "tests/"), "the prefix does not leak across a sibling dir")
    ok(matches("a/b.py", "a/b.py"), "an exact path matches")
    ok(not matches("a/bc.py", "a/b.py"), "a longer filename is not a match")

    starts = [{"body": "▶️ START · branch `claude/other`\n" + body,
               "url": "u", "created_at": "t"}]
    v = assess(["docs/claude/OPEN-ITEMS.json"], starts, my_branch="claude/mine")
    ok(v["state"] == "overlap" and v["hits"][0]["declared"] == "docs/claude/OPEN-ITEMS.json",
       "the real 2026-08-31 collision is detected")
    ok(v["unparsed_hints"] == [], "a resolvable body reports no unresolved hints")

    v = assess(["src/unrelated.py"], starts, my_branch="claude/mine")
    ok(v["state"] == "no_overlap" and v["parsed"] > 0,
       "a clean PR reports no_overlap WITH its denominator")

    v = assess(["docs/claude/OPEN-ITEMS.json"], starts, my_branch="claude/other")
    ok(v["state"] == "no_overlap" and v["self_declared"] == 1,
       "a session never collides with its OWN declaration")

    v = assess([], starts, my_branch="claude/mine")
    ok(v["state"] == "could_not_check", "no changed-file list is could_not_check, NOT no_overlap")
    v = assess(["a.py"], [], my_branch="claude/mine")
    ok(v["state"] == "could_not_check" and "never silent" in v["reason"],
       "an empty board is a failed read, not 'nobody declared anything'")

    vague = [{"url": "u", "created_at": "t",
              "body": "▶️ START · branch `claude/other`\n**Touching:** "
                      "`docs/claude/OPEN-ITEMS.json` and `several other files`."}]
    v = assess(["docs/claude/OPEN-ITEMS.json"], vague, my_branch="claude/mine")
    ok(v["unparsed_hints"] and "LOWER BOUND" in render(v, pr=1, changed_n=1),
       "unresolvable prose makes the report declare itself a lower bound")
    v_land = assess(["docs/claude/OPEN-ITEMS.json"], vague, my_branch="claude/mine",
                    branch_states={"claude/other": "merged"})
    ok("LOWER BOUND" in render(v_land, pr=1, changed_n=1),
       "the LOWER BOUND caveat survives on the landed-only render too — prose the "
       "extractor could not resolve is unchecked whoever declared it")

    ok("not a clean result" in render(
        {"state": "could_not_check", "reason": "r", "hits": [], "parsed": 0,
         "explicitly_excluded": 0, "unparsed_hints": []}, pr=1, changed_n=0),
       "the could-not-check render never reads as a clean pass")

    ok(set(STATES) == {"overlap", "no_overlap", "could_not_check"},
       "the three verdict states are exactly these")

    print(f"scope-overlap: self-test OK — {fired} planted controls all fire")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--input", help="JSON: {changed_files, starts, my_branch, pr, my_body, done_posts, branch_states}")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    if not args.input:
        ap.error("pass --self-test or --input")
    data = json.loads(open(args.input, encoding="utf-8").read())
    v = assess(data.get("changed_files") or [], data.get("starts") or [],
               my_branch=data.get("my_branch") or "",
               my_pr=data.get("pr"),
               my_body=data.get("my_body") or "",
               done_posts=data.get("done_posts") or [],
               branch_states=data.get("branch_states") or {})
    print(json.dumps({**v, "markdown": render(v, pr=data.get("pr", 0),
                                              changed_n=len(data.get("changed_files") or []))},
                     indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
