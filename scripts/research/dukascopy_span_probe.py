#!/usr/bin/env python3
# wiring: manual-only - dispatched by .github/workflows/dukascopy-span-probe.yml.
# Giving it a schedule would be scheduling a question nobody asked; the span is
# stable on the timescale that matters and re-probing nightly buys nothing.
"""How FAR BACK does Dukascopy actually serve each blocked symbol's instrument?

WHY THIS EXISTS
---------------
``dukascopy-coverage-probe.yml`` answered EXISTENCE — which of the 18 symbols
behind the blocked backtest cells map to a Dukascopy instrument at all — and
``docs/research/dukascopy-coverage-adjudication-2026-08-24.md`` adjudicated the
mapping by hand. That work closed with an explicit caveat, recorded in
``ROADMAP.md``:

    "Existence is not span, and span is the actual question — Dukascopy depth
     remains unmeasured."

This probe measures the span. It matters because the consumers ask for DEPTH,
not for a ticker to exist:

* ``e35-bracket-sweep.yml`` fetches ``days: 1830`` (~5 years) per leg.
* yfinance is MEASURED to serve at most 730 d of 1h history and REFUSES a longer
  request outright (proof run 32734360738), which is what made Dukascopy the
  candidate in the first place.

An instrument that exists but only carries 18 months of 1h bars does not unblock
a 1830-day sweep. "It's in the catalogue" and "it can serve the span we need"
are different claims, and only the second one is useful.

FOUR STATES, NEVER COLLAPSED
----------------------------
Per (instrument, anchor) the probe records exactly one of:

``bars``                the call returned rows — the venue HAS data there.
``empty``               the call succeeded and returned zero rows — we looked,
                        and the venue has nothing there. This is a real answer.
``error``               the call RAISED — **we did not look.** Never folded into
                        ``empty``: "the venue has no 2015 data" and "our request
                        blew up" are opposite statements, and a probe that
                        reports the second as the first manufactures a span
                        limit that does not exist.
``unknown_instrument``  the name is not in ``dukascopy_python.instruments`` —
                        a MAPPING bug in this file, not a fact about depth.

``earliest_bars_anchor`` is therefore reported beside ``anchors_errored``. An
earliest-bar claim over a run where anchors errored is a LOWER BOUND on depth,
not a measurement of it, and the summary says so rather than leaving the reader
to notice.

WHAT THIS DOES NOT DO
---------------------
It does not decide whether an instrument may stand in for one of our symbols.
That is the adjudication doc's job and it is a JUDGEMENT — ``MES`` is a CME
future and ``INSTRUMENT_IDX_AMERICA_E_SANDP_500`` is an index CFD with different
venue, hours, financing and settlement. Every row here carries the ``relation``
the adjudication assigned, so a proxy's span is never read as the real
instrument's span. The two leveraged ETFs (``QLD``, ``TQQQ``) are absent
deliberately: the adjudication REFUSED a proxy for them, because a daily
leverage reset means the path is not N x the underlying, and probing QQQ's depth
would invite exactly the substitution that refusal exists to prevent.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

REPO_ROOT = __file__.rsplit("/scripts/", 1)[0]
sys.path.insert(0, f"{REPO_ROOT}/scripts/ops")

import dukascopy_instruments as _dk  # noqa: E402  (path shimmed above)

#: The adjudicated mapping comes from ONE owner —
#: `scripts/ops/dukascopy_instruments.py`, which transcribes
#: docs/research/dukascopy-coverage-adjudication-2026-08-24.md. It is imported,
#: never restated: a second copy is how this probe would measure the depth of a
#: different instrument than the fetcher actually pulls.
#:
#: Keyed by INSTRUMENT, not by our symbol, because several symbols share one
#: (SPLG->SPY, IAUM->GLD, SCHA->IWM, MGC->XAU_USD); a per-symbol loop would
#: double the requests while measuring the same thing.
INSTRUMENTS: Dict[str, Dict[str, object]] = _dk.instruments_with_symbols()

#: Symbols the adjudication REFUSED a proxy for — carried so the output can say
#: why they are absent rather than leaving a reader to wonder.
REFUSED = dict(_dk.REFUSED)

#: Look-back anchors in days. 1830 is the span `e35-bracket-sweep.yml` requests,
#: so it is the one that decides whether this feed unblocks that sweep; the
#: others bracket it so a failure at 1830 is placed rather than just reported.
DEFAULT_ANCHORS = (365, 730, 1095, 1830, 2555, 3650)

STATE_BARS = "bars"
STATE_EMPTY = "empty"
STATE_ERROR = "error"
STATE_UNKNOWN = "unknown_instrument"


def probe_one(instrument: str, anchor_days: int, interval: str,
              window_days: int, *, now: datetime,
              fetcher: Optional[Callable] = None) -> Dict[str, object]:
    """Probe ONE (instrument, anchor). Returns a row; never raises.

    The three failure directions are kept apart deliberately -- see the module
    docstring. In particular a raised call is `error`, never `empty`.
    """
    start = now - timedelta(days=anchor_days)
    end = start + timedelta(days=window_days)
    row: Dict[str, object] = {
        "instrument": instrument,
        "anchor_days": anchor_days,
        "window_start": start.strftime("%Y-%m-%d"),
        "window_end": end.strftime("%Y-%m-%d"),
        "interval": interval,
        "state": None,
        "rows": None,
        "first_bar": None,
        "error": None,
    }
    if fetcher is None:
        from fetch_dukascopy_ohlcv import fetch as fetcher  # type: ignore

    try:
        df = fetcher(instrument, start, end, interval)
    except SystemExit as exc:
        # fetch_dukascopy_ohlcv.fetch raises SystemExit for a name that is not
        # in the instruments module. That is a bug in INSTRUMENTS above, not a
        # statement about how deep the venue goes -- so it gets its own state.
        row["state"] = STATE_UNKNOWN
        row["error"] = str(exc)
        return row
    # allow-silent: NOT silent — this is the `error` state itself. A broad catch is
    # what makes "we did not look" first-class instead of a crash, and the row keeps
    # the exception text while summarize() downgrades the instrument's depth_claim to
    # `lower_bound_some_anchors_errored`. Narrowing it would let an unanticipated
    # exception type kill the run and lose every other instrument's measurement.
    except Exception as exc:  # noqa: BLE001  # allow-silent: this IS the `error` state
        row["state"] = STATE_ERROR
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row

    n = 0 if df is None else len(df)
    row["rows"] = n
    if n > 0:
        row["state"] = STATE_BARS
        try:
            row["first_bar"] = str(df["timestamp"].iloc[0])
        # allow-silent: nothing is emptied — `state` stays `bars` and `rows` keeps the
        # real count. Only the cosmetic first-bar stamp is dropped, and a frame that
        # HAS rows but no readable timestamp must not turn a positive depth answer
        # into an error.
        except Exception:  # noqa: BLE001  # allow-silent: cosmetic stamp only; state stays `bars`
            row["first_bar"] = None
    else:
        row["state"] = STATE_EMPTY
    return row


def summarize(rows: List[Dict[str, object]]) -> Dict[str, object]:
    """Per-instrument roll-up. The error count travels WITH the depth claim."""
    by_inst: Dict[str, Dict[str, object]] = {}
    for r in rows:
        inst = r["instrument"]
        b = by_inst.setdefault(inst, {
            "instrument": inst,
            "serves": INSTRUMENTS.get(inst, {}).get("serves", []),
            "relation": INSTRUMENTS.get(inst, {}).get("relation"),
            "anchors_probed": 0,
            "anchors_with_bars": 0,
            "anchors_empty": 0,
            "anchors_errored": 0,
            "anchors_unknown": 0,
            "earliest_bars_anchor": None,
            "depth_claim": None,
        })
        b["anchors_probed"] += 1
        st = r["state"]
        if st == STATE_BARS:
            b["anchors_with_bars"] += 1
            cur = b["earliest_bars_anchor"]
            if cur is None or r["anchor_days"] > cur:
                b["earliest_bars_anchor"] = r["anchor_days"]
        elif st == STATE_EMPTY:
            b["anchors_empty"] += 1
        elif st == STATE_ERROR:
            b["anchors_errored"] += 1
        else:
            b["anchors_unknown"] += 1

    for b in by_inst.values():
        # THE CLAIM IS GRADED, not just the number. An earliest-bar over a run
        # where anchors errored is a LOWER BOUND -- the venue may go deeper and
        # we simply failed to ask. Saying so here means a reader cannot pick up
        # the number without the caveat attached to it.
        if b["anchors_unknown"]:
            b["depth_claim"] = "unknown_instrument"
        elif b["earliest_bars_anchor"] is None:
            b["depth_claim"] = ("no_bars_at_any_anchor" if not b["anchors_errored"]
                                else "not_established_probe_errored")
        elif b["anchors_errored"]:
            b["depth_claim"] = "lower_bound_some_anchors_errored"
        else:
            b["depth_claim"] = "measured"
    return {"by_instrument": sorted(by_inst.values(), key=lambda x: str(x["instrument"]))}


def run(anchors, interval: str, window_days: int, *, now: Optional[datetime] = None,
        fetcher: Optional[Callable] = None) -> Dict[str, object]:
    now = now or datetime.now(timezone.utc)
    rows: List[Dict[str, object]] = []
    for inst in INSTRUMENTS:
        for a in anchors:
            rows.append(probe_one(inst, a, interval, window_days, now=now, fetcher=fetcher))
    out = {
        "probed_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interval": interval,
        "window_days": window_days,
        "anchors_days": list(anchors),
        "instruments_probed": len(INSTRUMENTS),
        "refused_symbols": REFUSED,
        "rows": rows,
    }
    out.update(summarize(rows))
    return out


def render(out: Dict[str, object]) -> str:
    lines = [
        f"dukascopy span probe — {out['probed_at_utc']} — interval={out['interval']} "
        f"window={out['window_days']}d anchors={out['anchors_days']}",
        "",
        "| instrument | serves | relation | deepest anchor with bars | claim | bars/empty/err |",
        "|---|---|---|---|---|---|",
    ]
    for b in out["by_instrument"]:
        deepest = b["earliest_bars_anchor"]
        lines.append(
            f"| `{b['instrument']}` | {', '.join(b['serves'])} | {b['relation']} | "
            f"{str(deepest) + 'd' if deepest is not None else '—'} | {b['depth_claim']} | "
            f"{b['anchors_with_bars']}/{b['anchors_empty']}/{b['anchors_errored']} |"
        )
    lines += ["", "REFUSED (no proxy probed, deliberately):"]
    for sym, why in out["refused_symbols"].items():
        lines.append(f"  - {sym}: {why}")
    errored = [b for b in out["by_instrument"] if b["anchors_errored"]]
    if errored:
        lines += ["", "⚠️ Anchors ERRORED on "
                  f"{len(errored)} instrument(s) — those depths are LOWER BOUNDS, "
                  "not measurements. 'We did not look' is not 'the venue has nothing'."]
    return "\n".join(lines)


def _self_test() -> int:
    checks = []

    def ck(name, ok):
        checks.append(bool(ok))
        print(f"  {'ok ' if ok else 'FAIL'} {name}")

    import pandas as pd
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)

    def fake(inst, start,
             end,        # inert: present to MATCH fetch()'s real signature — the
                         # window's END never decides whether the venue has data
                         # at its START, which is the only thing span asks.
             interval):  # inert: same reason; the probe pins one interval per run.
        """Deep for SPY (10y), shallow for QQQ (<=730d), raises for TLT."""
        if "TLT" in inst:
            raise RuntimeError("boom")
        age = (now - start).days
        if "QQQ" in inst and age > 800:
            return pd.DataFrame()
        return pd.DataFrame({"timestamp": [pd.Timestamp(start)], "open": [1.0],
                             "high": [1.0], "low": [1.0], "close": [1.0]})

    out = run((365, 1830), "1h", 3, now=now, fetcher=fake)
    by = {b["instrument"]: b for b in out["by_instrument"]}

    spy = by["INSTRUMENT_ETF_CFD_US_SPY_US_USD"]
    ck("deep instrument reports its deepest anchor", spy["earliest_bars_anchor"] == 1830)
    ck("a clean run is claimed 'measured'", spy["depth_claim"] == "measured")

    qqq = by["INSTRUMENT_ETF_CFD_US_QQQ_US_USD"]
    ck("shallow instrument stops at the shallow anchor", qqq["earliest_bars_anchor"] == 365)
    ck("an EMPTY answer is a real answer, not an error", qqq["anchors_empty"] == 1
       and qqq["anchors_errored"] == 0)

    tlt = by["INSTRUMENT_ETF_CFD_US_TLT_US_USD"]
    ck("a RAISED call is 'error', never 'empty'", tlt["anchors_errored"] == 2
       and tlt["anchors_empty"] == 0)
    ck("errored-everywhere is NOT 'no bars' — it is 'not established'",
       tlt["depth_claim"] == "not_established_probe_errored")

    # The load-bearing one: a partial error must DOWNGRADE the claim, not be
    # silently dropped while the number is kept.
    def flaky(inst, start, end, interval):
        if "GLD" in inst and (now - start).days == 1830:
            raise RuntimeError("transient")
        return fake(inst.replace("GLD", "SPY"), start, end, interval)

    out2 = run((365, 1830), "1h", 3, now=now, fetcher=flaky)
    gld = {b["instrument"]: b for b in out2["by_instrument"]}["INSTRUMENT_ETF_CFD_US_GLD_US_USD"]
    ck("a partial error downgrades the depth claim to a lower bound",
       gld["earliest_bars_anchor"] == 365
       and gld["depth_claim"] == "lower_bound_some_anchors_errored")

    # An unknown instrument name is a MAPPING bug, kept apart from depth.
    def missing(inst,
                start,      # inert: this stand-in fails at NAME RESOLUTION, which
                end,        # inert: happens before any window is looked at — so
                interval):  # inert: none of the window args can reach a decision.
        raise SystemExit(f"unknown instrument {inst!r}")

    out3 = run((365,), "1h", 3, now=now, fetcher=missing)
    ck("an unknown instrument name is its own state, not an error or a depth",
       all(b["depth_claim"] == "unknown_instrument" for b in out3["by_instrument"]))

    ck("the refused leveraged ETFs are absent from the probe set",
       not any("QLD" in i or "TQQQ" in i for i in INSTRUMENTS))
    ck("...and are named in the output with a reason",
       set(REFUSED) >= {"QLD", "TQQQ"})
    ck("render mentions the lower-bound caveat when anchors errored",
       "LOWER BOUNDS" in render(out2))

    ok = sum(checks)
    print(f"self-test: {ok}/{len(checks)} passed")
    return 0 if ok == len(checks) else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--interval", default="1h")
    p.add_argument("--window-days", type=int, default=3,
                   help="size of each probe window; small on purpose")
    p.add_argument("--anchors", default=",".join(str(a) for a in DEFAULT_ANCHORS),
                   help="comma-separated look-back distances in days")
    p.add_argument("--json-out", default="")
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args(argv)

    if a.self_test:
        return _self_test()

    anchors = tuple(int(x) for x in a.anchors.split(",") if x.strip())
    out = run(anchors, a.interval, a.window_days)
    print(render(out))
    if a.json_out:
        with open(a.json_out, "w") as fh:
            json.dump(out, fh, indent=2)
        print(f"\nwrote {a.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
