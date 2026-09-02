#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::daily-brief-guard (--self-test) + a MANAGER
# close-out invocation (`--write`). Deliberately NOT a cron — see WHO GENERATES
# IT below; the one input that answers the operator's actual question is
# unreachable from a runner, so a cron would ship a brief that is structurally
# incapable of answering it and would say so in small print every morning.
"""THE DAILY BRIEF — what the operator is handed in the morning, GENERATED.

WHY THIS EXISTS (the operator's own words are the acceptance criterion)
-----------------------------------------------------------------------
Operator, 2026-09-02, on the daily cadence they set:

    "by the [brief] in the morning, I want that to include what was done
    overnight and what was wrapped up after I went to bed, **so that I know
    where I'm starting off from**."

They are the TRIGGER, not the author — they *get* the brief. Before this,
nothing produced one. `MI-75`.

⚠️ **A brief that renders but does not answer that sentence FAILS**, however
well-formed it is. That is why this module is split into a DELTA half (what
moved overnight) and a STATE half (where you are standing now): the first
alone is a changelog, and a changelog does not tell anyone where they are.

ONE ARTIFACT OR TWO? — THE CHOICE, ARGUED
------------------------------------------
Two consumers were named, and they are not identical:

  1. **The operator, in the morning.** Readable prose they paste as the opening
     of the next manager's prompt. Needs the overnight delta.
  2. **A successor manager, mid-day.** Needs live state: the checklist, live
     sub-sessions, open PRs *with the conditions attached to their approvals*,
     `loud` open-items, the lease, what is blocked and on what.

**Decision: ONE artifact, two clearly-headed sections, regenerable — not two
renderers.** The argument that settles it is the operator's own workflow: they
read the brief, add notes, and **paste it as the prompt that starts the next
manager**. That manager IS consumer (2). So an artifact carrying only the delta
would start a manager as under-informed as one arriving cold — the artifact is
literally the successor's input, so it must carry the successor's needs.

The mid-day case is then served by **re-running this**, which reads live files,
rather than by re-reading a stale morning copy. That is the honest answer to
"two consumers": one generator, two moments, and the second one re-runs it.

⚠️ **What the single-artifact choice costs, stated rather than hidden:** the
state section is the one that grows. `render_session_brief.py` records the
failure — *"a section that only grows is one more wall of text to skim past"*.
The line drawn here is **by STATE, not by count**: `in_flight`,
`landed_unproven` and `blocked` are enumerated in full and never truncated
(silently dropping live work is the failure the brief exists to prevent),
everything else is a count. Where a cap would be needed, the count is printed
beside the enumeration so a truncation could never hide.

WHO GENERATES IT — AND THE CONSTRAINT THIS DOES NOT FAKE AROUND
----------------------------------------------------------------
Three of the four inputs are reachable from anywhere:

  * what MERGED overnight        — git, via `work_digest.build_digest`
  * the registers and the store  — repo files
  * what is DUE                  — `render_due_list.collect`

The fourth is not. **What a night session CONCLUDED lives in `get_session`'s
`post_turn_summary`, and `mcp__*` tools are unavailable to CI and to
Routine-fired turns.** No amount of engineering in this file changes that.

So: **the brief is a CLOSE-OUT DELIVERABLE the night manager runs before it
ends**, not a cron. The manager holds `get_session` / `list_sessions`; it writes
what it observed into a `--session-notes` file and this renders it.

⚠️ **And it must still run WITHOUT that observation**, because the case the
whole of Phase E exists for is *the manager died*. Omitting `--session-notes`
therefore renders **`not_observed`** — a declared hole — never silence, and
never "nothing was concluded". A dead manager produces a brief that says what
it could not see, which is strictly better than no brief.

TWO VERDICT AXES, AND WHY NOT ONE
----------------------------------
`registersVerdict` grades the repo files; `observationsVerdict` grades the
MCP-only inputs. They are deliberately **not pooled**.

A single verdict would read `partial` on every CI run forever, because the live
observation is structurally absent there — and a permanently-degraded verdict
gets skimmed past, which is the exact hazard `CLAUDE.md` names for the constraint
readout. Splitting them keeps *"I could not read OPEN-ITEMS.json today"* — real,
transient, actionable — from being buried under *"no manager supplied a live
observation"*, which is expected and structural.

THE READ STATES, NEVER COLLAPSED
---------------------------------
  ``read``          opened and parsed.
  ``absent``        we LOOKED and it is not there. An observation.
  ``unreadable``    it exists and will not parse. **We could not look.**
  ``not_observed``  requires a live observation nothing here can make.

`absent` and `unreadable` are opposite facts, and `not_observed` is a third
thing again: nobody could have looked from here. Collapsing any two of them
reproduces the `curl … || echo '{}'` failure `CLAUDE.md` records — a watcher
that checked nothing and reported a clean result.

WHAT MUST NOT REGRESS
----------------------
  * **GENERATED, never hand-written.** A hand-written brief dies with the
    session that wrote it, which is the failure Phase E exists for.
  * **It DECLARES what it could not read**, per input, rather than omitting it.
  * **`landed_unproven` is NOT `done` and is never flattened into it.** A merge
    is a deploy, not an observation. Saying an item is finished when its effect
    was never observed actively misinforms the person starting the day, and the
    self-test asserts the two never render under one heading.

Usage::

    # the night manager, at close-out (the intended path)
    python3 scripts/ops/render_daily_brief.py \\
        --since '2026-09-02T22:00Z' \\
        --session-notes /tmp/night-sessions.json \\
        --live-sessions /tmp/list_sessions.json \\
        --write

    # anyone, any time — a brief with declared holes rather than no brief
    python3 scripts/ops/render_daily_brief.py

    python3 scripts/ops/render_daily_brief.py --self-test
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ── REUSED, NOT RE-DERIVED ────────────────────────────────────────────────
# `RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED` is a named recurrence here and one
# landed the day before this was written. Every one of these was measured on the
# live tree before this module was started:
#   * work_digest ALREADY renders the overnight delta (run over a real 24h
#     window it reported 44 state changes across 6/6 registers). Imported.
#   * render_due_list ALREADY owns the three-state source discipline and the
#     `partial`-must-name-the-source rule. Imported.
#   * session_registry ALREADY parses a pasted `list_sessions` observation,
#     tolerantly. Imported rather than given a second, drifting parser.
from scripts.ops import work_digest as _digest            # noqa: E402
from scripts.ops import render_due_list as _due           # noqa: E402
from scripts.ops.session_registry import _load_observation  # noqa: E402
#   * render_session_brief ALREADY renders the cycle priority AND already gets
#     its null case right (`no priority is set` and `the renderer broke` must
#     not look identical). A second copy would be free to drift from the block
#     in CLAUDE.md, which is the one a session actually inherits.
from scripts.ops.render_session_brief import priority_lines  # noqa: E402

BRIEF_DIR = REPO_ROOT / "comms" / "briefs"

_CHECKLIST = Path("docs/claude/work/MANAGER-CHECKLIST.json")
_SESSIONS = Path("docs/claude/work/SESSIONS.json")
_OPEN_PRS = Path("docs/claude/work/OPEN-PRS.json")
_LEASE = Path("docs/claude/work/MANAGER-LEASE.json")
_OPEN_ITEMS = Path("docs/claude/OPEN-ITEMS.json")
_CYCLE = Path("docs/claude/CYCLE-PRIORITY.json")

#: Every repo input this brief reads. Enumerated so §4 can report on ALL of
#: them, including the ones that read cleanly — an input that only appears when
#: it fails leaves the reader with no denominator.
REGISTER_INPUTS: tuple[tuple[str, Path], ...] = (
    ("cycle_priority", _CYCLE),
    ("manager_checklist", _CHECKLIST),
    ("sub_session_registry", _SESSIONS),
    ("open_pr_record", _OPEN_PRS),
    ("manager_lease", _LEASE),
    ("open_items", _OPEN_ITEMS),
)

#: Inputs that CANNOT be produced from here. Named so their absence is a
#: declared hole rather than an empty section.
OBSERVATION_INPUTS: tuple[tuple[str, str], ...] = (
    ("night_session_conclusions",
     "get_session's post_turn_summary — mcp__* is unavailable to CI and to "
     "Routine-fired turns, so only a manager can supply this (--session-notes)"),
    ("live_sub_sessions",
     "a list_sessions observation — same tool boundary (--live-sessions)"),
    ("open_pr_completeness",
     "a live open-PR list — api.github.com is 403 at the sandbox proxy "
     "(--open-prs)"),
)

READ_STATES = ("read", "absent", "unreadable", "not_observed")
REGISTER_VERDICTS = ("all_read", "partial", "none_read")
OBSERVATION_VERDICTS = ("all_observed", "partial", "none_observed")

#: Checklist states that need EYES, enumerated in full and never truncated.
#: The line is drawn by STATE rather than by count, because silently dropping a
#: live row is the failure this brief exists to prevent.
EYES_STATES: tuple[str, ...] = ("in_flight", "blocked", "landed_unproven")

#: ⚠️ `landed_unproven` is MERGED WITH ITS EFFECT UNOBSERVED. It is NOT `done`
#: and must never be counted, headed or summarised as such.
NOT_DONE_BUT_MERGED = "landed_unproven"
DONE_STATES: frozenset[str] = frozenset({"done"})


# ── reading, with the state kept ──────────────────────────────────────────

def read_json(path: Path, root: Path | None = None) -> tuple[Any, str]:
    """Return ``(data, state)`` where state ∈ read | absent | unreadable.

    ⚠️ `absent` and `unreadable` are OPPOSITE FACTS. A file that is not there
    was looked for and found missing; a file that will not parse was not looked
    at. Returning `None` for both — the obvious shape — is the collapse that
    makes a broken register indistinguishable from an empty one.
    """
    p = (root or REPO_ROOT) / path
    if not p.exists():
        return None, "absent"
    try:
        return json.loads(p.read_text(encoding="utf-8")), "read"
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None, "unreadable"


def registers_verdict(states: dict[str, str]) -> str:
    considered = list(states.values())
    if not considered:
        return "none_read"
    ok = [s for s in considered if s in ("read", "absent")]
    if len(ok) == len(considered):
        return "all_read"
    if not ok:
        return "none_read"
    return "partial"


def observations_verdict(states: dict[str, str]) -> str:
    considered = list(states.values())
    if not considered:
        return "none_observed"
    seen = [s for s in considered if s == "read"]
    if len(seen) == len(considered):
        return "all_observed"
    if not seen:
        return "none_observed"
    return "partial"


# ── the overnight window ──────────────────────────────────────────────────

#: Accepted `--since` forms. Kept narrow on purpose: this is a human typing
#: "when I went to bed", and every accepted form must be one git ALSO parses
#: the same way, or the validation would pass a string git then reads
#: differently.
_SINCE_FORMATS = ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%MZ",
                  "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                  "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


class BadSince(ValueError):
    """A `--since` string this cannot vouch for. REFUSED, never guessed."""


def parse_since(since: str) -> datetime:
    """Validate `--since` OURSELVES, because git will not.

    ⚠️ **THIS IS THE WHOLE POINT AND IT IS NOT DEFENSIVE PROGRAMMING.**
    `git rev-list -1 --before=<garbage> HEAD` does NOT fail — it **ignores the
    unparseable date and returns HEAD**, exit 0. Measured:

        $ git rev-list -1 --before="not-a-timestamp" HEAD
        b37b15ff…            # ← HEAD. rc=0.

    Handed to `resolve_since` that produces a base of HEAD, so the overnight
    window is `HEAD..HEAD` — **EMPTY** — and the brief then reports a quiet
    night for a window nobody chose, with total confidence and no hint that the
    argument was thrown away. A typo in the one flag that means *"when I went to
    bed"* would silently answer the operator's question with "nothing
    happened".

    That is the `curl … || echo '{}'` shape one level up: a confident negative
    produced by a step that never ran. So an unparseable value is **REFUSED**,
    loudly, rather than resolved into a lie.
    """
    s = since.strip()
    for fmt in _SINCE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        return dt.replace(tzinfo=dt.tzinfo or timezone.utc)
    raise BadSince(
        f"--since {since!r} is not a timestamp this can vouch for. "
        f"git would IGNORE it and silently use HEAD, making the overnight "
        f"window empty and the brief report a quiet night for a window nobody "
        f"chose. Accepted: {', '.join(_SINCE_FORMATS)} "
        f"(e.g. '2026-09-02T22:00Z')."
    )


def resolve_since(since: str | None, *, now: datetime, root: Path | None = None) -> tuple[str, str]:
    """Resolve the overnight window's base ref, and SAY how it was chosen.

    Returns ``(ref, how)``. ``how`` is rendered, because "the window you asked
    for" and "the window we could actually resolve" are different claims and
    only the first is what the operator meant by *after I went to bed*.

    Raises `BadSince` on a value git would silently ignore — see `parse_since`.
    """
    cwd = root or REPO_ROOT
    if since is not None:
        parse_since(since)  # refuse before git gets a chance to ignore it
    when = since or (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        out = subprocess.run(
            ["git", "rev-list", "-1", f"--before={when}", "HEAD"],
            cwd=cwd, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError) as exc:
        return "", f"could not resolve a base ref for {when} ({type(exc).__name__})"
    if not out:
        # ⚠️ NOT "nothing happened" — no commit is that old HERE.
        # ⚠️ AND THE CAUSE IS MEASURED, NOT ASSUMED. This line used to assert
        # "the clone is shallow" unconditionally, which is UNPROVENANCED
        # DIAGNOSTIC OUTPUT sub-class A: a failure message naming a cause no
        # code path tested. A full clone of a young repo hits this too, and a
        # reader told "shallow" would go and deepen a clone that is already
        # complete. `_is_shallow` is imported from work_digest rather than
        # re-implemented — two copies of "is this clone shallow" is how they
        # would come to disagree.
        why = ("this clone is SHALLOW, so older history is simply absent — "
               "deepen it (`git fetch --depth=N`) and re-run"
               if _digest._is_shallow() else
               "this clone is COMPLETE, so no commit is genuinely that old — "
               "the window predates the repo, or the timestamp is wrong")
        return "", (f"no commit at or before {when} in this clone: {why}. "
                    f"The window could NOT be established")
    return out, f"first commit at or before {when}"


# ── the state half ────────────────────────────────────────────────────────

def checklist_view(doc: Any) -> dict[str, Any]:
    """Split the checklist by state, keeping `landed_unproven` OUT of `done`."""
    items = (doc or {}).get("items") or []
    by_state: dict[str, list[dict]] = {}
    for it in items:
        by_state.setdefault(str(it.get("state") or "unstated"), []).append(it)
    return {
        "total": len(items),
        "managerSession": (doc or {}).get("manager_session"),
        "asOf": (doc or {}).get("as_of") or (doc or {}).get("updated_at"),
        "counts": {k: len(v) for k, v in sorted(by_state.items())},
        "byState": by_state,
        # Published as its own number so no consumer can arrive at a "finished"
        # figure by adding it to `done`.
        "doneCount": sum(len(v) for k, v in by_state.items() if k in DONE_STATES),
        "mergedEffectUnobservedCount": len(by_state.get(NOT_DONE_BUT_MERGED, [])),
    }


def _blocked_on_text(it: dict) -> str:
    edges = it.get("blocked_on")
    if not edges:
        return ""
    if isinstance(edges, str):
        return edges
    parts = []
    for e in edges if isinstance(edges, list) else [edges]:
        if isinstance(e, dict):
            parts.append(f"{e.get('kind', '?')}:{e.get('ref', '?')}"
                         + (f" — {e['what']}" if e.get("what") else ""))
        else:
            parts.append(str(e))
    return "; ".join(parts)


def open_pr_view(doc: Any) -> dict[str, Any]:
    """Open-PR rows, carrying each approval's CONDITION and SCOPE verbatim.

    ⚠️ These are NOT summarised, and that is the whole point of the section.
    `docs/claude/work/README.md`: a successor knowing *nothing* about an
    approval stalls and re-asks (safe); one knowing *"approved"* without the
    condition could merge a demo-only change onto a real-money account. **Only
    the half-informed case is dangerous**, so compressing `operator_decision`
    into its verdict would manufacture exactly that reader.
    """
    rows = (doc or {}).get("open_prs") or []
    out = []
    for r in rows:
        d = r.get("operator_decision")
        if isinstance(d, dict):
            decision = {
                "verdict": d.get("verdict"),
                "condition": d.get("condition"),
                "scope": d.get("scope"),
                "decidedOn": d.get("decided_on"),
                "text": d.get("text"),
                # An older free-text row is graded, never passed as a verdict.
                "form": "typed",
            }
        elif d:
            decision = {"verdict": None, "condition": None, "scope": None,
                        "decidedOn": None, "text": str(d),
                        "form": "prose_ungradeable"}
        else:
            decision = {"verdict": None, "condition": None, "scope": None,
                        "decidedOn": None, "text": None, "form": "none_recorded"}
        out.append({
            "pr": r.get("pr") or r.get("number"),
            "title": r.get("title"),
            "owner": r.get("owner"),
            "intent": r.get("intent") or r.get("why"),
            "decision": decision,
        })
    return {
        "rows": out,
        "count": len(out),
        "asOf": (doc or {}).get("as_of"),
        "lastReconciledAt": (doc or {}).get("last_reconciled_at"),
    }


def loud_open_items(doc: Any) -> list[dict]:
    """`loud: true` rows — the ones a closing summary must REPORT ON."""
    items = (doc or {}).get("items") or (doc if isinstance(doc, list) else [])
    return [
        {"id": i.get("id"), "summary": i.get("summary") or i.get("title"),
         "state": i.get("state") or i.get("kind"),
         "verifiedAt": i.get("verified_at")}
        for i in items if isinstance(i, dict) and i.get("loud") is True
    ]


def lease_view(doc: Any, state: str, *, now: datetime) -> dict[str, Any]:
    if state != "read":
        # ⚠️ An unreadable lease is NOT an unheld one. `manager_lease.py`
        # REFUSES a claim on `unreadable` for exactly this reason.
        return {"state": f"lease_{state}", "holder": None, "expiresAt": None,
                "expired": None}
    holder, expires = (doc or {}).get("holder"), (doc or {}).get("expires_at")
    expired = None
    if expires:
        try:
            exp = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            expired = exp <= now
        except ValueError:
            expired = None  # undateable — never rendered as "still valid"
    return {"state": (doc or {}).get("state"), "holder": holder,
            "expiresAt": expires, "expired": expired}


def session_notes_view(notes: Any, state: str) -> dict[str, Any]:
    if state != "read":
        return {"state": state, "sessions": [], "observedAt": None, "observedBy": None}
    rows = (notes or {}).get("sessions") or []
    return {
        "state": "read",
        "observedAt": (notes or {}).get("observed_at"),
        "observedBy": (notes or {}).get("observed_by"),
        "sessions": [
            {"sessionId": s.get("session_id"), "title": s.get("title"),
             "status": s.get("status"),
             "concluded": s.get("concluded") or s.get("post_turn_summary"),
             "needsAction": s.get("needs_action")}
            for s in rows if isinstance(s, dict)
        ],
    }


# ── assembly ──────────────────────────────────────────────────────────────

def build(*, now: datetime | None = None, since: str | None = None,
          session_notes_path: str | None = None,
          live_sessions_path: str | None = None,
          open_prs_path: str | None = None,
          root: Path | None = None,
          due_token: str | None = None) -> dict[str, Any]:
    """Assemble the brief. Reads files and git; writes nothing."""
    now = now or datetime.now(timezone.utc)
    root = root or REPO_ROOT

    docs: dict[str, Any] = {}
    reg_states: dict[str, str] = {}
    for name, path in REGISTER_INPUTS:
        docs[name], reg_states[name] = read_json(path, root)

    # ── the DELTA half — imported wholesale from work_digest ──────────────
    base, how = resolve_since(since, now=now, root=root)
    digest = _digest.build_digest(base or "___no_such_ref___", "HEAD", now=now)
    digest_text = _digest.render(digest)

    # ── the observation half ─────────────────────────────────────────────
    obs_states: dict[str, str] = {k: "not_observed" for k, _ in OBSERVATION_INPUTS}
    notes_doc: Any = None
    if session_notes_path:
        notes_doc, st = read_json(Path(session_notes_path), Path("/"))
        if st == "absent":  # a path was GIVEN and is not there — a real defect
            obs_states["night_session_conclusions"] = "absent"
        else:
            obs_states["night_session_conclusions"] = st
    live = None
    if live_sessions_path:
        try:
            live = _load_observation(live_sessions_path)
            obs_states["live_sub_sessions"] = "read" if live else "unreadable"
        except (OSError, ValueError):
            obs_states["live_sub_sessions"] = "unreadable"
    live_prs = None
    if open_prs_path:
        try:
            live_prs = _load_observation(open_prs_path)
            obs_states["open_pr_completeness"] = "read" if live_prs else "unreadable"
        except (OSError, ValueError):
            obs_states["open_pr_completeness"] = "unreadable"

    # ── what is DUE — imported wholesale from render_due_list ─────────────
    due_env = _due.build(_due.collect(root, now.date(), token=due_token), now=now)

    return {
        "schemaVersion": 1,
        "whatThisIs": (
            "The DAILY BRIEF. GENERATED by scripts/ops/render_daily_brief.py — "
            "never hand-written, because a hand-written brief dies with the "
            "session that wrote it. Read §0 before anything else: it says what "
            "this brief could NOT see."
        ),
        "generatedAt": now.replace(microsecond=0).isoformat(),
        "forDate": now.date().isoformat(),
        "window": {"base": base or None, "how": how,
                   "resolved": bool(base), "since": since},
        "registerStates": reg_states,
        "registersVerdict": registers_verdict(reg_states),
        "observationStates": obs_states,
        "observationsVerdict": observations_verdict(obs_states),
        "digest": digest,
        "digestText": digest_text,
        "sessionNotes": session_notes_view(notes_doc,
                                           obs_states["night_session_conclusions"]),
        "liveSessions": live,
        "checklist": checklist_view(docs.get("manager_checklist")),
        "openPrs": open_pr_view(docs.get("open_pr_record")),
        "lease": lease_view(docs.get("manager_lease"),
                            reg_states["manager_lease"], now=now),
        "loudOpenItems": loud_open_items(docs.get("open_items")),
        "cyclePriority": docs.get("cycle_priority") or {},
        "due": due_env,
        # ⚠️ NEVER True. This brief reads the registers, the work store and git;
        # it does not read `src/`, the VM, or the three review backlogs' bodies.
        # A consumer treating it as the whole of system state has misread it.
        "coverageComplete": False,
    }


# ── rendering ─────────────────────────────────────────────────────────────

_HOLE = {"absent": "⛔ ABSENT (we looked; it is not there)",
         "unreadable": "⛔ UNREADABLE (**we could not look** — this is not 'empty')",
         "not_observed": "◻️ NOT OBSERVED (nothing here could look — see §4)",
         "read": "✅ read"}


def _hdr(b: dict) -> list[str]:
    rv, ov = b["registersVerdict"], b["observationsVerdict"]
    L = [f"# DAILY BRIEF — {b['forDate']}", "",
         f"_Generated `{b['generatedAt']}` by `scripts/ops/render_daily_brief.py`._",
         "_**GENERATED — do not hand-edit.** Re-run it; a hand-written brief dies "
         "with the session that wrote it._", ""]
    L += ["## §0 — WHAT THIS BRIEF COULD NOT SEE", ""]
    L += [f"- **Registers: `{rv}`**" + (
        "  — every register answered." if rv == "all_read"
        else "  — ⚠️ at least one register could not be read. Sections below are a "
             "LOWER BOUND: an empty one may mean nothing is there, or may mean "
             "nobody looked. See §4."), ]
    L += [f"- **Live observations: `{ov}`**" + (
        "  — a manager supplied every live observation." if ov == "all_observed"
        else "  — ⚠️ one or more inputs need a live `mcp__*` observation that "
             "**cannot be produced by CI or by a Routine-fired turn**. Absent "
             "here means *nobody could look*, never *nothing happened*. See §4."), ""]
    L += ["> These two verdicts are **deliberately not pooled.** The live half is "
          "structurally degraded outside a manager session, and a single verdict "
          "would read `partial` forever — which is how a readout gets skimmed "
          "past. A register that failed **today** must stay visible.", ""]
    return L


def _overnight(b: dict) -> list[str]:
    L = ["---", "", "## §1 — WHAT HAPPENED OVERNIGHT", "",
         f"_Window: {b['window']['how']}._", ""]
    if not b["window"]["resolved"]:
        L += ["> ⛔ **THE WINDOW COULD NOT BE ESTABLISHED.** Everything below "
              "describes a window nobody examined. This is *we could not look*, "
              "**not** a quiet night.", ""]
    L += ["### What merged and what moved", "", "```",
          b["digestText"].rstrip(), "```", ""]

    sn = b["sessionNotes"]
    L += ["### What the night session(s) CONCLUDED", ""]
    if sn["state"] != "read":
        L += [f"{_HOLE[sn['state']]}", "",
              "> This is the one thing git cannot tell you. A session's "
              "conclusions live in `get_session`'s `post_turn_summary`, and "
              "`mcp__*` tools are unavailable to CI and to Routine-fired turns — "
              "so only a MANAGER can supply it, with `--session-notes`. "
              "**Its absence is a declared hole, not evidence that nothing was "
              "concluded.**", ""]
    elif not sn["sessions"]:
        L += ["A manager supplied an observation and it named **no sessions**. "
              "That is a reading, not a hole.", ""]
    else:
        L += [f"_Observed {sn['observedAt'] or '(undated)'} by "
              f"`{sn['observedBy'] or '(unnamed)'}`._", ""]
        for s in sn["sessions"]:
            L.append(f"- **`{s['sessionId']}`** — {s['title'] or '(untitled)'}"
                     f" · status `{s['status'] or 'unstated'}`")
            if s["concluded"]:
                L.append(f"  - **Concluded:** {s['concluded']}")
            if s["needsAction"]:
                L.append(f"  - 🔔 **Needs you:** {s['needsAction']}")
        L.append("")
    return L


def _standing(b: dict) -> list[str]:
    ck, pr, ls = b["checklist"], b["openPrs"], b["lease"]
    L = ["---", "", "## §2 — WHERE YOU ARE STARTING FROM", ""]

    # ⚠️ RENDERED BY THE SAME FUNCTION THAT WRITES THE CLAUDE.md BLOCK, so the
    # priority the operator reads here and the one a session inherits can never
    # disagree. It also already handles the null case correctly.
    L += priority_lines(b["cyclePriority"] or {}, date.fromisoformat(b["forDate"]))
    L += ["> The **constraint readout** and the **sunset pass** are deliberately "
          "NOT repeated here — both are already inlined in `CLAUDE.md`'s SESSION "
          "BRIEF, which every session receives before its first tool call, and a "
          "second copy would be free to drift from the one that actually reaches "
          "a session. §3 below DOES carry the due list, because the operator "
          "reading this does not read `CLAUDE.md`.", ""]

    exp = {True: "⛔ **EXPIRED**", False: "held", None: "⚠️ undateable — "
           "**not** rendered as valid"}[ls["expired"]]
    L += [f"**Manager lease:** `{ls['state']}`"
          + (f", holder `{ls['holder']}`" if ls["holder"] else "")
          + (f", expires `{ls['expiresAt']}` ({exp})" if ls["expiresAt"] else ""), ""]

    if b["registerStates"]["manager_checklist"] != "read":
        L += [f"**Manager checklist:** {_HOLE[b['registerStates']['manager_checklist']]}"
              " — the standing picture below is missing its centre.", ""]
        return L

    counts = ", ".join(f"`{k}` {v}" for k, v in ck["counts"].items())
    L += [f"**Manager checklist** — {ck['total']} items (as of "
          f"{ck['asOf'] or 'undated'}, manager `{ck['managerSession']}`): {counts}.", ""]
    L += [f"> ⚠️ **`{NOT_DONE_BUT_MERGED}` ({ck['mergedEffectUnobservedCount']}) is NOT "
          f"`done` ({ck['doneCount']}).** A merge is a deploy, not an observation. "
          "The two are counted and listed separately below and are never added "
          "together — an item reported as finished whose effect was never seen "
          "actively misinforms the person starting the day.", ""]

    for state in EYES_STATES:
        rows = ck["byState"].get(state, [])
        head = {"in_flight": "🔧 IN FLIGHT — a session is actively working it",
                "blocked": "⛔ BLOCKED — waiting on a named thing",
                "landed_unproven": ("⏳ LANDED, EFFECT UNOBSERVED — merged; "
                                    "**NOT done**")}[state]
        L += [f"### {head} ({len(rows)})", ""]
        if not rows:
            L += ["_None._", ""]
            continue
        for it in rows:
            line = f"- **{it.get('id')}** — {it.get('title') or '(untitled)'}"
            if it.get("owner"):
                line += f" · owner `{it['owner']}`"
            if it.get("pr"):
                line += f" · {it['pr']}"
            L.append(line)
            bo = _blocked_on_text(it)
            if bo:
                L.append(f"  - blocked on: {bo}")
            if state == NOT_DONE_BUT_MERGED and it.get("landed_unproven_because"):
                L.append(f"  - unproven because: {it['landed_unproven_because']}")
        L.append("")
    other = {k: v for k, v in ck["counts"].items() if k not in EYES_STATES}
    if other:
        L += ["_Everything else, by count only (nothing here needs your eyes "
              "this morning): " + ", ".join(f"`{k}` {v}" for k, v in other.items())
              + "._", ""]

    # ── open PRs ─────────────────────────────────────────────────────────
    L += [f"### 📋 OPEN PRs YOU INHERIT ({pr['count']})", ""]
    if b["registerStates"]["open_pr_record"] != "read":
        L += [f"{_HOLE[b['registerStates']['open_pr_record']]} — "
              "`docs/claude/work/OPEN-PRS.json`.", ""]
    elif not pr["rows"]:
        L += [f"The record holds **no rows** (as of {pr['asOf'] or 'undated'}).", "",
              "> ⚠️ That is *the record is empty*, **not** *no PR is open*. "
              "Completeness needs a live open-PR list, which is "
              f"`{b['observationStates']['open_pr_completeness']}` here — see §4.", ""]
    else:
        L += ["> ⚠️ **Read the CONDITION, not just the verdict.** A successor "
              "knowing *nothing* about an approval stalls and re-asks, which is "
              "safe; one knowing *\"approved\"* without its condition could merge "
              "a demo-only change onto a real-money account. Only the "
              "half-informed case is dangerous, so these are reproduced "
              "verbatim and never summarised.", ""]
        for r in pr["rows"]:
            L.append(f"- **{r['pr']}** — {r['title'] or '(untitled)'}"
                     + (f" · owner `{r['owner']}`" if r["owner"] else ""))
            d = r["decision"]
            if d["form"] == "none_recorded":
                L.append("  - operator decision: **none recorded** "
                         "(`unknown`, never a pass)")
            elif d["form"] == "prose_ungradeable":
                L.append("  - operator decision: ⚠️ **`prose_ungradeable`** — "
                         "free-text form, which is `unknown`, never a pass: "
                         f"“{d['text']}”")
            else:
                L.append(f"  - verdict: **`{d['verdict']}`**"
                         + (f" ({d['decidedOn']})" if d["decidedOn"] else ""))
                if d["condition"]:
                    L.append(f"  - ⚠️ **CONDITION:** {d['condition']}")
                if d["scope"]:
                    L.append(f"  - ⚠️ **SCOPE:** {d['scope']}")
                if d["text"]:
                    L.append(f"  - operator's own words: “{d['text']}”")
        L.append("")

    # ── sub-sessions ─────────────────────────────────────────────────────
    L += ["### 🧵 SUB-SESSIONS", ""]
    if b["observationStates"]["live_sub_sessions"] == "read":
        n = len(b["liveSessions"]) if isinstance(b["liveSessions"], list) else "?"
        L += [f"A live observation was supplied ({n} rows). Reconcile it against "
              "the registry with `python3 scripts/ops/handoff_check.py "
              "--live-sessions <file>` — this brief reports, it does not grade.", ""]
    else:
        L += [f"◻️ **{b['observationStates']['live_sub_sessions']}** — no live "
              "`list_sessions` observation. `docs/claude/work/SESSIONS.json` is "
              "the registry, and it has been measured **incomplete twice** "
              "(3 of 6, then 6 of 9 with 5 live), so reading it alone is a lower "
              "bound and never a roster.", ""]

    # ── loud open-items ──────────────────────────────────────────────────
    loud = b["loudOpenItems"]
    L += [f"### 🔔 `loud` OPEN-ITEMS — must be REPORTED ON, not silently carried "
          f"({len(loud)})", ""]
    if b["registerStates"]["open_items"] != "read":
        L += [f"{_HOLE[b['registerStates']['open_items']]} — "
              "`docs/claude/OPEN-ITEMS.json`.", ""]
    elif not loud:
        L += ["_None._", ""]
    else:
        for i in loud:
            L.append(f"- **{i['id']}** — last observed "
                     f"`{i['verifiedAt'] or 'never'}`")
            if i["summary"]:
                L.append(f"  - {str(i['summary'])[:400]}")
        L.append("")
    return L


def _due_section(b: dict) -> list[str]:
    env = b["due"]
    L = ["---", "", f"## §3 — WHAT IS DUE ({env['counts']['due']}, "
         f"{env['counts']['loud']} loud)", "",
         f"_From `scripts/ops/render_due_list.py` · verdict "
         f"**`{env['verdict']}`**._", ""]
    if env["verdict"] != "all_sources_read":
        L += ["> ⚠️ **LOWER BOUND.** Could not read: "
              f"`{'`, `'.join(env['unreadable_sources']) or '(none)'}`.", ""]
    if not env["rows"]:
        L += ["Nothing due from the sources that answered.", ""]
    for r in env["rows"]:
        age = f" · {r['age_days']}d" if r["age_days"] is not None else ""
        L.append(f"- {'🔔 ' if r['loud'] else ''}**{r['id']}** "
                 f"({r['source']}{age}) — {r['why_due']}")
    L.append("")
    L.append("_This list decides nothing. Every row is for a session to judge. "
             "It is `scripts/ops/render_due_list.py`'s output VERBATIM — the same "
             "rows it writes to `docs/claude/DUE.md` — and is deliberately NOT "
             "truncated, because silently dropping a due row is the exact failure "
             "the due-list exists to prevent._")
    L.append("")
    return L


def _inputs(b: dict) -> list[str]:
    L = ["---", "", "## §4 — EVERY INPUT, AND WHETHER IT WAS READ", "",
         "_Listed in full, including the ones that read cleanly. An input that "
         "only appears when it fails leaves the reader with no denominator._", "",
         "| input | state | |", "|---|---|---|"]
    for name, path in REGISTER_INPUTS:
        st = b["registerStates"][name]
        L.append(f"| `{name}` | `{st}` | `{path}` |")
    for name, why in OBSERVATION_INPUTS:
        L.append(f"| `{name}` | `{b['observationStates'][name]}` | {why} |")
    L += ["", "**The four states are never collapsed.** `absent` = we looked and "
          "it is not there. `unreadable` = it exists and will not parse, i.e. "
          "**we could not look**. `not_observed` = nothing here could look at "
          "all. `read` = opened and parsed. Reporting any of the first three as "
          "the fourth is the `curl … || echo '{}'` failure this repo records: a "
          "watcher that checked nothing and reported a clean result.", "",
          "⚠️ **`coverageComplete` is `false` on every brief.** This reads the "
          "registers, the work store and git. It does NOT read `src/`, either "
          "VM, or the bodies of the three review backlogs. A reader treating it "
          "as the whole of system state has misread it.", ""]
    return L


def _footer(b: dict) -> list[str]:
    return ["---", "",
            "## HOW TO USE THIS", "",
            "**In the morning:** read §1, add your notes, and paste the whole "
            "thing as the opening of the next manager's prompt. §2 is what that "
            "manager needs in order not to arrive cold.", "",
            "**Mid-day, taking over:** do not read a stale copy — **re-run it**, "
            "`python3 scripts/ops/render_daily_brief.py`. It reads live files, so "
            "a regenerated brief is the current state and a saved one is a "
            "snapshot.", "",
            f"_Window base: `{b['window']['base'] or '(unresolved)'}` · "
            f"registers `{b['registersVerdict']}` · observations "
            f"`{b['observationsVerdict']}`._", ""]


def render(b: dict) -> str:
    parts = (_hdr(b) + _overnight(b) + _standing(b)
             + _due_section(b) + _inputs(b) + _footer(b))
    return "\n".join(parts).rstrip() + "\n"


# ── self-test: planted controls in BOTH directions ────────────────────────

def _self_test() -> int:
    now = datetime(2026, 9, 3, 6, 20, tzinfo=timezone.utc)
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(f"  self-test ({name}): {'PASS' if cond else 'FAIL'}")
        ok = ok and cond

    # 1+2 — absent and unreadable must NOT collapse into one another.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        r = Path(td)
        (r / "docs/claude/work").mkdir(parents=True)
        (r / "docs/claude/work/MANAGER-LEASE.json").write_text("{not json",
                                                              encoding="utf-8")
        _, s_absent = read_json(Path("docs/claude/work/SESSIONS.json"), r)
        _, s_bad = read_json(Path("docs/claude/work/MANAGER-LEASE.json"), r)
        check("absent and unreadable are different states",
              s_absent == "absent" and s_bad == "unreadable")
        check("an unreadable register downgrades the verdict, an absent one does not",
              registers_verdict({"a": "absent"}) == "all_read"
              and registers_verdict({"a": "absent", "b": "unreadable"}) == "partial")

    # 3 — the two verdict axes are independent. A perfect register read must not
    #     be dragged down by the structurally-absent live observation, and a
    #     supplied observation must not paper over a broken register.
    check("the two verdict axes do not contaminate each other",
          registers_verdict({"a": "read"}) == "all_read"
          and observations_verdict({"x": "not_observed"}) == "none_observed"
          and observations_verdict({"x": "read", "y": "not_observed"}) == "partial")

    # 4+5 — `landed_unproven` is never folded into `done`. THE load-bearing one.
    ck = checklist_view({"items": [
        {"id": "A", "state": "done", "title": "observed"},
        {"id": "B", "state": "landed_unproven", "title": "merged only",
         "landed_unproven_because": "no fleet observation yet"},
        {"id": "C", "state": "landed_unproven", "title": "also merged only"},
        {"id": "D", "state": "queued", "title": "not started"},
    ]})
    check("done count EXCLUDES landed_unproven",
          ck["doneCount"] == 1 and ck["mergedEffectUnobservedCount"] == 2)

    b = {
        "forDate": "2026-09-03", "generatedAt": now.isoformat(),
        "registersVerdict": "all_read", "observationsVerdict": "none_observed",
        "registerStates": {k: "read" for k, _ in REGISTER_INPUTS},
        "observationStates": {k: "not_observed" for k, _ in OBSERVATION_INPUTS},
        "window": {"base": "abc1234", "how": "test window", "resolved": True,
                   "since": None},
        "digestText": "[work digest] test\n1 state change.",
        "digest": {},
        "sessionNotes": {"state": "not_observed", "sessions": [],
                         "observedAt": None, "observedBy": None},
        "liveSessions": None, "checklist": ck,
        "openPrs": {"rows": [], "count": 0, "asOf": None, "lastReconciledAt": None},
        "lease": {"state": "held", "holder": "s1", "expiresAt": "2026-09-03T07:00:00Z",
                  "expired": False},
        "loudOpenItems": [], "cyclePriority": {}, "due": _due.build([], now=now),
        "coverageComplete": False,
    }
    md = render(b)
    lu_head = md.index("LANDED, EFFECT UNOBSERVED")
    check("landed_unproven rows render under a NOT-done heading",
          "**NOT done**" in md
          and md.index("- **B**") > lu_head and md.index("- **C**") > lu_head)
    check("the done/unproven distinction is stated, not merely counted",
          "is NOT `done`" in md and "A merge is a deploy, not an observation" in md)

    # 6 — an unsupplied observation SAYS SO, in operator-visible prose, and says
    #     it is not evidence of absence.
    check("an unobserved night says 'nobody could look', not 'nothing happened'",
          "NOT OBSERVED" in md
          and "not evidence that nothing was" in md.replace("\n", " "))

    # 7 — an EMPTY open-PR record must not read as "no PR is open".
    check("an empty open-PR record declares itself empty, not complete",
          "not* *no PR is open*" in md.replace("\n", " ").replace("**", "*")
          or "not** *no PR is open*" in md.replace("\n", " "))

    # 8 — the negative control: a SUPPLIED observation must change the output.
    #     One direction proves a branch runs, never that it discriminates.
    b2 = dict(b)
    b2["sessionNotes"] = {"state": "read", "observedAt": "2026-09-03T05:00Z",
                          "observedBy": "session_x",
                          "sessions": [{"sessionId": "s9", "title": "night work",
                                        "status": "archived",
                                        "concluded": "shipped the thing",
                                        "needsAction": "merge #1"}]}
    b2["observationStates"] = dict(b["observationStates"],
                                   night_session_conclusions="read")
    md2 = render(b2)
    check("a supplied observation renders the conclusion and drops the hole",
          "shipped the thing" in md2 and "Needs you:** merge #1" in md2
          and "NOT OBSERVED" not in md2.split("## §2")[0])

    # 9 — an open-PR approval's CONDITION and SCOPE survive rendering verbatim.
    #     Compressing them into the verdict manufactures the half-informed
    #     reader that is the one dangerous case.
    v = open_pr_view({"open_prs": [{
        "pr": "#10746", "title": "graded coverage",
        "operator_decision": {
            "verdict": "approved_with_conditions",
            "condition": "hold arming until the soak",
            "scope": "bybit_1 (demo) ONLY. NOT fleet-wide.",
            "decided_on": "2026-09-02", "text": "hold it until the soak"}}]})
    b3 = dict(b, openPrs=v)
    md3 = render(b3)
    check("an approval's condition and scope render VERBATIM",
          "hold arming until the soak" in md3
          and "bybit_1 (demo) ONLY" in md3 and "hold it until the soak" in md3)

    # 10 — a free-text decision grades ungradeable rather than passing as approved.
    v2 = open_pr_view({"open_prs": [{"pr": "#1", "title": "t",
                                     "operator_decision": "he said yes I think"}]})
    check("a prose decision grades `prose_ungradeable`, never a pass",
          v2["rows"][0]["decision"]["form"] == "prose_ungradeable"
          and "prose_ungradeable" in render(dict(b, openPrs=v2)))

    # 11 — an unreadable lease is not an unheld one.
    check("an unreadable lease reports its read failure, not 'nobody holds it'",
          lease_view(None, "unreadable", now=now)["state"] == "lease_unreadable")

    # 12 — an unresolvable window says so LOUDLY and is never a quiet night.
    b4 = dict(b, window={"base": None, "how": "no commit that old", "resolved": False,
                         "since": None})
    check("an unresolvable window is 'we could not look', not a quiet night",
          "COULD NOT BE ESTABLISHED" in render(b4)
          and "not** a quiet night" in render(b4))

    # 12b — a `--since` git would SILENTLY IGNORE is REFUSED, not resolved.
    #       git returns HEAD (rc 0) for an unparseable --before, which would
    #       make the window empty and the brief report a quiet night for a
    #       window nobody chose. Both directions: garbage refuses, valid passes.
    refused = True
    try:
        parse_since("not-a-timestamp")
        refused = False
    except BadSince:
        pass
    check("an unparseable --since is REFUSED, never resolved to HEAD",
          refused and parse_since("2026-09-02T22:00Z").hour == 22
          and parse_since("2026-09-02").day == 2)

    # 12c — the shallow claim is MEASURED, not asserted. A failure message
    #       naming a cause no code path tested is diagnostic-provenance
    #       sub-class A, and this line used to assert "shallow" unconditionally.
    _, how_old = resolve_since("1999-01-01T00:00:00Z", now=now)
    check("the no-commit-that-old message names a MEASURED cause",
          ("SHALLOW" in how_old) is _digest._is_shallow()
          and ("COMPLETE" in how_old) is not _digest._is_shallow())

    # 13 — every declared input appears in §4, so there is always a denominator.
    md5 = render(b)
    check("§4 lists every declared input, read or not",
          all(f"`{n}`" in md5 for n, _ in REGISTER_INPUTS)
          and all(f"`{n}`" in md5 for n, _ in OBSERVATION_INPUTS))

    # 14 — every declared register path exists on disk. A brief that reads a
    #      path nobody writes would report `absent` forever and look fine.
    missing = [str(p) for _, p in REGISTER_INPUTS if not (REPO_ROOT / p).exists()]
    check(f"every declared register exists on disk (missing: {missing or 'none'})",
          not missing)

    # 15 — coverage is declared incomplete, always.
    check("coverage is declared incomplete", "`coverageComplete` is `false`" in md5)

    print(f"daily-brief self-test: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def _check() -> int:
    """Render the STATE half over the LIVE registers and assert the invariants.

    ⚠️ **THIS GRADES THE CODE, NOT THE DATA — and the distinction is the whole
    design of this check.** A register that will not parse is a real problem,
    but failing here on it would red every open PR for a defect none of them
    introduced — the lesson `session-brief-guard` already learned
    (`BL-20260830-A-TRANSIENT-RED-BASE-PERMANENTLY-STRANDS-AN-AUTOMERGE-BRANCH`),
    and `workflow-trigger-reachability`'s "an unreadable origin PASSES
    (loudly)". So an unreadable register is REPORTED, loudly, and passes; what
    FAILS is the renderer raising, or a load-bearing sentence going missing.

    Deliberately offline and windowless: no git window, no due-list collection.
    Both reach the network or the clone's depth, and a guard that can fail on a
    shallow checkout or an API blip is a guard that reds unrelated PRs.
    """
    now = datetime.now(timezone.utc)
    docs, states = {}, {}
    for name, path in REGISTER_INPUTS:
        docs[name], states[name] = read_json(path)

    b = {
        "schemaVersion": 1, "forDate": now.date().isoformat(),
        "generatedAt": now.replace(microsecond=0).isoformat(),
        "registerStates": states, "registersVerdict": registers_verdict(states),
        "observationStates": {k: "not_observed" for k, _ in OBSERVATION_INPUTS},
        "observationsVerdict": "none_observed",
        "window": {"base": None, "how": "not resolved — --check is windowless "
                                        "on purpose", "resolved": False,
                   "since": None},
        "digest": {}, "digestText": "(not built — --check is windowless)",
        "sessionNotes": {"state": "not_observed", "sessions": [],
                         "observedAt": None, "observedBy": None},
        "liveSessions": None,
        "checklist": checklist_view(docs.get("manager_checklist")),
        "openPrs": open_pr_view(docs.get("open_pr_record")),
        "lease": lease_view(docs.get("manager_lease"), states["manager_lease"],
                            now=now),
        "loudOpenItems": loud_open_items(docs.get("open_items")),
        "cyclePriority": docs.get("cycle_priority") or {},
        "due": _due.build([], now=now),
        "coverageComplete": False,
    }
    try:
        md = render(b)
    except Exception as exc:  # noqa: BLE001 — the failure IS the finding
        print(f"daily-brief: FAIL — the renderer raised on the live registers: "
              f"{type(exc).__name__}: {exc}")
        return 1

    # The sentences that carry the invariants. If one of these disappears in a
    # refactor the brief still renders and quietly stops saying the thing it
    # exists to say — which is the only failure mode a smoke test would miss.
    required = [
        ("landed_unproven is not flattened into done", "is NOT `done`"),
        ("a merge is distinguished from an observation",
         "A merge is a deploy, not an observation"),
        ("the unread half is declared", "WHAT THIS BRIEF COULD NOT SEE"),
        ("every input is enumerated", "EVERY INPUT, AND WHETHER IT WAS READ"),
        ("coverage is declared incomplete", "`coverageComplete` is `false`"),
        ("the artifact says it is generated", "do not hand-edit"),
    ]
    missing = [why for why, frag in required if frag not in md]
    if missing:
        for why in missing:
            print(f"  ::FINDING:: the brief no longer states: {why}")
        print(f"daily-brief: FAIL — {len(missing)} invariant sentence(s) missing")
        return 1

    # Loud, and PASSING. A reader must see a broken register here even though it
    # does not red the PR — reporting it only on a failure would leave the quiet
    # case reading as full coverage.
    bad = [n for n, s in states.items() if s != "read"]
    for n in bad:
        print(f"  ::NOTICE:: register `{n}` reads `{states[n]}` — the brief will "
              f"declare it, and this check PASSES rather than reding unrelated PRs")
    ck = b["checklist"]
    print(f"daily-brief: OK — renders over {len(REGISTER_INPUTS)} registers "
          f"(verdict {b['registersVerdict']}); checklist {ck['total']} items, "
          f"{ck['doneCount']} done and {ck['mergedEffectUnobservedCount']} "
          f"landed_unproven, counted SEPARATELY")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default=None,
                    help="ISO timestamp for the start of the overnight window "
                         "(e.g. '2026-09-02T22:00Z' — 'when I went to bed'). "
                         "Default: 24h before now.")
    ap.add_argument("--session-notes", default=None,
                    help="JSON of what the night session(s) CONCLUDED, written "
                         "by a manager from get_session's post_turn_summary. "
                         "Omitted -> `not_observed`, never silence.")
    ap.add_argument("--live-sessions", default=None,
                    help="A list_sessions observation ('-' for stdin).")
    ap.add_argument("--open-prs", default=None,
                    help="A live open-PR list ('-' for stdin).")
    ap.add_argument("--json", action="store_true", help="Emit the envelope.")
    ap.add_argument("--write", action="store_true",
                    help=f"Write to {BRIEF_DIR.relative_to(REPO_ROOT)}/<date>.md")
    ap.add_argument("--check", action="store_true",
                    help="Guard mode: render the state half over the live "
                         "registers and assert the invariant sentences. Grades "
                         "the CODE, not the data — an unreadable register is "
                         "reported loudly and PASSES.")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()
    if a.check:
        return _check()

    try:
        b = build(since=a.since, session_notes_path=a.session_notes,
                  live_sessions_path=a.live_sessions, open_prs_path=a.open_prs)
    except BadSince as exc:
        # Refuse rather than render. A brief built on a window the operator did
        # not choose is worse than no brief: it answers their question with
        # "nothing happened" and gives them no way to see that it was asked
        # wrong.
        print(f"daily-brief: REFUSED — {exc}", file=sys.stderr)
        return 2
    text = json.dumps(b, indent=2, default=str) if a.json else render(b)

    if a.write:
        BRIEF_DIR.mkdir(parents=True, exist_ok=True)
        out = BRIEF_DIR / f"{b['forDate']}.md"
        out.write_text(render(b), encoding="utf-8")
        print(f"daily-brief: wrote {out.relative_to(REPO_ROOT)} "
              f"(registers={b['registersVerdict']} "
              f"observations={b['observationsVerdict']})")
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
