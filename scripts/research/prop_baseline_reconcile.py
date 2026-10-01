#!/usr/bin/env python3
"""PROP-JOURNAL item 2: decompose breakout_1's journal-vs-venue baseline residual.

residual = (venue balance - account_size) - sum(journaled closed pnl, duplicates removed)

Every term carries a src/runtime/provenance.py bucket. UNVERIFIED is the remainder and is
never folded into a measured term. Rerun (live reads, no auth):
    python3 scripts/research/prop_baseline_reconcile.py [--out FILE]
Per-fill tables below were read off each fill's own `reason` text (MEASURED where a
commission is stated for both legs) -- re-read them if the journal rows are ever amended.
"""
import argparse, json, sys, urllib.request

BASE = "https://ict-bot.duckdns.org/api/bot/prop"
ACCOUNT_SIZE = 5000.0
# Journal rows that double-count one real round trip (the estimated close row is superseded by the
# venue-figure row). #52 = executor's last-unrealized estimate of the close #53 re-reports.
DUPLICATES = {52: "superseded by #53 (same round trip, venue figures)"}
# Commissions stated for BOTH legs in the fill text, on a fill whose pnl is gross of them: MEASURED.
COMMISSION_MEASURED = {14: 2.54, 29: 4.52, 41: 4.14, 45: 2.80}
# gross-or-unknown-basis fills with no both-leg figure: ESTIMATED at 2 x 4.0 bps x notional
# (4.0 bps/side MEASURED: #29 2.25/(3x1874.34), #35 1.97/(2x2453.56), #41 2.06/(49x105.04)).
COMMISSION_ESTIMATED_NOTIONAL = {2: 1290.0, 11: 1838.0, 23: 3756.0, 25: 3679.0, 27: 3768.0, 38: 2499.0, 39: 3797.0}
# Midnight-UTC crossings (DXtrade debits swap once per midnight crossed; 3.3 bps/day of notional;
# rate MEASURED twice: #9 swap 1.96 = 1183 x 3.3bps x 5, #44 financing 1.17 = 3466 x 3.38bps x 1).
# Excluded: #9 (swap already inside its net pnl), #17 (its net-gross gap already contains swap).
SWAP_CROSSINGS = {2: (1290, 1), 11: (1838, 1), 14: (3018, 3), 19: (2773, 2), 23: (3756, 1), 25: (3679, 2),
                  27: (3768, 2), 38: (2499, 1), 39: (3797, 1), 41: (5147, 1), 45: (3466, 2)}
SWAP_BPS_PER_CROSSING = 3.3


def get(path):
    with urllib.request.urlopen(f"{BASE}/{path}", timeout=60) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out"); a = ap.parse_args()
    fills = get("fills?account_id=breakout_1&limit=500")["fills"]
    st = get("status?account_id=breakout_1")["status"]
    closed = [f for f in fills if f["status"] == "closed" and f["id"] not in DUPLICATES]
    pnl = round(sum(f["pnl"] or 0 for f in closed), 2)
    venue = round(st["balance"] - ACCOUNT_SIZE, 2)
    resid = round(venue - pnl, 2)
    comm_m = round(sum(COMMISSION_MEASURED.values()), 2)
    comm_e = round(sum(n * 8e-4 for n in COMMISSION_ESTIMATED_NOTIONAL.values()), 2)
    swap_e = round(sum(n * k * SWAP_BPS_PER_CROSSING * 1e-4 for n, k in SWAP_CROSSINGS.values()), 2)
    unexplained = round(resid + comm_m + comm_e + swap_e, 2)
    out = {
        "account": "breakout_1", "as_of": st["reported_at"], "open_positions_at_snapshot": True if st.get("unrealized") else False,
        "population": f"{len(closed)} closed journal fills (duplicates removed: {sorted(DUPLICATES)}); venue balance snapshot",
        "terms": {
            "venue_balance_minus_account_size": {"usd": venue, "provenance": "measured"},
            "sum_journaled_closed_pnl": {"usd": pnl, "provenance": "measured (mixed gross/net basis per fill)"},
            "residual_to_explain": {"usd": resid},
            "commissions_on_gross_basis_fills_stated": {"usd": -comm_m, "provenance": "measured", "fills": sorted(COMMISSION_MEASURED)},
            "commissions_on_gross_or_unknown_basis_fills": {"usd": -comm_e, "provenance": "estimated", "fills": sorted(COMMISSION_ESTIMATED_NOTIONAL)},
            "swap_by_midnight_crossings": {"usd": -swap_e, "provenance": "estimated", "rate_bps_per_crossing": SWAP_BPS_PER_CROSSING,
                                            "note": "crossing counts partly from report timestamps, not venue open/close times"},
            "unexplained_remainder": {"usd": unexplained, "provenance": "unverified"},
        },
        "not_folded": "the unexplained remainder is UNVERIFIED and is not added to any measured term",
    }
    s = json.dumps(out, indent=1)
    if a.out:
        open(a.out, "w").write(s + "\n")
    print(s)


if __name__ == "__main__":
    sys.exit(main())
