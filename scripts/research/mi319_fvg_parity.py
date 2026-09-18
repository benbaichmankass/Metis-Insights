#!/usr/bin/env python3
# wiring: manual-only — a one-shot ADJUDICATION of two contradictory claims about one
# leg's harness. It grades a docstring against the code, which is a question asked once
# per claim and answered in a committed memo; re-running it on a cadence would re-measure
# a settled verdict, which is the failure mode OI-20260906 exists to stop. Re-run it when
# either side of the comparison CHANGES — its own resolution criteria say so, and the
# backlog row it files names the exact command.
"""MI-319 — grade the ``fvg_range_15m`` VERBATIM-PORT claim by measurement.

THE CLAIM, AND WHY IT IS NOT SETTLED
------------------------------------
``src/units/strategies/fvg_range_15m.py``'s ``order_package`` docstring says:

    The logic is a VERBATIM port of scripts/backtest_fvg_range.py::run_backtest's
    per-bar entry block, evaluated on the most recent closed bar (index -1),
    so live signals match the validated simulation.

The MI-319 dispatch says the opposite -- that this harness *reimplements* entry
and *computes its own target*, so it is NOT a verbatim port the way scalp's is.

**BOTH ARE CLAIMS AND NEITHER HAS BEEN MEASURED.** This module refuses to pick a
side from prose. ``scripts/research/trend_harness_divergence.py`` already states
the rule this repo settled on: *"a ``def run_backtest(...)`` node is a fact about
the code, a docstring saying 'RETIRED' is a claim, and a guard that trusts the
claim is cheaper to lie to than to satisfy"*. So the docstring is the thing being
graded, never the evidence.

FOUR PROBES, DELIBERATELY SEPARATE -- THEY ANSWER DIFFERENT QUESTIONS
--------------------------------------------------------------------
Pooling them is how "the port is fine" and "the shipped run is fine" get
confused, which is the whole defect class here.

* **P1 STRUCTURAL** -- do the two entry blocks *reject on the same predicates*?
  Read from each module's AST, never from its prose. Answers: is the CODE a port?
* **P2 BEHAVIOURAL** -- fed the same candles, do they *accept the same bars* and
  emit the same entry/sl/tp? Answers: does the port BEHAVE as one? Run in two
  window modes, because they are different questions:
    - ``full_prefix`` -- the live unit sees every bar before ``i``, exactly as the
      harness does. Isolates CODE divergence with the inputs held identical.
    - ``live_250``    -- the live unit sees the 250-bar tail the runtime actually
      fetches (``strategy_signal_builders.py:2390``). Isolates the INPUT SEAM.
  A disagreement between the two modes is an input-seam defect, not a code one,
  and saying which is the entire value of running both.
* **P3 INPUT SEAM** -- is any quantity in the entry block history-length
  dependent? Measured, not reasoned: ``_atr`` is ``rolling(...).mean()`` (finite
  window), ``range_hi``/``range_lo`` are rolling extrema, touches and the FVG
  scan are finite slices -- but ``_adx`` is a THREE-deep ``ewm(adjust=False)``
  recursion seeded at the first observation, so its value at bar ``i`` depends on
  everything before ``i``. P3 measures whether 250 bars is enough for it to have
  converged, and counts how often the residual would FLIP the ``adx < adx_max``
  regime gate.
* **P4 TARGET** -- the docstring's claim is scoped to the *entry block*. The
  target is computed OUTSIDE that block in the harness (``exit_style``), so
  grading it under the same verdict would let a true entry claim launder a false
  target one, or vice versa. P4 grades it on its own.

EVERY STATE IS NEVER-COLLAPSED
------------------------------
In particular ``no_accepts_in_population`` is NOT agreement: two implementations
that both accept nothing agree about nothing. A parity run whose accept
denominator is zero has tested the reject side only, and says so.

POPULATION
----------
Stated per probe in the artifact and never assumed. The shipped-parameter
population on the candles available to this container is EMPTY by measurement
(0 accepts over 673 bars, both sides) -- so the accept-side comparison is run at
RELAXED gates as well. That is not a weaker test: the parity claim is about code
that does not know which parameters it was handed, and relaxing the gates raises
the accept count, i.e. the test's power. Both populations are reported; neither
substitutes for the other.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ⚠️ BOUND EAGERLY, BEFORE THE HARNESS IS LOADED. backtest_fvg_range.py does
# `sys.path.insert(0, <repo>/scripts)` at import time for `target_basis`, after
# which `scripts/ml/` SHADOWS the real top-level `ml` package for the rest of the
# process (BL-20260918-SCRIPTS-ON-SYS-PATH-SHADOWS-THE-ML-PACKAGE). Importing the
# live unit first means this module never depends on the resolution order.
from src.units.strategies import fvg_range_15m as LIVE  # noqa: E402

HARNESS_PATH = _REPO_ROOT / "scripts" / "backtest_fvg_range.py"
LIVE_PATH = _REPO_ROOT / "src" / "units" / "strategies" / "fvg_range_15m.py"

#: The candle window ``strategy_signal_builders.fvg_range_15m_signal_builder``
#: actually fetches. READ FROM THE BUILDER, not assumed -- see
#: ``measure_runtime_candle_limit``.
_RUNTIME_LIMIT_FALLBACK = 250

#: The harness arm whose target equals the live unit's (`tp = R` / `tp = S`, the
#: opposite boundary). The harness's own DEFAULT is `mid`.
LIVE_PARITY_EXIT_STYLE = "far"
HARNESS_DEFAULT_EXIT_STYLE = "mid"

# --------------------------------------------------------------------------
# Never-collapsed state vocabularies
# --------------------------------------------------------------------------
P1_IDENTICAL = "identical"
P1_EQUIVALENT_REORDERED = "equivalent_modulo_order"
P1_DIVERGENT = "divergent"
P1_COULD_NOT_PARSE = "could_not_parse"          # we did not look

P2_AGREE = "agree"
P2_DISAGREE = "disagree"
P2_NO_ACCEPTS = "no_accepts_in_population"      # NOT agreement
P2_COULD_NOT_RUN = "could_not_run"              # we did not look

P3_CONVERGED = "converged"
P3_DIVERGES = "diverges"
P3_COULD_NOT_MEASURE = "could_not_measure"      # we did not look

#: A hundredth of an ADX point. Chosen, not tuned: the gate compares ADX to
#: `adx_max` (20.0), so a residual below this can only change the verdict on a
#: bar sitting within 0.01 of the threshold -- which is why `min_margin_to_gate`
#: is reported beside it rather than the tolerance being asserted as safe.
ADX_CONVERGED_TOL = 0.01

BAR_BOTH_ACCEPT = "both_accept"
BAR_BOTH_REJECT = "both_reject"
BAR_LIVE_ONLY = "live_only"
BAR_HARNESS_ONLY = "harness_only"
BAR_SUPPRESSED = "harness_occupied"             # harness could not enter: in a trade

#: Gate tokens, keyed off a STABLE fragment of each ValueError the live unit
#: raises. A message this table does not recognise grades `unknown_gate` rather
#: than being silently bucketed -- a gate we cannot name is a gate we did not
#: classify, which is not the same as a gate that did not fire.
_LIVE_GATE_PHRASES: Tuple[Tuple[str, str], ...] = (
    ("missing OHLC columns", "missing_columns"),
    ("need at least", "insufficient_candles"),
    ("ATR non-positive or range undefined", "atr_or_range_undefined"),
    ("range width", "width_bounds"),
    ("regime not chop", "adx_regime"),
    ("already outside the range", "price_outside_range"),
    ("middle of the range", "no_boundary_edge"),
    ("range not confirmed", "touches"),
    ("no intact matching FVG", "no_fvg"),
    ("wick-rejection", "no_wick_rejection"),
    ("non-positive risk", "non_positive_risk"),
    ("wrong side of entry", "degenerate_target"),
    ("below min_confidence", "confidence_floor"),
)
UNKNOWN_GATE = "unknown_gate"


def classify_live_rejection(message: str) -> str:
    """Map a live ``ValueError`` to a gate token.

    Returns ``UNKNOWN_GATE`` for anything the table does not recognise. This is
    deliberately NOT a broad fallback onto the nearest bucket: an unrecognised
    message most likely means the unit grew a gate this instrument does not know
    about, which is a finding, and burying it in `no_fvg` would hide it.
    """
    text = str(message or "")
    for fragment, token in _LIVE_GATE_PHRASES:
        if fragment in text:
            return token
    return UNKNOWN_GATE


# --------------------------------------------------------------------------
# Harness loading -- by spec, registered in sys.modules
# --------------------------------------------------------------------------
def load_harness():
    """Import ``scripts/backtest_fvg_range.py`` WITHOUT editing it.

    Registered in ``sys.modules`` before ``exec_module`` because the module
    defines a ``@dataclass``, and ``dataclasses`` resolves annotations through
    ``sys.modules[cls.__module__]`` -- an unregistered spec-loaded module makes
    that lookup return ``None`` and the import dies inside ``_is_type``.
    """
    name = "mi319_bt_fvg"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HARNESS_PATH)
    if spec is None or spec.loader is None:      # pragma: no cover - env fault
        raise RuntimeError(f"cannot load harness at {HARNESS_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# P1 -- structural: the rejection predicates, read from the AST
# --------------------------------------------------------------------------
def _normalise_expr(node: ast.AST) -> str:
    """Unparse a predicate and fold the aliases that differ only by name.

    The two blocks bind the same quantity under two names in exactly two places
    (`entry` vs `price`, and the live `tp` vs the harness `unit_target`), both of
    which are assignments of one to the other in their own source. Folding them
    is a NAMING normalisation, never a semantic one: no operator, constant or
    operand order is touched.
    """
    text = ast.unparse(node)
    for src, dst in (("unit_target", "__TARGET__"), ("tp", "__TARGET__"),
                     ("entry", "__PRICE__"), ("price", "__PRICE__")):
        # word-boundary-ish replacement without a regex import: pad and strip
        text = f" {text} ".replace(f" {src} ", f" {dst} ").strip()
        text = text.replace(f"({src} ", f"({dst} ").replace(f" {src})", f" {dst})")
    return text


def _rejection_predicates(fn: ast.AST, *, reject_types: Tuple[type, ...]) -> List[str]:
    """Every ``if <test>:`` whose body is ONLY a rejection, in source order.

    A rejection is `continue` (harness) or `raise ValueError` (live). The body
    must contain nothing else -- an `if` that also computes something is not a
    gate and must not be counted as one.
    """
    out: List[str] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        body = [s for s in node.body if not isinstance(s, ast.Pass)]
        rejects = [s for s in body if isinstance(s, reject_types)]
        # the harness bumps `i += 1` before `continue`; that is loop bookkeeping,
        # not logic, so an AugAssign to a bare name is tolerated in the body.
        others = [s for s in body
                  if not isinstance(s, reject_types)
                  and not (isinstance(s, ast.AugAssign) and isinstance(s.target, ast.Name))]
        if rejects and not others and not node.orelse:
            out.append(_normalise_expr(node.test))
    return out


def _find_function(path: Path, name: str) -> Optional[ast.AST]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _disjuncts(text: str) -> List[str]:
    """Split a normalised predicate into its top-level ``or`` operands.

    A gate written as two ``if``s and the same gate written as one ``if A or B``
    reject exactly the same bars. Comparing whole predicates calls that a
    divergence; comparing DISJUNCTS does not. This is a structural equivalence,
    not an allowlist of strings someone noticed -- which is the difference
    between a check and a presence-only marker.
    """
    try:
        node = ast.parse(text, mode="eval").body
    except SyntaxError:
        return [text]
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        out: List[str] = []
        for v in node.values:
            out.extend(_disjuncts(ast.unparse(v)))
        return out
    return [ast.unparse(node)]


#: Names bound OUTSIDE the per-bar entry block on one side only. A predicate
#: mentioning one of these is not an entry rule -- it is the harness's own loop
#: bookkeeping, or the live unit validating an argument the harness validates
#: once up front. Classified by the NAME it references, read from the AST.
_HARNESS_BOOKKEEPING_NAMES = ("next_idx",)
_LIVE_VALIDATION_NAMES = ("candles_df", "missing_cols", "needed")


def _mentions(text: str, names: Tuple[str, ...]) -> bool:
    try:
        node = ast.parse(text, mode="eval")
    except SyntaxError:
        return False
    return any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(node))


def probe_structural(harness_path: Path = HARNESS_PATH,
                     live_path: Path = LIVE_PATH) -> Dict[str, Any]:
    h_fn = _find_function(harness_path, "run_backtest")
    l_fn = _find_function(live_path, "order_package")
    if h_fn is None or l_fn is None:
        return {"state": P1_COULD_NOT_PARSE,
                "reason": "run_backtest or order_package not found",
                "harness_found": h_fn is not None, "live_found": l_fn is not None}
    h = _rejection_predicates(h_fn, reject_types=(ast.Continue,))
    lv = _rejection_predicates(l_fn, reject_types=(ast.Raise,))

    hd = sorted(d for p in h for d in _disjuncts(p))
    ld = sorted(d for p in lv for d in _disjuncts(p))
    h_only = sorted(set(hd) - set(ld))
    l_only = sorted(set(ld) - set(hd))

    residuals: Dict[str, List[str]] = {
        "harness_loop_bookkeeping": [], "live_input_validation": [],
        "unclassified_divergence": []}
    for d in h_only:
        key = ("harness_loop_bookkeeping" if _mentions(d, _HARNESS_BOOKKEEPING_NAMES)
               else "unclassified_divergence")
        residuals[key].append(d)
    for d in l_only:
        key = ("live_input_validation" if _mentions(d, _LIVE_VALIDATION_NAMES)
               else "unclassified_divergence")
        residuals[key].append(d)

    if not residuals["unclassified_divergence"]:
        state = P1_IDENTICAL if h == lv else P1_EQUIVALENT_REORDERED
    else:
        state = P1_DIVERGENT
    return {
        "state": state,
        "harness_predicates": h,
        "live_predicates": lv,
        "harness_disjuncts": hd,
        "live_disjuncts": ld,
        "shared_disjunct_count": len(set(hd) & set(ld)),
        "harness_only_disjuncts": h_only,
        "live_only_disjuncts": l_only,
        "residual_classification": residuals,
        "order_matches": h == lv,
        "verdict_note": ("state is DERIVED from whether any residual disjunct is "
                         "UNCLASSIFIED. A residual that is the harness's own loop "
                         "bookkeeping or the live unit's argument validation is not "
                         "an entry rule and is reported rather than counted."),
    }


# --------------------------------------------------------------------------
# P2 -- behavioural: same candles, compare accepts bar by bar
# --------------------------------------------------------------------------
def harness_entries(mod, df, params: Dict[str, Any], *, exit_style: str) -> Dict[str, Any]:
    """Run the UNMODIFIED harness and read its own per-trade emit.

    Occupancy is reconstructed from the emitted rows (`entry_time` + `hold_bars`)
    rather than re-derived: the harness suppresses a candidate while a trade is
    open (`if i < next_idx: continue`), and a bar it never evaluated must not be
    scored as a disagreement.
    """
    ts_index = {str(t): i for i, t in enumerate(df["timestamp"].tolist())}
    with tempfile.TemporaryDirectory() as tmp:
        emit = os.path.join(tmp, "emit.jsonl")
        summary = mod.run_backtest(df.copy(), exit_style=exit_style,
                                   emit_path=emit, **params)
        rows = []
        if os.path.exists(emit):
            with open(emit, "r", encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh if line.strip()]
    accepts: Dict[int, Dict[str, Any]] = {}
    occupied: set = set()
    for r in rows:
        idx = ts_index.get(str(r.get("entry_time")))
        if idx is None:
            continue
        accepts[idx] = r
        hold = int(r.get("hold_bars") or 0)
        for k in range(idx + 1, idx + hold + 1 + int(params.get("cooldown_bars", 0))):
            occupied.add(k)
    return {"accepts": accepts, "occupied": occupied,
            "total_trades": summary.get("total_trades"), "emitted_rows": len(rows)}


def live_decision(df, i: int, cfg: Dict[str, Any], *, window: Optional[int]):
    """Evaluate the live unit as if bar ``i`` were the latest closed bar.

    ``window`` None => the whole prefix (code-parity mode). An int => the tail
    the runtime actually fetches (input-seam mode).
    """
    start = 0 if window is None else max(0, i + 1 - window)
    sub = df.iloc[start:i + 1]
    try:
        pkg = LIVE.order_package(dict(cfg), sub)
    except ValueError as exc:
        return ("reject", classify_live_rejection(str(exc)), None)
    return ("accept", None, pkg)


def probe_behavioural(mod, df, params: Dict[str, Any], cfg: Dict[str, Any], *,
                      exit_style: str, window: Optional[int],
                      max_bars: Optional[int] = None) -> Dict[str, Any]:
    try:
        h = harness_entries(mod, df, params, exit_style=exit_style)
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        return {"state": P2_COULD_NOT_RUN, "reason": f"harness: {type(exc).__name__}: {exc}"}
    needed = max(int(params["range_lookback"]), int(params["atr_period"]),
                 int(params["adx_period"])) + 3
    lo = needed - 1
    hi = len(df) - 2                     # harness loop is `while i < n - 1`
    if hi < lo:
        return {"state": P2_COULD_NOT_RUN, "reason": "fewer bars than the warm-up needs"}
    indices = list(range(lo, hi + 1))
    if max_bars is not None:
        indices = indices[:max_bars]
    verdicts: Dict[str, int] = {}
    gate_counts: Dict[str, int] = {}
    field_deltas: List[Dict[str, Any]] = []
    disagreements: List[Dict[str, Any]] = []
    for i in indices:
        kind, gate, pkg = live_decision(df, i, cfg, window=window)
        in_harness = i in h["accepts"]
        if i in h["occupied"]:
            verdicts[BAR_SUPPRESSED] = verdicts.get(BAR_SUPPRESSED, 0) + 1
            continue
        if kind == "accept" and in_harness:
            verdicts[BAR_BOTH_ACCEPT] = verdicts.get(BAR_BOTH_ACCEPT, 0) + 1
            row = h["accepts"][i]
            delta = {
                "bar": i,
                "direction_match": pkg["direction"] == row.get("direction"),
                "entry_abs": abs(float(pkg["entry"]) - float(row["entry"])),
                "sl_abs": abs(float(pkg["sl"]) - float(row["sl"])),
                "tp_abs": (abs(float(pkg["tp"]) - float(row["unit_tp"]))
                           if row.get("unit_tp") is not None else None),
                "confidence_abs": abs(float(pkg["confidence"]) - float(row.get("confidence", 0.0))),
                "live_adx": pkg.get("meta", {}).get("adx"),
            }
            field_deltas.append(delta)
        elif kind == "accept":
            verdicts[BAR_LIVE_ONLY] = verdicts.get(BAR_LIVE_ONLY, 0) + 1
            disagreements.append({"bar": i, "kind": BAR_LIVE_ONLY,
                                  "live_direction": pkg["direction"]})
        elif in_harness:
            verdicts[BAR_HARNESS_ONLY] = verdicts.get(BAR_HARNESS_ONLY, 0) + 1
            disagreements.append({"bar": i, "kind": BAR_HARNESS_ONLY, "live_gate": gate})
        else:
            verdicts[BAR_BOTH_REJECT] = verdicts.get(BAR_BOTH_REJECT, 0) + 1
            gate_counts[gate] = gate_counts.get(gate, 0) + 1
    both = verdicts.get(BAR_BOTH_ACCEPT, 0)
    only = verdicts.get(BAR_LIVE_ONLY, 0) + verdicts.get(BAR_HARNESS_ONLY, 0)
    if both == 0 and only == 0:
        state = P2_NO_ACCEPTS
    elif only == 0 and all(d["direction_match"] and d["entry_abs"] < 1e-6
                           and d["sl_abs"] < 1e-6 and (d["tp_abs"] is None or d["tp_abs"] < 1e-6)
                           for d in field_deltas):
        state = P2_AGREE
    else:
        state = P2_DISAGREE
    return {
        "state": state,
        "window": window,
        "exit_style": exit_style,
        "bars_graded": len(indices),
        "verdicts": verdicts,
        "reject_gates": gate_counts,
        "harness_total_trades": h["total_trades"],
        "accept_denominator": both + only,
        "field_deltas": field_deltas[:50],
        "disagreements": disagreements[:50],
        "unknown_gate_bars": gate_counts.get(UNKNOWN_GATE, 0),
    }


# --------------------------------------------------------------------------
# P3 -- input seam: has ADX converged inside the window live actually gets?
# --------------------------------------------------------------------------
def probe_input_seam(df, *, adx_period: int, adx_max: float,
                     windows: Tuple[int, ...]) -> Dict[str, Any]:
    """Compare ADX on a tail window against ADX on the full prefix, per bar.

    ``_adx`` is imported from the LIVE unit rather than re-implemented -- a second
    copy of the formula could drift from both of the ones being compared, which
    is the defect this whole unit is about, one level up.
    """
    try:
        import numpy as np
        full = LIVE._adx(df, adx_period).shift(1).to_numpy(dtype=float)
    except Exception as exc:  # noqa: BLE001
        return {"state": P3_COULD_NOT_MEASURE, "reason": f"{type(exc).__name__}: {exc}"}
    out: Dict[str, Any] = {"adx_period": adx_period, "adx_max": adx_max, "windows": {}}
    worst_state = P3_CONVERGED
    for w in windows:
        deltas: List[float] = []
        margins: List[float] = []
        flips = 0
        graded = 0
        for i in range(w, len(df)):
            sub = df.iloc[i + 1 - w:i + 1]
            tail = LIVE._adx(sub, adx_period).shift(1).to_numpy(dtype=float)[-1]
            ref = full[i]
            if np.isnan(tail) or np.isnan(ref):
                continue
            graded += 1
            deltas.append(abs(float(tail) - float(ref)))
            margins.append(abs(float(ref) - adx_max))
            if (float(tail) < adx_max) != (float(ref) < adx_max):
                flips += 1
        if not graded:
            out["windows"][w] = {"state": P3_COULD_NOT_MEASURE, "graded": 0}
            worst_state = P3_COULD_NOT_MEASURE if worst_state == P3_CONVERGED else worst_state
            continue
        mx = max(deltas)
        # ⚠️ GRADED ON GATE FLIPS, with the residual reported beside them and a
        # STATED tolerance. An earlier draft used `mx < 1e-6`, which graded the
        # deployed 250-bar window `diverges` on a 5.2e-05 residual that flipped
        # NOTHING -- a verdict driven by the threshold rather than by the
        # condition. `min_margin` is the smallest distance any graded bar's ADX
        # sat from the gate, so a reader can check that the residual could not
        # have moved one instead of taking it on trust.
        state = (P3_CONVERGED if flips == 0 and mx < ADX_CONVERGED_TOL
                 else P3_DIVERGES)
        if state == P3_DIVERGES:
            worst_state = P3_DIVERGES
        out["windows"][w] = {"state": state, "graded": graded,
                             "tolerance": ADX_CONVERGED_TOL,
                             "min_margin_to_gate": round(min(margins), 6) if margins else None,
                             "max_abs_delta": round(mx, 10),
                             "mean_abs_delta": round(sum(deltas) / graded, 10),
                             "gate_flips": flips,
                             "gate_flip_pct": round(100.0 * flips / graded, 4)}
    deployed = out["windows"].get(max(windows))
    out["state"] = worst_state
    out["state_at_deployed_window"] = (deployed or {}).get("state")
    out["deployed_window"] = max(windows)
    out["note"] = ("`state` is the WORST across every window probed, including "
                   "windows nothing actually passes. `state_at_deployed_window` "
                   "is the one that describes the running system; quoting the "
                   "first as if it were the second would report a converged seam "
                   "as a broken one.")
    return out


# --------------------------------------------------------------------------
# P4 -- the target, graded on its own
# --------------------------------------------------------------------------
def measure_runtime_candle_limit(
        builder_path: Optional[Path] = None) -> Dict[str, Any]:
    """Read the candle ``limit=`` the fvg builder passes, from its AST.

    Returns a state rather than a bare int, because *we could not read it* and
    *it is 250* must not look alike to a caller.
    """
    path = builder_path or (_REPO_ROOT / "src" / "runtime" / "strategy_signal_builders.py")
    fn = _find_function(path, "fvg_range_15m_signal_builder")
    if fn is None:
        return {"state": "could_not_read", "limit": None,
                "reason": "fvg_range_15m_signal_builder not found"}
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "fetch_candles":
            for kw in node.keywords:
                if kw.arg == "limit" and isinstance(kw.value, ast.Constant):
                    return {"state": "read", "limit": int(kw.value.value)}
    return {"state": "could_not_read", "limit": None,
            "reason": "no fetch_candles(limit=<const>) call in the builder"}


def probe_target(mod) -> Dict[str, Any]:
    """Which harness arm reproduces the live target, and what the default does."""
    styles = list(getattr(mod, "_EXIT_STYLES", ()))
    return {
        "live_target": "opposite range boundary (tp = R long / tp = S short), unclamped",
        "harness_styles": styles,
        "harness_default": HARNESS_DEFAULT_EXIT_STYLE,
        "live_parity_style": LIVE_PARITY_EXIT_STYLE,
        "default_is_live_parity": HARNESS_DEFAULT_EXIT_STYLE == LIVE_PARITY_EXIT_STYLE,
        "note": ("the docstring's claim is scoped to the per-bar ENTRY block; the "
                 "target is computed outside it, so this is graded separately and "
                 "a verdict on one never carries to the other"),
    }


# --------------------------------------------------------------------------
# P5 -- is the declared-minimum gap a CLASS or a one-leg instance?
# --------------------------------------------------------------------------
#: The three strategy units that share the ewm-recursive `_adx`, found by
#: probing for the recursion rather than by naming them: a unit that grows one
#: later must be discovered, not remembered.
def _units_with_recursive_adx(root: Optional[Path] = None) -> List[Path]:
    base = root or (_REPO_ROOT / "src" / "units" / "strategies")
    out = []
    for f in sorted(base.glob("*.py")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if "def _adx" in text and "ewm(alpha=alpha, adjust=False)" in text:
            out.append(f)
    return out


def _eval_needed(path: Path) -> Optional[int]:
    """Evaluate the unit's own ``needed = <expr>`` against its own ``_DEFAULTS``.

    Read and evaluated structurally -- never hardcoded -- so a unit that changes
    its warm-up arithmetic is re-measured rather than re-remembered. Returns
    ``None`` (we could not read it) rather than a guess.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    defaults: Dict[str, Any] = {}
    expr: Optional[ast.AST] = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == "_DEFAULTS" and node.value is not None:
            try:
                defaults = ast.literal_eval(node.value)
            except ValueError:
                return None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == "needed" and expr is None:
            expr = node.value
    if expr is None or not defaults:
        return None

    def ev(n: ast.AST):
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            return defaults.get(n.id)
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
            a, b = ev(n.left), ev(n.right)
            return None if a is None or b is None else a + b
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "max":
            vals = [ev(a) for a in n.args]
            return None if any(v is None for v in vals) else max(vals)
        return None
    got = ev(expr)
    return int(got) if isinstance(got, (int, float)) else None


def _builder_limit(unit_stem: str) -> Dict[str, Any]:
    return measure_runtime_candle_limit_for(f"{unit_stem}_signal_builder")


def measure_runtime_candle_limit_for(fn_name: str,
                                     builder_path: Optional[Path] = None) -> Dict[str, Any]:
    path = builder_path or (_REPO_ROOT / "src" / "runtime" / "strategy_signal_builders.py")
    fn = _find_function(path, fn_name)
    if fn is None:
        return {"state": "could_not_read", "limit": None, "reason": f"{fn_name} not found"}
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "fetch_candles":
            for kw in node.keywords:
                if kw.arg == "limit" and isinstance(kw.value, ast.Constant):
                    return {"state": "read", "limit": int(kw.value.value)}
    return {"state": "could_not_read", "limit": None,
            "reason": "no fetch_candles(limit=<const>) call"}


def probe_declared_minimum_class() -> Dict[str, Any]:
    """Structural exposure only -- the 21.9% figure is MEASURED for fvg alone.

    ⚠️ This probe reports the GAP between what each unit declares sufficient and
    what its builder actually fetches. It does NOT measure the other two units'
    ADX divergence: that needs their own candles and configs, and quoting fvg's
    number for them would be exactly the unprovenanced generalisation this repo
    has a guard class for.
    """
    rows = []
    for path in _units_with_recursive_adx():
        stem = path.stem
        needed = _eval_needed(path)
        lim = _builder_limit(stem)
        rows.append({
            "unit": stem,
            "declared_minimum": needed,
            "declared_minimum_state": "read" if needed is not None else "could_not_read",
            "builder_limit": lim["limit"],
            "builder_limit_state": lim["state"],
            "gap_state": ("could_not_grade" if needed is None or lim["limit"] is None
                          else "minimum_below_fetched" if needed < lim["limit"]
                          else "minimum_at_or_above_fetched"),
        })
    return {"units_probed": len(rows), "rows": rows,
            "note": ("a unit whose declared minimum sits far below what its builder "
                     "fetches has an UNTESTED contract, not a live fault -- the live "
                     "path passes the larger number. It becomes a fault the moment "
                     "any other caller honours the declared minimum.")}


# --------------------------------------------------------------------------
# Selftest
# --------------------------------------------------------------------------
def _selftest() -> int:
    checks: List[Tuple[str, bool]] = []

    def ck(name: str, cond: bool) -> None:
        checks.append((name, bool(cond)))

    # --- gate classification -------------------------------------------------
    ck("gate: adx", classify_live_rejection(
        "Strategy 'fvg_range_15m': regime not chop (ADX=31 >= 20) — non-actionable.")
        == "adx_regime")
    ck("gate: touches", classify_live_rejection(
        "Strategy 'fvg_range_15m': range not confirmed (touches R=1/S=0 < 4)")
        == "touches")
    ck("gate: width", classify_live_rejection(
        "Strategy 'fvg_range_15m': range width 0.3 of price outside [0.015, 0.12]")
        == "width_bounds")
    # NEG1 -- an unrecognised message must NOT be bucketed into the nearest gate
    ck("NEG1: unknown message grades unknown_gate",
       classify_live_rejection("Strategy 'fvg_range_15m': some future gate")
       == UNKNOWN_GATE)
    ck("NEG1b: empty message grades unknown_gate",
       classify_live_rejection("") == UNKNOWN_GATE)

    # --- P1 on synthetic sources --------------------------------------------
    same_h = "def run_backtest():\n    while True:\n        if a < b:\n            i += 1\n            continue\n"
    same_l = "def order_package():\n    if a < b:\n        raise ValueError('x')\n"
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        hp, lp = Path(td) / "h.py", Path(td) / "l.py"
        hp.write_text(same_h)
        lp.write_text(same_l)
        r = probe_structural(hp, lp)
        ck("P1: matching predicates grade identical", r["state"] == P1_IDENTICAL)
        # NEG2 -- a real divergence must NOT grade identical
        lp.write_text("def order_package():\n    if a <= b:\n        raise ValueError('x')\n")
        r2 = probe_structural(hp, lp)
        ck("NEG2: `<` vs `<=` grades divergent", r2["state"] == P1_DIVERGENT)
        # NEG3 -- reordering must be reported as reordered, never as identical
        hp.write_text("def run_backtest():\n    while True:\n        if a < b:\n            continue\n        if c > d:\n            continue\n")
        lp.write_text("def order_package():\n    if c > d:\n        raise ValueError('x')\n    if a < b:\n        raise ValueError('y')\n")
        r3 = probe_structural(hp, lp)
        ck("NEG3: reordered grades equivalent_modulo_order",
           r3["state"] == P1_EQUIVALENT_REORDERED)
        # NEG7 -- a gate split across two ifs is NOT a divergence
        hp.write_text("def run_backtest():\n    while True:\n        if a < b:\n            continue\n        if c > d:\n            continue\n")
        lp.write_text("def order_package():\n    if a < b or c > d:\n        raise ValueError('x')\n")
        r7 = probe_structural(hp, lp)
        ck("NEG7: split-vs-combined gate is not a divergence",
           r7["state"] == P1_EQUIVALENT_REORDERED
           and r7["residual_classification"]["unclassified_divergence"] == [])
        # NEG8 -- a residual that is neither bookkeeping nor validation must NOT be
        # excused by the classifier; the whole hazard of a classifier is that it
        # becomes a way to launder a real finding.
        hp.write_text("def run_backtest():\n    while True:\n        if a < b:\n            continue\n")
        lp.write_text("def order_package():\n    if a < b:\n        raise ValueError('x')\n    if zzz > 3:\n        raise ValueError('y')\n")
        r8 = probe_structural(hp, lp)
        ck("NEG8: an unexplained residual still grades divergent",
           r8["state"] == P1_DIVERGENT
           and r8["residual_classification"]["unclassified_divergence"] == ["zzz > 3"])
        # NEG9 -- classification must key on the NAME in the AST, not on the
        # predicate happening to contain the substring
        ck("NEG9: classifier reads names, not substrings",
           _mentions("next_idx > 3", _HARNESS_BOOKKEEPING_NAMES) is True
           and _mentions("'next_idx' > x", _HARNESS_BOOKKEEPING_NAMES) is False)
        # NEG4 -- an unparseable source must grade could_not_parse, never identical
        lp.write_text("def order_package(:\n")
        r4 = probe_structural(hp, lp)
        ck("NEG4: syntax error grades could_not_parse", r4["state"] == P1_COULD_NOT_PARSE)
        # an `if` whose body also computes is not a gate
        hp.write_text("def run_backtest():\n    while True:\n        if a < b:\n            x = 1\n            continue\n")
        ck("P1: if-with-side-effect is not counted as a gate",
           _rejection_predicates(_find_function(hp, "run_backtest"),
                                 reject_types=(ast.Continue,)) == [])
        # builder limit read
        bp = Path(td) / "b.py"
        bp.write_text("def fvg_range_15m_signal_builder(s):\n    candles_df = fetch_candles(sym, tf, exchange_client=e, limit=250)\n")
        ck("P4: builder limit read", measure_runtime_candle_limit(bp)
           == {"state": "read", "limit": 250})
        # NEG5 -- a builder we cannot read must not report a number
        bp.write_text("def fvg_range_15m_signal_builder(s):\n    pass\n")
        got = measure_runtime_candle_limit(bp)
        ck("NEG5: unreadable builder reports could_not_read, not a default",
           got["state"] == "could_not_read" and got["limit"] is None)

    # --- normalisation -------------------------------------------------------
    ck("norm: entry/price fold", _normalise_expr(ast.parse("entry <= 0", mode="eval").body)
       == _normalise_expr(ast.parse("price <= 0", mode="eval").body))
    ck("norm: tp/unit_target fold",
       _normalise_expr(ast.parse("tp <= entry", mode="eval").body)
       == _normalise_expr(ast.parse("unit_target <= price", mode="eval").body))
    # NEG6 -- normalisation must not fold two genuinely different operands
    ck("NEG6: risk and entry are not folded together",
       _normalise_expr(ast.parse("risk <= 0", mode="eval").body)
       != _normalise_expr(ast.parse("entry <= 0", mode="eval").body))

    # --- states are distinct -------------------------------------------------
    ck("states: P2_NO_ACCEPTS is not P2_AGREE", P2_NO_ACCEPTS != P2_AGREE)
    ck("states: P3_COULD_NOT_MEASURE is not P3_CONVERGED",
       P3_COULD_NOT_MEASURE != P3_CONVERGED)
    ck("P3: tolerance is stated, not 1e-6", ADX_CONVERGED_TOL == 0.01)
    ck("states: all P2 tokens distinct",
       len({P2_AGREE, P2_DISAGREE, P2_NO_ACCEPTS, P2_COULD_NOT_RUN}) == 4)

    # --- the real sources parse ---------------------------------------------
    real = probe_structural()
    ck("P1: real sources parse", real["state"] != P1_COULD_NOT_PARSE)
    ck("P1: real harness has gates", len(real.get("harness_predicates", [])) > 0)
    ck("P1: real live unit has gates", len(real.get("live_predicates", [])) > 0)

    p5 = probe_declared_minimum_class()
    ck("P5: probes at least the fvg unit", p5["units_probed"] >= 1)
    ck("P5: every row carries a gap state",
       all(r["gap_state"] in ("minimum_below_fetched", "minimum_at_or_above_fetched",
                              "could_not_grade") for r in p5["rows"]))
    ck("P5: fvg declared minimum is read", any(
        r["unit"] == "fvg_range_15m" and r["declared_minimum_state"] == "read"
        for r in p5["rows"]))
    # NEG10 -- a unit we cannot read must report could_not_grade, never a number
    import tempfile as _t2
    with _t2.TemporaryDirectory() as td2:
        bad = Path(td2) / "x.py"
        bad.write_text("def _adx():\n    pass\n")
        ck("NEG10: no _DEFAULTS => declared minimum is None, not a guess",
           _eval_needed(bad) is None)
    mod = load_harness()
    ck("harness loads", hasattr(mod, "run_backtest"))
    t = probe_target(mod)
    ck("P4: default is not live parity", t["default_is_live_parity"] is False)
    ck("P4: far is a declared style", LIVE_PARITY_EXIT_STYLE in t["harness_styles"])

    ok = sum(1 for _, c in checks if c)
    for name, c in checks:
        if not c:
            print(f"  FAIL  {name}")
    print(f"mi319 selftest: {ok}/{len(checks)} passed")
    return 0 if ok == len(checks) else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--candles", default="data/btc_1m_sample.csv")
    ap.add_argument("--out", default="docs/research/mi319-fvg-parity-2026-09-18.json")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.run:
        ap.print_help()
        return 2
    return _run(args)


def _run(args) -> int:
    import pandas as pd
    mod = load_harness()
    raw = pd.read_csv(args.candles)
    raw["timestamp"] = pd.to_datetime(raw["timestamp"])
    df = mod._resample(raw, "15min")

    limit = measure_runtime_candle_limit()
    live_window = limit["limit"] or _RUNTIME_LIMIT_FALLBACK

    shipped = {k: v for k, v in LIVE._DEFAULTS.items()
               if k not in ("timeframe", "min_confidence", "timeout_bars")}
    harness_params = dict(
        range_lookback=shipped["range_lookback"], atr_period=shipped["atr_period"],
        adx_period=shipped["adx_period"], adx_max=shipped["adx_max"],
        min_width_pct=shipped["min_width_pct"], max_width_pct=shipped["max_width_pct"],
        touch_tol_pct=shipped["touch_tol_pct"], min_touches=shipped["min_touches"],
        third_frac=shipped["third_frac"], fvg_search=shipped["fvg_search"],
        min_fvg_size_bps=shipped["min_fvg_size_bps"],
        atr_stop_buffer=shipped["atr_stop_buffer"], tp_r=1.0,
        timeout_bars=int(LIVE._DEFAULTS["timeout_bars"]), cooldown_bars=0,
        min_confidence=float(LIVE._DEFAULTS["min_confidence"]),
        timeframe="15m", symbol="BTCUSDT")

    arms = {
        "shipped": {},
        "relaxed_touches_width": dict(min_width_pct=0.0005, min_touches=1),
        "relaxed_all": dict(min_width_pct=0.0005, max_width_pct=0.9, min_touches=1,
                            adx_max=100.0, min_fvg_size_bps=0.0),
    }
    behavioural: Dict[str, Any] = {}
    for arm, over in arms.items():
        hp = dict(harness_params)
        hp.update(over)
        cfg = {"symbol": "BTCUSDT", "timeframe": "15m"}
        cfg.update({k: hp[k] for k in (
            "range_lookback", "atr_period", "adx_period", "adx_max",
            "min_width_pct", "max_width_pct", "touch_tol_pct", "min_touches",
            "third_frac", "fvg_search", "min_fvg_size_bps", "atr_stop_buffer",
            "timeout_bars", "min_confidence")})
        behavioural[arm] = {
            "full_prefix": probe_behavioural(mod, df, hp, cfg,
                                             exit_style=LIVE_PARITY_EXIT_STYLE,
                                             window=None),
            f"live_{live_window}": probe_behavioural(mod, df, hp, cfg,
                                                     exit_style=LIVE_PARITY_EXIT_STYLE,
                                                     window=live_window),
        }

    payload = {
        "unit": "MI-319",
        "generated_at": "2026-09-18",
        "population": {
            "candles": args.candles,
            "resampled_to": "15m",
            "bars": int(len(df)),
            "first": str(df["timestamp"].iloc[0]),
            "last": str(df["timestamp"].iloc[-1]),
            "symbol_assumed": "BTCUSDT",
        },
        "runtime_candle_limit": limit,
        "p1_structural": probe_structural(),
        "p2_behavioural": behavioural,
        "p3_input_seam": probe_input_seam(
            df, adx_period=int(LIVE._DEFAULTS["adx_period"]),
            adx_max=float(LIVE._DEFAULTS["adx_max"]),
            windows=(51, 100, live_window)),
        "p4_target": probe_target(mod),
        "p5_declared_minimum_class": probe_declared_minimum_class(),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
