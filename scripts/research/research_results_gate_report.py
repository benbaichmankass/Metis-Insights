#!/usr/bin/env python3
"""R4 — research→results shadow-gate REPORTER (observe-only, P0).

Computes, for every live strategy leg, the R4 mirror verdict
(``pass`` / ``would_block`` / ``abstain_unverified`` / ``abstain_thin``) from the
SAME ``/api/bot/performance`` aggregation the endpoint serves, and writes an
observe-only report. **Nothing is enforced** — this is the evidence trail that
the gate would have made the right call (the same discipline every other soak
follows), before the operator flips it to enforcing (P2, Tier-3).

Design: ``docs/research/research-to-results-cost-gate-DESIGN-2026-08-01.md`` §6.

Provenance of every number printed (there is no hidden substitution):
  * The per-leg stats come from ``performance._aggregate`` over the closed-trade
    window — the identical rollup ``GET /api/bot/performance`` returns, so a leg's
    ``totalPnlMeasured`` / ``pnlCoverage`` here == what a consumer sees there.
  * ``real`` = real-money rows (``demo=False``); ``mirror`` = the paper
    live-portfolio-mirror books (``paper_role: portfolio``), NOT the full soak
    roster. When no portfolio books are declared the mirror read is empty and
    every leg's mirror source abstains ``thin`` — surfaced, never hidden.
  * The verdict logic is ``src.runtime.research_results_gate`` — imported, not
    re-derived here.

Usage:
    python -m scripts.research.research_results_gate_report [--window 7d] \
        [--coverage-floor 0.6] [--min-trades 20] [--out PATH] [--json]

Exit status is always 0 on a successful read (this is a reporter, not a gate);
a genuine DB/read error exits non-zero so a scheduled run surfaces the failure
instead of silently writing an empty report.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.runtime.research_results_gate import (  # noqa: E402
    COVERAGE_FLOOR,
    MIN_TRADES,
    combined_leg_verdict,
    summarize,
)
from src.web.api.routers.performance import (  # noqa: E402
    _aggregate,
    _portfolio_paper_account_ids,
    _query,
    _window_since,
)
from src.utils.paths import trade_journal_db_path  # noqa: E402


def _index_by_name(agg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Map ``perStrategy`` list → ``{strategy_name: stats}``."""
    return {s["name"]: s for s in agg.get("perStrategy", [])}


def build_report(
    db_path: Path,
    window: str,
    *,
    coverage_floor: float = COVERAGE_FLOOR,
    min_trades: int = MIN_TRADES,
) -> Dict[str, Any]:
    """Compute the observe-only R4 verdict for every leg present in either the
    real-money or the mirror book, over *window*."""
    since = _window_since(window)
    real_agg = _aggregate(_query(db_path, since, demo=False), window, since)
    portfolio_ids = _portfolio_paper_account_ids()
    if portfolio_ids:
        mirror_agg = _aggregate(
            _query(db_path, since, demo=True, account_ids=portfolio_ids), window, since
        )
        mirror_scope = portfolio_ids
    else:
        # No portfolio-mirror books declared — the mirror read is empty by
        # design (NOT the full soak roster), so every leg's mirror abstains thin.
        # State it in the report rather than silently substituting all-paper.
        mirror_agg = _aggregate([], window, since)
        mirror_scope = []

    real_by = _index_by_name(real_agg)
    mirror_by = _index_by_name(mirror_agg)

    legs: List[Dict[str, Any]] = []
    for name in sorted(set(real_by) | set(mirror_by)):
        verdict = combined_leg_verdict(
            real_by.get(name), mirror_by.get(name),
            coverage_floor=coverage_floor, min_trades=min_trades,
        )
        legs.append({"strategy": name, **verdict})
    # would_block first (the leg a review must look at), then abstains, passes last.
    _order = {"would_block": 0, "abstain_unverified": 1, "abstain_thin": 2, "pass": 3}
    legs.sort(key=lambda leg: (_order.get(leg["status"], 9), leg["strategy"]))

    return {
        "generated_for_window": window,
        "since": since,
        "coverage_floor": coverage_floor,
        "min_trades": min_trades,
        "mirror_account_ids": mirror_scope,
        "mirror_declared": bool(mirror_scope),
        "enforced": False,  # OBSERVE-ONLY — this report gates nothing (P0)
        "summary": summarize(legs),
        "legs": legs,
    }


def _render_markdown(report: Dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        f"# R4 research→results shadow-gate report (OBSERVE-ONLY) — window {report['generated_for_window']}",
        "",
        f"- coverage floor: {report['coverage_floor']:.2f} · min trades: {report['min_trades']}",
        f"- mirror books: {', '.join(report['mirror_account_ids']) or '(none declared — mirror abstains thin)'}",
        f"- **pass {s['pass']} · would_block {s['would_block']} · "
        f"abstain_unverified {s['abstain_unverified']} · abstain_thin {s['abstain_thin']}**",
        "- NOTHING ENFORCED — this is the evidence trail (design §6 P0).",
        "",
        "| leg | verdict | source | measured net | totalPnl (contrast) | coverage | trades | why |",
        "|---|---|---|--:|--:|--:|--:|---|",
    ]
    for leg in report["legs"]:
        chosen = leg["real"] if leg["chosenSource"] == "real_money" else leg["mirror"]
        mn = chosen.get("totalPnlMeasured")
        tp = chosen.get("totalPnl")
        cov = chosen.get("pnlCoverage")
        lines.append(
            f"| {leg['strategy']} | {leg['status']} | {leg['chosenSource']} | "
            f"{'—' if mn is None else f'{mn:+.2f}'} | "
            f"{'—' if tp is None else f'{tp:+.2f}'} | "
            f"{'—' if cov is None else f'{cov:.3f}'} | "
            f"{chosen.get('trades', 0)} | {leg['detail']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--window", default="7d", help="24h|7d|30d|all (default 7d)")
    ap.add_argument("--coverage-floor", type=float, default=COVERAGE_FLOOR)
    ap.add_argument("--min-trades", type=int, default=MIN_TRADES)
    ap.add_argument("--out", type=Path, default=None,
                    help="write the JSON report here (default: stdout only)")
    ap.add_argument("--json", action="store_true", help="print JSON, not markdown")
    args = ap.parse_args(argv)

    db_path = Path(trade_journal_db_path())
    if not db_path.exists():
        print(f"error: trade journal not found at {db_path}", file=sys.stderr)
        return 2

    report = build_report(
        db_path, args.window,
        coverage_floor=args.coverage_floor, min_trades=args.min_trades,
    )

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)

    print(json.dumps(report, indent=2) if args.json else _render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
