#!/usr/bin/env python3
# wiring: manual-only — a one-shot CENSUS of how each pre-live backtest harness
# obtains its entry decision, and of which live legs sit behind each answer. It
# reads the repo's own source and config; it has no scheduled caller and wants
# none, because its output is a finding to be read once, not a metric to trend.
"""MI-320 — which pre-live harnesses RUN the live entry decision, which
REIMPLEMENT it, and which only CLAIM to.

WHY THIS EXISTS
---------------
MI-319 adjudicated exactly one harness/unit pair (``fvg_range_15m`` against
``scripts/backtest_fvg_range.py``) and found the ENTRY a verbatim port while the
TARGET contract was wrong. Adjudication therefore is not a formality: it changed
the answer. The obvious next question is how many other pairs are in that state
— and the first thing measured here is that the population is not the one a
reader would assume, because **not every harness reimplements anything**.

WHAT IT REFUSES TO DO
---------------------
It does not re-run MI-319's behavioural probe, commission a sweep, or propose a
parameter. Every finding is FILED. ``config/`` is read and never written.

THE FOUR PARTS
--------------
A. PROVENANCE — how does this harness obtain an entry decision?
   ``imports_live_unit`` · ``reimplements`` · ``no_corresponding_unit`` ·
   ``could_not_classify``. Driven off the **AST**, never a grep: one harness
   (``backtest_system.py``) reaches ``order_package`` through
   ``importlib.import_module``, and a grep for a static import reports it as a
   reimplementation — the exact inversion this part exists to prevent.

B. CLAIM — does the LIVE UNIT assert it is a port of that harness?
   ⚠️ The load-bearing claim lives in the **unit**, not the harness, and it runs
   the other way round from what a reader expects: the unit claims to port the
   harness. The harness-side "Live-parity …" comments are a DIFFERENT and much
   narrower claim (a confidence formula, one gate) and are never pooled with it.
   The extractor ships with a POSITIVE CONTROL — it must find ``fvg_range_15m``,
   the one pair already adjudicated — because a claim census that silently finds
   nothing is indistinguishable from a repo that makes no claims.

C. EXPOSURE — how many legs, and how many ``execution: live``, resolve to each
   unit. Via ``pipeline.monitor_unit_for``, the repo's own resolver, so this
   cannot drift from what the order-monitor believes.

D. ADJUDICATION ATTEMPT — point MI-319's ``probe_structural`` at each
   reimplementing pair and report what comes back, **including its refusals**.

RESIDUALS: WHY SIX CLASSES AND NOT MI-319'S THREE
-------------------------------------------------
MI-319's classifier was fitted to one pair and knows two benign residuals
(harness loop bookkeeping, live argument validation). Pointed at a second pair
it therefore returns ``divergent`` for differences that are not divergences, and
that verdict is worse than no verdict because it is specific and confident. Three
further classes are needed, and each is **MEASURED, never assumed**:

  ``renamed_counterpart``     the two predicates have the SAME AST SHAPE once
                              every identifier is erased. Reported as a
                              CANDIDATE pairing with both texts and the implied
                              name map — never silently folded into "identical",
                              because equal shape is not equal meaning.
  ``implemented_upstream``    the gate exists live, in the BUILDER or intent
                              layer rather than the unit. Established by
                              searching ``src/`` OUTSIDE the units for the
                              identifier, WITH a positive control, because a
                              unit-scoped comparison otherwise reports a gate
                              the live system does run.
  ``harness_only_lever``      a research CLI lever whose argparse default is
                              off/None AND which no leg declares, so it is inert
                              in the shipped configuration. Both halves are
                              checked; either alone is not enough.

Anything left is ``unclassified_divergence`` — the finding.

⚠️ A CLASSIFIED RESIDUAL IS NOT A CLEARED ONE. Classification says *we know what
this difference is*, never *the two run the same trade*. Only the behavioural
probe can say that, and this instrument does not run one.

Usage
-----
    python3 scripts/research/mi320_harness_provenance.py --selftest
    python3 scripts/research/mi320_harness_provenance.py --run [--out PATH]
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) in sys.path:
    sys.path.remove(str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT))

HARNESS_DIR = _REPO_ROOT / "scripts"
UNITS_DIR = _REPO_ROOT / "src" / "units" / "strategies"
STRATEGIES_YAML = _REPO_ROOT / "config" / "strategies.yaml"

# ---------------------------------------------------------------------------
# Part A -- provenance states. Never collapsed.
# ---------------------------------------------------------------------------
A_IMPORTS = "imports_live_unit"        # the live decision function is CALLED
A_REIMPLEMENTS = "reimplements"        # a unit CLAIMS to port it; not called
A_UNCLAIMED = "corresponds_but_unclaimed"
#   A unit REFERENCES this harness (docstring or comment) but makes no port
#   claim about it. ⚠️ A SEPARATE STATE ON PURPOSE. Folding it into
#   `no_corresponding_unit` says *there is nothing to compare*, which is false
#   and reassuring: `backtest_pullback.py` is referenced by
#   `htf_pullback_trend_2h`, the second-largest unit in the fleet (19 legs, 16
#   `execution: live`), and what is absent is the CLAIM, not the correspondence.
A_NO_UNIT = "no_corresponding_unit"    # nothing to port FROM -- parity undefined
A_UNKNOWN = "could_not_classify"       # we looked and could not tell

DECISION_FN = "order_package"

# ---------------------------------------------------------------------------
# Part B -- the formulaic port claim, as the units actually write it.
# ``ports the validated entry logic from scripts/backtest_fvg_range.py``
# ``ports the validated entry/exit logic from ``scripts/backtest_trend.py```
# The backtick run is 0..n on purpose: fvg writes the path bare and trend wraps
# it in double backticks, and an earlier draft that required at least one
# backtick MISSED fvg -- i.e. it missed the one pair already adjudicated, which
# is why the positive control below is asserted rather than eyeballed.
# ---------------------------------------------------------------------------
CLAIM_RE = re.compile(
    r"ports?\s+the\s+validated\s+(entry(?:/exit)?)\s+logic\s+from\s+`*"
    r"(scripts/[A-Za-z0-9_./-]+\.py)`*",
    re.S,
)
CLAIM_POSITIVE_CONTROL = "fvg_range_15m"

# ---------------------------------------------------------------------------
# Part D -- residual classes beyond MI-319's three.
# ---------------------------------------------------------------------------
D_BOOKKEEPING = "harness_loop_bookkeeping"
D_VALIDATION = "live_input_validation"
D_RENAMED = "renamed_counterpart"
D_UPSTREAM = "implemented_upstream"
D_LEVER = "harness_only_lever"
D_UNCLASSIFIED = "unclassified_divergence"

# Identifiers whose live home is the BUILDER / intent layer rather than the unit,
# mapped from the name a harness happens to use to the CANONICAL name to search
# for. The alias half is load-bearing and was found by running this: the trend
# harness writes the side filter as a local `_sf`, which exists nowhere in
# `src/`, so searching for the alias returns `not_found` and the gate reads as a
# divergence -- when it is implemented live one layer up.
# NOT a hardcoded answer: every search is CHECKED by `implemented_upstream`,
# which must find a known positive AND miss a known negative before it reports.
UPSTREAM_CANDIDATE_NAMES = {"side_filter": "side_filter", "_sf": "side_filter"}


# ---------------------------------------------------------------------------
def _parse(path: Path) -> Optional[ast.AST]:
    try:
        return ast.parse(path.read_text())
    except (OSError, SyntaxError, ValueError):
        return None


def referencing_units(harness_stem: str,
                      units_dir: Path = UNITS_DIR) -> List[str]:
    """Units whose source MENTIONS this harness by path.

    A weaker signal than the formulaic port claim, deliberately: it establishes
    CORRESPONDENCE (these two are about the same strategy) without asserting
    PARITY (the live one reproduces the other). Keeping the two apart is what
    lets `corresponds_but_unclaimed` exist as its own answer.
    """
    needle = f"scripts/{harness_stem}.py"
    out = []
    for up in sorted(units_dir.glob("*.py")):
        if up.stem.startswith("_"):
            continue
        try:
            if needle in up.read_text():
                out.append(up.stem)
        except OSError:
            continue
    return out


def classify_provenance(harness_path: Path,
                        unit_exists: bool,
                        referenced_by: Optional[List[str]] = None,
                        ) -> Dict[str, Any]:
    """How does this harness obtain an entry decision?

    ⚠️ Three independent signals, because each alone is wrong somewhere:

      * a STATIC ``from src.units.strategies.X import order_package``
      * a DYNAMIC ``importlib.import_module`` (``backtest_system.py``), which a
        grep for the static form reports as a reimplementation
      * a LOCAL ``def order_package`` -- a reimplementation WEARING THE NAME,
        which would otherwise satisfy "the call exists" and invert the verdict

    The call site is required too: importing the symbol and never calling it is
    not running the live decision.
    """
    tree = _parse(harness_path)
    if tree is None:
        return {"state": A_UNKNOWN, "reason": "unparseable"}

    static: List[str] = []
    dynamic = False
    defines_locally = False
    calls_decision = False

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if "units.strategies" in node.module:
                for a in node.names:
                    if a.name == DECISION_FN:
                        static.append(node.module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == DECISION_FN:
                defines_locally = True
        if isinstance(node, ast.Call):
            fname = getattr(node.func, "id", None)
            attr = getattr(node.func, "attr", None)
            if fname == DECISION_FN:
                calls_decision = True
            if attr == "import_module" or fname == "import_module":
                dynamic = True
            # getattr(mod, "order_package")
            if (fname == "getattr" or attr == "getattr") and node.args:
                last = node.args[-1]
                if isinstance(last, ast.Constant) and last.value == DECISION_FN:
                    dynamic = True

    reaches = bool(static) or dynamic
    unclaimed = bool(referenced_by)

    def _no_claim_state() -> str:
        return A_UNCLAIMED if unclaimed else A_NO_UNIT

    if defines_locally and not static:
        # The harness has its own function of that name. Whatever it calls, it
        # is not demonstrably the live one -- refuse rather than credit it.
        state = A_REIMPLEMENTS if unit_exists else _no_claim_state()
    elif reaches and calls_decision:
        state = A_IMPORTS
    elif reaches and not calls_decision:
        state = A_UNKNOWN          # imported and never called: we cannot say
    elif unit_exists:
        state = A_REIMPLEMENTS
    else:
        state = _no_claim_state()
    return {
        "state": state,
        "static_import_modules": sorted(set(static)),
        "dynamic_import": dynamic,
        "defines_decision_locally": defines_locally,
        "calls_decision_fn": calls_decision,
        "corresponding_unit_exists": unit_exists,
        "referenced_by_units": sorted(referenced_by or []),
    }


def extract_port_claim(unit_path: Path) -> Dict[str, Any]:
    """Does this UNIT's module docstring claim to port a named harness?

    ⚠️ Scope is carried, never flattened: ``entry`` and ``entry/exit`` are
    different claims and only the first is what MI-319 adjudicated.
    """
    tree = _parse(unit_path)
    if tree is None:
        return {"claims": False, "reason": "unparseable"}
    doc = ast.get_docstring(tree) or ""
    flat = " ".join(doc.split())
    m = CLAIM_RE.search(flat)
    if not m:
        return {"claims": False}
    window = flat[m.start(): m.start() + 400]
    return {
        "claims": True,
        "scope": m.group(1),
        "harness": m.group(2),
        "says_verbatim": "VERBATIM" in window,
        "text": flat[max(0, m.start() - 40): m.start() + 240],
    }


# ---------------------------------------------------------------------------
# Part D helpers
# ---------------------------------------------------------------------------
class _Eraser(ast.NodeTransformer):
    """Replace every identifier with a single placeholder.

    Two predicates with the same erased shape are a RENAME CANDIDATE. They are
    not thereby equal: ``a > b`` and ``c > d`` share a shape and may mean
    opposite things, which is exactly why the result is reported as a candidate
    with both texts rather than folded into "identical".
    """

    def visit_Name(self, node: ast.Name) -> ast.AST:  # noqa: N802
        return ast.copy_location(ast.Name(id="_", ctx=node.ctx), node)

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        return ast.copy_location(ast.Attribute(value=node.value, attr="_",
                                               ctx=node.ctx), node)


def erased_shape(expr: str) -> Optional[str]:
    """The predicate's shape with identifiers erased, or None if unparseable."""
    try:
        tree = ast.parse(expr, mode="eval")
    except (SyntaxError, ValueError):
        return None
    try:
        return ast.dump(_Eraser().visit(tree), annotate_fields=False)
    except (RecursionError, AttributeError, TypeError):
        return None


def name_map(a: str, b: str) -> Optional[Dict[str, str]]:
    """Positional identifier correspondence between two same-shape predicates.

    Returns None when the shapes differ or when one name would have to map to
    two different names -- an INCONSISTENT map is not a rename, and reporting it
    as one is how a real difference gets excused.
    """
    if erased_shape(a) is None or erased_shape(a) != erased_shape(b):
        return None
    def _ordered_names(expr: str) -> List[str]:
        return [n.id for n in ast.walk(ast.parse(expr, mode="eval"))
                if isinstance(n, ast.Name)]

    ia, ib = _ordered_names(a), _ordered_names(b)
    if len(ia) != len(ib):
        return None
    out: Dict[str, str] = {}
    for x, y in zip(ia, ib):
        if x in out and out[x] != y:
            return None
        out[x] = y
    return out


def _names_in(expr: str) -> List[str]:
    try:
        tree = ast.parse(expr, mode="eval")
    except (SyntaxError, ValueError):
        return []
    return sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name)})


def implemented_upstream(identifier: str,
                         root: Path = _REPO_ROOT) -> Dict[str, Any]:
    """Is this gate implemented live, OUTSIDE the units layer?

    ⚠️ The search ships with BOTH controls, because a bare grep hit proves
    nothing on its own:

      positive -- a name known to live upstream must be found (``side_filter``,
                  whose owner is ``strategy_signal_builders._resolve_side_filter``)
      negative -- a name that exists nowhere must NOT be found

    A search that cannot demonstrate it finds a known positive is not evidence
    of absence, and this repo has a rule about exactly that.
    """
    src = root / "src"
    if not src.is_dir():
        return {"state": "could_not_look", "reason": "src/ absent"}

    def _hits(name: str) -> List[str]:
        out: List[str] = []
        for p in src.rglob("*.py"):
            rel = p.relative_to(root).as_posix()
            if rel.startswith("src/units/strategies/"):
                continue
            try:
                if re.search(rf"\b{re.escape(name)}\b", p.read_text()):
                    out.append(rel)
            except OSError:
                continue
        return sorted(out)

    pos = _hits("side_filter")
    neg = _hits("__mi320_name_that_cannot_exist__")
    if not pos or neg:
        return {"state": "could_not_look",
                "reason": "controls failed",
                "positive_control_hits": len(pos),
                "negative_control_hits": len(neg)}
    found = _hits(identifier)
    return {
        "state": "found" if found else "not_found",
        "files": found[:6],
        "n_files": len(found),
        "controls_ok": True,
    }


def harness_only_lever(identifier: str, harness_path: Path,
                       unit_legs: Optional[Dict[str, Dict[str, Any]]] = None,
                       strategies_yaml: Path = STRATEGIES_YAML) -> Dict[str, Any]:
    """Is this a research CLI lever that is OFF in the shipped configuration?

    BOTH halves are required and neither is sufficient:
      1. the harness's own argparse default is off/None/0, and
      2. no leg **of this unit** declares it.

    A lever whose default is off but which a leg turns on is live. A lever no
    leg declares but whose default is ON is live for every run. Only the
    conjunction makes the harness gate inert in the shipped configuration.

    ⚠️ CLAUSE 2 IS SCOPED PER UNIT, AND A FLEET-WIDE VERSION OVERSTATES
    DIVERGENCE. Measured: ``adx_min`` is declared by 6 of the 19
    ``htf_pullback_trend_2h`` legs and by **0 of the 23 ``trend_donchian``
    legs**, while the trend UNIT implements no ADX band at all (``adx_min`` and
    ``adx_max`` appear 0 times in it). A fleet-wide "is it declared anywhere"
    test finds the pullback legs, grades the trend harness's ADX gate live, and
    reports a divergence on a lever that is inert for every leg that unit has.
    When ``unit_legs`` is None the check falls back to the fleet-wide read and
    SAYS SO in ``declaration_scope``, because an unscoped answer is a weaker
    answer and must not read like the scoped one.
    """
    tree = _parse(harness_path)
    if tree is None:
        return {"state": "could_not_look", "reason": "harness unparseable"}
    flag = "--" + identifier.replace("_", "-")
    default: Any = "__absent__"
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", None) != "add_argument":
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        if node.args[0].value != flag:
            continue
        for kw in node.keywords:
            if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                default = kw.value.value
    if default == "__absent__":
        return {"state": "no_such_flag", "flag": flag}
    default_off = default in (None, "off", "", 0, 0.0, False)
    if unit_legs is not None:
        scope = "unit_legs"
        decl_legs = [n for n, cfgrow in unit_legs.items()
                     if cfgrow.get(identifier) not in (None, "", 0, 0.0, False)]
        declared = bool(decl_legs)
    else:
        scope = "fleet_wide_fallback"
        decl_legs = []
        try:
            declared = bool(re.search(rf"^\s*{re.escape(identifier)}\s*:",
                                      strategies_yaml.read_text(), re.M))
        except OSError:
            return {"state": "could_not_look",
                    "reason": "strategies.yaml unreadable"}
    return {
        "state": "inert_in_shipped_config" if (default_off and not declared)
                 else "live_or_declared",
        "flag": flag,
        "argparse_default": default,
        "default_is_off": default_off,
        "declared_by_a_leg": declared,
        "declaration_scope": scope,
        "declaring_legs": sorted(decl_legs)[:6],
        "n_unit_legs": len(unit_legs) if unit_legs is not None else None,
    }


def classify_residual(disjunct: str, side: str, harness_path: Path,
                      counterpart_pool: List[str],
                      unit_legs: Optional[Dict[str, Dict[str, Any]]] = None,
                      ) -> Dict[str, Any]:
    """Classify one residual disjunct into the six classes.

    ORDER MATTERS AND IS DELIBERATE: the two cheap, already-known classes are
    tested first, then the rename candidate, then the two that cost a search.
    ``unclassified_divergence`` is the fall-through, so a class that cannot be
    established never silently becomes a benign one.
    """
    if "next_idx" in disjunct and side == "harness":
        return {"class": D_BOOKKEEPING}
    if side == "live" and any(t in disjunct for t in
                              ("candles_df", "missing_cols", "needed")):
        return {"class": D_VALIDATION}

    for other in counterpart_pool:
        nm = name_map(disjunct, other)
        if nm is not None and any(k != v for k, v in nm.items()):
            return {"class": D_RENAMED, "counterpart": other, "name_map": nm}

    names = _names_in(disjunct)
    for alias, canonical in UPSTREAM_CANDIDATE_NAMES.items():
        if alias in names:
            up = implemented_upstream(canonical)
            if up.get("state") == "found":
                return {"class": D_UPSTREAM, "identifier": canonical,
                        "written_in_harness_as": alias, "evidence": up}

    if side == "harness":
        for n in names:
            lev = harness_only_lever(n, harness_path, unit_legs)
            if lev.get("state") == "inert_in_shipped_config":
                return {"class": D_LEVER, "identifier": n, "evidence": lev}

    # ⚠️ A HINT, NEVER A CLASS. A residual that shares an identifier stem with
    # something on the other side is worth a human's attention, and that is ALL
    # this says: `float(vp) > vol_skip_above_pctl` and `vol_pctl > vol_above`
    # plainly concern the same lever and differ in shape, so no rename map
    # exists and none is invented. Promoting a name overlap to a classification
    # would launder exactly the differences this instrument is for.
    stems = {n.split("_")[0] for n in names if len(n.split("_")[0]) > 2}
    hints = [o for o in counterpart_pool
             if stems & {n.split("_")[0] for n in _names_in(o)
                         if len(n.split("_")[0]) > 2}]
    out: Dict[str, Any] = {"class": D_UNCLASSIFIED}
    if hints:
        out["possible_counterpart_by_name"] = hints[:3]
        out["hint_note"] = ("shares an identifier stem with the other side; a "
                            "HINT for a reader, never evidence of equivalence")
    return out


# ---------------------------------------------------------------------------
def _leg_exposure() -> Dict[str, Any]:
    """Legs and ``execution: live`` counts per unit, via the repo's OWN resolver."""
    try:
        import yaml  # noqa: PLC0415
        from src.runtime.pipeline import monitor_unit_for  # noqa: PLC0415
    except ImportError as exc:
        return {"state": "could_not_look", "reason": f"import: {exc}"}
    try:
        cfg = yaml.safe_load(STRATEGIES_YAML.read_text())
    except (OSError, ValueError) as exc:
        return {"state": "could_not_look", "reason": f"yaml: {exc}"}
    strat = cfg.get("strategies", cfg)
    per: Dict[str, Dict[str, int]] = {}
    legs_by_unit: Dict[str, Dict[str, Dict[str, Any]]] = {}
    unresolved = 0
    for name, s in strat.items():
        if not isinstance(s, dict):
            continue
        try:
            unit = monitor_unit_for(name)
        except (ImportError, AttributeError, KeyError, TypeError,
                ValueError, OSError):
            # NARROWED rather than annotated, on MI-319's lesson: a broad catch
            # would grade a genuine defect in this instrument as `unresolved`,
            # which is the register's *we could not look* value -- and a defect
            # that renders as a refusal is one nobody goes looking for. A
            # resolver raising anything else is a finding and must propagate.
            unresolved += 1
            continue
        row = per.setdefault(unit, {"legs": 0, "live": 0})
        legs_by_unit.setdefault(unit, {})[name] = s
        row["legs"] += 1
        if s.get("execution", "live") == "live":
            row["live"] += 1
    return {"state": "measured", "per_unit": per,
            "total_legs": sum(r["legs"] for r in per.values()),
            "total_live": sum(r["live"] for r in per.values()),
            "unresolved_legs": unresolved,
            "_legs_by_unit": legs_by_unit}


def _load_mi319():
    """Import MI-319's structural probe rather than reimplementing it.

    A second copy of "what counts as a rejection predicate" is how the two
    would drift, which is the defect class this whole unit is about.
    """
    import importlib.util  # noqa: PLC0415
    path = _REPO_ROOT / "scripts" / "research" / "mi319_fvg_parity.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("mi319_fvg_parity", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
def _selftest() -> int:
    ok = fail = 0

    def check(label: str, cond: bool) -> None:
        nonlocal ok, fail
        if cond:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    # --- Part A -------------------------------------------------------------
    real = classify_provenance(HARNESS_DIR / "backtest_ict_scalp.py", True, [])
    check("A: ict_scalp harness imports the live unit",
          real["state"] == A_IMPORTS and real["static_import_modules"])
    dyn = classify_provenance(HARNESS_DIR / "backtest_system.py", True, [])
    check("A: backtest_system's DYNAMIC import is caught (a grep misses it)",
          dyn["state"] == A_IMPORTS and dyn["dynamic_import"] is True)
    reimpl = classify_provenance(HARNESS_DIR / "backtest_fvg_range.py", True, [])
    check("A: fvg harness reimplements", reimpl["state"] == A_REIMPLEMENTS)
    check("A: no corresponding unit is its OWN state, not 'reimplements'",
          classify_provenance(HARNESS_DIR / "backtest_fvg_range.py",
                              False, [])["state"] == A_NO_UNIT)
    # ⚠️ The distinction that keeps 16 live legs visible: a harness a unit
    # REFERENCES but does not CLAIM must not read as "nothing to compare".
    pb = classify_provenance(HARNESS_DIR / "backtest_pullback.py", False,
                             referencing_units("backtest_pullback"))
    check("A: a referenced-but-unclaimed harness gets its OWN state",
          pb["state"] == A_UNCLAIMED
          and "htf_pullback_trend_2h" in pb["referenced_by_units"])
    check("A(neg): a harness no unit mentions stays no_corresponding_unit",
          classify_provenance(HARNESS_DIR / "backtest_orb.py", False,
                              referencing_units("backtest_orb"))["state"]
          == A_NO_UNIT)
    check("A: referencing_units finds the claimed pair too (control)",
          "fvg_range_15m" in referencing_units("backtest_fvg_range"))
    check("A: an unparseable harness is could_not_classify, never a verdict",
          classify_provenance(HARNESS_DIR / "__does_not_exist__.py",
                              True, [])["state"] == A_UNKNOWN)

    # NEGATIVE CONTROL: a harness defining its own order_package must NOT read
    # as running the live one.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "fake.py"
        p.write_text("def order_package(cfg, candles_df=None):\n    return {}\n"
                     "x = order_package({})\n")
        check("A(neg): a LOCAL def order_package is a reimplementation",
              classify_provenance(p, True, [])["state"] == A_REIMPLEMENTS)
        q = Path(td) / "imported_never_called.py"
        q.write_text("from src.units.strategies.ict_scalp import order_package\n")
        check("A(neg): imported and never called is could_not_classify",
              classify_provenance(q, True, [])["state"] == A_UNKNOWN)

    # --- Part B -------------------------------------------------------------
    pc = extract_port_claim(UNITS_DIR / f"{CLAIM_POSITIVE_CONTROL}.py")
    check("B: POSITIVE CONTROL -- the extractor finds the adjudicated pair",
          pc.get("claims") is True)
    check("B: fvg's claim is scoped to ENTRY and says VERBATIM",
          pc.get("scope") == "entry" and pc.get("says_verbatim") is True)
    tc = extract_port_claim(UNITS_DIR / "trend_donchian.py")
    check("B: trend_donchian claims entry/exit",
          tc.get("claims") is True and tc.get("scope") == "entry/exit")
    check("B(neg): a unit with no claim reports claims=False",
          extract_port_claim(UNITS_DIR / "squeeze_breakout_4h.py")
          .get("claims") is False)
    check("B(neg): an absent file never fabricates a claim",
          extract_port_claim(UNITS_DIR / "__nope__.py").get("claims") is False)

    # --- Part D: shape + rename --------------------------------------------
    check("D: identical predicates share a shape",
          erased_shape("a > b") == erased_shape("a > b"))
    check("D: a rename preserves the shape",
          erased_shape("adx_val > adx_max") == erased_shape("adx_val > adx_max_p"))
    check("D: a different OPERATOR does not",
          erased_shape("a > b") != erased_shape("a < b"))
    check("D: a different ARITY does not",
          erased_shape("a > b") != erased_shape("a > b and c"))
    check("D: an unparseable predicate yields None, never a shape",
          erased_shape("a >") is None)
    nm = name_map("adx_max is not None and adx_val > adx_max",
                  "adx_max_p is not None and adx_val > adx_max_p")
    check("D: the name map is recovered",
          nm == {"adx_max": "adx_max_p", "adx_val": "adx_val"})
    check("D(neg): an INCONSISTENT map is refused, never reported as a rename",
          name_map("a > a", "b > c") is None)
    check("D(neg): different shapes have no map",
          name_map("a > b", "a < b") is None)
    check("D(neg): a constant difference is not a rename",
          name_map("a > 1", "a > 2") is None)

    # --- Part D: residual classification ------------------------------------
    hp = HARNESS_DIR / "backtest_pullback.py"
    check("D: harness loop bookkeeping is classified",
          classify_residual("i < next_idx", "harness", hp, [])["class"]
          == D_BOOKKEEPING)
    check("D(neg): the SAME text on the LIVE side is not bookkeeping",
          classify_residual("i < next_idx", "live", hp, [])["class"]
          != D_BOOKKEEPING)
    check("D: live argument validation is classified",
          classify_residual("len(candles_df) < needed", "live", hp, [])["class"]
          == D_VALIDATION)
    r = classify_residual("adx_max is not None and adx_val > adx_max", "harness",
                          hp, ["adx_max_p is not None and adx_val > adx_max_p"])
    check("D: a renamed counterpart is classified and carries its map",
          r["class"] == D_RENAMED and r["name_map"]["adx_max"] == "adx_max_p")
    up = classify_residual("side_filter == 'long' and direction == 'short'",
                           "harness", hp, [])
    check("D: a gate implemented in the BUILDER is classified upstream",
          up["class"] == D_UPSTREAM and up["evidence"]["controls_ok"] is True)
    # The trend harness writes the same gate as a local `_sf`, which exists
    # NOWHERE in src/. Without the alias map it reads as a divergence.
    alias = classify_residual("_sf == 'long' and direction == 'short'",
                              "harness", hp, [])
    check("D: the harness-local ALIAS of an upstream gate resolves too",
          alias["class"] == D_UPSTREAM
          and alias["identifier"] == "side_filter"
          and alias["written_in_harness_as"] == "_sf")
    hinted = classify_residual("float(vp) > vol_skip_above_pctl", "harness", hp,
                               ["vol_pctl > vol_above"])
    check("D(neg): a name-overlap HINT never becomes a classification",
          hinted["class"] == D_UNCLASSIFIED
          and "possible_counterpart_by_name" in hinted)
    check("D(neg): an unmatched residual falls through to UNCLASSIFIED",
          classify_residual("wholly_unknown_thing > 3", "harness", hp, [])["class"]
          == D_UNCLASSIFIED)

    # --- upstream + lever controls -----------------------------------------
    check("upstream: the positive control resolves",
          implemented_upstream("side_filter")["state"] == "found")
    check("upstream(neg): a name that exists nowhere is not_found",
          implemented_upstream("__mi320_absent__")["state"] == "not_found")
    lev = harness_only_lever("direction_filter", hp)
    check("lever: --direction-filter defaults off and no leg declares it",
          lev["state"] == "inert_in_shipped_config"
          and lev["default_is_off"] is True
          and lev["declared_by_a_leg"] is False)
    check("lever(neg): a flag the harness does not define is no_such_flag",
          harness_only_lever("__not_a_flag__", hp)["state"] == "no_such_flag")
    # ⚠️ THE SCOPING CORRECTION, asserted rather than trusted: a lever declared
    # by ANOTHER unit's legs must not make this unit's harness gate read live.
    th = HARNESS_DIR / "backtest_trend.py"
    fleet = harness_only_lever("adx_min", th)
    scoped = harness_only_lever("adx_min", th, {"a": {}, "b": {"tp_r": 3}})
    check("lever: fleet-wide scope says DECLARED (another unit's legs)",
          fleet["declared_by_a_leg"] is True
          and fleet["declaration_scope"] == "fleet_wide_fallback")
    check("lever: per-unit scope says INERT, and that is the right answer",
          scoped["state"] == "inert_in_shipped_config"
          and scoped["declaration_scope"] == "unit_legs")
    check("lever: a leg of THIS unit declaring it keeps the lever live",
          harness_only_lever("adx_min", th,
                             {"a": {"adx_min": 20}})["state"]
          == "live_or_declared")

    # --- Part C -------------------------------------------------------------
    exp = _leg_exposure()
    check("C: exposure is measured and every leg resolves",
          exp["state"] == "measured" and exp["unresolved_legs"] == 0)
    check("C: live legs are a strict subset of all legs",
          0 < exp["total_live"] <= exp["total_legs"])

    print(f"mi320 selftest: {ok}/{ok + fail} passed")
    return 0 if fail == 0 else 1


# ---------------------------------------------------------------------------
def _run(out_path: Path) -> int:
    mi319 = _load_mi319()
    unit_stems = {p.stem for p in UNITS_DIR.glob("*.py")
                  if not p.stem.startswith("_")}

    # --- A + B --------------------------------------------------------------
    harnesses: Dict[str, Any] = {}
    for hp in sorted(HARNESS_DIR.glob("backtest_*.py")):
        # A harness "corresponds" to a unit when some unit NAMES it in a port
        # claim, or when the harness imports one. Never by filename similarity:
        # backtest_trend -> trend_donchian is not a name match.
        harnesses[hp.stem] = {"path": hp.relative_to(_REPO_ROOT).as_posix()}

    claims: Dict[str, Any] = {}
    for up in sorted(UNITS_DIR.glob("*.py")):
        if up.stem.startswith("_"):
            continue
        c = extract_port_claim(up)
        if c.get("claims"):
            claims[up.stem] = c
    claim_control_ok = CLAIM_POSITIVE_CONTROL in claims

    claimed_harness = {Path(c["harness"]).stem: u for u, c in claims.items()}
    for stem, row in harnesses.items():
        row.update(classify_provenance(HARNESS_DIR / f"{stem}.py",
                                       stem in claimed_harness,
                                       referencing_units(stem)))
        row["claimed_by_unit"] = claimed_harness.get(stem)

    # --- C ------------------------------------------------------------------
    exposure = _leg_exposure()

    # --- D ------------------------------------------------------------------
    adjudications: Dict[str, Any] = {}
    if mi319 is None:
        adjudications["state"] = "could_not_look"
        adjudications["reason"] = "mi319_fvg_parity.py not importable"
    else:
        for unit, c in sorted(claims.items()):
            hstem = Path(c["harness"]).stem
            hp = HARNESS_DIR / f"{hstem}.py"
            res = mi319.probe_structural(hp, UNITS_DIR / f"{unit}.py")
            hpool = res.get("harness_only_disjuncts", []) or []
            lpool = res.get("live_only_disjuncts", []) or []
            legs = (exposure.get("_legs_by_unit") or {}).get(unit)
            classified = []
            for d in hpool:
                classified.append({"side": "harness", "disjunct": d,
                                   **classify_residual(d, "harness", hp, lpool,
                                                       legs)})
            for d in lpool:
                classified.append({"side": "live", "disjunct": d,
                                   **classify_residual(d, "live", hp, hpool,
                                                       legs)})
            unresolved = [c2 for c2 in classified
                          if c2["class"] == D_UNCLASSIFIED]
            adjudications[unit] = {
                "harness": c["harness"],
                "claim_scope": c["scope"],
                "mi319_state": res.get("state"),
                "gate_counts": res.get("gate_counts"),
                "shared_disjunct_count": res.get("shared_disjunct_count"),
                "residuals": classified,
                "unclassified_count": len(unresolved),
                "verdict_note": (
                    "A CLASSIFIED residual is not a CLEARED one: this says what "
                    "the difference IS, never that the two would place the same "
                    "trade. Only a behavioural probe says that, and this "
                    "instrument does not run one."),
            }

    art = {
        "unit": "MI-320",
        "question": ("Which pre-live harnesses RUN the live entry decision, "
                     "which REIMPLEMENT it, and which only CLAIM to?"),
        "generated_at_utc": subprocess.run(
            ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
            capture_output=True, text=True, check=False).stdout.strip(),
        "populations": {
            "harnesses_scanned": len(harnesses),
            "unit_modules_scanned": len(unit_stems),
            "legs": exposure.get("total_legs"),
            "legs_execution_live": exposure.get("total_live"),
        },
        "a_provenance": harnesses,
        "b_claims": {"claims": claims,
                     "positive_control_unit": CLAIM_POSITIVE_CONTROL,
                     "positive_control_found": claim_control_ok},
        "c_exposure": {k: v for k, v in exposure.items()
                       if not k.startswith("_")},
        "d_adjudication": adjudications,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(art, indent=2) + "\n")
    print(f"wrote {out_path.relative_to(_REPO_ROOT)}")

    by_state: Dict[str, int] = {}
    for row in harnesses.values():
        by_state[row["state"]] = by_state.get(row["state"], 0) + 1
    print("A provenance:", by_state)
    print(f"B claims: {len(claims)} unit(s) "
          f"(positive control found: {claim_control_ok})")
    if exposure.get("state") == "measured":
        print(f"C exposure: {exposure['total_legs']} legs, "
              f"{exposure['total_live']} execution:live, "
              f"{exposure['unresolved_legs']} unresolved")
    for unit, a in adjudications.items():
        if isinstance(a, dict) and "mi319_state" in a:
            print(f"D {unit:24} {a['mi319_state']:24} "
                  f"unclassified={a['unclassified_count']}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--out",
                    default="docs/research/mi320-harness-provenance-2026-09-18.json")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if args.run:
        return _run(_REPO_ROOT / args.out)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
