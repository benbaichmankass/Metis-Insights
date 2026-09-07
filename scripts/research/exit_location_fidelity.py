#!/usr/bin/env python3
# wiring: manual-only — the same call bracket_calibration_report.py makes and for
# the same reason. This is condition 1 of MI-151's gate, and the gate is answered
# once per decision about the legs it names, not on a schedule. A CI job asserting
# a particular fidelity verdict would fail every time a leg is retuned or a soak
# deepens. Its --selftest IS the wired half.
"""MI-155 · per-leg backtest<->live EXIT LOCATION fidelity, percent-of-entry.

WHAT THIS IS CONDITION 1 OF
---------------------------
`docs/research/ml2-predictive-bracket-2026-09-06.md` § 5 makes every per-leg
target conditional on this measurement:

    *"Backtest<->live exit-location fidelity is MEASURED, per leg, on the
    percent-of-entry basis ... Until then a backtest MFE quantile is a statement
    about the harness's book, not about what the fleet will do."*

THREE VERDICTS, NEVER COLLAPSED
-------------------------------
* ``fidelity_ok``      — both sides measured at n, and they agree.
* ``fidelity_failed``  — both sides measured at n, and they do not.
* ``insufficient_n``   — **WE COULD NOT LOOK.** This is a third state, not a
  soft ``ok`` and not a soft ``failed``. Folding it into ``ok`` licenses a
  target set off nothing; folding it into ``failed`` condemns a leg for having
  thin data. It carries a ``reason`` naming WHICH side was missing, because
  "no backtest corpus for this leg" and "live soak too shallow" are different
  facts with different remedies.

THE BASIS QUESTION, SETTLED (MI-148 vs MI-151)
----------------------------------------------
MI-151 § 5 says the target is "a per-leg MFE quantile **in R**". MI-148 says the
instrument is **percent-of-entry, never R**, for two independent measured
reasons. MI-151's own condition 1 then says fidelity is measured **on the
percent-of-entry basis**. These are reconcilable and this module implements the
reconciliation:

    MEASURE in percent-of-entry. EXPRESS the config knob as `tp_r`.

They are not competing bases, they are different roles. Percent-of-entry is the
only basis on which *prediction vs artefact* is decidable, because
``TP_VENUE_CAP_PCT`` is itself a percent of entry (9.9%) and the R denominator
is measurably contaminated (`trades.stop_loss` is the FINAL trailed stop).
`tp_r` is what ``config/strategies.yaml`` actually takes, so a proposal must
land there.

⚠️ **THE CONVERSION IS LOSSY IN ONE DIRECTION AND THAT IS NOT A DETAIL.**
``tp_venue_cap.py`` states that *no ``tp_r`` reproduces the clamp*:
``cap_r = TP_VENUE_CAP_PCT * entry / risk`` is a percent-of-entry against a
multiple-of-risk, so the two coincide only at one risk value. A leg whose
measured percent-of-entry target is expressed as a single ``tp_r`` therefore
gets a target that drifts with per-trade risk. This module reports the measured
percent and the ``tp_r`` that reproduces it AT THE LEG'S OWN MEDIAN RISK, and
labels that figure ``tp_r_at_median_risk`` so it can never be read as an
identity. Setting any such value is Tier-3 and is not done here.

WHY NOT m31_mfe_parity
----------------------
`scripts/research/m31_mfe_parity.py` already grades backtest<->live MFE parity
and its abstention structure is inherited here wholesale. It grades **in R**
(harness ``mfe_r`` vs live ``peak_r``) — the basis MI-148 rules out. This module
is that comparison moved onto the percent-of-entry basis; it is not a second
opinion on the same quantity. `_pct`-style quantiles come from
`src.runtime.bracket_calibration.quantile`, imported and not re-derived, so two
definitions of p90 cannot drift apart.

WHAT IT REFUSES TO DO
---------------------
1. **It never joins across TIMEFRAME.** The only backtest corpus that exists is
   `trend_donchian` at **15m**; every live `trend_donchian` leg runs at **1h or
   4h**. Excursion in percent-of-entry scales with horizon — measured *within
   the live book alone*, p90 goes 3.01% (1h) -> 7.07% (2h) -> 9.77% (4h). A
   15m-vs-4h ratio is a horizon difference, not a fidelity failure, and
   reporting it as one is the defect family M31 exists to close.
2. **It never grades a live row whose lifecycle it does not know.** Inherited
   from `m31_mfe_parity` refusal #1: `position_telemetry` is UPSERTed on every
   exit pass and carries no status of its own, so an open trade's ``peak_r`` is
   a partial, not that trade's MFE.
3. **It never treats live ``peak_r`` as MFE-final.** ``peak_r_is_lower_bound``
   is True on every row, so every live figure is a LOWER BOUND and a divergence
   is only ever called in the direction that bound permits.

Tier 1 — research tooling. Reads nothing that mutates, writes no config,
changes no exit. Every lever value stays Tier-3.

Usage
-----
    exit_location_fidelity.py --selftest
    exit_location_fidelity.py --telemetry-file t.json --strategies config/strategies.yaml
    exit_location_fidelity.py            # pulls live over Caddy with DIAG_READ_TOKEN
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import urllib.request
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])

from src.runtime.bracket_calibration import quantile  # noqa: E402
from src.runtime.tp_venue_cap import TP_VENUE_CAP_PCT  # noqa: E402

DEFAULT_API = "https://ict-bot.duckdns.org"
BACKTEST_REF = "docs/research/data/backtest-mfe-reference-2026-09-07.json"

#: THE ABSTAIN FLOOR — the minimum live n below which a leg gets no target from
#: the MFE-quantile route. It is a JUDGEMENT, made explicitly here rather than
#: left implied by whatever the data happens to support. It is built from two
#: independent arguments, and the STRICTER one is adopted.
#:
#: (1) AN ESTIMATOR FLOOR OF 20 — necessary, not sufficient.
#: `bracket_calibration.quantile` is linear-interpolated at ``pos = q*(n-1)``.
#: At q=0.90 the estimate is an interpolation between the top two order
#: statistics whenever ``floor(0.9*(n-1)) >= n-2``, i.e. for all ``n <= 11``.
#: Below that, "p90" is a synonym for "the largest one or two trades this leg
#: ever had", and a target set from it is a target set from an anecdote. n = 20
#: is the first round number at which the top decile holds ~3 observations and
#: at least 2 sit strictly above the estimate, so the figure reflects a tail
#: rather than an extremum.
#:
#: (2) THE REPO'S EXISTING PRECEDENT IS 30, AND IT IS ADOPTED.
#: `scripts/research/backtest_fidelity_calibrate.py` sets ``MIN_LIVE_N = 30``
#: for its own backtest<->live comparison (win-rate agreement + a two-sample KS
#: on realised R), below which it returns `insufficient-live`. Inventing a
#: LOOSER floor here would be indefensible: this module estimates a 0.90 TAIL
#: quantile, which needs MORE n than the distribution-centre comparison that
#: floor was set for, not less. So 20 is the absolute minimum any defensible
#: floor could take and 30 is the one in force.
#:
#: ⚠️ IT IS A FLOOR FOR LOOKING AT ALL, NOT A THRESHOLD FOR CONFIDENCE. The
#: binomial standard error on a 10% exceedance rate at n=30 is
#: sqrt(.1*.9/30) = 5.5pp — the tail is still estimated to within ±55% of
#: itself. A leg that clears 30 has earned a look, not a number.
#:
#: ⚠️ AND ON TODAY'S FLEET THE VERDICT DOES NOT TURN ON THIS CHOICE. Max
#: per-leg live n is 8, so every leg abstains at any floor in [10, 30]. The
#: floor is stated because it must be decided BEFORE the soak deepens — not
#: because it is currently doing the work.
ABSTAIN_FLOOR_N = 30

#: How far the two sides may differ before the verdict is `fidelity_failed`.
#: MI-151 reported the live/backtest disagreement as "~2.5x" and called it the
#: single most load-bearing number in the chain; 1.5x is deliberately tighter
#: than the discrepancy that motivated the measurement, so the test can fail on
#: something smaller than the thing it was built to explain.
FIDELITY_RATIO_TOLERANCE = 1.5


def _get(api: str, path: str, token: str) -> Any:
    req = urllib.request.Request(api.rstrip("/") + path)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=90) as fh:
        return json.loads(fh.read().decode("utf-8"))


def pct_of_entry(row: Dict[str, Any]) -> Optional[float]:
    """Live MFE as a fraction of entry price.

    ``peak_r / cap_r`` is the share of the venue ceiling the trade reached, and
    the ceiling is ``TP_VENUE_CAP_PCT`` OF ENTRY by construction — so the
    product is a percent-of-entry with no reliance on ``trades.stop_loss``,
    which is the contaminated field MI-148 rules the R basis out for.
    """
    try:
        peak, cap = row.get("peak_r"), row.get("cap_r")
        if peak is None or not cap:
            return None
        return float(peak) / float(cap) * TP_VENUE_CAP_PCT
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def live_rows_for_grading(rows: Sequence[Dict[str, Any]], *, require_final: bool
                          ) -> List[Dict[str, Any]]:
    """Refusals 2 and 3, applied.

    ``require_final`` is the `m31_mfe_parity` gate: only ``lifecycle == closed``
    rows carry a trade's FINAL excursion. It is a parameter rather than a
    constant because MI-148's published View 2 did not apply it, and
    reproducing that number is the positive control this module needs before it
    is entitled to quote a different one.
    """
    out = []
    for r in rows:
        if not r.get("peak_gradeable"):
            continue
        if pct_of_entry(r) is None:
            continue
        if require_final and r.get("lifecycle") != "closed":
            continue
        out.append(r)
    return out


def load_strategy_meta(path: str) -> Dict[str, Dict[str, Any]]:
    """leg -> {timeframe, enabled, execution}. Empty dict if unreadable."""
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return {}
    try:
        with open(path) as fh:
            cfg = yaml.safe_load(fh) or {}
    except OSError:
        return {}
    strat = cfg.get("strategies") or cfg
    meta: Dict[str, Dict[str, Any]] = {}
    for name, body in (strat or {}).items():
        if not isinstance(body, dict):
            continue
        meta[str(name)] = {
            "timeframe": body.get("timeframe") or body.get("interval"),
            "enabled": bool(body.get("enabled")),
            "execution": str(body.get("execution", "live")),
        }
    return meta


def enabled_live_legs(meta: Dict[str, Dict[str, Any]]) -> List[str]:
    return sorted(k for k, v in meta.items()
                  if v.get("enabled") and v.get("execution") == "live")


def load_backtest_reference(path: str) -> Dict[str, Dict[str, Any]]:
    """Keyed by (symbol, family, timeframe) — never by symbol alone.

    Keying by symbol alone is exactly the join that produces a horizon
    difference wearing a fidelity label, so the key carries the timeframe and
    the consumer has no way to drop it.
    """
    try:
        with open(path) as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return {}
    out = {}
    for leg in payload.get("legs") or []:
        key = (str(leg.get("symbol")), str(leg.get("family")), str(leg.get("timeframe")))
        out[key] = leg
    return out


def family_of(leg: str) -> str:
    if leg.startswith("trend_donchian"):
        return "trend_donchian"
    if "pullback" in leg:
        return "pullback"
    if leg.startswith("ict_scalp"):
        return "ict_scalp"
    return "other"


def grade_leg(leg: str, live_pcts: Sequence[float], *, timeframe: Optional[str],
              symbol: Optional[str], backtest: Dict[str, Dict[str, Any]],
              floor: int = ABSTAIN_FLOOR_N) -> Dict[str, Any]:
    """One leg's verdict. Never raises; never collapses the three states."""
    n_live = len(live_pcts)
    bt = backtest.get((str(symbol), family_of(leg), str(timeframe)))
    n_bt = int(bt["n"]) if bt else 0
    out: Dict[str, Any] = {
        "leg": leg, "timeframe": timeframe, "symbol": symbol,
        "n_live": n_live, "n_backtest": n_bt,
        "live_p90_pct": quantile(list(live_pcts), 0.90) if live_pcts else None,
        "backtest_p90_pct": (bt or {}).get("p90"),
        "live_is_lower_bound": True,
        "verdict": "insufficient_n", "reason": None, "ratio": None,
    }
    # BOTH denominators are stated on every row, including the abstaining ones —
    # a ratio without both is what hid MI-151's 3,194-vs-63 in the first place.
    if n_bt == 0 and n_live < floor:
        out["reason"] = "no_backtest_corpus_for_leg AND live_n_below_abstain_floor"
    elif n_bt == 0:
        out["reason"] = ("no_backtest_corpus_for_leg — no timeframe-matched harness "
                         "book exists for this leg")
    elif n_live < floor:
        out["reason"] = f"live_n_below_abstain_floor ({n_live} < {floor})"
    if out["reason"]:
        return out

    lp, bp = out["live_p90_pct"], out["backtest_p90_pct"]
    if not lp or not bp:
        out["reason"] = "a p90 was unreadable on one side"
        return out
    ratio = max(lp, bp) / min(lp, bp)
    out["ratio"] = ratio
    out["verdict"] = ("fidelity_ok" if ratio <= FIDELITY_RATIO_TOLERANCE
                      else "fidelity_failed")
    return out


def report(telemetry: Sequence[Dict[str, Any]], meta: Dict[str, Dict[str, Any]],
           backtest: Dict[str, Dict[str, Any]], *, require_final: bool) -> List[Dict[str, Any]]:
    graded = live_rows_for_grading(telemetry, require_final=require_final)
    legs = enabled_live_legs(meta)
    print(f"POPULATION (live): {len(telemetry)} position_telemetry rows returned; "
          f"{len(graded)} pass the grading gate "
          f"(peak_gradeable + readable peak_r/cap_r"
          f"{' + lifecycle==closed' if require_final else ''}).")
    lb = sum(1 for r in telemetry if r.get("peak_r_is_lower_bound"))
    print(f"⚠️ peak_r_is_lower_bound on {lb}/{len(telemetry)} rows — every live figure "
          f"below is a LOWER BOUND on the excursion.")
    print(f"POPULATION (legs): {len(legs)} enabled+live legs in config/strategies.yaml.")
    print(f"POPULATION (backtest): {len(backtest)} leg(s) with a committed "
          f"percent-of-entry MFE distribution.")
    print(f"ABSTAIN FLOOR: n_live >= {ABSTAIN_FLOOR_N}.\n")

    byleg = collections.defaultdict(list)
    sym = {}
    for r in graded:
        leg = str(r.get("strategy"))
        byleg[leg].append(pct_of_entry(r))
        sym.setdefault(leg, r.get("symbol"))

    verdicts = []
    for leg in legs:
        verdicts.append(grade_leg(
            leg, byleg.get(leg, []), timeframe=(meta.get(leg) or {}).get("timeframe"),
            symbol=sym.get(leg), backtest=backtest))

    print(f"{'leg':<28} {'tf':<5} {'n_live':>6} {'n_bt':>6} {'liveP90':>8} "
          f"{'btP90':>7} {'verdict':<16} reason")
    print("-" * 118)
    for v in sorted(verdicts, key=lambda d: (-d["n_live"], d["leg"])):
        lp = "   n/a" if v["live_p90_pct"] is None else f"{v['live_p90_pct']*100:6.2f}%"
        bp = "  n/a" if v["backtest_p90_pct"] is None else f"{v['backtest_p90_pct']*100:5.2f}%"
        print(f"{v['leg']:<28} {str(v['timeframe'] or '?'):<5} {v['n_live']:>6} "
              f"{v['n_backtest']:>6} {lp:>8} {bp:>7} {v['verdict']:<16} {v['reason'] or ''}")

    tally = collections.Counter(v["verdict"] for v in verdicts)
    print(f"\nVERDICT TALLY over the {len(verdicts)} enabled+live legs: {dict(tally)}")
    below = [v["leg"] for v in verdicts if v["n_live"] < ABSTAIN_FLOOR_N]
    print(f"BELOW THE ABSTAIN FLOOR (n_live < {ABSTAIN_FLOOR_N}): {len(below)} of "
          f"{len(verdicts)} legs.")
    if len(below) == len(verdicts):
        print("  -> EVERY enabled+live leg. No leg in the fleet may take a target "
              "from the MFE-quantile route today.")
    return verdicts


def horizon_ladder(telemetry: Sequence[Dict[str, Any]],
                   meta: Dict[str, Dict[str, Any]], *, crypto_only: bool) -> None:
    """The control that explains MI-151's ~2.5x WITHOUT invoking the harness.

    If excursion scales this steeply with timeframe inside a single book, then a
    15m-backtest-vs-4h-live ratio is explained before fidelity is reached for.
    """
    BYBIT = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "BNBUSDT"}
    rows = live_rows_for_grading(telemetry, require_final=False)
    if crypto_only:
        rows = [r for r in rows if str(r.get("symbol") or "") in BYBIT]
    bytf = collections.defaultdict(list)
    for r in rows:
        tf = (meta.get(str(r.get("strategy"))) or {}).get("timeframe")
        if tf:
            bytf[str(tf)].append(pct_of_entry(r))
    label = "Bybit-traded crypto only" if crypto_only else "all symbols"
    print(f"\n=== HORIZON LADDER, live book only ({label}) ===")
    print("POPULATION: peak_gradeable live telemetry rows joined to their leg's "
          "declared timeframe in config/strategies.yaml.")
    print(f"{'timeframe':<10} {'n':>4} {'p50%':>8} {'p75%':>8} {'p90%':>8}")
    print("-" * 44)
    order = {"5m": 0, "15m": 1, "1h": 2, "2h": 3, "4h": 4, "1d": 5}
    for tf, v in sorted(bytf.items(), key=lambda kv: order.get(kv[0], 99)):
        print(f"{tf:<10} {len(v):>4} {quantile(v,.5)*100:>8.2f} "
              f"{quantile(v,.75)*100:>8.2f} {quantile(v,.9)*100:>8.2f}")


def selftest() -> int:
    """Invariants that never change though the fleet does."""
    bt = {("BTCUSDT", "trend_donchian", "15m"):
          {"leg": "x", "symbol": "BTCUSDT", "family": "trend_donchian",
           "timeframe": "15m", "n": 3194, "p90": 0.0387}}

    # 1. "We could not look" never becomes a pass. A leg with a huge backtest n
    #    and a thin live n abstains — it does NOT inherit the backtest's
    #    confidence.
    v = grade_leg("trend_donchian_btc_15m", [0.01] * 5, timeframe="15m",
                  symbol="BTCUSDT", backtest=bt)
    assert v["verdict"] == "insufficient_n", v
    assert "abstain_floor" in v["reason"], v
    assert v["n_backtest"] == 3194 and v["n_live"] == 5, v  # BOTH n's, always

    # 2. ...and it never becomes a failure either.
    assert v["verdict"] != "fidelity_failed", v

    # 3. The timeframe is part of the join. The SAME symbol and family at a
    #    different timeframe finds no corpus — this is the refusal that keeps a
    #    horizon difference from being reported as a fidelity failure.
    v4h = grade_leg("trend_donchian_btc_4h", [0.09] * 40, timeframe="4h",
                    symbol="BTCUSDT", backtest=bt)
    assert v4h["verdict"] == "insufficient_n", v4h
    assert v4h["reason"].startswith("no_backtest_corpus_for_leg"), v4h

    # 4. At n over the floor on BOTH sides, agreement grades ok and the ~2.5x
    #    that motivated this module grades failed.
    ok = grade_leg("trend_donchian_x", [0.0387] * 40, timeframe="15m",
                   symbol="BTCUSDT", backtest=bt)
    assert ok["verdict"] == "fidelity_ok", ok
    bad = grade_leg("trend_donchian_x", [0.0970] * 40, timeframe="15m",
                    symbol="BTCUSDT", backtest=bt)
    assert bad["verdict"] == "fidelity_failed", bad
    assert bad["ratio"] > 2.0, bad

    # 5. An empty live population yields no p90 — never 0.0, which is a real
    #    quantile of a real distribution.
    empty = grade_leg("trend_donchian_x", [], timeframe="15m", symbol="BTCUSDT",
                      backtest=bt)
    assert empty["live_p90_pct"] is None, empty
    assert empty["verdict"] == "insufficient_n", empty

    # 6. The lifecycle gate actually excludes. An open row is not a final MFE.
    rows = [{"peak_gradeable": True, "peak_r": 1.0, "cap_r": 2.0, "lifecycle": "open"},
            {"peak_gradeable": True, "peak_r": 1.0, "cap_r": 2.0, "lifecycle": "closed"}]
    assert len(live_rows_for_grading(rows, require_final=True)) == 1
    assert len(live_rows_for_grading(rows, require_final=False)) == 2

    # 7. percent-of-entry never touches trades.stop_loss: peak_r/cap_r*CAP.
    assert abs(pct_of_entry({"peak_r": 1.0, "cap_r": 2.0}) - TP_VENUE_CAP_PCT / 2) < 1e-12
    assert pct_of_entry({"peak_r": 1.0, "cap_r": 0}) is None  # never ZeroDivision

    print("exit_location_fidelity --selftest: OK (7 invariants)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api", default=os.environ.get("DIAG_BASE_URL") or DEFAULT_API)
    ap.add_argument("--token", default=os.environ.get("DIAG_READ_TOKEN", ""))
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--telemetry-file")
    ap.add_argument("--strategies", default="config/strategies.yaml")
    ap.add_argument("--backtest-ref", default=BACKTEST_REF)
    ap.add_argument("--final-only", action="store_true",
                    help="restrict live rows to lifecycle==closed (final MFE)")
    ap.add_argument("--ladder", action="store_true", help="print the horizon ladder")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    if a.telemetry_file:
        payload = json.load(open(a.telemetry_file))
    else:
        payload = _get(a.api, f"/api/diag/position_telemetry?limit={a.limit}", a.token)
    rows = payload.get("rows") or [] if isinstance(payload, dict) else payload
    meta = load_strategy_meta(a.strategies)
    if not meta:
        print("REFUSING: could not read strategy metadata from "
              f"{a.strategies} — without it the timeframe join is unavailable, "
              "and a join without a timeframe is the defect this module exists "
              "to avoid.")
        return 2
    backtest = load_backtest_reference(a.backtest_ref)
    report(rows, meta, backtest, require_final=a.final_only)
    if a.ladder:
        horizon_ladder(rows, meta, crypto_only=True)
        horizon_ladder(rows, meta, crypto_only=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
