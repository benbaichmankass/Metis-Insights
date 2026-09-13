#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py (open-items-guard)
"""Keep ``docs/claude/OPEN-ITEMS.json`` SHORT, WORKABLE and HONEST.

WHY THIS EXISTS
---------------
Operator directive, 2026-08-26: *"we need to improve the mechanisms for
following up on items that need to be resolved/verified across sessions —
there needs to be some sort of log that new sessions know to check to see what
open items they need to be aware of, whether for verification/updates or just
to know about processes going on in the background that could affect their
work."*

The register is the log. **This guard is what stops it becoming the thing it
replaced.** `docs/claude/health-review-backlog.json` is 951 rows and 5.1 MB —
nobody reads that at session start, which is precisely why items were being
lost between sessions. A register nobody reads is worse than none: it *looks*
like the follow-up mechanism exists.

⚠️ **THE CAP IS GONE, and this paragraph used to argue for it.** It read "the
cap is the feature, not a limitation of it. Adding a 13th item means clearing
one first, and that pressure is the whole design" — describing a `MAX_ITEMS`
that was set to `None` on 2026-08-26 by operator direction (*"we don't want to
cap the number of bugs we can track, we want to ensure that they are actually
being tracked, fixed, and learned from"*). See the `MAX_ITEMS` comment below
for the reasoning; FIELD BEATS COMMENT, and this comment was the field's
loudest contradiction. Corrected 2026-08-29 by /system-review, together with
the same false claim in `CLAUDE.md`'s SESSION BRIEF. It is not a cosmetic
edit: a session that believes a cap is enforced either declines to file a row
it should file, or DELETES a live row to make room — a register of known
problems that deletes knowledge to stay short is the bandaid the operator
removed.

WHAT BOUNDS THE REGISTER INSTEAD is that a `monitoring` row must be RE-OBSERVED
on its own cadence: it cannot be carried by doing nothing.

TWO CHECKS, and each maps to a way the register dies:
* **workability**  — a row with no `clears_when` names no observable end
                     condition, so nobody can ever tell it is finished and it
                     is carried forever. Same failure `check_backlog_criteria`
                     exists for, and the same remedy.
* **staleness**    — rows silently outlive their relevance and the register
                     becomes a museum. A row older than `_STALE_DAYS` must be
                     re-affirmed (bump `reaffirmed`) or cleared. Re-affirming
                     is cheap; that is the point — the cost is *looking*, not
                     typing.

⚠️ **This guard does NOT check whether an item is resolved.** It cannot: the
whole class of item here is one whose resolution is only observable on the live
fleet. A guard that pretended otherwise would be cheaper to satisfy than to
honour, which is the `new-table-wiring-guard` lesson (a presence-only marker
made the cheapest way to silence a real finding *naming a table that does not
exist*).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _git_base  # noqa: E402  — the ONE owner of "resolve a base ref honestly"

_REGISTER = Path("docs/claude/OPEN-ITEMS.json")

#: DELIBERATELY NO CAP (operator-directed 2026-08-26: "we don't want to cap the
#: number of bugs we can track, we want to ensure that they are actually being
#: tracked, fixed, and learned from"). An earlier version capped this at 12 and
#: that was a bandaid: it bounded the LIST rather than making anything get read
#: or fixed, and a cap on a register of KNOWN PROBLEMS just deletes knowledge.
#: What bounds the register instead is that a `monitoring` row must be
#: RE-OBSERVED on its own cadence — it cannot be carried by doing nothing.
MAX_ITEMS = None

#: How long a row may sit without being re-affirmed. Chosen, not measured:
#: long enough that an ordinary week of sessions does not churn it, short
#: enough that a row cannot quietly outlive a milestone.
_STALE_DAYS = 21

_REQUIRED = ("id", "opened", "kind", "summary", "clears_when")
#: `monitoring` is the enforced kind: it must be re-OBSERVED on a cadence and is
#: rendered into CLAUDE.md when due. The others are context a session should
#: know but need not act on.
_KINDS = {"monitoring", "awaiting_verification", "background_awareness",
          "pending_decision"}


def _parse_day(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def check(path: Path, today: date | None = None) -> list[str]:
    """Return a list of human-readable problems. Empty == clean."""
    today = today or datetime.now(timezone.utc).date()
    problems: list[str] = []

    if not path.is_file():
        return [f"{path} is MISSING. It is named in CLAUDE.md § 'Every session' "
                f"as the first thing a session reads; a session that cannot "
                f"find it has no follow-up surface at all."]
    # ⚠️ DUPLICATE KEYS FIRST, AND BEFORE ANY ORDINARY PARSE, BECAUSE AN
    # ORDINARY PARSE CANNOT SEE THEM. `json.loads` keeps the LAST value for a
    # repeated key and discards the earlier ones silently, so a row carrying
    # `"observation": "<1746 chars a session measured>"` followed by
    # `"observation": ""` reads as NEVER OBSERVED to this guard, to the session
    # brief, and to every human — while the work was done and written down.
    #
    # MEASURED 2026-09-12 on OI-20260904-MANAGER-WAKE-BUILT-AND-ITS-SCHEDULER-
    # DOES-NOT-EXIST: exactly that, 1746 characters opening "MEASURED BY
    # CREATING AND FIRING IT, after five sessions asserted without testing",
    # invisible behind a trailing empty duplicate.
    #
    # This file does NOT round-trip (manager_preflight.py records that a naive
    # dump rewrites essentially the whole file), so every edit to it is a
    # SPLICE — and a splice that adds a key beside an existing one produces a
    # file that PARSES and changes NOTHING. That is why this is a guard and not
    # a note: the failure is invisible at exactly the moment it happens.
    dup_problems: list[str] = []

    def _dup_hook(pairs: list[tuple[str, object]]) -> dict:
        obj = dict(pairs)
        counts: dict[str, int] = {}
        for k, _v in pairs:
            counts[k] = counts.get(k, 0) + 1
        repeated = sorted(k for k, n in counts.items() if n > 1)
        if repeated:
            who = str(obj.get("id") or "<an object with no id>")
            dup_problems.append(
                f"{who}: DUPLICATE KEY(S) {repeated} — JSON keeps the LAST "
                f"value and silently discards the earlier one(s), so whatever "
                f"was written first is invisible to every reader. Fill the "
                f"existing field; never add a second one."
            )
        return obj

    try:
        data = json.loads(path.read_text(encoding="utf-8"),
                          object_pairs_hook=_dup_hook)
    except (json.JSONDecodeError, OSError) as exc:
        return [f"{path} did not parse: {exc}"]
    problems.extend(dup_problems)

    items = data.get("items")
    if not isinstance(items, list):
        return [f"{path}: 'items' is not a list"]

    seen: set[str] = set()
    for i, row in enumerate(items):
        if not isinstance(row, dict):
            problems.append(f"item[{i}] is not an object")
            continue
        rid = str(row.get("id") or f"<no id, index {i}>")

        for field in _REQUIRED:
            val = row.get(field)
            if not isinstance(val, str) or not val.strip():
                problems.append(f"{rid}: missing or empty '{field}'")

        if rid in seen:
            problems.append(f"{rid}: duplicate id")
        seen.add(rid)

        kind = row.get("kind")
        if isinstance(kind, str) and kind not in _KINDS:
            problems.append(
                f"{rid}: kind '{kind}' is not one of {sorted(_KINDS)}")

        clears = row.get("clears_when")
        if isinstance(clears, str) and clears.strip():
            # A clears_when that restates the fix is not an observable
            # condition. This catches the laziest form only, deliberately —
            # a checker that tried to judge observability would be guessing.
            lowered = clears.strip().lower()
            if lowered in ("the fix works", "it is fixed", "resolved", "done",
                           "when it works", "n/a", "tbd"):
                problems.append(
                    f"{rid}: clears_when '{clears}' names no observable "
                    f"condition — nobody can tell when this row is finished, "
                    f"so it will be carried forever")

        if row.get("kind") == "monitoring":
            # A monitoring row is only worth anything if it records WHAT WAS SEEN.
            # `loud: true` used to stand here and it enforced nothing — it was an
            # adjective, and an alarm nobody must answer is one more alarm to walk
            # past (operator, 2026-08-26). The cadence + observation pair is what
            # makes carrying the row cost an honest look.
            try:
                every = int(row.get("check_every_days"))
            except (TypeError, ValueError):
                every = 0
            if every <= 0:
                problems.append(
                    f"{rid}: kind 'monitoring' needs a positive 'check_every_days' "
                    f"— without a cadence it can be carried forever by doing nothing")
            obs = row.get("observation")
            if not isinstance(obs, str) or len(obs.strip()) < 40:
                problems.append(
                    f"{rid}: kind 'monitoring' needs an 'observation' saying what was "
                    f"actually SEEN at the last check. A claim of progress is not an "
                    f"observation, and an empty one makes the cadence decorative")

        opened = _parse_day(row.get("opened"))
        reaffirmed = _parse_day(row.get("reaffirmed")) or _parse_day(row.get("verified_at"))
        anchor = reaffirmed or opened
        if anchor is None:
            problems.append(
                f"{rid}: 'opened' is not a readable date, so staleness cannot "
                f"be judged — that is 'we did not look', not 'it is fresh'")
        else:
            age = (today - anchor).days
            if age > _STALE_DAYS:
                problems.append(
                    f"{rid}: {age} days since it was last affirmed (limit "
                    f"{_STALE_DAYS}). Re-check it and set 'reaffirmed' to "
                    f"today, or clear the row. The cost is LOOKING, not typing."
                )
    return problems


# ── Does THIS DIFF shorten a row's own observation? ───────────────────────
#
# WHY THIS EXISTS, and why it is a guard rather than a note. On 2026-09-11 a
# session intending to APPEND a third reading to one row's `observation`
# REPLACED it: 3377 characters became 2628 with ZERO characters of the prior
# text surviving, and what was destroyed included ANOTHER session's correction
# whose own first sentence said it must be read before the text under it.
# `BL-20260911-AN-OPEN-ITEMS-SPLICE-CAN-REPLACE-THE-OBSERVATION-IT-MEANT-TO-EXTEND-AND-EVERY-PROOF-THE-REPO-HAS-PASSES-OVER-IT`
#
# ⚠️ THE FINDING IS THE VERIFICATION, NOT THE WRITE. All three instruments a
# session is told to use reported CLEAN over that diff: the id-set fingerprint
# proof ("80 other rows verified byte-identical, 81 items" — true, and by
# construction excluding the one row being edited), `run_guards --base main`
# (PASS 58 / FAIL 0), and this very guard ("every one workable and affirmed"),
# because an overwritten observation is still a NON-EMPTY observation. The id
# cardinality was 81 on both sides, so nothing keyed on ids or counts could see
# it either.
#
# ⚠️ NOT A ONE-OFF, MEASURED RATHER THAN ASSUMED. Population: all 233 commits
# touching the register on `main` as of 2026-09-13, of which 232 parent/child
# pairs were gradeable (1 unreadable), comparing every row present on BOTH
# sides. Base observation text was lost in **38 of 232 commits (16.4%)**,
# affecting **82 rows**; 36 of those were a net shrink, the largest dropping
# **15,720 characters** (16,989 → 1,269, at `566c0ff4c`) — an observation
# carrying several sessions' dated addenda replaced by one fresh reading.
#
# ⚠️ THIS IS NOT `register-field-loss-guard`, AND I CHECKED BEFORE BUILDING IT.
# `scripts/ci/check_register_field_loss.py` already grades the shared registers
# for a DISAPPEARED field, row or top-level key — and its own docstring rules
# this case out in as many words: *"A CHANGED VALUE IS NOT A LOSS AND IS NOT
# GRADED. Editing a field is ordinary work. Only DISAPPEARANCE is the signature
# of a merge that dropped somebody's write-back."* That is the right line for
# that guard to draw; the 2026-09-11 splice is the case on the other side of
# it, where the key survives and its CONTENT does not. The two overlap on
# exactly one input — deleting `observation` outright — and both catch it,
# which is cheap insurance rather than duplication.
#
# ⚠️ AN APPEND-ONLY RULE WOULD BE WRONG and this is not one. Rows legitimately
# get corrected and superseded in place all over this file. What is refused is
# a SILENT loss: say so in the text and it passes.
#
# ⚠️ WHAT THIS DOES NOT DECIDE, SAID RATHER THAN LEFT TO BE DISCOVERED: how
# LONG an observation may get. Preferring the append leaves the field growing,
# and one already reached 16,989 characters before somebody cut it. That is a
# real tension and this rule does not resolve it — it only makes the cut a
# DECLARED act instead of an invisible one. A compaction policy is a decision,
# not a guard.
# `BL-20260913-THE-OBSERVATION-PRESERVATION-RULE-PREFERS-APPENDING-AND-NOTHING-BOUNDS-HOW-LONG-AN-OBSERVATION-MAY-GET`
OBS_PRESERVED = "preserved"
OBS_LOST = "lost"
OBS_SUPERSEDED = "superseded_declared"
#: The row is not in the base at all, or carried no observation there — there
#: is nothing to lose. NOT the same as "we compared and it was fine".
OBS_NOTHING_TO_LOSE = "nothing_to_lose"

MARKER_NONE = "none"
MARKER_STALE = "stale"
MARKER_THIN = "thin"
MARKER_DECLARED = "declared"

#: The declaration. VERIFIED, never presence-only — the `# inert:` and
#: `# provenance:` markers in this repo both had to name what they excused
#: before they were worth anything, and a marker cheaper to lie to than to
#: satisfy is worse than no marker (the `new-table-wiring-guard` lesson).
_MARKER_RE = re.compile(
    r"SUPERSEDES-OBSERVATION\s+(\d{4}-\d{2}-\d{2})\s*:\s*(\S[^\n]*)")
#: How old the marker's own date may be. A PR can legitimately sit a day or
#: two; what this stops is a marker written weeks ago silently licensing
#: today's deletion, which is how an override becomes ambient.
_MARKER_FRESH_DAYS = 3
#: A reason short enough to be a shrug is not a reason.
_MARKER_MIN_REASON = 24


def _norm(text: object) -> str:
    """Whitespace-collapsed comparison form.

    ⚠️ Deliberately NOT byte-exact. Preserved text that was re-wrapped or
    re-indented is still preserved, and failing on that would train sessions to
    reach for the marker to silence a non-finding. Collapsing whitespace cannot
    hide a DELETION: removed words are still removed.
    """
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text).strip()


def marker_state(text: object, today: date) -> tuple[str, str]:
    """`(state, detail)` for the supersession declaration in `text`."""
    hits = _MARKER_RE.findall(text if isinstance(text, str) else "")
    if not hits:
        return MARKER_NONE, ""
    stale: list[str] = []
    for raw_day, reason in hits:
        day = _parse_day(raw_day)
        if day is None or abs((today - day).days) > _MARKER_FRESH_DAYS:
            stale.append(raw_day)
            continue
        if len(reason.strip()) < _MARKER_MIN_REASON:
            return MARKER_THIN, reason.strip()
        return MARKER_DECLARED, f"{raw_day}: {reason.strip()[:80]}"
    return MARKER_STALE, ", ".join(stale)


def observation_delta(base_row: object, head_row: object,
                      today: date) -> tuple[str, str]:
    """`(state, detail)` — did the head row keep the base row's observation?"""
    base_obs = _norm((base_row or {}).get("observation") if isinstance(base_row, dict) else None)
    if not base_obs:
        return OBS_NOTHING_TO_LOSE, ""
    head_raw = (head_row or {}).get("observation") if isinstance(head_row, dict) else None
    head_obs = _norm(head_raw)
    if base_obs in head_obs:
        return OBS_PRESERVED, ""
    state, detail = marker_state(head_raw, today)
    if state == MARKER_DECLARED:
        return OBS_SUPERSEDED, detail
    return OBS_LOST, (f"{len(base_obs)} → {len(head_obs)} chars; "
                      f"declaration: {state}"
                      + (f" ({detail})" if detail else ""))


def check_observation_loss(base_items: object, head_items: object,
                           today: date | None = None) -> list[str]:
    """Rows whose observation lost base text with nothing declaring it.

    Pure — it takes two parsed registers, so the rule is arguable in tests
    rather than only against a live branch.
    """
    today = today or datetime.now(timezone.utc).date()
    if not isinstance(base_items, list) or not isinstance(head_items, list):
        return []
    base_by_id = {r.get("id"): r for r in base_items
                  if isinstance(r, dict) and r.get("id")}
    problems: list[str] = []
    for row in head_items:
        if not isinstance(row, dict):
            continue
        rid = row.get("id")
        if rid not in base_by_id:
            continue
        state, detail = observation_delta(base_by_id[rid], row, today)
        if state != OBS_LOST:
            continue
        problems.append(
            f"{rid}: this diff DROPS text that its 'observation' carried at "
            f"the merge base ({detail}). That field is where sessions leave "
            f"corrections for each other, and a splice that replaces it "
            f"instead of extending it is invisible to every id-based or "
            f"count-based proof. Either keep the prior text (append below it), "
            f"or declare the removal in the observation itself as "
            f"'SUPERSEDES-OBSERVATION {today.isoformat()}: <why the prior "
            f"reading no longer holds>'."
        )
    return problems


def observation_loss_against_base(path: Path, base_ref: str,
                                  repo: Path | None = None,
                                  today: date | None = None
                                  ) -> tuple[str, list[str], str]:
    """`(state, problems, note)` — the git half, kept out of the pure rule.

    ⚠️ THE FORK POINT, NOT THE TIP, and the distinction is the whole question
    here: "did THIS DIFF shorten a row" is only answerable against the branch's
    ANCESTOR. Read at a moved-ahead tip, another session's legitimate edit to
    the same row lands in this diff's lap. Resolution is delegated to
    `_git_base`, which owns it repo-wide.
    """
    resolved, how = _git_base.resolve_base(base_ref, repo=repo)
    rel = str(path)
    read_state, text = _git_base.read_at(resolved, rel, repo=repo)
    if read_state == _git_base.UNREADABLE:
        return ("unreadable", [],
                f"could not read {rel} at {base_ref} — NOTHING was compared "
                f"here; this is not a pass")
    if read_state == _git_base.ABSENT_AT_BASE:
        return ("absent_at_base", [],
                f"{rel} does not exist at {base_ref} — no prior observation "
                f"exists to lose")
    try:
        base_items = json.loads(text or "")["items"]
        head_items = json.loads(path.read_text(encoding="utf-8"))["items"]
    except (json.JSONDecodeError, OSError, KeyError, TypeError) as exc:
        return ("unreadable", [],
                f"{type(exc).__name__} parsing one side — NOTHING was "
                f"compared; this is not a pass")
    return ("read", check_observation_loss(base_items, head_items, today),
            f"compared against {how} of {base_ref}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default=str(_REGISTER))
    ap.add_argument("--self-test", action="store_true")
    # ⚠️ `--base` is what makes the observation-preservation rule a RULE rather
    # than a census. WITHOUT it the diff-scoped half DOES NOT RUN, and the
    # summary says so — an absent comparison must never read as a clean one.
    ap.add_argument("--base", default="",
                    help="base ref; its MERGE BASE with HEAD is what is read")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    problems = check(Path(args.path))

    if args.base:
        obs_state, obs_problems, obs_note = observation_loss_against_base(
            Path(args.path), args.base)
        problems.extend(obs_problems)
    else:
        obs_state, obs_note = "not_graded", (
            "no --base given, so the observation-preservation rule DID NOT "
            "RUN — that is 'we did not look', not 'nothing was dropped'")

    if problems:
        print("::error::docs/claude/OPEN-ITEMS.json is the register EVERY "
              "session reads at start. It is not workable as it stands:")
        for p in problems:
            print(f"  - {p}")
        print(f"  (observation-preservation {obs_state} — {obs_note})")
        return 1
    items = json.loads(Path(args.path).read_text(encoding="utf-8"))["items"]
    mon = sum(1 for i in items if i.get("kind") == "monitoring")
    print(f"open-items-guard: OK — {len(items)} items ({mon} monitoring), every one "
          f"workable and affirmed within {_STALE_DAYS} days.")
    # Printed on the PASS path too, and named rather than implied: a reader
    # must be able to tell "no row dropped its observation" from "nobody
    # compared". They are different facts and only one of them is a pass.
    print(f"open-items-guard: observation-preservation {obs_state} — {obs_note}")
    return 0


def _self_test() -> int:
    """Prove the guard can find a positive before its silence is trusted."""
    import tempfile

    ok = True
    with tempfile.TemporaryDirectory() as d:
        base = {"schema_version": 1, "items": []}
        good = {"id": "OI-1", "opened": "2026-08-26", "kind": "background_awareness",
                "summary": "s", "clears_when": "a named observable thing happens"}

        def run(items, today="2026-08-26"):
            p = Path(d) / "r.json"
            p.write_text(json.dumps({**base, "items": items}))
            return check(p, today=date.fromisoformat(today))

        # ⚠️ THE DUPLICATE-KEY CASE MUST BE WRITTEN AS RAW TEXT, and that is not
        # a shortcut — `json.dumps` CANNOT emit a repeated key, so a fixture
        # built the normal way could never exercise the rule. A test that only
        # uses `run()` here would pass forever while the guard did nothing.
        def run_raw(text, today="2026-08-26"):
            p = Path(d) / "raw.json"
            p.write_text(text, encoding="utf-8")
            return check(p, today=date.fromisoformat(today))

        _dup_row = (
            '{"schema_version": 1, "items": [{'
            '"id": "OI-DUP", "opened": "2026-08-26", '
            '"kind": "background_awareness", "summary": "s", '
            '"clears_when": "a named observable thing happens", '
            '"observation": "a real measurement somebody took", '
            '"observation": ""'
            '}]}'
        )
        _single_row = _dup_row.replace(', "observation": ""', "")

        cases = [
            ("clean register passes", run([good]), False),
            ("there is NO cap — many rows is fine",
             run([{**good, "id": f"OI-{i}"} for i in range(40)]), False),
            ("a monitoring row with no cadence is a finding",
             run([{**good, "kind": "monitoring",
                   "observation": "x" * 50, "verified_at": "2026-08-26"}]), True),
            ("a monitoring row with no observation is a finding",
             run([{**good, "kind": "monitoring", "check_every_days": 2,
                   "verified_at": "2026-08-26"}]), True),
            ("a complete monitoring row passes",
             run([{**good, "kind": "monitoring", "check_every_days": 2,
                   "verified_at": "2026-08-26", "observation": "x" * 50}]), False),
            ("missing clears_when is a finding",
             run([{k: v for k, v in good.items() if k != "clears_when"}]), True),
            ("a non-observable clears_when is a finding",
             run([{**good, "clears_when": "the fix works"}]), True),
            ("a stale row is a finding", run([good], today="2026-10-01"), True),
            ("re-affirming clears staleness",
             run([{**good, "reaffirmed": "2026-09-28"}], today="2026-10-01"), False),
            ("an undateable row is a finding, not a pass",
             run([{**good, "opened": "whenever"}]), True),
            ("a duplicate id is a finding", run([good, good]), True),
            # The planted positive for the rule this guard gained on
            # 2026-09-12. It is the ONLY case in this suite whose fixture is
            # raw text, for the reason given at run_raw.
            ("a DUPLICATE KEY is a finding — json.loads keeps the last value "
             "and silently discards the first, so a real observation can be "
             "invisible behind an empty one",
             run_raw(_dup_row), True),
            ("...and the same row with ONE observation passes, so the rule is "
             "not simply refusing everything",
             run_raw(_single_row), False),
            ("a missing register is a finding",
             check(Path(d) / "nope.json"), True),
        ]

        # ── The observation-preservation rule ─────────────────────────────
        # Planted defects and their positive controls. The pure function is
        # what is exercised here; the git half has its own tests, and the
        # REAL instance (the 2026-09-11 splice) is replayed there against
        # actual history rather than a fixture.
        TODAY = date(2026, 9, 13)
        was = {"id": "OI-OBS", "opened": "2026-09-01", "kind": "monitoring",
               "check_every_days": 2, "verified_at": "2026-09-13",
               "summary": "s", "clears_when": "a named observable thing happens",
               "observation": "session A measured 47 rows and 6 were real"}

        def obs(head_observation, base=None, today=TODAY):
            head = dict(was)
            if head_observation is None:
                head.pop("observation", None)
            else:
                head["observation"] = head_observation
            return check_observation_loss([base or was], [head], today)

        kept = was["observation"]
        cases += [
            ("REPLACING an observation is a finding — the 2026-09-11 shape",
             obs("session B measured something else entirely"), True),
            ("APPENDING to it is not",
             obs(kept + " || session B adds a second reading"), False),
            ("PREPENDING to it is not either — order is not the question",
             obs("session B adds a correction above || " + kept), False),
            ("re-wrapping the same text is not a finding: whitespace is "
             "collapsed before comparing, so a reflow cannot manufacture one",
             obs(kept.replace(" ", "\n   ")), False),
            ("DELETING the field outright is a finding, not an exemption",
             obs(None), True),
            ("emptying the field is a finding",
             obs("   "), True),
            ("a row that had NO observation at the base cannot lose one",
             obs("a brand new reading",
                 base={k: v for k, v in was.items() if k != "observation"}), False),
            ("a FRESH, REASONED declaration permits the replacement — rows do "
             "get legitimately superseded and this rule is not append-only",
             obs("SUPERSEDES-OBSERVATION 2026-09-13: the endpoint it measured "
                 "was retired, so that reading cannot be reproduced"), False),
            ("a STALE declaration does NOT — otherwise one marker licenses "
             "every future deletion of that row",
             obs("SUPERSEDES-OBSERVATION 2026-08-01: reasons given long ago "
                 "and far away, no longer about this edit"), True),
            ("a declaration with a shrug for a reason does not",
             obs("SUPERSEDES-OBSERVATION 2026-09-13: stale"), True),
            ("the marker without a date does not",
             obs("SUPERSEDES-OBSERVATION: it was wrong, here is the new one"),
             True),
            ("a row only in the HEAD is not graded — it has no base to lose",
             check_observation_loss([], [was], TODAY), False),
            ("a row DROPPED from the head is not graded here either: removing "
             "a row is a different act with its own surfaces, and grading it "
             "as a lost observation would misname it",
             check_observation_loss([was], [], TODAY), False),
            ("a non-list on either side grades nothing rather than crashing",
             check_observation_loss(None, [was], TODAY), False),
        ]
        for label, problems, want_problem in cases:
            got = bool(problems)
            status = "PASS" if got == want_problem else "FAIL"
            if got != want_problem:
                ok = False
            print(f"  self-test ({label}): {status}"
                  + (f" -- {problems}" if got != want_problem else ""))
    print("open-items-guard self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
