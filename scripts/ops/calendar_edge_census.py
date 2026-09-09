#!/usr/bin/env python3
# wiring: manual-only — a one-shot MEASUREMENT answering MI-218 ("how many
# operating-layer surfaces require calendar time to reach an EDGE verdict?").
# There is no cadence at which it should fire: the count it produces is a
# finding to be acted on once, not a condition to be re-graded hourly, and the
# standing enforcement of the rule it measures is check_soak_doctrine.py's
# check D, which IS wired into run_guards.py on every PR. Re-run it by hand
# when a gate surface is added or when check D is widened.
# ⚠️ It cannot run at all until #11529 lands: it IMPORTS check D's detector and
# refuses (exit 2) rather than reporting a count without it.
# Its invariants ARE wired — tests/test_calendar_edge_census.py pins the
# refusal path, the positive control and the widened predicate, because those
# do not move even though the tree it measures does.
"""Census: how many operating-layer surfaces require CALENDAR TIME to reach an EDGE verdict?

Answers the question MI-218 was opened to answer, with a stated classifier and a
stated population, per ``docs/CLAUDE-RULES-CANONICAL.md`` § "Always state the
population" and § "Promotion evidence — offline edge, live mechanics" clause 3
(*"No gate may require calendar-time accrual to prove edge"*).

WHY THIS IS NOT A SECOND DETECTOR
---------------------------------
MI-215 built the right instrument for the CODE half: ``check_soak_doctrine.py``
check D resolves variable aliases to a fixpoint and flags a branch whose *test*
reads a live/calendar-accrual quantity and whose *body* assigns a verdict. A
keyword matcher would have missed six of the seven branches it found. This
census therefore **imports that detector and points it at more surfaces** rather
than reimplementing it — the reuse is the design, not an optimisation.

What it adds is REACH, in three passes that answer three different questions:

  Pass A — CHECK-D REACH.  check D's algorithm, unchanged, run over EVERY
    function in every in-scope Python file instead of the two hand-listed in its
    ``GATE_SURFACES`` map.  That map is check D's own declared hole ("a gate
    absent from this map is UNREACHED"); enumerating functions closes it without
    editing the guard.

  Pass B — INSTRUMENT GAP.  The same algorithm with WIDENED constants
    (``ACCRUAL_SEEDS`` and the verdict attribute), to measure what check D's
    deliberately narrow vocabulary structurally cannot see today.  The constants
    are varied by patching, never by forking the algorithm, so a Pass-B hit is
    an argument for widening a constant in check D — NOT a competing count.

  Pass C — PROSE AND CONFIG.  ``OPEN-ITEMS.json`` ``clears_when`` texts, the
    three review backlogs' ``snoozed_until`` rows, and the review skills'
    rubrics.  check D parses Python; **it cannot reach these at all**, and no
    amount of pointing will make it.  This is the one place a different
    instrument is genuinely required, and it is a language classifier whose
    terms are declared below and whose precision is settled by reading each hit,
    never by the regex alone.

TWO QUESTIONS PER SURFACE, AND ONLY THE FIRST IS A FINDING
----------------------------------------------------------
  1. Does the surface decide **EDGE** (expectancy, win rate, PnL, promote/kill)
     or **MECHANICS** (did the order reach the venue, does the live execution
     match the simulator, did the alarm fire)?
  2. Does it require calendar time / live-outcome accrual to reach a verdict?

A MECHANICS gate that waits for two executions is the doctrine working. Only an
**EDGE** verdict blocked on accrual contradicts clause 3. The automated passes
below decide question 2 and produce a CANDIDATE population; question 1 is a
semantic judgement recorded per row in the census report. Both numbers are
reported, so recall and precision are separable.

THE POSITIVE CONTROL IS LOAD-BEARING, NOT A FORMALITY
------------------------------------------------------
``--census`` REFUSES to print a count unless Pass A independently rediscovers the
known M7 instance (``scripts/ml/strategy_review_packet.py::decide`` gating a
verdict on ``n_closed < MIN_CLOSED_FOR_ACTION``) **without being told the file or
the function name**. A silent probe is indistinguishable from a clean result —
which is exactly what check D's predecessor did for eight days while the rule it
enforces was being contradicted. Zero is only an acceptable answer with this
control shown.

STATES ARE NOT COLLAPSED
------------------------
``detector_state`` is ``available`` (check D's ``find_accrual_gated_verdicts``
imported) or ``absent`` (check D has not landed yet). On ``absent`` the Pass A
and Pass B counts are **None**, never ``0`` — *we could not look* is not *we
looked and found nothing*, and reporting 0 there would manufacture the clean
result this whole census exists to distrust.

Tier-1: reads only. It changes no gate, no threshold and no config.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# --------------------------------------------------------------------------
# The detector, imported. See the module docstring for why it is not forked.
# --------------------------------------------------------------------------
DETECTOR_AVAILABLE = False
_csd = None
try:  # pragma: no cover - exercised by both branches in the test suite
    import scripts.check_soak_doctrine as _csd  # type: ignore

    DETECTOR_AVAILABLE = hasattr(_csd, "find_accrual_gated_verdicts")
except Exception:  # noqa: BLE001 - an import failure is a state, not a crash
    DETECTOR_AVAILABLE = False

#: The known instance the probe must rediscover before any count is trusted.
POSITIVE_CONTROL = ("scripts/ml/strategy_review_packet.py", "decide")

#: Trees the census walks. The operating layer plus the engine it decides over.
CODE_SCOPE = ("scripts", "src", "ml")

#: Directories excluded from the code walk, with the reason each is excluded.
CODE_EXCLUDE = {
    "tests",           # a test asserting a gate's shape is not itself a gate
    "__pycache__",
    ".venv",
    "node_modules",
}

# --------------------------------------------------------------------------
# Pass B — the widened constants. Every name here denotes a quantity that only
# grows with WALL-CLOCK TIME or LIVE OUTCOMES, so a verdict control-dependent on
# one cannot be reached faster by computing harder. Purely additive to check D's
# own ACCRUAL_SEEDS, which are unioned in at run time.
# --------------------------------------------------------------------------
WIDE_ACCRUAL_SEEDS: Set[str] = {
    # live-outcome counts
    "n_closed", "n_trades", "n_fills", "n_live", "n_samples", "n_obs",
    "closed_count", "trade_count", "fill_count", "live_count", "sample_count",
    "num_trades", "num_closed", "n_episodes", "episodes", "n_rows", "row_count",
    # the floors those counts are compared against
    "MIN_CLOSED_FOR_ACTION", "MIN_TRADES", "MIN_N", "MIN_SAMPLES", "MIN_ROWS",
    "MIN_CLOSED", "MIN_EPISODES", "MIN_OBS", "min_trades", "min_n", "min_rows",
    "min_samples", "min_closed", "below_evidence_floor", "insufficient_n",
    # calendar quantities
    "shadow_soak_days", "soak_days", "window_days", "days_at_stage", "days_live",
    "age_days", "elapsed_days", "days_since", "calendar_days", "distinct_days",
    "n_days", "days", "weeks", "months", "MIN_DAYS", "min_days", "MIN_SOAK_DAYS",
    "check_every_days", "snoozed_until", "soak_until", "valid_until",
    # projections of WHEN accrual would suffice
    "observed_close_rate_per_day", "days_to_floor_point",
    "days_to_floor_optimistic", "days_to_floor_conservative",
}

#: Names a gate may record its verdict under. check D pins exactly one
#: (``action``); a gate recording its answer under any other name is invisible
#: to it today. Each is run as a separate pass and the hits unioned.
WIDE_VERDICT_ATTRS: Tuple[str, ...] = (
    "action", "verdict", "decision", "recommendation", "status", "state",
    "grade", "outcome", "disposition", "result", "promote", "readiness",
)

#: ⚠️ THE THIRD HOLE, and the one the census found by CONTROL rather than by
#: reading: check D's ``_assigns_verdict`` matches only an ``ast.Attribute``
#: target (``decision.action = ...``). A gate that writes a plain local
#: (``verdict = "hold"``), a dict slot (``row["action"] = ...``) or simply
#: ``return "hold"`` assigns a verdict just as surely and is invisible to it.
#: Verified with a synthetic control before this constant was written: the same
#: accrual-gated branch is FOUND with an attribute target and MISSED with a name
#: target. Pass B therefore patches the PREDICATE too — the alias fixpoint, which
#: is the hard and valuable part of the algorithm, is still check D's.
VERDICT_VALUES: Set[str] = {
    "hold", "kill", "promote", "demote", "demote_shadow", "tune", "retire",
    "advisory", "shadow", "candidate", "not_ready", "ready", "insufficient",
    "insufficient_basis", "no_action", "approve", "reject", "block",
}

# --------------------------------------------------------------------------
# Pass C — the prose/config classifier. Declared here so the terms can be
# argued with, and so a reader can see exactly what a hit and a miss mean.
# --------------------------------------------------------------------------

#: Does the text require WALL-CLOCK TIME or LIVE ACCRUAL to elapse?
ACCRUAL_LANG = [
    re.compile(r"\b\d+\s*(?:\+\s*)?(?:calendar\s+|trading\s+|distinct\s+(?:UTC\s+)?)?"
               r"(?:day|days|week|weeks|month|months)\b", re.I),
    re.compile(r"\bsoak(?:ing|s|ed)?\b", re.I),
    re.compile(r"\baccru(?:e|es|ed|ing|al)\b", re.I),
    re.compile(r"\bspanning at least\b", re.I),
    re.compile(r"\bat least \d+\s+(?:graded\s+|closed\s+|live\s+)?"
               r"(?:rows?|trades?|closes?|fills?|episodes?|samples?|observations?)\b", re.I),
    re.compile(r"\bover (?:a|the) (?:full |whole )?(?:window|sweep cycle|period)\b", re.I),
    re.compile(r"\bwait(?:s|ing|ed)? (?:for|on|until)\b", re.I),
    re.compile(r"\benough (?:closes|trades|data|history|samples)\b", re.I),
]

#: Is the thing being decided an EDGE claim?
EDGE_LANG = [
    re.compile(r"\bexpectanc(?:y|ies)\b|\bexpectancyR\b", re.I),
    re.compile(r"\bedge\b", re.I),
    re.compile(r"\bwin[- ]rate\b|\bwin rate\b", re.I),
    re.compile(r"\bprofitab(?:le|ility)\b|\bPnL\b|\bP&L\b", re.I),
    re.compile(r"\bpromot(?:e|ed|ion)\b|\bdemot(?:e|ed|ion)\b|\bkill\b|\bretire\b", re.I),
    re.compile(r"\bperformance\b|\btrack record\b|\bsharpe\b|\bdrawdown\b", re.I),
    re.compile(r"\bR[- ]multiple\b|\boos_edge\b|\bout[- ]of[- ]sample\b", re.I),
    re.compile(r"\bworks?\b(?=[^.\n]{0,40}\b(?:strategy|leg|model|signal)\b)", re.I),
]

#: Is the thing being decided MECHANICS? Present so a hit can be graded, not so
#: it can be silently dropped — a row matching BOTH is reported as ``both`` and
#: read by a human, because that ambiguity is itself worth seeing.
MECHANICS_LANG = [
    re.compile(r"\breach(?:ed|es)? the venue\b|\bplaced\b|\bexecut(?:ed|ion)\b", re.I),
    re.compile(r"\breconcil(?:e|ed|es|iation)\b|\bparity\b", re.I),
    re.compile(r"\bfir(?:e|ed|es)\b|\bdeliver(?:ed|y)\b|\barriv(?:e|ed)\b", re.I),
    re.compile(r"\bdeploy(?:ed|ment)?\b|\brestart(?:ed)?\b|\barm(?:ed|ing)?\b", re.I),
    re.compile(r"\bcron\b|\bschedule[d]?\b|\bwired\b|\brout(?:ed|ing)\b", re.I),
    re.compile(r"\bobserved\b|\bwrites?\b|\blogged\b|\bping\b", re.I),
]


@dataclass(frozen=True)
class CodeHit:
    """One branch whose verdict is control-dependent on accrual."""

    path: str
    func: str
    lineno: int
    evidence: str
    verdict_attr: str
    pass_name: str

    def key(self) -> Tuple[str, str, int]:
        return (self.path, self.func, self.lineno)


@dataclass
class ProseHit:
    source: str
    row_id: str
    field: str
    accrual_terms: List[str]
    edge_terms: List[str]
    mechanics_terms: List[str]

    @property
    def klass(self) -> str:
        """``edge`` / ``mechanics`` / ``both`` / ``unclassified``.

        ``both`` and ``unclassified`` are deliberately NOT folded into either
        side: a row the classifier cannot separate is a row a human must read,
        and silently bucketing it would be the collapsed state this repo keeps
        paying for.
        """
        if self.edge_terms and self.mechanics_terms:
            return "both"
        if self.edge_terms:
            return "edge"
        if self.mechanics_terms:
            return "mechanics"
        return "unclassified"


@dataclass
class Census:
    detector_state: str
    pass_a: Optional[List[CodeHit]] = None
    pass_b: Optional[List[CodeHit]] = None
    pass_c: List[ProseHit] = field(default_factory=list)
    files_walked: int = 0
    funcs_walked: int = 0
    parse_failures: List[str] = field(default_factory=list)
    control_found: Optional[bool] = None


# --------------------------------------------------------------------------
# Walking
# --------------------------------------------------------------------------

def _python_files() -> List[Path]:
    out: List[Path] = []
    for top in CODE_SCOPE:
        base = ROOT / top
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            if any(part in CODE_EXCLUDE for part in p.parts):
                continue
            out.append(p)
    return out


def _all_function_names(tree: ast.AST) -> Tuple[str, ...]:
    """Every function name in the module — check D's ``func_names`` argument.

    This is the whole of Pass A's added reach: check D asks its caller WHICH
    functions to inspect, and its shipped caller names two. Handing it every
    function turns a hand-maintained allowlist into a census.
    """
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return tuple(sorted(names))


def _run_detector(source: str, funcs: Tuple[str, ...]) -> List[Tuple[int, str, str]]:
    assert _csd is not None
    return _csd.find_accrual_gated_verdicts(source, funcs)


def _widened_assigns_verdict(body: List[ast.stmt]) -> bool:
    """check D's ``_assigns_verdict``, widened past the attribute-only target.

    Recognises, in addition to ``obj.<attr> = ...``:
      * ``<name> = ...`` where the name is a verdict name,
      * ``d["<attr>"] = ...`` (a dict-shaped verdict record),
      * ``return "<verdict literal>"`` — a gate that returns its answer instead
        of storing it is still a gate.
    """
    for stmt in body:
        for node in ast.walk(stmt):
            targets: List[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets = [node.target]
            elif isinstance(node, ast.Return):
                v = node.value
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    if v.value.strip().lower() in VERDICT_VALUES:
                        return True
                continue
            for t in targets:
                if isinstance(t, ast.Attribute) and t.attr in WIDE_VERDICT_ATTRS:
                    return True
                if isinstance(t, ast.Name) and t.id in WIDE_VERDICT_ATTRS:
                    return True
                if isinstance(t, ast.Subscript):
                    sl = t.slice
                    if isinstance(sl, ast.Constant) and sl.value in WIDE_VERDICT_ATTRS:
                        return True
    return False


def run_code_passes(census: Census) -> None:
    """Pass A (check D as-shipped) and Pass B (widened constants)."""
    if not DETECTOR_AVAILABLE:
        # Three states, never collapsed: absent means we could not look.
        census.pass_a = None
        census.pass_b = None
        return

    a: List[CodeHit] = []
    b: List[CodeHit] = []
    wide_seeds = set(getattr(_csd, "ACCRUAL_SEEDS", set())) | WIDE_ACCRUAL_SEEDS
    shipped_attr = getattr(_csd, "VERDICT_ATTR", "action")

    for path in _python_files():
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except Exception as exc:  # noqa: BLE001
            census.parse_failures.append(f"{path.relative_to(ROOT)}: {exc}")
            continue
        census.files_walked += 1
        funcs = _all_function_names(tree)
        census.funcs_walked += len(funcs)
        if not funcs:
            continue
        rel = str(path.relative_to(ROOT))

        for lineno, func, ev in _run_detector(source, funcs):
            a.append(CodeHit(rel, func, lineno, ev, shipped_attr, "A"))

        # Pass B: check D's algorithm, wider CONSTANTS and a wider verdict
        # PREDICATE. Both are patched, never forked -- the alias fixpoint stays
        # check D's, so a Pass-B hit is an argument for widening check D.
        with mock.patch.object(_csd, "ACCRUAL_SEEDS", wide_seeds), \
             mock.patch.object(_csd, "_assigns_verdict", _widened_assigns_verdict):
            try:
                hits = _run_detector(source, funcs)
            except Exception as exc:  # noqa: BLE001
                census.parse_failures.append(f"{rel} (pass B): {exc}")
                hits = []
        for lineno, func, ev in hits:
            b.append(CodeHit(rel, func, lineno, ev, "<widened>", "B"))

    census.pass_a = sorted(set(a), key=lambda h: h.key())
    a_keys = {h.key() for h in census.pass_a}
    # Pass B reports only what Pass A could NOT see, so the two never double-count.
    seen: Set[Tuple[str, str, int]] = set()
    b_only: List[CodeHit] = []
    for h in sorted(b, key=lambda h: h.key()):
        if h.key() in a_keys or h.key() in seen:
            continue
        seen.add(h.key())
        b_only.append(h)
    census.pass_b = b_only

    census.control_found = POSITIVE_CONTROL in {(h.path, h.func) for h in census.pass_a}


# --------------------------------------------------------------------------
# Pass C
# --------------------------------------------------------------------------

def _terms(text: str, patterns: Sequence[re.Pattern]) -> List[str]:
    found: List[str] = []
    for rx in patterns:
        m = rx.search(text)
        if m:
            found.append(m.group(0).strip())
    return found


def _classify_text(source: str, row_id: str, field_name: str, text: str) -> Optional[ProseHit]:
    accrual = _terms(text, ACCRUAL_LANG)
    if not accrual:
        return None
    return ProseHit(
        source=source,
        row_id=row_id,
        field=field_name,
        accrual_terms=accrual,
        edge_terms=_terms(text, EDGE_LANG),
        mechanics_terms=_terms(text, MECHANICS_LANG),
    )


PROSE_SOURCES: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    ("docs/claude/OPEN-ITEMS.json", "items", ("clears_when", "summary")),
    ("docs/claude/health-review-backlog.json", "items", ("snoozed_until", "clears_when")),
    ("docs/claude/performance-review-backlog.json", "items", ("snoozed_until", "clears_when")),
    ("docs/claude/ml-review-backlog.json", "items", ("snoozed_until", "clears_when")),
    ("docs/claude/research-review-backlog.json", "items", ("snoozed_until", "clears_when")),
)


def _rows(payload) -> List[dict]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("items", "rows", "backlog", "open_items"):
            if isinstance(payload.get(key), list):
                return [r for r in payload[key] if isinstance(r, dict)]
        # A dict of id -> row.
        return [v for v in payload.values() if isinstance(v, dict)]
    return []


def run_prose_pass(census: Census) -> None:
    for rel, _container, fields in PROSE_SOURCES:
        path = ROOT / rel
        if not path.exists():
            census.parse_failures.append(f"{rel}: absent (NOT the same as empty)")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            census.parse_failures.append(f"{rel}: {exc}")
            continue
        for row in _rows(payload):
            rid = str(row.get("id") or row.get("row_id") or row.get("key") or "<unnamed>")
            for fname in fields:
                val = row.get(fname)
                if not isinstance(val, str) or not val.strip():
                    continue
                hit = _classify_text(rel, rid, fname, val)
                if hit:
                    census.pass_c.append(hit)

    for skill in sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md")):
        try:
            text = skill.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            census.parse_failures.append(f"{skill}: {exc}")
            continue
        rel = str(skill.relative_to(ROOT))
        for para in re.split(r"\n\s*\n", text):
            hit = _classify_text(rel, skill.parent.name, "rubric", para)
            if hit and hit.klass in ("edge", "both"):
                census.pass_c.append(hit)
                break  # one representative hit per skill; the doc records detail


def run_census() -> Census:
    census = Census(detector_state="available" if DETECTOR_AVAILABLE else "absent")
    run_code_passes(census)
    run_prose_pass(census)
    return census


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def _fmt_count(v: Optional[int]) -> str:
    return "None (we could not look)" if v is None else str(v)


def report(census: Census, as_json: bool = False) -> int:
    a_n = None if census.pass_a is None else len(census.pass_a)
    b_n = None if census.pass_b is None else len(census.pass_b)

    if as_json:
        print(json.dumps({
            "detector_state": census.detector_state,
            "positive_control": {
                "target": f"{POSITIVE_CONTROL[0]}::{POSITIVE_CONTROL[1]}",
                "found": census.control_found,
            },
            "population": {
                "python_files_parsed": census.files_walked,
                "functions_inspected": census.funcs_walked,
                "parse_failures": len(census.parse_failures),
            },
            "pass_a_check_d_reach": a_n,
            "pass_b_instrument_gap": b_n,
            "pass_c_prose_candidates": len(census.pass_c),
            "pass_c_by_class": {
                k: sum(1 for h in census.pass_c if h.klass == k)
                for k in ("edge", "both", "mechanics", "unclassified")
            },
            "hits": {
                "A": [vars(h) for h in (census.pass_a or [])],
                "B": [vars(h) for h in (census.pass_b or [])],
                "C": [{**vars(h), "class": h.klass} for h in census.pass_c],
            },
        }, indent=2))
        return 0

    print("=" * 78)
    print("CALENDAR-TIME-TO-EDGE CENSUS")
    print("=" * 78)
    print(f"detector_state            : {census.detector_state}")
    print("  (check D imported from  : scripts/check_soak_doctrine.py)")
    print()
    print("POSITIVE CONTROL — the probe must rediscover the known M7 instance")
    print(f"  target : {POSITIVE_CONTROL[0]}::{POSITIVE_CONTROL[1]}")
    print(f"  found  : {census.control_found}")
    if census.control_found is not True:
        # The refusal states its own denominator on purpose: "we refuse" with no
        # scope reads like "we found nothing", which is the exact substitution
        # this whole census exists to distrust.
        print()
        print("  !! CONTROL NOT ESTABLISHED — the count is WITHHELD, not zero.")
        print(f"     scope searched : {census.files_walked} python file(s), "
              f"{census.funcs_walked} function(s)")
        print(f"     detector_state : {census.detector_state}")
        print(f"     A probe that misses M7 is broken; across those "
              f"{census.files_walked} file(s) a silent probe is "
              "indistinguishable from a clean result.")
        return 2
    print()
    print("POPULATION")
    print(f"  python files parsed     : {census.files_walked}  (scope: {', '.join(CODE_SCOPE)}/)")
    print(f"  functions inspected     : {census.funcs_walked}")
    print(f"  parse failures          : {len(census.parse_failures)}")
    print(f"  prose/config sources    : {len(PROSE_SOURCES)} registers + "
          f"{len(list((ROOT / '.claude' / 'skills').glob('*/SKILL.md')))} skills")
    print()
    print(f"PASS A — check D's reach, every function        : {_fmt_count(a_n)}")
    for h in (census.pass_a or []):
        print(f"    {h.path}:{h.lineno}  {h.func}()   [{h.evidence}]")
    print()
    print(f"PASS B — hits check D cannot see today         : {_fmt_count(b_n)}")
    for h in (census.pass_b or []):
        print(f"    {h.path}:{h.lineno}  {h.func}()   [{h.evidence}] -> .{h.verdict_attr}")
    print()
    print(f"PASS C — prose/config accrual candidates        : {len(census.pass_c)}")
    for k in ("edge", "both", "mechanics", "unclassified"):
        n = sum(1 for h in census.pass_c if h.klass == k)
        print(f"    {k:<14}: {n}")
    print()
    print("Pass C is a CANDIDATE population. 'edge' vs 'mechanics' is settled by")
    print("reading each row, not by the regex — see the census report doc.")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--class", dest="klass", default=None,
                    help="Pass C: print only rows of this class")
    args = ap.parse_args(argv)

    census = run_census()
    if args.klass:
        for h in census.pass_c:
            if h.klass == args.klass:
                print(f"{h.source} :: {h.row_id} :: {h.field}")
                print(f"    accrual : {h.accrual_terms}")
                print(f"    edge    : {h.edge_terms}")
                print(f"    mech    : {h.mechanics_terms}")
        return 0
    return report(census, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
