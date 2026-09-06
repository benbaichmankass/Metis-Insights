#!/usr/bin/env python3
# wiring: manual-only - an investigative probe a session runs against a JOURNAL DUMP when it
# is about to quote or act on an R-based figure (a promotion verdict, a review headline).
# Deliberately NOT scheduled: it takes the `rows` arrays from /api/bot/db/table/* as files so
# it can run off-VM, and a cron that quietly recomputed a contamination share nobody read
# would be the "written and never consumed" failure this repo already paid for once.
"""Reproduce ``/api/bot/performance`` R aggregates, then decompose them by R-provenance.

WHY THIS EXISTS
---------------
On 2026-09-06 the 30d real-money window published ``expectancyR +0.9818`` while its
own ``totalPnl`` was ``-3.6266`` and ``profitFactor`` ``0.95``. Both cannot be true.
``R`` feeds the promotion gates, so a wrong-signed ``expectancyR`` makes every
promote/demote verdict computed from it unsafe.

The mechanism is structural, not a data-entry error. ``_clean_trades.r_multiple``
computes ``pnl / (|entry - stop| * |qty| * contract_value)``. The ``abs()`` means a
stop stored on the WRONG SIDE of entry still yields a positive risk, so such a row
produces a finite R instead of being refused; and ``trades.stop_loss`` holds the
FINAL stop (``order_monitor._apply_update`` writes trailing amends into it), so a
trade that trailed through breakeven stores a stop beyond entry, ``|entry - stop|``
collapses toward zero, and R explodes. R is defined against ENTRY-TIME risk; the
column holds EXIT-TIME stop.

WHAT IT PRINTS, AND WHAT EACH PART IS FOR
-----------------------------------------
1. A REPRODUCTION of the endpoint's own headline fields (n / wins / totalPnl /
   totalR) on the population the endpoint actually uses -- ``demo=False`` means
   REAL MONEY ONLY, plus the three documented exclusions. Without this the
   decomposition would describe a lookalike population rather than the instrument.
2. The R decomposition by ``src.runtime.r_provenance.classify_r`` state.
3. The ``|R| > threshold`` control, which is what makes the discriminator
   credible rather than fitted: on 2026-09-06 it separated the two states
   completely (35 contaminated, 0 confirmed_initial).

Provenance vocabulary is IMPORTED from ``src.runtime.r_provenance``; this script
does not re-derive it (CLAUDE.md forbids a second copy).

Read-only. Takes rows from a JSON dump so it can run off-VM:
    python3 scripts/research/r_contamination_audit.py --trades t.json --packages p.json
where each file is the ``rows`` array from ``/api/bot/db/table/{trades,order_packages}``.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from src.runtime.local_pnl import contract_value_usd_for  # noqa: E402
from src.runtime.r_provenance import (  # noqa: E402
    R_CONFIRMED_INITIAL,
    R_CONTAMINATED,
    R_NO_BASIS,
    R_UNVERIFIED,
    classify_r,
)
from src.web.api._clean_trades import account_class_wire, r_multiple  # noqa: E402

# The endpoint's own exclusions, mirrored. Each drops rows that are not strategy
# exit decisions; see _clean_trades for the incident behind each one.
_RECONCILER_PSEUDO = ("orphan_adopt",)


def _parse(value):
    """ISO-ish timestamp -> aware datetime, or None. Never raises."""
    if not value:
        return None
    text = str(value).replace(" ", "T").replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def close_time(trade, packages):
    """The endpoint's close-time basis: closed_at, else the package's updated_at,
    else the row timestamp. Mirrors ``close_time_sql``."""
    package = packages.get(trade.get("order_package_id")) or {}
    return (
        _parse(trade.get("closed_at"))
        or _parse(package.get("updated_at"))
        or _parse(trade.get("timestamp"))
    )


def in_endpoint_population(trade, packages, since, real_money_only):
    """True when the endpoint would aggregate this row."""
    if trade.get("status") != "closed" or trade.get("is_backtest"):
        return False
    if trade.get("pnl") is None:
        return False
    if real_money_only:
        wire = account_class_wire(trade.get("account_class"), trade.get("is_demo"))
        if wire != "real_money":
            return False
    if (trade.get("strategy_name") or "") in _RECONCILER_PSEUDO:
        return False
    if (trade.get("reconcile_status") or "") == "superseded":
        return False
    if (trade.get("exit_reason") or "") == "exchange_reset_flat":
        return False
    if since is None:
        return True
    closed = close_time(trade, packages)
    return bool(closed and closed >= since)


def audit(trades, packages, since, real_money_only, control_threshold):
    """Return (population, per-state buckets, per-state |R|>threshold counts)."""
    population = [
        t for t in trades
        if in_endpoint_population(t, packages, since, real_money_only)
    ]
    # [count, sum_r, sum_pnl] per r_provenance state.
    buckets = collections.defaultdict(lambda: [0, 0.0, 0.0])
    control = collections.Counter()
    for trade in population:
        package = packages.get(trade.get("order_package_id")) or {}
        state, _reason = classify_r({
            "direction": trade.get("direction"),
            "entry_price": trade.get("entry_price"),
            "stop_loss": trade.get("stop_loss"),
            "take_profit_1": trade.get("take_profit_1"),
            "qty": trade.get("position_size"),
            "package_meta": package.get("meta"),
        })
        r_value = r_multiple(
            trade["pnl"], trade.get("entry_price"), trade.get("stop_loss"),
            trade.get("position_size"), contract_value_usd_for(trade.get("symbol")),
        )
        buckets[state][0] += 1
        buckets[state][2] += float(trade["pnl"])
        if r_value is not None:
            buckets[state][1] += r_value
            if abs(r_value) > control_threshold:
                control[state] += 1
    return population, buckets, control


def report(population, buckets, control, label, control_threshold):
    total_r = sum(b[1] for b in buckets.values())
    total_pnl = sum(float(t["pnl"]) for t in population)
    wins = sum(1 for t in population if float(t["pnl"]) > 0)
    print(f"\n=== {label} ===")
    print(f"POPULATION n={len(population)}  wins={wins}  "
          f"totalPnl={total_pnl:+.4f}  totalR={total_r:+.2f}")
    if not population:
        print("  (empty population -- this is 'we selected nothing', NOT 'nothing is wrong')")
        return
    print(f"\n  {'r_provenance':20s} {'n':>5} {'sum R':>11} {'sum PnL':>11} "
          f"{'|R|>' + str(control_threshold):>8}")
    for state in (R_CONTAMINATED, R_CONFIRMED_INITIAL, R_UNVERIFIED, R_NO_BASIS):
        count, sum_r, sum_pnl = buckets[state]
        if count:
            print(f"  {state:20s} {count:5d} {sum_r:11.2f} {sum_pnl:11.2f} "
                  f"{control[state]:8d}")
    contaminated_n, contaminated_r, _ = buckets[R_CONTAMINATED]
    if contaminated_n and total_r:
        clean_r = total_r - contaminated_r
        clean_n = len(population) - contaminated_n
        print(f"\n  contaminated: {contaminated_n} rows = "
              f"{100 * contaminated_n / len(population):.1f}% of the window, "
              f"carrying {100 * contaminated_r / total_r:.1f}% of its R")
        if clean_n:
            print(f"  EXCLUDING them: totalR {clean_r:+.2f} over {clean_n} rows "
                  f"-> expectancyR {clean_r / clean_n:+.3f}")
    # The control is the reason to believe the discriminator. Report it as a
    # separation, and say plainly when it does NOT separate.
    if control[R_CONTAMINATED] and not control[R_CONFIRMED_INITIAL]:
        print(f"  CONTROL: |R|>{control_threshold} occurs {control[R_CONTAMINATED]}x among "
              f"contaminated and 0x among confirmed_initial -- total separation.")
    else:
        print(f"  CONTROL: |R|>{control_threshold} occurs {control[R_CONTAMINATED]}x "
              f"contaminated / {control[R_CONFIRMED_INITIAL]}x confirmed_initial -- "
              f"NOT a clean separation on this population; do not quote it as one.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trades", required=True, type=pathlib.Path)
    ap.add_argument("--packages", required=True, type=pathlib.Path)
    ap.add_argument("--days", type=int, default=30,
                    help="lookback for the windowed pass; 0 = no since filter")
    ap.add_argument("--control-threshold", type=float, default=10.0,
                    help="|R| above which a row is treated as a blow-up for the control")
    args = ap.parse_args(argv)

    trades = json.loads(args.trades.read_text())
    packages = {p["order_package_id"]: p
                for p in json.loads(args.packages.read_text())}
    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=args.days) if args.days else None

    pop, buckets, control = audit(trades, packages, None, False, args.control_threshold)
    report(pop, buckets, control,
           "WHOLE JOURNAL (closed, non-backtest, pnl NOT NULL, all accounts)",
           args.control_threshold)

    pop, buckets, control = audit(trades, packages, since, True, args.control_threshold)
    report(pop, buckets, control,
           f"REPRODUCING /api/bot/performance?window={args.days}d "
           f"(real money only + the 3 documented exclusions)",
           args.control_threshold)
    print("\nCompare the second block against the live endpoint before trusting it: "
          "if n / wins / totalPnl / totalR do not match, you are decomposing a "
          "lookalike population, not the instrument.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
