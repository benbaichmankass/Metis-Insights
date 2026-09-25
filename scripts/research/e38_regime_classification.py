#!/usr/bin/env python3
# wiring: manual-only - a one-shot research measurement (checklist E38) a session RUNS to answer
# "does a regime explain this leg"; it gates nothing and no schedule should re-run it unregistered.
"""E38 — under what market conditions is each leg expected to perform?

Runs the method PRE-REGISTERED in ``docs/research/e38-regime/REGISTRATION.yaml``
(committed before this script existed) over every measured
``comms/strategy_evidence/*.json`` record, and writes
``docs/research/e38-regime/results.json`` + ``RESULTS.md``.

Every constant that decides a verdict is read from the registration file, so
the method run here cannot drift from the method registered without a diff to
that file.

Inputs:
  * per-trade series at each record's own ``source_run`` (net_r, entry_time)
  * the record's ``fold_detail`` (fold start/end)
  * daily USDT-M futures klines from data.binance.vision, fetched on demand
    (no committed per-symbol corpus exists yet -- checklist E4). The 5,001-row
    ``data/backtest_candles.csv`` fixture is NEVER read; a symbol that cannot be
    fetched makes its legs ``not_attempted``.

Usage:
    python3 scripts/research/e38_regime_classification.py            # full run
    python3 scripts/research/e38_regime_classification.py --current  # also call today's regime
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ops"))

OUT_DIR = REPO / "docs" / "research" / "e38-regime"
REG_PATH = OUT_DIR / "REGISTRATION.yaml"
EVIDENCE_GLOB = str(REPO / "comms" / "strategy_evidence" / "*.json")
FIXTURE = REPO / "data" / "backtest_candles.csv"

PRIMARY = [
    "trend_donchian_eth", "sol_pullback_2h", "trend_donchian_avax_4h",
    "trend_donchian_ada_4h", "trend_donchian_sol_4h", "trend_donchian_sol",
]
FEATURES = ("rv20", "er20")
N_PERM = 10_000
SEED = 38
CIRC_MAX_P = 0.10
MIN_TRAIN = 15


# --------------------------------------------------------------------------- data
def load_registration() -> dict:
    reg = yaml.safe_load(REG_PATH.read_text())
    assert tuple(reg["features"]) == FEATURES, "feature set drifted from registration"
    return reg


def fetch_daily(symbol: str, start: datetime, end: datetime) -> pd.DataFrame | None:
    """Daily klines for a USDT perp, or None when unreachable (never the fixture)."""
    if not symbol.endswith("USDT"):
        return None
    from fetch_backtest_candles import fetch_klines_binance_vision  # noqa: E402

    rows = fetch_klines_binance_vision(
        symbol, "D", int(start.timestamp() * 1000), int(end.timestamp() * 1000))
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["open_time"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    df["close"] = df["close"].astype(float)
    df["close_time"] = df["open_time"] + pd.Timedelta(days=1)
    return df[["open_time", "close_time", "close"]]


def features_at(daily: pd.DataFrame, t: pd.Timestamp) -> dict:
    """rv20 / er20 from daily bars CLOSED at or before t (no look-ahead)."""
    closed = daily[daily["close_time"] <= t]["close"].to_numpy()
    if len(closed) < 21:
        return {"rv20": None, "er20": None}
    c = closed[-21:]
    lr = np.diff(np.log(c))
    path = np.abs(np.diff(c)).sum()
    return {
        "rv20": float(np.std(lr)),
        "er20": float(abs(c[-1] - c[0]) / path) if path > 0 else 0.0,
    }


# --------------------------------------------------------------------------- stats
def spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    if rx.std() == 0 or ry.std() == 0:
        return 0.0
    return float(np.corrcoef(rx, ry)[0, 1])


def perm_p(x: np.ndarray, y: np.ndarray, rho: float, rng: np.random.Generator) -> float:
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    rx = (rx - rx.mean()) / rx.std()
    ry = (ry - ry.mean()) / ry.std()
    n = len(rx)
    hits = 0
    for _ in range(N_PERM):
        if abs(float(np.dot(rx, rng.permutation(ry)) / n)) >= abs(rho) - 1e-12:
            hits += 1
    return (hits + 1) / (N_PERM + 1)


def circular_p(x: np.ndarray, y: np.ndarray, rho: float) -> float:
    n = len(x)
    hits = sum(abs(spearman(x, np.roll(y, s))) >= abs(rho) - 1e-12 for s in range(1, n))
    return (hits + 1) / n


def min_detectable_rho(n: int, alpha: float) -> float:
    """|rho| a two-sided test at `alpha` can detect at all (Fisher-z, ~50% power)."""
    return round(math.tanh(NormalDist().inv_cdf(1 - alpha / 2) / math.sqrt(n - 3)), 3)


def walk_forward(tr: pd.DataFrame, feat: str) -> dict:
    folds = sorted(f for f in tr["fold"].dropna().unique())
    rows = []
    for k in folds:
        if k == folds[0]:
            continue
        train = tr[(tr["fold"] < k)]
        test = tr[tr["fold"] == k]
        if len(train) < MIN_TRAIN or test.empty:
            rows.append({"fold": int(k), "evaluated": False, "train_n": int(len(train))})
            continue
        rho = spearman(train[feat].to_numpy(), train["net_r"].to_numpy())
        thr = float(train[feat].median())
        on = test[feat] >= thr if rho > 0 else test[feat] < thr
        gated = float(test.loc[on, "net_r"].sum())
        ungated = float(test["net_r"].sum())
        rows.append({
            "fold": int(k), "evaluated": True, "train_n": int(len(train)),
            "train_rho": round(rho, 4), "threshold": round(thr, 6),
            "on_side": ">=" if rho > 0 else "<",
            "test_n": int(len(test)), "on_n": int(on.sum()),
            "gated_net_r": round(gated, 4), "ungated_net_r": round(ungated, 4),
            "improvement_r": round(gated - ungated, 4),
        })
    ev = [r for r in rows if r["evaluated"]]
    imp = sum(r["improvement_r"] for r in ev)
    wins = sum(r["gated_net_r"] > r["ungated_net_r"] for r in ev)
    gated_sum = sum(r["gated_net_r"] for r in ev)
    passed = (len(ev) >= 2 and imp > 0 and wins * 2 > len(ev) and gated_sum > 0)
    return {
        "feature": feat, "folds": rows, "n_evaluated": len(ev),
        "sum_improvement_r": round(imp, 4), "folds_improved": wins,
        "sum_gated_net_r": round(gated_sum, 4),
        "sum_ungated_net_r": round(sum(r["ungated_net_r"] for r in ev), 4),
        "pass": bool(passed),
    }


# --------------------------------------------------------------------------- per leg
def load_trades(rec: dict) -> pd.DataFrame:
    rows = [json.loads(ln) for ln in (REPO / rec["source_run"]).read_text().splitlines() if ln.strip()]
    tr = pd.DataFrame(rows)
    tr["entry_time"] = pd.to_datetime(tr["entry_time"], utc=True)
    tr = tr.sort_values("entry_time").reset_index(drop=True)
    tr["fold"] = np.nan
    for f in rec["fold_detail"]:
        s, e = pd.Timestamp(f["start"]), pd.Timestamp(f["end"])
        tr.loc[(tr["entry_time"] >= s) & (tr["entry_time"] <= e), "fold"] = f["fold"]
    return tr


def fold_descriptive(rec: dict, daily: pd.DataFrame) -> list[dict]:
    out = []
    for f in rec["fold_detail"]:
        s, e = pd.Timestamp(f["start"]), pd.Timestamp(f["end"])
        days = pd.date_range(s.normalize() + pd.Timedelta(days=1), e.normalize(), freq="D")
        vals = [features_at(daily, d) for d in days]
        rv = [v["rv20"] for v in vals if v["rv20"] is not None]
        er = [v["er20"] for v in vals if v["er20"] is not None]
        out.append({
            "fold": f["fold"], "start": f["start"], "end": f["end"],
            "trades": f["trades"], "net_r": f["net_r"], "won": f["net_r"] > 0,
            "mean_rv20": round(float(np.mean(rv)), 5) if rv else None,
            "mean_er20": round(float(np.mean(er)), 4) if er else None,
        })
    return out


def analyse_leg(rec: dict, daily: pd.DataFrame | None, reg: dict,
                rng: np.random.Generator) -> dict:
    base = {
        "leg": rec["strategy"], "symbol": rec["symbol"], "timeframe": rec["timeframe"],
        "source_run": rec["source_run"], "n_trades": rec["n_trades_oos"],
        "net_r_oos": rec["net_r_oos"], "folds": rec["folds"],
        "folds_positive": rec["folds_positive"],
        "family": "primary" if rec["strategy"] in PRIMARY else "secondary",
    }
    if daily is None:
        return {**base, "verdict": "not_attempted",
                "reason": f"daily candles for {rec['symbol']} not reachable this session "
                          "(non-USDT symbol: yfinance not installed, Yahoo returned 429)"}
    tr = load_trades(rec)
    feats = [features_at(daily, t) for t in tr["entry_time"]]
    for k in FEATURES:
        tr[k] = [f[k] for f in feats]
    missing = int(tr["rv20"].isna().sum())
    tr = tr.dropna(subset=list(FEATURES))
    n = len(tr)
    base["n_with_features"] = n
    base["n_dropped_no_history"] = missing
    base["fold_level"] = {
        "n_folds": len(rec["fold_detail"]),
        "verdict": "insufficient_n" if len(rec["fold_detail"]) < reg["fold_level_test"]["min_n_folds"] else "tested",
        "folds": fold_descriptive(rec, daily),
    }
    win = tr["net_r"] > 0
    base["won_vs_lost"] = {
        k: {"median_winners": round(float(tr.loc[win, k].median()), 5) if win.any() else None,
            "median_losers": round(float(tr.loc[~win, k].median()), 5) if (~win).any() else None,
            "n_winners": int(win.sum()), "n_losers": int((~win).sum())}
        for k in FEATURES
    }
    if n < reg["trade_level_test"]["min_n_trades"]:
        return {**base, "verdict": "insufficient_n", "_tr": tr}
    tests = {}
    for k in FEATURES:
        x, y = tr[k].to_numpy(), tr["net_r"].to_numpy()
        rho = spearman(x, y)
        tests[k] = {"rho": round(rho, 4), "p_iid": round(perm_p(x, y, rho, rng), 5),
                    "p_circular": round(circular_p(x, y, rho), 4)}
    base["trade_level"] = tests
    base["walk_forward"] = {k: walk_forward(tr, k) for k in FEATURES}
    base["_tr"] = tr
    return base


def decide(leg: dict, alpha: float) -> None:
    if leg.get("verdict"):
        return
    leg["alpha_corrected"] = alpha
    leg["min_detectable_abs_rho"] = min_detectable_rho(leg["n_with_features"], alpha)
    passing = [k for k, t in leg["trade_level"].items()
               if t["p_iid"] < alpha and t["p_circular"] <= CIRC_MAX_P]
    if not passing:
        leg["verdict"] = "no_regime_signal"
        return
    feat = min(passing, key=lambda k: leg["trade_level"][k]["p_iid"])
    leg["feature_used"] = feat
    if not leg["walk_forward"][feat]["pass"]:
        leg["verdict"] = "in_sample_only"
        return
    leg["verdict"] = "regime_explains"
    tr = leg["_tr"]
    rho = leg["trade_level"][feat]["rho"]
    thr = float(tr[feat].median())
    leg["regime_rule"] = {
        "feature": feat, "side": ">=" if rho > 0 else "<", "threshold": round(thr, 6),
        "statement": f"{leg['leg']} is expected to perform when daily {feat} "
                     f"{'>=' if rho > 0 else '<'} {thr:.5g} (threshold = full-record median)",
    }


# --------------------------------------------------------------------------- report
def _fmt(v) -> str:
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v)


def render_md(out: dict) -> str:
    """Generated table. Interpretation lives in the dated memo, not here."""
    lines = [
        "# E38 regime classification — generated results",
        "",
        "> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in "
        "[`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this "
        "document's status — do not act on it as current**",
        "",
        f"> Generated by `{out['generated_by']}` at {out['generated_at']} under "
        f"`{out['registration']}` ({out['registration_id']}). **Do not hand-edit** — "
        "re-run the script. Interpretation: [`../e38-regime-classification-2026-09-25.md`](../e38-regime-classification-2026-09-25.md).",
        "",
        "Population: every `comms/strategy_evidence/*.json` record with "
        "`coverage_state=measured`, per-trade `net_r` (full cost stack) from its own "
        "`source_run`. Features from data.binance.vision USDT-M daily closes closed "
        "at or before each entry. `—` = not computed (see verdict).",
        "",
        "| leg | family | n | folds + | verdict | rho rv20 | p rv20 (iid / circ) "
        "| rho er20 | p er20 (iid / circ) | min detectable abs rho | WF rv20 (Δ R, pass) | WF er20 (Δ R, pass) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    order = {"primary": 0, "secondary": 1}
    for leg in sorted(out["legs"], key=lambda x: (order[x["family"]], x["leg"])):
        tl, wf = leg.get("trade_level", {}), leg.get("walk_forward", {})
        cells = [f"`{leg['leg']}`", leg["family"], _fmt(leg.get("n_with_features", leg["n_trades"])),
                 f"{_fmt(leg['folds_positive'])}/{_fmt(leg['folds'])}", f"**{leg['verdict']}**"]
        for k in FEATURES:
            t = tl.get(k)
            cells += [_fmt(t and t["rho"]),
                      f"{t['p_iid']} / {t['p_circular']}" if t else "—"]
        cells.append(_fmt(leg.get("min_detectable_abs_rho")))
        for k in FEATURES:
            w = wf.get(k)
            cells.append(f"{w['sum_improvement_r']:+} ({'pass' if w['pass'] else 'fail'}, "
                         f"{w['n_evaluated']} folds)" if w else "—")
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "Not measured (no per-trade series to analyse): " + ", ".join(
        f"`{r['leg']}` ({r['coverage_state']})" for r in out["not_measured_records"]) + "."]
    if out.get("current_features"):
        lines += ["", "## Current daily features (the live read a regime rule would use)", "",
                  "| symbol | as of (last closed daily bar) | rv20 | er20 |", "|---|---|---|---|"]
        for sym, c in sorted(out["current_features"].items()):
            lines.append(f"| {sym} | {c['as_of']} | {_fmt(c['rv20'])} | {_fmt(c['er20'])} |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- self-test
def self_test() -> int:
    """Positive + negative control, offline: a quiet probe must be able to fire.

    Plants a known feature->net_r relationship on an autocorrelated synthetic
    feature (n=107, the largest primary leg) and requires the registered test to
    detect it; requires pure noise NOT to pass. No network, no fixture.
    """
    rng = np.random.default_rng(SEED)
    alpha = 0.05 / 12
    n = 107
    f = np.zeros(n)
    for i in range(1, n):
        f[i] = 0.9 * f[i - 1] + rng.normal()
    folds = np.repeat([1, 2, 3, 4], [26, 26, 26, 29])
    ok = True
    for label, y, expect in (
        ("planted", 0.8 * (f - f.mean()) / f.std() + rng.normal(size=n), True),
        ("noise", rng.normal(size=n), False),
    ):
        rho = spearman(f, y)
        p, pc = perm_p(f, y, rho, rng), circular_p(f, y, rho)
        wf = walk_forward(pd.DataFrame({"rv20": f, "net_r": y, "fold": folds}), "rv20")
        passed = p < alpha and pc <= CIRC_MAX_P and wf["pass"]
        print(f"{label}: rho={rho:.3f} p_iid={p:.5f} p_circ={pc:.3f} wf={wf['pass']} -> {passed}")
        ok &= passed == expect
    print("self-test", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--current", action="store_true",
                    help="also compute today's features per symbol and call each rule")
    ap.add_argument("--self-test", action="store_true",
                    help="offline positive/negative control of the registered test")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    reg = load_registration()
    rng = np.random.default_rng(SEED)

    recs = [json.load(open(f)) for f in sorted(glob.glob(EVIDENCE_GLOB))]
    measured = [r for r in recs if r.get("coverage_state") == "measured" and r.get("source_run")]
    skipped = [{"leg": r["strategy"], "coverage_state": r.get("coverage_state")}
               for r in recs if r not in measured]

    start = datetime(2025, 6, 1, tzinfo=timezone.utc)
    end = datetime.now(timezone.utc) + timedelta(days=1)
    daily: dict[str, pd.DataFrame | None] = {}
    provenance = {}
    for sym in sorted({r["symbol"] for r in measured}):
        try:
            df = fetch_daily(sym, start, end)
        except (OSError, RuntimeError, ValueError) as exc:  # unreachable is a stated state
            print(f"fetch {sym} failed: {exc}", file=sys.stderr)
            df = None
        daily[sym] = df
        if df is not None:
            provenance[sym] = {
                "rows": len(df), "first_open": str(df["open_time"].iloc[0]),
                "last_open": str(df["open_time"].iloc[-1]),
                "sha256_close": hashlib.sha256(
                    df["close"].round(8).to_numpy().tobytes()).hexdigest()[:16],
            }
        else:
            provenance[sym] = None

    legs = [analyse_leg(r, daily[r["symbol"]], reg, rng) for r in measured]
    for fam in ("primary", "secondary"):
        tested = [leg for leg in legs if leg["family"] == fam and not leg.get("verdict")]
        alpha = reg["trade_level_test"]["alpha_familywise"] / max(1, len(tested) * len(FEATURES))
        for leg in tested:
            decide(leg, alpha)

    current = {}
    if args.current:
        now = pd.Timestamp(datetime.now(timezone.utc))
        for sym, df in daily.items():
            if df is not None:
                current[sym] = {"as_of": str(df[df["close_time"] <= now]["close_time"].iloc[-1]),
                                **{k: (round(v, 6) if v is not None else None)
                                   for k, v in features_at(df, now).items()}}
        for leg in legs:
            if leg.get("verdict") == "regime_explains":
                r, c = leg["regime_rule"], current.get(leg["symbol"], {})
                v = c.get(r["feature"])
                leg["current_call"] = None if v is None else (
                    "ON" if (v >= r["threshold"] if r["side"] == ">=" else v < r["threshold"]) else "OFF")

    for leg in legs:
        leg.pop("_tr", None)
    out = {
        "registration": str(REG_PATH.relative_to(REPO)),
        "registration_id": reg["id"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_by": "scripts/research/e38_regime_classification.py",
        "fixture_read": False,
        "candle_provenance": provenance,
        "current_features": current or None,
        "not_measured_records": skipped,
        "legs": legs,
    }
    assert out["fixture_read"] is False and not any(
        str(FIXTURE) in json.dumps(x) for x in provenance.values())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "results.json").write_text(json.dumps(out, indent=1, default=str) + "\n")
    (OUT_DIR / "RESULTS.md").write_text(render_md(out))
    summary = pd.DataFrame([{
        "leg": leg["leg"], "family": leg["family"], "n": leg.get("n_with_features", leg["n_trades"]),
        "verdict": leg["verdict"],
        **{f"rho_{k}": leg.get("trade_level", {}).get(k, {}).get("rho") for k in FEATURES},
        **{f"p_{k}": leg.get("trade_level", {}).get(k, {}).get("p_iid") for k in FEATURES},
    } for leg in legs])
    print(summary.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
