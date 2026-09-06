#!/usr/bin/env python3
"""POSITIVE CONTROL on the M20 exit-lever sweep — can the detector see an effect
it is TOLD is there?

MI-145 · object WO-20260906-POSITIVE-CONTROL-ON-THE-EXIT-SWEEP-320.

THE QUESTION. `docs/research/exit-refinement-coverage.json` carries 320
`honest_negative` cells (68.4% of 468) and 39 `shipped` ones. Nobody had ever
run a positive control on the instrument that produced them, so every negative
was unfalsifiable and every ship rested on an uncalibrated gauge. This repo's
own RULE ONE says a negative needs a denominator and that a probe must be shown
to find a positive before its silence is trusted. This applies that rule to the
research harness.

WHAT IS UNDER TEST. Not the market and not any lever: the DETECTOR — the chain
`run_cell` -> `beats` -> `walkforward` -> verdict inside
`scripts/research/m20_fleet_exit_sweep.py`. Every one of those is imported and
called here, unmodified. The only new code is a wrapper harness
(`_control_oracle_harness.py`) that plants a transform whose sign is known
before the run.

THE ARMS, PRE-REGISTERED. Expectations are written down here, in the source,
BEFORE any run — that is what stops the control being fitted to its answer.

  N1  wrapper-faithfulness    wrapper(identity) vs the real harness.
      EXPECT: identical `net_total_r` / `max_drawdown_r` within rounding.
      If it fails, nothing else in this script means anything.

  N2  inert-lever null        real harness, base vs base + a giveback stop
                              armed at a threshold no trade can reach.
      EXPECT: d_net_r == 0.0 and d_max_dd == 0.0 EXACTLY, verdict is_oos_fail.
      A non-zero delta would mean the two arms differ for a reason other than
      the lever — i.e. every measured delta in the corpus is contaminated.

  O1  loss-free oracle        wrapper(identity) vs wrapper(loss_free).
      EXPECT: PASS. beats() in BOTH windows and ok in EVERY usable fold.
      Provable, with no appeal to market behaviour: every trade's R weakly
      improves (so net_total_r is >= base, strictly > with one loser present),
      and every R is >= 0 (so the equity curve is monotone and max_drawdown_r
      is exactly 0.0 <= base). All three clauses of
      `cn >= bn and cd <= bd and (cn > bn or cd < bd)` hold by construction.

⚠️ NOT TUNED, AND MUST NOT BE. If O1 comes back negative that is the RESULT —
report it. Re-running O1 with a different threshold until it goes green fits the
control to the answer and destroys it.

⚠️ WHAT A PASSING O1 DOES *NOT* ESTABLISH. It shows the detector is not blind
and its plumbing is sound. It says nothing about SENSITIVITY — whether the gate
can resolve an effect of the size a real lever produces. A control is a floor on
an instrument's credibility, never a ceiling.

Tier-1 research tooling. Writes no config, ships nothing, and its cells never
enter the coverage matrix.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "research"))

import yaml  # noqa: E402

import m20_fleet_exit_sweep as SW  # noqa: E402  the instrument under test

WRAPPER = "scripts/research/_control_oracle_harness.py"

# A giveback stop armed at 10_000 R. `backtest_trend`/`backtest_pullback` arm
# the lever on `mfe >= giveback_min_mfe_r`; no trade in any book reaches that,
# so the cell is inert BY CONSTRUCTION rather than by luck. This is the object's
# own "a stop so wide it cannot fire" shape.
INERT_LEVER = ["--giveback-min-mfe-r", "10000", "--giveback-r", "10000"]


def _num(d: dict, k: str):
    try:
        return float(d[k])
    except (KeyError, TypeError, ValueError):
        return None


def _arm(harness, base_args, cell_args, *, label, expect, require_dd=True,
         split=None, run_wf=True):
    """One arm, graded by the SWEEP'S OWN functions."""
    out = {"arm": label, "expectation": expect, "harness": harness}
    b_is = SW.run_cell(harness, base_args, end=split) if split else SW.run_cell(harness, base_args)
    c_is = SW.run_cell(harness, cell_args, end=split) if split else SW.run_cell(harness, cell_args)
    if "error" in b_is or "error" in c_is:
        out["observed"] = {"error": b_is.get("error") or c_is.get("error")}
        out["outcome"] = "could_not_measure"
        return out
    windows = {"IS" if split else "full": (b_is, c_is)}
    if split:
        windows["OOS"] = (SW.run_cell(harness, base_args, start=split),
                          SW.run_cell(harness, cell_args, start=split))
    obs = {}
    gate_all = True
    # AN EMPTY BOOK IS "WE COULD NOT LOOK", NEVER A NEGATIVE. `beats()` returns
    # False on two zero-trade books with `reason: tie_no_improvement`, which is
    # byte-identical to a measured negative — the vacuity class this repo already
    # names ("a verdict computed from zero inputs is vacuous, not thin",
    # docs/CLAUDE-RULES-CANONICAL.md § Green is not evidence). Observed live on
    # the first smoke run of this very control, which is why the guard is here.
    empty = {w: b.get("total_trades") for w, (b, _c) in windows.items()
             if not b.get("total_trades")}
    if empty:
        for w, (b, c) in windows.items():
            obs[w] = {"base_trades": b.get("total_trades"),
                      "cell_trades": c.get("total_trades")}
        out["observed"] = obs
        out["outcome"] = "could_not_test"
        out["why"] = f"empty_base_book in window(s) {sorted(empty)}"
        out["verdict"] = None
        return out
    for w, (b, c) in windows.items():
        if "error" in b or "error" in c:
            obs[w] = {"error": b.get("error") or c.get("error")}
            gate_all = False
            continue
        passed = SW.beats(c, b)
        gate_all = gate_all and passed
        obs[w] = {
            "base_trades": b.get("total_trades"), "cell_trades": c.get("total_trades"),
            "base_net_r": _num(b, "net_total_r"), "cell_net_r": _num(c, "net_total_r"),
            "base_max_dd": _num(b, "max_drawdown_r"), "cell_max_dd": _num(c, "max_drawdown_r"),
            "d_net_r": round((_num(c, "net_total_r") or 0) - (_num(b, "net_total_r") or 0), 6),
            "d_max_dd": round((_num(c, "max_drawdown_r") or 0) - (_num(b, "max_drawdown_r") or 0), 6),
            "beats": passed,
            "beats_detail": SW.beats_detail(c, b),
        }
    out["observed"] = obs
    out["gate_passed_all_windows"] = gate_all
    if run_wf and gate_all:
        wf = SW.walkforward(harness, base_args, cell_args,
                            lambda _row: None, label, label, require_dd=require_dd)
        out["walkforward"] = {"summary": wf["summary"],
                              "summary_effective": wf.get("summary_effective"),
                              "wins": wf["wins"], "usable": wf["usable"],
                              "inert_wins": wf.get("inert_wins"),
                              "folds": wf["folds"]}
        out["verdict"] = ("PASS" if wf["usable"] and wf["wins"] * 3 >= wf["usable"] * 2
                          else "wf_fail")
    else:
        out["verdict"] = "is_oos_fail"
        out["walkforward"] = {"ran": False, "why": "gate not passed in every window"}
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--leg", required=True, help="leg name from config/strategies.yaml")
    ap.add_argument("--data-dir", default=str(REPO / "data"))
    ap.add_argument("--split", default=None,
                    help="IS/OOS boundary date. Omit to derive it with the "
                         "sweep's own resolve_split (--split-mode oos-trades).")
    ap.add_argument("--split-target-oos", type=int, default=50)
    ap.add_argument("--no-wf", action="store_true",
                    help="skip the walk-forward (fast plumbing check only)")
    ap.add_argument("--out", default=None, help="write the result JSON here")
    a = ap.parse_args(argv)

    strategies = (yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())
                  or {}).get("strategies") or {}
    cfg = strategies.get(a.leg)
    if not isinstance(cfg, dict):
        print(f"ERROR: leg {a.leg!r} not in config/strategies.yaml")
        return 2
    fam = SW.classify(a.leg)
    if fam is None:
        print(f"ERROR: leg {a.leg!r} classifies to no harness family")
        return 2
    harness = SW.FAMILY_HARNESS[fam]
    sym = (cfg.get("symbols") or [None])[0]
    tf = str(cfg.get("timeframe") or "1h")
    data, proxy, resample = SW.resolve_data(str(sym), tf, Path(a.data_dir))
    if data is None:
        # "we could not look" is not "the control failed" — say which.
        print(json.dumps({"outcome": "could_not_test",
                          "why": f"data_missing:{sym}",
                          "leg": a.leg, "data_dir": a.data_dir}, indent=2))
        return 3
    base = SW.base_args(a.leg, cfg, fam, data, resample)

    split, split_meta = (a.split, {"mode": "explicit"}) if a.split else \
        SW.resolve_split(harness, base, "oos-trades", "2025-07-01", a.split_target_oos)

    result = {
        "control": "MI-145 exit-sweep positive control",
        "leg": a.leg, "family": fam, "symbol": sym, "timeframe": tf,
        "data": data, "proxy": proxy, "resample": resample,
        "harness": harness, "split": split, "split_meta": split_meta,
        "detector": "scripts/research/m20_fleet_exit_sweep.py "
                    "(run_cell/beats/walkforward, imported unmodified)",
        "arms": [],
    }

    # N1 — is the wrapper faithful? Compare it to the real harness, full history.
    real = SW.run_cell(harness, base)
    wrapped = SW.run_cell(WRAPPER, base + ["--control-harness", harness,
                                           "--control-transform", "identity"])
    n1 = {"arm": "N1_wrapper_faithfulness",
          "expectation": "wrapper(identity) reproduces the real harness within rounding"}
    if "error" in real or "error" in wrapped:
        n1["observed"] = {"error": real.get("error") or wrapped.get("error")}
        n1["outcome"] = "could_not_measure"
    else:
        dn = (_num(wrapped, "net_total_r") or 0) - (_num(real, "net_total_r") or 0)
        dd = (_num(wrapped, "max_drawdown_r") or 0) - (_num(real, "max_drawdown_r") or 0)
        n1["observed"] = {"real_net_r": _num(real, "net_total_r"),
                          "wrapped_net_r": _num(wrapped, "net_total_r"),
                          "real_max_dd": _num(real, "max_drawdown_r"),
                          "wrapped_max_dd": _num(wrapped, "max_drawdown_r"),
                          "real_trades": real.get("total_trades"),
                          "wrapped_trades": wrapped.get("total_trades"),
                          "d_net_r": round(dn, 6), "d_max_dd": round(dd, 6)}
        n1["outcome"] = ("faithful" if abs(dn) <= 0.01 and abs(dd) <= 0.01
                         else "WRAPPER_UNFAITHFUL")
    result["arms"].append(n1)

    # N2 — inert lever null, on the REAL harness both sides.
    result["arms"].append(_arm(
        harness, base, base + INERT_LEVER,
        label="N2_inert_lever_null",
        expect="d_net_r == 0.0 and d_max_dd == 0.0 exactly; verdict is_oos_fail",
        split=split, run_wf=False))

    # O1 — the positive control.
    result["arms"].append(_arm(
        WRAPPER,
        base + ["--control-harness", harness, "--control-transform", "identity"],
        base + ["--control-harness", harness, "--control-transform", "loss_free"],
        label="O1_loss_free_oracle",
        expect="PASS — beats() in every window and ok in every usable fold",
        split=split, run_wf=not a.no_wf))

    txt = json.dumps(result, indent=2, default=str)
    if a.out:
        Path(a.out).write_text(txt)
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
