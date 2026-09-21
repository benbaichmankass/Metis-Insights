#!/usr/bin/env python3
"""canonical-doc-coherence — mechanical guard against governance-doc drift.

This is the "teeth" behind the doc-freshness skill. It catches the exact
classes of drift that accumulated silently and produced the recurring
operator pain (stale VM topology, removed gates described as live, the
7-stage ML ladder, and the two hierarchy lists falling out of sync).

It is intentionally simple and stdlib-only so it can run in CI and locally
over the working tree. Each check prints PASS/FAIL lines; the process exits
non-zero if any check fails.

Run:  python scripts/ci/check_canonical_doc_coherence.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Files a live session follows day-to-day. Drift here is what misleads Claude.
ACTIVE_DOCS = [
    "CLAUDE.md",
    "docs/CLAUDE-RULES-CANONICAL.md",
    "docs/ARCHITECTURE-CANONICAL.md",
    # Split out of CLAUDE.md on 2026-09-02 (the API payload contract). It carries
    # the `POST /api/bot/prop/report` fail-CLOSED value contract below, so dropping
    # it here would have silently retired a check by MOVING the text it reads —
    # exactly the "the guard still passes because it stopped looking" failure this
    # file's own header warns about.
    "docs/reference/bot-api-reference.md",
    "docs/github-actions-workflows.md",
    "docs/claude/system-actions.md",
    "docs/claude/vm-operator-mode.md",
    "docs/claude/trainer-vm-mode.md",
    "docs/claude/diag-relay.md",
    "docs/claude/deployment-ops.md",
]


def _active_files() -> list[Path]:
    files = [ROOT / p for p in ACTIVE_DOCS]
    files += sorted((ROOT / ".claude" / "skills").rglob("SKILL.md"))
    files += sorted((ROOT / ".claude" / "commands").glob("*.md"))
    return [f for f in files if f.exists()]


# ---------------------------------------------------------------------------
# Is the FILE itself intact? (2026-09-13)
# ---------------------------------------------------------------------------
# Three states, never collapsed. This repo has already ruled on this class
# TWICE, and both rulings stopped short of the canonical prose docs:
#   * `check_register_reserialization.py` grades a JSON register carrying
#     conflict markers UNREADABLE — *we could not look* — rather than clean.
#     It walks `docs/claude/**/*.json`.
#   * `check_document_index.py` R7 (#12156) refuses `docs/DOCUMENT-INDEX.md`
#     for the same condition.
# Neither reaches CLAUDE.md.
CONFLICT_CLEAN = "clean"
CONFLICT_FOUND = "conflicted"
CONFLICT_UNREADABLE = "unreadable"       # we could not look — NOT a pass

#: git writes exactly seven characters at the START of a line, followed by a
#: space and a label or nothing. Anchoring on that shape is what makes this
#: safe to run over prose that TALKS about merge conflicts: a mention inside a
#: sentence, a table cell or a backtick span is indented or preceded by other
#: text and can never match. MEASURED 2026-09-13 — a naive substring test would
#: misfire today: `docs/claude/health-review-backlog.json` mentions the string
#: on 4 lines, all of them row prose, while NO file in this corpus carries a
#: line-anchored marker.
_CONFLICT_OPEN = re.compile(r"^<<<<<<<(?: .*)?$")
_CONFLICT_CLOSE = re.compile(r"^>>>>>>>(?: .*)?$")
#: Corroboration only, NEVER a trigger. A line of bare `=` is also a valid
#: setext heading underline in markdown, so firing on it alone would invent
#: findings in a document nobody ever conflicted.
_CONFLICT_MID = re.compile(r"^=======$")


#: Paths that LEGITIMATELY carry a line-start conflict marker — e.g. a doc that
#: SHOWS a conflict block while explaining how to resolve one. Maps a
#: repo-relative path to the EXACT line numbers allowed to carry one.
#:
#: ⚠️ VERIFIED, NOT PRESENCE-ONLY, and both directions are enforced. Declaring a
#: file does not silence it: a marker on any line NOT in the declared set still
#: fails, so a real conflict landing elsewhere in an annotated file is still
#: caught. And an entry whose file carries NO marker at the declared line is
#: ITSELF a finding, so a stale entry cannot sit here quietly widening what is
#: excused. A marker cheaper to lie to than to satisfy is worse than none —
#: the lesson `new-table-wiring-guard` paid for.
#:
#: ⚠️ EMPTY BY DESIGN, AND MEASURED SO. Over all 1,052 tracked `docs/**/*.md`
#: on 2026-09-17 the scan flags ZERO files; 10 files mention a marker in prose
#: and NONE carries one at line start in tracked content (the single repo-wide
#: `^<<<<<<<` hit is a git-ignored `.pyc`). This exists so the first doc that
#: legitimately needs one has a declared route instead of a reason to weaken
#: the check.
CONFLICT_MARKER_EXPECTED: dict[str, set[int]] = {}


def _conflict_corpus() -> list[Path]:
    """`_active_files()` plus `ROADMAP.md` plus every tracked `docs/**/*.md`.

    ⚠️ **THE SCOPE IS A DECISION, recorded 2026-09-17** —
    `BL-20260913-THE-CONFLICT-MARKER-REFUSAL-WAS-RULED-FOR-TWO-REGISTER-FAMILIES-AND-NEVER-EXTENDED-TO-THE-CANONICAL-PROSE-A-SESSION-READS`,
    whose criterion is that somebody DECIDE the repo-wide scope rather than let
    it stay unstated. The decided set is *every prose document a session may
    read*: `ACTIVE_DOCS`, `ROADMAP.md`, every `SKILL.md` and command, and all of
    `docs/**/*.md`.

    **The gap that decided it was the instruction hierarchy's own level 4.**
    Levels 1-3, 5 and 6 were covered and `docs/sprint-logs/` — 309 files — was
    not, so "the current sprint log" could carry both sides of a contested edit
    and pass every guard in the repo.

    MEASURED before extending, because the FALSE POSITIVE is the hard half here
    and the row says so: over all 1,052 tracked `docs/**/*.md` the scan flags
    **0** files, and the whole sweep costs **0.14 s** for 14.7 MB. The predicate
    is anchored to git's exact seven-character line-start shape, so a doc that
    MENTIONS a marker inline or shows one indented stays clean — asserted, not
    assumed. Where a file genuinely needs a marker at line start,
    :data:`CONFLICT_MARKER_EXPECTED` is the declared route.

    ⚠️ The JSON registers and `docs/DOCUMENT-INDEX.md` keep their OWN rulings
    (`check_register_reserialization.py`, `check_document_index.py` R7). This
    does not replace them, and the overlap on `DOCUMENT-INDEX.md` is harmless.

    ⚠️ THIS CHECK DELIBERATELY READS ONE MORE FILE THAN THE OTHERS, and the
    asymmetry is the point rather than an oversight. `ROADMAP.md` is third in
    the instruction hierarchy and is edited by many sessions, so it carries the
    same merge-resolution exposure as the rest — but putting it in
    `ACTIVE_DOCS` would change what FIVE content checks read, which is a
    separate decision with its own blast radius. MEASURED 2026-09-13: adding it
    there is clean today (0 issues across all checks), so that decision is
    available and cheap; it is simply not this one.

    The direction matters. This file's own header warns that DROPPING a file
    from the scanned set silently retires a check by moving the text it reads.
    Adding one file to one check is the opposite of that.
    """
    files = _active_files()
    roadmap = ROOT / "ROADMAP.md"
    if roadmap.exists():
        files = files + [roadmap]
    files = files + sorted((ROOT / "docs").rglob("*.md"))
    # Dedupe while keeping order stable, so a file in ACTIVE_DOCS *and* under
    # docs/ is read once and cannot be reported twice.
    seen: set[Path] = set()
    out: list[Path] = []
    for f in files:
        r = f.resolve()
        if r in seen or not f.is_file():
            continue
        seen.add(r)
        out.append(f)
    return out


def file_integrity(text: str | None) -> tuple[str, list[tuple[int, str]]]:
    """`(state, [(line_no, line), ...])` — is this file free of conflict markers?

    Pure, so what the guard CLAIMS is arguable in a test rather than only
    against a real merge.
    """
    if text is None:
        return CONFLICT_UNREADABLE, []
    hits: list[tuple[int, str]] = []
    triggered = False
    for i, line in enumerate(text.splitlines(), start=1):
        if _CONFLICT_OPEN.match(line) or _CONFLICT_CLOSE.match(line):
            triggered = True
            hits.append((i, line))
        elif _CONFLICT_MID.match(line) and triggered:
            hits.append((i, line))
    return (CONFLICT_FOUND if triggered else CONFLICT_CLEAN), hits


def check_conflict_markers() -> list[str]:
    """No governance doc may carry an UNRESOLVED MERGE CONFLICT.

    ⚠️ WHY THIS IS A RULE ABOUT THE FILE AND NOT ABOUT ITS CONTENT. Every other
    check here is a line-level scan for a stale CLAIM; none of them asks whether
    the document is well-formed. MEASURED 2026-09-13 by planting a committed
    conflict block in `CLAUDE.md` and running the FULL guard registry against
    it: 68 guards passed and the single failure was `pr-landing-guard`, for the
    unrelated reason that the probe branch carried no landing record. Nothing in
    the repo graded the corruption. `docs/DOCUMENT-INDEX.md`, planted the same
    way, was caught — by the R7 rule added the same day for that one file.

    ⚠️ IT DOES NOT SHORT-CIRCUIT THE OTHER CHECKS, deliberately. A conflict
    block CAN distort them — `check_hierarchy_mirror` would read both copies of
    a contested list — but this returning a finding already makes the run exit
    non-zero, so the conflicted file cannot ride a green out. Suppressing the
    remaining output would hide findings that are still true.

    ⚠️ AN UNREADABLE FILE IS A FINDING, never a pass: *we could not look* and
    *we looked and it was fine* are different facts.
    """
    fails: list[str] = []
    for f in _conflict_corpus():
        rel = f.relative_to(ROOT)
        try:
            text: str | None = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            text = None
            reason = f"{type(exc).__name__}: {exc}"
        state, hits = file_integrity(text)
        if state == CONFLICT_UNREADABLE:
            fails.append(
                f"{rel}: could not be READ ({reason}). That is 'we could not "
                "look', never 'the document is intact'."
            )
        elif state == CONFLICT_FOUND:
            allowed = CONFLICT_MARKER_EXPECTED.get(str(rel))
            if allowed is not None:
                # DECLARED lines are excused; anything else in the same file is
                # NOT. A real conflict landing in an annotated doc still fails.
                hits = [(n, ln) for n, ln in hits if n not in allowed]
                if not hits:
                    continue
            where = ", ".join(f"line {n}: {ln}" for n, ln in hits[:6])
            more = f" (+{len(hits) - 6} more)" if len(hits) > 6 else ""
            extra = (
                " Lines declared in CONFLICT_MARKER_EXPECTED are excused; these "
                "are not." if allowed is not None else
                " If this document legitimately SHOWS a conflict block, declare "
                "its exact line numbers in CONFLICT_MARKER_EXPECTED rather than "
                "narrowing the scan."
            )
            fails.append(
                f"{rel}: UNRESOLVED MERGE CONFLICT — {where}{more}. "
                "Resolve it; a governance doc carrying both sides of a "
                f"contested edit is not a document anyone can follow.{extra}"
            )

    # THE OTHER DIRECTION: a declared entry that excuses nothing is itself a
    # finding. A stale entry left behind after a doc is rewritten would sit here
    # silently widening what is excused, which is how a verified override decays
    # into a presence-only one.
    for decl_path, decl_lines in sorted(CONFLICT_MARKER_EXPECTED.items()):
        f = ROOT / decl_path
        if not f.is_file():
            fails.append(
                f"{decl_path}: declared in CONFLICT_MARKER_EXPECTED but the file "
                "does not exist. Remove the entry — a declaration that names "
                "nothing excuses nothing and hides what it might excuse later."
            )
            continue
        try:
            state, hits = file_integrity(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue  # the read failure is already reported above
        present = {n for n, _ in hits}
        stale = sorted(set(decl_lines) - present)
        if stale:
            fails.append(
                f"{decl_path}: CONFLICT_MARKER_EXPECTED declares line(s) "
                f"{stale} that carry NO marker. The entry is stale — correct or "
                "remove it, so the override keeps saying something true."
            )
    return fails


def _iter_windows(files: list[Path], radius: int = 2):
    """Yield (rel, lineno, line, context) where context is the line plus
    `radius` neighbours on each side joined — so a historical/removal marker
    on an adjacent wrapped line still suppresses a false positive."""
    for f in files:
        rel = f.relative_to(ROOT)
        lines = f.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines, 1):
            lo = max(0, i - 1 - radius)
            hi = min(len(lines), i + radius)
            context = " ".join(lines[lo:hi])
            yield rel, i, line, context


def check_dead_vm_ip() -> list[str]:
    """The terminated x86 micro must never appear as the *current* live VM.

    Allowed only on lines explicitly framing it as past/historical.
    """
    DEAD_IP = "158.178.210.252"
    OLD_IP = "129.159.83.68"  # an even-older pre-micro live IP
    HIST = re.compile(
        r"terminat|retir|histor|pre-2026-06-14|migration source|decommiss|"
        r"supersed|old x86|former|was the|no longer|micro\b",
        re.I,
    )
    fails = []
    for rel, i, line, context in _iter_windows(_active_files()):
        if DEAD_IP in line or OLD_IP in line:
            if not HIST.search(context):
                fails.append(f"{rel}:{i}: dead VM IP without historical marker -> {line.strip()}")
    return fails


def check_removed_gates() -> list[str]:
    """Removed feature gates must only appear flagged as removed/historical."""
    GATES = re.compile(
        r"MULTI_SYMBOL_ENABLED|NEWS_ENABLED|NAKED_POSITION_AUTOPROTECT|"
        r"MONITOR_RECONCILE_ENABLED|POSITION_NETTING_GUARD_ENABLED|"
        r"POSITION_NETTING_GUARD_ACCOUNTS",
    )
    OK = re.compile(
        r"remov|retir|supersed|histor|ignored|baseline|no longer|legacy|"
        r"deprecat|example|stranded|unconditional|purge",
        re.I,
    )
    fails = []
    for rel, i, line, context in _iter_windows(_active_files()):
        if GATES.search(line) and not OK.search(context):
            fails.append(f"{rel}:{i}: removed gate described as live -> {line.strip()}")
    return fails


def check_seven_stage_ladder() -> list[str]:
    """No 7-stage ML ladder in the skill/command catalog."""
    SEVEN = re.compile(r"7[- ]stage|seven[- ]stage", re.I)
    # Allowed when the mention is a legacy-alias note or meta text (e.g. this
    # guard's own description, or "the legacy 7-stage names alias to ...").
    OK = re.compile(
        r"legacy|alias|collaps|former|\bold\b|should be empty|stale 7-stage|"
        r"detect|guard|aliases to",
        re.I,
    )
    fails = []
    cat = sorted((ROOT / ".claude").rglob("*.md"))
    for f in cat:
        rel = f.relative_to(ROOT)
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if SEVEN.search(line) and not OK.search(line):
                fails.append(f"{rel}:{i}: stale 7-stage ladder -> {line.strip()}")
    return fails


_HIER_KEYS = [
    ("rules", re.compile(r"CLAUDE-RULES-CANONICAL", re.I)),
    ("architecture", re.compile(r"ARCHITECTURE-CANONICAL", re.I)),
    ("plan", re.compile(r"OPERATING-PLAN", re.I)),
    ("checklist", re.compile(r"MANAGER-CHECKLIST", re.I)),
    ("roadmap", re.compile(r"ROADMAP", re.I)),
    ("sprintlog", re.compile(r"sprint log|sprint-logs", re.I)),
    ("skills", re.compile(r"\.claude/skills|^.*\bSkills\b", re.I)),
    ("claudemd", re.compile(r"this file|root .?CLAUDE\.md|\bCLAUDE\.md\b", re.I)),
    ("implspecs", re.compile(r"implementation spec", re.I)),
    ("historical", re.compile(r"docs/claude|historical", re.I)),
]


def _normalize_item(text: str) -> str | None:
    for key, pat in _HIER_KEYS:
        if pat.search(text):
            return key
    return None


def _extract_hierarchy(path: Path, heading_substr: str) -> list[str] | None:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#") and heading_substr.lower() in line.lower():
            start = i
            break
    if start is None:
        return None
    seq: list[str] = []
    for line in lines[start + 1:]:
        m = re.match(r"\s*\d+\.\s+(.*)", line)
        if m:
            key = _normalize_item(m.group(1))
            if key:
                seq.append(key)
            continue
        # A numbered list item may wrap onto indented continuation lines —
        # those start with whitespace and must NOT end the list. The list
        # ends at the first non-indented, non-numbered prose line (or heading)
        # once we have started collecting items.
        if seq and line and not line[0].isspace():
            break
    return seq


def check_hierarchy_mirror() -> list[str]:
    """CLAUDE.md Instruction hierarchy must mirror canonical Document Priority."""
    claude = _extract_hierarchy(ROOT / "CLAUDE.md", "Instruction hierarchy")
    canon = _extract_hierarchy(ROOT / "docs/CLAUDE-RULES-CANONICAL.md", "Document Priority")
    fails = []
    if not claude:
        fails.append("CLAUDE.md: could not parse § Instruction hierarchy")
    if not canon:
        fails.append("docs/CLAUDE-RULES-CANONICAL.md: could not parse § Document Priority")
    if claude and canon and claude != canon:
        fails.append(
            "hierarchy mismatch:\n"
            f"    CLAUDE.md           -> {claude}\n"
            f"    CLAUDE-RULES-CANON  -> {canon}"
        )
    return fails


# --------------------------------------------------------------------------- #
# declared values — does the prose match the file that actually sets it?
# --------------------------------------------------------------------------- #
#
# WHY (2026-08-10). The four checks above passed 4/4 while FIVE canonical docs
# described branch protection incorrectly, because none of them compares a
# claim about a live setting against the file that sets it. The same session
# also found `CLAUDE.md` describing `POST /api/bot/prop/report` as PERMISSIVELY
# token-gated ("when set") when `_require_write_token` is fail-CLOSED (503 when
# the token is unset). That second one is the shape that reaches the trader: a
# session reasoning from it would conclude an unauthenticated write path exists
# where it does not, or vice versa.
#
# Same idiom as `check_removed_gates`: a phrase asserting the wrong value is a
# finding unless the surrounding context marks it historical/corrected.
#
# TWO RULES FOR ADDING A CONTRACT — both are the point, not ceremony:
#
#  1. THE SOURCE MUST BE IN-REPO. A value that lives only on the VM (e.g. the
#     live `BYBIT_TPSL_MODE`) is deliberately EXCLUDED: this guard would be
#     asserting a value it cannot read, which is precisely the defect it
#     exists to catch. Verify those by diag, not here.
#  2. AN UNREADABLE SOURCE IS A FAILURE, NOT A PASS. If the extractor stops
#     matching (someone renames `STRICT=`), the check reports that loudly. A
#     silently-disabled check is the "green that checked nothing" this repo
#     already treats as worse than a red.
#
# WHAT THIS DOES **NOT** PROVE — stated so the PASS line is not read as more
# than it is (the same defect one level up):
#
#  * It matches KNOWN STALE PHRASINGS, not meaning. A doc can assert the wrong
#    value in words no pattern here anticipates and this check will pass. It
#    is a ratchet against recurrence of drift that actually happened, not a
#    general prover.
#  * Coverage is deliberately ASYMMETRIC. Each contract lists patterns only
#    for the value(s) the source does NOT currently hold; the pattern list for
#    the current value is empty. Flipping a source value therefore does not
#    immediately start flagging the now-stale prose — the phrases that would
#    catch it ("unticked", "off since") are the same ones `_HISTORICAL` uses to
#    suppress corrected text, and conflating those would make the guard fire on
#    its own retrospective notes. **When you flip a value, sweep its docs by
#    hand and move the patterns across.**
#  * It reads REPO state. Anything whose truth lives on the VM is out of scope
#    by rule 1 above.

def _historical_near(context: str, line: str, m: "re.Match", radius: int = 300) -> bool:
    """Is there a historical/corrected marker NEAR this match?

    Suppression has to be local. Testing `_HISTORICAL` against the whole joined
    context works for prose, where a line is a sentence or two, and **fails
    completely on a long single line** — `.claude/settings.json` is minified
    JSON whose merge-guard hook is one ~2 KB line, so the window is the entire
    hook and is guaranteed to contain some word like "was" or "correct". Result:
    the stale `sync IMMEDIATELY before merging` in the deny message matched a
    pattern, sat in a scanned file, and was silently suppressed anyway.

    Measured, not assumed: with whole-context suppression the planted stale line
    in `settings.json` produced PASS; with this window it produces the finding.

    Falling back to the whole context when the line cannot be located keeps this
    strictly no-stricter than before for every file that already passed.
    """
    at = context.find(line)
    if at < 0:
        return bool(_HISTORICAL.search(context))
    pos = at + m.start()
    return bool(_HISTORICAL.search(context[max(0, pos - radius): pos + radius]))


_HISTORICAL = re.compile(
    r"remov|retir|supersed|histor|no longer|was |used to|until |previously|"
    r"correct|~~|deprecat|before 20|off since|unticked|past tense|RESOLVED",
    re.I,
)

# (id, source_file, extractor, {value: [patterns asserting THAT value]})
VALUE_CONTRACTS = [
    {
        "id": "branch-protection require-up-to-date",
        "source": ".github/workflows/branch-protection-sync.yml",
        "extract": re.compile(r"^\s*STRICT=(true|false)\s*$", re.M),
        "asserts": {
            "true": [
                re.compile(r"safety net is .{0,40}branch.protection \(require-up-to-date\)", re.I),
                re.compile(r'"?Require branches to be up to date[^\n]{0,60}\b(is ON|ticked|enabled)', re.I),
                re.compile(r"sync to `?main`? LAST, right before merging", re.I),
                # Added after the phrasing below survived the 2026-08-10 sweep in
                # BOTH `coordination-board.md` and the merge-guard's own deny
                # message in `.claude/settings.json` — the single highest-leverage
                # instruction surface here, since a session reads it at the exact
                # moment it is about to merge. This check SCANNED
                # coordination-board.md and passed, because the pattern list said
                # "LAST, right before merging" and the file said "IMMEDIATELY
                # before merging". Exactly the known-stale-phrasings-not-meaning
                # limit declared above, hit within minutes of declaring it.
                re.compile(r"sync THIS branch to `?origin/main`?\s+IMMEDIATELY", re.I),
                re.compile(r"sync .{0,30}\bbranch\b.{0,40}\bimmediately before merging", re.I),
            ],
            "false": [],
        },
    },
    {
        "id": "POST /api/bot/prop/report write gate",
        "source": "src/web/api/routers/prop.py",
        # fail-closed iff the token-unset branch raises 503.
        "extract": lambda t: "fail_closed" if re.search(
            r"def _require_write_token.*?status_code=503", t, re.S) else "permissive",
        "asserts": {
            "permissive": [
                re.compile(r"prop/report[^\n]{0,200}token-gated[^\n]{0,60}\bwhen set\b", re.I),
            ],
            "fail_closed": [],
        },
    },
    {
        "id": "/api/bot/devices admin-token gate",
        "source": "src/web/api/routers/devices.py",
        # permissive iff the token-unset branch returns instead of raising.
        "extract": lambda t: "permissive" if re.search(
            r"def _check_admin_token.*?if not expected:\s*\n\s*return", t, re.S) else "fail_closed",
        "asserts": {
            "fail_closed": [
                re.compile(r"/devices[^\n]{0,120}\bfail-?closed\b", re.I),
                re.compile(r"/devices[^\n]{0,120}\b503\b", re.I),
            ],
            "permissive": [],
        },
    },
]

# Docs where a claim about a live gate misleads a session. Broader than
# ACTIVE_DOCS: the 2026-08-10 drift was in the runbook and the board JSON,
# neither of which the other checks read.
_VALUE_DOC_EXTRAS = [
    "docs/runbooks/merge-queue.md",
    "docs/claude/coordination-board.md",
    "docs/claude/session-board.json",
    # The hook deny/nudge messages. Not prose about the rules — the text a
    # session is handed AT the moment it acts, which outranks any doc it might
    # not open. It carried the stale "sync IMMEDIATELY before merging" for an
    # hour after the flag was unticked, and nothing scanned it: `_active_files`
    # reads `.claude/**/SKILL.md` and `.claude/commands/*.md`, never
    # `settings.json`. An instruction surface that no guard reads is the same
    # blind spot one layer down.
    ".claude/settings.json",
]


def check_declared_values() -> list[str]:
    """A doc must not assert a live setting's value that the source contradicts."""
    fails: list[str] = []
    files = _active_files()
    files += [ROOT / p for p in _VALUE_DOC_EXTRAS if (ROOT / p).exists()]

    for c in VALUE_CONTRACTS:
        src = ROOT / c["source"]
        if not src.exists():
            fails.append(f"{c['source']}: source for '{c['id']}' is missing — "
                         f"this check cannot run; fix the path or drop the contract")
            continue
        text = src.read_text(encoding="utf-8")
        ex = c["extract"]
        if callable(ex) and not hasattr(ex, "search"):
            actual = ex(text)
        else:
            m = ex.search(text)
            actual = m.group(1) if m else None
        if actual is None:
            fails.append(f"{c['source']}: could not read the current value for "
                         f"'{c['id']}' — the extractor no longer matches, so this "
                         f"check is silently disabled. Fix it, do not ignore it")
            continue

        wrong = [(v, pats) for v, pats in c["asserts"].items() if v != actual]
        for claimed, patterns in wrong:
            for rel, i, line, context in _iter_windows(files):
                for pat in patterns:
                    m2 = pat.search(line)
                    if m2 and not _historical_near(context, line, m2):
                        fails.append(
                            f"{rel}:{i}: says '{c['id']}' is {claimed!r}, but "
                            f"{c['source']} sets it to {actual!r} -> {line.strip()[:110]}"
                        )
                        break
    return fails


# ---------------------------------------------------------------------------
# THE BACKLOG `status` ENUM, MIRRORED INTO THE DOC THAT INSTRUCTS SESSIONS.
#
# Added 2026-09-12 (MI-280,
# BL-20260912-THE-CANONICAL-DOCS-FIVE-TERMINAL-BACKLOG-DISPOSITIONS-ARE-NONE-OF-THE-SIX-THE-GUARD-ACCEPTS).
# § "Backlog governance" rule 3 listed five words as the terminal dispositions
# and the intersection with the enforced enum was EMPTY, so the document this
# repo ranks FIRST told a session to write a `status` CI refuses. That is the
# same class as `check_hierarchy_mirror` one file over: two lists of the same
# thing, one of them not executable, drifting apart in silence.
#
# ⚠️ IT READS THE ENUM, NEVER A THIRD COPY. A literal here would be a second
# place to forget, which is the defect rather than the fix.
#
# ⚠️ A MISSING MARKER, AN UNREADABLE ENUM OR AN EMPTY LIST ALL FAIL. Each of
# them is a way for this check to stop looking while still printing PASS, and
# `check_declared_values` above already carries that lesson in its own body.
# ---------------------------------------------------------------------------
STATUS_ENUM_DOC = "docs/CLAUDE-RULES-CANONICAL.md"
STATUS_ENUM_SOURCE = "scripts/check_claim_basis.py"
_ENUM_BEGIN = "<!-- status-enum:begin"
_ENUM_END = "<!-- status-enum:end"
_ENUM_MEMBER = re.compile(r"`([a-z_]+)`")


def _enforced_status_enum() -> set[str] | None:
    """The live value of `check_claim_basis.STATUS_ENUM`, or None."""
    src = ROOT / STATUS_ENUM_SOURCE
    if not src.exists():
        return None
    import importlib.util

    spec = importlib.util.spec_from_file_location("_ccb_enum", src)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:  # noqa: BLE001
        return None
    got = getattr(mod, "STATUS_ENUM", None)
    return set(got) if got else None


def _mirrored_status_enum() -> set[str] | None:
    """The values between the doc's mirror markers, or None if absent."""
    doc = ROOT / STATUS_ENUM_DOC
    if not doc.exists():
        return None
    text = doc.read_text(encoding="utf-8")
    a = text.find(_ENUM_BEGIN)
    b = text.find(_ENUM_END, a + 1) if a != -1 else -1
    if a == -1 or b == -1:
        return None
    body = text[text.find("-->", a) + 3:b]
    return set(_ENUM_MEMBER.findall(body)) or None


def check_status_enum_mirror() -> list[str]:
    enforced = _enforced_status_enum()
    if enforced is None:
        return [f"{STATUS_ENUM_SOURCE}: could not read STATUS_ENUM — this check "
                f"is silently disabled. Fix it, do not ignore it"]
    mirrored = _mirrored_status_enum()
    if mirrored is None:
        return [f"{STATUS_ENUM_DOC}: the `status-enum` mirror block is missing or "
                f"empty, so the doc no longer states the enum CI enforces "
                f"({sorted(enforced)})"]
    fails = []
    if mirrored - enforced:
        fails.append(
            f"{STATUS_ENUM_DOC}: mirror lists {sorted(mirrored - enforced)}, which "
            f"{STATUS_ENUM_SOURCE}::STATUS_ENUM does NOT accept — a session "
            f"following the doc would be refused by claim-basis-guard")
    if enforced - mirrored:
        fails.append(
            f"{STATUS_ENUM_DOC}: mirror omits {sorted(enforced - mirrored)}, which "
            f"{STATUS_ENUM_SOURCE}::STATUS_ENUM accepts — the doc understates the "
            f"vocabulary")
    return fails


# DOES THE DOC SAY WHAT A LIVE-FLIPPABLE KNOB IS ACTUALLY SET TO?
#
# `check_declared_values` above catches *the doc asserts X and the source says
# Y*. It cannot catch **silence** — a knob whose live value is flippable without
# a commit, whose value the doc never states at all. That is the more common
# failure and the harder one to notice, because a doc that says nothing reads
# exactly like a doc that is correct.
#
# ⚠️ IT ALSO HAD NO DENOMINATOR. VALUE_CONTRACTS is a hand-maintained list of
# THREE, and none of its entries is an env knob — so "the doc-value guard is
# green" said nothing whatever about the env table. MEASURED 2026-09-12:
# `get_env.py::ALLOWED_KEYS` carries 73 keys (each one a knob whose LIVE value
# is readable off `/proc/<MainPID>/environ`), CLAUDE.md's env table names 120
# distinct knobs, and of the 73 readable ones the doc claims a live value for
# EIGHTEEN — 40 are named and SILENT about it, 15 are not in the table at all.
#
# ⚠️ THAT 18 WAS 12 UNTIL THE PROBE WAS CHECKED AGAINST ITSELF. The first
# version of `_LIVE_CLAIM` had its `\b` anchors corrupted to literal backspace
# bytes, so four of the six phrases could never match and the check reported a
# WORSE coverage than the truth — in the direction that looks like a finding,
# which is the direction nobody re-checks. Caught by a control asserting that a
# row saying `LIVE VALUE` grades `claimed`, not by reading the regex.
#
# ⚠️ THE READ SURFACE IS THE DENOMINATOR, and it is IMPORTED, never restated.
# `ALLOWED_KEYS` already answers "which knobs can we read the live value of?";
# a second list here would be free to drift from the one the read path uses.
# ---------------------------------------------------------------------------
GET_ENV = "scripts/ops/get_env.py"

#: ⚠️ RE-POINTED 2026-09-21 by the operating reset. This read `CLAUDE.md` until
#: the env table — 186 KB of it, a third of that file — moved VERBATIM to
#: `docs/reference/env-vars.md`. Leaving the constant pointing at `CLAUDE.md`
#: would have been exactly the failure this module's own header warns about:
#: the guard still passes (or here, still fails) because the text it reads
#: MOVED, not because anything about the knobs changed. The name is kept so
#: every message below still reads naturally about "the env table".
CLAUDE_MD = "docs/reference/env-vars.md"

#: THREE states, never collapsed. `silent` is the one this check exists for —
#: it is NOT `undocumented` (the doc has a row and says nothing about the live
#: value) and it is emphatically not `claimed`.
ENV_CLAIMED = "claimed"
ENV_SILENT = "silent"
ENV_UNDOCUMENTED = "undocumented"
ENV_COVERAGE_STATES = (ENV_CLAIMED, ENV_SILENT, ENV_UNDOCUMENTED)

#: A row makes a LIVE-VALUE claim when it says so in one of these forms. Chosen
#: from what the document ACTUALLY uses (measured 2026-09-12: `LIVE VALUE` x7,
#: `live value` x3, `SHIPPED STATE` x2, `/proc/<MainPID>/environ` x9), never
#: invented — a classifier matching phrases nobody writes reports zero forever.
#:
#: ⚠️ "Default `X`" IS DELIBERATELY NOT ONE OF THEM. That is a fact about the
#: CODE, not a claim about the running process, and counting it would
#: manufacture coverage for every row in the table — the exact
#: bulk-by-construction defect this repo has already paid for once in the work
#: store's stage histogram.
_LIVE_CLAIM = re.compile(
    r"LIVE VALUES?\b|live value\b|SHIPPED STATE\b|shipped state\b"
    r"|/proc/<MainPID>/environ"
    r"|\bat default on the (?:live )?VM\b|\bunset on the live VM\b",
)

#: A first table cell, which may name several knobs (`A` / `B`). Anchored to the
#: leading pipe so prose backticks elsewhere cannot be mistaken for a row.
_ENV_ROW = re.compile(r"^\| (`[A-Z0-9_]{4,}`(?: / `[A-Z0-9_]{4,}`)*) \|(.*)$", re.M)

#: ⚠️ A MULTI-KEY ROW CREDITS EVERY KEY IT NAMES, and that is an OVER-CREDIT
#: wherever such a row states a live value for only some of them. It is stated
#: rather than corrected because the alternative — attributing a claim to the
#: nearest key by proximity — would be a guess dressed as a measurement, and the
#: rows that do this (`CONVICTION_SIZING_MODE / _DIRECTION / _ACCOUNTS`) genuinely
#: do state all three. So read `claimed` as an UPPER BOUND on coverage; `silent`
#: is the lower bound on the gap and is the number that matters.
#:
#: The `claimed` count on 2026-09-12, as a RATCHET. It may only go UP.
#:
#: ⚠️ THE GUARD DOES NOT FAIL ON TODAY'S RESIDUE, deliberately — coverage is 7
#: of 73 and failing on that would red every PR in the repo the day it merged,
#: which is how a guard gets switched off. It fails when coverage REGRESSES, and
#: it PRINTS the denominator on every run so a zero-coverage state can never
#: again read as a green.
ENV_CLAIMED_BASELINE = 18


def _allowed_keys() -> set[str] | None:
    """`get_env.ALLOWED_KEYS`, or None when it cannot be read.

    None is *we could not look*, never an empty set — an empty set would make
    every count below read `0 of 0`, which is a clean-looking answer to a
    question nobody asked.
    """
    src = ROOT / GET_ENV
    if not src.exists():
        return None
    m = re.search(r"ALLOWED_KEYS: tuple\[str, \.\.\.\] = \((.*?)\n\)",
                  src.read_text(encoding="utf-8"), re.S)
    if not m:
        return None
    return set(re.findall(r'"([A-Z0-9_]+)"', m.group(1))) or None


def env_knob_coverage(doc_text: str | None = None,
                      keys: set[str] | None = None) -> dict | None:
    """Per readable knob: does the doc CLAIM its live value, or say nothing?"""
    keys = keys if keys is not None else _allowed_keys()
    if not keys:
        return None
    if doc_text is None:
        doc = ROOT / CLAUDE_MD
        if not doc.exists():
            return None
        doc_text = doc.read_text(encoding="utf-8")

    claims: dict[str, bool] = {}
    for cell, rest in _ENV_ROW.findall(doc_text):
        makes_claim = bool(_LIVE_CLAIM.search(rest))
        for name in re.findall(r"`([A-Z0-9_]{4,})`", cell):
            claims[name] = claims.get(name, False) or makes_claim

    out: dict[str, str] = {}
    for k in sorted(keys):
        if k not in claims:
            out[k] = ENV_UNDOCUMENTED
        else:
            out[k] = ENV_CLAIMED if claims[k] else ENV_SILENT
    counts = {s: sum(1 for v in out.values() if v == s) for s in ENV_COVERAGE_STATES}
    return {"population": len(keys), "counts": counts, "per_key": out,
            "documented_rows": len(claims)}


def check_env_knob_live_values() -> list[str]:
    cov = env_knob_coverage()
    if cov is None:
        return [f"{GET_ENV}: could not read ALLOWED_KEYS (or {CLAUDE_MD} is "
                f"unreadable), so env-knob live-value coverage has NO "
                f"denominator. This check is silently disabled without it — fix "
                f"it, do not ignore it."]
    c = cov["counts"]
    print(f"      env live-value coverage: {c[ENV_CLAIMED]} claimed · "
          f"{c[ENV_SILENT]} silent · {c[ENV_UNDOCUMENTED]} undocumented "
          f"of {cov['population']} readable knob(s) "
          f"({cov['documented_rows']} named in the {CLAUDE_MD} env table)")
    if c[ENV_CLAIMED] < ENV_CLAIMED_BASELINE:
        return [f"{CLAUDE_MD}: live-value coverage REGRESSED — "
                f"{c[ENV_CLAIMED]} knob(s) carry a live-value claim, against a "
                f"baseline of {ENV_CLAIMED_BASELINE} on 2026-09-12. A knob whose "
                f"stated live value was removed reads exactly like one that never "
                f"had one. Restore it, or raise ENV_CLAIMED_BASELINE deliberately "
                f"with the reason."]
    return []


CHECKS = [
    # FIRST, because the checks below read these files' CONTENT and a
    # conflicted file makes some of their verdicts untrustworthy. It does
    # not short-circuit them — see `check_conflict_markers` for why.
    ("governance docs carry no unresolved merge conflict", check_conflict_markers),
    ("dead VM IP single-source", check_dead_vm_ip),
    ("removed gates not described as live", check_removed_gates),
    ("no 7-stage ML ladder in catalog", check_seven_stage_ladder),
    ("instruction-hierarchy mirror", check_hierarchy_mirror),
    ("declared values match their source", check_declared_values),
    ("backlog status enum mirrored into the canonical doc",
     check_status_enum_mirror),
    ("env knobs state their live value (ratchet + denominator)",
     check_env_knob_live_values),
]


def main() -> int:
    total = 0
    for name, fn in CHECKS:
        fails = fn()
        if fails:
            total += len(fails)
            print(f"FAIL  {name}  ({len(fails)})")
            for f in fails:
                print(f"      {f}")
        else:
            print(f"PASS  {name}")
    if total:
        print(f"\ncanonical-doc-coherence: {total} issue(s). See docs/CLAUDE-RULES-CANONICAL.md.")
        return 1
    print("\ncanonical-doc-coherence: all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
