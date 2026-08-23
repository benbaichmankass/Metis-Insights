#!/usr/bin/env python3
"""Reconcile journal-DECLARED protection against broker-RESTING protection.

WHY THIS EXISTS — the third axis of one method's blind spot
===========================================================
``IBClient.protection_coverage`` has now been corrected twice, and each fix
closed a different axis of the same question:

  1. BL-20260814-IB-PROTECTION-BOOLEAN-NOT-QUANTITY -- it answered a BOOLEAN
     ("does any protective leg rest?"). Fixed to a QUANTITY.
  2. BL-20260816-COVERAGE-IS-ONE-SIDED -- it graded a stop and a take-profit as
     interchangeable. Fixed to two SIDES (``stop_qty`` / ``target_qty``).

It is still blind to a third axis: **PRICE**. The verdict it returns is
``{size, covered_qty, stop_qty, target_qty, legs, unknown_qty_legs,
oca_groups, source}`` -- there is no price in it, and there is no price in any
consumer. Measured 2026-08-20: ``auxPrice`` appears in ``src/`` exactly ONCE,
in ``IBClient.list_open_orders``, the read surface that merely dumps rows; and
``aux_price``/``lmt_price`` appear in ``scripts/`` ZERO times. So no code
anywhere compares the price of a resting protective leg to the price the
strategy declared in ``trades.stop_loss`` / ``trades.take_profit_1``.

That gap is not hypothetical. Measured on ``ib_paper`` at 2026-08-20T20:23Z:

    MES trade 4350, 15 long
      journal-declared stop_loss : 7533.696429
      the ONLY resting IB stop   : 7516.50   (order 338, oca-protect-336)
      divergence                 : 17.196 pt = 69 ticks = $1,289.73 on 15 MES

The position graded FULLY STOP-COVERED throughout -- ``stop_qty`` 15 against a
size of 15 -- because the quantity was right and the side was right. Only the
price was wrong, and nothing looks at the price. For context, that stop is the
one that SURVIVED the over-cover remediation: the row
BL-20260816-IB-STOPS-OVER-COVER-IN-DISJOINT-OCA-GROUPS records order 375 at
7533.75, which matches the journal to within one tick, and order 338 at 7516.5,
which does not. The leg that matched the journal was cancelled and the stray
kept -- the exact outcome that row's own resolution criterion #1 was written to
prevent ("read the journal's declared stop_loss ... and cancel the leg that
does not match it"). MGC and MHG both match their journals to within a tick in
the same read, so this is a genuine outlier and not a rounding artifact.

WHAT IT CHECKS
==============
Per open journal trade on an IB account, five conditions:

  stop side      -- ``stop_naked`` / ``stop_partial``            (a safety gap)
                 -- ``stop_over_cover``  stop_qty > position     (see below)
                 -- ``stop_disjoint_oca`` stops in >1 OCA group  (see below)
  target side    -- ``target_naked_declared`` / ``target_partial_declared``
  BOTH sides     -- ``stop_price_diverges`` / ``target_price_diverges``  <-- NEW

``stop_over_cover`` and ``stop_disjoint_oca`` port to IB the detect-only
``over_covered`` signal ``order_monitor._bybit_position_protection`` has
emitted for Bybit since 2026-07-30. A venue-specific detector for a
venue-independent failure was the actual gap named in that row's resolution
criterion #3. They are separate findings on purpose: quantity over-coverage is
a sizing anomaly, whereas stops in DISJOINT OCA groups are the naked-short
hazard -- ocaType=1 cancels the rest of the SAME group and says nothing about
a different one, so one stop firing flattens the position and leaves the other
resting to sell again into a naked short.

DECLARED-ONLY ON THE TARGET SIDE, DELIBERATELY
==============================================
A missing stop is a safety gap that may be closed blind. A missing target is
decision-time geometry: the level must be READ from ``trades.take_profit_1``,
never invented. So a position whose trade declares NO target is reported as
``target_absent_undeclared`` -- an INFO state, not a finding. Imposing a target
on a strategy that never chose one is a Tier-3 strategy question, not a repair.
This mirrors the rule already applied by
``order_monitor._check_broker_naked_ib_positions``.

THREE-STATE, NEVER COLLAPSED
============================
Per account: ``not_ib`` (no such surface -- we did not fail, there is nothing
to read) / ``could_not_look`` (gateway unreachable, breaker open, ambiguous
read) / ``reconciled`` (a confirmed clean read). A ``could_not_look`` account
NEVER contributes "0 findings" -- that is the collapse this whole family of
bugs is made of, and it is why the exit code for it is distinct.

Exit codes:  0 = reconciled and clean · 1 = findings · 2 = usage/parse error
             3 = could_not_look for at least one account (nothing was graded)

Read-only. Consumes the two diag payloads; opens no socket of its own, touches
no order path, and cannot refuse a trade.

SCHEDULED CALLER
================
``.github/workflows/broker-bracket-reconcile.yml`` runs this every 6h and on
demand. It RECORDS every run and COMMENTS only when ``fingerprint()`` moves,
because both of this tool's alarm-shaped outputs are true in the steady state
(``total_findings`` = 3 and exit 3, measured live 2026-08-21T06:39:13Z), so
pinging on either would fire constantly -- the desensitized alarm CLAUDE.md
names as its own P1 bug. Silence from that issue means "the same set of
problems", NEVER "no problems"; a stale "Last checked" line there means the
schedule itself has died.

Usage
-----
    bash scripts/ops/diag_fetch.sh '/api/diag/ib_open_orders' > /tmp/o.json
    curl -sS "$BASE/api/bot/positions?include_paper=true"     > /tmp/p.json
    python3 scripts/ops/broker_bracket_reconcile.py \
        --orders-json /tmp/o.json --positions-json /tmp/p.json

    python3 scripts/ops/broker_bracket_reconcile.py --self-test
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:  # this script is run by path, not as a package module
    sys.path.insert(0, _ROOT)

# THE COMPARATOR IS SHARED, NOT REIMPLEMENTED (2026-08-23).
# `src/runtime/protection_price.py` is the one definition of "does the resting
# leg agree with the declared level", and the LIVE sweep
# (`order_monitor._check_broker_naked_ib_positions`) grades through the same
# function. This file used to carry its own nearest-leg / tolerance arithmetic;
# two definitions of "diverges" are free to drift, and a detector that
# disagrees with the enforcing path about a live position is worse than either
# alone. BL-20260823-EXIT-SWEEP-CANNOT-INFER-A-BREACHED-BRACKET criterion 1
# names this explicitly. The module is PURE (stdlib only), so importing it
# costs this script nothing it did not already pay.
from src.runtime.protection_price import (  # noqa: E402
    PRICE_DIVERGES,
    PRICE_NO_TICK_SIZE,
    grade_protection_price,
)

# How many ticks of disagreement between the declared price and the resting
# price before it is a finding. 1.0 absorbs the venue's own rounding of a
# fractional declared level onto the tick grid (MGC declares 4371.1469 and
# rests at 4371.1 -- correct, not a divergence) while still catching MES's 69.
_DEFAULT_TICK_TOLERANCE = 1.0

# Fallback tick sizes, used ONLY when config/instruments.yaml cannot be read.
# The config is authoritative; these exist so the detector still grades rather
# than abstaining wholesale when run outside the repo.
_FALLBACK_TICKS = {"MES": 0.25, "MGC": 0.10, "MHG": 0.0005}


# --------------------------------------------------------------------------
# leg classification
# --------------------------------------------------------------------------
def protective_leg_side(order_type: str | None) -> str | None:
    """``stop`` / ``target`` / None.

    THE STOP FAMILY IS TESTED FIRST, and that ordering is load-bearing:
    ``"STP LMT"`` (a stop-limit) contains the substring ``LMT`` while being a
    STOP. An LMT-first test would file every stop-limit as a take-profit and
    MANUFACTURE target coverage that does not exist -- strictly worse than the
    bug it would replace, because the old one hid a real gap while that would
    invent a fake one. Mirrors ``ib_client._protective_leg_side`` deliberately;
    a second definition free to drift from the enforcing one would be its own
    defect, so this is kept byte-comparable in behaviour and tested as such.
    """
    t = str(order_type or "").strip().upper()
    if not t:
        return None
    if "TRAIL" in t or t.startswith("STP") or t in ("STOP", "STOP LIMIT"):
        return "stop"
    if "LMT" in t or t == "LIMIT":
        return "target"
    return None


def load_tick_sizes(path: str | None = None) -> dict[str, float]:
    """Tick size per symbol from ``config/instruments.yaml`` -- the field, not a
    guess. (Hardcoding these is how an earlier draft of this analysis used
    12500 for MHG's multiplier when the config says 2500.)"""
    path = path or os.path.join(_ROOT, "config", "instruments.yaml")
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001 -- fall back, never abstain wholesale
        return dict(_FALLBACK_TICKS)
    out: dict[str, float] = {}
    blocks = doc.get("instruments", doc) if isinstance(doc, dict) else {}
    if isinstance(blocks, dict):
        for sym, spec in blocks.items():
            if isinstance(spec, dict) and spec.get("tick_size") is not None:
                try:
                    out[str(sym).upper()] = float(spec["tick_size"])
                except (TypeError, ValueError):
                    continue
    for k, v in _FALLBACK_TICKS.items():
        out.setdefault(k, v)
    return out


# --------------------------------------------------------------------------
# reconciliation
# --------------------------------------------------------------------------
def _legs_for(orders: list[dict[str, Any]], symbol: str) -> tuple[list, list]:
    sym = str(symbol or "").upper()
    stops, targets = [], []
    for o in orders:
        if str(o.get("symbol") or "").upper() != sym:
            continue
        side = protective_leg_side(o.get("order_type"))
        if side == "stop":
            stops.append(o)
        elif side == "target":
            targets.append(o)
    return stops, targets


def _side_qty(legs: list[dict[str, Any]]) -> tuple[float, int, list[str]]:
    """Qty for one side, applying the within-OCA-group max.

    An OCA group is one-fills-cancels-the-rest, so two stops in one group are
    still one stop's worth of coverage. Ungrouped legs each stand alone.
    Returns (qty, unknown_qty_legs, sorted distinct group names).
    """
    grouped: dict[str, float] = {}
    loose = 0.0
    unknown = 0
    groups: list[str] = []
    for o in legs:
        q = o.get("total_quantity")
        try:
            q = abs(float(q))
        except (TypeError, ValueError):
            unknown += 1
            continue
        g = str(o.get("oca_group") or "")
        if g:
            grouped[g] = max(grouped.get(g, 0.0), q)
            if g not in groups:
                groups.append(g)
        else:
            loose += q
    return loose + sum(grouped.values()), unknown, sorted(groups)


def reconcile_position(
    trade: dict[str, Any],
    orders: list[dict[str, Any]],
    tick: float,
    tick_tolerance: float = _DEFAULT_TICK_TOLERANCE,
) -> dict[str, Any]:
    """Grade ONE open journal trade against the broker's resting legs."""
    sym = str(trade.get("symbol") or "").upper()
    size = abs(float(trade.get("qty") or 0.0))
    d_sl = trade.get("stopLoss")
    d_tp = trade.get("takeProfit")

    stops, targets = _legs_for(orders, sym)
    stop_qty, stop_unknown, stop_groups = _side_qty(stops)
    tgt_qty, tgt_unknown, _tgt_groups = _side_qty(targets)

    findings: list[dict[str, Any]] = []
    info: list[str] = []

    def add(kind: str, detail: str, **extra: Any) -> None:
        findings.append({"kind": kind, "detail": detail, **extra})

    # --- an ungradeable leg is NOT a clean grade -------------------------
    if stop_unknown or tgt_unknown:
        add("coverage_ungradeable",
            f"{stop_unknown + tgt_unknown} resting leg(s) have an unreadable "
            f"quantity, so coverage cannot be graded -- not re-armed, not "
            f"declared clean",
            stop_unknown=stop_unknown, target_unknown=tgt_unknown)

    # --- stop side: quantity ---------------------------------------------
    if size > 0:
        if stop_qty == 0:
            add("stop_naked", f"{sym} {size:g} open with NO resting stop",
                size=size, stop_qty=stop_qty)
        elif stop_qty < size:
            add("stop_partial",
                f"{sym} {size:g} open with only {stop_qty:g} of stop coverage",
                size=size, stop_qty=stop_qty)
        elif stop_qty > size:
            # Ported from the Bybit `over_covered` signal. Detect-only.
            add("stop_over_cover",
                f"{sym} holds {stop_qty:g} of stop against a position of "
                f"{size:g} ({stop_qty / size:.2f}x)",
                size=size, stop_qty=stop_qty, multiple=round(stop_qty / size, 4))

    # --- stop side: DISJOINT OCA GROUPS (the naked-short hazard) ---------
    if len(stop_groups) > 1:
        add("stop_disjoint_oca",
            f"{sym} stops rest in {len(stop_groups)} DISJOINT OCA groups "
            f"{stop_groups} -- one firing flattens the position and leaves the "
            f"other resting, which sells again into a naked short",
            oca_groups=stop_groups)

    # --- target side: declared-only --------------------------------------
    if d_tp is None:
        if tgt_qty == 0:
            info.append("target_absent_undeclared")
    elif size > 0:
        if tgt_qty == 0:
            add("target_naked_declared",
                f"{sym} {size:g} open with a DECLARED target of {d_tp} and NO "
                f"resting target -- the position can only stop out or run",
                size=size, declared_tp=d_tp)
        elif tgt_qty < size:
            add("target_partial_declared",
                f"{sym} {size:g} open with only {tgt_qty:g} of target coverage "
                f"against a declared target of {d_tp}",
                size=size, target_qty=tgt_qty, declared_tp=d_tp)

    # --- BOTH sides: PRICE (the axis nothing else checks) ----------------
    # Graded by `src.runtime.protection_price.grade_protection_price`, the SAME
    # function the live IB sweep calls. This file no longer owns a second
    # nearest-leg / tolerance arithmetic of its own.
    direction = trade.get("side") or trade.get("direction")
    for label, declared, legs, price_key in (
        ("stop", d_sl, stops, "aux_price"),
        ("target", d_tp, targets, "lmt_price"),
    ):
        if declared is None or not legs:
            continue
        # A leg whose price is unreadable is DROPPED here rather than passed as
        # 0.0 -- a zero would be compared against the declared level as a
        # catastrophic divergence when the truth is that we could not read it.
        prices: list[float] = []
        by_price: dict[float, dict[str, Any]] = {}
        for o in legs:
            try:
                px = float(o.get(price_key) or 0.0)
            except (TypeError, ValueError):
                continue
            if px > 0:
                prices.append(px)
                by_price.setdefault(px, o)
        if not prices:
            continue

        verdict = grade_protection_price(
            declared=declared,
            resting_prices=prices,
            direction=direction,
            side=label,
            tick_size=tick,
            tick_tolerance=tick_tolerance,
        )

        # "We could not resolve this instrument's tick" is NOT "the price
        # agrees" -- an assumed tick silently rescales the tolerance, and a
        # tolerance that is too wide turns a real divergence into a clean pass.
        # This used to be papered over with a 0.01 default two frames up.
        if verdict["state"] == PRICE_NO_TICK_SIZE:
            add(f"{label}_price_ungradeable",
                f"{sym} declares {label} {verdict['declared']:.6f} and a leg "
                f"rests at {verdict['nearest_resting']:.6f}, but no tick size "
                f"resolves for {sym} -- the divergence is NOT graded, which is "
                f"not the same as agreeing",
                declared=verdict["declared"],
                resting=verdict["nearest_resting"],
                delta=round(abs(verdict["diff"]), 8))
            continue

        if verdict["state"] == PRICE_DIVERGES:
            best_o = by_price.get(verdict["nearest_resting"], {})
            # `exposure` names the CONSEQUENCE, which |diff| cannot: a long
            # stop BELOW its declared level carries risk that was never
            # agreed, one ABOVE exits early. Same magnitude, opposite meaning.
            exposure = verdict.get("exposure")
            suffix = f" [{exposure}]" if exposure else ""
            add(f"{label}_price_diverges",
                f"{sym} declares {label} {verdict['declared']:.6f} but the "
                f"nearest resting {label} is {verdict['nearest_resting']:.6f} "
                f"-- {verdict['ticks']:.0f} ticks "
                f"({abs(verdict['diff']):.6f}) away{suffix}",
                declared=verdict["declared"],
                resting=verdict["nearest_resting"],
                delta=round(abs(verdict["diff"]), 8),
                ticks=round(verdict["ticks"], 2),
                side_of_declared=verdict.get("side_of_declared"),
                exposure=exposure,
                order_id=best_o.get("order_id"),
                oca_group=best_o.get("oca_group"))

    return {
        "symbol": sym,
        "trade_id": trade.get("id"),
        "size": size,
        "declared_stop": d_sl,
        "declared_target": d_tp,
        "stop_qty": stop_qty,
        "target_qty": tgt_qty,
        "stop_oca_groups": stop_groups,
        "tick": tick,
        "findings": findings,
        "info": info,
        "clean": not findings,
    }


def reconcile(
    orders_doc: dict[str, Any],
    positions: list[dict[str, Any]],
    ticks: dict[str, float] | None = None,
    tick_tolerance: float = _DEFAULT_TICK_TOLERANCE,
) -> dict[str, Any]:
    """Reconcile every IB account present in the orders payload."""
    ticks = ticks if ticks is not None else load_tick_sizes()
    accounts_out: list[dict[str, Any]] = []
    any_could_not_look = False
    total_findings = 0

    for acct in orders_doc.get("accounts", []):
        acct_id = acct.get("account_id")
        read_state = acct.get("read_state")

        if read_state == "not_ib":
            accounts_out.append({"account_id": acct_id, "state": "not_ib",
                                 "positions": [], "findings": 0})
            continue

        if read_state != "orders_read" or acct.get("orders") is None:
            # We did not look. This is NOT "clean" and NOT "0 findings".
            any_could_not_look = True
            accounts_out.append({
                "account_id": acct_id, "state": "could_not_look",
                "read_state": read_state, "error": acct.get("error"),
                "positions": [], "findings": None,
            })
            continue

        orders = acct.get("orders") or []
        rows = [p for p in positions if p.get("account") == acct_id]
        graded = []
        for tr in rows:
            sym = str(tr.get("symbol") or "").upper()
            # `None`, NEVER a 0.01 default. An assumed tick rescales the
            # tolerance silently, and too wide a tolerance turns a real
            # divergence into a clean pass -- the failure direction that
            # matters. `grade_protection_price` reports `no_tick_size`
            # for it, which this file surfaces as its own finding kind.
            tick = ticks.get(sym) or _FALLBACK_TICKS.get(sym)
            graded.append(reconcile_position(tr, orders, tick, tick_tolerance))
        n = sum(len(g["findings"]) for g in graded)
        total_findings += n
        accounts_out.append({
            "account_id": acct_id, "state": "reconciled",
            "resting_legs": len(orders), "open_journal_rows": len(rows),
            "positions": graded, "findings": n,
        })

    return {
        "captured_at": orders_doc.get("captured_at"),
        "accounts": accounts_out,
        "total_findings": total_findings,
        "any_could_not_look": any_could_not_look,
    }


# --------------------------------------------------------------------------
# change detection
# --------------------------------------------------------------------------
def fingerprint(result: dict[str, Any]) -> tuple[str, str]:
    """Stable identity of the GRADED STATE, for a scheduled caller.

    WHY A FINGERPRINT AND NOT AN ALARM ON ``total_findings``
    =======================================================
    Both of this tool's alarm-shaped outputs are TRUE IN THE STEADY STATE, so a
    caller that pinged on either would ping on every single run. Measured live
    2026-08-21T06:39Z on ``ib_paper``/``ib_live``:

      * ``total_findings`` = 3 -- MGC 4773 ``target_naked_declared``, MES 4350
        ``target_naked_declared`` + ``stop_price_diverges``. Standing, and the
        repair for them is DECLINED/Tier-3, so they will not clear on their own.
      * ``any_could_not_look`` = True -- ``ib_live`` is a declared account the
        stack deliberately never dials, so exit 3 is the NORMAL result, not an
        outage.

    An alarm that fires constantly and is routinely walked past is itself a P1
    bug (``CLAUDE.md`` s "If you see something, say something"), so the caller
    records EVERY run and speaks only when this fingerprint MOVES.

    WHAT IS IN IT, AND WHAT IS DELIBERATELY NOT
    ===========================================
    In: each account's ``state`` (so ``ib_paper`` slipping to
    ``could_not_look`` -- a wedged gateway -- moves it, while ``ib_live``
    sitting there forever does not), and the identity of every finding as
    ``(account, trade_id, symbol, kind)``.

    NOT in: the MAGNITUDE of a finding. MES 4350 drifting from 69 ticks to 200
    is the SAME fingerprint and will NOT re-alert. That is a real limitation,
    stated rather than hidden: this answers "is the set of problems the same?",
    never "has an existing problem got worse?". The caller therefore always
    rewrites the current numbers into its tracking record, so the magnitude is
    visible to a reader even on a run that stays silent.

    Returns ``(digest, body)`` -- the body is the exact preimage, so a reader
    can always see WHY two runs differ instead of comparing two opaque hashes.
    """
    parts: list[str] = []
    for acct in result.get("accounts", []):
        aid = str(acct.get("account_id"))
        parts.append(f"A:{aid}={acct.get('state')}")
        for pos in acct.get("positions") or []:
            for f in pos.get("findings") or []:
                parts.append(
                    f"F:{aid}:{pos.get('trade_id')}:{pos.get('symbol')}:{f.get('kind')}")
    body = "\n".join(sorted(parts))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16], body


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def render(result: dict[str, Any]) -> str:
    out: list[str] = []
    out.append(f"broker-bracket reconcile   captured_at={result.get('captured_at')}")
    for acct in result["accounts"]:
        st = acct["state"]
        if st == "not_ib":
            continue
        if st == "could_not_look":
            out.append(f"\n  {acct['account_id']}: COULD NOT LOOK "
                       f"(read_state={acct.get('read_state')!r}) "
                       f"-- nothing was graded for this account")
            continue
        out.append(f"\n  {acct['account_id']}: {acct['open_journal_rows']} open "
                   f"journal row(s) vs {acct['resting_legs']} resting leg(s)")
        for p in acct["positions"]:
            tag = "clean" if p["clean"] else f"{len(p['findings'])} FINDING(S)"
            out.append(f"    {p['symbol']:<5} trade {p['trade_id']:<6} "
                       f"size {p['size']:<7g} stop {p['stop_qty']:<7g} "
                       f"target {p['target_qty']:<7g}  {tag}"
                       + (f"  [{','.join(p['info'])}]" if p["info"] else ""))
            for f in p["findings"]:
                out.append(f"        - {f['kind']}: {f['detail']}")
    if result["any_could_not_look"]:
        out.append("\n  NOTE: at least one account could not be read. "
                   "A finding count of 0 for those accounts would be a "
                   "fabricated clean bill -- they were not graded.")
    out.append(f"\n  total findings: {result['total_findings']}")
    return "\n".join(out)


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------
def _self_test() -> int:
    """Prove the probe can find each positive AND that it stays quiet on a
    correctly-bracketed position.

    A detector that flags everything is as broken as one that flags nothing,
    which is why MHG -- a position holding BOTH a stop and a target at the
    declared levels, in one OCA group -- is the control and MUST pass.
    """
    ticks = {"MES": 0.25, "MGC": 0.10, "MHG": 0.0005}
    failures: list[str] = []

    total = [0]

    def check(name: str, cond: bool) -> None:
        total[0] += 1
        print(f"  {'ok  ' if cond else 'FAIL'} {name}")
        if not cond:
            failures.append(name)

    def kinds(trade, orders, sym="MES"):
        tick = ticks[sym]
        return {f["kind"] for f in reconcile_position(trade, orders, tick)["findings"]}

    # ---- the CONTROL: MHG, fully and correctly bracketed ----------------
    mhg = {"id": "4796", "symbol": "MHG", "qty": 29.0,
           "stopLoss": 6.22171429, "takeProfit": 7.141302}
    mhg_orders = [
        {"symbol": "MHG", "order_type": "STP", "total_quantity": 29.0,
         "aux_price": 6.2215, "lmt_price": 0.0, "oca_group": "308977633",
         "order_id": 399},
        {"symbol": "MHG", "order_type": "LMT", "total_quantity": 29.0,
         "aux_price": 0.0, "lmt_price": 7.1415, "oca_group": "308977633",
         "order_id": 398},
    ]
    check("CONTROL MHG (stop+target at declared levels) is CLEAN",
          kinds(mhg, mhg_orders, "MHG") == set())

    # ---- the live MGC state: target-naked with a declared target --------
    mgc = {"id": "4773", "symbol": "MGC", "qty": 95.0,
           "stopLoss": 4371.1469, "takeProfit": 4393.02071429}
    mgc_orders = [{"symbol": "MGC", "order_type": "STP", "total_quantity": 95.0,
                   "aux_price": 4371.1, "lmt_price": 0.0,
                   "oca_group": "oca-protect-389", "order_id": 391}]
    k = kinds(mgc, mgc_orders, "MGC")
    check("MGC target-naked-with-declared-TP is FLAGGED",
          "target_naked_declared" in k)
    check("MGC stop at 4371.1 vs declared 4371.1469 is NOT a price finding "
          "(within one tick -- venue rounding)",
          "stop_price_diverges" not in k)

    # ---- the live MES state: the price divergence nothing else sees -----
    # `side` matches what /api/bot/positions actually emits ("buy"/"sell" via
    # dashboard._normalise_side), NOT "long"/"short". Omitting it here is how
    # the unknown-direction defect in grade_protection_price was found: the
    # grader read a missing direction as SHORT and published an exposure label
    # that was exactly backwards. It now returns None for that, and this
    # fixture carries the key the live payload carries.
    mes = {"id": "4350", "symbol": "MES", "qty": 15.0, "side": "buy",
           "stopLoss": 7533.696429, "takeProfit": 8390.59025}
    mes_orders = [{"symbol": "MES", "order_type": "STP", "total_quantity": 15.0,
                   "aux_price": 7516.5, "lmt_price": 0.0,
                   "oca_group": "oca-protect-336", "order_id": 338}]
    k = kinds(mes, mes_orders)
    check("MES stop_price_diverges (69 ticks) is FLAGGED", "stop_price_diverges" in k)
    check("MES target_naked_declared is FLAGGED", "target_naked_declared" in k)
    check("MES is NOT reported as stop-naked (quantity IS correct -- this is "
          "exactly what the quantity-only grade misses)",
          "stop_naked" not in k and "stop_partial" not in k)

    # ---- an UNKNOWN tick is graded as ungradeable, never as agreeing ----
    # The old code defaulted an unresolvable tick to 0.01. For MES (true tick
    # 0.25) that is 25x too TIGHT and would over-report; for MHG (0.0005) it is
    # 20x too WIDE and would hide a real divergence behind a tolerance nobody
    # chose. Either direction is a verdict about a number that was guessed.
    def kinds_no_tick(trade, orders):
        return {f["kind"] for f in
                reconcile_position(trade, orders, None)["findings"]}
    k = kinds_no_tick(mes, mes_orders)
    check("an unresolvable tick reports *_price_ungradeable, NOT agreement",
          "stop_price_ungradeable" in k)
    check("an unresolvable tick does NOT also claim a graded divergence",
          "stop_price_diverges" not in k)
    # And the non-price findings must survive: a missing tick blinds the PRICE
    # axis only. Grading the quantity axis does not need one.
    check("an unresolvable tick still grades the QUANTITY axis "
          "(target_naked_declared survives)", "target_naked_declared" in k)

    # ---- exposure names the consequence, which |diff| cannot ------------
    # MES 4350 is LONG and its only stop rests 17.2 pts BELOW the declared
    # level: risk that was never agreed to. The mirror -- a stop equally far
    # ABOVE -- is the same magnitude and the opposite meaning, so a build that
    # reports only |diff| cannot tell a reviewer which one they are looking at.
    f_below = [f for f in reconcile_position(mes, mes_orders, ticks["MES"])["findings"]
               if f["kind"] == "stop_price_diverges"]
    check("a long stop BELOW its declared level is graded more_exposed",
          len(f_below) == 1 and f_below[0].get("exposure") == "more_exposed")
    above = [dict(mes_orders[0], aux_price=7533.696429 + 17.196429)]
    f_above = [f for f in reconcile_position(mes, above, ticks["MES"])["findings"]
               if f["kind"] == "stop_price_diverges"]
    check("a long stop ABOVE its declared level is graded exits_earlier -- "
          "same |diff|, opposite consequence",
          len(f_above) == 1 and f_above[0].get("exposure") == "exits_earlier")
    check("the two mirrored divergences carry the SAME magnitude, so the "
          "direction is the ONLY thing separating them",
          abs(f_below[0]["delta"] - f_above[0]["delta"]) < 1e-6)

    no_side = {k: v for k, v in mes.items() if k != "side"}
    f_nodir = [f for f in reconcile_position(no_side, mes_orders, ticks["MES"])["findings"]
               if f["kind"] == "stop_price_diverges"]
    check("a trade with NO readable direction still reports the divergence "
          "but NO exposure -- the geometry is measurable, its consequence is not",
          len(f_nodir) == 1 and f_nodir[0].get("exposure") is None
          and f_nodir[0].get("side_of_declared") == "below")

    # ---- the comparator is SHARED with the live sweep, not a copy -------
    # If this file ever regrows its own arithmetic, this check fails rather
    # than the two definitions silently drifting apart on a live position.
    from src.runtime import protection_price as _pp
    v = _pp.grade_protection_price(
        declared=7533.696429, resting_prices=[7516.5], direction="long",
        side="stop", tick_size=0.25)
    check("the shared grader independently returns 'diverges' for MES 4350",
          v["state"] == _pp.PRICE_DIVERGES and round(v["ticks"]) == 69)

    # ---- the historical over-cover: 30 stop vs 15, two OCA groups -------
    over = [
        {"symbol": "MES", "order_type": "STP", "total_quantity": 15.0,
         "aux_price": 7516.5, "lmt_price": 0.0, "oca_group": "oca-protect-336",
         "order_id": 338},
        {"symbol": "MES", "order_type": "STP", "total_quantity": 15.0,
         "aux_price": 7533.75, "lmt_price": 0.0, "oca_group": "oca-protect-373",
         "order_id": 375},
    ]
    k = kinds(mes, over)
    check("historical MES over-cover (30 vs 15) is FLAGGED", "stop_over_cover" in k)
    check("historical MES DISJOINT OCA groups are FLAGGED", "stop_disjoint_oca" in k)
    check("with order 375 present the declared stop IS represented, so no "
          "price finding -- the divergence appeared when 375 was cancelled",
          "stop_price_diverges" not in k)

    # ---- two stops in ONE group are one stop's worth, not over-cover ----
    same_group = [dict(o, oca_group="oca-protect-336") for o in over]
    k = kinds(mes, same_group)
    check("two stops in the SAME OCA group are NOT over-cover", "stop_over_cover" not in k)
    check("two stops in the SAME OCA group are NOT disjoint", "stop_disjoint_oca" not in k)

    # ---- stop-limit must classify as a STOP, never as a target ----------
    check("'STP LMT' classifies as stop", protective_leg_side("STP LMT") == "stop")
    check("'LMT' classifies as target", protective_leg_side("LMT") == "target")
    check("'TRAIL LIMIT' classifies as stop", protective_leg_side("TRAIL LIMIT") == "stop")
    check("'MKT' is not protective", protective_leg_side("MKT") is None)
    stp_lmt = [{"symbol": "MES", "order_type": "STP LMT", "total_quantity": 15.0,
                "aux_price": 7533.70, "lmt_price": 7533.50,
                "oca_group": "g1", "order_id": 1}]
    check("a stop-limit does NOT manufacture target coverage",
          "target_naked_declared" in kinds(mes, stp_lmt))

    # ---- an undeclared target is INFO, never a finding ------------------
    # The stop here MATCHES the declared level, so the only thing this fixture
    # can possibly surface is the target-undeclared state -- an earlier draft
    # reused the diverging MES stop and the case failed for an unrelated
    # reason, which is exactly the confounded-fixture trap.
    no_tp = {"id": "1", "symbol": "MES", "qty": 15.0,
             "stopLoss": 7516.5, "takeProfit": None}
    r = reconcile_position(no_tp, mes_orders, ticks["MES"])
    check("no declared target => NOT a finding (Tier-3 strategy question)",
          r["clean"] and "target_absent_undeclared" in r["info"])

    # ---- stop-naked is still caught -------------------------------------
    check("a position with no stop at all is FLAGGED stop_naked",
          "stop_naked" in kinds(mes, []))

    # ---- could_not_look never reads as clean ----------------------------
    res = reconcile({"accounts": [{"account_id": "ib_paper",
                                   "read_state": "could_not_look",
                                   "orders": None}]}, [])
    a = res["accounts"][0]
    check("could_not_look yields findings=None (never 0) and sets the flag",
          a["findings"] is None and res["any_could_not_look"] is True)

    print(f"\n  {'PASS' if not failures else 'FAIL'}: "
          f"{total[0] - len(failures)}/{total[0]} checks")
    return 1 if failures else 0


# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--orders-json", help="payload of /api/diag/ib_open_orders")
    ap.add_argument("--positions-json",
                    help="payload of /api/bot/positions?include_paper=true")
    ap.add_argument("--tick-tolerance", type=float, default=_DEFAULT_TICK_TOLERANCE,
                    help="ticks of price disagreement tolerated (default 1.0)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--fingerprint", action="store_true",
                    help="print the graded-state digest, then the preimage "
                         "(for a scheduled caller's change detection)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    if not args.orders_json or not args.positions_json:
        ap.error("--orders-json and --positions-json are required "
                 "(or use --self-test)")

    try:
        with open(args.orders_json, "r", encoding="utf-8") as fh:
            orders_doc = json.loads(fh.read().split("diag_fetch:")[0])
        with open(args.positions_json, "r", encoding="utf-8") as fh:
            pos = json.loads(fh.read().split("diag_fetch:")[0])
    except Exception as exc:  # noqa: BLE001
        print(f"could not parse inputs: {exc}", file=sys.stderr)
        return 2

    positions = pos if isinstance(pos, list) else pos.get("positions", [])
    result = reconcile(orders_doc, positions, tick_tolerance=args.tick_tolerance)

    if args.fingerprint:
        digest, body = fingerprint(result)
        print(digest)
        print(body)
    else:
        print(json.dumps(result, indent=2) if args.json else render(result))

    if result["any_could_not_look"]:
        return 3
    return 1 if result["total_findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
