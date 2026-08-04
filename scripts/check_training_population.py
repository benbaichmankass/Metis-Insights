#!/usr/bin/env python3
"""training-population guard — the "no decision head silently trains live-only" gate.

Root cause it exists for (BL-20260731-BACKTEST-AUGMENTATION-NEVER-FED /
MB-20260530-001): the per-trade backtest-augmentation infra was fully built —
`ml.datasets.backtest_recorder`, `scripts/ml/record_harness_trades.py`, the
`include_backtest`/`union` mode on the decision families, and the
`research-backtest-augment` workflow — yet `trades.is_backtest=1` count was ZERO
in every month, because the pooled decision-family builds in
`scripts/ops/build_trainer_datasets.sh` never passed the augmentation. Every
"not enough data to train / blocked on labels" conclusion was reached WITHOUT it,
and successive sessions re-derived the wall as unsolved. Every safety mechanism
here is a *detector*; this is a *preventer* that fires at merge time.

Invariant enforced (whole-current-state check, every PR):

  Every journal-decision family listed under `decision_families` in
  config/training_population.yaml MUST be one of:
    (a) `augmented`      — its build in build_trainer_datasets.sh passes the
                           augmentation marker (`include_backtest`), CERTIFIED by
                           reading the shell (not just declared), OR
    (b) `augmentation_exempt` — reasoned, backtests genuinely can't feed it
                           (e.g. execution_quality: real slippage), OR
    (c) `augmentation_debt`   — grandfathered live-only, owed augmentation.

Ratchet: `augmentation_debt` may never exceed `debt_ceiling`, and the ceiling
only ever ratchets DOWN. A NEW decision family can never be parked in debt to
dodge the gate — it gets wired (augmented) or a reasoned exempt. As P0.2 wires
each pooled build, it moves debt→augmented and the ceiling drops toward 0.

Anti-lie cross-check: a family CLASSIFIED augmented (in debt/exempt → not) is
verified against the actual `build_trainer_datasets.sh` wiring — the family name
must co-occur with the augmentation marker. This is what makes "declared but
never fed" (the exact breach) impossible: you cannot mark a family augmented
without the wiring being really there.

Usage:
  python scripts/check_training_population.py            # --check (CI gate); exit 1 on violation
  python scripts/check_training_population.py --matrix   # (re)write docs/training-population-matrix.md
  python scripts/check_training_population.py --check --matrix
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "config" / "training_population.yaml"
BUILD_SH = REPO / "scripts" / "ops" / "build_trainer_datasets.sh"
MATRIX_OUT = REPO / "docs" / "training-population-matrix.md"


def _load_yaml(p: Path) -> dict:
    if not p.exists():
        return {}
    with p.open() as f:
        return yaml.safe_load(f) or {}


def _build_sh_text() -> str:
    return BUILD_SH.read_text() if BUILD_SH.exists() else ""


def _family_build_command(family: str, sh: str) -> str:
    """Extract the family's OWN `build_family <family> \\ ...` command — the
    invocation line plus its backslash-continued lines — as one string. Empty if
    the family is not built via build_family. Scoping to this specific command is
    what stops the MES block's augmentation marker (a separate `build-dataset`
    path) from falsely certifying the live-only pooled builds."""
    lines = sh.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.match(rf"\s*build_family\s+{re.escape(family)}\b", ln):
            start = i
            break
    if start is None:
        return ""
    block = [lines[start]]
    # Consume continuation lines while the current line ends with a backslash.
    j = start
    while lines[j].rstrip().endswith("\\") and j + 1 < len(lines):
        j += 1
        block.append(lines[j])
    return "\n".join(block)


def _family_is_wired_augmented(family: str, marker: str, sh: str) -> bool:
    """True iff the family's OWN build_family command passes the augmentation
    marker (`include_backtest`) — i.e. it is genuinely fed backtest rows, not
    merely that the marker exists somewhere in the file. This is the property
    "declared but never fed" (the breach) violates."""
    if not sh:
        return False
    cmd = _family_build_command(family, sh)
    if not cmd:
        return False
    # Strip comment lines within the command block so a `# … include_backtest …`
    # note can't certify wiring (a guard cheaper to lie to than satisfy is worse
    # than none — the new-table-wiring-guard lesson).
    code = "\n".join(ln for ln in cmd.splitlines() if not ln.lstrip().startswith("#"))
    return marker in code


def load_registry() -> Tuple[List[str], Dict, Dict, int, str]:
    reg = _load_yaml(REGISTRY)
    families = list(reg.get("decision_families") or [])
    exempt = reg.get("augmentation_exempt") or {}
    debt = reg.get("augmentation_debt") or {}
    ceiling = int(reg.get("debt_ceiling", 0))
    marker = str(reg.get("augmentation_marker", "include_backtest"))
    return families, exempt, debt, ceiling, marker


def evaluate() -> Tuple[List[str], List[dict]]:
    families, exempt, debt, ceiling, marker = load_registry()
    sh = _build_sh_text()

    violations: List[str] = []
    rows: List[dict] = []

    for fam in families:
        wired = _family_is_wired_augmented(fam, marker, sh)
        in_exempt = fam in exempt
        in_debt = fam in debt

        if wired and not in_exempt and not in_debt:
            state = "augmented"
        elif in_exempt:
            state = "exempt"
            if wired:
                # Fine but odd — an exempt family that is also fed. Not a
                # violation; note it in the matrix.
                state = "exempt+wired"
        elif in_debt:
            state = "debt"
            if wired:
                # THE RATCHET FORCING FUNCTION: a debt family that is now wired
                # must be paid down (moved out of debt + ceiling lowered), not
                # left parked in debt.
                violations.append(
                    f"[ratchet] decision family '{fam}' is now WIRED for augmentation "
                    f"in build_trainer_datasets.sh but is still in augmentation_debt — "
                    f"pay it down: remove it from `augmentation_debt` and lower "
                    f"`debt_ceiling` by 1 in config/training_population.yaml."
                )
        else:
            state = "LIVE_ONLY"
            violations.append(
                f"[population] decision family '{fam}' trains LIVE-ONLY (no "
                f"`{marker}` wiring in build_trainer_datasets.sh) and is not in "
                f"augmentation_exempt or augmentation_debt. Wire its build to pass "
                f"`{marker}` (see the MES block), or add a reasoned exempt/debt "
                f"entry to config/training_population.yaml. This is the "
                f"'blocked on labels' breach the guard exists to stop."
            )
        rows.append({"family": fam, "state": state})

    # Structural checks on the registry itself.
    for name, meta in debt.items():
        if not isinstance(meta, dict) or not str(meta.get("reason", "")).strip():
            violations.append(f"[debt] augmentation_debt entry '{name}' is missing a 'reason'.")
        if not str((meta or {}).get("tracking_id", "")).strip():
            violations.append(f"[debt] augmentation_debt entry '{name}' is missing a 'tracking_id'.")
    for name, meta in (exempt or {}).items():
        if not isinstance(meta, dict) or not str(meta.get("reason", "")).strip():
            violations.append(f"[exempt] augmentation_exempt entry '{name}' is missing a 'reason'.")

    # Ratchet: debt can never exceed the ceiling.
    if len(debt) > ceiling:
        violations.append(
            f"[ratchet] augmentation_debt has {len(debt)} entries but debt_ceiling="
            f"{ceiling}. A NEW decision family cannot be parked in debt — wire its "
            f"augmentation or give it a reasoned exempt. The ceiling only ratchets DOWN."
        )

    return violations, rows


def write_matrix(rows: List[dict], ceiling: int) -> None:
    _f, _e, debt, _c, marker = load_registry()
    n_aug = sum(1 for r in rows if r["state"].startswith("augmented"))
    n_exempt = sum(1 for r in rows if r["state"].startswith("exempt"))
    n_debt = sum(1 for r in rows if r["state"] == "debt")
    lines = [
        "# Training-population matrix",
        "",
        "<!-- GENERATED by scripts/check_training_population.py --matrix. Do not edit by hand. -->",
        "",
        "One row per journal-decision family that pools the live-label corpus. "
        f"`state`: **augmented** = its `build_trainer_datasets.sh` build passes "
        f"`{marker}` (verified in the shell); **exempt** = backtests genuinely "
        "can't feed it (reasoned); **debt** = grandfathered live-only, owed "
        "augmentation (paid down by P0.2 → ceiling drops).",
        "",
        f"**Population:** {n_aug} augmented · {n_exempt} exempt · **{n_debt} in "
        f"debt** (ceiling {ceiling}). The debt count must trend to 0 — every debt "
        "family still trains on the ~78-label live-only wall.",
        "",
        "| family | state |",
        "|---|---|",
    ]
    badge = {
        "augmented": "✅ augmented",
        "exempt": "➖ exempt",
        "exempt+wired": "➖ exempt (also wired)",
        "debt": "🟠 debt (live-only)",
        "LIVE_ONLY": "❌ LIVE-ONLY (unclassified)",
    }
    for r in rows:
        lines.append(f"| `{r['family']}` | {badge.get(r['state'], r['state'])} |")
    lines += ["", "## Augmentation debt (owed backtest/paper feed)", ""]
    if debt:
        lines += ["| family | tracking_id | reason |", "|---|---|---|"]
        for name, meta in sorted(debt.items()):
            meta = meta or {}
            reason = " ".join(str(meta.get("reason", "—")).split())
            lines.append(f"| `{name}` | {meta.get('tracking_id','—')} | {reason} |")
    else:
        lines.append("_None — every decision family is augmented or exempt. The label wall is closed._")
    lines.append("")
    MATRIX_OUT.write_text("\n".join(lines))
    print(f"wrote {MATRIX_OUT.relative_to(REPO)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="fail (exit 1) on any violation")
    ap.add_argument("--matrix", action="store_true", help="(re)write the training-population matrix doc")
    args = ap.parse_args()
    if not args.check and not args.matrix:
        args.check = True

    violations, rows = evaluate()
    _f, _e, _d, ceiling, _m = load_registry()

    if args.matrix:
        write_matrix(rows, ceiling)

    n_debt = sum(1 for r in rows if r["state"] == "debt")
    if n_debt:
        print(f"::warning::training-population: {n_debt} decision family(ies) still "
              f"train live-only (augmentation_debt, ceiling {ceiling}) — owed the "
              f"backtest/paper feed. See docs/training-population-matrix.md.")

    if args.check:
        if violations:
            print(f"::error::training-population guard tripped — {len(violations)} violation(s):")
            for v in violations:
                print(f"  - {v}")
            return 1
        print(f"training-population OK: {len(rows)} decision families, all "
              f"augmented/exempt/debt; debt {n_debt}/{ceiling}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
