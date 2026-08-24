#!/usr/bin/env python3
"""Shard the bracket-geometry sweep ONE LEG PER CI JOB.

WHY THIS EXISTS. `e35_bracket_geometry_sweep.py` run as a single serial pass is
a multi-hour job, and on 2026-08-20 it was killed three times mid-run by the
ephemeral sandbox being recycled (measured: `uptime` 0 min at three consecutive
check-ins 45-55 min apart, with 30 GB disk free and 799 MB of 16 GB memory in
use, and no OOM lines in `dmesg` — so a container recycle, not resource
exhaustion). Nothing was lost, because per-leg reports persist and the resume
skips completed legs, but each recycle cost wall-clock.

The fix is not a bigger box. It is to stop running a multi-hour job anywhere it
can be recycled: emit a GitHub Actions matrix so each leg is its OWN job on a
free `ubuntu-latest` runner. A leg that dies costs one leg, and the results land
as artifacts that outlive any one machine. This is the same shape
`.github/workflows/m20-exit-lever-sweep.yml` already uses.

FAILURE MODE THIS MODULE IS BUILT AROUND: **a matrix that expands to zero jobs
is a green run that tested nothing.** GitHub reports an empty matrix as success,
so the surrounding workflow cannot tell "19 legs all passed" from "no leg ever
ran". This module therefore treats an empty include-list as an ERROR — it writes
nothing and exits non-zero — rather than printing `{"include": []}` and letting
CI go green over it. Same reasoning as the sweep's own `data_missing` handling:
a leg that could not run must never read as a leg that ran and found nothing.

Observe-only, Tier-1: reads config + resolves data paths, emits JSON, runs no
harness and touches nothing live.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "research"))

import e35_bracket_geometry_sweep as sweep  # noqa: E402
import m20_fleet_exit_sweep as fleet  # noqa: E402

# Leg timeframe -> the interval code `scripts/ops/fetch_backtest_candles.py`
# wants. Deliberately NOT a `.get(tf, <default>)` lookup: see `fetch_interval`.
TF_TO_FETCH_INTERVAL = {
    "1m": "1", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "1d": "D",
}


class UnknownTimeframe(ValueError):
    """A leg whose timeframe has no known fetch interval."""


def fetch_interval(tf: str) -> str:
    """The fetcher's interval code for a leg timeframe. RAISES on unknown.

    ⚠️ **An unknown timeframe must NOT fall back to a default**, which is why
    this raises instead of returning `.get(tf, "60")` or `None`.

    A wrong interval does not error anywhere downstream — the fetcher happily
    returns 1h bars for a leg that trades 4h, the sweep runs, and every verdict
    it produces is a measurement of a population nobody asked for. That is the
    exact hazard `m20-exit-lever-sweep.yml` already calls out for its own data
    source ("Sweeping a scalp leg against data/ would not error — it would
    quietly measure a different population"). A default here would reintroduce
    it one layer up.

    Raising makes the leg's absence LOUD: `build_matrix` records it as a skip
    with a named reason and the census reports it, so a leg dropped for an
    unmapped timeframe is distinguishable from one dropped for missing data.
    """
    try:
        return TF_TO_FETCH_INTERVAL[str(tf)]
    except KeyError:
        raise UnknownTimeframe(
            f"no fetch interval mapped for timeframe {tf!r}; refusing to guess "
            f"— known: {sorted(TF_TO_FETCH_INTERVAL)}") from None


def build_matrix(runnable: list[dict]) -> tuple[list[dict], list[dict]]:
    """(include, refused) — one matrix entry per runnable leg.

    Split rather than filtered, so a leg refused HERE (an unmapped timeframe)
    stays distinguishable from one the sweep itself never offered (missing data,
    out-of-scope family). Two different problems with two different fixes.
    """
    include, refused = [], []
    for p in runnable:
        try:
            iv = fetch_interval(p["tf"])
        except UnknownTimeframe as exc:
            refused.append({"leg": p["leg"], "reason": f"unmapped_timeframe:{p['tf']}",
                            "detail": str(exc)})
            continue
        include.append({
            "leg": p["leg"], "symbol": p["symbol"], "tf": p["tf"],
            "family": p["family"], "fetch_interval": iv,
        })
    return include, refused


def census(include: list[dict], skipped: list[dict],
           refused: list[dict], data_pending: int = 0) -> str:
    """One line naming what ran and what did not, grouped by reason.

    A bare count of jobs cannot say WHY the other legs are absent, and "19 legs
    skipped" with no breakdown is the kind of number that gets read as fine.

    ``data_pending`` is reported SEPARATELY rather than folded into the job
    count: "43 jobs, data on disk" and "43 jobs whose data does not exist yet
    and whose first step is to fetch it" are different claims, and a reader who
    cannot tell them apart cannot tell a planned run from a runnable one.
    """
    by = Counter(str(s.get("reason", "?")).split(":")[0] for s in skipped)
    by.update(Counter(str(r["reason"]).split(":")[0] for r in refused))
    detail = ", ".join(f"{k}={v}" for k, v in sorted(by.items())) or "none"
    pend = f"; {data_pending} awaiting fetch" if data_pending else ""
    return (f"shard-plan: {len(include)} job(s){pend}; "
            f"{len(skipped) + len(refused)} not scheduled ({detail})")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", default=str(REPO / "data"))
    ap.add_argument("--only", default=None, help="comma-separated leg names")
    ap.add_argument("--out", default=None,
                    help="write the matrix here (default: stdout)")
    # Default comes from the sweep's own source of truth, never a literal — a
    # second copy of the live TP clamp is free to drift from the first.
    ap.add_argument("--tp-cap-pct", type=float, default=fleet.LIVE_TP_CAP_PCT)
    ap.add_argument(
        "--ignore-missing-data", action="store_true",
        help="Plan from CONFIG alone, dropping ONLY the data-presence gate. "
             "Default OFF so local behaviour is unchanged. Set it in CI, where "
             "the per-leg job fetches its own candles: leg CSVs are gitignored "
             "(.gitignore `data/*.csv`), so on a fresh checkout every leg "
             "resolves data=None and the matrix expands to zero jobs. The flag "
             "is implemented in sweep.plan_legs, NOT re-implemented here, so "
             "the plan and the sweep cannot disagree about what is in scope.")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv[1:])
    if a.selftest:
        return _selftest()

    only = [s.strip() for s in a.only.split(",")] if a.only else None
    runnable, skipped = sweep.plan_legs(
        Path(a.data_dir), only, a.tp_cap_pct,
        ignore_missing_data=a.ignore_missing_data)
    include, refused = build_matrix(runnable)
    pending = sum(1 for r in runnable if r.get("data_pending"))
    print(census(include, skipped, refused, pending), file=sys.stderr)
    for r in refused:
        print(f"  REFUSED {r['leg']}: {r['reason']}", file=sys.stderr)

    if not include:
        # WRITE NOTHING. A partial write here would leave the workflow reading a
        # stale or empty file and expanding to zero jobs — green, and vacuous.
        print("::error::shard-plan produced ZERO jobs. An empty matrix is a "
              "green run that tested nothing, so this is a failure, not an "
              "empty success. Check --data-dir: the sweep reports "
              f"{len(skipped)} skipped leg(s).", file=sys.stderr)
        return 1

    payload = json.dumps({"include": include}, separators=(",", ":"))
    if a.out:
        Path(a.out).write_text(payload + "\n")
    else:
        print(payload)
    return 0


def _selftest() -> int:
    ok = fail = 0

    def chk(name, got, want):
        nonlocal ok, fail
        if got == want:
            ok += 1
        else:
            fail += 1
            print(f"FAIL {name}: got {got!r} want {want!r}")

    def raises(name, fn):
        nonlocal ok, fail
        try:
            fn()
        except UnknownTimeframe:
            ok += 1
            return
        # allow-silent: this is the OPPOSITE of a silent-empty — the handler's
        # whole job is to turn an unexpected exception TYPE into a loud test
        # FAILURE (it increments `fail` and names the type it got). Narrowing it
        # would make the test weaker, not safer: if `fetch_interval` ever raised
        # TypeError instead of UnknownTimeframe the self-test would then ERROR
        # OUT rather than report a fail, and the assertion would stop being an
        # assertion. Nothing is swallowed and nothing returns empty.
        except Exception as exc:  # noqa: BLE001  # allow-silent: inverts the pattern — this handler EXISTS to turn an unexpected exception TYPE into a loud test FAILURE (increments `fail`, names the type). Narrowing it would make the self-test ERROR OUT instead of reporting a fail, i.e. stop being an assertion. Nothing swallowed, nothing returns empty.
            fail += 1
            print(f"FAIL {name}: raised {type(exc).__name__}, want UnknownTimeframe")
            return
        fail += 1
        print(f"FAIL {name}: did not raise")

    chk("1h", fetch_interval("1h"), "60")
    chk("2h", fetch_interval("2h"), "120")
    chk("4h", fetch_interval("4h"), "240")
    chk("1d", fetch_interval("1d"), "D")
    chk("15m", fetch_interval("15m"), "15")
    chk("5m", fetch_interval("5m"), "5")
    # THE LOAD-BEARING ONE: an unknown timeframe must not silently become a
    # default. A default would fetch the wrong bars and measure a different
    # population without erroring anywhere.
    raises("unknown tf raises", lambda: fetch_interval("3h"))
    raises("empty tf raises", lambda: fetch_interval(""))
    raises("None tf raises", lambda: fetch_interval(None))

    legs = [{"leg": "a", "symbol": "BTCUSDT", "tf": "1h", "family": "donchian"},
            {"leg": "b", "symbol": "ETHUSDT", "tf": "3h", "family": "donchian"}]
    inc, ref = build_matrix(legs)
    chk("matrix keeps mapped leg", [x["leg"] for x in inc], ["a"])
    chk("matrix refuses unmapped leg", [x["leg"] for x in ref], ["b"])
    chk("refusal names the reason", ref[0]["reason"], "unmapped_timeframe:3h")
    chk("entry carries interval", inc[0]["fetch_interval"], "60")
    chk("entry key set", sorted(inc[0]),
        ["family", "fetch_interval", "leg", "symbol", "tf"])
    chk("empty in -> empty out", build_matrix([]), ([], []))

    # The census must name the reasons, not just count.
    line = census(inc, [{"reason": "data_missing:SPY"}], ref)
    chk("census counts jobs", "1 job(s)" in line, True)
    chk("census counts unscheduled", "2 not scheduled" in line, True)
    chk("census names data_missing", "data_missing=1" in line, True)
    chk("census names unmapped", "unmapped_timeframe=1" in line, True)
    chk("census with nothing missing", "none" in census(inc, [], []), True)

    # `data_pending` is reported SEPARATELY, never folded into the job count:
    # "43 jobs, data on disk" and "43 jobs whose data does not exist yet" are
    # different claims about whether this plan is runnable right now.
    pend_line = census(inc, [], [], 1)
    chk("census names awaiting fetch", "1 awaiting fetch" in pend_line, True)
    chk("census still counts jobs with pending", "1 job(s)" in pend_line, True)
    chk("census omits pending when zero",
        "awaiting fetch" in census(inc, [], [], 0), False)

    print(f"selftest: {ok} pass, {fail} fail")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
