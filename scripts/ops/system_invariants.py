#!/usr/bin/env python3
"""Executable SYSTEM INVARIANTS — assert OUTCOMES against broker/journal ground
truth, not against the system's own verdicts.

WHY THIS EXISTS
---------------
Five full-system audits (2026-06-28 → 2026-08-04) ran on two axes — does
everything agree with everything (consistency), and is everything reachable
(liveness) — plus a per-line read sweep. All three are satisfiable by a
comprehensively broken system.

The case that proves it: ``IBClient.protection_coverage`` graded a resting stop
and a resting take-profit with ONE membership test, so a stop-only position
reported *fully covered*. It landed 2026-07-26 (#7641), survived the 07-31 and
08-04 audits, and was caught 2026-08-16 only when ``/api/diag/ib_open_orders``
gave someone a way to CONTRADICT the reduced verdict. MGC 4487 then sat 122.74
points past its declared target for 11 days.

The lesson generalises: **when the auditor's instrument is the audited system's
own summariser, a broken summariser produces a clean audit.** So every check
here consumes the least-reduced surface available (order ROWS, position ROWS)
and recomputes the verdict itself.

THREE-STATE VERDICTS, NEVER COLLAPSED
-------------------------------------
Each invariant returns ``pass`` / ``fail`` / ``not_measured``. ``not_measured``
means *we could not look* — an absent payload, a ``could_not_look`` read_state,
a null count. It is emphatically NOT a pass, and conflating the two is the
exact defect class ``collapsed-state-guard`` exists for. A suite that reports
"0 violations" over a population it never read is the "green that checked
nothing" this repo already has a rule about.

ALWAYS STATE THE POPULATION
---------------------------
Every result carries ``population`` (what was examined) and ``n``. A violation
count with no denominator is not evidence.

THE SELF-TEST IS PART OF THE CONTRACT
-------------------------------------
``--self-test`` plants, for every invariant, (a) a known-BAD fixture that must
FAIL, (b) a known-GOOD fixture that must PASS, and (c) an ABSENT fixture that
must return ``not_measured``. A check that cannot be shown to fail is not
evidence of anything. This mirrors ``scripts/ci/guard_selftests.py``, which
covers 10 of the repo's 41 guards.

USAGE
    python3 scripts/ops/system_invariants.py --self-test
    python3 scripts/ops/system_invariants.py --payloads <dir-of-diag-json>
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]

PASS = "pass"
FAIL = "fail"
NOT_MEASURED = "not_measured"


# ---------------------------------------------------------------- classifier
def _protective_leg_side(order_type: Optional[str]) -> Optional[str]:
    """Canonical stop/target classifier.

    Imported from ``src.units.accounts.ib_client`` when importable so this file
    can never drift from the live rule; the inline copy below is a byte-faithful
    fallback for a sandbox without the runtime deps installed. Re-deriving this
    per-probe is what produced two independently-wrong probes on one day
    (``scripts/ml/_regime_score_semantics.py`` documents the same lesson).

    Order matters: the STOP family is tested first because ``"STP LMT"``
    contains ``"LMT"``.
    """
    t = str(order_type or "").strip().upper()
    if not t:
        return None
    if "TRAIL" in t or t.startswith("STP") or t in ("STOP", "STOP LIMIT"):
        return "stop"
    if "LMT" in t or t == "LIMIT":
        return "target"
    return None


try:  # pragma: no cover - import path depends on host deps
    sys.path.insert(0, str(REPO))
    from src.units.accounts.ib_client import (  # type: ignore  # noqa: E402
        _protective_leg_side as _canonical_leg_side,
    )
    _protective_leg_side = _canonical_leg_side  # type: ignore[assignment]
    _LEG_SIDE_SOURCE = "src.units.accounts.ib_client"
except Exception:
    _LEG_SIDE_SOURCE = "inline-fallback"


def _root(sym: str) -> str:
    """Futures root from a symbol or local symbol.

    IB reports ``symbol`` as the root ("MHG") and ``local_symbol`` with the
    contract month ("MHGU6"). Stripping trailing month+year digits/letters is
    unreliable across products, so callers must pass the ROOT field; this only
    normalises case and strips non-alphanumerics.
    """
    return "".join(ch for ch in str(sym or "").upper() if ch.isalnum())


class Result:
    def __init__(self, verdict: str, population: str, n: int,
                 violations: Optional[List[str]] = None, detail: str = "") -> None:
        self.verdict = verdict
        self.population = population
        self.n = n
        self.violations = violations or []
        self.detail = detail

    def as_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "population": self.population,
            "n": self.n,
            "violation_count": len(self.violations),
            "violations": self.violations,
            "detail": self.detail,
        }


def _num(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------- IB protection
def _ib_leg_index(ib_payload: Any):
    """Return (index, accounts_read, accounts_could_not_look).

    index: {(account, symbol_root): {"stop_by_oca": {group: qty}, "target": qty}}

    Reads ``read_state`` rather than inferring from an empty list — ``[]`` after
    ``orders_read`` genuinely means the account holds nothing, while ``[]``
    after ``could_not_look`` means we never asked. Those are opposite facts.
    """
    idx: Dict[Any, Dict[str, Any]] = defaultdict(
        lambda: {"stop_by_oca": defaultdict(float), "target": 0.0, "ungrouped_stop": 0.0}
    )
    read, blind = [], []
    accounts = (ib_payload or {}).get("accounts") or []
    for acct in accounts:
        state = acct.get("read_state")
        aid = acct.get("account_id")
        if state == "orders_read":
            read.append(aid)
        elif state == "could_not_look":
            blind.append(aid)
            continue
        else:  # not_ib — nothing to read, not a failure
            continue
        for o in acct.get("orders") or []:
            side = _protective_leg_side(o.get("order_type"))
            if side is None:
                continue
            qty = _num(o.get("total_quantity"))
            if qty is None:
                continue
            # `symbol` is the ROOT ("MHG"); `local_symbol` carries the contract
            # month ("MHGU6"). Keying on local_symbol made every IB future read
            # naked on the first live run of this suite — a FALSE POSITIVE the
            # planted control below now catches. Root first, local as fallback.
            sym = str(o.get("symbol") or o.get("local_symbol") or "").upper()
            root = _root(sym)
            key = (aid, root)
            if side == "stop":
                grp = str(o.get("oca_group") or "")
                if grp:
                    # within one OCA group legs protect the SAME qty -> max
                    cur = idx[key]["stop_by_oca"][grp]
                    idx[key]["stop_by_oca"][grp] = max(cur, qty)
                else:
                    idx[key]["ungrouped_stop"] += qty
            else:
                idx[key]["target"] += qty
    return idx, read, blind


def _ib_positions(pos_payload: Any) -> List[Dict[str, Any]]:
    """Open IB positions from /api/bot/positions, keyed for the leg index."""
    rows = pos_payload if isinstance(pos_payload, list) else (pos_payload or {}).get("positions") or []
    out = []
    for p in rows:
        acct = str(p.get("account") or "")
        if not acct.startswith("ib_"):
            continue
        sym = str(p.get("symbol") or "").upper()
        root = _root(sym)
        out.append({
            "account": acct,
            "symbol": sym,
            "root": root,
            "qty": abs(_num(p.get("qty")) or 0.0),
            "take_profit": _num(p.get("takeProfit")),
            "stop_loss": _num(p.get("stopLoss")),
            "id": p.get("id"),
        })
    return out


def inv_stop_covers_position(ctx) -> Result:
    """Every open IB position must have resting STOP quantity >= its size."""
    ib, pos = ctx.get("ib_open_orders"), ctx.get("positions")
    if ib is None or pos is None:
        return Result(NOT_MEASURED, "IB open positions", 0,
                      detail="ib_open_orders and/or positions payload absent")
    idx, read, blind = _ib_leg_index(ib)
    rows = _ib_positions(pos)
    rows = [r for r in rows if r["account"] in read]
    if not rows:
        return Result(NOT_MEASURED, "IB open positions on cleanly-read accounts", 0,
                      detail=f"no IB rows on an orders_read account (blind: {blind})")
    viol = []
    for r in rows:
        e = idx.get((r["account"], r["root"]))
        stop = 0.0
        if e:
            stop = sum(e["stop_by_oca"].values()) + e["ungrouped_stop"]
        if stop + 1e-9 < r["qty"]:
            viol.append(f"{r['account']}/{r['symbol']} trade {r['id']}: "
                        f"position {r['qty']:g} vs resting stop {stop:g} — NAKED by {r['qty']-stop:g}")
    return Result(FAIL if viol else PASS, "IB open positions on cleanly-read accounts",
                  len(rows), viol, detail=f"blind accounts (not counted): {blind}")


def inv_stop_not_over_covering(ctx) -> Result:
    """Resting STOP quantity must not EXCEED the position size.

    Two disjoint OCA stop groups over one long mean either fill flattens the
    position and leaves the other resting, which then SELLS AGAIN into a naked
    short (BL-20260816-IB-STOPS-OVER-COVER-IN-DISJOINT-OCA-GROUPS).
    """
    ib, pos = ctx.get("ib_open_orders"), ctx.get("positions")
    if ib is None or pos is None:
        return Result(NOT_MEASURED, "IB open positions", 0, detail="payload absent")
    idx, read, blind = _ib_leg_index(ib)
    rows = [r for r in _ib_positions(pos) if r["account"] in read]
    if not rows:
        return Result(NOT_MEASURED, "IB open positions on cleanly-read accounts", 0,
                      detail=f"no IB rows on an orders_read account (blind: {blind})")
    viol = []
    for r in rows:
        e = idx.get((r["account"], r["root"]))
        if not e:
            continue
        stop = sum(e["stop_by_oca"].values()) + e["ungrouped_stop"]
        if stop > r["qty"] + 1e-9:
            groups = len(e["stop_by_oca"]) + (1 if e["ungrouped_stop"] else 0)
            viol.append(f"{r['account']}/{r['symbol']} trade {r['id']}: "
                        f"position {r['qty']:g} vs resting stop {stop:g} across {groups} group(s) "
                        f"— OVER-COVERED by {stop-r['qty']:g}")
    return Result(FAIL if viol else PASS, "IB open positions on cleanly-read accounts",
                  len(rows), viol, detail=f"blind accounts (not counted): {blind}")


def inv_declared_target_rests(ctx) -> Result:
    """A position declaring take_profit must have a resting TARGET leg.

    This is the invariant the one-sided protection_coverage could not express:
    a stop-only book graded `covered`, so MGC 4487 ran 11 days past its target.
    """
    ib, pos = ctx.get("ib_open_orders"), ctx.get("positions")
    if ib is None or pos is None:
        return Result(NOT_MEASURED, "IB positions declaring a take_profit", 0,
                      detail="payload absent")
    idx, read, blind = _ib_leg_index(ib)
    rows = [r for r in _ib_positions(pos)
            if r["account"] in read and r["take_profit"] is not None and r["take_profit"] > 0]
    if not rows:
        return Result(NOT_MEASURED, "IB positions declaring a take_profit", 0,
                      detail=f"no such row on an orders_read account (blind: {blind})")
    viol = []
    for r in rows:
        e = idx.get((r["account"], r["root"]))
        tgt = e["target"] if e else 0.0
        if tgt + 1e-9 < r["qty"]:
            viol.append(f"{r['account']}/{r['symbol']} trade {r['id']}: declares tp "
                        f"{r['take_profit']:g} but resting target qty {tgt:g} < position {r['qty']:g}")
    return Result(FAIL if viol else PASS, "IB positions declaring a take_profit",
                  len(rows), viol, detail=f"blind accounts (not counted): {blind}")


def inv_journal_matches_exchange(ctx) -> Result:
    """Journal open qty per (account, symbol) must reconcile to exchange size."""
    ex, pos = ctx.get("exchange_positions"), ctx.get("positions")
    if ex is None or pos is None:
        return Result(NOT_MEASURED, "(account, symbol) pairs", 0, detail="payload absent")
    exch: Dict[Any, float] = {}
    blind: List[Any] = []
    readable: set = set()
    for acct in (ex or {}).get("accounts") or []:
        aid = acct.get("account_id")
        plist = acct.get("positions")
        if plist is None:          # could-not-read — NOT flat
            blind.append(aid)
            continue
        # An account PRESENT with a list was genuinely read; [] means flat.
        # An account ABSENT from the payload entirely was never read at all,
        # and treating its journal rows as "exchange 0" would manufacture a
        # violation per row. This check had exactly that bug on its first live
        # run: a truncated payload produced 22 phantom divergences.
        readable.add(aid)
        for p in plist:
            sym = str(p.get("symbol") or "").upper().replace("/", "").replace(":USDT", "")
            exch[(aid, sym)] = exch.get((aid, sym), 0.0) + abs(_num(p.get("size")) or 0.0)
    jr: Dict[Any, float] = {}
    rows = pos if isinstance(pos, list) else (pos or {}).get("positions") or []
    for p in rows:
        aid = p.get("account")
        if aid not in readable:      # blind OR absent from the payload
            continue
        sym = str(p.get("symbol") or "").upper().replace("/", "").replace(":USDT", "")
        jr[(aid, sym)] = jr.get((aid, sym), 0.0) + abs(_num(p.get("qty")) or 0.0)
    # Grade only accounts observed on BOTH sides. An account readable on the
    # exchange but carrying no journal row is ambiguous — a genuine orphan, or
    # a truncated journal payload — and asserting a divergence on it would be
    # the same absent-vs-flat collapse. Orphan detection needs a payload known
    # to be complete and is a separate invariant, not this one.
    pos_accounts = {p.get("account") for p in rows}
    paired = readable & pos_accounts
    unpaired = sorted(str(a) for a in (readable - pos_accounts))
    keys = {k for k in (set(exch) | set(jr)) if k[0] in paired}
    if not keys:
        return Result(NOT_MEASURED, "(account, symbol) pairs observed on BOTH sides", 0,
                      detail=f"no account observed on both sides "
                             f"(could-not-read: {blind} · exchange-only: {unpaired})")
    viol = []
    for k in sorted(keys, key=str):
        j, e = jr.get(k, 0.0), exch.get(k, 0.0)
        if e <= 0 and j <= 0:
            continue
        denom = max(e, j, 1e-9)
        if abs(j - e) / denom > 0.02:      # 2% tolerance for rounding/fees
            ratio = (j / e) if e else float("inf")
            viol.append(f"{k[0]}/{k[1]}: journal {j:g} vs exchange {e:g} (ratio {ratio:.2f}x)")
    unread = sorted({str(p.get("account")) for p in rows} - readable)
    return Result(FAIL if viol else PASS, "(account, symbol) pairs observed on BOTH sides",
                  len(keys), viol,
                  detail=f"could-not-read: {blind} · journal-only (excluded): {unread} "
                         f"· exchange-only (excluded): {unpaired}")


def inv_exit_loop_meets_requirement(ctx) -> Result:
    """The decoupled exit loop must meet its 60s re-evaluation requirement.

    Reads ``requirement_state`` BESIDE ``intervals_measured``: the grade is
    per-process, so ``within`` on a tiny n can mean "no process lived long
    enough to draw the tail", not "the requirement was met".
    """
    h = ctx.get("exit_loop_health")
    if not h:
        return Result(NOT_MEASURED, "exit-loop passes", 0, detail="payload absent")
    body = h.get("content") if isinstance(h, dict) and "content" in h else h
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            return Result(NOT_MEASURED, "exit-loop passes", 0, detail="unparseable payload")
    state = (body or {}).get("requirement_state")
    n = int((body or {}).get("intervals_measured") or 0)
    mx = _num((body or {}).get("max_interval_ms"))
    if state in (None, "unknown", "not_measured"):
        return Result(NOT_MEASURED, "exit-loop inter-evaluation intervals", n,
                      detail=f"requirement_state={state!r} (n={n})")
    if state == "breached":
        return Result(FAIL, "exit-loop inter-evaluation intervals", n,
                      [f"requirement_state=breached, max_interval={mx}ms over n={n}"])
    if n < 30:
        return Result(NOT_MEASURED, "exit-loop inter-evaluation intervals", n,
                      detail=f"within, but n={n} is too small to have drawn the tail")
    return Result(PASS, "exit-loop inter-evaluation intervals", n,
                  detail=f"within; max_interval={mx}ms over n={n}")


def inv_count_null_when_blind(ctx) -> Result:
    """A blind read must report ``count: null``, never ``0``.

    ``0`` there is the collapse the three-state read_state exists to prevent —
    "we could not look" rendered identically to "the account holds nothing".
    """
    ib = ctx.get("ib_open_orders")
    if ib is None:
        return Result(NOT_MEASURED, "IB account read_states", 0, detail="payload absent")
    accts = (ib or {}).get("accounts") or []
    if not accts:
        return Result(NOT_MEASURED, "IB account read_states", 0, detail="no accounts in payload")
    viol = []
    for a in accts:
        if a.get("read_state") == "could_not_look" and a.get("count") == 0:
            viol.append(f"{a.get('account_id')}: read_state=could_not_look but count=0 "
                        f"(must be null — a 0 reads as 'holds nothing')")
    return Result(FAIL if viol else PASS, "IB account read_states", len(accts), viol)



def inv_no_netted_duplicate_upnl(ctx) -> Result:
    """Sibling journal rows on one netted position must not each carry the
    WHOLE position's unrealised PnL.

    This is `netted_duplicate_unattributed` (src/runtime/provenance.py) — a
    defect this repo already found, named, and fixed on the REALISED side
    (BL-20260806, writer fix `order_monitor._prorate_netted_broker_pnl`,
    history marked by `scripts/ops/mark_netted_duplicate_pnl.py`). The
    UNREALISED sibling surface was never swept.

    Detection is arithmetic and needs no code reading: two rows of the same
    (account, symbol) with DIFFERENT quantities and DIFFERENT entry prices
    cannot have the SAME unrealised PnL. If they do, at least one value is not
    that row's PnL and any consumer summing them double-counts.
    """
    pos = ctx.get("positions")
    if pos is None:
        return Result(NOT_MEASURED, "netted (account, symbol) groups", 0,
                      detail="positions payload absent")
    rows = pos if isinstance(pos, list) else (pos or {}).get("positions") or []
    groups: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[(r.get("account"), str(r.get("symbol") or "").upper())].append(r)
    multi = {k: v for k, v in groups.items() if len(v) > 1}
    if not multi:
        return Result(NOT_MEASURED, "netted (account, symbol) groups", 0,
                      detail="no symbol carries >1 open journal row — nothing to test")
    viol = []
    for k, v in sorted(multi.items(), key=str):
        ups = [_num(x.get("unrealizedPnl")) for x in v]
        qtys = [_num(x.get("qty")) for x in v]
        if any(u is None for u in ups):
            continue
        if len(set(ups)) == 1 and len(set(qtys)) > 1:
            viol.append(
                f"{k[0]}/{k[1]}: {len(v)} rows "
                f"{[(x.get('id'), q, u) for x, q, u in zip(v, qtys, ups)]} "
                f"— identical uPnL {ups[0]:g} on differing quantities; "
                f"consumer sum = {sum(ups):g} (double-counts the position)")
    return Result(FAIL if viol else PASS, "netted (account, symbol) groups",
                  len(multi), viol)


INVARIANTS: List[Dict[str, Any]] = [
    {"id": "INV-PROTECT-STOP", "blast": "money-at-risk",
     "q": "Does every open IB position have a resting stop covering its size?",
     "fn": inv_stop_covers_position},
    {"id": "INV-PROTECT-OVERCOVER", "blast": "money-at-risk",
     "q": "Is resting stop quantity bounded ABOVE by the position size?",
     "fn": inv_stop_not_over_covering},
    {"id": "INV-PROTECT-TARGET", "blast": "money-at-risk",
     "q": "Does a position declaring a take_profit have a resting target?",
     "fn": inv_declared_target_rests},
    {"id": "INV-JOURNAL-EXCHANGE", "blast": "accounting",
     "q": "Does journal open qty reconcile to exchange position size?",
     "fn": inv_journal_matches_exchange},
    {"id": "INV-NETTED-DUP-UPNL", "blast": "accounting",
     "q": "Do sibling rows on one netted position each carry the whole uPnL?",
     "fn": inv_no_netted_duplicate_upnl},
    {"id": "INV-EXIT-INTERVAL", "blast": "money-at-risk",
     "q": "Is the exit loop meeting its 60s re-evaluation requirement?",
     "fn": inv_exit_loop_meets_requirement},
    {"id": "INV-BLIND-COUNT-NULL", "blast": "observability",
     "q": "Does a blind read report count=null rather than 0?",
     "fn": inv_count_null_when_blind},
]


# ------------------------------------------------------------------ fixtures
def _fx_good() -> Dict[str, Any]:
    return {
        "ib_open_orders": {"accounts": [
            {"account_id": "ib_paper", "read_state": "orders_read", "count": 2, "orders": [
                {"symbol": "MGC", "order_type": "STP", "total_quantity": 105, "oca_group": "g1"},
                {"symbol": "MGC", "order_type": "LMT", "total_quantity": 105, "oca_group": "g1"},
            ]}]},
        "positions": [
            {"id": "4487", "account": "ib_paper", "symbol": "MGC",
             "qty": 105, "stopLoss": 3000.0, "takeProfit": 3400.0},
            # correctly PRORATED siblings: identical per-unit delta, sums to one
            # position figure (the live bybit_1 AVAXUSDT shape)
            {"id": "4817", "account": "bybit_1", "symbol": "AVAXUSDT",
             "qty": 822.9, "entryPrice": 6.755, "unrealizedPnl": 10.90},
            {"id": "4795", "account": "bybit_1", "symbol": "AVAXUSDT",
             "qty": 5508.1, "entryPrice": 6.58, "unrealizedPnl": 72.97},
        ],
        "exchange_positions": {"accounts": [
            {"account_id": "ib_paper", "positions": [{"symbol": "MGC", "size": 105}]},
            # the prorated siblings reconcile exactly: 822.9 + 5508.1 = 6331
            {"account_id": "bybit_1",
             "positions": [{"symbol": "AVAX/USDT:USDT", "size": 6331.0}]}]},
        "exit_loop_health": {"requirement_state": "within", "intervals_measured": 694,
                             "max_interval_ms": 41500},
    }


def _fx_bad() -> Dict[str, Any]:
    """The real 2026-08-16 shape: a full stop, ZERO targets, doubled stop qty."""
    return {
        "ib_open_orders": {"accounts": [
            {"account_id": "ib_paper", "read_state": "orders_read", "count": 2, "orders": [
                {"symbol": "MES", "order_type": "STP", "total_quantity": 15, "oca_group": "g1"},
                {"symbol": "MES", "order_type": "STP", "total_quantity": 15, "oca_group": "g2"},
            ]},
            {"account_id": "ib_live", "read_state": "could_not_look", "count": 0, "orders": None},
        ]},
        "positions": [
            {"id": "9001", "account": "ib_paper", "symbol": "MES",
             "qty": 15, "stopLoss": 5000.0, "takeProfit": 5400.0},
                {"id": "4816", "account": "bybit_1", "symbol": "SOLUSDT",
             "qty": 1409.4, "entryPrice": 84.54, "unrealizedPnl": 255.16},
            {"id": "4810", "account": "bybit_1", "symbol": "SOLUSDT",
             "qty": 367.8, "entryPrice": 85.14, "unrealizedPnl": 255.16},
        ],
        "exchange_positions": {"accounts": [
            {"account_id": "bybit_1", "positions": [{"symbol": "SOL/USDT:USDT", "size": 4.6}]}]},
        "exit_loop_health": {"requirement_state": "breached", "intervals_measured": 694,
                             "max_interval_ms": 61040},
    }


def _fx_naked() -> Dict[str, Any]:
    fx = _fx_good()
    fx["ib_open_orders"]["accounts"][0]["orders"] = []
    fx["ib_open_orders"]["accounts"][0]["count"] = 0
    return fx


def _self_test() -> int:
    checks: List[tuple] = []

    def expect(name: str, inv_id: str, ctx: Dict[str, Any], want: str):
        inv = next(i for i in INVARIANTS if i["id"] == inv_id)
        got = inv["fn"](ctx).verdict
        checks.append((name, got == want, f"want {want}, got {got}"))

    good, bad, naked, absent = _fx_good(), _fx_bad(), _fx_naked(), {}

    # (a) known-BAD must FAIL
    expect("overcover detects 2 disjoint OCA stop groups", "INV-PROTECT-OVERCOVER", bad, FAIL)
    expect("target-naked detects stop-only book", "INV-PROTECT-TARGET", bad, FAIL)
    expect("journal-vs-exchange detects 451x divergence", "INV-JOURNAL-EXCHANGE", bad, FAIL)
    expect("exit-interval detects breach", "INV-EXIT-INTERVAL", bad, FAIL)
    expect("blind-count detects count=0 on could_not_look", "INV-BLIND-COUNT-NULL", bad, FAIL)
    expect("netted-dup detects identical uPnL on differing qty",
           "INV-NETTED-DUP-UPNL", bad, FAIL)
    expect("stop-coverage detects a fully naked position", "INV-PROTECT-STOP", naked, FAIL)

    # (b) known-GOOD must PASS  (a check that always fails is as useless as one
    #     that always passes)
    expect("stop-coverage passes a covered book", "INV-PROTECT-STOP", good, PASS)
    expect("overcover passes an exactly-sized book", "INV-PROTECT-OVERCOVER", good, PASS)
    expect("target passes when a target rests", "INV-PROTECT-TARGET", good, PASS)
    expect("journal-vs-exchange passes when reconciled", "INV-JOURNAL-EXCHANGE", good, PASS)
    expect("exit-interval passes within at adequate n", "INV-EXIT-INTERVAL", good, PASS)
    expect("blind-count passes a clean read", "INV-BLIND-COUNT-NULL", good, PASS)
    expect("netted-dup passes correctly-prorated siblings",
           "INV-NETTED-DUP-UPNL", good, PASS)

    # (c) ABSENT input must be not_measured, NEVER pass
    for inv in INVARIANTS:
        expect(f"{inv['id']} says not_measured on absent input", inv["id"], absent, NOT_MEASURED)

    # (c2) REGRESSION CONTROL — the false positive this suite produced on its
    #      own first live run. An IB future whose order rows carry a
    #      contract-month local_symbol ("MHGU6") must still match its position
    #      ("MHG"); keying on local_symbol reported all three IB futures naked.
    month_coded = {
        "ib_open_orders": {"accounts": [
            {"account_id": "ib_paper", "read_state": "orders_read", "count": 2, "orders": [
                {"symbol": "MHG", "local_symbol": "MHGU6", "order_type": "STP",
                 "total_quantity": 29, "oca_group": "g1"},
                {"symbol": "MHG", "local_symbol": "MHGU6", "order_type": "LMT",
                 "total_quantity": 29, "oca_group": "g1"},
            ]}]},
        "positions": [{"id": "t1", "account": "ib_paper", "symbol": "MHG",
                       "qty": 29, "stopLoss": 6.22, "takeProfit": 7.14}],
    }
    expect("month-coded local_symbol still matches its root (stop)",
           "INV-PROTECT-STOP", month_coded, PASS)
    expect("month-coded local_symbol still matches its root (target)",
           "INV-PROTECT-TARGET", month_coded, PASS)

    # (c3) REGRESSION CONTROL — a TRUNCATED exchange payload must not
    #      manufacture divergences. On this suite's first live run an
    #      exchange payload carrying only ib_paper made all 22 bybit/alpaca
    #      journal rows read as "exchange 0". Absent != flat.
    truncated = {
        "positions": [{"id": "x", "account": "bybit_1", "symbol": "SOLUSDT", "qty": 367.8}],
        "exchange_positions": {"accounts": [
            {"account_id": "ib_paper", "positions": [{"symbol": "MGC", "size": 95}]}]},
    }
    expect("truncated exchange payload does not manufacture divergence",
           "INV-JOURNAL-EXCHANGE", truncated, NOT_MEASURED)

    # (d) the classifier's stop-family-first rule
    checks.append(("STP LMT classifies as stop, not target",
                   _protective_leg_side("STP LMT") == "stop",
                   f"got {_protective_leg_side('STP LMT')}"))
    checks.append(("LMT classifies as target",
                   _protective_leg_side("LMT") == "target",
                   f"got {_protective_leg_side('LMT')}"))
    checks.append(("MKT is not protective",
                   _protective_leg_side("MKT") is None,
                   f"got {_protective_leg_side('MKT')}"))

    # (e) small-n must NOT read as a pass
    small = _fx_good()
    small["exit_loop_health"] = {"requirement_state": "within",
                                 "intervals_measured": 4, "max_interval_ms": 20000}
    expect("exit-interval refuses to pass on n=4", "INV-EXIT-INTERVAL", small, NOT_MEASURED)

    ok = sum(1 for _, good_, _ in checks if good_)
    for name, good_, why in checks:
        if not good_:
            print(f"  FAIL  {name}: {why}")
    print(f"leg-side classifier source: {_LEG_SIDE_SOURCE}")
    print(f"self-test: {ok}/{len(checks)} passed")
    return 0 if ok == len(checks) else 1


def _load(payload_dir: Path) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {}
    for f in payload_dir.glob("*.json"):
        try:
            ctx[f.stem] = json.loads(f.read_text())
        except Exception as exc:
            print(f"warn: {f.name} unreadable ({exc})", file=sys.stderr)
    return ctx


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--payloads", help="directory of diag JSON payloads "
                                       "(ib_open_orders.json, positions.json, ...)")
    ap.add_argument("--json", action="store_true", help="emit machine-readable results")
    a = ap.parse_args()

    if a.self_test:
        return _self_test()
    if not a.payloads:
        ap.error("--payloads is required (or --self-test)")

    ctx = _load(Path(a.payloads))
    results = []
    for inv in INVARIANTS:
        r = inv["fn"](ctx)
        results.append({"id": inv["id"], "blast_radius": inv["blast"],
                        "question": inv["q"], **r.as_dict()})

    if a.json:
        print(json.dumps({"results": results}, indent=2))
    else:
        order = {FAIL: 0, NOT_MEASURED: 1, PASS: 2}
        for r in sorted(results, key=lambda x: order.get(x["verdict"], 3)):
            mark = {FAIL: "FAIL", PASS: "pass", NOT_MEASURED: "NOT-MEASURED"}[r["verdict"]]
            print(f"[{mark:>12}] {r['id']}  ({r['blast_radius']})")
            print(f"               population: {r['population']}  n={r['n']}")
            if r["detail"]:
                print(f"               {r['detail']}")
            for v in r["violations"]:
                print(f"               !! {v}")
        nf = sum(1 for r in results if r["verdict"] == FAIL)
        nm = sum(1 for r in results if r["verdict"] == NOT_MEASURED)
        print(f"\n{len(results)} invariants: {nf} FAIL, {nm} NOT-MEASURED, "
              f"{len(results)-nf-nm} pass")
        print("NOT-MEASURED is not a pass — it means the check could not look.")
    return 1 if any(r["verdict"] == FAIL for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
