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

  AND every `execution: live` strategy MUST have an entry in
  `src/runtime/intents.py::DEFAULT_PRIORITIES` (or be listed under
  `priority_exempt:`), AND `_UNKNOWN_STRATEGY_PRIORITY` MUST be strictly below
  `min(DEFAULT_PRIORITIES.values())`.

The last two are the arbitration half, added 2026-09-12 for
BL-20260909-UNKNOWN-STRATEGY-PRIORITY-NOW-BEATS-45-OF-50-DECLARED-LEGS-AND-THE-CONTENTION-IS-LIVE
(audit F-29, itself F-32 from 2026-08-20 unremediated). `_UNKNOWN_STRATEGY_PRIORITY`
is the priority an UNLISTED strategy resolves to, and its own comment says it was
"picked deliberately below the in-scope strategies so a misconfigured new strategy
never silently overrides Turtle Soup / VWAP". Measured 2026-09-12 the map holds
n=50 with min 0 and the constant is 10, so 45 of 50 legs sit BELOW the fallback and
omission WINS the arbitration -- the exact inverse of the stated purpose. The second
invariant is the transferable one: it is the generic detector for *a default whose
fail-safety depends on a distribution that has since moved*, and the distribution
moved here without anyone editing the constant.

Ratchet: `coverage_debt` may never exceed `debt_ceiling` in the exemptions file,
`priority_exempt` may never exceed `priority_exempt_ceiling`, and both ceilings
only ever ratchet DOWN. So a NEW live strategy can never be
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
import ast
import json
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import yaml

REPO = Path(__file__).resolve().parent.parent
STRATEGIES = REPO / "config" / "strategies.yaml"
REGIME_POLICY = REPO / "config" / "regime_policy.yaml"
EXEMPTIONS = REPO / "config" / "regime_coverage_exemptions.yaml"
DESCRIPTIONS = REPO / "config" / "strategy_descriptions.json"
INTENTS = REPO / "src" / "runtime" / "intents.py"
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


# --- the arbitration half (2026-09-12) -------------------------------------
#
# READ STATES, never collapsed. "we could not look" is not "it is fine": a
# constant whose fail-safety we could not read is graded as a VIOLATION below,
# not as a pass. That is the whole reason this returns a state string instead
# of a bool -- an AST that stops finding `DEFAULT_PRIORITIES` (renamed, moved
# behind a function, built from a comprehension) would otherwise silently turn
# this guard into a no-op that keeps printing OK.
PRIORITY_READ_STATES = (
    "read",                    # both names found and literal-evaluated
    "no_intents_file",         # src/runtime/intents.py is absent
    "no_priority_map",         # DEFAULT_PRIORITIES not found at module level
    "no_unknown_constant",     # _UNKNOWN_STRATEGY_PRIORITY not found
    "empty_priority_map",      # found, but empty -- min() is undefined
    "non_literal",             # found, but not a literal we can evaluate
    "unparseable",             # the file does not parse
)


def read_priority_map() -> Tuple[Optional[Dict[str, int]], Optional[int], str]:
    """Return (DEFAULT_PRIORITIES, _UNKNOWN_STRATEGY_PRIORITY, read_state).

    Read by AST, never by import: importing `src.runtime.intents` from a CI
    guard would drag the live runtime's dependency tree into the guard job and
    make a guard failure indistinguishable from an import error.

    Both names are module-level `AnnAssign` today (`DEFAULT_PRIORITIES: Dict[
    str, int] = {...}`), which a walker that only handles `ast.Assign` misses
    entirely -- returning None and reading exactly like "the map is gone".
    Both node types are handled, and the self-test plants each read state.
    """
    if not INTENTS.exists():
        return None, None, "no_intents_file"
    try:
        tree = ast.parse(INTENTS.read_text())
    except SyntaxError:
        return None, None, "unparseable"

    raw: Dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name) and t.id in ("DEFAULT_PRIORITIES", "_UNKNOWN_STRATEGY_PRIORITY"):
                raw[t.id] = node.value

    if "DEFAULT_PRIORITIES" not in raw:
        return None, None, "no_priority_map"
    if "_UNKNOWN_STRATEGY_PRIORITY" not in raw:
        return None, None, "no_unknown_constant"
    try:
        prios = ast.literal_eval(raw["DEFAULT_PRIORITIES"])
        const = ast.literal_eval(raw["_UNKNOWN_STRATEGY_PRIORITY"])
    except (ValueError, TypeError, SyntaxError):
        return None, None, "non_literal"
    if not isinstance(prios, dict) or not isinstance(const, int):
        return None, None, "non_literal"
    if not prios:
        return {}, const, "empty_priority_map"
    return prios, const, "read"


def load_priority_exemptions() -> Tuple[Dict, int, Optional[Dict]]:
    """Return (priority_exempt, priority_exempt_ceiling, priority_inversion_waiver)."""
    ex = _load_yaml(EXEMPTIONS)
    pe = ex.get("priority_exempt") or {}
    ceiling = int(ex.get("priority_exempt_ceiling", 0))
    waiver = ex.get("priority_inversion_waiver")
    return (pe if isinstance(pe, dict) else {}), ceiling, (waiver if isinstance(waiver, dict) else None)


def evaluate() -> Tuple[List[str], List[dict]]:
    """Return (violations, rows). rows drive the matrix."""
    live = globals()["live_strategies"]()
    covered = globals()["regime_covered"]()
    exempt, debt, desc_exempt, ceiling = globals()["load_exemptions"]()
    desc = globals()["description_keys"]()
    prios, unknown_prio, read_state = globals()["read_priority_map"]()
    prio_exempt, prio_ceiling, waiver = globals()["load_priority_exemptions"]()

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

        # Invariant 4 -- arbitration priority coverage. Only gradeable if the
        # map was actually read; an ungradeable read is reported ONCE below
        # rather than as 44 identical per-strategy violations.
        if read_state != "read":
            prio_state = "ungradeable"
        elif name in prios:
            prio_state = "mapped"
        elif name in prio_exempt:
            prio_state = "exempt"
        else:
            prio_state = "MISSING"
            violations.append(
                f"[priority] live strategy '{name}' has no "
                f"src/runtime/intents.py::DEFAULT_PRIORITIES entry and is not in "
                f"`priority_exempt` — an unlisted leg falls back to "
                f"_UNKNOWN_STRATEGY_PRIORITY, so its arbitration rank is decided by a "
                f"constant nobody chose for it. Add the entry, or add a dated, reasoned "
                f"`priority_exempt` entry to config/regime_coverage_exemptions.yaml."
            )

        rows.append({"name": name, "regime": regime_state,
                     "desc": "yes" if has_desc else "MISSING", "prio": prio_state})

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

    violations.extend(
        globals()["evaluate_arbitration"](prios, unknown_prio, read_state,
                                          prio_exempt, prio_ceiling, waiver)
    )
    return violations, rows


def evaluate_arbitration(prios, unknown_prio, read_state, prio_exempt, prio_ceiling,
                         waiver) -> List[str]:
    """Invariant 5 (the distribution assertion) + the structure that supports it.

    Kept as its own function because it is the TRANSFERABLE half of this guard:
    the shape is "a default whose fail-safety depends on a distribution that has
    since moved", and nothing about it is specific to regime coverage.
    """
    out: List[str] = []

    # An unreadable map is a VIOLATION, never a pass. If this guard cannot find
    # the constant it is meant to be watching, the honest report is that it is
    # not watching it.
    if read_state != "read":
        out.append(
            f"[priority] could not read src/runtime/intents.py: read_state={read_state!r}. "
            f"This guard grades DEFAULT_PRIORITIES and _UNKNOWN_STRATEGY_PRIORITY by AST; "
            f"if either has been renamed, moved out of module scope, or built from a "
            f"non-literal expression, update read_priority_map() rather than leaving the "
            f"arbitration invariants silently ungraded."
        )
        return out

    # Structure of the priority_exempt register itself.
    for name, meta in prio_exempt.items():
        meta = meta if isinstance(meta, dict) else {}
        for field in ("reason", "tracking_id", "added"):
            if not str(meta.get(field, "")).strip():
                out.append(f"[priority-exempt] entry '{name}' is missing '{field}'.")
        # Self-clearing: an exemption for a leg that now HAS an entry is stale,
        # and a stale exemption is what lets the next real gap hide behind it.
        if name in prios:
            out.append(
                f"[priority-exempt] entry '{name}' is stale — it now HAS a "
                f"DEFAULT_PRIORITIES entry ({prios[name]}). Delete the exemption."
            )
    if len(prio_exempt) > prio_ceiling:
        out.append(
            f"[priority-ratchet] priority_exempt has {len(prio_exempt)} entries but "
            f"priority_exempt_ceiling={prio_ceiling}. A NEW live leg cannot be parked "
            f"here; give it a DEFAULT_PRIORITIES entry. The ceiling only ratchets DOWN."
        )

    # INVARIANT 5 -- the distribution assertion.
    #
    # read_priority_map() never returns ("read", {}) -- an empty map is its own
    # state. This is belt-and-braces for a future refactor: min() on an empty
    # map raises, and a guard that CRASHES reports "the guard is broken", not
    # "the invariant is violated". Found by the self-test, which staged exactly
    # that pair before the fixture was corrected.
    if not prios:
        out.append(
            "[priority] DEFAULT_PRIORITIES read as EMPTY while read_state='read'. "
            "min() is undefined, so the distribution assertion cannot be graded — "
            "this is 'we could not look', not 'the constant is safely below "
            "everything'."
        )
        return out
    mn = min(prios.values())
    inverted = not (unknown_prio < mn)

    if waiver is None:
        if inverted:
            out.append(
                f"[priority-inversion] _UNKNOWN_STRATEGY_PRIORITY={unknown_prio} is NOT "
                f"strictly below min(DEFAULT_PRIORITIES.values())={mn} "
                f"({sum(1 for v in prios.values() if v < unknown_prio)} of {len(prios)} legs "
                f"sit below it), so an UNLISTED strategy WINS the arbitration instead of "
                f"losing it — the inverse of the constant's stated purpose. Lower the "
                f"constant below {mn}."
            )
        return out

    # A waiver exists. It excuses the MEASURED STATE it was written against, not
    # the invariant -- so it must be structurally complete, must still be needed,
    # and must still be describing reality.
    for field in ("reason", "tracking_id", "added", "observed_constant", "observed_min"):
        if field in ("observed_constant", "observed_min"):
            if not isinstance(waiver.get(field), int):
                out.append(
                    f"[priority-waiver] priority_inversion_waiver.{field} must be an int "
                    f"pinning the state the waiver was written against."
                )
        elif not str(waiver.get(field, "")).strip():
            out.append(f"[priority-waiver] priority_inversion_waiver is missing '{field}'.")
    if out:
        return out

    if not inverted:
        out.append(
            f"[priority-waiver] the inversion is FIXED (_UNKNOWN_STRATEGY_PRIORITY="
            f"{unknown_prio} < min={mn}) but priority_inversion_waiver is still present. "
            f"Delete it from config/regime_coverage_exemptions.yaml — a waiver that "
            f"outlives its condition is how the next inversion gets waived by accident."
        )
        return out

    if waiver["observed_constant"] != unknown_prio or waiver["observed_min"] != mn:
        out.append(
            f"[priority-waiver] the distribution has MOVED since the waiver was written: "
            f"waiver pins (constant={waiver['observed_constant']}, min="
            f"{waiver['observed_min']}), actual is (constant={unknown_prio}, min={mn}). "
            f"The waiver excuses the state it was measured against, not the invariant. "
            f"Fix the constant, or re-measure and re-justify the pin."
        )
        return out

    print(
        f"::warning::strategy-coverage: arbitration priority is INVERTED and WAIVED — "
        f"_UNKNOWN_STRATEGY_PRIORITY={unknown_prio} vs min={mn}, "
        f"{sum(1 for v in prios.values() if v < unknown_prio)} of {len(prios)} legs below it. "
        f"Waived under {waiver['tracking_id']} since {waiver['added']}. An unlisted leg WINS "
        f"arbitration until the constant is lowered."
    )
    return out


def write_matrix(rows: List[dict], ceiling: int) -> None:
    _exempt, debt, _de, _c = load_exemptions()
    n_cell = sum(1 for r in rows if r["regime"] == "cell")
    n_exempt = sum(1 for r in rows if r["regime"] == "exempt")
    n_debt = sum(1 for r in rows if r["regime"] == "debt")
    n_prio_mapped = sum(1 for r in rows if r["prio"] == "mapped")
    n_prio_exempt = sum(1 for r in rows if r["prio"] == "exempt")
    n_prio_ungrad = sum(1 for r in rows if r["prio"] == "ungradeable")
    prios, unknown_prio, _rs = read_priority_map()
    _pe, prio_ceiling, waiver = load_priority_exemptions()
    prio_min = min(prios.values()) if prios else None
    lines = [
        "# Strategy coverage matrix",
        "",
        "<!-- GENERATED by scripts/check_strategy_coverage.py --matrix. Do not edit by hand. -->",
        "",
        "One row per `execution: live` strategy. `regime`: **cell** = has a "
        "`config/regime_policy.yaml` entry; **exempt** = permanently regime-gating-N/A "
        "(reasoned); **debt** = grandfathered, owed a cell (paid down by Phase-2 / the "
        "system-review). `desc` = has a `config/strategy_descriptions.json` entry. "
        "`prio` = has a `src/runtime/intents.py::DEFAULT_PRIORITIES` entry (**mapped**), "
        "or a dated `priority_exempt` entry (**exempt** — owed one, not excused from "
        "one), or the map could not be read (**ungradeable**).",
        "",
        f"**Coverage:** {n_cell} celled · {n_exempt} exempt · **{n_debt} in debt** "
        f"(ceiling {ceiling}). The debt count must trend to 0.",
        "",
        f"**Arbitration priority:** {n_prio_mapped} mapped · **{n_prio_exempt} unmapped** "
        f"(ceiling {prio_ceiling}) · {n_prio_ungrad} ungradeable. "
        + (
            f"⚠️ `_UNKNOWN_STRATEGY_PRIORITY` = {unknown_prio} is NOT below "
            f"`min(DEFAULT_PRIORITIES.values())` = {prio_min}, so an unlisted leg **wins** "
            f"arbitration. Waived under `{waiver['tracking_id']}`."
            if waiver and unknown_prio is not None and prio_min is not None
            and not (unknown_prio < prio_min)
            else ""
        ),
        "",
        "| strategy | regime | desc | prio |",
        "|---|---|---|---|",
    ]
    for r in rows:
        badge = {"cell": "✅ cell", "exempt": "➖ exempt", "debt": "🟠 debt", "MISSING": "❌ MISSING"}[r["regime"]]
        d = "✅" if r["desc"] == "yes" else "❌"
        pbadge = {"mapped": "✅ mapped", "exempt": "🟠 unmapped",
                  "ungradeable": "❓ ungradeable", "MISSING": "❌ MISSING"}[r["prio"]]
        lines.append(f"| `{r['name']}` | {badge} | {d} | {pbadge} |")
    lines += ["", "## Unmapped arbitration priority (owed a DEFAULT_PRIORITIES entry)", ""]
    if _pe:
        lines += ["| strategy | tracking_id | added | reason |", "|---|---|---|---|"]
        for name, meta in sorted(_pe.items()):
            meta = meta or {}
            reason = " ".join(str(meta.get("reason", "—")).split())
            lines.append(f"| `{name}` | {meta.get('tracking_id','—')} | "
                         f"{meta.get('added','—')} | {reason} |")
    else:
        lines.append("_None — every live leg carries an arbitration priority._")

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
            ("live_strategies", "regime_covered", "load_exemptions", "description_keys",
             "read_priority_map", "load_priority_exemptions")}

    def stage(live, covered, exempt, debt, desc_exempt, ceiling, desc,
              prios, unknown_prio, read_state, prio_exempt, prio_ceiling, waiver):
        g["live_strategies"] = lambda: list(live)
        g["regime_covered"] = lambda: set(covered)
        g["load_exemptions"] = lambda: (exempt, debt, set(desc_exempt), ceiling)
        g["description_keys"] = lambda: set(desc)
        g["read_priority_map"] = lambda: (prios, unknown_prio, read_state)
        g["load_priority_exemptions"] = lambda: (prio_exempt, prio_ceiling, waiver)

    # A well-formed waiver for the fixture distribution below: the fixture is
    # INVERTED (constant 10, min 0) exactly like the live tree, so the waiver
    # controls exercise the same arithmetic the live config takes.
    WAIVER = {"tracking_id": "WO-1", "added": "2026-09-12", "reason": "r",
              "observed_constant": 10, "observed_min": 0}

    ok = dict(live=["s_ok"], covered={"s_ok"}, exempt={}, debt={},
              desc_exempt=set(), ceiling=0, desc={"s_ok"},
              prios={"s_ok": 0}, unknown_prio=-1, read_state="read",
              prio_exempt={}, prio_ceiling=0, waiver=None)
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

        # ---- INVARIANT 4: arbitration priority coverage -------------------
        # 9. A live leg absent from DEFAULT_PRIORITIES with no exemption. The
        #    map is NON-EMPTY and simply does not contain this leg, so "absent
        #    from the map" is not conflated with "the map is empty" (which is
        #    its own state, control 33).
        stage(**{**ok, "prios": {"other": 0}})
        check("[priority] an unmapped live leg returns 1", main(["--check"]), 1)

        # 10. ...and the SAME fixture WITH a dated exemption passes, so control
        #     9 is shown to fire on the gap and not on the leg merely existing.
        stage(**{**ok, "prios": {"other": 0},
                 "prio_exempt": {"s_ok": {"reason": "r", "tracking_id": "T",
                                          "added": "2026-09-12"}},
                 "prio_ceiling": 1})
        check("[priority] the same leg under a dated exemption returns 0",
              main(["--check"]), 0)

        # 11-13. Each mandatory field of a priority_exempt entry, one at a time.
        for field in ("reason", "tracking_id", "added"):
            meta = {"reason": "r", "tracking_id": "T", "added": "2026-09-12"}
            meta.pop(field)
            stage(**{**ok, "prios": {"other": 0},
                     "prio_exempt": {"s_ok": meta}, "prio_ceiling": 1})
            check(f"[priority-exempt] an entry with no {field!r} returns 1",
                  main(["--check"]), 1)

        # 14. STALE exemption: the leg now HAS a map entry, so the exemption is
        #     dead weight that would hide the next real gap behind it.
        stage(**{**ok,
                 "prio_exempt": {"s_ok": {"reason": "r", "tracking_id": "T",
                                          "added": "2026-09-12"}},
                 "prio_ceiling": 1})
        check("[priority-exempt] a stale exemption (leg is mapped) returns 1",
              main(["--check"]), 1)

        # 15. THE RATCHET. Parking a leg above the declared ceiling must fail.
        stage(**{**ok, "prios": {"other": 0},
                 "prio_exempt": {"s_ok": {"reason": "r", "tracking_id": "T",
                                          "added": "2026-09-12"}},
                 "prio_ceiling": 0})
        check("[priority-ratchet] an exemption above the ceiling returns 1",
              main(["--check"]), 1)

        # ---- INVARIANT 5: the distribution assertion ----------------------
        # 16. The finding itself: the constant is NOT below the map's minimum,
        #     so an unlisted leg wins. No waiver -> refuse.
        stage(**{**ok, "unknown_prio": 10, "prios": {"s_ok": 0}})
        check("[priority-inversion] constant not below min returns 1",
              main(["--check"]), 1)

        # 17. ...and the same fixture with the constant BELOW the minimum
        #     passes, so control 16 is shown to fire on the inversion and not
        #     on the constant merely being read.
        stage(**{**ok, "unknown_prio": -1, "prios": {"s_ok": 0}})
        check("[priority-inversion] constant below min returns 0",
              main(["--check"]), 0)

        # 18. EQUALITY IS NOT SAFE. `strictly below` is the criterion: at
        #     constant == min an unlisted leg TIES the lowest mapped leg and
        #     the tiebreak decides, which is not "loses".
        stage(**{**ok, "unknown_prio": 0, "prios": {"s_ok": 0}})
        check("[priority-inversion] constant EQUAL to min returns 1",
              main(["--check"]), 1)

        # 19. A correctly pinned waiver over the inverted fixture passes.
        stage(**{**ok, "unknown_prio": 10, "prios": {"s_ok": 0}, "waiver": WAIVER})
        check("[priority-waiver] a correctly pinned waiver returns 0",
              main(["--check"]), 0)

        # 20-21. THE PIN. The waiver excuses the state it was measured against,
        #        not the invariant — so ANY movement in either term re-arms it.
        stage(**{**ok, "unknown_prio": 11, "prios": {"s_ok": 0}, "waiver": WAIVER})
        check("[priority-waiver] the constant moved off the pin returns 1",
              main(["--check"]), 1)
        stage(**{**ok, "unknown_prio": 10, "prios": {"s_ok": 1}, "waiver": WAIVER})
        check("[priority-waiver] the map minimum moved off the pin returns 1",
              main(["--check"]), 1)

        # 22. SELF-CLEARING. Once the inversion is fixed the waiver must GO —
        #     a waiver that outlives its condition silently excuses the next
        #     inversion. This is the property that makes deleting it cheaper
        #     than keeping it.
        stage(**{**ok, "unknown_prio": -1, "prios": {"s_ok": 0}, "waiver": WAIVER})
        check("[priority-waiver] a waiver left behind after the fix returns 1",
              main(["--check"]), 1)

        # 23-27. Every mandatory waiver field, one at a time. The two pinned
        #        numbers are typed, so a waiver pinning "10" as a string — which
        #        can never equal an int and would otherwise fail confusingly —
        #        is refused for the right reason.
        for field in ("reason", "tracking_id", "added"):
            w = dict(WAIVER)
            w.pop(field)
            stage(**{**ok, "unknown_prio": 10, "prios": {"s_ok": 0}, "waiver": w})
            check(f"[priority-waiver] a waiver with no {field!r} returns 1",
                  main(["--check"]), 1)
        for field in ("observed_constant", "observed_min"):
            w = dict(WAIVER)
            w[field] = str(w[field])
            stage(**{**ok, "unknown_prio": 10, "prios": {"s_ok": 0}, "waiver": w})
            check(f"[priority-waiver] a waiver whose {field!r} is not an int returns 1",
                  main(["--check"]), 1)

        # ---- READ STATES: "we could not look" is never a pass -------------
        # 28-32. If this guard cannot find the constant it is meant to be
        #        watching, the honest report is that it is not watching it.
        #        Without these, a rename in intents.py would turn the whole
        #        arbitration half into a no-op that keeps printing OK.
        for st in ("no_intents_file", "no_priority_map", "no_unknown_constant",
                   "non_literal", "unparseable"):
            stage(**{**ok, "prios": None, "unknown_prio": None, "read_state": st})
            check(f"[priority] read_state={st!r} returns 1", main(["--check"]), 1)

        # 33. An EMPTY map is its own state: min() is undefined, so it is not
        #     "the constant is safely below everything".
        stage(**{**ok, "prios": {}, "unknown_prio": 10, "read_state": "empty_priority_map"})
        check("[priority] read_state='empty_priority_map' returns 1",
              main(["--check"]), 1)

        # 33b. ...and the same empty map mislabelled 'read' is refused too,
        #      rather than crashing on min() of an empty sequence. A guard that
        #      CRASHES reports "the guard is broken", not "the invariant is
        #      violated", and the two get triaged very differently.
        stage(**{**ok, "prios": {}, "unknown_prio": 10, "read_state": "read"})
        check("[priority] an empty map mislabelled read returns 1",
              main(["--check"]), 1)

        # 34. THE AST WALK, against the REAL src/runtime/intents.py. Both names
        #     are module-level AnnAssign (`DEFAULT_PRIORITIES: Dict[str, int] =
        #     {...}`), which a walker handling only ast.Assign misses — and a
        #     miss returns None, which reads exactly like "the map is gone".
        #     Every control above stages this loader, so without this one the
        #     real reader would never execute at all.
        for k, v in real.items():
            g[k] = v
        _prios, _const, _st = read_priority_map()
        check("read_priority_map reads the real intents.py", _st, "read")
        check("...and finds a non-empty DEFAULT_PRIORITIES",
              bool(_prios) and isinstance(_prios, dict), True)
        check("...and finds an int _UNKNOWN_STRATEGY_PRIORITY",
              isinstance(_const, int), True)

        # 35. REMOVE EVERY PLANT: the LIVE config must still pass. This is the
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
