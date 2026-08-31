#!/usr/bin/env python3
# wiring: library — imported by scripts/research/dispatch_queue.py (the CLI) and
# by tests/test_research_queue.py. Deliberately has NO side effects: it reads
# queue files and returns verdicts. Firing anything is the dispatcher's job.
"""The research/testing job queue — schema, the power gate, and routing.

WHY THIS EXISTS
---------------
`docs/research/RESEARCH-WORKFLOW-ARCHITECTURE-2026-08-27.md` § 4 lays out R1–R5.
This module is R4 (pre-registration with a BLOCKING power gate) and the decision
half of R5 (the scheduler's routing). Operator-decided 2026-08-27: the queue is
**one file per job** under ``research/queue/``, and the dispatcher **fires** what
it routes.

**MEASURED, 2026-08-27, before building anything:** nothing in this repo
dispatches compute from a register. ``docs/claude/strategy-refinement-queue.json``
has exactly ONE non-doc reader (``scripts/ops/classify_strategy_tier.py``) and the
three review backlogs (983 / 109 / 104 rows) are read only by guards and
reporters. So this is new capability, not a rebuild — the check that
``RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED`` demands, run first rather than
asserted afterwards.

⚠️ **WHAT THIS IS NOT.** ``scripts/ml/_heavy_queue.py`` is NOT this. That is a
mutual-exclusion FLOCK that serialises heavy jobs on the 6 GB trainer; it holds
no list of pending work and routes nothing. The two compose: a job this module
routes to the trainer will take that lock when it runs. Confusing them would
produce a second lock, which is strictly worse than one.

ONE FILE PER JOB, AND WHY
-------------------------
The obvious alternative was a fifth big JSON beside the existing registers. It
was rejected on this repo's own measured history: ``scripts/ops/backlog_append.py``
exists *solely* because a naive append to a 983-row JSON reformats ~21k lines and
re-attributes every pre-existing row to the appending PR. One file per job has
none of that — a job is addressable by path, diffs in isolation, and is added or
retired by adding or deleting a file.

THE STATES, AND WHY NONE OF THEM MAY BE COLLAPSED
-------------------------------------------------
``docs/CLAUDE-RULES-CANONICAL.md`` § "Collapsed states": a field encoding a
condition must be able to say *"we did not look"* distinctly from *"we looked and
found nothing"*.

``power_state`` — may this job run at all?
  ``cleared``        the entry declares n, effect and basis, and the declared n
                     meets the floor for the declared effect.
  ``underpowered``   it declares them and the n does NOT meet the floor. BLOCKED.
                     Per R4 this converts to a data-acquisition task; it is not a
                     job that runs and reports a weak answer.
  ``undeclared``     the entry does not declare them. **BLOCKED, and emphatically
                     not "fine"** — this is the state that today's advisory
                     regime produces by default, and § R4 measured its cost: 329
                     ``honest_negative`` verdicts at a median OOS base of 33
                     trades, of which only 96 state a denominator at all.
  ``unverifiable``   declared, but the basis for ``expected_n`` is missing. We
                     cannot tell a derived number from a wish. BLOCKED — and
                     distinct from ``undeclared``, because the remedy differs
                     (write down the derivation vs design the experiment).

``route_state`` — where would it run?
  ``runner`` · ``trainer`` · ``gpu`` · ``unroutable``.
  ``unroutable`` is a REFUSAL, never a fallback to the runner. A job declaring
  both trainer-resident data and a GPU names no destination that exists, and
  guessing one would run it somewhere its own declaration says it cannot work.

THE POWER FLOOR IS A FLOOR, NOT A POWER ANALYSIS
------------------------------------------------
``required_n`` uses the NORMAL approximation at the declared alpha/power:

    one-sample : n >= (z_a2 + z_b)^2 / d^2
    two-sample : n >= 2 * (z_a2 + z_b)^2 / d^2   (per group)

⚠️ **Clearing it is NECESSARY, NOT SUFFICIENT, and the error is one-directional.**
It assumes iid draws. Backtest trade sequences are autocorrelated and walk-forward
folds overlap, so the TRUE required n is **higher** than this floor, never lower.
A job that clears this gate can still be underpowered; a job that fails it is
underpowered for certain. That asymmetry is why the gate is worth having even
though the model is crude — and why no caller may read ``cleared`` as "this
experiment is adequately powered".

It also says nothing about multiplicity. A sweep testing 40 cells needs its alpha
adjusted, and this module does not do that for you: declare the adjusted alpha in
the entry.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --- power_state ------------------------------------------------------------
CLEARED = "cleared"
UNDERPOWERED = "underpowered"
UNDECLARED = "undeclared"
UNVERIFIABLE = "unverifiable"
NOT_APPLICABLE = "not_applicable"
#: The declared n is CONTRADICTED BY OBSERVED DATA — the legs this job runs on
#: have never produced it and cannot be expected to. Distinct from
#: ``UNDERPOWERED``, and the distinction is the whole point of this state:
#: ``UNDERPOWERED`` means "you asked for too small an n given your own effect
#: size" (fix the design), while ``INFEASIBLE`` means "your n is fine on paper
#: and this leg cannot deliver it" (fix the SCOPE, or wait for data). Reporting
#: one as the other sends the author to rewrite the wrong half.
INFEASIBLE = "infeasible"
#: Declared data-acquisition: the author states UP FRONT that this run is not
#: expected to answer its question yet, and says what would change that. It RUNS
#: — that is the point, you cannot accrue data without running — but its output
#: must never be read as a test result.
#:
#: ⚠️ IT EXISTS SO THE GATE CAN BE TIGHTENED WITHOUT BLOCKING REAL WORK. Without
#: it an author with a legitimately thin leg has exactly two moves: declare an n
#: the leg cannot reach (which CLEARS, and produces a confident-looking verdict
#: over 5 trades), or be blocked from running at all. The first is what actually
#: happened — see the README's worked example. A state for "we know, and we are
#: running it anyway on purpose" is what makes the honest option available.
ACCRUING = "accruing"
POWER_STATES = (CLEARED, UNDERPOWERED, UNDECLARED, UNVERIFIABLE, NOT_APPLICABLE,
                INFEASIBLE, ACCRUING)

#: The power_states that may run.
#:
#: ⚠️ ``NOT_APPLICABLE`` IS NOT ``CLEARED`` AND MUST NEVER BE FOLDED INTO IT.
#: A deterministic job faced no power bar; a cleared one faced it and met it.
#: Collapsing them would let a reader tally "N jobs cleared the power gate" over
#: a population where some never took the test — the unstated-denominator error
#: (`diagnostic-provenance` sub-class C) applied to our own governance.
#: ⚠️ ``ACCRUING`` IS RUNNABLE AND IS NOT ``CLEARED`` EITHER. Same rule as
#: ``NOT_APPLICABLE`` one line up: three different things may run, for three
#: different reasons, and a tally of "N jobs cleared the power gate" that pools
#: them is counting over a population where some never took the test and some
#: took it and declared they would fail. Consumers must branch on the state, not
#: on ``runnable``.
RUNNABLE_POWER_STATES = (CLEARED, NOT_APPLICABLE, ACCRUING)

#: A job is inferential (estimates an effect from a sample → the R4 gate binds)
#: or deterministic (re-grades / rebuilds / extracts → no sample, no effect).
KIND_EXPERIMENT = "experiment"
KIND_DETERMINISTIC = "deterministic"
KINDS = (KIND_EXPERIMENT, KIND_DETERMINISTIC)

# --- route_state ------------------------------------------------------------
RUNNER = "runner"
TRAINER = "trainer"
GPU = "gpu"
UNROUTABLE = "unroutable"
ROUTE_STATES = (RUNNER, TRAINER, GPU, UNROUTABLE)

# --- dispatch outcome -------------------------------------------------------
DISPATCHED = "dispatched"
BLOCKED_POWER = "blocked_power"
BLOCKED_ROUTE = "blocked_route"
NOT_DUE = "not_due"
INVALID = "invalid"
DISPATCH_FAILED = "dispatch_failed"

#: A GitHub runner is 4 cores / 16 GB (measured, § 2 of the architecture doc).
#: A job declaring more memory than a runner has cannot go there, whatever else
#: it declares. The trainer is 1 OCPU / 6 GB, so it is not a fallback for a
#: memory-hungry job either — that is what makes such a job `unroutable`.
RUNNER_MEMORY_GB = 16.0
TRAINER_MEMORY_GB = 6.0

_ID_RE = re.compile(r"^RQ-\d{8}-[0-9]{3}$")
_CADENCES = ("once", "daily", "weekly", "monthly")
_LOG = logging.getLogger(__name__)

_DESIGNS = ("one_sample", "two_sample")
_STATUSES = ("queued", "running", "done", "blocked", "retired")

#: Two-sided normal quantiles. Only the pairs the gate actually offers are
#: tabulated — an unlisted alpha/power is a REFUSAL rather than an interpolation,
#: because silently interpolating a quantile would put a made-up number under a
#: gate whose whole purpose is to stop made-up numbers.
_Z_TWO_SIDED = {0.10: 1.6449, 0.05: 1.9600, 0.01: 2.5758}
_Z_POWER = {0.80: 0.8416, 0.90: 1.2816, 0.95: 1.6449}


@dataclass(frozen=True)
class PowerVerdict:
    state: str
    required_n: Optional[float]
    expected_n: Optional[float]
    effect_size_d: Optional[float]
    reason: str = ""

    @property
    def runnable(self) -> bool:
        return self.state in RUNNABLE_POWER_STATES


@dataclass(frozen=True)
class RouteVerdict:
    state: str
    reason: str = ""

    @property
    def runnable(self) -> bool:
        return self.state != UNROUTABLE


@dataclass
class QueueJob:
    path: Path
    raw: Dict[str, Any]
    errors: List[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return str(self.raw.get("id") or self.path.stem)

    @property
    def status(self) -> str:
        return str(self.raw.get("status") or "queued")

    @property
    def valid(self) -> bool:
        return not self.errors


def required_n(effect_size_d: float, *, design: str = "one_sample",
               alpha: float = 0.05, power: float = 0.80) -> float:
    """Normal-approximation sample-size FLOOR. See the module docstring's caveat.

    Raises ``KeyError`` on an untabulated alpha/power — deliberately, rather than
    interpolating a quantile nobody chose.
    """
    if effect_size_d <= 0:
        raise ValueError("effect_size_d must be > 0")
    if design not in _DESIGNS:
        raise ValueError(f"design must be one of {_DESIGNS}, got {design!r}")
    z_a2 = _Z_TWO_SIDED[round(float(alpha), 4)]
    z_b = _Z_POWER[round(float(power), 4)]
    base = (z_a2 + z_b) ** 2 / (effect_size_d ** 2)
    return base * 2.0 if design == "two_sample" else base



#: How a job may ground its ``expected_n``. ``corpus`` is checked against real
#: observed rows; ``none`` is the honest answer for a leg with no history and
#: REQUIRES the accrual declaration, so it cannot become a free pass.
FEASIBILITY_SOURCES = ("corpus", "none")

#: Which order statistic over the corpus's per-leg achieved n the author is
#: claiming. Declaring it is the point: "50 is achievable" is ambiguous until
#: you say whether you mean by EVERY leg or by SOME leg, and those differ by an
#: order of magnitude on a real fleet (measured on e35: min 4, max 50).
FEASIBILITY_STATISTICS = ("min_per_leg", "median_per_leg", "max_per_leg")


def observed_n_by_leg(corpus: str) -> Dict[str, float]:
    """Achieved per-leg sample sizes from a landed corpus, or ``{}``.

    Lazy import: the queue module is imported by the dispatcher on every tick
    and must not pull the disposition reader's file I/O into that path unless a
    job actually asks for a corpus-grounded check.
    """
    # ⚠️ LOAD BY PATH, NOT BY NAME. A bare `import research_disposition` only
    # resolves when this module was itself imported with scripts/research on
    # sys.path. The tests import it as `scripts.research.research_queue`, where
    # that name does NOT resolve — so the bare import raised ImportError, this
    # returned {}, and every corpus-grounded job graded `unverifiable` under
    # pytest while grading correctly from a shell. A silent empty read that
    # depends on the caller's import style is the worst kind: it passes locally
    # and is wrong in CI, in the direction of refusing valid jobs.
    import importlib.util  # noqa: PLC0415
    sibling = Path(__file__).resolve().parent / "research_disposition.py"
    spec = importlib.util.spec_from_file_location("_rq_research_disposition", sibling)
    if spec is None or spec.loader is None:  # pragma: no cover - file missing
        return {}
    _rd = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(_rd)
    except Exception:  # noqa: BLE001  # allow-silent: the empty is NOT collapsed — the caller reports `unverifiable / could not look`, which BLOCKS the job rather than admitting it, and the cause is logged with a stack trace below
        # ⚠️ FAIL CLOSED, AND SAY SO. Returning {} here does not admit anything:
        # `grade_power` renders an empty read as `unverifiable` with "we could
        # not look, which is NOT the same as the data refuting expected_n", so a
        # broken reader blocks jobs rather than waving them through. What must
        # NOT happen is the cause vanishing — hence the exception log. Narrowing
        # the type is wrong here: this executes an arbitrary sibling module and
        # any exception it raises is equally "we could not look".
        _LOG.exception("could not load research_disposition from %s", sibling)
        return {}
    if corpus not in _rd.CORPORA:
        return {}
    state, units = _rd.load_units(corpus)
    if state != "read":
        return {}
    out: Dict[str, float] = {}
    for (_stamp, leg), meta in units.items():
        n = meta.get("n_oos")
        if n is None:
            continue
        # Keep the BEST observation per leg: a leg that reached 50 once is
        # demonstrably able to, and an older thinner run does not un-demonstrate
        # it. The pessimism belongs in the STATISTIC the author declares, not
        # smuggled in here where nobody declared it.
        out[leg] = max(out.get(leg, 0.0), float(n))
    return out


def _feasibility_statistic(values: List[float], statistic: str) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if statistic == "min_per_leg":
        return ordered[0]
    if statistic == "max_per_leg":
        return ordered[-1]
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def grade_power(entry: Dict[str, Any]) -> PowerVerdict:
    """Grade one entry's ``power`` block into a :data:`POWER_STATES` verdict.

    ⚠️ **THE ``deterministic`` KIND IS AN EXEMPTION, NOT A BYPASS.** A job that
    re-grades an existing ledger or rebuilds a store estimates no effect from a
    sample, so an expected-n and an MDE would be theatre. But an exemption that
    costs nothing to claim is one every job eventually claims — which would gut
    R4 while leaving the gate visibly "in place", the worst of both. So the
    exemption REQUIRES ``why_not_inferential``: a written reason, checked
    non-empty. Declaring it is cheap; declaring it *falsely* is a sentence
    someone has to write and a reviewer can read.
    """
    kind = str(entry.get("kind") or KIND_EXPERIMENT).strip().lower()
    if kind == KIND_DETERMINISTIC:
        why = entry.get("why_not_inferential")
        if not (isinstance(why, str) and why.strip()):
            return PowerVerdict(UNDECLARED, None, None, None,
                                "kind=deterministic claims exemption from the power gate "
                                "but states no `why_not_inferential` — an exemption "
                                "nobody had to justify is a bypass")
        return PowerVerdict(NOT_APPLICABLE, None, None, None,
                            f"kind=deterministic: {why.strip()}")
    if kind not in KINDS:
        return PowerVerdict(UNVERIFIABLE, None, None, None,
                            f"kind={entry.get('kind')!r} is not one of {KINDS}")

    block = entry.get("power")
    if not isinstance(block, dict) or not block:
        return PowerVerdict(UNDECLARED, None, None, None,
                            "no `power:` block — R4 requires the question's "
                            "expected n and minimum detectable effect BEFORE it runs")

    expected_n = block.get("expected_n")
    mde = block.get("min_detectable_effect")
    sd = block.get("sd")
    basis = block.get("basis")

    missing = [k for k, v in (("expected_n", expected_n),
                              ("min_detectable_effect", mde)) if v is None]
    if missing:
        return PowerVerdict(UNDECLARED, None, _as_float(expected_n), None,
                            f"`power` is missing {', '.join(missing)}")

    # A number with no stated derivation is a wish. This is the `unverifiable`
    # state and it is deliberately NOT folded into `undeclared`: the remedy for
    # an undeclared experiment is to design it, the remedy here is to write down
    # where the n came from.
    if not (isinstance(basis, str) and basis.strip()):
        return PowerVerdict(UNVERIFIABLE, None, _as_float(expected_n), None,
                            "`power.basis` is empty — expected_n must state how it "
                            "was derived, or it cannot be told from a guess")

    # ------------------------------------------------------------------
    # FEASIBILITY — is the declared n grounded in DATA, or merely asserted?
    #
    # ⚠️ THIS EXISTS BECAUSE `basis` IS PROSE AND WAS PRESENCE-CHECKED ONLY.
    # The check above rejects an EMPTY basis with "a number with no stated
    # derivation is a wish" — but any non-empty string was accepted as the
    # derivation, with nothing asking whether it was true. That is the exact
    # `new-table-wiring-guard` lesson this repo already paid for: a guard that
    # is cheaper to lie to than to satisfy is worse than no guard.
    #
    # Measured consequence, and the reason this is not hypothetical:
    # RQ-20260830-002 declared expected_n=50 against a floor of 49.06 — it
    # CLEARED, legitimately, by 0.94 — while 5 of its 14 legs are 1d equity
    # legs trading ~20x/year that achieved 4, 5, 6, 7 and 8 OOS trades. That
    # was knowable in advance from the legs' own trade frequency. Nothing asked.
    #
    # ⚠️ IT IS NOT ENOUGH TO REQUIRE A CORPUS REFERENCE. A genuinely new
    # experiment on a new leg has no history to ground against, and demanding
    # one would make new research impossible — so `source: none` is a first
    # class answer, PROVIDED the author also declares the accrual (below).
    # The author must say WHICH situation they are in; the gate refuses to
    # guess.
    feasibility = block.get("feasibility")
    if not isinstance(feasibility, dict) or not feasibility:
        return PowerVerdict(UNVERIFIABLE, None, _as_float(expected_n), None,
                            "`power.feasibility` is missing — `basis` is prose and "
                            "cannot be checked, so expected_n must also name the "
                            "OBSERVED data that supports it (or declare "
                            "`source: none` with an accrual declaration)")

    n = _as_float(expected_n)
    effect = _as_float(mde)
    if n is None or effect is None or effect <= 0 or n <= 0:
        return PowerVerdict(UNVERIFIABLE, None, n, None,
                            "expected_n / min_detectable_effect are not positive numbers")

    # The effect may be declared as a standardised d, or in the metric's own
    # units alongside an sd. Requiring one or the other is what keeps a raw
    # effect from being silently read as a d — the semantic substitution that
    # `diagnostic-provenance-guard` exists to catch.
    # Echo the author's ORIGINAL spelling in any refusal. Reporting the
    # normalised form would show them a value they did not write, which is the
    # semantic substitution `diagnostic-provenance-guard` catches — small here,
    # and exactly how a reader loses a minute wondering what changed their input.
    declared_units = block.get("effect_units")
    units = str(declared_units or "sd").strip().lower()
    if units == "sd":
        d = effect
    else:
        sd_f = _as_float(sd)
        if sd_f is None or sd_f <= 0:
            return PowerVerdict(UNVERIFIABLE, None, n, None,
                                f"effect_units={declared_units!r} is in the metric's own "
                                "units, so `power.sd` is required to standardise it")
        d = effect / sd_f

    design = str(block.get("design") or "one_sample")
    alpha = float(block.get("alpha") or 0.05)
    power = float(block.get("power") or 0.80)
    try:
        need = required_n(d, design=design, alpha=alpha, power=power)
    except (KeyError, ValueError) as exc:
        return PowerVerdict(UNVERIFIABLE, None, n, d,
                            f"cannot compute the floor: {exc}")

    # --- ACCRUAL comes first, deliberately -----------------------------
    # An accruing job has ALREADY declared it cannot answer yet, so reporting
    # it UNDERPOWERED would restate its own declaration back at it and block
    # the run it explicitly asked for. The author has been honest; the gate's
    # job here is to record that, not to argue.
    source = str(feasibility.get("source") or "").strip().lower()
    if source not in FEASIBILITY_SOURCES:
        return PowerVerdict(UNVERIFIABLE, need, n, d,
                            f"`power.feasibility.source` must be one of "
                            f"{list(FEASIBILITY_SOURCES)}, got {feasibility.get('source')!r}")

    accrual_basis = feasibility.get("accrual_basis")
    declares_accrual = bool(str(accrual_basis or "").strip())
    if source == "none":
        # ⚠️ `source: none` WITHOUT an accrual basis is the free pass this whole
        # block exists to close: it would let any author opt out of grounding by
        # writing one word. The remedy is not to ban `none` — a new leg really
        # does have no history — but to make it cost a written justification of
        # what would make the question answerable later.
        if not declares_accrual:
            return PowerVerdict(UNVERIFIABLE, need, n, d,
                                "`feasibility.source: none` requires "
                                "`accrual_basis` — say why no observed data "
                                "supports expected_n and what would change that, "
                                "or the declaration is an opt-out rather than an "
                                "answer")
        return PowerVerdict(ACCRUING, need, n, d,
                            f"declared data-acquisition: n={n:g} against floor "
                            f"{need:.1f}; RUNS, and its output must not be read "
                            f"as a test result")

    # --- corpus-grounded: go and LOOK ----------------------------------
    corpus = str(feasibility.get("corpus") or "").strip()
    statistic = str(feasibility.get("statistic") or "").strip().lower()
    if statistic not in FEASIBILITY_STATISTICS:
        return PowerVerdict(UNVERIFIABLE, need, n, d,
                            f"`power.feasibility.statistic` must be one of "
                            f"{list(FEASIBILITY_STATISTICS)}, got "
                            f"{feasibility.get('statistic')!r} — '50 is achievable' "
                            f"is ambiguous until you say by EVERY leg or by SOME leg")
    observed = observed_n_by_leg(corpus)
    # ⚠️ SCOPE THE OBSERVATION TO THE LEGS THIS JOB ACTUALLY RUNS ON.
    # Without this the statistic is computed over EVERY leg in the corpus while
    # the claim being graded is about a subset — grading a per-leg claim against
    # a fleet-wide number, which is the semantic substitution
    # `diagnostic-provenance-guard` exists to catch. It is not academic: on e35
    # the fleet max is 50 (sol_4h) while `trend_donchian` has never exceeded 49,
    # so an unscoped `max_per_leg` would certify a single-leg job as feasible on
    # the strength of a DIFFERENT leg's data.
    if not observed:
        # ⚠️ THIS GUARD MUST PRECEDE THE PER-LEG CHECK BELOW, and it did not in
        # the first draft. With an empty read, EVERY declared leg lands in
        # `missing` and the refusal reads "this leg has never been measured" —
        # reporting a total read failure as a fact about the leg. That is the
        # could-not-look / looked-and-found-nothing collapse, written by the
        # same change that added a comment forbidding it one block down.
        return PowerVerdict(UNVERIFIABLE, need, n, d,
                            f"no achieved sample sizes readable from corpus "
                            f"{corpus!r} — we could not look, which is NOT the "
                            f"same as the data refuting expected_n")
    declared_legs = feasibility.get("legs")
    if declared_legs is not None:
        if not isinstance(declared_legs, list) or not declared_legs:
            return PowerVerdict(UNVERIFIABLE, need, n, d,
                                "`power.feasibility.legs` must be a non-empty list "
                                "when present")
        wanted = {str(x) for x in declared_legs}
        missing = sorted(wanted - set(observed))
        observed = {k: v for k, v in observed.items() if k in wanted}
        if missing:
            # Named a leg the corpus has never measured. That is "we could not
            # look" for that leg, not evidence against it — and silently
            # dropping it would compute the statistic over a smaller population
            # than the author declared, without saying so.
            return PowerVerdict(UNVERIFIABLE, need, n, d,
                                f"`feasibility.legs` names {len(missing)} leg(s) with "
                                f"no achieved n in {corpus}: {', '.join(missing[:5])} — "
                                f"we cannot ground expected_n on a leg we have never "
                                f"measured; use `source: none` with an accrual_basis "
                                f"for those")
    supported = _feasibility_statistic(list(observed.values()), statistic)
    if supported is not None and n > supported + 1e-9:
        short = sorted((leg, v) for leg, v in observed.items() if v < n)[:5]
        detail = ", ".join(f"{leg}={v:g}" for leg, v in short)
        return PowerVerdict(
            INFEASIBLE, need, n, d,
            f"expected_n={n:g} exceeds what {corpus} has ACTUALLY observed "
            f"({statistic}={supported:g} over {len(observed)} legs) — "
            f"{len(short)} short leg(s): {detail}. The design is not the problem; "
            f"the SCOPE is. Narrow to the legs that can deliver n, declare "
            f"`source: none` with an accrual_basis, or wait for data",
        )

    if n + 1e-9 < need:
        return PowerVerdict(
            UNDERPOWERED, need, n, d,
            f"declared n={n:g} is below the floor {need:.1f} for d={d:.4g} "
            f"({design}, alpha={alpha}, power={power}) — per R4 this converts to a "
            f"data-acquisition task rather than running and reporting a weak answer",
        )
    return PowerVerdict(CLEARED, need, n, d,
                        f"n={n:g} >= floor {need:.1f} (d={d:.4g}, {design}); "
                        "FLOOR ONLY — the iid assumption makes the true requirement higher")


def grade_route(entry: Dict[str, Any]) -> RouteVerdict:
    """Route one entry by its DECLARED requirements. Never guesses."""
    block = entry.get("routing")
    if not isinstance(block, dict) or not block:
        return RouteVerdict(UNROUTABLE, "no `routing:` block — the dispatcher does "
                                        "not infer where a job can run")

    needs_vm = bool(block.get("needs_trainer_resident_data"))
    needs_gpu = bool(block.get("needs_gpu"))
    mem = _as_float(block.get("peak_memory_gb"))

    if mem is None:
        return RouteVerdict(UNROUTABLE, "`routing.peak_memory_gb` is undeclared — the "
                                        "6 GB trainer was OOM-quarantined for 18.7 h in "
                                        "D-state by a job whose footprint nobody stated")

    if needs_gpu and needs_vm:
        return RouteVerdict(UNROUTABLE, "declares BOTH a GPU and trainer-resident data; "
                                        "no destination has both, and picking either "
                                        "would run it where its own declaration says it "
                                        "cannot work")
    if needs_gpu:
        return RouteVerdict(GPU, "declares needs_gpu")
    if needs_vm:
        if mem > TRAINER_MEMORY_GB:
            return RouteVerdict(UNROUTABLE,
                                f"needs trainer-resident data but declares {mem:g} GB > the "
                                f"trainer's {TRAINER_MEMORY_GB:g} GB — this is the OOM shape, "
                                "so it is refused rather than sent to be killed")
        return RouteVerdict(TRAINER, "needs trainer-resident data and fits in 6 GB")
    if mem > RUNNER_MEMORY_GB:
        return RouteVerdict(UNROUTABLE,
                            f"declares {mem:g} GB > a runner's {RUNNER_MEMORY_GB:g} GB and does "
                            "not need trainer residency; the trainer is smaller still, so no "
                            "destination fits")
    return RouteVerdict(RUNNER, f"no VM-resident state, no GPU, {mem:g} GB fits a runner")


def validate(entry: Dict[str, Any], *, path: Optional[Path] = None) -> List[str]:
    """Structural errors in one entry. An empty list means structurally valid.

    ⚠️ A malformed job is an ERROR, never a skip. A dispatcher that silently
    drops an unparseable entry reports "nothing to do" for a queue that has work
    in it — the `filter_state` collapse, one layer up.
    """
    errs: List[str] = []
    jid = entry.get("id")
    if not isinstance(jid, str) or not _ID_RE.match(jid):
        errs.append(f"id must match RQ-YYYYMMDD-NNN, got {jid!r}")
    elif path is not None and path.stem != jid:
        errs.append(f"filename {path.stem!r} must equal id {jid!r} — the path is the "
                    "job's address, so they cannot be allowed to disagree")
    for key in ("title", "question"):
        if not (isinstance(entry.get(key), str) and entry[key].strip()):
            errs.append(f"`{key}` is required and must be non-empty")
    cadence = entry.get("cadence")
    if cadence not in _CADENCES:
        errs.append(f"cadence must be one of {_CADENCES}, got {cadence!r}")
    status = entry.get("status")
    if status not in _STATUSES:
        errs.append(f"status must be one of {_STATUSES}, got {status!r}")
    run = entry.get("run")
    if not isinstance(run, dict) or not run.get("workflow"):
        errs.append("`run.workflow` is required — the dispatcher fires a workflow, "
                    "so a job that names none cannot be dispatched")
    lands = entry.get("lands")
    if not isinstance(lands, dict) or not lands.get("store"):
        errs.append("`lands.store` is required — R2: a run's deliverable is a LANDED "
                    "result, so a job must say where its rows go before it may run")
    return errs


def _as_float(value: Any) -> Optional[float]:
    """Coerce to float, or None. Returns None rather than raising or defaulting.

    A default here would be the fabrication this whole module exists to refuse:
    an unparseable ``expected_n`` silently becoming ``0.0`` would read as a
    declared-and-failing job, when the truth is that nobody declared one.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) or math.isinf(out) else out


def load_queue(queue_dir: Path) -> Tuple[List[QueueJob], Optional[str]]:
    """Load every ``*.yaml`` under *queue_dir*.

    Returns ``(jobs, read_error)``. ⚠️ **An empty list with ``read_error=None``
    means the queue is genuinely empty; an empty list with a ``read_error`` means
    WE COULD NOT LOOK.** They are returned as separate channels precisely so a
    caller cannot conflate them — a dispatcher reporting "0 jobs due" for a
    directory it failed to read is `silent-empty-guard`'s exact target.

    A file that fails to parse is NOT dropped: it comes back as a QueueJob with
    errors, so it is counted, reported, and visibly refused.
    """
    try:
        import yaml  # noqa: PLC0415 — optional at import time, required to load
    except ImportError as exc:  # pragma: no cover - environment-dependent
        # NARROW on purpose. A broad except here would report a real bug inside
        # pyyaml as "pyyaml unavailable" — a failure message naming a cause no
        # code path tested, which is the A-variant of unprovenanced diagnostic
        # output. Only an import failure may claim the module is missing.
        return [], f"pyyaml unavailable: {exc}"

    try:
        if not queue_dir.is_dir():
            return [], f"queue dir {queue_dir} does not exist"
        paths = sorted(queue_dir.glob("*.yaml"))
    except OSError as exc:
        return [], f"cannot list {queue_dir}: {exc}"

    jobs: List[QueueJob] = []
    for path in paths:
        try:
            raw = yaml.safe_load(path.read_text()) or {}
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            # The three things reading a job file can actually fail on: the file
            # (OSError), its bytes (UnicodeDecodeError), its syntax (YAMLError).
            # An earlier draft wrote `(OSError, Exception)`, which is just
            # `Exception` — it would have swallowed a genuine bug in this loader
            # and filed it as a malformed job, blaming the author for our defect.
            jobs.append(QueueJob(path=path, raw={}, errors=[f"unreadable: {exc}"]))
            continue
        if not isinstance(raw, dict):
            jobs.append(QueueJob(path=path, raw={}, errors=["top level is not a mapping"]))
            continue
        jobs.append(QueueJob(path=path, raw=raw, errors=validate(raw, path=path)))
    return jobs, None
