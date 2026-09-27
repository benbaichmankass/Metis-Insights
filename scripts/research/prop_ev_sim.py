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
      (``--funded-start fresh``). A third-party summary of Breakout's FAQ says
      the balance resets on passing; Breakout's own article checked on
      2026-09-27 does not say either way. ``--funded-start carry`` is the
      optimistic alternative.
  A7  A 1-day approval gap (KYC + agreement, "12-24 hours") between passing and
      the funded account trading. No trades are taken in it.
  A8  The first-payout fee refund some reviews mention is NOT modelled by
      default (unconfirmed); ``--first-payout-refund`` adds it.
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
        )


@dataclass
class SimConfig:
    risk_pct: float = 0.015
    sizing: str = "balance"          # balance | start
    funded_start: str = "fresh"      # fresh | carry
    approval_days: float = 1.0
    horizon_days: float = 730.0
    block_days: int = 30
    first_payout_refund: bool = False
    stop_at_pass: bool = False       # positive controls only: end the life at the eval pass


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
    bal = start
    day_start = start
    next_reset = _DAY_RESET
    funded_at: Optional[float] = None
    next_payout: Optional[float] = None
    refund_due = cfg.first_payout_refund
    # open positions: leg -> (exit_t, pnl_usd, mark_usd)
    open_pos: Dict[str, Tuple[float, float, float]] = {}
    exits: List[Tuple[float, str]] = []
    marks_sum = 0.0
    paths: Dict[str, Tuple[float, np.ndarray, float]] = {}   # `path` mode: leg -> (entry, marks_r, risk_usd)
    next_tick = math.inf

    src = stream if stream is not None else trade_stream(hist, rng, cfg.block_days, H)
    pending = next(src, None)

    def breached(now: float) -> Optional[str]:
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
            day_start = bal                 # the 00:30 balance, excluding open positions
            next_reset += 1.0
        elif kind == "funded":
            phase = "funded"
            next_payout = now + rules.first_payout_after_days
            day_start = bal
        elif kind == "payout":
            w = bal - withdraw_above
            if w >= max(rules.min_withdrawal, 1e-9):
                life.banked += w * rules.profit_split
                bal -= w
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
            if phase == "eval" and bal >= target - 1e-9:
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
            else:
                risk_usd = cfg.risk_pct * (bal if cfg.sizing == "balance" else start)
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
                heapq.heappush(exits, (x, t.leg))
                marks_sum += mark
                life.trades_taken += 1

        cause = breached(now)
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
        "net_usd_percentiles": {f"p{q}": round(_pct(net, q), 2) for q in (5, 25, 50, 75, 95)},
        "mean_payouts_per_life": round(float(np.mean([lf.n_payouts for lf in lives])), 2),
        "mean_trades_per_life": round(float(np.mean([lf.trades_taken for lf in lives])), 1),
        "mean_trades_skipped_leg_busy_per_life": round(
            float(np.mean([lf.trades_skipped_leg_busy for lf in lives])), 2),
        "death_causes": dict(sorted(causes.items())),
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
BOOKS = {"breakout_1": ("trend_donchian_sol_prop", "trend_donchian_eth_prop")}


def resolve_book(book: str) -> Tuple[Dict[str, Path], Dict[str, Any]]:
    """Map each leg on `book` to the per-trade rows beside its committed evidence
    record, and verify the record still describes the leg's CURRENT params."""
    import yaml
    sys.path.insert(0, str(REPO / "scripts" / "ci"))
    from check_roster_promotion_evidence import config_fingerprint  # byte-identical to the producer's
    accounts = yaml.safe_load((REPO / "config" / "accounts.yaml").read_text())
    acct = (accounts.get("accounts") or accounts).get(book) or {}
    roster = list(acct.get("strategies") or [])
    if roster != list(BOOKS[book]):
        raise SystemExit(f"{book} roster in config/accounts.yaml is {roster}, this tool's "
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
    except Exception:  # noqa: BLE001
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

    print(f"\nself-test: {'PASS' if not fails else 'FAIL'} ({len(fails)} failure(s))")
    return 0 if not fails else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--book", choices=sorted(BOOKS), help="score a live prop book from its evidence records")
    ap.add_argument("--trades", action="append", default=[], metavar="LEG=PATH",
                    help="per-leg trade JSONL (repeatable). See INPUT SCHEMA in --help / the module doc.")
    ap.add_argument("--ruleset", default=str(DEFAULT_RULESET))
    ap.add_argument("--risk-pct", type=float, default=0.015, help="FRACTION of balance risked per trade")
    ap.add_argument("--sizing", choices=("balance", "start"), default="balance")
    ap.add_argument("--funded-start", choices=("fresh", "carry"), default="fresh")
    ap.add_argument("--approval-days", type=float, default=1.0)
    ap.add_argument("--horizon-days", type=float, default=730.0)
    ap.add_argument("--block-days", type=int, default=30)
    ap.add_argument("--first-payout-refund", action="store_true")
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
    costs = CostConfig(mode=args.costs, commission_bps_rt=args.commission_bps_rt,
                       slippage_bps_rt=args.slippage_bps_rt, swap_daily=args.swap_daily,
                       swap_model=args.swap_model)
    cfg = SimConfig(risk_pct=args.risk_pct, sizing=args.sizing, funded_start=args.funded_start,
                    approval_days=args.approval_days, horizon_days=args.horizon_days,
                    block_days=args.block_days, first_payout_refund=args.first_payout_refund)
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
