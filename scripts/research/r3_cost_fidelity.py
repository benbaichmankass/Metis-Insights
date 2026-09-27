#!/usr/bin/env python3
"""R3: per-leg cost fidelity, the standing Gate-1 (Stage 1 -> Stage 2) test.

ONE QUESTION (checklist row R3, 2026-09-25): per leg and per account, is the
realized round-trip slippage the one the leg's Stage-0 evidence record assumed?

The rule this grades against is ``RULE-R3-GATE1-COST-FIDELITY-v1``,
registered in ``docs/research/r3-gate1-cost-fidelity-rule-2026-09-25.md`` in
a commit that holds no result. The constants below are copied from that
document. ``tests/test_r3_cost_fidelity.py`` pins them, so if the rule text
and the code drift apart, a test fails.

Measurement is D3's ``realized_slippage.measure()``, reused unchanged: same
reference prices, same sign (positive = adverse), and package-referenced exits
only. This script only groups D3's rows by (leg, account), compares each group
to the leg's ``comms/strategy_evidence/<leg>.json`` ``cost_stack.slippage``,
and applies the rule. It also reports the E62 done-when observation: whether
any Alpaca sl-family close carries ``exit_price_source=exchange_fill``.

Read-only. It changes no default, no config, no roster and no evidence record.

Usage
-----
  python3 scripts/research/realized_slippage.py pull --out-dir D      # DIAG_READ_TOKEN
  python3 scripts/research/r3_cost_fidelity.py --in-dir D --as-of <pull time> \\
      --out comms/research/r3_cost_fidelity/<date>.json
"""
from __future__ import annotations

# wiring: manual-only - the Gate-1 measurement (checklist row R3); a session re-runs it by hand with the commands in the docstring, as D3's and E62's scripts are. A scheduled weekly run is D3's build step, not this script.

import argparse
import collections
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "research"))

import realized_slippage as d3  # noqa: E402
from src.config.accounts_loader import load_accounts_dict  # noqa: E402
from src.runtime import execution_costs  # noqa: E402

RULE_ID = "RULE-R3-GATE1-COST-FIDELITY-v1"
RULE_DOC = "docs/research/r3-gate1-cost-fidelity-rule-2026-09-25.md"
N_FLOOR = 10            # per side, per cell (rule § "The floor")
VENUE_MIN_N = d3.MIN_N  # venue-level pooled figures keep D3's floor (20)
TOLERANCE_SOURCE = ("config/mandates.yaml", "MD-PROMOTE-S1-S2", "bar.cost_tolerance_bps")
EVIDENCE_DIR = REPO / "comms" / "strategy_evidence"

#: Stage per account, the resolver's map (scripts/ops/mandate_resolver.py).
#: Mirrors are Stage 2 (paper side).
STAGE_OF_ACCOUNT = {
    "bybit_1": "S1", "alpaca_paper": "S1", "ib_paper": "S1",
    "bybit_2": "S2", "alpaca_live": "S2", "ib_live": "S2",
    "bybit_portfolio": "S2-mirror", "alpaca_portfolio": "S2-mirror",
}
SL_FAMILY = {"sl", "sl_cross", "giveback_stop"}
E62_DEPLOY = "2026-09-24T18:18:38+00:00"  # #12878 boot, row E62
RATE_WINDOW_DAYS = 60

INSUFFICIENT, DIVERGENT, CONSISTENT, INCONCLUSIVE, NO_RECORD = (
    "insufficient_n", "divergent", "consistent", "inconclusive", "no_record")


def tolerance_bps(root: Path = REPO) -> float:
    """T, read from the field the rule names. A missing/invalid field raises:
    the rule refuses to grade against a tolerance nobody stated."""
    doc = yaml.safe_load(open(root / TOLERANCE_SOURCE[0])) or {}
    for m in doc.get("mandates") or []:
        if m.get("id") == TOLERANCE_SOURCE[1]:
            tol = (m.get("bar") or {}).get("cost_tolerance_bps")
            if isinstance(tol, (int, float)) and not isinstance(tol, bool) and tol >= 0:
                return float(tol)
            raise ValueError(f"{TOLERANCE_SOURCE[1]}.bar.cost_tolerance_bps={tol!r} is not stated")
    raise ValueError(f"{TOLERANCE_SOURCE[1]} not found under mandates: in {TOLERANCE_SOURCE[0]}")


def grade(entry: List[float], exit_: List[float], modelled: Optional[float],
          tol: float) -> Dict[str, Any]:
    """Apply the rule to one cell. Pure: the rule's whole decision is here."""
    out: Dict[str, Any] = {"n_entry": len(entry), "n_exit": len(exit_),
                           "n": min(len(entry), len(exit_)), "modelled_bps": modelled,
                           "threshold_bps": None if modelled is None else round(modelled + tol, 3)}
    if entry:
        out["entry_mean"] = round(sum(entry) / len(entry), 3)
    if exit_:
        out["exit_mean"] = round(sum(exit_) / len(exit_), 3)
    if len(entry) < N_FLOOR or len(exit_) < N_FLOOR:
        out["verdict"] = INSUFFICIENT
        out["why"] = f"entry n={len(entry)}, exit n={len(exit_)}; need >= {N_FLOOR} each"
        return out
    out["roundtrip_mean"] = round(out["entry_mean"] + out["exit_mean"], 3)
    lo, hi = d3._boot_ci(entry, exit_)
    out["ci95"] = [lo, hi]
    if modelled is None:
        out["verdict"] = NO_RECORD
        return out
    thr = modelled + tol
    if lo > thr:
        out["verdict"] = DIVERGENT
    elif hi <= thr:
        out["verdict"] = CONSISTENT
        out["record_overcharges"] = hi < modelled
    else:
        out["verdict"] = INCONCLUSIVE
    return out


def _record(leg: str) -> Optional[dict]:
    p = EVIDENCE_DIR / f"{leg}.json"
    return json.load(open(p)) if p.exists() else None


def _brief(t: dict) -> dict:
    return {"trade_id": t["id"], "account_id": t["account_id"], "strategy": t.get("strategy_name"),
            "symbol": t.get("symbol"), "exit_reason": t.get("exit_reason"),
            "closed_at": t.get("closed_at")}


def _ts(s: str) -> datetime:
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _leg_verdict(cells: List[dict]) -> Dict[str, Any]:
    """Rule § Basis: a decisive MARKET cell wins; else the Stage-1 soak cell;
    else any other decisive cell; else the leg is insufficient_n."""
    decisive = {DIVERGENT, CONSISTENT, INCONCLUSIVE}

    def rank(c: dict) -> int:
        if c["basis"] == "market":
            return 0
        return 1 if c["stage"] == "S1" else 2

    for c in sorted(cells, key=rank):
        if c["verdict"] in decisive:
            return {"verdict": c["verdict"], "from_account": c["account_id"], "basis": c["basis"]}
    if any(c["verdict"] == NO_RECORD for c in cells):
        return {"verdict": NO_RECORD, "from_account": None, "basis": None}
    return {"verdict": INSUFFICIENT, "from_account": None, "basis": None}


def run(in_dir: Path, as_of: datetime) -> dict:
    acfg = load_accounts_dict()
    tol = tolerance_bps()
    m = d3.measure(in_dir)
    rows = m["rows"]
    trades = json.load(open(in_dir / "trades.json"))

    # ---- per (leg, account) cells -----------------------------------------
    groups: Dict[tuple, Dict[str, List[float]]] = collections.defaultdict(
        lambda: {"entry": [], "exit": []})
    for r in rows:
        if r["side"] == "entry":
            groups[(r["strategy"], r["account_id"])]["entry"].append(r["bps"])
        elif r.get("ref_source") == "order_packages":
            groups[(r["strategy"], r["account_id"])]["exit"].append(r["bps"])

    rosters = {a: set(c.get("strategies") or []) for a, c in acfg.items()}
    # provenance: EVIDENCE_DIR — the UNION of every committed evidence record and every leg with fills, not a newest pick; its size is published as population.legs_reported
    legs = sorted({k[0] for k in groups if k[0]} | {p.stem for p in EVIDENCE_DIR.glob("*.json")})
    per_leg: Dict[str, dict] = {}
    for leg in legs:
        rec = _record(leg)
        cs = (rec or {}).get("cost_stack") or {}
        modelled = cs.get("slippage") if isinstance(cs.get("slippage"), (int, float)) else None
        sym = (rec or {}).get("symbol")
        cells = []
        for (lg, acct), g in sorted(groups.items(), key=lambda kv: str(kv[0])):
            if lg != leg:
                continue
            c = grade(g["entry"], g["exit"], modelled, tol)
            if rec is None and c["verdict"] != INSUFFICIENT:
                c["verdict"] = NO_RECORD
            klass = (acfg.get(acct) or {}).get("account_class")
            c.update({"account_id": acct, "account_class": klass,
                      "basis": "market" if klass == "real_money" else "simulator",
                      "stage": STAGE_OF_ACCOUNT.get(acct, "unstaged"),
                      "venue": d3.VENUE_OF.get((acfg.get(acct) or {}).get("exchange"))})
            cells.append(c)
        on = sorted(a for a, legs_ in rosters.items() if leg in legs_)
        if not cells and not on:
            continue  # a record for a leg no account runs and that never filled
        per_leg[leg] = {
            "symbol": sym, "record": f"comms/strategy_evidence/{leg}.json" if rec else None,
            "record_stage0_verdict": ((rec or {}).get("decision_rule") or {}).get("verdict"),
            "modelled_slippage_bps": modelled,
            "current_default_bps": (execution_costs.slippage_bps_roundtrip_for(sym) if sym else None),
            "on_rosters_now": on,
            "cells": cells,
            "leg": _leg_verdict(cells) if cells else {"verdict": INSUFFICIENT, "from_account": None,
                                                      "basis": None, "why": "no measured fills"},
        }
        lv = per_leg[leg]["leg"]["verdict"]
        per_leg[leg]["action"] = {
            DIVERGENT: "DEMOTE one stage AND invalidate the Stage-0 record (rule § consequence)",
            CONSISTENT: "Gate-1 cost clause PASSES",
        }.get(lv, "none: keep accruing")

    # ---- per venue x basis, pooled (D3's floor) -----------------------------
    per_venue: Dict[str, dict] = {}
    for r in rows:
        klass = (acfg.get(r["account_id"]) or {}).get("account_class")
        key = f"{r['venue']}|{'market' if klass == 'real_money' else 'simulator'}"
        v = per_venue.setdefault(key, {"entry": [], "exit": [], "accounts": set()})
        v["accounts"].add(r["account_id"])
        if r["side"] == "entry":
            v["entry"].append(r["bps"])
        elif r.get("ref_source") == "order_packages":
            v["exit"].append(r["bps"])
    venue_out = {}
    for key, v in sorted(per_venue.items()):
        venue, basis = key.split("|")
        rep = {"bybit_perps": "BTCUSDT", "alpaca_equities": "SPY",
               "ibkr_futures": "MES"}.get(venue)
        modelled = execution_costs.slippage_bps_roundtrip_for(rep) if rep else None
        g = grade(v["entry"], v["exit"], modelled, tol)
        enough = g["n_entry"] >= VENUE_MIN_N and g["n_exit"] >= VENUE_MIN_N
        g.update({"venue": venue, "basis": basis, "accounts": sorted(v["accounts"]),
                  "modelled_is_current_default_for": rep,
                  "enough_n_at_venue_floor": enough, "venue_floor": VENUE_MIN_N})
        venue_out[key] = g

    # ---- E62 done-when: an Alpaca sl-family close with exchange_fill -------
    alp = [t for t in trades if str(t.get("account_id") or "").startswith("alpaca")
           and not t.get("is_backtest") and t.get("status") == "closed"
           and str(t.get("exit_reason") or "") in SL_FAMILY]
    def _hit(t: dict) -> bool:
        return d3._notes(t).get("exit_price_source") == "exchange_fill"

    post = [t for t in alp if t.get("closed_at") and _ts(t["closed_at"]) >= _ts(E62_DEPLOY)]
    hits = [t for t in post if _hit(t)]
    # A pre-deploy exchange_fill close was written by some OTHER path than the
    # #12878 fix, so it cannot be the observation that proves the fix.
    pre_hits = [t for t in alp if _hit(t) and t not in post]
    alp_closes_post_any = [t for t in trades if str(t.get("account_id") or "").startswith("alpaca")
                           and not t.get("is_backtest") and t.get("status") == "closed"
                           and t.get("closed_at") and _ts(t["closed_at"]) >= _ts(E62_DEPLOY)]
    src_census = collections.Counter(str(d3._notes(t).get("exit_price_source")) for t in post)
    since = as_of - timedelta(days=RATE_WINDOW_DAYS)
    in_win = [t for t in alp if t.get("closed_at") and _ts(t["closed_at"]) >= since]

    def _fc(k: int) -> dict:
        rate = k / RATE_WINDOW_DAYS
        lo, hi = (x / RATE_WINDOW_DAYS for x in _poisson_ci(k))
        return {"closes_in_window": k, "rate_per_day": round(rate, 4),
                "rate_ci95_per_day": [round(lo, 4), round(hi, 4)],
                "expected_days_to_first": round(1 / rate, 1) if rate > 0 else None,
                "p_at_least_one_in_7d": round(1 - math.exp(-7 * rate), 3),
                "p_at_least_one_in_7d_at_ci_low": round(1 - math.exp(-7 * lo), 3)}
    e62 = {
        "done_when": "an Alpaca sl-family close carries exit_price_source=exchange_fill",
        "sl_family": sorted(SL_FAMILY),
        "observed": bool(hits),
        "counts_only_closes_after": E62_DEPLOY,
        "hits": [_brief(t) for t in hits],
        "pre_deploy_exchange_fill_sl_family_closes": [_brief(t) for t in pre_hits],
        "alpaca_sl_family_closes_since_deploy": len(post),
        "alpaca_closes_any_reason_since_deploy": len(alp_closes_post_any),
        "their_exit_price_source": dict(src_census),
        "deploy": E62_DEPLOY,
        "forecast": {
            "basis": (f"Alpaca sl-family closes over the last {RATE_WINDOW_DAYS}d (the #12878 fix "
                      "applies to every Alpaca account, paper included); exact Poisson 95% interval "
                      "on the rate. HEADLINE = legs on each account's CURRENT roster (alpaca_portfolio "
                      "was cut 14 -> 2 on 2026-09-21, so the all-legs rate overstates the forward "
                      "rate). INFERRED that a post-deploy sl-family close resolves to exchange_fill, "
                      "which is exactly what is unobserved"),
            "current_roster": _fc(sum(1 for t in in_win if t.get("strategy_name") in
                                      rosters.get(t.get("account_id"), set()))),
            "all_legs_upper_bound": _fc(len(in_win)),
            "alpaca_positions_open_now": sum(
                1 for t in trades if str(t.get("account_id") or "").startswith("alpaca")
                and not t.get("is_backtest") and t.get("status") == "open"),
        },
    }

    verdicts = collections.Counter(v["leg"]["verdict"] for v in per_leg.values())
    return {
        "question": "R3: per leg, is realized round-trip slippage the one its Stage-0 record assumed?",
        "rule": {"id": RULE_ID, "doc": RULE_DOC, "n_floor_per_side": N_FLOOR,
                 "tolerance_bps": tol, "tolerance_source": ".".join(TOLERANCE_SOURCE[1:])
                 + f" in {TOLERANCE_SOURCE[0]}", "venue_floor": VENUE_MIN_N},
        "generated_by": "scripts/research/r3_cost_fidelity.py (reuses realized_slippage.measure)",
        "as_of": as_of.isoformat(),
        "population": {
            "trades_rows": len(trades), "measured_rows": len(rows),
            "entry_rows": sum(1 for r in rows if r["side"] == "entry"),
            "package_referenced_exit_rows": sum(1 for r in rows if r["side"] == "exit"
                                                and r.get("ref_source") == "order_packages"),
            "legs_reported": len(per_leg), "cells": sum(len(v["cells"]) for v in per_leg.values()),
            "excluded_counts": m["excluded"],
        },
        "leg_verdict_counts": dict(verdicts),
        "per_venue": venue_out,
        "per_leg": per_leg,
        "e62": e62,
        "_rows": rows,
    }


def _poisson_ci(k: int) -> List[float]:
    """Exact (Garwood) 95% interval on a Poisson count (E62's implementation)."""
    import e62_slippage_accrual as e62

    return e62._poisson_ci(k)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--in-dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--as-of", required=True, help="ISO time of the pull")
    a = ap.parse_args()
    res = run(a.in_dir, _ts(a.as_of))
    rows = res.pop("_rows")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    json.dump(res, open(a.out, "w"), indent=1, default=list)
    # one row per measured entry/exit (D3's rows), so every cell can be re-derived
    with open(a.out.with_name(a.out.stem + "__rows.jsonl"), "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    pull_log = a.in_dir / "fills_pull_log.json"
    if pull_log.exists():
        a.out.with_name(a.out.stem + "__fills_pull_log.json").write_text(pull_log.read_text())
    print(json.dumps({"leg_verdict_counts": res["leg_verdict_counts"],
                      "per_venue": {k: {x: v.get(x) for x in ("n_entry", "n_exit", "roundtrip_mean",
                                                              "ci95", "modelled_bps", "verdict")}
                                    for k, v in res["per_venue"].items()},
                      "e62_observed": res["e62"]["observed"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
