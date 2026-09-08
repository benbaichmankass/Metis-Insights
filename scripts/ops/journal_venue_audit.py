#!/usr/bin/env python3
"""Compare open JOURNAL rows against VENUE positions for every declared account.

# wiring: manual-only - invoked by a session or operator running the pipeline-integrity
# lane, deliberately NOT on a cadence. Putting this comparison on a schedule is an OPEN
# QUESTION this session was scoped out of deciding (MEASURE ONLY): see "What the next
# pass should check" item 2 in docs/claude/work/JOURNAL-VENUE-FLEET-AUDIT-2026-09-08.md.
# Wiring it to a workflow now would answer that question by default rather than by
# decision, and a fleet audit that runs unattended needs its read-state failures routed
# somewhere a human sees them first — which is exactly what is not yet designed.

WHY THIS EXISTS
---------------
Every journal-vs-venue divergence this system has found was found by ACCIDENT,
while someone was looking at something else — MGC's phantom lots during a health
review, Bybit's SOLUSDT divergence during a netting investigation,
``alpaca_portfolio``/TLT because one session went looking at one symbol. MI-177
asked the question fleet-wide for the first time on 2026-09-08 (see
``docs/claude/work/JOURNAL-VENUE-FLEET-AUDIT-2026-09-08.md``) and found it is a
one-shot read nobody could repeat. This is the repeatable form.

READ-ONLY. It opens no broker socket of its own, places no order, and writes
nothing. It reads three documents and does arithmetic on them.

⚠️ THE TWO READ CAPS THIS SCRIPT EXISTS TO ROUTE AROUND
-------------------------------------------------------
Both are silent, and either one alone manufactures FALSE divergences:

1. ``/api/diag/journal?table=trades`` is ``ORDER BY id DESC LIMIT <=1000``.
   On 2026-09-08 the trades table held 5545 rows, so 4 of the 29 open rows were
   below the window. Each presented as "the venue holds a position the journal
   has no row for" — four phantom divergences that do not exist.
2. ``/api/bot/positions`` (used here instead) has a hardcoded ``LIMIT 50``
   with no total and no truncation flag — see
   BL-20260908-BOT-POSITIONS-SILENTLY-CAPS-AT-50-OPEN-ROWS-AND-THE-FLEET-IS-AT-29.
   It was at 29/50 when this was written.

So the journal side is NEVER trusted on one surface. Its per-account notional is
cross-checked against ``/api/diag/exposure``, which serves
``RiskManager._open_gross_notional_from_db`` — an UNCAPPED SQL SUM over every
open non-backtest row. Agreement between the two is what makes
``journal_read_state = complete`` an assertion rather than a hope; disagreement
sets ``incomplete`` and the affected account's divergences are NOT reportable.

⚠️ THREE READ STATES, NEVER COLLAPSED
-------------------------------------
``/api/diag/exchange_positions`` returns ``positions: null`` for could-not-read
and ``[]`` for genuinely flat, and its ``error`` is null in BOTH cases. An
account whose venue could not be read is reported ``venue_read_state=NOT_READ``
and its divergence is ``UNKNOWN`` — it must NEVER be counted as reconciling.
That distinction is the entire value of this audit: an unasserted denominator
here produces a confident, wrong "the fleet reconciles".

⚠️ WHAT THIS CANNOT SEE
-----------------------
It compares SUMMED size per (account, symbol). It is therefore blind to
row-packing: two journal rows of 16 and 56 against one venue position of 72 net
to exact, however wrongly the 72 is attributed between them. Four symbols
carried that shape on 2026-09-08. A clean verdict here says the QUANTITIES
agree; it does not say per-trade attribution is correct.

Usage:
    python3 scripts/ops/journal_venue_audit.py            # table
    python3 scripts/ops/journal_venue_audit.py --json     # machine-readable
    python3 scripts/ops/journal_venue_audit.py --self-test

Exit codes: 0 = ran (whatever it found); 2 = could not read a required source.
A DIVERGENCE IS NOT A NON-ZERO EXIT — this measures, it does not gate.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DIAG_FETCH = REPO_ROOT / "scripts" / "ops" / "diag_fetch.sh"
ACCOUNTS_YAML = REPO_ROOT / "config" / "accounts.yaml"
POSITIONS_URL = "https://ict-bot.duckdns.org/api/bot/positions?include_paper=true"

# /api/bot/positions LIMIT 50 (src/web/api/routers/dashboard.py). Read from the
# source, not guessed; if the response reaches it we cannot tell truncated from
# complete, so we say so rather than reporting a short read as the whole book.
BOT_POSITIONS_LIMIT = 50

READ = "READ"
NOT_READ = "NOT_READ"


def _sign(side: Any) -> int:
    """buy/long -> +1, sell/short -> -1, anything else 0.

    Signed, deliberately: an unsigned comparison nets a long 10 against a short
    10 to zero and calls it reconciled.
    """
    s = str(side or "").strip().lower()
    if s in ("buy", "long"):
        return 1
    if s in ("sell", "short"):
        return -1
    return 0


def _diag(path: str) -> Any:
    proc = subprocess.run(
        ["bash", str(DIAG_FETCH), path],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"diag_fetch {path!r} failed (rc={proc.returncode}): {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def _bot_positions() -> list[dict]:
    with urllib.request.urlopen(POSITIONS_URL, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def collect() -> dict[str, Any]:
    import yaml

    declared = yaml.safe_load(ACCOUNTS_YAML.read_text(encoding="utf-8"))["accounts"]
    venue_doc = _diag("exchange_positions")
    exposure_doc = _diag("exposure")
    open_rows = _bot_positions()
    return build(declared, venue_doc, exposure_doc, open_rows)


def build(declared: dict, venue_doc: dict, exposure_doc: dict, open_rows: list) -> dict[str, Any]:
    """Pure — no I/O, so the self-test can drive it with planted documents."""
    jrows: dict[tuple[str, str], list] = defaultdict(list)
    for r in open_rows:
        jrows[(r.get("account"), r.get("symbol"))].append(r)

    venue: dict[tuple[str, str], float] = {}
    venue_state: dict[str, str] = {}
    for a in venue_doc.get("accounts", []):
        aid = a.get("account_id")
        positions = a.get("positions")
        if positions is None:
            # null = could-not-read. NOT flat. `error` is null here too, so the
            # response alone cannot tell a deliberate dry-account skip from an
            # outage — see the module docstring.
            venue_state[aid] = NOT_READ
            continue
        venue_state[aid] = READ
        for p in positions:
            venue[(aid, p.get("symbol"))] = _sign(p.get("side")) * float(p.get("size") or 0)

    exposure = {
        a.get("account_id"): (a.get("exposure") or {}).get("open_gross_notional")
        for a in exposure_doc.get("accounts", [])
    }

    truncation_suspected = len(open_rows) >= BOT_POSITIONS_LIMIT

    accounts: list[dict[str, Any]] = []
    for aid, cfg in declared.items():
        rows_here = [r for k, v in jrows.items() if k[0] == aid for r in v]
        seen_notional = sum(abs(float(r.get("qty") or 0) * float(r.get("entryPrice") or 0)) for r in rows_here)
        db_notional = exposure.get(aid)

        # journal completeness is an ASSERTION only when a second, uncapped
        # source agrees. Otherwise it is unproven and divergences here are not
        # reportable as divergences.
        if truncation_suspected:
            j_state, j_why = "incomplete", f"/api/bot/positions returned {len(open_rows)} rows against its own LIMIT {BOT_POSITIONS_LIMIT} — cannot tell truncated from complete"
        elif db_notional is None:
            j_state, j_why = "unproven", "exposure unmeasurable for this account — no independent cross-check available"
        elif abs(float(db_notional) - seen_notional) > max(0.01, 0.0001 * max(float(db_notional), 1.0)):
            j_state, j_why = "incomplete", f"notional mismatch: seen {seen_notional:.2f} vs uncapped DB {float(db_notional):.2f}"
        else:
            j_state, j_why = "complete", "notional matches the uncapped DB sum"

        v_state = venue_state.get(aid, NOT_READ)
        symbols = sorted({s for (a, s) in jrows if a == aid} | {s for (a, s) in venue if a == aid})
        rows_out = []
        for sym in symbols:
            rs = jrows.get((aid, sym), [])
            j_size = sum(_sign(r.get("side")) * float(r.get("qty") or 0) for r in rs)
            if v_state == NOT_READ:
                rows_out.append({"symbol": sym, "journal_rows": len(rs), "journal_size": j_size,
                                 "venue_size": None, "divergence": None, "verdict": "UNKNOWN",
                                 "trade_ids": [r.get("id") for r in rs]})
                continue
            v_size = venue.get((aid, sym), 0.0)
            d = j_size - v_size
            exact = abs(d) < 1e-9
            rows_out.append({"symbol": sym, "journal_rows": len(rs), "journal_size": j_size,
                             "venue_size": v_size, "divergence": d,
                             "verdict": ("EXACT" if exact else "DIVERGES") if j_state == "complete"
                                        else ("EXACT_BUT_UNPROVEN" if exact else "UNRELIABLE"),
                             "trade_ids": [r.get("id") for r in rs]})

        accounts.append({
            "account_id": aid,
            "account_class": cfg.get("account_class"),
            "exchange": cfg.get("exchange"),
            "mode": cfg.get("mode"),
            "real_money": cfg.get("account_class") == "real_money",
            "venue_read_state": v_state,
            "journal_read_state": j_state,
            "journal_read_note": j_why,
            "symbols": rows_out,
        })

    comparable = [r for a in accounts if a["venue_read_state"] == READ and a["journal_read_state"] == "complete"
                  for r in a["symbols"]]
    return {
        "declared_accounts": len(declared),
        "both_sides_readable": sum(1 for a in accounts
                                   if a["venue_read_state"] == READ and a["journal_read_state"] == "complete"),
        "venue_not_read": [a["account_id"] for a in accounts if a["venue_read_state"] == NOT_READ],
        "open_journal_rows": len(open_rows),
        "bot_positions_limit": BOT_POSITIONS_LIMIT,
        "truncation_suspected": truncation_suspected,
        "symbol_pairs_compared": len(comparable),
        "exact": sum(1 for r in comparable if r["verdict"] == "EXACT"),
        "diverging": [dict(r, account_id=a["account_id"], real_money=a["real_money"])
                      for a in accounts for r in a["symbols"] if r["verdict"] == "DIVERGES"],
        "accounts": accounts,
    }


def render(report: dict[str, Any]) -> str:
    out: list[str] = []
    out.append(f"declared accounts: {report['declared_accounts']} · both sides readable: {report['both_sides_readable']}")
    out.append(f"symbol pairs compared: {report['symbol_pairs_compared']} · exact: {report['exact']} · diverging: {len(report['diverging'])}")
    if report["truncation_suspected"]:
        out.append(f"!! /api/bot/positions returned {report['open_journal_rows']} rows at LIMIT {report['bot_positions_limit']} — EVERY verdict below is unreliable")
    if report["venue_not_read"]:
        out.append(f"!! venue NOT READ (never 'clean'): {', '.join(report['venue_not_read'])}")
    out.append("")
    # Real money first — an operator reading one line should read that one.
    for a in sorted(report["accounts"], key=lambda x: (not x["real_money"], x["account_id"])):
        tag = "REAL MONEY" if a["real_money"] else (a["account_class"] or "?")
        out.append(f"### {a['account_id']} [{tag}] venue={a['venue_read_state']} journal={a['journal_read_state']} ({a['journal_read_note']})")
        for r in a["symbols"]:
            v = "NOT_READ" if r["venue_size"] is None else f"{r['venue_size']:g}"
            d = "UNKNOWN" if r["divergence"] is None else f"{r['divergence']:+g}"
            out.append(f"    {r['symbol']:10s} rows={r['journal_rows']} journal={r['journal_size']:<12g} venue={v:<12s} div={d:<10s} {r['verdict']}  ids={','.join(map(str, r['trade_ids'])) or '-'}")
        if not a["symbols"]:
            out.append("    (no symbols on either side)")
    return "\n".join(out)


def _self_test() -> int:
    """Positive controls. A probe not shown finding a planted divergence proves
    nothing about a clean run — that is this repo's RULE ONE applied to itself."""
    declared = {
        "acct_real": {"account_class": "real_money", "exchange": "bybit", "mode": "live"},
        "acct_dry": {"account_class": "paper", "exchange": "oanda", "mode": "dry_run"},
    }
    venue = {"accounts": [
        {"account_id": "acct_real", "positions": [{"symbol": "XX", "side": "Buy", "size": 11.0}]},
        {"account_id": "acct_dry", "positions": None, "error": None},
    ]}
    exposure = {"accounts": [
        {"account_id": "acct_real", "exposure": {"open_gross_notional": 54.0}},
        {"account_id": "acct_dry", "exposure": {"open_gross_notional": None}},
    ]}
    rows = [
        {"id": "1", "account": "acct_real", "symbol": "XX", "side": "buy", "qty": 11.0, "entryPrice": 1.0},
        {"id": "2", "account": "acct_real", "symbol": "XX", "side": "buy", "qty": 43.0, "entryPrice": 1.0},
    ]
    fails = 0
    rep = build(declared, venue, exposure, rows)

    # 1 — the planted MGC-shaped divergence (journal 54 vs venue 11) is FOUND.
    d = rep["diverging"]
    ok = len(d) == 1 and d[0]["symbol"] == "XX" and abs(d[0]["divergence"] - 43.0) < 1e-9 and d[0]["real_money"]
    print(f"  self-test 1 (planted 54-vs-11 divergence is found, and flagged real-money): {'PASS' if ok else 'FAIL'}")
    fails += not ok

    # 2 — the unreadable account is NOT counted as reconciling.
    ok = rep["venue_not_read"] == ["acct_dry"] and rep["both_sides_readable"] == 1
    print(f"  self-test 2 (could-not-read account is excluded, never 'clean'): {'PASS' if ok else 'FAIL'}")
    fails += not ok

    # 3 — a journal side that disagrees with the uncapped sum is UNRELIABLE,
    #     not reported as a divergence. This is the false-positive class.
    bad_exposure = {"accounts": [{"account_id": "acct_real", "exposure": {"open_gross_notional": 500.0}},
                                 {"account_id": "acct_dry", "exposure": {"open_gross_notional": None}}]}
    rep3 = build(declared, venue, bad_exposure, rows)
    ok = rep3["accounts"][0]["journal_read_state"] == "incomplete" and not rep3["diverging"]
    print(f"  self-test 3 (journal disagreeing with the uncapped sum yields no divergence claim): {'PASS' if ok else 'FAIL'}")
    fails += not ok

    # 4 — hitting the bot/positions cap poisons every verdict rather than
    #     silently under-reporting the journal side.
    many = [dict(rows[0], id=str(i)) for i in range(BOT_POSITIONS_LIMIT)]
    rep4 = build(declared, venue, exposure, many)
    ok = rep4["truncation_suspected"] and rep4["both_sides_readable"] == 0
    print(f"  self-test 4 (at the LIMIT, nothing is reported as reconciling): {'PASS' if ok else 'FAIL'}")
    fails += not ok

    print("journal-venue-audit self-test:", "OK" if not fails else f"{fails} FAILURE(S)")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="emit the machine-readable report")
    ap.add_argument("--self-test", action="store_true", help="run the planted-positive controls and exit")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()
    try:
        report = collect()
    except Exception as exc:  # noqa: BLE001 — a read failure is "could not look", reported as such
        print(f"journal-venue-audit: could not read a required source: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
