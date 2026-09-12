#!/usr/bin/env python3
# wiring: manual-only - a one-off counterfactual for a DATED change (the e35
# bracket geometry shipped 2026-08-30T08:53:19Z). It answers a question asked
# once, about a decision the operator must take once; a scheduled runner would
# re-answer it every day against a moving population and quietly turn a recorded
# verdict into a drifting one. The standing, cadenced thing this window wants is
# the sustained-losing-streak detector (MI-271 memo section 6), which is a
# DIFFERENT deliverable and is deliberately not this file.
"""The e35 stop-width counterfactual (MI-275 / WO-20260911-THE-STOP-WIDTH-COUNTERFACTUAL).

THE QUESTION
------------
`docs/research/bleed-attribution-2026-09-11.md` (MI-271) established that e35
raised its own legs' stop-out rate from ~0 to ~57% (p=0.015 on broker fills) and
explicitly recommended NOT reverting until one thing was measured: **for each
post-2026-08-30 stop-out on an e35 leg, would the pre-e35 2.5-ATR stop have been
hit?** If most would have stopped anyway, e35 is exonerated on the losses and
reverting buys nothing. If many would have survived, the tightening is doing real
damage and the revert has its evidence.

WHY THE RECORDED TELEMETRY CANNOT ANSWER IT (established before building this)
------------------------------------------------------------------------------
The dispatch for this unit assumed `position_telemetry` could answer the question
"directly from recorded telemetry rather than from a re-simulation". It cannot,
and the reason is structural rather than a coverage problem:

  * `position_telemetry` records `peak_r` -- maximum **FAVOURABLE** excursion
    (MFE), from bar extremes, stamped ESTIMATED. It records **no maximum adverse
    excursion**. `src/runtime/position_telemetry.py` ships `peak_r`, `open_r`,
    `giveback_r`, `r_to_stop`, `cap_r` and nothing that retains how far adverse
    price travelled.
  * `record_position_telemetry` is an **UPSERT** -- one row per open trade,
    overwritten on every exit-loop pass -- so only the LAST state survives. There
    is no path.
  * The counterfactual needs the adverse path **beyond** the tightened stop, i.e.
    price action AFTER the position was already closed. Observation stops at the
    close, so telemetry could not hold it at 100% coverage either.

MI-271 refused the MFE-at-stop join on an n argument (36% join, cells of 2-15,
one empty). That refusal was right, and the deeper reason is that the quantity
needed was never the one recorded.

SO IT IS ANSWERED BY RE-SIMULATION, AND THE PRICE BASIS IS THE RISK
-------------------------------------------------------------------
`api.bybit.com` is geo-blocked from the research container (HTTP 403, CloudFront
country block) and `api.binance.com` likewise (451). OKX answers, and
`*-USDT-SWAP` is the same instrument class as Bybit linear perps -- a perpetual,
not a spot feed -- which is why it is the basis here. 1m history resolves back
past 2026-08-19, covering the whole window.

OKX is not Bybit, so THREE POSITIVE CONTROLS RUN BEFORE ANY VERDICT and are
reported whether they pass or fail. If they fail, the right output of this script
is a refusal, not a counterfactual built on an unfit basis:

  PC1 BASIS. The journal's `entry_price` must fall inside the OKX 1m bar covering
      the fill minute. Reported as a bp deviation per package.
  PC2 REPRODUCTION. For every ARM A package, the ACTUAL (tight, e35) stop must be
      touched in the OKX candles, at a time near the recorded `closed_at`. A
      simulator that cannot reproduce the stop-out that DID happen cannot be
      trusted about one that did not.
  PC3 FALSE-POSITIVE BOUND. For pre-era trades that demonstrably did NOT stop out,
      the trade's own declared 2.5-ATR stop must read UNTOUCHED. If it reads
      touched, the touch detector over-fires and ARM B's "would have been hit" is
      inflated. This is the control that bounds the headline.

TWO ARMS, AND ARM B IS THE PRIMARY ONE
---------------------------------------
ARM A (forward, what the row literally asks). Post-deploy stop-outs: would the
  WIDER 2.5-ATR stop have been hit? This needs price action after the real exit,
  so it needs a HORIZON -- an assumption. Reported at 24h / 72h / 168h and to the
  end of available data, never at a single invented cap.

ARM B (reverse, and strictly stronger). Pre-deploy trades carried the 2.5-ATR
  stop and ran to a KNOWN outcome. Ask of each: would the TIGHTER e35 stop have
  been touched inside the trade's OWN observed lifetime? That window is bounded by
  the trade itself, so **ARM B needs no horizon assumption at all**, and its
  damage cell is directly decision-relevant: a pre-era WINNER that the e35 stop
  would have killed is e35 converting a winner into a loser, measured on a trade
  whose real outcome is not in doubt.

THE UNIT OF OBSERVATION IS THE ORDER PACKAGE, NOT THE TRADE ROW
---------------------------------------------------------------
One signal fans out to several accounts, each writing its own `trades` row with
the same symbol, direction, entry and stop. Those rows share ONE price path, so
they are not independent observations of this question and counting them as such
inflates n. Measured here: 15 post-deploy e35 stop-out ROWS collapse to 8 distinct
PACKAGES. Every rate in this script is therefore reported per package, with the
row count stated beside it.

THE E35 LADDER IS NOT "2.5 -> 2.0 ON 9 LEGS"
---------------------------------------------
It is a five-level ladder and this script uses each leg's own before/after pair,
imported from MI-271's module rather than restated. Two legs went to 1.5, one was
WIDENED to 3.0, and `ada_pullback_2h` had NO stop change (tp_r only) -- measured
here, its packages carry `atr_stop_mult: 1.5` from its own config, which is not an
e35 value. A leg with no stop change has no stop-width counterfactual and is
EXCLUDED from both arms rather than silently graded at a ratio of 1.0.

POPULATION (stated, per the top-level binding rule)
----------------------------------------------------
closed, non-backtest, `pnl NOT NULL`, pairs sleeve excluded -- MI-271's decision
population, imported via `population()` so the two analyses cannot drift -- then
restricted to e35 legs carrying an `atr_stop_mult` change, split on `created_at`
(OPEN time: a leg carries the geometry it was opened under).

WHAT THIS SCRIPT DOES NOT CLAIM
--------------------------------
It answers "would the other stop have been TOUCHED", and nothing more. It does
NOT reconstruct what the trade's PnL would have been under the other geometry,
because:
  * the strategy's own monitor() exits (donchian channel, trail, trail_decay,
    stale_stop, giveback_stop) cannot be re-simulated here, and at least one of
    them demonstrably rewrote a live stop in this very population;
  * `risk_per_unit` scales with the stop, so position SIZE and the absolute
    take-profit price both move with it -- the counterfactual trade is not the
    same trade with one level shifted.
Ignoring the trailing levers biases ARM A TOWARD "survived to target" (a trailed
stop is hit sooner than a fixed one), i.e. toward finding e35 harmful. That
direction is stated rather than corrected, because correcting it would require
simulating the levers.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import importlib.util
import json
import os
import pathlib
import statistics
import subprocess
import sys
import time
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

# ---------------------------------------------------------------------------
# Reuse MI-271's instrument rather than rebuilding it. Its adjudicator carries a
# measured positive control (88.4% recall on declared stops, 100% on declared
# targets) and its E35 ladder was read off the commit. A second copy of either
# is how two analyses of the same event come to disagree about a row.
# ---------------------------------------------------------------------------
_BA_PATH = os.path.join(HERE, "bleed_attribution_2026_09_11.py")
_spec = importlib.util.spec_from_file_location("_bleed_attribution", _BA_PATH)
BA = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BA)

E35_LEGS = BA.E35_LEGS
E35_DEPLOY_UTC = BA.E35_DEPLOY_UTC
adjudicate_exit = BA.adjudicate_exit
population = BA.population
group_of = BA.group_of
era_of = BA.era_of
wilson = BA.wilson
fisher_2x2 = BA.fisher_2x2

# OKX perpetual-swap instrument ids. USDT-margined swaps, matching Bybit linear.
INST = {
    "BTCUSDT": "BTC-USDT-SWAP", "ETHUSDT": "ETH-USDT-SWAP", "SOLUSDT": "SOL-USDT-SWAP",
    "XRPUSDT": "XRP-USDT-SWAP", "ADAUSDT": "ADA-USDT-SWAP", "AVAXUSDT": "AVAX-USDT-SWAP",
    "BNBUSDT": "BNB-USDT-SWAP",
}
BAR_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000}

# How close the journal's entry must sit to the OKX bar at the fill minute for the
# basis to count as fit. 25bp is ~5x the widest deviation measured here and still
# an order of magnitude below the smallest stop distance in the population.
BASIS_TOL_BP = 25.0
# How close the simulated ACTUAL-stop touch must land to the recorded closed_at
# for PC2 to count as a reproduction. Generous on purpose: the exit loop runs on a
# ~30s cadence and a venue fill, its journal write and a cross-venue bar all
# differ; PC2 is asking "the same event", not "the same second".
PC2_TOL_MIN = 180.0


# ===========================================================================
# Candles
# ===========================================================================
def _curl_json(url: str, tries: int = 4) -> dict:
    last = ""
    for i in range(tries):
        p = subprocess.run(["curl", "-sS", "--max-time", "30", url],
                           capture_output=True, text=True)
        if p.returncode == 0 and p.stdout.strip().startswith("{"):
            try:
                d = json.loads(p.stdout)
            except json.JSONDecodeError as exc:
                last = str(exc)
            else:
                if d.get("code") == "0":
                    return d
                last = f"code={d.get('code')} msg={d.get('msg')}"
        else:
            last = (p.stderr or p.stdout or "")[:160]
        time.sleep(0.6 * (i + 1))
    raise RuntimeError(f"okx fetch failed after {tries} tries ({last}): {url[:140]}")


def fetch_candles(cache_dir: str, symbol: str, bar: str,
                  start_ms: int, end_ms: int) -> list[list[float]]:
    """Ascending [[ts_ms, open, high, low, close], ...] covering the range.

    Cached per (instrument, bar, UTC day) so overlapping trade windows -- which
    are the norm, since one signal fans out and legs re-enter the same symbol --
    cost one fetch rather than one per window.
    """
    if symbol not in INST:
        raise KeyError(f"no OKX instrument mapped for {symbol}")
    inst = INST[symbol]
    step = BAR_MS[bar]
    start_ms = (start_ms // step) * step
    end_ms = ((end_ms // step) + 1) * step
    os.makedirs(cache_dir, exist_ok=True)
    merged: dict[int, list[float]] = {}
    day = dt.datetime.fromtimestamp(start_ms / 1000, dt.timezone.utc).date()
    last_day = dt.datetime.fromtimestamp(end_ms / 1000, dt.timezone.utc).date()
    while day <= last_day:
        path = os.path.join(cache_dir, f"{inst}_{bar}_{day}.json")
        if os.path.exists(path):
            merged.update({int(k): v for k, v in json.load(open(path)).items()})
            day += dt.timedelta(days=1)
            continue
        ds = int(dt.datetime.combine(day, dt.time(0, 0), dt.timezone.utc).timestamp() * 1000)
        de = ds + 86_400_000
        got: dict[int, list[float]] = {}
        after = de  # OKX `after` returns rows strictly OLDER than this ts
        while after > ds:
            d_ = _curl_json(
                "https://www.okx.com/api/v5/market/history-candles"
                f"?instId={inst}&bar={bar}&after={after}&limit=100")
            rows = d_.get("data") or []
            if not rows:
                break
            for r in rows:
                ts = int(r[0])
                if ds <= ts < de:
                    got[ts] = [float(r[1]), float(r[2]), float(r[3]), float(r[4])]
            oldest = min(int(r[0]) for r in rows)
            if oldest >= after:
                break
            after = oldest
            time.sleep(0.06)
        json.dump({str(k): v for k, v in got.items()}, open(path, "w"))
        merged.update(got)
        day += dt.timedelta(days=1)
    return [[ts] + merged[ts] for ts in sorted(merged) if start_ms <= ts <= end_ms]


# ===========================================================================
# Small helpers
# ===========================================================================
# --- MI-278 U8: one owner for "declared stop or an amended one?" ----------
# Imported BY NAME so this script and the owner can never drift apart.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from src.research.stop_attribution import classify as classify_stop_basis  # noqa: E402

#: This memo's published vocabulary, preserved so its committed output and every
#: sentence written about it stay valid. The owner's names are the general ones.
_MI275_STATE = {
    "entry_declared": "stop_is_entry_declared",
    "amended_tighter": "stop_amended_tighter",
    "amended_wider": "stop_amended_wider",
    "ungradeable_no_final_stop": "ungradeable_no_final_stop",
    "ungradeable_no_declared_stop": "ungradeable_no_final_stop",
    "ungradeable_declared_stop_not_entry_frozen": "ungradeable_no_final_stop",
    "ungradeable_no_anchor": "ungradeable_no_final_stop",
    "ungradeable_no_direction": "ungradeable_no_final_stop",
    "ungradeable_zero_declared_distance": "ungradeable_no_final_stop",
}


def _f(v: Any) -> float | None:
    try:
        if v is None:
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _ms(iso: str | None) -> int | None:
    if not iso:
        return None
    s = str(iso).replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)


def _iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat()


def first_touch(candles: list[list[float]], level: float, side: str) -> int | None:
    """First bar ts at which `level` is touched. `side` is which way price must go.

    A stop or target triggers on a TOUCH, so the bar extreme is the right test --
    using closes would miss a level that was traded through inside the bar.
    """
    for ts, _o, hi, lo, _c in candles:
        if side == "below" and lo <= level:
            return int(ts)
        if side == "above" and hi >= level:
            return int(ts)
    return None


def extreme(candles: list[list[float]], side: str) -> float | None:
    if not candles:
        return None
    return min(c[3] for c in candles) if side == "below" else max(c[2] for c in candles)


# ===========================================================================
# Population
# ===========================================================================
def stop_change(leg: str) -> tuple[float, float] | None:
    """(old_mult, new_mult) for a leg whose e35 change touched `atr_stop_mult`.

    `None` for a leg e35 changed only via `tp_r` -- it has no stop-width
    counterfactual, and grading it at a ratio of 1.0 would silently add a row
    that can never show an effect.
    """
    spec = E35_LEGS.get(leg) or {}
    pair = spec.get("atr_stop_mult")
    if not pair:
        return None
    return float(pair[0]), float(pair[1])


def build_units(trades: list[dict], pkgs: dict[str, dict], tol: float) -> dict:
    """Group the e35 decision population into per-package units, with geometry.

    Returns {'pre': [...], 'post': [...], 'excluded': Counter, 'mult_mismatch': [...]}
    """
    out: dict[str, Any] = {"pre": [], "post": [], "excluded": collections.Counter(),
                           "mult_mismatch": [], "rows_pre": 0, "rows_post": 0}
    pop = population(trades)
    e35 = [r for r in pop if group_of(str(r.get("strategy_name") or "")) == "e35"]
    by_pkg: dict[str, list[dict]] = collections.defaultdict(list)
    for r in e35:
        pid = r.get("order_package_id")
        if not pid:
            out["excluded"]["no_order_package_id"] += 1
            continue
        by_pkg[pid].append(r)

    for pid, rows in by_pkg.items():
        leg = str(rows[0].get("strategy_name") or "")
        era = era_of(rows[0])
        if era == "unknown":
            out["excluded"]["unknown_era"] += 1
            continue
        chg = stop_change(leg)
        if chg is None:
            out["excluded"][f"no_stop_change:{leg}"] += 1
            continue
        old_mult, new_mult = chg
        pkg = pkgs.get(pid)
        if pkg is None:
            out["excluded"]["package_absent_from_window"] += 1
            continue
        try:
            meta = json.loads(pkg["meta"]) if isinstance(pkg.get("meta"), str) else (pkg.get("meta") or {})
        except (json.JSONDecodeError, TypeError):
            out["excluded"]["package_meta_unparseable"] += 1
            continue
        atr = _f(meta.get("atr"))
        declared_mult = _f(meta.get("atr_stop_mult"))
        if atr is None or atr <= 0 or declared_mult is None:
            out["excluded"]["no_entry_frozen_atr_or_mult"] += 1
            continue

        # The era's expected multiplier, from the commit. A mismatch means the
        # deploy had not reached the trader for this trade (or the split is wrong)
        # -- recorded, never silently re-based onto whatever the row happens to say.
        expected = old_mult if era == "pre" else new_mult
        if abs(declared_mult - expected) > 1e-9:
            out["mult_mismatch"].append(
                {"package": pid, "leg": leg, "era": era,
                 "declared_mult": declared_mult, "expected_mult": expected})
            out["excluded"]["entry_frozen_mult_disagrees_with_era"] += 1
            continue

        # The entry-frozen declared stop -- the level the bot actually placed.
        try:
            ep = json.loads(pkg["exit_plan"]) if isinstance(pkg.get("exit_plan"), str) else (pkg.get("exit_plan") or {})
        except (json.JSONDecodeError, TypeError):
            ep = {}
        declared_stop = _f((ep.get("stop") or {}).get("price"))
        if declared_stop is None:
            declared_stop = _f(pkg.get("sl"))   # pkg.sl may already be trailed
            declared_stop_source = "package_sl_MAY_BE_TRAILED"
        else:
            declared_stop_source = "exit_plan_stop_entry_frozen"
        if declared_stop is None or declared_stop <= 0:
            out["excluded"]["no_entry_frozen_declared_stop"] += 1
            continue

        symbol = str(rows[0].get("symbol") or "")
        if symbol not in INST:
            out["excluded"][f"no_candle_source:{symbol}"] += 1
            continue
        direction = str(rows[0].get("direction") or "").lower()
        if direction not in ("long", "short"):
            out["excluded"]["no_direction"] += 1
            continue
        # ANCHOR = the package's declared entry, because that is what the bot
        # measured the stop from. The trade row's `entry_price` is the FILL and the
        # two differ; using the fill silently re-bases the whole counterfactual.
        anchor = _f(pkg.get("entry"))
        fill = _f(rows[0].get("entry_price"))
        if anchor is None or anchor <= 0:
            anchor = fill
        if anchor is None or anchor <= 0:
            out["excluded"]["no_entry_price"] += 1
            continue

        sgn = -1.0 if direction == "long" else 1.0
        adverse = "below" if direction == "long" else "above"
        favourable = "above" if direction == "long" else "below"
        declared_dist = abs(anchor - declared_stop)
        this_mult = old_mult if era == "pre" else new_mult
        other_mult = new_mult if era == "pre" else old_mult
        cf_dist = declared_dist * (other_mult / this_mult) if this_mult else None
        unit = {
            "package": pid, "leg": leg, "era": era, "symbol": symbol,
            "direction": direction, "entry": anchor, "fill": fill, "atr": atr,
            "old_mult": old_mult, "new_mult": new_mult,
            "declared_stop": declared_stop,
            "declared_stop_source": declared_stop_source,
            "declared_dist": declared_dist,
            # CHECK, not basis: should land on this era's multiplier.
            "declared_dist_in_atr": round(declared_dist / atr, 3),
            "counterfactual_stop": (anchor + sgn * cf_dist) if cf_dist is not None else None,
            "stop_old": declared_stop if era == "pre" else (anchor + sgn * cf_dist),
            "stop_new": declared_stop if era == "post" else (anchor + sgn * cf_dist),
            "take_profit": _f(rows[0].get("take_profit_1")) or _f(pkg.get("tp")),
            "adverse_side": adverse, "favourable_side": favourable,
            "opened_ms": _ms(rows[0].get("created_at")),
            "rows": [],
        }
        for r in sorted(rows, key=lambda x: str(x.get("closed_at") or "")):
            unit["rows"].append({
                "trade_id": r.get("id"), "account_id": r.get("account_id"),
                "account_class": r.get("account_class"),
                "closed_ms": _ms(r.get("closed_at")),
                "closed_at": r.get("closed_at"),
                "pnl": _f(r.get("pnl")), "exit_price": _f(r.get("exit_price")),
                "exit_reason": r.get("exit_reason"),
                "final_stop": _f(r.get("stop_loss")),
                "adjudicated": adjudicate_exit(r, tol),
            })
        # Was the stop amended after entry? If the final stop sits INSIDE the
        # entry-declared e35 stop, something else (a trail, trail_decay,
        # stale_stop) ended the trade and e35's width is not what did it.
        #
        # DELEGATED to src/research/stop_attribution.py (MI-278 U8, 2026-09-12)
        # so this script and every later analysis can never disagree about what
        # counts as an amended stop -- the discipline m20_corpus_union.py uses
        # when it imports `measurement_key` by name rather than re-deriving it.
        # The row that asked for it is
        # BL-20260911-A-TRAILED-STOP-OUT-IS-ATTRIBUTED-TO-THE-DECLARED-GEOMETRY-THAT-DID-NOT-END-IT,
        # filed by THIS memo.
        #
        # ⚠️ THE VERDICTS ARE UNCHANGED, AND THAT IS MEASURED RATHER THAN
        # ASSUMED. The owner grades `|final - declared|` against 1% of the
        # DECLARED WIDTH; this script graded it against 0.02 ATR. The two
        # coincide exactly at a 2.0-ATR declared width and differ slightly
        # elsewhere, so they were run side by side over all 322 closed
        # package-linked trades in the 2026-09-12 journal tail that carry an
        # entry-frozen ATR: **0 disagreements**. Pinned by
        # tests/test_stop_attribution.py::TestAgreesWithMI275sOwnTolerance.
        fs = unit["rows"][0]["final_stop"]
        _att = classify_stop_basis(
            {"entry": anchor, "direction": direction,
             "exit_plan": {"stop": {"price": declared_stop}},
             "meta": {"atr": atr}},
            {"direction": direction, "stop_loss": fs},
        )
        unit["stop_integrity"] = _MI275_STATE[_att["state"]]
        if fs is not None:
            unit["final_stop_atr_from_entry"] = round(_att["final_stop_atr_from_anchor"], 3)
            unit["stop_moved_atr_vs_declared"] = round(_att["moved_atr"], 3)
        out["pre" if era == "pre" else "post"].append(unit)
        out["rows_pre" if era == "pre" else "rows_post"] += len(rows)
    out["pre"].sort(key=lambda u: u["opened_ms"] or 0)
    out["post"].sort(key=lambda u: u["opened_ms"] or 0)
    return out


# ===========================================================================
# Positive controls
# ===========================================================================
def pc1_basis(units: list[dict], cache: str) -> dict:
    """Does the journal's entry price sit inside the OKX bar at the fill minute?"""
    devs, rows, unfit = [], [], 0
    for u in units:
        t = u["opened_ms"]
        if t is None:
            continue
        c = fetch_candles(cache, u["symbol"], "1m", t - 60_000, t + 60_000)
        bar = next((b for b in c if b[0] <= t < b[0] + 60_000), None)
        if bar is None:
            rows.append({"package": u["package"], "state": "no_bar_at_fill_minute"})
            unfit += 1
            continue
        lo, hi, e = bar[3], bar[2], u["entry"]
        inside = lo <= e <= hi
        dev_bp = 0.0 if inside else (min(abs(e - lo), abs(e - hi)) / e) * 10_000.0
        devs.append(dev_bp)
        if dev_bp > BASIS_TOL_BP:
            unfit += 1
        rows.append({"package": u["package"], "symbol": u["symbol"],
                     "entry": e, "bar_low": lo, "bar_high": hi,
                     "inside_bar": inside, "deviation_bp": round(dev_bp, 2),
                     "state": "fit" if dev_bp <= BASIS_TOL_BP else "unfit"})
    return {"n": len(rows), "unfit": unfit,
            "inside_bar": sum(1 for r in rows if r.get("inside_bar")),
            "max_deviation_bp": round(max(devs), 2) if devs else None,
            "median_deviation_bp": round(statistics.median(devs), 2) if devs else None,
            "tolerance_bp": BASIS_TOL_BP,
            "verdict": "fit" if rows and unfit == 0 else ("unfit" if rows else "no_data"),
            "rows": rows}


def pc2_reproduce(units: list[dict], cache: str, pad_min: float) -> dict:
    """Can the simulator reproduce the stop-out that actually happened?"""
    rows, failures = [], 0
    for u in units:
        t0 = u["opened_ms"]
        r0 = u["rows"][0]
        t1 = r0["closed_ms"]
        if t0 is None or t1 is None:
            rows.append({"package": u["package"], "state": "ungradeable_no_window"})
            failures += 1
            continue
        c = fetch_candles(cache, u["symbol"], "1m", t0, t1 + int(pad_min * 60_000))
        hit = first_touch(c, u["stop_new"], u["adverse_side"])
        if hit is None:
            rows.append({"package": u["package"], "leg": u["leg"],
                         "state": "actual_stop_NOT_reproduced",
                         "stop_new": u["stop_new"],
                         "window_extreme": extreme(c, u["adverse_side"]),
                         "closed_at": r0["closed_at"]})
            failures += 1
            continue
        delta_min = (hit - t1) / 60_000.0
        ok = abs(delta_min) <= pad_min
        rows.append({"package": u["package"], "leg": u["leg"],
                     "state": "reproduced" if ok else "reproduced_off_window",
                     "stop_new": u["stop_new"], "touch_at": _iso(hit),
                     "closed_at": r0["closed_at"],
                     "touch_minus_close_min": round(delta_min, 1)})
        if not ok:
            failures += 1
    return {"n": len(rows), "failures": failures, "tolerance_min": pad_min,
            "verdict": "pass" if rows and failures == 0 else ("fail" if rows else "no_data"),
            "rows": rows}


def pc3_false_positive(units: list[dict], cache: str) -> dict:
    """On pre-era trades that did NOT stop out, is their own 2.5 stop untouched?

    This bounds the touch detector's false-positive rate, which is exactly the
    error that would inflate ARM B's headline.
    """
    rows, fp = [], 0
    for u in units:
        r0 = u["rows"][0]
        if r0["adjudicated"] in ("reached_stop", "ungradeable_no_price"):
            continue
        t0, t1 = u["opened_ms"], r0["closed_ms"]
        if t0 is None or t1 is None:
            continue
        c = fetch_candles(cache, u["symbol"], "1m", t0, t1)
        hit = first_touch(c, u["stop_old"], u["adverse_side"])
        bad = hit is not None
        if bad:
            fp += 1
        rows.append({"package": u["package"], "leg": u["leg"],
                     "adjudicated": r0["adjudicated"],
                     "own_stop": u["stop_old"],
                     "state": "FALSE_POSITIVE_own_stop_reads_touched" if bad else "clean",
                     "touch_at": _iso(hit)})
    return {"n": len(rows), "false_positives": fp,
            "rate": (fp / len(rows)) if rows else None,
            "verdict": "pass" if rows and fp == 0 else ("fail" if rows else "no_data"),
            "rows": rows}


# ===========================================================================
# ARM B -- the reverse counterfactual (no horizon assumption)
# ===========================================================================
def arm_b(units: list[dict], cache: str) -> dict:
    rows = []
    for u in units:
        r0 = u["rows"][0]          # earliest close = the most conservative window
        rlast = u["rows"][-1]
        t0 = u["opened_ms"]
        if t0 is None or r0["closed_ms"] is None:
            rows.append({**_unit_head(u), "state": "ungradeable_no_window"})
            continue
        c = fetch_candles(cache, u["symbol"], "1m", t0, r0["closed_ms"])
        if not c:
            rows.append({**_unit_head(u), "state": "ungradeable_no_candles"})
            continue
        hit = first_touch(c, u["stop_new"], u["adverse_side"])
        # Sensitivity: the widest window any fanned row observed.
        hit_wide = hit
        if rlast["closed_ms"] and rlast["closed_ms"] > r0["closed_ms"]:
            cw = fetch_candles(cache, u["symbol"], "1m", t0, rlast["closed_ms"])
            hit_wide = first_touch(cw, u["stop_new"], u["adverse_side"])
        won = [r for r in u["rows"] if (r["pnl"] or 0) > 0]
        actual = "win" if len(won) > len(u["rows"]) / 2 else "loss"
        mae_atr = abs(u["entry"] - (extreme(c, u["adverse_side"]) or u["entry"])) / u["atr"]
        rows.append({
            **_unit_head(u),
            "state": "e35_stop_would_hit" if hit else "e35_stop_untouched",
            "state_widest_window": "e35_stop_would_hit" if hit_wide else "e35_stop_untouched",
            "actual_outcome": actual,
            "actual_adjudicated": r0["adjudicated"],
            "touch_at": _iso(hit),
            "hold_hours": round((r0["closed_ms"] - t0) / 3_600_000.0, 2),
            "max_adverse_excursion_atr": round(mae_atr, 3),
            "new_stop_at_atr": u["new_mult"], "own_stop_at_atr": u["old_mult"],
            "e35_stop_is": "tighter" if u["new_mult"] < u["old_mult"] else "WIDER",
            "pnl_rows": [r["pnl"] for r in u["rows"]],
            "damage": bool(hit) and actual == "win",
        })
    graded = [r for r in rows if r["state"].startswith("e35_stop")]
    winners = [r for r in graded if r["actual_outcome"] == "win"]
    killed = [r for r in winners if r["state"] == "e35_stop_would_hit"]
    return {
        "packages": len(rows), "gradeable": len(graded),
        "would_hit": sum(1 for r in graded if r["state"] == "e35_stop_would_hit"),
        "untouched": sum(1 for r in graded if r["state"] == "e35_stop_untouched"),
        "ungradeable": collections.Counter(
            r["state"] for r in rows if not r["state"].startswith("e35_stop")),
        "winners": len(winners), "winners_killed_by_e35_stop": len(killed),
        "winners_killed_ci": wilson(len(killed), len(winners)) if winners else None,
        "killed_packages": [r["package"] for r in killed],
        "sensitivity_widest_window_would_hit": sum(
            1 for r in graded if r["state_widest_window"] == "e35_stop_would_hit"),
        "rows": rows,
    }


def _unit_head(u: dict) -> dict:
    return {"package": u["package"], "leg": u["leg"], "symbol": u["symbol"],
            "direction": u["direction"], "entry": u["entry"], "atr": u["atr"],
            "stop_old": u["stop_old"], "stop_new": u["stop_new"],
            "stop_integrity": u["stop_integrity"],
            "opened_at": _iso(u["opened_ms"]),
            "trade_ids": [r["trade_id"] for r in u["rows"]],
            "accounts": [r["account_id"] for r in u["rows"]]}


# ===========================================================================
# ARM A -- the forward counterfactual (needs a horizon, so reports several)
# ===========================================================================
HORIZONS_H = (24, 72, 168)


def arm_a(units: list[dict], cache: str, data_end_ms: int) -> dict:
    rows = []
    for u in units:
        r0 = u["rows"][0]
        t0 = u["opened_ms"]
        if t0 is None:
            rows.append({**_unit_head(u), "state": "ungradeable_no_window"})
            continue
        per_h = {}
        for h in HORIZONS_H:
            end = t0 + h * 3_600_000
            c = fetch_candles(cache, u["symbol"], "1m", t0, min(end, data_end_ms))
            per_h[f"{h}h"] = _race(u, c)
        c_all = fetch_candles(cache, u["symbol"], "1m", t0, data_end_ms)
        per_h["to_data_end"] = _race(u, c_all)
        rows.append({**_unit_head(u),
                     "take_profit": u["take_profit"],
                     "actual_exit_at": r0["closed_at"],
                     "actual_exit_price": r0["exit_price"],
                     "pnl_rows": [r["pnl"] for r in u["rows"]],
                     "accounts_real_money": [r["account_id"] for r in u["rows"]
                                             if r["account_class"] == "real_money"],
                     "horizons": per_h,
                     "state": per_h["to_data_end"]["outcome"]})
    by_h = {}
    for key in [f"{h}h" for h in HORIZONS_H] + ["to_data_end"]:
        c = collections.Counter(r["horizons"][key]["outcome"] for r in rows if "horizons" in r)
        by_h[key] = dict(c)
    return {"packages": len(rows), "by_horizon": by_h, "rows": rows}


def _race(u: dict, candles: list[list[float]]) -> dict:
    """Which came first after entry: the WIDE (pre-e35) stop, or the target?"""
    if not candles:
        return {"outcome": "ungradeable_no_candles"}
    ws = first_touch(candles, u["stop_old"], u["adverse_side"])
    tp = u["take_profit"]
    tph = first_touch(candles, tp, u["favourable_side"]) if tp else None
    mfe_atr = abs((extreme(candles, u["favourable_side"]) or u["entry"]) - u["entry"]) / u["atr"]
    # Express MFE in the PRE-e35 risk unit, since that is the geometry under test.
    res = {"wide_stop_touch_at": _iso(ws), "target_touch_at": _iso(tph),
           "max_favourable_excursion_atr": round(mfe_atr, 3),
           "max_favourable_excursion_old_R": round(mfe_atr / u["old_mult"], 3),
           "bars": len(candles)}
    if ws is None and tph is None:
        res["outcome"] = "neither_at_horizon"
    elif ws is not None and tph is None:
        res["outcome"] = "wide_stop_also_hit"
    elif ws is None and tph is not None:
        res["outcome"] = "target_reached_first"
    elif ws == tph:
        # Both inside one 1m bar: the order is not observable at this granularity.
        res["outcome"] = "same_bar_ambiguous"
    else:
        res["outcome"] = "wide_stop_also_hit" if ws < tph else "target_reached_first"
    return res



# ===========================================================================
# Excursion regime -- the discriminator that needs no counterfactual at all
# ===========================================================================
def excursion_regime(units_pre: list[dict], units_post: list[dict], cache: str) -> dict:
    """Max adverse / favourable excursion IN ATR UNITS over each trade's own life.

    This is the measurement that separates the two candidate mechanisms without
    simulating any alternative geometry, and it is why it is reported beside the
    arms rather than inside them:

      * A stop is placed at `k x ATR`. If the market's excursions in ATR units are
        UNCHANGED across the split, then ATR scaling absorbed whatever the market
        did, and a stop-rate rise has to come from `k` -- i.e. from e35.
      * If excursions in ATR units GREW, the market moved further per unit of the
        volatility that sized the stop, which tightens every stop in the book
        including the ones e35 never touched.

    Horizon-free: each trade's own [open, close] window bounds it. Reported per era
    with n, median and the full spread, never as a single ratio.
    """
    out: dict[str, Any] = {}
    for era, units in (("pre", units_pre), ("post", units_post)):
        mae, mfe, rows = [], [], []
        for u in units:
            r0 = u["rows"][0]
            t0, t1 = u["opened_ms"], r0["closed_ms"]
            if t0 is None or t1 is None:
                continue
            c = fetch_candles(cache, u["symbol"], "1m", t0, t1)
            if not c:
                continue
            a = abs(u["entry"] - (extreme(c, u["adverse_side"]) or u["entry"])) / u["atr"]
            f = abs((extreme(c, u["favourable_side"]) or u["entry"]) - u["entry"]) / u["atr"]
            mae.append(a)
            mfe.append(f)
            rows.append({"package": u["package"], "leg": u["leg"],
                         "mae_atr": round(a, 3), "mfe_atr": round(f, 3),
                         "declared_stop_at_atr": u["new_mult"] if era == "post" else u["old_mult"],
                         "hold_hours": round((t1 - t0) / 3_600_000.0, 2)})
        out[era] = {
            "n_packages": len(mae),
            "mae_atr_median": round(statistics.median(mae), 3) if mae else None,
            "mae_atr_mean": round(statistics.fmean(mae), 3) if mae else None,
            "mae_atr_max": round(max(mae), 3) if mae else None,
            "mfe_atr_median": round(statistics.median(mfe), 3) if mfe else None,
            "mfe_atr_max": round(max(mfe), 3) if mfe else None,
            "mfe_over_mae_median": (round(statistics.median(mfe) / statistics.median(mae), 3)
                                    if mae and mfe and statistics.median(mae) else None),
            "rows": rows,
        }
    a, b = out.get("pre", {}), out.get("post", {})
    if a.get("mae_atr_median") and b.get("mae_atr_median"):
        out["mae_atr_median_ratio_post_over_pre"] = round(
            b["mae_atr_median"] / a["mae_atr_median"], 3)
    if a.get("mfe_atr_median") and b.get("mfe_atr_median"):
        out["mfe_atr_median_ratio_post_over_pre"] = round(
            b["mfe_atr_median"] / a["mfe_atr_median"], 3)
    out["how_to_read"] = (
        "mae_atr ~ 1.0 on both sides means ATR scaling absorbed the market and a "
        "stop-rate rise must come from the multiplier. mae_atr RISING means the "
        "market moved further per unit of sizing volatility, which tightens every "
        "stop in the book including legs e35 never touched. Small n on both sides "
        "-- read the n before the ratio."
    )
    return out



# ===========================================================================
# Candle adjudication -- a stricter instrument than comparing the exit PRICE
# ===========================================================================
def candle_adjudication(units_pre: list[dict], units_post: list[dict], cache: str) -> dict:
    """Was the DECLARED stop / target actually TOUCHED inside the trade's own life?

    MI-271 adjudicates exit location by comparing `exit_price` against the declared
    levels inside a 15bp band, and proved that instrument with a positive control
    (88.4% recall on declared stops). This asks the same question of the PRICE PATH
    instead of the fill, which is strictly more information: a stop-out whose fill
    comes back outside the band is invisible to a price comparison and plain here.

    It is reported BESIDE MI-271's adjudication, never instead of it, with the
    disagreements enumerated -- because a second adjudicator that silently replaced
    the first is how two analyses of one event come to disagree about a row.

    Measured motivation: PC3 flagged pkg-5eb2f3a75d394be3 (trade 4916) as a
    "false positive" -- its own 2.5-ATR stop read touched on a trade MI-271 graded
    `neither`. It is not a false positive. That long entered at 8.064 with its stop
    at 7.45989, price touched 7.45989 at 05:10, and the trade closed three minutes
    later at 7.488 by `monitor_reconciler` -- 38bp above the stop, i.e. OUTSIDE a
    15bp band. It is a stop-out counted as `neither`, and it sits in the PRE era,
    where a missed stop-out DEFLATES the pre-period stop rate and so INFLATES the
    pre->post rise that the e35 indictment rests on.
    """
    out: dict[str, Any] = {"disagreements": []}
    for era, units in (("pre", units_pre), ("post", units_post)):
        counts = collections.Counter()
        for u in units:
            r0 = u["rows"][0]
            t0, t1 = u["opened_ms"], r0["closed_ms"]
            if t0 is None or t1 is None:
                counts["ungradeable_no_window"] += 1
                continue
            c = fetch_candles(cache, u["symbol"], "1m", t0, t1)
            if not c:
                counts["ungradeable_no_candles"] += 1
                continue
            # The level the trade actually carried at the end, and the one it was
            # given at entry, are different questions; grade the DECLARED one here
            # and let stop_integrity carry the amendment.
            s_hit = first_touch(c, u["declared_stop"], u["adverse_side"])
            tp = u["take_profit"]
            t_hit = first_touch(c, tp, u["favourable_side"]) if tp else None
            if s_hit is not None and (t_hit is None or s_hit <= t_hit):
                verdict = "stop_touched"
            elif t_hit is not None:
                verdict = "target_touched"
            else:
                verdict = "neither_touched"
            counts[verdict] += 1
            if (verdict == "stop_touched") != (r0["adjudicated"] == "reached_stop"):
                out["disagreements"].append({
                    "package": u["package"], "leg": u["leg"], "era": era,
                    "trade_ids": [r["trade_id"] for r in u["rows"]],
                    "mi271_price_adjudication": r0["adjudicated"],
                    "candle_adjudication": verdict,
                    "declared_stop": u["declared_stop"],
                    "exit_price": r0["exit_price"],
                    "exit_reason": r0["exit_reason"],
                    "exit_vs_stop_bp": (round(abs((r0["exit_price"] - u["declared_stop"])
                                                  / u["declared_stop"]) * 10_000.0, 1)
                                        if r0["exit_price"] else None),
                    "stop_touch_at": _iso(s_hit),
                    "closed_at": r0["closed_at"],
                })
        g = counts["stop_touched"] + counts["target_touched"] + counts["neither_touched"]
        out[era] = {"packages": sum(counts.values()), "gradeable": g,
                    "counts": dict(counts),
                    "stop_rate": round(counts["stop_touched"] / g, 3) if g else None,
                    "stop_rate_ci": wilson(counts["stop_touched"], g) if g else None}
    a, b = out.get("pre", {}), out.get("post", {})
    if a.get("gradeable") and b.get("gradeable"):
        out["stop_rate_fisher_p"] = fisher_2x2(
            a["counts"].get("stop_touched", 0), a["gradeable"] - a["counts"].get("stop_touched", 0),
            b["counts"].get("stop_touched", 0), b["gradeable"] - b["counts"].get("stop_touched", 0))
    return out


# ===========================================================================
# Stop-integrity census -- WHICH stop actually ended each trade
# ===========================================================================
def stop_integrity_census(units_pre: list[dict], units_post: list[dict]) -> dict:
    """Of the stop-outs, how many exited at the ENTRY-declared stop?

    This is the decision-relevant decomposition and it is not optional. Reverting
    `atr_stop_mult` changes the ENTRY stop. A trade whose stop had already been
    moved INSIDE that level by a trailing lever before it was hit would have exited
    at the same trailed level under either geometry, so reverting e35 cannot
    recover it. Counting such trades as evidence for a revert overstates the case
    for one.
    """
    out = {}
    for era, units in (("pre", units_pre), ("post", units_post)):
        c = collections.Counter()
        detail = []
        for u in units:
            r0 = u["rows"][0]
            if r0["adjudicated"] != "reached_stop":
                continue
            c[u["stop_integrity"]] += 1
            detail.append({"package": u["package"], "leg": u["leg"],
                           "stop_integrity": u["stop_integrity"],
                           "declared_stop": u["declared_stop"],
                           "final_stop": r0["final_stop"],
                           "declared_dist_in_atr": u["declared_dist_in_atr"],
                           "final_stop_atr_from_entry": u.get("final_stop_atr_from_entry"),
                           "stop_moved_atr_vs_declared": u.get("stop_moved_atr_vs_declared"),
                           "trade_ids": [r["trade_id"] for r in u["rows"]],
                           "pnl_rows": [r["pnl"] for r in u["rows"]]})
        out[era] = {"stop_out_packages": sum(c.values()), "by_integrity": dict(c),
                    "detail": detail}
    return out



# ===========================================================================
# Fixed-window excursion -- the same regime question with the truncation removed
# ===========================================================================
FIXED_WINDOWS_H = (4, 12, 24, 48)


def fixed_window_excursion(units_pre: list[dict], units_post: list[dict],
                           cache: str, data_end_ms: int) -> dict:
    """MAE / MFE in ATR units over a FIXED window from entry, ignoring the exit.

    WHY THIS EXISTS, and it is not a refinement -- it is the control on
    `excursion_regime`, which cannot be trusted without it. That function measures
    each trade over its OWN lifetime, and post-era trades were stopped out sooner
    precisely because their stops were tighter. A shorter observation window
    mechanically lowers MFE and caps MAE, so part of any measured collapse could be
    the tighter stop truncating the observation rather than the market changing --
    the confound would manufacture exactly the finding being claimed.

    Measuring a FIXED number of hours from entry removes it: the window no longer
    depends on the geometry under test, on when the trade closed, or on which lever
    closed it. A difference that survives here is a statement about the market.
    """
    out: dict[str, Any] = {"windows_h": list(FIXED_WINDOWS_H)}
    for era, units in (("pre", units_pre), ("post", units_post)):
        per_w: dict[str, Any] = {}
        for h in FIXED_WINDOWS_H:
            mae, mfe, truncated = [], [], 0
            for u in units:
                t0 = u["opened_ms"]
                if t0 is None:
                    continue
                end = t0 + h * 3_600_000
                if end > data_end_ms:
                    # The window runs past the data. Counting it anyway would
                    # compare a full window against a clipped one, which is the
                    # same truncation defect one level up.
                    truncated += 1
                    continue
                c = fetch_candles(cache, u["symbol"], "1m", t0, end)
                if not c:
                    continue
                mae.append(abs(u["entry"] - (extreme(c, u["adverse_side"]) or u["entry"])) / u["atr"])
                mfe.append(abs((extreme(c, u["favourable_side"]) or u["entry"]) - u["entry"]) / u["atr"])
            per_w[f"{h}h"] = {
                "n": len(mae),
                "excluded_window_past_data_end": truncated,
                "mae_atr_median": round(statistics.median(mae), 3) if mae else None,
                "mfe_atr_median": round(statistics.median(mfe), 3) if mfe else None,
                "mfe_over_mae_median": (round(statistics.median(mfe) / statistics.median(mae), 3)
                                        if mae and mfe and statistics.median(mae) else None),
            }
        out[era] = per_w
    ratios = {}
    for h in FIXED_WINDOWS_H:
        k = f"{h}h"
        a, b = out["pre"].get(k, {}), out["post"].get(k, {})
        if a.get("mae_atr_median") and b.get("mae_atr_median"):
            ratios[k] = {
                "n_pre": a["n"], "n_post": b["n"],
                "mae_ratio_post_over_pre": round(b["mae_atr_median"] / a["mae_atr_median"], 3),
                "mfe_ratio_post_over_pre": (round(b["mfe_atr_median"] / a["mfe_atr_median"], 3)
                                            if a.get("mfe_atr_median") and b.get("mfe_atr_median") else None),
            }
    out["ratios"] = ratios
    out["how_to_read"] = (
        "These windows do not depend on when the trade closed, so a difference here "
        "is not the tighter stop truncating its own observation. Read n first: a "
        "longer window excludes more recent trades, so the 48h cell is a different "
        "and smaller population than the 4h one."
    )
    return out


# ===========================================================================
# Dose-response -- the natural experiment, no simulation needed
# ===========================================================================
def dose_response(trades: list[dict], tol: float) -> dict:
    """Group e35 legs by the SIZE of their stop change and compare eras.

    If tighter stops are the cause, the dose must track the damage: the 1.5 legs
    degrade most, the 2.0 legs less, and the WIDENED 3.0 leg not at all.
    """
    pop = population(trades)
    buckets: dict[str, dict[str, list[dict]]] = collections.defaultdict(
        lambda: {"pre": [], "post": []})
    for r in pop:
        leg = str(r.get("strategy_name") or "")
        if group_of(leg) != "e35":
            continue
        spec = E35_LEGS.get(leg) or {}
        pair = spec.get("atr_stop_mult")
        if not pair:
            dose = "no_stop_change(tp_r_only)"
        else:
            old, new = float(pair[0]), float(pair[1])
            ratio = new / old
            dose = (f"tightened_to_{new:g}_from_{old:g}" if new < old
                    else f"WIDENED_to_{new:g}_from_{old:g}")
            dose = f"{dose} (ratio {ratio:.2f})"
        era = era_of(r)
        if era in ("pre", "post"):
            buckets[dose][era].append(r)
    out = {}
    for dose, eras in buckets.items():
        cell = {}
        for era, rs in eras.items():
            n = len(rs)
            if not n:
                cell[era] = {"n": 0}
                continue
            adj = collections.Counter(adjudicate_exit(r, tol) for r in rs)
            gradeable = adj["reached_stop"] + adj["reached_target"] + adj["neither"]
            wins = sum(1 for r in rs if (_f(r.get("pnl")) or 0) > 0)
            cell[era] = {
                "rows": n,
                "packages": len({r.get("order_package_id") for r in rs}),
                "wins": wins, "win_rate": round(wins / n, 3),
                "stop_outs": adj["reached_stop"], "gradeable": gradeable,
                "stop_rate": round(adj["reached_stop"] / gradeable, 3) if gradeable else None,
                "stop_rate_ci": wilson(adj["reached_stop"], gradeable) if gradeable else None,
                "pnl": round(sum(_f(r.get("pnl")) or 0 for r in rs), 2),
            }
        a, b = cell.get("pre", {}), cell.get("post", {})
        if a.get("gradeable") and b.get("gradeable"):
            cell["stop_rate_fisher_p"] = fisher_2x2(
                a["stop_outs"], a["gradeable"] - a["stop_outs"],
                b["stop_outs"], b["gradeable"] - b["stop_outs"])
        out[dose] = cell
    return out


# ===========================================================================
# Self-test -- the arithmetic, not the network
# ===========================================================================
def self_test() -> int:
    fails = []

    def chk(name, got, want):
        if got != want:
            fails.append(f"{name}: got {got!r} want {want!r}")

    # first_touch respects bar EXTREMES, not closes, in both directions.
    bars = [[0, 10.0, 10.5, 9.8, 10.1], [60_000, 10.1, 10.4, 9.0, 9.2]]
    chk("touch_below_found", first_touch(bars, 9.5, "below"), 60_000)
    chk("touch_below_first_bar", first_touch(bars, 9.9, "below"), 0)
    chk("touch_below_missing", first_touch(bars, 8.0, "below"), None)
    chk("touch_above_found", first_touch(bars, 10.45, "above"), 0)
    chk("touch_above_missing", first_touch(bars, 11.0, "above"), None)
    chk("extreme_below", extreme(bars, "below"), 9.0)
    chk("extreme_above", extreme(bars, "above"), 10.5)
    chk("extreme_empty", extreme([], "below"), None)

    # stop_change must REFUSE a tp_r-only leg rather than return a 1.0 ratio.
    chk("tp_r_only_leg_refused", stop_change("ada_pullback_2h"), None)
    chk("sol_leg_ladder", stop_change("trend_donchian_sol_4h"), (2.5, 1.5))
    chk("widened_leg_ladder", stop_change("htf_pullback_trend_2h"), (2.5, 3.0))
    chk("unknown_leg_refused", stop_change("not_a_leg"), None)

    # The race never collapses "both in one bar" into an ordering.
    u = {"stop_old": 9.0, "take_profit": 11.0, "adverse_side": "below",
         "favourable_side": "above", "entry": 10.0, "atr": 0.5, "old_mult": 2.5}
    chk("race_empty", _race(u, [])["outcome"], "ungradeable_no_candles")
    chk("race_neither", _race(u, [[0, 10.0, 10.2, 9.8, 10.0]])["outcome"], "neither_at_horizon")
    chk("race_stop_only", _race(u, [[0, 10.0, 10.2, 8.9, 9.0]])["outcome"], "wide_stop_also_hit")
    chk("race_target_only", _race(u, [[0, 10.0, 11.1, 9.8, 11.0]])["outcome"], "target_reached_first")
    chk("race_same_bar", _race(u, [[0, 10.0, 11.1, 8.9, 10.0]])["outcome"], "same_bar_ambiguous")
    chk("race_stop_first",
        _race(u, [[0, 10.0, 10.2, 8.9, 9.0], [60_000, 9.0, 11.2, 9.0, 11.0]])["outcome"],
        "wide_stop_also_hit")
    chk("race_target_first",
        _race(u, [[0, 10.0, 11.2, 9.9, 11.0], [60_000, 11.0, 11.0, 8.9, 9.0]])["outcome"],
        "target_reached_first")
    # MFE is expressed in the PRE-e35 risk unit, which is what is under test.
    chk("race_mfe_old_R",
        _race(u, [[0, 10.0, 11.25, 9.9, 11.0]])["max_favourable_excursion_old_R"], 1.0)

    # _ms / _iso round-trip, including a bare (tz-naive) stamp.
    chk("ms_none", _ms(None), None)
    chk("ms_bad", _ms("not-a-date"), None)
    chk("ms_z_and_offset_agree", _ms("2026-08-30T08:53:19Z"), _ms("2026-08-30T08:53:19+00:00"))
    chk("ms_naive_is_utc", _ms("2026-08-30T00:00:00"), _ms("2026-08-30T00:00:00+00:00"))

    # The WIDENED rung must never be described as a tightening.
    chk("widened_rung_direction",
        "tighter" if stop_change("htf_pullback_trend_2h")[1] < stop_change("htf_pullback_trend_2h")[0] else "WIDER",
        "WIDER")
    chk("tightened_rung_direction",
        "tighter" if stop_change("trend_donchian_sol_4h")[1] < stop_change("trend_donchian_sol_4h")[0] else "WIDER",
        "tighter")

    print("self-test: FAIL" if fails else "self-test: OK")
    for f in fails:
        print("  -", f)
    return 1 if fails else 0


# ===========================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--trades", help="JSON from /api/diag/journal?table=trades&limit=1000")
    ap.add_argument("--packages", help="JSON from /api/diag/journal?table=order_packages&limit=1000")
    ap.add_argument("--cache", default=os.environ.get("OKX_CACHE", "/tmp/okx-candles"))
    ap.add_argument("--tol", type=float, default=0.0015,
                    help="adjudicator touch tolerance (MI-271's default)")
    ap.add_argument("--out", help="write the full result JSON here")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not a.trades or not a.packages:
        ap.error("--trades and --packages are required (or use --self-test)")

    trades = json.load(open(a.trades))
    if isinstance(trades, dict):
        trades = trades.get("rows") or trades.get("data") or []
    pk = json.load(open(a.packages))
    if isinstance(pk, dict):
        pk = pk.get("rows") or pk.get("data") or []
    pkgs = {p["order_package_id"]: p for p in pk if p.get("order_package_id")}

    units = build_units(trades, pkgs, a.tol)
    data_end = max(_ms(r.get("closed_at")) or 0 for r in trades if r.get("closed_at"))
    data_end = max(data_end, int(time.time() * 1000) - 2 * 3_600_000)

    post_stops = [u for u in units["post"]
                  if u["rows"][0]["adjudicated"] == "reached_stop"
                  and u["stop_integrity"] == "stop_is_entry_declared"]
    post_stops_amended = [u for u in units["post"]
                          if u["rows"][0]["adjudicated"] == "reached_stop"
                          and u["stop_integrity"] != "stop_is_entry_declared"]

    result = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "e35_deploy_utc": E35_DEPLOY_UTC,
        "price_basis": {
            "source": "OKX /api/v5/market/history-candles, *-USDT-SWAP, 1m",
            "why_not_bybit": "api.bybit.com HTTP 403 (CloudFront country block) and "
                             "api.binance.com HTTP 451 from the research container; "
                             "the VENUE refuses on geography, not the sandbox proxy",
            "instrument_class": "USDT-margined perpetual swap, matching Bybit linear perps",
        },
        "population": {
            "definition": "closed, non-backtest, pnl NOT NULL, pairs excluded "
                          "(MI-271's population(), imported) -> e35 legs with an "
                          "atr_stop_mult change -> split on created_at (OPEN time)",
            "unit_of_observation": "order package (one price path); fanned-out "
                                   "trade rows share it and are NOT independent",
            "pre_packages": len(units["pre"]), "pre_rows": units["rows_pre"],
            "post_packages": len(units["post"]), "post_rows": units["rows_post"],
            "excluded": dict(units["excluded"]),
            "entry_frozen_mult_disagrees_with_era": units["mult_mismatch"],
        },
        "arm_a_scope": {
            "post_stop_out_packages_counterfactualable": len(post_stops),
            "post_stop_out_packages_excluded_stop_amended": [
                {"package": u["package"], "leg": u["leg"],
                 "stop_integrity": u["stop_integrity"],
                 "final_stop_atr_from_entry": round(u.get("final_stop_atr_from_entry", 0), 3),
                 "declared_stop_at_atr": u["new_mult"],
                 "trade_ids": [r["trade_id"] for r in u["rows"]]}
                for u in post_stops_amended],
        },
        "dose_response": dose_response(trades, a.tol),
    }

    print("fetching candles + running positive controls ...", file=sys.stderr)
    result["pc1_basis"] = pc1_basis(units["pre"] + units["post"], a.cache)
    result["pc2_reproduce_actual_stop"] = pc2_reproduce(post_stops, a.cache, PC2_TOL_MIN)
    result["pc3_false_positive_bound"] = pc3_false_positive(units["pre"], a.cache)

    gate = [result["pc1_basis"]["verdict"], result["pc2_reproduce_actual_stop"]["verdict"],
            result["pc3_false_positive_bound"]["verdict"]]
    result["controls_verdict"] = ("pass" if all(g in ("fit", "pass") for g in gate)
                                 else "at_least_one_control_failed_read_before_trusting_arms")

    print("running ARM B (reverse, horizon-free) ...", file=sys.stderr)
    result["arm_b_reverse"] = arm_b(units["pre"], a.cache)
    print("running ARM A (forward, horizoned) ...", file=sys.stderr)
    result["arm_a_forward"] = arm_a(post_stops, a.cache, data_end)
    print("measuring the excursion regime (both eras) ...", file=sys.stderr)
    result["excursion_regime"] = excursion_regime(units["pre"], units["post"], a.cache)
    print("adjudicating exit location from the PRICE PATH ...", file=sys.stderr)
    result["candle_adjudication"] = candle_adjudication(units["pre"], units["post"], a.cache)
    print("measuring fixed-window excursions (truncation control) ...", file=sys.stderr)
    result["fixed_window_excursion"] = fixed_window_excursion(
        units["pre"], units["post"], a.cache, data_end)
    result["stop_integrity_census"] = stop_integrity_census(units["pre"], units["post"])
    result["declared_dist_in_atr_check"] = {
        era: sorted({u["leg"]: u["declared_dist_in_atr"] for u in units[era]}.items())
        for era in ("pre", "post")}

    if a.out:
        json.dump(result, open(a.out, "w"), indent=1, default=str)
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(json.dumps(result, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
