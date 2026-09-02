#!/usr/bin/env python3
# wiring: docs/claude/OPEN-ITEMS.json `soak` block; read by scripts/ops/render_due_list.py::src_soaks
"""Grade a declared SOAK against a threshold, so a session is told when to come back.

STANDING OPERATOR DIRECTIVE, 2026-09-02
---------------------------------------
    "Anything soaking needs to be logged with an alarm that has either a timer
     or a soak threshold, so that we know to get back to it when the soak is
     ready."

This is a STANDING RULE, not a one-off. The canonical statement is
`docs/CLAUDE-RULES-CANONICAL.md` § "A soak must carry its own alarm"; this
module is the executable half.

WHY A TIMER ALONE IS INSUFFICIENT, MEASURED
-------------------------------------------
Every one of the 36 `monitoring` rows in `docs/claude/OPEN-ITEMS.json` was
TIMER-ONLY before this shipped: `check_every_days` + `verified_at`, which says
how often a human must LOOK and can say nothing about whether the thing being
waited for has HAPPENED. There was no field anywhere that could express *"come
back when the soak has N rows"* or *"when `verdicts_differ >= 1`"*.

A timer answers *"is it time to check?"*. A threshold answers *"is it ready?"*.
The register could ask the first question and not the second, so a soak that
went ready on day 2 waited for its day-3 cadence, and a soak that had DIED on
day 1 was re-checked on the same cadence forever with nothing to find.

THE FOUR STATES, NEVER COLLAPSED
--------------------------------
    ready         the threshold is MET -- come back now
    accruing      rows are arriving, threshold not met
    not_writing   NO rows at all since the soak was declared -- THE SOAK IS DEAD
    unknown       we could not READ the soak file

⚠️ `not_writing` IS THE STATE THAT DID NOT EXIST, AND IT IS THE DANGEROUS ONE.
A soak that silently stopped writing is today indistinguishable from one
patiently accruing: both render as "not ready yet", both re-check on the same
cadence, and the operator waits indefinitely on evidence that was never coming.
That is the failure the directive is about, and it is why the state had to
exist before a threshold could mean anything.

⚠️ `unknown` IS NOT `not_writing`, AND FOLDING THEM IS THE WHOLE ERROR AGAIN.
*"The log is unreadable"* and *"the log is empty"* are OPPOSITE findings: the
first says nothing about the world, the second is a real and alarming
measurement of it. Reading an unreadable log as an empty one would manufacture
a dead-soak alarm out of a broken probe; reading an empty one as unreadable
would hide a dead soak behind a shrug. This repo has paid for that collapse
twice (`curl … || echo '{}'`), and `render_due_list.py`'s envelope verdict, the
probe family's exit codes and `exit_anchor.py`'s three-way status all exist to
refuse it.

WHICH STATES ESCALATE, AND WHY THE OTHER TWO DO NOT
---------------------------------------------------
    ready        ESCALATES -- a due row, loud. Something is waiting on a person.
    not_writing  ESCALATES -- a due row, loud. The soak is dead and the wait is
                 futile; every day this stays quiet is a day of false patience.
    unknown      surfaces as a due row, NOT loud. The row is currently
                 unwatched, which is worth saying and is not an emergency --
                 the same polarity `src_probes` already uses for a probe that
                 could not run.
    accruing     DOES NOT ESCALATE. It appears in the due-list BODY as context
                 and produces no row.

⚠️ THE LAST ONE IS A DELIBERATE REFUSAL, NOT AN OVERSIGHT. A daily "soak not
ready" ping is the desensitised-alarm P1 `CLAUDE.md` names in its own words --
*"an alarm that fires constantly and is routinely walked past is not background
noise, the desensitized alarm is ITSELF a P1 bug"* -- and this repo has already
paid 202 CRITICALs for one instance of it
(`BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART`, where a
per-process latch re-armed on every restart and put one condition on 53.7% of
the operator's entire ERROR+ feed). `accruing` is the EXPECTED state of a
healthy soak for its entire life. Alarming on it would train the operator to
walk past the two states that matter.

⚠️ AND THAT IS NOT HYPOTHETICAL HERE. `render_due_list.py::src_probes` emits a
LOUD due row for every probe whose state is `fail` -- and before the
2026-09-02 probe-layer change, a soak probe that was patiently accruing
returned exactly that. So a healthy soak was already firing a loud daily row
in the very machinery meant to fix this. Splitting `source_empty` out of `fail`
is what lets `accruing` go quiet without also silencing a dead soak.

WHAT THIS MODULE IS NOT
-----------------------
**It grades. It clears nothing.** `ready` means the declared threshold is met,
never that the row's `clears_when` is satisfied -- those clauses routinely carry
conditions a predicate cannot express (a human confirming which Telegram chat a
ping landed in, an operator decision). A session clears a row. This says when a
session should look.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: The four verdicts. Never collapsed -- see the module docstring.
STATES = ("ready", "accruing", "not_writing", "unknown")

#: The states that produce a due-list row at all. `accruing` is absent on
#: purpose: it is the healthy, expected state and belongs in the body as
#: context, never as a page.
SURFACING_STATES = ("ready", "not_writing", "unknown")

#: The states that produce a LOUD row -- one CLAUDE.md requires to be reported
#: on in a session's closing summary. `unknown` surfaces without being loud,
#: matching `src_probes`' treatment of a probe that could not run.
ESCALATING_STATES = ("ready", "not_writing")


@dataclass(frozen=True)
class SoakVerdict:
    """One soak's grade, plus everything a reader needs to check it.

    `matched` and `scanned` are `None` when unknown -- NEVER 0. A missing
    denominator read as zero would manufacture the exact `not_writing` alarm
    this module exists to make trustworthy.
    """

    state: str
    why: str
    matched: int | None = None
    scanned: int | None = None
    days_since_declared: int | None = None

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"bad soak state {self.state!r}")

    @property
    def surfaces(self) -> bool:
        return self.state in SURFACING_STATES

    @property
    def escalates(self) -> bool:
        return self.state in ESCALATING_STATES


def declaration_problems(rid: str, soak: object) -> list[str]:
    """Validate a `soak` block. Empty list == usable.

    Deliberately strict about `min_matching`: a soak whose threshold is 0 is
    ready the moment it is declared, which is not a threshold at all. Refusing
    it here stops a decorative declaration from reading as a real one.
    """
    problems: list[str] = []
    if not isinstance(soak, dict):
        return [f"{rid}: `soak` must be an object"]

    if not str(soak.get("log", "")).strip():
        problems.append(
            f"{rid}: `soak.log` is empty — without the log's name nothing can "
            f"say whether it is writing, so `not_writing` is unreachable and "
            f"the declaration cannot do the one thing it exists for")
    if not str(soak.get("declared_at", "")).strip():
        problems.append(
            f"{rid}: `soak.declared_at` is empty — `not_writing` means 'no rows "
            f"SINCE THE SOAK WAS DECLARED', and with no start date that "
            f"sentence has no meaning")
    if not str(soak.get("ready_when", "")).strip():
        problems.append(
            f"{rid}: `soak.ready_when` is empty — this is the whole point of "
            f"the block. State what READY means in DATA (a `probe_lib` "
            f"condition such as `verdicts_differ=true`), not in elapsed days; "
            f"`check_every_days` already carries the timer")

    n = soak.get("min_matching", 1)
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        problems.append(
            f"{rid}: `soak.min_matching` must be an integer >= 1 (got {n!r}). "
            f"A threshold of 0 is met before the soak has written anything, "
            f"which is a decorative declaration wearing a real one's clothes")
    return problems


def grade(
    soak: dict,
    *,
    probe_state: str | None,
    matched: int | None,
    scanned: int | None,
    today: date | None = None,
) -> SoakVerdict:
    """Grade one declared soak from the last probe run's result.

    `probe_state` is the `run_probes.py` state — `pass` / `fail` /
    `source_empty` / `could_not_run` — or `None` when the row has NO probe at
    all. Those two are different facts and are graded differently:
    a broken probe and an undeclared one both mean we cannot see the soak, but
    only the first is a fault.

    ⚠️ THE COUNTS ARE ADVISORY AND THE STATE IS AUTHORITATIVE. `matched` /
    `scanned` refine the WHY text and let `min_matching > 1` be enforced; they
    never invent a state the probe did not report. A probe that published no
    counts yields `None`, and `None` must never be read as 0 — that coercion is
    how an unread denominator becomes a false dead-soak alarm.
    """
    declared = _day(soak.get("declared_at"))
    age = (today - declared).days if (today and declared) else None
    need = soak.get("min_matching", 1)
    need = need if isinstance(need, int) and not isinstance(need, bool) and need >= 1 else 1
    ready_when = str(soak.get("ready_when") or "(no criterion declared)")

    def v(state: str, why: str) -> SoakVerdict:
        return SoakVerdict(state, why, matched=matched, scanned=scanned,
                           days_since_declared=age)

    if probe_state is None:
        return v("unknown",
                 f"NO PROBE IS DECLARED for this soak, so nothing is reading it. "
                 f"Ready means: {ready_when}. This is a KNOWN, DECLARED gap "
                 f"(`probe_absent_reason` says why) — it is NOT evidence the "
                 f"soak is empty, and it is not evidence it is accruing either. "
                 f"Nobody has looked.")

    if probe_state == "could_not_run":
        return v("unknown",
                 f"the probe COULD NOT READ the soak — this says nothing about "
                 f"whether rows exist. ⚠️ It is NOT `not_writing`: an "
                 f"unreadable log and an empty log are opposite findings. The "
                 f"row is currently unwatched and would not tell us if the soak "
                 f"had died.")

    if probe_state == "source_empty":
        # The state that did not exist before 2026-09-02.
        since = (f" — {age}d since it was declared on {declared.isoformat()}"
                 if age is not None and declared else "")
        return v("not_writing",
                 f"⚠️ THE SOAK HAS WRITTEN NOTHING{since}. The log was READ "
                 f"successfully and held ZERO rows, so this is a measurement, "
                 f"not a failure to look. Waiting longer cannot help: either the "
                 f"writer is not deployed, not armed, or broken. Ready would "
                 f"mean: {ready_when} — and no row can ever satisfy it while "
                 f"nothing is being written.")

    if probe_state == "pass":
        # The probe matched. `min_matching` can still hold it back when the
        # declaration asks for more than one sighting.
        if matched is not None and matched < need:
            return v("accruing",
                     f"{matched} of {need} required matching row(s) — the "
                     f"criterion has been seen but not yet {need} times "
                     f"({ready_when})")
        seen = f"{matched} matching row(s)" if matched is not None else "a matching row"
        of = f" of {scanned} scanned" if scanned is not None else ""
        return v("ready",
                 f"✅ READY — {seen}{of} satisfy the declared criterion "
                 f"({ready_when}). ⚠️ Ready is not CLEARED: read the row's "
                 f"`clears_when`, which may carry clauses no predicate can "
                 f"express.")

    if probe_state == "fail":
        # Read, rows present, criterion not met. The healthy waiting state --
        # and the one that must NOT page.
        n = f"{scanned} row(s)" if scanned is not None else "rows"
        since = f", {age}d in" if age is not None else ""
        return v("accruing",
                 f"accruing — the soak holds {n} and none satisfies the "
                 f"criterion yet ({ready_when}){since}. The writer is alive; "
                 f"this is the expected state and does not need attention.")

    return v("unknown",
             f"probe state {probe_state!r} is not one this grader recognises, so "
             f"the soak is UNGRADED. Surfaced rather than dropped: a state we "
             f"cannot read is not a soak we have checked.")


def _day(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


# ── self-test: planted controls, so a vacuous pass is impossible ───────────

def _self_test() -> int:
    fired = 0

    def ok(cond, label):
        nonlocal fired
        assert cond, f"control FAILED: {label}"
        fired += 1

    soak = {"log": "x_soak", "declared_at": "2026-09-01",
            "ready_when": "verdicts_differ=true", "min_matching": 1}
    today = date(2026, 9, 5)

    # ── the four states are each REACHABLE and DISTINCT ────────────────────
    dead = grade(soak, probe_state="source_empty", matched=0, scanned=0, today=today)
    accr = grade(soak, probe_state="fail", matched=0, scanned=412, today=today)
    rdy = grade(soak, probe_state="pass", matched=3, scanned=412, today=today)
    unk = grade(soak, probe_state="could_not_run", matched=None, scanned=None, today=today)

    ok(dead.state == "not_writing", "a source read EMPTY is the dead-soak state")
    ok(accr.state == "accruing", "a source read with rows and no match is accruing")
    ok(rdy.state == "ready", "a matched criterion is ready")
    ok(unk.state == "unknown", "a probe that could not run is unknown")
    ok(len({dead.state, accr.state, rdy.state, unk.state}) == 4,
       "all four states are DISTINCT from one another — the whole point. Before "
       "MI-61 `not_writing` did not exist and a dead soak was indistinguishable "
       "from an accruing one, so the operator waited forever on evidence that "
       "was never coming")

    # ── the two collapses this module exists to refuse ─────────────────────
    ok(dead.state != accr.state,
       "not_writing is NOT accruing: 0 rows and 412 rows are opposite findings "
       "about whether the writer is alive")
    ok(dead.state != unk.state,
       "not_writing is NOT unknown: 'the log is empty' is a MEASUREMENT, 'the "
       "log is unreadable' is the absence of one, and neither may absorb the other")
    ok("NOT `not_writing`" in unk.why,
       "and the unknown verdict says so in words, not only in the state string")
    ok("READ successfully" in dead.why and "ZERO rows" in dead.why,
       "the dead-soak verdict states that the read SUCCEEDED, so a reader "
       "cannot mistake it for a failed look")

    # ── escalation policy: accruing must never page ────────────────────────
    ok(rdy.escalates and dead.escalates,
       "ready and not_writing both escalate — one needs a person, the other "
       "means the wait is futile")
    ok(not accr.escalates and not accr.surfaces,
       "⚠️ accruing NEITHER escalates NOR surfaces as a row. A daily 'soak not "
       "ready' ping is the desensitised-alarm P1 this repo paid 202 CRITICALs "
       "for; accruing is the EXPECTED state of a healthy soak for its whole life")
    ok(unk.surfaces and not unk.escalates,
       "unknown surfaces (the row is unwatched) without being loud — the same "
       "polarity src_probes uses for a probe that could not run")

    # ── counts are advisory; None is never 0 ───────────────────────────────
    dead_nc = grade(soak, probe_state="source_empty", matched=None, scanned=None, today=today)
    ok(dead_nc.state == "not_writing" and dead_nc.scanned is None,
       "the STATE is authoritative and survives absent counts, and an unknown "
       "denominator stays None rather than being coerced to 0")
    accr_nc = grade(soak, probe_state="fail", matched=None, scanned=None, today=today)
    ok(accr_nc.state == "accruing",
       "⚠️ a `fail` with NO counts is still accruing, never not_writing — "
       "coercing a missing denominator to 0 is precisely how an unread probe "
       "would manufacture a false dead-soak alarm")

    # ── min_matching: one sighting is not always the threshold ─────────────
    strict = {**soak, "min_matching": 3}
    ok(grade(strict, probe_state="pass", matched=1, scanned=9, today=today).state == "accruing",
       "a pass below `min_matching` is still accruing — the criterion was seen, "
       "not yet often enough")
    ok(grade(strict, probe_state="pass", matched=3, scanned=9, today=today).state == "ready",
       "and reaching min_matching flips it to ready")
    ok(grade(strict, probe_state="pass", matched=None, scanned=9, today=today).state == "ready",
       "a pass with UNKNOWN counts trusts the probe's own verdict rather than "
       "inventing a shortfall from a number nobody published")

    # ── an undeclared probe is not a broken one ────────────────────────────
    none_probe = grade(soak, probe_state=None, matched=None, scanned=None, today=today)
    ok(none_probe.state == "unknown" and "NO PROBE IS DECLARED" in none_probe.why,
       "a row with no probe grades unknown and says WHY — a declared gap is a "
       "different fact from a probe that broke, though both mean nobody looked")

    # ── an unrecognised state is surfaced, never silently dropped ──────────
    ok(grade(soak, probe_state="banana", matched=None, scanned=None, today=today).state == "unknown",
       "an unrecognised probe state is UNGRADED and surfaced, not treated as clean")

    # ── age is reported so 'how long has it been dead' is answerable ───────
    ok(dead.days_since_declared == 4 and "4d since it was declared" in dead.why,
       "the dead-soak verdict carries how long it has been silent — 'wrote "
       "nothing for 4 days' is a different alarm from 'wrote nothing yet today'")
    ok(grade(soak, probe_state="source_empty", matched=0, scanned=0,
             today=None).days_since_declared is None,
       "and with no clock the age is None rather than a fabricated 0")

    # ── declaration validation ─────────────────────────────────────────────
    ok(declaration_problems("R", soak) == [], "a complete declaration is clean")
    ok(any("ready_when" in p for p in declaration_problems("R", {**soak, "ready_when": ""})),
       "a soak with no `ready_when` is refused — that field IS the threshold, "
       "and without it the block is just a second timer")
    ok(any("declared_at" in p for p in declaration_problems("R", {**soak, "declared_at": ""})),
       "a soak with no `declared_at` is refused — 'no rows SINCE IT WAS "
       "DECLARED' is meaningless without a start date, so not_writing is unreachable")
    ok(any("log" in p for p in declaration_problems("R", {**soak, "log": ""})),
       "a soak with no `log` is refused — nothing could establish whether it writes")
    ok(any("min_matching" in p for p in declaration_problems("R", {**soak, "min_matching": 0})),
       "min_matching 0 is refused: it is met before a single row exists, which "
       "is a decorative threshold")
    ok(any("min_matching" in p for p in declaration_problems("R", {**soak, "min_matching": True})),
       "and a bool is refused rather than being silently read as 1 — True is an "
       "int in Python and would slip through a naive isinstance check")
    ok(declaration_problems("R", "not-an-object"),
       "a non-object `soak` is refused rather than crashing the renderer")

    print(f"soak-alarm: self-test OK — {fired} planted controls all fire")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
