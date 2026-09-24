#!/usr/bin/env python3
"""E62: what it takes to measure round-trip slippage for Alpaca equities and
IBKR futures, and how much of it can be measured today.

ONE QUESTION (checklist row E62, 2026-09-24). D3
(``scripts/research/realized_slippage.py``) measured Bybit perps and found the
equity and futures venues unmeasurable. It gave two reasons: too few real fills,
and 0 MEASURED Alpaca sl/tp exits. This script reuses D3's ``measure()``
unchanged (same reference prices, same sign, same MIN_N). On top of it, it
reports:

  A. ENTRY-side slippage per account, with a 95% bootstrap CI of the mean.
     Paper and live are reported separately. ib_paper is labelled a simulator.
     A side with n < MIN_N is reported as insufficient, and its figure is
     never used.
  A'. A sensitivity that drops entries created in the first 5 minutes of the
     US cash session (09:30 to 09:35 America/New_York). For a ``_1d`` leg the
     package's entry is the prior bar's level and the order fills at the open,
     so those rows measure the overnight gap, not the cost of executing. Both
     numbers are reported, and the headline is D3's (all entries).
  B'. An exit census: every sl/tp-family close, by the provenance class of its
     ``exit_price_source``. This is what the held Tier-2 fix changes.
  C. Accrual. For each venue: the rate at which usable round trips arrive, and
     the date n reaches 20 (D3's MIN_N) and 30. It uses an exact Poisson 95%
     interval on the rate. The live roster's forward rate is measured on the
     SAME legs' alpaca_paper history, because 3 live fills cannot bound a rate.

Read-only. It changes no default, no config and no evidence record.

Usage
-----
  python3 scripts/research/realized_slippage.py pull --out-dir D      # DIAG_READ_TOKEN
  python3 scripts/research/e62_slippage_accrual.py --in-dir D \\
      --as-of 2026-09-24T15:44:00Z \\
      --out comms/research/e62_equity_futures_slippage/2026-09-24.json
"""
from __future__ import annotations

# wiring: manual-only - a one-question measurement (checklist row E62); a session re-runs it by hand with the commands in the docstring, as D3's script is.

import argparse
import collections
import json
import math
import random
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "research"))

import realized_slippage as d3  # noqa: E402
from src.config.accounts_loader import load_accounts_dict  # noqa: E402
from src.runtime.provenance import classify  # noqa: E402

ACCOUNTS = ("alpaca_live", "alpaca_paper", "alpaca_portfolio", "ib_live", "ib_paper")
EXIT_FAMILY = {"sl", "sl_cross", "tp", "tp_cross", "giveback_stop"}
TARGETS = (d3.MIN_N, 30)
NY = ZoneInfo("America/New_York")
RATE_WINDOW_DAYS = 60


def _ts(s: str) -> datetime:
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _boot_mean_ci(xs: List[float], reps: int = 4000) -> Optional[List[float]]:
    if len(xs) < 2:
        return None
    rng = random.Random(20260924)  # D3's seed, so a re-run reproduces
    ms = sorted(statistics.fmean(xs[rng.randrange(len(xs))] for _ in xs) for _ in range(reps))
    return [round(ms[int(0.025 * reps)], 3), round(ms[int(0.975 * reps)], 3)]


def _poisson_cdf(k: int, mu: float) -> float:
    return sum(math.exp(-mu + i * math.log(mu) - math.lgamma(i + 1)) for i in range(k + 1)) if mu > 0 else 1.0


def _poisson_ci(k: int) -> List[float]:
    """Exact (Garwood) 95% interval on a Poisson count, by bisection."""
    def solve(f, lo, hi):
        for _ in range(200):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(mid) else (lo, mid)
        return (lo + hi) / 2
    lower = 0.0 if k == 0 else solve(lambda mu: 1 - _poisson_cdf(k - 1, mu) < 0.025, 0.0, 10.0 * k + 10)
    upper = solve(lambda mu: _poisson_cdf(k, mu) > 0.025, 0.0, 10.0 * k + 20)
    return [lower, upper]


def _side_stats(xs: List[float], account_class: str) -> dict:
    out = d3._dist(xs)
    out["mean_bootstrap_ci95"] = _boot_mean_ci(xs)
    out["basis"] = "simulator fill model" if account_class != "real_money" else "real market"
    out["state"] = ("measured" if len(xs) >= d3.MIN_N
                    else f"INSUFFICIENT: n={len(xs)} < {d3.MIN_N}; not usable")
    return out


def _is_open_window(created_at: str) -> bool:
    t = _ts(created_at).astimezone(NY)
    return t.hour == 9 and 30 <= t.minute < 35


def _projection(k: int, days: float, have: int, as_of: datetime) -> dict:
    """Rate of k events over `days`, and when `have` reaches each target."""
    if days <= 0:
        return {"events": k, "days": days, "rate_per_day": None}
    lo, hi = (x / days for x in _poisson_ci(k))
    rate = k / days
    out = {"events": k, "days": round(days, 2), "rate_per_day": round(rate, 4),
           "rate_ci95_per_day": [round(lo, 4), round(hi, 4)], "have_now": have, "reach": {}}
    for tgt in TARGETS:
        need = max(0, tgt - have)
        def when(r):
            if need == 0:
                return as_of.date().isoformat()
            return None if r <= 0 else (as_of + timedelta(days=need / r)).date().isoformat()
        out["reach"][f"n{tgt}"] = {"point": when(rate), "earliest_ci": when(hi), "latest_ci": when(lo)}
    return out


def run(in_dir: Path, as_of: datetime) -> dict:
    acfg = load_accounts_dict()
    m = d3.measure(in_dir)
    rows = m["rows"]
    trades = json.load(open(in_dir / "trades.json"))

    per_account: Dict[str, dict] = {}
    for acct in ACCOUNTS:
        cfg = acfg.get(acct, {})
        klass = cfg.get("account_class") or "unknown"
        ent = [r for r in rows if r["account_id"] == acct and r["side"] == "entry"]
        ex = [r for r in rows if r["account_id"] == acct and r["side"] == "exit"
              and r["ref_source"] == "order_packages"]
        e_all = [r["bps"] for r in ent]
        e_ex_open = [r["bps"] for r in ent if not _is_open_window(r["created_at"])]
        census = collections.Counter()
        for t in trades:
            if t.get("account_id") != acct or t.get("is_backtest") or t.get("status") != "closed":
                continue
            if str(t.get("exit_reason") or "") not in EXIT_FAMILY:
                continue
            src = d3._notes(t).get("exit_price_source")
            census[f"{classify(src, 'exit_price_source')}:{src}"] += 1
        per_account[acct] = {
            "account_class": klass, "mode": cfg.get("mode"),
            "roster": cfg.get("strategies") or [],
            "venue": d3.VENUE_OF.get(cfg.get("exchange"), cfg.get("exchange")),
            "entry": _side_stats(e_all, klass),
            "entry_excluding_session_open_5min": {
                **_side_stats(e_ex_open, klass),
                "dropped": len(e_all) - len(e_ex_open)},
            "exit_package_referenced": _side_stats([r["bps"] for r in ex], klass),
            "exit_family_close_census_by_provenance": dict(sorted(census.items())),
        }

    # ---- C. accrual -------------------------------------------------------
    since = as_of - timedelta(days=RATE_WINDOW_DAYS)
    live_roster = set(acfg.get("alpaca_live", {}).get("strategies") or [])

    def closes(acct: str, legs: Optional[set] = None) -> List[dict]:
        return [t for t in trades if t.get("account_id") == acct and not t.get("is_backtest")
                and t.get("status") == "closed" and t.get("closed_at")
                and str(t.get("exit_reason") or "") in EXIT_FAMILY
                and (legs is None or t.get("strategy_name") in legs)]

    def entries(acct: str, legs: Optional[set] = None) -> List[dict]:
        return [r for r in rows if r["account_id"] == acct and r["side"] == "entry"
                and (legs is None or r["strategy"] in legs)]

    def in_window(ts: str) -> bool:
        return _ts(ts) >= since

    have_live_rt = 0  # 0 MEASURED alpaca_live sl/tp exits today (census above)
    accrual = {
        "rate_window": [since.isoformat(), as_of.isoformat()],
        "definition": ("a usable ROUND TRIP = a closed sl/tp-family trade whose entry AND exit "
                       "are both MEASURED against a package reference. Today alpaca exits are "
                       "never MEASURED (structural), so alpaca's usable count is 0 regardless of "
                       "trades; the projection below is what accrues AFTER the held fix deploys."),
        "alpaca_live": {
            "basis": ("forward rate of the CURRENT alpaca_live roster, measured on the SAME legs' "
                      f"alpaca_paper history over the last {RATE_WINDOW_DAYS}d (3 live fills cannot "
                      "bound a rate). alpaca_paper runs each leg at paper size; INFERRED that "
                      "live signal frequency matches (same strategy, same symbol, same bars)."),
            "roster": sorted(live_roster),
            "entries": _projection(
                sum(1 for r in entries("alpaca_paper", live_roster) if in_window(r["created_at"])),
                RATE_WINDOW_DAYS, len(entries("alpaca_live")), as_of),
            "round_trips": _projection(
                sum(1 for t in closes("alpaca_paper", live_roster) if in_window(t["closed_at"])),
                RATE_WINDOW_DAYS, have_live_rt, as_of),
            "observed_on_alpaca_live_itself": {
                "entries": len(entries("alpaca_live")),
                "sl_tp_closes": len(closes("alpaca_live")),
                "first_entry": min((r["created_at"] for r in entries("alpaca_live")), default=None),
            },
        },
        "ib_live": {
            "basis": "mode dry_run with an EMPTY strategies roster: it produces no fills at any rate",
            "rate_per_day": 0.0, "reach": {f"n{t}": "never, until the operator puts a leg on a "
                                                   "live ib_live roster (Tier-3)" for t in TARGETS},
        },
        "simulators_for_reference": {
            acct: {
                "entries": _projection(sum(1 for r in entries(acct) if in_window(r["created_at"])),
                                       RATE_WINDOW_DAYS, len(entries(acct)), as_of),
                "sl_tp_closes": _projection(sum(1 for t in closes(acct) if in_window(t["closed_at"])),
                                            RATE_WINDOW_DAYS,
                                            per_account[acct]["exit_package_referenced"]["n"], as_of),
            } for acct in ("alpaca_paper", "alpaca_portfolio", "ib_paper")
        },
        "caveat_alpaca_portfolio": ("its roster was cut 14 -> 2 legs on 2026-09-21 (plan item B2), "
                                    "so its 60d rate is the OLD roster's and overstates the forward "
                                    "rate; its 2 legs are a subset of the alpaca_live roster above"),
    }
    return {
        "question": ("E62: what does it take to produce an evidence-backed round-trip slippage "
                     "figure for Alpaca equities and IBKR futures, and how much can be done now?"),
        "generated_by": "scripts/research/e62_slippage_accrual.py (reuses realized_slippage.measure)",
        "as_of": as_of.isoformat(),
        "reference_price": "identical to D3: order_packages.entry / .sl / .tp; positive bps = adverse",
        "min_n_per_side": d3.MIN_N,
        "per_account": per_account,
        "accrual": accrual,
        "excluded_counts": m["excluded"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--in-dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--as-of", required=True, help="ISO time of the pull")
    a = ap.parse_args()
    res = run(a.in_dir, _ts(a.as_of))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    json.dump(res, open(a.out, "w"), indent=1)
    print(json.dumps({k: {"entry": (v["entry"].get("n"), v["entry"].get("mean"),
                                    v["entry"].get("mean_bootstrap_ci95")),
                          "exit_census": v["exit_family_close_census_by_provenance"]}
                      for k, v in res["per_account"].items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
