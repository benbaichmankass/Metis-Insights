#!/usr/bin/env python3
"""cost-model-single-owner — a fee constant must ALIAS the owner, not restate it.

WHY THIS EXISTS (B4, fix 2.4 of the 2026-08-20 full-system audit)
-----------------------------------------------------------------
`src/runtime/execution_costs.py` declares itself *"the ONE shared execution-realism
cost model"* and says outright that `DEFAULT_FEE_BPS_ROUNDTRIP` *"lives HERE now …
so there is exactly one owner of the round-trip fee constant."* Measured 2026-08-20
across 861 Python files, that ownership was a claim, not a fact:

  * **11 files** define `FEE_BPS_ROUNDTRIP = 7.5` as a bare literal and never import
    the owner. All 11 agree with it **today**; nothing enforces that they still will.
  * **1 file** — `scripts/backtest_system.py` — imported the owner at line 77 AND
    hardcoded `7.5` at line 108, with different call sites reading each. That is the
    F-113 shape (one name, two homes, agreeing by luck) inside a single file.
  * **5 files** already do the right thing: `FEE_BPS_ROUNDTRIP =
    execution_costs.DEFAULT_FEE_BPS_ROUNDTRIP`.

⚠️ THE 5 CORRECT FILES ARE WHY THIS GUARD READS THE *EXPRESSION*, NOT THE NAME.
The audit that motivated this recorded "five harnesses hardcode their own" — and a
census showed those five are aliases, i.e. already single-sourced. A sweep driven by
the constant's NAME would have "fixed" five correct files, which is exactly the
mistake `check_risk_basis_agreement` was reshaped to avoid after it flagged
`pairs_dollar_lots.py` for using the convention that file correctly uses. What is
wrong is a NUMERIC LITERAL; an alias is the remedy, not the offence.

WHAT IS CHECKED
---------------
1. NEW LITERAL — a file assigning a fee constant to a numeric literal is a finding
   unless it is registered in `KNOWN_DUPLICATES` below.
2. NO SHADOWING — a file that IMPORTS the owner may not also assign the constant a
   numeric literal. There is no legitimate reason: the owner is already in scope.
   This has no debt register, deliberately; it is one line to fix wherever it occurs.
3. DEBT DOES NOT DRIFT — each `KNOWN_DUPLICATES` entry records the value measured at
   filing. If a registered file's value CHANGES, it fires again: a grandfather that
   silently absorbs new divergence is the `new-table-wiring-guard` failure (a guard
   cheaper to lie to than to satisfy is worse than none).

Exit 0 clean, 1 with findings. `--self-test` runs planted controls.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
OWNER = REPO / "src" / "runtime" / "execution_costs.py"
OWNER_NAME = "DEFAULT_FEE_BPS_ROUNDTRIP"

#: Names that mean "the round-trip fee in bps". Deliberately narrow: a per-venue or
#: per-strategy fee OVERRIDE is a legitimate different quantity, and widening this to
#: every `*_BPS` name would flag the taker/maker split and the 2x-fee stress arms.
_FEE_NAMES = re.compile(r"^_?FEE_BPS_ROUNDTRIP$")

#: path -> the literal measured at filing. These predate the guard and are NOT
#: retro-fixed here: each is a separate harness whose PnL series would move if the
#: value ever changed, so the migration is per-file work with its own verification.
#: The registry's job is to stop the population GROWING and to fire if one drifts.
KNOWN_DUPLICATES: dict[str, str] = {
    "scripts/backtest_chop_scalp.py": "7.5",
    "scripts/backtest_funding_carry.py": "7.5",
    "scripts/backtest_fvg_range.py": "7.5",
    "scripts/backtest_pairs.py": "7.5",
    "scripts/backtest_xsec_momentum.py": "7.5",
    "scripts/research/hf_solo_sim.py": "7.5",
    "scripts/research/hf_vectorized.py": "7.5",
    "scripts/research/m20_regime_flip_replay.py": "7.5",
    "scripts/research/regime_matrix.py": "7.5",
    "scripts/research/research_momentum.py": "7.5",
    "src/backtest/run_backtest_vwap.py": "7.5",
}


def owner_value() -> str | None:
    """The owner's declared value, or None if it cannot be read.

    None is returned, never a default: a guard that invents the number it is
    comparing against would pass while comparing nothing.
    """
    try:
        tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        if target == OWNER_NAME and isinstance(value, ast.Constant):
            return repr(value.value) if not isinstance(value.value, float) \
                else f"{value.value:g}"
    return None


def _fee_literals(text: str) -> list[tuple[str, str]]:
    """(name, literal) for each fee constant assigned a NUMERIC LITERAL.

    An alias (`= execution_costs.DEFAULT_FEE_BPS_ROUNDTRIP`) is not a literal and is
    deliberately invisible here — it is the fix, not the finding.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        targets = []
        value = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        for t in targets:
            if isinstance(t, ast.Name) and _FEE_NAMES.match(t.id) \
                    and isinstance(value, ast.Constant) \
                    and isinstance(value.value, (int, float)) \
                    and not isinstance(value.value, bool):
                out.append((t.id, f"{float(value.value):g}"))
    return out


def _imports_owner(text: str) -> bool:
    return bool(re.search(r"execution_costs\s+import|import\s+execution_costs", text))


def scan(root: pathlib.Path) -> list[str]:
    findings: list[str] = []
    own = owner_value()
    if own is None:
        return [f"cannot read {OWNER_NAME} from {OWNER} — refusing to report clean "
                f"over a comparison that was never made"]
    scanned = 0
    for path in sorted(list((root / "scripts").rglob("*.py"))
                       + list((root / "src").rglob("*.py"))
                       + list((root / "ml").rglob("*.py"))):
        if path.resolve() == OWNER.resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        lits = _fee_literals(text)
        if not lits:
            continue
        rel = str(path.relative_to(root))
        imports = _imports_owner(text)
        for name, lit in lits:
            if imports:
                findings.append(
                    f"{rel}: `{name} = {lit}` SHADOWS the owner this file already "
                    f"imports. Use `execution_costs.{OWNER_NAME}` — the value is "
                    f"already in scope, so two homes for it can only drift.")
            elif rel not in KNOWN_DUPLICATES:
                findings.append(
                    f"{rel}: `{name} = {lit}` restates the round-trip fee that "
                    f"{OWNER.relative_to(root)} owns (currently {own}). Alias it: "
                    f"`from src.runtime.execution_costs import {OWNER_NAME}`. If this "
                    f"file genuinely needs a DIFFERENT fee, name it differently — a "
                    f"second quantity under this name is the defect, not the value.")
            elif KNOWN_DUPLICATES[rel] != lit:
                findings.append(
                    f"{rel}: registered duplicate DRIFTED — was "
                    f"{KNOWN_DUPLICATES[rel]} at filing, now {lit} (owner {own}). "
                    f"Either re-point it at the owner or update the register with "
                    f"the reason it legitimately differs.")
    if not scanned:
        findings.append("scanned 0 files — refusing to report clean over an empty "
                        "population")
    return findings


def _self_test() -> int:
    """Planted controls. A guard that cannot fail proves nothing."""
    ok = 0
    total = 0

    def check(label: str, cond: bool) -> None:
        nonlocal ok, total
        total += 1
        if cond:
            ok += 1
            print(f"  ok    {label}")
        else:
            print(f"  FAIL  {label}")

    check("a bare literal is detected",
          _fee_literals("FEE_BPS_ROUNDTRIP = 7.5") == [("FEE_BPS_ROUNDTRIP", "7.5")])
    check("an ALIAS is NOT a finding (the 5 correct files)",
          _fee_literals("FEE_BPS_ROUNDTRIP = execution_costs.DEFAULT_FEE_BPS_ROUNDTRIP") == [])
    check("an annotated literal is detected",
          _fee_literals("FEE_BPS_ROUNDTRIP: float = 9.0") == [("FEE_BPS_ROUNDTRIP", "9")])
    check("a DIFFERENTLY-NAMED fee is left alone",
          _fee_literals("TAKER_FEE_BPS = 7.5") == [])
    check("a bool is not read as a number",
          _fee_literals("FEE_BPS_ROUNDTRIP = True") == [])
    check("unparseable source yields nothing rather than raising",
          _fee_literals("def (:") == [])
    check("the owner's value is readable", owner_value() == "7.5")
    check("import detection sees the real import line",
          _imports_owner("from src.runtime import execution_costs") is True)
    check("import detection is not fooled by an unrelated word",
          _imports_owner("# costs are execution related") is False)
    real = scan(REPO)
    check("the real repo is clean (every duplicate registered, none shadowing)",
          real == [])
    print(f"self-test: {ok}/{total} passed")
    return 0 if ok == total else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    findings = scan(REPO)
    if findings:
        print("\ncost-model-single-owner: FINDINGS")
        print("=" * 60)
        for f in findings:
            print(f"  - {f}")
        print(f"\n{len(KNOWN_DUPLICATES)} pre-existing duplicate(s) are registered "
              f"and not retro-fixed; the register fires if one DRIFTS.")
        return 1
    print(f"cost-model-single-owner: clean "
          f"({len(KNOWN_DUPLICATES)} registered duplicates, none drifted)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
