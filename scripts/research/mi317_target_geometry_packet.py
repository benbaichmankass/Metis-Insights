#!/usr/bin/env python3
"""MI-317 — assemble the per-leg target-geometry decision packet.

This unit COMMISSIONS NOTHING. It joins evidence that already exists:

  * ``config/strategies.yaml``            — what each leg declares
  * ``config/accounts.yaml``              — where each leg's orders go (real money or not)
  * ``docs/research/mi307-offline-mfe-2026-09-18.json``   — MI-307, the 19 clamping-family legs
  * ``docs/research/mi312-scalp-target-arms-2026-09-18.json`` — MI-312, the 8 non-clamping legs
  * ``docs/research/e35-bracket-corpus.jsonl``            — the ``tp_r`` sweep, 41 legs
  * ``docs/research/exit-refinement-coverage.json``       — the shipped/unshipped bracket status

It IMPORTS rather than re-derives every definition it uses -- ``tp_venue_cap``
for the clamp and the clamping families, ``m20_fleet_exit_sweep.classify`` for
the leg->family resolution -- so a packet figure cannot drift from the
instrument that produced it.

``--selftest`` runs the checks, four of which are NEGATIVE CONTROLS that fail if
the rule they guard is deleted.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.runtime.tp_venue_cap import (  # noqa: E402
    CLAMPING_FAMILIES,
    CLAMPING_UNIT_MODULES,
    TP_VENUE_CAP_PCT,
    unit_module_clamps,
)

# ⚠️ BOUND EAGERLY, AND THE ORDER IS LOAD-BEARING. ``m20_fleet_exit_sweep``
# inserts ``<repo>/scripts`` at ``sys.path[0]`` on import, after which
# ``scripts/ml/`` SHADOWS the real top-level ``ml`` package -- so any import of
# ``src.runtime.pipeline`` attempted afterwards dies on ``No module named
# 'ml.datasets'``. Resolving this symbol before the family resolver is ever
# touched is what keeps the two usable in one process.
# Filed: BL-20260918-SCRIPTS-ON-SYS-PATH-SHADOWS-THE-ML-PACKAGE.
from src.runtime.pipeline import monitor_unit_for  # noqa: E402

MI307 = ROOT / "docs/research/mi307-offline-mfe-2026-09-18.json"
MI312 = ROOT / "docs/research/mi312-scalp-target-arms-2026-09-18.json"
CORPUS = ROOT / "docs/research/e35-bracket-corpus.jsonl"
COVERAGE = ROOT / "docs/research/exit-refinement-coverage.json"
SENTINEL_TP_R = 50.0

# A cell carrying a timeout component is UNSHIPPABLE BY CONSTRUCTION: no live
# trend/pullback/squeeze unit implements a bar-count exit.
# BL-20260829-HARNESS-FORCE-CLOSES-TREND-PULLBACK-TRADES-ON-BAR-COUNT-AND-LIVE-NEVER-DOES,
# and the selection rule of e35-passed-unshipped-proposal-2026-08-31.md § 1.
TIMEOUT_MARKER = "_to"
# path_b is the WEAKER route -- it asks only for net_R across folds, and both
# cells it has ever named FAILED gate_is_passed/gate_oos_passed on maxdd_worse
# (same memo, § 3). The two are counted apart, never pooled.
WF_STRONG = "wf_pass"
WF_WEAK = "path_b_wf_pass"


def _yaml(path):
    import yaml

    return yaml.safe_load((ROOT / path).read_text())


def load_legs():
    return _yaml("config/strategies.yaml")["strategies"]


def load_routing():
    """leg -> {accounts, real_money_accounts, prop_accounts}. Only accounts that
    are themselves ``mode: live`` can route an order, so a dry_run account is
    recorded but never counted as exposure."""
    accounts = _yaml("config/accounts.yaml")["accounts"]
    out = defaultdict(lambda: {"accounts": [], "real_money": [], "prop": []})
    for aid, cfg in accounts.items():
        live = (cfg.get("mode") or "").strip() == "live"
        klass = (cfg.get("account_class") or "").strip()
        for leg in cfg.get("strategies") or []:
            out[leg]["accounts"].append(aid)
            if not live:
                continue
            if klass == "real_money":
                out[leg]["real_money"].append(aid)
            elif klass == "prop":
                out[leg]["prop"].append(aid)
    return out


def family_of(leg_name):
    """Resolve a leg's family through the ONE resolver the sweeps use.

    ``classify`` takes the NAME ONLY and returns ``str | None``. This is not
    defended with a broad ``except``: an earlier draft wrapped it in one, passed
    the wrong arity, and every one of the 55 legs silently resolved ``None`` --
    so the packet reported that no leg is clamped while importing the very
    frozenset that says four families are. A resolver that cannot answer must
    raise, because 'we could not look' is what a bare except turns into 'there
    is nothing there'.
    """
    from scripts.research.m20_fleet_exit_sweep import classify

    return classify(leg_name)


def unit_of(leg_name):
    """Resolve a leg to the unit module that owns its ``monitor()``.

    ``monitor_unit_for`` is the ONE resolver the order-monitor itself uses, and
    it is imported rather than approximated. It is NOT wrapped in a broad
    ``except``: if the registry cannot be reached the packet must fail loudly,
    because a resolver that quietly returns nothing reports every leg as
    unclamped (see ``family_of``).
    """
    return monitor_unit_for(leg_name)


def clamp_applies(leg_name):
    """Does this leg's LIVE unit apply the venue TP clamp?

    Structural -- read from code and config, NOT from a distribution -- so it is
    knowable for a leg whose candles cannot be fetched. That is why it is
    reported separately from whether the binding RATE was measured.

    ⚠️ Resolved on the UNIT MODULE, never on the family string. ``tp_venue_cap``
    says why in terms: the equity legs are named ``qqq_trend_long_1d`` /
    ``scha_trend_long_1d`` and their family does not resolve, yet their signal
    builder imports ``order_package`` from ``trend_donchian``, which clamps --
    "a family-only test under-claims on all of them". Measured here: the
    family-only test misses ``fade_breakout_4h`` outright.
    """
    return unit_module_clamps(unit_of(leg_name))


def load_mi307():
    if not MI307.exists():
        return {}
    rows = json.loads(MI307.read_text())["results"]
    # The UNCAPPED arm is the target-setting basis (MI-307 § 2), and ONLY rows
    # that actually ran. MI-307 emits a row per leg per arm including its
    # no_data / no_harness / not_capped_capable states; treating one of those as
    # a distribution would report a leg nobody measured as measured.
    return {r["leg"]: r for r in rows if not r.get("capped") and r.get("state") == "ok"}


MI312_OVERRIDE = None


def load_mi312(path=None):
    """MI-312's artifact lands with #12515. Until that merges it is read from an
    explicit ``--mi312`` path, and its absence is reported as ``absent`` rather
    than silently grading the 8 scalp legs unmeasured -- those are opposite
    facts and the packet must not confuse them."""
    p = pathlib.Path(path or MI312_OVERRIDE or MI312)
    if not p.exists():
        return {}
    return {r["leg"]: r for r in json.loads(p.read_text())["legs"]}


def load_corpus():
    """Per leg: the ``tp_r`` cells the e35 sweep actually measured, and how many
    survived each gate stage. Rows are DEDUPED on ``measurement_key`` -- the
    corpus retains superseded runs alongside current ones, so counting raw lines
    would double-count a leg that was swept twice."""
    if not CORPUS.exists():
        return {}
    per = defaultdict(
        lambda: {
            "cells": 0,
            "is_oos": 0,
            "wf_strong": 0,
            "wf_weak": 0,
            "shippable": [],
            "timeout_only": 0,
            "tp_r_values": set(),
        }
    )
    seen = set()
    for line in CORPUS.open():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        tp = r.get("tp_r")
        if tp is None or tp >= SENTINEL_TP_R:
            continue
        key = (r.get("measurement_key"), r.get("sweep_generated_at"))
        if key in seen:
            continue
        seen.add(key)
        d = per[r["leg"]]
        d["cells"] += 1
        d["tp_r_values"].add(tp)
        if r.get("gate_is_passed") and r.get("gate_oos_passed"):
            d["is_oos"] += 1
        verdict = r.get("gate_verdict")
        cell = r.get("cell") or ""
        if verdict in (WF_STRONG, WF_WEAK):
            d["wf_strong" if verdict == WF_STRONG else "wf_weak"] += 1
            if TIMEOUT_MARKER in cell:
                d["timeout_only"] += 1
            else:
                folds = r.get("wf_folds") or []
                # ⚠️ An INERT fold is one where the cell changed no trade. The
                # sweep scores it ``ok: True`` and it lands in ``wf_wins``, so a
                # cell can read 5/6 while only 2 folds actually did anything --
                # BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS. The
                # folds are read rather than the summary quoted.
                inert = sum(1 for f in folds if isinstance(f, dict) and f.get("inert"))
                real_wins = sum(
                    1
                    for f in folds
                    if isinstance(f, dict) and f.get("ok") and not f.get("inert")
                )
                effective = sum(
                    1 for f in folds if isinstance(f, dict) and not f.get("inert")
                )
                d["shippable"].append(
                    {
                        "cell": cell,
                        "tp_r": tp,
                        "route": verdict,
                        "d_net_r": r.get("d_net_r"),
                        "gate_is_passed": bool(r.get("gate_is_passed")),
                        "gate_oos_passed": bool(r.get("gate_oos_passed")),
                        "gate_reason": r.get("gate_is_reason")
                        or r.get("gate_oos_reason"),
                        "wf_wins_reported": r.get("wf_wins")
                        if isinstance(r.get("wf_wins"), int)
                        else len(
                            [f for f in folds if isinstance(f, dict) and f.get("ok")]
                        ),
                        "wf_folds_total": len(folds),
                        "wf_folds_inert": inert,
                        "wf_real_wins": real_wins,
                        "wf_effective_folds": effective,
                    }
                )
    for d in per.values():
        d["tp_r_values"] = sorted(d["tp_r_values"])
        # dedupe identical cells arriving from two runs
        uniq = {}
        for c in d["shippable"]:
            uniq.setdefault((c["cell"], c["tp_r"]), c)
        d["shippable"] = sorted(uniq.values(), key=lambda c: -(c["d_net_r"] or 0))
    return per


def load_coverage():
    if not COVERAGE.exists():
        return {}
    rows = json.loads(COVERAGE.read_text())["rows"]
    return {
        r["strategy"]: (r.get("bracket_geometry") or {}).get("status") for r in rows
    }


def target_setter(leg_name, leg_cfg, m307, m312):
    """WHAT SETS THIS LEG'S TARGET TODAY.

    Five never-collapsed states. ``clamp_rate_state`` is reported SEPARATELY,
    because whether the clamp applies is structural (knowable from config for
    every leg) while how often it BINDS needs a distribution (knowable for 27).
    Collapsing the two would report a leg we could not measure as a leg with no
    clamp.
    """
    declared_r = leg_cfg.get("tp_r")
    declared_at_r = leg_cfg.get("tp_at_r") or leg_cfg.get("tp1_at_r")
    if clamp_applies(leg_name):
        if m307:
            rate = (m307.get("clamp_binds_rate") or {}).get(_grid_key(declared_r))
            if rate is not None:
                return (
                    "venue_clamp" if rate >= 0.5 else "declared_tp_r",
                    "measured",
                    rate,
                )
            return ("venue_clamp", "not_measured", None)
        # sentinel means the clamp is the only thing that can set a target
        if declared_r is None or declared_r >= SENTINEL_TP_R:
            return ("venue_clamp", "not_measured", None)
        return ("venue_clamp", "not_measured", None)
    if m312 is not None and m312:
        return ("declared_unclamped", "measured", 0.0)
    if declared_at_r is not None or declared_r is not None:
        return ("declared_unclamped", "not_measured", None)
    return ("no_target_declared", "not_measured", None)


def _grid_key(tp_r):
    if tp_r is None:
        return "10"
    for k in ("1", "1.5", "2", "2.5", "3", "4", "5", "6", "8", "10"):
        if abs(float(k) - float(tp_r)) < 1e-9:
            return k
    return "10"  # a sentinel tp_r is above every grid point; 10 is the top rung


def build():
    legs = load_legs()
    routing = load_routing()
    m307, m312, corpus, coverage = (
        load_mi307(),
        load_mi312(),
        load_corpus(),
        load_coverage(),
    )
    out = []
    for name, cfg in sorted(legs.items()):
        fam = family_of(name)
        unit = unit_of(name)
        a, b = m307.get(name), m312.get(name)
        setter, rate_state, rate = target_setter(name, cfg, a, b)
        intent = cfg.get("tp_intent") or {}
        ev = corpus.get(name)
        row = {
            "leg": name,
            "symbols": cfg.get("symbols") or [],
            "timeframe": cfg.get("timeframe"),
            "family": fam,
            "unit_module": unit,
            "enabled": bool(cfg.get("enabled")),
            "execution": cfg.get("execution", "live"),
            "accounts": routing[name]["accounts"],
            "real_money_accounts": routing[name]["real_money"],
            "prop_accounts": routing[name]["prop"],
            "declared_tp_r": cfg.get("tp_r"),
            "declared_tp_at_r": cfg.get("tp_at_r") or cfg.get("tp1_at_r"),
            "declared_atr_stop_mult": cfg.get("atr_stop_mult"),
            "tp_intent_mode": intent.get("mode"),
            "tp_intent_reason": intent.get("reason"),
            "target_setter": setter,
            "clamp_applies": clamp_applies(name),
            "clamp_binds_rate": rate,
            "clamp_rate_state": rate_state,
            "mfe_source": "MI-307" if a else ("MI-312" if b else None),
            "n": (a or {}).get("n_mfe") or (b or {}).get("n_mfe"),
            "mfe_r_p50": (a or {}).get("mfe_r_p50") or (b or {}).get("mfe_r_p50"),
            "mfe_r_p80": (a or {}).get("mfe_r_p80") or (b or {}).get("mfe_r_p80"),
            "mfe_r_p90": (a or {}).get("mfe_r_p90") or (b or {}).get("mfe_r_p90"),
            "cap_r_p50": (a or {}).get("cap_r_p50"),
            "mfe_state": (a or {}).get("state") or ("ok" if b else None),
            "e35_cells": (ev or {}).get("cells", 0),
            "e35_is_oos_pass": (ev or {}).get("is_oos", 0),
            "e35_wf_strong": (ev or {}).get("wf_strong", 0),
            "e35_wf_weak": (ev or {}).get("wf_weak", 0),
            "e35_wf_timeout_only": (ev or {}).get("timeout_only", 0),
            "e35_shippable_cells": (ev or {}).get("shippable", []),
            "bracket_geometry_status": coverage.get(name),
        }
        row["evidence_state"] = evidence_state(row)
        row["disposition"], row["disposition_reason"] = disposition(row)
        out.append(row)
    return out


def disposition(row):
    """The packet's per-leg answer. DERIVED, never hand-typed, so it cannot
    disagree with the evidence columns beside it.

    ``no_change`` is a first-class verdict here, not a default: a leg whose
    sweep ran ~180 cells and produced nothing that generalises has its declared
    value standing BY EVIDENCE, which is a different and harder fact than a leg
    nobody looked at.
    """
    # A surviving cell = walk-forward pass, no timeout component (unshippable by
    # construction), and not the inert-fold degenerate shape.
    live = [
        c
        for c in row["e35_shippable_cells"]
        if c["wf_folds_inert"] == 0 and c["wf_real_wins"] >= 4
    ]
    declared = row["declared_tp_r"]
    if row["evidence_state"] == "not_measured":
        return ("not_measured", "no distribution and no sweep -- nobody has looked")
    if live:
        matched = [
            c
            for c in live
            if declared is not None and abs(c["tp_r"] - float(declared)) < 1e-9
        ]
        if matched:
            return (
                "no_change_evidence_already_shipped",
                f"the sweep's surviving cell is tp_r={matched[0]['tp_r']} and config "
                f"already declares {declared} -- the evidence was acted on",
            )
        return (
            "operator_decision_unshipped_cell",
            f"a surviving cell declares tp_r={live[0]['tp_r']} against config's {declared}",
        )
    if row["target_setter"] == "declared_unclamped" and row["mfe_source"] == "MI-312":
        return (
            "operator_decision_never_chosen_against_a_distribution",
            "unclamped: the declared target is the whole mechanism, and it was set "
            "identically fleet-wide rather than from this leg's own excursion",
        )
    if row["e35_cells"] > 0:
        return (
            "no_change_sweep_exhausted",
            f"{row['e35_cells']} tp_r cells swept, zero survive walk-forward without a "
            "timeout component -- the declared value stands by evidence",
        )
    return ("not_measured", "no sweep cells for this leg")


def evidence_state(row):
    """Never collapse 'we looked and there is nothing' with 'we did not look'."""
    has_mfe = row["mfe_source"] is not None
    has_sweep = row["e35_cells"] > 0
    if has_mfe and has_sweep:
        return "distribution_and_sweep"
    if has_mfe:
        return "distribution_only"
    if has_sweep:
        return "sweep_only"
    return "not_measured"


# ---------------------------------------------------------------- selftest
def selftest():
    checks, failures = 0, []

    def ok(cond, label):
        nonlocal checks
        checks += 1
        if not cond:
            failures.append(label)

    rows = build()
    ok(len(rows) == 55, f"55 legs in the population, got {len(rows)}")
    by = {r["leg"]: r for r in rows}

    # --- structural facts imported, not restated
    ok(abs(TP_VENUE_CAP_PCT - 0.099) < 1e-9, "clamp constant is the imported one")
    ok("scalp" not in CLAMPING_FAMILIES, "scalp is NOT a clamping family")
    ok("donchian" in CLAMPING_FAMILIES, "donchian IS a clamping family")

    # --- NEGATIVE CONTROL 0: the family resolver must actually RESOLVE.
    # An earlier draft called classify() with the wrong arity behind a broad
    # except; every leg came back None and the packet reported 0 clamped legs
    # while importing the frozenset naming four clamping families. These two
    # assertions fail on that.
    resolved = sum(1 for r in rows if r["unit_module"])
    ok(resolved == 55, f"NEG0: every leg resolves a unit module, got {resolved}")
    n_clamped = sum(1 for r in rows if r["clamp_applies"])
    ok(n_clamped > 0, "NEG0b: at least one leg is clamped (the frozenset is consulted)")
    ok(
        by["trend_donchian"]["clamp_applies"],
        "NEG0c: trend_donchian is clamped",
    )
    # NEGATIVE CONTROL 0d: the UNIT test must not silently become a FAMILY test.
    # fade_breakout_4h has no harness, so classify() returns None and a
    # family-only test calls it unclamped -- while its unit module imports the
    # clamp. This assertion fails the moment the resolution regresses.
    f4 = by["fade_breakout_4h"]
    ok(f4["family"] is None, "NEG0d: fade_breakout_4h has no harness family")
    ok(f4["clamp_applies"], "NEG0d2: ...and is STILL correctly reported as clamped")
    ok(
        "fade_breakout_4h" in CLAMPING_UNIT_MODULES,
        "NEG0d3: the unit-module frozenset is what carries it",
    )

    # --- NEGATIVE CONTROL 1: a scalp leg must never be reported as clamped.
    # If clamp_applies stops consulting CLAMPING_FAMILIES this fails.
    s = by.get("ict_scalp_5m")
    ok(s is not None and not s["clamp_applies"], "NEG1: ict_scalp_5m is not clamped")
    ok(
        s is not None and s["target_setter"] == "declared_unclamped",
        "NEG1b: scalp target is its own",
    )

    # --- NEGATIVE CONTROL 2: evidence_state must distinguish absence from silence.
    ok(
        any(r["evidence_state"] == "not_measured" for r in rows),
        "NEG2: some leg is honestly 'not_measured'",
    )
    ok(
        any(r["evidence_state"] == "sweep_only" for r in rows),
        "NEG2b: sweep_only is reachable and distinct",
    )

    # --- NEGATIVE CONTROL 3: a timeout-bearing cell is never called shippable.
    for r in rows:
        for c in r["e35_shippable_cells"]:
            ok(
                TIMEOUT_MARKER not in c["cell"],
                f"NEG3: {r['leg']} cell {c['cell']} has no timeout",
            )

    # --- NEGATIVE CONTROL 4: the two walk-forward routes are never pooled.
    ok(WF_STRONG != WF_WEAK, "NEG4: strong and weak WF routes are distinct keys")
    weak = sum(r["e35_wf_weak"] for r in rows)
    strong = sum(r["e35_wf_strong"] for r in rows)
    ok(
        weak > 0 and strong > 0,
        "NEG4b: both routes populated, so the split is observable",
    )

    # --- NEGATIVE CONTROL 5: inert folds must be counted OUT of the wins.
    # A cell whose reported wf_wins exceeds its non-inert fold count is exactly
    # BL-20260817's degenerate shape; the packet must be able to SEE that.
    cells = [c for r in rows for c in r["e35_shippable_cells"]]
    ok(cells, "NEG5: at least one shippable cell exists to grade")
    ok(
        all(c["wf_real_wins"] <= c["wf_effective_folds"] for c in cells),
        "NEG5b: real wins never exceed non-inert folds",
    )
    ok(
        any(c["wf_folds_inert"] > 0 for c in cells),
        "NEG5c: an inert fold is actually observed, so the check is not vacuous",
    )

    # --- population arithmetic must close
    cens = Counter(r["evidence_state"] for r in rows)
    ok(sum(cens.values()) == 55, "evidence_state census closes on 55")
    dcens = Counter(r["disposition"] for r in rows)
    ok(sum(dcens.values()) == 55, "disposition census closes on 55")
    # NEGATIVE CONTROL 6: "change nothing" must be reachable AND distinguishable
    # from "nobody looked". Collapsing the two is the whole failure this packet
    # exists to avoid.
    ok(
        dcens.get("no_change_sweep_exhausted", 0) > 0,
        "NEG6: no_change-by-evidence is reachable",
    )
    ok(
        dcens.get("not_measured", 0) > 0,
        "NEG6b: not_measured is reachable and separate",
    )
    ok(
        all(
            r["disposition"] != "no_change_sweep_exhausted" or r["e35_cells"] > 0
            for r in rows
        ),
        "NEG6c: a no_change-by-evidence verdict always has cells behind it",
    )
    ok(
        sum(1 for r in rows if r["mfe_source"] == "MI-307") == 19,
        "19 legs carry MI-307's distribution",
    )
    n312 = sum(1 for r in rows if r["mfe_source"] == "MI-312")
    if load_mi312():
        ok(n312 == 8, f"8 legs carry MI-312's distribution, got {n312}")
    else:
        # #12515 not merged and no --mi312 given: the 8 legs MUST read
        # not_measured, never 0-with-a-number.
        ok(n312 == 0, "MI-312 absent -> no leg falsely claims its distribution")
        ok(
            by["ict_scalp_5m"]["evidence_state"] in ("not_measured", "sweep_only"),
            "NEG5: an unread scalp leg is reported unmeasured, not clamped-and-fine",
        )

    print(f"mi317 selftest: {checks - len(failures)}/{checks} passed")
    for f in failures:
        print("  FAIL:", f)
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--write", metavar="PATH")
    ap.add_argument("--report", action="store_true")
    ap.add_argument(
        "--mi312", metavar="PATH", help="MI-312 artifact until #12515 merges"
    )
    args = ap.parse_args()
    global MI312_OVERRIDE
    MI312_OVERRIDE = args.mi312
    if args.selftest:
        raise SystemExit(selftest())
    rows = build()
    if args.write:
        pathlib.Path(args.write).write_text(
            json.dumps(
                {
                    "unit": "MI-317",
                    "tp_venue_cap_pct": TP_VENUE_CAP_PCT,
                    "clamping_families": sorted(CLAMPING_FAMILIES),
                    "population": "all 55 entries in config/strategies.yaml",
                    "legs": rows,
                },
                indent=1,
                sort_keys=True,
            )
            + "\n"
        )
        print(f"wrote {args.write} ({len(rows)} legs)")
    if args.report or not args.write:
        print(json.dumps(Counter(r["evidence_state"] for r in rows), indent=1))
        print(json.dumps(Counter(r["target_setter"] for r in rows), indent=1))


if __name__ == "__main__":
    main()
