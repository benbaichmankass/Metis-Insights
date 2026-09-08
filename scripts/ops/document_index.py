#!/usr/bin/env python3
"""Build `docs/DOCUMENT-INDEX.md` — the one centralized document register — and
stamp each registered document's status into its OWN header.

WHY THIS EXISTS (operator, 2026-09-07):

    "we need a library for our documents. Like, we're having a hard time
    keeping track of things, and you're right that a lot of things probably
    look fine if you just open them on their own. So maybe, like, one
    centralized table where we keep track of everything, and everything has to
    be organized correctly and logged and categorized."

THE MOTIVATING FAILURE, measured the same day. A manager reported roadmap
status to the operator off `docs/research/WORKPLAN-2026-08-14.md`, which
declares **"Status: ACTIVE"** in its own header while being three generations
superseded (08-14 -> 08-21 -> 08-26 -> 08-29). Twelve work-plan documents
exist, `ROADMAP.md` pointed at the wrong one, and `docs/claude/CYCLE-PRIORITY.json`
-- newer than every work plan -- is what actually drives sessions. Three
surfaces, three answers, and the operator got a wrong report.

**It looked fine opened on its own.** That is the operator's own diagnosis, and
it is why this ships TWO artifacts rather than one:

  1. the centralized table (`docs/DOCUMENT-INDEX.md`), and
  2. a status stamp in every registered document's own header,

kept in agreement by `scripts/ci/check_document_index.py` in CI. An index alone
would not have prevented the failure, because the manager never opened it.

THE ONE RULE THAT SHAPES EVERYTHING HERE: a status this tool cannot ESTABLISH
is written `unknown`, never guessed. An invented status is worse than an absent
one, because it reads as checked -- which is precisely how 08-14 fooled a
manager. Every row therefore carries a `basis` naming what established it, and
a row whose basis is `not-assessed` is making no claim at all.

Usage:
    python3 scripts/ops/document_index.py --census        # report only, no writes
    python3 scripts/ops/document_index.py --write         # rebuild index + stamp headers
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Tuple

REPO = Path(__file__).resolve().parents[2]
INDEX_PATH = REPO / "docs" / "DOCUMENT-INDEX.md"

ROWS_BEGIN = "<!-- DOCUMENT-INDEX-ROWS-BEGIN -->"
ROWS_END = "<!-- DOCUMENT-INDEX-ROWS-END -->"

# The visible stamp a reader sees when they open ONE file directly.
# Deliberately NOT an HTML comment: a comment is invisible when the markdown is
# rendered, which would leave the reader in exactly the position the manager was
# in on 2026-09-07 -- a document that "looks fine opened on its own".
STAMP_SENTINEL = "**Doc status:**"
STAMP_RE = re.compile(
    r"^>\s*\*\*Doc status:\*\*\s*`(?P<status>[a-z_]+)`", re.MULTILINE
)

# ---------------------------------------------------------------------------
# THE POPULATION
# ---------------------------------------------------------------------------
# Every document a session might read AS INSTRUCTION OR AS EVIDENCE. Resolved
# through git rather than a filesystem walk, so gitignored scratch can never
# silently enter or leave the register -- but INCLUDING newly-added files, for
# the reason `population()` documents below.
# ⚠️ THE `:(glob)` MAGIC IS LOAD-BEARING AND WAS MISSING ON THE FIRST BUILD.
# These strings are handed to `git ls-files` as PATHSPECS, not to Python's
# `glob`. In a pathspec with NO magic, git matches with wildmatch WITHOUT
# WM_PATHNAME, so `*` already matches `/` — `docs/*.md` is recursive — while
# `docs/**/` requires a LITERAL intervening slash. `docs/**/*.md` therefore
# matched every NESTED document and EXCLUDED every `.md` sitting directly in
# `docs/`: 28 files, among them `docs/CLAUDE-RULES-CANONICAL.md` (instruction
# hierarchy level 1), `docs/ARCHITECTURE-CANONICAL.md` (level 2),
# `docs/api-tier-policy.md`, `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`,
# `docs/TRADE-PIPELINE.md` and `docs/workplan.md`. The guard meanwhile printed
# `population=971 registered=971` and `document-index: OK` — 100% coverage of a
# population that silently excluded its own most important members, which is
# UNPROVENANCED DIAGNOSTIC OUTPUT sub-class C (an unasserted denominator).
#
# This is the SECOND instance of that class in this one function; `population()`
# below records the first (`--others --exclude-standard` missing, "reported OK
# and the count never moved"). Both were caught the same way — by running the
# thing against a real file instead of reasoning about it.
#
# `:(glob)` selects wildmatch WITH WM_PATHNAME, under which `*` stops at a `/`
# and `/**/` means "zero or more directories" — so `docs/**/*.md` now means what
# every reader already thought it meant, and covers `docs/x.md` AND
# `docs/a/b/x.md`. The alternative that also works, bare `docs/*.md`, relies on
# `*` crossing `/` — the very non-obvious behaviour that caused this bug — so it
# is deliberately NOT the fix. `.claude/skills/**/*.md` carried the identical
# latent defect (today every skill is nested one deep, so it excluded nothing;
# a top-level `.claude/skills/NOTES.md` would have been invisible) and is fixed
# in the same way rather than left to bite later.
# Verified, not asserted (`git ls-files --cached --others --exclude-standard`):
#   docs/**/*.md          -> 936 files,   0 top-level, 936 nested
#   :(glob)docs/**/*.md   -> 964 files,  28 top-level, 936 nested
POPULATION_GLOBS = [
    ":(glob)docs/**/*.md",
    "ROADMAP*.md",
    "CLAUDE.md",
    ":(glob).claude/skills/**/*.md",
]

# ---------------------------------------------------------------------------
# CATEGORY -- a CLOSED set of six
# ---------------------------------------------------------------------------
# The three the brief requires are distinguished, and the reason they must be is
# the 08-14 failure itself: a PLAN was obeyed as an INSTRUCTION. So `plan` is
# its own category rather than being folded into either.
#
# Note the deliberate name `lookup` rather than `reference`: `reference` is
# already a STATUS value, and one word meaning two things in one table is how a
# vocabulary stops being closed.
CATEGORIES = {
    "instruction": "A session must OBEY it. Binding.",
    "architecture": "A declared contract about how the system is built; read as truth about the system.",
    "plan": "A forward commitment -- what we intend to do next. NEVER obeyed as instruction.",
    "evidence": "A measurement that may be CITED. True of its date and population.",
    "history": "A record of what happened. Never obeyed, never cited as a current measurement.",
    "lookup": "Consulted for a fact. Neither obeyed, nor a measurement, nor a record of events.",
}

# Ordered; first match wins. The BASIS of every rule is the repo's own directory
# structure, which is a real structural fact about where this repo puts things --
# not an inference about any individual file's content.
# RECORD directories, resolved FIRST and unconditionally. A record of a session
# that happened is history REGARDLESS of what the session was about -- a sprint
# log named `S-ROADMAP-WORKPLAN-REVIEW-2026-08-14.md` is a record of a review,
# not a work plan. Letting a filename override these would misfile 365 rows.
RECORD_DIRS: List[Tuple[str, str]] = [
    ("docs/sprint-logs/", "dir:sprint-logs-are-session-records"),
    ("docs/sprint-summaries/", "dir:sprint-summaries-are-session-records"),
    ("docs/sprints/", "dir:sprints-are-session-records"),
    ("docs/claude/checkpoints/", "dir:checkpoints-are-session-state-records"),
    ("docs/claude/dispositions/", "dir:dispositions-record-decisions-taken"),
]

# STRONG name rules — the filename DECLARES the document's kind, and that beats
# the directory it happens to sit in.
#
# ⚠️ THIS ORDERING IS LOAD-BEARING AND WAS WRONG ON THE FIRST BUILD. With the
# directory consulted first, `docs/research/WORKPLAN-2026-08-14.md` — the exact
# file that misled a manager on 2026-09-07 — categorised as `evidence`, because
# it lives under `docs/research/`. It is a PLAN. Filing a plan as a measurement
# is a sibling of the failure this register exists to stop: the whole reason
# `plan` is its own category is that on 2026-09-07 a plan was read as something
# it was not.
CATEGORY_NAME_RULES_STRONG: List[Tuple[re.Pattern, str, str]] = [
    (re.compile(r"workplan", re.I), "plan", "name:workplan-is-a-forward-commitment"),
    (re.compile(r"roadmap", re.I), "plan", "name:roadmap-is-a-forward-commitment"),
    (re.compile(r"-DESIGN\.md$", re.I), "architecture", "name:design-declares-a-contract"),
    (re.compile(r"-PLAN[-.]", re.I), "plan", "name:plan-is-a-forward-commitment"),
]

CATEGORY_RULES: List[Tuple[str, str, str]] = [
    (".claude/skills/", "instruction", "dir:skills-are-binding-workflows"),
    # More specific prefixes first -- first match wins.
    ("docs/claude/diagnoses/", "evidence", "dir:a-diagnosis-is-a-finding"),
    ("docs/strategies/", "architecture", "dir:strategy-specs-declare-behaviour"),
    ("docs/data/", "architecture", "dir:schema-and-taxonomy-are-contracts"),
    ("docs/operator/", "lookup", "dir:operator-setup-procedures"),
    ("docs/sprint-plans/", "plan", "dir:sprint-plans-are-forward-commitments"),
    ("docs/research/", "evidence", "dir:research-is-measurement"),
    ("docs/audits/", "evidence", "dir:audits-are-measurement"),
    ("docs/audit/", "evidence", "dir:audits-are-measurement"),
    ("docs/backtests/", "evidence", "dir:backtests-are-measurement"),
    ("docs/design/", "architecture", "dir:design-declares-contracts"),
    ("docs/architecture/", "architecture", "dir:architecture-declares-contracts"),
    ("docs/runbooks/", "lookup", "dir:runbooks-are-procedures-to-consult"),
    ("docs/reference/", "lookup", "dir:reference-is-lookup"),
    ("docs/workflows/", "lookup", "dir:workflow-notes-are-lookup"),
    ("docs/integrations/", "architecture", "dir:integration-specs-declare-contracts"),
]

# Individually assigned, because these paths' categories are NOT derivable from
# a directory. Each is a file this session opened.
CATEGORY_EXPLICIT: Dict[str, Tuple[str, str]] = {
    "CLAUDE.md": ("instruction", "read:repo-orientation-and-binding-brief"),
    "docs/CLAUDE-RULES-CANONICAL.md": ("instruction", "read:hierarchy-rank-1"),
    "docs/ARCHITECTURE-CANONICAL.md": ("architecture", "read:hierarchy-rank-2"),
    "ROADMAP.md": ("plan", "read:hierarchy-rank-3-milestone-record"),
    "ROADMAP_MACRO.md": ("plan", "read:macro-milestone-record"),
    "docs/SPRINT-LOG-TEMPLATE-CANONICAL.md": ("instruction", "read:mandatory-format-spec"),
    "docs/api-tier-policy.md": ("instruction", "read:ci-enforced-tier-inventory"),
    # A forward commitment (a ranking of what to do next), NOT an instruction --
    # the `plan` category exists precisely because on 2026-09-07 a plan was
    # obeyed as one. Read in full by MI-162; no directory or name rule reaches
    # it, so it graded `unknown` until assigned here.
    "docs/claude/TASK-PRIORITY-2026-09-07.md": ("plan", "read:ranks-tasks-under-the-current-cycle-priority"),
}

# ---------------------------------------------------------------------------
# STATUS -- the mandated CLOSED set
# ---------------------------------------------------------------------------
STATUSES = {
    "live": "Current. A session may act on it today.",
    "superseded": "Overtaken by a named successor. REQUIRES `superseded_by`.",
    "closed_unfinished": "Abandoned mid-flight. NOT the same fact as superseded -- record what was left.",
    "historical": "A record of something that happened. Correct forever, actionable never.",
    "reference": "Consulted on demand. Neither current-and-actionable nor superseded.",
    "unknown": "NOBODY HAS CHECKED. Not a soft 'live'. The honest state, and a required one.",
}

# ---------------------------------------------------------------------------
# STATUS_EXPLICIT -- a status a SESSION ESTABLISHED by opening the file
# ---------------------------------------------------------------------------
# ⚠️ THIS IS NOT A PLACE TO MAKE THE CENSUS LOOK BETTER. 551 of 971 rows read
# `unknown` and that is the honest shape; `unknown` means *we did not look*, and
# for a document nobody has read it is the CORRECT value. An invented `live` is
# worse than an absent one, because it reads as checked -- which is exactly how
# `WORKPLAN-2026-08-14.md` fooled a manager on 2026-09-07 and is the whole
# reason this register exists.
#
# The bar for an entry: a session OPENED the file, and the basis names what that
# session established. It is consulted LAST, below every derivable rule (see
# `status_for` step 6), so it can only ever convert `unknown` into a recorded
# determination -- never overrule a guard, an import, or a self-declaration.
#
# Bulk-adding entries here to drain the `unknown` column would reproduce the
# defect. One row per document actually read, or leave it `unknown`.
STATUS_EXPLICIT: Dict[str, Tuple[str, str, str]] = {
    # Written 2026-09-07 and read in full by MI-162 the same day. It is the
    # CURRENT task ranking: it anchors to `CYCLE-PRIORITY.json`'s
    # `CY-20260906-TRADING-TRUTH` (operator-set 09-06, re-affirmed 09-07), names
    # `WORKPLAN-2026-08-29.md` -- MI-159's one live plan -- as its companion
    # rather than its replacement, and nothing supersedes it. It shipped
    # carrying `unknown` only because it was written minutes before this index
    # landed, so no rule had yet been able to see it.
    "docs/claude/TASK-PRIORITY-2026-09-07.md": (
        "live",
        "read:MI-162-opened-it-anchored-to-the-current-cycle-priority",
        "ranks TASKS under CY-20260906-TRADING-TRUTH; companion to the live "
        "work plan WORKPLAN-2026-08-29.md, not a replacement for it",
    ),
}

# ---------------------------------------------------------------------------
# The WORK-PLAN family -- status IMPORTED from MI-159, never re-derived
# ---------------------------------------------------------------------------
# ⚠️ THIS WAS `deferred:MI-159` UNTIL #11241 MERGED (34e7a2bd, 2026-09-07). Those
# rows were `unknown` because a concurrent session owned the determination and
# two sessions deciding independently which plan is live would have reproduced
# the exact defect both were fixing. #11241 has landed, so the deferral is over
# and this register now READS its answer.
#
# It reads it by IMPORTING that session's own parser rather than copying a table
# of statuses. A second copy of "which work plan is live" is precisely the
# multi-surface drift this register exists to stop -- the same reason
# `_canonical_active_docs` imports `ACTIVE_DOCS` instead of restating it. If
# MI-159's guard and this index ever disagree, that is a bug in one of them, not
# a fact to be reconciled by hand.
#
# Its vocabulary is nearly ours; `closed_finished` is the one value we do not
# carry, and it maps to `historical` (a completed plan is a record of something
# that happened -- correct forever, actionable never).
_MI159_GUARD = "scripts/ci/check_one_live_workplan.py"
_MI159_STATE_MAP = {
    "live": "live",
    "superseded": "superseded",
    "closed_unfinished": "closed_unfinished",
    "closed_finished": "historical",
    "historical": "historical",
}

# ROADMAP*.md is NOT covered by that guard (its discovery matches work plans
# only). MI-159 corrected ROADMAP.md's statuses on 2026-09-07, but there is no
# machine-readable status header on it, so this register does not assert one.
ROADMAP_RE = re.compile(r"^ROADMAP.*\.md$", re.IGNORECASE)
ROADMAP_NOTE = ("MI-159 (#11241) corrected this file's milestone statuses 2026-09-07; "
                "it carries no machine-readable status header, so no status is asserted here")


def _mi159_states() -> Dict[str, Dict[str, str]]:
    """Map rel-path -> MI-159's parsed header, or {} if its guard is absent."""
    path = REPO / _MI159_GUARD
    if not path.exists():
        return {}
    try:
        spec = importlib.util.spec_from_file_location("_olw", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out = {}
        for rel in mod.discover(REPO):
            d = mod.parse(REPO, rel)
            if d.get("state"):
                out[rel] = d
        return out
    except Exception:
        # A parser we cannot run is "we did not look" -- those rows fall through
        # to `unknown`, never to an invented status.
        return {}

# A document that declares itself DEAD is recorded on that declaration. A
# document that declares itself ALIVE is NOT -- that asymmetry is the whole
# lesson of 2026-09-07: `WORKPLAN-2026-08-14.md` says "Status: ACTIVE" and is
# three generations superseded. A self-declared death is rarely wrong in the
# dangerous direction; a self-declared life is exactly the claim that misled a
# manager, so it buys nothing here and is treated as `unknown`.
SELF_DEAD_RE = re.compile(
    r"^\s*[>*_#\s-]*\**(?:status|state)\**\s*[:=]\s*\**\s*"
    r"(?P<v>superseded|obsolete|retired|abandoned|deprecated|parked|"
    r"not\s+shipped|cancelled|canceled|withdrawn)",
    re.IGNORECASE | re.MULTILINE,
)
SELF_DEAD_MAP = {
    "superseded": "superseded",
    "obsolete": "superseded",
    "retired": "superseded",
    "deprecated": "superseded",
    "abandoned": "closed_unfinished",
    "parked": "closed_unfinished",
    "not shipped": "closed_unfinished",
    "cancelled": "closed_unfinished",
    "canceled": "closed_unfinished",
    "withdrawn": "closed_unfinished",
}

# ---------------------------------------------------------------------------
# GENERATED documents -- exempt from HEADER stamping, and the exemption is
# VERIFIED, never presence-only.
# ---------------------------------------------------------------------------
# A generator rewrites these wholesale, so a stamp written into one would be
# silently erased on the next run -- an index and a header that disagree with
# nobody at fault. They are still REGISTERED; only the header rule is waived.
#
# The waiver is verified at build time: the named generator must exist AND must
# name the file. A marker naming a generator that does not exist grants nothing.
# (This repo has been bitten by presence-only markers that were cheaper to lie
# to than to satisfy -- `new-table-wiring-guard`.)
GENERATED: Dict[str, str] = {
    "docs/claude/READOUT.md": "scripts/ops/constraint_readout.py",
    "docs/claude/DUE.md": "scripts/ops/render_due_list.py",
    # Both became visible only when the population pathspec was fixed (MI-162):
    # they sit directly in `docs/` and were outside the register entirely. Each
    # is rewritten wholesale by its own `--matrix` generator, so a stamp written
    # into one is erased on the next run and its guard then fails the build with
    # "matrix is stale" — observed, not predicted: stamping them failed
    # `strategy-coverage-guard` and `training-population-guard` on the first
    # full run of this branch. They are REGISTERED like everything else; only
    # the R3 header rule is waived, and the waiver is verified at build time.
    "docs/strategy-coverage-matrix.md": "scripts/check_strategy_coverage.py",
    "docs/training-population-matrix.md": "scripts/check_training_population.py",
}


def _sh(argv: List[str]) -> str:
    return subprocess.run(
        argv, cwd=REPO, capture_output=True, text=True, check=True
    ).stdout


def population() -> List[str]:
    """Every document in scope, tracked OR newly added but not gitignored.

    ⚠️ `--others --exclude-standard` is load-bearing and was MISSING on the
    first build. With plain `git ls-files` the population is TRACKED files only,
    so a document a session had just written was invisible: the guard read the
    new file's own directory, reported `OK`, and the count never moved. That is
    the clean negative this repo has a rule about — a guard answering "nothing
    unregistered" over a population that silently excluded the very file being
    added. Caught by running the guard against a genuinely new document rather
    than by reasoning about it.

    `--exclude-standard` keeps gitignored scratch out, so the original intent
    (an untracked scratch file cannot silently enter the register) survives.
    """
    out = _sh(["git", "ls-files", "--cached", "--others", "--exclude-standard",
               "--"] + POPULATION_GLOBS)
    return sorted({p for p in out.splitlines() if p.strip()})


def _canonical_active_docs() -> List[str]:
    """The docs whose CURRENCY a CI guard already enforces.

    Imported from `check_canonical_doc_coherence.py` rather than copied. A
    second list of "which docs are live" would be free to drift from the one CI
    actually enforces, and this file exists because surfaces drifted.
    """
    path = REPO / "scripts" / "ci" / "check_canonical_doc_coherence.py"
    spec = importlib.util.spec_from_file_location("_cdc", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod.ACTIVE_DOCS)


# Applied only where NO directory rule fires. Each keys on a word the document's
# own filename uses to declare its KIND -- a `*-policy.md` prescribes, a
# `*-DESIGN.md` declares a contract, an `*-audit-*.md` measures. Weaker evidence
# than a directory, so it runs second and anything it misses stays `unknown`.
CATEGORY_NAME_RULES: List[Tuple[re.Pattern, str, str]] = [
    (re.compile(r"-design[-.]", re.I), "architecture", "name:design-declares-a-contract"),
    (re.compile(r"polic(y|ies)|-rules|-protocol|permissions-tiers", re.I), "instruction", "name:policy-prescribes"),
    (re.compile(r"audit|inventory|assessment|diagnos", re.I), "evidence", "name:audit-measures"),
    (re.compile(r"handoff|checkpoint|-state\.md$", re.I), "history", "name:record-of-a-past-state"),
    (re.compile(r"contracts?\.md$|-architecture", re.I), "architecture", "name:declares-a-contract"),
]


def categorize(rel: str) -> Tuple[str, str]:
    """Resolution order, strongest evidence first.

    1. explicit  -- a path this session opened and assigned individually
    2. RECORD_DIRS -- a record is a record whatever it is about
    3. strong name rules -- the filename declares the KIND
    4. directory rules -- where this repo puts things
    5. weak name rules
    6. `unknown` -- nobody could establish it. Marked, never guessed.
    """
    if rel in CATEGORY_EXPLICIT:
        return CATEGORY_EXPLICIT[rel]
    for prefix, basis in RECORD_DIRS:
        if rel.startswith(prefix):
            return "history", basis
    name = Path(rel).name
    for pat, cat, basis in CATEGORY_NAME_RULES_STRONG:
        if pat.search(name):
            return cat, basis
    for prefix, cat, basis in CATEGORY_RULES:
        if rel.startswith(prefix):
            return cat, basis
    for pat, cat, basis in CATEGORY_NAME_RULES:
        if pat.search(name):
            return cat, basis
    return "unknown", "not-assessed"


def _head(rel: str, lines: int = 20) -> str:
    try:
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[:lines])


def status_for(rel: str, active_docs: List[str], category: str,
               mi159: Dict[str, Dict[str, str]]) -> Tuple[str, str, str, str]:
    """Return (status, superseded_by, basis, note).

    Conservative by construction: every path out of this function that is not a
    VERIFIED determination returns `unknown`.
    """
    # 1. The work-plan family: MI-159's determination, imported.
    #    Scoped to the PLANS themselves. A sprint LOG that happens to carry
    #    "WORKPLAN" in its name is a record of a session that already happened
    #    and is graded `historical` by rule 4 below; MI-159's guard does not
    #    discover it either, so the two agree by construction.
    if category != "history":
        d = mi159.get(rel)
        if d:
            mapped = _MI159_STATE_MAP.get(d["state"])
            if mapped:
                left = (d.get("what_was_left") or "").strip()
                note = ""
                if mapped == "closed_unfinished":
                    note = (f"what was left: {left}" if left
                            else "abandoned mid-flight; the file records no residual")
                return (mapped, d.get("superseded_by") or "",
                        f"mi159:plan-status-header:{d['state']}", note)
        if ROADMAP_RE.search(rel):
            return "unknown", "", "not-assessed", ROADMAP_NOTE

    # 2. A CI guard actively enforces this document's currency. That guard
    #    failing on drift IS the evidence it is live -- not an assertion.
    if rel in active_docs:
        return "live", "", "ci:canonical-doc-coherence-ACTIVE_DOCS", ""

    # 3. Skills are loaded and offered to every session by the harness; a stale
    #    one is executed, not merely read. Live by construction.
    if rel.startswith(".claude/skills/") and rel.endswith("SKILL.md"):
        return "live", "", "harness:skill-is-loaded-and-invocable", ""

    # 4. A record of a session that already happened. This is what the
    #    directory MEANS, so it is a structural fact, not an inference.
    if category == "history":
        return "historical", "", "dir:record-of-a-completed-session", ""

    # 5. The document declares itself DEAD. Recorded; see SELF_DEAD_RE.
    m = SELF_DEAD_RE.search(_head(rel))
    if m:
        key = " ".join(m.group("v").lower().split())
        mapped = SELF_DEAD_MAP.get(key)
        if mapped:
            note = ""
            if mapped == "closed_unfinished":
                note = "self-declared abandoned/parked; WHAT WAS LEFT is not recorded in the file"
            return mapped, "", f"self-declared:{key}", note

    # 6. A status a SESSION established by opening the file. Deliberately the
    #    LAST rung before `unknown`: it can only ever turn "nobody looked" into
    #    a recorded determination, and can never override an imported (rule 1),
    #    CI-enforced (rule 2), structural (rules 3-4) or self-declared (rule 5)
    #    answer. A human-entered status that outranked a machine-checkable one
    #    would be the multi-surface drift this register exists to end.
    if rel in STATUS_EXPLICIT:
        st, basis, note = STATUS_EXPLICIT[rel]
        return st, "", basis, note

    # 7. Nobody has checked. The required honest state.
    return "unknown", "", "not-assessed", ""


def verify_generated() -> Dict[str, str]:
    """Return the SUBSET of GENERATED whose waiver actually verifies."""
    ok: Dict[str, str] = {}
    for doc, gen in GENERATED.items():
        gp = REPO / gen
        if not gp.exists():
            continue
        if Path(doc).name in gp.read_text(encoding="utf-8", errors="replace"):
            ok[doc] = gen
    return ok


# ---------------------------------------------------------------------------
# Header stamping
# ---------------------------------------------------------------------------
def stamp_line(rel: str, status: str, category: str, superseded_by: str,
               last_verified: str) -> str:
    depth = len(Path(rel).parts) - 1
    up = "../" * depth if depth else ""
    sup = f" · superseded by [`{superseded_by}`]({up}{superseded_by})" if superseded_by else ""
    tail = ""
    if status == "unknown":
        tail = " · **nobody has verified this document's status — do not act on it as current**"
    return (
        f"> {STAMP_SENTINEL} `{status}` · category `{category}` · "
        f"last verified `{last_verified}`{sup} · "
        f"registered in [`docs/DOCUMENT-INDEX.md`]({up}docs/DOCUMENT-INDEX.md){tail}"
    )


def _insert_at(lines: List[str]) -> int:
    """Where the stamp goes: after YAML frontmatter, else after the H1, else top.

    Frontmatter matters and is not hypothetical -- 32 documents in the
    population open with `---`, and every one of them is a SKILL.md whose
    frontmatter the harness parses. A stamp written above or inside that block
    would break the skill.
    """
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].strip() == "---":
        for j in range(i + 1, len(lines)):
            if lines[j].strip() == "---":
                return j + 1
        return i  # unterminated frontmatter: do not pretend to understand it
    if i < len(lines) and lines[i].startswith("# "):
        return i + 1
    return i


def apply_stamp(rel: str, stamp: str) -> bool:
    """Insert or replace the stamp. Returns True if the file changed.

    Idempotent, and it REPLACES its own previous stamp rather than accumulating
    one per run.
    """
    path = REPO / rel
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    existing = [n for n, ln in enumerate(lines) if ln.startswith(f"> {STAMP_SENTINEL}")]
    if existing:
        n = existing[0]
        if lines[n] == stamp:
            return False
        lines[n] = stamp
    else:
        at = _insert_at(lines)
        block = [stamp, ""] if (at < len(lines) and lines[at].strip()) else [stamp]
        if at > 0 and lines[at - 1].strip():
            block = [""] + block
        lines[at:at] = block

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Index rendering
# ---------------------------------------------------------------------------
def esc(s: str) -> str:
    return s.replace("|", "\\|")


def assess(rel: str, active: List[str], mi159: Dict[str, Dict[str, str]],
           today: str) -> Dict[str, str]:
    """The index row for ONE path. The single authority for a document's status.

    Factored out of `build_rows` so `stamp_for` below can derive a file's HEADER
    from the same computation that produces its ROW. R3 compares those two
    surfaces; deriving them from one function is what makes them unable to
    disagree. A second copy of this arithmetic -- even a two-line one -- would be
    free to drift, which is the failure this whole register exists to end.
    """
    cat, cbasis = categorize(rel)
    st, sup, sbasis, note = status_for(rel, active, cat, mi159)
    return {
        "path": rel,
        "category": cat,
        "status": st,
        "superseded_by": sup,
        "last_verified": "never" if sbasis == "not-assessed" else today,
        "basis": f"{cbasis} / {sbasis}",
        "note": note,
    }


def stamp_for(rel: str, today: str) -> str:
    """The exact stamp line the index would compute for `rel`.

    FOR GENERATORS THAT REWRITE THEIR OWN DOCUMENT WHOLESALE. Such a generator
    has two honest options and this is the better one: emit the stamp itself, or
    take a `GENERATED` waiver. The waiver drops the header a reader sees, so it
    is right only where a stamp would actively break the file's own guard (a
    `--matrix` generator whose output must be byte-exact). Where the generator
    CAN carry a stamp, it should -- and it must derive it from here rather than
    hardcode one, because a hardcoded status is a second surface, free to drift
    from the row R3 compares it against. That is precisely the
    two-surfaces-two-answers failure R3 exists to catch, and hardcoding it into
    the producer would reintroduce it one level up.
    """
    r = assess(rel, _canonical_active_docs(), _mi159_states(), today)
    return stamp_line(rel, r["status"], r["category"],
                      r["superseded_by"], r["last_verified"])


def build_rows(today: str) -> List[Dict[str, str]]:
    active = _canonical_active_docs()
    mi159 = _mi159_states()
    return [assess(rel, active, mi159, today) for rel in population()]


def render_index(rows: List[Dict[str, str]], today: str, generated: Dict[str, str]) -> str:
    n = len(rows)
    by_cat: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for r in rows:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    cat_tbl = "\n".join(
        f"| `{k}` | {by_cat.get(k, 0)} | {v} |" for k, v in CATEGORIES.items()
    )
    uncat = by_cat.get("unknown", 0)
    if uncat:
        cat_tbl += f"\n| `unknown` | {uncat} | **Could not be categorised. Not a category — the absence of one.** |"

    st_tbl = "\n".join(
        f"| `{k}` | {by_status.get(k, 0)} | {v} |" for k, v in STATUSES.items()
    )

    gen_tbl = "\n".join(
        f"| `{d}` | `{g}` | verified: generator exists and names the file |"
        for d, g in sorted(generated.items())
    ) or "| _(none)_ | | |"

    head = f"""# Document index — the centralized register

> {STAMP_SENTINEL} `live` · category `instruction` · last verified `{today}` · registered in [`docs/DOCUMENT-INDEX.md`](DOCUMENT-INDEX.md)

**One table. Every document a session might read as instruction or as evidence.**

Operator, 2026-09-07:

> *"we need a library for our documents. Like, we're having a hard time keeping
> track of things, and you're right that a lot of things probably look fine if
> you just open them on their own. So maybe, like, one centralized table where
> we keep track of everything, and everything has to be organized correctly and
> logged and categorized."*

## The failure this exists to stop, measured 2026-09-07

A manager reported roadmap status **to the operator** off
[`docs/research/WORKPLAN-2026-08-14.md`](research/WORKPLAN-2026-08-14.md), which
declares **`Status: ACTIVE`** in its own header while being **three generations
superseded** (08-14 → 08-21 → 08-26 → 08-29). Twelve work-plan documents exist,
`ROADMAP.md` pointed readers at the wrong one, and
[`docs/claude/CYCLE-PRIORITY.json`](claude/CYCLE-PRIORITY.json) — newer than
every work plan — is what actually drives sessions. Three surfaces, three
answers, and the operator got a wrong report.

**It looked fine opened on its own.** That is why this ships as two artifacts,
not one: this table, **and** a status stamp in every registered document's own
header. A reader who opens one file directly must not be misled. An index alone
would not have prevented the failure, because the manager never opened it.

## What keeps it true

[`scripts/ci/check_document_index.py`](../scripts/ci/check_document_index.py)
runs in CI on every PR and **fails** when:

| # | condition |
|---|---|
| **R1** | a document exists in the population and is **not registered** here |
| **R2** | a registered row names a file that **no longer exists** |
| **R3** | a file's own header status **disagrees** with its row here |
| **R4** | a row is `superseded` without naming a `superseded_by` |
| **R5** | a row uses a `category` or `status` outside the closed sets below |

It carries a **planted-positive control** (`--self-test`) that runs on every
invocation and fails the job unless each rule actually fires on a known-bad
input. A guard nobody has seen fail is not evidence.

## Category — a closed set of six

Assigned from the repo's own directory structure (a real structural fact about
where this repo puts things), or individually for the handful of paths where no
directory rule applies. **`instruction`, `evidence` and `history` are kept
apart because conflating them is how a superseded plan gets obeyed** — and
`plan` is its own category for exactly the same reason: on 2026-09-07 a *plan*
was read as an *instruction*.

> The set deliberately says `lookup`, not `reference` — `reference` is already
> a **status** value, and one word meaning two things in one table is how a
> vocabulary stops being closed.

| category | rows | meaning |
|---|---|---|
{cat_tbl}

## Status — a closed set of six

| status | rows | meaning |
|---|---|---|
{st_tbl}

⚠️ **`superseded` and `closed_unfinished` are DIFFERENT FACTS and are never
collapsed.** Overtaken by a successor is not the same as abandoned mid-flight.
Where a document closed unfinished, the row records what was left — or records
plainly that the file itself does not say.

⚠️ **`unknown` is a real, required state, and it is not a soft `live`.** It
means *nobody has checked*. A status this register cannot establish is written
`unknown` rather than guessed: an invented status is worse than an absent one,
because it reads as checked. That is exactly how `WORKPLAN-2026-08-14.md`
fooled a manager.

## How a status is established — the `basis` column

Every row names what established it. A row whose status basis is
`not-assessed` is making **no claim at all**.

| basis | what it means |
|---|---|
| `ci:canonical-doc-coherence-ACTIVE_DOCS` | a CI guard enforces this document's currency; the guard failing on drift **is** the evidence it is live. Imported from that guard, never copied. |
| `harness:skill-is-loaded-and-invocable` | the harness loads and offers this skill to every session; a stale one is executed, not merely read. |
| `dir:record-of-a-completed-session` | a record of a session that already happened — what the directory *means*, not an inference about the file. |
| `self-declared:<word>` | the document declares itself dead. See the asymmetry below. |
| `deferred:MI-159` | a concurrent session owns this determination. See below. |
| `not-assessed` | **nobody looked.** Status is `unknown`. |

### The self-declaration asymmetry — deliberate

A document that declares itself **dead** is recorded on that declaration. A
document that declares itself **alive** is **not** — it is `unknown`.

That asymmetry *is* the lesson of 2026-09-07. A self-declared death is rarely
wrong in the dangerous direction; a self-declared life is precisely the claim
that misled a manager. Measured across this population, **201 of {n} documents
declare something status-like in their first 15 lines, in an entirely
uncontrolled vocabulary** — including `tier`, `scope`, `a proposal`,
`measured`, and `credentialfree pipeline built`. There was no controlled status
vocabulary anywhere in this repo before this file.

### The work-plan family — imported from MI-159, not re-derived

⚠️ **These rows read `deferred:MI-159` / `unknown` until #11241 merged
(`34e7a2bd`, 2026-09-07). Do not re-quote that.** While it was open, a
concurrent session owned the determination and two sessions deciding
independently which plan is live would have reproduced the exact defect both
were fixing. It has landed, so the deferral is over and this register now reads
its answer — `live` ×1, `superseded` ×3 (each naming its successor),
`closed_unfinished` ×6, `historical` ×2.

**It is IMPORTED, never copied.** The basis `mi159:plan-status-header:<state>`
comes from running
[`check_one_live_workplan.py`](../scripts/ci/check_one_live_workplan.py)'s own
`discover` + `parse` over the tree — the same discipline
`ci:canonical-doc-coherence-ACTIVE_DOCS` uses. A second hand-maintained table of
"which work plan is live" is precisely the multi-surface drift this register
exists to stop; if that guard and this index ever disagree, one of them is
broken and it is not a fact to reconcile by hand.

Its vocabulary is nearly ours. `closed_finished` is the one value we do not
carry, and it maps to `historical` — a completed plan is a record of something
that happened: correct forever, actionable never. Where a plan is
`closed_unfinished`, this table carries **what was left**, taken from the file's
own header rather than summarised.

**`ROADMAP*.md` stays `unknown`, deliberately.** MI-159 corrected its milestone
statuses the same day, but that guard's discovery matches work plans only and
`ROADMAP.md` carries no machine-readable status header — so there is nothing to
import, and this register does not assert a status it cannot establish.

## Documents exempt from the HEADER rule (R3), and why

A generator rewrites these wholesale, so a stamp written into one would be
erased on its next run — leaving an index and a header disagreeing with nobody
at fault. They stay **registered**; only R3 is waived.

**The waiver is verified, never presence-only:** the named generator must exist
*and* must name the file, checked at build time. A marker naming a generator
that does not exist grants nothing. (This repo has been bitten by presence-only
markers that were cheaper to lie to than to satisfy — `new-table-wiring-guard`.)

| document | generator | waiver |
|---|---|---|
{gen_tbl}

## Maintaining this file

Rebuild it — never hand-edit a row:

```bash
python3 scripts/ops/document_index.py --census   # report only, no writes
python3 scripts/ops/document_index.py --write    # rebuild table + stamp headers
```

Adding a document to `docs/**` (at ANY depth, top level included), `ROADMAP*.md`, `CLAUDE.md` or
`.claude/skills/**` and not re-running `--write` **fails CI (R1)**. That is the
point: registration is not optional, and it is not left to memory.

## The table

**Population: {n} documents** — every file matching the git pathspecs
`:(glob)docs/**/*.md`, `ROADMAP*.md`, `CLAUDE.md`, `:(glob).claude/skills/**/*.md`
as tracked by `git ls-files` (so an untracked scratch file can never silently
enter or leave the register). **{n} registered.**

⚠️ **The `:(glob)` prefix is part of the population, not decoration.** These are
git PATHSPECS: without it, `*` crosses `/` and `**/` needs a literal intervening
slash, so `docs/**/*.md` silently excluded all 28 `.md` files sitting directly in
`docs/` — including the two that outrank `CLAUDE.md` — while this table reported
100% coverage over what was left (population 971, all registered, guard `OK`).
Fixed 2026-09-07, population 971 → 999; the guard's `--self-test` now plants a
real top-level `docs/*.md` and fails if the population builder cannot see it.

{ROWS_BEGIN}

| path | category | status | superseded_by | last_verified | basis | note |
|---|---|---|---|---|---|---|
"""

    body = "\n".join(
        "| `{p}` | {c} | {s} | {sb} | {lv} | `{b}` | {nt} |".format(
            p=esc(r["path"]), c=r["category"], s=r["status"],
            sb=(f"`{esc(r['superseded_by'])}`" if r["superseded_by"] else "—"),
            lv=r["last_verified"], b=esc(r["basis"]),
            nt=esc(r["note"]) if r["note"] else "—",
        )
        for r in rows
    )

    return head + body + "\n\n" + ROWS_END + "\n"


def census(rows: List[Dict[str, str]]) -> str:
    n = len(rows)
    by_cat: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for r in rows:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    out = [f"POPULATION: {n} documents (git-tracked or newly added and not gitignored; globs={POPULATION_GLOBS})",
           f"REGISTERED: {n}", "", "BY CATEGORY:"]
    for k in list(CATEGORIES) + ["unknown"]:
        if by_cat.get(k):
            out.append(f"  {k:<14} {by_cat[k]:>4}")
    out += ["", "BY STATUS:"]
    for k in STATUSES:
        out.append(f"  {k:<18} {by_status.get(k, 0):>4}")
    out += ["",
            f"COULD NOT CATEGORISE: {by_cat.get('unknown', 0)}",
            f"STATUS NOT ESTABLISHED (unknown): {by_status.get('unknown', 0)}"]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="rebuild index and stamp headers")
    ap.add_argument("--census", action="store_true", help="report only; no writes")
    a = ap.parse_args()

    today = date.today().isoformat()
    rows = build_rows(today)
    generated = verify_generated()

    if a.census or not a.write:
        print(census(rows))
        unverified = set(GENERATED) - set(generated)
        if unverified:
            print(f"\n⚠️ generated-waiver DID NOT VERIFY (waiver refused): {sorted(unverified)}")
        return 0

    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(render_index(rows, today, generated), encoding="utf-8")
    print(f"wrote {INDEX_PATH.relative_to(REPO)} ({len(rows)} rows)")

    changed = 0
    for r in rows:
        if r["path"] in generated:
            continue
        if apply_stamp(r["path"], stamp_line(
                r["path"], r["status"], r["category"],
                r["superseded_by"], r["last_verified"])):
            changed += 1
    print(f"stamped {changed} document header(s); "
          f"{len(generated)} generated document(s) exempt from R3")
    print()
    print(census(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
