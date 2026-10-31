#!/usr/bin/env python3
# wiring: research/templates/leg-corpus-s1.yaml -> research/queue units via queue_replenish.py -> research-script-run.yml
"""ML-LINE-S1-CORPUS -- the STANDING per-leg Stage-0 corpus producer (station S1 of the ML line).

Docs: docs/plans/ml-pipeline-design-2026-10-29.md sections 1.2 and 2 (S1). For every MONEY leg of one
FAMILY it regenerates a config-exact, cost-net harness corpus on the free runner and writes, per leg,

    <out>/<family>/<leg>/trades.jsonl   --emit-trades rows (net_r + cost_fee_r / cost_slippage_r / cost_funding_r)
    <out>/<family>/<leg>/paths.jsonl    --emit-paths rows (per-bar state of the exit actually traded; --no-paths skips)
    <out>/<family>/<leg>/bt.json        the harness summary
    <out>/<family>/<leg>/meta.json      what the station metrics are read from (below)
    <out>/verdict.json, manifest.json   the research-script-run.yml contract

MONEY LEG (re-parsed from config on every run, never quoted): status ``production`` (scripts/ops/strategy_status.py)
or rostered on an ``account_class: prop`` account, and ``enabled`` is not false. FAMILY = ``<harness>_<timeframe>``
(Donchian trend 1h / 4h, pullback 2h, pullback 1d ...). A family with no money leg is simply absent.

COST STACK: the harness charges fee + slippage + funding itself (execution_costs.resolve_cost_policy); this runner
passes no cost override. It NEVER uses scripts/backtest_orb.py (fee only) or src/backtest/backtester.py (own cost
dict, no funding) -- CLAUDE.md. The argv is built by exit_lever_ablation.build_arm_argv (the committed-record builder).

STATION METRICS (computed from the meta.json files, ``station_metrics``):
  coverage      = money legs with a corpus <= 7 days old AND bt.json timeframe == config / money legs
  fidelity      = legs whose harness ``params`` agree with the leg's config on every shared key / legs run
  cost complete = trades with fee, slippage and funding all non-null / trades
A leg that could not be run is reported with its reason; it is never counted as covered (collapsed-states rule).

Tier-1 research tooling: public candles in, files out. No config write, no order path, no broker state.

    python3 scripts/research/leg_corpus_s1.py --family trend_4h --out comms/research/x
    python3 scripts/research/leg_corpus_s1.py --list-families
    python3 scripts/research/leg_corpus_s1.py --self-test
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
for _p in (str(REPO), str(REPO / "scripts"), str(REPO / "scripts" / "research"), str(REPO / "scripts" / "ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FRESH_DAYS = 7
COST_FIELDS = ("cost_fee_r", "cost_slippage_r", "cost_funding_r")
#: bt.json ``params`` key -> leg-config key, for keys both carry under the same name or a known alias.
PARAM_ALIASES: Dict[str, str] = {
    "donchian": "donchian", "atr_period": "atr_period", "atr_stop_mult": "atr_stop_mult",
    "trail_mult": "trail_mult", "min_confidence": "min_confidence", "trend_lookback": "trend_lookback",
    "pullback_lookback": "pullback_lookback", "pullback_frac": "pullback_frac",
}


def _yaml(rel: str, root: Path) -> Dict[str, Any]:
    import yaml
    return yaml.safe_load((root / rel).read_text(encoding="utf-8")) or {}


def money_legs(root: Path = REPO) -> Dict[str, Dict[str, Any]]:
    """{leg: strategies.yaml block} for every money leg. Re-parsed from config each call."""
    import strategy_status as ss
    accounts = _yaml("config/accounts.yaml", root).get("accounts") or {}
    strategies = _yaml("config/strategies.yaml", root).get("strategies") or {}
    ros = ss.rosters(accounts)
    prop = {leg for a, cfg in accounts.items() if isinstance(cfg, dict) and cfg.get("account_class") == "prop"
            for leg in ros.get(a, [])}
    out: Dict[str, Dict[str, Any]] = {}
    for leg, cfg in sorted(strategies.items()):
        if not isinstance(cfg, dict) or cfg.get("enabled") is False:
            continue
        if ss.status_of(leg, ros, strategies) == ss.PRODUCTION or leg in prop:
            out[leg] = cfg
    return out


def family_of(cfg: Dict[str, Any]) -> str:
    import regime_debt_matrix as rdm
    return f"{rdm.classify(cfg)}_{cfg.get('timeframe')}"


def money_families(root: Path = REPO) -> Dict[str, List[str]]:
    fam: Dict[str, List[str]] = {}
    for leg, cfg in money_legs(root).items():
        fam.setdefault(family_of(cfg), []).append(leg)
    return {k: sorted(v) for k, v in sorted(fam.items())}


def config_hash(cfg: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:16]


def params_agreement(bt: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Harness ``params`` vs the leg config on every shared key. ``hash_equal`` is the fidelity bit."""
    params = bt.get("params") or {}
    a, b = {}, {}
    for pk, ck in PARAM_ALIASES.items():
        if pk in params and cfg.get(ck) is not None:
            a[pk] = float(params[pk])
            b[pk] = float(cfg[ck])
    h = lambda d: hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]  # noqa: E731
    return {"keys": sorted(a), "harness_hash": h(a), "config_hash": h(b), "hash_equal": bool(a) and a == b,
            "mismatch": {k: [a[k], b[k]] for k in a if a[k] != b[k]}}


def cost_completeness(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(trades)
    ok = sum(1 for t in trades if all(t.get(f) is not None for f in COST_FIELDS))
    return {"trades": n, "complete": ok, "share": (round(ok / n, 4) if n else None)}


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def leg_meta(leg: str, cfg: Dict[str, Any], family: str, trades: List[Dict[str, Any]], bt: Dict[str, Any],
             now: datetime, feed: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    tf_ok = str(bt.get("timeframe")) == str(cfg.get("timeframe"))
    pa = params_agreement(bt, cfg)
    cc = cost_completeness(trades)
    return {"leg": leg, "family": family, "symbol": (cfg.get("symbols") or [None])[0],
            "config_timeframe": cfg.get("timeframe"), "bt_timeframe": bt.get("timeframe"), "timeframe_equal": tf_ok,
            "config_hash": config_hash(cfg), "params": pa, "cost": cc, "n_trades": len(trades),
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "feed": feed or {}, "state": "ok"}


def station_metrics(legs: List[str], metas: Dict[str, Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    """coverage / fidelity / cost completeness over the money-leg list ``legs`` (the denominator)."""
    covered, ran, fid, tr, cc = [], 0, 0, 0, 0
    reasons: Dict[str, str] = {}
    for leg in legs:
        m = metas.get(leg)
        if not m or m.get("state") != "ok":
            reasons[leg] = (m or {}).get("reason", "no corpus")
            continue
        ran += 1
        age_ok = now - datetime.strptime(m["generated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) \
            <= timedelta(days=FRESH_DAYS)
        if age_ok and m["timeframe_equal"] and m["n_trades"] > 0:
            covered.append(leg)
        else:
            reasons[leg] = "stale" if not age_ok else ("timeframe mismatch" if not m["timeframe_equal"] else "0 trades")
        fid += 1 if m["params"]["hash_equal"] else 0
        tr += m["cost"]["trades"]
        cc += m["cost"]["complete"]
    d = len(legs)
    return {"population": f"{d} money legs (re-parsed from config)", "coverage": {"covered": len(covered), "of": d,
            "share": round(len(covered) / d, 4) if d else None, "legs": covered, "not_covered": reasons},
            "fidelity": {"hash_equal": fid, "of": ran, "share": round(fid / ran, 4) if ran else None},
            "cost_completeness": {"complete": cc, "of": tr, "share": round(cc / tr, 4) if tr else None}}


def run_family(family: str, out: Path, *, days: int, start: Optional[str], end: Optional[str], paths: bool,
               non_crypto_source: str, root: Path = REPO) -> int:
    import path_metric_giveback as pmg
    fams = money_families(root)
    legs = fams.get(family, [])
    cfgs = money_legs(root)
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    metas: Dict[str, Dict[str, Any]] = {}
    for leg in legs:
        d = out / family / leg
        d.mkdir(parents=True, exist_ok=True)
        try:
            rows, info = pmg.run_leg_paths(leg, cfgs[leg], days=days, start=start, end=end, wd=d, data_csv=None,
                                           non_crypto_source=non_crypto_source)
            for src, dst in ((f"{leg}__trades.jsonl", "trades.jsonl"), (f"{leg}__bt.json", "bt.json"),
                             (f"{leg}__paths.jsonl", "paths.jsonl")):
                (d / dst).write_bytes((d / src).read_bytes())
                (d / src).unlink()
            (d / f"{leg}__data.csv").unlink(missing_ok=True)
            if not paths:
                (d / "paths.jsonl").unlink(missing_ok=True)
            trades = read_jsonl(d / "trades.jsonl")
            metas[leg] = leg_meta(leg, cfgs[leg], family, trades, info["bt"], now,
                                  {k: v for k, v in info["feed"].items() if k in ("source", "interval", "resample")})
        except Exception as exc:  # noqa: BLE001 -- contained per leg, reported (no-halting rule)
            metas[leg] = {"leg": leg, "family": family, "state": "producer_failed", "reason": repr(exc)[:400]}
        (d / "meta.json").write_text(json.dumps(metas[leg], indent=2, sort_keys=True))
        print(f"{leg:26} {metas[leg]['state']:16} n={metas[leg].get('n_trades')} "
              f"tf_equal={metas[leg].get('timeframe_equal')} fid={metas[leg].get('params', {}).get('hash_equal')}")
    sm = station_metrics(legs, metas, now)
    ok = [m for m in metas.values() if m["state"] == "ok"]
    n = sum(m["n_trades"] for m in ok)
    full = bool(legs) and sm["coverage"]["covered"] == len(legs)
    verdict = {"verdict": "pass" if full else ("indeterminate" if ok else "not_applicable" if not legs else "indeterminate"),
               "read_state": ("measured" if ok else ("not_attempted" if not legs else "producer_failed")),
               "n": n if ok else None, "population": f"family {family}: {len(legs)} money legs, {days} d window",
               "note": ("every money leg covered" if full else f"not covered: {sm['coverage']['not_covered']}"),
               "measurement": sm}
    (out / "verdict.json").write_text(json.dumps(verdict, indent=2, sort_keys=True))
    (out / "manifest.json").write_text(json.dumps({"family": family, "legs": legs, "metas": metas,
                                                    "station_metrics": sm}, indent=2, sort_keys=True))
    return 0


def _self_test() -> int:
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    cfg = {"timeframe": "4h", "symbols": ["ETHUSDT"], "donchian": 20, "atr_stop_mult": 2.5}
    tr = [{"cost_fee_r": 0.1, "cost_slippage_r": 0.0, "cost_funding_r": 0.0}, {"cost_fee_r": 0.1, "cost_slippage_r": None,
          "cost_funding_r": 0.0}]
    good = leg_meta("a", cfg, "trend_4h", tr, {"timeframe": "4h", "params": {"donchian": 20, "atr_stop_mult": 2.5}}, now)
    bad_tf = leg_meta("b", cfg, "trend_4h", tr, {"timeframe": "1h", "params": {"donchian": 20, "atr_stop_mult": 2.5}}, now)
    bad_p = leg_meta("c", cfg, "trend_4h", tr, {"timeframe": "4h", "params": {"donchian": 21, "atr_stop_mult": 2.5}}, now)
    old = dict(good, generated_at="2026-10-20T00:00:00Z")
    sm = station_metrics(["a", "b", "c", "d", "e"], {"a": good, "b": bad_tf, "c": bad_p, "e": old}, now)
    assert sm["coverage"]["covered"] == 2 and sm["coverage"]["of"] == 5, sm          # a, c; b tf, d absent, e stale
    assert sm["coverage"]["not_covered"]["b"] == "timeframe mismatch" and sm["coverage"]["not_covered"]["e"] == "stale"
    assert sm["fidelity"] == {"hash_equal": 3, "of": 4, "share": 0.75}, sm["fidelity"]  # a, b, e equal; c mismatched
    assert sm["cost_completeness"]["share"] == 0.5
    assert cost_completeness([])["share"] is None
    print("self-test ok")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--family")
    ap.add_argument("--out")
    ap.add_argument("--days", type=int, default=2330)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--no-paths", action="store_true")
    ap.add_argument("--non-crypto-source", choices=("auto", "dukascopy"), default="auto")
    ap.add_argument("--list-families", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if a.list_families:
        print(json.dumps(money_families(), indent=2))
        return 0
    if not a.family or not a.out:
        ap.error("--family and --out are required")
    return run_family(a.family, Path(a.out), days=a.days, start=a.start, end=a.end, paths=not a.no_paths,
                      non_crypto_source=a.non_crypto_source)


if __name__ == "__main__":
    sys.exit(main())
