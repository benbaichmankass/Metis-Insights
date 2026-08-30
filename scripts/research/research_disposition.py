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

#: Per-corpus field holding the OOS sample size, for the R4 power gate. `None`
#: where the corpus does not carry one — reported as `n_oos: null`, NEVER 0. A
#: zero would assert a measured empty sample; absent is not empty.
#: `gld_compat` is None because that job is DETERMINISTIC — its queue unit's
#: `why_not_inferential` says an expected-n would be "theatre rather than a bar",
#: since re-running a fixed grader over a fixed ledger gives the same answer every
#: time. So `n_oos: null` here means "no sample size APPLIES", not "we failed to
#: read one"; the R4 power gate correctly declines to grade it.
N_FIELD = {"e35": None, "m20": "base_trades_OOS", "gld_compat": None}

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
                u = units.setdefault((stamp, leg), {"rows": 0, "n_oos": None})
                u["rows"] += 1
                if n_field:
                    v = row.get(n_field)
                    if isinstance(v, (int, float)):
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
        for (stamp, leg), meta in sorted(units.items()):
            st = state_for_unit((corpus, stamp, leg), present, seen, latest)
            counts[st] += 1
            out["units"].append({
                "corpus": corpus, "run_stamp": stamp, "leg": leg,
                "rows": meta["rows"], "n_oos": meta["n_oos"], "state": st,
            })
    out["summary"] = counts
    return out


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--report", action="store_true", help="grade every unit")
    ap.add_argument("--unread-only", action="store_true",
                    help="list only the finding state")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return _selftest()

    s = survey()
    if CORPUS_UNREADABLE in (s["ledger_state"], *s["corpora"].values()):
        print("::error::a store could not be READ - this is not 'nothing unread'",
              file=sys.stderr)
        print(json.dumps({"ledger": s["ledger_state"], "corpora": s["corpora"]}, indent=2))
        return 2

    print("state counts:", json.dumps(s["summary"]))
    rows = [u for u in s["units"] if not a.unread_only or u["state"] == UNREAD]
    for u in sorted(rows, key=lambda u: (u["state"], u["corpus"], u["run_stamp"])):
        n = "n/a" if u["n_oos"] is None else u["n_oos"]
        print(f"  {u['state']:<18} {u['corpus']:<4} {u['leg']:<26} "
              f"rows={u['rows']:<5} n_oos={n:<6} {u['run_stamp']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
