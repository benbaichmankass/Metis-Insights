#!/usr/bin/env python3
"""Does `/api/bot/performance` publish an expectancyR that agrees with its own PnL?

MI-278 U13, for `PB-20260906-R-CONTAMINATION-QUANTIFIED-AND-THE-HEADLINE-SIGN-IS-WRONG`.

That row's clause (1) is *"expectancyR AGREES IN SIGN with its own totalPnl and
profitFactor on the SAME payload -- verified by reading the LIVE endpoint, not a
test"*. This grades exactly that, per block, off the endpoint's own response.

WHY IT READS THE ENDPOINT RATHER THAN REPRODUCING IT
  The row's own reproducer re-ran the query against the journal. That no longer
  works from the diag surface: `/api/diag/journal` HARD-CAPS at 1000 rows whatever
  limit is asked for (measured -- limit=3000 and limit=5000 both return 1000), and
  1000 rows now reaches back only ~25 days, so a 30d window cannot be rebuilt.
  Measured consequence: a journal-side rebuild of the real-money block gives n=38
  against the endpoint's 41. Reading the payload is EXACT by construction, needs no
  filter to be re-implemented, and cannot drift from the endpoint's own predicates.

WHAT THE PAYLOAD ALREADY CARRIES, AND WHY THAT IS THE POINT
  `rProvenance` (contaminated / confirmedInitial / unverified / noBasis) and `rBasis`
  (declaredInitial / storedStop / refusedWrongSide / noBasis) are ALREADY PUBLISHED
  beside the aggregate. The grading exists; the aggregate ignores it. This module
  changes nothing -- it states the contradiction in the terms the row asks for.

THREE SIGN STATES, NEVER COLLAPSED
  `agree` / `disagree` / `ungradeable` -- the last covering a zero or absent term,
  because "expectancyR is 0.0" and "we could not compare" are different facts and
  only one of them is a finding.
"""

from __future__ import annotations

# wiring: manual-only - it grades a LIVE endpoint response against a specific backlog row's clause (1). Running it on a cadence would publish a verdict nobody reads; the row's own exit is a human reading the live endpoint, which is what this automates for that reader.

import argparse
import json
import pathlib
import urllib.request

DEFAULT_URL = "https://ict-bot.duckdns.org/api/bot/performance?window=30d"

SIGN_STATES = ("agree", "disagree", "ungradeable")
FANOUT_STATES = ("measured", "partially_reachable", "unreachable")
"""`partially_reachable` is *the journal tail does not span this block's window*.
It must never render as 1.000x -- an inflation computed over a shorter window is a
different quantity, and reporting it as the block's would be the exact
unprovenanced-diagnostic error this repo names."""

BLOCKS = ("", "demo", "paper", "paperPortfolio")


def sign_verdict(total_pnl, expectancy_r, profit_factor) -> dict:
    """Grade one block's internal consistency.

    profitFactor is reported beside the pair rather than folded into it: it is a
    THIRD statement of the same direction, and the row names all three.
    """
    if total_pnl is None or expectancy_r is None:
        return {"state": "ungradeable", "why": "totalPnl or expectancyR absent"}
    if total_pnl == 0 or expectancy_r == 0:
        return {"state": "ungradeable",
                "why": "a zero term has no sign -- not the same as a disagreement"}
    agree = (total_pnl > 0) == (expectancy_r > 0)
    out = {
        "state": "agree" if agree else "disagree",
        "total_pnl": total_pnl,
        "expectancy_r": expectancy_r,
        "profit_factor": profit_factor,
    }
    if profit_factor is not None:
        # PF < 1 means the block lost money; it should side with totalPnl.
        out["profit_factor_sides_with"] = (
            "pnl" if ((profit_factor < 1) == (total_pnl < 0)) else "expectancy_r")
    return out


def grade_block(b: dict) -> dict:
    rp = b.get("rProvenance") or {}
    rb = b.get("rBasis") or {}
    n = b.get("totalTrades")
    contaminated = rp.get("contaminated")
    return {
        "n": n,
        "sign": sign_verdict(b.get("totalPnl"), b.get("expectancyR"), b.get("profitFactor")),
        "total_r": b.get("totalR"),
        "r_provenance": rp,
        "r_basis": rb,
        "contaminated_share": (contaminated / n) if (contaminated is not None and n) else None,
        # Which basis the R was actually computed from -- the row's first candidate
        # fix was "read the declared initial risk", so whether that already happened
        # is the first thing a fixer needs and the payload states it.
        "uses_stored_stop": rb.get("storedStop"),
        "uses_declared_initial": rb.get("declaredInitial"),
        "refused_wrong_side": rb.get("refusedWrongSide"),
    }


def fanout_for_rows(rows: list[dict], window_start: str | None) -> dict:
    """The U12 term for this block's population, with its reachability stated.

    Returns `partially_reachable` when the journal tail begins AFTER the block's
    window does -- the inflation then describes a shorter window than the block.
    """
    if not rows:
        return {"state": "unreachable", "why": "no rows for this block in the journal pull"}
    oldest = min((r.get("created_at") or "") for r in rows)
    pkgs = {r.get("order_package_id") for r in rows if r.get("order_package_id")}
    if not pkgs:
        return {"state": "unreachable", "why": "no row carries an order_package_id"}
    infl = len(rows) / len(pkgs)
    state = "measured"
    if window_start and oldest and oldest > window_start:
        state = "partially_reachable"
    return {
        "state": state,
        "rows": len(rows),
        "packages": len(pkgs),
        "inflation": infl,
        "reachable_from": oldest,
        "block_window_starts": window_start,
    }


def report(payload: dict, journal: list[dict] | None) -> str:
    L = []
    since = payload.get("since")
    L.append(f"ENDPOINT: window={payload.get('window')} since={since}")
    L.append("")
    for key in BLOCKS:
        b = payload if key == "" else payload.get(key)
        if not isinstance(b, dict) or "totalTrades" not in b:
            continue
        label = key or "real_money (the default payload)"
        g = grade_block(b)
        s = g["sign"]
        L.append(f"{label}  n={g['n']}")
        mark = "  *** DISAGREE ***" if s["state"] == "disagree" else ""
        L.append(f"  totalPnl {s.get('total_pnl')}   expectancyR {s.get('expectancy_r')}   "
                 f"profitFactor {s.get('profit_factor')}   -> sign {s['state']}{mark}")
        rp = g["r_provenance"]
        share = g["contaminated_share"]
        L.append(f"  rProvenance contaminated={rp.get('contaminated')} "
                 f"confirmedInitial={rp.get('confirmedInitial')} unverified={rp.get('unverified')}"
                 + (f"   ({share:.1%} of the block)" if share is not None else ""))
        L.append(f"  rBasis declaredInitial={g['uses_declared_initial']} "
                 f"storedStop={g['uses_stored_stop']} refusedWrongSide={g['refused_wrong_side']}")
        if journal is not None:
            rows = _rows_for_block(journal, key)
            f = fanout_for_rows(rows, since)
            if f["state"] == "measured":
                L.append(f"  fan-out (U12): {f['inflation']:.3f}x over {f['rows']} rows / {f['packages']} packages")
            elif f["state"] == "partially_reachable":
                L.append(f"  fan-out (U12): {f['inflation']:.3f}x over {f['rows']} rows / "
                         f"{f['packages']} packages -- PARTIALLY REACHABLE ONLY "
                         f"(journal reaches {f['reachable_from'][:10]}, block starts {str(since)[:10]}); "
                         "NOT this block's inflation")
            else:
                L.append(f"  fan-out (U12): {f['state']} -- {f.get('why')}")
        L.append("")
    return "\n".join(L)


def _rows_for_block(journal: list[dict], key: str) -> list[dict]:
    """Approximate a block's population from a journal pull. Real money only for the
    default block; paper for the rest. Deliberately crude -- its output is only ever
    reported with its reachability state attached."""
    want_real = key == ""
    out = []
    for r in journal:
        if r.get("status") != "closed" or r.get("is_backtest") or r.get("pnl") is None:
            continue
        is_real = r.get("account_class") == "real_money"
        if is_real == want_real:
            out.append(r)
    return out


def self_test() -> int:
    ok = 0

    def chk(c, m):
        nonlocal ok
        print(("  ok  " if c else "  FAIL ") + m)
        ok += 0 if c else 1

    chk(sign_verdict(-10, 0.5, 0.8)["state"] == "disagree",
        "a losing block with a positive expectancyR DISAGREES -- the row's clause (1)")
    chk(sign_verdict(-10, -0.5, 0.8)["state"] == "agree", "both negative agree")
    chk(sign_verdict(10, 0.5, 2.0)["state"] == "agree", "both positive agree")
    chk(sign_verdict(0, 0.5, 1.0)["state"] == "ungradeable",
        "a ZERO term is ungradeable, never a disagreement")
    chk(sign_verdict(-10, 0.0, 0.8)["state"] == "ungradeable",
        "  and that holds for a zero expectancyR too")
    chk(sign_verdict(None, 0.5, 1.0)["state"] == "ungradeable", "an absent term cannot be graded")
    v = sign_verdict(-10, 0.5, 0.8)
    chk(v["profit_factor_sides_with"] == "pnl",
        "profitFactor is reported as a THIRD statement, and here it sides with PnL")

    g = grade_block({"totalTrades": 40, "totalPnl": -1.0, "expectancyR": 0.5,
                     "profitFactor": 0.9,
                     "rProvenance": {"contaminated": 10},
                     "rBasis": {"storedStop": 0, "declaredInitial": 40, "refusedWrongSide": 0}})
    chk(abs(g["contaminated_share"] - 0.25) < 1e-9, "the contaminated share is per block")
    chk(g["uses_stored_stop"] == 0 and g["uses_declared_initial"] == 40,
        "the R BASIS is surfaced -- a fixer must know the declared-initial path is already on")
    chk(grade_block({"totalTrades": 0, "rProvenance": {"contaminated": 0}})["contaminated_share"] is None,
        "an empty block yields None, never a 0.0 share")

    f = fanout_for_rows([{"order_package_id": "p", "created_at": "2026-09-01"}], "2026-08-13")
    chk(f["state"] == "partially_reachable",
        "a journal that starts AFTER the block's window is partially_reachable, never `measured`")
    f2 = fanout_for_rows([{"order_package_id": "p", "created_at": "2026-08-01"}], "2026-08-13")
    chk(f2["state"] == "measured" and f2["inflation"] == 1.0,
        "a journal that spans the window IS measured")
    chk(fanout_for_rows([], "2026-08-13")["state"] == "unreachable",
        "no rows is `unreachable`, never 1.0x")
    chk(fanout_for_rows([{"created_at": "2026-08-01"}], "2026-08-13")["state"] == "unreachable",
        "rows with no package id cannot be graded")
    chk(set(SIGN_STATES) == {"agree", "disagree", "ungradeable"}, "the sign vocabulary is closed")

    print("self-test: OK" if ok == 0 else f"self-test: {ok} FAILURE(S)")
    return 1 if ok else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--payload", help="a saved /api/bot/performance response")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--journal", help="a saved /api/diag/journal?table=trades pull (optional)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()

    if a.payload:
        payload = json.loads(pathlib.Path(a.payload).read_text())
    else:
        with urllib.request.urlopen(a.url, timeout=30) as fh:
            payload = json.loads(fh.read().decode())
    journal = None
    if a.journal:
        journal = json.loads(pathlib.Path(a.journal).read_text())
        if isinstance(journal, dict):
            journal = journal.get("rows") or []

    if a.json:
        out = {}
        for key in BLOCKS:
            b = payload if key == "" else payload.get(key)
            if isinstance(b, dict) and "totalTrades" in b:
                out[key or "real_money"] = grade_block(b)
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print(report(payload, journal))

    disagree = [
        k or "real_money"
        for k in BLOCKS
        if isinstance(payload if k == "" else payload.get(k), dict)
        and "totalTrades" in (payload if k == "" else payload[k])
        and grade_block(payload if k == "" else payload[k])["sign"]["state"] == "disagree"
    ]
    if disagree:
        print(f"CLAUSE (1) OF PB-20260906 IS FAILING on: {', '.join(disagree)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
