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
# Every document a session might read AS INSTRUCTION OR AS EVIDENCE. Tracked
# via `git ls-files` rather than a filesystem walk so an untracked scratch file
# in a working tree can never silently enter or leave the register.
POPULATION_GLOBS = [
    "docs/**/*.md",
    "ROADMAP*.md",
    "CLAUDE.md",
    ".claude/skills/**/*.md",
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

# Paths whose status is owned by the CONCURRENT session MI-159 (PR #11241),
# which is establishing which work plan is live and correcting roadmap statuses.
# Two sessions independently deciding that would reproduce the exact defect both
# are fixing, so these rows are registered `unknown` and their determination is
# deferred -- deliberately, and named.
MI159_OWNED_RE = re.compile(r"(^ROADMAP.*\.md$)|(WORKPLAN)", re.IGNORECASE)
MI159_NOTE = "status owned by MI-159 (PR #11241, open at index build); not decided here"

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
}


def _sh(argv: List[str]) -> str:
    return subprocess.run(
        argv, cwd=REPO, capture_output=True, text=True, check=True
    ).stdout


def population() -> List[str]:
    out = _sh(["git", "ls-files", "--"] + POPULATION_GLOBS)
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


def status_for(rel: str, active_docs: List[str], category: str) -> Tuple[str, str, str, str]:
    """Return (status, superseded_by, basis, note).

    Conservative by construction: every path out of this function that is not a
    VERIFIED determination returns `unknown`.
    """
    # 1. Deferred to the concurrent session that owns the determination.
    #    Scoped to the PLANS themselves. A sprint LOG that happens to carry
    #    "WORKPLAN" in its name is a record of a session that already happened;
    #    its status is not contested by MI-159 and deferring it would overstate
    #    that session's scope while leaving a resolvable row unresolved.
    if category != "history" and MI159_OWNED_RE.search(rel):
        return "unknown", "", "deferred:MI-159", MI159_NOTE

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

    # 6. Nobody has checked. The required honest state.
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


def build_rows(today: str) -> List[Dict[str, str]]:
    active = _canonical_active_docs()
    rows = []
    for rel in population():
        cat, cbasis = categorize(rel)
        st, sup, sbasis, note = status_for(rel, active, cat)
        verified = today if sbasis not in ("not-assessed", "deferred:MI-159") else "never"
        rows.append({
            "path": rel,
            "category": cat,
            "status": st,
            "superseded_by": sup,
            "last_verified": verified,
            "basis": f"{cbasis} / {sbasis}",
            "note": note,
        })
    return rows


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

### Rows deferred to MI-159 — not an omission

`ROADMAP*.md` and the `WORKPLAN-*` family are registered `unknown` with basis
`deferred:MI-159`. A concurrent session (**MI-159, PR #11241 — open, not merged
at index build**) is establishing which work plan is live and correcting
roadmap statuses. **Two sessions independently deciding which plan is live
would reproduce the exact defect both are fixing.** When #11241 lands, those
rows get their status from its work.

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

Adding a document to `docs/**`, `ROADMAP*.md`, `CLAUDE.md` or
`.claude/skills/**` and not re-running `--write` **fails CI (R1)**. That is the
point: registration is not optional, and it is not left to memory.

## The table

**Population: {n} documents** — every file matching `docs/**/*.md`,
`ROADMAP*.md`, `CLAUDE.md`, `.claude/skills/**/*.md` as tracked by `git ls-files`
(so an untracked scratch file can never silently enter or leave the register).
**{n} registered.**

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
    out = [f"POPULATION: {n} documents (git-tracked, globs={POPULATION_GLOBS})",
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
