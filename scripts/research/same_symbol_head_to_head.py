#!/usr/bin/env python3
# wiring: research/queue/RQ-*-SSA-*.yaml (run.command, via research-script-run.yml) + tests/test_same_symbol_head_to_head.py
"""Same-symbol head-to-head — the BACKTEST-QUALITY INPUT per leg on a shared (account, symbol).

OPERATOR, 2026-10-15 (re-scope ~20:25Z): "We can have different strategies working with netted positions as long as we
know how much of that position belongs to each strategy ... we just need to make sure we know how to allocate between
the trades correctly. And it should be based on those other things, not just backtesting. It's just that backtesting is
also a parameter here."  So this PICKS NOTHING. It produces one input to the EXISTING allocator
(``src/runtime/allocator_ev.py`` / ``allocator_corr.py`` / ``allocator_soak.py`` / ``conviction_*``), beside the factors
those already compute.

HARNESS: ``scripts/backtest_system.py::run_system_backtest`` — the REAL ``aggregate_intents`` election, ONE shared netted
position per symbol, each strategy's REAL ``order_package``/``monitor``, and the venue-aware cost stack. Arms:

  * ``solo:<leg>``   each leg alone — the INDEPENDENT-SLICE proxy (own full slot, no netting). A true per-slice
                     simulation needs lane NETTED-SLICES' slice model; until it lands this is the proxy, stated as such;
  * ``together``     all legs under the live election — the SHARED-SLOT book.

REPORT RULE (registered before the run): report each leg's solo net-R per capital-day and the combined book's. No winner,
no exclusion verdict. ``pass`` = all arms measured at >= ``min_oos_trades``; else ``indeterminate`` naming the starved arm.

  net R          = sum over OOS closed trades of pnl / (qty * |entry - entry_stop|)   (pnl net of fee+slippage+funding)
  capital-days   = sum over OOS closed trades of (exit_ts - entry_ts) in days
  metric         = net R / capital-days          (OOS = entries on/after ``oos_start``)

COST STACK TRAP (do not remove): ``backtest_system`` module globals ``SLIPPAGE_BPS_ROUNDTRIP`` /
``FUNDING_BPS_PER_WINDOW`` default to 0.0 and are only set from ``execution_costs`` inside its ``main()``;
``apply_cost_policy`` sets them here and records the policy.

Output: ``<out>/verdict.json`` (script_run contract) and ``<out>/head_to_head.json``. Tier-1 research tooling.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_REPO = Path(__file__).resolve().parents[2]

SECONDS_PER_DAY = 86400.0


def metric_from_trades(trades: Sequence[Dict[str, Any]], oos_start: Any) -> Dict[str, Any]:
    """OOS net-R per capital-day from plain trade dicts ``{entry_ts, exit_ts, qty, entry, sl, pnl}``."""
    import pandas as pd

    cut = pd.Timestamp(oos_start, tz="UTC") if not hasattr(oos_start, "tzinfo") else pd.Timestamp(oos_start)
    net_r = 0.0
    cap_days = 0.0
    n = 0
    skipped = 0
    for t in trades:
        et = pd.Timestamp(t["entry_ts"])
        if et.tzinfo is None:
            et = et.tz_localize("UTC")
        if et < cut:
            continue
        risk = abs(float(t["qty"]) * (float(t["entry"]) - float(t["sl"])))
        if risk <= 0:
            skipped += 1
            continue
        xt = pd.Timestamp(t["exit_ts"])
        if xt.tzinfo is None:
            xt = xt.tz_localize("UTC")
        net_r += float(t["pnl"]) / risk
        cap_days += max((xt - et).total_seconds(), 0.0) / SECONDS_PER_DAY
        n += 1
    return {
        "n_oos": n, "n_skipped_zero_risk": skipped,
        "net_r": round(net_r, 4), "capital_days": round(cap_days, 4),
        "net_r_per_capital_day": round(net_r / cap_days, 6) if cap_days > 0 else None,
    }


def decide(arms: Dict[str, Dict[str, Any]], min_oos_trades: int) -> Dict[str, Any]:
    """The registered REPORT rule, pure. It ranks nothing and excludes nothing.

    ``pass`` = every arm measured with >= ``min_oos_trades`` OOS trades, so the per-leg backtest-quality inputs are
    usable by the allocator. ``indeterminate`` names the starved arm. There is deliberately no ``winner``.
    """
    solos = {k: v for k, v in arms.items() if k.startswith("solo:")}
    if "together" not in arms or len(solos) < 2:
        return {"verdict": "indeterminate", "reason": "need together + >=2 solo arms"}
    starved = sorted(k for k, v in arms.items()
                     if v.get("n_oos", 0) < min_oos_trades or v.get("net_r_per_capital_day") is None)
    if starved:
        return {"verdict": "indeterminate",
                "reason": f"arm(s) below min_oos_trades={min_oos_trades} or zero capital-days: {starved}"}
    return {"verdict": "pass", "reason": "all arms measured; per-leg inputs reported, no exclusion",
            "per_leg_net_r_per_capital_day": {k.split(":", 1)[1]: v["net_r_per_capital_day"] for k, v in solos.items()},
            "combined_book_net_r_per_capital_day": arms["together"]["net_r_per_capital_day"]}


def apply_cost_policy(bs: Any, symbol: str) -> Dict[str, Any]:
    """Set backtest_system's module cost globals the way its main() does; return the policy for the record."""
    ec = bs.execution_costs
    bs.SLIPPAGE_BPS_ROUNDTRIP = ec.slippage_bps_roundtrip_for(symbol)
    bs.FUNDING_BPS_PER_WINDOW = ec.funding_bps_per_window_for(symbol)
    return {"fee_bps_roundtrip": bs.FEE_BPS_ROUNDTRIP, "slippage_bps_roundtrip": bs.SLIPPAGE_BPS_ROUNDTRIP,
            "funding_bps_per_window": bs.FUNDING_BPS_PER_WINDOW, "funding_window_hours": bs.FUNDING_WINDOW_HOURS}


def _load_bs() -> Any:
    sys.path.insert(0, str(_REPO))
    import importlib.util
    spec = importlib.util.spec_from_file_location("backtest_system", _REPO / "scripts" / "backtest_system.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["backtest_system"] = mod  # dataclasses need the module registered
    spec.loader.exec_module(mod)
    return mod


def _trade_dicts(closed: Sequence[Any]) -> List[Dict[str, Any]]:
    return [{"entry_ts": t.entry_ts, "exit_ts": t.exit_ts, "qty": t.qty, "entry": t.entry,
             "sl": t.sl if t.sl is not None else t.entry, "pnl": t.pnl} for t in closed]


FETCH_START = "2023-01-01"


def fetch_5m(symbol: str, dest: Path, start: str) -> Optional[Path]:
    """Public Binance-Vision USD-M perp 5m klines -> ``<dest>/<SYMBOL>_5m.csv``; None when the archive returns nothing
    (the caller lands that as producer_failed -- "could not look", never an empty result)."""
    import time

    import pandas as pd
    import importlib.util
    spec = importlib.util.spec_from_file_location("_fetch_candles", _REPO / "scripts" / "ops" / "fetch_backtest_candles.py")
    fbc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fbc)
    start_ms = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp() * 1000)
    rows = fbc.fetch_klines_binance_vision(symbol, "5", start_ms, int(time.time() * 1000))
    if not rows:
        return None
    dest.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([{k: r[k] for k in ("timestamp", "open", "high", "low", "close", "volume")} for r in rows])
    path = dest / f"{symbol}_5m.csv"
    df.to_csv(path, index=False)
    return path


def run(unit_path: Path, out_dir: Path) -> int:
    import yaml

    unit = yaml.safe_load(unit_path.read_text())
    h = unit["head_to_head"]
    legs: List[str] = list(h["legs"])
    symbol: str = h["symbol"]
    out_dir.mkdir(parents=True, exist_ok=True)

    def finish(verdict: Dict[str, Any], population: str, n: Any, **extra: Any) -> int:
        rec = {"unit": unit["id"], "symbol": symbol, "legs": legs, "accounts": h.get("accounts"), **extra, **verdict}
        (out_dir / "head_to_head.json").write_text(json.dumps(rec, indent=2, default=str))
        (out_dir / "verdict.json").write_text(json.dumps({
            "verdict": verdict["verdict"], "read_state": extra.get("read_state", "measured"),
            "population": population, "n": n, "measurement": rec, "note": verdict.get("reason")}, default=str))
        return 0

    def fault(reason: str) -> int:
        # A route/harness fault is not a verdict (the unit's own rule). research_result's contract:
        # read_state producer_failed -> verdict not_applicable, n null. Until 2026-10-16 these exits
        # wrote read_state 'unavailable' + verdict 'indeterminate', which the collector refused as a
        # malformed verdict.json, so the REASON never reached the record (RQ-20261015-1631-01/-02).
        return finish({"verdict": "not_applicable", "reason": reason}, f"{symbol} {legs}", None,
                      read_state="producer_failed")

    bs = _load_bs()
    missing = [leg for leg in legs if leg not in bs.ROSTER]
    if missing:
        return fault(f"legs not registered in backtest_system.ROSTER: {missing} — harness gap, not a verdict")
    from src.research import risk_basis
    from src.runtime.intents import aggregate_intents  # noqa: F401  (import proves the live election is reachable)

    try:
        src = bs._backtest_data_source.resolve_or_refuse(symbol, "5m", h.get("data"))
    except (OSError, ValueError, KeyError) as exc:  # allow-silent: not swallowed -- landed as read_state=producer_failed with the reason in verdict.json
        return fault(f"data source: {exc}")
    candles_path = src.path if src.ok else None
    if candles_path is None:
        # A runner has no data/ directory (RQ-20261015-1631-01/-02, RQ-20261018-001 all landed producer_failed
        # on this). Fetch the public Binance-Vision USD-M 5m archive -- keyless, not geoblocked.
        try:
            candles_path = fetch_5m(symbol, out_dir / "candles", h.get("fetch_start", FETCH_START))
        except (OSError, RuntimeError, ValueError) as exc:  # allow-silent: landed as read_state=producer_failed with the reason in verdict.json
            return fault(f"no 5m candle source for the symbol ({getattr(src, 'reason', 'unresolved')}); fetch failed: {exc}")
        if candles_path is None:
            return fault("no 5m candle source for the symbol: " + str(getattr(src, "reason", "unresolved"))
                         + "; Binance-Vision returned no rows")
    from src.research.run_costs import resolve_run_costs
    cost = resolve_run_costs(symbol)  # the full fee+slippage+funding stack, applied via costs= below
    live = risk_basis.live_risk(h.get("risk_account", risk_basis.DEFAULT_REFERENCE_ACCOUNT))
    if not live.ok or live.percent is None:
        return fault(f"live risk unresolved: {live.describe()}")
    base5m = bs._load_candles(str(candles_path))

    def one(roster: List[str]) -> Dict[str, Any]:
        s = bs.run_system_backtest(
            base5m, roster=roster, start=h.get("start"), end=h.get("end"),
            initial_balance=float(h.get("initial_balance", 10_000.0)), risk_pct=float(live.percent),
            daily_loss_pct=float(h.get("daily_loss_pct", 3.0)), signal_ttl_bars=int(h.get("signal_ttl_bars", 1)),
            overrides={}, refresh=False, clock_tf=h.get("clock_tf", "15m"), attach_full=True, symbol=symbol,
            costs=cost, require_full_costs=True)
        return metric_from_trades(_trade_dicts(s["closed_trades"]), h["oos_start"])

    arms = {f"solo:{leg}": one([leg]) for leg in legs}
    arms["together"] = one(legs)
    verdict = decide(arms, int(h.get("min_oos_trades", 20)))
    return finish(verdict, f"{symbol} legs={legs} OOS from {h['oos_start']} (candles {candles_path})",
                  {k: v["n_oos"] for k, v in arms.items()}, arms=arms, cost_policy=cost,
                  risk_pct=live.percent, oos_start=h["oos_start"])


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--unit", required=True, help="queue unit id (RQ-...) or a YAML path")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    p = Path(a.unit)
    if not p.exists():
        p = _REPO / "research" / "queue" / f"{a.unit}.yaml"
    return run(p, Path(a.out))


if __name__ == "__main__":
    sys.exit(main())
