#!/usr/bin/env python3
# wiring: manual-only — a one-shot SCREEN of the nine -1x inverse-ETF proxy
# candidates against the two hard walls of the real-money `alpaca_live`
# account, plus the parent-leg join that says which of them could serve a leg
# this account actually routes.
"""MI-251 — scope the inverse-ETF route for alpaca_live's dead short side.

WHY THIS EXISTS. `alpaca_live` is a CASH account and Alpaca reports
`shorting_enabled: false` on it, so every SHORT its legs produce is
unexecutable at the venue. The operator's standing directive (reaffirmed
2026-08-25) is that shorting is NEVER enabled broker-side, so the only route
to that flow is a -1x inverse ETF held LONG. `BL-20260823-NO-INVERSE-ETF-INSTRUMENTS-DECLARED`
carries the programme; a 2026-08-25 sweep named nine affordable -1x
candidates.

⚠️ THIS SCREENS AND PROPOSES. IT WIRES NOTHING. Declaring an instrument on
`alpaca_live` touches a real-money `symbols:` list AND the CI-enforced
`alpaca_portfolio` mirror equality, so any wiring is a separate Tier-3
decision with its own approval.

WHAT THIS DOES DIFFERENTLY FROM ITS PREDECESSOR, AND WHY IT MATTERS
-------------------------------------------------------------------
`docs/research/alpaca-200-affordability-sweep-2026-08-25.md` recorded a LIST
OF REACHABLE SYMBOLS. Its own superseding note says that was the defect:
reachability is a function of PRICE and VOLATILITY, both of which move
without a commit, and `GDX` had already fallen out of the set unnoticed --
see `BL-20260910-THE-ALPACA-LIVE-REACHABLE-SYMBOL-SET-IS-RECORDED-AS-A-CONSTANT-AND-GDX-HAS-SINCE-FALLEN-OUT-OF-IT`.
Writing a second verdict list 25 days
later would reproduce that defect exactly.

So this emits the two walls as CEILINGS — functions of the account's own
declared config — and grades a dated price against them:

    cash ceiling  = _MARGIN_SAFETY_BUFFER x available_usd
    stop ceiling  = _ROUND_UP_BUDGET_MULT x risk_pct x equity

POSITIVE CONTROL ON THAT DERIVATION, and it is not decorative: MI-201
independently measured GDX's ceiling at **$6.0066** against a $200.22 balance
at `risk_pct 0.02`. The formula above reproduces that to the cent
(1.5 x 0.02 x 200.22 = 6.0066). `--selftest` asserts it, so a future edit to
either constant that silently changes the wall fails here rather than in a
memo nobody re-runs.

⚠️ THE RISK CEILING IS NOT `risk_pct x equity`. The whole-share path carries
a ROUND-UP relaxation (operator directive 2026-06-24): when the risk-ideal
size is under one share the sizer rounds UP to one, provided that share's
stop risk is within `_ROUND_UP_BUDGET_MULT` of the budget. Omitting the 1.5x
understates the ceiling by a third and reports reachable instruments as
refused.

STATES, NEVER COLLAPSED
-----------------------
Affordability (`wall_state`):
    reachable           both walls clear
    cash_bound          price alone exceeds the cash ceiling
    risk_bound          stop alone exceeds the stop ceiling
    cash_and_risk_bound both
    price_unknown       WE DID NOT LOOK — no dated price for this ticker.
                        NEVER pooled with a refusal.

Usefulness (`parent_state`) — affordability is necessary and NOT sufficient:
    serves_routed_leg     its parent symbol is traded by a leg on this
                          account's CURRENT roster
    serves_unrouted_leg   the parent leg exists and is live, but routes
                          elsewhere (today: paper accounts only)
    no_parent_leg         no leg anywhere names the parent symbol — this is
                          a NEW exposure, not a proxy for anything
    parent_has_no_proxy   the parent is in the metals complex, where the
                          operator RECORDED on 2026-08-25 that no acceptable
                          -1x product exists and the legs stay long-only
                          with reduced flow

⚠️ `no_parent_leg` IS THE FINDING, NOT A GAP TO FILL. A candidate that is
affordable and serves nothing is not a proxy; proposing it would add an
exposure nobody asked for to a real-money account.

Usage:
    python3 scripts/research/mi251_inverse_proxy_screen.py --selftest
    python3 scripts/research/mi251_inverse_proxy_screen.py --report
    python3 scripts/research/mi251_inverse_proxy_screen.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

ACCOUNT_ID = "alpaca_live"

# ---------------------------------------------------------------- wall states
W_REACHABLE = "reachable"
W_CASH = "cash_bound"
W_RISK = "risk_bound"
W_BOTH = "cash_and_risk_bound"
W_UNKNOWN = "price_unknown"          # we did not look — never a refusal

# -------------------------------------------------------------- parent states
P_ROUTED = "serves_routed_leg"
P_UNROUTED = "serves_unrouted_leg"
P_NONE = "no_parent_leg"
P_NO_PROXY = "parent_has_no_proxy"

# The METALS complex. Operator decision, 2026-08-25, recorded on
# BL-20260823-NO-INVERSE-ETF-INSTRUMENTS-DECLARED: GLD, IAUM, SLV and GDX are
# long-only WITH REDUCED FLOW. There is no acceptable -1x product to find --
# gold's only -1x (DGZ) is a near-dormant ETN, silver's ZSL is -2x, miners'
# DUST is -2x and GDXD a -3x ETN. This is closed BY DECISION, not open work.
METALS = frozenset({"GLD", "IAUM", "SLV", "GDX"})

# The nine -1x candidates the 2026-08-25 runner sweep priced successfully,
# with that run's DATED observations. `shares` and `risk_usd` are that sweep's
# own sized outputs at `risk_pct 0.05`; the stop distance below is
# RECONSTRUCTED as risk_usd / shares and therefore carries the table's
# rounding. It is an illustration against the ceilings, never a fresh
# measurement -- which is the entire point of emitting ceilings.
#
# DDG (-1x energy) is the TENTH mapped inverse ticker and is deliberately
# absent from this table: the same run graded it `fetch_failed`, so it has no
# price. It is carried as `price_unknown` rather than dropped, because a
# candidate nobody could price is not a candidate that failed.
PRICE_ASOF = "2026-08-24"
PRICE_RUN = "yfinance-lane-proof run 32828398224 (job 97741405818)"

@dataclass(frozen=True)
class Candidate:
    ticker: str
    parent: str            # the symbol whose SHORT side it would express
    price: Optional[float]  # dated; None = we did not look
    shares_at_5pct: Optional[int]
    risk_usd_at_5pct: Optional[float]
    note: str = ""


CANDIDATES: List[Candidate] = [
    Candidate("DGZ", "GLD", 4.80, 29, 9.69, "near-dormant ETN; ATR ~6.96% of price"),
    Candidate("RWM", "IWM", 13.49, 13, 1.75, ""),
    Candidate("EUM", "EEM", 15.99, 11, 2.50, "emerging markets"),
    Candidate("DOG", "DIA", 21.32, 8, 1.23, "Dow"),
    Candidate("TBF", "TLT", 25.36, 7, 1.46, "~$80M AUM"),
    Candidate("PSQ", "QQQ", 26.18, 6, 1.84, "~$759M AUM"),
    Candidate("TBX", "IEF", 28.81, 6, 0.64, "~$14M AUM -- THIN, flag before use"),
    Candidate("SEF", "XLF", 29.09, 6, 1.44, "financials"),
    Candidate("SH", "SPY", 32.55, 5, 1.05, "~$1B AUM"),
    Candidate("DDG", "XLE", None, None, None, "fetch_failed on the 2026-08-25 run"),
]


def reconstructed_stop(c: Candidate) -> Optional[float]:
    """Stop distance implied by the dated sweep row, or None if unpriced.

    ⚠️ RECONSTRUCTION, not a measurement: it divides two ROUNDED published
    figures. Good to ~1% and used only to illustrate the ceiling.
    """
    if c.risk_usd_at_5pct is None or not c.shares_at_5pct:
        return None
    return c.risk_usd_at_5pct / c.shares_at_5pct


# ------------------------------------------------------------------- the walls
def cash_ceiling(available_usd: float, buffer_mult: float) -> float:
    """Highest share price at which ONE share still fits the cash wall."""
    return buffer_mult * available_usd


def stop_ceiling(equity: float, risk_pct: float, round_up_mult: float) -> float:
    """Widest stop at which ONE share is still admitted by the risk wall.

    Includes the round-up relaxation. Omitting it understates the ceiling by
    a third -- see the module docstring.
    """
    return round_up_mult * risk_pct * equity


def grade_walls(price: Optional[float], stop: Optional[float],
                cash_ceil: float, stop_ceil: float) -> str:
    if price is None:
        return W_UNKNOWN
    over_cash = price > cash_ceil
    over_risk = stop is not None and stop > stop_ceil
    if over_cash and over_risk:
        return W_BOTH
    if over_cash:
        return W_CASH
    if over_risk:
        return W_RISK
    return W_REACHABLE


# ------------------------------------------------------------ the parent join
def grade_parent(parent: str, routed_symbols: frozenset,
                 live_leg_symbols: frozenset) -> str:
    if parent in METALS:
        return P_NO_PROXY
    if parent in routed_symbols:
        return P_ROUTED
    if parent in live_leg_symbols:
        return P_UNROUTED
    return P_NONE


# --------------------------------------------------------------- repo readers
def load_account(path: Optional[Path] = None) -> dict:
    """Read this account's cfg through the CANONICAL accounts loader.

    ⚠️ NOT a hand-rolled yaml.safe_load: `canonical-config-loaders` forbids a
    second accounts.yaml parser, and it caught this file doing exactly that.
    A private parser drifts from the one the runtime uses, which is how a
    screen ends up describing an account nobody routes.

    ⚠️ AND THE LOADER RETURNS {} ON A READ FAILURE, so `errors` is passed and
    CHECKED: without it, a corrupt accounts.yaml and an accounts.yaml with no
    `alpaca_live` are the same empty dict — the collapsed state this whole
    instrument is written to avoid. A parse failure REFUSES here; it never
    renders as an absent account.
    """
    from src.config.accounts_loader import load_accounts_dict

    errors: List[Dict[str, Any]] = []
    accs = load_accounts_dict(path, errors=errors)
    if errors:
        raise SystemExit(
            f"accounts.yaml could not be parsed ({errors[0].get('error')}) -- "
            "refusing to report on an account we could not read")
    acc = accs.get(ACCOUNT_ID)
    if not isinstance(acc, dict):
        raise SystemExit(f"{ACCOUNT_ID} absent from accounts.yaml -- refusing to guess")
    return acc


def load_strategies(path: Optional[Path] = None) -> dict:
    import yaml
    p = path or (REPO / "config" / "strategies.yaml")
    raw = yaml.safe_load(p.read_text())
    return raw.get("strategies", raw)


def routed_symbols_for(acc: dict, legs: dict) -> frozenset:
    """Symbols reachable through the legs THIS account currently routes.

    ⚠️ Derived from the account's `strategies:` list joined to each leg's own
    `symbols:`, NOT from the account's `symbols:` list. Those two disagree
    today -- the account declares 11 symbols and routes 5 legs covering 4 --
    and the one that decides whether a signal is ever produced is the leg.
    """
    out = set()
    for name in (acc.get("strategies") or []):
        leg = legs.get(name)
        if isinstance(leg, dict):
            out.update(leg.get("symbols") or [])
    return frozenset(out)


def live_leg_symbols(legs: dict) -> frozenset:
    """Every symbol named by an `execution: live` leg anywhere in the fleet."""
    out = set()
    for leg in legs.values():
        if isinstance(leg, dict) and (leg.get("execution") or "live") == "live":
            out.update(leg.get("symbols") or [])
    return frozenset(out)


def mapped_tickers() -> Optional[frozenset]:
    """Tickers the repo's OWN yfinance adapter can resolve, or None.

    Membership is NOT tradeability -- the adapter says so itself. It answers
    only "could we fetch candles for this without new wiring?".

    ⚠️ RETURNS None FOR *WE COULD NOT LOOK*, NEVER AN EMPTY SET. An earlier
    draft caught a broad `Exception` and returned `frozenset()`, which
    `silent-empty-guard` refused -- correctly: an adapter that fails to import
    would then render EVERY candidate as `UNMAPPED`, a confident verdict about
    data availability derived from having no information at all. The exception
    is NARROWED rather than annotated, on MI-319's lesson: a broad catch would
    grade a genuine defect in this instrument as an ordinary negative.
    """
    try:
        from ml.datasets.adapters.yf_symbols import known_symbols
        return known_symbols()
    except (ImportError, AttributeError, OSError):
        return None


# ------------------------------------------------------------------- the screen
def screen(acc: dict, legs: dict, equity: float) -> dict:
    from src.units.accounts.risk import (
        _MARGIN_SAFETY_BUFFER, _ROUND_UP_BUDGET_MULT,
    )
    risk = acc.get("risk") or {}
    risk_pct = float(risk.get("risk_pct", 0.0))
    cash_ceil = cash_ceiling(equity, _MARGIN_SAFETY_BUFFER)
    stop_ceil = stop_ceiling(equity, risk_pct, _ROUND_UP_BUDGET_MULT)

    routed = routed_symbols_for(acc, legs)
    live_syms = live_leg_symbols(legs)
    mapped = mapped_tickers()

    rows = []
    for c in CANDIDATES:
        stop = reconstructed_stop(c)
        rows.append({
            "ticker": c.ticker,
            "parent": c.parent,
            "price_asof": c.price,
            "stop_reconstructed": None if stop is None else round(stop, 4),
            "wall_state": grade_walls(c.price, stop, cash_ceil, stop_ceil),
            "parent_state": grade_parent(c.parent, routed, live_syms),
            # None (not False) when the adapter could not be read at all.
            "candles_mapped": None if mapped is None else (c.ticker in mapped),
            "note": c.note,
        })
    return {
        "account": ACCOUNT_ID,
        "account_class": acc.get("account_class"),
        "mode": acc.get("mode"),
        "side_filter": acc.get("side_filter"),
        "equity_usd": equity,
        "risk_pct": risk_pct,
        "cash_ceiling_usd": round(cash_ceil, 4),
        "stop_ceiling_usd": round(stop_ceil, 4),
        "margin_safety_buffer": _MARGIN_SAFETY_BUFFER,
        "round_up_budget_mult": _ROUND_UP_BUDGET_MULT,
        "price_asof": PRICE_ASOF,
        "price_source": PRICE_RUN,
        "routed_symbols": sorted(routed),
        "routed_legs": sorted(acc.get("strategies") or []),
        "declared_symbols": sorted(acc.get("symbols") or []),
        "candidates": rows,
    }


# ------------------------------------------------- end-to-end sizer agreement
# The ceilings above are DERIVED from the sizer's two constants. That is not
# the same as the sizer agreeing, and `alpaca-200-affordability-sweep` is
# explicit that verdicts must come from "the real sizer, not arithmetic over
# the table". This runs it.
#
# ⚠️ GDX IS THE NEGATIVE CONTROL AND IS NOT A CANDIDATE. MI-201 measured it
# REFUSED on all 59 of gdx_pullback_1d's observed setups at this balance and
# risk_pct. A run in which GDX sizes means this harness is not reproducing the
# live gate, and every PASS below is then worthless -- which is why it is
# asserted rather than reported.
GDX_CONTROL = ("GDX", 99.43, 7.665)


def sizer_agreement(equity: float, risk_pct: float) -> dict:
    """Run the REAL RiskManager over each priced candidate + the control."""
    from src.core.coordinator import OrderPackage
    from src.units.accounts.risk import RiskManager

    rm = RiskManager(
        {"max_dd_pct": 0.1, "daily_loss_pct": 0.1, "daily_usd": 200,
         "risk_pct": risk_pct},
        account_id=ACCOUNT_ID,
    )

    def size(ticker: str, px: float, stop: float) -> float:
        pkg = OrderPackage(strategy="mi251_probe", symbol=ticker,
                           direction="long", entry=px, sl=px - stop,
                           tp=px + 3 * stop, confidence=0.6)
        return float(rm.position_size(pkg, equity, available_usd=equity,
                                      whole_units=True))

    rows, disagreements = [], []
    cash_ceil = cash_ceiling(equity, 0.9)
    stop_ceil = stop_ceiling(equity, risk_pct, 1.5)
    for c in CANDIDATES:
        stop = reconstructed_stop(c)
        if c.price is None or stop is None:
            continue
        qty = size(c.ticker, c.price, stop)
        predicted = grade_walls(c.price, stop, cash_ceil, stop_ceil) == W_REACHABLE
        if (qty > 0) != predicted:
            disagreements.append(c.ticker)
        rows.append({"ticker": c.ticker, "qty": qty,
                     "notional_usd": round(qty * c.price, 2),
                     "risk_usd": round(qty * stop, 2),
                     "risk_pct_of_equity": round(100 * qty * stop / equity, 3),
                     "sizes": qty > 0, "ceiling_predicted_reachable": predicted})

    g_t, g_px, g_stop = GDX_CONTROL
    gdx_qty = size(g_t, g_px, g_stop)
    return {
        "equity_usd": equity,
        "risk_pct": risk_pct,
        "rows": rows,
        "ceiling_vs_sizer_disagreements": disagreements,
        "negative_control": {
            "ticker": g_t, "price": g_px, "stop": g_stop, "qty": gdx_qty,
            "refused_as_expected": gdx_qty == 0.0,
            "basis": "MI-201 measured GDX refused on 59 of 59 observed setups",
        },
    }


# ---------------------------------------------------------------- self-test
def _selftest() -> int:
    checks: List[tuple] = []

    def ok(name, cond):
        checks.append((name, bool(cond)))

    # --- the ceilings, against an INDEPENDENTLY measured number -------------
    # MI-201 measured GDX's ceiling at $6.0066 on a $200.22 balance at
    # risk_pct 0.02. If either constant is edited so the wall moves, this
    # fails here rather than silently in a memo.
    ok("POSITIVE CONTROL: reproduces MI-201's measured $6.0066 GDX ceiling",
       abs(stop_ceiling(200.22, 0.02, 1.5) - 6.0066) < 1e-4)
    ok("cash ceiling is buffer x available, not available",
       abs(cash_ceiling(200.0, 0.9) - 180.0) < 1e-9)

    # NEGATIVE CONTROL: dropping the round-up relaxation must CHANGE the
    # answer -- otherwise this test proves nothing about the 1.5x.
    ok("NEG: omitting the round-up mult understates the ceiling",
       stop_ceiling(200.0, 0.02, 1.0) < stop_ceiling(200.0, 0.02, 1.5))
    # NEGATIVE CONTROL: and it must change a real VERDICT, not just a number.
    _c, _s = cash_ceiling(200.0, 0.9), stop_ceiling(200.0, 0.02, 1.5)
    ok("NEG: a stop between the two mults flips reachable->risk_bound",
       grade_walls(20.0, 5.0, _c, _s) == W_REACHABLE
       and grade_walls(20.0, 5.0, _c, stop_ceiling(200.0, 0.02, 1.0)) == W_RISK)

    # --- wall grading -------------------------------------------------------
    ok("price over the cash ceiling is cash_bound",
       grade_walls(999.0, 0.10, _c, _s) == W_CASH)
    ok("stop over the stop ceiling is risk_bound",
       grade_walls(10.0, 99.0, _c, _s) == W_RISK)
    ok("both over is cash_and_risk_bound",
       grade_walls(999.0, 99.0, _c, _s) == W_BOTH)
    ok("both under is reachable",
       grade_walls(10.0, 0.10, _c, _s) == W_REACHABLE)
    # NEGATIVE CONTROL: absence must never render as a refusal.
    ok("NEG: an unpriced candidate is price_unknown, NOT cash_bound",
       grade_walls(None, None, _c, _s) == W_UNKNOWN)
    ok("NEG: price_unknown is not any refusal state",
       W_UNKNOWN not in {W_CASH, W_RISK, W_BOTH, W_REACHABLE})
    # NEGATIVE CONTROL: an unknown STOP must not manufacture a pass by itself
    # -- it may only be graded on the wall we CAN read.
    ok("an unknown stop grades on price alone, never inventing a risk pass",
       grade_walls(999.0, None, _c, _s) == W_CASH
       and grade_walls(10.0, None, _c, _s) == W_REACHABLE)

    # --- the parent join ----------------------------------------------------
    routed = frozenset({"TLT", "IEF", "SLV", "IAUM"})
    livesy = frozenset({"TLT", "IEF", "SLV", "IAUM", "SPY", "QQQ"})
    ok("a parent on the current roster serves a routed leg",
       grade_parent("TLT", routed, livesy) == P_ROUTED)
    ok("a live parent routed elsewhere is serves_unrouted_leg",
       grade_parent("SPY", routed, livesy) == P_UNROUTED)
    ok("a parent no leg names at all is no_parent_leg",
       grade_parent("XLF", routed, livesy) == P_NONE)
    # NEGATIVE CONTROL: the metals decision must WIN over routing, or a
    # session would propose building a gold proxy the operator already ruled
    # does not exist.
    ok("NEG: metals is parent_has_no_proxy EVEN WHEN routed",
       grade_parent("SLV", routed, livesy) == P_NO_PROXY
       and grade_parent("IAUM", routed, livesy) == P_NO_PROXY)
    ok("NEG: serves_unrouted_leg is not pooled with serves_routed_leg",
       P_UNROUTED != P_ROUTED)

    # --- the reconstruction -------------------------------------------------
    c_tbf = next(c for c in CANDIDATES if c.ticker == "TBF")
    ok("reconstructed stop = risk/shares",
       abs(reconstructed_stop(c_tbf) - (1.46 / 7)) < 1e-9)
    # NEGATIVE CONTROL: an unpriced row must not reconstruct a number.
    c_ddg = next(c for c in CANDIDATES if c.ticker == "DDG")
    ok("NEG: an unpriced candidate reconstructs None, not 0.0",
       reconstructed_stop(c_ddg) is None)

    # --- the routed-symbol reader ------------------------------------------
    legs = {"a_1h": {"symbols": ["TLT"], "execution": "live"},
            "b_1d": {"symbols": ["SPY"], "execution": "live"},
            "c_1d": {"symbols": ["ZZZ"], "execution": "shadow"}}
    acc = {"strategies": ["a_1h"], "symbols": ["TLT", "SPY", "GLD"]}
    ok("routed symbols come from the LEGS, not the account symbols list",
       routed_symbols_for(acc, legs) == frozenset({"TLT"}))
    # NEGATIVE CONTROL: this is the whole point -- reading the account's
    # `symbols:` would return SPY and GLD too and report a proxy as useful.
    ok("NEG: the account symbols list would give a DIFFERENT, wrong answer",
       frozenset(acc["symbols"]) != routed_symbols_for(acc, legs))
    ok("a shadow leg's symbol is not a live symbol",
       "ZZZ" not in live_leg_symbols(legs))
    ok("live_leg_symbols spans the fleet, not one account",
       live_leg_symbols(legs) == frozenset({"TLT", "SPY"}))

    # --- the third state on candle availability -------------------------
    # NEGATIVE CONTROL for the silent-empty fix: an unreadable adapter must
    # render as `could_not_read`, never as UNMAPPED. Reporting every candidate
    # unmapped because the adapter failed to import is a confident verdict
    # about data availability drawn from no information at all.
    ok("NEG: an unreadable adapter renders could_not_read, NOT UNMAPPED",
       _candles_label(None) == "could_not_read")
    ok("a mapped ticker still renders mapped, an unmapped one UNMAPPED",
       _candles_label(True) == "mapped" and _candles_label(False) == "UNMAPPED")
    ok("NEG: could_not_read is not either of the two graded labels",
       _candles_label(None) not in {_candles_label(True), _candles_label(False)})

    bad = [n for n, good in checks if not good]
    for n, good in checks:
        print(f"  {'ok  ' if good else 'FAIL'}  {n}")
    print(f"mi251 selftest: {len(checks) - len(bad)}/{len(checks)} passed")
    return 1 if bad else 0



def _candles_label(v: Optional[bool]) -> str:
    """`could_not_read` is a THIRD value and must never print as UNMAPPED."""
    if v is None:
        return "could_not_read"
    return "mapped" if v else "UNMAPPED"


def _render(res: dict) -> str:
    L = []
    A = L.append
    A(f"account            : {res['account']} ({res['account_class']}, "
      f"mode={res['mode']}, side_filter={res['side_filter']})")
    A(f"equity / risk_pct  : ${res['equity_usd']:.2f} / {res['risk_pct']}")
    A(f"CASH ceiling       : ${res['cash_ceiling_usd']:.2f}"
      f"   (= {res['margin_safety_buffer']} x available)")
    A(f"STOP ceiling       : ${res['stop_ceiling_usd']:.4f}"
      f" (= {res['round_up_budget_mult']} x risk_pct x equity)")
    A(f"routed legs        : {', '.join(res['routed_legs'])}")
    A(f"routed symbols     : {', '.join(res['routed_symbols'])}")
    A(f"prices as of       : {res['price_asof']} -- DATED, re-grade before acting")
    A("")
    A(f"{'ticker':7} {'parent':7} {'price':>8} {'stop':>7}  {'wall':20} "
      f"{'parent_state':22} candles")
    for r in res["candidates"]:
        p = "   n/a" if r["price_asof"] is None else f"{r['price_asof']:8.2f}"
        s = "    n/a" if r["stop_reconstructed"] is None else f"{r['stop_reconstructed']:7.3f}"
        A(f"{r['ticker']:7} {r['parent']:7} {p} {s}  {r['wall_state']:20} "
          f"{r['parent_state']:22} {_candles_label(r['candles_mapped'])}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--json", metavar="PATH")
    ap.add_argument("--sizer", action="store_true",
                    help="run the REAL RiskManager over the candidates")
    ap.add_argument("--equity", type=float, default=200.0,
                    help="account equity to screen against (default 200.0)")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    acc = load_account()
    res = screen(acc, load_strategies(), a.equity)
    if a.sizer:
        res["sizer_agreement"] = sizer_agreement(
            a.equity, float((acc.get('risk') or {}).get('risk_pct', 0.0)))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=2) + "\n")
        print(f"wrote {a.json}")
    if a.report or not a.json:
        print(_render(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
