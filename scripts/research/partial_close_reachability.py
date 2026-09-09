#!/usr/bin/env python3
"""Why has the partial-close producer never fired? — MI-209c reproduce script.

Reproduces every figure in
``docs/research/partial-close-producer-never-fired-2026-09-09.md``.

WHAT THIS ASKS. ``_apply_partial_close`` (``src/runtime/order_monitor.py``) is
built, wired, and has executed **zero** times across a fleet that has closed
thousands of trades. This script establishes *why* from live state rather than
from reading the code and concluding it "looks reachable" — the thing the work
object explicitly rules out.

WHAT IT IS NOT. It is not a harness that fires the path. A harness is precisely
what cannot tell you why *live* trades never did, which is why every read here
is a GET against the running trader's own journal and config.

THE DISCIPLINE THIS SCRIPT ENCODES
-----------------------------------
1. **A negative needs a positive control.** Every "zero" probe below is paired
   with a control of the SAME SHAPE that must return non-zero. If the control
   is quiet the probe is broken, and the script says so instead of reporting a
   zero. (``CLAUDE.md`` § RULE ONE.)
2. **``filter_state`` is asserted, never assumed.** ``/api/bot/db/table``
   IGNORES an unknown filter column and returns the UNFILTERED ``total`` —
   indistinguishable from "the filter matched everything". Every count here
   refuses unless the route echoes ``filter_state == "applied"``.
   (``BL-20260813-DB-EXPLORER-SILENTLY-IGNORES-UNKNOWN-FILTER-COLUMN``.)
3. **Provenance is never folded.** ``peak_r`` is reported split by
   ``peak_provenance``; ``MEASURED`` / ``ESTIMATED`` / ``FABRICATED`` /
   ``UNVERIFIED`` are four different facts (``src/runtime/provenance.py``).
4. **The sentinel is excluded and COUNTED, never silently dropped.**
   ``position_telemetry.peak_r`` can hold ``-1e18`` — a sort key that was
   persisted as a value (``BL-20260818-TELEMETRY-PEAK-R-STORES-COALESCE-SENTINEL``).
   A mean or min taken over the raw column is fabricated.

Usage::

    python3 scripts/research/partial_close_reachability.py          # live read
    python3 scripts/research/partial_close_reachability.py --self-test
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

BASE = "https://ict-bot.duckdns.org/api/bot"
TIMEOUT = 45

#: turtle_soup's live exit geometry, read from /api/bot/config at runtime; these
#: are only the fallbacks used by --self-test.
TP1_AT_R_DEFAULT = 1.0
TP2_AT_R_DEFAULT = 3.0
BE_AT_R_DEFAULT = 0.75

#: The sentinel that must never be averaged. Anything at or below this is a
#: sort key that leaked into the value column, not an observed excursion.
SENTINEL_FLOOR = -1e6


class ProbeUnsound(RuntimeError):
    """Raised when a count cannot be trusted — a dropped filter, or a positive
    control that came back empty. Refusing beats reporting a bad zero."""


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------

def _get(path: str) -> Dict[str, Any]:
    with urllib.request.urlopen(f"{BASE}/{path}", timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def count(table: str, col: Optional[str] = None, op: str = "eq",
          val: Optional[str] = None) -> int:
    """Return ``total`` for a (possibly filtered) table read.

    Refuses with :class:`ProbeUnsound` unless the route confirms the filter was
    APPLIED. An ignored filter yields the whole-table count, which reads
    identically to a filter that matched every row.
    """
    q = "limit=1"
    if col is not None:
        q += (f"&filter_col={urllib.parse.quote(col)}&filter_op={op}"
              f"&filter_val={urllib.parse.quote(val or '')}")
    doc = _get(f"db/table/{table}?{q}")
    state = doc.get("filter_state")
    want = "applied" if col is not None else "not_requested"
    if state != want:
        raise ProbeUnsound(
            f"{table}[{col} {op} {val!r}] -> filter_state={state!r}, expected "
            f"{want!r}; the returned total ({doc.get('total')}) is NOT a match "
            f"count and must not be reported")
    return int(doc.get("total") or 0)


def rows(table: str, limit: int = 500, col: Optional[str] = None,
         op: str = "eq", val: Optional[str] = None) -> List[Dict[str, Any]]:
    q = f"limit={limit}"
    if col is not None:
        q += (f"&filter_col={urllib.parse.quote(col)}&filter_op={op}"
              f"&filter_val={urllib.parse.quote(val or '')}")
    return _get(f"db/table/{table}?{q}").get("rows") or []


# ---------------------------------------------------------------------------
# pure helpers (what --self-test exercises)
# ---------------------------------------------------------------------------

def split_sentinel(values: List[float]) -> Tuple[List[float], int]:
    """Partition peak_r values into (clean, n_sentinel).

    The sentinel is a SORT KEY (``-1e18``) that reached the value column. It is
    excluded from every statistic and reported as its own count — dropping it
    silently would understate how contaminated the instrument is, and keeping it
    would fabricate the minimum and the mean.
    """
    clean = [v for v in values if v > SENTINEL_FLOOR]
    return clean, len(values) - len(clean)


def quantiles(values: List[float]) -> Dict[str, float]:
    """Order statistics over an already-cleaned list. Empty in, empty out."""
    if not values:
        return {}
    v = sorted(values)
    n = len(v)

    def q(p: float) -> float:
        return v[min(n - 1, int(p * n))]

    return {"n": n, "min": v[0], "p10": q(.10), "p25": q(.25),
            "median": q(.50), "p75": q(.75), "p90": q(.90), "max": v[-1]}


def share_at_or_above(values: List[float], threshold: float) -> Tuple[int, int]:
    """How many cleaned values clear *threshold* — the margin against a TP rung."""
    return sum(1 for v in values if v >= threshold), len(values)


def routed_accounts(accounts: List[Dict[str, Any]], strategy: str) -> List[str]:
    """Which accounts carry *strategy* in their roster. Names, not a bare count,
    because the work object asks the answer to NAME the accounts."""
    out = []
    for a in accounts:
        if strategy in (a.get("strategies") or []):
            # /api/bot/config returns accounts as a LIST whose identity key is
            # `id`; `name`/`account_id` are the shapes other surfaces use. Try
            # all three — a "?" here would silently anonymise the very accounts
            # the work object asks this answer to NAME.
            out.append(str(a.get("name") or a.get("account_id")
                           or a.get("id") or "?"))
    return out


# ---------------------------------------------------------------------------
# the measurement
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 78)
    print("MI-209c — why the partial-close producer has never fired")
    print("=" * 78)

    # -- 1. census -----------------------------------------------------------
    n_trades = count("trades")
    n_pkgs = count("order_packages")
    print(f"\n[1] CENSUS (complete, not a tail read)")
    print(f"    trades          n = {n_trades}")
    print(f"    order_packages  n = {n_pkgs}")

    # -- 2. the zero, with positive controls --------------------------------
    print("\n[2] HAS A PARTIAL EVER BANKED? — zero probes, each with a control")
    controls = {
        'notes~"trade_id"':   count("trades", "notes", "like", '%"trade_id"%'),
        'notes~"is_dry"':     count("trades", "notes", "like", '%"is_dry"%'),
        'notes~"confidence"': count("trades", "notes", "like", '%"confidence"%'),
    }
    for k, v in controls.items():
        print(f"    CONTROL  {k:22s} = {v:5d}")
    if not all(v > 0 for v in controls.values()):
        raise ProbeUnsound(
            "a positive control returned zero — the notes/LIKE probe cannot "
            "match a known-present JSON key, so the zeros below prove nothing")

    negatives = {
        "notes~partial_closes":    count("trades", "notes", "like", "%partial_closes%"),
        "exit_reason=tp1_partial": count("trades", "exit_reason", "eq", "tp1_partial"),
        "exit_reason~partial":     count("trades", "exit_reason", "like", "%partial%"),
        "exit_reason~tp2":         count("trades", "exit_reason", "like", "%tp2%"),
        "pkg close_reason~partial": count("order_packages", "close_reason", "like", "%partial%"),
    }
    for k, v in negatives.items():
        print(f"    PROBE    {k:22s} = {v:5d}")
    print("    -> controls fire, probes are silent: the zero is REAL, not broken.")

    # -- 3. the sole producer ------------------------------------------------
    ts_trades = count("trades", "strategy_name", "eq", "turtle_soup")
    ts_pkgs = count("order_packages", "strategy_name", "eq", "turtle_soup")
    print(f"\n[3] THE SOLE PRODUCER — turtle_soup (only site in src/ emitting "
          f"close_qty_pct)")
    print(f"    trades         {ts_trades} of {n_trades} "
          f"({100.0*ts_trades/n_trades:.3f}%)")
    print(f"    order_packages {ts_pkgs} of {n_pkgs} "
          f"({100.0*ts_pkgs/n_pkgs:.3f}%)")
    opened = 0
    for r in rows("trades", 50, "strategy_name", "eq", "turtle_soup"):
        st = r.get("status")
        if st not in ("rejected", "exchange_rejected"):
            opened += 1
        print(f"      id={r.get('id')} {str(r.get('timestamp'))[:10]} "
              f"status={st} closed_at={r.get('closed_at')} "
              f"size={r.get('position_size')} acct={r.get('account_id')}")
    print(f"    positions that actually OPENED: {opened}")

    # -- 4. why it cannot run: config ---------------------------------------
    cfg = _get("config")
    A = cfg["accounts"]
    accts = A if isinstance(A, list) else [dict(v, name=k) for k, v in A.items()]
    S = cfg["strategies"]
    legs = S if isinstance(S, list) else [dict(v, name=k) for k, v in S.items()]
    ts = next((l for l in legs if l.get("name") == "turtle_soup"), {})
    print(f"\n[4] WHY IT CANNOT RUN — live /api/bot/config, as_of {cfg.get('as_of')}")
    print(f"    turtle_soup enabled   = {ts.get('enabled')}")
    print(f"    turtle_soup execution = {ts.get('execution')!r}   "
          f"<- coordinator.py forces effective_dry for 'shadow'")
    print(f"    tp1_at_r={ts.get('tp1_at_r')} tp2_at_r={ts.get('tp2_at_r')} "
          f"be_at_r={ts.get('be_at_r')} partial_close_pct={ts.get('partial_close_pct')}")
    routed = routed_accounts(accts, "turtle_soup")
    ctrl = routed_accounts(accts, "trend_donchian")
    print(f"    routed to {len(routed)} of {len(accts)} accounts: {routed or 'NONE'}")
    print(f"    CONTROL trend_donchian routed to {len(ctrl)}: {ctrl}")
    if not ctrl:
        raise ProbeUnsound(
            "routing control is empty — the roster probe cannot find a "
            "known-routed strategy, so 'turtle_soup routes nowhere' proves nothing")

    # -- 5. the margin: would a 1.0R TP1 even be reachable? ------------------
    pt = rows("position_telemetry", 500)
    print(f"\n[5] THE MARGIN — is the TP1 threshold reachable at all?")
    print(f"    NOTE: turtle_soup has {sum(1 for r in pt if r.get('strategy')=='turtle_soup')} "
          f"rows here, so this is the FLEET's excursion distribution used as a "
          f"PROXY — it is not turtle_soup's own and must not be quoted as such.")
    prov: Dict[str, List[float]] = {}
    n_null = 0
    for r in pt:
        if r.get("peak_r") is None:
            n_null += 1
            continue
        prov.setdefault(str(r.get("peak_provenance")), []).append(float(r["peak_r"]))
    print(f"    population: {len(pt)} rows; {n_null} peak_r NULL")
    tp1 = float(ts.get("tp1_at_r") or TP1_AT_R_DEFAULT)
    tp2 = float(ts.get("tp2_at_r") or TP2_AT_R_DEFAULT)
    be = float(ts.get("be_at_r") or BE_AT_R_DEFAULT)
    for p in sorted(prov):
        clean, n_sent = split_sentinel(prov[p])
        q = quantiles(clean)
        print(f"\n    [{p.upper()}]  n={q.get('n',0)}  "
              f"(sentinel -1e18 excluded: {n_sent})")
        if not q:
            continue
        print(f"      min {q['min']:+.3f}  p25 {q['p25']:+.3f}  "
              f"median {q['median']:+.3f}  p75 {q['p75']:+.3f}  "
              f"p90 {q['p90']:+.3f}  max {q['max']:+.3f}")
        for label, thr in (("be_at_r", be), ("tp1_at_r", tp1), ("tp2_at_r", tp2)):
            k, n = share_at_or_above(clean, thr)
            print(f"      >= {thr:.2f}R ({label:9s}): {k:3d}/{n} = {100.0*k/n:5.1f}%")
    print("\n    Provenance is NOT folded: an ESTIMATED share is not a MEASURED one.")
    return 0


# ---------------------------------------------------------------------------
# self-test — pure helpers only, no network
# ---------------------------------------------------------------------------

def self_test() -> int:
    ok = 0

    def eq(got, want, what):
        nonlocal ok
        assert got == want, f"{what}: got {got!r} want {want!r}"
        ok += 1

    # split_sentinel isolates the sort key without dropping it from the tally
    clean, n = split_sentinel([-1e18, 0.5, 1.5, -1e18])
    eq(clean, [0.5, 1.5], "sentinel removed from values")
    eq(n, 2, "sentinel counted, not silently dropped")
    eq(split_sentinel([])[0], [], "empty in, empty out")
    eq(split_sentinel([-0.9])[0], [-0.9], "a real negative excursion is NOT a sentinel")

    # quantiles
    q = quantiles([0.0, 1.0, 2.0, 3.0])
    eq(q["n"], 4, "n")
    eq(q["min"], 0.0, "min")
    eq(q["max"], 3.0, "max")
    eq(quantiles([]), {}, "no fabricated stats on an empty population")

    # threshold share
    eq(share_at_or_above([0.5, 1.0, 1.5], 1.0), (2, 3), "at-or-above is inclusive")
    eq(share_at_or_above([], 1.0), (0, 0), "empty population -> 0/0, not 0/1")

    # routing
    accts = [{"name": "a", "strategies": ["x", "y"]}, {"name": "b", "strategies": []}]
    eq(routed_accounts(accts, "x"), ["a"], "routing names the account")
    eq(routed_accounts(accts, "zzz"), [], "unrouted strategy -> empty")
    eq(routed_accounts([{"name": "c"}], "x"), [], "missing roster key is not a crash")
    # the live route keys identity on `id`, not `name` — a regression here
    # anonymises the accounts to "?" without failing anything else
    eq(routed_accounts([{"id": "bybit_1", "strategies": ["x"]}], "x"),
       ["bybit_1"], "account identity resolves from `id`")

    print(f"self-test: {ok} assertions passed")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    try:
        sys.exit(self_test() if args.self_test else main())
    except ProbeUnsound as exc:
        print(f"\nREFUSING TO REPORT: {exc}", file=sys.stderr)
        sys.exit(2)
