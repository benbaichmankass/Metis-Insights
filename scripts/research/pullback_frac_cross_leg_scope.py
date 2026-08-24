#!/usr/bin/env python3
"""Establish the RUNNABLE POPULATION for a pullback_frac cross-leg test.

Operator decision 2026-08-24: run the cross-leg test. This is the half that
must come first — *"the criterion goes first"* (the donchian § 6.0b lesson: a
shortlist chosen before its criterion is a shortlist chosen by the argmax).

WHY A POPULATION TOOL AND NOT A SWEEP
-------------------------------------
`pullback_frac` is currently declared by 19 enabled legs at TWO values
(0.5 and 0.618), and the question is whether either GENERALISES or whether each
per-leg value is fit to its own leg. That is a cross-leg claim, so the first
thing that can invalidate it is a population that quietly excludes legs — and
12 of the 19 are exactly the symbols that had **no free candle lane at all**
until 2026-08-24.

⚠️ SPAN IS PART OF THE POPULATION, NOT A FOOTNOTE. yfinance serves `1d`
uncapped but REFUSES a >730 d `1h` request outright (measured, proof run
32734360738). Blending a 10-year daily leg with a 2-year hourly one into a
single "15 legs agree" claim would be an unstated-denominator error, so the
capped legs are reported as their own stratum and never merged.

STATES, NEVER COLLAPSED
  crypto_archive  bybit / binance_vision — full history
  yfinance/full   `1d`, uncapped
  yfinance/capped `1h`, 730 d ceiling — INCLUDED but stratified, never blended
  NOT_SERVED      no lane resolves. **We cannot test this leg**, which is a
                  different statement from "this leg disagreed".
"""
from __future__ import annotations

import collections
import importlib.util
import sys
from pathlib import Path
from typing import Dict, List, Optional

_REPO = Path(__file__).resolve().parents[2]


def _yf_symbols():
    """Load the canonical map WITHOUT importing the ml package (its __init__
    pulls in fourteen dataset builders and needs pyyaml-only deps this tool
    should not require). Same by-path load the candle puller uses."""
    path = _REPO / "ml" / "datasets" / "adapters" / "yf_symbols.py"
    spec = importlib.util.spec_from_file_location("_yf_symbols", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def classify_leg(symbol: Optional[str], timeframe: Optional[str],
                 yf) -> Dict[str, object]:
    """Which lane serves this leg, and with how much history."""
    if not symbol:
        return {"lane": "NOT_SERVED", "span": None, "reason": "no symbol declared"}
    if symbol.endswith("USDT"):
        return {"lane": "crypto_archive", "span": "full", "reason": None}
    if symbol in yf.known_symbols():
        try:
            cap = yf.max_history_days(timeframe)
        except KeyError:
            # An unknown timeframe is NOT "uncapped" — we did not look.
            return {"lane": "yfinance", "span": None,
                    "reason": f"no recorded cap for timeframe {timeframe!r}"}
        return {"lane": "yfinance", "span": "full" if cap is None else f"{cap}d",
                "reason": None}
    return {"lane": "NOT_SERVED", "span": None,
            "reason": f"{symbol} maps to no lane"}


def scope(strategies: Dict[str, dict], yf) -> List[dict]:
    out = []
    for name, cfg in strategies.items():
        if not isinstance(cfg, dict) or cfg.get("pullback_frac") is None:
            continue
        if not cfg.get("enabled"):
            continue
        syms = cfg.get("symbols") or []
        sym = syms[0] if syms else None
        lane = classify_leg(sym, cfg.get("timeframe"), yf)
        out.append({"leg": name, "pullback_frac": cfg["pullback_frac"],
                    "symbol": sym, "timeframe": cfg.get("timeframe"), **lane})
    return out


def selftest() -> int:
    yf = _yf_symbols()
    checks, failed = [], []

    def ck(label, cond):
        checks.append(label)
        if not cond:
            failed.append(label)

    ck("a USDT pair resolves to the crypto archive at full span",
       classify_leg("XRPUSDT", "2h", yf)["lane"] == "crypto_archive")
    ck("a mapped 1d symbol is uncapped",
       classify_leg("SPY", "1d", yf)["span"] == "full")
    ck("a mapped 1h symbol carries the 730 d cap",
       classify_leg("SPY", "1h", yf)["span"] == "730d")
    ck("an unmapped symbol is NOT_SERVED, not silently full",
       classify_leg("NOTATICKER", "1d", yf)["lane"] == "NOT_SERVED")
    ck("an unknown timeframe is not reported as uncapped",
       classify_leg("SPY", "3h", yf)["span"] is None)
    ck("a leg with no symbol is NOT_SERVED rather than crashing",
       classify_leg(None, "1d", yf)["lane"] == "NOT_SERVED")
    # The capped stratum must stay distinguishable from the full one.
    ck("capped and full spans are different values",
       classify_leg("SPY", "1h", yf)["span"]
       != classify_leg("SPY", "1d", yf)["span"])

    print(f"pullback_frac cross-leg scope self-test: {len(checks)} checks")
    for f in failed:
        print(f"  FAIL: {f}")
    if failed:
        return 1
    print("  all pass — every lane/span state is distinguishable")
    return 0


def main(argv) -> int:
    if "--self-test" in argv:
        return selftest()
    if selftest() != 0:
        return 1

    import yaml
    doc = yaml.safe_load((_REPO / "config" / "strategies.yaml").read_text())
    rows = scope(doc.get("strategies", doc), _yf_symbols())

    print("\n=== pullback_frac cross-leg: RUNNABLE POPULATION ===")
    for r in sorted(rows, key=lambda r: (r["lane"], str(r["span"]), r["leg"])):
        span = r["span"] or f"UNKNOWN ({r['reason']})"
        print(f"  {r['leg']:26s} frac={r['pullback_frac']:<6} "
              f"{str(r['symbol']):9s} {str(r['timeframe']):4s}  "
              f"{r['lane']:14s} {span}")

    full = [r for r in rows if r["span"] == "full"]
    capped = [r for r in rows if r["span"] and r["span"] != "full"]
    unserved = [r for r in rows if not r["span"]]

    print(f"\nenabled legs declaring pullback_frac : {len(rows)}")
    print(f"  FULL history (the primary stratum) : {len(full)}  "
          f"{dict(collections.Counter(r['pullback_frac'] for r in full))}")
    print(f"  CAPPED (own stratum, never blended): {len(capped)}  "
          f"{dict(collections.Counter(r['pullback_frac'] for r in capped))}")
    print(f"  NOT SERVED (cannot be tested)      : {len(unserved)}")

    print("\n⚠️ THE CAPPED STRATUM IS NOT A SMALLER VERSION OF THE FULL ONE. "
          "A 730 d hourly leg and a decade-long daily leg do not share a "
          "denominator; reporting 'N legs agree' across both would be an "
          "unstated-population claim. Report them separately or not at all.")
    print("⚠️ NOT_SERVED means WE CANNOT TEST IT — never 'it disagreed'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
