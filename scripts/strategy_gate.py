#!/usr/bin/env python3
"""Strategy selection gate — computed promote/demote recommendations (M7).

Mirrors ``ml/promotion/gates.py`` one level up, at the STRATEGY layer. The ML
gate decides "has this model earned shadow→advisory?"; this decides "has this
strategy earned (or lost) its ``execution: live`` seat on real money?".

WHY THIS EXISTS. Strategy selection has been a judgment call that the evidence
contradicts: ``fade_breakout_4h`` is +64R STANDALONE but −$673 IN-SYSTEM;
``turtle_soup`` + ``ict_scalp_5m`` lose ~$7,490 between them in-system even
under the best flip policy, yet both are ``execution: live`` on real money
(see docs/audits/system-portfolio-backtest-2026-05-30.md). Standalone backtest
edge does NOT predict live contribution. This module judges a strategy on its
IN-SYSTEM, NET-OF-FEE, recent contribution — the only numbers that correlate
with money.

SCOPE & TIER. Tier-1, read-only. It REPORTS; it never writes ``execution:`` or
``enabled:`` and never touches the order path. Flipping a strategy's gate is a
Tier-3 operator-approved ``config/strategies.yaml`` PR. This is the exact
"proposes, never flips" contract the Prime Directive requires (no auto-flip)
and the same one ``ml/promotion/stage_guard.py`` follows for models.

A gate status is one of (same vocabulary as ml.promotion.gates):
- ``pass``               — check ran, strategy cleared the bar.
- ``fail``               — check ran, strategy is below the bar.
- ``insufficient_data``  — check could not run (e.g. a brand-new strategy with
  no live/shadow fills yet). Treated as NOT-promotable — you cannot promote on
  missing evidence — but distinguished from ``fail`` so the operator knows
  whether to WAIT (collect data) or ABANDON.

INPUTS (all read-only):
- Per-strategy net-of-fee (P2/P3/D1/D3): ``fifo_pnl_by_strategy`` from
  ``src.runtime.exchange_fills_store`` (the cross-zero P3c primitive). Needs an
  ``order_id -> strategy`` map; until the P3b resolver lands the caller passes
  ``{}`` and these gates read ``insufficient_data`` (NOT a silent pass).
- In-system contribution (P5/D2) + return/DD (P6): the consolidated harness
  JSON (``sim`` Phase-5 account block, or the retained
  ``scripts/backtest_system.py`` JSON — same ``per_strategy_attribution`` /
  ``return_dd_ratio`` shape).
- Current gate state: ``config/strategies.yaml::execution``.

The thresholds live in ``GateThresholds`` (defaults below). Tuning them is a
Tier-3 decision; the evaluator itself is Tier-1.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Thresholds (Tier-3 to change the VALUES; the evaluator is Tier-1)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GateThresholds:
    # Promotion (shadow -> live) — ALL must pass.
    min_live_trades: int = 30            # P1 sample sufficiency
    min_net_pnl_usd: float = 0.0         # P2 net-positive after fees (> bar)
    max_fee_pct_of_gross: float = 40.0   # P3 fee drag (<= bar); audit floor (vwap 418%)
    min_oos_retention: float = 0.50      # P4 OOS expectancy / train expectancy (>= bar)
    min_insystem_pnl_usd: float = 0.0    # P5 in-system contribution (> bar)
    min_return_dd: float = 1.0           # P6 book return/DD floor (4h roster was ~0.1)
    max_book_correlation: float = 0.50   # P7 |corr| to live book (<= bar)
    # Demotion (live -> shadow) — ANY triggers.
    demote_net_pnl_usd: float = 0.0      # D1 net-negative after fees (< bar)
    demote_fee_pct_of_gross: float = 100.0  # D3 fee runaway (> bar)
    demote_min_trades_for_expectancy: int = 20  # D4 N for live-expectancy check


@dataclass(frozen=True)
class GateResult:
    name: str
    status: str  # "pass" | "fail" | "insufficient_data"
    detail: str
    value: Optional[float] = None
    threshold: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "status": self.status, "detail": self.detail,
            "value": self.value, "threshold": self.threshold,
        }


@dataclass(frozen=True)
class StrategyScorecard:
    strategy: str
    current_gate: str  # "live" | "shadow" (from config)
    promotion_gates: tuple[GateResult, ...] = field(default_factory=tuple)
    demotion_triggers: tuple[GateResult, ...] = field(default_factory=tuple)
    degraded: bool = False  # True when net-of-fee ran on journal-pnl proxy (P3b not landed)

    @property
    def recommended_action(self) -> str:
        """The single proposed action. Demotion (money-protective) wins ties."""
        fired = [d for d in self.demotion_triggers if d.status == "fail"]
        if self.current_gate == "live":
            if fired:
                return "PROPOSE_DEMOTE_TO_SHADOW"
            return "KEEP_LIVE"
        # currently shadow
        promo = list(self.promotion_gates)
        if any(g.status == "insufficient_data" for g in promo):
            return "HOLD_SHADOW_COLLECT_DATA"
        if all(g.status == "pass" for g in promo):
            return "PROPOSE_PROMOTE_TO_LIVE"
        return "KEEP_SHADOW"

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "current_gate": self.current_gate,
            "recommended_action": self.recommended_action,
            "degraded_net_of_fee": self.degraded,
            "promotion_gates": [g.to_dict() for g in self.promotion_gates],
            "demotion_triggers": [d.to_dict() for d in self.demotion_triggers],
        }


def _g(name: str, ok: Optional[bool], detail: str,
       value: Optional[float] = None, threshold: Optional[float] = None) -> GateResult:
    """Build a GateResult; ok=None -> insufficient_data."""
    status = "insufficient_data" if ok is None else ("pass" if ok else "fail")
    return GateResult(name=name, status=status, detail=detail, value=value, threshold=threshold)


def evaluate_strategy(
    strategy: str,
    current_gate: str,
    *,
    net_of_fee: Optional[Mapping[str, Any]],
    insystem: Optional[Mapping[str, Any]],
    book_return_dd: Optional[float],
    book_correlation: Optional[float],
    oos_retention: Optional[float],
    degraded: bool = False,
    thr: GateThresholds = GateThresholds(),
) -> StrategyScorecard:
    """Score one strategy. All inputs optional; missing -> insufficient_data
    on the dependent gate (never a silent pass).

    net_of_fee: a row from fifo_pnl_by_strategy (net_pnl/total_fees/
        fee_pct_of_gross/fill_count) or None.
    insystem:   {"pnl": float, "trades": int} from the harness
        per_strategy_attribution, or None.
    book_return_dd: the harness return_dd_ratio WITH this strategy in the
        live roster, or None.
    book_correlation: |monthly-return corr| to the rest of the live book, or None.
    oos_retention: OOS expectancy / train expectancy from the walk-forward, or None.
    """
    nf = net_of_fee or {}
    net = nf.get("net_pnl")
    feepct = nf.get("fee_pct_of_gross")
    nfills = nf.get("fill_count")
    isy = insystem or {}
    is_pnl = isy.get("pnl")

    # ---- Promotion gates (shadow -> live) ----
    promo = (
        _g("P1_sample_sufficiency",
           None if nfills is None else (nfills >= thr.min_live_trades),
           f"fills={nfills}", value=nfills, threshold=thr.min_live_trades),
        _g("P2_net_positive_after_fees",
           None if net is None else (net > thr.min_net_pnl_usd),
           f"net=${net}", value=net, threshold=thr.min_net_pnl_usd),
        _g("P3_fee_drag",
           None if feepct is None else (feepct <= thr.max_fee_pct_of_gross),
           f"fee%={feepct}", value=feepct, threshold=thr.max_fee_pct_of_gross),
        _g("P4_oos_retention",
           None if oos_retention is None else (oos_retention >= thr.min_oos_retention),
           f"oos/train={oos_retention}", value=oos_retention, threshold=thr.min_oos_retention),
        _g("P5_insystem_positive",
           None if is_pnl is None else (is_pnl > thr.min_insystem_pnl_usd),
           f"in-system=${is_pnl}", value=is_pnl, threshold=thr.min_insystem_pnl_usd),
        _g("P6_return_dd_floor",
           None if book_return_dd is None else (book_return_dd >= thr.min_return_dd),
           f"ret/DD={book_return_dd}", value=book_return_dd, threshold=thr.min_return_dd),
        _g("P7_low_correlation",
           None if book_correlation is None else (abs(book_correlation) <= thr.max_book_correlation),
           f"|corr|={book_correlation}", value=book_correlation, threshold=thr.max_book_correlation),
    )

    # ---- Demotion triggers (live -> shadow): a "fail" = trigger fired ----
    demote = (
        _g("D1_sustained_net_negative",
           None if net is None else (net >= thr.demote_net_pnl_usd),
           f"net=${net}", value=net, threshold=thr.demote_net_pnl_usd),
        _g("D2_insystem_drag",
           None if is_pnl is None else (is_pnl >= 0.0),
           f"in-system=${is_pnl}", value=is_pnl, threshold=0.0),
        _g("D3_fee_runaway",
           None if feepct is None else (feepct <= thr.demote_fee_pct_of_gross),
           f"fee%={feepct}", value=feepct, threshold=thr.demote_fee_pct_of_gross),
    )

    return StrategyScorecard(
        strategy=strategy, current_gate=current_gate,
        promotion_gates=promo, demotion_triggers=demote, degraded=degraded,
    )


# ---------------------------------------------------------------------------
# CLI — assemble inputs and print the ranked scorecard (read-only)
# ---------------------------------------------------------------------------
def _load_current_gates(strategies_yaml: Path) -> dict[str, str]:
    """Read each strategy's ``execution: live|shadow`` from config. Best-effort.

    Returns {} if pyyaml is unavailable or the file is unreadable — the caller
    then treats every strategy's gate as unknown rather than guessing.
    """
    try:
        import yaml
    except ImportError:
        return {}
    try:
        data = yaml.safe_load(strategies_yaml.read_text()) or {}
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, str] = {}
    for name, cfg in (data.get("strategies") or {}).items():
        if isinstance(cfg, dict) and "execution" in cfg:
            out[str(name)] = str(cfg["execution"]).strip().lower()
    return out


def _insystem_from_harness(harness_json: Optional[Path]) -> tuple[dict[str, dict], Optional[float]]:
    """Pull per_strategy_attribution + return_dd_ratio from a harness JSON
    (backtest_system.py or the sim Phase-5 ``account`` block). Returns
    ({} , None) when no JSON is given."""
    if not harness_json or not harness_json.exists():
        return {}, None
    try:
        d = json.loads(harness_json.read_text())
    except Exception:  # noqa: BLE001
        return {}, None
    # backtest_system.py shape
    attribution = d.get("per_strategy_attribution")
    ret_dd = d.get("return_dd_ratio")
    # sim Phase-5 shape nests $ metrics under "account"
    if attribution is None and isinstance(d.get("account"), dict):
        acct = d["account"]
        attribution = acct.get("per_strategy_usd") or acct.get("per_strategy_attribution")
        ret_dd = acct.get("return_over_dd", ret_dd)
    return (attribution or {}), ret_dd


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Strategy selection gate (read-only; proposes, never flips).")
    p.add_argument("--strategies-yaml", default=str(_REPO_ROOT / "config" / "strategies.yaml"))
    p.add_argument("--harness-json", default=None,
                   help="system/sim backtest JSON for in-system attribution + return/DD")
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    gates = _load_current_gates(Path(args.strategies_yaml))
    insystem_map, ret_dd = _insystem_from_harness(
        Path(args.harness_json) if args.harness_json else None
    )
    # Net-of-fee per strategy needs the P3b order_id->strategy resolver (not yet
    # landed). Until then net_of_fee is None per strategy -> P2/P3/D1/D3 read
    # insufficient_data (degraded=True flags that the verdict is incomplete).
    cards = []
    for name, gate in sorted(gates.items()):
        isy = insystem_map.get(name)
        cards.append(evaluate_strategy(
            name, gate, net_of_fee=None, insystem=isy,
            book_return_dd=ret_dd if isy else None,
            book_correlation=None, oos_retention=None, degraded=True,
        ))
    # Rank by in-system pnl desc (winners first); None last.
    cards.sort(key=lambda c: (insystem_map.get(c.strategy) or {}).get("pnl", float("-inf")),
               reverse=True)

    if not insystem_map:
        note = ("NO harness JSON supplied — every evidence gate is "
                "insufficient_data, so all verdicts default to KEEP_*. "
                "Pass --harness-json for a real in-system verdict.")
    else:
        note = ("net-of-fee gates (P2/P3/D1/D3) degraded pending the P3b "
                "order_id->strategy resolver; verdicts rest on in-system "
                "attribution + return/DD only.")
    report = {"scorecards": [c.to_dict() for c in cards], "note": note}
    out = json.dumps(report, indent=2, default=str)
    if args.json_out:
        Path(args.json_out).write_text(out)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
