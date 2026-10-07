#!/usr/bin/env python3
"""B6 — the prop bar as an EXPECTED-VALUE question: E[profit before the account dies] > the account cost.

WHAT THIS ANSWERS
-----------------
Operator directive 2026-09-22 (checklist row B6): *"it's ok if there is ever
breach, the idea is that we want to promote strategies that create a portfolio
that we would statistically expect to produce a profit (over the initial account
cost of about 1%) before the account dies."* Breach is the accepted cost of the
bet, not a failure to prevent. So the quantity is a SURVIVAL-WEIGHTED EXPECTED
VALUE over ONE account life:

    net_per_life = (trader share of every payout banked before the account dies) - account fee

and the bar is ``E[net_per_life] > 0``. This tool Monte-Carlos that quantity for
a PORTFOLIO of legs traded on ONE shared prop account.

WHY A NEW TOOL AND NOT `src/prop/montecarlo.py::run_ev_montecarlo`
-------------------------------------------------------------------
That precedent was audited before writing this (Generation Discipline Rule 2).
It is sound for what it does, but it cannot answer B6, for five named reasons:

  1. ONE SHARED POSITION AT A TIME. It walks a single ledger sequentially, so a
     portfolio of legs whose trades OVERLAP on one account cannot be expressed.
     On breakout_1 two 1.5%-risk positions open together already carry ~2R of a
     2R daily-loss room — overlap is the mechanism that kills the account, so a
     model without it is not a model of this account.
  2. DAILY LOSS IS REALISED-ONLY (its own docstring says so, "optimistic"). The
     real rule fires on intraday EQUITY vs the 00:30-UTC balance.
  3. IT PRICES NO BREAKOUT COSTS. It replays a Bybit-cost ledger; Breakout
     charges its own commission and a daily swap on notional.
  4. ITS HEADLINE IS NET-$ PER CALENDAR HORIZON WITH RE-BUY, not E[net] per
     account life, which is the operator's bar.
  5. IT CARRIES THE EVAL BALANCE INTO THE FUNDED PHASE and banks the eval profit.
     The evidence available says a passed account is re-issued at the starting
     balance (see ASSUMPTIONS A6), which removes up to $400 of phantom payout.

INPUT SCHEMA (the contract P2 writes against) — JSONL, one closed trade per line
--------------------------------------------------------------------------------
Required on every row:
    entry_time   ISO-8601 UTC timestamp ("2025-09-28 22:00:00+00:00" or "...Z")
    exit_time    ISO-8601 UTC timestamp, >= entry_time
and EITHER (a) the raw fields, from which Breakout costs are recomputed
(``--costs breakout``, the default):
    gross_r      trade outcome in R BEFORE any cost (R = entry->initial-stop distance)
    entry        entry price (> 0)
    sl           initial stop price (!= entry)
OR (b) an already-costed outcome (``--costs as_given``):
    net_r        trade outcome in R net of the costs the producer applied
Optional:
    leg          leg name. Otherwise the leg comes from ``--trades LEG=PATH``.
    mfe_r        max favourable excursion in R (gross). Used ONLY by the
                 ``bridge`` intra-trade model; rows without it fall back to the
                 ``stop`` bound for that trade and are counted in the output.
    direction    informational only. Swap is side-agnostic on Breakout.

The per-trade rows the harness already commits beside every evidence record
(``comms/strategy_evidence/runs/<date>/<leg>__trades.jsonl``) satisfy schema (a)
verbatim, which is how today's book is scored.

MODEL — what is simulated
-------------------------
* ONE account life = buy (fee) -> evaluation -> [pass -> approval gap -> funded
  -> payouts] -> death, or the horizon, whichever comes first.
* Trades from all legs share ONE balance, overlapping in calendar time exactly
  as the resampled history overlapped them.
* Sizing: risk_usd = risk_pct x REALISED balance at entry — the live ticket says
  "RISK 1.5% of your CURRENT live balance" (src/prop/breakout_ticket.py);
  ``--sizing start`` sizes off the starting balance instead.
* Daily loss: the limit is ``daily_loss_pct x`` the realised balance at each
  00:30 UTC reset; breach when EQUITY (realised + open-position marks) falls
  to/through ``day_start x (1 - daily_loss_pct)``. Checked at every event.
* Static DD: breach when equity falls to/through ``start x (1 - max_dd_pct)``.
  Not trailing, not raised on payout (breakout.yaml ``drawdown_type: static``).
* Evaluation passes when the realised balance reaches ``start x (1+target)``.
* Payouts: first at ``first_payout_after_days`` after funding, then every
  ``payout_frequency_days``; each withdraws ALL realised balance above the
  starting balance (breakout.yaml ``withdrawal_policy: above_start / bank_asap``)
  when at least ``min_withdrawal_usd``; the trader banks ``profit_split`` of it.
* Rebuy: lives are i.i.d., so EV per life IS the per-purchase EV. The long-run
  rate of running the programme with rebuy-on-death is the renewal-reward
  ratio ``E[net] / E[lifetime]``, reported per 365 days.

THE INTRA-TRADE PATH — what this cannot see, and how it is bounded
-------------------------------------------------------------------
The evidence carries each trade's entry, exit, final R and (usually) MFE — NOT
its intra-trade price path. The daily-loss and DD rules fire on intraday equity,
so the unseen path matters. Three marks for an OPEN position, reported side by
side; the truth under the harness's fill model lies between the first and last:

  realized  open positions mark at 0 until they close. No intra-trade excursion
            at all. OPTIMISTIC — the realised-only model the precedent uses.
  bridge    each trade's intra-trade MINIMUM is sampled from the Brownian-bridge
            minimum distribution between 0 and its final gross R, with the
            bridge variance calibrated so its MEDIAN MAXIMUM equals the trade's
            recorded MFE; floored at the stop. The trade is marked at that
            minimum for its WHOLE life, so the depth is modelled but the timing
            is still worst-case. INTERMEDIATE, and it is a model, not a bound.
  stop      every open position is marked at its initial stop (-1R minus its
            costs) for its whole life, all of them at once. A trade that did
            not lose can approach but never touch its stop, so its mark sits a
            hair above. The UPPER BOUND on breach (so the LOWER bound on EV)
            under the harness's fill model — it cannot see a gap THROUGH the
            stop that the harness itself did not record.

ASSUMPTIONS — stated because each one moves the answer
-------------------------------------------------------
  A1  EVERY ticket is placed, at the harness's entry and exit. The bridge is
      manual; skipped or late tickets are not modelled.
  A2  The per-trade outcome distribution is the committed evidence window's.
      The block bootstrap cannot invent a regime the window did not contain.
  A3  Calendar blocks (default 30 days) are drawn with replacement from the
      pooled history and shifted by WHOLE days, so cross-leg overlap within a
      block, 00:30 resets and midnight swap debits are preserved exactly.
      Dependence ACROSS block seams is lost. A trade whose leg is already
      holding a position is skipped (live suppresses re-entry on one leg).
  A4  Breakout costs (``--costs breakout``): commission 0.04%/side = 8 bps
      round trip; slippage 3 bps round trip (the harness's own figure — manual
      placement may be worse); swap 0.033%/day of notional debited once per
      00:00-UTC crossing (DXTrade's schedule, ``--swap-model dxtrade``) or
      prorated by hold time (``--swap-model prorated``, conservative). Sources:
      docs/research/eth-pullback-prop-swap-aware-2026-06-25.md, which cites
      Breakout's help centre; NOT re-verified against the FAQ by this tool.
  A5  Open positions at the moment the evaluation passes are dropped (the
      account is re-issued), and contribute nothing to either phase.
  A6  The funded account starts FRESH at the starting balance
      (``--funded-start fresh``). OPERATOR-CONFIRMED 2026-09-27 ~11:12Z ("Resets
      to a fresh $5,000"), declared as ``economics.funded_start: fresh`` in
      config/prop_rulesets/breakout.yaml and read from there as the default.
      ``--funded-start carry`` remains only as a counterfactual.
  A7  A 1-day approval gap (KYC + agreement, "12-24 hours") between passing and
      the funded account trading. No trades are taken in it.
  A8  NO first-payout fee refund. OPERATOR-CONFIRMED 2026-09-27 ~11:12Z ("No
      refund"), declared as ``economics.first_payout_fee_refund: false`` in the
      ruleset and read from there as the default. ``--first-payout-refund``
      remains only as a counterfactual.
  A9  A life still alive at the horizon (default 730 days) is scored at what it
      has BANKED — its remaining value is counted as zero. Conservative; the
      fraction alive at the horizon is reported so the truncation is visible.
  A10 Withdrawals are taken whether or not positions are open, and lower the
      day's reference balance by the amount withdrawn (a withdrawal is not a
      trading loss). The prop ticket risk gate is in ``annotate`` mode live, so
      no entry is refused for lack of cushion.

UNCERTAINTY
-----------
Two layers, never folded together:
  * Simulation noise: the mean over all simulated lives, with its standard error.
  * EVIDENCE uncertainty (the one that matters at n~200): an OUTER bootstrap
    re-draws the history itself (whole calendar blocks, with replacement) and
    re-runs the inner simulation on each re-drawn history. The 5th-95th
    percentile of the per-history mean is the reported CI.

# wiring: manual-only - a research CLI run by a session (and by P2's candidate lane) on
# committed evidence; no schedule should score a prop book without a human reading it.

Tier-1 research tooling. Pure, deterministic given a seed. No network, no live
path, no config write.

Run:
    python3 scripts/research/prop_ev_sim.py --self-test
    python3 scripts/research/prop_ev_sim.py --book breakout_1
    python3 scripts/research/prop_ev_sim.py --trades legA=a.jsonl --trades legB=b.jsonl
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

REPO = Path(__file__).resolve().parents[2]
DEFAULT_RULESET = REPO / "config" / "prop_rulesets" / "breakout.yaml"
TOOL = "scripts/research/prop_ev_sim.py"

MARK_MODES = ("realized", "bridge", "stop", "path")
#: The modes the REGISTERED V1 rule reads. `path` was added after V1 was run.
V1_MODES = ("realized", "bridge", "stop")
_EPS_R = 1e-6          # a non-losing trade approaches but never touches its stop
_DAY_RESET = 0.5 / 24  # 00:30 UTC as a fraction of a day

# ---------------------------------------------------------------------------
# The decision rule — REGISTERED HERE, BEFORE the tool was run on the real
# book (committed ahead of the scoring commit on branch claude/w6-prop-ev).
# ---------------------------------------------------------------------------
DECISION_RULE_ID = "RULE-B6-PROP-EV-PER-ACCOUNT-LIFE"
DECISION_RULE_REGISTERED_AT = "2026-09-27T06:27:22Z"
DECISION_RULE = (
    "Operator bar (B6, 2026-09-22): E[profit before the account dies] > the account "
    "cost. Predicate over this tool's output for ONE account life, net of the "
    "account fee: PASS if the 5th percentile of the outer-bootstrap (evidence) "
    "distribution of mean net-$ per life is > 0 under the `bridge` intra-trade "
    "model AND the point estimate is > 0 under the `stop` bound; FAIL if the 95th "
    "percentile of that distribution is < 0 under the `realized` (optimistic) "
    "model; otherwise INDETERMINATE. Descriptive of the book; does not itself "
    "authorize or perform any roster, execution or sizing change (Tier-3)."
)


# V2 — REGISTERED 2026-09-27 AFTER V1 WAS RUN ON breakout_1, and it says so.
# Why it exists: V1's `bridge` clause marks each trade at its sampled minimum for
# its WHOLE life, so two overlapping trades' minima always coincide; on
# breakout_1 that timing assumption, not the excursion depth, decides the answer
# (bridge ~= stop). `path` samples WHEN the excursion happens (hourly bridge
# path). V2 is the rule for candidates scored FROM HERE ON (P2). It must NOT be
# used to re-grade breakout_1's 2026-09-27 run, whose registered verdict is V1's.
DECISION_RULE_V2_ID = "RULE-B6-PROP-EV-PER-ACCOUNT-LIFE-V2"
DECISION_RULE_V2_REGISTERED_AT = "2026-09-27T07:05:00Z"
DECISION_RULE_V2 = (
    "Same bar as V1 (E[net-$ per account life, after the fee] > 0). PASS if the 5th "
    "percentile of the outer-bootstrap (evidence) distribution of mean net-$ per life "
    "under the `path` model is > 0; FAIL if its 95th percentile is < 0; otherwise "
    "INDETERMINATE. `realized` and `stop` are reported as the optimistic / pessimistic "
    "bounds and do not enter the predicate. Descriptive; authorizes nothing (Tier-3)."
)


# ---------------------------------------------------------------------------
# Rules + economics
# ---------------------------------------------------------------------------
def _hhmm_to_day_fraction(v: Any) -> float:
    """'HH:MM' -> fraction of a day; None keeps the historical 00:30 UTC default."""
    if v is None:
        return _DAY_RESET
    hh, mm = str(v).split(":")
    return (int(hh) + int(mm) / 60.0) / 24.0


@dataclass
class PropRules:
    start: float = 5000.0
    target_pct: float = 0.10
    daily_loss_pct: float = 0.03
    max_dd_pct: float = 0.06
    fee: float = 45.0
    profit_split: float = 0.80
    first_payout_after_days: float = 14.0
    payout_frequency_days: float = 7.0
    min_withdrawal: float = 50.0
    withdraw_buffer: float = 0.0
    # Operator-confirmed Breakout terms (2026-09-27): the funded account resets
    # to a fresh start balance, and the eval fee is not refunded.
    funded_start: str = "fresh"
    first_payout_refund: bool = False
    # Daily-loss reset instant as a fraction of a UTC day. Breakout: 00:30 UTC.
    # A ruleset may declare `limits.daily_loss_reset_utc: "HH:MM"` (Tradeify 247: 22:00).
    day_reset: float = _DAY_RESET
    # ── Velotrade-shaped rules (lane PROP-FIRM-DEEP, 2026-10-04). ALL OFF BY DEFAULT so
    # every Breakout / Tradeify / HyroTrader result published before this date is
    # unchanged. A ruleset opts in by declaring the keys named in from_yaml().
    # Qualifying trading days: a day counts only if the REALISED P&L closed inside it is
    # >= qual_day_profit_pct x START. The evaluation cannot pass until qual_day_min_days
    # such days have accrued (checked at each reset and each exit); the funded count
    # restarts at zero and the FIRST payout waits for funded_qual_days_before_payout.
    qual_day_min_days: int = 0
    qual_day_profit_pct: Optional[float] = None       # None => the gate is OFF
    funded_qual_days_before_payout: int = 0
    # Payout cap: the first `payout_cap_first_n` payouts bank at most
    # payout_cap_mult_fee x fee each; the excess LEAVES the account and is forfeited
    # (Velotrade Terms 5.4(f): "All Payouts are full withdrawals ... Any balance
    # exceeding the Payout Cap is forfeited").
    payout_cap_first_n: int = 0
    payout_cap_mult_fee: float = 0.0
    # Daily-loss reset basis: "balance" (Breakout/Tradeify, the default) or
    # "max_balance_equity" (Velotrade: "whichever is higher, your balance or your
    # equity" at 00:30 UTC -- the STRICTER basis whenever a position is in profit).
    day_reset_basis: str = "balance"

    @classmethod
    def from_yaml(cls, path: Path) -> "PropRules":
        import yaml
        d = yaml.safe_load(Path(path).read_text())
        econ = d.get("economics") or {}
        pay = econ.get("payout") or {}
        wp = econ.get("withdrawal_policy") or {}
        lim = d.get("limits") or {}
        if (lim.get("drawdown_type") or "static") != "static":
            raise ValueError("only a STATIC drawdown ruleset is modelled")
        if wp.get("mode", "above_start") != "above_start":
            raise ValueError("only withdrawal_policy.mode above_start is modelled")
        ev = d["phases"]["evaluation"]
        qual_pct = ev.get("qualifying_day_profit_pct")
        basis = str(lim.get("daily_loss_reset_basis") or "balance")
        if basis not in ("balance", "max_balance_equity"):
            raise ValueError(f"limits.daily_loss_reset_basis must be balance|max_balance_equity, got {basis!r}")
        return cls(
            start=float(d["account_size_usd"]),
            target_pct=float(d["phases"]["evaluation"]["profit_target_pct"]),
            daily_loss_pct=float(lim["daily_loss_pct"]),
            max_dd_pct=float(lim["max_drawdown_pct"]),
            fee=float(econ["account_fee_usd"]),
            profit_split=float(d["profit_split"]),
            first_payout_after_days=float(pay["first_payout_after_days"]),
            payout_frequency_days=float(pay["payout_frequency_days"]),
            min_withdrawal=float(pay.get("min_withdrawal_usd") or 0.0),
            withdraw_buffer=float(wp.get("buffer_usd") or 0.0),
            funded_start=str(econ.get("funded_start") or "fresh"),
            first_payout_refund=bool(econ.get("first_payout_fee_refund") or False),
            day_reset=_hhmm_to_day_fraction(lim.get("daily_loss_reset_utc")),
            qual_day_min_days=int(ev.get("min_trading_days") or 0) if qual_pct is not None else 0,
            qual_day_profit_pct=(float(qual_pct) if qual_pct is not None else None),
            funded_qual_days_before_payout=(int(pay.get("min_trading_days_before_payout") or 0)
                                            if qual_pct is not None else 0),
            payout_cap_first_n=int(pay.get("first_payouts_capped") or 0),
            payout_cap_mult_fee=float(pay.get("first_payout_cap_multiple_of_fee") or 0.0),
            day_reset_basis=basis,
        )


@dataclass
class SimConfig:
    risk_pct: float = 0.015
    sizing: str = "balance"          # balance | start | room
    room_frac: float = 0.45          # `room` sizing: max share of the binding cushion one new trade may risk
    min_risk_usd: float = 1.0        # a trade whose sized risk is below this is skipped (counted)
    start_balance: Optional[float] = None  # resume an EXISTING eval account at this balance (flat)
    funded_start: str = "fresh"      # fresh | carry
    approval_days: float = 1.0
    horizon_days: float = 730.0
    block_days: int = 30
    first_payout_refund: bool = False
    stop_at_pass: bool = False       # positive controls only: end the life at the eval pass
    # Per-position notional cap as a multiple of the CURRENT balance, per leg
    # (Tradeify 247: 5x BTC/ETH, 2x alts). Risk is scaled DOWN so notional <= cap x balance.
    lev_caps: Optional[Dict[str, float]] = None
    # Refuse an entry that would be simultaneously long and short the SAME symbol
    # across legs (Tradeify 247: hedging is banned). Needs `symbol`/`direction` on rows.
    no_hedge: bool = False


@dataclass
class CostConfig:
    mode: str = "breakout"           # breakout | as_given
    commission_bps_rt: float = 8.0
    slippage_bps_rt: float = 3.0
    swap_daily: float = 0.00033
    swap_model: str = "dxtrade"      # dxtrade | prorated


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------
@dataclass
class Trade:
    leg: str
    entry: float        # days since the history's day-0 00:00 UTC
    exit: float
    net_r: float        # after the cost model
    cost_r: float       # total cost in R charged by the cost model (>= 0)
    gross_r: Optional[float]
    mfe_r: Optional[float]
    stop_frac: Optional[float] = None   # |entry-sl|/entry when the row carries both
    symbol: Optional[str] = None
    direction: Optional[str] = None


def _parse_ts(v: Any) -> datetime:
    s = str(v).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _midnights_crossed(a: datetime, b: datetime) -> int:
    """00:00-UTC instants in (a, b] — DXTrade debits swap on positions open at 00:00."""
    return max(0, (b.date() - a.date()).days)


def trade_cost_r(row: Dict[str, Any], costs: CostConfig, t_in: datetime, t_out: datetime) -> float:
    entry = float(row["entry"])
    sl = float(row["sl"])
    if entry <= 0 or sl == entry:
        raise ValueError(f"row needs entry>0 and sl!=entry: {row}")
    risk_frac = abs(entry - sl) / entry
    fixed = (costs.commission_bps_rt + costs.slippage_bps_rt) / 1e4
    if costs.swap_model == "dxtrade":
        swap_days = float(_midnights_crossed(t_in, t_out))
    elif costs.swap_model == "prorated":
        swap_days = max(0.0, (t_out - t_in).total_seconds() / 86400.0)
    else:
        raise ValueError(f"unknown swap model {costs.swap_model!r}")
    return (fixed + costs.swap_daily * swap_days) / risk_frac


def load_rows(path: Path) -> List[Dict[str, Any]]:
    rows = []
    for i, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}:{i}: not JSON ({e})") from e
    return rows


def build_trades(rows_by_leg: Dict[str, List[Dict[str, Any]]], costs: CostConfig
                 ) -> Tuple[List[Trade], datetime, int]:
    """Validate rows, apply the cost model, place on a day-0-anchored clock.

    Returns (trades sorted by entry, day-0 as a UTC midnight, history length in
    whole days). Raises on any row that does not meet the schema — a silently
    dropped row is a changed population.
    """
    parsed: List[Tuple[str, datetime, datetime, Dict[str, Any]]] = []
    for leg, rows in rows_by_leg.items():
        for r in rows:
            name = str(r.get("leg") or leg)
            if "entry_time" not in r or "exit_time" not in r:
                raise ValueError(f"leg {name}: row missing entry_time/exit_time: {r}")
            a, b = _parse_ts(r["entry_time"]), _parse_ts(r["exit_time"])
            if b < a:
                raise ValueError(f"leg {name}: exit_time before entry_time: {r}")
            parsed.append((name, a, b, r))
    if not parsed:
        raise ValueError("no trades")
    first = min(p[1] for p in parsed)
    last = max(p[2] for p in parsed)
    day0 = datetime(first.year, first.month, first.day, tzinfo=timezone.utc)
    hist_days = int(math.ceil((last - day0).total_seconds() / 86400.0)) + 1

    out: List[Trade] = []
    for name, a, b, r in parsed:
        if costs.mode == "breakout":
            for k in ("gross_r", "entry", "sl"):
                if k not in r:
                    raise ValueError(f"leg {name}: --costs breakout needs `{k}`: {r}")
            c = trade_cost_r(r, costs, a, b)
            gross = float(r["gross_r"])
            net = gross - c
        elif costs.mode == "as_given":
            if "net_r" not in r:
                raise ValueError(f"leg {name}: --costs as_given needs `net_r`: {r}")
            net = float(r["net_r"])
            gross = float(r["gross_r"]) if r.get("gross_r") is not None else None
            c = (gross - net) if gross is not None else 0.0
        else:
            raise ValueError(f"unknown cost mode {costs.mode!r}")
        mfe = r.get("mfe_r")
        out.append(Trade(
            leg=name,
            entry=(a - day0).total_seconds() / 86400.0,
            exit=(b - day0).total_seconds() / 86400.0,
            net_r=net, cost_r=max(0.0, c), gross_r=gross,
            mfe_r=(float(mfe) if mfe is not None else None),
            stop_frac=(abs(float(r["entry"]) - float(r["sl"])) / float(r["entry"])
                       if r.get("entry") and r.get("sl") is not None and float(r["entry"]) > 0 else None),
            symbol=r.get("symbol"), direction=r.get("direction"),
        ))
    out.sort(key=lambda t: (t.entry, t.leg))
    return out, day0, hist_days


# ---------------------------------------------------------------------------
# Intra-trade marks
# ---------------------------------------------------------------------------
def stop_mark_r(t: Trade) -> float:
    """Worst the open trade can be marked while the harness still records it
    open: its initial stop, net of its costs. A trade that did NOT lose that
    much never touched it, so it sits _EPS_R above."""
    floor = -1.0 - t.cost_r
    if t.net_r <= floor:
        return t.net_r                  # it lost at least a full stop — mark the realised loss
    return floor + _EPS_R


def bridge_min_r(t: Trade, u: float) -> Optional[float]:
    """Sampled intra-trade minimum (gross R) of a Brownian bridge 0 -> gross_r.

    P(min <= x) = exp(-2 x (x - R) / s2) for x <= min(0, R), s2 = sigma^2 T.
    s2 is calibrated so the bridge's MEDIAN max equals the recorded MFE:
    P(max >= m) = exp(-2 m (m - R) / s2) = 1/2  =>  s2 = 2 m (m - R) / ln 2.
    Returns None when the row lacks what the calibration needs.
    """
    if t.gross_r is None or t.mfe_r is None:
        return None
    R = t.gross_r
    m = t.mfe_r
    top = max(0.0, R)
    if m <= top + 1e-9:
        return min(0.0, R)              # no excursion above the endpoints recorded
    s2 = 2.0 * m * (m - R) / math.log(2.0)
    u = min(max(u, 1e-12), 1.0)
    x = 0.5 * (R - math.sqrt(R * R - 2.0 * s2 * math.log(u)))
    return min(x, 0.0, R)


def open_mark_r(t: Trade, mode: str, rng: np.random.Generator, counters: Dict[str, int]) -> float:
    if mode == "realized":
        return 0.0
    if mode == "path":
        raise ValueError("`path` marks are time-varying — see sample_path_r")
    stop = stop_mark_r(t)
    if mode == "stop":
        return stop
    if mode == "bridge":
        g = bridge_min_r(t, float(rng.random()))
        if g is None:
            counters["bridge_fallback_to_stop"] = counters.get("bridge_fallback_to_stop", 0) + 1
            return stop
        # entry-side costs are paid at entry; mark the gross minimum net of them,
        # never below the stop mark and never below the realised outcome's floor.
        m = g - t.cost_r
        return max(m, stop) if t.net_r > -1.0 - t.cost_r else max(m, t.net_r)
    raise ValueError(f"unknown mark mode {mode!r}")


def sample_path_r(t: Trade, rng: np.random.Generator, counters: Dict[str, int]) -> np.ndarray:
    """`path` model: an HOURLY Brownian-bridge path of the open trade's mark (R,
    net of its full cost from entry), 0 -> gross_r over its hold.

    The bridge variance is calibrated as in :func:`bridge_min_r` (median maximum
    == the recorded MFE), then each path is drawn conditional on the two facts the
    row records: a non-loser never touches its stop, and the path's maximum lies
    within 10% (+0.05R) of the MFE (up to 40 draws; else the closest admissible
    draw, counted). A loser is clipped at its own final value. Hourly resolution:
    an intrabar dip deeper than the hourly sample is not seen (optimistic). Rows with no MFE fall back to the
    stop mark for their whole life (counted).
    """
    n = max(1, int(round((t.exit - t.entry) * 24.0)))
    if t.gross_r is None or t.mfe_r is None:
        counters["path_fallback_to_stop"] = counters.get("path_fallback_to_stop", 0) + 1
        return np.full(n + 1, stop_mark_r(t))
    R = t.gross_r
    top = max(0.0, R)
    m = t.mfe_r
    s2 = 2.0 * m * (m - R) / math.log(2.0) if m > top + 1e-9 else 0.0
    k = np.arange(n + 1) / n
    loser = t.net_r <= -1.0 - t.cost_r
    lo = min(-1.0, R) if loser else -1.0 + _EPS_R
    b = k * R
    if s2 > 0:
        # Condition on BOTH recorded facts: never touching the stop (non-losers)
        # AND a maximum close to the recorded MFE. Conditioning on the stop alone
        # lifts the median max ~8% above the MFE (caught by the self-test).
        tol = 0.10 * m + 0.05
        best, best_gap = None, math.inf
        for _ in range(40):
            w = np.concatenate(([0.0], np.cumsum(rng.normal(0.0, math.sqrt(s2 / n), n))))
            cand = w - k * w[-1] + k * R
            if not loser and cand.min() <= lo:
                continue
            gap = abs(float(cand.max()) - m)
            if gap < best_gap:
                best, best_gap = cand, gap
            if gap <= tol:
                break
        if best is None:
            counters["path_clipped_at_stop"] = counters.get("path_clipped_at_stop", 0) + 1
            best = cand
        elif best_gap > tol:
            counters["path_mfe_outside_band"] = counters.get("path_mfe_outside_band", 0) + 1
        b = best
    b = np.maximum(b, lo)
    b[-1] = R
    return b - t.cost_r


# ---------------------------------------------------------------------------
# History resampling
# ---------------------------------------------------------------------------
class History:
    """The pooled trade history, circular over `days`, sampled in whole-day blocks."""

    def __init__(self, trades: Sequence[Trade], days: int):
        self.trades = list(trades)
        self.days = int(days)
        self.entries = np.array([t.entry for t in self.trades], dtype=float)

    def block(self, offset_day: int, block_days: int) -> List[Tuple[float, Trade]]:
        """Trades whose entry lies in [offset, offset+block) on the circular
        clock, as (entry time relative to the block start, trade)."""
        out: List[Tuple[float, Trade]] = []
        lo = offset_day
        hi = offset_day + block_days
        i0, i1 = np.searchsorted(self.entries, [lo, hi], side="left")
        for t in self.trades[i0:i1]:
            out.append((t.entry - lo, t))
        if hi > self.days:                                    # wrapped part
            j1 = int(np.searchsorted(self.entries, hi - self.days, side="left"))
            for t in self.trades[:j1]:
                out.append((t.entry + self.days - lo, t))
        return out

    def redraw(self, rng: np.random.Generator, block_days: int) -> "History":
        """OUTER bootstrap: a same-length history rebuilt from whole blocks
        drawn with replacement (evidence uncertainty)."""
        n_blocks = max(1, int(math.ceil(self.days / block_days)))
        new: List[Trade] = []
        for k in range(n_blocks):
            off = int(rng.integers(0, self.days))
            for rel, t in self.block(off, block_days):
                shift = k * block_days + rel - t.entry
                new.append(Trade(t.leg, t.entry + shift, t.exit + shift, t.net_r,
                                 t.cost_r, t.gross_r, t.mfe_r))
        new.sort(key=lambda t: (t.entry, t.leg))
        return History(new, n_blocks * block_days)


def trade_stream(hist: History, rng: np.random.Generator, block_days: int, horizon: float):
    """Yield (entry, exit, trade) on the synthetic life clock, blocks laid end to end."""
    t0 = 0
    while t0 < horizon:
        off = int(rng.integers(0, hist.days))
        for rel, t in hist.block(off, block_days):
            e = t0 + rel
            yield e, e + (t.exit - t.entry), t
        t0 += block_days


# ---------------------------------------------------------------------------
# One account life
# ---------------------------------------------------------------------------
@dataclass
class Life:
    passed: bool = False
    died: bool = False
    death_cause: Optional[str] = None
    death_phase: Optional[str] = None
    lifetime_days: float = 0.0
    days_to_pass: Optional[float] = None
    banked: float = 0.0
    n_payouts: int = 0
    trades_taken: int = 0
    trades_skipped_leg_busy: int = 0
    alive_at_horizon: bool = False
    # Qualifying-day observability (lane VELOTRADE-FIT, 2026-10-06). The gate itself is
    # older (PROP-FIRM-DEEP); these two fields make it MEASURABLE whether the gate binds:
    # the first eval exit at which the balance reached the target (None if never), and the
    # qualifying days counted while still in evaluation. passed => target reached, so
    # P(pass) - P(target reached) is the share of lives the gate (plus a later death) cost.
    target_first_reached_days: Optional[float] = None
    qual_days_eval: int = 0
    # reset instants (days) at which each EVAL qualifying day was counted, in order --
    # so "P(5 qualifying days by day N)" and "expected qualifying days per N days" are
    # readable off a life without re-running it
    qual_day_times: List[float] = field(default_factory=list)

    def net(self, fee: float) -> float:
        return self.banked - fee


def simulate_life(hist: History, rules: PropRules, cfg: SimConfig, mode: str,
                  rng: np.random.Generator, counters: Optional[Dict[str, int]] = None,
                  stream=None) -> Life:
    counters = counters if counters is not None else {}
    life = Life()
    H = cfg.horizon_days
    start = rules.start
    target = start * (1.0 + rules.target_pct)
    floor = start * (1.0 - rules.max_dd_pct)
    dl = rules.daily_loss_pct
    withdraw_above = start + rules.withdraw_buffer

    phase = "eval"
    bal = float(cfg.start_balance) if cfg.start_balance is not None else start
    day_start = bal
    next_reset = rules.day_reset
    funded_at: Optional[float] = None
    next_payout: Optional[float] = None
    refund_due = cfg.first_payout_refund
    # qualifying-day gate (OFF unless rules.qual_day_profit_pct is set)
    qual_on = rules.qual_day_profit_pct is not None
    qual_days = 0
    day_realized = 0.0
    day_closed = 0
    # open positions: leg -> (exit_t, pnl_usd, mark_usd)
    open_pos: Dict[str, Tuple[float, float, float]] = {}
    open_trades: Dict[str, "Trade"] = {}
    exits: List[Tuple[float, str]] = []
    marks_sum = 0.0
    paths: Dict[str, Tuple[float, np.ndarray, float]] = {}   # `path` mode: leg -> (entry, marks_r, risk_usd)
    next_tick = math.inf

    src = stream if stream is not None else trade_stream(hist, rng, cfg.block_days, H)
    pending = next(src, None)

    def breached() -> Optional[str]:
        eq = bal + marks_sum
        if eq <= floor + 1e-9:
            return "static_drawdown"
        if eq <= day_start * (1.0 - dl) + 1e-9:
            return "daily_loss"
        return None

    now = 0.0
    while True:
        # next event: exit, entry, reset, payout, funding start, horizon
        cand: List[Tuple[float, int, str]] = [(H, 9, "horizon"), (next_reset, 1, "reset")]
        if exits:
            cand.append((exits[0][0], 2, "exit"))
        if pending is not None:
            cand.append((pending[0], 4, "entry"))
        if phase == "gap" and funded_at is not None:
            cand.append((funded_at, 0, "funded"))
        if phase == "funded" and next_payout is not None:
            cand.append((next_payout, 3, "payout"))
        if paths:
            cand.append((next_tick, 5, "tick"))
        now, _, kind = min(cand)

        if kind == "horizon":
            life.alive_at_horizon = True
            life.lifetime_days = H
            break

        if kind == "tick":
            for leg, (e0, pr, risk_usd) in paths.items():
                j = min(len(pr) - 1, int(round((now - e0) * 24.0)))
                x, pnl, old = open_pos[leg]
                new = pr[j] * risk_usd
                open_pos[leg] = (x, pnl, new)
                marks_sum += new - old
            next_tick = now + 1.0 / 24.0
        elif kind == "reset":
            if qual_on:
                if day_closed > 0 and day_realized >= rules.qual_day_profit_pct * start - 1e-9:
                    qual_days += 1
                    if phase == "eval":
                        life.qual_days_eval = qual_days
                        life.qual_day_times.append(now)
                day_realized = 0.0
                day_closed = 0
            # the 00:30 balance, excluding open positions -- or, on Velotrade, the
            # higher of balance and equity (open marks included) at that instant
            day_start = (max(bal, bal + marks_sum) if rules.day_reset_basis == "max_balance_equity"
                         else bal)
            next_reset += 1.0
            # Velotrade: "If you hit your profit target before completing 5 minimum
            # trading days, your phase is not passed yet" -- so the pass can land at a
            # reset, when the last qualifying day is counted.
            if (qual_on and phase == "eval" and bal >= target - 1e-9
                    and qual_days >= rules.qual_day_min_days):
                kind = "pass"
        elif kind == "funded":
            phase = "funded"
            next_payout = now + rules.first_payout_after_days
            day_start = bal
            qual_days = 0                   # Velotrade: "The count resets to zero when the funded account begins"
            day_realized = 0.0
            day_closed = 0
        elif kind == "payout":
            if (qual_on and life.n_payouts == 0
                    and qual_days < rules.funded_qual_days_before_payout):
                counters["payout_deferred_qual_days"] = counters.get("payout_deferred_qual_days", 0) + 1
                next_payout = now + 1.0     # re-ask tomorrow; the gate applies to the first payout only
                continue
            w = bal - withdraw_above
            if w >= max(rules.min_withdrawal, 1e-9):
                paid = w
                if life.n_payouts < rules.payout_cap_first_n and rules.payout_cap_mult_fee > 0:
                    paid = min(w, rules.payout_cap_mult_fee * rules.fee)
                    if paid < w:
                        counters["payout_forfeited_usd"] = counters.get("payout_forfeited_usd", 0.0) + (w - paid)
                        counters["payouts_capped"] = counters.get("payouts_capped", 0) + 1
                life.banked += paid * rules.profit_split
                bal -= w                    # a FULL withdrawal: the forfeited excess leaves the account too
                day_start -= w              # a withdrawal is not a trading loss (A10)
                life.n_payouts += 1
                if refund_due:
                    life.banked += rules.fee
                    refund_due = False
            next_payout = now + rules.payout_frequency_days
        elif kind == "exit":
            _, leg = heapq.heappop(exits)
            ex, pnl, mark = open_pos.pop(leg)
            paths.pop(leg, None)
            marks_sum -= mark
            bal += pnl
            day_realized += pnl
            day_closed += 1
            if phase == "eval" and bal >= target - 1e-9:
                if life.target_first_reached_days is None:
                    life.target_first_reached_days = now
                if not qual_on or qual_days >= rules.qual_day_min_days:
                    kind = "pass"
                else:
                    # target met, pass withheld: the qualifying-day gate is binding right now
                    counters["pass_deferred_qual_days"] = counters.get("pass_deferred_qual_days", 0) + 1
        if kind == "pass":
            life.passed = True
            life.days_to_pass = now
            if cfg.stop_at_pass:
                life.lifetime_days = now
                break
            phase = "gap"
            funded_at = now + cfg.approval_days
            # A5: the account is re-issued — open positions do not carry.
            open_pos.clear()
            paths.clear()
            exits.clear()
            marks_sum = 0.0
            if cfg.funded_start == "fresh":
                bal = start
            day_start = bal
            continue
        elif kind == "entry":
            e, x, t = pending
            pending = next(src, None)
            if phase == "gap":
                counters["skipped_in_approval_gap"] = counters.get("skipped_in_approval_gap", 0) + 1
            elif t.leg in open_pos:
                life.trades_skipped_leg_busy += 1
            elif cfg.no_hedge and any(
                    (o.symbol == t.symbol and o.direction != t.direction) for o in
                    (open_trades[k] for k in open_pos if k in open_trades)):
                counters["hedge_blocked"] = counters.get("hedge_blocked", 0) + 1
            else:
                if cfg.sizing == "room":
                    # Fit the NEW trade inside the binding cushion (static floor or today's
                    # daily limit), net of what open positions are already marked at.
                    eq = bal + marks_sum
                    room = min(eq - floor, eq - day_start * (1.0 - dl))
                    risk_usd = min(cfg.risk_pct * bal, cfg.room_frac * max(0.0, room))
                else:
                    risk_usd = cfg.risk_pct * (bal if cfg.sizing == "balance" else start)
                if cfg.lev_caps and t.stop_frac and t.leg in cfg.lev_caps:
                    capped = cfg.lev_caps[t.leg] * bal * t.stop_frac
                    if capped < risk_usd:
                        counters["lev_capped"] = counters.get("lev_capped", 0) + 1
                        risk_usd = capped
                if risk_usd < cfg.min_risk_usd:
                    counters["skipped_no_room"] = counters.get("skipped_no_room", 0) + 1
                    continue
                pnl = t.net_r * risk_usd
                if mode == "path":
                    pr = sample_path_r(t, rng, counters)
                    paths[t.leg] = (e, pr, risk_usd)
                    mark = float(pr[0]) * risk_usd
                    if len(paths) == 1:
                        next_tick = e + 1.0 / 24.0
                else:
                    mark = open_mark_r(t, mode, rng, counters) * risk_usd
                open_pos[t.leg] = (x, pnl, mark)
                open_trades[t.leg] = t
                heapq.heappush(exits, (x, t.leg))
                marks_sum += mark
                life.trades_taken += 1

        cause = breached()
        if cause:
            life.died = True
            life.death_cause = cause
            life.death_phase = "eval" if phase == "eval" else "funded"
            life.lifetime_days = now
            break
    return life


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def _pct(a: np.ndarray, q: float) -> float:
    return float(np.percentile(a, q)) if a.size else float("nan")


def summarize(lives: Sequence[Life], rules: PropRules) -> Dict[str, Any]:
    n = len(lives)
    net = np.array([lf.net(rules.fee) for lf in lives], dtype=float)
    life_days = np.array([lf.lifetime_days for lf in lives], dtype=float)
    passed = [lf for lf in lives if lf.passed]
    funded_died_before_payout = sum(1 for lf in passed if lf.died and lf.n_payouts == 0)
    died_before_payout = sum(1 for lf in lives if lf.died and lf.n_payouts == 0)
    mean_net = float(net.mean())
    se = float(net.std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
    mean_life = float(life_days.mean())
    causes: Dict[str, int] = {}
    for lf in lives:
        if lf.died:
            key = f"{lf.death_phase}:{lf.death_cause}"
            causes[key] = causes.get(key, 0) + 1
    dtp = np.array([lf.days_to_pass for lf in passed], dtype=float)
    reached = [lf for lf in lives if lf.target_first_reached_days is not None]
    t2p = np.array([lf.days_to_pass - lf.target_first_reached_days for lf in passed
                    if lf.target_first_reached_days is not None], dtype=float)
    qde = np.array([lf.qual_days_eval for lf in lives], dtype=float)
    need = rules.qual_day_min_days or 5
    qual_block = {
        # P(the eval has accrued `need` qualifying days by day N); a life dead before N
        # without them counts as not reached. Expected eval qualifying days inside the
        # first N days, over ALL lives (deaths censor naturally).
        "p_reach_min_qual_days_by_day": {
            str(N): round(sum(1 for lf in lives if len(lf.qual_day_times) >= need
                              and lf.qual_day_times[need - 1] <= N) / n, 4)
            for N in (30, 60, 90, 180)},
        "expected_qual_days_by_day": {
            str(N): round(float(np.mean([sum(1 for t in lf.qual_day_times if t <= N) for lf in lives])), 2)
            for N in (30, 60, 90)},
        "min_qual_days": need,
        # share of lives whose eval balance ever reached the target (passed is a subset)
        "p_target_reached_eval": round(len(reached) / n, 4),
        "p_pass_given_target_reached": (round(len(passed) / len(reached), 4) if reached else None),
        # the gate's cost: reached the target, never passed (died or ran out of horizon first)
        "p_target_reached_not_passed": round((len(reached) - len(passed)) / n, 4),
        "days_target_to_pass": ({"p50": round(_pct(t2p, 50), 1), "p90": round(_pct(t2p, 90), 1)}
                                if t2p.size else None),
        "qual_days_eval": {"mean": round(float(qde.mean()), 2), "p50": round(_pct(qde, 50), 1)},
    }
    return {
        "n_lives": n,
        "ev_net_usd_per_life": round(mean_net, 2),
        "ev_net_usd_per_life_mc_se": round(se, 2),
        "ev_banked_usd_per_life": round(mean_net + rules.fee, 2),
        "account_fee_usd": rules.fee,
        "p_pass_eval": round(len(passed) / n, 4),
        "p_death_before_first_payout": round(died_before_payout / n, 4),
        "p_death_before_first_payout_given_funded": (
            round(funded_died_before_payout / len(passed), 4) if passed else None),
        "p_net_positive": round(float((net > 0).mean()), 4),
        "p_alive_at_horizon": round(sum(1 for lf in lives if lf.alive_at_horizon) / n, 4),
        "lifetime_days": {"mean": round(mean_life, 1), "p50": round(_pct(life_days, 50), 1),
                          "p90": round(_pct(life_days, 90), 1)},
        "days_to_pass": ({"p50": round(_pct(dtp, 50), 1), "p90": round(_pct(dtp, 90), 1)}
                         if dtp.size else None),
        "qualifying_days": qual_block,
        "net_usd_percentiles": {f"p{q}": round(_pct(net, q), 2) for q in (5, 25, 50, 75, 95)},
        "mean_payouts_per_life": round(float(np.mean([lf.n_payouts for lf in lives])), 2),
        "mean_trades_per_life": round(float(np.mean([lf.trades_taken for lf in lives])), 1),
        "mean_trades_skipped_leg_busy_per_life": round(
            float(np.mean([lf.trades_skipped_leg_busy for lf in lives])), 2),
        "death_causes": dict(sorted(causes.items())),
        # per-life breach probabilities, any phase (the gate the operator re-scoped to on
        # 2026-10-06: "strategies that we know meet the risk requirements")
        "p_breach_daily_loss": round(sum(1 for lf in lives if lf.died and lf.death_cause == "daily_loss") / n, 4),
        "p_breach_max_dd": round(sum(1 for lf in lives if lf.died and lf.death_cause == "static_drawdown") / n, 4),
        "p_breach_daily_loss_in_eval": round(sum(1 for lf in lives if lf.died and lf.death_cause == "daily_loss"
                                                 and lf.death_phase == "eval") / n, 4),
        "p_breach_max_dd_in_eval": round(sum(1 for lf in lives if lf.died and lf.death_cause == "static_drawdown"
                                             and lf.death_phase == "eval") / n, 4),
        "ev_net_usd_per_365d_with_rebuy": (
            round(365.0 * mean_net / mean_life, 2) if mean_life > 0 else None),
    }


def run(hist: History, rules: PropRules, cfg: SimConfig, *, modes: Sequence[str] = MARK_MODES,
        n_lives: int = 4000, outer: int = 100, lives_per_outer: int = 200,
        seed: int = 20260927) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for mi, mode in enumerate(modes):
        counters: Dict[str, int] = {}
        rng = np.random.default_rng(seed + 1000 * mi)
        lives = [simulate_life(hist, rules, cfg, mode, rng, counters) for _ in range(n_lives)]
        res = summarize(lives, rules)
        # OUTER: evidence uncertainty — re-draw the history, re-simulate.
        orng = np.random.default_rng(seed + 1000 * mi + 7)
        means: List[float] = []
        ppass: List[float] = []
        for _ in range(outer):
            h2 = hist.redraw(orng, cfg.block_days)
            ls = [simulate_life(h2, rules, cfg, mode, orng) for _ in range(lives_per_outer)]
            means.append(float(np.mean([lf.net(rules.fee) for lf in ls])))
            ppass.append(float(np.mean([lf.passed for lf in ls])))
        if means:
            m = np.array(means)
            res["evidence_ci"] = {
                "method": f"outer block bootstrap of the history ({outer} re-drawn histories x "
                          f"{lives_per_outer} lives)",
                "ev_net_usd_p5": round(_pct(m, 5), 2),
                "ev_net_usd_p50": round(_pct(m, 50), 2),
                "ev_net_usd_p95": round(_pct(m, 95), 2),
                "p_ev_positive": round(float((m > 0).mean()), 4),
                "p_pass_eval_p5": round(_pct(np.array(ppass), 5), 4),
                "p_pass_eval_p95": round(_pct(np.array(ppass), 95), 4),
            }
        res["counters"] = counters
        out[mode] = res
    return out


def grade(results: Dict[str, Any]) -> Tuple[str, str]:
    """Apply DECISION_RULE. Returns (verdict, reason)."""
    try:
        b5 = results["bridge"]["evidence_ci"]["ev_net_usd_p5"]
        s_pt = results["stop"]["ev_net_usd_per_life"]
        r95 = results["realized"]["evidence_ci"]["ev_net_usd_p95"]
    except KeyError as e:
        return "indeterminate", f"rule inputs missing ({e}); all three modes + the outer CI are required"
    if b5 > 0 and s_pt > 0:
        return "pass", f"bridge evidence-p5 {b5:+.2f} > 0 and stop point {s_pt:+.2f} > 0"
    if r95 < 0:
        return "fail", f"even the optimistic realized model's evidence-p95 is {r95:+.2f} < 0"
    return "indeterminate", (f"bridge evidence-p5 {b5:+.2f}, stop point {s_pt:+.2f}, "
                             f"realized evidence-p95 {r95:+.2f} — neither clause decides")


def grade_v2(results: Dict[str, Any]) -> Tuple[str, str]:
    try:
        ci = results["path"]["evidence_ci"]
    except KeyError as e:
        return "indeterminate", f"rule inputs missing ({e}); the `path` mode + outer CI are required"
    p5, p95 = ci["ev_net_usd_p5"], ci["ev_net_usd_p95"]
    if p5 > 0:
        return "pass", f"path evidence-p5 {p5:+.2f} > 0"
    if p95 < 0:
        return "fail", f"path evidence-p95 {p95:+.2f} < 0"
    return "indeterminate", f"path evidence CI [{p5:+.2f}, {p95:+.2f}] straddles 0"


# ---------------------------------------------------------------------------
# Today's book
# ---------------------------------------------------------------------------
# breakout_1 retired 2026-10-07 (empty roster); breakout_2 is the live Breakout book.
BOOKS = {"breakout_2": ("trend_donchian_eth_prop", "trend_donchian_sol_prop")}
# `--book breakout_1` is still named by queued research units (RQ-20260928-015). The legs'
# evidence records are per-leg, not per-account, so the retired book scores identically on
# breakout_2's roster; the alias keeps those units runnable.
BOOK_ALIASES = {"breakout_1": "breakout_2"}


def resolve_book(book: str) -> Tuple[Dict[str, Path], Dict[str, Any]]:
    """Map each leg on `book` to the per-trade rows beside its committed evidence
    record, and verify the record still describes the leg's CURRENT params."""
    import yaml
    book = BOOK_ALIASES.get(book, book)
    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(REPO / "scripts" / "ci"))
    from check_roster_promotion_evidence import config_fingerprint  # byte-identical to the producer's
    from src.config.accounts_loader import load_accounts_dict
    acct = load_accounts_dict().get(book) or {}
    roster = list(acct.get("strategies") or [])
    if roster != list(BOOKS[book]):
        raise SystemExit(f"{book} roster in the canonical accounts config is {roster}, this tool's "
                         f"BOOKS entry is {list(BOOKS[book])} — update BOOKS deliberately")
    strategies = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())["strategies"]
    paths: Dict[str, Path] = {}
    prov: Dict[str, Any] = {}
    for leg in roster:
        rec = json.loads((REPO / "comms" / "strategy_evidence" / f"{leg}.json").read_text())
        bt = REPO / rec["cost_stack"]["source"]
        rows = bt.with_name(bt.name.replace("__bt.json", "__trades.jsonl"))
        fp_now = config_fingerprint(strategies[leg])
        paths[leg] = rows
        prov[leg] = {
            "evidence_record": f"comms/strategy_evidence/{leg}.json",
            "trades_file": str(rows.relative_to(REPO)),
            "n_trades_oos_record": rec.get("n_trades_oos"),
            "net_r_oos_record_bybit_costs": rec.get("net_r_oos"),
            "config_fingerprint_record": rec.get("config_fingerprint"),
            "config_fingerprint_current": fp_now,
            "fingerprint_matches": rec.get("config_fingerprint") == fp_now,
        }
    return paths, prov


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    except (subprocess.CalledProcessError, OSError) as e:  # allow-silent: provenance label only; the result still says "unknown"
        print(f"warning: could not read git HEAD ({e})", file=sys.stderr)
        return "unknown"


# ---------------------------------------------------------------------------
# Self-test — positive controls with known answers
# ---------------------------------------------------------------------------
def _toy_rows(outcomes: Sequence[float], *, hours_between: float = 24.0, hold_h: float = 1.0,
              start: str = "2025-01-01T02:00:00+00:00") -> List[Dict[str, Any]]:
    t0 = _parse_ts(start)
    from datetime import timedelta
    rows = []
    for i, r in enumerate(outcomes):
        a = t0 + timedelta(hours=hours_between * i)
        rows.append({"entry_time": a.isoformat(), "exit_time": (a + timedelta(hours=hold_h)).isoformat(),
                     "net_r": float(r)})
    return rows


def gamblers_ruin(p: float, i: int, n: int) -> float:
    """P(reach n before 0 from i) for a +-1 walk with up-probability p."""
    if abs(p - 0.5) < 1e-12:
        return i / n
    q = (1 - p) / p
    return (1 - q ** i) / (1 - q ** n)


def _self_test() -> int:
    fails: List[str] = []

    def check(cond: bool, msg: str) -> None:
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    as_given = CostConfig(mode="as_given")

    # --- 1. POSITIVE CONTROL: gambler's ruin -------------------------------------
    # Fixed $100 risk on $5,000 (2%, sizing=start), +-1R outcomes, one trade per
    # day, daily limit out of reach: target +10% = +5 steps, static DD 6% = -3
    # steps (touching the floor is a breach) => P(pass) = ruin(p, 3, 8).
    rules = PropRules(daily_loss_pct=0.50)
    cfg = SimConfig(risk_pct=0.02, sizing="start", horizon_days=10_000, block_days=1, stop_at_pass=True)
    for p in (0.5, 0.6):
        # EXACT win fraction, so the empirical distribution IS p (a random draw
        # of 4,000 outcomes carries ~0.8pp of p-noise, which moves the answer).
        k_win = int(round(p * 4000))
        outs = np.array([1.0] * k_win + [-1.0] * (4000 - k_win))
        np.random.default_rng(1).shuffle(outs)
        rows = _toy_rows(list(outs))
        trades, _, days = build_trades({"toy": rows}, as_given)
        hist = History(trades, days)
        lives = [simulate_life(hist, rules, cfg, "realized", np.random.default_rng(100 + k))
                 for k in range(8000)]
        got = np.mean([lf.passed for lf in lives])
        want = gamblers_ruin(p, 3, 8)
        tol = 3.0 * math.sqrt(want * (1 - want) / len(lives))
        check(abs(got - want) <= tol,
              f"gambler's ruin p={p}: simulated P(pass) {got:.4f} vs analytic {want:.4f} (tol {tol:.4f})")
    # expected trades to absorption at p=0.5 from 3 of 8 = 3*5 = 15
    outs = np.array([1.0] * 2000 + [-1.0] * 2000)
    np.random.default_rng(2).shuffle(outs)
    rows = _toy_rows(list(outs))
    trades, _, days = build_trades({"toy": rows}, as_given)
    hist = History(trades, days)
    lives = [simulate_life(hist, rules, cfg, "realized", np.random.default_rng(900 + k)) for k in range(3000)]
    all_abs = float(np.mean([lf.trades_taken for lf in lives]))
    check(abs(all_abs - 15.0) < 0.6, f"expected trades to absorption {all_abs:.2f} vs analytic 15")

    # --- 2. payout mechanics, deterministic -------------------------------------
    # +1R every day at 2% of START ($100). Eval: 5 wins -> pass on day 4 (+$500).
    # Fresh funded $5,000 after the 1-day gap; first payout 14d later, then
    # weekly. Each window withdraws everything above start; trader keeps 80%.
    rows = _toy_rows([1.0] * 400)
    trades, _, days = build_trades({"toy": rows}, as_given)
    hist = History(trades, days)
    cfg2 = SimConfig(risk_pct=0.02, sizing="start", horizon_days=60, block_days=days)
    lf = simulate_life(hist, PropRules(), cfg2, "realized", np.random.default_rng(0),
                       stream=((t.entry, t.exit, t) for t in hist.trades))
    # pass at day 4 (+ 2h trade + 1h hold) -> funded ~day 5.125; payouts at +14, +21, +28, +35, +42, +49
    check(lf.passed and not lf.died, "deterministic winner passes and never dies")
    check(abs((lf.days_to_pass or 0) - (4 + 3 / 24)) < 1e-6, f"days_to_pass {lf.days_to_pass:.4f} == 4.125")
    # funded trades: entries at day k + 2/24 for k >= 6 (day 5 entry at 5.083 < funded 5.125 is skipped)
    exp_banked = 0.0
    funded = 4 + 3 / 24 + 1.0
    payout_times = [funded + 14 + 7 * j for j in range(10) if funded + 14 + 7 * j < 60]
    last = funded
    for pt in payout_times:
        wins = sum(1 for k in range(0, 400) if last < k + 2 / 24 + 1 / 24 <= pt and k + 2 / 24 >= funded)
        exp_banked += wins * 100 * 0.8
        last = pt
    check(abs(lf.banked - exp_banked) < 1e-6, f"banked {lf.banked:.2f} == hand-computed {exp_banked:.2f}")
    lf_carry = simulate_life(hist, PropRules(), SimConfig(risk_pct=0.02, sizing="start", horizon_days=60,
                             block_days=days, funded_start="carry"), "realized", np.random.default_rng(0),
                             stream=((t.entry, t.exit, t) for t in hist.trades))
    check(abs(lf_carry.banked - lf.banked - 500 * 0.8) < 1e-6,
          "funded_start=carry banks exactly the eval's +$500 x 80% more than fresh (A6 is load-bearing)")

    # --- 3. daily loss vs static DD ---------------------------------------------
    # Two -1R losers at 1.5% the SAME day: 2 x $75 = $150 = 3% of $5,000 -> daily breach.
    rows = [{"entry_time": "2025-01-01T01:00:00+00:00", "exit_time": "2025-01-01T03:00:00+00:00", "net_r": -1.0},
            {"entry_time": "2025-01-01T02:00:00+00:00", "exit_time": "2025-01-01T04:00:00+00:00", "net_r": -1.0}]
    trades, _, days = build_trades({"a": rows[:1], "b": rows[1:]}, as_given)
    hist = History(trades, days)
    c3 = SimConfig(risk_pct=0.015, sizing="start", horizon_days=5, block_days=days)
    lf = simulate_life(hist, PropRules(), c3, "realized", np.random.default_rng(0),
                       stream=((t.entry, t.exit, t) for t in hist.trades))
    check(lf.died and lf.death_cause == "daily_loss", f"two same-day stops breach daily loss ({lf.death_cause})")
    # Same two losers on consecutive days: $75/day < $150 -> survives.
    rows2 = [dict(rows[0]), {"entry_time": "2025-01-02T02:00:00+00:00", "exit_time": "2025-01-02T04:00:00+00:00",
                             "net_r": -1.0}]
    trades, _, days = build_trades({"a": rows2[:1], "b": rows2[1:]}, as_given)
    hist = History(trades, days)
    lf = simulate_life(hist, PropRules(), c3, "realized", np.random.default_rng(0),
                       stream=((t.entry, t.exit, t) for t in hist.trades))
    check(not lf.died, "the same two stops on separate days survive")
    # stop bound: both open at once and the first WINS — realized survives, stop mark
    # (2 x (-1R + eps) = just above -$150) must NOT breach, bridge must not either.
    rows3 = [{"entry_time": "2025-01-01T01:00:00+00:00", "exit_time": "2025-01-01T05:00:00+00:00",
              "net_r": 2.0, "gross_r": 2.0, "mfe_r": 2.5},
             {"entry_time": "2025-01-01T02:00:00+00:00", "exit_time": "2025-01-01T06:00:00+00:00",
              "net_r": 1.0, "gross_r": 1.0, "mfe_r": 1.2}]
    trades, _, days = build_trades({"a": rows3[:1], "b": rows3[1:]}, as_given)
    hist = History(trades, days)
    for mode in MARK_MODES:
        lf = simulate_life(hist, PropRules(), c3, mode, np.random.default_rng(0),
                           stream=((t.entry, t.exit, t) for t in hist.trades))
        check(not lf.died, f"{mode}: two overlapping WINNERS at exactly 2R of open risk do not breach")
    # with non-zero cost the stop mark of two open trades is past 2R -> stop mode breaches
    rows4 = [dict(r, gross_r=r["net_r"] + 0.05) for r in rows3]
    trades, _, days = build_trades({"a": rows4[:1], "b": rows4[1:]}, as_given)
    hist = History(trades, days)
    lf = simulate_life(hist, PropRules(), c3, "stop", np.random.default_rng(0),
                       stream=((t.entry, t.exit, t) for t in hist.trades))
    check(lf.died and lf.death_cause == "daily_loss",
          "stop bound: two open 1.5% positions whose stops cost > 2R breach daily loss (the concurrency risk)")

    # --- 3b. resuming an existing account --------------------------------------------
    # $4,724 flat, floor $4,700: one -1R stop at 1.5% of balance ($70.86) breaches;
    # `room` sizing risks at most 45% of the $24 cushion and survives it.
    rows5 = [{"entry_time": "2025-01-01T01:00:00+00:00", "exit_time": "2025-01-01T03:00:00+00:00", "net_r": -1.0}]
    trades, _, days = build_trades({"a": rows5}, as_given)
    hist = History(trades, days)
    for sz, dies in (("balance", True), ("room", False)):
        lf = simulate_life(hist, PropRules(), SimConfig(risk_pct=0.015, sizing=sz, start_balance=4724.0,
                           horizon_days=5, block_days=days), "realized", np.random.default_rng(0),
                           stream=((t.entry, t.exit, t) for t in hist.trades))
        check(lf.died == dies, f"resume at $4,724: one stop with sizing={sz} -> died={lf.died} (want {dies})")

    # --- 4. costs ------------------------------------------------------------------
    c = CostConfig()
    row = {"entry": 100.0, "sl": 98.0, "gross_r": 1.0}
    a, b = _parse_ts("2025-01-01T20:00:00+00:00"), _parse_ts("2025-01-03T04:00:00+00:00")
    want = (0.0011 + 0.00033 * 2) / 0.02          # 2 midnights crossed
    check(abs(trade_cost_r(row, c, a, b) - want) < 1e-12, f"dxtrade swap: 2 midnights -> {want:.4f}R")
    cp = CostConfig(swap_model="prorated")
    want_p = (0.0011 + 0.00033 * (32 / 24)) / 0.02
    check(abs(trade_cost_r(row, cp, a, b) - want_p) < 1e-12, f"prorated swap: 32h -> {want_p:.4f}R")
    same_day = trade_cost_r(row, c, _parse_ts("2025-01-01T01:00:00+00:00"), _parse_ts("2025-01-01T23:00:00+00:00"))
    check(abs(same_day - 0.0011 / 0.02) < 1e-12, "dxtrade swap: intraday trade pays no swap")

    # --- 5. bridge minimum is bounded and monotone ------------------------------------
    t = Trade("x", 0, 1, 1.5, 0.05, 1.55, 2.0)
    mins = [bridge_min_r(t, u) for u in (0.01, 0.5, 0.99)]
    check(all(m <= 0.0 for m in mins) and mins[0] <= mins[1] <= mins[2],
          f"bridge minima non-positive and monotone in u: {[round(m, 3) for m in mins]}")
    check(open_mark_r(t, "bridge", np.random.default_rng(0), {}) >= stop_mark_r(t),
          "bridge mark never below the stop mark for a non-loser")

    # --- 5b. path model -------------------------------------------------------------
    t = Trade("x", 0.0, 200 / 24, 1.5 - 0.05, 0.05, 1.5, 2.5)
    cnt: Dict[str, int] = {}
    rng = np.random.default_rng(3)
    ps = [sample_path_r(t, rng, cnt) for _ in range(800)]
    check(all(abs(pr[0] + 0.05) < 1e-12 and abs(pr[-1] - t.net_r) < 1e-12 for pr in ps),
          "path starts at -cost and ends exactly at net_r")
    check(all(pr.min() > -1.0 - 0.05 for pr in ps), "a non-loser's path never touches its stop")
    med_max = float(np.median([pr.max() + 0.05 for pr in ps]))
    check(abs(med_max - 2.5) < 0.1, f"path calibration: median gross max {med_max:.3f} ~= recorded MFE 2.5 (hourly)")
    loser = Trade("y", 0.0, 10 / 24, -1.05, 0.05, -1.0, 0.3)
    pl = sample_path_r(loser, rng, cnt)
    check(pl.min() >= -1.0 - 0.05 - 1e-12, "a loser's path is clipped at its own final value")

    # --- 6. schema refusals -------------------------------------------------------------
    for bad, why in (({"entry_time": "2025-01-01T00:00:00Z"}, "missing exit_time"),
                     ({"entry_time": "2025-01-02T00:00:00Z", "exit_time": "2025-01-01T00:00:00Z",
                       "net_r": 1}, "exit before entry")):
        try:
            build_trades({"x": [bad]}, as_given)
            check(False, f"refuses row: {why}")
        except ValueError:
            check(True, f"refuses row: {why}")
    try:
        build_trades({"x": [{"entry_time": "2025-01-01T00:00:00Z", "exit_time": "2025-01-01T01:00:00Z",
                             "net_r": 1.0}]}, CostConfig())
        check(False, "--costs breakout refuses a row with no gross_r/entry/sl")
    except ValueError:
        check(True, "--costs breakout refuses a row with no gross_r/entry/sl")

    # --- 7. decision rule grading ---------------------------------------------------------
    def fake(b5, spt, r95):
        return {"bridge": {"evidence_ci": {"ev_net_usd_p5": b5}},
                "stop": {"ev_net_usd_per_life": spt},
                "realized": {"evidence_ci": {"ev_net_usd_p95": r95}}}
    check(grade(fake(10, 5, 50))[0] == "pass", "rule: pass case")
    check(grade(fake(-10, 5, -1))[0] == "fail", "rule: fail case")
    check(grade(fake(-10, -5, 50))[0] == "indeterminate", "rule: indeterminate case")

    # --- 8. Velotrade-shaped gates (PROP-FIRM-DEEP, 2026-10-04) -- positive controls --------
    # Same deterministic +1R-per-day winner as section 2 ($100/day at 2% of start).
    rows = _toy_rows([1.0] * 400)
    trades, _, days = build_trades({"toy": rows}, as_given)
    hist = History(trades, days)
    cfg8 = SimConfig(risk_pct=0.02, sizing="start", horizon_days=60, block_days=days)
    def stream8():
        return ((t.entry, t.exit, t) for t in hist.trades)
    # 8a. qualifying days: 5 days at >= 2% of start. Day k's trade closes 03:00 on day k; the
    # 00:30 reset on day k+1 counts it. The 5th qualifying reset is at day 5 + 00:30, and the
    # target (+$500) was already met at the day-4 exit, so the pass lands AT THAT RESET.
    r8 = PropRules(qual_day_min_days=5, qual_day_profit_pct=0.02)
    lf = simulate_life(hist, r8, cfg8, "realized", np.random.default_rng(0), stream=stream8())
    check(lf.passed and abs((lf.days_to_pass or 0) - (5 + 0.5 / 24)) < 1e-6,
          f"qualifying-day gate: pass moves from day 4.125 to the day-5 reset ({lf.days_to_pass})")
    lf8a = lf
    check(lf8a.target_first_reached_days is not None and lf8a.target_first_reached_days < lf8a.days_to_pass
          and lf8a.qual_days_eval == 5,
          f"qualifying-day observability: target reached at {lf8a.target_first_reached_days} before the pass, "
          f"{lf8a.qual_days_eval} eval qualifying days counted")
    # 8b. a threshold no day can meet (3% of start > the $100 daily win) never passes
    c8b: Dict[str, int] = {}
    lf = simulate_life(hist, PropRules(qual_day_min_days=5, qual_day_profit_pct=0.03), cfg8,
                       "realized", np.random.default_rng(0), counters=c8b, stream=stream8())
    check(not lf.passed and lf.alive_at_horizon, "qualifying-day gate: unreachable threshold never passes")
    check(lf.target_first_reached_days is not None and lf.qual_days_eval == 0
          and c8b.get("pass_deferred_qual_days", 0) > 0,
          f"qualifying-day observability: target reached, 0 qualifying days, "
          f"{c8b.get('pass_deferred_qual_days', 0)} deferred passes counted")
    lf8b = lf
    s8 = summarize([lf8a, lf8b], PropRules())
    q8 = s8["qualifying_days"]
    check(len(lf8a.qual_day_times) == 5
          and all(abs(t - (k + 0.5 / 24)) < 1e-6 for k, t in enumerate(lf8a.qual_day_times, start=1)),
          f"qualifying-day times: the five eval qualifying resets are days 1..5 at 00:30 ({lf8a.qual_day_times})")
    check(q8["p_reach_min_qual_days_by_day"]["30"] == 0.5 and q8["expected_qual_days_by_day"]["30"] == 2.5
          and q8["p_reach_min_qual_days_by_day"]["180"] == 0.5,
          f"qualifying-day timing summary: half the lives reach 5 by day 30, 2.5 expected per 30d ({q8})")
    check(s8["p_breach_daily_loss"] == 0.0 and s8["p_breach_max_dd"] == 0.0,
          "breach probabilities: a deterministic winner never breaches")
    check(q8["p_target_reached_eval"] == 1.0 and s8["p_pass_eval"] == 0.5
          and q8["p_pass_given_target_reached"] == 0.5 and q8["p_target_reached_not_passed"] == 0.5
          and q8["days_target_to_pass"]["p50"] > 0,
          f"qualifying-day summary: P(target)=1.0, P(pass)=0.5, gate cost 0.5 ({q8})")
    # 8c. gate OFF (qual_day_profit_pct None) reproduces section 2 exactly, even with min days set
    lf_off = simulate_life(hist, PropRules(qual_day_min_days=5), cfg8, "realized",
                           np.random.default_rng(0), stream=stream8())
    lf_ref = simulate_life(hist, PropRules(), cfg8, "realized", np.random.default_rng(0), stream=stream8())
    check(lf_off.banked == lf_ref.banked and lf_off.days_to_pass == lf_ref.days_to_pass,
          "qualifying-day gate is OFF unless qualifying_day_profit_pct is declared")
    check(lf_ref.target_first_reached_days == lf_ref.days_to_pass and lf_ref.qual_days_eval == 0,
          "gate OFF: the pass lands at the exit that reaches the target, and no qualifying day is counted")
    # 8d. first-payout funded gate: 5 qualifying funded days are banked well before the day-14
    # first payout, so the gate must not change the outcome; 20 qualifying days must defer it.
    lf5 = simulate_life(hist, PropRules(qual_day_min_days=0, qual_day_profit_pct=0.02,
                                        funded_qual_days_before_payout=5), cfg8, "realized",
                        np.random.default_rng(0), stream=stream8())
    c20: Dict[str, int] = {}
    lf20 = simulate_life(hist, PropRules(qual_day_min_days=0, qual_day_profit_pct=0.02,
                                         funded_qual_days_before_payout=20), cfg8, "realized",
                         np.random.default_rng(0), counters=c20, stream=stream8())
    check(lf5.banked == lf_ref.banked, "funded qualifying-day gate (5) is already met at the first payout")
    check(lf20.n_payouts < lf_ref.n_payouts and c20.get("payout_deferred_qual_days", 0) == 7,
          f"funded qualifying-day gate (20) defers the first payout by 7 daily re-asks "
          f"({lf20.n_payouts} vs {lf_ref.n_payouts} payouts)")
    # 8e. payout cap: first payout capped at 1x fee ($45) -- the rest of that withdrawal is forfeited
    c8: Dict[str, Any] = {}
    lf_cap = simulate_life(hist, PropRules(payout_cap_first_n=1, payout_cap_mult_fee=1.0), cfg8,
                           "realized", np.random.default_rng(0), counters=c8, stream=stream8())
    forfeited = c8.get("payout_forfeited_usd", 0.0)
    check(forfeited > 0 and abs((lf_ref.banked - lf_cap.banked) - 0.8 * forfeited) < 1e-6
          and c8.get("payouts_capped") == 1,
          f"payout cap: exactly one capped payout, trader loses 80% of the ${forfeited:.0f} forfeited")
    # 8f. reset basis. A +3R trade is OPEN across the 00:30 reset and closes at 03:00
    # (bal $5,300); a -4R trade then closes at 04:00 (bal $4,900). On the balance basis the
    # daily floor is $5,000 x 0.97 = $4,850 and the life survives; on the Velotrade basis the
    # floor is ($5,000 + open mark) x 0.97, which exceeds $4,900 whenever the mark at the
    # reset is above about +0.3R. With mfe_r == gross_r the `path` bridge is the deterministic
    # straight line 0 -> +3R, so the reset mark is ~+1.4R (= $139) on every seed: the Velotrade
    # basis must kill every life the balance basis spares, and never the reverse.
    # (`path` marks need the raw schema -- gross_r/entry/sl -- so Breakout costs apply here:
    # 0.11R per trade on a 1% stop, which keeps bal at $5,289 then $4,878, still above $4,850.)
    rows_a = [{"entry_time": "2025-01-01T23:00:00+00:00", "exit_time": "2025-01-02T03:00:00+00:00",
               "gross_r": 3.0, "mfe_r": 3.0, "entry": 100.0, "sl": 99.0}]
    rows_b = [{"entry_time": "2025-01-02T03:30:00+00:00", "exit_time": "2025-01-02T04:00:00+00:00",
               "gross_r": -4.0, "mfe_r": 0.0, "entry": 100.0, "sl": 99.0}]
    trades_f, _, days_f = build_trades({"a": rows_a, "b": rows_b}, CostConfig())
    hist_f = History(trades_f, days_f)
    cfg_f = SimConfig(risk_pct=0.02, sizing="start", horizon_days=3, block_days=days_f)
    stricter, lenient = 0, 0
    for seed in range(40):
        died = {}
        for basis in ("balance", "max_balance_equity"):
            lf = simulate_life(hist_f, PropRules(daily_loss_pct=0.03, max_dd_pct=0.50, day_reset_basis=basis),
                               cfg_f, "path", np.random.default_rng(seed),
                               stream=((t.entry, t.exit, t) for t in hist_f.trades))
            died[basis] = lf.died
        stricter += int(died["max_balance_equity"] and not died["balance"])
        lenient += int(died["balance"] and not died["max_balance_equity"])
    check(stricter == 40 and lenient == 0,
          f"reset basis: max(balance, equity) is strictly stricter ({stricter}/40 extra deaths, {lenient} spared)")

    print(f"\nself-test: {'PASS' if not fails else 'FAIL'} ({len(fails)} failure(s))")
    return 0 if not fails else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--book", choices=sorted(set(BOOKS) | set(BOOK_ALIASES)), help="score a live prop book from its evidence records")
    ap.add_argument("--trades", action="append", default=[], metavar="LEG=PATH",
                    help="per-leg trade JSONL (repeatable). See INPUT SCHEMA in --help / the module doc.")
    ap.add_argument("--ruleset", default=str(DEFAULT_RULESET))
    ap.add_argument("--risk-pct", type=float, default=0.015,
                    help="FRACTION of balance risked per trade (0.015 = breakout_routing.yaml's 1.5%%)")
    ap.add_argument("--sizing", choices=("balance", "start", "room"), default="balance",
                    help="room = min(risk_pct x balance, room_frac x binding cushion), cushion = the "
                         "smaller of equity-to-static-floor and equity-to-today's-daily-limit")
    ap.add_argument("--no-hedge", action="store_true",
                    help="skip an entry that would hold long+short in the same symbol across legs")
    ap.add_argument("--lev-cap", action="append", default=[], metavar="LEG=X",
                    help="per-position notional cap as a multiple of balance for LEG (repeatable)")
    ap.add_argument("--room-frac", type=float, default=0.45)
    ap.add_argument("--min-risk", type=float, default=1.0, help="skip a trade sized below this many USD of risk")
    ap.add_argument("--start-balance", type=float, default=None,
                    help="resume an EXISTING evaluation account at this (flat) balance; pair with "
                         "--fee 0 when the account fee is already sunk")
    ap.add_argument("--fee", type=float, default=None, help="override the ruleset account fee")
    ap.add_argument("--funded-start", choices=("fresh", "carry"), default=None,
                    help="default: the ruleset's economics.funded_start (Breakout: fresh, operator 2026-09-27)")
    ap.add_argument("--approval-days", type=float, default=1.0)
    ap.add_argument("--horizon-days", type=float, default=730.0)
    ap.add_argument("--block-days", type=int, default=30)
    ap.add_argument("--first-payout-refund", action="store_true", default=None,
                    help="counterfactual only; default: the ruleset's economics.first_payout_fee_refund "
                         "(Breakout: false, operator 2026-09-27)")
    ap.add_argument("--costs", choices=("breakout", "as_given"), default="breakout")
    ap.add_argument("--commission-bps-rt", type=float, default=8.0)
    ap.add_argument("--slippage-bps-rt", type=float, default=3.0)
    ap.add_argument("--swap-daily", type=float, default=0.00033)
    ap.add_argument("--swap-model", choices=("dxtrade", "prorated"), default="dxtrade")
    ap.add_argument("--modes", default=",".join(MARK_MODES))
    ap.add_argument("--lives", type=int, default=4000)
    ap.add_argument("--outer", type=int, default=100)
    ap.add_argument("--lives-per-outer", type=int, default=200)
    ap.add_argument("--seed", type=int, default=20260927)
    ap.add_argument("--out", help="write the full JSON result here")
    ap.add_argument("--emit-result", action="store_true",
                    help="also land an E5 result record under research/results/_unattributed/")
    ap.add_argument("--run-id", default=None, help="run id for --emit-result (path-safe)")
    ap.add_argument("--rule", choices=("v1", "v2"), default="v2",
                    help="which registered rule the E5 record is graded against. v2 for any "
                         "candidate scored from 2026-09-27 on; v1 ONLY for breakout_1's 2026-09-27 run")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    provenance: Dict[str, Any] = {}
    paths: Dict[str, Path] = {}
    if args.book:
        paths, provenance = resolve_book(args.book)
        stale = [leg for leg, p in provenance.items() if not p["fingerprint_matches"]]
        if stale:
            print(f"REFUSING: evidence no longer describes the current params of {stale}", file=sys.stderr)
            return 2
    for spec in args.trades:
        if "=" not in spec:
            ap.error(f"--trades expects LEG=PATH, got {spec!r}")
        leg, p = spec.split("=", 1)
        paths[leg] = Path(p)
    if not paths:
        ap.error("give --book or at least one --trades LEG=PATH")

    rules = PropRules.from_yaml(Path(args.ruleset))
    if args.fee is not None:
        rules.fee = float(args.fee)
    costs = CostConfig(mode=args.costs, commission_bps_rt=args.commission_bps_rt,
                       slippage_bps_rt=args.slippage_bps_rt, swap_daily=args.swap_daily,
                       swap_model=args.swap_model)
    cfg = SimConfig(risk_pct=args.risk_pct, sizing=args.sizing, room_frac=args.room_frac,
                    min_risk_usd=args.min_risk,
                    start_balance=args.start_balance,
                    funded_start=args.funded_start or rules.funded_start,
                    approval_days=args.approval_days, horizon_days=args.horizon_days,
                    block_days=args.block_days,
                    first_payout_refund=(rules.first_payout_refund if args.first_payout_refund is None
                                         else bool(args.first_payout_refund)))
    cfg.no_hedge = bool(args.no_hedge)
    for spec in args.lev_cap:
        if "=" not in spec:
            ap.error(f"--lev-cap expects LEG=X, got {spec!r}")
        leg, x = spec.split("=", 1)
        cfg.lev_caps = {**(cfg.lev_caps or {}), leg: float(x)}
    rows = {leg: load_rows(p) for leg, p in paths.items()}
    trades, day0, hist_days = build_trades(rows, costs)
    hist = History(trades, hist_days)
    per_leg = {}
    for leg in rows:
        ts = [t for t in trades if t.leg == leg]
        per_leg[leg] = {"n": len(ts), "net_r_total_after_cost_model": round(sum(t.net_r for t in ts), 4),
                        "expectancy_r": round(sum(t.net_r for t in ts) / len(ts), 4) if ts else None,
                        "mean_cost_r": round(sum(t.cost_r for t in ts) / len(ts), 4) if ts else None,
                        "rows_with_mfe": sum(1 for t in ts if t.mfe_r is not None)}
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    results = run(hist, rules, cfg, modes=modes, n_lives=args.lives, outer=args.outer,
                  lives_per_outer=args.lives_per_outer, seed=args.seed)
    verdict, reason = grade(results) if set(V1_MODES) <= set(modes) else ("indeterminate", "V1 modes not all run")
    v2, v2_reason = grade_v2(results)
    doc = {
        "tool": TOOL, "commit_sha": _git_sha(),
        "decision_rule": {"id": DECISION_RULE_ID, "registered_at": DECISION_RULE_REGISTERED_AT,
                          "rule": DECISION_RULE, "verdict": verdict, "reason": reason},
        "decision_rule_v2": {"id": DECISION_RULE_V2_ID, "registered_at": DECISION_RULE_V2_REGISTERED_AT,
                             "rule": DECISION_RULE_V2, "verdict": v2, "reason": v2_reason},
        "inputs": {"legs": per_leg, "provenance": provenance, "history_day0": day0.isoformat(),
                   "history_days": hist_days, "n_trades": len(trades)},
        "config": {"rules": rules.__dict__, "sim": cfg.__dict__, "costs": costs.__dict__},
        "results": results,
    }
    text = json.dumps(doc, indent=2, sort_keys=False)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n")
    print(text)

    if args.emit_result:
        sys.path.insert(0, str(REPO / "scripts" / "research"))
        import research_result as rr
        run_id = args.run_id or f"b6-prop-ev-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
        use = doc["decision_rule"] if args.rule == "v1" else doc["decision_rule_v2"]
        other = doc["decision_rule_v2"] if args.rule == "v1" else doc["decision_rule"]
        rec = rr.build(
            research_unit=None, decision_rule_id=use["id"],
            decision_rule_registered_at=use["registered_at"], verdict=use["verdict"],
            read_state="measured",
            population_description=(
                "one Breakout $5k 1-Step account life, simulated over the pooled per-trade OOS rows of "
                + ", ".join(f"{k} (n={v['n']})" for k, v in per_leg.items())
                + f"; {hist_days}-day history from {day0.date()}; n = total trades in the resampled history"),
            n=len(trades), workflow="manual (Claude session, lane w6-prop-ev)", run_id=run_id,
            commit_sha=doc["commit_sha"], tool=TOOL,
            measurement={m: {k: results[m].get(k) for k in (
                "ev_net_usd_per_life", "evidence_ci", "p_pass_eval", "p_death_before_first_payout",
                "lifetime_days", "ev_net_usd_per_365d_with_rebuy")} for m in results},
            artifact_store=(args.out or "stdout only"),
            artifact_locator="the full JSON output of this tool; inputs listed under inputs.provenance",
            note=(f"{use['id']}: {use['verdict']} ({use['reason']}). Also reported, NOT the "
                  f"verdict of record: {other['id']}: {other['verdict']} ({other['reason']})."),
        )
        path = rr.write([rec], research_unit=None, run_id=run_id)
        print(f"landed E5 result: {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
