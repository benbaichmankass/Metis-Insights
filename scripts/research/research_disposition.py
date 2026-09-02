#!/usr/bin/env python3
"""Did anyone READ this research result, and what did they decide?

R2 of `docs/research/RESEARCH-WORKFLOW-ARCHITECTURE-2026-08-27.md` makes landing
part of the run: a job that exits 0 having landed nothing is a failed job. R1-R6
then STOP THERE. Nothing in the architecture, and nothing in any of the five
review skills, covers the next step — a row that landed and that nobody ever
read. Measured 2026-08-30: `grep -c 'corpus\\|research/queue'` over the
system-review / health-review / performance-review / ml-review / research-driver
SKILL.md files returns **0 for all five**. The research pipeline is invisible to
every review we run.

⚠️ THE CORPORA ARE NOT UNREAD IN THE `provenance-consumer-guard` SENSE. Eight
analysis scripts read them (`m20_coverage_rollup.py`, `e35_matrix_recheck.py`,
`e35_resweep_verdict_diff.py`, ...). That guard asks *does a consumer exist*;
this asks the question one level over, which no guard asks: **was the consumer
RUN, on THIS batch, and did a decision come out of it.** A tool that could have
read a result is not a record that anyone did.

THE UNIT IS `(corpus, run_stamp, leg)`, not the row. A row is too fine to decide
anything about (8,321 in e35 alone) and a whole corpus is too coarse to decide
anything at all. The leg is what a decision is actually taken about — "does this
leg get a bracket change" — and the run stamp is what separates this batch's
answer from the answer six runs ago. Measured on the live stores: 70 units in
e35 over 66 stamps, 218 in m20 over 218 stamps.

⚠️ THE LEDGER IS A SEPARATE STORE, DELIBERATELY. Stamping a `dispositioned_at`
onto the corpus row would make an append-only MEASUREMENT record mutable, and a
re-run that supersedes a row by `measurement_key` would silently carry the old
disposition onto a number nobody read. The measurement and the reading of it are
different facts with different lifetimes.

FIVE STATES, NEVER COLLAPSED (`collapsed-state-guard` contract
`research_disposition.state`):

  dispositioned      an entry exists for this unit
  unread             rows exist, no entry, and NO later run covers this leg
                     — THE FINDING
  superseded_unread  no entry, but a later run for the same (corpus, leg) landed
                     — reading it is moot, and calling that a failure is how a
                     detector trains its reader to ignore it
  no_rows            the corpus holds nothing for this unit
  corpus_unreadable  WE COULD NOT LOOK — never folded into "nothing unread"

The distinction between `unread` and `superseded_unread` is the whole reason this
is not a two-state check. m20 carries 218 stamps against 51 legs: most historical
units are superseded by construction, and a detector reporting 218 failures on
day one is the desensitized-alarm P1 this repo files as its own bug class.

A DISPOSITION MUST STATE A REASON, AND `no_action_warranted` IS THE ONE THAT CAN
LIE. `backlog_drive` already had to be hardened against exactly this: measured
2026-08-13, **75 recorded review touches said "no new evidence bearing on this
item, carried forward unchanged"** while the guard stayed green and the backlog
grew +129 net over 30 days. The same non-reason vocabulary is refused here, and
it is IMPORTED from `render_system_report.py` rather than re-typed — two copies
of "what counts as a non-reason" is how the two drift.

Tier-1: reads two committed JSONL stores and appends to a third. No live path, no
order path, no VM, no network.
# wiring: manual-only - the /performance-review session runs `--report` as its
# research_results_disposition coverage key; there is deliberately no blocking CI
# guard, because the operator's decision is that a review READS the results and
# decides, rather than a gate refusing every PR while 92 units sit unread.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

LEDGER = REPO / "docs/research/research-disposition-ledger.jsonl"

#: The corpora this covers: (path, run-stamp field, LEG field).
#:
#: ⚠️ THE LEG FIELD IS PER-CORPUS AND MUST NOT GO BACK TO A HARDCODED "leg".
#: It was hardcoded until 2026-08-30, and the failure mode is the one this whole
#: module exists to prevent. `load_units` skips any row missing stamp-or-leg, so
#: a corpus whose rows name their unit anything else contributes ZERO units —
#: SILENTLY, and indistinguishably from a corpus that is genuinely empty. The
#: read stage would report nothing unread and be believed.
#:
#: Found by landing one: gld_compat rows carry `account_id`, never `leg`, so
#: registering it with the old signature would have produced a corpus that reads
#: as fully dispositioned while nobody had read a single row.
#:
#: ⚠️ REGISTERING A NEW CORPUS IS PART OF LANDING IT. A producer that writes to
#: a store absent from this table has built a landing nothing can READ, which is
#: the R1-R6 gap (the chain ends at `landed`) reproduced one level down.
CORPORA = {
    "e35": (REPO / "docs/research/e35-bracket-corpus.jsonl", "sweep_generated_at", "leg"),
    "m20": (REPO / "docs/research/m20-sweep-corpus.jsonl", "sweep_generated_at", "leg"),
    # The decision unit here is the ACCOUNT, not the strategy: RQ-20260827-001
    # asks which PER-ACCOUNT verdicts move, so each account's verdict is what
    # someone has to read and rule on. One run yields ~11 units, deliberately.
    "gld_compat": (
        REPO / "docs/research/gld-compat-matrix-verdicts.jsonl",
        "run_generated_at",
        "account_id",
    ),
}

#: Per-corpus field holding the ACHIEVED OOS sample size, for the R4 power gate.
#: `None` where the corpus does not carry one — reported as `n_oos: null`, NEVER
#: 0. A zero would assert a measured empty sample; absent is not empty.
#:
#: ⚠️ e35 IS `base_oos_trades`, NOT `split_target_oos`. It read `None` until
#: 2026-08-31 and every e35 unit was therefore UNGRADEABLE. The tempting field
#: is `split_target_oos`, and it is the wrong one twice over: it is a run
#: TARGET rather than a measurement, and it is sparse — non-null on 566 of
#: 8,520 rows (6.6%). Keying the gate on it would grade 6.6% of the corpus
#: against a run setting and call the rest unknown.
#:
#: ⚠️ THIS COMMENT SAID "exactly one distinct value, 50" UNTIL 2026-08-31 — do
#: not re-quote that. The re-sweep ran at target 60, so the corpus carries
#: {50: 377, 60: 189}. The constant was only ever CIRCUMSTANTIAL support; the
#: primary reason is target-is-not-measurement, and that is now measurable
#: rather than inferred: of the 287 rows carrying BOTH fields, 280 (97.6%) have
#: `base_oos_trades != split_target_oos`, and where the target is 60 the
#: achieved count takes 11 distinct values including 4, 5 and 8. The choice of
#: `base_oos_trades` is therefore on STRONGER evidence than when it was made.
#:
#: THAT 97.6% FIGURE IS NOW PINNED, as of 2026-09-02. It was recorded-but-
#: unguarded until then: `tests/test_e35_achieved_oos_count.py` pinned a
#: coverage ceiling and the observed value set {50, 60}, and NEITHER would fail
#: if `split_target_oos` started tracking the achieved count — the one
#: development that would reopen this choice. It now carries
#: `assert_target_diverges_from_achieved`, which fails below 50% divergence over
#: rows holding both fields, with a denominator floor so a corpus that stopped
#: carrying the evidence reads as "could not look" rather than as a pass.
#:
#: PROVEN TO FIRE, not assumed to: with the live corpus rewritten so the target
#: tracks the achieved count *within the already-recorded value set* {50, 60} —
#: the case built specifically to slip past the two older pins — the new
#: assertion was the ONLY one of the eight tests in that file to fail
#: (280/287 = 97.6% -> 0/287 = 0.0%). Re-measured the same day at 943a7192:
#: 8,520 rows, 287 carrying both fields, 280 differing.
#:
#: ⚠️ ROWS EXTRACTED BEFORE 2026-08-31 CARRY NO ACHIEVED COUNT AND CANNOT BE
#: BACK-FILLED FROM A SESSION. The source `report.json` files are not committed
#: — they exist only as workflow artifacts, and root CLAUDE.md states a PM-side
#: session has no artifact download. So those rows stay `n_oos: null` (correctly
#: ungradeable) until their legs are RE-SWEPT, which is the multi-hour run the
#: corpus exists to avoid. The fix is forward-looking; it does not retroactively
#: unblock the units that were already stuck.
#: `gld_compat` is None because that job is DETERMINISTIC — its queue unit's
#: `why_not_inferential` says an expected-n would be "theatre rather than a bar",
#: since re-running a fixed grader over a fixed ledger gives the same answer every
#: time. So `n_oos: null` here means "no sample size APPLIES", not "we failed to
#: read one"; the R4 power gate correctly declines to grade it.
N_FIELD = {"e35": "base_oos_trades", "m20": "base_trades_OOS", "gld_compat": None}

#: Mirrors research_queue.ACCRUING. Duplicated as a literal rather than
#: imported because this module must stay readable when the queue package
#: is not importable; a test pins the two equal so they cannot drift.
ACCRUING_STATE = "accruing"

#: Mirrors research_queue.DATA_SHORTFALL_STATES — the admission verdicts that
#: mean "there is not enough DATA to answer this yet", as opposed to "nobody
#: declared what would count as an answer".
#:
#: ⚠️ THIS SET IS THE OTHER HALF OF A LENIENCY DECISION AND MUST NOT SHRINK
#: BELOW THE QUEUE'S. Operator directive 2026-08-31 made `underpowered` and
#: `infeasible` RUNNABLE — *"I'd rather err on the lenient side here and not
#: exclude tests that may [give] some insights"*. That is only safe because
#: nothing may CLOSE such a unit as answered. Widening the front door without
#: widening this refusal converts "we ran it anyway, honestly labelled" into
#: "we ran it and nothing stops us calling the answer a result", which is the
#: precise failure the admission gate was built to prevent. A test pins this
#: equal to the queue's tuple so the two cannot drift apart.
DATA_SHORTFALL_STATES = ("accruing", "underpowered", "infeasible")

DISPOSITIONED = "dispositioned"
UNREAD = "unread"
SUPERSEDED_UNREAD = "superseded_unread"
NO_ROWS = "no_rows"
CORPUS_UNREADABLE = "corpus_unreadable"

STATES = (DISPOSITIONED, UNREAD, SUPERSEDED_UNREAD, NO_ROWS, CORPUS_UNREADABLE)

#: What a reader may conclude. `underpowered` is not a failure to read — it is a
#: read whose answer is "this cannot answer the question", which R4 converts into
#: a data-acquisition task rather than a verdict.
VERDICTS = ("actioned", "no_action_warranted", "underpowered", "superseded")

#: The verdicts that CLOSE the question. `underpowered` and `superseded` are
#: deliberately NOT here: both are honest non-answers, and a unit admitted as
#: `accruing` may legitimately be recorded as either. What must not happen is a
#: unit the ADMISSION GATE accepted on the explicit basis that it cannot answer
#: its question yet being recorded as having answered it.
TERMINAL_VERDICTS = ("actioned", "no_action_warranted")


def _non_reasons() -> tuple[str, ...]:
    """The non-reason vocabulary, IMPORTED from the review validator.

    Not re-typed. `render_system_report.py::_NON_REASONS` is the one definition;
    a second copy here would be free to drift from the rule it is enforcing, and
    the drift would silently WIDEN what counts as a real reason.
    """
    sys.path.insert(0, str(REPO))
    from scripts.reports.render_system_report import _NON_REASONS  # noqa: PLC0415

    return tuple(_NON_REASONS)


def load_units(corpus: str) -> tuple[str, dict]:
    """Return (read_state, {(stamp, leg): {"rows": int, "n_oos": int|None}}).

    read_state is `read` or `corpus_unreadable`. A missing file is UNREADABLE,
    not empty: "the store is not there" and "the store holds nothing" are
    different facts, and only one of them is safe to report as no findings.
    """
    path, stamp_field, leg_field = CORPORA[corpus]
    n_field = N_FIELD[corpus]
    if not path.exists():
        return CORPUS_UNREADABLE, {}
    units: dict = {}
    try:
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                stamp, leg = row.get(stamp_field), row.get(leg_field)
                if not stamp or not leg:
                    continue
                u = units.setdefault((stamp, leg), {"rows": 0, "n_oos": None,
                                                    "rows_with_n": 0,
                                                    "power_state": None,
                                                    "research_unit": None})
                u["rows"] += 1
                # ⚠️ THE READER THAT MAKES `accruing` MEAN SOMETHING. Without
                # it a row from a job that declared UP FRONT it cannot answer
                # its question yet is graded as an ordinary unit, and the
                # queue's "do not read this as a test result" is a promise
                # nothing keeps.
                #
                # WORST-STATE WINS across a unit's rows: if ANY row came from an
                # accruing run the whole unit is accrual-tainted, because a unit
                # is only as gradeable as its least gradeable evidence. Taking
                # the newest or the majority would let one clean row launder a
                # unit that is mostly accrual.
                #
                # `None` = NOT QUEUE-DISPATCHED (manual run, or extracted before
                # the stamp shipped). It is NOT a clearance and must never be
                # read as one — which is why it stays a distinct value rather
                # than defaulting to something that looks like a pass.
                ps = row.get("research_power_state")
                if isinstance(ps, str) and ps.strip():
                    if u["power_state"] != ACCRUING_STATE:
                        u["power_state"] = ps.strip()
                ru = row.get("research_unit")
                if isinstance(ru, str) and ru.strip() and not u["research_unit"]:
                    u["research_unit"] = ru.strip()
                if n_field:
                    v = row.get(n_field)
                    if isinstance(v, (int, float)):
                        # ⚠️ `max()` IS CORRECT HERE, AND THAT IS NOW MEASURED
                        # RATHER THAN ASSUMED. `n_oos` is a LEG-LEVEL CONSTANT:
                        # every row of a unit describes one cell of the same
                        # base backtest, so they all report the same achieved
                        # OOS book. MEASURED 2026-09-02 over all 315 units of
                        # the two power-graded corpora at 943a7192 — e35 97 +
                        # m20 218 — the 258 units carrying at least one value
                        # (41 e35, 217 m20) have EXACTLY ONE distinct value
                        # each; units where it varies: 0. So max(), min() and
                        # "worst-state wins" return the same number on 258/258,
                        # and the neighbouring `power_state` reducer's
                        # worst-state discipline has nothing to disagree with.
                        # Changing this to min() would be a no-op dressed as a
                        # correctness fix.
                        #
                        # ⚠️ WHAT IS *NOT* SAFE IS REPORTING IT WITHOUT
                        # `rows_with_n`. On e35 the value is carried by 7 of a
                        # unit's 199 rows — 3.52%, on 41/41 of the units that
                        # have one — while the report prints `rows=199` on the
                        # same line. 199 is the unit's row count, NOT the
                        # denominator of n_oos, and the two sitting side by side
                        # invite exactly the gated-subset misread. The invariant
                        # that makes max() safe is the constancy, not the
                        # coverage; `rows_with_n` is what lets a reader check
                        # the constancy is still being relied on honestly.
                        # `tests/test_research_disposition_denominator.py` fails
                        # if a unit's values ever diverge.
                        u["rows_with_n"] += 1
                        u["n_oos"] = max(u["n_oos"] or 0, int(v))
    except (OSError, json.JSONDecodeError):
        return CORPUS_UNREADABLE, {}
    return "read", units


def load_ledger(ledger: Path = LEDGER) -> tuple[str, set]:
    """Return (read_state, {(corpus, stamp, leg)}) of dispositioned units.

    An ABSENT ledger is `read` with an empty set — nothing has been dispositioned
    yet is a true and expected state on day one. An UNREADABLE one is not.
    """
    if not ledger.exists():
        return "read", set()
    seen = set()
    try:
        with ledger.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                seen.add((e.get("corpus"), e.get("run_stamp"), e.get("leg")))
    except (OSError, json.JSONDecodeError):
        return CORPUS_UNREADABLE, set()
    return "read", seen


def state_for_unit(key, units: dict, seen: set, latest_by_leg: dict) -> str:
    """Grade ONE unit. Pure — the policy is arguable in a test, not against a store.

    Extracted for the same reason `stuck_automation_branches.state_for_age` was:
    a self-test that recomputes the rule instead of calling it proves nothing.
    """
    corpus, stamp, leg = key
    if key in seen:
        return DISPOSITIONED
    if (corpus, leg) not in units:
        return NO_ROWS
    if latest_by_leg.get((corpus, leg)) != stamp:
        return SUPERSEDED_UNREAD
    return UNREAD


def survey(corpora=None, ledger: Path = LEDGER) -> dict:
    """Grade every unit across the corpora. The read half of the mechanism."""
    corpora = corpora or list(CORPORA)
    led_state, seen = load_ledger(ledger)
    out = {"ledger_state": led_state, "corpora": {}, "units": [], "summary": {}}
    counts = dict.fromkeys(STATES, 0)
    for corpus in corpora:
        read_state, units = load_units(corpus)
        out["corpora"][corpus] = read_state
        if read_state == CORPUS_UNREADABLE:
            counts[CORPUS_UNREADABLE] += 1
            continue
        present = {(corpus, leg) for (_s, leg) in units}
        latest = {}
        for (stamp, leg) in units:
            k = (corpus, leg)
            if k not in latest or stamp > latest[k]:
                latest[k] = stamp
        # Every stamp a leg carries, oldest first — the supersession chain.
        # Needed because `superseded_unread` names a state but not WHAT
        # superseded the unit, and "was the thing that superseded me ever
        # actually READ" is the only question that separates the benign residue
        # of a re-measurement from a leg no measurement of which was ever read.
        chain: dict = {}
        for (stamp, leg) in units:
            chain.setdefault((corpus, leg), []).append(stamp)
        for stamps_for_leg in chain.values():
            stamps_for_leg.sort()
        for (stamp, leg), meta in sorted(units.items()):
            st = state_for_unit((corpus, stamp, leg), present, seen, latest)
            counts[st] += 1
            # The superseding unit is the LATEST stamp for the leg, which is what
            # `state_for_unit` compares against. Its own state is resolved below,
            # after every unit has been graded.
            sup_by = latest.get((corpus, leg)) if st == SUPERSEDED_UNREAD else None
            out["units"].append({
                "corpus": corpus, "run_stamp": stamp, "leg": leg,
                "rows": meta["rows"], "n_oos": meta["n_oos"], "state": st,
                # ⚠️ THE DENOMINATOR OF `n_oos`, AND IT IS NOT `rows`. On e35 a
                # unit holds 199 rows and 7 of them carry the achieved count, so
                # printing `rows=199 n_oos=49` alone invites a reader to take 49
                # as a statistic over 199 rows. It is a statistic over 7. A
                # number without its population is not yet a claim.
                "rows_with_n": meta["rows_with_n"],
                # `None` on anything that is not superseded. NOT folded into the
                # state: "superseded by a unit somebody read" and "superseded by
                # a unit nobody read" are the benign and the load-bearing halves
                # of the same count, and collapsing them is what left 256 sitting
                # undifferentiated for two days.
                "superseded_by": sup_by,
                # ⚠️ THESE TWO ARE THE READER. `load_units` recorded them from
                # 2026-08-31 and NOTHING read them back — written-and-never-read,
                # the `exit_price_source` shape this repo has already paid for
                # (12 writers, 1 unrelated reader). A field a reviewer can see in
                # the store but not in the tool's output is one the tool is
                # implicitly asserting does not matter.
                "power_state": meta["power_state"],
                "research_unit": meta["research_unit"],
            })
    # Resolve each superseded unit's successor STATE, once every unit is graded.
    state_by_key = {(u["corpus"], u["leg"], u["run_stamp"]): u["state"]
                    for u in out["units"]}
    for u in out["units"]:
        u["superseded_by_state"] = (
            state_by_key.get((u["corpus"], u["leg"], u["superseded_by"]))
            if u["superseded_by"] else None
        )
    out["summary"] = counts
    return out


#: How a `superseded_unread` unit partitions once you ask what superseded it.
#:
#: ⚠️ THE TEST IS THE SUCCESSOR'S STATE, NOT THE CHAIN LENGTH. A unit three
#: re-sweeps back whose leg was eventually READ is benign: a measurement of that
#: leg WAS read, which is the criterion's own stated rationale. Counting each
#: intermediate link of such a chain as a gap inflates the answer badly — over
#: the live stores it is the difference between 33 and 161 (see
#: `partition_superseded`).
SUPERSEDED_BENIGN = "benign_read_successor"
SUPERSEDED_GAP = "gap_no_measurement_ever_read"


def partition_superseded(s: dict) -> dict:
    """Split the `superseded_unread` pile into the benign half and the real gap.

    THE COUNT ALONE WAS NEVER THE FINDING. `superseded_unread: 256` is a state,
    not a problem: most of it is the ordinary residue of re-measuring a leg, and
    a detector that reports it as 256 failures is the desensitized-alarm bug
    class this module already refuses to be. The question worth asking is
    narrower — **is there a leg for which NO measurement was ever read?**

    A unit is BENIGN when the newest run for its leg is `dispositioned`: someone
    read that leg, on the newest evidence, and the older rows are superseded by a
    reading rather than by silence. It is a GAP when the newest run is itself
    `unread` — nobody has read any measurement of that leg, and the superseded
    rows underneath it are not moot, they are hidden behind an unread one.

    ⚠️ THE GAP IS NOT WORK IN ITSELF — ITS LEG'S NEWEST UNIT IS. Dispositioning
    the newest run converts every superseded unit under it in one step, which is
    why this reports the distinct legs alongside the raw count: the count is the
    exposure, the leg list is the task list, and they differ by an order of
    magnitude.
    """
    rows = [u for u in s["units"] if u["state"] == SUPERSEDED_UNREAD]
    out = {"total": len(rows), SUPERSEDED_BENIGN: 0, SUPERSEDED_GAP: 0,
           "gap_legs": [], "by_corpus": {}}
    gap_legs = set()
    for u in rows:
        half = (SUPERSEDED_BENIGN if u["superseded_by_state"] == DISPOSITIONED
                else SUPERSEDED_GAP)
        out[half] += 1
        c = out["by_corpus"].setdefault(
            u["corpus"], {SUPERSEDED_BENIGN: 0, SUPERSEDED_GAP: 0})
        c[half] += 1
        if half == SUPERSEDED_GAP:
            gap_legs.add((u["corpus"], u["leg"], u["superseded_by"]))
    out["gap_legs"] = sorted(gap_legs)
    # An arithmetic cross-check, inside the transform, because a re-read would
    # not catch a unit counted into both halves — the shape that made a row
    # count disagree by 2 on 2026-08-09.
    assert out[SUPERSEDED_BENIGN] + out[SUPERSEDED_GAP] == out["total"], out
    return out


#: What `append` was able to establish about the unit's admission state.
#: NEVER COLLAPSED, and the reason is the usual one: `unit_absent` and
#: `corpus_unreadable` are *we could not look*, which is a different fact from
#: `clear` (*we looked and it was not accruing*). Folding either into `clear`
#: would let a corpus outage silently launder exactly the writes this refuses.
ACCRUAL_CHECKS = ("clear", "accruing_overridden", "unit_absent", "corpus_unreadable")


def _accrual_check(entry: dict, non_reasons=None) -> str:
    """Grade the unit's admission state, and REFUSE a terminal verdict on one
    the R4 gate admitted as `accruing`.

    WHY THIS EXISTS. The admission gate marks a unit with one of the
    DATA_SHORTFALL_STATES — "there is not enough data to answer this yet" —
    and the extractor stamps that onto every row it produces. Until 2026-08-31
    the chain STOPPED THERE: nothing prevented the same unit being closed
    `actioned` or `no_action_warranted`. The gate's whole promise is that such
    a result is not read as a test result, and the ledger is the one place that
    promise could be broken on the record.

    ⚠️ IT CARRIES MORE WEIGHT SINCE THE SAME DAY'S LENIENCY DECISION. When
    `underpowered` and `infeasible` BLOCKED, the front door did most of the
    work and this was a backstop. Now that all three shortfall states RUN, this
    refusal is the ONLY thing standing between a deliberately-thin run and a
    ledger entry claiming it answered something.

    ⚠️ ONLY THE TERMINAL VERDICTS ARE REFUSED. `underpowered` and `superseded`
    are honest non-answers and are exactly what an accruing unit SHOULD be
    recorded as; refusing them would leave the reviewer no legal way to write
    down what they found, which is how a gate teaches people to route around it.

    ⚠️ IT FAILS OPEN ON AN UNREADABLE CORPUS, AND STAMPS THAT IT DID. This gates
    a bookkeeping RECORD, not an order, so blocking every disposition during a
    corpus outage would suppress honest reading to prevent a rare dishonest one.
    What must not happen is the distinction vanishing — hence the stamp.
    """
    override = str(entry.get("accrual_override_reason") or "").strip()
    try:
        state, units = load_units(entry["corpus"])
    except Exception:  # noqa: BLE001  # allow-silent: recorded as corpus_unreadable below, never as `clear`
        return "corpus_unreadable"
    if state != "read":
        return "corpus_unreadable"
    meta = units.get((entry["run_stamp"], entry["leg"]))
    if meta is None:
        return "unit_absent"
    admitted = meta.get("power_state")
    if admitted not in DATA_SHORTFALL_STATES:
        return "clear"
    if entry["verdict"] not in TERMINAL_VERDICTS:
        return "clear"
    if len(override) < 20:
        raise ValueError(
            f"unit was admitted as {admitted!r} and cannot be closed "
            f"{entry['verdict']!r}. The gate let this job RUN on the stated "
            "basis that there is not enough data to answer its question yet, "
            "so recording an answer contradicts its own admission. Record "
            "'underpowered' or 'superseded', or state an "
            "`accrual_override_reason` saying what changed such that this unit "
            "IS now decidable."
        )
    low = override.lower()
    for bad in (non_reasons if non_reasons is not None else _non_reasons()):
        if bad in low:
            raise ValueError(
                f"accrual_override_reason reads as a non-reason ({bad!r})"
            )
    return "accruing_overridden"


def append(entry: dict, ledger: Path = LEDGER, non_reasons=None) -> dict:
    """Append one disposition. Refuses a vacuous reason.

    Refusing is the point. A ledger that accepts "carried forward unchanged" is a
    ledger that reports full coverage over 75 touches that decided nothing.
    """
    for f in ("corpus", "run_stamp", "leg", "verdict", "reason"):
        if not entry.get(f):
            raise ValueError(f"disposition needs a non-empty {f!r}")
    if entry["verdict"] not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, got {entry['verdict']!r}")
    if entry["corpus"] not in CORPORA:
        raise ValueError(f"unknown corpus {entry['corpus']!r}")
    reason = str(entry["reason"]).strip()
    if len(reason) < 20:
        raise ValueError("reason is too short to be a reason")
    low = reason.lower()
    for bad in (non_reasons if non_reasons is not None else _non_reasons()):
        if bad in low:
            raise ValueError(
                f"reason reads as a non-reason ({bad!r}). A disposition states what "
                "the numbers said and what follows from it; deferring is allowed but "
                "must be SAID, not achieved by silence."
            )
    if entry["verdict"] == "actioned" and not entry.get("actions"):
        raise ValueError("verdict 'actioned' must name what was done in `actions`")
    entry = dict(entry)
    entry["accrual_check"] = _accrual_check(entry, non_reasons=non_reasons)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def _selftest() -> int:
    """Non-vacuity controls: each asserts the rule by CALLING it, never by
    recomputing it. A self-test that re-derives the predicate proves only that
    the test and the code agree with themselves.
    """
    import tempfile

    ok, fail = 0, 0

    def check(name, cond):
        nonlocal ok, fail
        if cond:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL: {name}")

    units = {("m20", "avax"), ("m20", "sol")}
    latest = {("m20", "avax"): "T2", ("m20", "sol"): "T1"}

    check("a ledgered unit is dispositioned",
          state_for_unit(("m20", "T2", "avax"), units, {("m20", "T2", "avax")}, latest)
          == DISPOSITIONED)
    check("the newest unread run for a leg is UNREAD",
          state_for_unit(("m20", "T2", "avax"), units, set(), latest) == UNREAD)
    check("an older unread run for the same leg is SUPERSEDED_UNREAD, not unread",
          state_for_unit(("m20", "T1", "avax"), units, set(), latest) == SUPERSEDED_UNREAD)
    check("a leg with no rows is NO_ROWS",
          state_for_unit(("m20", "T1", "ghost"), units, set(), latest) == NO_ROWS)

    # ── the states are genuinely distinguishable ─────────────────────────────
    # Not decoration: if SUPERSEDED_UNREAD ever collapsed into UNREAD, the
    # detector would report 218 m20 failures on day one and be ignored by week
    # two. That is the failure mode, so it gets its own assertion.
    check("UNREAD and SUPERSEDED_UNREAD are different values", UNREAD != SUPERSEDED_UNREAD)

    # ── a missing corpus is UNREADABLE, never empty ──────────────────────────
    real = CORPORA["m20"]
    try:
        CORPORA["m20"] = (Path("/nonexistent/nope.jsonl"), "sweep_generated_at", "leg")
        check("a missing corpus reads as corpus_unreadable",
              load_units("m20")[0] == CORPUS_UNREADABLE)
    finally:
        CORPORA["m20"] = real

    # ── the reason gate ──────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as d:
        led = Path(d) / "led.jsonl"
        base = {"corpus": "m20", "run_stamp": "T1", "leg": "avax",
                "verdict": "no_action_warranted"}

        def refuses(reason, **extra):
            try:
                append({**base, "reason": reason, **extra}, ledger=led)
                return False
            except ValueError:
                return True

        check("a vacuous reason is REFUSED",
              refuses("No new evidence bearing on this leg, carried forward."))
        check("a too-short reason is REFUSED", refuses("looks fine"))
        check("an unknown verdict is REFUSED",
              refuses("The OOS book is thin but the sign is stable across folds.",
                      **{"verdict": "looks_good"}))
        check("'actioned' with no actions is REFUSED",
              refuses("Cell tp6_to96 clears the gate on both folds, shipping it.",
                      **{"verdict": "actioned"}))

        good = "OOS base book is 12 trades, below the declared floor of 49; converting to a data-acquisition task."
        append({**base, "verdict": "underpowered", "reason": good}, ledger=led)
        check("a real reason is ACCEPTED", led.exists() and led.read_text().count("\n") == 1)
        check("the accepted entry is then seen as dispositioned",
              ("m20", "T1", "avax") in load_ledger(led)[1])

    print(f"selftest: {ok}/{ok + fail} passed")
    return 0 if fail == 0 else 1


def _record(a) -> int:
    """Write ONE disposition through `append`, with a pre-flight existence check.

    ⚠️ THE WRITE HALF HAD NO REACHABLE SURFACE UNTIL 2026-08-31. `append` was
    complete and well-guarded from the day it shipped, and `main` exposed only
    `--report` / `--unread-only` / `--selftest` — so the ONLY way to record a
    disposition was to import the module from an ad-hoc snippet. Measured: no
    production caller anywhere in the repo, and all 75 existing ledger entries
    were written that way. That is the `exit_price_source` shape inverted: not a
    field written and never read, but a reader with no way to write down what it
    read. A mechanism whose supported path is "hand-roll a snippet" is one whose
    validation (`_accrual_check`, the non-reason vocabulary) is one forgotten
    import away from being skipped entirely.

    ⚠️ IT REFUSES A UNIT THE CORPUS DOES NOT HOLD, BEFORE WRITING. `_accrual_check`
    already grades that case `unit_absent`, but it does so INSIDE `append`, after
    the entry is committed — so a typo'd leg or stamp lands a ledger row claiming
    coverage of a unit that does not exist, and `survey` then reports it
    `dispositioned` forever (`state_for_unit` checks membership in `seen` FIRST).
    A disposition for a nonexistent unit is worse than a missing one: it reads as
    coverage. The check is skippable only via `--force`, which stamps
    `unit_absent_override` on the entry so the skip is on the record rather than
    invisible.
    """
    entry = {
        "corpus": a.corpus,
        "run_stamp": a.run_stamp,
        "leg": a.leg,
        "verdict": a.verdict,
        "reason": a.reason,
        "actions": a.actions or [],
        "dispositioned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dispositioned_by": a.by,
    }
    if a.accrual_override_reason:
        entry["accrual_override_reason"] = a.accrual_override_reason

    if a.corpus not in CORPORA:
        print(f"::error::unknown corpus {a.corpus!r}; known: {sorted(CORPORA)}",
              file=sys.stderr)
        return 2
    state, units = load_units(a.corpus)
    if state != "read":
        # Never "nothing to disposition" — we could not look.
        print(f"::error::corpus {a.corpus!r} is UNREADABLE; refusing to record "
              "against a store we could not read", file=sys.stderr)
        return 2
    present = (a.run_stamp, a.leg) in units
    if not present and not a.force:
        near = sorted({leg for (_s, leg) in units if leg == a.leg})
        stamps = sorted({s for (s, leg) in units if leg == a.leg})
        print(f"::error::no unit ({a.run_stamp!r}, {a.leg!r}) in corpus "
              f"{a.corpus!r}. A disposition for a unit the corpus does not hold "
              "reads as COVERAGE of something that does not exist.",
              file=sys.stderr)
        if near:
            print(f"  leg {a.leg!r} exists under stamps: {stamps}", file=sys.stderr)
        else:
            print(f"  leg {a.leg!r} is not in this corpus at all.", file=sys.stderr)
        print("  Re-check the stamp/leg, or pass --force (recorded on the entry).",
              file=sys.stderr)
        return 2
    if not present:
        entry["unit_absent_override"] = True

    if a.dry_run:
        # Validate without writing: run the same guards `append` runs.
        try:
            for f in ("corpus", "run_stamp", "leg", "verdict", "reason"):
                if not entry.get(f):
                    raise ValueError(f"disposition needs a non-empty {f!r}")
            if entry["verdict"] not in VERDICTS:
                raise ValueError(f"verdict must be one of {VERDICTS}")
            if len(str(entry["reason"]).strip()) < 20:
                raise ValueError("reason is too short to be a reason")
            low = str(entry["reason"]).lower()
            for bad in _non_reasons():
                if bad in low:
                    raise ValueError(f"reason reads as a non-reason ({bad!r})")
            check = _accrual_check(entry)
        except ValueError as exc:
            print(f"::error::{exc}", file=sys.stderr)
            return 1
        print(f"(dry-run: nothing written) accrual_check={check}")
        return 0

    try:
        written = append(entry)
    except ValueError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    print(f"recorded {a.corpus}/{a.leg} @ {a.run_stamp}: "
          f"verdict={written['verdict']} accrual_check={written['accrual_check']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--report", action="store_true", help="grade every unit")
    ap.add_argument("--unread-only", action="store_true",
                    help="list only the finding state")
    ap.add_argument("--superseded-partition", action="store_true",
                    help="split superseded_unread into the benign re-measurement "
                         "residue and the legs no measurement of which was read")
    ap.add_argument("--selftest", action="store_true")
    rec = ap.add_argument_group(
        "record one disposition",
        "The WRITE half. Routes to `append`, which owns every validation rule; "
        "this adds only the stamps and a pre-flight existence check.")
    rec.add_argument("--record", action="store_true",
                     help="append one disposition to the ledger")
    rec.add_argument("--corpus", help=f"one of {sorted(CORPORA)}")
    rec.add_argument("--run-stamp", dest="run_stamp")
    rec.add_argument("--leg")
    rec.add_argument("--verdict", choices=VERDICTS)
    rec.add_argument("--reason", help="what the numbers said and what follows")
    rec.add_argument("--actions", action="append", default=[],
                     help="repeatable; REQUIRED when --verdict actioned")
    rec.add_argument("--accrual-override-reason", dest="accrual_override_reason",
                     help="what changed such that an accruing unit IS decidable")
    rec.add_argument("--by", default="", help="who is recording this")
    rec.add_argument("--force", action="store_true",
                     help="record even though the corpus holds no such unit "
                          "(stamps `unit_absent_override` on the entry)")
    rec.add_argument("--dry-run", action="store_true",
                     help="validate and print the accrual_check; write nothing")
    a = ap.parse_args()

    if a.selftest:
        return _selftest()

    if a.record:
        missing = [f for f in ("corpus", "run_stamp", "leg", "verdict", "reason")
                   if not getattr(a, f, None)]
        if missing:
            print(f"::error::--record needs {missing}", file=sys.stderr)
            return 2
        return _record(a)

    s = survey()
    if a.superseded_partition:
        if CORPUS_UNREADABLE in (s["ledger_state"], *s["corpora"].values()):
            print("::error::a store could not be READ - refusing to partition a "
                  "pile we could not fully see", file=sys.stderr)
            return 2
        pt = partition_superseded(s)
        print(json.dumps({k: v for k, v in pt.items() if k != "gap_legs"},
                         indent=2, sort_keys=True))
        # The task list, not the exposure. One disposition per line here clears
        # every superseded unit counted under it.
        print(f"\nlegs whose NEWEST run is unread ({len(pt['gap_legs'])} — "
              "dispositioning these clears the gap half):")
        for corpus, leg, stamp in pt["gap_legs"]:
            print(f"  {corpus:<11} {leg:<26} newest={stamp}")
        return 0
    if CORPUS_UNREADABLE in (s["ledger_state"], *s["corpora"].values()):
        print("::error::a store could not be READ - this is not 'nothing unread'",
              file=sys.stderr)
        print(json.dumps({"ledger": s["ledger_state"], "corpora": s["corpora"]}, indent=2))
        return 2

    print("state counts:", json.dumps(s["summary"]))
    # The admission-state census. Printed BESIDE the state counts because a
    # reviewer deciding what to read next needs to know how much of the pile is
    # accrual-tainted before reading any of it.
    census: dict = {}
    for u in s["units"]:
        k = u["power_state"] or "not_queue_dispatched"
        census[k] = census.get(k, 0) + 1
    print("admission states:", json.dumps(census, sort_keys=True))
    rows = [u for u in s["units"] if not a.unread_only or u["state"] == UNREAD]
    for u in sorted(rows, key=lambda u: (u["state"], u["corpus"], u["run_stamp"])):
        n = "n/a" if u["n_oos"] is None else u["n_oos"]
        # `not_queue_dispatched` rather than a blank or a dash: the unit came
        # from a manual run (or predates the stamp), which is NOT a clearance
        # and must not render as one.
        ps = u["power_state"] or "not_queue_dispatched"
        ru = u["research_unit"] or "-"
        # `n_oos` is printed AS A FRACTION of the rows that carried it, never
        # bare beside `rows`. On e35 those are 7 and 199 — a reader who sees
        # `rows=199 n_oos=49` has been handed a statistic and the wrong
        # denominator on the same line.
        nd = "n/a" if u["n_oos"] is None else f"{n}/{u['rows_with_n']}r"
        print(f"  {u['state']:<18} {u['corpus']:<4} {u['leg']:<26} "
              f"rows={u['rows']:<5} n_oos={nd:<10} power={ps:<20} "
              f"unit={ru:<18} {u['run_stamp']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
