"""SIM Phase-4 — multi-variation sweep.

Runs a set of **variants** over the SAME history and ranks them by overall
portfolio performance — the operator's "simulate changes against actual
historical overall performance of everything together, in different
variations" ask. A variant toggles which strategies are on, which models are
advisory (and at what downsize policy), so you can A/B e.g. "roster as-is" vs
"roster + btc-regime-1h advisory at size_floor 0.5" over five years and see
which portfolio wins on net-R / drawdown / expectancy.

Each variant is one Phase-1/2 ``run_replay`` (with the Phase-3 attrition
attached), so the sweep inherits all the faithfulness guarantees — it's just
an orchestration layer over the engine, not new trading logic.

Output mirrors the existing sweep surface
(``runtime_logs/trainer_mirror/backtests/<date>/{SUMMARY.md,all_metrics.json}``)
so the dashboard's Backtesting tab shows SIM sweeps next to the operator's
manual ones. Writing to that mirror dir is **opt-in** (``publish=True``) so a
SIM run never clobbers a real backtest sweep by default.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _variant_scorer(variant: dict, default_registry_root: Optional[str]):
    """Build a ModelScorer for a variant, or None when it lists no models."""
    model_ids = [m for m in (variant.get("models") or []) if m]
    if not model_ids:
        return None, []
    from sim.models import ModelScorer

    quorum = variant.get("quorum", "majority")
    policy_cfg = {"advisory_policy": {
        "mode": "downsize",
        "bearish_threshold": float(variant.get("bearish_threshold", 0.35)),
        "size_floor": float(variant.get("size_floor", 0.5)),
        "quorum": quorum,
    }}
    scorer = ModelScorer(
        model_ids=model_ids, policy_cfg=policy_cfg,
        registry_root=variant.get("registry_root") or default_registry_root,
    )
    return scorer, model_ids


def run_sweep(
    *,
    variants: list[dict],
    candles: list[dict[str, Any]],
    symbol: str = "BTCUSDT",
    warmup_bars: int = 200,
    fee_bps_roundtrip: float = 7.5,
    timeout_bars: int = 0,
    registry_root: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Run every variant over ``candles`` and return results ranked by net_r.

    Each result: ``{name, headline, summary}``. ``headline`` is the comparable
    scalar set (net_r preferring the with-model figure when a variant has
    models, else the portfolio net_r). Sorted best-first by ranking net_r.
    """
    from sim.engine import run_replay
    from sim.attrition import compute_attrition, eval_n_from_registry

    results: list[dict[str, Any]] = []
    for variant in variants:
        name = str(variant.get("name") or f"variant_{len(results)}")
        strategies = list(variant.get("strategies") or [])
        if not strategies:
            raise ValueError(f"variant {name!r} lists no strategies")
        scorer, model_ids = _variant_scorer(variant, registry_root)

        ledger = run_replay(
            candles=candles, strategies=strategies, symbol=symbol,
            warmup_bars=warmup_bars, fee_bps_roundtrip=fee_bps_roundtrip,
            timeout_bars=timeout_bars, model_scorer=scorer,
        )
        summary = ledger.summary()
        if model_ids:
            eval_n = eval_n_from_registry(model_ids, registry_root=registry_root)
            summary["decision_attrition"] = compute_attrition(
                ledger.trades,
                bearish_threshold=float(variant.get("bearish_threshold", 0.35)),
                eval_n_by_model=eval_n,
            )

        port = summary["portfolio"]
        # Ranking net_r: the realized portfolio under THIS variant. When the
        # variant has advisory models, that's the with-model figure (the
        # decision the operator is actually evaluating); else the raw portfolio.
        rank_net_r = port["net_r"]
        if summary.get("models_in_loop"):
            rank_net_r = summary["models_in_loop"]["net_r_with_model"]

        results.append({
            "name": name,
            "headline": {
                "strategies": strategies,
                "models": model_ids,
                "closed_trades": port["closed_trades"],
                "win_rate": port["win_rate"],
                "net_r": rank_net_r,
                "net_r_no_model": port["net_r"],
                "expectancy_r": port["expectancy_r"],
                "max_drawdown_r": port["max_drawdown_r"],
            },
            "summary": summary,
        })

    results.sort(key=lambda r: r["headline"]["net_r"], reverse=True)
    return results


def render_summary_md(results: list[dict[str, Any]], *, span: list[str], symbol: str) -> str:
    """Markdown leaderboard (the SUMMARY.md the dashboard renders)."""
    lines = [
        f"# SIM variation sweep — {symbol}",
        f"Window: {span[0]} .. {span[1]}  ·  {len(results)} variants  ·  ranked by net_R",
        "",
        "| rank | variant | strategies | models | trades | win% | net_R | exp_R | maxDD_R |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(results, 1):
        h = r["headline"]
        lines.append(
            f"| {i} | {r['name']} | {','.join(h['strategies'])} | "
            f"{','.join(h['models']) or '—'} | {h['closed_trades']} | "
            f"{h['win_rate']*100:.1f} | {h['net_r']} | {h['expectancy_r']} | {h['max_drawdown_r']} |"
        )
    # Surface any model that fails the funnel-volume readiness gate.
    flags = []
    for r in results:
        for mid, a in (r["summary"].get("decision_attrition") or {}).items():
            if "insufficient" in a["readiness"] or "never" in a["readiness"]:
                flags.append(f"- `{r['name']}` / `{mid}`: {a['readiness']}")
    if flags:
        lines += ["", "## Attrition flags", *flags]
    return "\n".join(lines) + "\n"


def to_all_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """The all_metrics.json payload ({headline, extra, generated_at})."""
    return {
        "headline": results[0]["headline"] if results else {},
        "extra": {"variants": [{"name": r["name"], **r["headline"]} for r in results]},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_sweep(
    results: list[dict[str, Any]], *, out_dir: Path, span: list[str], symbol: str,
) -> None:
    """Write SUMMARY.md + all_metrics.json + per-variant summaries to ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "SUMMARY.md").write_text(render_summary_md(results, span=span, symbol=symbol))
    (out_dir / "all_metrics.json").write_text(json.dumps(to_all_metrics(results), indent=2))
    (out_dir / "variants.json").write_text(json.dumps(
        {r["name"]: r["summary"] for r in results}, indent=2))
