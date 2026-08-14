#!/usr/bin/env python3
"""matrix-config-agreement — the coverage matrix must not contradict live config.

THE CLASS. `docs/research/exit-refinement-coverage.json` records, per
(leg, lever), whether an exit lever is `shipped`. `config/strategies.yaml`
decides whether it actually IS — it is the file the trader loads. Nothing
checked that the two agree, so the decision record could say a lever was never
shipped while production ran it.

MEASURED 2026-08-14 (BL-20260814-MATRIX-STATUS-CONTRADICTS-LIVE-CONFIG-ON-SIX-LEVER-CELLS):
six cells where config ARMS the lever and the matrix does not say `shipped`.
**Five of the six read `honest_negative`** — which a reader takes as *"we
measured this and it did not work"* — about a `trail_decay` running live on that
leg right now (`eth_pullback_2h`, `qqq_trend_long_1d`, `trend_donchian_sol_4h`,
`xrp_pullback_2h`, plus `avax_pullback_2h` at `execution: shadow`). The sixth,
`trend_donchian_avax_4h`, reads `passed_unshipped` and its ref literally ends
*"AWAITING TIER-3 operator approval to declare in YAML"* — the approval landed
in #8985 on 2026-08-13 and the cell was never flipped.

THE ASYMMETRY IS THE FINDING. The reverse direction was **clean**: zero cells
claimed `shipped` without a declare. Every discrepancy ran one way, which is
what a drift of *record behind reality* looks like — the declare ships through a
Tier-3 PR and the matrix update is a separate, forgettable step.

TWO CAUSES, both visible in the refs and both still live:
  * a landed declare that never updated the cell; and
  * the multileg-row explosion (BL-20260809-COVERAGE-MATRIX-MULTILEG-ROW-ONE-STATUS)
    keeping a ref's FIRST sentence ("FAIL: ... qqq ...") over a LATER sentence in
    the SAME ref recording the pass and the merge.

WHAT THIS GUARD DOES NOT DO. It does not read the matrix as authority over
config, ever. Config is the field; the matrix is prose about it, and *field
beats comment* is the repo's rule. So a disagreement is always reported as
"the RECORD is stale", never as a reason to touch a live declare — every one of
these declares is operator-approved Tier-3 work.

It also deliberately does NOT check whether the shipped VALUE matches the
evidence, or whether the evidence is at live parity. Those are
`matrix-corpus-agreement`'s job and the `tp_geometry` marker's job respectively.
Flipping a cell to `shipped` records that the lever IS shipped; it is not a
claim that it was re-graded. Merging those two claims is how a status becomes
untrustworthy, so this guard checks exactly one thing.

⚠️ NOT YET REGISTERED IN `scripts/ci/run_guards.py`, ON PURPOSE. It fails on the
current tree — those six cells are real and unreconciled — so registering it now
would turn CI red on every unrelated PR until they are fixed. Reconciling them
is NOT a mechanical sync: criterion (2) of the backlog row requires each cell's
ref to record WHICH evidence supports the shipped value, and for several the
supporting evidence is pre-2026-08-10 no-take-profit, so the flip must preserve
the `tp_geometry` marker rather than imply a re-grade. That is a judgement call
on cells that drive dispositions, so it is queued for the operator as Tier-3
decision (j) in the sprint log. **The registration block lands in the same
change as the reconciliation** — a guard whose first CI run is red teaches
everyone to skip it. Until then `tests/test_matrix_config_agreement.py` runs the
self-test, so the guard cannot silently rot while it waits.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "research"))
sys.path.insert(0, str(REPO / "scripts"))

MATRIX = REPO / "docs" / "research" / "exit-refinement-coverage.json"
STRATEGIES = REPO / "config" / "strategies.yaml"

# The matrix says a lever IS shipped only with this status. Everything else --
# honest_negative, passed_unshipped, pending, blocked:* -- asserts it is not.
SHIPPED = "shipped"


def lever_declared_keys() -> dict[str, tuple[str, ...]]:
    """Import the key sets from the sweep, never restate them.

    A second copy would be free to drift from the one that actually decides
    which levers a leg arms (`declared_levers_present`), and the failure it
    would produce -- a guard grading a lever the sweep does not recognise --
    looks exactly like a real finding.
    """
    import m20_fleet_exit_sweep as sweep  # noqa: PLC0415

    return sweep.LEVER_DECLARED_KEYS


def _arms(cfg: dict, keys: tuple[str, ...]) -> bool:
    return any(cfg.get(k) is not None for k in keys)


def disagreements(matrix: dict, strategies: dict,
                  keys: dict[str, tuple[str, ...]]) -> list[dict]:
    """Cells where config and the matrix disagree about shipped-ness.

    Rows whose `strategy` is absent from strategies.yaml are SKIPPED and
    counted, not silently dropped: the matrix carries aggregate roll-up rows
    (e.g. `shadow fleet (...)` / symbol `various`) that are not legs and cannot
    be resolved against config. Dropping them quietly would understate the
    denominator; treating them as legs would manufacture findings.
    """
    out: list[dict] = []
    for row in matrix.get("rows", []):
        leg = row.get("strategy")
        cfg = strategies.get(leg)
        if not isinstance(cfg, dict):
            continue
        for lever, lever_keys in keys.items():
            cell = row.get(lever)
            if not isinstance(cell, dict):
                continue
            status = cell.get("status")
            if status is None:
                continue
            armed = _arms(cfg, lever_keys)
            if armed and status != SHIPPED:
                out.append({"leg": leg, "lever": lever, "status": status,
                            "direction": "config_arms_matrix_denies",
                            "execution": cfg.get("execution", "live"),
                            "keys": [k for k in lever_keys if cfg.get(k) is not None]})
            elif status == SHIPPED and not armed:
                out.append({"leg": leg, "lever": lever, "status": status,
                            "direction": "matrix_claims_shipped_config_silent",
                            "execution": cfg.get("execution", "live"),
                            "keys": []})
    return out


def unresolvable_rows(matrix: dict, strategies: dict) -> list[str]:
    return [r.get("strategy") for r in matrix.get("rows", [])
            if not isinstance(strategies.get(r.get("strategy")), dict)]


def _load() -> tuple[dict, dict]:
    import yaml  # noqa: PLC0415

    matrix = json.loads(MATRIX.read_text())
    raw = yaml.safe_load(STRATEGIES.read_text())
    return matrix, raw.get("strategies", raw)


def _self_test() -> int:
    keys = {"trail_decay": ("trail_decay_stall_bars", "trail_decay_tight_mult")}
    ok = True

    def check(label: str, cond: bool) -> None:
        nonlocal ok
        print(f"  {label}: {'PASS' if cond else 'FAIL'}")
        ok = ok and cond

    print("matrix-config-agreement self-test")

    # 1. The measured shape: config arms it, the matrix says honest_negative.
    m = {"rows": [{"strategy": "leg_a",
                   "trail_decay": {"status": "honest_negative"}}]}
    s = {"leg_a": {"trail_decay_stall_bars": 6, "trail_decay_tight_mult": 2.5,
                   "execution": "live"}}
    d = disagreements(m, s, keys)
    check("1 (config arms it while the matrix denies -> flagged, with the "
          "status named)",
          len(d) == 1 and d[0]["status"] == "honest_negative"
          and d[0]["direction"] == "config_arms_matrix_denies")

    # 2. Agreement is silent.
    m2 = {"rows": [{"strategy": "leg_a", "trail_decay": {"status": "shipped"}}]}
    check("2 (matrix `shipped` + config arms it -> clean)",
          disagreements(m2, s, keys) == [])

    # 3. The REVERSE direction is caught too. It was clean in the live data, and
    #    a guard that only ever checked one direction would report that clean as
    #    evidence when it had never looked.
    s3 = {"leg_a": {"execution": "live"}}
    d3 = disagreements(m2, s3, keys)
    check("3 (matrix claims shipped while config is silent -> flagged)",
          len(d3) == 1
          and d3[0]["direction"] == "matrix_claims_shipped_config_silent")

    # 4. A lever the leg never armed, recorded as not-shipped, is CORRECT --
    #    that is `passed_unshipped` working as intended, and flagging it would
    #    bury the real findings in noise.
    m4 = {"rows": [{"strategy": "leg_a",
                    "trail_decay": {"status": "passed_unshipped"}}]}
    check("4 (not armed + not shipped -> clean, not a finding)",
          disagreements(m4, s3, keys) == [])

    # 5. An aggregate row that is not a leg is skipped, and SAYS it was skipped.
    m5 = {"rows": [{"strategy": "shadow fleet (a, b, c)",
                    "trail_decay": {"status": "honest_negative"}}]}
    check("5 (a non-leg roll-up row is skipped, and counted as unresolvable)",
          disagreements(m5, s, keys) == []
          and unresolvable_rows(m5, s) == ["shadow fleet (a, b, c)"])

    # 6. A `None` value is not an arm. YAML `trail_decay_stall_bars:` with no
    #    value parses to None, which is the lever being explicitly OFF.
    s6 = {"leg_a": {"trail_decay_stall_bars": None, "execution": "live"}}
    check("6 (an explicit null is not an arm)",
          disagreements(m, s6, keys) == [])

    print("self-test OK — catches both directions, stays silent on agreement, "
          "and never grades a non-leg row."
          if ok else "self-test FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return _self_test()

    matrix, strategies = _load()
    keys = lever_declared_keys()
    found = disagreements(matrix, strategies, keys)
    skipped = unresolvable_rows(matrix, strategies)

    rows = len(matrix.get("rows", []))
    print(f"matrix-config-agreement: {rows} matrix rows, "
          f"{rows - len(skipped)} resolved to a leg in strategies.yaml, "
          f"{len(skipped)} aggregate/unresolvable "
          f"({', '.join(skipped) if skipped else 'none'}); "
          f"{len(keys)} offerable levers checked.")

    if not found:
        print("OK — every matrix cell agrees with config on shipped-ness.")
        return 0

    print("\n::error::the coverage matrix contradicts config/strategies.yaml. "
          "Config is the field the trader loads; the matrix is prose about it, "
          "so the RECORD is what is wrong here — do NOT touch a declare to "
          "satisfy this guard.")
    for d in sorted(found, key=lambda x: (x["direction"], x["leg"], x["lever"])):
        if d["direction"] == "config_arms_matrix_denies":
            print(f"  {d['leg']} / {d['lever']}: config arms it "
                  f"({', '.join(d['keys'])}) on an `execution: {d['execution']}` "
                  f"leg, but the matrix says `{d['status']}`"
                  # BOTH conditions, because the annotation asserts BOTH. An
                  # earlier draft keyed only on the status and printed "about a
                  # live lever" over `avax_pullback_2h`, which is
                  # `execution: shadow` -- a label naming something the code had
                  # not checked, which is the exact sub-class A defect this
                  # repo's diagnostic-provenance rule exists to stop.
                  + ("  <-- reads as 'measured, did not work' about a lever "
                     "running LIVE on that leg"
                     if d["status"] == "honest_negative"
                     and d["execution"] == "live" else ""))
        else:
            print(f"  {d['leg']} / {d['lever']}: the matrix says `shipped` but "
                  f"config arms no key for it")
    print("\nFix: reconcile the cell's status against config and record WHICH "
          "evidence supports the shipped value. Flipping a cell to `shipped` "
          "records that the lever IS shipped — it is NOT a claim that it was "
          "re-graded at live parity, so keep any `tp_geometry` marker.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
