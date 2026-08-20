#!/usr/bin/env python3
"""E0 — does this leg have an EXIT MECHANISM, or only a safeguard?

THE QUESTION (operator, 2026-08-19): *"A bracket isn't an exit strategy. It's a
safeguard. That shouldn't be what is deciding most exits unless that's how it's
set from the beginning."* Process doc:
``docs/design/exit-mechanism-construction-PROCESS.md`` § E0.

WHAT IT REPORTS, per ``(strategy, symbol)`` leg:

1. **Who decides the exit** — every ``exit_reason`` sorted into one of four
   classes, and the class shares. The load-bearing one is ``path_share``: the
   fraction of exits decided by something that could only be known AFTER entry.
2. **MFE capture rate** — ``net_r / mfe_r``, the standard practitioner measure
   of exit timing (§ 1.5.1). Never computed anywhere in this repo before.
3. **MAE-to-stop ratio** — the stop-calibration measure. **Usually UNMEASURED**:
   most harnesses do not emit ``mae_r``. That is reported as a coverage gap, not
   omitted, because a missing calibration number and a good one look identical
   in a report that simply leaves the row out.
4. **Hold duration**, from the emitted timestamps.

FOUR EXIT CLASSES, NEVER COLLAPSED
----------------------------------
``bracket``       a PRICE LEVEL fixed at entry (stop / take-profit / target).
                  Carries zero post-entry information.
``clock``         a TIMER fixed at entry (timeout / max-hold). Also carries no
                  market information, but it is a different instrument from a
                  price level and the literature treats it separately (§ 1.5.6),
                  so it is not folded into ``bracket``.
``path``          decided by something observed after entry (trailing stops and
                  every M20 lever). **This is the only class that constitutes an
                  exit mechanism.**
``unclassified``  a reason this module does not recognise. Counted and NAMED in
                  the output, never bucketed into a neighbour and never dropped.

⚠️ **THE VOCABULARY IS DISCOVERED, NOT ASSUMED.** Harnesses disagree on spelling
(``stop``/``sl``, ``tp``/``take_profit``/``target``), so a census that silently
ignored an unrecognised reason would under-count exactly the class it failed to
learn about — and would do it while printing a confident share. Any leg whose
``unclassified`` share exceeds ``MAX_UNCLASSIFIED_SHARE`` grades ``ungradeable``
rather than getting a verdict computed over a vocabulary we did not understand.

WHAT THIS IS NOT
----------------
Descriptive only. A leg with a bad capture rate is **not** thereby shown to have
an improvable exit — that is what E2 measures. Nothing here proposes a lever and
nothing here reads the live journal or any order path.

Usage
-----
    python3 scripts/research/exit_census.py trades1.jsonl [trades2.jsonl ...]
    python3 scripts/research/exit_census.py --self-test
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: A price level fixed at entry. Spellings vary by harness; all of them mean
#: "the order the venue was already holding decided this".
BRACKET_REASONS = frozenset({
    "stop", "sl", "sl_hit", "stop_loss", "stopped",
    "tp", "tp_hit", "take_profit", "target", "target_hit",
})

#: A timer fixed at entry.
CLOCK_REASONS = frozenset({"timeout", "time_exit", "max_hold", "max_hold_bars", "eod"})

#: Decided by post-entry observation. Trailing stops count: the level moves with
#: the path, so the exit price is not knowable at entry.
PATH_REASONS = frozenset({
    "trail_stop", "trailing_stop", "trail", "chandelier",
    "stale_stop", "giveback_stop", "rr_floor_exit", "rr_floor",
    "trail_decay", "exit_head", "thesis_decay",
    "banked", "bank", "reverse", "opposite", "signal_exit",
})

#: Above this share of unrecognised reasons a leg is not graded at all.
MAX_UNCLASSIFIED_SHARE = 0.05

#: Below this many closed trades a leg's shares are reported but not graded.
MIN_ROWS_TO_GRADE = 20

#: E0's falsifier: more than this fraction decided by something fixed at entry
#: means the leg has no exit mechanism.
NO_MECHANISM_FIXED_SHARE = 0.70

#: Tier-B practitioner line for "systematically early exits" (§ 1.5.1).
POOR_CAPTURE = 0.50


def classify_exit_reason(reason: Optional[str]) -> str:
    """Return the exit CLASS for one reason string.

    Unrecognised and missing reasons both return ``unclassified`` — they are
    different facts, but both mean "this module cannot say who decided", and the
    caller counts them separately by name.
    """
    if reason is None:
        return "unclassified"
    key = str(reason).strip().lower()
    if key in BRACKET_REASONS:
        return "bracket"
    if key in CLOCK_REASONS:
        return "clock"
    if key in PATH_REASONS:
        return "path"
    return "unclassified"


def _hold_hours(row: Dict[str, Any]) -> Optional[float]:
    """Hold duration in hours from the emitted timestamps, or None."""
    a, b = row.get("entry_time"), row.get("exit_time")
    if not a or not b:
        return None
    try:
        t0 = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return (t1 - t0).total_seconds() / 3600.0


def _capture(row: Dict[str, Any]) -> Optional[float]:
    """MFE capture rate for one trade, or None when UNDEFINED.

    A trade whose MFE was never positive has no capture ratio — the denominator
    does not exist. That is not a capture of 0.0, and folding it in as one would
    drag every leg's median toward zero with trades that had nothing to capture.
    """
    try:
        mfe = float(row["mfe_r"])
        net = float(row["net_r"])
    except (KeyError, TypeError, ValueError):
        return None
    if mfe <= 0:
        return None
    return net / mfe


def _mae_to_stop(rows: List[Dict[str, Any]]) -> Tuple[Optional[float], int]:
    """(mean |mae_r| against the 1R stop, rows that carried mae_r).

    `mae_r` is already expressed in R, and the stop is 1R by construction, so the
    ratio IS the mean absolute `mae_r`. Returns (None, 0) when no row carries the
    field — the honest "we did not look", distinct from a measured 0.0.
    """
    vals = []
    for r in rows:
        v = r.get("mae_r")
        if v is None:
            continue
        try:
            vals.append(abs(float(v)))
        except (TypeError, ValueError):
            continue
    if not vals:
        return None, 0
    return statistics.fmean(vals), len(vals)


def census_leg(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Census one leg. Every share ships beside the count it was taken over."""
    n = len(rows)
    reasons = Counter(str(r.get("exit_reason")).strip().lower()
                      if r.get("exit_reason") is not None else "(null)"
                      for r in rows)
    classes = Counter(classify_exit_reason(r.get("exit_reason")) for r in rows)
    unclassified_names = sorted(
        name for name in reasons
        if classify_exit_reason(None if name == "(null)" else name) == "unclassified")

    bracket, clock, path = classes["bracket"], classes["clock"], classes["path"]
    unclassified = classes["unclassified"]
    fixed_share = (bracket + clock) / n if n else None
    path_share = path / n if n else None
    unclassified_share = unclassified / n if n else None

    caps = [c for c in (_capture(r) for r in rows) if c is not None]
    mae_mean, mae_n = _mae_to_stop(rows)
    holds = [h for h in (_hold_hours(r) for r in rows) if h is not None]

    if n < MIN_ROWS_TO_GRADE:
        verdict, why = "ungradeable", f"n={n} below MIN_ROWS_TO_GRADE={MIN_ROWS_TO_GRADE}"
    elif unclassified_share is not None and unclassified_share > MAX_UNCLASSIFIED_SHARE:
        verdict, why = "ungradeable", (
            f"unclassified_share={unclassified_share:.3f} exceeds "
            f"{MAX_UNCLASSIFIED_SHARE}; unknown reasons {unclassified_names}")
    elif fixed_share is not None and fixed_share > NO_MECHANISM_FIXED_SHARE:
        verdict, why = "no_mechanism", (
            f"{fixed_share:.1%} of exits decided by a level or timer fixed at entry")
    elif path_share is not None and path_share >= 0.50:
        verdict, why = "mechanism", f"{path_share:.1%} of exits carry post-entry information"
    else:
        verdict, why = "partial", (
            f"path_share={path_share:.1%} — a mechanism exists but does not decide most exits")

    return {
        "n": n,
        "verdict": verdict,
        "verdict_reason": why,
        "by_class": {"bracket": bracket, "clock": clock, "path": path,
                     "unclassified": unclassified},
        "fixed_at_entry_share": round(fixed_share, 4) if fixed_share is not None else None,
        "path_share": round(path_share, 4) if path_share is not None else None,
        "unclassified_share": (round(unclassified_share, 4)
                               if unclassified_share is not None else None),
        "unclassified_reasons": unclassified_names,
        "exit_reasons": dict(reasons.most_common()),
        "capture": {
            "median": round(statistics.median(caps), 4) if caps else None,
            "mean": round(statistics.fmean(caps), 4) if caps else None,
            "share_below_poor": (round(sum(1 for c in caps if c < POOR_CAPTURE) / len(caps), 4)
                                 if caps else None),
            "gradeable_n": len(caps),
            "ungradeable_n": n - len(caps),
            "basis": "net_r / mfe_r over rows with mfe_r > 0",
        },
        "mae_to_stop": {
            "mean": round(mae_mean, 4) if mae_mean is not None else None,
            "measured_n": mae_n,
            "state": "measured" if mae_n else "unmeasured_no_mae_field",
        },
        "hold_hours": {
            "median": round(statistics.median(holds), 3) if holds else None,
            "max": round(max(holds), 3) if holds else None,
            "measured_n": len(holds),
        },
    }


def census(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Census every ``(strategy, symbol)`` leg present in ``rows``."""
    legs: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        legs[(str(r.get("strategy") or "?"), str(r.get("symbol") or "?"))].append(r)
    out = {f"{s}|{y}": census_leg(rs) for (s, y), rs in sorted(legs.items())}
    verdicts = Counter(v["verdict"] for v in out.values())
    return {
        "rows_total": len(rows),
        "legs": len(out),
        "by_verdict": dict(verdicts),
        "vocabulary_seen": dict(Counter(
            str(r.get("exit_reason")).strip().lower()
            if r.get("exit_reason") is not None else "(null)" for r in rows).most_common()),
        "per_leg": out,
    }


def _load(paths: List[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for p in paths:
        with open(Path(p), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def _self_test() -> int:
    def leg(name, reason, n, mfe=2.0, net=1.0, **kw):
        return [{"strategy": name, "symbol": "X", "exit_reason": reason,
                 "mfe_r": mfe, "net_r": net, **kw} for _ in range(n)]

    checks = []
    # Classification, including the spellings that differ across harnesses.
    for r, want in [("stop", "bracket"), ("sl", "bracket"), ("TP", "bracket"),
                    ("take_profit", "bracket"), ("target", "bracket"),
                    ("timeout", "clock"), ("max_hold", "clock"),
                    ("trail_stop", "path"), ("stale_stop", "path"),
                    ("rr_floor_exit", "path"), ("banked", "path"),
                    ("who_knows", "unclassified"), (None, "unclassified")]:
        checks.append((f"classify {r!r} -> {want}", classify_exit_reason(r) == want))

    # A bracket-dominated leg is graded no_mechanism, matching the E0 falsifier.
    brackety = leg("a", "stop", 60) + leg("a", "trail_stop", 40)
    c = census_leg(brackety)
    checks.append(("60% bracket + 40% path -> no_mechanism is FALSE",
                   c["verdict"] == "partial"))
    brackety2 = leg("a", "stop", 80) + leg("a", "trail_stop", 20)
    c2 = census_leg(brackety2)
    checks.append(("80% bracket -> no_mechanism", c2["verdict"] == "no_mechanism"))
    checks.append(("fixed_at_entry_share is 0.8", c2["fixed_at_entry_share"] == 0.8))

    # clock is NOT folded into bracket, but does count as fixed-at-entry.
    mixed = leg("a", "stop", 40) + leg("a", "timeout", 40) + leg("a", "trail_stop", 20)
    cm = census_leg(mixed)
    checks.append(("clock counted separately", cm["by_class"]["clock"] == 40))
    checks.append(("clock counts as fixed", cm["fixed_at_entry_share"] == 0.8))
    checks.append(("clock-heavy leg -> no_mechanism", cm["verdict"] == "no_mechanism"))

    # A path-dominated leg grades mechanism.
    pathy = leg("a", "trail_stop", 70) + leg("a", "stop", 30)
    checks.append(("70% path -> mechanism", census_leg(pathy)["verdict"] == "mechanism"))

    # THE REGRESSION CONTROL: an unknown vocabulary must REFUSE a verdict rather
    # than silently under-count the class it failed to recognise. Without the
    # unclassified gate this leg would read 100% bracket -> no_mechanism, a
    # confident verdict over a vocabulary the module does not understand.
    alien = leg("a", "stop", 50) + leg("a", "quantum_exit", 50)
    ca = census_leg(alien)
    checks.append(("alien vocabulary -> ungradeable", ca["verdict"] == "ungradeable"))
    checks.append(("alien reason is NAMED", ca["unclassified_reasons"] == ["quantum_exit"]))
    checks.append(("alien leg does not claim no_mechanism", ca["verdict"] != "no_mechanism"))
    # ...and a positive control: the same shape with a KNOWN reason does grade.
    known = leg("a", "stop", 50) + leg("a", "trail_stop", 50)
    checks.append(("positive control grades", census_leg(known)["verdict"] != "ungradeable"))

    # Small n is refused, not graded on 3 trades.
    checks.append(("n=3 -> ungradeable", census_leg(leg("a", "stop", 3))["verdict"] == "ungradeable"))

    # Capture: mfe_r <= 0 is UNDEFINED, never a capture of 0.
    caps = leg("a", "stop", 10, mfe=2.0, net=1.0) + leg("a", "stop", 10, mfe=0.0, net=-1.0)
    cc = census_leg(caps)
    checks.append(("capture median over gradeable only", cc["capture"]["median"] == 0.5))
    checks.append(("capture gradeable_n excludes mfe<=0", cc["capture"]["gradeable_n"] == 10))
    checks.append(("capture ungradeable_n counted", cc["capture"]["ungradeable_n"] == 10))
    checks.append(("poor-capture share measured",
                   cc["capture"]["share_below_poor"] == 0.0))
    poor = leg("a", "stop", 20, mfe=4.0, net=1.0)
    checks.append(("capture 0.25 flagged poor",
                   census_leg(poor)["capture"]["share_below_poor"] == 1.0))

    # MAE: absent field is a declared gap, not a 0.
    cno = census_leg(leg("a", "stop", 20))
    checks.append(("no mae_r -> unmeasured state",
                   cno["mae_to_stop"]["state"] == "unmeasured_no_mae_field"))
    checks.append(("no mae_r -> mean is None, NOT 0.0",
                   cno["mae_to_stop"]["mean"] is None))
    cyes = census_leg(leg("a", "stop", 20, mae_r=-0.6))
    checks.append(("mae_r present -> measured", cyes["mae_to_stop"]["state"] == "measured"))
    checks.append(("mae ratio is |mae_r|", cyes["mae_to_stop"]["mean"] == 0.6))
    checks.append(("mae measured_n reported", cyes["mae_to_stop"]["measured_n"] == 20))

    # Hold duration from timestamps; unparseable is skipped, not zeroed.
    held = leg("a", "stop", 20, entry_time="2026-01-01T00:00:00+00:00",
               exit_time="2026-01-01T06:00:00+00:00")
    checks.append(("hold hours median", census_leg(held)["hold_hours"]["median"] == 6.0))
    bad = leg("a", "stop", 20, entry_time="not-a-date", exit_time="also-not")
    checks.append(("unparseable time -> measured_n 0",
                   census_leg(bad)["hold_hours"]["measured_n"] == 0))
    checks.append(("unparseable time -> median None",
                   census_leg(bad)["hold_hours"]["median"] is None))

    # Legs are split by (strategy, symbol), never merged.
    two = ([{"strategy": "a", "symbol": "X", "exit_reason": "stop", "mfe_r": 1, "net_r": 1}] * 5
           + [{"strategy": "a", "symbol": "Y", "exit_reason": "stop", "mfe_r": 1, "net_r": 1}] * 5)
    checks.append(("same strategy, two symbols -> two legs", census(two)["legs"] == 2))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} passed")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpora", nargs="*", help="--emit-trades JSONL files")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true", help="emit the full JSON report")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()
    if not args.corpora:
        ap.error("give at least one corpus, or --self-test")
    report = census(_load(args.corpora))
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"rows={report['rows_total']} legs={report['legs']} "
          f"verdicts={report['by_verdict']}")
    print(f"vocabulary seen: {report['vocabulary_seen']}")
    for name, leg in report["per_leg"].items():
        cap = leg["capture"]
        print(f"\n{name}  n={leg['n']}  {leg['verdict'].upper()}")
        print(f"  {leg['verdict_reason']}")
        print(f"  classes {leg['by_class']}  path_share={leg['path_share']}")
        print(f"  capture median={cap['median']} (n={cap['gradeable_n']}, "
              f"undefined={cap['ungradeable_n']}, below {POOR_CAPTURE}: "
              f"{cap['share_below_poor']})")
        print(f"  mae_to_stop {leg['mae_to_stop']['state']} "
              f"mean={leg['mae_to_stop']['mean']} n={leg['mae_to_stop']['measured_n']}")
        print(f"  hold_hours median={leg['hold_hours']['median']} "
              f"max={leg['hold_hours']['max']} n={leg['hold_hours']['measured_n']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
