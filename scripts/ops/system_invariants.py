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

PAYLOAD FILES
-------------
``--payloads`` reads ``<dir>/*.json`` and keys each by its FILE STEM. The stems
the invariants read, and the route each comes from:

    ib_open_orders.json      /api/diag/ib_open_orders
    bybit_open_orders.json   /api/diag/bybit_open_orders
    exchange_positions.json  /api/diag/exchange_positions
    positions.json           /api/bot/positions
    journal_trades.json      /api/diag/journal?table=trades
    exit_loop_health.json    /api/diag/log_file?name=exit_loop_health

⚠️ A payload that is simply ABSENT makes its invariants report
``not_measured``, which is correct but is byte-identical to a blind read of a
route that answered emptily. Capture every file above, or read the ``detail``
line to see which one was missing.

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

    ⚠️ ``near_miss`` IS GRADED BEFORE THE SMALL-n REFUSAL, and that asymmetry is
    deliberate. ``within`` at n=4 is an ABSENCE of observation and is refused;
    ``near_miss`` at n=4 is a POSITIVE observation — a gap really did reach 90% of
    the requirement — and a small sample does not un-see it. It returns ``pass``
    because the invariant asserted here is "the loop MET its 60s requirement", and
    a near miss met it; the margin is carried in ``detail`` rather than promoted
    to a violation, because spending FAIL on a promise that was KEPT is how a
    suite gets ignored. The WARN half is the operator ping in
    ``exit_loop_health.run_exit_loop_health_check``, which is where a warning
    belongs.
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
    if state == "near_miss":
        ratio = _num((body or {}).get("requirement_ratio"))
        pct = f"{round(ratio * 100, 1)}%" if ratio is not None else "unknown%"
        req = (body or {}).get("requirement_s")
        return Result(PASS, "exit-loop inter-evaluation intervals", n,
                      detail=f"NEAR MISS — max_interval={mx}ms is {pct} of the "
                             f"{req}s requirement over n={n}. INSIDE the "
                             f"requirement, so not a violation — but `within` and "
                             f"this are not the same fact, and the margin is the "
                             f"finding. Measured 2026-09-09: the promise cleared "
                             f"by 48.8ms and every instrument read `within`.")
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


# ------------------------------------------------- Bybit per-ROW leg sizing
def _bybit_leg_qty_index(payload: Any):
    """``({order_id: {"qty", "symbol", "account"}}, read, blind, unchecked)``.

    ``read`` / ``blind`` come from each account's ``read_state``, never from an
    empty ``orders`` list: ``[]`` after ``orders_read`` means the account holds
    nothing resting, while ``[]`` after ``could_not_look`` means we never
    asked. Those are opposite facts.

    ``unchecked`` carries the payload's own ``order_symbols_unchecked`` — the
    DENOMINATOR for the orders read. A leg id absent from this index on such a
    symbol may be resting-but-unread rather than gone, which is why an absent
    id is never graded a violation below.

    ``qty`` is ``None`` when the venue reported it unreadable; the surface
    publishes an unset numeric as ``null`` rather than ``0.0`` on purpose, and
    a ``0.0`` there would compare as a wildly under-sized leg.
    """
    idx: Dict[str, Dict[str, Any]] = {}
    read: List[Any] = []
    blind: List[Any] = []
    unchecked: List[str] = []
    for acct in (payload or {}).get("accounts") or []:
        aid = acct.get("account_id")
        state = acct.get("read_state")
        if state == "could_not_look":
            blind.append(aid)
            continue
        if state != "orders_read":      # not_bybit — nothing to read
            continue
        read.append(aid)
        result = acct.get("result") or {}
        for sym in result.get("order_symbols_unchecked") or []:
            unchecked.append(f"{aid}/{sym}")
        for o in result.get("orders") or []:
            oid = o.get("order_id")
            if oid is None:
                continue
            idx[str(oid)] = {
                "qty": _num(o.get("qty")),
                "symbol": str(o.get("symbol") or "").upper(),
                "account": aid,
            }
    return idx, read, blind, unchecked


def _open_journal_trades(payload: Any) -> List[Dict[str, Any]]:
    """Open, non-backtest ``trades`` rows from ``/api/diag/journal?table=trades``.

    Accepts the route's bare list as well as a ``{"rows": [...]}`` wrapper so a
    payload captured either way grades identically.
    """
    rows = payload if isinstance(payload, list) else (payload or {}).get("rows") or []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if str(r.get("status") or "").lower() != "open":
            continue
        try:
            if int(r.get("is_backtest") or 0) != 0:
                continue
        except (TypeError, ValueError):
            continue
        out.append(r)
    return out


def inv_protective_leg_matches_row(ctx) -> Result:
    """A tracked protective leg's venue qty must equal THAT ROW's position_size.

    Audit F-36 (2026-09-09). ``apply_intent_reduce_partial_close`` shrank a
    parent row and never resized the leg its own ``sl_order_id`` points at, so
    a reduce-only stop stayed sized for the PRE-reduce position. Measured live
    on ``bybit_1``/ADAUSDT: trade 5417 ``position_size`` 628.0 against a
    tracked stop of 63,943.0 — **101.82x the row it protects**.

    ⚠️ **THIS IS STRICTLY STRONGER THAN ANY SYMBOL-LEVEL CHECK, AND THAT IS THE
    WHOLE POINT.** In the live instance the journal reconciled to the exchange
    EXACTLY (79227.0 + 628.0 = 79855.0), so ``INV-JOURNAL-EXCHANGE`` passed and
    ``exit_path_coverage.py`` graded 5417 ``bStop LIVE`` on the leg's mere
    PRESENCE. A per-symbol sum is blind to how the protection is distributed
    ACROSS the rows that share the position; this asks the per-row question.
    ``_fx_leg_mismatch`` plants exactly that shape and the self-test asserts
    that ``INV-JOURNAL-EXCHANGE`` PASSES on it while this one FAILS.

    ⚠️ **A tracked id that is NOT resting is a DIFFERENT question and is never
    a violation here.** The leg may have fired, been cancelled, or simply sit
    on a symbol whose order read was incomplete. Whether a declared leg still
    rests is stop-coverage's question; this one grades SIZE, and only over legs
    the venue confirmed are resting. The count is reported in ``detail`` so an
    ungraded leg is never mistaken for a clean one.

    Population: (open journal row, tracked leg) pairs whose leg is RESTING on a
    cleanly-read Bybit account.
    """
    ob, tr = ctx.get("bybit_open_orders"), ctx.get("journal_trades")
    pop = "(open trade, tracked protective leg) pairs resting on a cleanly-read account"
    if ob is None or tr is None:
        return Result(NOT_MEASURED, pop, 0,
                      detail="bybit_open_orders and/or journal_trades payload absent")
    idx, read, blind, unchecked = _bybit_leg_qty_index(ob)
    rows = _open_journal_trades(tr)

    graded: List[tuple] = []       # (row, leg-kind, order_id, leg_qty, size)
    not_resting = 0
    leg_qty_unreadable = 0
    size_unreadable = 0
    rows_with_a_tracked_id = 0
    for r in rows:
        pairs = [("sl", r.get("sl_order_id")), ("tp", r.get("tp_order_id"))]
        pairs = [(k, v) for k, v in pairs if v not in (None, "")]
        if not pairs:
            continue
        rows_with_a_tracked_id += 1
        size = _num(r.get("position_size"))
        for kind, oid in pairs:
            leg = idx.get(str(oid))
            if leg is None:
                not_resting += 1
                continue
            if leg["qty"] is None:
                leg_qty_unreadable += 1
                continue
            if size is None or size <= 0:
                size_unreadable += 1
                continue
            graded.append((r, kind, str(oid), leg["qty"], size))

    if not graded:
        return Result(
            NOT_MEASURED, pop, 0,
            detail=f"nothing gradeable — {len(rows)} open journal row(s), "
                   f"{rows_with_a_tracked_id} carrying a tracked leg id, of which "
                   f"{not_resting} leg(s) are not in the resting book, "
                   f"{leg_qty_unreadable} have an unreadable venue qty and "
                   f"{size_unreadable} an unreadable position_size · "
                   f"blind accounts: {blind} · incomplete order reads: {unchecked}")

    viol = []
    for r, kind, oid, leg_qty, size in graded:
        # A leg should carry its row's size exactly; the tolerance absorbs
        # float noise only. Both directions are violations: an OVER-sized leg
        # can cut a live sibling, an UNDER-sized one leaves the row part naked.
        if abs(leg_qty - size) > max(1e-9, 1e-4 * size):
            viol.append(
                f"{r.get('account_id')}/{r.get('symbol')} trade {r.get('id')}: "
                f"position_size {size:g} but its own tracked {kind} leg {oid} "
                f"rests at {leg_qty:g} ({leg_qty / size:.2f}x)")
    return Result(
        FAIL if viol else PASS, pop, len(graded), viol,
        detail=f"read accounts: {read} · blind (not counted): {blind} · "
               f"incomplete order reads: {unchecked} · "
               f"tracked legs not in the resting book (a different question, "
               f"not graded here): {not_resting} · unreadable venue qty: "
               f"{leg_qty_unreadable} · unreadable position_size: {size_unreadable}")


# ------------------------------------------- package-leg reachability (F-39)
#
# THE VERDICT IS NOT RE-DERIVED HERE. `src/runtime/package_leg_coverage.py`
# declares itself the single owner ("one assessor, two consumers"); this is the
# third consumer. When the import fails the invariant reports `not_measured`
# rather than falling back to an inline copy -- a second copy of the rule is
# precisely how the live alert and this suite would come to disagree about a
# package, which is what that module exists to prevent. (`_protective_leg_side`
# above keeps an inline fallback because it is a 6-line pure classifier this
# file can be shown to reproduce byte-faithfully; a 4-verdict assessor is not.)
try:  # pragma: no cover - import path depends on host deps
    sys.path.insert(0, str(REPO))
    from src.runtime.package_leg_coverage import (  # type: ignore  # noqa: E402
        assess as _assess_package_legs,
    )
    _PACKAGE_ASSESSOR_SOURCE = "src.runtime.package_leg_coverage"
except Exception:
    _assess_package_legs = None  # type: ignore[assignment]
    _PACKAGE_ASSESSOR_SOURCE = "unavailable"


def _package_index(payload: Any) -> tuple:
    """``(packages by id, linked_trade_id -> package id)``.

    From ``/api/diag/journal?table=order_packages``. The route orders
    ``datetime(updated_at) DESC``, so ``setdefault`` keeps the NEWEST package
    naming a given trade rather than an older superseded one.
    """
    rows = payload if isinstance(payload, list) else (payload or {}).get("rows") or []
    by_id: Dict[str, dict] = {}
    by_linked: Dict[str, str] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        pid = r.get("order_package_id")
        if pid in (None, ""):
            continue
        by_id[str(pid)] = r
        linked = r.get("linked_trade_id")
        if linked not in (None, ""):
            by_linked.setdefault(str(linked), str(pid))
    return by_id, by_linked


def _open_book(payload: Any) -> List[Dict[str, Any]]:
    """Open positions from ``/api/bot/positions?include_paper=true``.

    ⚠️ **THIS IS WHY THE INVARIANT DOES NOT READ ``journal_trades`` ALONE.**
    ``/api/diag/journal`` is hard-clamped to ``_MAX_LIMIT = 1000`` rows ordered
    ``id DESC`` (``src/web/api/routers/diag.py:912``), so an open leg older than
    the newest 1000 rows **is not in that payload at all**. MEASURED
    2026-09-10T01:0xZ: the window ran **4618..5617** while stranded leg **4347**
    (``alpaca_paper``/SPY, open since 2026-08-03) sat below it -- one of the four
    legs this invariant exists to catch, invisible on the surface the suite's
    own docstring names. Grading the journal payload alone would have reported
    that leg CLEAN, which is audit F-37's class (the instrument truncating its
    own population) reproduced inside the fix for F-39.

    ``include_paper=true`` matters for the same reason: the default is
    ``False`` and returns 1 of 26 rows (F-37).
    """
    rows = payload if isinstance(payload, list) else (payload or {}).get("positions") or []
    return [p for p in rows if isinstance(p, dict) and p.get("id") not in (None, "")]


def inv_package_legs_reachable(ctx) -> Result:
    """Every STANDING open leg must belong to a package the exit loop can select.

    Audit F-39, operator-approved Tier-2 on
    ``WO-20260909-DECISION-PACKAGES-CLOSED-WHILE-HOLDING-OPEN-LEGS``
    (``chosen: detector_and_reattach``, 2026-09-09).

    ``order_monitor`` drives exits per ORDER PACKAGE -- it selects
    ``status="open"`` packages and resolves ONE row from ``linked_trade_id`` --
    so a package flipped to ``closed`` while any leg is still open leaves the
    loop's ``status="open"`` filter unable to select it ever again. The
    survivors then exit only on their own resting bracket or the reconciler.
    MEASURED live 2026-09-10T01:0xZ: three closed packages held four open legs,
    one of them trade **5474 on real-money ``bybit_2``** -- and 5474 is the
    package's own ``linked_trade_id``, so it is the MANAGED leg that was
    stranded, not merely a sibling.

    WHY THIS RECOMPUTES INSTEAD OF READING THE LATCH
    ------------------------------------------------
    ``package_leg_coverage`` already computes this verdict and it lands only in
    ``runtime_logs/package_leg_coverage_state.json`` -- a latch whose clear path
    is unreachable once a package's last leg closes (audit F-38), so **17 of its
    20 entries described conditions that no longer existed**, the oldest 22 days
    stale. **That 15%-live figure is what "restricted to LIVE legs" refers to,
    and honouring it is this invariant's whole design:** it recomputes over the
    CURRENT open book and never opens the latch file. Failing the suite off the
    latch would red the build on 22-day-old ghosts -- the desensitised-alarm P1
    with a red build attached.

    ⚠️ **ONLY ``{stranded, divergent}`` ARE VIOLATIONS**, which is the scope the
    operator chose. ``linked_unresolvable`` is *we could not identify the managed
    leg*; it is counted and named in ``detail`` but is **not** a FAIL, because
    "we did not look" must no more render as a violation than as a pass.

    Population: standing open legs resolvable to an order package.
    """
    pop = "standing open legs resolvable to an order package"
    pkg_payload = ctx.get("order_packages")
    pos_payload = ctx.get("positions")
    tr_payload = ctx.get("journal_trades")

    if _assess_package_legs is None:
        return Result(NOT_MEASURED, pop, 0,
                      detail="src.runtime.package_leg_coverage.assess is not "
                             "importable -- refusing to re-derive the verdict "
                             "from a second copy of the rule")
    if pkg_payload is None or (pos_payload is None and tr_payload is None):
        return Result(NOT_MEASURED, pop, 0,
                      detail="order_packages payload absent, or neither "
                             "positions nor journal_trades present")

    packages, by_linked = _package_index(pkg_payload)
    legs: Dict[str, Dict[str, Any]] = {}
    klass: Dict[str, str] = {}

    # The journal is the least-reduced source and carries `order_package_id`
    # directly, so it is preferred wherever it reaches.
    for r in (_open_journal_trades(tr_payload) if tr_payload is not None else []):
        legs[str(r.get("id"))] = {
            "id": r.get("id"), "account_id": r.get("account_id"),
            "symbol": r.get("symbol"),
            "position_size": _num(r.get("position_size")),
            "stop_loss": _num(r.get("stop_loss")),
            "order_package_id": r.get("order_package_id"),
            "strategy_name": r.get("strategy_name"),
        }

    outside_window = 0
    recovered_via_link = 0
    for p in (_open_book(pos_payload) if pos_payload is not None else []):
        tid = str(p.get("id"))
        klass[tid] = str(p.get("accountClass") or "")
        if tid in legs:
            continue
        # Below the journal payload's 1000-row window. The package can still be
        # recovered from the REVERSE direction -- order_packages.linked_trade_id
        # -- which is how leg 4347 is graded at all.
        outside_window += 1
        pid = by_linked.get(tid)
        if pid:
            recovered_via_link += 1
        legs[tid] = {
            "id": p.get("id"), "account_id": p.get("account"),
            "symbol": p.get("symbol"),
            "position_size": abs(_num(p.get("qty")) or 0.0),
            "stop_loss": _num(p.get("stopLoss")),
            "order_package_id": pid,
            "strategy_name": p.get("pattern"),
        }

    verdicts = _assess_package_legs(list(legs.values()), packages)

    graded = 0
    ungraded = 0
    viol: List[str] = []
    for pkg_id, row in verdicts.items():
        v = row.get("verdict")
        n_legs = int(row.get("leg_count") or 0)
        if v == "linked_unresolvable":
            ungraded += n_legs
            continue
        graded += n_legs
        if v not in ("stranded", "divergent"):
            continue
        for leg in row.get("legs", []):
            tid = str(leg.get("trade_id"))
            cls = klass.get(tid) or "account-class-unknown"
            viol.append(
                f"{leg.get('account')}/{row.get('symbol')} trade {tid} "
                f"[{cls}]: {str(v).upper()} -- {row.get('reason')} "
                f"(package {pkg_id}, strategy {row.get('strategy')})")

    xcheck = ("ran" if pos_payload is not None else
              "DID NOT RUN -- positions payload absent, so any open leg below "
              "the journal's 1000-row window is invisible here")
    detail = (f"packages examined: {len(verdicts)} · legs graded: {graded} · "
              f"legs UNGRADED as linked_unresolvable (we could not identify the "
              f"managed leg -- never a pass): {ungraded} · open legs below the "
              f"journal payload's 1000-row window: {outside_window}, of which "
              f"{recovered_via_link} recovered via "
              f"order_packages.linked_trade_id · open-book cross-check: "
              f"{xcheck} · assessor: {_PACKAGE_ASSESSOR_SOURCE}")

    if graded == 0:
        return Result(NOT_MEASURED, pop, 0, detail=detail)
    return Result(FAIL if viol else PASS, pop, graded, viol, detail=detail)


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
    {"id": "INV-PROTECT-LEG-MATCHES-ROW", "blast": "money-at-risk",
     "q": "Does a tracked protective leg carry the size of the ROW it protects?",
     "fn": inv_protective_leg_matches_row},
    {"id": "INV-JOURNAL-EXCHANGE", "blast": "accounting",
     "q": "Does journal open qty reconcile to exchange position size?",
     "fn": inv_journal_matches_exchange},
    {"id": "INV-NETTED-DUP-UPNL", "blast": "accounting",
     "q": "Do sibling rows on one netted position each carry the whole uPnL?",
     "fn": inv_no_netted_duplicate_upnl},
    {"id": "INV-EXIT-INTERVAL", "blast": "money-at-risk",
     "q": "Is the exit loop meeting its 60s re-evaluation requirement?",
     "fn": inv_exit_loop_meets_requirement},
    {"id": "INV-PACKAGE-LEG-REACHABLE", "blast": "money-at-risk",
     "q": "Is every standing open leg still selectable by the exit loop?",
     "fn": inv_package_legs_reachable},
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
        # Per-ROW leg sizing: each tracked leg carries its own row's size.
        "bybit_open_orders": {"accounts": [
            {"account_id": "bybit_1", "read_state": "orders_read",
             "result": {"orders": [
                 {"order_id": "sl-4817", "symbol": "AVAXUSDT", "qty": 822.9},
                 {"order_id": "sl-4795", "symbol": "AVAXUSDT", "qty": 5508.1},
             ], "order_symbols_unchecked": []}},
        ]},
        "journal_trades": [
            {"id": 4817, "account_id": "bybit_1", "symbol": "AVAXUSDT",
             "status": "open", "is_backtest": 0, "position_size": 822.9,
             "sl_order_id": "sl-4817", "tp_order_id": None},
            {"id": 4795, "account_id": "bybit_1", "symbol": "AVAXUSDT",
             "status": "open", "is_backtest": 0, "position_size": 5508.1,
             "sl_order_id": "sl-4795", "tp_order_id": None},
        ],
    }


def _fx_leg_mismatch() -> Dict[str, Any]:
    """THE LIVE F-36 SHAPE, and it is built to pass every symbol-level check.

    ``bybit_1``/ADAUSDT as measured 2026-09-09T16:47Z: trade 5417 carries
    ``position_size`` 628.0 while its OWN tracked stop rests at 63,943.0, and
    the sibling 5479 is exactly sized. Journal 79227.0 + 628.0 = 79855.0 equals
    the exchange position to the unit, so ``INV-JOURNAL-EXCHANGE`` PASSES on
    this fixture — which the self-test asserts explicitly. That contrast IS the
    "strictly stronger than the symbol-level check" claim, made executable.
    """
    return {
        "bybit_open_orders": {"accounts": [
            {"account_id": "bybit_1", "read_state": "orders_read",
             "result": {"orders": [
                 {"order_id": "sl-5479", "symbol": "ADAUSDT", "qty": 79227.0},
                 {"order_id": "sl-5417", "symbol": "ADAUSDT", "qty": 63943.0},
             ], "order_symbols_unchecked": []}},
        ]},
        "journal_trades": [
            {"id": 5479, "account_id": "bybit_1", "symbol": "ADAUSDT",
             "status": "open", "is_backtest": 0, "position_size": 79227.0,
             "sl_order_id": "sl-5479"},
            {"id": 5417, "account_id": "bybit_1", "symbol": "ADAUSDT",
             "status": "open", "is_backtest": 0, "position_size": 628.0,
             "sl_order_id": "sl-5417"},
        ],
        # The symbol-level view, which reconciles EXACTLY.
        "positions": [
            {"id": "5479", "account": "bybit_1", "symbol": "ADAUSDT", "qty": 79227.0},
            {"id": "5417", "account": "bybit_1", "symbol": "ADAUSDT", "qty": 628.0},
        ],
        "exchange_positions": {"accounts": [
            {"account_id": "bybit_1",
             "positions": [{"symbol": "ADA/USDT:USDT", "size": 79855.0}]}]},
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
        "bybit_open_orders": {"accounts": [
            {"account_id": "bybit_1", "read_state": "orders_read",
             "result": {"orders": [
                 {"order_id": "sl-4816", "symbol": "SOLUSDT", "qty": 1409.4},
                 # 4810's own leg still sized for the PRE-reduce position.
                 {"order_id": "sl-4810", "symbol": "SOLUSDT", "qty": 1777.2},
             ], "order_symbols_unchecked": []}},
            {"account_id": "bybit_2", "read_state": "could_not_look", "result": None},
        ]},
        "journal_trades": [
            {"id": 4816, "account_id": "bybit_1", "symbol": "SOLUSDT",
             "status": "open", "is_backtest": 0, "position_size": 1409.4,
             "sl_order_id": "sl-4816"},
            {"id": 4810, "account_id": "bybit_1", "symbol": "SOLUSDT",
             "status": "open", "is_backtest": 0, "position_size": 367.8,
             "sl_order_id": "sl-4810"},
        ],
    }


def _fx_naked() -> Dict[str, Any]:
    fx = _fx_good()
    fx["ib_open_orders"]["accounts"][0]["orders"] = []
    fx["ib_open_orders"]["accounts"][0]["count"] = 0
    return fx


def _fx_pkg_legs(stranded: bool = True) -> Dict[str, Any]:
    """THE LIVE F-39 SHAPE, transcribed from the 2026-09-10T01:0xZ read.

    ``pkg-293021e2e84a48db`` (xrp_pullback_2h/XRPUSDT) closed
    ``reconciler_filled`` with ``linked_trade_id`` 5474, while BOTH 5474
    (``bybit_2``, REAL MONEY, 58.5) and 5475 (``bybit_portfolio``, 11903.8)
    were still open. Note it is the LINKED leg that is stranded here, not just
    a sibling -- the managed row itself fell out of the loop.

    ``stranded=False`` flips only ``status`` to ``open``: the linked id is then
    among the open legs and the two stops agree, so the same fixture must grade
    ``managed``. That pair IS the planted-control contract -- fail with the
    condition planted, pass once it is removed, one field apart.
    """
    return {
        "order_packages": [
            {"order_package_id": "pkg-293021e2e84a48db",
             "strategy_name": "xrp_pullback_2h", "symbol": "XRPUSDT",
             "status": "closed" if stranded else "open",
             "sl": 1.34631429, "tp": 1.5535464,
             "linked_trade_id": 5474, "close_reason": "reconciler_filled"}],
        "journal_trades": [
            {"id": 5474, "account_id": "bybit_2", "symbol": "XRPUSDT",
             "status": "open", "is_backtest": 0, "position_size": 58.5,
             "stop_loss": 1.34631429,
             "order_package_id": "pkg-293021e2e84a48db"},
            {"id": 5475, "account_id": "bybit_portfolio", "symbol": "XRPUSDT",
             "status": "open", "is_backtest": 0, "position_size": 11903.8,
             "stop_loss": 1.34631429,
             "order_package_id": "pkg-293021e2e84a48db"}],
        "positions": [
            {"id": "5474", "account": "bybit_2", "symbol": "XRPUSDT",
             "qty": 58.5, "stopLoss": 1.34631429, "accountClass": "real_money"},
            {"id": "5475", "account": "bybit_portfolio", "symbol": "XRPUSDT",
             "qty": 11903.8, "stopLoss": 1.34631429, "accountClass": "paper"}],
    }


def _fx_pkg_leg_below_journal_window() -> Dict[str, Any]:
    """Leg **4347** as it really is: open, and BELOW the journal's 1000-row cap.

    MEASURED 2026-09-10T01:0xZ -- ``/api/diag/journal?table=trades`` returned
    ids 4618..5617, so 4347 (``alpaca_paper``/SPY, open since 2026-08-03, its
    package ``pkg-32a5162bbbdd4ef6`` closed ``exchange_flat_reconciled``) is
    **absent from journal_trades entirely** while sitting in the open book.

    ``journal_trades`` here holds one unrelated, healthy in-window row so the
    graded denominator is non-zero -- otherwise a PASS and a vacuous
    ``not_measured`` would be indistinguishable.
    """
    return {
        "order_packages": [
            {"order_package_id": "pkg-32a5162bbbdd4ef6",
             "strategy_name": "spy_trend_long_1d", "symbol": "SPY",
             "status": "closed", "sl": 744.47714286, "tp": 830.42638,
             "linked_trade_id": 4347,
             "close_reason": "exchange_flat_reconciled"},
            {"order_package_id": "pkg-healthy", "strategy_name": "s",
             "symbol": "AVAXUSDT", "status": "open", "sl": 7.8, "tp": 7.6,
             "linked_trade_id": 5617, "close_reason": None}],
        "journal_trades": [
            {"id": 5617, "account_id": "bybit_1", "symbol": "AVAXUSDT",
             "status": "open", "is_backtest": 0, "position_size": 20065.4,
             "stop_loss": 7.81461429, "order_package_id": "pkg-healthy"}],
        "positions": [
            {"id": "5617", "account": "bybit_1", "symbol": "AVAXUSDT",
             "qty": 20065.4, "stopLoss": 7.81461429, "accountClass": "paper"},
            {"id": "4347", "account": "alpaca_paper", "symbol": "SPY",
             "qty": 11.0, "stopLoss": 744.47714286, "accountClass": "paper"}],
    }


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
    expect("per-row leg sizing detects an oversized tracked leg",
           "INV-PROTECT-LEG-MATCHES-ROW", bad, FAIL)

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
    expect("per-row leg sizing passes exactly-sized legs",
           "INV-PROTECT-LEG-MATCHES-ROW", good, PASS)

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

    # (c4) THE LOAD-BEARING CONTROL for INV-PROTECT-LEG-MATCHES-ROW: on the
    #      real F-36 shape the symbol reconciles EXACTLY, so the symbol-level
    #      check PASSES on the very fixture the per-row check must FAIL. If
    #      these two ever agree, the new invariant has stopped being stronger
    #      than the one it was added to exceed.
    mismatch = _fx_leg_mismatch()
    expect("per-row leg sizing FAILS the live F-36 shape",
           "INV-PROTECT-LEG-MATCHES-ROW", mismatch, FAIL)
    expect("...while the SYMBOL-level check PASSES on the same fixture",
           "INV-JOURNAL-EXCHANGE", mismatch, PASS)

    # (c5) An UNDER-sized leg is a violation too — the check is equality, not a
    #      ceiling. An under-sized stop leaves part of the row unprotected.
    under = _fx_leg_mismatch()
    under["bybit_open_orders"]["accounts"][0]["result"]["orders"][1]["qty"] = 1.0
    under["journal_trades"][1]["position_size"] = 628.0
    expect("per-row leg sizing detects an UNDER-sized leg",
           "INV-PROTECT-LEG-MATCHES-ROW", under, FAIL)

    # (c6) A tracked leg that is NOT in the resting book is a DIFFERENT
    #      question (did it fire? was it cancelled?) and must not be graded a
    #      size violation here. One exactly-sized leg keeps n > 0 so this is a
    #      PASS rather than a vacuous not_measured.
    absent_leg = _fx_leg_mismatch()
    absent_leg["bybit_open_orders"]["accounts"][0]["result"]["orders"] = [
        {"order_id": "sl-5479", "symbol": "ADAUSDT", "qty": 79227.0}]
    expect("a tracked leg missing from the resting book is not a size violation",
           "INV-PROTECT-LEG-MATCHES-ROW", absent_leg, PASS)

    # (c7) A row on a could_not_look account must not be graded at all —
    #      "we did not look" is never a pass and never a failure.
    blind_only = _fx_leg_mismatch()
    blind_only["bybit_open_orders"]["accounts"][0]["read_state"] = "could_not_look"
    blind_only["bybit_open_orders"]["accounts"][0]["result"] = None
    expect("rows on a blind account are not_measured, never graded",
           "INV-PROTECT-LEG-MATCHES-ROW", blind_only, NOT_MEASURED)

    # (c8) The venue surface publishes an unset qty as null, never 0.0. A null
    #      is "we could not read the size" and must not be graded as a leg of
    #      size zero, which would render as maximally under-sized.
    null_qty = _fx_leg_mismatch()
    for o in null_qty["bybit_open_orders"]["accounts"][0]["result"]["orders"]:
        o["qty"] = None
    expect("a null venue qty is not graded as a zero-sized leg",
           "INV-PROTECT-LEG-MATCHES-ROW", null_qty, NOT_MEASURED)

    # (c9) CLOSED and BACKTEST rows are out of population. A closed row's leg
    #      no longer has a size to match, and a backtest row never had a leg.
    closed_rows = _fx_leg_mismatch()
    closed_rows["journal_trades"][1]["status"] = "closed"
    expect("a closed row is out of population (only the sized sibling remains)",
           "INV-PROTECT-LEG-MATCHES-ROW", closed_rows, PASS)
    bt_rows = _fx_leg_mismatch()
    bt_rows["journal_trades"][1]["is_backtest"] = 1
    expect("a backtest row is out of population",
           "INV-PROTECT-LEG-MATCHES-ROW", bt_rows, PASS)

    # ---------------------------------------------------------------- F-39
    # (f1/f2) THE PLANTED-CONTROL PAIR, one field apart. A check that cannot be
    #         shown to FAIL on the real condition, and to stop failing once the
    #         condition is removed, is evidence of nothing.
    expect("package-leg detects the live F-39 stranded shape (real-money 5474)",
           "INV-PACKAGE-LEG-REACHABLE", _fx_pkg_legs(stranded=True), FAIL)
    expect("...and PASSES once the package is no longer closed",
           "INV-PACKAGE-LEG-REACHABLE", _fx_pkg_legs(stranded=False), PASS)

    # (f3/f4) THE LOAD-BEARING CONTROL. Leg 4347 sits BELOW the diag journal's
    #         1000-row cap, so a journal-only reading cannot see it at all. f3
    #         asserts the invariant still catches it (via the open book plus
    #         order_packages.linked_trade_id); f4 asserts that the SAME fixture
    #         WITHOUT the positions payload reports CLEAN -- i.e. that the
    #         open-book cross-check is load-bearing rather than decorative. If
    #         these two ever agree, this invariant has gone blind to exactly the
    #         leg class F-39 named and would say so with a green tick.
    below = _fx_pkg_leg_below_journal_window()
    expect("package-leg catches a stranded leg BELOW the journal's 1000-row cap",
           "INV-PACKAGE-LEG-REACHABLE", below, FAIL)
    journal_only = {k: v for k, v in below.items() if k != "positions"}
    expect("...while the journal-only view reports it clean (the cap is real)",
           "INV-PACKAGE-LEG-REACHABLE", journal_only, PASS)

    # (f5) `linked_unresolvable` is *we could not identify the managed leg*. It
    #      is counted, never a violation -- "we did not look" must no more read
    #      as a FAIL than as a pass. One healthy row keeps the denominator > 0
    #      so this is a real PASS rather than a vacuous not_measured.
    unresolvable = _fx_pkg_leg_below_journal_window()
    unresolvable["order_packages"] = [
        pk for pk in unresolvable["order_packages"]
        if pk["order_package_id"] == "pkg-healthy"]
    expect("an unresolvable leg is counted, not failed",
           "INV-PACKAGE-LEG-REACHABLE", unresolvable, PASS)

    # (f6) The other half of the operator's chosen scope: an OPEN package whose
    #      siblings hold different stops. A trail moved the linked leg only.
    divergent = _fx_pkg_legs(stranded=False)
    divergent["journal_trades"][1]["stop_loss"] = 1.29
    divergent["positions"][1]["stopLoss"] = 1.29
    expect("package-leg detects divergent sibling stops on an open package",
           "INV-PACKAGE-LEG-REACHABLE", divergent, FAIL)

    # (f7) THE F-38 GHOST, and the reason this invariant recomputes instead of
    #      reading the latch: a CLOSED package whose legs have ALL since closed
    #      is not a standing condition and must NOT fail the suite. 17 of the
    #      latch's 20 entries were exactly this, the oldest 22 days stale.
    ghost = _fx_pkg_leg_below_journal_window()
    ghost["positions"] = [q for q in ghost["positions"] if q["id"] != "4347"]
    expect("a closed package whose legs have all closed does NOT fail the suite",
           "INV-PACKAGE-LEG-REACHABLE", ghost, PASS)

    # (e) small-n must NOT read as a pass
    small = _fx_good()
    small["exit_loop_health"] = {"requirement_state": "within",
                                 "intervals_measured": 4, "max_interval_ms": 20000}
    expect("exit-interval refuses to pass on n=4", "INV-EXIT-INTERVAL", small, NOT_MEASURED)

    # (e2) a NEAR MISS is a positive observation and passes on its own n — it is
    #      not refused the way a small-n `within` is, because something was seen.
    #      It must not be graded FAIL: the requirement was met.
    nearmiss = _fx_good()
    nearmiss["exit_loop_health"] = {"requirement_state": "near_miss",
                                    "intervals_measured": 4,
                                    "requirement_s": 60.0,
                                    "requirement_ratio": 0.9992,
                                    "max_interval_ms": 59951.2}
    expect("a near miss passes on n=4 rather than being refused",
           "INV-EXIT-INTERVAL", nearmiss, PASS)

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
