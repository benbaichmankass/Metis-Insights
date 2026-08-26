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


#: Intervals the Dukascopy lane serves. Mirrors
#: `scripts/ops/fetch_backtest_candles.py::_BYBIT_TO_DK_TIMEFRAME` and is
#: ASSERTED against it by a test rather than trusted — a planner that schedules
#: a leg the fetcher then refuses is exactly the two-copies drift this file
#: already avoids for leg scope.
_DK_SERVABLE_INTERVALS = {"1", "5", "15", "60", "240", "D", "1D"}


#: Intervals the yfinance lane serves. Mirrors
#: `scripts/ops/fetch_backtest_candles.py::_BYBIT_TO_YF_TIMEFRAME` and is
#: ASSERTED against it by a test, for the same reason the Dukascopy set above
#: is: a planner that schedules a leg the fetcher then refuses is the
#: two-copies drift this file already avoids for leg scope.
_YF_SERVABLE_INTERVALS = {"1", "5", "15", "60", "D", "1D"}


class NoFeedSource(RuntimeError):
    """No feed can serve this leg. A REFUSAL, carrying why."""


def data_basename(symbol: str, tf: str) -> str:
    """The file stem the SWEEP will actually look for. One definition, not two.

    ⚠️ **THIS EXISTS BECAUSE THERE WERE TWO, AND THEY DISAGREED**
    (`BL-20260826-E35-RUNNER-WRITES-A-FILENAME-THE-SWEEP-NEVER-READS`).
    `e35-bracket-sweep.yml` wrote `data/{SYMBOL}_{tf}.csv`, while
    `m20_fleet_exit_sweep.resolve_data` applies `PROXY_DATA`
    (`MES->ES_F`, `MGC`/`XAUUSD->GC_F`, `MHG->HG_F`) **unconditionally and with
    no native fallback** — so it looked for `ES_F_1d.csv` and never for the
    `MES_1d.csv` the runner had just fetched.

    The failure is silent in the worst way. Measured 2026-08-26 by invoking the
    sweep exactly as the workflow does, with `MES_1d.csv` present::

        plan: 0 legs runnable, 1 skipped
          SKIP mes_trend_long_1d: data_missing:MES
        EXIT CODE = 0     report.json -> legs= 0

    The leg pays the fetch, the job goes **GREEN**, and the artifact holds a
    report with an empty ``legs`` list. Four of the 25 cells recorded
    `blocked:no_free_lane_candle_feed` behaved this way.

    Deriving the stem from `fleet.PROXY_DATA` — the map `resolve_data` itself
    reads — is what makes the two unable to drift again. A literal
    ``f"{symbol}_{tf}"`` here would just recreate the second copy.

    ⚠️ **THE LOOKUP IS CASE-SENSITIVE ON PURPOSE, MIRRORING `resolve_data`
    EXACTLY** (`alt = PROXY_DATA.get(symbol)`, no `.upper()`). A first draft of
    this function normalised the key, which looks strictly more helpful and is
    the divergence again in miniature: for a lowercase ``mes`` the runner would
    write ``ES_F_1d.csv`` while `resolve_data` — not normalising — would look
    for ``mes_1d`` and find nothing, i.e. **the very green-job-measures-nothing
    failure this function exists to prevent**. Verified divergent before the
    change and identical after.

    Being *more permissive than the thing you mirror* is not a safe direction
    here; it is how the two copies come apart. Inert today (all 24 configured
    symbols are uppercase) — which is exactly why it had to be fixed by reading
    rather than waiting for a failure.
    """
    return f"{fleet.PROXY_DATA.get(symbol, symbol)}_{tf}"


def resolve_feed_source(symbol: str, interval: str) -> str:
    """Which feed serves this leg. RAISES rather than guessing.

    WHY THIS EXISTS (`BL-20260824-E35-SWEEP-FEED-IS-BINANCE-ONLY-FOR-A-MOSTLY-NON-CRYPTO-MATRIX`).
    The workflow hardcoded `binance_vision` for the WHOLE matrix. Measured on
    the first-ever run (32783849276): GLD and GDX are ETFs, Binance does not
    list them, and each spent **10 minutes** in a retry loop before failing —
    24 of 43 legs, ~4 runner-hours per dispatch on work that could not succeed.

    ⚠️ IT REFUSES INSTEAD OF FALLING BACK. A leg quietly re-routed to a feed
    serving a DIFFERENT instrument is a wrong backtest that looks fine — the
    hazard `fetch_backtest_candles` already states for why `yfinance` is not in
    its `auto` chain. Three symbols have no honest source (`QLD`/`TQQQ`: a daily
    leverage reset means the path is not N x the underlying, so a QQQ series is
    not a substitute; `MHG`: the only catalogue hit was a Norwegian salmon
    farmer) and are refused BY NAME at plan time rather than scheduled and left
    to burn a runner.

    Depth is MEASURED, not assumed: run 32788423940 probed all 11 mapped
    instruments and every one carries bars past the sweep's own 1830 d request.
    """
    sym = str(symbol).upper()
    if sym.endswith("USDT"):
        return "binance_vision"

    # ⚠️ A `PROXY_DATA` SYMBOL GOES TO YFINANCE, AND THAT IS A PROVENANCE
    # DECISION RATHER THAN A PREFERENCE (operator-approved 2026-08-26).
    #
    # `resolve_data` will only ever look for these under their PROXY stem
    # (`ES_F`/`GC_F`/`HG_F` — see `data_basename`), so whatever feed serves the
    # leg gets WRITTEN under that name. Those stems mean one specific thing in
    # this repo: the yfinance full-size contract. `yf_symbols` maps
    # `MES->ES=F`, `MGC`/`XAUUSD->GC=F`, `MHG->HG=F`, which is exactly that
    # series — root `CLAUDE.md` records `datasets-out/market_raw/MGC/1d` as
    # "byte-for-byte the proxy: 2,511 of 2,512 overlapping closes identical to
    # `GC_F_1d.csv`". So yfinance-under-the-proxy-stem is self-consistent.
    #
    # Dukascopy is NOT, for these four, even though it maps them and carries
    # more depth: it would serve `MES` from an S&P **index CFD** and `MGC` from
    # **spot** XAU_USD, then write them into a file whose name asserts the
    # futures contract. That is a file "whose NAME asserts a provenance its
    # CONTENT does not have" — the exact trap `resolve_data`'s own docstring
    # warns about, and the reason this function refuses rather than re-routes.
    #
    # COST, STATED: yfinance caps intraday history (~730 d at 1h), so the two
    # 1h legs get a PARTIAL window against the sweep's 1830 d request. The
    # fetcher clamps and says so on stderr, so a short span is never silently
    # read as a full one; the 1d legs are uncapped and unaffected.
    #
    # ⚠️ `MHG` rides this route too, as a CONSEQUENCE of applying the rule
    # uniformly rather than as a separate decision — it is in `PROXY_DATA`, and
    # `HG=F` is the honest series for `HG_F`. It was previously refused because
    # Dukascopy's only catalogue hit was a Norwegian salmon farmer; that
    # refusal was about Dukascopy, never about the leg being unservable.
    # `QLD`/`TQQQ` are NOT in `PROXY_DATA`, so this rule does not touch them and
    # they stay refused — correctly: a daily leverage reset means the path is
    # not N x the underlying, so no proxy is honest.
    if sym in fleet.PROXY_DATA:
        if str(interval) not in _YF_SERVABLE_INTERVALS:
            raise NoFeedSource(
                f"yfinance_interval_unsupported:{sym} — the lane does not "
                f"serve interval {interval!r}")
        return "yfinance"

    sys.path.insert(0, str(REPO / "scripts" / "ops"))
    import dukascopy_instruments as _dk  # noqa: E402  (path shimmed above)

    res = _dk.resolve(sym)
    if res.state != _dk.STATE_MAPPED:
        raise NoFeedSource(f"{res.state}:{sym} — {res.reason}")
    if str(interval) not in _DK_SERVABLE_INTERVALS:
        raise NoFeedSource(
            f"dukascopy_interval_unsupported:{sym} — the venue does not serve "
            f"interval {interval!r}")
    return "dukascopy"


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
        try:
            src = resolve_feed_source(p["symbol"], iv)
        except NoFeedSource as exc:
            # Refused HERE, before a runner is spent. Counted separately from an
            # unmapped timeframe: "no feed carries this instrument" and "we do
            # not know this bar size" are different problems with different
            # fixes, and folding them would hide both.
            refused.append({"leg": p["leg"], "reason": f"no_feed_source:{p['symbol']}",
                            "detail": str(exc)})
            continue
        include.append({
            "leg": p["leg"], "symbol": p["symbol"], "tf": p["tf"],
            "family": p["family"], "fetch_interval": iv, "feed_source": src,
            # The runner writes `data/{data_basename}.csv`. Carried in the
            # matrix rather than rebuilt in YAML so the workflow cannot hold a
            # second opinion about where a leg's candles live — see
            # `data_basename`, which is where the first two disagreed.
            "data_basename": data_basename(p["symbol"], p["tf"]),
        })
    return include, refused


def census(include: list[dict], skipped: list[dict],
           refused: list[dict], data_pending: int = 0) -> str:
    """One line naming what ran and what did not, grouped by reason.

    A bare count of jobs cannot say WHY the other legs are absent, and "19 legs
    skipped" with no breakdown is the kind of number that gets read as fine.

    ``data_pending`` counts SCHEDULED legs only — see the caller. It is reported
    SEPARATELY rather than folded into the job count: "43 jobs, data on disk" and "43 jobs whose data does not exist yet
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
    # Counted over what is SCHEDULED, not over everything runnable. A leg
    # refused for having no feed source is never fetched, so counting it as
    # "awaiting fetch" would print more pending fetches than there are jobs —
    # which is what this line did the moment the feed-source refusal was added
    # ("40 job(s); 43 awaiting fetch"). The population a number describes has to
    # be the population it is computed over.
    _scheduled = {r["leg"] for r in include}
    pending = sum(1 for r in runnable
                  if r.get("data_pending") and r["leg"] in _scheduled)
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

    def raises(name, fn, want=UnknownTimeframe):
        """`want` defaults to UnknownTimeframe so every pre-existing call site
        is unchanged. It is a PARAMETER because this file now has two distinct
        refusals — an unmapped timeframe and no-feed-source — and a helper that
        accepted either would stop distinguishing them, which is the whole
        reason they are separate exception types."""
        nonlocal ok, fail
        try:
            fn()
        except want:
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
            print(f"FAIL {name}: raised {type(exc).__name__}, want {want.__name__}")
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
    # UPDATED 2026-08-24 with `feed_source`. This pin is what forced the change
    # to be deliberate — the workflow consumes these keys by name, so a silent
    # schema drift here is a matrix the sweep cannot read.
    chk("entry key set", sorted(inc[0]),
        ["data_basename", "family", "feed_source", "fetch_interval", "leg",
         "symbol", "tf"])
    chk("entry carries the stem the SWEEP reads", inc[0]["data_basename"],
        "BTCUSDT_1h")
    chk("empty in -> empty out", build_matrix([]), ([], []))

    # --- per-leg feed source (BL-20260824-E35-SWEEP-FEED-IS-BINANCE-ONLY-FOR-A-MOSTLY-NON-CRYPTO-MATRIX)
    chk("crypto routes to binance_vision", resolve_feed_source("BTCUSDT", "60"),
        "binance_vision")
    chk("a mapped ETF routes to dukascopy", resolve_feed_source("SPY", "D"),
        "dukascopy")
    # ⚠️ CHANGED 2026-08-26: MES no longer routes to dukascopy. The previous
    # line asserted `"dukascopy"` and was CORRECT about the routing while the
    # leg was nonetheless unservable — dukascopy's S&P index CFD would be
    # written under `ES_F_1d.csv`, a name asserting the futures contract. See
    # `resolve_feed_source` for why a PROXY_DATA symbol goes to yfinance.
    chk("a PROXY_DATA symbol routes to yfinance, not dukascopy",
        resolve_feed_source("MES", "D"), "yfinance")
    chk("MGC likewise", resolve_feed_source("MGC", "60"), "yfinance")
    chk("XAUUSD likewise (spot gold still writes the GC_F stem)",
        resolve_feed_source("XAUUSD", "60"), "yfinance")
    chk("MHG rides the same rule (HG=F is the honest HG_F series)",
        resolve_feed_source("MHG", "D"), "yfinance")
    # A non-PROXY_DATA symbol is untouched by that rule.
    chk("a plain mapped ETF still routes to dukascopy",
        resolve_feed_source("GLD", "D"), "dukascopy")
    # An interval the yfinance lane cannot serve is REFUSED, not coerced —
    # the same discipline the dukascopy branch already applies. 2h/4h have no
    # yfinance mapping, so a PROXY_DATA leg at those bars must not be scheduled.
    raises("yfinance 4h is refused, not coerced",
           lambda: resolve_feed_source("MGC", "240"), NoFeedSource)
    raises("yfinance 2h is refused, not coerced",
           lambda: resolve_feed_source("MES", "120"), NoFeedSource)

    # --- data_basename: ONE definition of where a leg's candles live
    # (BL-20260826-E35-RUNNER-WRITES-A-FILENAME-THE-SWEEP-NEVER-READS)
    chk("a plain symbol keeps its own stem", data_basename("SPY", "1d"),
        "SPY_1d")
    chk("a PROXY_DATA symbol takes the proxy stem",
        data_basename("MES", "1d"), "ES_F_1d")
    chk("MGC -> GC_F", data_basename("MGC", "1h"), "GC_F_1h")
    chk("XAUUSD -> GC_F too", data_basename("XAUUSD", "1h"), "GC_F_1h")
    # ⚠️ A lowercase symbol must NOT resolve the proxy — `resolve_data` does not
    # normalise either, and being more permissive than the map we mirror is the
    # divergence this function exists to prevent. See the docstring.
    chk("lower-case symbol does NOT resolve the proxy (mirrors resolve_data)",
        data_basename("mes", "1d"), "mes_1d")
    # The stem is DERIVED from the map the sweep reads, never a literal — this
    # is the assertion that keeps the two from drifting apart again.
    chk("derived from fleet.PROXY_DATA, not a copy",
        {s_: data_basename(s_, "1d").rsplit("_", 1)[0]
         for s_ in sorted(fleet.PROXY_DATA)},
        {s_: fleet.PROXY_DATA[s_] for s_ in sorted(fleet.PROXY_DATA)})
    # The three refusals stay APART from each other and from a successful route.
    raises("a REFUSED symbol has no feed (QLD, daily leverage reset)",
           lambda: resolve_feed_source("QLD", "D"), NoFeedSource)
    raises("an UNADJUDICATED symbol has no feed (NVDA)",
           lambda: resolve_feed_source("NVDA", "D"), NoFeedSource)
    raises("an interval dukascopy cannot serve is refused, not coerced (2h)",
           lambda: resolve_feed_source("SPY", "120"), NoFeedSource)
    # THE LOAD-BEARING ONE: the planner's servable-interval set must agree with
    # the fetcher's own map, or the planner schedules legs the fetcher refuses.
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "_fbc", str(REPO / "scripts" / "ops" / "fetch_backtest_candles.py"))
    _fbc = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_fbc)
    chk("planner's dukascopy intervals == the fetcher's own map",
        sorted(_DK_SERVABLE_INTERVALS), sorted(_fbc._BYBIT_TO_DK_TIMEFRAME))
    # A crypto symbol must NEVER reach the CFD venue: that would substitute a
    # different instrument for the one the strategy actually trades.
    chk("no crypto symbol can route to dukascopy",
        [resolve_feed_source(s_, "60") for s_ in ("BTCUSDT", "ETHUSDT", "SOLUSDT")],
        ["binance_vision"] * 3)

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
