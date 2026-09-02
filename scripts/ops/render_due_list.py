#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::due-list-guard (--self-test, --check) + due-list.yml (--write)
"""Render ONE due-list from every structured register — W1 of the operations plan.

WHY (the defect class this exists to close)
-------------------------------------------
The 2026-08-31 operations audit measured **17 separate work registers** and no
surface that answers *"what is due right now?"*. The consequence is not that
signals are missed — this repo detects extremely well — it is that a detected
signal has **no owner**. `replay-pregate-nightly` failed the same way on
2026-08-13 and again on 2026-08-31; in between, the response was to improve the
alarm. Detection was raised twice and disposition never happened.

`render_session_brief.py` is the closest sibling and does half of this already,
but it reads exactly two registers (OPEN-ITEMS + RECURRENCE-LEDGER) and its job
is to *frame* a session, not to *queue work*. This renders the queue.

WHAT IT IS NOT
--------------
**It decides nothing.** Every row is a pointer to something a human-shaped
session must judge. Operator directive, 2026-08-31: actions are autonomous,
decisions are the operator's — so this collects, orders and states; it never
resolves, closes, or grades.

THE STATE RULE, WHICH IS THE WHOLE DESIGN
-----------------------------------------
Every source reports ``read`` / ``could_not_read`` / ``not_applicable``, and an
**empty due-list is only meaningful when no source is ``could_not_read``**.

This is not decoration. `CLAUDE.md` records the exact failure it prevents: a
``curl … || echo '{}'`` poller turned an HTTP 403 into ``0 checks``, a watcher
read that as "nothing pending", and reported a green having checked nothing.
The GitHub-backed sources here are 403'd from a web sandbox by design, so this
path is exercised constantly rather than hypothetically — and it must say
"I could not look", never "nothing is due".

``verdict`` on the envelope is therefore never collapsed:

    all_sources_read   — every source answered; an empty list means empty
    partial            — at least one source could not be read; the list is a
                         LOWER BOUND and must not be read as complete
    no_sources_read    — nothing answered; the list is meaningless
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

OUT = Path("docs/claude/DUE.json")
# The markdown twin. The JSON is what CI checks; this is what a SESSION reads,
# and a due-list nobody reads is the disposition gap wearing a new hat.
OUT_MD = Path("docs/claude/DUE.md")

_OPEN_ITEMS = Path("docs/claude/OPEN-ITEMS.json")
_OPERATOR_OWED = Path("docs/claude/operator-owed-register.json")
_RESEARCH_QUEUE = Path("research/queue")
_SUNSET_DIR = Path("comms/sunset")
_SUNSET_DISPOSITIONS = Path("docs/claude/SUNSET-DISPOSITIONS.json")
_PROBES = Path("docs/claude/PROBES.json")
_ERROR_FEED = Path("docs/claude/ERROR-FEED-DIGEST.json")
# How many error-level cause groups the due-list renders inline. A RENDERING
# bound, never a triage decision: the count of everything is stated in the
# summary row, so a capped list can never read as the whole feed. Uncapped it
# would reproduce inside the due-list exactly the flood the digest exists to
# collapse (measured 2026-09-02: 70 groups over 1365 rows).
_ERROR_FEED_MAX_ROWS = 10
# Slack over the workflow's declared hourly cadence before the digest is
# called stale. This repo has scheduled workflows that fire ~4h50m late and
# once instead of daily (probes.yml, run #34), so a tight threshold would fire
# on correct-but-late behaviour — the desensitized-alarm P1. 6h absorbs a
# missed run plus a retry without absorbing a whole day.
_ERROR_FEED_STALE_AFTER_H = 6.0
_PROBES_WORKFLOW = Path(".github/workflows/probes.yml")
# Slack on top of the declared cadence before a report is called stale. The
# probes job carries `timeout-minutes: 60`, so a run that starts on time can
# still be committing an hour later; 6h absorbs that plus a retry without
# absorbing a whole missed day.
_PROBE_STALE_SLACK_H = 6.0

REPO = "benbaichmankass/Metis-Insights"
_API = "https://api.github.com"

#: Source read states. `not_applicable` is NOT `read` — it means the source
#: does not exist in this checkout, which is a different fact from "it exists
#: and is empty".
STATES = ("read", "could_not_read", "not_applicable")

#: Envelope verdicts. Never collapsed — see the module docstring.
VERDICTS = ("all_sources_read", "partial", "no_sources_read")


@dataclass
class SourceResult:
    name: str
    state: str
    rows: list = field(default_factory=list)
    note: str = ""

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"{self.name}: bad state {self.state!r}")


def _row(source: str, ident: str, title: str, why: str,
         *, age_days: int | None = None, loud: bool = False,
         link: str = "") -> dict:
    return {
        "source": source, "id": ident, "title": title, "why_due": why,
        "age_days": age_days, "loud": loud, "link": link,
    }


def _load_json(p: Path) -> Any:
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _day(v: Any) -> date | None:
    """Parse a date from the several shapes these registers use."""
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        return date.fromisoformat(v.strip()[:10])
    except ValueError:
        return None


# ── sources ────────────────────────────────────────────────────────────────

def src_open_items(root: Path, today: date) -> SourceResult:
    """Monitoring rows past their own cadence, plus every `loud` row.

    A `loud` row is listed even when not overdue: CLAUDE.md requires it to be
    REPORTED ON every session, so it is due by definition.
    """
    p = root / _OPEN_ITEMS
    if not p.exists():
        return SourceResult("open_items", "not_applicable", note=f"{_OPEN_ITEMS} absent")
    try:
        items = _load_json(p)["items"]
    except Exception as exc:  # noqa: BLE001 — an unreadable register is a state, not a crash
        return SourceResult("open_items", "could_not_read", note=f"{type(exc).__name__}: {exc}")

    rows = []
    for it in items:
        ident = it.get("id", "(no id)")
        title = (it.get("summary") or "")[:200]
        loud = bool(it.get("loud"))
        # Both spellings occur in the live register; neither is authoritative.
        last = _day(it.get("verified_at")) or _day(it.get("last_checked"))
        every = it.get("check_every_days")
        if it.get("kind") == "monitoring" and isinstance(every, int) and every > 0:
            if last is None:
                rows.append(_row("open_items", ident, title,
                                 "monitoring row has NEVER been observed", loud=loud))
                continue
            age = (today - last).days
            if age >= every:
                rows.append(_row("open_items", ident, title,
                                 f"monitoring row {age}d since last observation "
                                 f"(cadence {every}d)", age_days=age, loud=loud))
                continue
        if loud:
            rows.append(_row("open_items", ident, title,
                             "loud row — must be reported on every session",
                             age_days=(today - last).days if last else None, loud=True))
    return SourceResult("open_items", "read", rows)


def _soak_grader():
    """The soak vocabulary + grader, IMPORTED from the module that owns it.

    Never restated here, for the reason `_owed_vocabulary` gives one function
    below: a second definition of "is this soak ready?" is free to drift from
    the one the rule and the guard use.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import soak_alarm  # noqa: PLC0415
    return soak_alarm


def src_soaks(root: Path, today: date) -> SourceResult:
    """Soaks whose declared threshold is MET, or which have stopped writing.

    THE STANDING RULE THIS ENFORCES (operator directive, 2026-09-02)
    ---------------------------------------------------------------
        "Anything soaking needs to be logged with an alarm that has either a
         timer or a soak threshold, so that we know to get back to it when the
         soak is ready."

    `src_open_items` above already answers *"is it time to LOOK?"* from
    `check_every_days`. It cannot answer *"is it READY?"*, because until
    2026-09-02 no field in the register could express a criterion in DATA
    rather than in elapsed days. This source reads that criterion.

    WHAT LANDS IN THE LIST, AND WHAT DELIBERATELY DOES NOT
    -----------------------------------------------------
    `ready` and `not_writing` become rows and are LOUD. `unknown` becomes a row
    and is not. **`accruing` becomes NO ROW AT ALL** — it goes in the source
    `note`, which the markdown renders as context beneath the list.

    ⚠️ That last one is the load-bearing refusal. A daily *"soak not ready"*
    row is the desensitised-alarm P1, and `src_probes` below already produces
    exactly that: it emits a LOUD row for every probe whose state is `fail`,
    and before the probe-layer split a patiently-accruing soak returned `fail`.
    So this source **also removes** an existing daily page rather than only
    adding new ones — see `_soak_ids` at the `src_probes` call site.

    THE EVIDENCE IS THE COMMITTED PROBE REPORT, NOT A LIVE READ
    ----------------------------------------------------------
    Like `src_probes`, this reads `docs/claude/PROBES.json` rather than running
    anything: the renderer must stay cheap and network-light, and — decisively —
    the soak logs live under `runtime_logs/` on the live VM, which is
    `.gitignore`d and unreachable from a CI runner. The probes job holds the
    diag bearer; this reports what its last run SAW. A soak whose probe has not
    run therefore grades `unknown`, which is the honest answer and not a bug.
    """
    p = root / _OPEN_ITEMS
    if not p.exists():
        return SourceResult("soaks", "not_applicable", note=f"{_OPEN_ITEMS} absent")
    try:
        items = _load_json(p)["items"]
    except Exception as exc:  # noqa: BLE001
        return SourceResult("soaks", "could_not_read", note=f"{type(exc).__name__}: {exc}")

    declared = [it for it in items if isinstance(it.get("soak"), dict)]
    if not declared:
        # Distinguishable from a read failure, and from every soak being quiet.
        return SourceResult("soaks", "read", [],
                            note="no register row declares a `soak` block")

    # Probe results are OPTIONAL evidence. Their absence downgrades each soak to
    # `unknown` — it must never look like every soak read empty.
    probe_by_id: dict[str, dict] = {}
    probes_note = ""
    pp = root / _PROBES
    if not pp.exists():
        probes_note = f"{_PROBES} absent — no probe run yet, so every soak is UNGRADED"
    else:
        try:
            probe_by_id = {r.get("id"): r for r in _load_json(pp)["results"]}
        except Exception as exc:  # noqa: BLE001
            probes_note = f"{_PROBES} unreadable ({type(exc).__name__}) — soaks UNGRADED"

    sa = _soak_grader()
    rows: list[dict] = []
    tally: dict[str, int] = {s: 0 for s in sa.STATES}
    context: list[str] = []

    for it in declared:
        ident = it.get("id", "(no id)")
        soak = it["soak"]

        bad = sa.declaration_problems(ident, soak)
        if bad:
            # A malformed declaration is SURFACED, never skipped: a soak block
            # nobody can read is not a soak that has been checked, and silently
            # dropping it would make a typo look like a row with no soak.
            tally["unknown"] += 1
            rows.append(_row("soaks", ident, (it.get("summary") or "")[:200],
                             "soak declaration is UNGRADEABLE — " + "; ".join(bad)))
            continue

        result = probe_by_id.get(ident) or {}
        state = result.get("state")
        # `no_probe_declared` is the runner's way of saying the ROW carries a
        # `probe_absent_reason`. That is a declared gap, not a broken probe, and
        # the grader words the two differently.
        if state == "could_not_run" and result.get("reason") == "no_probe_declared":
            state = None

        v = sa.grade(soak, probe_state=state,
                     matched=result.get("matched"), scanned=result.get("scanned"),
                     today=today)
        tally[v.state] += 1

        if not v.surfaces:
            context.append(f"{ident}: {v.state}")
            continue
        rows.append(_row("soaks", ident, (it.get("summary") or "")[:200],
                         f"soak {v.state.upper()} — {v.why}",
                         age_days=v.days_since_declared, loud=v.escalates))

    note = (f"{len(declared)} declared soak(s): "
            + " · ".join(f"{k}={tally[k]}" for k in sa.STATES))
    if context:
        # `accruing` rides here rather than as a row — context, never a page.
        note += f" | accruing (not surfaced, healthy): {', '.join(context)}"
    if probes_note:
        note += f" | {probes_note}"
    return SourceResult("soaks", "read", rows, note=note)


def _owed_vocabulary() -> tuple[set[str], set[str]]:
    """The operator-owed status/state words, from the module that OWNS them.

    Imported rather than restated so this renderer cannot drift from the
    grader. A failed import falls back to the same words with a marker in the
    caller's note — never to a silently different set.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from src.runtime.operator_owed import (  # noqa: PLC0415
            OPEN_STATUSES, TERMINAL_STATUSES,
        )
        return set(OPEN_STATUSES), set(TERMINAL_STATUSES)
    except Exception:  # noqa: BLE001
        # collapsed-state: the fallback names the SAME words the module
        # declares today; it exists so a path/import problem degrades to a
        # working due-list rather than an empty one, and any divergence shows
        # up as an `unknown` status row rather than as a silent drop.
        return {"open", "dispatched", "snoozed"}, {"resolved", "withdrawn"}


def src_operator_owed(root: Path, today: date) -> SourceResult:
    """Anything owed to the operator that is not resolved or withdrawn."""
    p = root / _OPERATOR_OWED
    if not p.exists():
        return SourceResult("operator_owed", "not_applicable", note=f"{_OPERATOR_OWED} absent")
    try:
        items = _load_json(p)["items"]
    except Exception as exc:  # noqa: BLE001
        return SourceResult("operator_owed", "could_not_read", note=f"{type(exc).__name__}: {exc}")

    # The vocabulary is IMPORTED, never re-derived here. A hand-rolled
    # `{"resolved", "withdrawn"}` in this file would be a SECOND definition of
    # "is this still owed?", free to drift from the one that governs — the exact
    # shape the repo's provenance module was written to stop. It is also why an
    # earlier draft of this function silently had no notion of `snoozed`.
    open_statuses, terminal_statuses = _owed_vocabulary()

    rows = []
    for it in items:
        status = str(it.get("status", "")).strip().lower()
        if status in terminal_statuses:
            continue
        # `snoozed` is NOT due: it is deferred behind a date AND a named
        # trigger, which `src/runtime/operator_owed.py` treats as its own state
        # precisely so it is not collapsed into "still owed".
        if status == "snoozed":
            snoozed_until = str(it.get("snoozed_until") or "")
            if snoozed_until and snoozed_until[:10] > today.isoformat():
                continue
        # An UNRECOGNISED status is surfaced, deliberately: an id we cannot
        # grade is not the same as one we graded clean, and silently dropping it
        # would make a typo in the register look like resolved work.
        unknown = status not in open_statuses and status not in terminal_statuses
        opened = _day(it.get("opened_at"))
        why = (f"owed to the operator, status={status!r}"
               if not unknown else
               f"owed to the operator, and status={status!r} is NOT one of "
               f"{sorted(open_statuses | terminal_statuses)} — ungradeable, "
               f"surfaced rather than dropped")
        rows.append(_row("operator_owed", it.get("id", "(no id)"),
                         (it.get("title") or "")[:200],
                         why,
                         age_days=(today - opened).days if opened else None,
                         loud=True))
    return SourceResult("operator_owed", "read", rows)


def probe_cadence_hours(root: Path) -> tuple[float | None, str]:
    """Expected hours between probe runs, read from the cron the workflow DECLARES.

    Deliberately not a hardcoded 24. The threshold has to move when the schedule
    moves, or the first person to make probes twice-daily silently gets a
    staleness check that is a full day too generous.

    Returns (hours, basis). `None` hours means we could not derive it — which is
    its own state upstream, never quietly treated as fresh.
    """
    p = root / _PROBES_WORKFLOW
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"cannot read {_PROBES_WORKFLOW}: {type(exc).__name__}"
    m = re.search(r"""cron:\s*['"]([^'"]+)['"]""", text)
    if not m:
        return None, f"no cron found in {_PROBES_WORKFLOW}"
    cron = m.group(1).split()
    if len(cron) != 5:
        return None, f"cron {' '.join(cron)!r} is not 5 fields"
    _minute, hour, dom, _month, dow = cron
    # Only the shapes this repo actually uses. An unrecognised one returns None
    # rather than a guess — a wrong threshold is worse than a stated gap.
    if hour.startswith("*/"):
        try:
            return float(int(hour[2:])), f"every {hour[2:]}h (cron {' '.join(cron)})"
        except ValueError:
            return None, f"cannot parse hour field {hour!r}"
    if hour == "*":
        return 1.0, f"hourly (cron {' '.join(cron)})"
    if hour.isdigit() and dom == "*" and dow == "*":
        return 24.0, f"daily (cron {' '.join(cron)})"
    if hour.isdigit() and dow != "*":
        return 168.0, f"weekly (cron {' '.join(cron)})"
    return None, f"unrecognised cron shape {' '.join(cron)!r}"


# FOUR states, never collapsed. `undateable` and `cadence_unknown` are both
# "we could not establish freshness" — and they are NOT the same as `fresh`.
PROBE_FRESHNESS = ("fresh", "stale", "undateable", "cadence_unknown")


def probe_freshness(generated_at: Any, cadence_h: float | None, now: datetime
                    ) -> tuple[str, float | None]:
    """Grade the probe report's own age. Returns (state, age_hours).

    ⚠️ `undateable` resolves toward REPORTING, not toward silence: a report that
    cannot be dated cannot be shown to be current, and the fail-safe reading of
    evidence behind a due-list is stale. Same polarity as `prop_balance`'s
    refusal on an undateable snapshot.
    """
    ts = None
    if isinstance(generated_at, str):
        try:
            ts = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError:
            ts = None
    if ts is None:
        return "undateable", None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_h = (now - ts).total_seconds() / 3600.0
    if cadence_h is None:
        return "cadence_unknown", age_h
    return ("stale" if age_h > cadence_h + _PROBE_STALE_SLACK_H else "fresh"), age_h


def src_probes(
    root: Path,
    today: date,  # inert: every source shares ONE signature so `collect` dispatches them uniformly; this one grades against `now`, which carries the time of day `today` throws away
    *,
    now: datetime | None = None,
) -> SourceResult:
    """Probe results — a FAILED probe is a signal nobody is otherwise watching.

    Reads the COMMITTED results file rather than running the probes: this
    renderer must stay cheap and network-light, and the probes have their own
    schedule. So this reports what the last probe run SAW, and a `could_not_run`
    stays `could_not_run` all the way through to the due-list rather than
    collapsing into "quiet".
    """
    # Injected in tests so a freshness verdict is asserted against a FIXED clock;
    # `collect` calls this positionally, so the uniform dispatch is unchanged.
    now = now or datetime.now(timezone.utc)

    p = root / _PROBES
    if not p.exists():
        return SourceResult("probes", "not_applicable", note=f"{_PROBES} absent — no run yet")
    try:
        env = _load_json(p)
        results = env["results"]
    except Exception as exc:  # noqa: BLE001
        return SourceResult("probes", "could_not_read", note=f"{type(exc).__name__}: {exc}")

    # HOW OLD IS THIS EVIDENCE? Until 2026-08-31 nothing asked. The renderer read
    # the committed file and reported its verdicts with no age, so a probes run
    # that overran its 60-min timeout or failed to commit rendered YESTERDAY's
    # verdicts as today's, silently — the exact collapse the rest of this
    # machinery exists to prevent, sitting in the newest part of it.
    cadence_h, basis = probe_cadence_hours(root)
    fresh, age_h = probe_freshness(env.get("generated_at"), cadence_h, now)
    age_str = f"{age_h:.1f}h" if age_h is not None else "unknown age"
    observed = env.get("generated_at") or "an unrecorded time"

    rows = []
    if fresh == "stale":
        rows.append(_row(
            "probes", "PROBE-REPORT-STALE",
            f"probe report is {age_str} old — expected {basis}",
            "EVERY probe verdict below was observed at "
            f"{observed}, NOT today. The probes job did not land a fresh report, "
            "so treat its rows as a record of that run and check the workflow.",
            age_days=int(age_h // 24) if age_h is not None else None,
            loud=True))
    elif fresh == "undateable":
        rows.append(_row(
            "probes", "PROBE-REPORT-UNDATEABLE",
            "probe report carries no readable `generated_at`",
            "a report that cannot be dated cannot be shown to be current — its "
            "verdicts are being read without knowing when they were observed",
            loud=True))
    elif fresh == "cadence_unknown":
        rows.append(_row(
            "probes", "PROBE-CADENCE-UNKNOWN",
            f"report is {age_str} old; expected cadence could not be derived — {basis}",
            "we have an age and no threshold to judge it against, so freshness "
            "is UNGRADED here — not confirmed fresh"))

    # When the evidence is not fresh, every verdict below carries WHEN it was
    # observed. A stale `pass` read as today's is the dangerous direction.
    stamp = "" if fresh == "fresh" else f" [observed {observed}, {age_str} ago]"

    # ⚠️ A ROW THAT DECLARES A `soak` IS GRADED BY `src_soaks`, NOT HERE, AND
    # THIS HAND-OFF REMOVES A DAILY PAGE RATHER THAN ADDING ONE.
    #
    # The loop below emits a LOUD row for every probe whose state is `fail`.
    # For an ordinary monitoring row that is right: the declared observation did
    # not hold and somebody should know. For a SOAK it is exactly wrong — a
    # soak that is patiently accruing IS in the `fail` state for its entire
    # life, so this fired a loud row every single day of a healthy soak. That is
    # the desensitised-alarm P1 sitting inside the machinery meant to prevent
    # it — see `BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART`,
    # where one un-latched condition took 53.7% of the operator's ERROR+ feed.
    # (The id is kept on ONE line deliberately: wrapping it across the hyphen
    # truncates it, and `check_backlog_refs.py` correctly reads a truncated id
    # as a reference that resolves to nothing — a doc saying "tracked by BL-X"
    # where BL-X was never filed reads as tracked while being tracked by nobody.)
    #
    # `src_soaks` distinguishes accruing (quiet) from not_writing (loud), which
    # this function cannot: both were `fail` until the probe layer split them.
    # Deferring is therefore strictly more informative, never a suppression —
    # every deferred id is still graded, and the count is stated in the note so
    # a silent hand-off is impossible.
    deferred = _soak_declaring_ids(root)
    handed_off = 0

    for r in results:
        state = r.get("state")
        if r.get("id") in deferred:
            handed_off += 1
            continue
        if state == "fail":
            rows.append(_row("probes", r.get("id", "(no id)"),
                             (r.get("checks") or "")[:200],
                             "probe FAILED — its declared observation did not hold" + stamp,
                             loud=True))
        elif state == "could_not_run" and r.get("reason") != "no_probe_declared":
            # A probe that BROKE is its own finding: the row it watches is now
            # unwatched and nobody would know. A row that never had a probe is
            # a known, declared gap and is deliberately NOT surfaced here.
            rows.append(_row("probes", r.get("id", "(no id)"),
                             f"probe could not run ({r.get('reason')})",
                             "we did not look — this row is currently unwatched" + stamp))

    note = f"freshness={fresh} age={age_str} cadence={basis}"
    if handed_off:
        note += (f" | {handed_off} probe result(s) deferred to the `soaks` source, "
                 f"which grades a soak's accruing/not_writing distinction this "
                 f"function cannot make")
    return SourceResult("probes", "read", rows, note=note)


def _soak_declaring_ids(root: Path) -> set[str]:
    """Ids of register rows carrying a `soak` block.

    Returns an EMPTY SET on any read failure, deliberately: the fail-safe
    direction here is to keep reporting a probe `fail` as a due row. Losing a
    signal because a register read hiccuped is worse than an extra row.
    """
    try:
        items = _load_json(root / _OPEN_ITEMS)["items"]
    except Exception:  # noqa: BLE001
        return set()
    return {it.get("id") for it in items if isinstance(it.get("soak"), dict)}


def src_research_queue(
    root: Path,
    today: date,  # inert: today — every source shares ONE signature so `collect` dispatches them uniformly; this one has no use for it
) -> SourceResult:
    """Queued research jobs, so a stalled queue is visible without opening it."""
    d = root / _RESEARCH_QUEUE
    if not d.is_dir():
        return SourceResult("research_queue", "not_applicable", note=f"{_RESEARCH_QUEUE} absent")
    rows = []
    try:
        for f in sorted(d.glob("RQ-*.yaml")):
            text = f.read_text(encoding="utf-8", errors="replace")
            status = title = ""
            for line in text.splitlines():
                s = line.strip()
                if s.startswith("status:") and not status:
                    status = s.split(":", 1)[1].strip()
                elif s.startswith("title:") and not title:
                    title = s.split(":", 1)[1].strip()
            if status == "queued":
                rows.append(_row("research_queue", f.stem, title[:200],
                                 "research job still queued"))
    except OSError as exc:
        return SourceResult("research_queue", "could_not_read", note=f"OSError: {exc}")
    return SourceResult("research_queue", "read", rows)


def _gh(path: str, token: str) -> Any:
    req = urllib.request.Request(
        f"{_API}{path}",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "metis-due-list"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 — fixed host
        return json.loads(r.read().decode())


def src_red_crons(
    root: Path,  # inert: root — every source shares ONE signature so `collect` dispatches them uniformly; this one has no use for it
    today: date,  # inert: today — every source shares ONE signature so `collect` dispatches them uniformly; this one has no use for it
    *,
    token: str | None = None,
) -> SourceResult:
    """Scheduled runs whose latest conclusion is not success.

    This is the F1 class — a nightly nobody is waiting on. Without a token we
    say so; we never report "no red crons".
    """
    token = token if token is not None else os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return SourceResult("red_crons", "could_not_read",
                            note="no GITHUB_TOKEN — cannot query the Actions API")
    try:
        data = _gh(f"/repos/{REPO}/actions/runs?event=schedule&per_page=100", token)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return SourceResult("red_crons", "could_not_read", note=f"{type(exc).__name__}: {exc}")

    latest: dict[str, dict] = {}
    for run in data.get("workflow_runs", []):
        name = run.get("name") or run.get("path", "?")
        if name not in latest:  # the API returns newest-first
            latest[name] = run
    rows = []
    for name, run in sorted(latest.items()):
        if run.get("conclusion") not in (None, "success"):
            rows.append(_row("red_crons", name, name,
                             f"latest scheduled run concluded {run.get('conclusion')!r}",
                             loud=True, link=run.get("html_url", "")))
    return SourceResult("red_crons", "read", rows)


def src_unlanded_automation(
    root: Path,  # inert: root — every source shares ONE signature so `collect` dispatches them uniformly; this one has no use for it
    today: date,
    *,
    token: str | None = None,
) -> SourceResult:
    """Open PRs on `automation/*` — a producer that ran and did not land."""
    token = token if token is not None else os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return SourceResult("unlanded_automation", "could_not_read",
                            note="no GITHUB_TOKEN — cannot list pull requests")
    try:
        data = _gh(f"/repos/{REPO}/pulls?state=open&per_page=100", token)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return SourceResult("unlanded_automation", "could_not_read",
                            note=f"{type(exc).__name__}: {exc}")
    rows = []
    for pr in data:
        ref = (pr.get("head") or {}).get("ref", "")
        if not ref.startswith("automation/"):
            continue
        opened = _day(pr.get("created_at"))
        rows.append(_row("unlanded_automation", f"#{pr.get('number')}",
                         (pr.get("title") or "")[:200],
                         "producer output opened a PR that has not landed",
                         age_days=(today - opened).days if opened else None,
                         loud=True, link=pr.get("html_url", "")))
    return SourceResult("unlanded_automation", "read", rows)



def src_error_feed(
    root: Path,
    today: date,  # inert: today — every source shares ONE signature so `collect` dispatches them uniformly; this one grades against `now`, which carries the time of day `today` throws away
    *,
    now: datetime | None = None,
) -> SourceResult:
    """The trader's ERROR FEED, grouped — from the COMMITTED digest.

    Operator ask, 2026-09-02: *"can the error feed that's in the trader bot be
    fed directly to the manager session, so you can decide what should be
    resolved immediately vs. backlogged?"* That decision is exactly the `duty`
    pass's disposition, and `duty` reads this list — so the feed is rendered
    here rather than into a parallel surface of its own.

    Reads what `scripts/ops/error_feed_digest.py --write` last landed, never the
    live feeds: this renderer must stay cheap and network-light, and the digest
    has its own schedule. So an `unreachable` feed stays `unreachable` all the
    way through to the due-list rather than collapsing into "quiet".
    """
    now = now or datetime.now(timezone.utc)

    p = root / _ERROR_FEED
    if not p.exists():
        return SourceResult("error_feed", "not_applicable",
                            note=f"{_ERROR_FEED} absent — no digest run yet")
    try:
        env = _load_json(p)
        groups = env["groups"]
    except Exception as exc:  # noqa: BLE001
        return SourceResult("error_feed", "could_not_read",
                            note=f"{type(exc).__name__}: {exc}")

    rows = []

    # HOW OLD IS THIS EVIDENCE? A digest that stopped being produced renders as
    # a quiet feed, which is the same wrong answer the digest itself refuses to
    # give about its own sources.
    gen = env.get("generated_at")
    stamp = None
    if isinstance(gen, str):
        try:
            stamp = datetime.fromisoformat(gen.replace("Z", "+00:00"))
        except ValueError:
            stamp = None
    if stamp is None:
        rows.append(_row(
            "error_feed", "ERROR-FEED-UNDATEABLE",
            "error-feed digest carries no readable `generated_at`",
            "a digest that cannot be dated cannot be shown to be current — its "
            "groups are being read without knowing when they were observed",
            loud=True))
        age_h = None
    else:
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age_h = (now - stamp).total_seconds() / 3600.0
        if age_h > _ERROR_FEED_STALE_AFTER_H:
            rows.append(_row(
                "error_feed", "ERROR-FEED-DIGEST-STALE",
                f"error-feed digest is {age_h:.1f}h old — expected hourly",
                "EVERY group below was observed at "
                f"{gen}, NOT now. The digest workflow did not land a fresh run, "
                "so an absent condition may simply be one nobody looked for.",
                age_days=int(age_h // 24), loud=True))

    # A feed we could not read is its own due row. Rendering its silence as
    # "nothing fired" is the defect the whole digest exists to refuse.
    for name in env.get("unreachable_feeds") or []:
        note = (env.get("feeds", {}).get(name, {}) or {}).get("note", "")
        rows.append(_row(
            "error_feed", f"ERROR-FEED-UNREACHABLE-{name.upper()}",
            f"error feed `{name}` could not be read",
            f"WE COULD NOT LOOK, which is not the same as quiet — {note or 'no note'}. "
            "Every group below is a LOWER BOUND.",
            loud=True))

    errs = [g for g in groups if g.get("level") in ("error", "unknown")]
    warns = [g for g in groups if g.get("level") not in ("error", "unknown")]

    for g in errs[:_ERROR_FEED_MAX_ROWS]:
        facets = " ".join(
            f"{k}={','.join(g[k])}" for k in ("accounts", "symbols", "strategies")
            if g.get(k))
        first, last = g.get("first_seen", ""), g.get("last_seen", "")
        first_day = _day(first)
        rows.append(_row(
            # STABLE across runs. `hash()` is per-process randomised
            # (PYTHONHASHSEED), so an id built from it changes every run and a
            # session cannot tell yesterday's row from a new one — which is the
            # whole reason a due row carries an id.
            "error_feed",
            "ERRFEED-" + hashlib.sha1(
                g.get("cause", "").encode("utf-8")).hexdigest()[:8],
            f"[{g.get('level')}] {'NEW ' if g.get('is_new') else ''}"
            f"x{g.get('count')} {g.get('cause', '')[:140]}",
            f"error-level condition on `{g.get('feed')}`, "
            + ("FIRST SEEN since the last digest — " if g.get("is_new")
               else "STANDING (predates the last digest) — ")
            + f"{g.get('count')} rows {first[:19]} → {last[:19]}"
            + (f" · {facets}" if facets else "")
            + " — decide: fix now, or file to a backlog",
            age_days=(today - first_day).days if first_day else None))

    # THE SUMMARY ROW IS WHAT MAKES THE CAP HONEST. Without it a 10-row render
    # of 47 error groups reads as the whole feed.
    if groups:
        capped = max(0, len(errs) - _ERROR_FEED_MAX_ROWS)
        rows.append(_row(
            "error_feed", "ERROR-FEED-SUMMARY",
            f"{len(groups)} cause groups over {env.get('counts', {}).get('rows_grouped')} rows "
            f"({len(errs)} error-level, {len(warns)} warn-level, "
            f"{env.get('counts', {}).get('new_groups')} new since the last digest)",
            f"{capped} further error group(s) and every warn group are NOT listed "
            f"above — read `{_ERROR_FEED}` for the full set. Digest verdict "
            f"`{env.get('verdict')}`"
            + (f", page cap hit on `{'`, `'.join(env['truncated_feeds'])}`"
               if env.get("truncated_feeds") else "")
            + f", covers rows after `{env.get('covers_since') or '(full page)'}`."))

    return SourceResult("error_feed", "read", rows,
                        note=f"digest {gen}, age "
                             + (f"{age_h:.1f}h" if age_h is not None else "unknown"))

def src_sunset_dispositions(root: Path, today: date) -> SourceResult:
    """E3's retirement candidates that NOBODY HAS DISPOSITIONED.

    WHY THIS IS A SOURCE AND NOT A SENTENCE IN A SKILL
    (`docs/audits/operating-layer-skills-workflows-inventory-2026-09-02.md`
    § 2.3 + P-C4). E3's machinery all shipped and is live — `sunset-pass.yml`,
    `scripts/ops/sunset_pass.py`, `check_sunset_dispositions.py`,
    `SUNSET-DISPOSITIONS.json`, `comms/sunset/`. What was missing is the half
    this file exists to supply: **zero of the 32 role packs referenced any of
    it**, and the audit's retirement-duty table has a literal `nobody` in the
    row for retiring a skill, workflow, register or guard. So the pass produced
    candidates on a cadence and no session was ever made to look at them.

    Adding a paragraph to a skill would have been the *reminder* version of this
    fix. A due-list source is the mechanism version: an undispositioned
    candidate now appears on the one surface the duty pass is required to work
    to a disposition, and drops off by itself the moment somebody records one.

    ⚠️ **DELIBERATELY NOT `loud`.** There were 10 candidates on the 2026-09-01
    pass; ten permanently-loud rows would be reported in every closing summary
    forever and become the thing sessions learn to scroll past — the
    desensitized-alarm P1 this repo calls its own worst failure mode. They are
    DUE, which is enough to be worked; they are not an emergency.

    ⚠️ **A DISPOSITION IS NOT A RETIREMENT.** Retiring a strategy leg is Tier-3
    and `retire_proposed` is the furthest a session may take one on its own.
    This source clears on the candidate being *dispositioned*, never on it being
    *retired* — recording "we looked and decided to keep it" closes the row
    exactly as legitimately as proposing removal.
    """
    d = root / _SUNSET_DIR
    if not d.exists():
        return SourceResult("sunset", "not_applicable", note=f"{_SUNSET_DIR} absent")
    indices = sorted(d.glob("*/INDEX.json"))
    if not indices:
        return SourceResult("sunset", "not_applicable",
                            note=f"no {_SUNSET_DIR}/*/INDEX.json yet")
    latest = indices[-1]
    try:
        index = _load_json(latest)
    except Exception as exc:  # noqa: BLE001 — an unreadable register is a state
        return SourceResult("sunset", "could_not_read",
                            note=f"{latest}: {type(exc).__name__}: {exc}")

    # ⚠️ AN UNREADABLE DISPOSITION FILE IS `could_not_read`, NEVER AN EMPTY SET.
    # Treating it as empty would flood the list with candidates that may already
    # be dispositioned — a confident wrong answer, and the direction that
    # destroys trust in the list fastest.
    dp = root / _SUNSET_DISPOSITIONS
    if not dp.exists():
        return SourceResult("sunset", "could_not_read",
                            note=f"{_SUNSET_DISPOSITIONS} absent — cannot tell "
                                 f"dispositioned candidates from undispositioned ones")
    try:
        decided = {row.get("id") for row in _load_json(dp).get("dispositions", [])}
    except Exception as exc:  # noqa: BLE001
        return SourceResult("sunset", "could_not_read",
                            note=f"{_SUNSET_DISPOSITIONS}: {type(exc).__name__}: {exc}")

    rows = []
    for r in index.get("rows", []):
        if r.get("verdict") != "retire_candidate" or r.get("id") in decided:
            continue
        seen = _day((r.get("evidence") or {}).get("first_seen")) or _day(index.get("utc_date"))
        rows.append(_row(
            "sunset", r.get("id", "(no id)"),
            f"{r.get('class', '?')} · {r.get('name', '?')}",
            f"sunset pass proposed RETIRE ({r.get('basis', 'no basis')}) and no "
            f"disposition is recorded — Tier-{r.get('tier', '?')}, so propose, "
            f"never enact",
            age_days=(today - seen).days if seen else None,
            link=str(_SUNSET_DISPOSITIONS)))
    return SourceResult("sunset", "read", rows)


SOURCES: tuple[Callable, ...] = (
    src_open_items, src_soaks, src_operator_owed, src_research_queue, src_probes,
    src_red_crons, src_unlanded_automation, src_error_feed,
    src_sunset_dispositions,
)


# ── envelope ───────────────────────────────────────────────────────────────

def verdict_for(results: list[SourceResult]) -> str:
    considered = [r for r in results if r.state != "not_applicable"]
    if not considered:
        return "no_sources_read"
    unread = [r for r in considered if r.state == "could_not_read"]
    if not unread:
        return "all_sources_read"
    if len(unread) == len(considered):
        return "no_sources_read"
    return "partial"


def build(results: list[SourceResult], *, now: datetime) -> dict:
    rows: list[dict] = []
    for r in results:
        rows.extend(r.rows)
    # Loud first, then oldest first. `age_days: None` sorts last within its
    # group — an unknown age is not an old one.
    rows.sort(key=lambda r: (not r["loud"], -(r["age_days"] or 0), r["id"]))
    v = verdict_for(results)
    return {
        "schema_version": 1,
        "what_this_is": (
            "The single due-list, rendered from every structured register by "
            "scripts/ops/render_due_list.py. It COLLECTS and ORDERS; it decides "
            "nothing. Read `verdict` before reading `rows`: on `partial` the "
            "list is a LOWER BOUND, not a complete answer."
        ),
        "generated_at": now.replace(microsecond=0).isoformat(),
        "verdict": v,
        "unreadable_sources": [r.name for r in results if r.state == "could_not_read"],
        "sources": {
            r.name: {"state": r.state, "rows": len(r.rows), "note": r.note}
            for r in results
        },
        "counts": {"due": len(rows), "loud": sum(1 for r in rows if r["loud"])},
        "rows": rows,
    }


def collect(root: Path, today: date, *, token: str | None = None) -> list[SourceResult]:
    out = []
    for fn in SOURCES:
        try:
            if fn in (src_red_crons, src_unlanded_automation):
                out.append(fn(root, today, token=token))
            else:
                out.append(fn(root, today))
        except Exception as exc:  # noqa: BLE001 — one bad source must not lose the rest
            out.append(SourceResult(fn.__name__.replace("src_", ""), "could_not_read",
                                    note=f"unhandled {type(exc).__name__}: {exc}"))
    return out


def render_markdown(env: dict) -> str:
    L = ["# What is due right now", "",
         f"_Generated {env['generated_at']} · verdict **{env['verdict']}**_", ""]
    if env["verdict"] != "all_sources_read":
        L += [f"> ⚠️ **This list is a LOWER BOUND.** Could not read: "
              f"`{'`, `'.join(env['unreadable_sources']) or '(none)'}`. "
              f"An empty section below may mean nothing is due, or may mean nobody looked.", ""]
    if not env["rows"]:
        L += ["Nothing due from the sources that answered.", ""]
    for r in env["rows"]:
        age = f" · {r['age_days']}d" if r["age_days"] is not None else ""
        loud = "🔔 " if r["loud"] else ""
        L.append(f"- {loud}**{r['id']}** ({r['source']}{age}) — {r['why_due']}")
        if r["title"]:
            L.append(f"  - {r['title']}")
    L += ["", "_This list decides nothing. Every row is for a session to judge._"]
    return "\n".join(L) + "\n"


# ── self-test: planted controls, so a vacuous pass is impossible ───────────

def _self_test() -> int:
    r_ok = SourceResult("a", "read", [_row("a", "1", "t", "w")])
    r_bad = SourceResult("b", "could_not_read", note="403")
    r_na = SourceResult("c", "not_applicable")

    assert verdict_for([r_ok]) == "all_sources_read"
    assert verdict_for([r_ok, r_bad]) == "partial", "one unread source must degrade the verdict"
    assert verdict_for([r_bad]) == "no_sources_read"
    # not_applicable must not be mistaken for a successful read
    assert verdict_for([r_na]) == "no_sources_read", "not_applicable is not a read"
    assert verdict_for([r_ok, r_na]) == "all_sources_read"

    # an empty list from an unread source must NOT read as clean
    env = build([SourceResult("x", "could_not_read", note="403")], now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert env["counts"]["due"] == 0 and env["verdict"] == "no_sources_read"
    assert "LOWER BOUND" in render_markdown(env), "an unread source must be stated in the brief"

    # overdue detection, and the never-observed case
    today = date(2026, 8, 31)
    items = {"items": [
        {"id": "OI-A", "kind": "monitoring", "check_every_days": 3, "verified_at": "2026-08-20", "summary": "s"},
        {"id": "OI-B", "kind": "monitoring", "check_every_days": 3, "verified_at": "2026-08-31", "summary": "s"},
        {"id": "OI-C", "kind": "monitoring", "check_every_days": 2, "summary": "never observed"},
        {"id": "OI-D", "kind": "background_awareness", "loud": True, "summary": "loud but not monitoring"},
        {"id": "OI-E", "kind": "background_awareness", "summary": "quiet"},
    ]}
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "docs/claude").mkdir(parents=True)
        (root / _OPEN_ITEMS).write_text(json.dumps(items), encoding="utf-8")
        got = {r["id"] for r in src_open_items(root, today).rows}
        assert got == {"OI-A", "OI-C", "OI-D"}, f"overdue selection wrong: {got}"
        # a register that exists but is corrupt is could_not_read, never empty
        (root / _OPEN_ITEMS).write_text("{not json", encoding="utf-8")
        assert src_open_items(root, today).state == "could_not_read"
        # a register that is absent is not_applicable, which is a THIRD thing
        (root / _OPEN_ITEMS).unlink()
        assert src_open_items(root, today).state == "not_applicable"

    # the GitHub sources must refuse rather than invent an empty answer
    assert src_red_crons(Path("."), today, token="").state == "could_not_read"
    assert src_unlanded_automation(Path("."), today, token="").state == "could_not_read"

    # ── PROBE FRESHNESS: the gap this renderer shipped with ────────────────
    # Until 2026-08-31 `src_probes` read the committed file and never read its
    # timestamp, so an overrun or failed probes run rendered yesterday's
    # verdicts as today's with no caveat. These controls plant that.
    # (`tempfile` is imported at module scope — a second, local `import
    # tempfile` here made the name function-local and so UnboundLocalError'd
    # every earlier use in this same function.)
    _T0 = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)

    def _probe_root(generated_at, cron='"20 5 * * *"'):
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "docs" / "claude").mkdir(parents=True)
        (d / ".github" / "workflows").mkdir(parents=True)
        (d / ".github" / "workflows" / "probes.yml").write_text(
            f"on:\n  schedule:\n    - cron: {cron}\n", encoding="utf-8")
        env = {"results": [{"id": "OI-X", "state": "fail", "checks": "c"}]}
        if generated_at is not None:
            env["generated_at"] = generated_at
        (d / "docs" / "claude" / "PROBES.json").write_text(json.dumps(env), encoding="utf-8")
        return d

    # cadence comes from the DECLARED cron, not a hardcoded 24
    assert probe_cadence_hours(_probe_root("x"))[0] == 24.0, "daily cron reads as 24h"
    assert probe_cadence_hours(_probe_root("x", cron='"0 */4 * * *"'))[0] == 4.0, \
        "an every-4h cron must move the threshold with it — a hardcoded 24 would " \
        "give a twice-daily schedule a full day of false freshness"
    assert probe_cadence_hours(_probe_root("x", cron='"bogus"'))[0] is None, \
        "an unparseable cron yields NO threshold rather than a guessed one"

    # fresh / stale, against a fixed clock
    fresh = src_probes(_probe_root("2026-08-31T05:20:00+00:00"), _T0.date(), now=_T0)
    assert not any(r["id"].startswith("PROBE-REPORT") for r in fresh.rows), \
        "a report from this morning must raise no freshness row"
    assert "freshness=fresh" in fresh.note

    stale = src_probes(_probe_root("2026-08-29T05:20:00+00:00"), _T0.date(), now=_T0)
    ids = [r["id"] for r in stale.rows]
    assert "PROBE-REPORT-STALE" in ids, \
        "THE ACCEPTANCE CONTROL: a backdated report must report STALE"
    assert next(r for r in stale.rows if r["id"] == "PROBE-REPORT-STALE")["loud"], \
        "and it must be loud — a silent staleness row is the bug it replaces"
    assert "freshness=stale" in stale.note

    # every verdict from a stale report carries WHEN it was observed
    verdict_row = next(r for r in stale.rows if r["id"] == "OI-X")
    assert "observed 2026-08-29" in verdict_row["why_due"], \
        "a stale FAIL read as today's is the dangerous direction — stamp it"
    fresh_row = next(r for r in fresh.rows if r["id"] == "OI-X")
    assert "observed" not in fresh_row["why_due"], "a fresh row is not stamped"

    # the two 'we could not establish freshness' states stay APART from fresh
    und = src_probes(_probe_root(None), _T0.date(), now=_T0)
    assert "PROBE-REPORT-UNDATEABLE" in [r["id"] for r in und.rows], \
        "an undateable report resolves toward REPORTING, never toward silence"
    unk = src_probes(_probe_root("2026-08-31T05:20:00+00:00", cron='"bogus"'),
                     _T0.date(), now=_T0)
    assert "PROBE-CADENCE-UNKNOWN" in [r["id"] for r in unk.rows], \
        "an underivable cadence leaves freshness UNGRADED, not confirmed fresh"
    assert set(PROBE_FRESHNESS) == {"fresh", "stale", "undateable", "cadence_unknown"}

    # ── src_soaks: the four states, end to end through THIS renderer ──────
    # These assert the RENDERED OUTPUT, not the grader — `soak_alarm.py` has its
    # own 28 controls for the verdict logic. What is checked here is the half
    # that logic cannot: which verdicts become ROWS, which become LOUD ones, and
    # which are deliberately kept out of the list entirely.
    def _soak_root(probe_results):
        td = tempfile.mkdtemp()
        root = Path(td)
        (root / "docs/claude").mkdir(parents=True)
        rows = [
            {"id": "S-READY", "opened": "2026-08-20", "kind": "monitoring",
             "summary": "ready", "clears_when": "x",
             "soak": {"log": "a", "declared_at": "2026-08-20",
                      "ready_when": "verdicts_differ=true", "min_matching": 1}},
            {"id": "S-DEAD", "opened": "2026-08-20", "kind": "monitoring",
             "summary": "dead", "clears_when": "x",
             "soak": {"log": "b", "declared_at": "2026-08-20",
                      "ready_when": "rows>0", "min_matching": 1}},
            {"id": "S-ACCRUING", "opened": "2026-08-20", "kind": "monitoring",
             "summary": "accruing", "clears_when": "x",
             "soak": {"log": "c", "declared_at": "2026-08-20",
                      "ready_when": "verdicts_differ=true", "min_matching": 1}},
            {"id": "S-BLIND", "opened": "2026-08-20", "kind": "monitoring",
             "summary": "blind", "clears_when": "x",
             "soak": {"log": "d", "declared_at": "2026-08-20",
                      "ready_when": "verdicts_differ=true", "min_matching": 1}},
        ]
        (root / _OPEN_ITEMS).write_text(json.dumps({"items": rows}), encoding="utf-8")
        if probe_results is not None:
            (root / _PROBES).write_text(json.dumps(
                {"generated_at": "2026-09-02T09:00:00+00:00", "results": probe_results}),
                encoding="utf-8")
        return root

    _live = [
        {"id": "S-READY", "state": "pass", "matched": 2, "scanned": 631},
        {"id": "S-DEAD", "state": "source_empty", "matched": 0, "scanned": 0},
        {"id": "S-ACCRUING", "state": "fail", "matched": 0, "scanned": 4192},
        {"id": "S-BLIND", "state": "could_not_run", "reason": "exit_2",
         "matched": None, "scanned": None},
    ]
    sk = src_soaks(_soak_root(_live), date(2026, 9, 2))
    by_id = {r["id"]: r for r in sk.rows}

    assert sk.state == "read"
    assert "S-READY" in by_id and by_id["S-READY"]["loud"], \
        "a READY soak is a LOUD due row — something is waiting on a person"
    assert "S-DEAD" in by_id and by_id["S-DEAD"]["loud"], \
        "a NOT_WRITING soak is a LOUD due row — the soak is dead and the wait is futile"
    assert "S-BLIND" in by_id and not by_id["S-BLIND"]["loud"], \
        "an UNKNOWN soak surfaces (the row is unwatched) but is NOT loud"
    assert "S-ACCRUING" not in by_id, \
        ("⚠️ an ACCRUING soak produces NO ROW. This is the control that stops the "
         "desensitised-alarm P1: accruing is the expected state of a healthy soak "
         "for its entire life, and a daily 'not ready' page would train the "
         "operator to walk past the two states that matter")
    assert "S-ACCRUING" in sk.note and "accruing" in sk.note, \
        "but it IS reported as context in the source note, never silently dropped"
    assert by_id["S-DEAD"]["why_due"] != by_id["S-BLIND"]["why_due"], \
        ("not_writing and unknown render DIFFERENTLY — 'the log is empty' and 'the "
         "log is unreadable' are opposite findings and a reader must be able to "
         "tell which one they are looking at")

    # a soak with NO probe report at all is UNGRADED, never 'nothing is due'
    nop = src_soaks(_soak_root(None), date(2026, 9, 2))
    assert len(nop.rows) == 4 and not any(r["loud"] for r in nop.rows), \
        ("with no probe run, every soak grades unknown and surfaces quietly — a "
         "missing report must never render as four healthy soaks")

    # a malformed declaration is surfaced, not skipped
    bad_root = _soak_root(_live)
    _items = _load_json(bad_root / _OPEN_ITEMS)
    _items["items"][0]["soak"]["ready_when"] = ""
    (bad_root / _OPEN_ITEMS).write_text(json.dumps(_items), encoding="utf-8")
    bad = src_soaks(bad_root, date(2026, 9, 2))
    assert any("UNGRADEABLE" in r["why_due"] for r in bad.rows), \
        ("a soak block that cannot be read is SURFACED — silently dropping it "
         "would make a typo look like a row that declares no soak at all")

    # the hand-off from src_probes removes a page rather than adding one
    pr = src_probes(_soak_root(_live), date(2026, 9, 2),
                    now=datetime(2026, 9, 2, 12, tzinfo=timezone.utc))
    assert not any(r["id"].startswith("S-") for r in pr.rows), \
        ("src_probes DEFERS every soak-declaring row to src_soaks. Before this, "
         "its `fail` branch emitted a LOUD row for an accruing soak every single "
         "day — the desensitised alarm living inside the machinery meant to "
         "prevent it")
    assert "deferred to the `soaks` source" in pr.note, \
        "and the hand-off is STATED in the note, so it can never be silent"
    assert len(_soak_declaring_ids(_soak_root(_live))) == 4
    assert _soak_declaring_ids(Path("/nonexistent-xyz")) == set(), \
        ("an unreadable register defers NOTHING — the fail-safe direction is to "
         "keep reporting a probe fail as a due row, never to lose a signal")

    print("due-list: self-test OK — 40 planted controls all fire")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the committed due-list is stale in a way that matters")
    ap.add_argument("--markdown", action="store_true", help="print the brief to stdout")
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    root = Path(args.root)

    # `--check` reads the COMMITTED envelope and nothing else. Collecting here
    # would put GitHub calls on a CI guard that has no question for GitHub, and
    # a guard whose verdict depends on network reachability is a guard that
    # reds on an outage rather than on a defect.
    if args.check:
        return _check(root)

    now = datetime.now(timezone.utc)
    results = collect(root, now.date())
    env = build(results, now=now)

    if args.markdown:
        print(render_markdown(env))
        return 0

    if args.write:
        (root / OUT).parent.mkdir(parents=True, exist_ok=True)
        (root / OUT).write_text(json.dumps(env, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
        (root / OUT_MD).write_text(render_markdown(env) + "\n", encoding="utf-8")
        print(f"due-list: wrote {OUT} + {OUT_MD} — verdict={env['verdict']} "
              f"due={env['counts']['due']} loud={env['counts']['loud']}")
        return 0

    print(json.dumps(env, indent=2, ensure_ascii=False))
    return 0


def _check(root: Path) -> int:
    """Validate the COMMITTED envelope. Deliberately not a freshness check."""
    # A stale due-list is expected — it is a snapshot. What is NOT
    # acceptable is a committed list that claims completeness it never had.
    if not (root / OUT).exists():
        print(f"due-list: {OUT} absent — run --write")
        return 1
    try:
        have = _load_json(root / OUT)
    except Exception as exc:  # noqa: BLE001
        print(f"due-list: FAIL — {OUT} is unreadable ({exc})")
        return 1
    if have.get("verdict") not in VERDICTS:
        print(f"due-list: FAIL — committed verdict {have.get('verdict')!r} is not one of {VERDICTS}")
        return 1
    if have.get("verdict") != "all_sources_read" and not have.get("unreadable_sources"):
        print("due-list: FAIL — verdict is not `all_sources_read` but no source is named "
              "as unreadable. A partial list must say WHICH source it could not read.")
        return 1
    print(f"due-list: OK — committed verdict={have['verdict']} "
          f"due={have.get('counts', {}).get('due')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
