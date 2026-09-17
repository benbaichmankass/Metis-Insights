#!/usr/bin/env python3
"""Generate a SESSION RECEIPT — the one canonical shape for "what did this session do".

WHY THIS IS A SCRIPT AND NOT A PROSE TEMPLATE
---------------------------------------------
Operator directive, 2026-09-17: *"i want this format of summary cannonized so that
this is what i get whenever i ask a session for a 'session receipt', and this is
what should be presented on the web page"*.

A format kept as prose drifts per session — that is the whole reason MI-290 exists.
Two of the five sections are **measurements**, not recollections, and a session that
retypes the query gets them wrong:

* Section B's *what actually landed* is produced by attributing commits on
  ``origin/main`` through their ``Claude-Session:`` trailer.
* Section C's PR list is verified by **grepping ``origin/main`` for the squash
  suffix** ``(#N)``.

Both were done that way on 2026-09-17 and **both caught errors in the same
session that wrote them** — an ancestry probe misreported a landed squash, and a
"three edits still owed" claim was two. So the queries live here, once.

⚠️ NEVER USE ``git merge-base --is-ancestor`` TO TEST WHETHER A PR LANDED.
This repo squash-merges. A squash rewrites the branch into a NEW commit on
``main``, so the branch's own commits are never ancestors of ``main`` and the
probe answers ``false`` for a PR that merged perfectly. :func:`verify_pr_landed`
greps the squash suffix instead, which is what actually appears on ``main``.

MEASURED 2026-09-17, and it is worse than "answers false" — **the probe cannot be
run at all.** PR #12072 merged as ``c068b7448``; its branch head ``09f76dcb`` (the
sha the 2026-09-17 receipt records it as green at) is **not present as an object
in a fresh clone**, because nothing fetches a squashed branch's commits. So
``merge-base --is-ancestor`` exits non-zero for *object not found*, which a caller
that reads any non-zero exit as "not an ancestor" books as ``not_landed``. The
squash-suffix test was run over the same 7 PRs in that receipt: **7 of 7 landed,
with shas matching the receipt exactly**, against a negative control (#99999999)
correctly reading ``not_landed``.

THE TWO BINDING RULES, ENFORCED RATHER THAN REQUESTED
-----------------------------------------------------
1. **Every number carries its population.** A count in this module's JSON is a
   :func:`counted` object — ``{"value": N, "population": "..."}`` — never a bare
   int. The repo's top-level rule (`CLAUDE-RULES-CANONICAL.md` § "Always state
   the population") is a habit everywhere else; here it is the type. The
   2026-09-17 delivery read *"349 attributed of 545 commits on origin/main since
   2026-09-11"*; a receipt that says "349 commits" is what this shape refuses to
   be able to express.

2. **Backlog ids are emitted VERBATIM from the backlog, never typed.**
   :func:`resolve_backlog_id` refuses an id that does not resolve, and
   :func:`expand_backlog_id` completes a unique prefix rather than letting a
   truncation through. This is not tidiness: ``check_backlog_refs`` fails any
   diff that introduces a reference resolving to nothing, and the 2026-09-17
   reference receipt **could not be committed** because it carried two shortened
   ids. The same trap fired FOUR times in that one session from three different
   sources — the author's own abbreviation twice, `CLAUDE.md`'s own prose, and
   the receipt. An author cannot be trusted to type these; a generator can read
   them.

THREE STATES, NEVER COLLAPSED
-----------------------------
``landed`` / ``not_landed`` / ``could_not_look`` — per `CLAUDE-RULES-CANONICAL.md`
§ "Collapsed states". A failed ``git`` read is **not** evidence a PR did not land,
and a receipt that renders the two identically is the defect this repo keeps a
guard family for. Same for attribution: a commit whose trailer could not be
parsed is ``unattributed``, never "somebody else's".

Stdlib-only. Read-only: it runs ``git log`` and reads committed JSON. It writes a
file only when ``--write`` is passed.

Usage:
  # the human-facing receipt, on demand, measuring a live session
  python3 scripts/ops/session_receipt.py --session-id session_01ABC --since 2026-09-11

  # the durable artifact the Workflow page reads
  python3 scripts/ops/session_receipt.py --session-id session_01ABC --since 2026-09-11 \
      --json --write

  # verify a PR list without building a whole receipt
  python3 scripts/ops/session_receipt.py --verify-prs 12072 12446 12447

  # planted-control self-test (no network, no live repo state)
  python3 scripts/ops/session_receipt.py --self-test
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from typing import Any, Iterable, Optional

REPO = pathlib.Path(__file__).resolve().parents[2]

#: Where a receipt lands. ⚠️ ``docs/claude/work/`` and NOT ``docs/claude/receipts/``,
#: and the directory level is load-bearing rather than cosmetic:
#: ``check_manager_scope``'s ``MANAGER_SURFACE`` admits ``docs/claude/work/**`` and
#: does not admit ``docs/claude/receipts/**``. A MANAGER is the primary author of
#: receipts, so a path off the manager surface makes the mechanism unusable by the
#: one role that needs it most — which is exactly what happened on 2026-09-17,
#: when the reference receipt was refused by R2 at ``docs/claude/receipts/``.
RECEIPTS_RELDIR = "docs/claude/work/receipts"

#: ONE FILE PER SESSION, never a shared register, and this is a decision with
#: measured backing rather than a preference. A receipt is written by exactly one
#: session about itself, and several lanes wrap at once. The repo has already paid
#: for the shared-mutable-array shape twice: ``health-review-backlog.json``
#: conflicts constantly, and ``session-board.json::merge_slot`` was measured at
#: **39 of the last 40 commits touching that file moving the same field**, which
#: made every armed branch go ``dirty`` and restarted its CI. The fix there was to
#: move to a per-branch file under ``.github/merge-slots/``; this is the same fix,
#: taken before the same cost. ``docs/claude/work/README.md`` states the principle:
#: one file per object, so two sessions touching different work never conflict.
RECEIPT_FILENAME = "{session_id}.json"

BACKLOG_RELPATHS = (
    "docs/claude/health-review-backlog.json",
    "docs/claude/performance-review-backlog.json",
    "docs/claude/ml-review-backlog.json",
    "docs/claude/research-review-backlog.json",
)

#: The trailer a Claude commit carries. Kept byte-compatible with
#: ``scripts/ci/check_manager_scope.py::SESSION_TRAILER`` on purpose — two
#: definitions of "which session wrote this commit" is how the manager guards and
#: the receipt would come to disagree about the same commit.
SESSION_TRAILER = re.compile(r"Claude-Session:\s*\S*?(session_[A-Za-z0-9]+)")

#: GitHub's squash suffix. ``git log --format=%s`` on ``main`` shows
#: ``<subject> (#12072)`` for a squashed PR; that string IS the landing.
_PR_SUFFIX = "(#{number})"

SESSION_ID_RE = re.compile(r"^session_[A-Za-z0-9]+$")

# ── the three never-collapsed landing states ────────────────────────────────
LANDED = "landed"
NOT_LANDED = "not_landed"
COULD_NOT_LOOK = "could_not_look"
LANDING_STATES = (LANDED, NOT_LANDED, COULD_NOT_LOOK)

# ── the five sections, named once ───────────────────────────────────────────
SECTIONS = (
    ("A", "DECISIONS"),
    ("B", "SESSIONS SPAWNED"),
    ("C", "THE SESSION'S OWN WORK"),
    ("D", "TOTALS"),
    ("E", "ERRORS"),
)


class ReceiptError(RuntimeError):
    """A refusal. Raised rather than degraded — a receipt that quietly omits a
    section it could not build reads as a claim that the section is empty."""


# ═══════════════════════════════════════════════════════════════════════════
# The population-carrying count
# ═══════════════════════════════════════════════════════════════════════════

def counted(value: int, population: str) -> dict[str, Any]:
    """A number that cannot be written without its population.

    This is the mechanical half of `CLAUDE-RULES-CANONICAL.md` § "Always state
    the population". Everywhere else in the repo that rule is prose and is
    violated by sessions that have read it; here a bare int is not
    representable, so the denominator cannot be dropped by forgetting.
    """
    population = (population or "").strip()
    if not population:
        raise ReceiptError(
            f"a count of {value} was written with no population. "
            "State which rows, which window, which instrument — a number "
            "without its basis is not a finding."
        )
    return {"value": int(value), "population": population}


# ═══════════════════════════════════════════════════════════════════════════
# git — attribution and landing verification
# ═══════════════════════════════════════════════════════════════════════════

def _git(args: list[str], *, repo: pathlib.Path) -> tuple[bool, str]:
    """Run git. Returns (ok, output). A failure is REPORTED, never coerced to ''.

    Coercing a failed read to an empty string is how ``could_not_look`` becomes
    ``not_landed`` — the collapse this module exists to avoid.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - env
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "").strip()
    return True, proc.stdout


def attribute_commits(
    session_id: str,
    *,
    since: Optional[str] = None,
    ref: str = "origin/main",
    repo: pathlib.Path = REPO,
) -> dict[str, Any]:
    """Attribute commits on ``ref`` to ``session_id`` through the commit trailer.

    Returns the attributed set AND THE DENOMINATOR — the total commits scanned in
    the same window — because "349 commits" is not a claim and
    "349 of 545 on origin/main since 2026-09-11" is.

    ``unattributed`` is reported separately and is NOT folded into "another
    session's": a commit with no parseable trailer is one nobody has attributed,
    which is a different fact and a more interesting one.
    """
    if not SESSION_ID_RE.match(session_id or ""):
        raise ReceiptError(
            f"{session_id!r} is not a session id (expected ^session_[A-Za-z0-9]+$). "
            "Attribution keyed on a malformed id silently matches nothing, which "
            "renders as 'this session landed nothing'."
        )

    args = ["log", ref, "--no-merges", "--format=%H%x1f%s%x1f%b%x1e"]
    if since:
        args.insert(2, f"--since={since}")
    ok, out = _git(args, repo=repo)
    window = f"{ref}" + (f" since {since}" if since else " (full history in this clone)")
    if not ok:
        # We did not look. Never 0 — a zero here would read as "this session
        # landed nothing", which is the opposite of "we could not check".
        return {
            "state": COULD_NOT_LOOK,
            "error": out,
            "window": window,
            "attributed": [],
            "attributed_count": None,
            "scanned_count": None,
            "unattributed_count": None,
        }

    scanned = 0
    mine: list[dict[str, str]] = []
    unattributed = 0
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record.strip():
            continue
        parts = record.split("\x1f")
        if len(parts) < 3:
            continue
        sha, subject, body = parts[0].strip(), parts[1].strip(), parts[2]
        scanned += 1
        found = SESSION_TRAILER.search(body)
        if not found:
            unattributed += 1
            continue
        if found.group(1) == session_id:
            mine.append({"sha": sha[:9], "full_sha": sha, "subject": subject})

    return {
        "state": "measured",
        "window": window,
        "attributed": mine,
        "attributed_count": counted(
            len(mine), f"commits on {window} carrying a Claude-Session trailer naming {session_id}"
        ),
        "scanned_count": counted(scanned, f"all non-merge commits on {window}"),
        "unattributed_count": counted(
            unattributed, f"commits on {window} carrying NO parseable Claude-Session trailer"
        ),
    }


def verify_pr_landed(
    number: int, *, ref: str = "origin/main", repo: pathlib.Path = REPO
) -> dict[str, Any]:
    """Did PR ``number`` land on ``ref``? MEASURED, by the squash suffix.

    ⚠️ Deliberately NOT ``git merge-base --is-ancestor``. This repo squash-merges,
    so a merged PR's branch commits are never ancestors of ``main`` and the
    ancestry probe reports ``false`` for a PR that landed cleanly. That exact
    misreport happened on 2026-09-17 and is why this function exists.

    Three states, never collapsed: ``landed`` / ``not_landed`` / ``could_not_look``.
    """
    try:
        n = int(number)
    except (TypeError, ValueError):
        raise ReceiptError(f"{number!r} is not a PR number") from None

    suffix = _PR_SUFFIX.format(number=n)
    ok, out = _git(
        ["log", ref, "--format=%H%x1f%s", f"--grep={re.escape(suffix)}", "--fixed-strings",
         f"--grep={suffix}"],
        repo=repo,
    )
    if not ok:
        return {"pr": n, "state": COULD_NOT_LOOK, "error": out, "sha": None, "subject": None}

    for line in out.splitlines():
        if "\x1f" not in line:
            continue
        sha, subject = line.split("\x1f", 1)
        # --grep is a regex OR across the two patterns above; confirm the literal
        # suffix really is in the subject rather than trusting the match.
        if suffix in subject:
            return {
                "pr": n,
                "state": LANDED,
                "sha": sha[:9],
                "full_sha": sha,
                "subject": subject.strip(),
            }
    return {"pr": n, "state": NOT_LANDED, "sha": None, "subject": None}


def verify_prs(
    numbers: Iterable[int], *, ref: str = "origin/main", repo: pathlib.Path = REPO
) -> dict[str, Any]:
    """Verify a PR list and summarise it WITH its population."""
    rows = [verify_pr_landed(n, ref=ref, repo=repo) for n in numbers]
    landed = sum(1 for r in rows if r["state"] == LANDED)
    unread = sum(1 for r in rows if r["state"] == COULD_NOT_LOOK)
    return {
        "rows": rows,
        "landed": counted(landed, f"of {len(rows)} PR number(s) checked against {ref} by squash suffix"),
        "could_not_look": counted(
            unread, f"of {len(rows)} PR number(s) whose landing state could NOT be read"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# backlog ids — emitted verbatim, never typed
# ═══════════════════════════════════════════════════════════════════════════

def load_backlog_ids(*, repo: pathlib.Path = REPO) -> tuple[set[str], list[str]]:
    """Every id in every review backlog, plus the files that could not be read.

    The unreadable list is RETURNED rather than swallowed: resolving an id
    against a partially-loaded corpus and reporting "does not resolve" would be
    a false refusal.
    """
    ids: set[str] = set()
    unreadable: list[str] = []
    for rel in BACKLOG_RELPATHS:
        path = repo / rel
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            unreadable.append(f"{rel}: {type(exc).__name__}")
            continue
        for row in data.get("items") or []:
            rid = row.get("id")
            if isinstance(rid, str) and rid:
                ids.add(rid)
    return ids, unreadable


def expand_backlog_id(candidate: str, known: set[str]) -> str:
    """Return the FULL id, or raise.

    An author writing ``BL-20260917-RANKING-KEY-AB…`` means a real row. This
    completes a unique prefix and REFUSES anything else, so a truncation can
    never reach a committed artifact. ``check_backlog_refs`` fails a diff that
    introduces a reference resolving to nothing, so the alternative to refusing
    here is a red PR later — after the receipt has been written and read.
    """
    candidate = (candidate or "").strip().rstrip("….")
    if not candidate:
        raise ReceiptError("empty backlog id")
    if candidate in known:
        return candidate
    matches = sorted(i for i in known if i.startswith(candidate))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ReceiptError(
            f"{candidate!r} resolves to NO backlog row. A reference that resolves "
            "to nothing reads as tracked, so nobody re-checks it — that is worse "
            "than no reference at all (check_backlog_refs' own rationale). File "
            "the row, or drop the citation."
        )
    raise ReceiptError(
        f"{candidate!r} is ambiguous — {len(matches)} rows share that prefix: "
        + ", ".join(matches[:4])
        + ("…" if len(matches) > 4 else "")
    )


def resolve_backlog_id(candidate: str, *, repo: pathlib.Path = REPO) -> str:
    """:func:`expand_backlog_id` against the live backlogs."""
    known, unreadable = load_backlog_ids(repo=repo)
    if unreadable and not known:
        raise ReceiptError(
            "could not read any backlog file, so no id can be resolved: "
            + "; ".join(unreadable)
            + ". This is 'we did not look', NOT 'the id is bad'."
        )
    return expand_backlog_id(candidate, known)


# ═══════════════════════════════════════════════════════════════════════════
# the receipt
# ═══════════════════════════════════════════════════════════════════════════

def build_receipt(
    *,
    session_id: str,
    since: Optional[str] = None,
    role: str = "unstated",
    ref: str = "origin/main",
    prs: Optional[list[int]] = None,
    repo: pathlib.Path = REPO,
    generated_at: Optional[str] = None,
) -> dict[str, Any]:
    """Build the machine half of the receipt: the two MEASURED sections.

    Sections A (decisions), B's mandates, and E (errors) are NARRATIVE and cannot
    be derived — a session knows what it was told and what it got wrong; git does
    not. They ship as explicit empty scaffolds with a ``basis`` of
    ``author_must_complete`` rather than being omitted, so a receipt missing them
    is visibly incomplete instead of silently reading as "there were none".
    """
    from datetime import datetime, timezone

    stamp = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    attribution = attribute_commits(session_id, since=since, ref=ref, repo=repo)
    pr_check = verify_prs(prs or [], ref=ref, repo=repo)

    landed_prs = [r for r in pr_check["rows"] if r["state"] == LANDED]

    return {
        "schema_version": 1,
        "kind": "session_receipt",
        "session_id": session_id,
        "role": role,
        "generated_at": stamp,
        "window": attribution["window"],
        # ── A ────────────────────────────────────────────────────────────────
        "decisions": {
            "basis": "author_must_complete",
            "note": (
                "Operator decisions taken this session, each with the answer "
                "recorded VERBATIM, and an explicit note wherever the operator "
                "OVERRULED this session's recommendation. An empty list here is a "
                "claim that none were taken."
            ),
            "rows": [],
        },
        # ── B ────────────────────────────────────────────────────────────────
        "sessions_spawned": {
            "basis": "author_supplies_mandate__generator_measures_landing",
            "note": (
                "One row per spawned session. The MANDATE is the author's (git "
                "cannot know what a session was told); WHAT LANDED is measured by "
                "attributing that session's own commits. Aborted and respawned "
                "sessions are listed, never silently dropped."
            ),
            "rows": [],
        },
        # ── C ────────────────────────────────────────────────────────────────
        "own_work": {
            "basis": "measured",
            "attribution": attribution,
            "prs": pr_check,
            "landed_prs": [
                {"pr": r["pr"], "sha": r["sha"], "subject": r["subject"]} for r in landed_prs
            ],
        },
        # ── D ────────────────────────────────────────────────────────────────
        "totals": {
            "basis": "measured",
            "prs_landed_by_this_session": pr_check["landed"],
            "commits_attributed_to_this_session": attribution["attributed_count"],
            "commit_population": attribution["scanned_count"],
            "commits_unattributed": attribution["unattributed_count"],
            "sessions_spawned": counted(0, "rows in section B, which the author completes"),
        },
        # ── E ────────────────────────────────────────────────────────────────
        "errors": {
            "basis": "author_must_complete",
            "note": (
                "What this session got wrong and how it was corrected. NOT "
                "optional: a receipt with no errors section reads as a claim of "
                "none, and every receipt written so far has had entries here."
            ),
            "rows": [],
        },
    }


def render_markdown(receipt: dict[str, Any]) -> str:
    """The human-facing receipt. Same content, same order, every time."""
    out: list[str] = []
    sid = receipt.get("session_id", "?")
    out.append(f"# Session receipt — `{sid}`")
    out.append("")
    out.append(f"**Role:** {receipt.get('role', 'unstated')}  ")
    out.append(f"**Window:** {receipt.get('window', '?')}  ")
    out.append(
        f"**Verified at:** {receipt.get('generated_at', '?')} — every measured figure "
        "below was read from `origin/main` at that time, not transcribed from earlier "
        "in the session."
    )
    out.append("")

    def render_count(c: Any) -> str:
        if not isinstance(c, dict):
            return "_could not look_"
        return f"**{c['value']}** ({c['population']})"

    # A
    out.append("## A. DECISIONS")
    out.append("")
    rows = receipt.get("decisions", {}).get("rows") or []
    if not rows:
        out.append("_None recorded. An empty section here is a claim that no operator "
                   "decision was taken this session — not that the author skipped it._")
    for r in rows:
        out.append(f"- **{r.get('question','?')}** → _{r.get('answer','?')}_")
        if r.get("overruled"):
            out.append(f"  - ⚠️ **The operator OVERRULED this session's recommendation:** {r['overruled']}")
    out.append("")

    # B
    out.append("## B. SESSIONS SPAWNED")
    out.append("")
    rows = receipt.get("sessions_spawned", {}).get("rows") or []
    if not rows:
        out.append("_None._")
    else:
        out.append("| session | item | lane | spawned | state | mandate | what landed (MEASURED) | maps to |")
        out.append("|---|---|---|---|---|---|---|---|")
        for r in rows:
            out.append(
                "| `{s}` | {i} | {l} | {t} | {st} | {m} | {w} | {mp} |".format(
                    s=r.get("session_id", "?"), i=r.get("checklist_item", "—"),
                    l=r.get("lane", "—"), t=r.get("spawned_at", "—"),
                    st=r.get("lifecycle", "—"), m=r.get("mandate", "—"),
                    w=r.get("landed", "—"), mp=r.get("maps_to", "—"),
                )
            )
    out.append("")

    # C
    out.append("## C. THE SESSION'S OWN WORK")
    out.append("")
    own = receipt.get("own_work", {})
    attribution = own.get("attribution", {})
    if attribution.get("state") == COULD_NOT_LOOK:
        out.append(
            "⚠️ **Attribution could not be read** — `could_not_look`, which is NOT "
            f"the same as 'this session landed nothing'. Error: `{attribution.get('error')}`"
        )
    else:
        out.append(f"Commits attributed: {render_count(attribution.get('attributed_count'))}")
        out.append("")
        for c in attribution.get("attributed", [])[:50]:
            out.append(f"- `{c['sha']}` {c['subject']}")
    out.append("")
    pr_rows = own.get("prs", {}).get("rows") or []
    if pr_rows:
        out.append("**PRs, verified by squash suffix on `origin/main` — never by ancestry:**")
        out.append("")
        out.append("| PR | state | sha | subject |")
        out.append("|---|---|---|---|")
        for r in pr_rows:
            mark = {LANDED: "✅ landed", NOT_LANDED: "❌ not landed",
                    COULD_NOT_LOOK: "⚠️ could not look"}[r["state"]]
            out.append(f"| #{r['pr']} | {mark} | `{r.get('sha') or '—'}` | {r.get('subject') or '—'} |")
    out.append("")

    # D
    out.append("## D. TOTALS")
    out.append("")
    t = receipt.get("totals", {})
    out.append(f"- PRs landed by this session: {render_count(t.get('prs_landed_by_this_session'))}")
    out.append(f"- Commits attributed to this session: {render_count(t.get('commits_attributed_to_this_session'))}")
    out.append(f"- Commit population scanned: {render_count(t.get('commit_population'))}")
    out.append(f"- Commits carrying no parseable trailer: {render_count(t.get('commits_unattributed'))}")
    out.append(f"- Sessions spawned: {render_count(t.get('sessions_spawned'))}")
    out.append("")

    # E
    out.append("## E. ERRORS")
    out.append("")
    rows = receipt.get("errors", {}).get("rows") or []
    if not rows:
        out.append("_None recorded._ ⚠️ A receipt with no errors section reads as a claim "
                   "of none. Every receipt written to date has had entries here.")
    for r in rows:
        out.append(f"- **{r.get('what','?')}** — corrected by: {r.get('correction','?')}")
    out.append("")
    return "\n".join(out)


def receipt_path(session_id: str, *, repo: pathlib.Path = REPO) -> pathlib.Path:
    if not SESSION_ID_RE.match(session_id or ""):
        raise ReceiptError(f"{session_id!r} is not a session id; refusing to build a path from it")
    return repo / RECEIPTS_RELDIR / RECEIPT_FILENAME.format(session_id=session_id)


# ═══════════════════════════════════════════════════════════════════════════
# self-test — planted controls, no live repo state
# ═══════════════════════════════════════════════════════════════════════════

def _self_test() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        if not cond:
            failures.append(f"{name}: {detail}")

    # 1. a count cannot be written without a population
    try:
        counted(5, "")
        check("population-required", False, "counted() accepted an empty population")
    except ReceiptError:
        check("population-required", True)
    good = counted(5, "of 9 rows")
    check("population-carried", good == {"value": 5, "population": "of 9 rows"}, repr(good))

    # 2. backlog id expansion: exact, unique-prefix, dangling, ambiguous
    known = {
        "BL-20260917-RANKING-KEY-AB-WORKFLOW-SPINS",
        "BL-20260917-RANKING-KEY-ZZ-SOMETHING-ELSE",
        "BL-20260730-M1-PRICE-JOIN-DEAD",
    }
    check("id-exact", expand_backlog_id("BL-20260730-M1-PRICE-JOIN-DEAD", known)
          == "BL-20260730-M1-PRICE-JOIN-DEAD")
    check("id-prefix-unique",
          expand_backlog_id("BL-20260917-RANKING-KEY-AB…", known)
          == "BL-20260917-RANKING-KEY-AB-WORKFLOW-SPINS")
    try:
        expand_backlog_id("BL-99999999-NOPE", known)
        check("id-dangling-refused", False, "a dangling id was accepted")
    except ReceiptError:
        check("id-dangling-refused", True)
    try:
        expand_backlog_id("BL-20260917-RANKING-KEY-", known)
        check("id-ambiguous-refused", False, "an ambiguous prefix was accepted")
    except ReceiptError:
        check("id-ambiguous-refused", True)

    # 3. a malformed session id is refused rather than matching nothing silently
    try:
        attribute_commits("not-a-session")
        check("bad-session-id-refused", False, "a malformed session id was accepted")
    except ReceiptError:
        check("bad-session-id-refused", True)

    # 4. the three landing states are distinct and a failed read is not not_landed
    check("landing-states-distinct", len(set(LANDING_STATES)) == 3, str(LANDING_STATES))
    missing = pathlib.Path("/nonexistent-repo-for-selftest")
    r = verify_pr_landed(1, repo=missing)
    check("failed-read-is-could-not-look", r["state"] == COULD_NOT_LOOK,
          f"a git failure graded {r['state']!r}, collapsing 'we did not look' into 'did not land'")
    a = attribute_commits("session_01Selftest", repo=missing)
    check("failed-attribution-is-could-not-look", a["state"] == COULD_NOT_LOOK,
          f"graded {a['state']!r}")
    check("failed-attribution-count-is-null", a["attributed_count"] is None,
          "an unreadable attribution reported a count, which reads as a measurement")

    # 5. the markdown always carries all five sections, even when empty
    skeleton = {
        "session_id": "session_01Selftest", "role": "test", "window": "w",
        "generated_at": "2026-09-17T00:00:00Z",
        "decisions": {"rows": []}, "sessions_spawned": {"rows": []},
        "own_work": {"attribution": {"state": COULD_NOT_LOOK, "error": "x"}, "prs": {"rows": []}},
        "totals": {}, "errors": {"rows": []},
    }
    md = render_markdown(skeleton)
    for letter, name in SECTIONS:
        check(f"section-{letter}-present", f"## {letter}. {name}" in md, f"missing {letter}. {name}")
    check("could-not-look-surfaced-in-md", "could not be read" in md,
          "an unreadable attribution rendered as if it were a measurement")

    # 6. the receipt path is on the MANAGER surface
    check("path-on-manager-surface", RECEIPTS_RELDIR.startswith("docs/claude/work/"),
          f"{RECEIPTS_RELDIR} is off check_manager_scope's MANAGER_SURFACE, so a "
          "manager could not commit its own receipt")

    for f in failures:
        print(f"session-receipt selftest FAIL — {f}", file=sys.stderr)
    total = 14
    print(f"session-receipt: self-test {total - len(failures)}/{total} checks passed")
    return 1 if failures else 0


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--session-id", help="the session the receipt is about")
    p.add_argument("--since", help="window start for the commit population, e.g. 2026-09-11")
    p.add_argument("--ref", default="origin/main", help="the ref to measure against")
    p.add_argument("--role", default="unstated", help="manager | lane:<name> | ...")
    p.add_argument("--pr", type=int, action="append", default=[],
                   help="a PR number this session claims; repeatable")
    p.add_argument("--verify-prs", type=int, nargs="+", help="verify PR numbers and exit")
    p.add_argument("--resolve-id", help="expand a backlog id to its full form and exit")
    p.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    p.add_argument("--write", action="store_true",
                   help=f"also write the JSON to {RECEIPTS_RELDIR}/<session_id>.json")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args(argv)

    if args.self_test:
        return _self_test()

    try:
        if args.resolve_id:
            print(resolve_backlog_id(args.resolve_id))
            return 0

        if args.verify_prs:
            result = verify_prs(args.verify_prs)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                for r in result["rows"]:
                    print(f"#{r['pr']}: {r['state']}"
                          + (f"  {r['sha']}  {r['subject']}" if r.get("sha") else ""))
                print(f"landed {result['landed']['value']} {result['landed']['population']}")
            return 0

        if not args.session_id:
            p.error("--session-id is required (or use --verify-prs / --resolve-id / --self-test)")

        receipt = build_receipt(
            session_id=args.session_id, since=args.since, role=args.role,
            ref=args.ref, prs=args.pr,
        )
        if args.write:
            path = receipt_path(args.session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
            print(f"session-receipt: wrote {path.relative_to(REPO)}", file=sys.stderr)
        print(json.dumps(receipt, indent=2) if args.json else render_markdown(receipt))
        return 0
    except ReceiptError as exc:
        print(f"session-receipt: REFUSED — {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
