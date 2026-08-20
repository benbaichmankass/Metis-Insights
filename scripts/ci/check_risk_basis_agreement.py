#!/usr/bin/env python3
"""Guard: a backtest harness must not silently size at a risk basis that
disagrees with live.

THE DEFECT THIS EXISTS FOR (measured 2026-08-20)
------------------------------------------------
``config/accounts.yaml`` declares ``risk.risk_pct: 0.015`` — a FRACTION,
1.5%, which is what ``src/units/accounts/risk.py`` multiplies balance by.
``scripts/backtest_system.py::_risk_qty`` computes
``(bal * (rpct / 100.0)) / stop_dist`` — a PERCENT — and defaults
``--risk-pct`` to ``0.3``. So the default backtest sizes at **0.3%, one
fifth of live**, and the comment above that formula claims it *"mirrors the
live RiskManager.position_size math"* while inserting the ``/ 100.0`` that
makes it not.

Nothing detected this because both halves are individually correct. The
defect lives at the seam, which is exactly the class this repo's guard
family exists to catch.

WHAT IT CHECKS
--------------
Every harness that declares a per-trade risk default is graded against the
live value read from ``config/accounts.yaml`` via
``src.research.risk_basis`` — the ONE definition. A site whose default
disagrees must be in :data:`KNOWN_DIVERGENCES` with its measured ratio.

⚠️ :data:`KNOWN_DIVERGENCES` IS A DEBT REGISTER, NOT AN EXEMPTION LIST.
Each entry records the ratio measured when it was filed. If a site's ratio
CHANGES the guard fails even though the site is registered — a stale
grandfather that silently absorbs new drift is worse than no guard
(``new-table-wiring-guard``'s presence-only ``# data-wiring:`` marker is
the local precedent: it made the cheapest way to silence a real finding
naming a table that does not exist).

Exit 0 clean, 1 on a finding, 2 on a usage error.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.research.risk_basis import (  # noqa: E402
    DEFAULT_REFERENCE_ACCOUNT,
    UNIT_FRACTION,
    UNIT_PERCENT,
    live_risk,
    to_percent,
)

# Files scanned for a risk default. Scoped to the harness fleet; the live
# sizer is the SOURCE of truth and is deliberately not graded against itself.
SCAN_GLOBS = (
    "scripts/backtest_*.py",
    "scripts/walkforward_*.py",
    "scripts/research/*.py",
    "scripts/ml/*backtest*.py",
    "scripts/ml/record_harness_trades.py",
    "scripts/prop/*.py",
    "src/backtest/*.py",
)

# Argparse flags that mean "per-trade risk".
_RISK_FLAGS = ("--risk-pct", "--base-risk-pct")

#: ⚠️ THE UNIT IS PER-FILE AND MUST BE DECLARED, NOT GUESSED.
#:
#: This map is the guard's whole reason for existing. The first real run
#: proved the point: it flagged ``pairs_dollar_lots.py --risk-pct 0.015`` as
#: "0.01x live" — but that file uses the FRACTION convention, where 0.015 IS
#: live (1.5%). Read as percent it looks 100x too small; read as fraction it
#: is exactly right. The NUMBER cannot tell you which, and neither can the
#: flag name, because they are identical.
#:
#: So a scanned file with a risk default MUST appear here. An unlisted file
#: is a FINDING, not a default-to-percent — guessing the unit is precisely
#: the mistake that produced a five-fold live/backtest gap nobody noticed.
FILE_UNITS: Dict[str, str] = {
    # percent: value is divided by 100 before use (the backtest fleet)
    "scripts/backtest_system.py": UNIT_PERCENT,
    "scripts/research/build_backtest_panel.py": UNIT_PERCENT,
    "scripts/research/allocator_multisymbol_backtest.py": UNIT_PERCENT,
    "scripts/walkforward_flip_policy.py": UNIT_PERCENT,
    "scripts/prop/evaluate_prop.py": UNIT_PERCENT,
    "scripts/ml/record_harness_trades.py": UNIT_PERCENT,
    "scripts/ml/backtest_augment_runner.py": UNIT_PERCENT,
    "scripts/prop/account_compat_matrix.py": UNIT_PERCENT,
    "scripts/prop/validate_alt_prop.py": UNIT_PERCENT,
    "scripts/prop/montecarlo_prop.py": UNIT_PERCENT,
    # fraction: value is used directly, as the LIVE sizer does
    "scripts/research/pairs_dollar_lots.py": UNIT_FRACTION,
    "scripts/prop/emit_breakout_ticket.py": UNIT_FRACTION,
}

#: site -> (measured_ratio_at_filing, why). Ratio is harness ÷ live.
#: Filed 2026-08-20 against live 1.5% (bybit_2). These are DEBT: each one
#: is a harness whose default answer is about a risk setting production
#: does not use.
KNOWN_DIVERGENCES: Dict[str, Tuple[float, str]] = {
    "scripts/backtest_system.py": (0.2, "0.3% vs live 1.5% — the fleet default"),
    "scripts/research/build_backtest_panel.py": (0.2, "0.3%; passes through to backtest_system"),
    "scripts/research/allocator_multisymbol_backtest.py": (0.2, "0.3%"),
    "scripts/walkforward_flip_policy.py": (0.2, "0.3%"),
    "scripts/prop/evaluate_prop.py": (0.2, "0.3%; prop rulesets size separately"),
    "scripts/ml/record_harness_trades.py": (0.6667, "1.0%"),
    "scripts/ml/backtest_augment_runner.py": (0.6667, "1.0%"),
    "scripts/prop/account_compat_matrix.py": (0.3333, "0.5% base; prop ruleset basis"),
    "scripts/prop/validate_alt_prop.py": (0.3333, "0.5% base — but ALREADY sweeps a grid (0.5,1.0,1.5), the precedent this guard wants generalized"),
    "scripts/prop/montecarlo_prop.py": (0.3333, "0.5% base; prop ruleset basis"),
}

_RATIO_TOL = 0.02  # a registered site may drift 2% before the guard re-fires


def _defaults_in(path: Path) -> List[Tuple[str, float, int]]:
    """Return (flag, default, lineno) for each risk flag with a numeric default."""
    try:
        tree = ast.parse(path.read_text())
    except (OSError, SyntaxError):
        return []
    found: List[Tuple[str, float, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "add_argument"):
            continue
        flag = None
        for a in node.args:
            if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value in _RISK_FLAGS:
                flag = a.value
        if flag is None:
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            v = kw.value
            if isinstance(v, ast.Constant) and isinstance(v.value, (int, float)):
                found.append((flag, float(v.value), node.lineno))
    return found


def scan(root: Path, only: Optional[List[str]] = None) -> Tuple[List[str], int]:
    live = live_risk(DEFAULT_REFERENCE_ACCOUNT)
    problems: List[str] = []
    if not live.ok or not live.percent:
        # We could not look. That is a FINDING, not a pass — a guard that
        # cannot read its reference must not report clean.
        return ([f"::error::risk-basis-agreement: could not resolve live risk "
                 f"({live.state}) — {live.detail}. Grading nothing is not a pass."], 0)

    checked = 0
    seen: set = set()
    for pattern in SCAN_GLOBS:
        for path in sorted(root.glob(pattern)):
            rel = path.relative_to(root).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            if only is not None and rel not in only:
                continue
            for flag, default, lineno in _defaults_in(path):
                checked += 1
                unit = FILE_UNITS.get(rel)
                if unit is None:
                    problems.append(
                        f"::error::{rel}:{lineno}: {flag} default {default:g} has NO "
                        f"declared unit. Add {rel!r} to FILE_UNITS as {UNIT_PERCENT!r} "
                        f"(the value is divided by 100 before use) or {UNIT_FRACTION!r} "
                        f"(used directly, like the live sizer). The number cannot tell "
                        f"you which and neither can the flag name — guessing is the "
                        f"mistake this guard exists to stop.")
                    continue
                # Normalise to PERCENT so unlike files compare.
                default_percent = (default if unit == UNIT_PERCENT
                                   else to_percent(default))
                ratio = default_percent / live.percent
                registered = KNOWN_DIVERGENCES.get(rel)
                if abs(ratio - 1.0) <= _RATIO_TOL:
                    if registered:
                        problems.append(
                            f"::error::{rel}:{lineno}: {flag} default {default:g} ({unit}) = "
                            f"{default_percent:g}% now MATCHES live ({live.percent:g}%) "
                            f"but is still in KNOWN_DIVERGENCES — "
                            f"remove the entry so the register keeps meaning something.")
                    continue
                if registered is None:
                    problems.append(
                        f"::error::{rel}:{lineno}: {flag} default {default:g} ({unit}) = "
                        f"{default_percent:g}%, which is {ratio:.3g}x live "
                        f"({live.percent:g}% from {live.source}). "
                        f"Resolve it through src.research.risk_basis instead of "
                        f"hardcoding, or register it in KNOWN_DIVERGENCES with the "
                        f"measured ratio and a reason.")
                    continue
                filed_ratio, _why = registered
                if abs(ratio - filed_ratio) > _RATIO_TOL:
                    problems.append(
                        f"::error::{rel}:{lineno}: {flag} default {default:g} ({unit}) = "
                        f"{default_percent:g}% is now {ratio:.3g}x live, but "
                        f"KNOWN_DIVERGENCES records {filed_ratio:.3g}x. "
                        f"The registered debt CHANGED — re-measure and update the entry "
                        f"rather than letting a stale grandfather absorb new drift.")
    return problems, checked


def _self_test() -> int:
    """Planted controls. A guard that cannot be shown to fire is not evidence."""
    import tempfile
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(f"  {name}: {'PASS' if cond else 'FAIL'}")
        ok = ok and cond

    print("risk-basis-agreement self-test")

    # 1. The parser finds a planted default.
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "backtest_planted.py"
        f.write_text("import argparse\np = argparse.ArgumentParser()\n"
                     "p.add_argument('--risk-pct', type=float, default=0.3)\n")
        got = _defaults_in(f)
        check("1 parser finds a planted --risk-pct default", got == [("--risk-pct", 0.3, 3)])

    # 2. The parser does NOT invent one where there is none (negative control).
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "backtest_none.py"
        f.write_text("import argparse\np = argparse.ArgumentParser()\n"
                     "p.add_argument('--limit', type=int, default=5)\n")
        check("2 parser is quiet on an unrelated flag", _defaults_in(f) == [])

    # 3. A default that MATCHES live is clean.
    live = live_risk(DEFAULT_REFERENCE_ACCOUNT)
    check("3 live risk resolves (the reference the whole guard rests on)", live.ok)
    if live.ok and live.percent:
        def _planted(default: float, unit: Optional[str], name: str):
            """Scan a one-file repo, optionally declaring the file's unit.

            The tempdir is context-managed and FILE_UNITS is restored in a
            `finally`, so one control cannot leak state into the next — a
            self-test whose cases contaminate each other is not a control.
            """
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / "scripts").mkdir()
                rel = f"scripts/{name}.py"
                (root / rel).write_text(
                    "import argparse\np = argparse.ArgumentParser()\n"
                    f"p.add_argument('--risk-pct', type=float, default={default})\n")
                saved = dict(FILE_UNITS)
                if unit is not None:
                    FILE_UNITS[rel] = unit
                try:
                    return scan(root)
                finally:
                    FILE_UNITS.clear()
                    FILE_UNITS.update(saved)

        probs, n = _planted(live.percent, UNIT_PERCENT, "backtest_matching")
        check("4 a matching PERCENT default is clean", probs == [] and n == 1)

        # THE UNIT CONTROL — the case that motivated the map. The SAME number
        # is right in one unit and 100x wrong in the other, so a guard that
        # guesses would file a correct file as debt (it did, on its first run).
        probs, n = _planted(live.fraction, UNIT_FRACTION, "backtest_fraction")
        check("5 the same value declared as FRACTION is also clean", probs == [] and n == 1)

        probs, n = _planted(live.fraction, UNIT_PERCENT, "backtest_misdeclared")
        check("6 that value MISDECLARED as percent FIRES (100x off)",
              len(probs) == 1 and n == 1)

        probs, n = _planted(live.percent * 0.2, UNIT_PERCENT, "backtest_diverged")
        check("7 an unregistered divergence FIRES", len(probs) == 1 and n == 1)

        probs, n = _planted(live.percent, None, "backtest_undeclared")
        check("8 an UNDECLARED unit FIRES even at a matching value",
              len(probs) == 1 and "NO declared unit" in probs[0])

    # 5. The real repo scan is self-consistent with its own register.
    probs, n = scan(_REPO_ROOT)
    check(f"9 repo scan is clean against KNOWN_DIVERGENCES ({n} sites)", probs == [])
    if probs:
        for p in probs:
            print("     " + p)
    check("10 the repo scan actually examined sites (non-zero denominator)", n > 0)

    print("self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--all", action="store_true", help="scan the whole fleet")
    ap.add_argument("paths", nargs="*", help="repo-relative paths (diff-scoped mode)")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    only = None if (args.all or not args.paths) else list(args.paths)
    problems, checked = scan(_REPO_ROOT, only=only)
    live = live_risk(DEFAULT_REFERENCE_ACCOUNT)
    print(f"risk-basis-agreement: {checked} risk default(s) checked against {live.describe()}")
    for p in problems:
        print(p)
    if problems:
        return 1
    print("risk-basis-agreement: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
