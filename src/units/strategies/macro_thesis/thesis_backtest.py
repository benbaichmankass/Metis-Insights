"""M28 P4 — the thesis backtest scoring core (calibration + net-of-cost).

P4 is the **decisive gate** (design §P4): a thesis engine graduates to a live
path only after it beats a naive baseline out-of-sample here. Weeks-horizon →
few trades, so — per §8 "low-n honesty" — the sleeve is validated by
**calibration** (does ``thesis_conviction`` predict realized hit-rate?) and
**net-of-cost return**, NEVER by claiming significance from a handful of wins.

This module is the **pure, correct-by-construction scoring machinery**:

- :func:`net_return` — a thesis's realized fractional return, direction-aware and
  **net of round-trip fees + carry** (the weeks-horizon holding cost).
- :func:`calibration_bins` / :func:`calibration_rank` — the calibration gate: bin
  outcomes by conviction and measure per-bin hit-rate + mean net return, plus a
  rank correlation between conviction and outcome (the "does conviction predict?"
  number).
- :func:`score_backtest` — the aggregate scorecard, optionally against a naive
  baseline (the beat-the-baseline gate).

The point-in-time *replay harness* (as-of reads → S1 former → forward prices)
composes these via :func:`run_thesis_backtest`, which takes **injected** per-date
``(thesis, forward_price)`` outcomes so point-in-time integrity (no lookahead) is
the caller's guarantee and this stays fully unit-testable offline. Pure — no I/O,
no clock, no order path.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

# ---------------------------------------------------------------------------
# per-thesis realized return (net of cost)
# ---------------------------------------------------------------------------


def net_return(
    direction: str,
    entry_price: float,
    exit_price: float,
    *,
    fee_frac: float = 0.0,
    carry_frac: float = 0.0,
) -> Optional[float]:
    """A thesis's realized fractional return, direction-aware + net of cost.

    ``long`` earns ``(exit-entry)/entry``, ``short`` the negative; then round-trip
    ``fee_frac`` and total holding ``carry_frac`` are subtracted (both are costs,
    always reduce the return). Returns ``None`` on a non-positive/……non-finite
    entry (honest-null — never a fabricated number)."""
    try:
        e = float(entry_price)
        x = float(exit_price)
    except (TypeError, ValueError):
        return None
    if not (e > 0) or x != x or e != e:  # non-positive entry / NaN
        return None
    raw = (x - e) / e
    if str(direction).lower() == "short":
        raw = -raw
    elif str(direction).lower() != "long":
        return None
    return raw - abs(fee_frac) - abs(carry_frac)


def thesis_outcome(
    thesis_conviction: Optional[float],
    direction: str,
    entry_price: float,
    exit_price: float,
    *,
    fee_frac: float = 0.0,
    carry_frac: float = 0.0,
    thesis_id: Optional[str] = None,
) -> Optional[dict]:
    """One scored outcome ``{thesis_id, conviction, net_return, win}`` or ``None``
    when the return is uncomputable / conviction is missing (dropped from the
    calibration, never counted as a loss)."""
    nr = net_return(direction, entry_price, exit_price,
                    fee_frac=fee_frac, carry_frac=carry_frac)
    if nr is None or thesis_conviction is None:
        return None
    return {"thesis_id": thesis_id, "conviction": float(thesis_conviction),
            "net_return": nr, "win": nr > 0.0}


# ---------------------------------------------------------------------------
# calibration — the low-n gate
# ---------------------------------------------------------------------------


def calibration_bins(outcomes: Sequence[Mapping[str, Any]], *, n_bins: int = 4) -> list[dict]:
    """Bin outcomes by conviction into ``n_bins`` equal-width ``[0,1]`` buckets;
    per bin: ``{lo, hi, n, hit_rate, mean_net_return}``. Empty bins are kept
    (``n=0``, null stats) so the conviction axis is always fully represented."""
    n_bins = max(1, int(n_bins))
    buckets: list[list[dict]] = [[] for _ in range(n_bins)]
    for o in outcomes or []:
        c = o.get("conviction")
        if c is None:
            continue
        idx = min(n_bins - 1, max(0, int(float(c) * n_bins)))  # 1.0 → last bin
        buckets[idx].append(dict(o))
    out: list[dict] = []
    for i, b in enumerate(buckets):
        lo, hi = i / n_bins, (i + 1) / n_bins
        n = len(b)
        hit = (sum(1 for x in b if x.get("win")) / n) if n else None
        mret = (sum(float(x.get("net_return", 0.0)) for x in b) / n) if n else None
        out.append({"lo": lo, "hi": hi, "n": n, "hit_rate": hit, "mean_net_return": mret})
    return out


def _rank(values: Sequence[float]) -> list[float]:
    """Fractional (tie-averaged) ranks — the Spearman building block."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based average rank across the tie block
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def calibration_rank(outcomes: Sequence[Mapping[str, Any]]) -> Optional[float]:
    """Spearman rank correlation between conviction and net return — the single
    "does conviction predict outcome?" number in ``[-1, 1]`` (positive = higher
    conviction → better realized return). ``None`` when < 2 points or no variance
    (honest-null; never a fabricated correlation on a degenerate sample)."""
    conv = [float(o["conviction"]) for o in outcomes if o.get("conviction") is not None
            and o.get("net_return") is not None]
    ret = [float(o["net_return"]) for o in outcomes if o.get("conviction") is not None
           and o.get("net_return") is not None]
    n = len(conv)
    if n < 2:
        return None
    rc, rr = _rank(conv), _rank(ret)
    mc, mr = sum(rc) / n, sum(rr) / n
    num = sum((a - mc) * (b - mr) for a, b in zip(rc, rr))
    dc = sum((a - mc) ** 2 for a in rc) ** 0.5
    dr = sum((b - mr) ** 2 for b in rr) ** 0.5
    if dc == 0 or dr == 0:
        return None
    return num / (dc * dr)


# ---------------------------------------------------------------------------
# aggregate scorecard
# ---------------------------------------------------------------------------


def score_backtest(
    outcomes: Sequence[Mapping[str, Any]],
    *,
    n_bins: int = 4,
    baseline_outcomes: Optional[Sequence[Mapping[str, Any]]] = None,
) -> dict:
    """The aggregate P4 scorecard: n / win_rate / mean_net_return / expectancy +
    the calibration bins + rank. With ``baseline_outcomes`` (e.g. a naive
    all-long arm) it adds ``edge_vs_baseline`` (mean-net-return delta) — the
    beat-the-baseline gate. All stats are ``None`` on an empty sample (never 0)."""
    valid = [o for o in outcomes or [] if o.get("net_return") is not None]
    n = len(valid)
    rets = [float(o["net_return"]) for o in valid]
    wins = [o for o in valid if o.get("win")]
    losses = [o for o in valid if not o.get("win")]
    win_rate = (len(wins) / n) if n else None
    mean_net = (sum(rets) / n) if n else None
    avg_win = (sum(float(o["net_return"]) for o in wins) / len(wins)) if wins else None
    avg_loss = (sum(float(o["net_return"]) for o in losses) / len(losses)) if losses else None
    # expectancy = win_rate*avg_win + (1-win_rate)*avg_loss (== mean_net when full)
    expectancy = mean_net
    card = {
        "n": n,
        "win_rate": win_rate,
        "mean_net_return": mean_net,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "calibration_bins": calibration_bins(valid, n_bins=n_bins),
        "calibration_rank": calibration_rank(valid),
    }
    if baseline_outcomes is not None:
        base = [float(o["net_return"]) for o in baseline_outcomes
                if o.get("net_return") is not None]
        base_mean = (sum(base) / len(base)) if base else None
        card["baseline_mean_net_return"] = base_mean
        card["edge_vs_baseline"] = (
            mean_net - base_mean if (mean_net is not None and base_mean is not None) else None
        )
    return card


# ---------------------------------------------------------------------------
# point-in-time replay harness (composes the primitives)
# ---------------------------------------------------------------------------


def run_thesis_backtest(
    entries: Iterable[Mapping[str, Any]],
    *,
    fee_frac: float = 0.0,
    carry_frac_per_day: float = 0.0,
    n_bins: int = 4,
) -> dict:
    """Score a sequence of point-in-time thesis entries into the P4 scorecard.

    Each entry is ``{thesis_id, conviction, direction, entry_price, exit_price,
    hold_days?}`` — produced by the caller's **point-in-time** replay (as-of value
    reads → S1 former → the forward price at the calendar exit), so no-lookahead
    integrity is guaranteed upstream and this stays pure. Carry is
    ``carry_frac_per_day × hold_days``. A naive all-long baseline (same entries,
    forced ``direction='long'``) is scored alongside for the beat-baseline edge."""
    outcomes: list[dict] = []
    baseline: list[dict] = []
    for e in entries or []:
        hold_days = float(e.get("hold_days", 0.0) or 0.0)
        carry = abs(carry_frac_per_day) * hold_days
        o = thesis_outcome(
            e.get("conviction"), e.get("direction"), e.get("entry_price"),
            e.get("exit_price"), fee_frac=fee_frac, carry_frac=carry,
            thesis_id=e.get("thesis_id"),
        )
        if o is not None:
            outcomes.append(o)
            b = thesis_outcome(
                e.get("conviction"), "long", e.get("entry_price"),
                e.get("exit_price"), fee_frac=fee_frac, carry_frac=carry,
                thesis_id=e.get("thesis_id"),
            )
            if b is not None:
                baseline.append(b)
    return score_backtest(outcomes, n_bins=n_bins, baseline_outcomes=baseline)
