#!/usr/bin/env python3
"""E3.5 — the BARRIER RACE: at entry, which bracket is a trade more likely to hit?

OPERATOR QUESTION (2026-08-20), recorded because it reframes the programme:

    "At any given moment is the trade more likely to hit the stop loss or the take
    profit first ... especially if we're relying mostly on the brackets for exit
    strategies. And then, like, either should the brackets be adjusted based on
    that risk or is the correct thing to drop the trade at that moment."

WHY THIS WAS NOT ALREADY BEING ASKED
------------------------------------
`e3_barrier_decomposition.py` measured that the terminal barrier alone accounts for
**13.9% / 27.0% / 46.6%** of `label_hold`'s entropy at h=12/24/48 — and treated that
as a CONTAMINANT, the reason E3's licence did not survive. The operator's question
inverts it: **the race is the quantity to predict, not the one to control for.**

The distinction that makes this legitimate where the E3 stratified view was not:
`e3_barrier_decomposition` STRATIFIED on `touch`, which conditions on a barrier the
trade only reaches LATER. Predicting `touch` from decision-time state does not — it
forecasts it. `src/research/triple_barrier.triple_barrier_forward` computes `touch`
over the strictly-future window `candles[t+1 : t+1+time_stop]`, so it is a proper
forward label.

WHAT THIS TOOL MEASURES — THE ENTRY-TIME HALF, WHICH NEEDS NO ML
----------------------------------------------------------------
Before any conditional model, a large part of the race is **deterministic and known
at entry**, and nothing in the system reads it.

The live take-profit on the donchian/pullback fleet is
``tp = min(entry*(1 + cap), entry + tp_r*risk)`` with ``cap = 0.099``. 17 of 19 legs
declare ``tp_r: 50.0`` — the "no R target" sentinel — so **the cap always binds and
the placed take-profit is the venue's rejection threshold, not a decision.** Its
distance in R is therefore

    tp_R = cap / (risk/entry)        and     risk = atr_stop_mult * ATR

so ``tp_R`` and ``ATR/close`` are **THE SAME VARIABLE** up to the constant
``cap / atr_stop_mult`` (verified to float precision: max deviation 2.78e-17 over
2,185 trades). Two consequences, both measured by `--report race`:

1. ``tp_R`` varies **28x-33x within a single leg**, so "how far is my target" is not
   a leg property at all — it is a per-trade one.
2. The venue cap silently imports a **volatility filter** into the exit policy that
   nobody designed: ``tp_R < 0.75`` is *exactly* ``ATR/close > 5.28%``.

⚠️ **THAT COLLINEARITY IS NOT SEPARABLE ON THIS DATA AND THE REPORT SAYS SO.** At a
fixed ``atr_stop_mult`` you cannot ask "is this the bracket geometry or the vol
regime" — they are one number. Separating them requires VARYING ``atr_stop_mult``,
which is what `e35_bracket_geometry_sweep.py` does. Any claim here that names one
cause over the other is unsupported, and this tool refuses to make it.

WHAT IT DOES NOT MEASURE
------------------------
The **conditional, mid-trade** race — P(sl first | state at bar t) — which is the
other half of the operator's question and the natural target for the exit-head ML
rig (`analyze_exit_head.py`, which today trains on `label_hold`). That needs the
per-bar panel and is deliberately NOT bolted on here.

Observe-only, Tier-1: runs the harnesses read-only, writes a report, touches nothing
live. Changing any bracket parameter in `config/strategies.yaml` is **Tier-3**.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "research"))

import yaml  # noqa: E402

import m20_fleet_exit_sweep as fleet  # noqa: E402

# tp_R buckets. Chosen so the boundary that MATTERS gets its own bucket: `< 1.0`
# is where the take-profit sits NEARER than the stop, which inverts the race. The
# 0.75/1.25 pair straddles it rather than splitting exactly at 1.0, so a trade at
# 0.99 and one at 1.01 are not reported as different populations.
BUCKETS = ((0.0, 0.75), (0.75, 1.25), (1.25, 2.0), (2.0, 3.0), (3.0, 5.0),
           (5.0, float("inf")))

# Exit reasons that ARE a bracket touch, and the ones that are not. `trail_stop`
# is deliberately its own thing: it is a stop that MOVED, so counting it as `stop`
# would attribute a managed exit to the entry-time bracket — the exact conflation
# this programme exists to stop.
TP_REASONS = frozenset({"take_profit"})
STOP_REASONS = frozenset({"stop"})


def bucket_label(lo: float, hi: float) -> str:
    return f"{lo:g}-{hi:g}" if hi != float("inf") else f">{lo:g}"


def bucket_of(tp_r: float) -> str | None:
    for lo, hi in BUCKETS:
        if lo <= tp_r < hi:
            return bucket_label(lo, hi)
    return None


def tp_r_of(entry: float, sl: float, cap_pct: float,
            declared_tp_r: float | None = None) -> float | None:
    """Distance to the take-profit ACTUALLY PLACED, in R. None when underivable.

    The unit places ``tp = min(entry*(1+cap), entry + tp_r*risk)``, so the effective
    distance is ``min(cap/(risk/entry), tp_r)`` — **the declared `tp_r` is not always
    the sentinel and must not be assumed away.**

    ⚠️ An earlier version of this function ignored `declared_tp_r` and returned the
    cap distance alone. That is right for the 16 legs at `tp_r: 50.0` (the cap always
    binds below 50) and WRONG for the three `_prop` legs at `tp_r: 6.0`, which it
    reported with a max of 13.74R against a declared ceiling of 6.0 — 1,532 of 6,428
    trades (23.8%) on a quantity that cannot exceed 6 by construction. Caught by the
    max exceeding the declared value, which is why the per-leg table prints both.
    """
    if not entry or entry <= 0 or cap_pct <= 0:
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    from_cap = cap_pct / (risk / entry)
    if declared_tp_r is not None and declared_tp_r > 0:
        return min(from_cap, float(declared_tp_r))
    return from_cap


def run_leg(leg: str, cfg: dict, data_dir: Path, cap_pct: float) -> dict:
    """One config-exact harness run, returning its emitted per-trade rows.

    Base args come from `m20_fleet_exit_sweep.base_args` — imported, not restated,
    so this tool and the fleet sweep can never disagree about what a leg's base is.
    """
    fam = fleet.classify(leg)
    if fam not in ("donchian", "pullback", "squeeze"):
        return {"leg": leg, "state": "out_of_scope", "family": fam}
    sym = (cfg.get("symbols") or [None])[0]
    tf = str(cfg.get("timeframe") or "1h")
    data, proxy, resample = fleet.resolve_data(str(sym), tf, data_dir)
    if data is None:
        return {"leg": leg, "state": "data_missing", "symbol": sym}
    harness = fleet.FAMILY_HARNESS[fam]
    base = fleet.base_args(leg, cfg, fam, data, resample, cap_pct)
    fd, path = tempfile.mkstemp(prefix="e35_race_", suffix=".jsonl")
    os.close(fd)
    fd2, jpath = tempfile.mkstemp(prefix="e35_race_", suffix=".json")
    os.close(fd2)
    try:
        p = subprocess.run(
            [sys.executable, str(REPO / harness), *base,
             "--emit-trades", path, "--json", jpath],
            capture_output=True, text=True, timeout=fleet.CELL_TIMEOUT_S)
        if p.returncode != 0:
            return {"leg": leg, "state": "error",
                    "why": (p.stderr or p.stdout)[-200:]}
        rows = []
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        try:
            summary = json.loads(Path(jpath).read_text())
        except (OSError, json.JSONDecodeError):
            summary = {}
        return {"leg": leg, "state": "measured", "family": fam, "symbol": sym,
                "tf": tf, "harness": harness, "data": data, "proxy": proxy,
                "execution": cfg.get("execution", "live"),
                "rows": rows, "summary": summary,
                "declared_tp_r": cfg.get("tp_r"), "cap_pct": cap_pct}
    except subprocess.TimeoutExpired:
        return {"leg": leg, "state": "error",
                "why": f"timeout after {fleet.CELL_TIMEOUT_S:.0f}s"}
    finally:
        for _p in (path, jpath):
            try:
                os.unlink(_p)
            except OSError:
                pass


def tally(rows: list[dict], cap_pct: float,
          declared_tp_r: float | None = None) -> dict:
    """Per-bucket race outcome + realised net R.

    `undeliverable` counts rows whose tp_R could not be derived — reported, never
    folded into a bucket, because a trade we could not place on the axis is not a
    trade at the bottom of it.
    """
    agg: dict[str, dict] = {}
    undeliverable = 0
    for r in rows:
        try:
            entry, sl = float(r["entry"]), float(r["sl"])
            net_r = float(r["net_r"])
            reason = str(r.get("exit_reason") or "")
        except (KeyError, TypeError, ValueError):
            undeliverable += 1
            continue
        t = tp_r_of(entry, sl, cap_pct,
                    r.get("_declared_tp_r", declared_tp_r))
        b = bucket_of(t) if t is not None else None
        if b is None:
            undeliverable += 1
            continue
        a = agg.setdefault(b, {"n": 0, "tp": 0, "stop": 0, "other": 0,
                               "net_r": 0.0, "tp_r_min": None, "tp_r_max": None})
        a["n"] += 1
        a["net_r"] += net_r
        if reason in TP_REASONS:
            a["tp"] += 1
        elif reason in STOP_REASONS:
            a["stop"] += 1
        else:
            a["other"] += 1
        a["tp_r_min"] = t if a["tp_r_min"] is None else min(a["tp_r_min"], t)
        a["tp_r_max"] = t if a["tp_r_max"] is None else max(a["tp_r_max"], t)
    for b, a in agg.items():
        n = a["n"]
        a["p_tp_first"] = round(100.0 * a["tp"] / n, 1)
        a["p_stop"] = round(100.0 * a["stop"] / n, 1)
        a["p_other"] = round(100.0 * a["other"] / n, 1)
        a["net_r"] = round(a["net_r"], 4)
        a["net_r_per_trade"] = round(a["net_r"] / n, 4)
        a["tp_r_min"] = round(a["tp_r_min"], 3)
        a["tp_r_max"] = round(a["tp_r_max"], 3)
    return {"buckets": agg, "undeliverable": undeliverable,
            "n": sum(a["n"] for a in agg.values())}


def tp_r_spread(rows: list[dict], cap_pct: float,
                atr_stop_mult: float | None = None,
                declared_tp_r: float | None = None) -> dict:
    """The within-leg tp_R distribution — the "is the bracket a leg property?" answer.

    `n_below_1` is the count whose take-profit sits NEARER than its stop; that is
    the population where the race inverts, and it is reported as a count beside `n`
    rather than as a rate alone.
    """
    vals = []
    for r in rows:
        try:
            t = tp_r_of(float(r["entry"]), float(r["sl"]), cap_pct,
                        declared_tp_r)
        except (KeyError, TypeError, ValueError):
            continue
        if t is not None:
            vals.append(t)
    if not vals:
        return {"state": "unmeasured", "n": 0}
    vals.sort()
    n = len(vals)
    return {"state": "measured", "n": n,
            "min": round(vals[0], 3),
            "median": round(vals[n // 2], 3),
            "max": round(vals[-1], 3),
            "spread_multiple": round(vals[-1] / vals[0], 1) if vals[0] > 0 else None,
            "n_below_1": sum(1 for v in vals if v < 1.0),
            # THE SAME CUT, SPELLED AS VOLATILITY. `tp_R = cap/(atr_stop_mult *
            # ATR/close)`, so `tp_R < x` is exactly `ATR/close > cap/(atr_stop_mult*x)`.
            # Reported beside the tp_R figures so a reader cannot mistake the tp_R
            # axis for something independent of volatility — it is not.
            # None (never a number) when atr_stop_mult was not readable: we did not
            # look, and a fabricated threshold here would be a false equivalence.
            "atr_close_at_tp_r_1": (round(cap_pct / atr_stop_mult, 5)
                                    if atr_stop_mult else None),
            "atr_stop_mult": atr_stop_mult}


def collinearity_check(rows: list[dict], cap_pct: float,
                       atr_stop_mult: float | None) -> dict:
    """POSITIVE CONTROL on the module's central algebraic claim.

    Asserts `tp_R` and `ATR/close` are the same variable by recomputing one from
    the other and reporting the worst absolute deviation. A claim this load-bearing
    is checked against the data rather than argued from the formula — the formula
    is where a wrong `atr_stop_mult` or a harness that clamps differently would hide.

    `max_abs_dev` at float epsilon confirms it; anything larger means the assumed
    relation does NOT hold on this leg and every tp_R-as-vol statement about it is
    void. Three states, never collapsed: `confirmed` / `violated` / `unchecked`
    (no `atr_stop_mult`, so there was nothing to check against).
    """
    if not atr_stop_mult or atr_stop_mult <= 0 or cap_pct <= 0:
        return {"state": "unchecked", "why": "atr_stop_mult or cap unreadable"}
    worst, n = 0.0, 0
    for r in rows:
        try:
            entry, sl = float(r["entry"]), float(r["sl"])
        except (KeyError, TypeError, ValueError):
            continue
        risk_frac = abs(entry - sl) / entry if entry else 0.0
        if risk_frac <= 0:
            continue
        t = cap_pct / risk_frac
        implied_atr_frac = cap_pct / (atr_stop_mult * t)
        worst = max(worst, abs(implied_atr_frac - risk_frac / atr_stop_mult))
        n += 1
    if n == 0:
        return {"state": "unchecked", "why": "no usable rows"}
    return {"state": "confirmed" if worst < 1e-9 else "violated",
            "max_abs_dev": worst, "n": n}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", default=str(REPO / "data"))
    ap.add_argument("--only", default=None, help="comma-separated leg names")
    ap.add_argument("--out", default=str(REPO / "runtime_logs" / "e35_race"))
    ap.add_argument("--tp-cap-pct", type=float, default=fleet.LIVE_TP_CAP_PCT)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv[1:])
    if a.selftest:
        return _selftest()

    only = [s.strip() for s in a.only.split(",")] if a.only else None
    strats = yaml.safe_load(
        (REPO / "config" / "strategies.yaml").read_text())["strategies"]
    legs, skipped = [], []
    for name, cfg in sorted(strats.items()):
        if not isinstance(cfg, dict) or (only and name not in only):
            continue
        r = run_leg(name, cfg, Path(a.data_dir), a.tp_cap_pct)
        if r["state"] != "measured":
            skipped.append(r)
            continue
        legs.append(r)
        print(f"  ran {name:26s} {len(r['rows'])} trades", flush=True)

    pooled_rows: list[dict] = []
    per_leg = []
    for r in legs:
        sm = fleet.flag_value(
            fleet.base_args(r["leg"], strats[r["leg"]], r["family"], r["data"],
                            None, a.tp_cap_pct), "--atr-stop-mult")
        per_leg.append({
            "leg": r["leg"], "symbol": r["symbol"], "tf": r["tf"],
            "execution": r["execution"], "declared_tp_r": r["declared_tp_r"],
            "trades": len(r["rows"]),
            "by_outcome": (r["summary"] or {}).get("by_outcome"),
            "net_total_r": (r["summary"] or {}).get("net_total_r"),
            "tp_r": tp_r_spread(r["rows"], a.tp_cap_pct, sm,
                                r["declared_tp_r"]),
            "race": tally(r["rows"], a.tp_cap_pct, r["declared_tp_r"]),
            "collinearity": collinearity_check(r["rows"], a.tp_cap_pct, sm),
        })
        # Pooled rows carry their leg's declared ceiling, because the three
        # `_prop` legs cap at 6.0 while the rest run the sentinel. Pooling raw
        # rows and applying ONE ceiling would misplace 1,532 of 6,428 trades.
        for row in r["rows"]:
            pooled_rows.append(dict(row, _declared_tp_r=r["declared_tp_r"]))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tp_cap_pct": a.tp_cap_pct,
        "population": {
            "legs": len(legs), "trades": len(pooled_rows),
            "note": "config-exact base runs, full available history, net of fees "
                    "(harness --fee-bps-roundtrip default = "
                    "execution_costs.DEFAULT_FEE_BPS_ROUNDTRIP)",
        },
        "skipped": skipped,
        "per_leg": per_leg,
        "pooled_race": tally(pooled_rows, a.tp_cap_pct),
    }
    run_dir = Path(a.out) / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    (run_dir / "SUMMARY.md").write_text(render(report))
    print(render(report))
    print(f"wrote {run_dir}/report.json + SUMMARY.md")
    return 0


def _order() -> list[str]:
    return [bucket_label(lo, hi) for lo, hi in BUCKETS]


def render(report: dict) -> str:
    pop = report["population"]
    L = ["# E3.5 — the barrier race at entry", "",
         f"Generated `{report['generated_at']}` · tp cap `{report['tp_cap_pct']}`",
         "",
         f"**POPULATION: {pop['trades']} trades across {pop['legs']} legs** "
         f"(donchian/pullback/squeeze, config-exact base, full available history, "
         f"net of fees).",
         "",
         "`tp_R = cap / (risk/entry)` is the entry-time distance to the "
         "venue-capped take-profit, in R.",
         "",
         "⚠️ **`tp_R` and `ATR/close` are the same variable** up to "
         "`cap / atr_stop_mult`, so this axis CANNOT distinguish "
         "\"the bracket geometry is wrong\" from \"the strategy loses in high "
         "vol\". Separating them needs `atr_stop_mult` to vary — that is "
         "`e35_bracket_geometry_sweep.py`, not this.",
         "", "## Pooled race", "",
         "| tp_R bucket | n | % pop | P(TP first) | P(stop) | P(other) | net_R | "
         "net_R/trade |", "|---|---|---|---|---|---|---|---|"]
    pr = report["pooled_race"]
    tot = pr["n"] or 1
    for b in _order():
        x = pr["buckets"].get(b)
        if not x:
            continue
        L.append(f"| `{b}` | {x['n']} | {100*x['n']/tot:.1f}% | "
                 f"{x['p_tp_first']}% | {x['p_stop']}% | {x['p_other']}% | "
                 f"{x['net_r']} | {x['net_r_per_trade']} |")
    if pr.get("undeliverable"):
        L.append(f"\n_{pr['undeliverable']} rows could not be placed on the "
                 f"tp_R axis and are excluded from every bucket (not folded "
                 f"into one)._")
    L += ["", "## Per leg — is the bracket a leg property?", "",
          "| leg | exec | declared tp_r | n | tp_R min | median | max | "
          "spread x | tp_R<1 | ATR/close at tp_R=1 | collinearity |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in report["per_leg"]:
        t = p["tp_r"]
        if t.get("state") != "measured":
            L.append(f"| `{p['leg']}` | {p['execution']} | "
                     f"{p['declared_tp_r']} | — | | | | | | | "
                     f"{t.get('state')} |")
            continue
        acl = t.get("atr_close_at_tp_r_1")
        L.append(f"| `{p['leg']}` | {p['execution']} | {p['declared_tp_r']} | "
                 f"{t['n']} | {t['min']} | {t['median']} | {t['max']} | "
                 f"{t['spread_multiple']}x | {t['n_below_1']} | "
                 f"{(f'{100*acl:.2f}%' if acl else '—')} | "
                 f"{p['collinearity']['state']} |")
    return "\n".join(L) + "\n"


def _selftest() -> int:
    ok = fail = 0

    def chk(name, got, want):
        nonlocal ok, fail
        if got == want:
            ok += 1
        else:
            fail += 1
            print(f"FAIL {name}: got {got!r} want {want!r}")

    # tp_r_of — the central formula.
    chk("tp_R at 5% risk", round(tp_r_of(100.0, 95.0, 0.099), 4), 1.98)
    chk("tp_R at 25.54% risk", round(tp_r_of(0.9699, 0.7222, 0.099), 3), 0.388)
    chk("tp_R short side", round(tp_r_of(100.0, 105.0, 0.099), 4), 1.98)
    chk("tp_R zero risk", tp_r_of(100.0, 100.0, 0.099), None)
    chk("tp_R zero entry", tp_r_of(0.0, 1.0, 0.099), None)
    chk("tp_R no cap", tp_r_of(100.0, 95.0, 0.0), None)
    # The declared ceiling BINDS when it is nearer than the cap. This is the
    # `_prop` legs (tp_r 6.0) and the case the first version of this function got
    # wrong on 23.8% of the population.
    chk("tp_R ceiling binds", tp_r_of(100.0, 99.0, 0.099, 6.0), 6.0)
    chk("tp_R cap binds under ceiling",
        round(tp_r_of(100.0, 95.0, 0.099, 6.0), 4), 1.98)
    chk("tp_R sentinel never binds",
        round(tp_r_of(100.0, 99.0, 0.099, 50.0), 3), 9.9)
    chk("tp_R ceiling ignored when non-positive",
        round(tp_r_of(100.0, 99.0, 0.099, 0.0), 3), 9.9)

    chk("bucket low", bucket_of(0.31), "0-0.75")
    chk("bucket straddle lo", bucket_of(0.75), "0.75-1.25")
    chk("bucket straddle hi", bucket_of(1.24), "0.75-1.25")
    chk("bucket top open", bucket_of(40.89), ">5")
    chk("bucket boundary 5", bucket_of(5.0), ">5")
    chk("bucket negative", bucket_of(-1.0), None)

    # tally — outcome classification. `trail_stop` must NOT count as `stop`:
    # a stop that MOVED is a managed exit, not the entry-time bracket.
    rows = [
        {"entry": 100.0, "sl": 95.0, "net_r": 1.98, "exit_reason": "take_profit"},
        {"entry": 100.0, "sl": 95.0, "net_r": -1.0, "exit_reason": "stop"},
        {"entry": 100.0, "sl": 95.0, "net_r": 0.4, "exit_reason": "trail_stop"},
        {"entry": 100.0, "sl": 95.0, "net_r": 0.1, "exit_reason": "timeout"},
    ]
    t = tally(rows, 0.099)
    b = t["buckets"]["1.25-2"]
    chk("tally n", b["n"], 4)
    chk("tally tp", b["tp"], 1)
    chk("tally stop counts only real stop", b["stop"], 1)
    chk("tally other holds trail+timeout", b["other"], 2)
    chk("tally p_tp", b["p_tp_first"], 25.0)
    chk("tally net", b["net_r"], 1.48)
    chk("tally per trade", b["net_r_per_trade"], 0.37)
    chk("tally total n", t["n"], 4)
    chk("tally undeliverable none", t["undeliverable"], 0)

    # A row we cannot place is EXCLUDED, never bucketed at the bottom.
    bad = tally(rows + [{"entry": 100.0, "sl": 100.0, "net_r": 0.0,
                         "exit_reason": "stop"},
                        {"entry": "x", "sl": 1, "net_r": 0, "exit_reason": "stop"}],
                0.099)
    chk("undeliverable counted", bad["undeliverable"], 2)
    chk("undeliverable not bucketed", bad["n"], 4)

    sp = tp_r_spread(rows, 0.099, 2.5)
    chk("spread n", sp["n"], 4)
    chk("spread min==max", (sp["min"], sp["max"]), (1.98, 1.98))
    chk("spread multiple", sp["spread_multiple"], 1.0)
    chk("spread below1", sp["n_below_1"], 0)
    # ATR/close at tp_R=1 is cap/atr_stop_mult -> 0.099/2.5 = 0.0396.
    chk("vol threshold", sp["atr_close_at_tp_r_1"], 0.0396)
    chk("vol threshold unknown mult",
        tp_r_spread(rows, 0.099, None)["atr_close_at_tp_r_1"], None)
    chk("spread empty", tp_r_spread([], 0.099, 2.5)["state"], "unmeasured")

    # collinearity_check — the positive control must CONFIRM on real geometry and
    # must be `unchecked` (never a fabricated pass) when the mult is unreadable.
    cc = collinearity_check(rows, 0.099, 2.5)
    chk("collinearity confirmed", cc["state"], "confirmed")
    chk("collinearity n", cc["n"], 4)
    chk("collinearity unchecked", collinearity_check(rows, 0.099, None)["state"],
        "unchecked")
    chk("collinearity no rows",
        collinearity_check([], 0.099, 2.5)["state"], "unchecked")

    chk("bucket order", _order(),
        ["0-0.75", "0.75-1.25", "1.25-2", "2-3", "3-5", ">5"])
    print(f"selftest: {ok} pass, {fail} fail")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
