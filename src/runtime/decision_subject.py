"""Does the thing a decision request ASKS ABOUT still exist?

MI-258. A decision request kept rendering on the operator's Workflow page as
work waiting on them for MORE THAN A DAY after its subject had been deleted:
``DR-20260908-CLEAR-THE-LOUD-TRADE-PRIORITISATION-ROW`` asks whether to remove
``OI-20260831-TRADE-PRIORITISATION-IS-LIVE-BUT-UNPROVEN-AND-ITS-AB-HARNESS-DOES-NOT-EXIST``,
and that row left ``docs/claude/OPEN-ITEMS.json`` on 2026-09-09 in ``ba5fccc1a``
(#11509). A perfectly graded question about a deleted row is still a question
that should not be on the operator's screen.

⚠️ THIS IS NOT MI-254 AND IS NOT A WIDENING OF IT. MI-254 taught the inbox that
a conversationally-recorded verdict is an ANSWER — it fixed how an answer state
is graded. This asks a different question one level up: *is there still anything
to answer about?* A request can be perfectly ungraded-as-answered and perfectly
moot at the same time.

────────────────────────────────────────────────────────────────────────────
WHY THE SUBJECT MUST BE **DECLARED** AND IS NEVER SCRAPED FROM THE PROSE
────────────────────────────────────────────────────────────────────────────
The obvious implementation — regex the question/context for ``OI-…``/``BL-…``
ids and paths, resolve what you find — was BUILT AS A PROBE FIRST and MEASURED
before any of it was shipped, and the measurement is why none of it is here.

POPULATION: all 26 ``decision_requests`` across the 21 objects that carry any,
out of 710 ``docs/claude/work/objects/*.yaml``, read 2026-09-11 at ``de46c805e``.

    requests the prose scan flagged as having a vanished subject ...  4
      of which FALSE ...........................................  3  (75%)
      of which TRUE ............................................  1
    requests carrying no extractable reference at all ..........  13  (50%)

The three false positives, each verified individually rather than assumed:

  * ``DEC-20260903-SUNSET-DISPOSITION-POLICY`` — the scan extracted
    ``OI-20260831-PER-ACCOUNT-`` and graded it gone. The id is TRUNCATED BY A
    YAML LINE WRAP mid-token (``WO-20260903-SUNSET-DISPOSITIONS-OWED.yaml``
    lines 57→58). The real row,
    ``OI-20260831-PER-ACCOUNT-ARBITRATION-SHIPPED-NOT-YET-ARMED-OR-EXERCISED``,
    is LIVE. The same request mentions it a second time in the abbreviated form
    ``OI-20260831-PER-ACCOUNT-ARBITRATION)``, which would have produced a second
    false ``gone`` from the same paragraph.
  * two ``path`` hits — ``…-2026-09-09.md.`` and ``…-2026-09-02.md.`` — were
    sentence-ending PERIODS captured into the path. Both files EXIST.

A 75% false-positive rate on a signal that REMOVES a question from the
operator's screen is not a tolerable instrument: the failure direction is
hiding a real decision. And covering half the population would have made the
silence on the other half read as "nothing is moot here". So the subject is a
DECLARED, TYPED field or it is nothing, and the absence of a declaration is
itself a published state rather than an assumption in either direction.

────────────────────────────────────────────────────────────────────────────
THE FOUR STATES, AND WHY NONE OF THEM COLLAPSES INTO ANOTHER
────────────────────────────────────────────────────────────────────────────
``subject_live``        a declared subject was resolved and still exists.
``subject_gone``        a declared subject was resolved and does NOT exist.
``subject_unknown``     a subject WAS declared and we could NOT resolve it —
                        an unreadable register, a kind with no offline
                        resolver (a PR, a branch), or an ambiguous ref.
                        *We did not look*, and it must never default to
                        ``gone`` (that silently hides a real question) nor to
                        ``live`` (that reproduces the bug MI-258 is about).
``subject_undeclared``  nobody wrote a subject down. ⚠️ DELIBERATELY NOT THE
                        SAME AS ``unknown``: "we tried to check and could not"
                        and "there is nothing here to check" are opposite
                        findings, and today they have opposite remedies —
                        the first is a resolver gap, the second is an author
                        gap. Measured at 25 of 26 requests on 2026-09-11, so
                        pooling them would report a 96% resolution FAILURE for
                        a resolver that was never asked to resolve anything.

They are branched on and not merely counted: ``subject_gone`` is the only state
that withdraws a request from the operator's work list, and ``subject_unknown``
is the only one that raises a resolution warning — ``undeclared`` raises none,
because nothing failed.

────────────────────────────────────────────────────────────────────────────
WHAT THIS MODULE REFUSES TO DO
────────────────────────────────────────────────────────────────────────────
* **It never fetches.** ``pull_request`` and ``branch`` subjects cannot be
  settled from the working tree, so they grade ``subject_unknown`` and SAY SO.
  Reaching the network on an API request path is the wedge class this repo has
  paid for twice (BL-20260609-001, and the 2026-06-10 cascade), and it is the
  same refusal ``work.py``'s freshness block makes about ``git fetch``.
* **It never deletes or hides a request.** It publishes a state; the caller
  sorts. MI-258: *a question that was asked and became moot is a record, and
  the reason it became moot is the useful part.*
* **It never grades ``gone`` on a read failure.** An unreadable register is
  ``unknown``. Inverting that is precisely the closed-flat-invariant defect
  (`OI-20260909-CLOSED-FLAT-INVARIANT-…`), where a failed read graded as the
  reassuring value and the falsifier was silenced by the condition it existed
  to catch.
* **It never matches by prefix or substring.** Exact id equality only. The
  truncation above is what a prefix match looks like when it is wrong, and a
  ref that is a strict prefix of exactly one live id grades ``unknown`` — NOT
  ``live`` (we cannot show the author meant that row) and NOT ``gone`` (we
  cannot show they did not).

⚠️ ``lifecycle: done`` ON THE PARENT OBJECT IS NOT CONSULTED HERE, on purpose.
MI-258 is explicit that it is EVIDENCE, not the test: an object can be done
while a decision it raised is genuinely still open, and suppressing on that
alone would hide real questions. ``work.py`` already publishes
``objectLifecycle`` beside every request, so the operator sees it without this
module treating it as an answer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "SUBJECT_LIVE",
    "SUBJECT_GONE",
    "SUBJECT_UNKNOWN",
    "SUBJECT_UNDECLARED",
    "SUBJECT_STATES",
    "SUBJECT_KINDS",
    "OFFLINE_UNRESOLVABLE_KINDS",
    "SUBJECT_STATE_NOTES",
    "normalise_subject",
    "grade_subject",
    "is_actionable",
    "SubjectResolver",
]

SUBJECT_LIVE = "subject_live"
SUBJECT_GONE = "subject_gone"
SUBJECT_UNKNOWN = "subject_unknown"
SUBJECT_UNDECLARED = "subject_undeclared"

#: Order is the attention order the caller may rely on, not alphabetical.
SUBJECT_STATES: tuple[str, ...] = (
    SUBJECT_LIVE,
    SUBJECT_UNDECLARED,
    SUBJECT_UNKNOWN,
    SUBJECT_GONE,
)

#: Every kind a `subject` block may declare. A kind NOT in here is a typo or a
#: shape nobody taught this module, and grades `unknown` rather than being
#: guessed at from the ref's spelling — a guess is how `OI-20260831-PER-ACCOUNT-`
#: became a confident `gone`.
SUBJECT_KINDS: tuple[str, ...] = (
    "open_item",
    "checklist_item",
    "backlog_row",
    "work_object",
    "decision_request",
    "path",
    "pull_request",
    "branch",
)

#: Kinds that are REAL and that this module deliberately cannot settle from the
#: working tree. They grade `unknown` with a stated reason — never `live` (which
#: would be a claim we did not check) and never `gone`.
OFFLINE_UNRESOLVABLE_KINDS: frozenset[str] = frozenset({"pull_request", "branch"})

SUBJECT_STATE_NOTES: dict[str, str] = {
    SUBJECT_LIVE: "The thing this asks about still exists.",
    SUBJECT_GONE: (
        "The thing this asks about no longer exists, so there is nothing left "
        "to decide. Kept on the page as a record — the reason it became moot "
        "is the useful part."
    ),
    SUBJECT_UNKNOWN: (
        "A subject was declared and could NOT be resolved. This is *we did not "
        "look*, not *nothing is there* — the question stays on the list."
    ),
    SUBJECT_UNDECLARED: (
        "No subject was declared, so there was nothing to check. Not a "
        "resolution failure: the question stays on the list."
    ),
}

_BACKLOG_FILES: tuple[str, ...] = (
    "docs/claude/health-review-backlog.json",
    "docs/claude/performance-review-backlog.json",
    "docs/claude/ml-review-backlog.json",
    "docs/claude/research-review-backlog.json",
)


def normalise_subject(raw: Any) -> dict[str, Any] | None:
    """The declared `subject` block, or ``None`` when the row declares none.

    A block that is present but unusable (no ``ref``, or a ``kind`` this module
    has never heard of) is NOT dropped to ``None``: dropping it would report an
    author's broken declaration as *nobody declared one*, and those have
    different remedies. It is returned with whatever it said, and
    :func:`grade_subject` grades it ``unknown``.
    """
    if not isinstance(raw, dict):
        return None
    ref = raw.get("ref")
    kind = raw.get("kind")
    if not isinstance(ref, str) or not ref.strip():
        if not isinstance(kind, str) or not kind.strip():
            return None
        ref = ""
    return {
        "kind": kind.strip() if isinstance(kind, str) and kind.strip() else None,
        "ref": ref.strip(),
        # Free prose from the author saying WHY this is the subject. Carried
        # verbatim so a `gone` verdict can be audited against the intent.
        "note": raw.get("note") if isinstance(raw.get("note"), str) else None,
    }


class SubjectResolver:
    """Resolves a declared subject against the repo's canonical registers.

    One instance per request-inbox build: every register is read AT MOST ONCE
    and cached, because the inbox grades every request in the store and
    re-reading a 1691-row backlog per request would put real cost on an API
    path for no new information.

    Every loader returns ``None`` for *could not read* and a set for *read it*.
    The two are never conflated — an empty set is a real (and alarming) reading
    of a register; ``None`` is the absence of one.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = Path(root) if root is not None else Path.cwd()
        self._cache: dict[str, frozenset[str] | None] = {}
        #: Set when at least one work-object file could not be parsed. A
        #: partial register may answer `live` (finding it is proof it is
        #: there) but may NEVER answer `gone`.
        self._work_partial = False
        self._work_partial_count = 0

    # ── loaders ──────────────────────────────────────────────────────────────

    def _json_ids(self, relpaths: Iterable[str]) -> frozenset[str] | None:
        """Union the ``id`` of every row across one or more JSON registers.

        ⚠️ If ANY named file fails to read, the whole answer is ``None``. A
        partial union would let a row that lives in the unread file grade
        ``gone`` — the read-failure-as-reassuring-value inversion this module
        exists to refuse.
        """
        out: set[str] = set()
        seen_any = False
        for rel in relpaths:
            path = self._root / rel
            if not path.exists():
                # A register that is not present at all is a different fact
                # from one we could not parse, but both are "we cannot say
                # whether the row is there", so both refuse.
                return None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
            rows = data if isinstance(data, list) else (
                data.get("items") or data.get("open_items") or data.get("backlog") or []
            )
            if not isinstance(rows, list):
                return None
            seen_any = True
            for row in rows:
                if isinstance(row, dict) and isinstance(row.get("id"), str):
                    out.add(row["id"])
        return frozenset(out) if seen_any else None

    def _work_ids(self) -> frozenset[str] | None:
        """Every work-object id, and every decision-request id, in one pass.

        Cached under two keys by :meth:`_universe`; the directory is walked once.
        """
        directory = self._root / "docs/claude/work/objects"
        if not directory.is_dir():
            return None
        try:
            import yaml  # local import: the API process already has it, but a
            # caller that only wants path/open_item resolution should not pay
            # an import it never uses.
        except Exception:
            return None
        objects: set[str] = set()
        requests: set[str] = set()
        any_parsed = False
        failures = 0
        for path in sorted(directory.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:
                # ⚠️ ONE unparseable object DOES degrade the whole register's
                # authority, and that is recorded rather than shrugged off. The
                # store is one-file-per-object so a bad file is local — but the
                # id we are asked about may be exactly the one inside it, and
                # "I read 709 of 710 files and did not find it" is NOT evidence
                # of absence. `_work_partial` carries that, and `resolve`
                # refuses to say `gone` while it is set.
                failures += 1
                continue
            any_parsed = True
            if isinstance(data, dict):
                if isinstance(data.get("id"), str):
                    objects.add(data["id"])
                for req in data.get("decision_requests") or []:
                    if isinstance(req, dict) and isinstance(req.get("id"), str):
                        requests.add(req["id"])
        if not any_parsed:
            return None
        self._work_partial = failures > 0
        self._work_partial_count = failures
        self._cache["work_object"] = frozenset(objects)
        self._cache["decision_request"] = frozenset(requests)
        return self._cache["work_object"]

    def _universe(self, kind: str) -> frozenset[str] | None:
        if kind in self._cache:
            return self._cache[kind]
        if kind == "open_item":
            value = self._json_ids(["docs/claude/OPEN-ITEMS.json"])
        elif kind == "checklist_item":
            value = self._json_ids(["docs/claude/work/MANAGER-CHECKLIST.json"])
        elif kind == "backlog_row":
            value = self._json_ids(_BACKLOG_FILES)
        elif kind in ("work_object", "decision_request"):
            self._work_ids()
            return self._cache.get(kind)
        else:
            value = None
        self._cache[kind] = value
        return value

    # ── the one public verb ──────────────────────────────────────────────────

    def resolve(self, subject: dict[str, Any]) -> tuple[str, str]:
        """``(state, basis)`` for one declared subject. Never raises."""
        kind = subject.get("kind")
        ref = subject.get("ref") or ""
        if not kind:
            return SUBJECT_UNKNOWN, "the subject block declares no `kind`"
        if kind not in SUBJECT_KINDS:
            return SUBJECT_UNKNOWN, f"`{kind}` is not a kind this resolver knows"
        if not ref:
            return SUBJECT_UNKNOWN, f"the `{kind}` subject declares no `ref`"
        if kind in OFFLINE_UNRESOLVABLE_KINDS:
            return SUBJECT_UNKNOWN, (
                f"a `{kind}` cannot be settled from the working tree, and this "
                "route deliberately performs no network call"
            )
        if kind == "path":
            # A path is resolved against the tree this process is serving, so
            # the answer is about THIS checkout — stated, because on the live VM
            # that tree lags `main` by up to one `ict-git-sync` pull.
            return (
                (SUBJECT_LIVE, f"`{ref}` exists in the served working tree")
                if (self._root / ref).exists()
                else (SUBJECT_GONE, f"`{ref}` is absent from the served working tree")
            )
        universe = self._universe(kind)
        if universe is None:
            return SUBJECT_UNKNOWN, (
                f"the register behind `{kind}` could not be read, so we cannot "
                "say whether the subject is there"
            )
        if ref in universe:
            return SUBJECT_LIVE, f"`{ref}` is present in the `{kind}` register"
        # ⚠️ THE AMBIGUITY RULE. An exact miss that is a STRICT PREFIX of a live
        # id is the shape a truncated reference takes, and truncation is
        # MEASURED on this very corpus: a YAML line wrap cut
        # `OI-20260831-PER-ACCOUNT-ARBITRATION-…` mid-token, and a naive reader
        # graded the live row `gone`. We cannot show the author meant that row,
        # and we cannot show they did not, so neither confident answer is
        # available and the question stays on the operator's list.
        if kind in ("work_object", "decision_request") and self._work_partial:
            return SUBJECT_UNKNOWN, (
                f"`{ref}` was not found, but {self._work_partial_count} work "
                "object file(s) could not be parsed — a partial register "
                "cannot establish an absence"
            )
        near = sorted(i for i in universe if i.startswith(ref) and i != ref)
        if near:
            return SUBJECT_UNKNOWN, (
                f"`{ref}` matches no `{kind}` exactly but is a prefix of "
                f"{len(near)} live id(s) (e.g. `{near[0]}`) — it looks "
                "truncated, and a truncated ref cannot be graded either way"
            )
        return SUBJECT_GONE, f"`{ref}` is absent from the `{kind}` register"


def grade_subject(
    request: dict[str, Any],
    resolver: SubjectResolver | None = None,
) -> dict[str, Any]:
    """Grade one normalised request. Always returns all four keys.

    A key that VANISHES makes a consumer branch on absence, and absence is not
    one of the states — the same contract every other field on this route keeps.
    """
    subject = request.get("subject")
    if not isinstance(subject, dict):
        return {
            "subject": None,
            "subjectState": SUBJECT_UNDECLARED,
            "subjectBasis": "the request declares no `subject`",
            "subjectNote": SUBJECT_STATE_NOTES[SUBJECT_UNDECLARED],
        }
    res = resolver or SubjectResolver()
    try:
        state, basis = res.resolve(subject)
    except Exception as exc:  # pragma: no cover - defensive
        # A resolver that blows up must not take the inbox down, and must not
        # grade `gone` on its way out.
        state, basis = SUBJECT_UNKNOWN, f"the resolver raised: {exc!r}"
    return {
        "subject": subject,
        "subjectState": state,
        "subjectBasis": basis,
        "subjectNote": SUBJECT_STATE_NOTES[state],
    }


def is_actionable(*, settled: bool, subject_state: str) -> bool:
    """Is this a question the operator should see as WORK right now?

    Published from the bot side deliberately. The SPA previously filtered its
    "Waiting on you" list in TypeScript and that became a SECOND definition of
    "answered", free to drift from the one this repo's guards read — the same
    mistake as merging `state` and `status` in the renderer. One owner.

    ⚠️ ONLY ``subject_gone`` WITHDRAWS A QUESTION. ``unknown`` and
    ``undeclared`` both keep it, because neither is evidence the subject is
    gone, and the failure direction that costs something is hiding a live
    decision — not showing a moot one.
    """
    if settled:
        return False
    return subject_state != SUBJECT_GONE
