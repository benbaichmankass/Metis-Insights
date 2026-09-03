#!/usr/bin/env python3
#
# wiring: manual-only — a MANAGER runs this before un-drafting, arming, merging
# or closing someone else's PR. Its `--self-test` runs in CI via `run_guards.py`
# (`manager-tooling-selftests`); the GATE itself cannot, and the docstring says
# why rather than leaving it to be rediscovered.
"""PR ACTION GATE — may this manager act on THIS pull request right now?

THE MEASURED EVENT THIS EXISTS FOR
-----------------------------------
`docs/claude/CYCLE-PRIORITY.json :: current.why`, clause (3), operator-set
2026-09-02:

    "The manager undrafted and armed #10857 while its author was still
     RUNNING, because there was no way to ask -- and nothing objected."

Verified against the PR itself rather than taken from the register: #10857
(`claude/mi83-per-merge-ping`) was created 2026-09-02T20:46:36Z and merged
21:59:16Z. And the same clause names the shape of the gap:

    "THE GAP IS THE MISSING READ, NOT A MISSING CHANNEL: a RUNNING session
     cannot be reached, an IDLE one CAN be woken, and nothing tells the
     manager which it is."

`manager_view.py` (MI-89) supplied the read. This is the refusal that uses it.
Together they are `DEC-20260902-HOW-A-MANAGER-IS-HELD-TO-ITS-MANDATE` → `both`.

WHY A GATE HERE AND NOT A CHECK IN CI
--------------------------------------
CI cannot call `list_sessions` — it holds no `mcp__*` tools. So a CI guard could
only ever grade `unknown` on the one input the whole question turns on, and a
check that is permanently `unknown` is a check everyone learns to walk past:
the desensitised-alarm P1 this repo has already paid for twice (202 of 376
CRITICALs in one window being a single un-latched alarm). `manager_preflight.py`
records the same reasoning for its own manual-only wiring.

**The refusal therefore lives where the observation lives — in the manager's
hands.** That is not a weaker position than CI; it is the only position from
which the question is answerable at all.

WHY NOT A CHECK INSIDE `manager_preflight.py`
----------------------------------------------
Preflight asks *"may this manager act AT ALL?"* — a fleet-wide readiness
question with no target. This asks *"may it act on THIS PR?"*, which needs a
target PR as an argument. Folding a target-taking check into a target-less tool
would force every existing preflight run to grade the new check `unknown` for
want of an argument, turning a working tool amber on day one. `spawn_gate.py` is
the precedent: a per-action gate, invoked at the moment of the action, separate
from the readiness sweep. This is its sibling.

WHAT IS DERIVED LIVE AND WHAT IS READ FROM THE REGISTER — the load-bearing split
--------------------------------------------------------------------------------
The register is measured wrong about STATE and is the only thing that knows
IDENTITY, so the two are read from different places on purpose:

  LIVENESS   comes ONLY from a live `list_sessions` observation. Never from
             `SESSIONS.json :: state`. MI-84 measured 17 of 17 inherited
             `working` rows wrong; MI-89 re-measured 24 of 24 register
             assertions contradicted by a live read. A refusal keyed on stored
             state would refuse on stale data and be switched off within a day.

  IDENTITY   (which session authored this PR) may come from the register,
             because a branch↔session association is an IMMUTABLE HISTORICAL
             FACT — session X opened branch Y, and that does not decay the way
             `state: working` decays. ⚠️ This distinction is the tool's main
             assumption and is stated so it can be argued with, not buried.

...and identity is preferred from the PR ITSELF, which is better than either:
`create_pull_request` bodies carry a `claude.ai/code/session_<id>` footer
written by the authoring session at open time. It travels with the PR, cannot
go stale, and needs no register at all.

  MEASURED 2026-09-03, population: ALL 8 open PRs on benbaichmankass/
  Metis-Insights, read via `list_pull_requests` —
    5 of 5 `claude/**` PRs carry the footer (#10900, #10895, #10893, #10888,
      #10877); #10895 carries it twice, identically.
    0 of 3 `automation/**` PRs carry one (#10902, #10901, #10398) — correctly,
      since a workflow-opened PR HAS no author session.

FOUR STATES FOR THE AUTHOR, NEVER COLLAPSED
--------------------------------------------
`docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed states" asks whether a field can
say *we did not look*. Four answers here, and the last two are the ones a
two-state version would fuse into a silent pass:

  a session id     we looked and the PR names its author.
  ``no_author``    we looked and this PR HAS no author session — an
                   `automation/**` head ref with no footer and no registry row.
                   A REAL FINDING, and the opposite of the one below.
  ``ambiguous``    the body names two or more DIFFERENT sessions. We looked and
                   cannot say which is the author.
  ``unidentified`` WE COULD NOT LOOK — no footer, no registry row, and the head
                   ref is not a reserved automation namespace.

⚠️ WHICH WAY `unknown` FAILS, DECIDED AND ARGUED
-------------------------------------------------
`unknown` is its own verdict with its own exit code (4). It is **neither a soft
pass nor a hard refusal**, and both alternatives were rejected for stated
reasons:

  NOT a hard refusal. A gate that blocks whenever it cannot look would block on
  a paginated `list_sessions` page, a missing `--live-sessions` flag, or a PR
  body someone reformatted. The manager would learn to pass a bypass flag on
  every invocation, and the gate would be decorative inside a day. This is the
  same call `spawn_gate.py` made ("a gate that fails closed on its own
  unreadable config would halt all spawning on a typo").

  NOT a soft pass. `unknown` exits non-zero, prints at the same volume as a
  refusal, and can never be reported as `permitted`. A caller that treats exit 4
  as success has to do so deliberately.

  ⚠️ AND `unknown` IS CHEAP TO CLEAR HERE, WHICH IS WHY IT IS TOLERABLE. The
  manager holds `list_sessions` and `list_pull_requests`. Unlike in CI — where
  `unknown` would be permanent and therefore corrosive — every `unknown` this
  tool emits names the one input that would resolve it, and the manager can
  fetch it in one call. A permanent `unknown` and a one-flag-away `unknown` are
  different things, and only the first breeds alarm fatigue.

⚠️ ABSENCE FROM THE OBSERVATION IS NOT DEATH
----------------------------------------------
`list_sessions` is PAGINATED — a page is not a population (spawn_gate.py's
docstring makes the same point against a 60-row page). So an author session that
does not appear in the supplied observation grades `unknown`, never `permitted`.
Reading "not on this page" as "finished" is precisely how the refusal would be
routed around by accident.

IT MUST NOT STALL WORK THAT IS GENUINELY DONE
----------------------------------------------
A session can be alive and finished — on 2026-09-03 three went idle with PRs
ready. Two paths forward, both of which PERMIT:

  1. **The author handed back.** A row carrying `review_ready` / `needs_action`
     / `blocked` / `awaiting` (`manager_preflight._NEEDS_ACTION_TOKENS`) is the
     author's OWN declaration that the PR is the manager's now. ⚠️ This is
     checked BEFORE liveness and therefore OUTRANKS it: a RUNNING session that
     has said "over to you" has answered the only question this gate asks, and
     the whole point of clause (3) is that there was "no way to ask". When the
     session has already answered unasked, refusing anyway would be ceremony.
  2. **The author is idle or terminal.** An idle session CAN be woken, which is
     the operator-stated remedy, not a blocker.

So the gate refuses exactly one condition: **an author observed live that has
NOT handed the work back.** That is the #10857 condition and nothing wider.

THE ESCAPE HATCH IS A FILE, NOT A FLAG
---------------------------------------
`docs/claude/work/pr-action-exception.yaml`, in `spawn-priority-exception.yaml`'s
shape and graded by `spawn_gate.exception_covers` — the SAME function, imported
rather than re-implemented, so the two cannot drift on what an approval means.
`decision: pending` still refuses (filing is not granting); an approval with no
`approved_by`/`approved_at` is a session approving itself; and it covers only
the PR refs it NAMES.

⚠️ THERE IS DELIBERATELY NO `--force`. A bypass flag is cheaper to lie to than
to satisfy, which is the `new-table-wiring-guard` lesson: a guard cheaper to lie
to than to satisfy is worse than no guard. Overriding this costs a committed,
dated, argued file naming the PR — visible to every later reader.

THREE VERDICTS
--------------
``permitted``  the author handed back, is idle/terminal, does not exist
               (automation), or an approved exception names this PR.
``refused``    a named author, observed LIVE, that has not handed back. The
               refusal SAYS WHO and SAYS WHAT TO DO, because a refusal that only
               says "no" teaches nothing and gets routed around.
``unknown``    we could not look. Never permission. See the block above.

EXIT CODES: 0 permitted · 3 refused · 4 unknown.

USAGE
-----
    python3 scripts/ops/pr_action_gate.py --pr 10857 --action undraft \\
        --live-sessions <(list_sessions output) \\
        --open-prs      <(list_pull_requests output)

WHAT THIS DELIBERATELY DOES NOT CHECK — the honest boundary
------------------------------------------------------------
* **Whether the action is the RIGHT one.** It grades who may be interrupted, not
  whether merging is wise. CI, tier and review are other mechanisms' jobs.
* **Whether a live author is actually still editing THIS branch.** Nothing
  reachable links a running session's current activity to a branch. A live
  author is treated as reachable-and-possibly-working, which is the pessimistic
  direction and the safe one.
* **PRs in another repository.** A `<repo>#<n>` reference to another repo is
  reported `other_repo` and not graded, for `manager_view.py`'s reason: grading
  it against THIS repo's observation would manufacture a finding.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import manager_preflight as mp  # noqa: E402
import manager_view as mv  # noqa: E402
import open_pr_record as opr  # noqa: E402
import session_registry as sr  # noqa: E402
import spawn_gate as sg  # noqa: E402

REPO_ROOT = sr.REPO_ROOT
EXCEPTION_PATH = REPO_ROOT / "docs" / "claude" / "work" / "pr-action-exception.yaml"

PERMITTED, REFUSED, UNKNOWN = "permitted", "refused", "unknown"
_EXIT = {PERMITTED: 0, REFUSED: 3, UNKNOWN: 4}

#: Author-resolution outcomes that are NOT a session id. Three distinct facts,
#: deliberately never one value — see the docstring.
NO_AUTHOR = "no_author"
AMBIGUOUS = "ambiguous"
UNIDENTIFIED = "unidentified"

#: The footer `create_pull_request` writes into a session's PR body. Anchored on
#: the URL prefix rather than harvested from free text, so it cannot match a
#: JSON key name — the false-positive class `session_registry` measured at 3 of
#: 32 findings when a loose pattern met real payloads.
_BODY_SESSION_RE = re.compile(r"claude\.ai/code/(session_[A-Za-z0-9]{6,})")

#: Actions a manager takes ON a PR that take it out of the author's hands. Free
#: text is accepted too; this list only drives the `--help` text and the label in
#: the verdict, never the policy. A policy that differed per action would be a
#: judgement about intent this tool cannot verify.
KNOWN_ACTIONS = ("undraft", "arm", "merge", "close", "push", "edit")


def _v(state: str, reason: str, **extra: Any) -> Dict[str, Any]:
    return dict(state=state, reason=reason, **extra)


# --------------------------------------------------------------------------- #
# author resolution — who opened this PR?
# --------------------------------------------------------------------------- #
def author_from_body(body: Any) -> Tuple[Optional[str], str]:
    """(session id, basis) from the PR body's own footer.

    ⚠️ Two DIFFERENT ids in one body is `AMBIGUOUS`, not "pick the first". A body
    can legitimately quote another session (several open PRs quote the session
    that dispatched them), and guessing between them would attribute the PR to a
    session that never touched it. The same id repeated is not ambiguity —
    #10895 carries its footer twice, identically.
    """
    if not isinstance(body, str) or not body.strip():
        return None, "the PR body is empty or was not supplied"
    found = list(dict.fromkeys(_BODY_SESSION_RE.findall(body)))
    if not found:
        return None, "the PR body carries no `claude.ai/code/session_…` footer"
    if len(found) > 1:
        return AMBIGUOUS, (f"the PR body names {len(found)} DIFFERENT sessions "
                           f"({', '.join(found)}), so its author cannot be "
                           f"established from it")
    return found[0], "the PR body's own `claude.ai/code/session_…` footer"


def author_from_registry(head_ref: str, pr_number: int, reg_doc: Any,
                         reg_readable: bool) -> Tuple[Optional[str], str]:
    """(session id, basis) from `SESSIONS.json`'s branch/PR associations.

    ⚠️ Reads `branches` and `prs` — an ASSOCIATION, which is immutable history —
    and NEVER `state`, which is measured stale. See the docstring's split.

    ⚠️ Branch refs are recorded both bare (`claude/x`) and repo-qualified
    (`Metis-Insights:claude/x`); measured on the live file, 61 rows carry
    `branches` in a mix of the two. Both forms are matched, because matching one
    would silently miss the other and read as "no author".
    """
    if not reg_readable:
        return None, "SESSIONS.json could not be read"
    rows = sr.registry_rows(reg_doc)
    hits: List[str] = []
    for row in rows:
        sid = row.get("session_id")
        if not sid:
            continue
        for ref in row.get("branches") or []:
            if not isinstance(ref, str):
                continue
            bare = ref.split(":", 1)[1] if ":" in ref else ref
            if head_ref and bare.strip() == head_ref:
                hits.append(sid)
        for ref in row.get("prs") or []:
            num, _ = mv.parse_pr_ref(ref)
            if num is not None and num == pr_number:
                hits.append(sid)
    hits = list(dict.fromkeys(hits))
    if not hits:
        return None, (f"no SESSIONS.json row names branch `{head_ref}` or "
                      f"PR #{pr_number}")
    if len(hits) > 1:
        return AMBIGUOUS, (f"{len(hits)} registry rows claim this PR "
                           f"({', '.join(hits)})")
    return hits[0], f"a SESSIONS.json row naming branch `{head_ref}`"


def resolve_author(pr_entry: Optional[Dict[str, Any]], pr_number: int,
                   reg_doc: Any, reg_readable: bool) -> Tuple[str, str]:
    """(session id | NO_AUTHOR | AMBIGUOUS | UNIDENTIFIED, basis).

    Order matters: the PR's OWN footer outranks the register, because it was
    written by the author at open time and cannot go stale, while a register row
    is a third party's bookkeeping about it.
    """
    if pr_entry is None:
        return UNIDENTIFIED, ("no open-PR observation was supplied, so neither "
                              "the PR body nor its head branch could be read")
    body_sid, body_why = author_from_body(pr_entry.get("body"))
    if body_sid == AMBIGUOUS:
        return AMBIGUOUS, body_why
    if body_sid:
        return body_sid, body_why

    head_ref = opr._head_ref(pr_entry)
    reg_sid, reg_why = author_from_registry(head_ref, pr_number, reg_doc,
                                            reg_readable)
    if reg_sid == AMBIGUOUS:
        return AMBIGUOUS, reg_why
    if reg_sid:
        return reg_sid, reg_why

    # ⚠️ THE ONLY ROUTE TO `no_author`, AND IT IS DELIBERATELY NARROW. A PR from
    # the reserved `automation/` namespace with no footer and no registry row was
    # opened by a workflow, so there is no session to interrupt — a real finding,
    # not a failure to look. `open_pr_record.is_automation_landing_pr` owns this
    # predicate (and the measurement that the AUTHOR field carries no signal
    # here, in either direction); it is imported rather than restated.
    if opr.is_automation_landing_pr(pr_entry):
        return NO_AUTHOR, (f"head branch `{head_ref}` is in the reserved "
                           f"`{opr.AUTOMATION_HEAD_PREFIX}` namespace and no "
                           f"session claims it — a workflow-opened PR has no "
                           f"author session to interrupt")
    return UNIDENTIFIED, f"{body_why}, and {reg_why}"


# --------------------------------------------------------------------------- #
# liveness — ONLY ever from the live observation
# --------------------------------------------------------------------------- #
#: Author-liveness readings. Five, because collapsing any two reintroduces the
#: defect (`src/runtime/exit_anchor.py` is the pattern this follows).
LIVE = "live"
HANDED_BACK = "handed_back"
NOT_WORKING = "not_working"
TERMINAL = "terminal"
UNCLASSIFIED = "unclassified"
ABSENT = "absent"


def author_liveness(session_id: str,
                    observation: Optional[List[Dict[str, Any]]]
                    ) -> Tuple[str, str, List[str]]:
    """(reading, basis, matched tokens) for one session, from a LIVE read.

    ⚠️ HAND-BACK IS TESTED FIRST, AND THAT PRECEDENCE IS THE DESIGN. A session
    that has declared `review_ready`/`needs_action` has already answered the
    question this gate exists to ask, so its liveness stops mattering. Testing
    liveness first would refuse a finished author and produce exactly the
    stalled, routed-around gate the brief warns about.

    ⚠️ ABSENT is NOT terminal. `list_sessions` is paginated; not appearing on the
    supplied page is not evidence of having stopped.
    """
    if observation is None:
        return UNCLASSIFIED, "no live-session observation was supplied", []
    row = next((r for r in observation if r.get("session_id") == session_id), None)
    if row is None:
        return ABSENT, ("the session does not appear in the supplied "
                        "observation — which is a PAGE, not a population"), []
    toks = mp._status_tokens(row)
    if not toks:
        return UNCLASSIFIED, "the observed row carries no status field", []
    matched = [t for t in mp._NEEDS_ACTION_TOKENS
               if any(t in tok for tok in toks)]
    if matched:
        return HANDED_BACK, ("the session's own status says it handed the work "
                             "back"), matched
    matched = [t for t in mp._TERMINAL_TOKENS if any(t in tok for tok in toks)]
    if matched:
        return TERMINAL, "the session is over", matched
    matched = [t for t in mp._NOT_WORKING_TOKENS if any(t in tok for tok in toks)]
    if matched:
        return NOT_WORKING, "the session is not actively working", matched
    return LIVE, f"observed status {toks!r} names nothing terminal or idle", toks


# --------------------------------------------------------------------------- #
# grading — PURE, so the policy is arguable in tests rather than against a live
# manager. Same reason `spawn_gate.grade` and `manager_preflight.grade` are pure.
# --------------------------------------------------------------------------- #
def grade(pr_number: Optional[int], pr_entry: Optional[Dict[str, Any]],
          observation: Optional[List[Dict[str, Any]]],
          reg_doc: Any, reg_readable: bool, exc: Optional[Any],
          action: str = "act on", other_repo: Optional[str] = None
          ) -> Dict[str, Any]:
    what = f"{action} #{pr_number}"

    if other_repo:
        return _v(UNKNOWN,
                  f"PR reference names repository `{other_repo}`, not "
                  f"`{mv.DEFAULT_REPO}`. A PR in another repository cannot be "
                  f"graded against this one's observations, and comparing them "
                  f"would manufacture a finding. NOT GRADED — this is not "
                  f"permission.", pr=pr_number)
    if pr_number is None:
        return _v(UNKNOWN,
                  "no PR was named (`--pr`), so there is nothing to grade. WE "
                  "DID NOT LOOK.")

    sid, basis = resolve_author(pr_entry, pr_number, reg_doc, reg_readable)

    if sid == NO_AUTHOR:
        return _v(PERMITTED,
                  f"may {what}: it has NO author session to interrupt — {basis}.",
                  pr=pr_number, author=NO_AUTHOR, author_basis=basis)
    if sid == AMBIGUOUS:
        return _v(UNKNOWN,
                  f"cannot grade {what}: its author is AMBIGUOUS — {basis}. WE "
                  f"LOOKED AND CANNOT SAY WHO. Resolve it by correcting the "
                  f"SESSIONS.json row, then re-run. Not permission.",
                  pr=pr_number, author=AMBIGUOUS, author_basis=basis)
    if sid == UNIDENTIFIED:
        return _v(UNKNOWN,
                  f"cannot grade {what}: its author could not be identified — "
                  f"{basis}. WE DID NOT LOOK. Supply `--open-prs` from a live "
                  f"`list_pull_requests` read (the body footer is the strongest "
                  f"link), or register the branch in SESSIONS.json. Not "
                  f"permission.",
                  pr=pr_number, author=UNIDENTIFIED, author_basis=basis)

    reading, why, toks = author_liveness(sid, observation)

    if reading in (UNCLASSIFIED, ABSENT):
        return _v(UNKNOWN,
                  f"cannot grade {what}: its author is `{sid}` ({basis}), but "
                  f"its LIVENESS is unknown — {why}. ⚠️ This is NOT read from "
                  f"SESSIONS.json `state` on purpose: 17 of 17 inherited "
                  f"`working` rows were measured wrong (MI-84), so a stale row "
                  f"would be worse than no answer. Pass `--live-sessions` from a "
                  f"`list_sessions` read covering this session. Not permission.",
                  pr=pr_number, author=sid, author_basis=basis, liveness=reading)

    if reading in (HANDED_BACK, NOT_WORKING, TERMINAL):
        nudge = ("" if reading != NOT_WORKING else
                 " An idle session CAN be woken if you need it — that is the "
                 "remedy, not a blocker.")
        return _v(PERMITTED,
                  f"may {what}: its author `{sid}` is `{reading}` — {why} "
                  f"(matched {toks}).{nudge}",
                  pr=pr_number, author=sid, author_basis=basis,
                  liveness=reading, tokens=toks)

    # reading == LIVE — the one refusal.
    covered, exc_why = sg.exception_covers(exc, str(pr_number))
    if covered:
        return _v(PERMITTED,
                  f"may {what} DESPITE its author `{sid}` being observed LIVE — "
                  f"permitted by an approved exception ({exc_why}). Recorded, "
                  f"not silent.",
                  pr=pr_number, author=sid, author_basis=basis,
                  liveness=LIVE, exception=True)

    return _v(REFUSED,
              f"DO NOT {what.upper()} — ITS AUTHOR IS STILL LIVE.\n"
              f"    author       : {sid}\n"
              f"    identified by: {basis}\n"
              f"    observed     : {why}\n"
              f"  This is the #10857 condition, verbatim: on 2026-09-02 a manager\n"
              f"  un-drafted and armed a PR while its author was still RUNNING\n"
              f"  'because there was no way to ask -- and nothing objected'\n"
              f"  (CYCLE-PRIORITY.json :: current.why). Now something objects.\n"
              f"  THREE WAYS FORWARD, in order of preference:\n"
              f"    1. ASK IT. Send the session a message and let it finish or\n"
              f"       hand the PR over. A status of review_ready/needs_action\n"
              f"       PERMITS this action immediately — no exception needed.\n"
              f"    2. WAIT for it to go idle, then act. An idle author permits.\n"
              f"    3. If it genuinely must be overridden, file\n"
              f"       docs/claude/work/pr-action-exception.yaml naming "
              f"`{pr_number}`,\n"
              f"       with an operator's `approved_by`/`approved_at`. There is\n"
              f"       deliberately no --force flag — a bypass flag is cheaper\n"
              f"       to lie to than to satisfy.\n"
              f"       (exception status right now: {exc_why})",
              pr=pr_number, author=sid, author_basis=basis, liveness=LIVE,
              tokens=toks)


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #
#: Envelope keys a `list_pull_requests` payload may be wrapped in. Mirrors
#: `manager_view.normalise_prs`'s descent deliberately — see `open_pr_entries`.
_PR_ENVELOPE_KEYS = ("pull_requests", "open_prs", "data", "results", "items")


def open_pr_entries(raw: Any) -> Optional[List[Dict[str, Any]]]:
    """Open-PR rows keeping ``body`` and the original ``head`` dict.

    ⚠️ WHY THIS IS NOT `manager_view.normalise_prs`, WHICH IT OTHERWISE MIRRORS.
    That function deliberately projects onto the four fields ITS join needs
    (`number`, `head_ref`, `title`, `draft`) and drops `body` — correct for a
    table renderer, and useless here, because the PR body's footer is the
    strongest author link there is.

    Widening it was the first thing tried and is the WRONG fix: MI-89's
    `manager_view._self_test` asserts its output by EXACT DICT EQUALITY, so
    adding a key would fail that session's suite (which `run_guards.py` runs) to
    serve a need its tool does not have. A caller wanting more fields recovers
    them here rather than re-shaping another owner's contract.

    ⚠️ Returns ``None`` when nothing usable was found — never ``[]``. "We could
    not read it" and "nothing is open" are opposite facts, and collapsing them
    is the named failure class this repo's `|| echo '{}'` rule exists for.
    """
    for _ in range(4):
        if not isinstance(raw, dict):
            break
        for key in _PR_ENVELOPE_KEYS:
            inner = raw.get(key)
            if isinstance(inner, (list, dict)):
                raw = inner
                break
        else:
            break
        if isinstance(raw, list):
            break
    if not isinstance(raw, list):
        return None
    out: List[Dict[str, Any]] = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        num = e.get("number", e.get("pr"))
        if isinstance(num, bool) or not isinstance(num, int):
            continue
        head = e.get("head")
        # Keep `head` in its ORIGINAL dict shape: `open_pr_record._head_ref`
        # owns the automation predicate and reads `head.ref`, and re-shaping it
        # here would fork that ownership.
        out.append({
            "number": num,
            "head": head if isinstance(head, dict)
            else {"ref": str(e.get("head_ref") or "")},
            "body": e.get("body"),
            "title": str(e.get("title") or ""),
            "draft": bool(e.get("draft")),
        })
    return out or None


def find_pr(open_prs: Optional[List[Dict[str, Any]]], pr_number: int
            ) -> Optional[Dict[str, Any]]:
    if not open_prs:
        return None
    return next((p for p in open_prs if p.get("number") == pr_number), None)


def read_exception(path: Path = EXCEPTION_PATH) -> Tuple[Optional[Any], bool]:
    return sg._read_yaml(path)


def grade_pr_action(pr_ref: str, action: str,
                    observation: Optional[Any] = None,
                    open_prs_raw: Optional[Any] = None) -> Dict[str, Any]:
    num, ok = mv.parse_pr_ref(pr_ref)
    other = None
    if not ok:
        m = mv._PR_REF_RE.match(str(pr_ref).strip())
        other = m.group(1) if m else str(pr_ref)
        num = int(m.group(2)) if m else None
    reg, reg_ok = sr.read_json(sr.REGISTRY_PATH)
    exc, _exc_ok = read_exception()
    obs = sr.normalise_observation(observation) if observation is not None else None
    prs = open_pr_entries(open_prs_raw) if open_prs_raw is not None else None
    return grade(num, find_pr(prs, num) if num else None, obs, reg, reg_ok, exc,
                 action, other)


# --------------------------------------------------------------------------- #
# self-test — every verdict must be shown to be reachable on a planted input.
# Runs on EVERY invocation, not behind a flag: a gate whose teeth are assumed
# rather than demonstrated is the `check_selftest_wiring` defect.
# --------------------------------------------------------------------------- #
def _self_test(quiet: bool = False) -> Tuple[bool, List[str]]:
    failures: List[str] = []

    def check(label: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r} want {want!r}")
        if not quiet:
            print(f"  self-test ({label}): "
                  f"{'PASS' if got == want else f'FAIL got={got!r} want={want!r}'}")

    SID = "session_01AAAAAAAAAAAAAAAAAAAA"
    OTHER = "session_01BBBBBBBBBBBBBBBBBBBB"
    body = f"work\n\nhttps://claude.ai/code/{SID}\n"
    pr = {"number": 10857, "body": body, "head": {"ref": "claude/mi83-x"}}
    auto = {"number": 10902, "body": "auto",
            "head": {"ref": "automation/work-digest-1"}}
    orphan = {"number": 999, "body": "no footer", "head": {"ref": "claude/nobody"}}
    live = [{"session_id": SID, "status": "running"}]
    idle = [{"session_id": SID, "status": "idle"}]
    done = [{"session_id": SID, "status": "archived"}]
    back = [{"session_id": SID, "status": "running",
             "post_turn_summary": {"status_bucket": "review_ready"}}]
    ok_exc = {"decision": "approved", "covers": ["10857"],
              "approved_by": "operator", "approved_at": "2026-09-03"}
    REG, RO = {"sessions": []}, True

    # ⚠️ THE NEGATIVE CONTROL IN BOTH DIRECTIONS. A gate that refuses everything
    # and a gate that permits everything are both "consistent"; only the pair
    # shows it discriminates.
    check("A LIVE AUTHOR REFUSES — the #10857 condition",
          grade(10857, pr, live, REG, RO, None)["state"], REFUSED)
    check("AN IDLE AUTHOR PERMITS (the gate is not a wall)",
          grade(10857, pr, idle, REG, RO, None)["state"], PERMITTED)

    # the refusal must teach, or it gets routed around
    r = grade(10857, pr, live, REG, RO, None)
    check("...and the refusal NAMES THE AUTHOR", SID in r["reason"], True)
    check("...and names the ASK-IT path first",
          r["reason"].index("ASK IT") < r["reason"].index("WAIT"), True)
    check("...and names the escape hatch",
          "pr-action-exception" in r["reason"], True)
    check("...and refuses to offer a --force flag",
          "--force" not in r["reason"].replace("no --force flag", ""), True)

    # liveness never comes from the register
    check("A REGISTER SAYING `working` CANNOT MAKE A LIVE VERDICT — an idle "
          "live read PERMITS over a `working` row",
          grade(10857, pr, idle,
                {"sessions": [{"session_id": SID, "state": "working",
                               "branches": ["claude/mi83-x"]}]},
                True, None)["state"], PERMITTED)
    check("...and a register saying `idle` cannot make a live author safe",
          grade(10857, pr, live,
                {"sessions": [{"session_id": SID, "state": "idle",
                               "branches": ["claude/mi83-x"]}]},
                True, None)["state"], REFUSED)

    # the work-is-done paths
    check("A HANDED-BACK AUTHOR PERMITS EVEN WHILE RUNNING — it already answered",
          grade(10857, pr, back, REG, RO, None)["state"], PERMITTED)
    check("...and that is recorded as `handed_back`, not as idleness",
          grade(10857, pr, back, REG, RO, None)["liveness"], HANDED_BACK)
    check("a TERMINAL author permits",
          grade(10857, pr, done, REG, RO, None)["state"], PERMITTED)

    # unknown — its own state, in every direction
    check("NO LIVE OBSERVATION is `unknown`, never permitted",
          grade(10857, pr, None, REG, RO, None)["state"], UNKNOWN)
    check("AN AUTHOR ABSENT FROM THE PAGE is `unknown`, NOT terminal",
          grade(10857, pr, [{"session_id": OTHER, "status": "idle"}],
                REG, RO, None)["state"], UNKNOWN)
    check("...and that absence is graded `absent`, distinct from `terminal`",
          grade(10857, pr, [{"session_id": OTHER, "status": "idle"}],
                REG, RO, None)["liveness"], ABSENT)
    check("A ROW WITH NO STATUS is `unknown` — 'we could not classify' is not idle",
          grade(10857, pr, [{"session_id": SID}], REG, RO, None)["state"], UNKNOWN)
    check("NO OPEN-PR OBSERVATION is `unknown`",
          grade(10857, None, live, REG, RO, None)["state"], UNKNOWN)
    check("an UNIDENTIFIABLE author is `unknown`, never a quiet pass",
          grade(999, orphan, live, REG, RO, None)["state"], UNKNOWN)
    check("a PR in ANOTHER REPO is `unknown`, never compared",
          grade(210, None, live, REG, RO, None, "merge", "ict-trader-dashboard"
                )["state"], UNKNOWN)
    check("...and `unknown` EXITS NON-ZERO so it cannot be read as a pass",
          _EXIT[UNKNOWN] != 0, True)
    check("...while `permitted` exits 0", _EXIT[PERMITTED], 0)
    check("...and `refused` exits 3", _EXIT[REFUSED], 3)

    # `no_author` is a real finding, and is NOT `unidentified`
    check("AN AUTOMATION PR PERMITS — it has no author session to interrupt",
          grade(10902, auto, live, REG, RO, None)["state"], PERMITTED)
    check("...graded `no_author`, which must never render as `unidentified`",
          grade(10902, auto, live, REG, RO, None)["author"], NO_AUTHOR)
    check("...and `automation/` is matched as an ANCHORED PREFIX, so "
          "`claude/automation-notes` is NOT excused",
          grade(999, {"number": 999, "body": "x",
                      "head": {"ref": "claude/automation-notes"}},
                live, REG, RO, None)["author"], UNIDENTIFIED)

    # author resolution
    check("the PR BODY FOOTER identifies the author",
          author_from_body(body)[0], SID)
    check("THE SAME id twice is NOT ambiguity (#10895 carries its footer twice)",
          author_from_body(f"{body}\n{body}")[0], SID)
    check("TWO DIFFERENT ids IS ambiguity — never 'pick the first'",
          author_from_body(f"see https://claude.ai/code/{OTHER}\n{body}")[0],
          AMBIGUOUS)
    check("...and an ambiguous author grades `unknown`, not refused",
          grade(10857, {"number": 10857, "head": {"ref": "claude/x"},
                        "body": f"https://claude.ai/code/{OTHER}\n{body}"},
                live, REG, RO, None)["state"], UNKNOWN)
    check("the footer pattern cannot match a bare JSON key like `session_status`",
          author_from_body("{'session_status': 'running'}")[0], None)
    check("the REGISTRY resolves an author when the body carries no footer",
          grade(999, orphan, idle,
                {"sessions": [{"session_id": SID,
                               "branches": ["claude/nobody"]}]},
                True, None)["author"], SID)
    check("...and matches a REPO-QUALIFIED branch ref too (both forms are live)",
          author_from_registry("claude/nobody", 999,
                               {"sessions": [{"session_id": SID, "branches":
                                              ["Metis-Insights:claude/nobody"]}]},
                               True)[0], SID)
    check("...and matches on a recorded PR NUMBER as well as a branch",
          author_from_registry("", 999,
                               {"sessions": [{"session_id": SID,
                                              "prs": ["Metis-Insights#999"]}]},
                               True)[0], SID)
    check("TWO registry rows claiming one PR is ambiguity, not a coin flip",
          author_from_registry("claude/nobody", 999,
                               {"sessions": [{"session_id": SID, "branches":
                                              ["claude/nobody"]},
                                             {"session_id": OTHER, "branches":
                                              ["claude/nobody"]}]},
                               True)[0], AMBIGUOUS)
    check("THE BODY FOOTER OUTRANKS THE REGISTER — it cannot go stale",
          grade(10857, pr, idle,
                {"sessions": [{"session_id": OTHER,
                               "branches": ["claude/mi83-x"]}]},
                True, None)["author"], SID)

    # the exception — spawn_gate's exact rules, imported not restated
    check("AN APPROVED EXCEPTION NAMING THIS PR PERMITS OVER A LIVE AUTHOR",
          grade(10857, pr, live, REG, RO, ok_exc)["state"], PERMITTED)
    check("`decision: pending` STILL REFUSES — filing is not granting",
          grade(10857, pr, live, REG, RO,
                dict(ok_exc, decision="pending"))["state"], REFUSED)
    check("an exception naming a DIFFERENT PR does not cover this one",
          grade(10857, pr, live, REG, RO,
                dict(ok_exc, covers=["10999"]))["state"], REFUSED)
    check("an exception naming NOTHING is a blanket bypass and refuses",
          grade(10857, pr, live, REG, RO, dict(ok_exc, covers=[]))["state"],
          REFUSED)
    check("an approval with NOBODY'S NAME on it refuses",
          grade(10857, pr, live, REG, RO,
                {k: v for k, v in ok_exc.items() if k != "approved_by"})["state"],
          REFUSED)
    check("...and the exception is graded by spawn_gate's OWN function, so the "
          "two cannot drift", sg.exception_covers(ok_exc, "10857")[0], True)

    # ⚠️ THE EXTRACTOR, PINNED — this suite ORIGINALLY PASSED WHILE THE TOOL WAS
    # BROKEN END TO END. Every case above builds its PR entry by hand, so all 40
    # passed while the real CLI path graded all 8 live open PRs `unidentified`:
    # it fed the payload through `manager_view.normalise_prs`, which drops `body`
    # and flattens `head`. Found only by running against a live read. These cases
    # exist so the join between the CLI and `grade()` cannot silently rot again.
    raw_payload = [{"number": 10895, "title": "t", "draft": True,
                    "head": {"ref": "claude/mi83-merge-ping-observability"},
                    "body": f"x\nhttps://claude.ai/code/{SID}\n"}]
    ent = open_pr_entries(raw_payload)
    check("the extractor KEEPS `body` — the strongest author link",
          (ent or [{}])[0].get("body", "").strip().endswith(SID), True)
    check("...and keeps `head` in its ORIGINAL dict shape, which "
          "`open_pr_record._head_ref` reads",
          opr._head_ref((ent or [{}])[0]),
          "claude/mi83-merge-ping-observability")
    check("...so a payload straight off `list_pull_requests` RESOLVES ITS AUTHOR "
          "(the end-to-end join the hand-built cases above could not catch)",
          resolve_author(find_pr(ent, 10895), 10895, REG, RO)[0], SID)
    check("...and a wrapper dict is descended, as it is for manager_view",
          (open_pr_entries({"pull_requests": raw_payload}) or [{}])[0]["number"],
          10895)
    check("an UNREADABLE open-PR payload is None, never [] — opposite facts",
          open_pr_entries("not json"), None)
    check("...and an EMPTY list is None too, for the same reason",
          open_pr_entries([]), None)

    if not quiet:
        print("pr-action-gate self-test:", "PASS" if not failures else "FAIL")
    return (not failures), failures


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--pr", default=None,
                    help="the PR to act on, e.g. 10857 or Metis-Insights#10857")
    ap.add_argument("--action", default="act on",
                    help=f"what you mean to do; e.g. {', '.join(KNOWN_ACTIONS)}")
    ap.add_argument("--live-sessions", default=None,
                    help="path to a `list_sessions` observation, or - for stdin. "
                         "⚠️ There is NO flag that asserts the author is fine.")
    ap.add_argument("--open-prs", default=None,
                    help="path to a `list_pull_requests` observation")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        ok, _ = _self_test()
        return 0 if ok else 1

    ok, failures = _self_test(quiet=True)
    if not ok:
        # Machinery that just failed to verify itself does not get to grade.
        print(f"pr-action-gate: REFUSING to grade — the planted-failure suite "
              f"did not pass ({len(failures)}): {'; '.join(failures[:3])}")
        return 4

    if not a.pr:
        print("pr-action-gate: [UNKNOWN] no --pr was named, so there is nothing "
              "to grade. WE DID NOT LOOK — this is not permission.")
        return _EXIT[UNKNOWN]

    v = grade_pr_action(a.pr, a.action, mv._load(a.live_sessions),
                        mv._load(a.open_prs))
    print(f"pr-action-gate: [{v['state'].upper()}] {v['reason']}")
    if a.json:
        print(json.dumps(v, indent=2, ensure_ascii=False))
    return _EXIT[v["state"]]


if __name__ == "__main__":
    raise SystemExit(main())
