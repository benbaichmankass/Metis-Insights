#!/usr/bin/env python3
"""strategy-coverage guard — the "no new loose ends when you add a strategy" gate.

Root cause it exists for: the roster grew from the original 6 BTC strategies to
~44, but `config/regime_policy.yaml` (the decision/regime layer) was NOT extended
with each new strategy, so 35 of 39 live strategies traded with no regime
protection at all — invisible until a bad day made someone look
(2026-07-16 review). Every safety mechanism in this repo is a *detector* that
fires after the fact; this is a *preventer* that fires at merge time.

Invariant enforced (whole-current-state check, every PR):

  Every `execution: live` strategy in config/strategies.yaml MUST be one of:
    (a) present in config/regime_policy.yaml           — a real regime cell, OR
    (b) listed under `exempt:` in the exemptions file  — permanent, gating N/A, OR
    (c) listed under `coverage_debt:` there            — grandfathered, owed a cell.
  AND it must have an entry in config/strategy_descriptions.json (or be listed
  under `description_exempt:`).

Ratchet: `coverage_debt` may never exceed `debt_ceiling` in the exemptions file,
and the ceiling only ever ratchets DOWN. So a NEW live strategy can never be
parked in debt to dodge the gate — it must get a real cell or a reasoned exempt.
The existing grandfathered strategies are paid down (ceiling lowered) as Phase-2
authors their cells; the system-review drives that debt toward zero.

Usage:
  python scripts/check_strategy_coverage.py            # --check (CI gate); exit 1 on violation
  python scripts/check_strategy_coverage.py --matrix   # (re)write docs/strategy-coverage-matrix.md
  python scripts/check_strategy_coverage.py --check --matrix
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Set, Tuple

import yaml

REPO = Path(__file__).resolve().parent.parent
STRATEGIES = REPO / "config" / "strategies.yaml"
REGIME_POLICY = REPO / "config" / "regime_policy.yaml"
EXEMPTIONS = REPO / "config" / "regime_coverage_exemptions.yaml"
DESCRIPTIONS = REPO / "config" / "strategy_descriptions.json"
MATRIX_OUT = REPO / "docs" / "strategy-coverage-matrix.md"


def _load_yaml(p: Path) -> dict:
    if not p.exists():
        return {}
    with p.open() as f:
        return yaml.safe_load(f) or {}


def live_strategies() -> List[str]:
    data = _load_yaml(STRATEGIES).get("strategies", {})
    return sorted(
        name
        for name, cfg in data.items()
        if isinstance(cfg, dict)
        and cfg.get("enabled")
        and str(cfg.get("execution", "live")).strip().lower() == "live"
    )


def regime_covered() -> Set[str]:
    pol = _load_yaml(REGIME_POLICY)
    covered: Set[str] = set()
    for block in ("trending", "transitional", "chop"):
        covered |= set((pol.get(block) or {}).keys())
    for _trend, vols in (pol.get("trend_vol") or {}).items():
        for _vol, cells in (vols or {}).items():
            covered |= set((cells or {}).keys())
    return covered


def description_keys() -> Set[str]:
    if not DESCRIPTIONS.exists():
        return set()
    d = json.loads(DESCRIPTIONS.read_text())
    return set(d.keys()) if isinstance(d, dict) else set()


def load_exemptions() -> Tuple[Dict, Dict, Set[str], int]:
    ex = _load_yaml(EXEMPTIONS)
    exempt = ex.get("exempt") or {}
    debt = ex.get("coverage_debt") or {}
    desc_exempt = set((ex.get("description_exempt") or {}).keys()) if isinstance(
        ex.get("description_exempt"), dict
    ) else set(ex.get("description_exempt") or [])
    ceiling = int(ex.get("debt_ceiling", 0))
    return exempt, debt, desc_exempt, ceiling


def evaluate() -> Tuple[List[str], List[dict]]:
    """Return (violations, rows). rows drive the matrix."""
    live = globals()["live_strategies"]()
    covered = globals()["regime_covered"]()
    exempt, debt, desc_exempt, ceiling = globals()["load_exemptions"]()
    desc = globals()["description_keys"]()

    violations: List[str] = []
    rows: List[dict] = []

    for name in live:
        if name in covered:
            regime_state = "cell"
        elif name in exempt:
            regime_state = "exempt"
        elif name in debt:
            regime_state = "debt"
        else:
            regime_state = "MISSING"
            violations.append(
                f"[regime] live strategy '{name}' has no regime_policy cell and is "
                f"not in exempt/coverage_debt — add a cell to config/regime_policy.yaml "
                f"or an explicit entry to config/regime_coverage_exemptions.yaml."
            )
        has_desc = name in desc or name in desc_exempt
        if not has_desc:
            violations.append(
                f"[description] live strategy '{name}' has no config/strategy_descriptions.json "
                f"entry (and is not description_exempt)."
            )
        rows.append({"name": name, "regime": regime_state, "desc": "yes" if has_desc else "MISSING"})

    # Structural checks on the exemptions file itself.
    for name, meta in debt.items():
        if not isinstance(meta, dict) or not str(meta.get("reason", "")).strip():
            violations.append(f"[debt] coverage_debt entry '{name}' is missing a 'reason'.")
        if not str((meta or {}).get("tracking_id", "")).strip():
            violations.append(f"[debt] coverage_debt entry '{name}' is missing a 'tracking_id'.")
    for name, meta in (exempt or {}).items():
        if not isinstance(meta, dict) or not str(meta.get("reason", "")).strip():
            violations.append(f"[exempt] exempt entry '{name}' is missing a 'reason'.")

    # Ratchet: debt can never exceed the ceiling.
    if len(debt) > ceiling:
        violations.append(
            f"[ratchet] coverage_debt has {len(debt)} entries but debt_ceiling={ceiling}. "
            f"A NEW live strategy cannot be parked in coverage_debt — give it a real "
            f"regime cell or a reasoned `exempt` entry. The ceiling only ratchets DOWN "
            f"as debt is paid off."
        )

    return violations, rows


def write_matrix(rows: List[dict], ceiling: int) -> None:
    _exempt, debt, _de, _c = load_exemptions()
    n_cell = sum(1 for r in rows if r["regime"] == "cell")
    n_exempt = sum(1 for r in rows if r["regime"] == "exempt")
    n_debt = sum(1 for r in rows if r["regime"] == "debt")
    lines = [
        "# Strategy coverage matrix",
        "",
        "<!-- GENERATED by scripts/check_strategy_coverage.py --matrix. Do not edit by hand. -->",
        "",
        "One row per `execution: live` strategy. `regime`: **cell** = has a "
        "`config/regime_policy.yaml` entry; **exempt** = permanently regime-gating-N/A "
        "(reasoned); **debt** = grandfathered, owed a cell (paid down by Phase-2 / the "
        "system-review). `desc` = has a `config/strategy_descriptions.json` entry.",
        "",
        f"**Coverage:** {n_cell} celled · {n_exempt} exempt · **{n_debt} in debt** "
        f"(ceiling {ceiling}). The debt count must trend to 0.",
        "",
        "| strategy | regime | desc |",
        "|---|---|---|",
    ]
    for r in rows:
        badge = {"cell": "✅ cell", "exempt": "➖ exempt", "debt": "🟠 debt", "MISSING": "❌ MISSING"}[r["regime"]]
        d = "✅" if r["desc"] == "yes" else "❌"
        lines.append(f"| `{r['name']}` | {badge} | {d} |")
    lines += ["", "## Coverage debt (owed a regime cell)", ""]
    if debt:
        lines += ["| strategy | tracking_id | reason |", "|---|---|---|"]
        for name, meta in sorted(debt.items()):
            meta = meta or {}
            lines.append(f"| `{name}` | {meta.get('tracking_id','—')} | {meta.get('reason','—')} |")
    else:
        lines.append("_None — debt fully paid off._")
    lines.append("")
    MATRIX_OUT.write_text("\n".join(lines))
    print(f"wrote {MATRIX_OUT.relative_to(REPO)}")


def self_test() -> int:
    """Plant each violation class and require main(["--check"]) to REFUSE it.

    The live config satisfies every branch of `evaluate()`, so without these
    fixtures NONE of this guard's six refusal paths would ever execute -- it
    shipped into the required `guards` merge context with no self-test and no
    test file, so its green had never been shown capable of turning red (F-05,
    2026-09-09 full-system audit).

    The bar is `main(["--check"])`'s RETURN VALUE, not its output. Every
    control below plants ONE violation class in isolation, so a probe that
    fires on the wrong thing is caught rather than credited; the final control
    removes every plant and requires 0, without which a guard that returned 1
    unconditionally would pass all the others.

    `--matrix` is never passed, so `docs/strategy-coverage-matrix.md` is not
    written. The real `config/` files are read-only here: the fixtures replace
    this module's own loaders and are restored in a `finally`.
    """
    fails: List[str] = []

    def check(label: str, got, want) -> None:
        if got != want:
            fails.append(f"  FAIL - {label}: got {got!r}, want {want!r}")
        else:
            print(f"  PASS - {label}")

    g = globals()
    real = {k: g[k] for k in
            ("live_strategies", "regime_covered", "load_exemptions", "description_keys")}

    def stage(live, covered, exempt, debt, desc_exempt, ceiling, desc):
        g["live_strategies"] = lambda: list(live)
        g["regime_covered"] = lambda: set(covered)
        g["load_exemptions"] = lambda: (exempt, debt, set(desc_exempt), ceiling)
        g["description_keys"] = lambda: set(desc)

    ok = dict(live=["s_ok"], covered={"s_ok"}, exempt={}, debt={},
              desc_exempt=set(), ceiling=0, desc={"s_ok"})
    try:
        # 0. BASELINE: a fully covered, fully described strategy passes. Every
        #    control below is this fixture with exactly one thing broken, so a
        #    failure is attributable to the plant and nothing else.
        stage(**ok)
        check("a covered, described strategy returns 0", main(["--check"]), 0)

        # 1. A live strategy with no cell and no exempt/debt entry.
        stage(**{**ok, "covered": set()})
        check("[regime] an uncelled live strategy returns 1", main(["--check"]), 1)

        # 2. A live strategy with no description entry.
        stage(**{**ok, "desc": set()})
        check("[description] a strategy with no description returns 1",
              main(["--check"]), 1)

        # 3-4. The exemptions file's OWN structure. A debt entry parked with no
        #      reason or no tracking_id is an untraceable exemption, which is
        #      the shape that turns a ratchet into a dumping ground.
        stage(**{**ok, "covered": set(),
                 "debt": {"s_ok": {"tracking_id": "MI-1"}}, "ceiling": 1})
        check("[debt] a debt entry with no reason returns 1", main(["--check"]), 1)
        stage(**{**ok, "covered": set(),
                 "debt": {"s_ok": {"reason": "r"}}, "ceiling": 1})
        check("[debt] a debt entry with no tracking_id returns 1",
              main(["--check"]), 1)

        # 5. An exempt entry with no stated reason.
        stage(**{**ok, "covered": set(), "exempt": {"s_ok": {}}})
        check("[exempt] an exempt entry with no reason returns 1",
              main(["--check"]), 1)

        # 6. THE RATCHET. Parking a NEW strategy in coverage_debt above the
        #    declared ceiling must fail -- the ceiling only ever moves down.
        stage(**{**ok, "covered": set(),
                 "debt": {"s_ok": {"reason": "r", "tracking_id": "MI-1"}},
                 "ceiling": 0})
        check("[ratchet] debt above the ceiling returns 1", main(["--check"]), 1)

        # 7. ...and the SAME fixture under a ceiling that admits it passes, so
        #    control 6 is shown to fire on the ratchet and not on the debt
        #    entry merely existing.
        stage(**{**ok, "covered": set(),
                 "debt": {"s_ok": {"reason": "r", "tracking_id": "MI-1"}},
                 "ceiling": 1})
        check("[ratchet] the same debt entry under a ceiling of 1 returns 0",
              main(["--check"]), 0)

        # 8. REMOVE EVERY PLANT: the LIVE config must still pass. This is the
        #    control that proves the guard is not simply failing everything.
        for k, v in real.items():
            g[k] = v
        check("the live config returns 0", main(["--check"]), 0)
    finally:
        for k, v in real.items():
            g[k] = v

    if fails:
        print("\n".join(fails))
        print("\nSELF-TEST FAILED")
        return 1
    print("\nALL PASS")
    return 0


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="fail (exit 1) on any violation")
    ap.add_argument("--matrix", action="store_true", help="(re)write the coverage matrix doc")
    ap.add_argument("--self-test", action="store_true",
                    help="run the planted-failure controls and exit")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    # default: --check
    if not args.check and not args.matrix:
        args.check = True

    violations, rows = evaluate()
    _e, _d, _de, ceiling = globals()["load_exemptions"]()

    if args.matrix:
        write_matrix(rows, ceiling)

    n_debt = sum(1 for r in rows if r["regime"] == "debt")
    if n_debt:
        print(f"::warning::strategy-coverage: {n_debt} live strategy(ies) in regime "
              f"coverage_debt (ceiling {ceiling}) — owed a regime cell. See "
              f"docs/strategy-coverage-matrix.md.")

    if args.check:
        if violations:
            print(f"::error::strategy-coverage guard tripped — {len(violations)} violation(s):")
            for v in violations:
                print(f"  - {v}")
            return 1
        print(f"strategy-coverage OK: {len(rows)} live strategies, all covered/exempt/debt; "
              f"debt {n_debt}/{ceiling}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
