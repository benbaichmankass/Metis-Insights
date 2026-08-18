"""Portfolio-level conflicts in the OPEN BOOK — the question a lever cannot ask.

WHY THIS EXISTS (operator directive, 2026-08-18).

    "We're still holding a short XRP trade from three weeks ago even though we
    just opened a long ETH trade ... those are relatively correlated symbols
    ... that is the kind of thing that I would want us to know to take a look
    at ... not just the individual levers, but how we create a holistic picture
    for making actual informed decisions."

Every exit lever this repo has built or swept — `stale_stop`, `giveback_stop`,
`trail_decay`, `rr_floor`, `thesis_decay` — is a univariate cut on a SINGLE
trade's own path. Each asks *"is this trade bad?"*. None can ask *"is this
trade the best use of this capital, given everything else we hold?"*, because
none of them can see anything but the one trade. That is a structural limit,
not a tuning gap, and sweeping more single-trade cells will keep returning the
same answer for the same reason.

This module reads the whole open book at once and reports CONFLICTS: states
that are suspicious on their face, independent of any one trade's P&L.

WHAT IT FOUND ON ITS FIRST RUN, which is why the conflicts below are the ones
it grades (live book, 35 open positions, 2026-08-18):

  * `eth_pullback_2h` held LONG on bybit_1 and SHORT on bybit_portfolio.
  * `trend_donchian_eth_4h` held LONG on bybit_1 + bybit_2 and SHORT on
    bybit_portfolio.
  * Four signals whose two mirror legs opened at an IDENTICAL entry now carry
    DIFFERENT stops — one trailed into profit, its twin still at original risk
    (XRP 4163/4164, TLT 4169/4170, QQQ 4468/4469, SPY 4347/4348).

The netting guard is per `(account, strategy, symbol)`, so cross-account
opposition sits outside what it checks. Nothing else looked at the book as a
book, so none of the above was visible on any surface — an operator noticed the
weakest instance of it by eye.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
It does not decide anything, and it does not rank. A conflict is a prompt to
look, not a verdict that a position is wrong: a paper mirror legitimately
diverging from the live book, and a mirror that silently stopped tracking, are
the same shape here and are told apart by reading the rows, not by this code.

It also **does not compute correlation**. Correlation must be MEASURED against
price history; asserting that XRP and ETH are correlated because they are both
crypto would be exactly the kind of assumed input this repo keeps getting
burned by. The correlated-exposure half lives in the caller, which supplies a
measured correlation matrix, and every symbol pair with no measurement is
reported ``unmeasured`` rather than assumed uncorrelated — the two are opposite
statements and only one of them is safe to act on.

Observe-only. Pure functions over a list of position dicts. No DB, no socket,
no order path.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Conflict kinds. Each is a DIFFERENT question; they are never merged into one
# "problem" score, because the remedies are different and a blended score would
# hide which one fired.
OPPOSING_SAME_SYMBOL = "opposing_same_symbol"
SELF_OPPOSING_STRATEGY = "self_opposing_strategy"
MIRROR_STOP_DIVERGENCE = "mirror_stop_divergence"
CORRELATED_OPPOSITION = "correlated_opposition"
NOMINAL_STOP = "nominal_stop"

_LONG, _SHORT = "long", "short"


def norm_side(side: Any) -> Optional[str]:
    """`long`/`short`, or `None` for anything non-directional or unreadable.

    `None` propagates as *"we could not read this row's side"* — a row we cannot
    orient is excluded from every directional conflict rather than defaulted to
    one side, which would manufacture or mask an opposition.
    """
    if not isinstance(side, str):
        return None
    s = side.strip().lower()
    if s in ("long", "buy"):
        return _LONG
    if s in ("short", "sell"):
        return _SHORT
    return None


def _num(value: Any) -> Optional[float]:
    """Finite float or `None`. Never coerces an unreadable value to 0.0."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


@dataclass(frozen=True)
class Conflict:
    """One finding, carrying the rows that produced it.

    `positions` rides along so a reader can check the finding without re-deriving
    it — the diagnostic-provenance discipline. A conflict that reports only its
    own conclusion cannot be contradicted, which is how a wrong one survives.
    """

    kind: str
    key: str
    detail: str
    positions: List[Dict[str, Any]] = field(default_factory=list)


def _rows_by(rows: Iterable[Dict[str, Any]], *keys: str) -> Dict[Tuple, List[Dict]]:
    out: Dict[Tuple, List[Dict]] = defaultdict(list)
    for r in rows:
        out[tuple(str(r.get(k)) for k in keys)].append(r)
    return out


def opposing_same_symbol(rows: Sequence[Dict[str, Any]]) -> List[Conflict]:
    """Both directions held on one symbol, anywhere in the book.

    Not necessarily wrong — two strategies on different timeframes can
    legitimately disagree, and the intent layer's `FLIP_POLICY=hold` explicitly
    permits a held position to survive an opposing signal. It is reported
    because nothing else reports it, and because the alternative to reporting is
    an operator spotting it by eye.
    """
    out = []
    for (symbol,), rs in sorted(_rows_by(rows, "symbol").items()):
        sides = {norm_side(r.get("side")) for r in rs} - {None}
        if len(sides) > 1:
            out.append(Conflict(
                OPPOSING_SAME_SYMBOL, symbol,
                f"{len(rs)} open positions on {symbol} hold BOTH directions",
                list(rs)))
    return out


def self_opposing_strategy(rows: Sequence[Dict[str, Any]]) -> List[Conflict]:
    """ONE strategy holding both directions on one symbol, across accounts.

    Strictly worse than `opposing_same_symbol`: two strategies disagreeing is a
    difference of opinion, whereas one strategy simultaneously long and short
    the same instrument is the same opinion held both ways. The netting guard
    keys on `(account, strategy, symbol)` and so cannot see this.
    """
    out = []
    for (symbol, strategy), rs in sorted(_rows_by(rows, "symbol", "pattern").items()):
        sides = {norm_side(r.get("side")) for r in rs} - {None}
        if len(sides) > 1:
            accounts = sorted({str(r.get("account")) for r in rs})
            out.append(Conflict(
                SELF_OPPOSING_STRATEGY, f"{strategy}:{symbol}",
                f"{strategy} is BOTH long and short {symbol} across {accounts}",
                list(rs)))
    return out


def mirror_stop_divergence(rows: Sequence[Dict[str, Any]]) -> List[Conflict]:
    """Legs of ONE signal, at an identical entry, now carrying different stops.

    Two legs that share (symbol, strategy, side, entry) were opened by the same
    signal onto different accounts. They should agree about where the stop is;
    if they do not, the trail ran on one and skipped its twin, and the book
    contains a position that is protected and a position that is not.

    A leg whose stop is UNREADABLE is reported as unmeasured within the group
    rather than dropped — a group silently reduced to one comparable leg would
    report agreement it never established.
    """
    out = []
    groups = _rows_by(rows, "symbol", "pattern", "side", "entryPrice")
    for (symbol, strategy, side, entry), rs in sorted(groups.items()):
        if len(rs) < 2:
            continue
        stops = {r.get("id"): _num(r.get("stopLoss")) for r in rs}
        readable = {v for v in stops.values() if v is not None}
        unreadable = [k for k, v in stops.items() if v is None]
        if len(readable) <= 1 and not unreadable:
            continue
        if len(readable) <= 1 and unreadable:
            out.append(Conflict(
                MIRROR_STOP_DIVERGENCE, f"{strategy}:{symbol}:{side}:{entry}",
                f"mirror legs agree on the readable stops but {len(unreadable)} "
                f"leg(s) have no readable stop: {unreadable}", list(rs)))
            continue
        ep, is_short = _num(entry), norm_side(side) == _SHORT
        parts = []
        for r in sorted(rs, key=lambda r: str(r.get("account"))):
            sl = _num(r.get("stopLoss"))
            if sl is None:
                parts.append(f"{r.get('account')}=UNREADABLE")
                continue
            locked = None if ep is None else ((ep - sl) if is_short else (sl - ep))
            tag = ("unmeasured" if locked is None
                   else "locked-profit" if locked > 0 else "at-original-risk")
            parts.append(f"{r.get('account')}={sl} ({tag})")
        out.append(Conflict(
            MIRROR_STOP_DIVERGENCE, f"{strategy}:{symbol}:{side}:{entry}",
            "mirror legs of one signal carry DIFFERENT stops: " + "; ".join(parts),
            list(rs)))
    return out


def correlated_opposition(
    rows: Sequence[Dict[str, Any]],
    correlation: Dict[Tuple[str, str], float],
    *,
    threshold: float = 0.6,
) -> Tuple[List[Conflict], List[Tuple[str, str]]]:
    """Opposite directions on two DIFFERENT symbols that move together.

    Returns `(conflicts, unmeasured_pairs)`. The second element is load-bearing
    and is why this returns a tuple: a symbol pair absent from `correlation` is
    **not** evidence the two are uncorrelated, and folding it into "no conflict"
    would report a clean book over an unstated denominator. The caller must show
    both.

    `correlation` is keyed by an unordered symbol pair; the caller measures it.
    This function asserts nothing about which symbols move together.
    """
    conflicts: List[Conflict] = []
    unmeasured: List[Tuple[str, str]] = []
    by_symbol: Dict[str, set] = defaultdict(set)
    for r in rows:
        side = norm_side(r.get("side"))
        if side:
            by_symbol[str(r.get("symbol"))].add(side)
    symbols = sorted(by_symbol)
    for i, a in enumerate(symbols):
        for b in symbols[i + 1:]:
            # An opposition needs a long on one and a short on the other.
            if not ((_LONG in by_symbol[a] and _SHORT in by_symbol[b])
                    or (_SHORT in by_symbol[a] and _LONG in by_symbol[b])):
                continue
            rho = correlation.get((a, b), correlation.get((b, a)))
            if rho is None:
                unmeasured.append((a, b))
                continue
            if rho >= threshold:
                held = [r for r in rows if str(r.get("symbol")) in (a, b)]
                conflicts.append(Conflict(
                    CORRELATED_OPPOSITION, f"{a}~{b}",
                    f"opposite directions on {a} and {b}, which are measured "
                    f"rho={rho:.2f} (>= {threshold})", held))
    return conflicts, unmeasured


def nominal_stop(
    rows: Sequence[Dict[str, Any]], *, frac: float = 0.5,
) -> List[Conflict]:
    """Positions whose stop sits so far from entry it cannot function as one.

    A stop at 2x entry on a short (or 0.5x on a long) is not a risk control; it
    is a placeholder that satisfies a not-null check. This matters to the
    hold-vs-cash question directly: a position with no working stop is not
    "risk we chose", it is unbounded exposure the book is carrying unpriced.

    EXPECTED OCCUPANT, stated so a reader does not mistake this for a defect
    hunt: the M22 market-neutral pairs sleeve runs its own isolated 2-leg
    executor that exits on the spread's z-score, not on a per-leg stop, so its
    legs legitimately carry placeholder stops (measured 2026-08-18: 4739
    ETHUSDT long entry 1894.27 / stop 947.135, and 4738 SOLUSDT short entry
    75.69 / stop 151.38 -- exactly 0.5x and 2.0x). They are reported anyway.
    A position with no working stop should be visible whether or not something
    else is supposed to be watching it; suppressing the known case is how the
    unknown one goes unseen.

    `frac` is the fraction of entry price beyond which a stop is called
    nominal. A row with an unreadable entry or stop is NOT graded -- it is
    returned in the `unreadable` bucket by the caller-facing `audit`, never
    silently passed.
    """
    out = []
    for r in rows:
        ep, sl = _num(r.get("entryPrice")), _num(r.get("stopLoss"))
        if ep is None or sl is None or ep == 0:
            continue
        dist = abs(sl - ep) / abs(ep)
        if dist >= frac:
            out.append(Conflict(
                NOMINAL_STOP, str(r.get("id")),
                f"stop is {dist:.0%} of entry away ({r.get('symbol')} "
                f"{r.get('side')} entry={ep} stop={sl}) -- not a working stop",
                [r]))
    return out


def audit(
    rows: Sequence[Dict[str, Any]],
    correlation: Optional[Dict[Tuple[str, str], float]] = None,
    *,
    threshold: float = 0.6,
    nominal_stop_frac: float = 0.5,
) -> Dict[str, Any]:
    """Every conflict kind over one open book, plus the stated denominators."""
    conflicts: List[Conflict] = []
    conflicts += opposing_same_symbol(rows)
    conflicts += self_opposing_strategy(rows)
    conflicts += mirror_stop_divergence(rows)
    conflicts += nominal_stop(rows, frac=nominal_stop_frac)
    unmeasured_pairs: List[Tuple[str, str]] = []
    correlation_state = "not_supplied"
    if correlation is not None:
        correlation_state = "supplied"
        corr_conflicts, unmeasured_pairs = correlated_opposition(
            rows, correlation, threshold=threshold)
        conflicts += corr_conflicts
    unreadable_side = [r.get("id") for r in rows if norm_side(r.get("side")) is None]
    # Rows the stop grader could not read. Reported, never counted as clean:
    # "this position's stop is fine" and "we could not read this position's
    # stop" are opposite statements.
    ungradeable_stop = [
        r.get("id") for r in rows
        if _num(r.get("entryPrice")) in (None, 0) or _num(r.get("stopLoss")) is None
    ]
    return {
        "positions": len(rows),
        # `not_supplied` is NOT "no correlated opposition found" — nobody looked.
        "correlation_state": correlation_state,
        "correlated_pairs_unmeasured": sorted(set(unmeasured_pairs)),
        "rows_with_unreadable_side": unreadable_side,
        "rows_with_ungradeable_stop": ungradeable_stop,
        "counts": {
            k: sum(1 for c in conflicts if c.kind == k)
            for k in (OPPOSING_SAME_SYMBOL, SELF_OPPOSING_STRATEGY,
                      MIRROR_STOP_DIVERGENCE, CORRELATED_OPPOSITION,
                      NOMINAL_STOP)
        },
        "conflicts": conflicts,
    }
