#!/usr/bin/env python3
"""M31 P4 — backtest<->live MFE parity.

THE DEFECT FAMILY THIS EXISTS TO CATCH is one shape: *the harness measured a
book production does not run*. Every bug in the tp-cap family
(`BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP`,
`BL-20260816-TRAIL-DECAY-ARM-R-SITS-ABOVE-THE-VENUE-TP-CAP`) is an instance,
and until `position_telemetry` shipped (M31 P2, 2026-08-16) there was **no live
measurement of the same quantity to check the harness against**. The harness
emits `mfe_r`; live emits `peak_r`; same definition, same leg.

TWO CHECKS, DELIBERATELY SEPARATE — they need different denominators:

* **Check A — ceiling (per ROW, works at n=1).** A live trade's `peak_r` must
  not exceed its own `cap_r`, the venue TP ceiling that ends the trade. A row
  above its cap means the TP did not fill or the cap is mis-modelled. This is a
  per-row invariant, so it is gradeable the day telemetry starts writing.
* **Check B — distribution (needs SOAK DEPTH).** The harness `mfe_r`
  distribution vs the live `peak_r` distribution for the same leg. This needs
  FINAL (post-close) live MFE at n, and it abstains until it has it.

Reporting only Check B would make P4 look blocked; reporting only Check A would
overclaim that parity has been established. Both ship, each with its own state.

THREE THINGS THIS REFUSES TO DO, each a defect it would otherwise commit:

1. **It never grades a live row whose lifecycle it does not know.** A row in
   `position_telemetry` is UPSERTed on every exit pass and carries no status;
   when the trade closes the row simply stops being updated. So "open" and
   "closed" are byte-identical from the table alone, and `peak_r` on an OPEN
   trade is not that trade's MFE — it is a partial. Grading without the
   `trades` join would bias the live distribution DOWNWARD by exactly the
   trades that have not peaked yet. Pass `--trades-json` or Check B reports
   `live_lifecycle_unknown` and grades nothing.
2. **It never compares against an UNCAPPED harness book.** `backtest_trend.py`
   defaults `tp_cap_pct=0.0` (no take-profit exit path at all), and an
   uncapped book's `mfe_r` runs past the venue ceiling by construction. A
   comparison against that is not a parity failure, it is a category error —
   `harness_uncapped`, graded nothing.
3. **It never treats live `peak_r` as MFE-final.** Even on a closed trade the
   last write precedes the close by up to one exit-loop pass, so live
   `peak_r` is a **LOWER BOUND**. Every Check-B output carries
   `live_peak_is_lower_bound: true`, and a divergence is only ever called in
   the direction that bound permits.

Both sides read a BAR EXTREME (harness: `df.high`/`df.low` per bar; live:
`trail_decay.since_entry_peak` over the same window shape), so neither sees an
intrabar excursion. That is what makes them comparable at all — and it is why
`peak_provenance` is `estimated` on every live row, never `measured`.

Usage
-----
    # Check A only (no harness input needed):
    m31_mfe_parity.py --live-json live.json

    # Both checks:
    m31_mfe_parity.py --live-json live.json --trades-json trades.json \\
                      --harness-emit emit_*.jsonl

    # Prove the probe can find a positive before trusting a quiet result:
    m31_mfe_parity.py --self-test

`--live-json` accepts the `/api/bot/db/table/position_telemetry` envelope (or a
bare row list). `--trades-json` accepts the `/api/bot/db/table/trades` envelope
or any row list carrying `id` + `status`. `--harness-emit` takes the JSONL
`backtest_trend.py --emit-trades` writes (`strategy` + `mfe_r` per row).

Tier 1 — research tooling. Reads nothing live, writes no config, changes no
exit. It reports; every lever value stays Tier-3.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

# The live TP cap production actually places. Declared in >=4 modules; this
# module does NOT re-derive it, it only reports rows that carry `cap_r`
# already computed by the writer (src/runtime/position_telemetry.py::cap_r).
# A second definition here would be free to drift from the enforcing one.
# The venue TP clamp -- ONE owner: src/runtime/tp_venue_cap.py. IMPORTED, not
# mirrored. This file used to carry its own `LIVE_TP_CAP_PCT = 0.099`, one of
# thirteen such literals with nothing binding them -- and this repo's own note
# on that was right: "if the live constant moves, this silently keeps measuring
# the OLD book, and the sweep will look correct while doing it". The owner
# imports only `typing`, and src/__init__.py + src/runtime/__init__.py are
# empty, so this adds no heavy dependency.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.runtime.tp_venue_cap import (  # noqa: E402
    TP_VENUE_CAP_PCT as LIVE_TP_CAP_PCT)

# Below this many FINAL live rows a leg's distribution is not a distribution.
# A CHOSEN floor, not a measured one — stated so it is not read as tuned.
DEFAULT_MIN_FINAL_N = 8

# ---------------------------------------------------------------------------
# Parity states. Never collapsed: each says WHY a leg was not graded, so an
# ungraded leg can never read as a passing one.
# ---------------------------------------------------------------------------
CEILING_STATES = (
    "within_cap",        # peak_r <= cap_r on every row: the invariant holds
    "above_cap",         # a row exceeded its own venue ceiling — a finding
    "no_cap",            # cap_r absent/unusable: we could not look
    "no_rows",           # the leg has no telemetry at all
)
PARITY_STATES = (
    "compared",                # both sides present and gradeable
    "harness_absent",          # no harness rows for this leg
    "harness_uncapped",        # harness swept with no TP — not comparable
    "live_absent",             # no telemetry rows
    "live_lifecycle_unknown",  # rows exist, but open-vs-closed is unknowable
    "live_no_final_rows",      # we looked: every row is still open
    "insufficient_n",          # final rows exist but below the floor
)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        out = float(v)
        return out if out == out else None  # NaN -> None
    except (TypeError, ValueError):
        return None


def _pct(values: Sequence[float], q: float) -> Optional[float]:
    """Nearest-rank percentile. No interpolation, no scipy, no fabricated tail."""
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[k]


def _rows(payload: Any) -> List[Dict[str, Any]]:
    """Accept a Data-Explorer envelope or a bare list.

    ASSERTS `filter_state` when the envelope carries one
    (BL-20260813-DB-EXPLORER-SILENTLY-IGNORES-UNKNOWN-FILTER-COLUMN): a dropped
    filter returns the WHOLE table under a `total` that reads as a match count.
    """
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    fs = payload.get("filter_state")
    if fs is not None and fs not in ("applied", "not_requested"):
        raise SystemExit(
            f"refusing to read rows: filter_state={fs!r} — the server DROPPED "
            "the filter, so `total` is the unfiltered count and these rows are "
            "not the population you asked for.")
    rows = payload.get("rows")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_jsonl(paths: Iterable[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


# ---------------------------------------------------------------------------
# Check A — ceiling. Per row; gradeable at n=1.
# ---------------------------------------------------------------------------
def check_ceiling(live_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    per_leg: Dict[str, Dict[str, Any]] = {}
    for r in live_rows:
        leg = r.get("strategy") or "(unattributed)"
        peak, cap = _f(r.get("peak_r")), _f(r.get("cap_r"))
        b = per_leg.setdefault(leg, {
            "rows": 0, "graded": 0, "no_cap": 0, "above_cap": 0,
            "max_peak_over_cap_pct": None, "breaches": [],
        })
        b["rows"] += 1
        if peak is None or cap is None or cap <= 0:
            b["no_cap"] += 1
            continue
        b["graded"] += 1
        ratio = 100.0 * peak / cap
        if b["max_peak_over_cap_pct"] is None or ratio > b["max_peak_over_cap_pct"]:
            b["max_peak_over_cap_pct"] = round(ratio, 2)
        if peak > cap:
            b["above_cap"] += 1
            b["breaches"].append({
                "order_package_id": r.get("order_package_id"),
                "trade_id": r.get("trade_id"),
                "peak_r": peak, "cap_r": cap,
                "peak_over_cap_pct": round(ratio, 2),
            })

    for leg, b in per_leg.items():
        if b["rows"] == 0:
            b["ceiling_state"] = "no_rows"
        elif b["graded"] == 0:
            b["ceiling_state"] = "no_cap"
        elif b["above_cap"] > 0:
            b["ceiling_state"] = "above_cap"
        else:
            b["ceiling_state"] = "within_cap"
    return per_leg


# ---------------------------------------------------------------------------
# Check B — distribution. Needs final (closed) live rows.
# ---------------------------------------------------------------------------
def _closed_trade_ids(trades_rows: Sequence[Dict[str, Any]]) -> Optional[set]:
    """Ids of trades that are NOT open. `None` when we were given nothing.

    `None` and `set()` are different facts and must not collapse: `None` = we
    could not look, `set()` = we looked and every trade is still open.
    """
    if not trades_rows:
        return None
    out = set()
    for t in trades_rows:
        tid = t.get("id")
        status = (t.get("status") or "").strip().lower()
        if tid is None or not status:
            continue
        if status != "open":
            out.add(str(tid))
    return out


def check_distribution(
    live_rows: Sequence[Dict[str, Any]],
    harness_rows: Sequence[Dict[str, Any]],
    closed_ids: Optional[set],
    *,
    harness_tp_cap_pct: Optional[float],
    min_final_n: int = DEFAULT_MIN_FINAL_N,
    harness_dist: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    live_by_leg: Dict[str, List[Dict[str, Any]]] = {}
    for r in live_rows:
        live_by_leg.setdefault(r.get("strategy") or "(unattributed)", []).append(r)

    # The harness side arrives as EITHER raw emit rows OR a committed per-leg
    # distribution (`m31_harness_mfe_dist.py`). Both are reduced to the same
    # per-leg stats here so the comparison below has ONE shape — and `source`
    # travels with them, because "a fresh sweep" and "a committed artifact" are
    # different provenance for the same claim and a reader must be able to tell.
    harness_stats: Dict[str, Dict[str, Any]] = {}
    if harness_dist:
        for leg, rec_d in harness_dist.items():
            n = int(rec_d.get("n") or 0)
            harness_stats[leg] = {
                "n": n,
                "p50": _f(rec_d.get("p50")), "p80": _f(rec_d.get("p80")),
                "max": _f(rec_d.get("max")),
                # Per-leg, NOT the global flag: a committed artifact can hold
                # legs swept under different settings, and one uncapped leg
                # must not condemn (or be excused by) its neighbours.
                "tp_cap_pct": _f(rec_d.get("tp_cap_pct")),
                "symbol": rec_d.get("symbol"),
                "timeframe": rec_d.get("timeframe"),
                "source": "committed_dist",
            }
    else:
        raw: Dict[str, List[float]] = {}
        for h in harness_rows:
            v = _f(h.get("mfe_r"))
            if v is not None:
                raw.setdefault(h.get("strategy") or "(unattributed)", []).append(v)
        for leg, hv in raw.items():
            harness_stats[leg] = {
                "n": len(hv), "p50": _pct(hv, 0.50), "p80": _pct(hv, 0.80),
                "max": max(hv), "tp_cap_pct": harness_tp_cap_pct,
                "symbol": None, "timeframe": None, "source": "emit_rows",
            }

    out: Dict[str, Any] = {}
    for leg in sorted(set(live_by_leg) | set(harness_stats)):
        rows = live_by_leg.get(leg, [])
        hs = harness_stats.get(leg)
        rec: Dict[str, Any] = {
            "live_rows": len(rows),
            "harness_n": (hs["n"] if hs else 0),
            "harness_source": (hs["source"] if hs else None),
            # ALWAYS stamped: the last telemetry write precedes the close, so a
            # closed row's peak_r is a floor on that trade's true MFE.
            "live_peak_is_lower_bound": True,
        }
        if hs and hs.get("symbol"):
            rec["harness_symbol"] = hs["symbol"]
            rec["harness_timeframe"] = hs["timeframe"]

        # Harness gate first — an uncapped book is not comparable at all.
        leg_cap = hs["tp_cap_pct"] if hs else harness_tp_cap_pct
        if leg_cap is not None and leg_cap <= 0.0:
            rec["parity_state"] = "harness_uncapped"
            rec["why"] = ("harness swept with tp_cap_pct<=0: no take-profit exit "
                          "path, so mfe_r runs past the venue ceiling by "
                          "construction")
            out[leg] = rec
            continue
        if not hs or hs["n"] <= 0:
            rec["parity_state"] = "harness_absent"
            out[leg] = rec
            continue
        if not rows:
            rec["parity_state"] = "live_absent"
            out[leg] = rec
            continue
        if closed_ids is None:
            rec["parity_state"] = "live_lifecycle_unknown"
            rec["why"] = ("position_telemetry carries no status and is UPSERTed "
                          "per pass, so open-vs-closed is unknowable without the "
                          "trades join; pass --trades-json")
            out[leg] = rec
            continue

        final = [_f(r.get("peak_r")) for r in rows
                 if str(r.get("trade_id")) in closed_ids]
        final = [v for v in final if v is not None]
        rec["live_final_n"] = len(final)
        if not final:
            rec["parity_state"] = "live_no_final_rows"
            out[leg] = rec
            continue
        if len(final) < min_final_n:
            rec["parity_state"] = "insufficient_n"
            rec["min_final_n"] = min_final_n
            out[leg] = rec
            continue

        rec.update({
            "parity_state": "compared",
            "live_p50": _pct(final, 0.50), "live_p80": _pct(final, 0.80),
            "live_max": max(final),
            "harness_p50": hs["p50"], "harness_p80": hs["p80"],
            "harness_max": hs["max"],
        })
        # DIRECTIONAL, because the live figure is a lower bound: live coming in
        # BELOW the harness is expected and not gradeable. Live ABOVE the
        # harness max is the finding — the harness modelled a ceiling the live
        # book exceeded, which is the tp-cap family's exact shape.
        rec["parity"] = "divergent" if rec["live_max"] > rec["harness_max"] else "consistent"
        out[leg] = rec
    return out


def run(live_rows, harness_rows, trades_rows, harness_tp_cap_pct, min_final_n,
        harness_dist=None):
    closed = _closed_trade_ids(trades_rows)
    ceiling = check_ceiling(live_rows)
    dist = check_distribution(
        live_rows, harness_rows, closed,
        harness_tp_cap_pct=harness_tp_cap_pct, min_final_n=min_final_n,
        harness_dist=harness_dist)
    breaches = sum(b["above_cap"] for b in ceiling.values())
    compared = sum(1 for r in dist.values() if r.get("parity_state") == "compared")
    divergent = sum(1 for r in dist.values() if r.get("parity") == "divergent")
    return {
        "live_rows_total": len(live_rows),
        "harness_rows_total": len(harness_rows),
        "trades_rows_total": len(trades_rows),
        "lifecycle_known": closed is not None,
        "closed_trade_ids_seen": (len(closed) if closed is not None else None),
        "harness_tp_cap_pct": harness_tp_cap_pct,
        # Which harness half served this run. A committed distribution and a
        # fresh sweep are different provenance for the same claim.
        "harness_side": ("committed_dist" if harness_dist else
                         ("emit_rows" if harness_rows else "absent")),
        "harness_dist_legs": (len(harness_dist) if harness_dist else 0),
        "ceiling": ceiling,
        "distribution": dist,
        "summary": {
            "legs_seen": len(set(ceiling) | set(dist)),
            "ceiling_breaches": breaches,
            "legs_compared": compared,
            "legs_divergent": divergent,
        },
    }


# ---------------------------------------------------------------------------
# Self-test. RULE ONE: show the probe finds a positive before trusting a quiet
# result. A checker that cannot fail is not evidence of anything.
# ---------------------------------------------------------------------------
def self_test() -> int:
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(f"  {name}: {'PASS' if cond else 'FAIL'}")
        ok = ok and cond

    print("m31-mfe-parity self-test")

    # 1 — a real ceiling breach is FLAGGED (the probe can find a positive).
    r = run([{"strategy": "leg_a", "peak_r": 5.0, "cap_r": 4.0,
              "order_package_id": "pkg-x", "trade_id": "1"}], [], [], 0.099, 8)
    check("1 ceiling breach is flagged",
          r["summary"]["ceiling_breaches"] == 1
          and r["ceiling"]["leg_a"]["ceiling_state"] == "above_cap")

    # 2 — a clean book is within_cap, NOT silently ungraded.
    r = run([{"strategy": "leg_a", "peak_r": 2.0, "cap_r": 4.0, "trade_id": "1"}],
            [], [], 0.099, 8)
    check("2 clean book grades within_cap",
          r["ceiling"]["leg_a"]["ceiling_state"] == "within_cap"
          and r["summary"]["ceiling_breaches"] == 0)

    # 3 — a missing cap is `no_cap`, never a pass.
    r = run([{"strategy": "leg_a", "peak_r": 2.0, "cap_r": None, "trade_id": "1"}],
            [], [], 0.099, 8)
    check("3 missing cap is no_cap, not a pass",
          r["ceiling"]["leg_a"]["ceiling_state"] == "no_cap")

    # 4 — NO trades join ⇒ lifecycle unknown ⇒ graded nothing.
    live = [{"strategy": "leg_a", "peak_r": 2.0, "cap_r": 9.0, "trade_id": str(i)}
            for i in range(20)]
    harness = [{"strategy": "leg_a", "mfe_r": 3.0}] * 50
    r = run(live, harness, [], 0.099, 8)
    check("4 no lifecycle ⇒ live_lifecycle_unknown",
          r["distribution"]["leg_a"]["parity_state"] == "live_lifecycle_unknown"
          and r["summary"]["legs_compared"] == 0)

    # 5 — all trades OPEN ⇒ live_no_final_rows (looked, nothing final).
    trades_open = [{"id": str(i), "status": "open"} for i in range(20)]
    r = run(live, harness, trades_open, 0.099, 8)
    check("5 all-open ⇒ live_no_final_rows",
          r["distribution"]["leg_a"]["parity_state"] == "live_no_final_rows")

    # 6 — below the floor ⇒ insufficient_n, never a pass.
    trades_few = ([{"id": "0", "status": "closed"}]
                  + [{"id": str(i), "status": "open"} for i in range(1, 20)])
    r = run(live, harness, trades_few, 0.099, 8)
    check("6 below floor ⇒ insufficient_n",
          r["distribution"]["leg_a"]["parity_state"] == "insufficient_n")

    # 7 — an UNCAPPED harness is refused outright.
    trades_all = [{"id": str(i), "status": "closed"} for i in range(20)]
    r = run(live, harness, trades_all, 0.0, 8)
    check("7 uncapped harness ⇒ harness_uncapped",
          r["distribution"]["leg_a"]["parity_state"] == "harness_uncapped")

    # 8 — with depth, a CONSISTENT book compares clean.
    r = run(live, harness, trades_all, 0.099, 8)
    d = r["distribution"]["leg_a"]
    check("8 depth + consistent ⇒ compared/consistent",
          d["parity_state"] == "compared" and d["parity"] == "consistent")

    # 9 — live ABOVE the harness ceiling is DIVERGENT (the tp-cap shape).
    live_hi = [{"strategy": "leg_a", "peak_r": 9.0, "cap_r": 12.0, "trade_id": str(i)}
               for i in range(20)]
    r = run(live_hi, harness, trades_all, 0.099, 8)
    check("9 live above harness max ⇒ divergent",
          r["distribution"]["leg_a"]["parity"] == "divergent"
          and r["summary"]["legs_divergent"] == 1)

    # 10 — the lower-bound caveat is stamped on EVERY distribution record.
    check("10 lower-bound caveat always stamped",
          all(v.get("live_peak_is_lower_bound") is True
              for v in r["distribution"].values()))

    # 11-14 — the COMMITTED-distribution harness half
    # (PB-20260817-NO-COMMITTED-PER-TRADE-HARNESS-MFE). Same verdicts as raw
    # emit rows, plus the two refusals a committed artifact makes possible.
    dist_ok = {"leg_a": {"leg": "leg_a", "symbol": "SOLUSDT", "timeframe": "4h",
                         "tp_cap_pct": 0.099, "n": 50, "p50": 3.0, "p80": 3.0,
                         "max": 3.0}}
    r = run(live, [], trades_all, 0.099, 8, harness_dist=dist_ok)
    d = r["distribution"]["leg_a"]
    check("11 a committed distribution compares like emit rows",
          d["parity_state"] == "compared" and d["harness_max"] == 3.0
          and d["harness_source"] == "committed_dist"
          and r["harness_side"] == "committed_dist")

    # The instrument identity must SURVIVE into the report, or a reader cannot
    # tell a 4h SOL distribution from a 1m BTC one.
    check("12 the distribution's symbol/timeframe reach the report",
          d.get("harness_symbol") == "SOLUSDT"
          and d.get("harness_timeframe") == "4h")

    # PER-LEG cap, not the global flag: an uncapped leg is refused on its own
    # even while the run's global cap says the live value.
    dist_uncapped = {"leg_a": dict(dist_ok["leg_a"], tp_cap_pct=0.0)}
    r = run(live, [], trades_all, 0.099, 8, harness_dist=dist_uncapped)
    check("13 an uncapped LEG is refused even when the global cap is live",
          r["distribution"]["leg_a"]["parity_state"] == "harness_uncapped")

    # A leg present in the artifact with n=0 is ABSENT, never a comparison
    # against an empty distribution.
    dist_empty = {"leg_a": dict(dist_ok["leg_a"], n=0)}
    r = run(live, [], trades_all, 0.099, 8, harness_dist=dist_empty)
    check("14 a zero-n leg is harness_absent, not compared",
          r["distribution"]["leg_a"]["parity_state"] == "harness_absent")

    print("SELF-TEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live-json", help="position_telemetry rows (envelope or list)")
    ap.add_argument("--trades-json", help="trades rows (id+status) for the lifecycle join")
    ap.add_argument("--harness-emit", nargs="*", default=[],
                    help="backtest_trend.py --emit-trades JSONL file(s)")
    ap.add_argument("--harness-dist", default=None,
                    help="COMMITTED per-leg mfe_r distribution written by "
                         "m31_harness_mfe_dist.py (percentiles + n, not raw "
                         "rows). Mutually exclusive with --harness-emit: two "
                         "harness sources at once is ambiguous, and silently "
                         "preferring one would make the report's provenance a "
                         "function of argument order.")
    ap.add_argument("--harness-tp-cap-pct", type=float, default=LIVE_TP_CAP_PCT,
                    help=("tp_cap_pct the harness rows were swept with. <=0 means "
                          "the book had NO take-profit and is not comparable "
                          f"(default {LIVE_TP_CAP_PCT}, the live cap)."))
    ap.add_argument("--min-final-n", type=int, default=DEFAULT_MIN_FINAL_N,
                    help=f"final-row floor for Check B (default {DEFAULT_MIN_FINAL_N}; CHOSEN, not measured)")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true", help="emit the full report as JSON")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.live_json:
        ap.error("--live-json is required (or --self-test)")
    if args.harness_dist and args.harness_emit:
        ap.error("--harness-dist and --harness-emit are mutually exclusive; "
                 "pass exactly one harness source so the report's provenance "
                 "is unambiguous")

    live = _rows(load_json(args.live_json))
    trades = _rows(load_json(args.trades_json)) if args.trades_json else []
    harness = load_jsonl(args.harness_emit) if args.harness_emit else []
    hdist = None
    if args.harness_dist:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from m31_harness_mfe_dist import load_dist  # noqa: PLC0415

        hdist = load_dist(Path(args.harness_dist))
        if not hdist:
            print(f"ERROR: --harness-dist {args.harness_dist} held no legs. "
                  "That is 'we read an empty artifact', not 'the harness has "
                  "no MFE' -- generate it with m31_harness_mfe_dist.py.",
                  file=sys.stderr)
            return 2

    rep = run(live, harness, trades, args.harness_tp_cap_pct, args.min_final_n,
              harness_dist=hdist)

    if args.json:
        print(json.dumps(rep, indent=2, sort_keys=True))
        return 0

    s = rep["summary"]
    print("M31 P4 — backtest<->live MFE parity")
    print(f"  live rows {rep['live_rows_total']} · harness rows "
          f"{rep['harness_rows_total']} · trades rows {rep['trades_rows_total']}")
    print(f"  lifecycle_known={rep['lifecycle_known']} "
          f"closed_ids={rep['closed_trade_ids_seen']}")
    print()
    print("CHECK A — ceiling (peak_r vs its own cap_r; per row, gradeable at n=1)")
    for leg in sorted(rep["ceiling"]):
        b = rep["ceiling"][leg]
        pk = b["max_peak_over_cap_pct"]
        print(f"  {leg:26s} {b['ceiling_state']:11s} rows={b['rows']:<3d} "
              f"graded={b['graded']:<3d} max_peak/cap="
              f"{(f'{pk:.1f}%' if pk is not None else '—')}")
        for br in b["breaches"]:
            print(f"      BREACH pkg={br['order_package_id']} "
                  f"peak_r={br['peak_r']} > cap_r={br['cap_r']} "
                  f"({br['peak_over_cap_pct']}%)")
    print(f"  -> ceiling breaches: {s['ceiling_breaches']}")
    print()
    print("CHECK B — distribution (harness mfe_r vs live peak_r; needs soak depth)")
    if not rep["distribution"]:
        print("  (no legs on either side)")
    for leg in sorted(rep["distribution"]):
        d = rep["distribution"][leg]
        line = (f"  {leg:26s} {d['parity_state']:23s} "
                f"live_rows={d['live_rows']:<3d} harness_n={d['harness_n']}")
        if d.get("parity_state") == "compared":
            line += (f" | live p50={d['live_p50']} max={d['live_max']}"
                     f" vs harness p50={d['harness_p50']} max={d['harness_max']}"
                     f" -> {d['parity']}")
        print(line)
    print(f"  -> compared: {s['legs_compared']} · divergent: {s['legs_divergent']}")
    print()
    print("  NOTE live peak_r is a LOWER BOUND on true MFE — the last telemetry")
    print("  write precedes the close by up to one exit-loop pass, and both")
    print("  sides read bar extremes, so neither sees an intrabar excursion.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
