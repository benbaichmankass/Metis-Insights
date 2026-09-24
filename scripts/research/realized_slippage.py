#!/usr/bin/env python3
"""D3 — realized slippage per venue from real fills, and the Stage-0 flip count.

ONE QUESTION (checklist row D3, 2026-09-24): what is realized round-trip slippage
per venue, measured from fills whose provenance is MEASURED, and at that value how
many Stage-0 verdicts in ``comms/strategy_evidence/*.json`` flip under
``RULE-D1-STAGE0-NET-OF-FULL-COST`` (``net_r_oos > 0``)?

Read-only. It changes no default, no config and no evidence record.

REFERENCE PRICE (the choice this measurement rests on — stated, not implied)
---------------------------------------------------------------------------
Slippage is measured against the price the Stage-0 HARNESS assumes it gets,
because the question is whether the harness's slippage term is the right size:

* ENTRY — reference = ``order_packages.entry``, the strategy's intended entry at
  submit time (the level the order was sized on; ``execute.py`` journals
  ``pkg.entry``). Fill = the qty-weighted VWAP of the venue fills whose
  ``order_id`` equals the trade's ``broker_order_id`` (``exchange_fills`` store,
  a venue-reported fill ⇒ MEASURED). Fills on the wrong side for the trade's
  direction are not an entry and are dropped.
* EXIT — only closed trades whose ``exit_price_source`` classifies MEASURED
  under ``src/runtime/provenance.py`` AND whose exit has a harness-equivalent
  level: ``sl``/``sl_cross`` → ``order_packages.sl`` (the LAST stop, i.e. after
  any trail — the level that was actually hit), ``tp``/``tp_cross`` →
  ``order_packages.tp``. Every other exit (reconciler, watchdog, intent-reduce,
  netted, market strategy-closes) has no fixed reference and is counted as
  excluded, never priced.

Sign: positive bps = ADVERSE (paid), negative = price improvement. Per side,
bps of the reference price. Round-trip = entry side + exit side, which is the
unit ``execution_costs.DEFAULT_SLIPPAGE_BPS_ROUNDTRIP`` is in (the SUM of both
sides, bps of notional).

Usage
-----
  # 1. pull the inputs (needs DIAG_READ_TOKEN; direct HTTPS to the live API)
  python3 scripts/research/realized_slippage.py pull --out-dir /tmp/d3
  # 2. measure + re-price the evidence corpus
  python3 scripts/research/realized_slippage.py analyze --in-dir /tmp/d3 \
      --out comms/research/d3_realized_slippage/2026-09-24.json

The pull is the "source query": ``GET /api/bot/pnl/exchange/fills`` (90d, per
account, split per symbol when a page is truncated), and ``GET
/api/diag/journal`` paged over ``trades`` and ``order_packages``.
"""
from __future__ import annotations

# wiring: manual-only - a one-question measurement (checklist row D3); a session re-runs it by hand with the two commands in the docstring. The recurring job is D3's later build step, not this script.

import argparse
import collections
import glob
import json
import os
import statistics
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.config.accounts_loader import load_accounts_dict  # noqa: E402
from src.runtime.provenance import MEASURED, classify  # noqa: E402

API_BASE = "https://ict-bot.duckdns.org"
FILLS_DAYS = 90

# Which fills are a MARKET and which are a simulator. Read from
# config/accounts.yaml::account_class at analysis time; this map only names the
# venue family each account belongs to.
VENUE_OF = {
    "bybit": "bybit_perps",
    "alpaca": "alpaca_equities",
    "interactive_brokers": "ibkr_futures",
    "breakout": "prop_breakout",
    "oanda": "oanda_fx",
}
SL_REASONS = {"sl", "sl_cross"}
TP_REASONS = {"tp", "tp_cross"}
MIN_N = 20  # below this a venue side is reported unmeasurable, not given a number


# ---------------------------------------------------------------- pull ----
def _curl_json(url: str) -> Any:
    tok = os.environ["DIAG_READ_TOKEN"]
    out = subprocess.run(
        ["curl", "-sS", "-f", "-m", "90", url, "-H", f"Authorization: Bearer {tok}"],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def pull(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    accounts = list(load_accounts_dict().keys())
    fills: Dict[str, dict] = {}
    pull_log: List[dict] = []

    def get(q: str) -> dict:
        return _curl_json(f"{API_BASE}/api/bot/pnl/exchange/fills?{q}")

    for acct in accounts:
        d = get(f"days={FILLS_DAYS}&limit=1000&account_id={acct}")
        pull_log.append({"account_id": acct, "symbol": None, "count": d["count"], "truncated": d["truncated"]})
        for f in d["fills"]:
            fills[f["exec_id"]] = f
        if d["truncated"]:
            for sym in sorted({f["symbol"] for f in d["fills"]}):
                e = get(f"days={FILLS_DAYS}&limit=1000&account_id={acct}&symbol={urllib.parse.quote(sym)}")
                pull_log.append({"account_id": acct, "symbol": sym, "count": e["count"], "truncated": e["truncated"]})
                for f in e["fills"]:
                    fills[f["exec_id"]] = f
    json.dump(list(fills.values()), open(out_dir / "fills.json", "w"))
    json.dump(pull_log, open(out_dir / "fills_pull_log.json", "w"), indent=1)

    for table in ("trades", "order_packages"):
        rows: Dict[str, dict] = {}
        off, total = 0, None
        while total is None or off < total:
            d = _curl_json(f"{API_BASE}/api/diag/journal?table={table}&limit=1000&offset={off}&envelope=true")
            total = d["total_rows"]
            key = "id" if table == "trades" else "order_package_id"
            for r in d["rows"]:
                rows[str(r[key])] = r
            off += 1000
            if not d["rows"]:
                break
        json.dump(list(rows.values()), open(out_dir / f"{table}.json", "w"))
    print(f"pulled {len(fills)} fills into {out_dir}")


# ------------------------------------------------------------- analyze ----
def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return v if v == v and v != 0.0 else None
    except (TypeError, ValueError):
        return None


def _notes(t: dict) -> dict:
    try:
        n = json.loads(t.get("notes") or "{}")
        return n if isinstance(n, dict) else {}
    except (TypeError, ValueError):
        return {}


def _adverse_bps(direction: str, side: str, fill: float, ref: float) -> float:
    """Positive = paid. `side` is 'entry' or 'exit'."""
    buying = (direction == "long") == (side == "entry")
    raw = (fill - ref) / ref * 1e4
    return raw if buying else -raw


def _dist(xs: List[float]) -> dict:
    if not xs:
        return {"n": 0}
    s = sorted(xs)

    def q(p: float) -> float:
        return round(s[min(len(s) - 1, int(round(p * (len(s) - 1))))], 3)

    # 5% two-sided trimmed mean: expectancy is linear in the MEAN cost, but
    # a single delayed/adopted entry can sit hundreds of bps from its package.
    k = int(len(s) * 0.05)
    trimmed = s[k:len(s) - k] if len(s) - 2 * k > 0 else s
    return {
        "n": len(s),
        "mean": round(statistics.fmean(s), 3),
        "trimmed_mean_5pct": round(statistics.fmean(trimmed), 3),
        "median": q(0.5), "p25": q(0.25), "p75": q(0.75), "p90": q(0.9),
        "min": round(s[0], 3), "max": round(s[-1], 3),
    }


def _boot_ci(entry: List[float], exit_: List[float], reps: int = 4000) -> List[float]:
    """95% bootstrap CI of mean(entry)+mean(exit); sides resampled independently
    (they are different trade populations). Fixed seed so a re-run reproduces."""
    import random

    rng = random.Random(20260924)
    ms = []
    for _ in range(reps):
        a = [entry[rng.randrange(len(entry))] for _ in entry]
        b = [exit_[rng.randrange(len(exit_))] for _ in exit_]
        ms.append(statistics.fmean(a) + statistics.fmean(b))
    ms.sort()
    return [round(ms[int(0.025 * reps)], 3), round(ms[int(0.975 * reps)], 3)]


def measure(in_dir: Path) -> dict:
    acfg = load_accounts_dict()
    fills = json.load(open(in_dir / "fills.json"))
    trades = json.load(open(in_dir / "trades.json"))
    pkgs = {p["order_package_id"]: p for p in json.load(open(in_dir / "order_packages.json"))}

    by_oid: Dict[str, List[dict]] = collections.defaultdict(list)
    for f in fills:
        if f.get("order_id"):
            by_oid[str(f["order_id"])].append(f)

    rows: List[dict] = []
    excl = collections.Counter()
    for t in trades:
        if t.get("is_backtest"):
            continue
        acct = t.get("account_id")
        if acct not in acfg:
            excl["unknown_account"] += 1
            continue
        direction = str(t.get("direction") or "").lower()
        if direction not in ("long", "short"):
            excl["no_direction"] += 1
            continue
        pkg = pkgs.get(t.get("order_package_id") or "")
        base = {
            "trade_id": t["id"], "account_id": acct,
            "account_class": acfg[acct].get("account_class"),
            "venue": VENUE_OF.get(acfg[acct].get("exchange"), acfg[acct].get("exchange")),
            "symbol": t.get("symbol"), "strategy": t.get("strategy_name"),
            "direction": direction, "created_at": t.get("created_at"),
        }
        # ENTRY
        ef = [f for f in by_oid.get(str(t.get("broker_order_id") or ""), [])
              if (f["side"].lower() == "buy") == (direction == "long")]
        ref = _f(pkg.get("entry")) if pkg else None
        if ef and ref:
            qty = sum(float(f["qty"]) for f in ef)
            vwap = sum(float(f["price"]) * float(f["qty"]) for f in ef) / qty
            rows.append({**base, "side": "entry", "ref": ref, "fill": round(vwap, 8),
                         "n_fills": len(ef), "fill_source": "exchange_fill",
                         "bps": round(_adverse_bps(direction, "entry", vwap, ref), 3)})
        elif ef and not ref:
            excl["entry_fill_no_package_entry"] += 1
        # EXIT
        if t.get("status") != "closed":
            continue
        src = _notes(t).get("exit_price_source")
        reason = str(t.get("exit_reason") or "")
        xp = _f(t.get("exit_price"))
        if classify(src, "exit_price_source") != MEASURED or xp is None:
            excl["exit_not_measured"] += 1
            continue
        if reason in SL_REASONS:
            key, tcol = "sl", "stop_loss"
        elif reason in TP_REASONS:
            key, tcol = "tp", "take_profit_1"
        else:
            excl[f"exit_measured_no_reference:{reason}"] += 1
            continue
        # Package level first. Pre-package-era rows (2026-05, no
        # order_package_id) fall back to the journal's own level, which agreed
        # with the package on 290 of 298 sl/tp closes where both exist (the 8
        # disagreements are sl: a trailed stop one side did not record).
        xref, ref_source = (_f(pkg.get(key)), "order_packages") if pkg else (None, None)
        if xref is None:
            xref, ref_source = _f(t.get(tcol)), "trades_column"
        if xref is None:
            excl["exit_no_reference_level"] += 1
            continue
        rows.append({**base, "side": "exit", "exit_reason": reason, "ref": xref, "fill": xp,
                     "fill_source": src, "ref_source": ref_source,
                     "bps": round(_adverse_bps(direction, "exit", xp, xref), 3)})

    # Per-account, then per-venue by class.
    per_account = {}
    for acct in sorted({r["account_id"] for r in rows}):
        rr = [r for r in rows if r["account_id"] == acct]
        e = [r["bps"] for r in rr if r["side"] == "entry"]
        x = [r["bps"] for r in rr if r["side"] == "exit"]
        xsl = [r["bps"] for r in rr if r["side"] == "exit" and r["exit_reason"] in SL_REASONS]
        xtp = [r["bps"] for r in rr if r["side"] == "exit" and r["exit_reason"] in TP_REASONS]
        per_account[acct] = {
            "account_class": rr[0]["account_class"], "venue": rr[0]["venue"],
            "window": [min(r["created_at"] for r in rr), max(r["created_at"] for r in rr)],
            "entry": _dist(e), "exit_all": _dist(x), "exit_sl": _dist(xsl), "exit_tp": _dist(xtp),
            "exit_all_package_ref_only": _dist([r["bps"] for r in rr if r["side"] == "exit"
                                                and r["ref_source"] == "order_packages"]),
            "exit_sl_package_ref_only": _dist([r["bps"] for r in rr if r["side"] == "exit"
                                               and r["ref_source"] == "order_packages"
                                               and r["exit_reason"] in SL_REASONS]),
            "exit_tp_package_ref_only": _dist([r["bps"] for r in rr if r["side"] == "exit"
                                               and r["ref_source"] == "order_packages"
                                               and r["exit_reason"] in TP_REASONS]),
            "exit_sources": dict(collections.Counter(r["fill_source"] for r in rr if r["side"] == "exit")),
        }
        # A round-trip estimate needs BOTH sides measured at MIN_N. The HEADLINE
        # exit side is package-referenced only: the journal-column fallback can
        # carry a pre-trail stop, which reads a trailed stop-out as a large
        # favourable fill (seen: -37.9 bps) and biases the mean LOW.
        # `roundtrip_incl_journal_ref` is the sensitivity with it included.
        ent = per_account[acct]["entry"]
        xpk = [r["bps"] for r in rr if r["side"] == "exit" and r["ref_source"] == "order_packages"]
        if ent["n"] >= MIN_N and len(xpk) >= MIN_N:
            ex = _dist(xpk)
            per_account[acct]["roundtrip_bps"] = {
                "mean": round(ent["mean"] + ex["mean"], 3),
                "trimmed_mean_5pct": round(ent["trimmed_mean_5pct"] + ex["trimmed_mean_5pct"], 3),
                "median_sum_of_sides": round(ent["median"] + ex["median"], 3),
                "mean_bootstrap_ci95": _boot_ci(e, xpk),
            }
            xall = per_account[acct]["exit_all"]
            per_account[acct]["roundtrip_incl_journal_ref"] = {
                "mean": round(ent["mean"] + xall["mean"], 3),
                "trimmed_mean_5pct": round(ent["trimmed_mean_5pct"] + xall["trimmed_mean_5pct"], 3),
                "mean_bootstrap_ci95": _boot_ci(e, x),
            }
            per_account[acct]["roundtrip_state"] = "measured"
        else:
            per_account[acct]["roundtrip_bps"] = None
            per_account[acct]["roundtrip_state"] = (
                f"unmeasurable: entry n={ent['n']}, package-referenced exit n={len(xpk)} "
                f"(need >= {MIN_N} each)")
    return {"rows": rows, "per_account": per_account, "excluded": dict(excl)}


# ----------------------------------------------------------- re-price ----
def _venue_of_symbol(sym: str) -> str:
    s = str(sym or "").upper()
    if s.endswith("USDT"):
        return "bybit_perps"
    if s in ("MES", "MGC", "MHG", "MNQ", "MCL"):
        return "ibkr_futures"
    return "alpaca_equities"


def reprice(slip_by_venue: Dict[str, Optional[float]]) -> List[dict]:
    """Re-price each Stage-0 record at a new round-trip slippage.

    Exact where the record's source_run rows carry ``cost_slippage_r`` (which is
    linear in bps: slippage_r = bps/1e4 · avg_price / risk). The positive control
    is that sum(net_r) over those rows reproduces the record's ``net_r_oos`` at
    its recorded 5.0 bps before anything is changed; a record that fails it is
    not re-priced.
    """
    out = []
    for p in sorted(glob.glob(str(REPO / "comms/strategy_evidence/*.json"))):
        d = json.load(open(p))
        leg = Path(p).stem
        cs = d.get("cost_stack") or {}
        rec = {"leg": leg, "symbol": d.get("symbol"), "coverage_state": d.get("coverage_state"),
               "venue": _venue_of_symbol(d.get("symbol")),
               "old_verdict": (d.get("decision_rule") or {}).get("verdict"),
               "old_net_r_oos": d.get("net_r_oos"), "n_trades_oos": d.get("n_trades_oos"),
               "old_slippage_bps": cs.get("slippage")}
        new_bps = slip_by_venue.get(rec["venue"])
        rec["new_slippage_bps"] = new_bps
        if d.get("coverage_state") != "measured" or d.get("net_r_oos") is None:
            rec["method"] = "not_repriced: no measured net_r_oos"
            out.append(rec)
            continue
        if new_bps is None:
            rec["method"] = "not_repriced: venue slippage unmeasurable"
            out.append(rec)
            continue
        old_bps = float(cs.get("slippage") or 0)
        sr = d.get("source_run") or ""
        srp = REPO / sr
        if sr and srp.exists():
            trs = [json.loads(line) for line in open(srp) if line.strip()]
            ctrl = round(sum(float(r["net_r"]) for r in trs), 4)
            rec["positive_control"] = {"sum_net_r_at_recorded_bps": ctrl,
                                       "record_net_r_oos": d["net_r_oos"],
                                       "reproduced": abs(ctrl - float(d["net_r_oos"])) < 1e-3}
            if not rec["positive_control"]["reproduced"] or old_bps <= 0 or not all(
                    "cost_slippage_r" in r for r in trs):
                rec["method"] = "not_repriced: positive control failed or no cost_slippage_r"
                out.append(rec)
                continue
            slip_r = sum(float(r["cost_slippage_r"]) for r in trs)
            # Second control: every row must satisfy net_r_fee_only = net_r +
            # slippage_r + funding_r, i.e. the cost terms are what they claim.
            if all("net_r_fee_only" in r and "cost_funding_r" in r for r in trs):
                resid = sum(float(r["net_r_fee_only"]) - float(r["net_r"]) - float(r["cost_slippage_r"])
                            - float(r["cost_funding_r"]) for r in trs)
                rec["positive_control"]["fee_only_identity_residual_r"] = round(resid, 4)
            # net_r already has slip_r SUBTRACTED: add it back, charge the new one.
            new = ctrl + slip_r - slip_r * (new_bps / old_bps)
            rec["method"] = "exact: per-trade cost_slippage_r rescaled"
            rec["slippage_r_total_at_old"] = round(slip_r, 4)
            rec["breakeven_slippage_bps"] = (round(old_bps * (ctrl + slip_r) / slip_r, 2)
                                             if slip_r > 0 else None)
        else:
            # No durable per-trade rows (source_run points at a deleted tmp dir).
            # net_r_oos_fee_only − net_r_oos = slippage_r + funding_r. With funding
            # 0 (equities, futures) that difference IS the slippage term, so the
            # rescale is exact; for perps it is an UPPER bound on slippage_r and
            # the result is a bound, labelled as such.
            fo = d.get("net_r_oos_fee_only")
            if fo is None:
                rec["method"] = "not_repriced: no per-trade rows and no fee-only arm"
                out.append(rec)
                continue
            diff = float(fo) - float(d["net_r_oos"])
            fund = float(cs.get("funding") or 0)
            net0 = float(d["net_r_oos"])
            rec["slippage_plus_funding_r_total_at_old"] = round(diff, 4)
            if fund == 0:
                new = net0 + diff - diff * (new_bps / old_bps)
                rec["method"] = "exact: fee-only arm difference (funding 0) rescaled"
                rec["breakeven_slippage_bps"] = (round(old_bps * (net0 + diff) / diff, 2)
                                                 if diff > 0 else None)
            else:
                # slippage_r is somewhere in (0, diff]; the answer is an interval.
                ends = sorted([net0, net0 + diff * (1 - new_bps / old_bps)])
                rec["new_net_r_oos_interval"] = [round(ends[0], 4), round(ends[1], 4)]
                v = {("pass" if e > 0 else "fail") for e in ends}
                if len(v) == 2:
                    rec["method"] = ("interval: no per-trade rows; fee-only diff = slippage+funding, "
                                     "split unknown, and the interval straddles 0 -> undetermined")
                    rec["new_verdict"] = "undetermined"
                    rec["flipped"] = None
                    out.append(rec)
                    continue
                new = ends[0] if new_bps >= old_bps else ends[1]
                rec["method"] = ("interval: no per-trade rows; fee-only diff = slippage+funding, "
                                 "split unknown; both ends give the same verdict")
        rec["new_net_r_oos"] = round(new, 4)
        rec["new_verdict"] = "pass" if new > 0 else "fail"
        rec["flipped"] = rec["new_verdict"] != rec["old_verdict"]
        n = d.get("n_trades_oos") or 0
        rec["old_expectancy_r"] = round(float(d["net_r_oos"]) / n, 4) if n else None
        rec["new_expectancy_r"] = round(new / n, 4) if n else None
        out.append(rec)
    return out


def analyze(in_dir: Path, out: Path) -> None:
    m = measure(in_dir)
    pa = m["per_account"]
    # REAL-market venues only feed the re-price. Paper/demo fills measure a
    # simulator's fill model, not a market, and are reported beside, not used.
    venue_bps: Dict[str, Optional[float]] = {v: None for v in set(VENUE_OF.values())}
    basis: Dict[str, str] = {}
    ci_hi: Dict[str, Optional[float]] = {v: None for v in venue_bps}
    for acct, s in pa.items():
        if s["account_class"] == "real_money" and s["roundtrip_bps"]:
            venue_bps[s["venue"]] = s["roundtrip_bps"]["mean"]
            ci_hi[s["venue"]] = s["roundtrip_bps"]["mean_bootstrap_ci95"][1]
            basis[s["venue"]] = (f"{acct} (real money): entry mean + package-referenced exit mean; "
                                 f"95% CI {s['roundtrip_bps']['mean_bootstrap_ci95']}")
    for v in venue_bps:
        basis.setdefault(v, "unmeasurable: no real-money account with both sides at n>=%d" % MIN_N)
    # Point estimate, and the pessimistic end of its 95% CI. A negative point
    # estimate would CREDIT price improvement into the harness; it is used as
    # measured, but the CI-upper scenario is the one a promotion should read.
    scenarios = {"measured_real_mean": venue_bps, "measured_real_ci95_upper": ci_hi}
    repriced = {k: reprice(v) for k, v in scenarios.items()}
    result = {
        "question": ("D3: realized round-trip slippage per venue from MEASURED fills; "
                     "Stage-0 verdict flips under RULE-D1-STAGE0-NET-OF-FULL-COST at that value"),
        "generated_by": "scripts/research/realized_slippage.py",
        "reference_price": {
            "entry": "order_packages.entry (intended entry at submit) vs VWAP of exchange_fills on trades.broker_order_id",
            "exit": "order_packages.sl (sl/sl_cross) or .tp (tp/tp_cross), falling back to trades.stop_loss/take_profit_1 for pre-package rows (ref_source says which), vs trades.exit_price where exit_price_source is MEASURED",
            "sign": "positive bps = adverse (paid)",
        },
        "source_query": {
            "fills": f"GET {API_BASE}/api/bot/pnl/exchange/fills?days={FILLS_DAYS}&limit=1000&account_id=<acct>[&symbol=<sym> when truncated]",
            "trades": f"GET {API_BASE}/api/diag/journal?table=trades&limit=1000&offset=<k*1000>&envelope=true",
            "order_packages": f"GET {API_BASE}/api/diag/journal?table=order_packages&limit=1000&offset=<k*1000>&envelope=true",
            "rerun": "python3 scripts/research/realized_slippage.py pull --out-dir D && python3 scripts/research/realized_slippage.py analyze --in-dir D --out <json>",
        },
        "min_n_per_side": MIN_N,
        "per_account": pa,
        "excluded_counts": m["excluded"],
        "venue_roundtrip_bps_used": venue_bps,
        "venue_basis": basis,
        "repriced": repriced,
        "flip_summary": {
            k: {"flipped": [f'{r["leg"]}: {r["old_verdict"]}->{r["new_verdict"]}' for r in v if r.get("flipped")],
                "undetermined": [r["leg"] for r in v if r.get("new_verdict") == "undetermined"],
                "n_repriced": sum(1 for r in v if r.get("new_verdict") in ("pass", "fail")),
                "n_not_repriced": sum(1 for r in v if "new_verdict" not in r)}
            for k, v in repriced.items()
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump(result, open(out, "w"), indent=1, sort_keys=False)
    rows_path = out.with_name(out.stem + "__rows.jsonl")
    with open(rows_path, "w") as fh:
        for r in m["rows"]:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    print(json.dumps({k: result[k] for k in ("venue_roundtrip_bps_used", "flip_summary")}, indent=1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull")
    p.add_argument("--out-dir", required=True, type=Path)
    a = sub.add_parser("analyze")
    a.add_argument("--in-dir", required=True, type=Path)
    a.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    if args.cmd == "pull":
        pull(args.out_dir)
    else:
        analyze(args.in_dir, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
