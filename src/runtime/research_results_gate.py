"""R4 — the research→results promotion gate: PURE verdict logic (observe-only).

Owns the ONE question the R4 gate exists to answer, per leg, per window:

    *Is this leg's realistic-size, real-execution result net-positive on the
    money that was actually MEASURED?*

Design of record: ``docs/research/research-to-results-cost-gate-DESIGN-2026-08-01.md``.
This module is deliberately import-only + side-effect-free so the eventual Tier-3
enforcing gate (``scripts/prop/account_compat_matrix.py`` + the M7
``scripts/ml/strategy_review_packet.py``) reads the SAME verdict the observe-only
reporter (``scripts/research/research_results_gate_report.py``) accrues evidence
with — never a re-derived second copy (the ``exit_price_source``-written-never-read
mistake this whole provenance line of work exists to kill).

THE BINDING CONSTRAINT (review 2026-08-01 + design §2/§3):

  Do NOT gate on ``totalPnl`` — it sums fabricated mark-price PnL (65.3% of
  July's closed rows). The gate reads the MEASURED subset (``totalPnlMeasured``,
  the {measured, estimated} sum surfaced by ``/api/bot/performance``) and
  **abstains** below a coverage floor. An unmeasured mirror is not evidence of
  anything — abstaining is the honest disposition, never a raw-pnl fallback.

Verdicts (a leg is never silently demoted — the gate FLAGS, the walk-forward
diagnosis DECIDES):

  * ``pass``               — coverage clears the floor AND measured net ≥ 0.
  * ``would_block``        — coverage clears the floor AND measured net < 0.
  * ``abstain_unverified`` — coverage below the floor (the poisoned-book state).
  * ``abstain_thin``       — too few closed rows in the window to judge at all.

Nothing here enforces. It computes the verdict; callers observe (P0) until the
operator flips the gate to enforcing (P2, Tier-3).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Defaults, per design §3. All are tuning knobs the caller may override; they
# are NOT env gates (this module never reads the environment) — the enforcing
# gate's thresholds are an operator-approved Tier-3 parameter set.
COVERAGE_FLOOR = 0.6      # the live real-money 7d pnlCoverage figure (design §3.1)
MIN_TRADES = 20          # below this a leg's measured net is too thin to judge

# Verdict status vocabulary — the one place these strings are defined.
PASS = "pass"
WOULD_BLOCK = "would_block"
ABSTAIN_UNVERIFIED = "abstain_unverified"
ABSTAIN_THIN = "abstain_thin"

# The two abstain states share one predicate: neither is a pass OR a fail.
ABSTAIN_STATES = frozenset({ABSTAIN_UNVERIFIED, ABSTAIN_THIN})


def source_verdict(
    stats: Optional[Dict[str, Any]],
    *,
    coverage_floor: float = COVERAGE_FLOOR,
    min_trades: int = MIN_TRADES,
) -> Dict[str, Any]:
    """Verdict for ONE leg from ONE aggregation source (a ``perStrategy`` entry
    from ``/api/bot/performance``, real-money or mirror).

    ``stats`` is the per-strategy dict (or ``None`` when the leg is absent from
    this source — e.g. a leg with no rows in the mirror book). A ``None`` /
    empty source abstains ``thin`` (there is nothing to judge), never passes.

    The pass decision keys on ``totalPnlMeasured`` — the {measured, estimated}
    sum, NEVER ``totalPnl``. The coverage FLOOR (``pnlCoverage``, the
    MEASURED-only fraction) decides whether that sum is trustworthy at all; the
    ordering matters — a leg below the floor abstains BEFORE its (untrustworthy)
    measured net is ever compared to zero.
    """
    trades = int((stats or {}).get("trades") or 0)
    if not stats or trades <= 0:
        return _verdict(ABSTAIN_THIN, stats, coverage_floor, min_trades,
                        detail="no closed rows for this leg in this source/window")
    if trades < min_trades:
        return _verdict(ABSTAIN_THIN, stats, coverage_floor, min_trades,
                        detail=f"{trades} closed rows < min_trades {min_trades}")

    coverage = stats.get("pnlCoverage")
    # coverage is None on a source that could not measure anything — treat as 0.
    cov = float(coverage) if coverage is not None else 0.0
    if cov < coverage_floor:
        return _verdict(ABSTAIN_UNVERIFIED, stats, coverage_floor, min_trades,
                        detail=f"pnlCoverage {cov:.3f} < floor {coverage_floor:.3f} "
                               "— measured subset too thin to trust")

    measured_net = float(stats.get("totalPnlMeasured") or 0.0)
    status = PASS if measured_net >= 0.0 else WOULD_BLOCK
    return _verdict(status, stats, coverage_floor, min_trades,
                    detail=f"measured net {measured_net:+.2f} over pnlCoverage "
                           f"{cov:.3f} (>= floor)")


def _verdict(
    status: str,
    stats: Optional[Dict[str, Any]],
    coverage_floor: float,
    min_trades: int,
    *,
    detail: str,
) -> Dict[str, Any]:
    """Assemble a verdict record — the numbers that JUSTIFY the status travel
    with it, so a reader (or the review packet) never has to re-query to see
    why. ``totalPnl`` is carried ONLY for the contrast (measured vs raw), never
    as the decision input."""
    s = stats or {}
    return {
        "status": status,
        "detail": detail,
        "trades": int(s.get("trades") or 0),
        "totalPnlMeasured": s.get("totalPnlMeasured"),
        "totalPnl": s.get("totalPnl"),          # contrast only — NOT the input
        "pnlCoverage": s.get("pnlCoverage"),
        "pnlMeasuredCount": s.get("pnlMeasuredCount"),
        "coverageFloor": coverage_floor,
        "minTrades": min_trades,
    }


def combined_leg_verdict(
    real_stats: Optional[Dict[str, Any]],
    mirror_stats: Optional[Dict[str, Any]],
    *,
    coverage_floor: float = COVERAGE_FLOOR,
    min_trades: int = MIN_TRADES,
) -> Dict[str, Any]:
    """Combine a leg's REAL-money read and its MIRROR (paper-portfolio) read
    into one verdict, per design §3.3.

    The real-money exchange-fills read is *ground truth* (few rows, ~2%
    fabricated); the portfolio mirror is *early-warning breadth* (realistic
    size, more rows). Priority: **use real-money when it does not abstain**
    (adequate volume + coverage), else the mirror, else abstain. The chosen
    source is named so a reader sees which instrument carried the call.
    """
    real = source_verdict(real_stats, coverage_floor=coverage_floor, min_trades=min_trades)
    mirror = source_verdict(mirror_stats, coverage_floor=coverage_floor, min_trades=min_trades)

    if real["status"] not in ABSTAIN_STATES:
        chosen, source = real, "real_money"
    elif mirror["status"] not in ABSTAIN_STATES:
        chosen, source = mirror, "mirror"
    else:
        # Both abstain — surface the LESS-abstaining reason as the headline, but
        # the status is abstain either way. Prefer real-money's reason (it is the
        # account the gate ultimately protects).
        chosen, source = real, "real_money"

    return {
        "status": chosen["status"],
        "chosenSource": source,
        "detail": chosen["detail"],
        "real": real,
        "mirror": mirror,
    }


def summarize(legs: List[Dict[str, Any]]) -> Dict[str, int]:
    """Roll up a list of combined-leg verdicts into a status histogram."""
    out = {PASS: 0, WOULD_BLOCK: 0, ABSTAIN_UNVERIFIED: 0, ABSTAIN_THIN: 0}
    for leg in legs:
        st = leg.get("status")
        if st in out:
            out[st] += 1
    return out
