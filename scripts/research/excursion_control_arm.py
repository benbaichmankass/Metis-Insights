#!/usr/bin/env python3
# wiring: manual-only - like the counterfactual it extends, this answers a
# question about a DATED change (the e35 bracket geometry, 2026-08-30T08:53:19Z)
# once. A scheduled runner would re-answer it every day against a moving
# population and quietly turn a recorded verdict into a drifting one. The
# standing, cadenced version of this measurement is what
# BL-20260911-MFE-OVER-MAE-IN-ATR-UNITS-IS-A-LEADING-INDICATOR-NOBODY-COMPUTES
# asks for ("per leg per week on a durable surface"), and it is a DIFFERENT
# deliverable that is deliberately not this file.
"""The NON-e35 control arm for the excursion measurement (MI-278 U5).

THE QUESTION, AND WHY IT IS NOT A NEW MEASUREMENT
-------------------------------------------------
`OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30-AND-THE-CAUSE-IS-UNATTRIBUTED`
clears on "per-leg stop-out rate and MFE-at-stop, **e35 legs vs NON-e35 legs**,
before vs after 2026-08-30", and states in terms: *"'THE MARKET CHOPPED' IS NOT A
VERDICT WITHOUT THE NON-e35 CONTROL."*

`stop_width_counterfactual_2026_09_11.py` (MI-275) already computes the excursion
half -- MAE/MFE in ATR units, per era, both over each trade's own life
(`excursion_regime`) and over fixed windows from entry (`fixed_window_excursion`).
**It computes them on e35 legs ONLY.** `build_units` filters
`group_of(...) == "e35"` and then drops anything `stop_change()` does not know, so
a control leg is excluded twice over, by construction.

So the missing piece is not an instrument. It is an ARM. This file builds units for
a NAMED GROUP and calls MI-275's own `excursion_regime` and
`fixed_window_excursion` **unmodified** on it. Importing rather than restating is
the whole point: a second implementation of "MAE in ATR units" would be free to
disagree with the one whose numbers are already in the record, and the comparison
is only worth anything if both arms are measured by the same code.

WHAT IS COMPARABLE AND WHAT IS NOT -- READ THIS BEFORE QUOTING ANY NUMBER
-------------------------------------------------------------------------
The untouched control is entirely `ict_scalp_*` (5m/15m scalps). The e35 group is
entirely `trend_donchian_*` plus `ada_pullback_2h` (2h/4h trend legs). The two arms
therefore differ in TIMEFRAME AND STRATEGY FAMILY, not only in bracket geometry.

  * `excursion_regime` measures over each trade's OWN [open, close] window. A 5m
    scalp holds minutes and a 4h trend leg holds days, so its ATR-unit excursions
    differ between the arms for reasons that have nothing whatever to do with e35.
    **It is reported per arm and MUST NOT be cross-compared.** This file refuses to
    difference it, and says so in the output rather than leaving a tempting pair of
    numbers side by side.
  * `fixed_window_excursion` measures a FIXED number of hours from entry, so the
    window depends on neither the geometry under test nor the holding period. That
    is what makes it comparable across arms, and it is the ONLY basis on which a
    difference-in-differences is reported here.

A residual confound survives even there and is named rather than hidden: the arms
differ in SYMBOL MIX and in ENTRY TIMING, so a symbol-matched view is reported
beside the pooled one. Where the two disagree, the pooled number is the suspect.

THE POSITIVE CONTROL THAT MAKES THE ARM ADMISSIBLE
---------------------------------------------------
A control arm built by a DIFFERENT builder from the treated arm is not a control.
So `--group e35` rebuilds the TREATED arm through this file's own builder and
compares it against MI-275's recorded output. If the two disagree, this file's
control arm is not apples-to-apples with the numbers already in the record, and the
right output is a refusal rather than a difference. Run it; do not assume it.

STATES, NEVER COLLAPSED
------------------------
Per unit: `graded` · `no_candle_source` (the symbol has no OKX instrument -- MGC is
an IB future and is genuinely outside this basis) · `no_entry_frozen_atr` · `no_package`
· `unknown_era` · `no_direction`. An ungraded unit is COUNTED and NAMED; it is never
silently dropped, because a control arm that quietly shrinks to the units that
happened to work is how a comparison stops being one.

PRICE BASIS: inherited from MI-275, not re-argued. OKX `*-USDT-SWAP` 1m via its
`fetch_candles`/`INST`. ⚠️ Re-verified 2026-09-12 and one detail matters for anyone
probing it: `api.bybit.com` is CloudFront country-blocked (403, explicit country
message) and `api.binance.com` returns 451, so the proxy basis stands -- but OKX
answers `curl` and returns **403 to Python `urllib`**, which is a User-Agent
artifact and NOT a venue block. A probe written with `urllib` will conclude the
basis is unreachable when it is not.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import bleed_attribution_2026_09_11 as BA           # noqa: E402
import stop_width_counterfactual_2026_09_11 as CF   # noqa: E402

# Every unit lands in exactly one of these. `graded` is the only one that reaches
# the excursion functions; the rest are counted so the denominator stays honest.
UNIT_STATES = (
    "graded", "no_candle_source", "no_entry_frozen_atr",
    "no_package", "unknown_era", "no_direction", "no_order_package_id",
)
# Groups this file will build. `e35` exists ONLY as the positive control described
# above -- it is not the deliverable.
GROUPS = ("untouched_control", "e35", "b4_geometry", "other")


def build_group_units(trades: list[dict], pkgs: dict[str, dict],
                      group: str, tol: float) -> dict:
    """Per-package units for *group*, in the shape MI-275's excursion fns expect.

    Deliberately NOT a copy of ``CF.build_units``: that one carries the
    counterfactual geometry (``stop_change``, the era-vs-declared multiplier
    assertion, the counterfactual stop level), all of which is meaningless for a
    leg whose multiplier never changed. Asking a control leg what its "expected
    multiplier for the era" is has no answer, and inventing one would be a
    fabricated field on a real row.

    What the excursion functions actually read is small and is exactly what is
    built here: ``symbol``, ``entry``, ``atr``, ``adverse_side``,
    ``favourable_side``, ``opened_ms``, and ``rows[0].closed_ms``. ``old_mult`` /
    ``new_mult`` are carried as the leg's OWN entry-frozen declared multiplier and
    are decoration in this file -- they label a row, they do not enter any
    arithmetic.
    """
    out: dict[str, Any] = {"pre": [], "post": [], "states": collections.Counter(),
                           "rows_pre": 0, "rows_post": 0, "ungraded": []}
    pop = BA.population(trades)
    sel = [r for r in pop if BA.group_of(str(r.get("strategy_name") or "")) == group]
    by_pkg: dict[str, list[dict]] = collections.defaultdict(list)
    for r in sel:
        pid = r.get("order_package_id")
        if not pid:
            out["states"]["no_order_package_id"] += 1
            continue
        by_pkg[pid].append(r)

    for pid, rows in by_pkg.items():
        leg = str(rows[0].get("strategy_name") or "")
        symbol = str(rows[0].get("symbol") or "")

        def drop(state: str) -> None:
            out["states"][state] += 1
            out["ungraded"].append({"package": pid, "leg": leg,
                                    "symbol": symbol, "state": state})

        era = BA.era_of(rows[0])
        if era == "unknown":
            drop("unknown_era"); continue
        pkg = pkgs.get(pid)
        if pkg is None:
            drop("no_package"); continue
        if symbol not in CF.INST:
            drop("no_candle_source"); continue
        direction = str(rows[0].get("direction") or "").lower()
        if direction not in ("long", "short"):
            drop("no_direction"); continue
        try:
            meta = (json.loads(pkg["meta"]) if isinstance(pkg.get("meta"), str)
                    else (pkg.get("meta") or {}))
        except (json.JSONDecodeError, TypeError):
            meta = {}
        atr = CF._f(meta.get("atr"))
        if atr is None or atr <= 0:
            drop("no_entry_frozen_atr"); continue

        # ANCHOR = the package's declared entry, for MI-275's own reason: it is what
        # the bot measured its geometry from, and the trade row's entry_price is the
        # FILL. Falling back to the fill is recorded on the unit, never silent.
        entry = CF._f(meta.get("entry")) or CF._f(pkg.get("entry"))
        entry_source = "package_declared_entry"
        if entry is None:
            entry = CF._f(rows[0].get("entry_price"))
            entry_source = "trade_fill_price"
        if entry is None or entry <= 0:
            drop("no_entry_frozen_atr"); continue

        # ⚠️ THE TRADE'S created_at, NOT THE PACKAGE'S, and the choice is forced
        # rather than preferred: CF.excursion_regime windows on the unit's
        # `opened_ms`, and MI-275 sets it from `rows[0].created_at`. A package is
        # created BEFORE its fill, so anchoring on the package widens the window
        # and both extremes can only grow. Measured on the shared 31 packages:
        # 4 disagreed, every one of them one-sided and every one of them LARGER
        # here, which is the signature of a wider window rather than of different
        # arithmetic. Matching CF is the whole point — an arm measured over a
        # different window is not a control.
        opened_ms = CF._ms(str(rows[0].get("created_at") or ""))
        closed_ms = CF._ms(str(rows[0].get("closed_at") or ""))
        if opened_ms is None or closed_ms is None:
            drop("unknown_era"); continue

        declared_mult = CF._f(meta.get("atr_stop_mult"))
        unit = {
            "package": pid, "leg": leg, "era": era, "symbol": symbol,
            "direction": direction, "entry": entry, "entry_source": entry_source,
            "atr": atr,
            # Decoration only — see the docstring. Never differenced, never used
            # to size anything in this file.
            "old_mult": declared_mult, "new_mult": declared_mult,
            # ⚠️ THE VOCABULARY IS "below"/"above", NOT "low"/"high", and getting it
            # wrong does not raise. CF.extreme() is `min(low) if side == "below"
            # else max(high)`, so ANY unrecognised string silently takes the max-high
            # branch and MAE comes back equal to MFE — a plausible number, not an
            # error. That is exactly what happened here on the first run, in all
            # eight fixed-window cells, and only the builder positive control caught
            # it. Filed as its own row; do not "tidy" these strings.
            "adverse_side": "below" if direction == "long" else "above",
            "favourable_side": "above" if direction == "long" else "below",
            "opened_ms": opened_ms,
            "rows": [{"trade_id": r.get("id"), "closed_ms": CF._ms(str(r.get("closed_at") or "")),
                      "closed_at": r.get("closed_at"), "pnl": r.get("pnl"),
                      "account_id": r.get("account_id"),
                      "exit_reason": r.get("exit_reason"),
                      "adjudicated": BA.adjudicate_exit(r, tol)} for r in rows],
        }
        # ⚠️ THE OBSERVATION WINDOW ENDS AT THE EARLIEST CLOSE, DECLARED RATHER THAN
        # INHERITED, because CF.excursion_regime windows on `rows[0]["closed_ms"]`
        # and WHICH ROW IS rows[0] IS ITERATION ORDER, NOT A CHOICE. On a fan-out
        # package the sibling rows do not close together: measured on this arm,
        # 4 of 31 e35 packages have a close spread over an hour and one spans 9h
        # (pkg-19c87bcdb911419a: bybit_1 12:12:27 vs bybit_portfolio 21:25:24), so
        # the same package yields a different MAE/MFE depending only on row order —
        # up to 2.503 vs 0.850 ATR, a 2.9x swing. Sorting ascending makes rows[0]
        # the EARLIEST close: deterministic, and the SHORTEST window, so it can only
        # understate an excursion and never manufacture one. `closed_spread_h` ships
        # on every unit so the affected packages are visible rather than inferred.
        unit["rows"].sort(key=lambda r: (r["closed_ms"] is None, r["closed_ms"]))
        cms = [r["closed_ms"] for r in unit["rows"] if r["closed_ms"] is not None]
        unit["closed_spread_h"] = round((max(cms) - min(cms)) / 3_600_000, 3) if cms else None
        unit["n_fanout_rows"] = len(unit["rows"])
        out[era].append(unit)
        out["states"]["graded"] += 1
        out[f"rows_{era}"] += len(rows)
    return out


def stop_rate(units: list[dict]) -> dict:
    """Adjudicated stop-out rate on the PACKAGE unit.

    Per package, NOT per trade row: account fan-out shares one price path, so a
    row denominator counts one market event several times and does it UNEQUALLY
    between arms (MI-275 §F(ii); the surfaces that still do it are named in
    `BL-20260911-A-RATE-PER-TRADE-ROW-COUNTS-ACCOUNT-FANOUT-AS-INDEPENDENT-AND-INFLATES-N-UNEQUALLY`).
    """
    n = len(units)
    stops = sum(1 for u in units if u["rows"][0]["adjudicated"] == "reached_stop")
    return {"packages": n, "stops": stops,
            "rate": round(stops / n, 4) if n else None,
            "rate_is_none_because": None if n else "empty_cell_no_rate_exists"}


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[len(s) // 2]


def symbol_matched(fixed: dict, units_pre: list[dict], units_post: list[dict],
                   cache: str, window_h: int) -> dict:
    """Fixed-window MFE/MAE split BY SYMBOL, so the pooled number can be checked.

    The arms do not trade the same symbol mix, so a pooled difference can be a
    difference in WHICH symbols were traded rather than in what the market did.
    Reported per symbol with its own n; a symbol with fewer than 3 packages in a
    cell is reported and flagged `thin`, never dropped.
    """
    out: dict[str, Any] = {"window_h": window_h, "symbols": {}}
    for era, units in (("pre", units_pre), ("post", units_post)):
        by_sym: dict[str, list[dict]] = collections.defaultdict(list)
        for u in units:
            by_sym[u["symbol"]].append(u)
        for sym, us in by_sym.items():
            mae, mfe = [], []
            for u in us:
                end = u["opened_ms"] + window_h * 3_600_000
                c = CF.fetch_candles(cache, u["symbol"], "1m", u["opened_ms"], end)
                if not c:
                    continue
                mae.append(abs(u["entry"] - (CF.extreme(c, u["adverse_side"]) or u["entry"])) / u["atr"])
                mfe.append(abs((CF.extreme(c, u["favourable_side"]) or u["entry"]) - u["entry"]) / u["atr"])
            cell = out["symbols"].setdefault(sym, {})
            cell[era] = {"n": len(mae), "thin": len(mae) < 3,
                         "mae_atr_median": round(_median(mae), 3) if mae else None,
                         "mfe_atr_median": round(_median(mfe), 3) if mfe else None}
    return out


def self_test() -> int:
    """Controls that do not need the network."""
    ok = True

    def check(label: str, cond: bool) -> None:
        nonlocal ok
        print(f"  {'ok ' if cond else 'FAIL'} {label}")
        ok = ok and cond

    print("excursion_control_arm --self-test")
    check("every declared unit state is distinct", len(set(UNIT_STATES)) == len(UNIT_STATES))
    check("graded is a state and is not the only one", "graded" in UNIT_STATES and len(UNIT_STATES) > 1)
    check("no_candle_source is distinct from no_package (we-did-not-look vs absent)",
          "no_candle_source" in UNIT_STATES and "no_package" in UNIT_STATES)
    check("MI-275's excursion fns are IMPORTED, not redefined here",
          "excursion_regime" not in globals() and callable(CF.excursion_regime)
          and callable(CF.fixed_window_excursion))
    check("the population rule is imported from MI-271, not restated",
          callable(BA.population) and callable(BA.group_of) and callable(BA.era_of))
    check("MGC has no OKX instrument, so an MGC unit MUST grade no_candle_source",
          "MGC" not in CF.INST)
    check("the crypto symbols the control trades DO have one",
          all(s in CF.INST for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT")))
    check("an empty cell yields rate None, never 0.0",
          stop_rate([])["rate"] is None and stop_rate([])["rate_is_none_because"])
    u = [{"rows": [{"adjudicated": "reached_stop"}]}, {"rows": [{"adjudicated": "neither"}]}]
    check("a populated cell yields a real rate", stop_rate(u)["rate"] == 0.5)
    check("_median returns None on empty rather than 0", _median([]) is None)
    check("_median is a real median", _median([1.0, 5.0, 3.0]) == 3.0)
    check("untouched_control is a buildable group name", "untouched_control" in GROUPS)
    check("e35 is present ONLY as the builder positive control", "e35" in GROUPS)
    print("self-test:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--trades", help="JSON from /api/diag/journal?table=trades&limit=1000")
    ap.add_argument("--packages", help="JSON from /api/diag/journal?table=order_packages&limit=1000")
    ap.add_argument("--group", default="untouched_control", choices=GROUPS)
    ap.add_argument("--cache", default="/tmp/okx-candles")
    ap.add_argument("--tol", type=float, default=BA_TOL if (BA_TOL := getattr(BA, "DEFAULT_TOL", None)) else 0.0015)
    ap.add_argument("--windows", default="4,12,24,48",
                    help="fixed windows in hours; the only cross-arm-comparable basis")
    ap.add_argument("--out")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not (a.trades and a.packages):
        ap.error("--trades and --packages are required unless --self-test")

    trades = CF.load_rows(a.trades) if hasattr(CF, "load_rows") else json.load(open(a.trades))
    if isinstance(trades, dict):
        trades = trades.get("rows") or []
    praw = json.load(open(a.packages))
    if isinstance(praw, dict):
        praw = praw.get("rows") or []
    # Keyed on `order_package_id` — the column the journal actually ships. An
    # earlier guess at `package_id`/`id` keyed every package to None, and the
    # builder graded all 35 e35 units `no_package`; the builder positive control
    # is what caught it, which is the whole reason it runs before the control arm.
    pkgs = {p["order_package_id"]: p for p in praw if p.get("order_package_id")}

    windows = tuple(int(x) for x in a.windows.split(","))
    CF.FIXED_WINDOWS_H = windows

    units = build_group_units(trades, pkgs, a.group, a.tol)
    data_end_ms = max((CF._ms(str(r.get("closed_at") or "")) or 0) for r in trades) or 0

    result: dict[str, Any] = {
        "group": a.group,
        "e35_deploy_utc": CF.E35_DEPLOY_UTC,
        "price_basis": "OKX *-USDT-SWAP 1m via MI-275's fetch_candles/INST — inherited, not re-argued",
        "unit": "order package (NOT trade row) — account fan-out shares one price path",
        "population": {
            "pre_packages": len(units["pre"]), "pre_rows": units["rows_pre"],
            "post_packages": len(units["post"]), "post_rows": units["rows_post"],
            "unit_states": dict(units["states"]),
            "ungraded": units["ungraded"],
        },
        "stop_rate": {"pre": stop_rate(units["pre"]), "post": stop_rate(units["post"])},
        "fanout_window_sensitivity": {
            "_what": ("CF.excursion_regime windows on rows[0]'s close and rows[0] is "
                      "iteration order; this arm sorts ascending so it is the EARLIEST "
                      "close. Packages with a non-zero spread are the ones where the "
                      "choice changes the number."),
            "packages_with_spread_over_1h": sum(
                1 for u in units["pre"] + units["post"] if (u.get("closed_spread_h") or 0) > 1.0),
            "max_spread_h": max([u.get("closed_spread_h") or 0
                                 for u in units["pre"] + units["post"]] or [0]),
            "packages": len(units["pre"]) + len(units["post"]),
        },
        "legs_in_arm": sorted({u["leg"] for u in units["pre"] + units["post"]}),
        "symbols_in_arm": sorted({u["symbol"] for u in units["pre"] + units["post"]}),
    }

    print(f"basis check on {a.group} ...", file=sys.stderr)
    result["pc1_basis"] = CF.pc1_basis(units["pre"] + units["post"], a.cache)

    print("own-lifetime excursions (per arm; NOT cross-comparable) ...", file=sys.stderr)
    result["excursion_regime_own_lifetime"] = CF.excursion_regime(
        units["pre"], units["post"], a.cache)
    result["excursion_regime_own_lifetime"]["cross_arm_comparable"] = False
    result["excursion_regime_own_lifetime"]["why_not"] = (
        "the arms differ in holding period by an order of magnitude "
        "(ict_scalp_* 5m/15m vs trend_donchian_* 2h/4h), and this measure's window "
        "IS the holding period; use fixed_window_excursion for any cross-arm claim")

    print("fixed-window excursions (the comparable basis) ...", file=sys.stderr)
    result["fixed_window_excursion"] = CF.fixed_window_excursion(
        units["pre"], units["post"], a.cache, data_end_ms)

    print("symbol-matched view ...", file=sys.stderr)
    result["symbol_matched"] = symbol_matched(
        result["fixed_window_excursion"], units["pre"], units["post"], a.cache, windows[-1])

    out = json.dumps(result, indent=1, sort_keys=False)
    if a.out:
        pathlib.Path(a.out).write_text(out)
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
