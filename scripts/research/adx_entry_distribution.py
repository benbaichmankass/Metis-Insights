#!/usr/bin/env python3
# wiring: manual-only — a one-shot measurement answering "what would an adx_min
# floor REFUSE on these legs"; the run needs live candle fetches and per-leg
# harness runs, so there is no cadence at which it should fire automatically.
# Its --selftest IS wired (artifact-validity-guard), because the invariants it
# pins — refusal is monotone in the floor, an unmeasured leg never reads as a
# clean zero — do not move even though the data does.
"""What would an `adx_min` floor actually REFUSE on the legs that have none?

WHY THIS EXISTS
---------------
`BL-20260823-TWELVE-PULLBACK-LEGS-HAVE-NO-ENTRY-REGIME-CONDITION`: 12 of 16
enabled+live `pullback` legs declare no `adx_min`. Because
`htf_pullback_trend_2h.py:76` defaults it to `None` and the gate at :297 runs
only when it is declared, those legs have **no ADX filter at entry at all**.

So "declare `adx_min` to fix `thesis_unknown`" is **not** an observability fix —
it starts refusing setups that are currently admitted. The only declared values
in the family are 25 / 28 / 30, every one on a **2h crypto** leg, while these 12
are 1d/1h equity, bond and metal. Porting one across instrument class and
timeframe would be a chosen number, and the operator's standing directive is
explicit: *"Do not reach for a clamp, a floor, or a refusal when the honest
answer is that the geometry was never constructed."*

This module makes the value DERIVABLE instead: for each leg it measures the
**ADX at the bars where entries actually occurred**, then reports, for a grid of
candidate floors, the fraction of those historical entries the floor would have
**refused**. A floor refusing ~0% is nearly inert; one refusing 40% is a
different strategy. Nobody could tell which before this ran.

⚠️ **ADX AT ENTRY BARS, NOT ADX OVER ALL BARS.** Those are different
distributions and only the first answers the question. Entry bars come from the
harness's own emitted trades, so the entry logic is the harness's, not a
reimplementation of it.

⚠️ **`_adx` IS IMPORTED, NEVER RE-DERIVED.** `scripts/backtest_pullback.py::_adx`
is a verbatim copy of the live strategy's own, which is what makes the live
predicate and the harness unable to disagree about the number. A second
implementation here would be free to drift and would look right in isolation —
the `candle_io` lesson.

⚠️ **THE HARNESS IS RUN WITH `--adx-min 0`, WHICH REJECTS NOTHING.**
`adx_active = adx_min is not None`, so ADX is only *computed* when a floor is
set; `0` makes it compute while `adx_val < 0` never fires. The emitted trade set
is therefore the **unfiltered** one — today's live behaviour — which is the
correct denominator for "what would a floor have refused".

STATES, NEVER COLLAPSED
-----------------------
`measured`        — entries found and graded.
`insufficient_n`  — fewer than `--min-trades` entries. A refusal rate over 3
                    trades is not a rate. **Not** a clean result.
`no_entries`      — the harness ran and produced zero trades. We looked.
`no_data`         — no candles for the symbol (e.g. IBKR futures returning
                    `no_data`). **We could not look** — never folded into
                    `no_entries`, which is the opposite claim.
`harness_error`   — the run failed; graded nothing.

Config-driven: every leg's parameters are read from `config/strategies.yaml`, so
this cannot silently measure a geometry the fleet does not run.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]

STATE_MEASURED = "measured"
STATE_INSUFFICIENT = "insufficient_n"
STATE_NO_ENTRIES = "no_entries"
STATE_NO_DATA = "no_data"
STATE_ERROR = "harness_error"

# Candidate floors. Deliberately spans well below and above the family's own
# declared 25/28/30 so the report shows where refusal STARTS, not just what the
# crypto legs happen to use.
DEFAULT_FLOORS = (10.0, 15.0, 18.0, 20.0, 22.0, 25.0, 28.0, 30.0, 35.0)


def _pct(values: List[float], q: float) -> Optional[float]:
    """Nearest-rank percentile. `q` in [0,100]. None on an empty series."""
    if not values:
        return None
    s = sorted(values)
    if q <= 0:
        return s[0]
    if q >= 100:
        return s[-1]
    import math
    k = max(1, math.ceil(q / 100.0 * len(s)))
    return s[min(k, len(s)) - 1]


def refusal_rate(adx_at_entry: List[float], floor: float) -> Optional[float]:
    """Fraction of entries an `adx_min = floor` would have REFUSED.

    The gate admits `adx >= adx_min`, so a refusal is strictly `adx < floor`.
    None on an empty population — a rate with no denominator is not zero.
    """
    if not adx_at_entry:
        return None
    return sum(1 for v in adx_at_entry if v < floor) / float(len(adx_at_entry))


def leg_configs(path: Path) -> Dict[str, Dict[str, Any]]:
    """Enabled+live pullback legs that declare NO adx_min — the study set."""
    import yaml
    raw = yaml.safe_load(path.read_text())
    strategies = raw.get("strategies", raw)
    out = {}
    for name, cfg in sorted(strategies.items()):
        if not isinstance(cfg, dict):
            continue
        is_pullback = "pullback_lookback" in cfg or "pullback" in name
        if not is_pullback:
            continue
        if not cfg.get("enabled", True) or cfg.get("execution", "live") != "live":
            continue
        if cfg.get("adx_min") is not None:
            continue
        out[name] = cfg
    return out


def run_leg(name: str, cfg: Dict[str, Any], csv_dir: Path, workdir: Path,
            min_trades: int, floors) -> Dict[str, Any]:
    sym = (cfg.get("symbols") or [None])[0]
    tf = cfg.get("timeframe")
    rec: Dict[str, Any] = {"leg": name, "symbol": sym, "timeframe": tf,
                           "state": None, "n_entries": None,
                           "atr_stop_mult": cfg.get("atr_stop_mult")}
    csv = csv_dir / f"{sym}_{tf}.csv"
    if not csv.exists():
        rec["state"] = STATE_NO_DATA
        rec["reason"] = f"no candles at {csv.name} — we could not look"
        return rec
    emit = workdir / f"{name}.jsonl"
    argv = [
        sys.executable, str(REPO / "scripts/backtest_pullback.py"),
        "--data", str(csv), "--symbol", str(sym), "--timeframe", str(tf),
        "--trend-lookback", str(cfg.get("trend_lookback", 40)),
        "--pullback-lookback", str(cfg.get("pullback_lookback", 10)),
        "--pullback-frac", str(cfg.get("pullback_frac", 0.5)),
        "--atr-period", str(cfg.get("atr_period", 14)),
        "--atr-stop-mult", str(cfg.get("atr_stop_mult", 2.5)),
        "--trail-mult", str(cfg.get("trail_mult", 5.0)),
        "--min-confidence", str(cfg.get("min_confidence", 0.0)),
        # computes ADX, rejects nothing — see module docstring
        "--adx-min", "0",
        "--adx-period", str(cfg.get("adx_period", 14)),
        "--emit-trades", str(emit),
    ]
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        rec["state"] = STATE_ERROR
        rec["reason"] = (proc.stderr or proc.stdout or "")[-300:]
        return rec
    rows, malformed = [], 0
    if emit.exists():
        for line in emit.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # COUNTED, never swallowed: a dropped emit row understates n and
                # would bias the distribution downward while looking like a
                # smaller sample. Surfaced on the record and escalated below.
                malformed += 1
    if malformed:
        rec["n_malformed_emit_rows"] = malformed
    if malformed and not rows:
        rec["state"] = STATE_ERROR
        rec["reason"] = f"{malformed} emit row(s) unparseable and none readable"
        return rec
    if not rows:
        rec["state"] = STATE_NO_ENTRIES
        rec["n_entries"] = 0
        return rec

    # ADX from the HARNESS'S OWN function, over the same candles.
    sys.path.insert(0, str(REPO / "scripts"))
    from backtest_pullback import _adx  # noqa: E402  (imported, never re-derived)
    from candle_io import load_candles  # noqa: E402
    import pandas as pd  # noqa: E402

    df = load_candles(str(csv))
    period = int(cfg.get("adx_period", 14) or 14)
    series = _adx(df, period)
    idx = pd.to_datetime(df["timestamp"], utc=True)
    lookup = {t: v for t, v in zip(idx, series)}

    vals, unmatched = [], 0
    for r in rows:
        t = r.get("entry_time")
        if not t:
            unmatched += 1
            continue
        ts = pd.to_datetime(t, utc=True)
        v = lookup.get(ts)
        if v is None or v != v:  # missing or NaN warm-up
            unmatched += 1
            continue
        vals.append(float(v))

    rec["n_entries"] = len(rows)
    rec["n_adx_resolved"] = len(vals)
    rec["n_unmatched"] = unmatched
    if len(vals) < min_trades:
        rec["state"] = STATE_INSUFFICIENT
        rec["adx_values"] = sorted(round(v, 2) for v in vals)
        return rec
    rec["state"] = STATE_MEASURED
    rec["adx_min_observed"] = round(min(vals), 2)
    rec["adx_p10"] = round(_pct(vals, 10), 2)
    rec["adx_p25"] = round(_pct(vals, 25), 2)
    rec["adx_p50"] = round(_pct(vals, 50), 2)
    rec["adx_p75"] = round(_pct(vals, 75), 2)
    rec["adx_max_observed"] = round(max(vals), 2)
    rec["refusal_rate"] = {str(f): round(refusal_rate(vals, f), 4) for f in floors}
    return rec


def selftest() -> int:
    fails = []
    def chk(label, got, want):
        if got != want:
            fails.append(f"{label}: got {got!r} want {want!r}")
    # the gate admits adx >= floor, so refusal is STRICT less-than
    v = [10.0, 20.0, 30.0, 40.0]
    chk("floor below all refuses none", refusal_rate(v, 5.0), 0.0)
    chk("floor above all refuses all", refusal_rate(v, 50.0), 1.0)
    chk("floor equal to a value does NOT refuse it", refusal_rate(v, 20.0), 0.25)
    chk("midpoint", refusal_rate(v, 35.0), 0.75)
    # monotonicity — the property that makes the table readable
    rates = [refusal_rate(v, f) for f in (5, 15, 25, 35, 45)]
    chk("refusal is monotone non-decreasing in the floor",
        all(a <= b for a, b in zip(rates, rates[1:])), True)
    # an empty population is NOT a clean zero
    chk("empty -> None, never 0.0", refusal_rate([], 25.0), None)
    chk("empty percentile -> None", _pct([], 50), None)
    # percentiles
    chk("p50 nearest-rank", _pct([1.0, 2.0, 3.0, 4.0], 50), 2.0)
    chk("p100 is the max", _pct([1.0, 9.0], 100), 9.0)
    # the study set must EXCLUDE legs that already declare a floor
    cfgs = leg_configs(REPO / "config/strategies.yaml")
    chk("study set excludes adx_min declarers",
        all(c.get("adx_min") is None for c in cfgs.values()), True)
    chk("study set is non-empty", len(cfgs) > 0, True)
    for f in fails:
        print("FAIL " + f)
    print("selftest: %d/%d passed" % (10 - len(fails), 10))
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--csv-dir", default="/tmp/w/csv")
    ap.add_argument("--workdir", default="/tmp/w/adx")
    ap.add_argument("--config", default=str(REPO / "config/strategies.yaml"))
    ap.add_argument("--min-trades", type=int, default=10,
                    help="below this an entry set is insufficient_n, not a rate")
    ap.add_argument("--json", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    wd = Path(a.workdir)
    wd.mkdir(parents=True, exist_ok=True)
    cfgs = leg_configs(Path(a.config))
    recs = [run_leg(n, c, Path(a.csv_dir), wd, a.min_trades, DEFAULT_FLOORS)
            for n, c in cfgs.items()]
    if a.json:
        Path(a.json).write_text(json.dumps({"floors": list(DEFAULT_FLOORS),
                                            "legs": recs}, indent=2))
    print("STUDY SET: %d enabled+live pullback legs declaring NO adx_min\n" % len(recs))
    print("%-22s %-6s %-4s %-16s %5s %6s %6s %6s %6s" %
          ("leg", "sym", "tf", "state", "n", "p10", "p25", "p50", "p75"))
    for r in recs:
        print("%-22s %-6s %-4s %-16s %5s %6s %6s %6s %6s" % (
            r["leg"], r["symbol"], r["timeframe"], r["state"],
            r.get("n_adx_resolved", r.get("n_entries", "-")),
            r.get("adx_p10", "-"), r.get("adx_p25", "-"),
            r.get("adx_p50", "-"), r.get("adx_p75", "-")))
    meas = [r for r in recs if r["state"] == STATE_MEASURED]
    if meas:
        print("\nREFUSAL RATE — fraction of historical entries an adx_min floor would REFUSE")
        print("%-22s %5s  %s" % ("leg", "n", "  ".join("%6.0f" % f for f in DEFAULT_FLOORS)))
        for r in meas:
            print("%-22s %5d  %s" % (
                r["leg"], r["n_adx_resolved"],
                "  ".join("%5.1f%%" % (100 * r["refusal_rate"][str(f)]) for f in DEFAULT_FLOORS)))
    bad = [r for r in recs if r.get("n_malformed_emit_rows")]
    if bad:
        print("\n⚠️ malformed emit rows (counted, not swallowed): " +
              ", ".join("%s=%d" % (r["leg"], r["n_malformed_emit_rows"]) for r in bad))
    print("\nstates: " + ", ".join(
        "%s=%d" % (s, sum(1 for r in recs if r["state"] == s))
        for s in (STATE_MEASURED, STATE_INSUFFICIENT, STATE_NO_ENTRIES, STATE_NO_DATA, STATE_ERROR)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
