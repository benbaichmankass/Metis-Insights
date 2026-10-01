#!/usr/bin/env python3
# wiring: research/queue/RQ-20260930-702 via .github/workflows/research-script-run.yml
"""E38 follow-up (a): POOLED trend_donchian family regime-dependence test.

PI-20260925-7P3VZ7EJ-0001. E38 (2026-09-25) found 0 of 22 crypto legs with regime dependence
on rv20/er20 at entry but single-leg n = 35-107 only detects |rho| >= 0.27-0.46. This pools the
trend_donchian_* legs so the test can detect a much smaller effect. Same features, same
no-look-ahead daily-bar construction and same walk-forward as E38 (imported, not re-derived).

De-duplication: a trade is the key (symbol, entry hour, direction). Legs sharing a symbol and
timeframe (eth / eth_prop, sol / sol_prop) re-run the SAME trades; keep the first occurrence,
visiting legs by descending n. Different timeframes (1h vs 4h) are different trades and stay.

Statistic: pooled Spearman rho between the WITHIN-SYMBOL percentile of the feature and net_r
(a within-symbol rank so a symbol's volatility level cannot masquerade as a regime effect).
p_iid: net_r permuted WITHIN symbol (10,000). p_circular: net_r rotated by one random shift per
symbol per draw (10,000). Walk-forward: E38's `walk_forward` over 4 chronological pooled folds.
Decision thresholds live in the queue unit's decision_rule (registered before the run).
Tier-1 research code; fetches public candles, writes only under --out.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "research"))
sys.path.insert(0, str(REPO / "scripts" / "ops"))

import e38_regime_classification as E38  # noqa: E402
from regime_impact_scoping import newest_ledgers, load_ledger, leg_symbol  # noqa: E402

FEATURES = E38.FEATURES           # ("rv20", "er20")
ALPHA = 0.05 / len(FEATURES)      # Bonferroni over the two features
CIRC_MAX_P = E38.CIRC_MAX_P
N_DRAWS = 10_000
MDE_BAR = 0.10                    # a null is only "well powered" if MDE <= this
N_BLOCKS = 4
MIN_TRADES = 200


def dedupe(legs: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    seen, rows = set(), []
    for name in sorted(legs, key=lambda k: -len(legs[k])):
        for r in legs[name].itertuples():
            key = (r.symbol, pd.Timestamp(r.entry_time).floor("h"), r.direction)
            if key in seen:
                continue
            seen.add(key)
            rows.append((name, r.symbol, r.entry_time, r.direction, float(r.net_r)))
    df = pd.DataFrame(rows, columns=["leg", "symbol", "entry_time", "direction", "net_r"])
    return df.sort_values("entry_time").reset_index(drop=True)


def pooled_tests(tr: pd.DataFrame, feat: str, rng: np.random.Generator) -> dict:
    pct = tr.groupby("symbol")[feat].rank(pct=True).to_numpy()
    y = tr["net_r"].to_numpy()
    sym = tr["symbol"].to_numpy()
    groups = [np.where(sym == s)[0] for s in np.unique(sym)]
    ry = pd.Series(y).rank().to_numpy()
    rx = pd.Series(pct).rank().to_numpy()
    rho = float(np.corrcoef(rx, ry)[0, 1])
    hits_i = hits_c = 0
    for _ in range(N_DRAWS):
        yi = ry.copy()
        yc = ry.copy()
        for g in groups:
            yi[g] = yi[g][rng.permutation(len(g))]
            yc[g] = np.roll(yc[g], int(rng.integers(1, len(g))))
        hits_i += abs(float(np.corrcoef(rx, yi)[0, 1])) >= abs(rho) - 1e-12
        hits_c += abs(float(np.corrcoef(rx, yc)[0, 1])) >= abs(rho) - 1e-12
    return {"rho": round(rho, 4), "p_iid": round((hits_i + 1) / (N_DRAWS + 1), 5),
            "p_circular": round((hits_c + 1) / (N_DRAWS + 1), 5), "pct": pct}


def analyse(tr: pd.DataFrame, seed: int = 38) -> dict:
    rng = np.random.default_rng(seed)
    n = len(tr)
    out: dict = {"n": n, "n_by_symbol": tr["symbol"].value_counts().to_dict(),
                 "alpha_corrected": ALPHA, "min_detectable_abs_rho": E38.min_detectable_rho(n, ALPHA)}
    tr = tr.copy()
    tr["fold"] = np.repeat(np.arange(N_BLOCKS), int(np.ceil(n / N_BLOCKS)))[:n].astype(float)
    feats = {}
    for f in FEATURES:
        t = pooled_tests(tr, f, rng)
        tr[f + "_pct"] = t.pop("pct")
        feats[f] = t
    out["trade_level"] = feats
    def wf(f: str) -> dict:
        t2 = tr.copy()
        t2[f] = t2[f + "_pct"]          # E38's walk_forward reads the column named `f`
        return E38.walk_forward(t2, f)
    out["walk_forward"] = {f: wf(f) for f in FEATURES}
    passing = [f for f, t in feats.items() if t["p_iid"] < ALPHA and t["p_circular"] <= CIRC_MAX_P]
    if not passing:
        out["label"] = "no_regime_signal"
    else:
        f = min(passing, key=lambda k: feats[k]["p_iid"])
        out["feature"] = f
        out["label"] = "regime_dependence_detected" if out["walk_forward"][f]["pass"] else "in_sample_only"
    return out


def grade(res: dict) -> str:
    if res["label"] == "regime_dependence_detected":
        return "pass"
    if res["label"] == "no_regime_signal" and res["min_detectable_abs_rho"] <= MDE_BAR:
        return "fail"
    return "indeterminate"


def main(argv: Optional[List[str]] = None, *,
         daily_source: Callable = E38.fetch_daily) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--ledgers", default=str(REPO / "comms/strategy_evidence/runs/*/*__trades.jsonl"))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    import yaml
    st = yaml.safe_load((REPO / "config/strategies.yaml").read_text()) or {}
    st = st.get("strategies", st)

    legs, skipped = {}, {}
    for leg, path in sorted(newest_ledgers(args.ledgers).items()):
        if not leg.startswith("trend_donchian"):
            continue
        df = load_ledger(path)
        sym = leg_symbol(leg, df, st)
        if not sym.endswith("USDT"):
            skipped[leg] = f"symbol {sym!r} not a USDT perp"
            continue
        df = df.assign(symbol=sym)
        legs[leg] = df[["symbol", "entry_time", "direction", "net_r"]]
    dd = dedupe(legs) if legs else pd.DataFrame()
    daily: Dict[str, Optional[pd.DataFrame]] = {}
    rows = []
    for sym, g in dd.groupby("symbol") if len(dd) else []:
        daily[sym] = daily_source(sym, g["entry_time"].min().to_pydatetime() - pd.Timedelta(days=60),
                                  g["entry_time"].max().to_pydatetime() + pd.Timedelta(days=2))
        if daily[sym] is None:
            skipped[f"symbol:{sym}"] = "daily candles unreachable - no_data"
            continue
        f = [E38.features_at(daily[sym], t) for t in g["entry_time"]]
        g = g.assign(rv20=[x["rv20"] for x in f], er20=[x["er20"] for x in f]).dropna(subset=list(FEATURES))
        rows.append(g)
    tr = pd.concat(rows).sort_values("entry_time").reset_index(drop=True) if rows else pd.DataFrame()
    pop = (f"trend_donchian_* crypto legs, newest committed ledger each, de-duplicated by (symbol, entry hour, direction): "
           f"{len(legs)} legs -> {len(tr)} pooled trades; skipped {len(skipped)}; rv20/er20 daily features (E38)")
    if len(tr) < MIN_TRADES:
        v = {"verdict": "indeterminate", "read_state": "no_data", "population": pop, "n": len(tr),
             "measurement": {"skipped": skipped, "legs": sorted(legs)},
             "note": f"could_not_measure: {len(tr)} pooled trades < {MIN_TRADES}"}
    else:
        res = analyse(tr)
        v = {"verdict": grade(res), "read_state": "measured", "population": pop, "n": len(tr),
             "measurement": {**res, "skipped": skipped, "legs": sorted(legs),
                             "dedup_removed": int(sum(len(x) for x in legs.values()) - len(dd))},
             "note": (f"{res['label']}; MDE |rho| {res['min_detectable_abs_rho']} at alpha {ALPHA}. "
                      "Feature-level regime dependence of trend_donchian pooled; NOT a cell-authoring case "
                      "(walk-forward on the real cell is a separate Tier-3 gate).")}
    (out / "verdict.json").write_text(json.dumps(v, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"e38_pooled_trend_donchian: {v['verdict']} n={v['n']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
